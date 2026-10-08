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

from video_director import audio, assets as assets_mod, config, matcher, narration, mpt_semantics
from video_director import planner as planner_mod
from video_director import renderer, style as style_mod, subtitle as subtitle_mod
from video_director import validator, qc as qc_mod
from video_director import vision as vision_mod


class DirectorAgentError(Exception):
    """Agent Loop 终止性错误（STOP/死循环/未恢复失败节点）。

    2026-10-07 教训：本类此前从未定义，execute() 的 STOP/retry 路径 raise
    NameError 自掩盖真实失败原因（如 edge-tts Invalid rate），把可诊断的
    参数错误变成裸 NameError。
    """


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
    # 保留 MPT pause markup 到 Narration 节点；视觉规划/素材检索在消费时再清除。
    raw_script = script or ""
    parts = [x.strip() for x in re.split(r"[。！？!?\n]+", raw_script) if x.strip()]
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
    try:
        from video_director import store
        store.update_script(state["project_id"], s)
    except Exception as exc:
        logger.warning("Director script store update failed: %s", exc)
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
            "scenes": [{
                "text": t,
                "visual_intent": t,
                "source_preference": "user_asset",
                "search_terms": [mpt_semantics.remove_pause_markup(t)],
            } for t in state["scene_texts"]],
        }
    else:
        raw = provider.generate(
            "请为视频设计可执行分镜。必须同时考虑用户素材、参考视频风格、"
            "软件项目 VERIFIED 事实。参考视频只借鉴镜头语言，不复制内容。"
            "同时为每个场景生成 3-8 个按叙事顺序排列的 stock 搜索词；"
            "搜索词用于真实图库检索，不能脱离该场景含义。"
            '只输出 JSON：{"scenes":[{"text":"文案","visual_intent":"画面意图",'
            '"source_preference":"user_asset|reference_style|software_evidence|stock|generated",'
            '"search_terms":["按叙事顺序的检索词"]}]}\n'
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
    pid = state["project_id"]
    out_dir = config.project_dir(pid) / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    scene_texts = state["scene_texts"]
    voice_mode = spec.get("voice_mode") or "auto"
    uploaded = spec.get("narration_upload") or ""

    if uploaded:
        from video_director import voice_upload
        up = voice_upload.import_uploaded_voice(
            pid,
            uploaded,
            scene_texts=[mpt_semantics.remove_pause_markup(t) for t in scene_texts],
        )
        audio_path = up["narration_file"]
        words = up.get("word_timeline") or []
        scenes = up.get("narration_scenes") or []
        tool_name = "narration.upload"
    else:
        voice = "silent" if voice_mode == "silent" else (
            spec.get("voice") or "zh-CN-XiaoxiaoNeural"
        )
        audio_path, words, scenes = narration.synth_narration(
            scene_texts, voice, out_dir,
            rate=narration.normalize_rate(spec.get("voice_rate")),
        )
        tool_name = "narration.synthesize"

    state["narration_file"] = audio_path
    state["word_timeline"] = words
    state["narration_scenes"] = scenes
    p1 = _json_file(
        config.project_dir(pid) / "plans" / "alignment" / "words.json",
        words,
    )
    p2 = _json_file(
        config.project_dir(pid) / "plans" / "scenes.json",
        scenes,
    )
    silent_ok = voice_mode == "silent" and not uploaded
    checks = [
        {"id": "audio", "ok": _file_ok(audio_path)},
        {"id": "scenes", "ok": bool(scenes)},
        {"id": "words", "ok": bool(words) or silent_ok},
        {"id": "alignment_file", "ok": _file_ok(p1)},
        {"id": "scene_file", "ok": _file_ok(p2)},
    ]
    ok = all(x["ok"] for x in checks)
    return {
        "ok": ok,
        "tool": tool_name,
        "status": "APPLIED" if ok else "FAILED",
        "result": {"audio": audio_path, "words": p1, "scenes": p2,
                   "source": "upload" if uploaded else voice_mode},
        "verify": {"ok": ok, "checks": checks},
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
    vision_provider = None
    try:
        vision_provider = vision_mod.FrameVisionProvider()
    except Exception:
        vision_provider = None
    manual_manifest = spec.get("manual_manifest") or ""
    if not manual_manifest and videos:
        sibling = Path(videos[0]).parent / "assets_semantic.json"
        if sibling.is_file():
            manual_manifest = str(sibling)
    if vv:
        vv = vision_mod.analyze_assets(
            vv, base / "assets", manual_manifest=manual_manifest,
            vision_provider=vision_provider,
        )
    all_assets = {**vv, **ii}
    order = vo + io

    visual_source = state.get("constraints", {}).get("visual_source") or "auto"
    assignments = {}
    match_enabled = bool(spec.get("match_materials_to_script", True))

    # MPT 语义：搜索词属于 Scene 的中间决策，不再由 Stock 层临时从一句文案猜一次。
    scene_plan_items = state.get("scene_plan", {}).get("scenes") or []
    search_terms_by_scene = []
    for i, scene in enumerate(state.get("narration_scenes") or []):
        planned = scene_plan_items[i] if i < len(scene_plan_items) else {}
        raw_terms = planned.get("search_terms") if isinstance(planned, dict) else None
        search_terms_by_scene.append(
            mpt_semantics.normalize_search_terms(raw_terms, scene.get("text", ""), amount=8)
        )
    intent_path = base / "plans" / "material_intents.json"
    intent_path.parent.mkdir(parents=True, exist_ok=True)
    intent_path.write_text(
        json.dumps(
            mpt_semantics.material_intent_snapshot(
                state.get("narration_scenes") or [],
                search_terms_by_scene={
                    str(s.get("id") or ""): t
                    for s, t in zip(state.get("narration_scenes") or [], search_terms_by_scene)
                },
                max_clip_duration=spec.get("clip_duration"),
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if visual_source != "online" and match_enabled and all_assets and state.get("narration_scenes"):
        try:
            assignments = matcher.match_assets(
                state["narration_scenes"], all_assets, llm_call
            ) or {}
        except Exception as exc:
            warnings.append("本地素材匹配失败：" + str(exc))
    elif all_assets and state.get("narration_scenes"):
        warnings.append("用户关闭脚本语义匹配：按素材顺序规划")

    missing = [
        s["id"] for s in state.get("narration_scenes") or []
        if s["id"] not in assignments
    ]
    local_only = visual_source == "local"
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
                search_terms_by_scene=[
                    search_terms_by_scene[i]
                    for i, s in enumerate(state.get("narration_scenes") or [])
                    if s["id"] in missing
                ],
            )
            stock_files = list(sr.get("files") or [])
            metadata.update(sr.get("metadata") or {})
            warnings.extend(sr.get("warnings") or [])
            if visual_source == "online":
                videos = list(stock_files)
                images = []
            else:
                videos.extend([x for x in stock_files if x not in videos and x not in images])
            vv, vo = assets_mod.build_manifest(videos, [], asset_metadata=metadata) if videos else ({}, [])
            ii, io = assets_mod.build_manifest([], images, asset_metadata=metadata) if images else ({}, [])
            all_assets, order = {**vv, **ii}, vo + io
            assignments = (
                matcher.match_assets(state["narration_scenes"], all_assets, llm_call)
                if match_enabled and visual_source != "online" else {}
            )
        except Exception as exc:
            warnings.append("网络素材补足失败：" + str(exc))

    if not all_assets:
        return {
            "ok": False, "tool": "material.match", "status": "MISSING_REQUIREMENT",
            "error": "没有可用视觉素材", "result": {"warnings": warnings},
            "verify": {"ok": False, "checks": [{"id": "asset_pool", "ok": False}]},
        }

    if match_enabled and local_only and any(
        s["id"] not in assignments for s in state["narration_scenes"]
    ):
        return {
            "ok": False, "tool": "material.match", "status": "MISSING_REQUIREMENT",
            "error": "仅本地素材模式下存在未匹配场景",
            "result": {"assignments": assignments, "warnings": warnings},
            "verify": {"ok": False},
        }

    selection_reasons = {
        sid: "场景语义匹配：结合场景文案、素材语义与素材可用性选择"
        for sid in assignments
    }
    intent_data = mpt_semantics.material_intent_snapshot(
        state.get("narration_scenes") or [],
        search_terms_by_scene={
            str(s.get("id") or ""): t
            for s, t in zip(state.get("narration_scenes") or [], search_terms_by_scene)
        },
        max_clip_duration=spec.get("clip_duration"),
        selected_assets=assignments,
        selection_reasons=selection_reasons,
    )
    intent_path.write_text(json.dumps(intent_data, ensure_ascii=False, indent=2), encoding="utf-8")

    state.update({
        "video_files": videos,
        "assets": all_assets,
        "asset_order": order,
        "assignments": assignments,
        "material_intents": intent_data,
    })
    path = _json_file(base / "assets" / "match_assignments.json", {
        "assignments": assignments,
        "selection_reasons": selection_reasons,
    })
    coverage_ok = (
        all(s["id"] in assignments for s in state["narration_scenes"])
        if match_enabled and visual_source != "online"
        and any(a.type == "video" for a in all_assets.values())
        else True
    )
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


def _tool_subtitle(spec: dict, state: dict) -> dict:
    width = int(spec.get("width") or config.DEFAULT_RESOLUTION[0])
    height = int(spec.get("height") or config.DEFAULT_RESOLUTION[1])
    font_size = int(round(state["style_contract"].subtitle_font_size * width / 1080))
    path = config.project_dir(state["project_id"]) / "render" / "master.ass"
    mode = spec.get("subtitle_mode") or "plain"
    if mode == "karaoke":
        body = subtitle_mod.build_ass_karaoke(
            state["narration_scenes"], state["word_timeline"],
            width=width, height=height, font_size=font_size)
    else:
        body = subtitle_mod.build_ass_plain(
            state["narration_scenes"], width=width, height=height, font_size=font_size)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    state["subtitle_file"] = str(path)
    state["subtitle_entries"] = subtitle_mod.srt_entries(state["narration_scenes"])
    ok = _file_ok(str(path)) and bool(state["subtitle_entries"])
    return {
        "ok": ok, "tool": "subtitle.generate", "status": "APPLIED" if ok else "FAILED",
        "result": {"file": str(path), "cue_count": len(state["subtitle_entries"])},
        "verify": {"ok": ok, "checks": [{"id": "subtitle_file", "ok": _file_ok(str(path))},
                                         {"id": "cue_count", "ok": bool(state["subtitle_entries"])}]},
    }


def _tool_music(spec: dict, state: dict) -> dict:
    requested = spec.get("bgm") or "auto"
    if requested in ("none", "off", "disabled"):
        state["bgm_asset_id"] = None
        return {"ok": True, "tool": "music.select", "status": "SKIPPED",
                "result": {"reason": "用户未要求 BGM"}, "verify": {"ok": True}}
    aid = audio.resolve_auto_bgm(state["style_contract"].style_id) if requested == "auto" else requested
    if not aid:
        state["bgm_asset_id"] = None
        return {"ok": True, "tool": "music.select", "status": "SKIPPED",
                "result": {"reason": "音乐库为空，未随机替代"}, "verify": {"ok": True}}
    if aid not in audio.scan_library():
        return {"ok": False, "tool": "music.select", "status": "MISSING_REQUIREMENT",
                "error": "BGM 资产不存在：" + str(aid), "verify": {"ok": False}}
    state["bgm_asset_id"] = aid
    state["bgm_volume"] = float(spec.get("bgm_volume") or state["style_contract"].bgm_volume)
    return {"ok": True, "tool": "music.select", "status": "APPLIED",
            "result": {"asset_id": aid, "volume": state["bgm_volume"]},
            "verify": {"ok": True, "checks": [{"id": "bgm_source", "ok": True}]}}


def _tool_composition(spec: dict, state: dict) -> dict:
    pid = state["project_id"]
    dirs = config.init_project_dirs(config.project_dir(pid))
    scenes = state["narration_scenes"]
    all_assets = state["assets"]
    order = state["asset_order"]
    if not scenes:
        raise RuntimeError("composition 节点没有 narration_scenes")
    if not all_assets:
        raise RuntimeError("composition 节点没有素材池")

    active = (
        planner_mod.ContentMatchPlanner(state["assignments"])
        if state.get("assignments") else planner_mod.RulePlanner()
    )
    ir = active.plan(
        scenes=scenes, assets=all_assets, asset_order=order,
        width=int(spec.get("width") or config.DEFAULT_RESOLUTION[0]),
        height=int(spec.get("height") or config.DEFAULT_RESOLUTION[1]),
        fps=int(spec.get("fps") or config.DEFAULT_FPS),
        fit_mode=spec.get("fit_mode") or "cover",
        clip_duration=spec.get("clip_duration"),
        clip_speed=spec.get("clip_speed"),
        concat_mode=spec.get("concat_mode") or "sequential",
    )
    style_mod.apply_to_ir(ir, state["style_contract"], config)
    # 转场改为 Proposal：AI 可以提出，但没有用户确认绝不写入 IR。
    transition_mode = spec.get("transition_mode") or "cut"
    transition_budget = int(state["style_contract"].transition_budget or 0)
    proposal_path = dirs["plans"] / "transition_proposals.json"
    proposals = []
    if transition_budget > 0:
        requested_type = transition_mode if transition_mode in ("crossfade", "fade") else "crossfade"
        boundaries = max(0, len(ir.scenes) - 1)
        count = min(transition_budget, boundaries)
        if count > 0:
            step = boundaries / count
            picks = sorted({min(boundaries - 1, int(step * (i + 0.5))) for i in range(count)})
            for bi in picks:
                proposals.append({
                    "from_scene_id": ir.scenes[bi].id,
                    "to_scene_id": ir.scenes[bi + 1].id,
                    "type": requested_type,
                    "duration": 0.5,
                    "status": "PROPOSED",
                })
    proposal_path.write_text(
        json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    from video_director.models import AudioTrack, AudioTrackType, LayerType

    duration = scenes[-1]["start"] + scenes[-1]["duration"]
    tracks = [
        AudioTrack(
            type=AudioTrackType.narration, start=0.0, duration=duration,
            volume=float(spec.get("narration_volume") or 1.0),
        )
    ]

    # 用户素材原声：先用既有 prober 确认实际视频流是否含音频，再按每个 video layer 分段入轨。
    from video_director import prober as _audio_prober
    audio_capability = {}
    for sc in ir.scenes:
        for lay in sc.layers:
            if lay.type != LayerType.video or not lay.asset_id:
                continue
            src = all_assets.get(lay.asset_id)
            if not src:
                continue
            if lay.asset_id not in audio_capability:
                try:
                    audio_capability[lay.asset_id] = bool(_audio_prober.probe_media(src.file).has_audio_stream)
                except Exception:
                    audio_capability[lay.asset_id] = False
            if not audio_capability[lay.asset_id]:
                continue
            tracks.append(AudioTrack(
                type=AudioTrackType.original,
                asset_id=lay.asset_id,
                start=round(sc.start + lay.start, 3),
                duration=round(lay.duration, 3),
                source_in=round(lay.source_in, 3),
                source_out=round(lay.source_out, 3),
                volume=1.0,
                ducking=False,
            ))
    audio_assets = {}
    if state.get("bgm_asset_id"):
        lib = audio.scan_library()
        aid = state["bgm_asset_id"]
        if aid in lib:
            audio_assets[aid] = lib[aid]
            tracks.append(audio.build_bgm_track(
                aid, duration, state.get("bgm_volume") or config.BGM_DEFAULT_VOLUME
            ))
    if spec.get("sfx_plan"):
        lib = audio.scan_library()
        for ev in audio.normalize_sfx_events(spec.get("sfx_plan") or []):
            aid = ev.get("asset_id") or audio.resolve_action_sfx(ev, lib)
            if not aid:
                continue
            audio_assets[aid] = lib[aid]
            tracks.append(audio.build_sfx_track(
                aid, ev["at"], ev.get("duration", 0.7), ev.get("volume")
            ))
    ir.audio = tracks
    render_assets = audio.append_audio_assets(all_assets, audio_assets)

    # MPT source-window 台账：记录真正消费了哪个源文件的哪个区间。
    usage = mpt_semantics.material_usage_from_ir(ir)
    usage_path = _json_file(
        dirs["plans"] / "material_usage.json",
        [u.__dict__ for u in usage],
    )
    build_fingerprint = mpt_semantics.build_artifact_fingerprint({
        "composition": json.loads(ir.model_dump_json()),
        "narration_duration": duration,
        "clip_duration": spec.get("clip_duration"),
        "clip_speed": spec.get("clip_speed"),
        "fit_mode": spec.get("fit_mode") or "cover",
        "concat_mode": spec.get("concat_mode") or "sequential",
        "transition_mode": spec.get("transition_mode") or "cut",
        "bgm_asset_id": state.get("bgm_asset_id"),
        "subtitle_mode": spec.get("subtitle_mode") or "plain",
    })
    fingerprint_path = _json_file(
        dirs["plans"] / "mpt_build_fingerprint.json",
        {"version": 1, "fingerprint": build_fingerprint},
    )

    check = validator.validate_composition(
        ir, render_assets, duration, state.get("subtitle_entries")
    )
    if not check.ok:
        return {
            "ok": False, "tool": "composition.build", "status": "INVALID_COMPOSITION",
            "error": json.dumps(check.to_dict(), ensure_ascii=False),
            "verify": {"ok": False, "checks": check.to_dict().get("checks", [])},
        }

    cpath = _json_file(
        dirs["plans"] / "composition.json",
        json.loads(ir.model_dump_json()),
    )
    mpath = _json_file(
        dirs["assets"] / "manifest.json",
        [a.model_dump() if hasattr(a, "model_dump") else a for a in render_assets.values()],
    )
    spath = _json_file(
        dirs["plans"] / "style.json",
        {
            "style_id": state["style_contract"].style_id,
            "reason": state["style_contract"].reason,
            "contract": {
                "subtitle_font_size": state["style_contract"].subtitle_font_size,
                "transition_budget": state["style_contract"].transition_budget,
                "bgm_volume": state["style_contract"].bgm_volume,
            },
        },
    )
    state.update({
        "ir": ir,
        "render_assets": render_assets,
        "narration_duration": duration,
        "composition_path": cpath,
        "manifest_path": mpath,
        "style_path": spath,
        "transition_proposals_path": str(proposal_path),
    })
    checks = [
        {"id": "composition_file", "ok": _file_ok(cpath)},
        {"id": "manifest_file", "ok": _file_ok(mpath)},
    ]
    ok = all(x["ok"] for x in checks)
    return {
        "ok": ok,
        "tool": "composition.build",
        "status": "APPLIED" if ok else "FAILED",
        "result": {
            "composition": cpath, "manifest": mpath, "style": spath,
            "transition_proposals": str(proposal_path),
            "original_audio_segments": sum(
                1 for sc in ir.scenes for lay in sc.layers
                if lay.type == LayerType.video and lay.asset_id
                and all_assets.get(lay.asset_id) is not None
                and audio_capability.get(lay.asset_id, False)
            ),
        },
        "verify": {"ok": all(x["ok"] for x in checks), "checks": checks},
    }


def _tool_watermark(spec: dict, state: dict) -> dict:
    text = (spec.get("watermark") or "").strip()
    if not text:
        return {
            "ok": True, "tool": "watermark.add", "status": "SKIPPED",
            "result": {"reason": "未指定水印"}, "verify": {"ok": True},
        }
    from video_director import tool_executor
    res = tool_executor.execute_tool(state["project_id"], {
        "tool": "watermark.add",
        "targetId": "project",
        "payload": {
            "kind": "text", "text": text, "anchor": "bottom_right",
            "opacity": 0.75, "scale": 1.0,
        },
    })
    ok = bool(res.get("ok")) and bool((res.get("verify") or {}).get("ok", True))
    return {
        "ok": ok, "tool": "watermark.add",
        "status": res.get("status") or "FAILED",
        "result": res.get("result") or {},
        "verify": res.get("verify") or {"ok": ok},
        "error": res.get("error"),
    }


def _tool_preview(spec: dict, state: dict) -> dict:
    from video_director import confirm
    try:
        storyboard = confirm.build_storyboard(
            ir=state["ir"],
            scenes=state["narration_scenes"],
            assignments=state.get("assignments") or {},
            style=state["style_contract"],
            assets=state["assets"],
        )
        sb_path = config.project_dir(state["project_id"]) / "plans" / "storyboard.json"
        confirm.save_storyboard(storyboard, sb_path)
        path = confirm.render_draft(
            state["project_id"], profile=spec.get("profile") or None
        )
    except Exception as exc:
        return {
            "ok": False,
            "tool": "render.draft",
            "status": "FAILED",
            "error": str(exc),
            "verify": {"ok": False},
        }
    state["draft_path"] = path
    state["storyboard_path"] = str(sb_path)
    ok = _file_ok(path) and _file_ok(str(sb_path))
    return {
        "ok": ok,
        "tool": "render.draft",
        "status": "APPLIED" if ok else "FAILED",
        "result": {"draft": path, "storyboard": str(sb_path)},
        "verify": {
            "ok": ok,
            "checks": [
                {"id": "draft", "ok": _file_ok(path)},
                {"id": "storyboard", "ok": _file_ok(str(sb_path))},
            ],
        },
    }


def _tool_verify(spec: dict, state: dict) -> dict:
    checks = []
    try:
        from video_director.editor import load_composition
        ir = load_composition(state["project_id"])
        checks.append({
            "id": "composition_readable",
            "ok": bool(ir and ir.scenes),
            "detail": str(len(ir.scenes) if ir else 0),
        })
    except Exception as exc:
        checks.append({"id": "composition_readable", "ok": False, "detail": str(exc)})
    checks.extend([
        {"id": "composition_file", "ok": _file_ok(state.get("composition_path", ""))},
        {"id": "subtitle_file", "ok": _file_ok(state.get("subtitle_file", ""))},
        {"id": "manifest", "ok": _file_ok(state.get("manifest_path", ""))},
    ])
    if state.get("draft_path"):
        checks.append({"id": "draft", "ok": _file_ok(state["draft_path"])})
    if state.get("storyboard_path"):
        checks.append({"id": "storyboard", "ok": _file_ok(state["storyboard_path"])})
    ok = all(x["ok"] for x in checks)
    return {
        "ok": ok, "tool": "composition.verify",
        "status": "PASS" if ok else "FAILED",
        "result": {"checks": checks},
        "verify": {"ok": ok, "checks": checks},
    }


def _tool_render(spec: dict, state: dict) -> dict:
    if (spec.get("confirm_mode") or "preview") != "direct":
        return {
            "ok": True, "tool": "render.final", "status": "SKIPPED",
            "result": {"reason": "等待用户确认"}, "verify": {"ok": True},
        }
    pid = state["project_id"]
    work = config.project_dir(pid) / "render" / "final_agent"
    work.mkdir(parents=True, exist_ok=True)
    title_card = None
    title = (spec.get("title") or "").strip()
    if title:
        from video_director import title_engine
        title_card = title_engine.render_title_card(
            title, str(work / "title_card.mp4"),
            width=int(spec.get("width") or config.DEFAULT_RESOLUTION[0]),
            height=int(spec.get("height") or config.DEFAULT_RESOLUTION[1]),
            fps=int(spec.get("fps") or config.DEFAULT_FPS),
        )
    out = renderer.render_composition(
        state["ir"], state["render_assets"], work,
        narration_file=state.get("narration_file"),
        subtitle_file=state.get("subtitle_file"),
        title_card=title_card,
        lower_third_name=(spec.get("lower_third_name") or "").strip() or None,
        lower_third_role=(spec.get("lower_third_role") or "").strip() or None,
    )
    final_path = config.project_dir(pid) / "output" / "final.mp4"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if Path(out.final_path) != final_path:
        shutil.copyfile(out.final_path, final_path)
    report = qc_mod.run_qc(
        state["ir"], str(final_path), state["render_assets"],
        state["narration_duration"], expect_audio=bool(state["ir"].audio),
    )
    report_path = _json_file(
        config.project_dir(pid) / "qc" / "deterministic.json",
        report.to_dict(),
    )
    state["final_path"] = str(final_path)
    state["qc_report"] = report.to_dict()
    ok = bool(report.ok and final_path.is_file())
    return {
        "ok": ok, "tool": "render.final",
        "status": "APPLIED" if ok else "FAILED",
        "result": {
            "final_path": str(final_path),
            "qc": report.to_dict(),
            "qc_report": report_path,
        },
        "verify": {
            "ok": ok,
            "checks": report.to_dict().get("checks", []),
        },
        "error": None if ok else "最终成片 QC 未通过",
    }


def _tool_understand(spec: dict, state: dict) -> dict:
    refs = state["source_refs"]
    return {
        "ok": True,
        "tool": "director.inspect",
        "status": "INSPECTED",
        "result": {"source_refs": refs},
        "verify": {"ok": bool(refs), "checks": [{"id": "input_context", "ok": bool(refs)}]},
    }


#: node_id → 真实 Tool 执行器；understand 在 execute 内额外并入（不参与 _ready_nodes 调度）。
_TOOLS = {
    "script": _tool_script,
    "scene_plan": _tool_scene_plan,
    "narration": _tool_narration,
    "material_match": _tool_material,
    "subtitle": _tool_subtitle,
    "music": _tool_music,
    "composition": _tool_composition,
    "watermark": _tool_watermark,
    "preview": _tool_preview,
    "verify": _tool_verify,
    "render": _tool_render,
}


def execute(spec: dict, run_id: str, plan: dict, update_node, emit) -> dict:
    """Adaptive creation loop: every node has a Tool Call, Result, Verify and next-node decision."""
    from video_director import director_inputs as di

    pid = spec.get("project_id") or ("web-" + str(time.time_ns()))
    bundle = di.build_bundle(spec)
    di.persist_bundle(pid, bundle)

    user_style = (bundle.get("creative_settings") or {}).get("style") or ""
    ref_style = di.reference_style_intent(bundle)
    seeded_style = "；".join(x for x in (user_style, ref_style) if x).strip()
    initial_script = (bundle.get("script") or {}).get("text") or ""
    style_contract, style_warnings = style_mod.select_style(
        initial_script, seeded_style or None
    )

    try:
        from video_director import store
        store.create_project(
            pid,
            (bundle.get("script") or {}).get("text") or bundle.get("brief") or "AI Director",
            {
                "voice": spec.get("voice") or "zh-CN-XiaoxiaoNeural",
                "voice_rate": spec.get("voice_rate") or "+0%",
                "narration_volume": spec.get("narration_volume") or 1.0,
                "width": spec.get("width"),
                "height": spec.get("height"),
                "fps": spec.get("fps"),
                "bgm": spec.get("bgm") or "auto",
                "subtitle_mode": spec.get("subtitle_mode") or "plain",
                "creative_brief": bundle.get("brief") or "",
                "style_id": style_contract.style_id,
                "fit_mode": spec.get("fit_mode") or "cover",
                "clip_duration": spec.get("clip_duration"),
                "clip_speed": spec.get("clip_speed"),
                "concat_mode": spec.get("concat_mode") or "sequential",
                "transition_mode": spec.get("transition_mode") or "cut",
                "match_materials_to_script": bool(spec.get("match_materials_to_script", True)),
            },
        )
    except Exception as exc:
        logger.warning("Director project row creation failed: %s", exc)

    state = {
        "project_id": pid,
        "bundle": bundle,
        "source_refs": plan.get("source_refs") or [],
        "constraints": bundle.get("constraints") or {},
        "style_contract": style_contract,
        "style_warnings": style_warnings,
        "video_files": [],
        "image_files": [],
        "assets": {},
        "asset_order": [],
        "assignments": {},
    }

    videos, images = di.attach_library_assets(pid, bundle)
    refs = di.materialize_reference(pid, bundle)
    repo_files = di.materialize_software_project_artifacts(pid, bundle)
    state["video_files"], state["image_files"] = videos, images
    bundle["materialized"] = {
        "reference_files": refs,
        "software_evidence_files": repo_files,
    }
    di.persist_bundle(pid, bundle)

    if initial_script:
        state["script"] = initial_script
        state["scene_texts"] = _scene_texts(initial_script)

    _TOOLS_WITH_UNDERSTAND = dict(_TOOLS)
    _TOOLS_WITH_UNDERSTAND["understand"] = _tool_understand

    retries: Dict[str, int] = {}
    current = "start"
    last_result = {"ok": True, "status": "START"}
    guard = max(24, len(plan.get("nodes") or []) * 3)

    emit(run_id, "director", {
        "phase": "planning",
        "project_id": pid,
        "source_refs": plan.get("source_refs", []),
    })

    for _ in range(guard):
        ready = _ready_nodes(plan)
        all_done = all(n.get("status") in ("DONE", "SKIPPED")
                       for n in plan.get("nodes", []))
        if all_done:
            break
        if not ready:
            raise DirectorAgentError("Director 没有满足依赖的可执行节点")

        choice, reason = _choose_next(plan, current, last_result, retries.get(current, 0))
        if choice == "STOP":
            raise DirectorAgentError("Director 停止：" + reason)

        if choice == "retry":
            if current == "start":
                raise DirectorAgentError("Director 要求 retry，但没有当前节点")
            node_id = current
            node = next(n for n in plan["nodes"] if n["id"] == node_id)
            node["status"] = "PENDING"
        else:
            node_id = choice

        if node_id not in ready and node_id != current:
            raise DirectorAgentError("Director 选择了不可执行节点：" + node_id)

        node = next(n for n in plan["nodes"] if n["id"] == node_id)
        update_node(
            run_id, pid, plan, node_id, "RUNNING",
            decision={"reason": reason, "ready": ready},
        )
        emit(run_id, "director", {
            "phase": "tool",
            "node": node_id,
            "project_id": pid,
        })

        fn = _TOOLS_WITH_UNDERSTAND.get(node_id)
        if fn is None:
            raise DirectorAgentError("节点没有 Tool：" + node_id)

        try:
            tool_result = fn(spec, state)
        except Exception as exc:
            tool_result = {
                "ok": False,
                "tool": "node." + node_id,
                "status": "EXECUTION_ERROR",
                "error": str(exc)[:1200],
                "result": {},
                "verify": {"ok": False},
            }

        verify = tool_result.get("verify") or {"ok": bool(tool_result.get("ok"))}
        node_ok = bool(tool_result.get("ok")) and bool(verify.get("ok", True))

        if node_ok:
            status = "SKIPPED" if tool_result.get("status") == "SKIPPED" else "DONE"
            result_values = tool_result.get("result") or {}
            artifacts = [
                str(v) for v in result_values.values()
                if isinstance(v, str) and Path(v).is_file()
            ]
            update_node(
                run_id, pid, plan, node_id, status,
                outputs=list(node.get("outputs") or []),
                tool_calls=[{
                    "tool": tool_result.get("tool"),
                    "status": tool_result.get("status"),
                    "result": result_values,
                }],
                decision={"reason": reason},
                verify=verify,
                artifacts=artifacts,
            )
            last_result = {"ok": True, **tool_result}
        else:
            update_node(
                run_id, pid, plan, node_id, "FAILED",
                outputs=[],
                tool_calls=[{
                    "tool": tool_result.get("tool"),
                    "status": tool_result.get("status"),
                    "result": tool_result.get("result") or {},
                    "error": tool_result.get("error"),
                }],
                decision={"reason": reason},
                verify=verify,
                error=tool_result.get("error") or "节点验证未通过",
            )
            retries[node_id] = retries.get(node_id, 0) + 1
            last_result = {"ok": False, **tool_result}
            if retries[node_id] < 2:
                # 保留失败结果，但重新开放该节点；下一轮由 Director 决定是否重试。
                node["status"] = "PENDING"
            else:
                raise DirectorAgentError(
                    "节点执行失败且无法恢复：" + node_id
                )

        current = node_id

        # Tool Result is fed into the next decision point through refreshed persisted context.
        state["bundle"] = di.load_bundle(pid) or state["bundle"]
        if node_id == "script" and state.get("script"):
            style_contract, style_warnings = style_mod.select_style(
                state["script"],
                seeded_style or None,
            )
            state["style_contract"] = style_contract
            state["style_warnings"] = style_warnings

        # Watermark changes composition through Command Bus; reload the actual IR for all later nodes.
        if node_id == "watermark":
            try:
                from video_director.editor import load_composition
                state["ir"] = load_composition(pid)
            except Exception:
                pass

        plan["runtime"] = {
            "last_node": current,
            "last_tool_result": {
                "tool": last_result.get("tool"),
                "status": last_result.get("status"),
                "ok": last_result.get("ok"),
            },
            "retries": dict(retries),
            "decision_reason": reason,
        }
        di.persist_plan(pid, plan)

        if not node_ok:
            # The next Director decision sees the failure; retry is the only allowed recovery here.
            continue

    else:
        raise DirectorAgentError("Director Agent loop 超过安全迭代上限")

    if any(n.get("status") == "FAILED" for n in plan.get("nodes", [])):
        raise DirectorAgentError("Director 存在未恢复失败节点")

    verify_node = next(n for n in plan["nodes"] if n["id"] == "verify")
    verify_ok = (
        verify_node.get("status") == "DONE"
        and bool(verify_node.get("verify", {}).get("ok"))
    )
    pending = (spec.get("confirm_mode") or "preview") != "direct"

    if pending and verify_ok is not True:
        raise DirectorAgentError("预览模式必须先完成真实 Verify")

    if pending:
        from video_director import store
        store.set_stage(pid, store.Stage.AWAITING_CONFIRM)
        try:
            if state.get("storyboard_path"):
                sb = json.loads(Path(state["storyboard_path"]).read_text(encoding="utf-8"))
                store.save_storyboard_snapshot(pid, sb)
        except Exception as exc:
            logger.warning("Director storyboard snapshot failed: %s", exc)
    elif state.get("final_path") and state.get("qc_report"):
        from video_director import store
        store.finish_project(
            pid, state["final_path"], state["narration_duration"], state["qc_report"]
        )

    result = {
        "ok": True,
        "project_id": pid,
        "final_path": state.get("final_path", ""),
        "qc": state.get("qc_report", {}),
        "pending_confirm": pending,
        "director_inputs": di.load_bundle(pid),
        "director_plan": plan,
        "director_verify_ok": verify_ok,
    }
    emit(run_id, "director", {
        "phase": "done",
        "project_id": pid,
        "verify_ok": verify_ok,
        "source_refs": plan.get("source_refs", []),
    })
    return result
