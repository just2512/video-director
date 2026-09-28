"""MPT 吸收方案 §34：MusicPolicy 统一（BGM 决策不再各工作流自定）。

六档（§34 原文）：
    none            无 BGM（纯旁白/原声）
    local_random     本地库随机（按风格关键词）
    local_selected   本地库指定曲目（asset_id）
    stock            Stock 音乐（§25 商业安全矩阵管音乐素材）
    generated        AI 生成配乐（§28 music provider；无 provider 诚实不可用）
    video_matched    按参考视频风格匹配（reference 链）

现有渲染链已支持 none/local_random(auto)/local_selected(显式 asset)；
本模块把六档统一成一个 Policy 契约，compose/BGM 选择经同一入口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: §34 六档 MusicPolicy
POLICIES = ("none", "local_random", "local_selected", "stock", "generated", "video_matched")


class MusicPolicyError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True)
class MusicPolicy:
    """§34 统一 BGM 决策契约。"""

    policy: str = "none"
    asset_id: Optional[str] = None   # local_selected 必填
    style_keywords: tuple = ()       # local_random 检索关键词
    volume: float = 0.3
    ducking: bool = True
    fadeIn: float = 0.0
    fadeOut: float = 0.0

    def __post_init__(self):
        if self.policy not in POLICIES:
            raise MusicPolicyError("INVALID_POLICY",
                                   f"policy {self.policy!r} 非法（{POLICIES}）")
        if self.policy == "local_selected" and not self.asset_id:
            raise MusicPolicyError("MISSING_ASSET",
                                   "local_selected 需 asset_id（先 music.search）")
        if not (0.0 <= self.volume <= 2.0):
            raise MusicPolicyError("INVALID_VOLUME", f"volume {self.volume} 越界 [0,2]")

    def to_dict(self) -> dict:
        return {
            "policy": self.policy, "assetId": self.asset_id,
            "styleKeywords": list(self.style_keywords),
            "volume": self.volume, "ducking": self.ducking,
            "fadeIn": self.fadeIn, "fadeOut": self.fadeOut,
        }

    def availability(self) -> dict:
        """六档执行可用性（诚实）：无 provider 的档不伪造可用。"""
        from video_director import model_config as mc
        return {
            "none": True,
            "local_random": True,       # audio.scan_library（本地库）
            "local_selected": True,     # 显式 asset
            "stock": False,             # §25 Stock 音乐导入通道（音乐类目未开）
            "generated": mc.is_role_enabled("llm"),  # 配乐生成 provider（llm 角色参与判定）
            "video_matched": False,      # §68 Reference→风格匹配链（音乐维度未开）
        }.get(self.policy, False)
