"""Project Management Contract（方案《FRONTEND_DEEP_UNIFICATION》§5/§6/§51-§53/§69）。

项目 = 有状态/有生命周期/有操作集合的一级业务对象。本模块是所有项目动作的
唯一实现层（server.py 不得自己删文件系统）：

    list_index()        Project Index（§52：服务端解释状态，前端不猜）
    inspect(pid)        项目详情（status/editable/legacy/actions 合并）
    rename(pid, name)   params_json["name"]
    clone(pid)          复制目录+DB 行（成片不复制——旧成片对新项目语义错误）
    archive / restore   params_json["archived"]
    delete(pid)         §6.3 硬规则：路径校验→active job REJECT→DB→目录→五层验证
    legacy_detect(pid)  无 composition.json 的旧 gfx 渲染项目
    legacy_convert(pid) §53：graphic_plan.json → CompositionIR → 新项目（旧的原样保留）

设计决策（可解释）：
- name/archived 不加 SQLite 列，全部走 params_json——135 行存量数据零迁移风险；
- archived 不移动目录——移动会破坏 final_path 等绝对路径引用（§5 语义是标记）；
- clone 不复制 output/render 下的 mp4——复制旧成片到新项目语义错误，Composition
  结构照常复制，新项目用「渲染」重新产出自己的成片。
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from loguru import logger

from video_director import config, store

#: §6.2 DELETE 硬规则：活跃任务阶段 → REJECT（没有任务取消能力时不能假装能删）
ACTIVE_STAGES = {"TTS", "ALIGNMENT", "MATCHING", "PLANNING", "RENDERING", "QC"}

#: §52 Project Index 只收录项目目录（web-/tpl-/gfx- 前缀语义与既有列表一致）
PROJECT_PREFIXES = ("web-", "tpl-", "gfx-", "ser-")

#: pid 合法形状（防路径穿越第一道闸）
_PID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ProjectActionError(Exception):
    """项目动作失败（人话 message + code，调用方直接透传给前端）。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _safe_dir(project_id: str) -> Path:
    """pid → 项目目录；非法/越界 → ProjectActionError（§6.3 路径校验）。"""
    if not project_id or not _PID_RE.match(project_id):
        raise ProjectActionError("INVALID_PROJECT_ID", "项目标识不合法")
    d = config.composer_storage_root() / project_id
    try:
        d.resolve().relative_to(config.composer_storage_root().resolve())
    except ValueError as exc:
        raise ProjectActionError("INVALID_PROJECT_ID", "项目路径越界") from exc
    return d


def _read_params(row: Optional[dict]) -> dict:
    """DB 行 → params_json dict（损坏/缺失给空 dict）。"""
    if not row or not row.get("params_json"):
        return {}
    try:
        params = json.loads(row["params_json"])
        return params if isinstance(params, dict) else {}
    except json.JSONDecodeError:
        return {}


def _dir_meta(project_id: str) -> dict:
    """目录状态探测（storyboard/run/draft/final/composition/graphic_plan）。

    目录在探测瞬间被删除（并发删除/清理）时各态按"不存在"处理，
    不让单项目竞态炸掉整个 Index。
    """
    d = config.composer_storage_root() / project_id
    try:
        mtime = int(d.stat().st_mtime) if d.is_dir() else 0
        return {
            "dir_exists": d.is_dir(),
            "has_composition": (d / "plans" / "composition.json").is_file(),
            "has_graphic_plan": (d / "plans" / "graphic_plan.json").is_file(),
            "has_storyboard": (d / "plans" / "storyboard.json").is_file(),
            "has_draft": (d / "output" / "draft.mp4").is_file(),
            "has_final": (d / "output" / "final.mp4").is_file(),
            "mtime": mtime,
        }
    except OSError:
        return {"dir_exists": False, "has_composition": False,
                "has_graphic_plan": False, "has_storyboard": False,
                "has_draft": False, "has_final": False, "mtime": 0}


def _resolve_name(project_id: str, row: Optional[dict], params: dict) -> str:
    """§52 name 来源：params.name → params.title → script 前 16 字 → pid。"""
    if params.get("name"):
        return str(params["name"])
    if params.get("title"):
        return str(params["title"])
    if row and row.get("script"):
        return str(row["script"])[:16]
    return project_id


