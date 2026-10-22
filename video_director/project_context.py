"""Unified project context for the Director, Studio and external coding agents.

CompositionIR is authoritative only before a film document is initialized. The
presence of film.html switches the project to FilmDocument authority, including
when film.html is malformed: context inspection must never fall back to stale IR.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from video_director import config, editor
from video_director.editor import load_composition


def _ratio_of(w: int, h: int) -> str:
    if w <= 0 or h <= 0:
        return "unknown"
    r = w / h
    if abs(r - 16 / 9) < 0.1:
        return "16:9"
    if abs(r - 9 / 16) < 0.1:
        return "9:16"
    if abs(r - 1.0) < 0.1:
        return "1:1"
    return f"{w}x{h}"


def _provider_availability() -> Dict[str, List[str]]:
    """Report configured providers only; never infer readiness from missing keys."""
    import os
    from video_director import model_config as mc

    providers: Dict[str, List[str]] = {
        "tts": [], "stock": [], "video_generation": [], "image": [],
    }
    try:
        import edge_tts  # noqa: F401
        providers["tts"].append("edge-tts")
    except Exception:
        pass
    for name, env in (("pexels", "PEXELS_API_KEY"), ("pixabay", "PIXABAY_API_KEY"),
                      ("coverr", "COVERR_API_KEY")):
        if os.environ.get(env):
            providers["stock"].append(name)
    for key, slot in (("video", "video_generation"), ("image", "image")):
        if mc.is_role_enabled(key):
            cfg = mc.get_role_config(key)
            providers[slot].append(cfg.get("model") or key)
    return providers


def _empty_mpt_context() -> dict:
    return {
        "material_intents": [],
        "material_usage": [],
        "build_fingerprint": "",
        "creative_candidates": [],
        "source_usage_policy": {},
    }


def _mpt_context(project_id: str) -> dict:
    """Read optional planning ledgers independently so one bad file cannot hide others."""
    from pathlib import Path

    base = config.composer_storage_root() / project_id
    result = _empty_mpt_context()
    paths = {
        "material_intents": (base / "plans" / "material_intents.json", "scenes"),
        "material_usage": (base / "plans" / "material_usage.json", None),
        "build_fingerprint": (base / "plans" / "mpt_build_fingerprint.json", "fingerprint"),
        "creative_candidates": (base / "plans" / "creative_candidates.json", "scenes"),
        "source_usage_policy": (base / "plans" / "source_usage_policy.json", None),
    }
    for key, (path, member) in paths.items():
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if member:
                value = value.get(member) if isinstance(value, dict) else None
            if key in {"material_usage"}:
                result[key] = value if isinstance(value, list) else []
            elif key == "source_usage_policy":
                result[key] = value if isinstance(value, dict) else {}
            elif key == "build_fingerprint":
                result[key] = str(value or "")
            else:
                result[key] = value if isinstance(value, list) else []
        except Exception:
            # Context inspection is read-only. A malformed optional ledger is an
            # explicit empty context field, not grounds to hide the current project.
            continue
    return result


def _normalize_selection(selection: Optional[Dict[str, Any]]) -> dict:
    if not isinstance(selection, dict):
        return {"type": None, "ids": []}
    value = dict(selection)
    if "ids" not in value:
        current_id = value.get("id")
        value["ids"] = [current_id] if current_id not in (None, "") else []
    return value


def _invalid_film_context(project_id: str, selection: Optional[Dict[str, Any]], error: Exception) -> dict:
    """Return a fail-closed context for a present but unreadable film.html."""
    from video_director import film_document
    from video_director.project_lock import validate_project_id

    try:
        pid = validate_project_id(project_id)
        path = config.composer_storage_root() / pid / film_document.FILM_NAME
        if not path.is_file():
            return {}
        raw = path.read_text(encoding="utf-8")
        revision = film_document.revision_of(raw)
    except Exception:
        return {}

    return {
        "authority": "film.html",
        "revision": revision,
        "project": {"id": project_id, "duration": None, "ratio": "unknown",
                    "width": None, "height": None},
        "selection": _normalize_selection(selection),
        "timeline": {"tracks": [], "scene_count": 0, "clip_count": 0, "valid": False},
        "assets": {"video": 0, "image": 0, "audio": 0},
        "activeFeatures": [],
        "providers": _provider_availability(),
        "mpt": _mpt_context(project_id),
        "film": {
            "exists": True, "valid": False, "revision": revision,
            "error": {
                "code": getattr(error, "code", "FILM_CONTEXT_ERROR"),
                "message": str(error),
            },
        },
    }


def _active_film_context(project_id: str, selection: Optional[Dict[str, Any]]) -> Optional[dict]:
    """Read FilmDocument first and never leak stale CompositionIR as current state."""
    from video_director import film_document
    from video_director.project_lock import validate_project_id

    try:
        state = film_document.read_project_film(project_id)
    except Exception as exc:
        return _invalid_film_context(project_id, selection, exc)
    if not state.get("exists"):
        return None

    document = state["doc"]
    tracks = document.get("tracks") or []
    track_summaries = []
    counts = {"video": 0, "image": 0, "audio": 0}
    active_features: List[str] = []
    flat_clips = []
    visual_clip_count = 0
    known_ends = []

    for track in tracks:
        attrs = track.get("attrs") or {}
        track_id = str(track.get("id") or "")
        label = str(attrs.get("data-label") or attrs.get("aria-label") or track_id)
        track_clips = []
        for clip in track.get("clips") or []:
            clip_attrs = clip.get("attrs") or {}
            tag = str(clip.get("tag") or "unknown").lower()
            clip_id = str(clip.get("id") or "")
            try:
                at = float(clip_attrs.get("at", 0) or 0)
            except (TypeError, ValueError):
                at = 0.0
            try:
                duration = film_document.clip_duration(clip)
            except Exception:
                duration = None
            if duration is not None and math.isfinite(float(duration)) and duration >= 0:
                duration = float(duration)
                end = at + duration
                known_ends.append(end)
            else:
                duration = None
            summary = {
                "id": clip_id,
                "track_id": track_id,
                "type": tag,
                "at": round(at, 6),
                "duration": round(duration, 6) if duration is not None else None,
                "title": str(clip_attrs.get("title") or clip_attrs.get("data-role") or clip_id),
                "src": str(clip_attrs.get("src") or ""),
            }
            track_clips.append(summary)
            flat_clips.append(summary)
            if tag == "video":
                counts["video"] += 1
                visual_clip_count += 1
            elif tag == "img":
                counts["image"] += 1
                visual_clip_count += 1
            elif tag == "iframe":
                visual_clip_count += 1
            elif tag == "audio":
                counts["audio"] += 1

        track_summaries.append({
            "id": track_id,
            "label": label,
            "locked": "locked" in attrs,
            "hidden": "hidden" in attrs,
            "muted": "muted" in attrs,
            "sync_locked": "sync-lock" in attrs,
            "clip_count": len(track_clips),
            "clips": track_clips[:200],
            "clips_truncated": len(track_clips) > 200,
        })
        folded = label.casefold()
        if any(token in folded for token in ("bgm", "music", "配乐", "背景音乐")):
            active_features.append("bgm")
        if any(token in folded for token in ("original", "原声")):
            active_features.append("original_audio")
        if any(token in folded for token in ("subtitle", "caption", "字幕")):
            active_features.append("subtitle")

    stage = document.get("stage") or {}
    width, height = int(stage.get("width") or 0), int(stage.get("height") or 0)
    duration = max(known_ends) if known_ends else None
    revision = str(state.get("rev") or "")
    return {
        "authority": "film.html",
        "revision": revision,
        "project": {
            "id": project_id,
            "duration": round(duration, 6) if duration is not None else None,
            "ratio": _ratio_of(width, height),
            "width": width,
            "height": height,
        },
        "selection": _normalize_selection(selection),
        "timeline": {
            "tracks": [item["label"] for item in track_summaries],
            "scene_count": visual_clip_count,
            "clip_count": len(flat_clips),
            "valid": True,
        },
        "assets": counts,
        "activeFeatures": list(dict.fromkeys(active_features)),
        "providers": _provider_availability(),
        "mpt": _mpt_context(project_id),
        "film": {
            "exists": True,
            "valid": True,
            "revision": revision,
            "fps": int(document.get("fps") or config.DEFAULT_FPS),
            "stage": {"width": width, "height": height},
            "track_count": len(track_summaries),
            "clip_count": len(flat_clips),
            "tracks": track_summaries,
            "clips": flat_clips[:500],
            "clips_truncated": len(flat_clips) > 500,
        },
    }


def inspect_project_context(project_id: str, selection: Optional[Dict[str, Any]] = None) -> dict:
    """Return one revision-pinned context; film.html is checked before CompositionIR."""
    film_context = _active_film_context(project_id, selection)
    if film_context is not None:
        return film_context

    try:
        ir = load_composition(project_id)
    except Exception:
        return {}

    video = sum(1 for sc in ir.scenes for layer in sc.layers if layer.type.value == "video")
    image = sum(1 for sc in ir.scenes for layer in sc.layers if layer.type.value == "image")
    audio_count = len(ir.audio)

    track_types: List[str] = []
    for track in ir.audio:
        name = {
            "narration": "Narration", "original": "OriginalAudio", "bgm": "BGM",
            "sfx": "SFX", "ambient": "Ambient", "ambience": "Ambience",
            "dialogue": "Dialogue",
        }.get(track.type.value, track.type.value)
        if name not in track_types:
            track_types.append(name)
    if any(layer.type.value in ("video", "image")
           for scene in ir.scenes for layer in scene.layers):
        if "MainVisual" not in track_types:
            track_types.append("MainVisual")
    if "Subtitle" not in track_types:
        track_types.append("Subtitle")

    version = None
    try:
        version = int(editor.composition_version(project_id))
    except Exception:
        try:
            version = len(editor.load_revisions(project_id)) + 1
        except Exception:
            version = None

    active: List[str] = []
    if any(track.type.value == "original" for track in ir.audio):
        active.append("original_audio")
    if any(track.type.value == "bgm" for track in ir.audio):
        active.append("bgm")
    if any(scene.selections for scene in ir.scenes):
        active.append("subtitle")

    return {
        "authority": "composition.json",
        "revision": version,
        "composition_version": version,
        "project": {
            "id": project_id,
            "duration": round(ir.project.duration, 2),
            "ratio": _ratio_of(ir.project.width, ir.project.height),
            "width": ir.project.width,
            "height": ir.project.height,
        },
        "selection": _normalize_selection(selection),
        "timeline": {"tracks": track_types, "scene_count": len(ir.scenes)},
        "assets": {"video": video, "image": image, "audio": audio_count},
        "activeFeatures": active,
        "providers": _provider_availability(),
        "mpt": _mpt_context(project_id),
        "film": {"exists": False, "valid": None, "revision": None},
    }
