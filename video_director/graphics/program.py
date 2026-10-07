"""GraphicProgram / GraphicScene 数据契约（GFX-002）。

方案 §12：GraphicProgram 是"可执行视觉程序"的核心对象——
id/width/height/fps/duration + html/css/assets/animations/variables +
renderer + provenance。它**不是** LLM 直接生成的任意 HTML（方案 §14：
HTML 是执行格式，不是 AI 知识格式）；animated 结构经 Block Resolve +
Validator，compiler 才把它编译成 HTML。

GraphicScene 对应一个时序 clip（data-clip 契约）。每个 scene 绑定一个
GraphicBlock + 槽位值 + 一个 entrance motion，渲染器逐帧 scrub。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from video_director.graphics.contract import RationalFps, fps_from_number


class AssetRef(BaseModel):
    """GraphicProgram 引用的资产（§12 assets: AssetRef[]）。

    图形域资产可以是图片/字体/外部 SVG；渲染前必须本地化（§51）。
    """

    id: str
    type: str  # image | font | svg | video | audio
    file: str
    duration: Optional[float] = None
    source: str = "user"  # user | own | generated | third-party
    license_note: Optional[str] = None


class AnimationProgram(BaseModel):
    """动画程序（§12 animations: AnimationProgram[]）。

    动画是 Renderer 无关声明（§9）：property + keyframes + easing，
    由 CSS/WAAPI 等 adapter 各自 seekFrame 实现。禁止携带 shell/ffmpeg/js 原文。
    """

    id: str
    target: str  # scene id 或 clip id
    property: str  # opacity | transform | clip-path
    from_value: object = 0.0
    to_value: object = 1.0
    duration: float = 0.5
    easing: str = "ease-out"

    @model_validator(mode="after")
    def _check(self):
        if self.duration <= 0:
            raise ValueError(f"animation {self.id}: duration 必须为正")
        return self


class GraphicScene(BaseModel):
    """单 clip：角色 + 时长 + block 绑定 + 槽位值 + motion。"""

    id: str
    role: str  # hook/intro/body/feature/proof/outro/cta/conclusion
    start: float = 0.0
    duration: float = 3.0
    block_id: str
    slot_values: Dict[str, str] = Field(default_factory=dict)
    motion_id: str = "graphics.fade-up"
    # 渲染引擎：native 使用现有 Graphics Block；procedural 使用注册的 Procedural Provider。
    engine: str = "native"
    provider_id: Optional[str] = None
    program_id: Optional[str] = None

    @model_validator(mode="after")
    def _check(self):
        if self.duration <= 0:
            raise ValueError(f"scene {self.id}: duration 必须为正")
        if self.start < 0:
            raise ValueError(f"scene {self.id}: start 为负")
        if self.engine not in ("native", "procedural"):
            raise ValueError(f"scene {self.id}: engine {self.engine!r} 非法")
        if self.engine == "native" and not self.block_id:
            raise ValueError(f"scene {self.id}: native scene 必须有 block_id")
        if self.engine == "procedural" and (not self.provider_id or not self.program_id):
            raise ValueError(f"scene {self.id}: procedural scene 必须有 provider_id + program_id")
        return self


class GraphicProgram(BaseModel):
    """可执行视觉程序（GFX-002 契约，pydantic 校验）。

    §12 字段全集：id/width/height/fps/duration/html/css/assets/animations/
    variables/renderer/provenance。html/css 是 compiler 产物（执行格式），
    不是 AI 输入（§14）。
    """

    id: str
    width: int = 1080
    height: int = 1920
    fps: float = 30.0
    duration: float = 0.0
    scenes: List[GraphicScene] = Field(default_factory=list)
    assets: List[AssetRef] = Field(default_factory=list)
    animations: List[AnimationProgram] = Field(default_factory=list)
    variables: Dict[str, str] = Field(default_factory=dict)
    #: compiler 编译产物（执行格式，Optional；preview/render 前必须非空）
    html: Optional[str] = None
    css: Optional[str] = None
    renderer: str = "browser"  # GraphicRendererTarget：browser | unavailable
    style_id: str = "editorial"
    provenance_ref: str = "src-own"

    @model_validator(mode="after")
    def _check(self):
        if not self.scenes:
            raise ValueError("GraphicProgram 至少一个 scene")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("画布宽高必须为正")
        # 场景连续性（与 CompositionIR 同语义：无缝铺满）
        cursor = 0.0
        for sc in sorted(self.scenes, key=lambda s: s.start):
            if abs(sc.start - cursor) > 0.01:
                raise ValueError(
                    f"scene {sc.id} @{sc.start} 与前一场景末尾 {cursor} gap/overlap"
                )
            cursor = sc.start + sc.duration
        total = cursor
        if self.duration > 0 and abs(total - self.duration) > 0.05:
            raise ValueError(f"场景总长 {total} 与 duration {self.duration} 不一致")
        if self.renderer not in ("browser", "unavailable"):
            raise ValueError(f"renderer {self.renderer!r} 不在 GraphicRendererTarget 内")
        return self

    @property
    def rational_fps(self) -> RationalFps:
        return fps_from_number(self.fps)

    @property
    def total_frames(self) -> int:
        return self.rational_fps.frame_at(self.duration)

    def model_dump_exclude_html(self) -> dict:
        """序列化（不含 renderer 态 html/css；plan 持久化用）。"""
        d = self.model_dump()
        d.pop("html", None)
        d.pop("css", None)
        return d