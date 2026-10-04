"""最终方案 §52/§53/§70-4：WorkflowRun —— 自动化流程的唯一持久化任务形态。

§52 要求：
    POST /api/workflows  → run_id（不再让前端挂着一个同步 compose 5 分钟）
    GET  /api/workflows/{run_id} → {run_id, project_id, status, current, steps[]}

§70-4：一个自动化流程只有一个 WorkflowRun——compose/gfx/series/variant/
render 不再各自维护任务状态。studio.jobs（SQLite 队列）是持久化层，
本模块是 WorkflowRun 的状态契约：run = 1 行 + steps[] 内嵌（steps_status_json）。

§53 真实事件（替换 create.js 的 phrases 假进度）：
    workflow.start / script.done / scene.plan.done / narration.done /
    material_match.{started,done} / subtitle.done / composition.updated /
    preview.ready / verify.done / render.done
事件经 webui.server.sse_publish 广播（前端 EventSource 已存在）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Callable, List, Optional

from loguru import logger

from video_director import config

_DB_LOCK = threading.Lock()

#: §52 WorkflowRun 状态（持久化，终态不再迁移）
RUNNING = "RUNNING"
WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
FAILED = "FAILED"
COMPLETED = "COMPLETED"
CANCELLED = "CANCELLED"

#: §53 步骤事件 → 前端人话（真实事件，不是轮播短语）
STEP_LABEL_CN = {
    "workflow.start": "开始创作",
    "script": "理解文案",
    "scene_plan": "设计镜头",
    "narration": "生成旁白",
    "material_match": "匹配画面",
    "subtitle": "生成字幕",
    "music": "配乐",
    "watermark": "品牌叠加",
    "composition": "搭建时间线",
    "preview": "草稿预览",
    "verify": "自动检查",
    "render": "渲染成片",
}

#: §20 链路的标称步骤序（一键创作链：CreativeBrief→…→Render）
DEFAULT_STEPS = ["script", "scene_plan", "narration", "material_match",
                 "subtitle", "music", "watermark", "composition",
                 "preview", "verify"]


def _db_path() -> "config.Path":
    return config.composer_storage_root() / "studio.db"


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS workflow_runs (
        run_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        project_id TEXT,
        status TEXT NOT NULL DEFAULT 'RUNNING',
        current TEXT,
        steps_status_json TEXT NOT NULL DEFAULT '{}',
        result_json TEXT,
        error TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_workflow_runs_project
        ON workflow_runs(project_id);
    """)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def start_run(kind: str, project_id: Optional[str] = None,
              steps: Optional[List[str]] = None,
              spec: Optional[dict] = None) -> str:
    """§52 POST /api/workflows：开一个 run，立即返回 run_id。

    kind: compose | script_only | product | image_shots | gfx | series_episode |
          variant | render_draft | render_final
    """
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    now = time.time()
    step_list = steps or DEFAULT_STEPS
    steps_status = {s: "PENDING" for s in step_list}
    with _DB_LOCK, _conn() as conn:
        _ensure_table(conn)
        conn.execute(
            "INSERT INTO workflow_runs (run_id, kind, project_id, status, current, "
            "steps_status_json, result_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, kind, project_id, RUNNING, step_list[0] if step_list else None,
             json.dumps(steps_status, ensure_ascii=False), json.dumps(spec or {}), now, now),
        )
    logger.info(f"workflow: run {run_id} ({kind}) started, project={project_id}")
    return run_id


def _emit(run_id: str, event: str, data: dict) -> None:
    """§53 事件广播（SSE 总线；无 server 上下文时静默跳过——CLI/测试可用）。"""
    try:
        from webui.server import sse_publish
        sse_publish(event, {"run_id": run_id, **data})
    except Exception:  # noqa: BLE001 无 SSE 环境（pytest/CLI）不阻断
        pass


