"""AI Director unified creation inputs.

All creation sources are first-class context for one Director:
brief/script/assets/reference/software-project evidence/constraints.
The bundle is persisted separately from the legacy creative_brief string so
source provenance and per-node I/O remain inspectable.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from video_director import asset_catalog, config


VERSION = "director-input-bundle-v1"

NODE_DEFS = [
    ("understand", "理解创作要求", "读取用户意图与全部来源上下文"),
    ("script", "文案", "确认用户文案或基于全部上下文生成文案"),
    ("scene_plan", "分镜", "根据文案、参考风格、软件证据规划场景"),
    ("narration", "配音", "把最终文案转为旁白/时间轴"),
    ("material_match", "画面", "优先采用用户素材，必要时补充可用素材"),
    ("subtitle", "字幕", "根据旁白时间轴生成字幕"),
    ("music", "音乐", "按内容与参考风格选择音乐"),
    ("watermark", "品牌", "应用水印/人物条等品牌约束"),
    ("composition", "时间线", "把脚本、画面、音频、字幕组合为 Composition"),
    ("preview", "预览", "生成可审片草稿或分镜产物"),
    ("verify", "预检", "重新读取项目并检查实际产物"),
    ("render", "成片", "在确认条件满足后输出最终视频"),
]


def _string(value: Any) -> str:
    return str(value or "").strip()


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for value in values:
        value = _string(value)
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _asset_ref(asset_id: str, kind: str = "", file_path: str = "") -> dict:
    entry = asset_catalog.get_asset(asset_id) if asset_id else None
    if entry:
        return {
            "asset_id": entry["asset_id"],
            "type": entry.get("type") or kind,
            "source": entry.get("source") or "unknown",
            "file": entry.get("file") or file_path,
            "description": entry.get("description") or "",
            "commercial_status": entry.get("commercial_status") or "UNKNOWN",
            "license_note": entry.get("license_note") or "",
        }
    return {
        "asset_id": asset_id or "",
        "type": kind or "unknown",
        "source": "path",
        "file": file_path,
        "description": "",
        "commercial_status": "UNKNOWN",
        "license_note": "",
    }


def _normalize_assets(spec: dict, source_assets: dict) -> dict:
    refs = []
    raw_refs = source_assets.get("asset_refs") or source_assets.get("assets") or []
    for item in raw_refs:
        if isinstance(item, str):
            refs.append(_asset_ref(item))
        elif isinstance(item, dict):
            aid = _string(item.get("asset_id") or item.get("id"))
            refs.append(_asset_ref(aid, item.get("type"), item.get("file")))
    # Legacy path fields are still accepted, but converted into the same Asset Pool.
    for p in spec.get("video_paths") or []:
        refs.append(_asset_ref("", "video", p))
    for p in spec.get("image_paths") or []:
        refs.append(_asset_ref("", "image", p))
    unique = []
    seen = set()
    for ref in refs:
        key = ref.get("asset_id") or ref.get("file")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return {
        "policy": _string(source_assets.get("policy") or "user_first"),
        "required": bool(source_assets.get("required", False)),
        "asset_refs": unique,
    }


def _normalize_reference(source: Any) -> List[dict]:
    if not source:
        return []
    values = source if isinstance(source, list) else [source]
    out = []
    for item in values:
        if not isinstance(item, dict):
            continue
        report = item.get("report") or item.get("analysis") or {}
        out.append({
            "id": _string(item.get("id") or item.get("reference_id")
                         or item.get("project_id") or f"reference-{len(out)+1}"),
            "project_id": _string(item.get("project_id")),
            "video": _string(item.get("video") or report.get("video")),
            "report_path": _string(item.get("report_path") or report.get("report_path")),
            "analysis": report,
            "apply": item.get("apply") or [
                "pace", "density", "motion", "transition", "shot_grammar"
            ],
            "provenance": "user_reference",
        })
    return out


def _normalize_project(source: Any) -> dict:
    if not isinstance(source, dict):
        return {}
    graph = source.get("evidence_graph") or source.get("graph") or {}
    claims = graph.get("claims") or source.get("claims") or []
    verified = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        if claim.get("status") not in ("VERIFIED", "verified"):
            continue
        verified.append({
            "id": claim.get("id"),
            "text": _string(claim.get("text")),
            "evidence_refs": claim.get("evidence_refs") or [],
        })
    evidence = graph.get("evidence") or source.get("evidence") or []
    evidence_rows = []
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        evidence_rows.append({
            "id": ev.get("id"),
            "source_path": _string(ev.get("source_path") or ev.get("path")),
            "evidence_type": _string(ev.get("evidence_type") or ev.get("type")),
        })
    return {
        "id": _string(source.get("id") or source.get("project_id") or source.get("root")),
        "root": _string(source.get("root")),
        "project_id": _string(source.get("project_id")),
        "snapshot": source.get("snapshot") or {},
        "evidence_graph": {
            "project_snapshot_id": graph.get("project_snapshot_id"),
            "claims": verified,
            "evidence": evidence_rows,
        },
        "usage": source.get("usage") or ["claims", "evidence"],
        "provenance": "user_software_project",
    }


def build_bundle(spec: dict) -> dict:
    """Normalize all creation sources into one DirectorInputBundle."""
    src = dict(spec.get("director_inputs") or {})
    raw_script = _string(src.get("script", {}).get("text")) if isinstance(src.get("script"), dict) else _string(src.get("script"))
    if not raw_script:
        raw_script = _string(spec.get("script"))
    script_source = "user" if raw_script else "director"
    script_input = src.get("script") if isinstance(src.get("script"), dict) else {}
    script_authority = (
        "authoritative" if raw_script and script_input.get("authority") != "generated"
        else "generated"
    )
    bundle = {
        "version": VERSION,
        "created_at": time.time(),
        "brief": _string(src.get("brief") or spec.get("brief") or spec.get("creative_brief")),
        "script": {
            "text": raw_script,
            "source": script_source,
            "authority": script_authority,
            "editable": True,
        },
        "assets": _normalize_assets(spec, src.get("assets") or {}),
        "references": _normalize_reference(src.get("references") or src.get("reference")),
        "software_project": _normalize_project(
            src.get("software_project") or src.get("repo")
        ),
        "constraints": dict(src.get("constraints") or {}),
        "creative_settings": {
            "platform": _string(src.get("platform") or spec.get("platform")),
            "duration": _string(src.get("duration") or spec.get("duration")),
            "style": _string(src.get("style") or spec.get("style") or spec.get("style_intent")),
        },
    }
    if bundle["assets"]["asset_refs"] and "visual_source" not in bundle["constraints"]:
        bundle["constraints"]["visual_source"] = spec.get("visual_source") or "auto"
    if "allow_stock" not in bundle["constraints"]:
        bundle["constraints"]["allow_stock"] = bundle["constraints"].get("visual_source", "auto") != "local"
    return bundle


def reference_style_intent(bundle: dict) -> str:
    """Turn structured reference analysis into a compact execution constraint."""
    hints = []
    for ref in bundle.get("references") or []:
        vl = ((ref.get("analysis") or {}).get("visual_language") or {})
        sg = (ref.get("analysis") or {}).get("shot_grammar") or []
        parts = []
        if vl.get("pace"): parts.append(f"节奏={vl['pace']}")
        if vl.get("density"): parts.append(f"密度={vl['density']}")
        if vl.get("motion"): parts.append(f"运动={vl['motion']}")
        if vl.get("transition"): parts.append(f"转场={vl['transition']}")
        if sg:
            durations = [str(x.get("duration", "")) for x in sg[:6] if x.get("duration") is not None]
            if durations:
                parts.append("镜头时长=" + "/".join(durations) + "s")
        if parts:
            hints.append("；".join(parts))
    return "；".join(hints)


def verified_project_claims(bundle: dict) -> List[str]:
    graph = bundle.get("software_project", {}).get("evidence_graph") or {}
    return [
        _string(c.get("text")) for c in graph.get("claims") or []
        if _string(c.get("text"))
    ]


def build_execution_brief(bundle: dict, user_brief: str = "") -> str:
    """Execution hint derived from structured sources; bundle remains the source of truth."""
    parts = [_string(user_brief) or _string(bundle.get("brief"))]
    settings = bundle.get("creative_settings") or {}
    if settings.get("platform"): parts.append(f"目标平台：{settings['platform']}")
    if settings.get("duration"): parts.append(f"目标时长：{settings['duration']} 秒")
    if settings.get("style"): parts.append(f"用户风格：{settings['style']}")

    assets = bundle.get("assets", {}).get("asset_refs") or []
    if assets:
        parts.append(
            "用户素材池："
            + "、".join(
                _string(a.get("description") or a.get("asset_id") or Path(a.get("file", "")).name)
                for a in assets[:24]
            )
            + "；优先使用用户素材"
        )

    ref_style = reference_style_intent(bundle)
    if ref_style:
        parts.append("参考视频风格约束（只借鉴风格，不复制内容）：" + ref_style)

    claims = verified_project_claims(bundle)
    if claims:
        parts.append(
            "软件项目 VERIFIED 卖点（只能使用这些已验证事实）："
            + "；".join(claims[:12])
        )

    project = bundle.get("software_project") or {}
    if project:
        root = _string(project.get("root"))
        if root:
            parts.append(f"软件项目证据根目录：{root}")

    return "；".join(p for p in parts if p)


def build_agent_scene_context(bundle: dict, script: str) -> dict:
    """Structured prompt context for the Director scene-planning step."""
    return {
        "goal": bundle.get("brief") or "",
        "script": script,
        "assets": bundle.get("assets") or {},
        "reference_style": [
            {
                "id": r.get("id"),
                "visual_language": (r.get("analysis") or {}).get("visual_language") or {},
                "shot_grammar": (r.get("analysis") or {}).get("shot_grammar") or [],
            }
            for r in bundle.get("references") or []
        ],
        "software_project": bundle.get("software_project") or {},
        "constraints": bundle.get("constraints") or {},
    }


def build_plan(bundle: dict, brief: str) -> dict:
    """Plan schema with explicit source refs, I/O, dependencies and verification slots."""
    sources = ["director:brief"]
    if bundle.get("script", {}).get("text"):
        sources.append("script:user")
    if bundle.get("assets", {}).get("asset_refs"):
        sources.append("asset_pool:user")
    if bundle.get("references"):
        sources.append("reference:style")
    if bundle.get("software_project"):
        sources.append("software_project:evidence")

    node_inputs = {
        "understand": sources,
        "script": sources,
        "scene_plan": ["script:main"] + [x for x in sources if x != "script:user"],
        "narration": ["script:main"],
        "material_match": ["scene_plan"] + [x for x in sources if x.startswith(("asset_pool", "reference:", "software_project:"))],
        "subtitle": ["narration:timeline"],
        "music": ["scene_plan", "reference:style"],
        "watermark": ["director:constraints"],
        "composition": ["scene_plan", "narration:timeline", "material_match:assets", "subtitle:track", "music:track", "director:constraints"],
        "preview": ["composition:main"],
        "verify": ["composition:main", "preview:draft"],
        "render": ["verify:report"],
    }
    dependencies = {
        "script": ["understand"], "scene_plan": ["script"], "narration": ["script"],
        "material_match": ["scene_plan"], "subtitle": ["narration"],
        "music": ["scene_plan"], "watermark": ["scene_plan"], "composition": [
            "scene_plan", "narration", "material_match", "subtitle", "music", "watermark"
        ], "preview": ["composition"], "verify": ["composition", "preview"], "render": ["verify"],
    }
    node_outputs = {
        "understand": ["director:context"],
        "script": ["script:main"],
        "scene_plan": ["scene_plan:main"],
        "narration": ["narration:timeline"],
        "material_match": ["material_match:assets"],
        "subtitle": ["subtitle:track"],
        "music": ["music:track"],
        "watermark": ["watermark:applied"],
        "composition": ["composition:main"],
        "preview": ["preview:draft_or_storyboard"],
        "verify": ["verify:report"],
        "render": ["render:final"],
    }
    nodes = []
    for node_id, label, description in NODE_DEFS:
        nodes.append({
            "id": node_id,
            "label": label,
            "description": description,
            "status": "PENDING",
            "inputs": node_inputs.get(node_id, []),
            "outputs": node_outputs.get(node_id, []),
            "tool_calls": [],
            "dependencies": dependencies.get(node_id, []),
            "decision": {},
            "verify": {"status": "PENDING"},
            "artifacts": [],
            "error": "",
        })
    return {
        "version": VERSION,
        "goal": brief or bundle.get("brief") or "按全部用户输入完成视频",
        "input_bundle_version": VERSION,
        "source_refs": sources,
        "inputs": bundle,
        "nodes": nodes,
    }


def persist_bundle(project_id: str, bundle: dict) -> str:
    base = config.project_dir(project_id)
    path = base / "plans" / "director_inputs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def persist_plan(project_id: str, plan: dict) -> str:
    base = config.project_dir(project_id)
    path = base / "plans" / "director_plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def update_node(project_id: str, plan: dict, node_id: str, status: str,
                *, outputs: Optional[List[str]] = None,
                tool_calls: Optional[List[dict]] = None,
                decision: Optional[dict] = None,
                verify: Optional[dict] = None,
                artifacts: Optional[List[str]] = None,
                error: str = "") -> None:
    node = next((n for n in plan.get("nodes", []) if n.get("id") == node_id), None)
    if not node:
        return
    node["status"] = status
    if outputs is not None:
        node["outputs"] = outputs
    if tool_calls is not None:
        node["tool_calls"] = tool_calls
    if decision is not None:
        node["decision"] = decision
    if verify is not None:
        node["verify"] = verify
    if artifacts is not None:
        node["artifacts"] = artifacts
    if error:
        node["error"] = error
    persist_plan(project_id, plan)


def attach_library_assets(project_id: str, bundle: dict) -> tuple[list[str], list[str]]:
    """Resolve Asset Pool refs and write project references; return paths for compose_auto."""
    videos, images = [], []
    for ref in bundle.get("assets", {}).get("asset_refs") or []:
        aid = _string(ref.get("asset_id"))
        entry = asset_catalog.get_asset(aid) if aid else None
        file_path = _string((entry or {}).get("file") or ref.get("file"))
        if not file_path or not Path(file_path).is_file():
            continue
        if aid:
            try:
                asset_catalog.project_reference(project_id, aid)
            except Exception:
                pass
        kind = _string((entry or {}).get("type") or ref.get("type"))
        if kind == "video" or Path(file_path).suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
            videos.append(file_path)
        else:
            images.append(file_path)
    return _dedupe(videos), _dedupe(images)


def materialize_reference(project_id: str, bundle: dict) -> List[str]:
    """Keep reference video artifacts inside the target project for traceability."""
    copied = []
    import shutil
    target = config.project_dir(project_id) / "input" / "references"
    target.mkdir(parents=True, exist_ok=True)
    for ref in bundle.get("references") or []:
        source = _string(ref.get("video"))
        if not source or not Path(source).is_file():
            # Fall back to the report's source path if the analyzer returned only that.
            report_path = _string(ref.get("report_path"))
            if report_path and Path(report_path).is_file():
                try:
                    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
                    source = _string(report.get("video"))
                except Exception:
                    source = ""
        if source and Path(source).is_file():
            dst = target / Path(source).name
            try:
                shutil.copy2(source, dst)
                copied.append(str(dst))
            except OSError:
                continue
    return copied


def materialize_software_project_artifacts(project_id: str, bundle: dict) -> List[str]:
    """Copy small, explicitly referenced evidence screenshots/assets when they exist."""
    copied = []
    project = bundle.get("software_project") or {}
    root = _string(project.get("root"))
    if not root or not Path(root).is_dir():
        return copied
    target = config.project_dir(project_id) / "input" / "software_project"
    target.mkdir(parents=True, exist_ok=True)
    evidence = (project.get("evidence_graph") or {}).get("evidence") or []
    import shutil
    for ev in evidence[:40]:
        rel = _string(ev.get("source_path"))
        if not rel:
            continue
        src = Path(root) / rel
        if not src.is_file() or src.stat().st_size > 8 * 1024 * 1024:
            continue
        # Only copy obvious visual/product evidence assets; text remains in the evidence graph.
        if src.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        dst = target / Path(rel).name
        try:
            shutil.copy2(src, dst)
            copied.append(str(dst))
        except OSError:
            continue
    return copied


def load_bundle(project_id: str) -> dict:
    path = config.project_dir(project_id) / "plans" / "director_inputs.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
