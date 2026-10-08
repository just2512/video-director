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
from pathlib import Path
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
INTERRUPTED = "INTERRUPTED"

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


def delete_run(run_id: str, *, allow_waiting: bool = False) -> dict:
    """删除单条 WorkflowRun；活动任务默认拒绝，项目删除时可放宽 waiting。"""
    with _DB_LOCK, _conn() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT run_id, project_id, status FROM workflow_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return {"ok": False, "code": "NOT_FOUND", "error": "任务不存在"}
        safe = row["status"] in (COMPLETED, FAILED, CANCELLED, INTERRUPTED) or (
            allow_waiting and row["status"] == WAITING_CONFIRMATION
        )
        if not safe:
            return {"ok": False, "code": "ACTIVE_RUN", "error": "任务正在执行或等待确认，不能删除"}
        conn.execute("DELETE FROM workflow_runs WHERE run_id=?", (run_id,))
    return {"ok": True, "run_id": run_id, "project_id": row["project_id"]}


def delete_runs(run_ids: List[str], *, allow_waiting: bool = False) -> dict:
    """批量删除 WorkflowRun：先全量预检，任一活动任务存在则整批拒绝。"""
    ids = list(dict.fromkeys(str(x).strip() for x in (run_ids or []) if str(x).strip()))
    if not ids:
        return {"ok": False, "code": "EMPTY_SELECTION", "error": "未选择任务"}
    with _DB_LOCK, _conn() as conn:
        _ensure_table(conn)
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT run_id, project_id, status FROM workflow_runs WHERE run_id IN ({marks})", ids
        ).fetchall()
        found = {r["run_id"]: r for r in rows}
        missing = [x for x in ids if x not in found]
        unsafe = [
            r["run_id"] for r in rows
            if not (r["status"] in (COMPLETED, FAILED, CANCELLED, INTERRUPTED)
                    or (allow_waiting and r["status"] == WAITING_CONFIRMATION))
        ]
        if missing:
            return {"ok": False, "code": "NOT_FOUND", "error": "存在不存在的任务：" + ",".join(missing)}
        if unsafe:
            return {"ok": False, "code": "ACTIVE_RUN",
                    "error": "所选任务中仍有运行中/待确认任务：" + ",".join(unsafe)}
        conn.execute(f"DELETE FROM workflow_runs WHERE run_id IN ({marks})", ids)
    return {"ok": True, "deleted": ids}


def delete_runs_of_project(project_id: str) -> int:
    """项目删除后的 WorkflowRun 全清理；调用方须先完成活动任务硬门槛。"""
    with _DB_LOCK, _conn() as conn:
        _ensure_table(conn)
        cur = conn.execute("DELETE FROM workflow_runs WHERE project_id=?", (project_id,))
    return cur.rowcount


def reconcile_runs(reason: str = "服务重启，上一任务已中断") -> dict:
    """启动时收敛残留 RUNNING：单机进程重启后不能留下永久“生成中”。"""
    with _DB_LOCK, _conn() as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT run_id, project_id FROM workflow_runs WHERE status=?", (RUNNING,)
        ).fetchall()
        now = time.time()
        conn.execute(
            "UPDATE workflow_runs SET status=?, error=?, updated_at=? WHERE status=?",
            (INTERRUPTED, reason, now, RUNNING),
        )
    projects = []
    for row in rows:
        pid = row["project_id"]
        if pid:
            try:
                from video_director import store
                p = store.get_project(pid)
                if p and p.get("stage") in {
                    store.Stage.TTS.value, store.Stage.ALIGNMENT.value,
                    store.Stage.MATCHING.value, store.Stage.PLANNING.value,
                    store.Stage.RENDERING.value, store.Stage.QC.value,
                }:
                    store.set_stage(pid, store.Stage.FAILED, reason)
                    projects.append(pid)
            except Exception as exc:
                logger.warning("workflow reconcile project %s failed: %s", pid, exc)
        _emit(row["run_id"], "workflow.done", {"status": INTERRUPTED})
    return {"interrupted_runs": [r["run_id"] for r in rows], "projects": projects}


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
    """AI Director 统一创作入口：输入上下文与 12 个 Director 节点一并进入 WorkflowRun。"""
    payload = dict(spec or {})
    payload["_run_kind"] = "director_create"
    payload["director_plan"] = plan or {}
    try:
        from video_director.director_inputs import NODE_DEFS
        steps = [node_id for node_id, _, _ in NODE_DEFS]
    except Exception:
        steps = ["understand", "script", "scene_plan", "narration", "material_match",
                 "subtitle", "music", "watermark", "composition", "preview", "verify", "render"]
    run_id = start_run("director_create", project_id=payload.get("project_id") or None,
                       steps=steps, spec=payload)
    t = threading.Thread(target=_compose_worker, args=(run_id, payload), daemon=True)
    t.start()
    return run_id


