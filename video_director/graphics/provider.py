"""RendererProvider 抽象（§5 Graphics Renderer Provider 架构）。

方案 §5：
    interface RendererProvider {
      id;
      canRender(input: RenderCapability): Compatibility;
      compile(composition): RenderArtifact;   // 结构化 → 可执行 HTML
      preview(composition): PreviewArtifact;  // 浏览器 iframe 即时预览
      render(artifact, config): RenderResult; // 逐帧捕获 + ffmpeg 编码
    }

五类 provider（§5）：ffmpeg / hyperframes / browser / gpu / image-sequence。
本仓库第一版实现 browser（Playwright Chromium + ffmpeg），其余 provider
诚实返回 UNAVAILABLE（§77：Provider 可返回 SUPPORTED/UNAVAILABLE，禁止假成功）。

Contract（§4/§47）：上层系统只依赖本 Provider 抽象，不依赖具体 Chromium/
Playwright；未来换掉 Graphics 执行后端（如接入 real HyperFrames engine）
只动这里，上层不变（Adapter Boundary）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol


class GraphicProviderError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True)
class RenderCapability:
    """渲染能力输入（§5 canRender 参数）。"""

    width: int
    height: int
    fps: int
    layer_types: tuple  # 该 composition 涉及的 graphics layer 类型


@dataclass
class RenderArtifact:
    """compiler 产物（§65 Graphic Compile API 输出）。"""

    program_id: str
    html: str
    css: str = ""
    assets: List[str] = field(default_factory=list)
    runtime: str = "browser"  # 执行后端
    compiler_version: str = "1.0.0"


@dataclass
class PreviewArtifact:
    """preview 产物（§53：同一 composition 的浏览器预览态）。"""

    html: str
    sandbox: dict = field(default_factory=lambda: {
        "isolated_origin": True, "block_network": True})


@dataclass
class RenderResult:
    """render 产物（§56：draft/final 同 composition 不同质量）。"""

    output_path: str
    duration: float
    frames: int
    width: int
    height: int
    fps: int


class RendererProvider(Protocol):
    """§5 RendererProvider 契约。"""

    id: str

    def can_render(self, capability: RenderCapability) -> str:
        """返回 SUPPORTED | UNAVAILABLE。"""
        ...

    def compile(self, program) -> RenderArtifact:
        ...

    def preview(self, program) -> PreviewArtifact:
        ...

    def render(self, program, out_path: str, quality: str = "draft") -> RenderResult:
        ...


# ---------------------------------------------------------------------------
# Browser Provider（Playwright Chromium + ffmpeg）
# ---------------------------------------------------------------------------

_BROWSER_LAYER_TYPES = (
    "dom", "html", "svg", "canvas", "chart", "diagram", "ui", "code",
    "table", "map", "data_viz",
)


class BrowserRendererProvider:
    """浏览器 Graphics 执行后端（§6：HTML → 逐帧 seek → 捕获 → ffmpeg）。"""

    id = "browser"

    def can_render(self, capability: RenderCapability) -> str:
        if capability.width <= 0 or capability.height <= 0 or capability.fps <= 0:
            return "UNAVAILABLE"
        return "SUPPORTED"

    def compile(self, program) -> RenderArtifact:
        from video_director.graphics.compiler import compile_to_html
        html = compile_to_html(program)
        return RenderArtifact(
            program_id=program.id, html=html, css="",
            runtime=self.id, assets=[],
        )

    def preview(self, program) -> PreviewArtifact:
        from video_director.graphics.compiler import compile_to_html
        from video_director.graphics.preflight import deterministic_preflight
        html = compile_to_html(program)
        rep = deterministic_preflight(html)
        if rep.status != "PASS":
            raise GraphicProviderError("NONDETERMINISTIC_HTML", "; ".join(rep.issues))
        return PreviewArtifact(html=html)

    def render(self, program, out_path: str, quality: str = "draft") -> RenderResult:
        from video_director.graphics.render import render_graphic_program
        render_graphic_program(program, out_path, quality=quality)
        rfp = program.rational_fps
        fps = int(rfp.value) if quality == "final" else max(15, int(rfp.value))
        return RenderResult(
            output_path=out_path, duration=program.duration,
            frames=max(1, int(program.duration * fps)),
            width=program.width, height=program.height, fps=fps,
        )


# ---------------------------------------------------------------------------
# 其它 Provider（§5 五类；未实现诚实 UNAVAILABLE，禁止假成功）
# ---------------------------------------------------------------------------

class FfmpegRendererProvider:
    """FFmpeg 直接渲染 provider（本仓库 media renderer 即此路径，Graphics 域未接线）。"""

    id = "ffmpeg"

    def can_render(self, capability: RenderCapability) -> str:
        return "UNAVAILABLE"  # §77：Graphics 域走 browser，不走 ffmpeg 直渲


class HyperframesRendererProvider:
    """接入真实 HyperFrames engine 的 adapter（§84：Adapter Boundary，未来）。"""

    id = "hyperframes"

    def can_render(self, capability: RenderCapability) -> str:
        return "UNAVAILABLE"  # 未接入 @hyperframes/engine，诚实不可用


def resolve_provider(program) -> RendererProvider:
    """Render Target Resolver（§3）：按 program.renderer 选 provider。"""
    if program.renderer == "browser":
        return BrowserRendererProvider()
    raise GraphicProviderError(
        "PROVIDER_UNAVAILABLE", f"renderer {program.renderer!r} 无可用 provider"
    )


_PROWIDERS = {
    BrowserRendererProvider.id: BrowserRendererProvider(),
    FfmpegRendererProvider.id: FfmpegRendererProvider(),
    HyperframesRendererProvider.id: HyperframesRendererProvider(),
}


def list_providers() -> List[dict]:
    """全部 provider 及其能力状态（§5 五类，能力面板可展示）。"""
    cap = RenderCapability(width=1080, height=1920, fps=30, layer_types=("html",))
    out = []
    for p in _PROWIDERS.values():
        out.append({
            "id": p.id,
            "can_render": p.can_render(cap),
        })
    return out