"""Graphics Renderer（GFX-006/007）：HTML → deterministic frames → MP4。

方案 §6/§7：
    HTML → Deterministic Composition Runtime → Seek frame N → Chromium BeginFrame
    → PNG frame → FFmpeg → MP4

两条路径（方案 §55/§56 Preview/Final 共 Runtime）：
    render_graphic_html(html, fps, frames, out, width, height, draft)
        → 逐帧 window.__gf.seek(frame) → 截图 PNG → ffmpeg 编码 MP4

渲染参数（方案 §56）：
    draft = 低分辨率/低碳水（快速预览）
    final = 完整分辨率（正式交付）
帧数由总时长 × fps 决定（frame 是唯一时间事实）；每帧之间零 wall-clock 依赖。

依赖：playwright（已随项目 E2E 依赖）+ bundled ffmpeg（prober._resolve_ffmpeg）。
provider unavailable 时抛 GraphicsRenderError（诚实，禁止 fake success）。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from loguru import logger

from video_director import prober
from video_director.graphics import sandbox as sandbox_mod
from video_director.graphics.preflight import deterministic_preflight


class GraphicsRenderError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


def _playwright_chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except ImportError:
        return False


def render_graphics(
    html: str,
    out_path: str,
    width: int,
    height: int,
    fps: int,
    total_frames: int,
    theme: str = "editorial",
    quality: str = "draft",  # draft | final
    font_stack: str = "'Microsoft YaHei', system-ui, sans-serif",
) -> str:
    """HTML → PNG 帧序列 → FFmpeg → MP4（GFX-006/007）。

    Args:
        html: 已编译的确定性 HTML（含 window.__gf.seek）
        out_path: 输出 mp4 路径
        width/height: 画布尺寸
        fps: 帧率
        total_frames: 总帧数（= 时长 × fps，frame 是唯一时间事实）
        quality: draft（低清快速）/ final（完整分辨率）
    """
    if not _playwright_chromium_available():
        raise GraphicsRenderError(
            "RENDERER_UNAVAILABLE", "playwright 未安装，Graphics Renderer 不可用"
        )

    # 确定性预检（渲染前闸门，GFX-027）
    report = deterministic_preflight(html)
    if report.status == "REJECT":
        raise GraphicsRenderError(
            "NONDETERMINISTIC_HTML",
            "HTML 未通过确定性预检：" + "; ".join(report.issues),
        )

    ff = prober._resolve_ffmpeg()
    if not ff or not (Path(ff).is_file() or _is_on_path(ff)):
        raise GraphicsRenderError("FFMPEG_UNAVAILABLE", "ffmpeg 二进制不可用")

    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="gfx_") as tmp:
        frame_dir = Path(tmp) / "frames"
        frame_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(args=list(sandbox_mod.chromium_args()))
            ctx = browser.new_context(viewport={"width": width, "height": height})
            page = ctx.new_page()
            # 网络阻断：渲染页禁发起任何请求（§49 沙箱）
            page.route("**/*", lambda route: route.abort())
            page.set_content(html, wait_until="load")

            for n in range(total_frames):
                page.evaluate(f"window.__gf && window.__gf.seek({n})")
                # CSS 变量 → style recalc → paint 需要一个渲染帧；等 2 个 vsync
                # 保证截图捕获的是 seek 后状态（frame 是唯一时间事实的采样保障）
                page.wait_for_timeout(33)
                # 截图整页（画布已 = viewport）
                page.screenshot(path=str(frame_dir / f"frame_{n:05d}.png"))

            browser.close()

        # FFmpeg 编码：PNG 序列 → MP4（确定性，frame 顺序即时间）
        cmd = [
            ff, "-y", "-loglevel", "error",
            "-framerate", str(fps),
            "-i", str(frame_dir / "frame_%05d.png"),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-crf", "18" if quality == "final" else "28",
            str(out_path),
        ]
        r = subprocess.run(
            [str(c) for c in cmd], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
        if r.returncode != 0:
            raise GraphicsRenderError(
                "FFMPEG_ENCODE_FAILED", (r.stderr or "")[-800:]
            )

    if not Path(out_path).is_file():
        raise GraphicsRenderError("RENDER_EMPTY", "编码后未产出 mp4")
    logger.info(
        f"graphics render: {total_frames} frames @{fps}fps {width}x{height} "
        f"[{quality}] → {out_path}"
    )
    return out_path


def _is_on_path(exe: str) -> bool:
    import shutil
    return shutil.which(exe) is not None


def render_still(
    html: str,
    out_path: str,
    width: int,
    height: int,
    frame: int = 0,
) -> str:
    """HTML → 单帧 PNG（EDT-008 生成素材用：静态视觉卡）。

    frame 是唯一时间事实：seek 到目标帧后截一帧，产出确定性 PNG。
    """
    if not _playwright_chromium_available():
        raise GraphicsRenderError("RENDERER_UNAVAILABLE", "playwright 未安装")

    report = deterministic_preflight(html)
    if report.status == "REJECT":
        raise GraphicsRenderError(
            "NONDETERMINISTIC_HTML",
            "HTML 未通过确定性预检：" + "; ".join(report.issues),
        )

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(args=list(sandbox_mod.chromium_args()))
        ctx = browser.new_context(viewport={"width": width, "height": height})
        page = ctx.new_page()
        page.route("**/*", lambda route: route.abort())
        page.set_content(html, wait_until="load")
        page.evaluate(f"window.__gf && window.__gf.seek({frame})")
        page.wait_for_timeout(33)
        page.screenshot(path=out_path)
        browser.close()
    if not Path(out_path).is_file() or Path(out_path).stat().st_size == 0:
        raise GraphicsRenderError("RENDER_EMPTY", "截图未产出 PNG")
    return out_path


def render_graphic_program(
    program,
    out_path: str,
    quality: str = "draft",
    draft_scale: float = 0.5,
    draft_fps: Optional[int] = None,
) -> str:
    """GraphicProgram → 编译 HTML → 渲染 MP4（一站式入口，方案 §55/§56）。

    draft：半分辨率 + 可降 fps（快速预览）；final：完整分辨率/帧率。
    编译产物与 preview 共用 compile_to_html → Preview/Final parity。
    """
    from video_director.graphics.compiler import compile_to_html

    html = compile_to_html(program)
    rfp = program.rational_fps
    fps = int(rfp.value) if quality == "final" else (draft_fps or max(15, int(rfp.value)))
    width = program.width if quality == "final" else max(320, int(program.width * draft_scale))
    height = program.height if quality == "final" else max(320, int(program.height * draft_scale))
    total_frames = max(1, int(program.duration * fps))
    return render_graphics(
        html, out_path, width, height, fps, total_frames,
        theme=program.style_id, quality=quality,
    )