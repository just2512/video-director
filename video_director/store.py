"""Composer 任务状态机与项目历史（借 Short Video Factory better-sqlite3 经验，Python 标准库实现）。

单机个人工具定位（V2 §10 补充：不需要 PostgreSQL/Redis/MinIO），SQLite 落：
- projects：跨会话项目历史（id/文案/参数/产物路径/状态）
- tasks：状态机（CREATED→TTS→ALIGNMENT→PLANNING→RENDERING→QC→DONE/FAILED）
- probe_cache：ffprobe 结果跨运行缓存（文件 path+mtime+size → duration/分辨率，
  修复大素材库重复探测的性能坑，SVF v1.2.2 同类优化）

状态更新幂等：compose pipeline 各阶段调用 set_stage()，进度可查询（WebUI 轮询用）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from loguru import logger

from video_director import config

#: 数据库位置：storage/composer/composer.db（与项目产物同级）
DB_LOCK = threading.Lock()


def db_path() -> Path:
    return config.composer_storage_root() / "composer.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    # Set the wait policy on every connection. Do not change journal_mode here:
    # WAL mode is persistent, and issuing PRAGMA journal_mode=WAL on every
    # process connection can itself race with another process opening the DB.
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    script TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    stage TEXT NOT NULL DEFAULT 'CREATED',
    progress INTEGER NOT NULL DEFAULT 0,
    final_path TEXT,
    narration_duration REAL,
    qc_json TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_stage ON projects(stage);

CREATE TABLE IF NOT EXISTS probe_cache (
    file_path TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    duration REAL,
    width INTEGER,
    height INTEGER,
    fps REAL,
    has_audio INTEGER NOT NULL DEFAULT 0,
    has_video INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_path)
);
"""


def init_db() -> None:
    """跨进程串行初始化 schema/WAL，避免并发首次访问互相锁库。"""
    from filelock import FileLock

    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # threading.Lock only protects this interpreter. probe_media() can call
    # init_db() from several worker processes at once, so the one-time WAL
    # transition and schema creation need an OS-visible lock as well.
    with FileLock(str(path.with_suffix(path.suffix + ".init.lock")), timeout=60):
        with DB_LOCK, _conn() as conn:
            mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            if mode != "wal":
                conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)


class Stage(str, Enum):
    """任务状态机（SVF §17-⑤ 同构：本地任务状态可解释）。

    AWAITING_CONFIRM（2026-09-20 新增）：PLANNING 之后、RENDERING 之前。
    compose_auto(auto_confirm=False) 在规划完成后暂停于此态，等待人工
    确认分镜书；confirm_project(approved=True) → RENDERING 继续，
    approved=False → CREATED 重新规划。分镜书快照存 plans/storyboard.json
    （params_json 字段亦存一份引用，跨会话可查可续）。
    """

    CREATED = "CREATED"
    TTS = "TTS"
    ALIGNMENT = "ALIGNMENT"
    MATCHING = "MATCHING"
    PLANNING = "PLANNING"
    AWAITING_CONFIRM = "AWAITING_CONFIRM"
    RENDERING = "RENDERING"
    QC = "QC"
    DONE = "DONE"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"  # 目录存在但 DB 无行（project_manager upsert 用，非管线阶段）


#: 各阶段展示进度（V2 §66 生成期间显示"导演工作流"，非技术步骤堆砌）
_STAGE_PROGRESS = {
    Stage.CREATED: 0,
    Stage.TTS: 10,
    Stage.ALIGNMENT: 25,
    Stage.MATCHING: 35,
    Stage.PLANNING: 45,
    Stage.AWAITING_CONFIRM: 50,
    Stage.RENDERING: 60,
    Stage.QC: 90,
    Stage.DONE: 100,
    Stage.FAILED: 100,
    Stage.UNKNOWN: 0,  # 占位（UNKNOWN 行不走 set_stage，防 KeyError）
}


def create_project(project_id: str, script: str, params: dict) -> None:
    init_db()
    now = time.time()
    with DB_LOCK, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO projects "
            "(project_id, script, params_json, stage, progress, created_at, updated_at) "
            "VALUES (?, ?, ?, 'CREATED', 0, ?, ?)",
            (project_id, script, json.dumps(params, ensure_ascii=False), now, now),
        )


def update_script(project_id: str, script: str) -> None:
    """Update persisted project script after Director generates or accepts copy."""
    init_db()
    with DB_LOCK, _conn() as conn:
        conn.execute("UPDATE projects SET script=?, updated_at=? WHERE project_id=?",
                     (script, time.time(), project_id))


