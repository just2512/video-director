"""Single catalog for executable Studio choices.

This module adapts existing registries/contracts; it does not create a parallel
style, template, or renderer registry. Metadata is selectable only when its declared
runtime, required capabilities, referenced recipes, and actual host dependencies agree.
"""
from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from video_director import capabilities, prober, style
from video_director.graphics.provider import list_providers
from video_director.registries.templates import TemplateRegistry


def _executable(command: str) -> str | None:
    if not command:
        return None
    candidate = Path(command)
    if candidate.is_file():
        return str(candidate)
    return shutil.which(command)


@lru_cache(maxsize=1)
def _ffmpeg_status() -> tuple[str, str, str]:
    try:
        resolved = prober._resolve_ffmpeg()
        executable = _executable(resolved)
        if not executable:
            return "MISSING", "", "找不到可执行的 FFmpeg；无法生成最终 MP4。"
        run = subprocess.run([executable, "-version"], capture_output=True, text=True, timeout=5)
        if run.returncode != 0:
            return "MISSING", executable, (run.stderr or "FFmpeg 启动失败")[:240]
        version = (run.stdout or "").splitlines()[0] if run.stdout else "FFmpeg 可运行"
        return "PASS", executable, version[:240]
    except Exception as exc:
        return "MISSING", "", f"FFmpeg 环境检查失败：{exc}"[:240]


@lru_cache(maxsize=1)
def _browser_status() -> tuple[str, str]:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
        return "PASS", "Playwright Chromium 可真实启动，可用于 Graphics 图形层预览/逐帧渲染。"
    except Exception as exc:
        return "MISSING", f"Graphics 浏览器执行环境不可用：{exc}"[:240]


def _runtime_status(renderer_id: str, ffmpeg_status: str, browser_status: str) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if renderer_id in ("ffmpeg", "media-ffmpeg"):
        if ffmpeg_status != "PASS":
            reasons.append("最终 MP4 所需 FFmpeg 不可执行")
    elif renderer_id in ("browser", "browser-graphics"):
        if browser_status != "PASS":
            reasons.append("Playwright Chromium 不可执行")
        if ffmpeg_status != "PASS":
            reasons.append("将浏览器帧编码为 MP4 所需 FFmpeg 不可执行")
    elif renderer_id in ("hyperframes", "remotion"):
        reasons.append(f"{renderer_id} 尚无本仓库可执行的 Provider")
    else:
        reasons.append(f"声明的引擎 {renderer_id or '(未声明)'} 没有匹配的执行适配器")
    return ("PASS" if not reasons else "MISSING"), reasons


