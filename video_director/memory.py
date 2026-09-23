"""创作记忆层（v1.2 §4-§16 护城河）。

系统要"越用越懂用户"：从每次创作的 Draft→Final Diff 提炼个人偏好，
聚合成 Style DNA，下次 Planning 读取作硬匹配推荐。

核心概念：
- CreationCase（§5）：一次创作的完整轨迹（input→initial_plan→draft→
  revisions→final→feedback）。
- Revision Diff / Preference Delta（§6）：Draft→Final 用户改了什么。
- Style DNA（§7-§8）：抽象创作规律（不是一支视频），字段 narrative/
  composition/motion/transition/typography/audio + compatible_with。
- Like/Dislike（§9-§10）：正式反馈信号，驱动 prefer/avoid。
- 推荐不评分（§12）：硬匹配 compatible_with 字段，命中即返回风格名。
- 绝不训练模型（§15）：结构化记忆 + 规则，读取到下次 Planning 用。

存储：SQLite storage/composer/preference.db（与 composer.db 分开，
记忆是独立域）。零外部依赖：sqlite3 + stdlib + loguru。不 import
pipeline（避免循环），仅 import config 拿 storage 根目录。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from video_director import config


# ---------------------------------------------------------------------------
# 存储路径
# ---------------------------------------------------------------------------

_DB_LOCK = threading.Lock()


def db_path() -> Path:
    """记忆数据库位置 storage/composer/preference.db（独立于 composer.db）。"""
    return config.composer_storage_root() / "preference.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS creation_cases (
    case_id TEXT PRIMARY KEY,
    input_type TEXT NOT NULL,
    script TEXT NOT NULL DEFAULT '',
    user_brief TEXT,
    assets_json TEXT NOT NULL DEFAULT '[]',
    initial_style_json TEXT NOT NULL DEFAULT '{}',
    initial_storyboard_json TEXT NOT NULL DEFAULT '{}',
    draft_artifact_json TEXT NOT NULL DEFAULT '{}',
    revisions_json TEXT NOT NULL DEFAULT '[]',
    final_artifact_json TEXT NOT NULL DEFAULT '{}',
    feedback_liked INTEGER,
    feedback_note TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_liked ON creation_cases(feedback_liked);

CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    field TEXT NOT NULL,
    value_from TEXT,
    value_to TEXT,
    normalized TEXT,
    signal TEXT NOT NULL CHECK(signal IN ('prefer','avoid')),
    created_at REAL NOT NULL,
    FOREIGN KEY(case_id) REFERENCES creation_cases(case_id)
);
CREATE INDEX IF NOT EXISTS idx_pref_signal ON preferences(signal);
CREATE INDEX IF NOT EXISTS idx_pref_field ON preferences(field);

CREATE TABLE IF NOT EXISTS style_dna (
    style_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    narrative_json TEXT NOT NULL DEFAULT '{}',
    composition_json TEXT NOT NULL DEFAULT '{}',
    motion_json TEXT NOT NULL DEFAULT '{}',
    transition_json TEXT NOT NULL DEFAULT '{}',
    typography_json TEXT NOT NULL DEFAULT '{}',
    audio_json TEXT NOT NULL DEFAULT '{}',
    compatible_with_json TEXT NOT NULL DEFAULT '[]',
    source_case_ids_json TEXT NOT NULL DEFAULT '[]',
    updated_at REAL NOT NULL
);
"""


def init_db() -> None:
    """幂等建表。"""
    with _DB_LOCK, _conn() as conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class CreationCase:
    """一次创作的完整轨迹（§5）。"""
    case_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    input_type: str = "script"           # script | brief | assets
    script: str = ""
    user_brief: Optional[str] = None
    assets: List[Any] = field(default_factory=list)
    initial_style: Dict[str, Any] = field(default_factory=dict)
    initial_storyboard: Dict[str, Any] = field(default_factory=dict)
    draft_artifact: Dict[str, Any] = field(default_factory=dict)
    revisions: List[Dict[str, Any]] = field(default_factory=list)
    final_artifact: Dict[str, Any] = field(default_factory=dict)
    feedback: Dict[str, Any] = field(default_factory=dict)  # {liked: bool, note: str}

    def feedback_liked(self) -> Optional[bool]:
        v = self.feedback.get("liked")
        if v is None:
            return None
        return bool(v)

    def feedback_note(self) -> Optional[str]:
        return self.feedback.get("note")


