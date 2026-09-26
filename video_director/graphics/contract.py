"""Graphics Composition Contract（GFX-001）：data-* 时序契约 + 有理 fps。

方案 §7/§8：frame 是唯一时间事实，禁止 wall-clock 决定画面。
本模块定义"Graphics Program 可以被确定性渲染"所需的结构化契约——
HTML 侧靠 data-composition-* 与 data-clip-* 属性自描述时序；
Python 侧靠 GraphicProgram（program.py）承载同一时序，两者一一对应。

HTML 契约（由 compiler 生成，本文件定义其 schema 供 preflight 校验）：
    data-composition-id  稳定 id
    data-width/height    画布
    data-fps             有理帧率（numerator/denominator，渲染器据此逐帧）
    data-duration        总时长（秒）
    data-clip-start/-duration  每个 clip 的时序窗口（frame 唯一时间事实）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class RationalFps:
    """有理帧率（方案 §7 runtime protocol：numerator/denominator）。

    30/1、30000/1001 等精确表达；禁止 float 漂移累积决定帧号。
    """

    numerator: int
    denominator: int = 1

    @property
    def value(self) -> float:
        return self.numerator / self.denominator

    @property
    def seconds_per_frame(self) -> float:
        return self.denominator / self.numerator

    def frame_at(self, seconds: float) -> int:
        """秒 → 帧号（向下取整，frame 是唯一时间事实）。"""
        return int(seconds * self.numerator / self.denominator)

    def seconds_at(self, frame: int) -> float:
        return frame * self.denominator / self.numerator


#: 契约允许的确定性关键帧属性（GFX-004 CSS/SVG Runtime 可编译）
#: 与 registries 的 Motion 属性白名单独立——这是 Graphics 侧的确定性动画。
DETERMINISTIC_ANIM_PROPS = (
    "opacity",
    "transform",
    "translate",
    "scale",
    "rotate",
    "clip-path",
)


#: 确定性预检禁用的非确定性标记（方案 §50）
FORBIDDEN_ND_TOKENS = (
    "Date.now",
    "performance.now",
    "Math.random",
    "requestAnimationFrame",
    "crypto.getRandomValues",
)

#: 确定性预检禁用的运行时依赖（网络/外链/未定行为）
FORBIDDEN_EXT_TOKENS = (
    "fetch(",
    "XMLHttpRequest",
    "WebSocket",
    "@import",
    "url(http",
    "url(https",
    "<script src",
    "<link rel=\"stylesheet\"",
)


def fps_from_number(value) -> RationalFps:
    """数值 fps → 有理 fps（对齐 HyperFrames runtimeProtocolFpsFromNumber 语义）。"""
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = 30.0
    if not (0 < n < 1000):
        n = 30.0
    if n == int(n):
        return RationalFps(int(n), 1)
    den = 1_000_000
    num = round(n * den)
    g = _gcd(num, den)
    return RationalFps(num // g, den // g)


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a or 1