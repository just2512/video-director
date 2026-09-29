"""§38 Adapter 防污染边界（系统能力归一方案）。

第三方系统（MPT / HyperFrames / Shotcraft / VideoLingo / Clypra）必须经 Adapter
翻译成 Creative OS Contract，不得直接污染 Core。

落地策略（不爆炸迁移）：
- 新接入的第三方系统 → 必须在本包登记 AdapterContract，实现走 provider/capability。
- 旧 Adapter（graphics/ 对应 HyperFrames、registries/ 对应 Shotcraft、
  voice/stock/audio 对应 MPT）暂不物理搬迁，往后真实修改到某处时「顺手迁移」
  （Boy Scout Migration，非 Big Bang Directory Migration）。

§3/§37 的物理目录重组（composer/ 分层）明确 DEFER：保持 video_director/ 现有结构，
本包与 capability_registry/graphics/registries 共同构成公共 import 边界。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class AdapterContract:
    """一条第三方系统接入契约（§55 五问的第 2/3 问：Contract + Provider/Adapter）。

    name          = 适配器名（kebab 目录名：money_printer_turbo / hyperframes / ...）
    source_system = 第三方系统（用于 provenance / 诚实溯源）
    target        = 目标 OS Contract（TTSProvider / StockProvider / GraphicsRenderer / ...）
    providers     = 注册进 ProviderRegistry 的 provider id 列表
    capabilities  = 注册进 Capability Registry 的能力 id 前缀（如 ["tts.", "stock."]）
    """

    name: str
    source_system: str
    target: str
    providers: Tuple[str, ...] = field(default_factory=tuple)
    capabilities: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source_system": self.source_system,
            "target": self.target,
            "providers": list(self.providers),
            "capabilities": list(self.capabilities),
        }


#: 已登记 Adapter 的登记册（新 Adapter 在此登记；旧 Adapter 兼容在注释说明）。
ADAPTER_REGISTRY: "dict[str, AdapterContract]" = {
    "money_printer_turbo": AdapterContract(
        name="money_printer_turbo",
        source_system="MoneyPrinterTurbo",
        target="TTSProvider / StockProvider / SubtitleProvider / MusicProvider / RendererProvider",
        providers=("edge", "kokoro", "pexels", "pixabay", "coverr"),
        capabilities=("tts.", "stock.", "subtitle.", "music.", "render."),
    ),
    "hyperframes": AdapterContract(
        name="hyperframes",
        source_system="HyperFrames",
        target="GraphicsRendererProvider",
        providers=(),
        capabilities=("graphic.",),
    ),
    "shotcraft": AdapterContract(
        name="shotcraft",
        source_system="Video Shotcraft",
        target="Shot / Motion / Transition Registry",
        providers=(),
        capabilities=("shot.", "motion.", "transition."),
    ),
}