def _scene_count_duration(project_id: str, meta: dict) -> tuple:
    """场景数/时长：storyboard.json → composition.json → (0, 0.0)。"""
    d = config.composer_storage_root() / project_id
    for fname in ("plans/storyboard.json", "plans/composition.json"):
        p = d / fname
        if not p.is_file():
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(j, dict):
            continue
        scenes = j.get("scenes") or []
        if "scene_count" in j and "total_duration" in j:
            return int(j["scene_count"] or 0), float(j["total_duration"] or 0)
        if scenes:
            return len(scenes), float(j.get("duration") or
                                      sum(float(s.get("duration") or 0) for s in scenes))
        break
    return 0, 0.0


def _actions_for(status: str, editable: bool, legacy: bool,
                 archived: bool, active_job: bool) -> dict:
    """§5 状态矩阵：不同状态提供不同操作（前端不猜）。"""
    a = {k: False for k in
         ("open", "continue_", "preview", "duplicate", "rename", "archive",
          "delete", "convert")}
    if archived:
        # 归档项目：恢复 + 删除 + 重命名；编辑类操作保持可回（open）
        a["open"] = editable
        a["rename"] = True
        a["delete"] = not active_job
        a["duplicate"] = editable
        return a
    if legacy:
        # §5 LEGACY GFX：转换/预览/归档/删除；open=false（Editor 不该试探它）
        a["convert"] = True
        a["preview"] = True
        a["archive"] = True
        a["delete"] = True
        a["rename"] = True
        return a
    a["rename"] = True
    a["archive"] = True
    a["delete"] = not active_job
    a["preview"] = True
    a["duplicate"] = not active_job
    if status == "DONE":
        a["open"] = True
    elif status == "AWAITING_CONFIRM":
        a["open"] = True
        a["continue_"] = True
    elif status == "FAILED":
        a["open"] = True
    elif active_job:
        a["open"] = True  # §5 RUNNING：可查看进度（打开工作台只读浏览）
    elif status in ("CREATED", "UNKNOWN"):
        a["open"] = editable
    else:
        a["open"] = editable
    if status == "UNKNOWN" and not editable:
        a["open"] = False
    return a


# ---------------------------------------------------------------------------
# Project Index（§52）
# ---------------------------------------------------------------------------