@dataclass
class StyleDNA:
    """抽象创作规律（§8）。"""
    style_id: str
    name: str
    narrative: Dict[str, Any] = field(default_factory=dict)
    composition: Dict[str, Any] = field(default_factory=dict)
    motion: Dict[str, Any] = field(default_factory=dict)
    transition: Dict[str, Any] = field(default_factory=dict)
    typography: Dict[str, Any] = field(default_factory=dict)
    audio: Dict[str, Any] = field(default_factory=dict)
    compatible_with: List[str] = field(default_factory=list)
    source_case_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# 字段值归一映射（§6 Preference Delta）
# ---------------------------------------------------------------------------
# 把常见取值归一成抽象倾向标签。未命中的原值原样保留。
# 设计意图：用户喜欢 cut（不要效果）→ prefer hard_cut；喜欢 zoom-through
# （重特效）→ prefer strong_transition；反之 disliked → avoid 对应方向。

_TRANSITION_MAP = {
    "cut": "hard_cut",
    "fade": "soft_transition",
    "dissolve": "soft_transition",
    "zoom-through": "strong_transition",
    "glitch": "strong_transition",
    "slide": "soft_transition",
    "wipe": "strong_transition",
}

_MOTION_MAP = {
    "subtle": "subtle_motion",
    "light": "subtle_motion",
    "gentle": "subtle_motion",
    "strong": "strong_motion",
    "heavy": "strong_motion",
    "intense": "strong_motion",
    "dynamic": "strong_motion",
}

_SFX_DENSITY_MAP = {
    "sparse": "sparse_sfx",
    "medium": "medium_sfx",
    "dense": "dense_sfx",
}

_SUBTITLE_POSITION_MAP = {
    "bottom": "subtitle_bottom",
    "center": "subtitle_center",
    "top": "subtitle_top",
}


def _normalize(field_name: str, value: Any) -> str:
    """把字段取值归一成倾向标签。未命中则 str(value)。"""
    if value is None:
        return ""
    s = str(value).lower().strip()
    if field_name == "transition":
        return _TRANSITION_MAP.get(s, s)
    if field_name in ("motion", "image_motion_intensity"):
        return _MOTION_MAP.get(s, s)
    if field_name == "sfx_density":
        return _SFX_DENSITY_MAP.get(s, s)
    if field_name == "subtitle_position":
        return _SUBTITLE_POSITION_MAP.get(s, s)
    if field_name == "bgm_volume":
        try:
            v = float(value)
            if v < 0.15:
                return "low_bgm"
            if v > 0.20:
                return "high_bgm"
            return "mid_bgm"
        except (TypeError, ValueError):
            return s
    if field_name == "transition_budget":
        try:
            v = int(value)
            if v <= 1:
                return "minimal_transitions"
            if v >= 3:
                return "frequent_transitions"
            return "moderate_transitions"
        except (TypeError, ValueError):
            return s
    return s


# ---------------------------------------------------------------------------
# 写入：CreationCase + Feedback + Preference Delta
# ---------------------------------------------------------------------------

def record_case(case: CreationCase) -> str:
    """记录一个 CreationCase（§5）。返回 case_id。

    幂等：同 case_id 再调则覆盖（INSERT OR REPLACE）。
    """
    init_db()
    now = time.time()
    liked = case.feedback_liked()
    note = case.feedback_note()
    with _DB_LOCK, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO creation_cases "
            "(case_id, input_type, script, user_brief, assets_json, "
            " initial_style_json, initial_storyboard_json, draft_artifact_json, "
            " revisions_json, final_artifact_json, feedback_liked, feedback_note, "
            " created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                case.case_id,
                case.input_type,
                case.script,
                case.user_brief,
                json.dumps(case.assets, ensure_ascii=False, default=str),
                json.dumps(case.initial_style, ensure_ascii=False, default=str),
                json.dumps(case.initial_storyboard, ensure_ascii=False, default=str),
                json.dumps(case.draft_artifact, ensure_ascii=False, default=str),
                json.dumps(case.revisions, ensure_ascii=False, default=str),
                json.dumps(case.final_artifact, ensure_ascii=False, default=str),
                int(liked) if liked is not None else None,
                note,
                now, now,
            ),
        )
    logger.info(
        f"memory: recorded case {case.case_id} "
        f"(input={case.input_type}, liked={liked})"
    )
    return case.case_id