def run_director_continue_async(
    project_id: str, target: str = "preview"
) -> Optional[str]:
    """从已完成的 Director checkpoint 继续到 preview，不重跑已完成节点。"""
    from video_director import director_inputs as di
    from video_director import store

    proj = store.get_project(project_id)
    if proj is None:
        return None

    runs = runs_of_project(project_id)
    source_run = next(
        (r for r in runs if r.get("kind") == "director_create"
         and r.get("status") == WAITING_CONFIRMATION),
        None,
    )
    if source_run is None:
        return None

    plan_path = config.project_dir(project_id) / "plans" / "director_plan.json"
    spec_path = config.project_dir(project_id) / "plans" / "director_spec.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(plan, dict) or not isinstance(spec, dict):
        return None

    runtime = plan.get("runtime") or {}
    checkpoint = str(runtime.get("checkpoint") or "")
    if not checkpoint:
        return None

    target = str(target or "preview").strip().lower()
    target_node = {"preview": "verify", "video": "render"}.get(target)
    if target_node is None:
        return None

    nodes = plan.get("nodes") or []
    ids = [str(n.get("id") or "") for n in nodes]
    if checkpoint not in ids or target_node not in ids:
        return None

    cp_i = ids.index(checkpoint)
    target_i = ids.index(target_node)
    if target_i < cp_i:
        return None

    # 目标节点已经完成时不启动空任务；用户直接进入工作台检查即可。
    if nodes[target_i].get("status") == "DONE":
        return None

    # checkpoint 之后、目标节点之前的 SKIPPED → PENDING；目标节点也恢复执行。
    # 目标之后继续保持 SKIPPED，下一次继续仍从这个构建边界推进。
    for i, node in enumerate(nodes):
        if i > cp_i and i <= target_i and node.get("status") == "SKIPPED":
            node["status"] = "PENDING"
            node["verify"] = {"status": "PENDING"}
            node["decision"] = {}
            node["error"] = ""

    plan["runtime"]["continued_from"] = checkpoint
    plan["runtime"]["checkpoint"] = ""
    spec["stop_at"] = target
    spec["confirm_mode"] = "preview"

    try:
        store.set_stage(project_id, store.Stage.CREATED)
    except Exception as exc:
        logger.warning("Director continue stage reset failed: %s", exc)

    # 不改变 bundle；Director execute 会从 checkpoint snapshot 恢复 script/audio/assets。
    return run_director_create_async(spec, plan=plan)


def run_director_continue_async(project_id: str, target: str = "preview") -> Optional[str]:
    """从最近的 Director checkpoint 继续；只恢复 checkpoint 后被 SKIPPED 的节点。"""
    from video_director import store

    if store.get_project(project_id) is None:
        return None

    waiting = next(
        (r for r in runs_of_project(project_id)
         if r.get("kind") == "director_create" and r.get("status") == WAITING_CONFIRMATION),
        None,
    )
    if waiting is None:
        return None

    plan_path = config.project_dir(project_id) / "plans" / "director_plan.json"
    spec_path = config.project_dir(project_id) / "plans" / "director_spec.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(plan, dict) or not isinstance(spec, dict):
        return None

    runtime = plan.get("runtime") or {}
    checkpoint = str(runtime.get("checkpoint") or "")
    target = str(target or "preview").strip().lower()
    target_node = {"preview": "verify", "video": "render"}.get(target)
    nodes = plan.get("nodes") or []
    ids = [str(n.get("id") or "") for n in nodes]
    if checkpoint not in ids or target_node not in ids:
        return None

    cp_i = ids.index(checkpoint)
    target_i = ids.index(target_node)
    if target_i < cp_i:
        return None

    # 已达到目标检查点：不要创建空 run。
    if nodes[target_i].get("status") == "DONE":
        return None

    for i, node in enumerate(nodes):
        if cp_i < i <= target_i and node.get("status") == "SKIPPED":
            node["status"] = "PENDING"
            node["verify"] = {"status": "PENDING"}
            node["decision"] = {}
            node["error"] = ""

    plan["runtime"]["continued_from"] = checkpoint
    plan["runtime"]["checkpoint"] = ""
    spec["stop_at"] = target
    # Continue 的 preview 只推进到 Verify；最终成片仍由现有工作台确认/渲染。
    spec["confirm_mode"] = "preview"

    try:
        store.set_stage(project_id, store.Stage.CREATED)
    except Exception as exc:
        logger.warning("Director continue stage reset failed: %s", exc)

    return run_director_create_async(spec, plan=plan)


