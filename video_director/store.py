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
    conn.execute("PRAGMA journal_mode=WAL")
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
    """幂等建表（首次调用即建）。"""
    with DB_LOCK, _conn() as conn:
        conn.executescript(_SCHEMA)


class Stage(str, Enum):
    """任务状态机（SVF §17-⑤ 同构：本地任务状态可解释）。"""

    CREATED = "CREATED"
    TTS = "TTS"
    ALIGNMENT = "ALIGNMENT"
    MATCHING = "MATCHING"
    PLANNING = "PLANNING"
    RENDERING = "RENDERING"
    QC = "QC"
    DONE = "DONE"
    FAILED = "FAILED"


#: 各阶段展示进度（V2 §66 生成期间显示"导演工作流"，非技术步骤堆砌）
_STAGE_PROGRESS = {
    Stage.CREATED: 0,
    Stage.TTS: 10,
    Stage.ALIGNMENT: 25,
    Stage.MATCHING: 35,
    Stage.PLANNING: 45,
    Stage.RENDERING: 60,
    Stage.QC: 90,
    Stage.DONE: 100,
    Stage.FAILED: 100,
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
            "SELECT project_id, stage, progress, final_path, narration_duration, "
            "created_at, updated_at FROM projects ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


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