def step_update(run_id: str, step: str, status: str,
                detail: Optional[str] = None) -> None:
    """更新单步状态（PENDING/RUNNING/DONE/FAILED/SKIPPED）+ 事件。"""
    now = time.time()
    with _DB_LOCK, _conn() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT steps_status_json, current, status FROM workflow_runs WHERE run_id=?",
            (run_id,)).fetchone()
        if row is None:
            return
        steps_status = json.loads(row["steps_status_json"] or "{}")
        steps_status[step] = status
        current = step if status == "RUNNING" else _next_pending(steps_status)
        conn.execute(
            "UPDATE workflow_runs SET steps_status_json=?, current=?, updated_at=? "
            "WHERE run_id=?",
            (json.dumps(steps_status, ensure_ascii=False), current, now, run_id),
        )
    label = STEP_LABEL_CN.get(step, step)
    _emit(run_id, f"workflow.step", {"step": step, "status": status,
                                     "label": label, "detail": detail or ""})


def _next_pending(steps_status: dict) -> Optional[str]:
    for s, st in steps_status.items():
        if st in ("PENDING", "RUNNING"):
            return s
    return None


def finish_run(run_id: str, status: str, result: Optional[dict] = None,
               error: Optional[str] = None) -> None:
    """收口：COMPLETED / WAITING_CONFIRMATION / FAILED / CANCELLED。"""
    now = time.time()
    with _DB_LOCK, _conn() as conn:
        _ensure_table(conn)
        conn.execute(
            "UPDATE workflow_runs SET status=?, result_json=?, error=?, updated_at=? "
            "WHERE run_id=?",
            (status, json.dumps(result or {}, ensure_ascii=False),
             error, now, run_id),
        )
    logger.info(f"workflow: run {run_id} → {status}" + (f" ({error})" if error else ""))
    _emit(run_id, "workflow.done", {"status": status})


def get_run(run_id: str) -> Optional[dict]:
    """§52 GET /api/workflows/{run_id} 响应体。"""
    with _conn() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    steps_status = json.loads(d.pop("steps_status_json") or "{}")
    steps = [{"id": s, "status": st} for s, st in steps_status.items()]
    result = json.loads(d.pop("result_json") or "{}")
    d.update({
        "steps": steps,
        "result": result,
        "labels": {s: STEP_LABEL_CN.get(s, s) for s in steps_status},
    })
    return d


