"""AI Director creation Agent loop.

Every creation node is an independent execution unit with Tool Result and Verify.
Creation never calls pipeline.compose_auto as one opaque operation.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Dict, List, Tuple

from loguru import logger

from video_director import audio, assets as assets_mod, config, matcher, narration
from video_director import planner as planner_mod
from video_director import renderer, style as style_mod, subtitle as subtitle_mod
from video_director import validator, qc as qc_mod


def _json_file(path: Path, value) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _file_ok(path: str) -> bool:
    p = Path(path)
    return p.is_file() and p.stat().st_size > 0


def _strip_code_fence(text: str) -> str:
    fence = chr(96) * 3
    raw = (text or "").strip()
    if raw.startswith(fence):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit(fence, 1)[0]
    return raw.strip()


def _scene_texts(script: str) -> List[str]:
    import re
    parts = [x.strip() for x in re.split(r"[。！？!?
]+", script or "") if x.strip()]
    return [x + ("。" if not x.endswith(("。", "！", "？", "!", "?")) else "") for x in parts]


def _ready_nodes(plan: dict) -> List[str]:
    by_id = {n.get("id"): n for n in plan.get("nodes") or []}
    out = []
    for n in plan.get("nodes") or []:
        if n.get("status") != "PENDING":
            continue
        if all(by_id.get(d, {}).get("status") in ("DONE", "SKIPPED")
               for d in n.get("dependencies") or []):
            out.append(n["id"])
    return out


def _choose_next(plan: dict, current: str, result: dict, retries: int) -> Tuple[str, str]:
    ready = _ready_nodes(plan)
    try:
        from video_director import llm_provider
        provider = llm_provider.get_llm()
    except Exception:
        provider = None
    if provider is not None:
        prompt = (
            "你是视频 AI Director 调度器。节点执行完成后，根据真实 Tool Result 选择下一节点。"
            '只能从 ready 中选择，或者 retry，或者 STOP。只输出 JSON：'
            '{"next":"节点ID|retry|STOP","reason":"一句话"}\n'
            + "current=" + current + "\n"
            + "result=" + json.dumps(result, ensure_ascii=False, default=str)[:7000] + "\n"
            + "ready=" + json.dumps(ready, ensure_ascii=False) + "\n"
            + "retries=" + str(retries)
        )
        try:
            raw = _strip_code_fence(provider.generate(
                prompt, system="只做节点调度，不修改项目，不虚构结果。"))
            a, b = raw.find("{"), raw.rfind("}")
            if a >= 0 and b > a:
                data = json.loads(raw[a:b + 1])
                choice = str(data.get("next") or "").strip()
                reason = str(data.get("reason") or "").strip()
                if choice in ready:
                    return choice, reason or "根据 Tool Result 继续"
                if choice == "retry" and retries < 2:
                    return "retry", reason or "重试当前节点"
                if choice == "STOP":
                    return "STOP", reason or "Director 停止"
        except Exception as exc:
            logger.warning("Director next-node decision failed: %s", exc)
    if not result.get("ok", True):
        return ("retry", "Tool Result 未通过，重试当前节点") if retries < 2 else ("STOP", "节点连续失败")
    return (ready[0], "按依赖图继续") if ready else ("STOP", "没有可继续节点")


def _tool_script(spec: dict, state: dict) -> dict:
    bundle = state["bundle"]
    s = (bundle.get("script") or {}).get("text") or ""
    if not (s and (bundle.get("script") or {}).get("authority") == "authoritative"):
        from video_director import llm_provider, director_inputs as di
        provider = llm_provider.get_llm()
        if provider is None:
            raise RuntimeError("没有用户文案且没有可用文本大模型")
        raw = provider.generate(
            "根据目标及全部结构化来源写可直接配音的中文视频文案。"
            "软件项目只能使用 VERIFIED 事实，参考视频只作风格约束。\n"
            + json.dumps(di.build_agent_scene_context(bundle, ""), ensure_ascii=False),
            system="只输出正文，不要标题和解释。",
        )
        s = _strip_code_fence(raw)
        if not s:
            raise RuntimeError("文案节点返回为空")
        bundle["script"]["text"] = s
        bundle["script"]["source"] = "director"
        bundle["script"]["authority"] = "generated"
        _json_file(config.project_dir(state["project_id"]) / "plans" / "director_inputs.json", bundle)
    state["script"] = s
    state["scene_texts"] = _scene_texts(s)
    path = config.project_dir(state["project_id"]) / "input" / "script.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(s, encoding="utf-8")
    return {
        "ok": bool(s),
        "tool": "content.generate_script" if bundle["script"]["source"] == "director" else "script.confirm",
        "status": "APPLIED",
        "result": {"script": str(path)},
        "verify": {"ok": bool(s), "checks": [{"id": "script_present", "ok": bool(s)}]},
    }


def _tool_scene_plan(spec: dict, state: dict) -> dict:
    from video_director import director_inputs as di
    provider = None
    try:
        from video_director import llm_provider
        provider = llm_provider.get_llm()
    except Exception:
        pass
    if provider is None:
        data = {
            "source": "rule",
            "scenes": [{"text": t, "visual_intent": t, "source_preference": "user_asset"}
                       for t in state["scene_texts"]],
        }
    else:
        raw = provider.generate(
            "请为视频设计可执行分镜。必须同时考虑用户素材、参考视频风格、"
            "软件项目 VERIFIED 事实。参考视频只借鉴镜头语言，不复制内容。"
            '只输出 JSON：{"scenes":[{"text":"文案","visual_intent":"画面意图",'
            '"source_preference":"user_asset|reference_style|software_evidence|stock|generated"}]}\n'
            + json.dumps(di.build_agent_scene_context(state["bundle"], state["script"]),
                         ensure_ascii=False, indent=2),
            system="你是视频 Director，只输出合法 JSON。",
        )
        raw = _strip_code_fence(raw)
        a, b = raw.find("{"), raw.rfind("}")
        if a < 0 or b <= a:
            raise RuntimeError("分镜节点没有返回合法 JSON")
        data = json.loads(raw[a:b + 1])
        if not isinstance(data, dict) or not isinstance(data.get("scenes"), list):
            raise RuntimeError("分镜节点缺少 scenes")
    state["scene_plan"] = data
    path = _json_file(config.project_dir(state["project_id"]) / "plans" / "director_scene_plan.json", data)
    ok = Path(path).is_file() and bool(data.get("scenes"))
    return {
        "ok": ok, "tool": "scene.plan", "status": "APPLIED",
        "result": {"scene_plan": path, "scene_count": len(data.get("scenes") or [])},
        "verify": {"ok": ok, "checks": [{"id": "scene_plan", "ok": ok}]},
    }


def _tool_narration(spec: dict, state: dict) -> dict:
    out_dir = config.project_dir(state["project_id"]) / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path, words, scenes = narration.synth_narration(
        state["scene_texts"],
        spec.get("voice") or "zh-CN-XiaoxiaoNeural",
        out_dir,
        rate=spec.get("voice_rate") or "+0%",
    )
    state["narration_file"] = audio_path
    state["word_timeline"] = words
    state["narration_scenes"] = scenes
    p1 = _json_file(config.project_dir(state["project_id"]) / "plans" / "alignment" / "words.json", words)
    p2 = _json_file(config.project_dir(state["project_id"]) / "plans" / "scenes.json", scenes)
    voice = spec.get("voice") or "zh-CN-XiaoxiaoNeural"
    checks = [
        {"id": "audio", "ok": _file_ok(audio_path)},
        {"id": "scenes", "ok": bool(scenes)},
        {"id": "words", "ok": bool(words) or voice in ("", "silent")},
        {"id": "alignment_file", "ok": _file_ok(p1)},
        {"id": "scene_file", "ok": _file_ok(p2)},
    ]
    return {
        "ok": all(x["ok"] for x in checks),
        "tool": "narration.synthesize",
        "status": "APPLIED",
        "result": {"audio": audio_path, "words": p1, "scenes": p2},
        "verify": {"ok": all(x["ok"] for x in checks), "checks": checks},
    }


def _tool_material(spec: dict, state: dict) -> dict:
    pid = state["project_id"]
    base = config.project_dir(pid)
    videos = list(state.get("video_files") or [])
    images = list(state.get("image_files") or [])
    metadata: Dict[str, dict] = {}
    warnings: List[str] = []
    llm_call = None
    try:
        from video_director import llm_provider
        provider = llm_provider.get_llm()
        if provider is not None:
            llm_call = lambda prompt: provider.generate(prompt)
    except Exception:
        pass

    vv, vo = assets_mod.build_manifest(videos, [], asset_metadata=metadata) if videos else ({}, [])
    ii, io = assets_mod.build_manifest([], images, asset_metadata=metadata) if images else ({}, [])
    all_assets = {**vv, **ii}
    order = vo + io

    assignments = {}
    if all_assets and state.get("narration_scenes"):
        try:
            assignments = matcher.match_assets(state["narration_scenes"], all_assets, llm_call) or {}
        except Exception as exc:
            warnings.append("本地素材匹配失败：" + str(exc))

    missing = [s["id"] for s in state.get("narration_scenes") or [] if s["id"] not in assignments]
    local_only = state.get("constraints", {}).get("visual_source") == "local"
    allow_stock = bool(state.get("constraints", {}).get("allow_stock", True))

    if missing and allow_stock and not local_only:
        try:
            from video_director import stock
            ratio = str(spec.get("width") or config.DEFAULT_RESOLUTION[0]) + "x" + str(
                spec.get("height") or config.DEFAULT_RESOLUTION[1])
            targets = [s for s in state["narration_scenes"] if s["id"] in missing]
            sr = stock.auto_download_for_scenes(
                [str(s.get("text", "")) for s in targets],
                base,
                target_ratio=ratio,
                llm_call=llm_call,
                per_scene=1,
                providers=["pexels", "pixabay"],
                scene_ids=[str(s.get("id") or "") for s in targets],
            )
            stock_files = list(sr.get("files") or [])
            metadata.update(sr.get("metadata") or {})
            warnings.extend(sr.get("warnings") or [])
            videos.extend([x for x in stock_files if x not in videos and x not in images])
            vv, vo = assets_mod.build_manifest(videos, [], asset_metadata=metadata) if videos else ({}, [])
            ii, io = assets_mod.build_manifest([], images, asset_metadata=metadata) if images else ({}, [])
            all_assets, order = {**vv, **ii}, vo + io
            assignments = matcher.match_assets(state["narration_scenes"], all_assets, llm_call)
        except Exception as exc:
            warnings.append("网络素材补足失败：" + str(exc))

    if not all_assets:
        return {
            "ok": False, "tool": "material.match", "status": "MISSING_REQUIREMENT",
            "error": "没有可用视觉素材", "result": {"warnings": warnings},
            "verify": {"ok": False, "checks": [{"id": "asset_pool", "ok": False}]},
        }

    if local_only and any(s["id"] not in assignments for s in state["narration_scenes"]):
        return {
            "ok": False, "tool": "material.match", "status": "MISSING_REQUIREMENT",
            "error": "仅本地素材模式下存在未匹配场景",
            "result": {"assignments": assignments, "warnings": warnings},
            "verify": {"ok": False},
        }

    state.update({
        "video_files": videos,
        "assets": all_assets,
        "asset_order": order,
        "assignments": assignments,
    })
    path = _json_file(base / "assets" / "match_assignments.json", assignments)
    coverage_ok = all(s["id"] in assignments for s in state["narration_scenes"]) if any(
        a.type == "video" for a in all_assets.values()
    ) else True
    checks = [
        {"id": "asset_pool", "ok": bool(all_assets)},
        {"id": "assignment_file", "ok": Path(path).is_file()},
        {"id": "scene_coverage", "ok": coverage_ok},
    ]
    return {
        "ok": all(x["ok"] for x in checks),
        "tool": "material.match",
        "status": "APPLIED",
        "result": {"asset_count": len(all_assets), "assignments": assignments, "warnings": warnings},
        "verify": {"ok": all(x["ok"] for x in checks), "checks": checks},
    }
