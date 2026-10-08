"""Artifact Dependency Graph + Selective Rebuild。

Composition IR 是源；渲染/预览/旁白等是 build artifacts。
本模块只做两件事：
1. 用稳定指纹描述 artifact 的真实输入依赖；
2. 下一次构建时，只复用指纹未变化且文件仍存在的 artifact。

不把「stale」当成永久真相：stale 是编辑事件留下的提示；真正是否可复用，
以当前 IR + 当前运行参数重新计算出的 fingerprint 为准。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from video_director import config, mpt_semantics


VERSION = 1

FRESH = "FRESH"
STALE = "STALE"
MISSING = "MISSING"


def manifest_path(project_id: str) -> Path:
    return config.project_dir(project_id) / "plans" / "artifact_manifest.json"


def load_manifest(project_id: str) -> dict:
    path = manifest_path(project_id)
    if not path.is_file():
        return {"version": VERSION, "project_id": project_id, "artifacts": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": VERSION, "project_id": project_id, "artifacts": {}}
    if not isinstance(value, dict) or not isinstance(value.get("artifacts"), dict):
        return {"version": VERSION, "project_id": project_id, "artifacts": {}}
    value.setdefault("version", VERSION)
    value.setdefault("project_id", project_id)
    return value


def save_manifest(project_id: str, manifest: dict) -> str:
    path = manifest_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def _file_identity(path: Optional[str]) -> dict:
    if not path:
        return {"path": "", "exists": False}
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return {"path": str(p), "exists": False}
    return {
        "path": str(p),
        "exists": True,
        "size": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
    }


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "value") and not isinstance(value, (str, bytes, bytearray)):
        return value.value
    return value


def _asset_signature(asset: Any) -> dict:
    data = _jsonable(asset)
    if not isinstance(data, dict):
        return {"value": data}
    return {
        "id": data.get("id"),
        "type": data.get("type"),
        "file": _file_identity(data.get("file")),
        "duration": data.get("duration"),
        "width": data.get("width"),
        "height": data.get("height"),
        "fps": data.get("fps"),
        "prompt": data.get("prompt"),
        "description": data.get("description"),
        "keywords": data.get("keywords") or [],
        "source": data.get("source"),
        "provenance": data.get("provenance"),
        "rights_status": data.get("rights_status"),
    }


def _scene_visual_payload(scene: Any) -> dict:
    data = _jsonable(scene)
    if not isinstance(data, dict):
        return {"value": data}

    # scene render 不依赖绝对 timeline start、scene id、纯语义候选选择及转场；
    # 这些变化由上层 visual/final artifact 处理，避免移动场景导致所有镜头重算。
    data.pop("id", None)
    data.pop("start", None)
    data.pop("text", None)
    data.pop("intent", None)
    data.pop("visual_intent", None)
    data.pop("selected_candidate_id", None)
    data.pop("selections", None)
    data.pop("transition_out", None)
    data.pop("continuity_in", None)
    data.pop("continuity_out", None)
    data.pop("semantic_role", None)
    data.pop("beats", None)
    return data


def _asset_ids_for_scene(scene: Any) -> list[str]:
    out = []
    seen = set()
    for layer in getattr(scene, "layers", []) or []:
        aid = str(getattr(layer, "asset_id", "") or "")
        if aid and aid not in seen:
            seen.add(aid)
            out.append(aid)
    return out


def _runtime(runtime: Optional[Mapping[str, Any]]) -> dict:
    raw = dict(runtime or {})
    return {
        "width": raw.get("width"),
        "height": raw.get("height"),
        "fps": raw.get("fps"),
        "kind": raw.get("kind") or "final",
        "subtitle_mode": raw.get("subtitle_mode") or "plain",
        "title_card": _file_identity(raw.get("title_card")),
        "narration_file": _file_identity(raw.get("narration_file")),
        "subtitle_file": _file_identity(raw.get("subtitle_file")),
        "lower_third_name": raw.get("lower_third_name") or "",
        "lower_third_role": raw.get("lower_third_role") or "",
        "watermark": raw.get("watermark") or "",
        "profile": raw.get("profile") or "",
    }


def build_graph(ir: Any, assets: Mapping[str, Any], runtime: Optional[Mapping[str, Any]] = None) -> dict:
    """计算当前 Composition 对应的 artifact dependency graph。"""
    rt = _runtime(runtime)
    render_settings = {
        "width": getattr(getattr(ir, "project", None), "width", rt["width"]),
        "height": getattr(getattr(ir, "project", None), "height", rt["height"]),
        "fps": getattr(getattr(ir, "project", None), "fps", rt["fps"]),
        "fit_mode": rt.get("fit_mode"),
        "kind": rt["kind"],
    }
    asset_sigs = {str(k): _asset_signature(v) for k, v in assets.items()}

    nodes: Dict[str, dict] = {}
    scene_ids = []
    for scene in getattr(ir, "scenes", []) or []:
        sid = str(getattr(scene, "id", "") or "")
        if not sid:
            continue
        scene_ids.append(sid)
        asset_ids = _asset_ids_for_scene(scene)
        scene_payload = {
            "scene": _scene_visual_payload(scene),
            "render_settings": render_settings,
            "assets": {aid: asset_sigs.get(aid, {"id": aid, "missing": True})
                       for aid in asset_ids},
        }
        aid = f"scene:{sid}:render"
        nodes[aid] = {
            "id": aid,
            "kind": "scene_render",
            "depends_on": [f"asset:{x}" for x in asset_ids] + [f"scene:{sid}:composition"],
            "fingerprint": mpt_semantics.build_artifact_fingerprint(scene_payload),
        }

    transition_payload = []
    for scene in getattr(ir, "scenes", []) or []:
        transition_payload.append({
            "id": getattr(scene, "id", ""),
            "transition": _jsonable(getattr(scene, "transition_out", None)),
        })
    visual_inputs = {
        "scene_order": scene_ids,
        "scene_fingerprints": [nodes[f"scene:{sid}:render"]["fingerprint"] for sid in scene_ids],
        "transitions": transition_payload,
        "title_card": rt["title_card"],
        "width": render_settings["width"],
        "height": render_settings["height"],
        "fps": render_settings["fps"],
    }
    nodes["visual"] = {
        "id": "visual",
        "kind": "visual",
        "depends_on": [f"scene:{sid}:render" for sid in scene_ids],
        "fingerprint": mpt_semantics.build_artifact_fingerprint(visual_inputs),
    }

    audio_asset_ids = []
    for track in getattr(ir, "audio", []) or []:
        aid = str(getattr(track, "asset_id", "") or "")
        if aid and aid not in audio_asset_ids:
            audio_asset_ids.append(aid)
    audio_inputs = {
        "visual": nodes["visual"]["fingerprint"],
        "audio_tracks": [_jsonable(x) for x in getattr(ir, "audio", []) or []],
        "audio_assets": {aid: asset_sigs.get(aid, {"id": aid, "missing": True})
                         for aid in audio_asset_ids},
        "narration_file": rt["narration_file"],
    }
    nodes["master"] = {
        "id": "master",
        "kind": "master",
        "depends_on": ["visual"] + [f"asset:{x}" for x in audio_asset_ids],
        "fingerprint": mpt_semantics.build_artifact_fingerprint(audio_inputs),
    }

    final_inputs = {
        "master": nodes["master"]["fingerprint"],
        "subtitle_file": rt["subtitle_file"],
        "subtitle_mode": rt["subtitle_mode"],
        "subtitle_style": _jsonable(getattr(ir, "subtitle_style", None)),
        "watermarks": _jsonable(getattr(ir, "watermarks", None)),
        "lower_third_name": rt["lower_third_name"],
        "lower_third_role": rt["lower_third_role"],
    }
    nodes["final"] = {
        "id": "final",
        "kind": "final",
        "depends_on": ["master", "subtitle"] if rt["subtitle_file"]["path"] else ["master"],
        "fingerprint": mpt_semantics.build_artifact_fingerprint(final_inputs),
    }
    if rt["subtitle_file"]["path"]:
        nodes["subtitle"] = {
            "id": "subtitle",
            "kind": "subtitle",
            "depends_on": ["composition", "narration"],
            "fingerprint": mpt_semantics.build_artifact_fingerprint({
                "subtitle_file": rt["subtitle_file"],
                "subtitle_mode": rt["subtitle_mode"],
                "subtitle_style": _jsonable(getattr(ir, "subtitle_style", None)),
            }),
        }

    nodes["composition"] = {
        "id": "composition",
        "kind": "composition",
        "depends_on": [],
        "fingerprint": mpt_semantics.build_artifact_fingerprint({
            "composition": _jsonable(ir),
        }),
    }

    # Rewire scene nodes to depend on the composition contract after composition exists.
    for sid in scene_ids:
        nodes[f"scene:{sid}:render"]["depends_on"].append("composition")

    # final -> subtitle/narration dependencies are semantic even when the current renderer
    # receives an already-built subtitle file; the file identity fingerprint is the actual gate.
    if "subtitle" in nodes:
        nodes["subtitle"]["depends_on"] = ["composition", "narration"]
    nodes["narration"] = {
        "id": "narration",
        "kind": "narration",
        "depends_on": [],
        "fingerprint": mpt_semantics.build_artifact_fingerprint({
            "narration_file": rt["narration_file"],
            "scene_texts": [getattr(s, "text", "") for s in getattr(ir, "scenes", []) or []],
            "scene_durations": [getattr(s, "duration", 0.0) for s in getattr(ir, "scenes", []) or []],
        }),
    }
    return {"version": VERSION, "runtime": rt, "artifacts": nodes}


def _legacy_stale_ids(stale_artifacts: Iterable[str]) -> set[str]:
    out = set()
    for value in stale_artifacts or []:
        raw = str(value or "")
        if ":render" in raw and raw.endswith(":render") and not raw.startswith("audio:"):
            sid = raw[:-len(":render")]
            if sid and sid != "project":
                out.add(f"scene:{sid}:render")
        elif raw.endswith(":preview"):
            out.add("visual")
        elif raw.startswith("audio:"):
            out.update({"master", "final"})
        elif raw.startswith("subtitle:"):
            out.update({"subtitle", "final"})
        elif raw.startswith("project:") or raw == "project:render":
            out.update({"visual", "master", "final"})
        elif raw.startswith("watermark:"):
            out.add("final")
        elif raw == "composition":
            out.add("composition")
    return out


def mark_stale(project_id: str, stale_artifacts: Iterable[str]) -> None:
    """把 Editor 的语义 stale 事件反映到 manifest；下次构建仍以新指纹复核。"""
    manifest = load_manifest(project_id)
    ids = _legacy_stale_ids(stale_artifacts)
    changed = False
    now = time.time()
    for aid in ids:
        row = manifest["artifacts"].get(aid)
        if not row:
            continue
        row["status"] = STALE
        row["stale_at"] = now
        changed = True
    if changed:
        save_manifest(project_id, manifest)


def prepare_render(
    project_id: str,
    ir: Any,
    assets: Mapping[str, Any],
    runtime: Optional[Mapping[str, Any]] = None,
) -> tuple[dict, dict]:
    """返回 (当前 graph, 可直接复用的 scene→path 映射)。"""
    current = build_graph(ir, assets, runtime)
    previous = load_manifest(project_id)
    previous_nodes = previous.get("artifacts") or {}
    reuse: Dict[str, str] = {}
    for aid, node in current["artifacts"].items():
        prev = previous_nodes.get(aid) or {}
        path = prev.get("path") or ""
        if (
            aid.startswith("scene:")
            and prev.get("fingerprint") == node.get("fingerprint")
            and prev.get("status") == FRESH
            and path
            and Path(path).is_file()
        ):
            reuse[aid] = path
    return current, reuse


def record_render(
    project_id: str,
    graph: dict,
    *,
    scene_paths: Mapping[str, str],
    visual_path: str,
    master_path: str,
    final_path: str,
) -> str:
    """成功渲染后原子替换 manifest；失败时旧 manifest 保留。"""
    path_by_id = {
        "visual": visual_path,
        "master": master_path,
        "final": final_path,
    }
    manifest = {
        "version": VERSION,
        "project_id": project_id,
        "updated_at": time.time(),
        "runtime": graph.get("runtime") or {},
        "artifacts": {},
    }
    for aid, node in (graph.get("artifacts") or {}).items():
        item = dict(node)
        if aid in scene_paths:
            item["path"] = scene_paths[aid]
        elif aid in path_by_id:
            item["path"] = path_by_id[aid]
        else:
            item["path"] = ""
        item["status"] = FRESH if item["path"] and Path(item["path"]).is_file() else MISSING
        item["updated_at"] = time.time()
        manifest["artifacts"][aid] = item
    return save_manifest(project_id, manifest)


def summarize(graph: Mapping[str, Any], reuse: Mapping[str, str]) -> dict:
    scene_nodes = [x for x in (graph.get("artifacts") or {}).values()
                   if str(x.get("id", "")).startswith("scene:")]
    reused = [str(x.get("id")) for x in scene_nodes if str(x.get("id")) in reuse]
    rebuild = [str(x.get("id")) for x in scene_nodes if str(x.get("id")) not in reuse]
    return {
        "reused_artifacts": reused,
        "rebuild_artifacts": rebuild,
        "reused_count": len(reused),
        "rebuild_count": len(rebuild),
    }