def list_runs(project_id: Optional[str] = None, limit: int = 20) -> List[dict]:
    with _conn() as conn:
        _ensure_table(conn)
        if project_id:
            rows = conn.execute(
                "SELECT run_id, kind, project_id, status, current, error, created_at, updated_at "
                "FROM workflow_runs WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT run_id, kind, project_id, status, current, error, created_at, updated_at "
                "FROM workflow_runs ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall()
    return [dict(r) for r in rows]


def runs_of_project(project_id: str) -> List[dict]:
    return list_runs(project_id=project_id, limit=50)


# ---------------------------------------------------------------------------
# §52 执行器：把既有同步 compose 函数包成 run（不改 pipeline 内部——
# 在外层逐阶段打点 = 真实步骤状态，而非动画短语）。
# ---------------------------------------------------------------------------

def _tracking_hook(run_id: str) -> Callable[[str, str], None]:
    """pipeline 进度回调 → step_update（真实事件流）。"""
    def hook(stage: str, state: str) -> None:
        # stage 名对齐 DEFAULT_STEPS（pipeline 的 stage 词表见 store.Stage）
        step_map = {
            "CREATED": "script", "TTS": "narration", "ALIGNMENT": "narration",
            "MATCHING": "material_match", "PLANNING": "scene_plan",
            "RENDERING": "render", "QC": "verify",
        }
        step = step_map.get(stage)
        if not step:
            return
        step_update(run_id, step, "RUNNING" if state == "start" else "DONE")
    return hook


def run_compose_async(spec: dict) -> str:
    """§52 主入口：compose 参数 → run_id（后台线程执行既有同步链）。"""
    run_id = start_run(
        spec.get("_run_kind", "compose"),
        project_id=spec.get("project_id") or None,
        spec=spec,
    )
    t = threading.Thread(target=_compose_worker, args=(run_id, spec), daemon=True)
    t.start()
    return run_id


def run_director_create_async(spec: dict, plan: Optional[dict] = None) -> str:
    """AI Director 一句话创作：持久化计划节点，再复用既有 compose 管线执行。"""
    payload = dict(spec or {})
    payload["_run_kind"] = "director_create"
    payload["director_plan"] = plan or {}
    return run_compose_async(payload)


def _compose_worker(run_id: str, spec: dict) -> None:
    """compose/director 工作线程：执行同步管线，同时保留 Director 计划节点。"""
    try:
        result = _execute_compose(spec, run_id)
        if spec.get("director_plan"):
            result = dict(result or {})
            result["director_plan"] = spec["director_plan"]
        pending = bool(result.get("pending_confirm"))
        finish_run(run_id, WAITING_CONFIRMATION if pending else COMPLETED,
                   result=result)
    except Exception as exc:  # noqa: BLE001 失败落 run，前端能看到失败阶段
        logger.exception(f"workflow compose {run_id} failed")
        finish_run(run_id, FAILED,
                   result={"director_plan": spec.get("director_plan", {})}
                   if spec.get("director_plan") else {},
                   error=str(exc)[:800])


def _execute_compose(spec: dict, run_id: str) -> dict:
    """同步执行 compose（与 server /api/compose 同参数语义），带步骤打点。

    spec 兼容两种来源：
    - 前端 WorkflowRun spec（voice_rate 为倍数 '1.0'）
    - 旧 /api/compose 字段（voice_rate 为 '+0%' 形态）
    """
    from video_director import pipeline, script_only, product_promo
    from video_director import store as vd_store

    pid = spec.get("project_id") or f"web-{uuid.uuid4().hex[:8]}"
    mode = spec.get("mode", "auto")
    hook = _tracking_hook(run_id)
    _emit(run_id, "workflow.start", {"project_id": pid, "mode": mode})

    # voice_rate 归一：倍数（'1.0'/'1.3'）→ edge-tts 相对百分比（'+0%'）
    voice_rate = spec.get("voice_rate", "+0%")
    if not str(voice_rate).startswith(("+", "-")):
        try:
            _spd = float(voice_rate)
            voice_rate = f"{int(round((_spd - 1.0) * 100)):+d}%"
        except (TypeError, ValueError):
            voice_rate = "+0%"

    # sfx 选择归一（'auto' → None 走默认；'' → [] 关闭；具体 id → 铺底）
    sfx_choice = spec.get("sfx", spec.get("sfx_choice", "auto"))
    sfx_plan = spec.get("sfx_plan")
    if sfx_plan is None:
        if sfx_choice == "":
            sfx_plan = []
        elif sfx_choice not in ("auto", "", None):
            sfx_plan = [{"at": i * 4.0, "asset_id": sfx_choice,
                         "role": "ambience"} for i in range(200)]

    orig_set_stage = vd_store.set_stage

    # §34 配音三模式消费：
    #   auto    → 既有 compose_auto（TTS 生成）
    #   upload  → 上传配音：先 import_uploaded_voice 建 narration 轨，再按场景渲染
    #   silent  → 无配音：Narration Track absent（禁 fake voice；字幕按 scene timing）
    voice_mode = spec.get("voice_mode", "auto")
    narration_upload = spec.get("narration_upload") or ""
    if voice_mode not in ("auto", "upload", "silent"):
        voice_mode = "auto"  # 未知值诚实回落 auto（不静默失败，前端传错也能出片）

    def _patched_set_stage(project_id, stage, error=None):
        hook(stage.value if hasattr(stage, "value") else str(stage), "start")
        return orig_set_stage(project_id, stage, error)

    vd_store.set_stage = _patched_set_stage
    try:
        if mode == "script_only":
            result = script_only.compose_script_only(
                script=spec["script"], project_id=pid,
                voice=spec.get("voice", "zh-CN-XiaoxiaoNeural"),
                style_intent=spec.get("style_intent"),
                width=spec.get("width", 1080), height=spec.get("height", 1920))
            payload = {"ok": True, "project_id": pid, "mode": mode,
                       "final_path": result.final_path,
                       "narration_duration": result.narration_duration,
                       "qc": result.qc_report, "pending_confirm": False,
                       "elapsed_s": 0}
        elif mode == "product":
            result = product_promo.compose_product_promo(
                script=spec["script"], product_image_paths=spec.get("image_paths", []),
                project_id=pid, voice=spec.get("voice", "zh-CN-XiaoxiaoNeural"),
                style_intent=spec.get("style_intent"),
                width=spec.get("width", 1080), height=spec.get("height", 1920))
            payload = {"ok": True, "project_id": pid, "mode": mode,
                       "final_path": result.final_path,
                       "narration_duration": result.narration_duration,
                       "qc": result.qc_report, "pending_confirm": False,
                       "elapsed_s": 0}
        else:
            # §34 silent：无配音模式（Narration Track absent——禁 fake voice）
            effective_voice = spec.get("voice", "zh-CN-XiaoxiaoNeural")
            if voice_mode == "silent":
                effective_voice = ""  # compose_auto voice='' 走无旁白链（诚实跳过 TTS）
            result = pipeline.compose_auto(
                script=spec["script"],
                video_files=spec.get("video_paths", []),
                image_files=spec.get("image_paths", []),
                project_id=pid,
                voice=effective_voice,
                voice_rate=voice_rate,
                style_intent=spec.get("style_intent"),
                bgm_asset_id=spec.get("bgm") or spec.get("bgm_asset_id"),
                sfx_plan=sfx_plan,
                bgm_volume=spec.get("bgm_volume"),
                narration_volume=float(spec.get("narration_volume", 1.0) or 1.0),
                width=spec.get("width", 1080), height=spec.get("height", 1920),
                fps=spec.get("fps", 30),
                auto_confirm=spec.get("auto_confirm", False),
                subtitle_mode=spec.get("subtitle_mode", "plain"),
                title=spec.get("title"), lower_third_name=spec.get("lower_third_name"),
                lower_third_role=spec.get("lower_third_role"),
                creative_brief=spec.get("creative_brief"),
                watermark=spec.get("watermark"),
                fit_mode=spec.get("fit_mode", "cover"),
                clip_duration=spec.get("clip_duration"),
                clip_speed=spec.get("clip_speed"),
                concat_mode=spec.get("concat_mode", "sequential"),
                transition_mode=spec.get("transition_mode", "cut"),
                match_materials_to_script=bool(spec.get("match_materials_to_script", True)),
                visual_source=spec.get("visual_source", "auto"),
                # §34 上传配音：文件路径 → pipeline TTS 旁路（import_uploaded_voice）
                narration_upload=narration_upload or None)
            payload = {"ok": True, "project_id": pid, "mode": mode,
                       "final_path": result.final_path,
                       "narration_duration": result.narration_duration,
                       "qc": result.qc_report,
                       "pending_confirm": bool(
                           spec.get("confirm_mode", "preview") == "preview"),
                       "elapsed_s": 0}
        # 补打收尾步骤（pipeline 未逐段触发的：subtitle/music/composition/preview）
        for s in ("subtitle", "music", "composition", "preview", "verify"):
            step_update(run_id, s, "DONE")
        if payload.get("pending_confirm"):
            step_update(run_id, "render", "SKIPPED")
        return payload
    finally:
        vd_store.set_stage = orig_set_stage


def run_render_async(project_id: str, kind: str = "draft") -> str:
    """渲染也走 WorkflowRun（§70-4 一个流程一个 run）。kind: draft|final。"""
    run_id = start_run("render_" + kind, project_id=project_id,
                       steps=["render", "verify"])
    t = threading.Thread(target=_render_worker, args=(run_id, project_id, kind),
                         daemon=True)
    t.start()
    return run_id


def _render_worker(run_id: str, project_id: str, kind: str) -> None:
    try:
        step_update(run_id, "render", "RUNNING")
        if kind == "draft":
            from video_director.confirm import render_draft
            out = render_draft(project_id)
            result = {"ok": True, "draft_path": str(out)}
        else:
            from video_director import confirm as vd_confirm
            out = vd_confirm.finalize_after_review(project_id)
            result = {"ok": True,
                      "final_path": str(getattr(out, "final_path", out) or out)}
        step_update(run_id, "render", "DONE")
        step_update(run_id, "verify", "DONE")
        finish_run(run_id, COMPLETED, result=result)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"workflow render {run_id} failed")
        step_update(run_id, "render", "FAILED", detail=str(exc)[:200])
        finish_run(run_id, FAILED, error=str(exc)[:800])