def list_index(q: str = "", name_filter: str = "all", limit: int = 200) -> list:
    """全量项目索引（含目录存在但 DB 无行的 UNKNOWN 项目——用户磁盘上能看到它们）。

    q：名称/ID 模糊过滤（大小写不敏感）；name_filter：all/pending/active/done/
    failed/archived/legacy（过滤为纯函数，见 filter_index——服务层可测）。
    """
    index = []
    root = config.composer_storage_root()
    seen = set()
    # 1. DB 行（有记录的项目——含 legacy gfx，它们的 graphic_plan 在 DB 有行）
    for row in store.list_projects(limit=1000):
        pid = row["project_id"]
        seen.add(pid)
        index.append(_entry(pid, row))
    # 2. 目录存在但 DB 无行（测试目录/异常创建——诚实列出为 UNKNOWN，可清理）
    if root.is_dir():
        def _mtime_safe(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0
        for d in sorted(root.iterdir(), key=_mtime_safe, reverse=True):
            if not d.is_dir() or d.name in seen:
                continue
            if not d.name.startswith(PROJECT_PREFIXES):
                continue
            try:
                index.append(_entry(d.name, None))
            except OSError:
                continue  # 遍历中目录被删（并发删除竞态）→ 跳过该目录不炸 Index
    out = filter_index(index, q, name_filter)
    return out[:min(limit, 500)]


def _entry(pid: str, row: Optional[dict]) -> dict:
    """单项目索引条目（§52 字段逐项）。"""
    params = _read_params(row)
    meta = _dir_meta(pid)
    status = (row and row.get("stage")) or "UNKNOWN"
    legacy = (not meta["has_composition"]) and meta["has_graphic_plan"]
    editable = meta["has_composition"]
    archived = bool(params.get("archived"))
    active_job = status in ACTIVE_STAGES
    scene_count, duration = _scene_count_duration(pid, meta)
    return {
        "project_id": pid,
        "name": _resolve_name(pid, row, params),
        "status": status,
        "editable": editable,
        "legacy": legacy,
        "archived": archived,
        "active_job": active_job,
        "scene_count": scene_count,
        "duration": round(duration, 2),
        "has_draft": meta["has_draft"],
        "has_final": meta["has_final"],
        "updated_at": meta["mtime"] or (row and row.get("updated_at")) or 0,
        "poster": "",
        "final_path": (str(config.composer_storage_root() / pid / "output" / "final.mp4")
                       if meta["has_final"] else ""),
        "actions": _actions_for(status, editable, legacy, archived, active_job),
    }


def filter_index(index: list, q: str, name_filter: str) -> list:
    """索引过滤（纯函数：§8 搜索 + 状态筛选 chips）。"""
    q = (q or "").strip().lower()
    out = []
    for p in index:
        if q and q not in p["name"].lower() and q not in p["project_id"].lower():
            continue
        f = name_filter or "all"
        if f == "pending" and not (p["status"] == "AWAITING_CONFIRM" and not p["archived"]):
            continue
        if f == "active" and not (p["active_job"] and not p["archived"]):
            continue
        if f == "done" and not (p["status"] == "DONE" and not p["archived"]):
            continue
        if f == "failed" and not (p["status"] == "FAILED" and not p["archived"]):
            continue
        if f == "archived" and not p["archived"]:
            continue
        if f == "legacy" and not p["legacy"]:
            continue
        out.append(p)
    return out


def inspect(project_id: str) -> dict:
    """单项目详情（§52 + /api/project_detail 增强字段）。"""
    d = _safe_dir(project_id)
    if not d.is_dir():
        raise ProjectActionError("NOT_FOUND", "项目不存在")
    return _entry(project_id, store.get_project(project_id))


# ---------------------------------------------------------------------------
# 项目动作（§61 全部经 POST /api/project_action）
# ---------------------------------------------------------------------------

def rename(project_id: str, new_name: str) -> dict:
    """重命名：name 写 params_json（目录无 DB 行时先 upsert）。"""
    name = (new_name or "").strip()
    if not name:
        raise ProjectActionError("INVALID_NAME", "项目名不能为空")
    if len(name) > 60:
        raise ProjectActionError("INVALID_NAME", "项目名过长（60 字以内）")
    _safe_dir(project_id)
    store.ensure_project_row(project_id, stage="UNKNOWN")
    if not store.rename_project(project_id, name):
        raise ProjectActionError("NOT_FOUND", "项目不存在")
    logger.info(f"project {project_id} renamed to '{name}'")
    return {"ok": True, "project_id": project_id, "name": name}


def clone(project_id: str) -> dict:
    """复制项目：目录 copytree（成片 mp4 除外）+ DB 行复制 → CREATED。

    成片不复制理由：旧项目的 output/final.mp4 是旧时间线的产物，出现在
    「副本」的成品位会误导（用户以为新项目已渲染完成）。Composition 结构
    照常复制，新项目用「渲染」产出自己的成片。
    """
    src = _safe_dir(project_id)
    if not src.is_dir():
        raise ProjectActionError("NOT_FOUND", "项目不存在")
    row = store.get_project(project_id)
    params = _read_params(row)
    if params.get("archived"):
        raise ProjectActionError("ARCHIVED", "已归档项目不能复制（先恢复）")
    new_pid = project_id.split("-")[0] + "-" + uuid.uuid4().hex[:8]
    if new_pid == project_id:  # 前缀意外相同时兜底换新
        new_pid = project_id + "-c" + uuid.uuid4().hex[:4]
    dst = config.composer_storage_root() / new_pid
    if dst.exists():
        raise ProjectActionError("INTERNAL", "目标目录已存在，请重试")
    shutil.copytree(src, dst)
    # 清掉复制来的成片/草稿（语义：副本是待编辑项目，不是已发布项目）
    for sub in ("output", "render"):
        sub_d = dst / sub
        if sub_d.is_dir():
            for f in sub_d.glob("*.mp4"):
                try:
                    f.unlink()
                except OSError as exc:  # noqa: BLE001 单文件清不掉不阻断
                    logger.warning(f"clone 清理 {f} 失败: {exc}")
    new_params = dict(params)
    new_params["name"] = (params.get("name") or project_id) + "（副本）"
    new_params["cloned_from"] = project_id
    new_params.pop("archived", None)
    new_params.pop("archived_at", None)
    store.ensure_project_row(new_pid, script=(row or {}).get("script") or "")
    store.clone_project_row(project_id, new_pid,
                            name_override=new_params["name"],
                            extra_params={"cloned_from": project_id})
    store.set_stage(new_pid, store.Stage.CREATED)
    logger.info(f"project {project_id} cloned to {new_pid}")
    return {"ok": True, "project_id": new_pid, "origin": project_id}


def archive(project_id: str) -> dict:
    """归档：params_json.archived=true（目录不动——final_path 绝对引用不破坏）。"""
    _safe_dir(project_id)
    store.ensure_project_row(project_id, stage="UNKNOWN")
    row = store.get_project(project_id)
    if row and row.get("stage") in ACTIVE_STAGES:
        raise ProjectActionError("ACTIVE_JOB", "项目正在执行任务，等任务结束后再归档")
    if not store.set_archived(project_id, True):
        raise ProjectActionError("NOT_FOUND", "项目不存在")
    logger.info(f"project {project_id} archived")
    return {"ok": True, "project_id": project_id}


def restore(project_id: str) -> dict:
    """恢复归档。"""
    _safe_dir(project_id)
    store.ensure_project_row(project_id, stage="UNKNOWN")
    if not store.set_archived(project_id, False):
        raise ProjectActionError("NOT_FOUND", "项目不存在")
    logger.info(f"project {project_id} restored")
    return {"ok": True, "project_id": project_id}


def delete(project_id: str) -> dict:
    """§6.3 删除硬规则（全序执行）：

    1. pid 合法性 + 路径越界检查
    2. active job → REJECT（没有任务取消能力时不假装能删）
    3. DB row → 项目目录（plans/output/assets/logs 随目录走）
    4. §69 五层验证：DB/目录/composition 全 MISSING 才算删除完成
    """
    d = _safe_dir(project_id)
    row = store.get_project(project_id)
    if row and row.get("stage") in ACTIVE_STAGES:
        raise ProjectActionError(
            "ACTIVE_JOB", "项目正在执行任务，当前不能删除（请先等待任务结束）")
    if not d.is_dir() and not row:
        raise ProjectActionError("NOT_FOUND", "项目不存在")
    # 3a. DB row 先删
    if row:
        store.delete_project_row(project_id)
    # 3b. 项目目录（含 plans/output/assets/logs/render/qc）
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=False)
    # 4. §69 验证（任何一层残留 → DELETE_INCOMPLETE，残留路径写进 error）
    v = delete_verify(project_id)
    leftovers = [k for k, missing in v.items() if not missing]
    if leftovers:
        raise ProjectActionError(
            "DELETE_INCOMPLETE",
            f"删除后仍残留：{', '.join(leftovers)}（{project_id}），未完成")
    logger.info(f"project {project_id} deleted (verified MISSING on all layers)")
    return {"ok": True, "project_id": project_id}


