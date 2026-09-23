"""SegmentationProvider：主体抠图接口（v1.2 §25-§27/§31）。

设计原则（§31 硬约束）：
    只有需要"主体脱离背景 / 前景背景分层 / 2.5D parallax"才用 segmentation。
    普通局部放大只需 crop+zoom（image_editor.crop_region + render_image_animation
    的 focus_zoom 动效），**绝不**引入 SAM2 或任何分割模型。

接口定位（§27）：
    - SegmentationProvider 只做「已指定对象的抠图」，不做语义理解
    - 调用方必须先通过 VisionProvider / ImageAnalyzer 确认主体位置，
      再把 bbox 或 label 传进来，provider 返回 mask / 简化 bbox
    - 默认实例（NoSegmentProvider）抛 NotImplementedError 或返回纯色近似兜底
    - SAM2 / REM2 / BackgroundMatting 等任何具体实现都是可选适配器，
      通过环境变量 SEG_PROVIDER=sam2 激活，默认不装任何重依赖

零外部依赖：仅 stdlib + numpy（若已装，用于 mask 数组）；
无 numpy 时返回 bbox 形式的简化 mask（纯 Python list）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from loguru import logger


class SegmentationError(Exception):
    """SegmentationProvider 结构性失败（provider 不可用 / 主体未找到 / 调用错误）。"""


# ---------------------------------------------------------------------------
# 数据契约
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentationMask:
    """segment() 返回的 mask 契约。

    字段语义：
        width / height      原图分辨率
        bbox                {x, y, w, h} 主体包围盒（像素坐标，已 clamp 到图内）
        mask                可选二值 mask（height × width，每像素 0/1）。
                            若 provider 无 numpy 或只做 bbox 近似，mask 为 None
        method              实际使用的方法（如 "sam2" / "rem2" / "solid_color_bbox"）
        provider            provider 类名（供 run manifest 追溯）
        notes               降级说明 / warning（如"返回纯色近似，非真 mask"）
    """

    width: int
    height: int
    bbox: Dict[str, int]
    mask: Optional[List[List[int]]] = None
    method: str = "solid_color_bbox"
    provider: str = "NoSegmentProvider"
    notes: str = ""


# ---------------------------------------------------------------------------
# Provider 统一接口
# ---------------------------------------------------------------------------


class SegmentationProvider(ABC):
    """主体抠图接口（§27）。

    职责边界（重要）：
        - **不**做语义理解（不知道"这是猫还是狗"）
        - **只**做"已指定对象的抠图"（调用方传 target_label / target_bbox，
          provider 把该对象从背景分离）
        - 普通局部放大 → 用 image_editor.crop_region 即可，不要调用本接口

    实现者须知：
        - available() 必须诚实：未装 SAM2/REM2 时返回 False
        - segment() 失败抛 SegmentationError，调用方逐级降级到 crop+zoom
        - 返回 SegmentationMask；若只能给 bbox 近似，method="solid_color_bbox"
          并在 notes 写明，让调用方知道这不是真 mask
    """

    @abstractmethod
    def available(self) -> bool:
        """是否可用（未装模型时返回 False，调用方走 crop+zoom 兜底）。"""
        ...

    @abstractmethod
    def segment(
        self,
        image_path: str,
        target_label: Optional[str] = None,
        target_bbox: Optional[Dict[str, int]] = None,
    ) -> SegmentationMask:
        """对指定对象抠图。

        Args:
            image_path: 图片路径
            target_label: 可选语义标签（如 "person"）。
                          provider 可用它做多对象场景的目标选择；
                          无语义能力的 provider 应忽略此参数并走 target_bbox。
            target_bbox: 可选 {x, y, w, h} 主体位置（来自 ImageAnalyzer.focal_point
                         或 VisionProvider 结果）。无语义能力的 provider 必须接受
                         此参数作为抠图目标。

        Returns:
            SegmentationMask

        Raises:
            SegmentationError: provider 不可用 / 主体未找到 / 渲染失败
        """
        ...


# ---------------------------------------------------------------------------
# 默认实现：纯色近似兜底（§27 默认不装 SAM2）
# ---------------------------------------------------------------------------


class NoSegmentProvider(SegmentationProvider):
    """默认 provider：永远不可用，segment() 抛 NotImplementedError。

    设计意图（§31）：
        - 普通场景下根本不应走到这里——需要"主体脱离背景"才用，
          需要"局部放大"请用 image_editor.crop_region + focus_zoom
        - 若调用方确实需要 parallax/前景分层却没装 SAM2，这里诚实抛错，
          让上层降级到"crop+zoom 的伪 parallax"（image_editor 已支持）

    不引入任何外部依赖（numpy/torch/transformers 全不 import）。
    """

    def available(self) -> bool:
        return False

    def segment(
        self,
        image_path: str,
        target_label: Optional[str] = None,
        target_bbox: Optional[Dict[str, int]] = None,
    ) -> SegmentationMask:
        raise SegmentationError(
            "NoSegmentProvider: 未配置任何分割模型（§27 默认不装 SAM2）。"
            "普通局部放大请用 image_editor.crop_region + focus_zoom；"
            "确需主体脱离背景/前景分层请配置 SAM2Provider（未实现）。"
        )


class SolidColorBboxProvider(SegmentationProvider):
    """纯色近似 provider：把 bbox 区域当作"主体"，返回 bbox 形式的简化 mask。

    适用场景（§31 降级路径）：
        - 调用方确实想要一个 SegmentationMask（接口消费方），
          但环境没装 SAM2，又不愿意让 pipeline 直接挂掉
        - 返回的 mask 是 bbox 的矩形填充，不是像素级精度——
          调用方必须在 notes 看到 "solid_color_bbox" 时知道这是近似

    零依赖：纯 Python list，不 import numpy。
    若后续装 numpy，可扩展 mask 为真实二维数组，但 bbox 精度不变。
    """

    def available(self) -> bool:
        return True  # 零依赖兜底，永远可用

    def segment(
        self,
        image_path: str,
        target_label: Optional[str] = None,
        target_bbox: Optional[Dict[str, int]] = None,
    ) -> SegmentationMask:
        from video_director import prober

        if target_bbox is None:
            raise SegmentationError(
                "SolidColorBboxProvider 需要 target_bbox（无语义能力，"
                "调用方必须先用 ImageAnalyzer/VisionProvider 指定主体位置）"
            )

        probe = prober.probe_image(image_path)
        w = probe.width or 0
        h = probe.height or 0
        if w == 0 or h == 0:
            raise SegmentationError(f"无法探测图片分辨率: {image_path}")

        # clamp bbox 到图内
        bx = max(0, min(int(target_bbox.get("x", 0)), w - 1))
        by = max(0, min(int(target_bbox.get("y", 0)), h - 1))
        bw = max(1, min(int(target_bbox.get("w", 0)), w - bx))
        bh = max(1, min(int(target_bbox.get("h", 0)), h - by))

        # 构造纯色近似 mask：bbox 内为 1，其余为 0（二维 list）
        # 大图时 mask 可能很大，这里只构造 bbox 外圈 padding=0 的最小表示
        # —— 但契约要求 height × width，所以按需展开
        # 性能：1920x1080 → 2M 个 int，~16MB 内存，可接受
        mask: List[List[int]] = [
            [1 if (bx <= x < bx + bw and by <= y < by + bh) else 0
             for x in range(w)]
            for y in range(h)
        ]

        return SegmentationMask(
            width=w,
            height=h,
            bbox={"x": bx, "y": by, "w": bw, "h": bh},
            mask=mask,
            method="solid_color_bbox",
            provider="SolidColorBboxProvider",
            notes="纯色近似 mask（bbox 矩形填充），非像素级精度。"
                  "确需主体脱离背景请配置 SAM2Provider。",
        )


# ---------------------------------------------------------------------------
# Provider 选择（按环境变量，默认 NoSegmentProvider）
# ---------------------------------------------------------------------------


def get_segmentation_provider() -> SegmentationProvider:
    """按环境变量 SEG_PROVIDER 选择 provider（默认 NoSegmentProvider）。

    支持的值：
        none (默认)       NoSegmentProvider：永远不可用，抛 NotImplementedError
        solid_color       SolidColorBboxProvider：bbox 纯色近似，永远可用
        sam2              SAM2Provider（未实现，留接口位）

    调用方须知（§31）：
        - 不要在"局部放大"场景调用本函数——用 image_editor.crop_region
        - 只在"主体脱离背景/前景背景分层/2.5D parallax"时才查 provider
    """
    import os

    kind = os.environ.get("SEG_PROVIDER", "none").strip().lower()

    if kind == "solid_color":
        return SolidColorBboxProvider()
    if kind == "sam2":
        try:
            # 未来 SAM2 适配器在此 import（避免顶层 import 重依赖）
            # from video_director.adapters.sam2_provider import SAM2Provider
            # return SAM2Provider()
            raise SegmentationError(
                "SAM2Provider 未实现（§27 接口预留，未装 SAM2）"
            )
        except SegmentationError:
            raise
        except Exception as exc:
            logger.warning(
                f"SAM2Provider 加载失败，降级到 NoSegmentProvider: {exc}"
            )
            return NoSegmentProvider()
    # 默认
    return NoSegmentProvider()


__all__ = [
    "SegmentationProvider",
    "SegmentationMask",
    "SegmentationError",
    "NoSegmentProvider",
    "SolidColorBboxProvider",
    "get_segmentation_provider",
]
