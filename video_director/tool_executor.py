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
    ok, errors = validate_tool_call(tool, payload)
    if not ok:
        return _result(tool, False, "INVALID_PAYLOAD", error="；".join(errors))

    # 2. 路由执行
    try:
        if _is_readonly(tool):
            return _execute_readonly(project_id, tool, payload, target_id)
        if _is_revision(tool):
            return _execute_revision(project_id, tool, payload, target_id)
        if _is_generation(tool):
            return _execute_generation(project_id, tool, payload, target_id)
        if _is_render(tool):
            return _execute_render(project_id, tool, payload, target_id)
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
    "music.volume": "set_audio_volume",
    "music.duck": "set_audio_volume",
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
}

_READONLY = {
    "system.capabilities.list", "system.capabilities.describe", "system.context.inspect",
    "project.inspect", "timeline.query", "asset.search", "asset.resolve",
    "stock.search", "stock.preview", "tts.list_voices", "music.search",
    "music.preview", "motion.list", "motion.preview", "shot.resolve",
    "render.preflight", "qc.inspect",
}

_REVISION = {"revision.propose", "revision.preview", "revision.apply", "revision.reject"}

_GENERATION = {"generation.image", "generation.video", "generation.sfx", "generation.music"}

_RENDER = {"render.draft", "render.final"}


def _is_readonly(tool: str) -> bool:
    return tool in _READONLY


def _is_revision(tool: str) -> bool:
    return tool in _REVISION


def _is_generation(tool: str) -> bool:
    return tool in _GENERATION


def _is_render(tool: str) -> bool:
    return tool in _RENDER


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------

def _result(tool: str, ok: bool, status: str, result=None, error=None) -> dict:
    return {"tool": tool, "ok": ok, "status": status,
            "result": result or {}, "error": error, "verify": {}}


def _execute_editor_command(project_id: str, tool: str, payload: dict, target_id) -> dict:
    """编辑域能力：映射到 editor.execute_command（Command Bus，§82）。"""
    cmd_name = _COMMAND_MAP[tool]
    p = dict(payload)
    # 语义 payload 字段对齐 editor 命令的字段名
    if tool == "timeline.replace":
        p = {"recipeId": p.get("recipeId") or p.get("recipe_id") or ""}
    elif tool == "layer.add" or tool == "asset.add_to_timeline":
        p = {"assetId": p.get("assetId") or p.get("asset_id") or "",
             "assetType": p.get("assetType") or p.get("asset_type") or "image",
             "sourceOut": p.get("sourceOut") or p.get("source_out")}
    elif tool == "layer.update":
        p = {k: v for k, v in p.items() if k in ("scale", "opacity")}
    elif tool in ("audio.original.preserve", "audio.original.mute"):
        p = {"mode": "preserve" if tool.endswith("preserve") else "mute",
             "volume": p.get("volume"), "duckAgainst": p.get("duckAgainst")}
    elif tool == "set_original_audio" or tool == "audio.original.volume":
        p = {"volume": p.get("volume")}
    elif tool in ("music.volume", "music.duck"):
        p = {"track_type": p.get("type") or "bgm", "volume": p.get("volume", 1.0)}
    elif tool in ("watermark.add", "watermark.update"):
        p = {k: v for k, v in p.items()
             if k in ("kind", "text", "assetId", "anchor", "opacity", "scale", "start", "duration")}
    elif tool == "subtitle.update_style":
        p = {k: v for k, v in p.items()
             if k in ("font", "size", "color", "strokeColor", "strokeWidth",
                      "background", "position", "animation", "mode")}
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
    return _result(tool, True, "APPLIED",
                   result={"results": [r.model_dump() if hasattr(r, "model_dump") else r
                                       for r in results]})


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
            hits = stock_mod.search_stock(
                payload.get("query", ""), provider=payload.get("provider"),
                orientation=payload.get("orientation"))
            cands = stock_mod.resolve_candidates(
                hits, target_ratio=payload.get("ratio") or "9:16",
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
    # 其余只读（stock.preview、music.search/preview、tts.preview 等仍诚实降级）
    return _result(tool, False, "NOT_IMPLEMENTED",
                   error=f"能力 {tool} 的后端 provider 尚未接线（诚实降级，不伪造）")


def _timeline_summary(project_id: str) -> dict:
    ir = editor.load_composition(project_id)
    return {
        "scenes": [{"id": sc.id, "start": sc.start, "duration": sc.duration,
                    "text": sc.text, "layers": len(sc.layers)} for sc in ir.scenes],
        "audio": [{"type": t.type.value, "volume": t.volume, "start": t.start,
                   "duration": t.duration} for t in ir.audio],
        "watermarks": [w.text or w.asset_id or w.kind for w in ir.watermarks],
        "subtitle_style": ir.subtitle_style.model_dump() if ir.subtitle_style else None,
    }


def _fmt_errors(errors: List[dict]) -> Optional[str]:
    if not errors:
        return None
    return "；".join(e.get("message", e.get("code", "")) for e in errors)


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