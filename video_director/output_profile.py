"""MPT 吸收方案 §43/§44：OutputProfile（4K 作为新导出档）+ 诚实提示。

§43 六档：
    1080p-landscape / 1080p-portrait / 1080p-square
    4k-landscape / 4k-portrait / 4k-square

§44 诚实边界：1080p 视频/720p 图片源导出 4K 时，Preflight 必须提示
"源素材本身不是 4K"，不能说"4K 高清画质"然后实际只是放大四倍。
HTML/SVG/Text/Chart/Vector 可原生 4K。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

#: 六档 Output Profile（§43：target 分辨率 + 像素维度）
PROFILES: Dict[str, dict] = {
    "1080p-landscape": {"width": 1920, "height": 1080, "label": "1080p 横屏 1920×1080"},
    "1080p-portrait": {"width": 1080, "height": 1920, "label": "1080p 竖屏 1080×1920"},
    "1080p-square": {"width": 1080, "height": 1080, "label": "1080p 方形 1080×1080"},
    "4k-landscape": {"width": 3840, "height": 2160, "label": "4K 横屏 3840×2160", "is_4k": True},
    "4k-portrait": {"width": 2160, "height": 3840, "label": "4K 竖屏 2160×3840", "is_4k": True},
    "4k-square": {"width": 2160, "height": 2160, "label": "4K 方形 2160×2160", "is_4k": True},
}

#: 矢量/程序化层（可直接 4K 渲染，不属"放大"欺骗 §44）
VECTOR_LAYER_TYPES = {"svg", "html", "dom", "canvas", "chart", "diagram",
                      "ui", "table", "data_viz", "text", "shape", "icon", "code"}


@dataclass(frozen=True)
class ProfileResolution:
    width: int
    height: int
    is_4k: bool


def resolve_profile(profile: Optional[str], fallback_w: int = 1080,
                    fallback_h: int = 1920) -> ProfileResolution:
    """按 profile 名解析目标分辨率；非 4K profile 或未知 profile 回退 IR 尺寸。"""
    p = PROFILES.get(profile or "")
    if p is None:
        return ProfileResolution(width=fallback_w, height=fallback_h, is_4k=False)
    return ProfileResolution(width=p["width"], height=p["height"],
                             is_4k=bool(p.get("is_4k")))


def profile_labels() -> Dict[str, str]:
    """§43 前端导出档选择器数据源。"""
    return {k: v["label"] for k, v in PROFILES.items()}


def preflight_4k_honesty(ir, assets) -> list:
    """§44：4K 导出前诚实校验。

    返回 warning 列表。vector/HTML 层 → 可原生 4K（无警告）；
    raster 层（video/image）源分辨率 < 4K → 提示"源素材本身非 4K"。
    """
    warnings = []
    for sc in ir.scenes:
        for lay in sc.layers:
            if lay.type.value in VECTOR_LAYER_TYPES:
                continue
            a = assets.get(lay.asset_id or "")
            if a is None:
                continue
            # raster 源 < 4K 阈值（宽或高不足 2160 且非 4K 宽）
            if (a.width or 0) < 2160 and (a.height or 0) < 2160:
                warnings.append(
                    f"层 {lay.asset_id} 源为 "
                    f"{a.width or '?'}×{a.height or '?'}，非 4K，导出为等比放大"
                )
    return warnings