def delete_verify(project_id: str) -> dict:
    """§69 五层删除验证（全部 True=MISSING 才算删除完成；供测试与验收）。"""
    d = config.composer_storage_root() / project_id
    return {
        "db_row": store.get_project(project_id) is None,
        "project_dir": not d.is_dir(),
        "composition": not (d / "plans" / "composition.json").is_file(),
        "final_output": not (d / "output" / "final.mp4").is_file(),
        "graphic_plan": not (d / "plans" / "graphic_plan.json").is_file(),
    }


# ---------------------------------------------------------------------------
# Legacy（§36/§53）
# ---------------------------------------------------------------------------

def legacy_detect(project_id: str) -> dict:
    """旧版项目识别：有 graphic_plan.json 且无 composition.json → legacy。"""
    _safe_dir(project_id)
    meta = _dir_meta(project_id)
    return {
        "project_id": project_id,
        "legacy": (not meta["has_composition"]) and meta["has_graphic_plan"],
        "editable": meta["has_composition"],
    }


def legacy_convert(project_id: str) -> dict:
    """§53：旧项目 → 新可编辑项目（旧的原样保留，origin 记 lineage）。

    graphic_plan.json → CompositionIR（每场景一个 graphic 层，与
    /api/graphics/plan_to_project 同构）→ 新 pid（gfx-*）→ 保存。
    """
    _safe_dir(project_id)
    src = config.composer_storage_root() / project_id
    plan_file = src / "plans" / "graphic_plan.json"
    if not plan_file.is_file():
        raise ProjectActionError("CONVERSION_ERROR",
                                 "缺少旧版方案文件（graphic_plan.json），无法转换")
    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectActionError("CONVERSION_ERROR", f"旧版方案文件损坏: {exc}") from exc
    scenes = plan.get("scenes") or []
    if not scenes:
        raise ProjectActionError("CONVERSION_ERROR", "旧版方案里没有场景，无法转换")

    from video_director.models import CompositionIR, SceneIR, Layer, LayerType
    new_pid = "gfx-" + uuid.uuid4().hex[:8]
    base = config.project_dir(new_pid)
    dirs = config.init_project_dirs(base)
    script = ""
    script_file = src / "input" / "script.txt"
    if script_file.is_file():
        try:
            script = script_file.read_text(encoding="utf-8")
        except OSError:
            script = ""
    # 尺寸：plan.width/height → 旧 run.json → 默认 9:16
    w = int(plan.get("width") or 1080)
    h = int(plan.get("height") or 1920)
    t0 = 0.0
    ir_scenes = []
    for sc in scenes:
        dur = max(0.5, float(sc.get("duration") or 3.0))
        gparams = {
            "block_id": sc.get("block_id") or "kb.title",
            "block_name": sc.get("block_name") or sc.get("block_id") or "",
            "layout": sc.get("layout") or "center",
            "source": sc.get("source") or "Generated Graphic",
            "motion_id": sc.get("motion_id") or "graphics.fade-up",
            "content_type": plan.get("content_type") or "knowledge",
            "style_id": plan.get("style_id") or "editorial",
            "narration": sc.get("narration") or sc.get("text") or "",
            "visual_intent": sc.get("visual_intent") or "",
        }
        ir_scenes.append(SceneIR.model_validate({
            "id": f"scene-{len(ir_scenes) + 1:02d}",
            "start": t0, "duration": dur,
            "text": gparams["narration"] or " ",
            "visual_intent": gparams["visual_intent"],
            "layers": [Layer(type=LayerType.graphic, start=0.0, duration=dur,
                             motion_recipe_id=gparams["motion_id"],
                             motion_instance={"parameters": {"graphic": gparams}})],
        }))
        t0 += dur
    ir = CompositionIR.model_validate({
        "project": {"title": script[:24] or "旧版项目转换",
                    "width": w, "height": h, "fps": 30, "duration": t0},
        "scenes": [json.loads(s.model_dump_json()) for s in ir_scenes],
        "audio": [], "watermarks": [],
    })
    (dirs["plans"] / "composition.json").write_text(
        json.dumps(json.loads(ir.model_dump_json()), ensure_ascii=False, indent=2),
        encoding="utf-8")
    (dirs["assets"] / "manifest.json").write_text("[]", encoding="utf-8")
    (dirs["input"] / "script.txt").write_text(script, encoding="utf-8")
    store.create_project(new_pid, "[图形] " + (plan.get("content_type") or "knowledge"),
                          {"mode": "graphic_workspace",
                           "content_type": plan.get("content_type"),
                           "origin": project_id})
    store.set_stage(new_pid, store.Stage.AWAITING_CONFIRM)
    logger.info(f"legacy project {project_id} converted to {new_pid} "
                f"({len(ir_scenes)} scenes, origin preserved)")
    return {"ok": True, "project_id": new_pid, "origin": project_id}
