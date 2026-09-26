"""Visual Element 核心单位（§86 最终系统定位）。

方案 §86：不要再把「视频素材」当系统的核心视觉单位——核心单位是
Visual Element，它可以是 Video/Image/HTML/SVG/Chart/Diagram/Text/UI/3D/
Generated Media，统一成为 Layer，由 Motion/Style/Timing/Interaction 驱动。

本模块是这一定位的**代码化**：
    - VISUAL_ELEMENT_TYPES：全部视觉元素类型（媒体域 + 图形域）的权威清单
    - element_domain(type)：该元素归 Media Runtime 还是 Graphics Runtime（§83）
    - capability_map()：§61 Capability Map 的可查询形态（GRAPHICS/CREATIVE
      CATALOG/GENERATION/EDITOR/MEDIA/RENDER 六域）

§83 最终架构的执行侧映射：
    Understand/Create/Import → Creative Director → Composition IR
    → {Media Runtime | Graphics Runtime | Audio Runtime} → Timeline
    → Preview → Human Review → Revision → Local Rebuild
    → Deterministic Render → QC
"""

from __future__ import annotations

from typing import Dict, List

#: §86 Visual Element 全集（媒体域 + §11 图形域）
VISUAL_ELEMENT_TYPES = (
    # Media 域（FFmpeg Media Runtime）
    "video", "image", "text", "background", "logo", "person", "shape", "icon",
    # Graphics 域（§11：程序化视觉，Graphics Runtime 渲染）
    "dom", "html", "svg", "canvas", "chart", "diagram", "ui", "webgl",
    "webgpu", "lottie", "three_scene", "code", "table", "map", "data_viz",
    # Generated 域（生成媒体：AI 生图/生视频产物，Media Runtime 消费）
    "generated_image", "generated_video",
)

#: 元素 → Runtime 域（§83 三 Runtime 分流）
_ELEMENT_DOMAIN: Dict[str, str] = {
    **{t: "media" for t in (
        "video", "image", "text", "background", "logo", "person", "shape",
        "icon", "generated_image", "generated_video")},
    **{t: "graphics" for t in (
        "dom", "html", "svg", "canvas", "chart", "diagram", "ui", "webgl",
        "webgpu", "lottie", "three_scene", "code", "table", "map", "data_viz")},
}


def element_domain(element_type: str) -> str:
    """Visual Element → 所属 Runtime（media | graphics；未知返回 unknown 诚实暴露）。"""
    return _ELEMENT_DOMAIN.get(element_type, "unknown")


def capability_map() -> Dict[str, List[str]]:
    """§61 Capability Map（能力地图，非开发批次）——系统自描述。"""
    return {
        "GRAPHICS": [
            "Graphic Composition", "HTML Compiler", "CSS/SVG Runtime",
            "Frame Seek Protocol", "Renderer Adapter",
        ],
        "CREATIVE CATALOG": [
            "Shot", "Block ×37（knowledge 20 / product 16 / custom）",
            "Motion", "Template", "Style",
        ],
        "GENERATION": [
            "Text → Graphic（知识/产品动效视频）",
        ],
        "EDITOR": [
            "Scene", "Block", "Motion", "Timeline", "Inspector",
            "AI Agent（提案式）", "Source Mode（开发者）", "Preview",
        ],
        "MEDIA": [
            "Resolve", "Freeze", "Provenance", "License", "Reuse", "Generation",
        ],
        "RENDER": [
            "Draft", "Final", "MP4",
        ],
    }


def system_identity() -> dict:
    """§90 系统定位（代码化的自描述，首页/文档/能力面板消费）。"""
    return {
        "name": "AI Video Creative OS",
        "core_loop": [
            "理解创意", "编排内容", "选择视觉语言", "组合媒体与程序化图形",
            "进入可编辑时间线", "人工确认", "AI 局部修改", "局部重建",
            "确定性渲染", "版本化", "复用",
        ],
        "three_changes": {  # §89 三变化
            "change_1": "视频从『媒体拼接』变成『程序化视觉组合』",
            "change_2": "AI 从『生成素材』升级成『编写视觉程序』",
            "change_3": "Renderer 从系统核心变成可替换执行层（Provider 抽象）",
        },
        "hyperframes_position": "Graphics Execution Backend（Adapter Boundary，可替换）",  # §84
    }
