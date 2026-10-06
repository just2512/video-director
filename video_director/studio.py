"""Creative OS 生产层（方案 §5/§9/§30/§40-42）。

四个能力，全部零第三方依赖（SQLite + 文件系统 + 后台线程，方案 §23 本地第一版原则）：

1. Variant 引擎（§40-41）：同一 IR 批量渲染多平台画幅变体（抖音/视频号 9:16、
   B站 16:9、小红书 3:4），Variant 树记录 Changed/Reused。
2. 系列生产（§5）：series_spec 模板（默认配音/画幅/字幕样式/BGM）+ 多集文案
   批量成片（每集独立 project，模板参数继承）。
3. Capture Inbox（§9/§30）：收集箱——链接/想法先入库，之后转创作。
4. Automation 队列（§42）：批量任务排队执行（variant/系列共用），
   Waiting/Running/Done/Failed 状态可查。

设计原则（方案 §3 Planning/Execution 分离）：本模块只做调度与状态，
渲染复用 pipeline/confirm 既有确定性执行器。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from video_director import config

#: 队列/收集箱/系列模板 共用一个 SQLite（studio.db，与 preference.db 分域）
_DB_LOCK = threading.Lock()


def _db_path() -> Path:
    return config.composer_storage_root() / "studio.db"


def _conn() -> sqlite3.Connection:
    # monkeypatched storage root（测试隔离）下目录可能尚未存在，先建再连
    _db_path().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,             -- series_episode | variant | analysis
    status TEXT NOT NULL DEFAULT 'waiting',  -- waiting|running|done|failed
    spec_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS inbox (
    item_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,             -- link | idea | note
    content TEXT NOT NULL,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'new',  -- new|converted|dismissed
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS series (
    series_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    spec_json TEXT NOT NULL DEFAULT '{}',  -- 模板：voice/aspect/subtitle/bgm
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


def init_db() -> None:
    with _DB_LOCK, _conn() as conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------------------
# 1. Capture Inbox（§30）
# ---------------------------------------------------------------------------
def inbox_add(kind: str, content: str, note: str = "") -> str:
    init_db()
    item_id = f"cap-{uuid.uuid4().hex[:8]}"
    now = time.time()
    with _DB_LOCK, _conn() as conn:
        conn.execute(
            "INSERT INTO inbox (item_id, kind, content, note, status, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (item_id, kind, content, note, "new", now),
        )
    return item_id


def inbox_list(limit: int = 50) -> List[dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM inbox ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def inbox_set_status(item_id: str, status: str) -> None:
    init_db()
    with _DB_LOCK, _conn() as conn:
        conn.execute(
            "UPDATE inbox SET status=? WHERE item_id=?", (status, item_id)
        )


def inbox_delete(item_id: str) -> None:
    init_db()
    with _DB_LOCK, _conn() as conn:
        conn.execute("DELETE FROM inbox WHERE item_id=?", (item_id,))


def inbox_bulk_delete(item_ids: List[str]) -> dict:
    ids = list(dict.fromkeys(str(x).strip() for x in (item_ids or []) if str(x).strip()))
    if not ids:
        return {"ok": False, "code": "EMPTY_SELECTION", "error": "未选择收集箱条目"}
    with _DB_LOCK, _conn() as conn:
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT item_id FROM inbox WHERE item_id IN ({marks})", ids).fetchall()
        found = {r["item_id"] for r in rows}
        missing = [x for x in ids if x not in found]
        if missing:
            return {"ok": False, "code": "NOT_FOUND", "error": "存在不存在的收集箱条目：" + ",".join(missing)}
        conn.execute(f"DELETE FROM inbox WHERE item_id IN ({marks})", ids)
    return {"ok": True, "deleted": ids}


def inbox_bulk_status(item_ids: List[str], status: str) -> dict:
    if status not in ("converted", "dismissed", "new"):
        return {"ok": False, "code": "INVALID_STATUS", "error": "不支持的收集箱状态"}
    ids = list(dict.fromkeys(str(x).strip() for x in (item_ids or []) if str(x).strip()))
    if not ids:
        return {"ok": False, "code": "EMPTY_SELECTION", "error": "未选择收集箱条目"}
    with _DB_LOCK, _conn() as conn:
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT item_id FROM inbox WHERE item_id IN ({marks})", ids).fetchall()
        found = {r["item_id"] for r in rows}
        missing = [x for x in ids if x not in found]
        if missing:
            return {"ok": False, "code": "NOT_FOUND", "error": "存在不存在的收集箱条目：" + ",".join(missing)}
        conn.execute(f"UPDATE inbox SET status=? WHERE item_id IN ({marks})", [status, *ids])
    return {"ok": True, "updated": ids, "status": status}


# ---------------------------------------------------------------------------
# 2. 系列模板（§5 Series/Episode）
# ---------------------------------------------------------------------------
def series_create(name: str, spec: Optional[dict]) -> str:
    init_db()
    sid = f"ser-{uuid.uuid4().hex[:8]}"
    with _DB_LOCK, _conn() as conn:
        conn.execute(
            "INSERT INTO series (series_id, name, spec_json, created_at) "
            "VALUES (?,?,?,?)",
            (sid, name, json.dumps(spec or {}, ensure_ascii=False), time.time()),
        )
    return sid


def series_list() -> List[dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM series ORDER BY created_at DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["spec"] = json.loads(d.pop("spec_json") or "{}")
        out.append(d)
    return out


def series_bulk_delete(series_ids: List[str]) -> dict:
    """删除系列配置，不删除已生成的单集项目；有排队/运行任务时整批拒绝。"""
    ids = list(dict.fromkeys(str(x).strip() for x in (series_ids or []) if str(x).strip()))
    if not ids:
        return {"ok": False, "code": "EMPTY_SELECTION", "error": "未选择系列"}
    with _DB_LOCK, _conn() as conn:
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT series_id FROM series WHERE series_id IN ({marks})", ids).fetchall()
        found = {r["series_id"] for r in rows}
        missing = [x for x in ids if x not in found]
        if missing:
            return {"ok": False, "code": "NOT_FOUND", "error": "存在不存在的系列：" + ",".join(missing)}
        job_rows = conn.execute(
            "SELECT job_id, status, spec_json FROM jobs WHERE status IN ('waiting','running')"
        ).fetchall()
        active_series = set()
        for row in job_rows:
            try:
                spec = json.loads(row["spec_json"] or "{}")
            except json.JSONDecodeError:
                spec = {}
            sid = spec.get("series_id")
            if sid in ids:
                active_series.add(sid)
        if active_series:
            return {"ok": False, "code": "ACTIVE_JOB",
                    "error": "以下系列仍有排队/执行任务：" + ",".join(sorted(active_series))}
        conn.execute(f"DELETE FROM series WHERE series_id IN ({marks})", ids)
    return {"ok": True, "deleted": ids}


# ---------------------------------------------------------------------------
# 3. Automation 队列（§42）+ 执行器
# ---------------------------------------------------------------------------
def job_enqueue(kind: str, spec: dict) -> str:
    """入队一个生产任务（series_episode | variant）。返回 job_id 立即。"""
    init_db()
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    now = time.time()
    with _DB_LOCK, _conn() as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, kind, status, spec_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (job_id, kind, "waiting", json.dumps(spec, ensure_ascii=False), now, now),
        )
    logger.info(f"queue: 入队 {kind} {job_id}")
    _kick_worker()
    return job_id


def job_list(limit: int = 50) -> List[dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["spec"] = json.loads(d.pop("spec_json") or "{}")
        if d.get("result_json"):
            d["result"] = json.loads(d["result_json"])
        out.append(d)
    return out


def job_get(job_id: str) -> Optional[dict]:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["spec"] = json.loads(d.pop("spec_json") or "{}")
    if d.get("result_json"):
        d["result"] = json.loads(d["result_json"])
    return d


def job_bulk_delete(job_ids: List[str]) -> dict:
    """只允许清理已结束任务；waiting/running 必须先停止或完成。"""
    ids = list(dict.fromkeys(str(x).strip() for x in (job_ids or []) if str(x).strip()))
    if not ids:
        return {"ok": False, "code": "EMPTY_SELECTION", "error": "未选择队列任务"}
    with _DB_LOCK, _conn() as conn:
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT job_id, status FROM jobs WHERE job_id IN ({marks})", ids
        ).fetchall()
        found = {r["job_id"]: r for r in rows}
        missing = [x for x in ids if x not in found]
        unsafe = [r["job_id"] for r in rows if r["status"] in ("waiting", "running")]
        if missing:
            return {"ok": False, "code": "NOT_FOUND", "error": "存在不存在的队列任务：" + ",".join(missing)}
        if unsafe:
            return {"ok": False, "code": "ACTIVE_JOB",
                    "error": "所选任务仍在排队/执行：" + ",".join(unsafe)}
        conn.execute(f"DELETE FROM jobs WHERE job_id IN ({marks})", ids)
    return {"ok": True, "deleted": ids}


def _set_job(job_id: str, status: str, result: dict = None, error: str = None):
    now = time.time()
    with _DB_LOCK, _conn() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, result_json=?, error=?, updated_at=? "
            "WHERE job_id=?",
            (status,
             json.dumps(result, ensure_ascii=False) if result else None,
             error, now, job_id),
        )


#: 平台 → 画幅（§41 Distribution Matrix）
PLATFORMS = {
    "douyin":   ("1080x1920", "抖音/视频号 9:16"),
    "bilibili": ("1920x1080", "B站/YouTube 16:9"),
    "xhs":      ("1080x1440", "小红书 3:4"),
    "square":   ("1080x1080", "朋友圈 1:1"),
}


def _run_variant(spec: dict) -> dict:
    """Variant 渲染：已完成项目 → 指定画幅重渲。

    两种路径：
    - AWAITING_CONFIRM 态项目：resume_after_confirm 续渲
    - 已完成（DONE）项目：读回 storyboard + narration，重跑 compose_project
      （幂等，storyboard/scenes/narration 全在项目包内，只换宽高）
    """
    from video_director import confirm as confirm_mod
    from video_director import pipeline, store
    pid = spec["project_id"]
    w, h = spec["width"], spec["height"]
    platform = spec.get("platform", "custom")

    proj = store.get_project(pid)
    stage = (proj or {}).get("stage")
    if stage == store.Stage.AWAITING_CONFIRM.value:
        result = pipeline.resume_after_confirm(pid, edits=None)
    else:
        # DONE/任意态：便携项目包重渲（Portable Project 的意义）
        result = pipeline._recompose_with_size(pid, width=w, height=h)

    final = Path(result.final_path)
    variant_dir = final.parent.parent / "variants"
    variant_dir.mkdir(parents=True, exist_ok=True)
    out = variant_dir / f"final_{platform}_{w}x{h}.mp4"
    if final != out:
        import shutil
        shutil.copyfile(final, out)
    return {"final_path": str(out), "platform": platform,
            "qc": result.qc_report.get("verdict")}


def _run_series_episode(spec: dict) -> dict:
    """系列单集：模板 spec + 一段文案 → compose_auto 独立成片。"""
    from video_director import pipeline
    sp = spec.get("spec", {}) or {}
    # pid 预生成于 enqueue_series_batch；兼容旧 job（无 project_id）才回退随机
    pid = spec.get("project_id") or (
        f"ser-{spec.get('series_id','x')[:8]}-ep{spec.get('episode', 0):02d}-{uuid.uuid4().hex[:4]}")
    result = pipeline.compose_auto(
        script=spec["script"],
        video_files=spec.get("video_files", []) or [],
        image_files=spec.get("image_files", []) or [],
        project_id=pid,
        voice=sp.get("voice", "zh-CN-XiaoxiaoNeural"),
        style_intent=sp.get("style_intent"),
        bgm_asset_id=sp.get("bgm"),
        width=int(sp.get("width", 1080)) if sp.get("width") else None,
        height=int(sp.get("height", 1920)) if sp.get("height") else None,
        subtitle_mode=sp.get("subtitle_mode", "plain"),
        title=sp.get("title"),
        watermark=sp.get("watermark"),
        auto_confirm=True,
    )
    return {"project_id": pid, "final_path": result.final_path,
            "qc": result.qc_report.get("verdict")}


_WORKER_STARTED = False


def _worker_loop():
    while True:
        job = None
        with _DB_LOCK, _conn() as conn:
            row = conn.execute(
                "SELECT job_id, kind, spec_json FROM jobs "
                "WHERE status='waiting' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE jobs SET status='running', updated_at=? WHERE job_id=?",
                    (time.time(), row["job_id"]),
                )
                job = dict(row)
        if job is None:
            time.sleep(1.5)
            continue
        spec = json.loads(job["spec_json"] or "{}")
        # §五十八/§70-4 任务统一：studio job 同步登记 WorkflowRun
        # （variant/series 也是"一个自动化流程"，查询面统一 /api/workflows）
        run_id = _mirror_to_workflow_run(job["job_id"], job["kind"], spec)
        try:
            if job["kind"] == "variant":
                result = _run_variant(spec)
            elif job["kind"] == "series_episode":
                result = _run_series_episode(spec)
            else:
                raise ValueError(f"未知任务类型 {job['kind']}")
            _set_job(job["job_id"], "done", result=result)
            if run_id:
                from video_director import workflow_run as vd_wf
                vd_wf.finish_run(run_id, vd_wf.COMPLETED, result=result)
            logger.info(f"queue: 完成 {job['job_id']} → {result.get('final_path') or result.get('project_id')}")
        except Exception as exc:
            _set_job(job["job_id"], "failed", error=str(exc)[:1000])
            if run_id:
                try:
                    from video_director import workflow_run as vd_wf
                    vd_wf.finish_run(run_id, vd_wf.FAILED, error=str(exc)[:800])
                except Exception:  # noqa: BLE001 run 登记失败不阻断队列本体
                    pass
            logger.warning(f"queue: 失败 {job['job_id']}: {exc}")


def _mirror_to_workflow_run(job_id: str, kind: str, spec: dict) -> Optional[str]:
    """§五十八：studio job → WorkflowRun 镜像（查询面统一；失败不阻断队列）。"""
    try:
        from video_director import workflow_run as vd_wf
        steps = ["script", "narration", "material_match", "composition",
                 "verify", "render"] if kind == "series_episode" else ["render", "verify"]
        return vd_wf.start_run(kind, project_id=spec.get("project_id"), steps=steps)
    except Exception:  # noqa: BLE001 无 workflow_runs 表环境（测试隔离）跳过
        return None


def _kick_worker():
    """启动单工作线程（幂等；队列串行=本地单机正确语义）。"""
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    init_db()
    # 启动时把上次崩溃残留的 running 复位为 waiting（进程重启可恢复）
    with _DB_LOCK, _conn() as conn:
        conn.execute(
            "UPDATE jobs SET status='waiting' WHERE status='running'"
        )
    t = threading.Thread(target=_worker_loop, daemon=True)
    t.start()
    _WORKER_STARTED = True
    logger.info("queue: 生产队列 worker 已启动")


def enqueue_series_batch(series_id: str, spec: dict, episodes: List[dict],
                          video_files: List[str] = None,
                          image_files: List[str] = None) -> List[str]:
    """系列批量：多集文案 → 逐集入队。episodes=[{episode, script}]。"""
    job_ids = []
    for ep in episodes:
        # pid 在入队时预生成（不再在执行期随机）：镜像 WorkflowRun 与 compose
        # 用同一 project_id——渲染中的 ser-* 项目可被 _workflow_snapshot 查到，
        # mid-render 删除保护（§四 WorkflowRun=运行态事实源）由此成立。
        pid = (f"ser-{series_id[:8]}-ep{ep.get('episode', 0):02d}-"
               f"{uuid.uuid4().hex[:4]}")
        job_ids.append(job_enqueue("series_episode", {
            "series_id": series_id,
            "episode": ep.get("episode", 0),
            "script": ep.get("script", ""),
            "video_files": video_files or [],
            "image_files": image_files or [],
            "project_id": pid,
            "spec": spec,
        }))
    return job_ids


def enqueue_platform_variants(project_id: str, platforms: List[str]) -> List[str]:
    """多平台变体批量：项目 → 每个平台画幅一个 variant 任务。"""
    job_ids = []
    for p in platforms:
        if p not in PLATFORMS:
            continue
        w, h = PLATFORMS[p][0].split("x")
        job_ids.append(job_enqueue("variant", {
            "project_id": project_id,
            "platform": p,
            "width": int(w), "height": int(h),
        }))
    return job_ids
