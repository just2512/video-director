"""Agent Gateway context backed by the shared Director/Studio project context."""
from __future__ import annotations

from typing import Dict, Optional

from video_director import project_context as shared_project_context, store
from video_director.capability_registry import list_capabilities


def project_context_snapshot(project_id: str,
                             selection: Optional[Dict] = None) -> dict:
    """Expose shared project authority/revision alongside the agent capability list."""
    try:
        stage = store.get_stage(project_id)
    except Exception:  # noqa: BLE001 未登记项目诚实返回空值
        stage = None

    context = shared_project_context.inspect_project_context(project_id, selection)
    caps = list_capabilities()
    names = sorted(caps.keys() if isinstance(caps, dict) else
                   [c["name"] if isinstance(c, dict) else str(c) for c in caps])

    authority = context.get("authority")
    is_film = authority == "film.html"
    timeline = context.get("timeline") or {}
    return {
        "project_id": project_id,
        "stage": stage,
        "scene_count": int(timeline.get("scene_count") or 0),
        "composition_version": context.get("composition_version") if not is_film else None,
        "selection": context.get("selection", selection),
        "capabilities": names,
        "authority": authority,
        "revision": context.get("revision"),
        "project": context.get("project"),
        "timeline": timeline,
        "assets": context.get("assets"),
        "film": context.get("film"),
    }
