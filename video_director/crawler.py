"""MPT 吸收方案 §26/§27：LLM 自主联网找素材的边界设计 + Crawler 不是 Asset Provider。

§26 边界：
    Discovery Provider → Candidate URL → Source Page → License Evidence →
    Rights Resolver → Commercial Status
    只有 CLEAR 才能自动进入 Commercial Publish；REVIEW/UNKNOWN 只能作为
    "推荐素材"，不能自动进入最终商业成片。

§27 Crawler 只返回 CandidateSource（不是 Asset）：
    {url, title, sourceSite, licenseEvidence, commercialStatus: UNKNOWN}
    用户点"确认使用"后才进入正式 Asset Resolution 流程。

同时必须遵守目标站点 ToS/robots/访问限制，不绕过登录/付费墙/验证码。

第一版：CandidateSource 数据契约 + Rights Resolver（基于 licenseEvidence
关键词的诚实判定）+ robots.txt 检查。不实现真实爬取（无授权站点不爬），
但契约层完整——未来接入任何 Discovery Provider 都走同一管道。
"""

from __future__ import annotations

import urllib.robotparser
from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger


class CrawlerError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True)
class CandidateSource:
    """§27：Crawler 返回候选来源（不是 Asset）。

    commercialStatus 默认 UNKNOWN（§26：未过 Rights Resolver 不给状态）。
    """

    url: str
    title: str
    source_site: str
    license_evidence: str          # 许可证据描述/链接（空=无证据）
    commercial_status: str = "UNKNOWN"
    media_type: str = "video"      # video | image | audio

    def to_dict(self) -> dict:
        return {
            "url": self.url, "title": self.title,
            "sourceSite": self.source_site,
            "licenseEvidence": self.license_evidence,
            "commercialStatus": self.commercial_status,
            "mediaType": self.media_type,
        }


#: §26 Rights Resolver：licenseEvidence → Commercial Status 的判定规则。
#: 关键词证据 → 状态映射（诚实：证据不明确一律 UNKNOWN，不猜 CLEAR）。
_RIGHTS_RULES = [
    (("public domain", "公有领域", "cc0", "CC0"), "CLEAR"),
    (("creative commons", "cc by", "cc-by", "知识共享"), "REVIEW"),  # CC 有署名义务
    (("royalty free", "免版税"), "REVIEW"),     # 免版税≠可商用转售
    (("commercial use", "可商用", "允许商业"), "REVIEW"),  # 仍需核对具体条款
    (("youtube", "yt-dlp"), "BLOCKED"),        # 平台内容非源站许可
    (("all rights reserved", "版权所有", "保留所有权利"), "BLOCKED"),
]


def resolve_rights(candidate: CandidateSource) -> str:
    """§26 Rights Resolver：licenseEvidence 关键词判定商业状态。

    返回 CLEAR / REVIEW / UNKNOWN / BLOCKED。无证据 → UNKNOWN（诚实）。
    """
    evidence = (candidate.license_evidence or "").lower()
    if not evidence.strip():
        return "UNKNOWN"
    for keywords, status in _RIGHTS_RULES:
        if any(k.lower() in evidence for k in keywords):
            return status
    return "UNKNOWN"


def resolve_candidate(candidate: CandidateSource) -> CandidateSource:
    """对候选跑 Rights Resolver，返回带商业状态的候选（§26 完整管道）。"""
    status = resolve_rights(candidate)
    # frozen dataclass：重建
    return CandidateSource(
        url=candidate.url, title=candidate.title,
        source_site=candidate.source_site,
        license_evidence=candidate.license_evidence,
        commercial_status=status, media_type=candidate.media_type,
    )


def can_auto_publish(candidate: CandidateSource) -> bool:
    """§26：只有 CLEAR 能自动进入 Commercial Publish。"""
    return candidate.commercial_status == "CLEAR"


def check_robots(url: str, user_agent: str = "*") -> bool:
    """§27 合规：遵守目标站点 robots.txt（不绕过访问控制）。

    读取 site/robots.txt 判定该 URL 是否允许抓取。robots 不可达 →
    保守允许（robots 缺失默认允许，RFC 9309），但记录日志。
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if not parsed.scheme.startswith("http"):
            return False
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(user_agent, url)
    except Exception as exc:
        logger.info(f"robots.txt 不可达（默认允许，RFC 9309）: {exc}")
        return True


def confirm_use(candidate: CandidateSource) -> dict:
    """§27：用户点"确认使用"后的入口——进入正式 Asset Resolution 流程。

    这里只做状态确认（候选 → 待导入），真实下载/探测由 assets.build_manifest
    在文件落盘后执行（现有链路），本函数不直接产生 Asset。
    """
    if candidate.commercial_status == "BLOCKED":
        raise CrawlerError("BLOCKED_SOURCE", f"来源 {candidate.source_site} 许可为保留所有权利，禁止使用")
    return {
        "confirmed": True,
        "candidate": candidate.to_dict(),
        "next": "asset_resolution",  # 进入正式 Asset Resolution（用户确认后）
    }