def set_stage(project_id: str, stage: Stage, error: Optional[str] = None) -> None:
    init_db()
    progress = _STAGE_PROGRESS[stage]
    now = time.time()
    with DB_LOCK, _conn() as conn:
        if stage == Stage.FAILED:
            conn.execute(
                "UPDATE projects SET stage=?, progress=?, error=?, updated_at=? WHERE project_id=?",
                (stage.value, progress, error, now, project_id),
            )
        else:
            conn.execute(
                "UPDATE projects SET stage=?, progress=?, updated_at=? WHERE project_id=?",
                (stage.value, progress, now, project_id),
            )
    logger.info(f"project {project_id} → {stage.value} ({progress}%)")


def finish_project(project_id: str, final_path: str, narration_duration: float,
                   qc_report: dict) -> None:
    init_db()
    with DB_LOCK, _conn() as conn:
        conn.execute(
            "UPDATE projects SET stage='DONE', progress=100, final_path=?, "
            "narration_duration=?, qc_json=?, updated_at=? WHERE project_id=?",
            (final_path, narration_duration, json.dumps(qc_report, ensure_ascii=False),
             time.time(), project_id),
        )


def save_storyboard_snapshot(project_id: str, storyboard: dict) -> None:
    """把分镜书快照写入 projects.params_json（跨会话可查）。

    分镜书原文同时落盘 plans/storyboard.json（由 confirm.py 负责），
    此处只在 projects 表里存一份引用 + 用户可见的关键字段，便于
    list_projects / WebUI 项目列表直接展示分镜概览。
    """
    init_db()
    now = time.time()
    snapshot = {
        "storyboard_ref": f"plans/storyboard.json",
        "style_id": storyboard.get("style_id", ""),
        "total_duration": storyboard.get("total_duration", 0.0),
        "scene_count": storyboard.get("scene_count", 0),
        "awaiting_since": now,
    }
    with DB_LOCK, _conn() as conn:
        row = conn.execute(
            "SELECT params_json FROM projects WHERE project_id=?", (project_id,)
        ).fetchone()
        params = {}
        if row and row["params_json"]:
            try:
                params = json.loads(row["params_json"])
                if not isinstance(params, dict):
                    params = {}
            except json.JSONDecodeError:
                params = {}
        params["storyboard_snapshot"] = snapshot
        conn.execute(
            "UPDATE projects SET params_json=?, updated_at=? WHERE project_id=?",
            (json.dumps(params, ensure_ascii=False), now, project_id),
        )
    logger.info(
        f"project {project_id} storyboard snapshot saved "
        f"(style={snapshot['style_id']}, scenes={snapshot['scene_count']})"
    )


def confirm_project(project_id: str, approved: bool,
                    edits: Optional[dict] = None) -> None:
    """人工确认分镜书：批准 → RENDERING 继续；拒绝 → CREATED 回退。

    edits: 用户编辑（改素材/删场景/调顺序），不为空时记入 params_json
    供 resume_after_confirm 读取重规划。
    """
    init_db()
    now = time.time()
    if approved:
        set_stage(project_id, Stage.RENDERING)
        logger.info(f"project {project_id} 确认通过 → RENDERING")
    else:
        # 拒绝：回 CREATED，允许用户改文案/素材后重新规划
        # edits 记入 params_json 供下次规划参考
        with DB_LOCK, _conn() as conn:
            row = conn.execute(
                "SELECT params_json FROM projects WHERE project_id=?", (project_id,)
            ).fetchone()
            params = {}
            if row and row["params_json"]:
                try:
                    params = json.loads(row["params_json"])
                    if not isinstance(params, dict):
                        params = {}
                except json.JSONDecodeError:
                    params = {}
            if edits:
                params["confirm_edits"] = edits
            conn.execute(
                "UPDATE projects SET stage='CREATED', progress=0, "
                "params_json=?, updated_at=? WHERE project_id=?",
                (json.dumps(params, ensure_ascii=False), now, project_id),
            )
        logger.info(
            f"project {project_id} 确认拒绝 → CREATED"
            + (f"（记录 edits: {list(edits.keys())}）" if edits else "")
        )


def get_project(project_id: str) -> Optional[dict]:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE project_id=?", (project_id,)
        ).fetchone()
    return dict(row) if row else None