def record_feedback(case_id: str, liked: bool, note: Optional[str] = None) -> None:
    """正式反馈信号（§9-§10）。liked=False → 进入 negative preference。"""
    init_db()
    now = time.time()
    with _DB_LOCK, _conn() as conn:
        row = conn.execute(
            "SELECT case_id FROM creation_cases WHERE case_id=?", (case_id,)
        ).fetchone()
        if row is None:
            logger.warning(f"memory: feedback for unknown case {case_id}, ignored")
            return
        conn.execute(
            "UPDATE creation_cases SET feedback_liked=?, feedback_note=?, updated_at=? "
            "WHERE case_id=?",
            (int(liked), note, now, case_id),
        )

        # 如果 case 已有 draft/final，自动抽取 preference delta
        row = conn.execute(
            "SELECT draft_artifact_json, final_artifact_json FROM creation_cases "
            "WHERE case_id=?",
            (case_id,),
        ).fetchone()
        if row is None:
            return
        try:
            draft = json.loads(row["draft_artifact_json"] or "{}")
            final = json.loads(row["final_artifact_json"] or "{}")
        except json.JSONDecodeError:
            logger.warning(f"memory: case {case_id} artifact json broken, skip delta")
            return
        if not draft or not final:
            return
        delta = extract_preference_delta(draft, final, positive=liked)
        _persist_delta(conn, case_id, delta)
    logger.info(
        f"memory: feedback recorded case={case_id} liked={liked} "
        f"(prefer={len(delta.get('prefer', []))} "
        f"avoid={len(delta.get('avoid', []))})"
    )


def extract_preference_delta(
    initial_dict: Dict[str, Any],
    final_dict: Dict[str, Any],
    positive: bool,
) -> Dict[str, List[Dict[str, Any]]]:
    """对比两个 dict 的差异，按字段提取 prefer/avoid（§6）。

    positive=True  （用户喜欢 final）→ final 取值进入 prefer
    positive=False （用户不喜欢）    → final 取值进入 avoid

    返回：{"prefer": [{field, from, to, normalized}, ...],
           "avoid":  [{...}, ...]}
    """
    prefer: List[Dict[str, Any]] = []
    avoid: List[Dict[str, Any]] = []
    for key in set(initial_dict.keys()) | set(final_dict.keys()):
        old = initial_dict.get(key)
        new = final_dict.get(key)
        if new is None or old == new:
            continue
        entry = {
            "field": key,
            "from": old,
            "to": new,
            "normalized": _normalize(key, new),
        }
        if positive:
            prefer.append(entry)
        else:
            avoid.append(entry)
    return {"prefer": prefer, "avoid": avoid}


def _persist_delta(
    conn: sqlite3.Connection,
    case_id: str,
    delta: Dict[str, List[Dict[str, Any]]],
) -> None:
    """把 delta 写入 preferences 表。"""
    now = time.time()
    for entry in delta.get("prefer", []):
        conn.execute(
            "INSERT INTO preferences "
            "(case_id, field, value_from, value_to, normalized, signal, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (case_id, entry["field"],
             _stringify(entry["from"]), _stringify(entry["to"]),
             entry["normalized"], "prefer", now),
        )
    for entry in delta.get("avoid", []):
        conn.execute(
            "INSERT INTO preferences "
            "(case_id, field, value_from, value_to, normalized, signal, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (case_id, entry["field"],
             _stringify(entry["from"]), _stringify(entry["to"]),
             entry["normalized"], "avoid", now),
        )


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


# ---------------------------------------------------------------------------
# 聚合：Style DNA
# ---------------------------------------------------------------------------

