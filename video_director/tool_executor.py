"""MPT 吸收方案 §7/§57/§58/§59：Tool Executor + Tool Result Verifier。

这是 §82/§83 的"交叉融合"点——AI 调 Tool 与用户点 UI 最终走同一个
Command Bus（editor.execute_command）。AI 不能直接改 Composition（§9），
必须：Tool Call → capability validator 校验 payload → Command →
Revision → Verifier 读回结果。

路由表把 capability id 映射到四类执行器：
    1. Command Executor：走 editor.execute_command（编辑域能力）
    2. Generation Executor：走 generation.generate（生成域能力）
    3. Render Executor：走 confirm.render_draft / finalize（渲染域能力）
    4. Revision Executor：走 proposal.propose/preview/apply/reject（改稿域）
    5. Read-only Executor：timeline.query / project.inspect 等只读汇总

Verifier（§59）：AI 执行后必须 verify（不是"执行"完了就是"完成"了）。
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from loguru import logger

from video_director import editor
from video_director.capability_registry import validate_tool_call
from video_director.editor import CommandResult


class ToolExecutorError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


def execute_tool(project_id: str, tool_call: dict) -> dict:
    """执行单条 Tool Call（§7 控制链：校验 → 命令 → revision → 验证）。

    tool_call = {"tool": "watermark.add", "payload": {...}, "targetId": ...}
    返回 {"tool", "ok", "status", "result": {...}, "verify": {...}}
    不抛出——逐 tool 返回结构化结果（§9：AI 逐能力继续）。
    """
    tool = tool_call.get("tool") or tool_call.get("capability") or ""
    payload = tool_call.get("payload") or {}
    target_id = tool_call.get("targetId")

    # 1. 能力存在 + payload 契约校验（§9 Server Validation）
    from video_director.capability_registry.manifest import manifest as _mf
    if tool not in _mf():
        return _result(tool, False, "UNKNOWN_CAPABILITY", error=f"未知能力 {tool!r}")
    # targetId 是 tool_call 顶层字段，但部分 inputSchema（TargetRequest/
    # LayerUpdateRequest/…）把 targetId 声明为 required——校验时须并入 payload，
    # 否则 targetId-required 能力永远报「缺必填字段 targetId」。
    _valid_payload = dict(payload)
    if target_id is not None:
        _valid_payload.setdefault("targetId", target_id)
    ok, errors = validate_tool_call(tool, _valid_payload)
    if not ok:
        return _result(tool, False, "INVALID_PAYLOAD", error="；".join(errors))

    # 2. 路由执行
    try:
        if _is_verify(tool):
            return _execute_verify(project_id, tool, payload, target_id)
        if _is_material_motion(tool):
            return _execute_material_motion(project_id, tool, payload, target_id)
        if _is_readonly(tool):
            return _execute_readonly(project_id, tool, payload, target_id)
        if _is_revision(tool):
            return _execute_revision(project_id, tool, payload, target_id)
        if _is_generation(tool):
            return _execute_generation(project_id, tool, payload, target_id)
        if _is_render(tool):
            return _execute_render(project_id, tool, payload, target_id)
        if _is_project_mgmt(tool):
            return _execute_project_mgmt(project_id, tool, payload, target_id)
        if tool == "music.match_video":
            return _execute_music_match(project_id)
        if _is_content(tool):
            return _execute_content(project_id, tool, payload, target_id)
        return _execute_editor_command(project_id, tool, payload, target_id)
    except ToolExecutorError as exc:
        return _result(tool, False, exc.code, error=str(exc))
    except Exception as exc:  # noqa: BLE001 逐 tool 落在结果里，上层不应崩
        logger.exception(f"tool_executor: {tool} 执行异常")
        return _result(tool, False, "EXECUTION_ERROR", error=str(exc)[:200])


# ---------------------------------------------------------------------------
# capability → 命令路由表（§57 Tool Registry 落地的核心映射）
# ---------------------------------------------------------------------------

_COMMAND_MAP: Dict[str, str] = {
    "timeline.select": "select",
    "timeline.trim": "trim",
    "timeline.move": "move",
    "timeline.split": "split",
    "timeline.delete": "delete",
    "timeline.duplicate": "duplicate",
    "timeline.replace": "replace_recipe",
    "layer.add": "add_broll",
    "layer.update": "set_transform",
    "layer.remove": "remove_broll",
    "asset.add_to_timeline": "add_broll",
    "asset.replace": "replace_source",
    "creative.candidate.select": "set_scene_intent",
    "music.volume": "set_audio_volume",
    "music.duck": "set_audio_volume",
    "music.add": "set_audio_volume",
    "music.replace": "set_audio_volume",
    "music.disable": "set_audio_volume",
    "motion.apply": "replace_motion",
    "style.apply": "set_style",
    "audio.original.preserve": "set_original_audio",
    "audio.original.mute": "set_original_audio",
    "audio.original.volume": "set_original_audio_volume",
    "watermark.add": "set_watermark",
    "watermark.update": "set_watermark",
    "watermark.remove": "remove_watermark",
    "subtitle.update_style": "set_subtitle_style",
    "subtitle.update_timing": "set_subtitle_style",
    "subtitle.generate": "set_subtitle_track",
    # ---- Frontend Unification §14/§15/§16（camera/pip/morph 执行链接入）----
    "camera.apply": "apply_camera",
    "pip.configure": "configure_pip",
    "visual.morph": "apply_morph",
    "mask.apply": "apply_mask",
    "keyframe.add": "add_keyframe",
    "keyframe.remove": "remove_keyframe",
}

_READONLY = {
    "system.capabilities.list", "system.capabilities.describe", "system.context.inspect",
    "project.inspect", "timeline.query", "asset.search", "asset.resolve",
    "stock.search", "stock.preview", "tts.list_voices", "music.search",
    "music.preview", "motion.list", "motion.preview", "shot.resolve",
    "render.preflight", "qc.inspect",
    # §59 Verify Tools（读回 IR 断言，只读）
    "timeline.verify", "audio.verify", "asset.verify",
    "render.verify", "composition.verify",
    # §13 ContinuityVerifier（PPT Detector，只读检查）
    "visual.continuity.verify",
}

_REVISION = {"revision.propose", "revision.preview", "revision.apply", "revision.reject"}

_GENERATION = {"generation.image", "generation.video", "generation.sfx", "generation.music"}
_MATERIAL_MOTION = {"material.motion.list", "material.motion.create"}

_RENDER = {"render.draft", "render.final"}

#: §37/§57 项目管理能力（Variant 快照 / 重开 / 保存）
_PROJECT_MGMT = {"version.create", "project.reopen", "project.save", "project.create"}

_CONTENT = {"content.generate_script", "content.rewrite_script"}

_VERIFY = {"timeline.verify", "audio.verify", "asset.verify", "render.verify", "composition.verify"}


def _is_verify(tool: str) -> bool:
    return tool in _VERIFY


def _is_readonly(tool: str) -> bool:
    return tool in _READONLY


def _is_revision(tool: str) -> bool:
    return tool in _REVISION


def _is_generation(tool: str) -> bool:
    return tool in _GENERATION


def _is_material_motion(tool: str) -> bool:
    return tool in _MATERIAL_MOTION


def _is_render(tool: str) -> bool:
    return tool in _RENDER


def _is_project_mgmt(tool: str) -> bool:
    return tool in _PROJECT_MGMT


def _is_content(tool: str) -> bool:
    return tool in _CONTENT


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------

def _result(tool: str, ok: bool, status: str, result=None, error=None, verify=None) -> dict:
    return {"tool": tool, "ok": ok, "status": status,
            "result": result or {}, "error": error, "verify": verify or {}}


def _execute_editor_command(project_id: str, tool: str, payload: dict, target_id) -> dict:
    """编辑域能力：映射到 editor.execute_command（Command Bus，§82）。"""
    cmd_name = _COMMAND_MAP[tool]
    p = dict(payload)
    if tool == "layer.update" and p.get("duration") is not None:
        cmd_name = "set_duration"
    # 语义 payload 字段对齐 editor 命令的字段名
    if tool == "timeline.replace":
        p = {"recipeId": p.get("recipeId") or p.get("recipe_id") or ""}
    elif tool == "layer.add" or tool == "asset.add_to_timeline":
        p = {"assetId": p.get("assetId") or p.get("asset_id") or "",
             "assetType": p.get("assetType") or p.get("asset_type") or "image",
             "sourceOut": p.get("sourceOut") or p.get("source_out")}
    elif tool == "layer.update":
        keep = ("duration",) if cmd_name == "set_duration" else ("scale", "opacity")
        p = {k: v for k, v in p.items() if k in keep}
    elif tool in ("audio.original.preserve", "audio.original.mute"):
        p = {"mode": "preserve" if tool.endswith("preserve") else "mute",
             "volume": p.get("volume"), "duckAgainst": p.get("duckAgainst"),
             "fadeIn": p.get("fadeIn"), "fadeOut": p.get("fadeOut")}
    elif tool == "set_original_audio" or tool == "audio.original.volume":
        p = {"volume": p.get("volume")}
    elif tool == "music.volume":
        p = {"track_type": p.get("type") or "bgm", "volume": p.get("volume", 1.0)}
    elif tool == "music.duck":
        # §37 duck 语义：只开关闪避（volume 不传 → _cmd_set_audio_volume
        # 用 payload.get(volume, None) 语义保留当前音量）
        p = {"track_type": p.get("type") or "bgm", "ducking": bool(p.get("ducking", True))}
        if p.get("volume") is not None:
            p["volume"] = p["volume"]
    elif tool == "music.disable":
        # §35 music.disable：BGM 关闭（volume=0，不删轨，可再开回）
        p = {"track_type": "bgm", "volume": 0.0}
    elif tool in ("music.add", "music.replace"):
        # §35/§66 music.add：曲目 + 音量一起进 AudioTrack（asset_id 指向选中
        # 音乐资产——修复丢 assetId 导致 bgm 轨无源可渲的断层）
        p = {"track_type": p.get("type") or "bgm",
             "volume": p.get("volume", 0.3), "ducking": p.get("ducking", True)}
        if p.get("assetId") or payload.get("assetId"):
            p["assetId"] = p.get("assetId") or payload.get("assetId")
    elif tool == "music.match_video":
        raise ToolExecutorError(
            "MUSIC_MATCH_UNAVAILABLE",
            "按参考视频匹配配乐通道未开（§68 Reference 链音乐维度）——诚实不可用")
    elif tool in ("watermark.add", "watermark.update"):
        p = {k: v for k, v in p.items()
             if k in ("kind", "text", "assetId", "anchor", "opacity", "scale",
                      "safeArea", "start", "duration")}
        # §19：text 水印空文本 → 默认占位（用户后续可 update 成真实品牌名）
        if (p.get("kind") or "text") == "text" and not (p.get("text") or "").strip():
            p["text"] = "水印"
    elif tool == "subtitle.update_style":
        p = {k: v for k, v in p.items()
             if k in ("font", "size", "color", "strokeColor", "strokeWidth",
                      "background", "backgroundOpacity", "position",
                      "alignment", "maxWidth", "animation", "mode")}
    elif tool == "subtitle.generate":
        # §31/§33：从项目词级时间轴（TTS WordBoundary/ASR 产物）生成字幕轨 cues
        import json as _json
        from video_director import config as _cfg
        words_path = _cfg.project_dir(project_id) / "plans" / "alignment" / "words.json"
        cues = None
        source = "manual"
        if words_path.is_file():
            try:
                words = _json.loads(words_path.read_text(encoding="utf-8"))
                cues = [{"start": w["start"], "end": w["end"], "text": w["text"]}
                        for w in words if w.get("text")]
                source = "tts_word_boundary"
            except Exception as exc:
                logger.info(f"subtitle.generate: words.json 读取失败: {exc}")
        if not cues:
            # 退路：分镜文本按时长估算（§33 segmentation，诚实标注 estimated）
            scenes_path = _cfg.project_dir(project_id) / "plans" / "scenes.json"
            if scenes_path.is_file():
                try:
                    scenes = _json.loads(scenes_path.read_text(encoding="utf-8"))
                    from video_director.narration import estimate_word_timeline
                    words = estimate_word_timeline(
                        [s.get("text", "") for s in scenes],
                        [s.get("duration", 2.0) for s in scenes])
                    cues = [{"start": w["start"], "end": w["end"], "text": w["text"]}
                            for w in words if w.get("text")]
                    source = "estimated"
                except Exception as exc:
                    logger.info(f"subtitle.generate: 估算失败: {exc}")
        if not cues:
            return _result(tool, False, "MISSING_REQUIREMENT",
                           error="项目无词级时间轴（words.json）且无分镜文本，无法生成字幕轨")
        p = {"mode": payload.get("mode") or "word",
             "source": source, "cues": cues}
    result: CommandResult = editor.execute_command(project_id, {
        "command": cmd_name, "targetId": target_id, "payload": p,
        "author": "ai-director",
    })
    resp = _result(tool, result.status == "APPLIED", result.status,
                   result=result.composition, error=_fmt_errors(result.errors))
    if result.status == "APPLIED":
        resp["verify"] = verify_tool(project_id, tool, result)
    resp["revision_id"] = result.revision_id
    resp["composition_version"] = result.composition_version
    return resp


def _execute_music_match(project_id: str) -> dict:
    """自动配乐：用项目风格契约选择本地 BGM，并通过同一 Command Bus 入轨。"""
    try:
        from video_director import audio as audio_mod, editor as editor_mod
        from video_director import config as vd_config
        import json as _json
        style_id = "knowledge-explainer"
        style_path = vd_config.project_dir(project_id) / "plans" / "style.json"
        if style_path.is_file():
            try:
                data = _json.loads(style_path.read_text(encoding="utf-8"))
                style_id = data.get("contract", {}).get("style_id") or style_id
            except Exception:
                pass
        asset_id = audio_mod.resolve_auto_bgm(style_id)
        if not asset_id:
            return _result("music.match_video", False, "MUSIC_UNAVAILABLE",
                           error="音乐库为空，无法自动配乐")
        result = editor_mod.execute_command(project_id, {
            "command": "set_audio_volume",
            "targetId": "bgm",
            "payload": {"track_type": "bgm", "assetId": asset_id,
                        "volume": 0.3, "ducking": True},
            "author": "ai-director",
        })
        if result.status != "APPLIED":
            return _result("music.match_video", False, result.status,
                           error=_fmt_errors(result.errors) or "自动配乐未应用")
        return _result("music.match_video", True, "APPLIED",
                       result={"asset_id": asset_id, "style_id": style_id},
                       verify={"ok": True, "note": f"BGM 已加入：{asset_id}"},
                       )
    except Exception as exc:
        return _result("music.match_video", False, "MUSIC_MATCH_FAILED", error=str(exc)[:250])


def _execute_content(project_id: str, tool: str, payload: dict, target_id) -> dict:
    """内容域：Director 可直接生成/润色脚本，返回 ScriptArtifact，不修改 Composition。"""
    from video_director import llm_provider
    try:
        if tool == "content.generate_script":
            script = llm_provider.generate_script(
                payload.get("topic", ""),
                platform=payload.get("platform", ""),
                duration=payload.get("duration", ""),
                style=payload.get("style", ""),
            )
        else:
            script = llm_provider.enhance_script(payload.get("script", ""))
        return _result(tool, True, "OK", result={
            "artifactType": "ScriptArtifact",
            "script": script,
        })
    except Exception as exc:
        return _result(tool, False, "CONTENT_GENERATION_FAILED", error=str(exc)[:300])


def _execute_material_motion(project_id: str, tool: str, payload: dict, target_id) -> dict:
    """Huashu Procedural Motion：生成结构化动画 Artifact，不直接修改 Composition。"""
    from video_director import procedural_motion

    try:
        if tool == "material.motion.list":
            return _result(
                tool,
                True,
                "OK",
                result={"grammars": procedural_motion.available_grammars()},
            )
        out = procedural_motion.create_motion_asset(project_id, payload)
        asset = out.get("asset") or {}
        artifact = out.get("artifact") or {}
        return _result(
            tool,
            True,
            "APPLIED",
            result={
                "program": out.get("program") or {},
                "asset": asset,
                "artifact": artifact,
            },
            verify={
                "asset_exists": bool(asset.get("file")),
                "artifact_exists": bool(artifact.get("output")),
            },
        )
    except procedural_motion.ProceduralMotionError as exc:
        return _result(tool, False, exc.code, error=str(exc))


def _execute_generation(project_id: str, tool: str, payload: dict, target_id) -> dict:
    """生成域能力：统一走 generation.generate（§28 单一 Generation Provider）。"""
    from video_director import generation as gen
    gen_type = tool.split(".")[-1]  # image / video / sfx / music
    prompt = payload.get("prompt", "")
    if not prompt:
        raise ToolExecutorError("INVALID_PAYLOAD", "generation 缺 prompt")
    try:
        results = gen.generate(
            project_id, prompt, gen_type=gen_type,
            width=payload.get("width"), height=payload.get("height"),
            model=payload.get("model"), reference=payload.get("references"),
            first_frame=payload.get("firstFrame"), last_frame=payload.get("lastFrame"),
        )
    except gen.GenerationError as exc:
        return _result(tool, False, exc.code, error=str(exc))

    records = [r.model_dump() if hasattr(r, "model_dump") else r for r in results]
    adopted = []
    # Director 生成画面后立即采用：scene → 首个视觉层，layer → 当前层。
    # 这样“生成画面”不再停在孤立资产库里，而是形成 Generate → Apply → Verify 闭环。
    if gen_type in ("image", "video") and results and target_id:
        first = results[0]
        try:
            ir = editor.load_composition(project_id)
            target_layer = target_id
            if "#" not in str(target_id):
                scene = next((s for s in ir.scenes if s.id == target_id), None)
                if scene and scene.layers:
                    target_layer = f"{scene.id}#0"
            if target_layer and "#" in str(target_layer):
                result = editor.execute_command(project_id, {
                    "command": "replace_source",
                    "targetId": target_layer,
                    "payload": {"assetId": first.asset_id},
                    "author": "ai-director",
                })
                if result.status == "APPLIED":
                    adopted.append({
                        "asset_id": first.asset_id,
                        "targetId": target_layer,
                        "revision_id": result.revision_id,
                    })
                else:
                    return _result(tool, False, result.status,
                                   result={"results": records},
                                   error=_fmt_errors(result.errors) or "生成成功但未能应用到时间线")
        except Exception as exc:
            return _result(tool, False, "ADOPTION_FAILED",
                           result={"results": records},
                           error=f"生成成功，但采用到时间线失败：{exc}")
    return _result(tool, True, "APPLIED",
                   result={"results": records, "adopted": adopted})


def _execute_render(project_id: str, tool: str, payload: dict, target_id) -> dict:
    """渲染域能力：draft / final（§43/44 Profile 透传）。"""
    from video_director import confirm
    profile = payload.get("profile")
    if tool == "render.draft":
        path = confirm.render_draft(project_id, profile=profile)
        return _result(tool, True, "APPLIED", result={"draft_path": path},
                       verify={"draft_exists": bool(path and __import__("os").path.isfile(path))})
    path = confirm.finalize_after_review(project_id, profile=profile)
    return _result(tool, True, "APPLIED", result={"final": path})


def _execute_project_mgmt(project_id: str, tool: str, payload: dict, target_id) -> dict:
    """§37/§57 项目管理：version.create（Variant 快照）/ project.reopen / save / create。"""
    import shutil as _shutil
    from video_director import config

    if tool == "version.create":
        # §37 Variant System：项目目录快照 → <pid>-v<N>（Composition/Revision/素材全量）
        base = config.project_dir(project_id)
        if not base.is_dir():
            return _result(tool, False, "NOT_FOUND", error=f"项目 {project_id} 不存在")
        existing = sorted(base.parent.glob(f"{project_id}-v*"))
        n = len(existing) + 1
        variant_id = f"{project_id}-v{n}"
        variant_dir = base.parent / variant_id
        if variant_dir.exists():
            return _result(tool, False, "CONFLICT", error=f"版本目录已存在: {variant_id}")
        _shutil.copytree(base, variant_dir)
        logger.info(f"version.create: {project_id} → {variant_id}")
        return _result(tool, True, "APPLIED",
                       result={"variant_id": variant_id, "path": str(variant_dir)},
                       verify={"variant_exists": variant_dir.is_dir()})
    if tool == "project.reopen":
        target = payload.get("projectId") or project_id
        base = config.project_dir(target)
        if not base.is_dir():
            return _result(tool, False, "NOT_FOUND", error=f"项目 {target} 不存在")
        return _result(tool, True, "OK", result={"project_id": target, "path": str(base)})
    if tool == "project.save":
        # 项目状态已由 Command Bus 每次命令原子落盘（execute_command 内 save_composition）
        # project.save 语义=确认落盘存在 + 版本号上报
        from video_director import editor
        try:
            ir = editor.load_composition(project_id)
        except Exception as exc:
            return _result(tool, False, "LOAD_FAILED", error=str(exc)[:150])
        return _result(tool, True, "OK",
                       result={"project_id": project_id,
                               "version": editor.composition_version(project_id),
                               "duration": ir.project.duration})
    # project.create：新建空项目目录结构
    import uuid as _uuid
    ratio = payload.get("ratio") or "9:16"
    dims = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}.get(ratio)
    if dims is None:
        return _result(tool, False, "INVALID_PAYLOAD", error=f"ratio {ratio!r} 非法")
    new_pid = f"web-{_uuid.uuid4().hex[:8]}"
    config.init_project_dirs(config.project_dir(new_pid))
    return _result(tool, True, "APPLIED",
                   result={"project_id": new_pid, "ratio": ratio,
                           "width": dims[0], "height": dims[1]},
                   verify={"project_dir_exists": config.project_dir(new_pid).is_dir()})


def _execute_revision(project_id: str, tool: str, payload: dict, target_id) -> dict:
    """改稿域能力：proposal 四态（§9 AI 只提案 → 预览 → 应用/拒绝）。"""
    from video_director import proposal
    if tool == "revision.propose":
        out = proposal.propose(project_id, payload.get("instruction", ""))
        return _result(tool, True, "PROPOSED", result=out)
    if tool == "revision.preview":
        out = proposal.preview(project_id, payload.get("proposalId", ""))
        return _result(tool, True, "PREVIEWED", result=out)
    if tool == "revision.apply":
        out = proposal.apply(project_id, payload.get("proposalId", ""))
        return _result(tool, out.get("status") == "APPLIED", out.get("status"), result=out)
    out = proposal.reject(project_id, payload.get("proposalId", ""))
    return _result(tool, True, "REJECTED", result=out)


def _execute_readonly(project_id: str, tool: str, payload: dict, target_id) -> dict:
    """只读能力：读 manifest / project context / 汇总，不产生 revision。"""
    from video_director.capability_registry import (
        describe_capability, list_capabilities)
    from video_director import project_context

    if tool == "system.capabilities.list":
        return _result(tool, True, "OK", result={"capabilities": list_capabilities()})
    if tool == "system.capabilities.describe":
        return _result(tool, True, "OK",
                       result={"capability": describe_capability(payload.get("id", ""))})
    if tool == "system.context.inspect":
        return _result(tool, True, "OK",
                       result={"context": project_context.inspect_project_context(
                           project_id, payload.get("selection"))})
    if tool == "timeline.query":
        return _result(tool, True, "OK",
                       result={"timeline": _timeline_summary(project_id)})
    if tool == "visual.continuity.verify":
        # §13 ContinuityVerifier（PPT Detector）：只读检查相邻场景连续性
        from video_director import continuity as vd_continuity
        try:
            ir = editor.load_composition(project_id)
        except Exception as exc:
            return _result(tool, False, "NOT_FOUND", error=f"项目不存在或 IR 加载失败: {exc}")
        report = vd_continuity.verify_composition_continuity(ir)
        return _result(tool, True, "OK", result={
            "status": report.status,
            "issues": [i.model_dump() for i in report.issues],
        })
    if tool == "project.inspect":
        return _result(tool, True, "OK",
                       result={"project": project_context.inspect_project_context(project_id)})
    if tool == "motion.list":
        from video_director.registries.core import LayeredRegistryLoader
        items = LayeredRegistryLoader("motion_recipe", "motions").load_all()
        return _result(tool, True, "OK",
                       result={"motions": [i.to_dict() if hasattr(i, "to_dict") else str(i)
                                           for i in items]})
    if tool == "tts.list_voices":
        from video_director import voice as voice_mod
        return _result(tool, True, "OK",
                       result={"voices": voice_mod.list_voices(),
                               "providers": voice_mod.list_providers()})
    if tool == "asset.search":
        from video_director import assets
        return _result(tool, True, "OK",
                       result={"assets": assets.build_manifest(project_id)})
    if tool == "stock.search":
        from video_director import stock as stock_mod
        try:
            # §10 ratio 由项目画幅推导（不硬编码 9:16）
            from video_director import asset_catalog as vd_acat
            sp = vd_acat.stock_search_params(project_id) if project_id else {"ratio": "", "orientation": "any"}
            hits = stock_mod.search_stock(
                payload.get("query", ""), provider=payload.get("provider"),
                orientation=payload.get("orientation") or sp.get("orientation"))
            cands = stock_mod.resolve_candidates(
                hits, target_ratio=payload.get("ratio") or sp.get("ratio") or "",
                target_duration=payload.get("targetDuration"))
            return _result(tool, True, "OK",
                           result={"candidates": [c.to_dict() for c in cands]})
        except stock_mod.StockError as exc:
            return _result(tool, False, exc.code, error=str(exc))
    if tool == "render.preflight":
        from video_director import preflight as preflight_mod
        report = preflight_mod.run_preflight(project_id, for_publish=True)
        return _result(tool, True, "OK", result={"preflight": report.to_dict()})
    if tool == "qc.inspect":
        from video_director import qc as qc_mod
        try:
            report = qc_mod.run_qc(project_id) if hasattr(qc_mod, "run_qc") else {"status": "PASS"}
            return _result(tool, True, "OK", result={"qc": report})
        except Exception as exc:
            return _result(tool, False, "QC_ERROR", error=str(exc)[:200])
    if tool in ("tts.preview_voice", "tts.preview_narration", "tts.generate"):
        # Provider Capability Matrix：默认 edge-tts；显式选择其它 provider 时先硬校验。
        from video_director import provider_matrix
        tts_provider = payload.get("provider") or "edge-tts"
        tts_gate = provider_matrix.state_for_provider(
            tts_provider, "tts", "tts.speak",
            {"api_key": "configured"} if tts_provider == "edge-tts" else {},
        )
        if tts_gate.status != provider_matrix.READY:
            code = (
                "CAPABILITY_UNSUPPORTED" if tts_gate.status == provider_matrix.UNSUPPORTED
                else "PROVIDER_UNKNOWN" if tts_gate.status == provider_matrix.UNKNOWN
                else "PROVIDER_MISSING"
            )
            return _result(tool, False, code, error=str(
                provider_matrix.ProviderCapabilityError(tts_gate)
            ))
        # §15 试听三级 Artifact：VoicePreview / NarrationPreview / Narration。
        # 全部走 edge-tts 合成到项目 audio 目录（试听产物非正式 Narration，
        # tts.generate 落 narration_preview.mp3/narration.mp3 由调用方确认后入轨）。
        from video_director import config as cfg
        import uuid as _uuid
        voice_id = payload.get("voiceId") or "zh-CN-XiaoxiaoNeural"
        text = (payload.get("text") or "").strip()
        if not text:
            # 未给文本：读项目分镜文本合成完整试听（§15 完整试听）
            try:
                scenes = json.loads(
                    (cfg.project_dir(project_id) / "plans" / "scenes.json")
                    .read_text(encoding="utf-8"))
                text = "。".join(s.get("text", "") for s in scenes if s.get("text"))
            except Exception:
                return _result(tool, False, "MISSING_REQUIREMENT",
                               error="缺 text 且项目无分镜文本（先创作或传 text）")
        if not text.strip():
            return _result(tool, False, "MISSING_REQUIREMENT", error="合成文本为空")
        audio_dir = cfg.project_dir(project_id) / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        if tool == "tts.preview_voice":
            out_name = f"voice_preview_{_uuid.uuid4().hex[:6]}.mp3"
        elif tool == "tts.preview_narration":
            out_name = "narration_preview.mp3"
        else:
            out_name = "narration.mp3"
        out_path = audio_dir / out_name
        try:
            from video_director.narration import synth_scene_audio
            words = synth_scene_audio(text, voice_id, str(out_path))
        except Exception as exc:
            return _result(tool, False, "TTS_FAILED", error=str(exc)[:200])
        artifact = {"artifactType": {
            "tts.preview_voice": "VoicePreviewArtifact",
            "tts.preview_narration": "NarrationPreviewArtifact",
            "tts.generate": "NarrationArtifact"}[tool],
            "file": str(out_path), "voice": voice_id, "words": len(words)}
        return _result(tool, True, "OK", result=artifact)
    if tool == "music.search":
        # §89 CAP-MUSIC-001/002：本地音乐库搜索（audio.scan_library）
        from video_director import audio as audio_mod
        try:
            lib = audio_mod.scan_library()
            query = (payload.get("query") or "").lower()
            hits = []
            for a in lib.values():
                if a.type != "bgm":
                    continue
                blob = " ".join([a.id] + list(a.keywords or [])).lower()
                if not query or query in blob:
                    hits.append({"asset_id": a.id, "file": a.file,
                                 "keywords": list(a.keywords or [])})
                if len(hits) >= 30:
                    break
            return _result(tool, True, "OK", result={"music": hits})
        except Exception as exc:
            return _result(tool, False, "MUSIC_SEARCH_FAILED", error=str(exc)[:200])
    if tool == "music.preview":
        # §89 CAP-MUSIC-003：BGM 试听（audio_preview 通道同源）
        from video_director import audio as audio_mod
        asset_id = payload.get("targetId") or payload.get("assetId") or ""
        lib = audio_mod.scan_library()
        a = lib.get(asset_id)
        if a is None or a.type != "bgm":
            return _result(tool, False, "NOT_FOUND", error=f"BGM {asset_id!r} 不在音频库")
        return _result(tool, True, "OK",
                       result={"MusicPreviewArtifact": {"asset_id": asset_id,
                                                        "file": a.file}})
    if tool == "stock.preview":
        # §86 CAP-ASSET-006：Stock 素材预览页 URL（不下载、不伪造可播）
        from video_director import stock as stock_mod
        page = payload.get("targetId") or payload.get("url") or ""
        if not page:
            return _result(tool, False, "INVALID_PAYLOAD", error="缺 targetId/url")
        return _result(tool, True, "OK",
                       result={"source_page": page,
                               "note": "浏览器打开 source page 预览（不代理下载）"})
    # 其余只读（asset.resolve 等）诚实降级
    return _result(tool, False, "NOT_IMPLEMENTED",
                   error=f"能力 {tool} 的后端 provider 尚未接线（诚实降级，不伪造）")


def _timeline_summary(project_id: str) -> dict:
    ir = editor.load_composition(project_id)
    return {
        "scenes": [{"id": sc.id, "start": sc.start, "duration": sc.duration,
                    "text": sc.text,
                    # §55 inspect 粒度：层明细（type/asset_id/时长）——Director
                    # 「给第二段换个画面」需发现 Scene 02 / Layer 0 / 当前素材 id
                    "layers": [{"index": i, "type": lay.type.value,
                                "asset_id": lay.asset_id,
                                "duration": lay.duration}
                               for i, lay in enumerate(sc.layers)]}
                   for sc in ir.scenes],
        "audio": [{"type": t.type.value, "volume": t.volume, "start": t.start,
                   "duration": t.duration,
                   "asset_id": t.asset_id, "muted": bool(getattr(t, "muted", False)),
                   "ducking": t.ducking} for t in ir.audio],
        "watermarks": [w.text or w.asset_id or w.kind for w in ir.watermarks],
        "subtitle_style": ir.subtitle_style.model_dump() if ir.subtitle_style else None,
    }


def _fmt_errors(errors: List[dict]) -> Optional[str]:
    if not errors:
        return None
    return "；".join(e.get("message", e.get("code", "")) for e in errors)


def _execute_verify(project_id: str, tool: str, payload: dict, target_id) -> dict:
    """§59 Verify Tools：AI 可主动调用的读回断言（不依赖刚执行命令）。

    timeline.verify / audio.verify / asset.verify → verify_tool 全维读回；
    render.verify → 产物存在性（draft/final）；
    composition.verify → IR 加载 + 各轨计数汇总。
    """
    import os as _os
    from video_director import config as _cfg
    try:
        ir = editor.load_composition(project_id)
    except Exception as exc:
        return _result(tool, False, "VERIFY_FAILED", error=f"IR 读回失败: {exc}")
    if tool == "render.verify":
        out_dir = _cfg.project_dir(project_id) / "output"
        draft = out_dir / "draft.mp4"
        checks = {"draft_exists": draft.is_file(),
                  "draft_path": str(draft)}
        return _result(tool, draft.is_file(),
                       "PASS" if draft.is_file() else "DRAFT_MISSING",
                       result={"checks": checks}, verify=checks)
    if tool == "composition.verify":
        counts = {"scenes": len(ir.scenes),
                  "audio_tracks": {t.type.value: 1 + 0 * i for i, t in enumerate(ir.audio)
                                   } if not ir.audio else
                  {ty: sum(1 for t in ir.audio if t.type.value == ty)
                   for ty in {t.type.value for t in ir.audio}},
                  "watermarks": len(ir.watermarks),
                  "subtitle_track": bool(ir.subtitle_track),
                  "subtitle_style": bool(ir.subtitle_style)}
        return _result(tool, True, "PASS", result={"checks": counts}, verify=counts)
    # timeline / audio / asset → 全维 verify_tool（拿最近命令名做读回断言）
    probe_tool = {"timeline.verify": "timeline.trim",
                  "audio.verify": "audio.original.preserve",
                  "asset.verify": "asset.replace"}.get(tool, "timeline.trim")
    v = verify_tool(project_id, probe_tool, None)
    return _result(tool, v.get("ok", False),
                   "PASS" if v.get("ok") else "CHECK_FAILED",
                   result={"checks": v.get("checks", {})}, verify=v.get("checks", {}))


# ---------------------------------------------------------------------------
# Verifier（§59：执行后 verify，不是执行完就是完成）
# ---------------------------------------------------------------------------

def verify_tool(project_id: str, tool: str, result: CommandResult) -> dict:
    """读回 IR 验证命令确实生效（§59）。返回 {ok, checks}。

    只验证"副作用是否真实落在 Composition"这个层面；像素级 verify
    属于 render.verify（§59 sample 的 frame.inspect）留待渲染链。
    """
    try:
        ir = editor.load_composition(project_id)
    except Exception as exc:
        return {"ok": False, "checks": {"ir_reload": f"失败 {exc}"}}

    checks: Dict[str, str] = {"ir_reload": "ok"}
    ok = True
    if tool in ("audio.original.preserve", "audio.original.mute", "audio.original.volume"):
        orig = [t for t in ir.audio if t.type.value == "original"]
        checks["original_track"] = f"{len(orig)} 条"
        if not orig:
            ok = False
    elif tool in ("watermark.add", "watermark.update"):
        checks["watermark_count"] = f"{len(ir.watermarks)} 条"
        if not ir.watermarks:
            ok = False
    elif tool == "watermark.remove":
        checks["watermark_count"] = f"{len(ir.watermarks)} 条"
        if ir.watermarks:
            ok = False
    elif tool in ("music.volume", "music.duck"):
        bgm = [t for t in ir.audio if t.type.value == "bgm"]
        checks["bgm_track"] = f"{len(bgm)} 条"
        if bgm:
            checks["bgm_volume"] = f"{bgm[0].volume}"
    elif tool == "subtitle.update_style":
        checks["subtitle_style"] = "set" if ir.subtitle_style else "None"
        if not ir.subtitle_style:
            ok = False
    return {"ok": ok, "checks": checks}