def list_projects(limit: int = 50) -> list:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT project_id, script, params_json, stage, progress, final_path, "
            "narration_duration, created_at, updated_at "
            "FROM projects ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Project Management Contract（§51/§52，Project Manager 专用底层）。
# 设计决策：name/archived 不加 SQLite 列，全部走 params_json——135 行存量
# 数据零迁移风险；schema 不动，读写语义集中在以下函数。
# ---------------------------------------------------------------------------

def _read_params_json(conn: sqlite3.Connection, project_id: str) -> dict:
    row = conn.execute("SELECT params_json FROM projects WHERE project_id=?",
                       (project_id,)).fetchone()
    if not row or not row["params_json"]:
        return {}
    try:
        params = json.loads(row["params_json"])
        return params if isinstance(params, dict) else {}
    except json.JSONDecodeError:
        return {}


def _write_params_json(conn: sqlite3.Connection, project_id: str, params: dict) -> None:
    conn.execute("UPDATE projects SET params_json=?, updated_at=? WHERE project_id=?",
                 (json.dumps(params, ensure_ascii=False), time.time(), project_id))


def read_project_params(project_id: str) -> dict:
    """读 projects.params_json（无行/损坏 → 空 dict）。项目级参数的公开读口。"""
    init_db()
    with _conn() as conn:
        return _read_params_json(conn, project_id)


def write_project_params(project_id: str, params: dict) -> None:
    """整份覆写 projects.params_json。项目级参数的公开写口（读-改-写两步式）。

    与既有 save_storyboard_snapshot/confirm_project 同模式：先读后合并再写，
    避免调用方各自拼 SQL；只改一个键时务必先 read_project_params 再回写。
    """
    init_db()
    with DB_LOCK, _conn() as conn:
        # 目录存在但 DB 无行时先 upsert，否则 UPDATE 静默影响 0 行
        conn.execute(
            "INSERT OR IGNORE INTO projects "
            "(project_id, script, params_json, stage, progress, created_at, updated_at) "
            "VALUES (?, '', '{}', 'UNKNOWN', 0, ?, ?)",
            (project_id, time.time(), time.time()),
        )
        _write_params_json(conn, project_id, params)


def ensure_project_row(project_id: str, script: str = "", stage: str = "UNKNOWN") -> None:
    """目录存在但 DB 无行的项目做动作前 upsert（不覆盖已有 stage）。"""
    init_db()
    now = time.time()
    with DB_LOCK, _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO projects "
            "(project_id, script, params_json, stage, progress, created_at, updated_at) "
            "VALUES (?, ?, '{}', ?, 0, ?, ?)",
            (project_id, script, stage, now, now),
        )


def rename_project(project_id: str, name: str) -> bool:
    """name 写 params_json['name']（行不存在返回 False）。"""
    init_db()
    with DB_LOCK, _conn() as conn:
        if conn.execute("SELECT 1 FROM projects WHERE project_id=?",
                         (project_id,)).fetchone() is None:
            return False
        params = _read_params_json(conn, project_id)
        params["name"] = name
        _write_params_json(conn, project_id, params)
    return True


def get_project_name(project_id: str) -> Optional[str]:
    """params_json.name（无行/无名 → None）。"""
    init_db()
    with _conn() as conn:
        params = _read_params_json(conn, project_id)
    return params.get("name")


def set_archived(project_id: str, archived: bool) -> bool:
    """archived 标记写 params_json（归档不移动目录——绝对路径引用不破坏）。"""
    init_db()
    with DB_LOCK, _conn() as conn:
        if conn.execute("SELECT 1 FROM projects WHERE project_id=?",
                         (project_id,)).fetchone() is None:
            return False
        params = _read_params_json(conn, project_id)
        params["archived"] = bool(archived)
        params["archived_at"] = time.time() if archived else None
        _write_params_json(conn, project_id, params)
    return True


def clone_project_row(src_pid: str, dst_pid: str, name_override: Optional[str] = None,
                      extra_params: Optional[dict] = None) -> bool:
    """复制 DB 行：script/params_json 拷贝，stage 固定 CREATED（副本是待编辑项目）。

    extra_params：调用方追加键（cloned_from/origin 等 lineage 记录）。
    """
    init_db()
    now = time.time()
    with DB_LOCK, _conn() as conn:
        row = conn.execute(
            "SELECT script, params_json FROM projects WHERE project_id=?",
            (src_pid,)).fetchone()
        if row is None:
            return False
        try:
            params = json.loads(row["params_json"]) if row["params_json"] else {}
            if not isinstance(params, dict):
                params = {}
        except json.JSONDecodeError:
            params = {}
        if name_override:
            params["name"] = name_override
        if extra_params:
            params.update(extra_params)
        conn.execute(
            "INSERT OR REPLACE INTO projects "
            "(project_id, script, params_json, stage, progress, created_at, updated_at) "
            "VALUES (?, ?, ?, 'CREATED', 0, ?, ?)",
            (dst_pid, row["script"], json.dumps(params, ensure_ascii=False), now, now),
        )
    return True