#: Style DNA 的六域字段名（§8）
_DNA_FIELDS = (
    "narrative", "composition", "motion",
    "transition", "typography", "audio",
)

#: preferences.field → 哪个 DNA 域归属
_FIELD_TO_DNA_DOMAIN = {
    "transition": "transition",
    "motion": "motion",
    "image_motion_intensity": "motion",
    "sfx_density": "audio",
    "bgm_volume": "audio",
    "subtitle_position": "typography",
    "subtitle_font_size": "typography",
    "subtitle_max_lines": "typography",
    "transition_budget": "transition",
    # 未列出的 field → 归到 narrative（最弱归属）
}


def update_style_dna(
    style_id: str = "user-personal",
    name: str = "个人风格记忆",
    compatible_with: Optional[List[str]] = None,
    source_case_ids: Optional[List[str]] = None,
) -> StyleDNA:
    """聚合 preferences 到 style_dna 表（§7-§8）。

    把当前所有 preferences 按 field 归到六域，每域存
    {"prefer": {normalized: count}, "avoid": {normalized: count}}。

    compatible_with/source_case_ids 不传则保留已有值。
    """
    init_db()
    now = time.time()

    # 读取已有 row（保留 compatible_with / source_case_ids）
    with _conn() as conn:
        row = conn.execute(
            "SELECT compatible_with_json, source_case_ids_json FROM style_dna "
            "WHERE style_id=?",
            (style_id,),
        ).fetchone()
    existing_compat: List[str] = []
    existing_sources: List[str] = []
    if row:
        try:
            existing_compat = json.loads(row["compatible_with_json"] or "[]")
            existing_sources = json.loads(row["source_case_ids_json"] or "[]")
        except json.JSONDecodeError:
            pass
    compat = compatible_with if compatible_with is not None else existing_compat
    sources = source_case_ids if source_case_ids is not None else existing_sources

    # 聚合 preferences
    with _conn() as conn:
        rows = conn.execute(
            "SELECT field, normalized, signal FROM preferences"
        ).fetchall()
    domains: Dict[str, Dict[str, Dict[str, int]]] = {
        d: {"prefer": {}, "avoid": {}} for d in _DNA_FIELDS
    }
    for r in rows:
        domain = _FIELD_TO_DNA_DOMAIN.get(r["field"], "narrative")
        if domain not in domains:
            domain = "narrative"
        norm = r["normalized"] or ""
        if not norm:
            continue
        bucket = domains[domain][r["signal"]]
        bucket[norm] = bucket.get(norm, 0) + 1

    domain_jsons = {d: json.dumps(domains[d], ensure_ascii=False) for d in _DNA_FIELDS}

    with _DB_LOCK, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO style_dna "
            "(style_id, name, narrative_json, composition_json, motion_json, "
            " transition_json, typography_json, audio_json, "
            " compatible_with_json, source_case_ids_json, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                style_id, name,
                domain_jsons["narrative"], domain_jsons["composition"],
                domain_jsons["motion"], domain_jsons["transition"],
                domain_jsons["typography"], domain_jsons["audio"],
                json.dumps(compat, ensure_ascii=False),
                json.dumps(sources, ensure_ascii=False),
                now,
            ),
        )
    logger.info(
        f"memory: style_dna updated style_id={style_id} "
        f"(domains={len(_DNA_FIELDS)}, preferences={len(rows)})"
    )
    return StyleDNA(
        style_id=style_id, name=name,
        narrative=domains["narrative"], composition=domains["composition"],
        motion=domains["motion"], transition=domains["transition"],
        typography=domains["typography"], audio=domains["audio"],
        compatible_with=compat, source_case_ids=sources,
    )


# ---------------------------------------------------------------------------
# 推荐：硬匹配 compatible_with（§12，不评分）
# ---------------------------------------------------------------------------