def run_director_retry_async(project_id: str) -> Optional[str]:
    """FAILED 导演项目「重新执行」：从持久化 bundle + params 重建 spec → 新 run。

    门禁（§四 活跃保护）：项目不存在 / 无 director_inputs.json / 已有
    RUNNING run → 拒绝（返回 None，调用方给出可解释错误）。
    """
    from video_director import store
    proj = store.get_project(project_id)
    if proj is None:
        logger.warning("director retry rejected: project %s not found", project_id)
        return None
    try:
        runs = list_runs(project_id=project_id, limit=1)
        if runs and get_run(runs[0]["run_id"])["status"] == RUNNING:
            logger.warning("director retry rejected: %s has RUNNING run", project_id)
            return None
    except Exception:
        pass

    from video_director import director_inputs as di
    bundle = di.load_bundle(project_id)
    if not bundle:
        logger.warning("director retry rejected: %s has no persisted bundle", project_id)
        return None

    try:
        params = json.loads(proj.get("params_json") or "{}")
        if not isinstance(params, dict):
            params = {}
    except json.JSONDecodeError:
        params = {}
    spec = {
        "project_id": project_id,
        # 重试沿用初次参数；script 覆盖：bundle 里可能有 director 生成稿
        "script": (bundle.get("script") or {}).get("text")
                 or proj.get("script") or "",
        "brief": (bundle.get("brief") or params.get("creative_brief") or ""),
        "director_inputs": bundle,
        "voice": params.get("voice") or "zh-CN-XiaoxiaoNeural",
        "voice_rate": params.get("voice_rate") or "+0%",
        "width": params.get("width"), "height": params.get("height"),
        "fps": params.get("fps"),
        "bgm": params.get("bgm") or "auto",
        "subtitle_mode": params.get("subtitle_mode") or "plain",
        "match_materials_to_script": params.get("match_materials_to_script", True),
    }
    try:
        store.set_stage(project_id, store.Stage.CREATED)
    except Exception as exc:
        logger.warning("director retry stage reset failed: %s", exc)
    return run_director_create_async(spec)


def _compose_worker(run_id: str, spec: dict) -> None:
    """compose/director 工作线程：Director 创建走统一 Agent；旧 compose 保持原执行链。"""
    try:
        if spec.get("_run_kind") == "director_create":
            result = _execute_director_create(spec, run_id)
        else:
            result = _execute_compose(spec, run_id)
        pending = bool(result.get("pending_confirm"))
        director_verify_ok = result.get("director_verify_ok", True)
        if spec.get("_run_kind") == "director_create" and not director_verify_ok:
            finish_run(run_id, FAILED, result=result,
                       error="AI Director 创建链验证未通过")
            return
        finish_run(run_id, WAITING_CONFIRMATION if pending else COMPLETED,
                   result=result)
    except Exception as exc:  # noqa: BLE001 失败落 run，前端能看到失败阶段
        logger.exception(f"workflow compose {run_id} failed")
        failed_plan = (spec.get("director_plan") or {})
        try:
            if spec.get("project_id"):
                from video_director import director_inputs as _di
                bundle = _di.build_bundle(spec)
                failed_plan = _di.build_plan(
                    bundle, spec.get("brief") or spec.get("creative_brief") or "AI Director 创作")
                _di.persist_bundle(spec["project_id"], bundle)
                _di.update_node(spec["project_id"], failed_plan, "understand",
                                "FAILED", error=str(exc)[:800])
        except Exception:
            pass
        finish_run(run_id, FAILED,
                   result={"director_plan": failed_plan} if failed_plan else {},
                   error=str(exc)[:800])


def _director_update_node(run_id: str, project_id: str, plan: dict, node_id: str,
                          status: str, **kwargs) -> None:
    from video_director import director_inputs as _di
    _di.update_node(project_id, plan, node_id, status, **kwargs)
    step_update(run_id, node_id, status, detail=kwargs.get("error") or None)