def build_studio_catalog() -> dict:
    """Return capability-backed choices for the existing creation/editor workflow."""
    ffmpeg_status, ffmpeg_path, ffmpeg_detail = _ffmpeg_status()
    browser_status, browser_detail = _browser_status()
    providers = {item["id"]: item["can_render"] for item in list_providers()}

    engines = [
        {
            "id": "ffmpeg", "name": "FFmpeg",
            "scope": "full_video", "status": ffmpeg_status,
            "selectable": ffmpeg_status == "PASS", "detail": ffmpeg_detail,
            "path": ffmpeg_path,
        },
        {
            "id": "browser", "name": "Playwright Chromium",
            "scope": "graphics_layer", "status": browser_status,
            "selectable": browser_status == "PASS" and ffmpeg_status == "PASS",
            "detail": browser_detail,
            "provider_contract": providers.get("browser", "MISSING"),
        },
        {
            "id": "hyperframes", "name": "HyperFrames",
            "scope": "graphics_layer", "status": "MISSING",
            "selectable": False,
            "detail": "当前仓库的 HyperframesRendererProvider 返回 UNAVAILABLE；未接通真实引擎。",
            "provider_contract": providers.get("hyperframes", "UNAVAILABLE"),
        },
    ]

    styles = []
    for style_id in style.available_styles():
        contract = style.get_style(style_id)
        styles.append({
            "id": style_id, "name": style_id,
            "engine_id": "ffmpeg", "status": ffmpeg_status,
            "selectable": ffmpeg_status == "PASS",
            "reason": list(contract.reason) if contract else [],
            "contract": ({
                "subtitle_position": contract.subtitle_position,
                "subtitle_font_size": contract.subtitle_font_size,
                "subtitle_max_lines": contract.subtitle_max_lines,
                "image_motion_intensity": contract.image_motion_intensity,
                "transition_budget": contract.transition_budget,
                "bgm_volume": contract.bgm_volume,
                "sfx_density": contract.sfx_density,
            } if contract else {}),
            "detail": "由现有 StyleContract 与媒体渲染流水线消费；不是仅导入的名称。",
        })

    available_caps: dict[str, str] = {}
    try:
        for name, cap in capabilities.registry().items():
            available_caps[name] = str(getattr(cap, "status", "MISSING")).upper()
    except Exception:
        available_caps = {}

    # Reuse the canonical registries. A valid metadata record alone is not enough.
    from video_director.shotbook import _motion_recipes, _recipes
    recipe_ids = {item.get("id") for item in _recipes()}
    motion_ids = {item.get("id") for item in _motion_recipes()}
    templates = []
    try:
        candidates = TemplateRegistry().all()
    except Exception as exc:
        candidates = []
        registry_error = str(exc)[:240]
    else:
        registry_error = ""

    for template in candidates:
        renderer_id = str((template.runtime or {}).get("renderer") or "")
        runtime_status, reasons = _runtime_status(renderer_id, ffmpeg_status, browser_status)
        missing_caps = []
        for name in template.required_capabilities:
            cap_status = available_caps.get(str(name), "MISSING")
            if cap_status == "MISSING":
                missing_caps.append(str(name))
        if missing_caps:
            reasons.append("所需能力不可用/未登记：" + "、".join(missing_caps[:8]))
        missing_recipes = []
        for scene in template.scenes:
            if scene.shot_recipe_id and scene.shot_recipe_id not in recipe_ids:
                missing_recipes.append(str(scene.shot_recipe_id))
            for layer in scene.layers:
                if layer.motion_recipe_id and layer.motion_recipe_id not in motion_ids:
                    missing_recipes.append(str(layer.motion_recipe_id))
        if missing_recipes:
            reasons.append("引用的镜头/动效配方不存在：" + "、".join(sorted(set(missing_recipes))[:8]))
        # Registry metadata and valid recipes do not themselves instantiate a template
        # into CompositionIR. No canonical Template -> Editor Command adapter currently
        # exists; keep it discoverable but explicitly non-selectable rather than faking support.
        reasons.append("当前没有从 TemplateDefinition 到 CompositionIR 的正式实例化执行适配器")
        status = "MISSING"
        templates.append({
            "id": template.id, "version": template.version, "name": template.name,
            "category": template.category, "supported_ratios": list(template.supported_ratios),
            "duration": {"min": template.duration_min, "recommended": template.duration_recommended,
                         "max": template.duration_max},
            "renderer": renderer_id, "required_capabilities": list(template.required_capabilities),
            "status": status, "selectable": status == "PASS",
            "commercial_status": template.commercial_status,
            "provenance_ref": template.provenance_ref,
            "detail": "模板已通过当前环境兼容检查。" if status == "PASS" else "；".join(reasons),
        })
    return {
        "ok": True, "engines": engines, "styles": styles, "templates": templates,
        "default_video_engine": "ffmpeg" if ffmpeg_status == "PASS" else "",
        "registry_error": registry_error,
        "rules": [
            "最终视频仅显示当前可执行的媒体渲染引擎。",
            "样式必须由现有 StyleContract 消费；模板还必须满足引擎、能力和配方依赖。",
            "仅有元数据而没有执行 Provider 的引擎不会被标为可选。",
        ],
    }