def recommend_style(
    content_type: Optional[str] = None,
    topic: Optional[str] = None,
    tone: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """硬匹配 compatible_with 标签（§12）。

    把 content_type/topic/tone 作为候选标签，匹配 style_dna.compatible_with
    的任一标签即命中。返回命中的 Style DNA（dict）；多个命中取 updated_at 最新；
    零命中返回 None。不评分、不打分。
    """
    init_db()
    tags = [t for t in (content_type, topic, tone) if t and str(t).strip()]
    if not tags:
        # 无标签：返回 updated_at 最新的 DNA（最常更新的）
        with _conn() as conn:
            row = conn.execute(
                "SELECT * FROM style_dna ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
    else:
        # 逐行检查 compatible_with 是否包含任一 tag（子串匹配，简单可解释）
        with _conn() as conn:
            rows = conn.execute(
                "SELECT * FROM style_dna ORDER BY updated_at DESC"
            ).fetchall()
        matched = []
        for r in rows:
            try:
                compat = json.loads(r["compatible_with_json"] or "[]")
            except json.JSONDecodeError:
                compat = []
            compat_strs = [str(c) for c in compat]
            hit = any(
                any(t in c or c in t for c in compat_strs)
                for t in tags
            )
            if hit:
                matched.append(r)
        row = matched[0] if matched else None

    if row is None:
        return None
    return _row_to_style_dna_dict(row)


def _row_to_style_dna_dict(row: sqlite3.Row) -> Dict[str, Any]:
    def _loads(s):
        try:
            return json.loads(s)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {
        "style_id": row["style_id"],
        "name": row["name"],
        "narrative": _loads(row["narrative_json"]),
        "composition": _loads(row["composition_json"]),
        "motion": _loads(row["motion_json"]),
        "transition": _loads(row["transition_json"]),
        "typography": _loads(row["typography_json"]),
        "audio": _loads(row["audio_json"]),
        "compatible_with": _loads(row["compatible_with_json"]),
        "source_case_ids": _loads(row["source_case_ids_json"]),
        "updated_at": row["updated_at"],
    }


# ---------------------------------------------------------------------------
# 读取辅助（供 Planning 调用）
# ---------------------------------------------------------------------------

def get_case(case_id: str) -> Optional[Dict[str, Any]]:
    """读回一个 CreationCase。"""
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM creation_cases WHERE case_id=?", (case_id,)
        ).fetchone()
    if row is None:
        return None
    def _loads(s, default):
        try:
            return json.loads(s) if s else default
        except json.JSONDecodeError:
            return default
    return {
        "case_id": row["case_id"],
        "input_type": row["input_type"],
        "script": row["script"],
        "user_brief": row["user_brief"],
        "assets": _loads(row["assets_json"], []),
        "initial_style": _loads(row["initial_style_json"], {}),
        "initial_storyboard": _loads(row["initial_storyboard_json"], {}),
        "draft_artifact": _loads(row["draft_artifact_json"], {}),
        "revisions": _loads(row["revisions_json"], []),
        "final_artifact": _loads(row["final_artifact_json"], {}),
        "feedback_liked": (
            None if row["feedback_liked"] is None else bool(row["feedback_liked"])
        ),
        "feedback_note": row["feedback_note"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_cases(liked: Optional[bool] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """列出 CreationCase。liked=None 全部；True/False 过滤。"""
    init_db()
    with _conn() as conn:
        if liked is None:
            rows = conn.execute(
                "SELECT case_id, input_type, feedback_liked, created_at, updated_at "
                "FROM creation_cases ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT case_id, input_type, feedback_liked, created_at, updated_at "
                "FROM creation_cases WHERE feedback_liked=? "
                "ORDER BY created_at DESC LIMIT ?",
                (int(liked), limit),
            ).fetchall()
    return [dict(r) for r in rows]


def get_style_dna(style_id: str = "user-personal") -> Optional[Dict[str, Any]]:
    """读回 Style DNA。"""
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM style_dna WHERE style_id=?", (style_id,)
        ).fetchone()
    if row is None:
        return None
    return _row_to_style_dna_dict(row)


def list_preferences(
    signal: Optional[str] = None,
    field_name: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """列出 preferences（prefer/avoid 清单）。"""
    init_db()
    clauses = []
    params: List[Any] = []
    if signal:
        clauses.append("signal=?")
        params.append(signal)
    if field_name:
        clauses.append("field=?")
        params.append(field_name)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT case_id, field, value_from, value_to, normalized, signal, "
            f"created_at FROM preferences{where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]
