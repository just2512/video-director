"""确定性预检（GFX-027）+ 沙箱策略（GFX-026）。

方案 §48-52：生成的 HTML 本质是代码，进入渲染前必须确定性校验 + 隔离运行。
两个独立能力：

    preflight(html)  扫描非确定性/网络/外链标记 → REJECT（诚实暴露，禁挽救到"好像通过"）
    sandbox          渲染层用隔离 origin 的 Chromium context + 网络阻断

确定性预检清单（§50）：
    No Date.now / performance.now / Math.random / rAF / crypto 随机（帧必须由 frame 决定）
    No fetch / XHR / WebSocket / <script src / 外链 css（网络依赖破坏确定性 + 沙箱逃逸面）
    字体本地化：不得 @import 或 url(http) 外链字体
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from video_director.graphics.contract import (
    FORBIDDEN_EXT_TOKENS,
    FORBIDDEN_ND_TOKENS,
)


@dataclass
class PreflightReport:
    status: str  # PASS | REJECT
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"status": self.status, "issues": self.issues}


def deterministic_preflight(html: str) -> PreflightReport:
    """确定性预检（GFX-027）。

    1. 非确定性标记 → REJECT（ND_*）
    2. 网络/外链标记 → REJECT（EXT_*）
    """
    issues: List[str] = []
    for tok in FORBIDDEN_ND_TOKENS:
        if tok in html:
            issues.append(f"ND_TIMING: 检测到非确定性标记 {tok!r}（frame 必须由帧号决定）")
    for tok in FORBIDDEN_EXT_TOKENS:
        if tok in html:
            issues.append(f"EXT_NETWORK: 检测到网络/外链标记 {tok!r}（渲染须自包含）")
    status = "PASS" if not issues else "REJECT"
    return PreflightReport(status, issues)