def delete_project_row(project_id: str) -> bool:
    """删除 DB 行（§6.3 步骤 3a；目录由 project_manager 负责）。"""
    init_db()
    with DB_LOCK, _conn() as conn:
        cur = conn.execute("DELETE FROM projects WHERE project_id=?", (project_id,))
    return cur.rowcount > 0


def jobs_of_project(project_id: str) -> list:
    """按项目查自动化任务（jobs 在 studio.db，spec_json 里引用 project_id）。

    §72 IG-PROJECT-MANAGEMENT Jobs 层：删除验证用（残留 job → 未删干净）。
    任务表归 studio 管（studio.db），此处只读委托。
    """
    from video_director import studio as vd_studio
    try:
        vd_studio.init_db()
    except Exception:  # noqa: BLE001 studio 库不可用时不阻断项目层
        return []
    import sqlite3 as _sq
    db = config.composer_storage_root() / "studio.db"
    conn = _sq.connect(str(db), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT job_id, kind, status, spec_json FROM jobs ORDER BY created_at DESC",
        ).fetchall()
    except sqlite3.OperationalError:
        return []  # jobs 表不存在（老库）→ 无任务可查
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            spec = json.loads(r["spec_json"]) if r["spec_json"] else {}
        except json.JSONDecodeError:
            spec = {}
        refs = [spec.get("project_id"), spec.get("pid"),
                (spec.get("episode") or {}).get("project_id") if isinstance(spec.get("episode"), dict) else None]
        if project_id in [x for x in refs if x]:
            out.append({"job_id": r["job_id"], "kind": r["kind"], "status": r["status"]})
    return out


def delete_jobs_of_project(project_id: str) -> int:
    """删除项目关联的自动化任务行（§6.3 删除实现：logs/run state 随项目清理）。"""
    targets = [j["job_id"] for j in jobs_of_project(project_id)]
    if not targets:
        return 0
    import sqlite3 as _sq
    db = config.composer_storage_root() / "studio.db"
    with DB_LOCK, _sq.connect(str(db), timeout=15) as conn:
        conn.executemany("DELETE FROM jobs WHERE job_id=?",
                         [(jid,) for jid in targets])
    return len(targets)


# ---------------------------------------------------------------------------
# ffprobe 结果缓存（SVF v1.2.2：大素材库 duration 获取/缓存优化）
# ---------------------------------------------------------------------------

def cache_key(file_path: str) -> Optional[tuple]:
    """缓存键 = (path, mtime, size)；文件变动自动失效。"""
    try:
        st = Path(file_path).stat()
        return (str(Path(file_path).resolve()), st.st_mtime, st.st_size)
    except OSError:
        return None


def get_cached_probe(file_path: str) -> Optional[dict]:
    """命中缓存返回 {duration,width,height,fps,has_audio,has_video}，否则 None。"""
    key = cache_key(file_path)
    if key is None:
        return None
    with DB_LOCK, _conn() as conn:
        row = conn.execute(
            "SELECT duration, width, height, fps, has_audio, has_video, mtime, size "
            "FROM probe_cache WHERE file_path=?",
            (key[0],),
        ).fetchone()
    if row is None or row["mtime"] != key[1] or row["size"] != key[2]:
        return None
    return {
        "duration": row["duration"], "width": row["width"], "height": row["height"],
        "fps": row["fps"], "has_audio": bool(row["has_audio"]),
        "has_video": bool(row["has_video"]),
    }


def save_probe(file_path: str, probe_result) -> None:
    """probe_media 结果落缓存（幂等 replace）。"""
    key = cache_key(file_path)
    if key is None:
        return
    with DB_LOCK, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO probe_cache "
            "(file_path, mtime, size, duration, width, height, fps, has_audio, has_video) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (key[0], key[1], key[2], probe_result.duration, probe_result.width,
             probe_result.height, probe_result.fps,
             int(probe_result.has_audio_stream), int(probe_result.has_video_stream)),
        )
