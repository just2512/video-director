"""GraphicProgram / GraphicScene 数据契约（GFX-002）。

方案 §12：GraphicProgram 是"可执行视觉程序"的核心对象——
width/height/fps/duration + 结构化 block/scene + 变量 + provenance。
它**不是** LLM 直接生成的任意 HTML（方案 §14：HTML 是执行格式，不是 AI 知识
格式）；它是经过 Block Resolve + Validator 的结构化对象，compiler 才把它编译
成 HTML。

GraphicScene 对应一个时序 clip（data-clip 契约）。每个 scene 绑定一个
GraphicBlock + 槽位值 + 一个 entrance motion，渲染器逐帧 scrub。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from video_director.graphics.contract import RationalFps, fps_from_number


class GraphicScene(BaseModel):
    """单 clip：角色 + 时长 + block 绑定 + 槽位值 + motion。"""

    id: str
    role: str  # hook/intro/body/feature/proof/outro/cta/conclusion
    start: float = 0.0
    duration: float = 3.0
    block_id: str
    slot_values: Dict[str, str] = Field(default_factory=dict)
    motion_id: str = "graphics.fade-up"

    @model_validator(mode="after")
    def _check(self):
        if self.duration <= 0:
            raise ValueError(f"scene {self.id}: duration 必须为正")
        if self.start < 0:
            raise ValueError(f"scene {self.id}: start 为负")
        return self


class GraphicProgram(BaseModel):
    """可执行视觉程序（GFX-002 契约，pydantic 校验）。

    provenance_ref 表明 block 内容的来源（own/CLEAR 自撰；第三方须独立授权）。
    """

    id: str
    width: int = 1080
    height: int = 1920
    fps: float = 30.0
    duration: float = 0.0
    scenes: List[GraphicScene] = Field(default_factory=list)
    variables: Dict[str, str] = Field(default_factory=dict)
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
        return self

    @property
    def rational_fps(self) -> RationalFps:
        return fps_from_number(self.fps)

    @property
    def total_frames(self) -> int:
        return self.rational_fps.frame_at(self.duration)

    def model_dump_exclude_html(self) -> dict:
        """序列化（不含渲染态；渲染态是 compiler 产物）。"""
        return self.model_dump()