"""Honest runtime readiness for template/engine metadata.

Registry metadata describes intent; it does not prove a template can be rendered.
This module checks installed media dependencies and every referenced recipe, then
separates executable capability from partial visual semantics (notably animated
text layers, which the current FFmpeg compositor does not implement as animations).
"""
from __future__ import annotations

from typing import Any, Dict, List

from video_director import prober
from video_director.registries.core import RegistryError
from video_director.registries.recipes import MotionRegistry, ShotRegistry


SUPPORTED_CAPABILITIES = {
    "image_render",
    "video_render",
    "text_render",  # static text rendered as a timed FFmpeg overlay
    "audio_mix",
}
SUPPORTED_TEMPLATE_LAYER_TYPES = {"image", "video", "text", "title"}


def template_runtime_report(template: Any) -> dict:
    """Return a user-facing readiness report without claiming an untested MP4.

    READY means declared runtime requirements are available and no known
    requested operation would be dropped. PARTIAL means a usable static render
    exists but requested motion/layer semantics are not implemented. BLOCKED
    means an engine dependency, capability, or referenced recipe is unavailable.
    Actual render/decode evidence is deliberately reported separately.
    """
    blockers: List[dict] = []
    warnings: List[dict] = []
    installed: Dict[str, bool] = {}
    ffmpeg = prober._resolve_ffmpeg()
    installed["ffmpeg"] = bool(ffmpeg)
    if not ffmpeg:
        blockers.append({
            "code": "ENGINE_NOT_INSTALLED",
            "message": "未找到 FFmpeg；安装 FFmpeg 并确认 ffmpeg/ffprobe 可执行后重试。",
        })

    required = list(getattr(template, "required_capabilities", ()) or ())
    missing = sorted(set(required) - SUPPORTED_CAPABILITIES)
    for capability in missing:
        blockers.append({
            "code": "UNSUPPORTED_CAPABILITY",
            "capability": capability,
            "message": f"当前 FFmpeg 引擎未声明支持模板要求的能力：{capability}",
        })

    if "text_render" in required:
        try:
            from video_director.title_engine import find_cjk_font
            has_font = bool(find_cjk_font())
        except Exception:
            has_font = False
        installed["text_font"] = has_font
        if not has_font:
            blockers.append({
                "code": "TEXT_FONT_MISSING",
                "message": "模板需要文字渲染，但未检测到可用字体；系统此前可能静默跳过文字，当前会阻止误报可执行。",
            })

    shot_registry = ShotRegistry()
    motion_registry = MotionRegistry()
    referenced_shots = set()
    referenced_motions = set()
    requested_static_text = []
    unsupported_layer_types = set()
    text_motion_refs = set()

    for scene in getattr(template, "scenes", ()):
        shot_id = getattr(scene, "shot_recipe_id", None)
        if shot_id:
            referenced_shots.add(shot_id)
            try:
                shot = shot_registry.get(shot_id)
            except RegistryError as exc:
                blockers.append({
                    "code": "SHOT_RECIPE_MISSING",
                    "reference": shot_id,
                    "message": f"模板引用的镜头配方不可解析：{exc}",
                })
            else:
                for plan in getattr(shot, "layer_plan", ()):
                    layer_type = getattr(plan, "layer_type", "")
                    layer_type = getattr(layer_type, "value", layer_type)
                    if layer_type not in {"image", "video", "text", "title"}:
                        unsupported_layer_types.add(str(layer_type))
        for layer in getattr(scene, "layers", ()):
            layer_type = getattr(layer, "type", "")
            if layer_type not in SUPPORTED_TEMPLATE_LAYER_TYPES:
                unsupported_layer_types.add(str(layer_type))
            motion_id = getattr(layer, "motion_recipe_id", None)
            if motion_id:
                referenced_motions.add(motion_id)
                try:
                    motion_registry.get(motion_id)
                except RegistryError as exc:
                    blockers.append({
                        "code": "MOTION_RECIPE_MISSING",
                        "reference": motion_id,
                        "message": f"模板引用的动效配方不可解析：{exc}",
                    })
                if layer_type in {"text", "title"}:
                    # A WatermarkLayer has a real time window, but it does not
                    # run MotionRecipe keyframes. Never advertise this as animated.
                    text_motion_refs.add(motion_id)
            if layer_type in {"text", "title"}:
                requested_static_text.append(getattr(layer, "id", ""))

    for layer_type in sorted(unsupported_layer_types):
        warnings.append({
            "code": "LAYER_PLAN_NOT_IMPLEMENTED",
            "layer_type": layer_type,
            "message": f"镜头配方声明了 {layer_type} 层；当前编译器/FFmpeg 时间线不会把它作为独立图层渲染。",
        })
    if text_motion_refs:
        warnings.append({
            "code": "TEXT_MOTION_NOT_IMPLEMENTED",
            "references": sorted(text_motion_refs),
            "message": "文字内容可作为定时静态覆盖层渲染，但对应文字入场/逐字/形变动效尚未执行；预览/导出不会伪装成动效已实现。",
        })

    status = "BLOCKED" if blockers else ("PARTIAL" if warnings else "READY")
    return {
        "template_id": getattr(template, "id", ""),
        "template_version": getattr(template, "version", ""),
        "engine": (getattr(template, "runtime", {}) or {}).get("renderer") or "ffmpeg",
        "status": status,
        "can_instantiate": not blockers,
        "can_render": not blockers,
        "sample_render": "NOT_RUN",
        "installed": installed,
        "required_capabilities": required,
        "supported_capabilities": sorted(set(required) & SUPPORTED_CAPABILITIES),
        "referenced_shots": sorted(referenced_shots),
        "referenced_motions": sorted(referenced_motions),
        "static_text_layers": requested_static_text,
        "warnings": warnings,
        "blockers": blockers,
        "contract": "runtime preflight only; READY is not proof of MP4 render/decode until a sample render is run",
    }


def style_runtime_report(style: Any) -> dict:
    """Styles are metadata until bound to a renderer contract or verified sample."""
    return {
        "style_id": getattr(style, "id", ""),
        "style_version": getattr(style, "version", ""),
        "status": "METADATA_ONLY",
        "can_render": False,
        "sample_render": "NOT_RUN",
        "message": "当前风格只有描述/标签/provenance 元数据，尚无经过运行验证的执行包与样片。不能仅凭风格名称声称可执行。",
    }
