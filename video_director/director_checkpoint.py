"""Director checkpoint persistence for resumable MPT-style creation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

STATE_KEYS = (
    "script", "scene_texts", "scene_plan", "narration_file", "word_timeline", "narration_scenes",
    "video_files", "image_files", "assets", "asset_order", "assignments",
    "material_intents", "subtitle_file", "subtitle_entries", "bgm_asset_id",
    "bgm_volume", "composition_path", "manifest_path", "style_path",
    "transition_proposals_path", "material_usage_path", "build_fingerprint_path",
    "draft_path", "storyboard_path", "final_path", "qc_report",
)


def checkpoint_path(project_id: str, project_dir_factory) -> Path:
    return project_dir_factory(project_id) / "plans" / "director_checkpoint.json"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def save(project_id: str, state: Dict[str, Any], project_dir_factory) -> str:
    path = checkpoint_path(project_id, project_dir_factory)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {"version": 1}
    for key in STATE_KEYS:
        if key in state:
            payload[key] = _jsonable(state[key])

    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return str(path)


def load(project_id: str, project_dir_factory) -> dict:
    path = checkpoint_path(project_id, project_dir_factory)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def restore_into(project_id: str, state: Dict[str, Any], project_dir_factory) -> bool:
    """Restore completed-node state; missing/corrupt state is ignored honestly."""
    data = load(project_id, project_dir_factory)
    if not data:
        return False

    for key in STATE_KEYS:
        if key in data:
            state[key] = data[key]
    return True