def _director_scene_plan(bundle: dict, script: str) -> dict:
    """一次真正的 Director 规划调用：场景意图必须同时看到全部来源。"""
    from video_director import director_inputs as di
    from video_director import llm_provider
    provider = llm_provider.get_llm()
    if provider is None:
        from video_director.pipeline import split_script_scenes
        scenes = split_script_scenes(script)
        return {
            "source": "rule_fallback",
            "scenes": [{"text": s, "visual_intent": s, "source_preference": "user_asset"} for s in scenes],
        }
    context = di.build_agent_scene_context(bundle, script)
    system = (
        "你是视频创作 Director。根据用户文案和全部输入来源设计分镜指导。"
        "用户素材必须优先；参考视频只借鉴镜头语言；软件项目只能使用 VERIFIED 事实。"
        '只输出 JSON：{"scenes":[{"text":"对应文案","visual_intent":"画面意图",'
        '"source_preference":"user_asset|reference_style|software_evidence|stock|generated"}]}'
    )
    raw = provider.generate(
        "完整输入上下文：\n" + json.dumps(context, ensure_ascii=False, indent=2)
        + "\n\n请设计可执行分镜指导。",
        system=system,
    )
    raw = (raw or "").strip()
    fence = chr(96) + chr(96) + chr(96)
    if raw.startswith(fence):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit(fence, 1)[0]
    a, b = raw.find("{"), raw.rfind("}")
    if a < 0 or b <= a:
        raise RuntimeError("Director 分镜规划没有返回合法 JSON")
    data = json.loads(raw[a:b + 1])
    if not isinstance(data, dict) or not isinstance(data.get("scenes"), list):
        raise RuntimeError("Director 分镜规划缺少 scenes")
    return {"source": "llm", "scenes": data["scenes"]}


def _director_verify_creation(project_id: str, payload: dict, confirm_mode: str) -> dict:
    """创建链确定性 Verify：必须能从实际文件/IR读到结果。"""
    checks = []
    try:
        from video_director.editor import load_composition
        ir = load_composition(project_id)
        checks.append({"id": "composition_readable", "ok": bool(ir and ir.scenes),
                       "detail": f"读取到 {len(ir.scenes) if ir else 0} 个场景"})
    except Exception as exc:
        checks.append({"id": "composition_readable", "ok": False, "detail": str(exc)})
        return {"ok": False, "checks": checks}
    qc = payload.get("qc") or {}
    verdict = str(qc.get("verdict") or "").upper()
    if verdict:
        checks.append({"id": "deterministic_qc", "ok": verdict in ("PASS", "OK"),
                       "detail": verdict})
    if confirm_mode == "preview":
        story = config.project_dir(project_id) / "plans" / "storyboard.json"
        checks.append({"id": "preview_artifact", "ok": story.is_file(),
                       "detail": str(story) if story.is_file() else "storyboard.json 不存在"})
    else:
        final_path = payload.get("final_path") or ""
        checks.append({"id": "final_artifact", "ok": bool(final_path and Path(final_path).is_file()),
                       "detail": final_path or "final_path 缺失"})
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def _execute_director_create(spec: dict, run_id: str) -> dict:
    """AI Director 创建唯一执行入口：交给独立 Agent Loop，禁止 compose_auto 一把梭。"""
    from video_director import director_agent
    from video_director import director_inputs as di

    pid = spec.get("project_id") or ("web-" + str(uuid.uuid4().hex[:8]))
    bundle = di.build_bundle(spec)
    plan = spec.get("director_plan") or di.build_plan(
        bundle, bundle.get("brief") or spec.get("brief") or "AI Director 创作"
    )
    di.persist_bundle(pid, bundle)
    di.persist_plan(pid, plan)
    return director_agent.execute(
        spec,
        run_id,
        plan,
        update_node=_director_update_node,
        emit=_emit,
    )


def _execute_compose(spec: dict, run_id: str, *, track_steps: bool = True) -> dict:
    """同步执行 compose（与 server /api/compose 同参数语义），带步骤打点。

    spec 兼容两种来源：
    - 前端 WorkflowRun spec（voice_rate 为倍数 '1.0'）
    - 旧 /api/compose 字段（voice_rate 为 '+0%' 形态）
    """
    from video_director import pipeline, script_only, product_promo
    from video_director import store as vd_store

    pid = spec.get("project_id") or f"web-{uuid.uuid4().hex[:8]}"
    mode = spec.get("mode", "auto")
    hook = _tracking_hook(run_id) if track_steps else (lambda stage, state: None)
    _emit(run_id, "workflow.start", {"project_id": pid, "mode": mode})

    # voice_rate 归一：倍数（'1.0'/'1.3'）→ edge-tts 相对百分比（'+0%'）
    # 2026-10-07：归一逻辑统一到 narration.normalize_rate（Director 路径共用）
    from video_director import narration as narration_mod
    voice_rate = narration_mod.normalize_rate(spec.get("voice_rate", "+0%"))

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
        if track_steps:
            # 旧 compose 运行保留兼容进度；Director 创建由 Director 节点自己管理状态。
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
