"""Block Resolver（GFX-010）：创意意图 → 候选 block，零评分。

对齐 registries.resolver 的三态契约（方案 §18/§19）：
    SUPPORTED / MISSING_REQUIREMENT / INCOMPATIBLE
绝不返回 score/rating/概率/推荐指数（Jerry 禁止评分制铁律 + 方案 §18）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from video_director.graphics.blocks import Block, all_blocks


@dataclass(frozen=True)
class BlockCandidate:
    block_id: str
    name: str
    category: str
    compatibility: str  # SUPPORTED | MISSING_REQUIREMENT | INCOMPATIBLE
    reasons: Tuple[str, ...] = ()
    missing_requirements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BlockResolveRequest:
    semantic_intents: Tuple[str, ...]
    content_type: str = ""  # knowledge | product_video | ...
    style_id: Optional[str] = None
    duration: float = 3.0
    ratio: str = "9:16"


def resolve_blocks(req: BlockResolveRequest) -> List[BlockCandidate]:
    """意图/内容类型/风格/时长 → 候选 block（三态）。

    判定规则：
    - category 与 content_type 硬性匹配（knowledge ↔ knowledge 类；
      product ↔ product_video/advertisement），不匹配 → INCOMPATIBLE
    - 语义意图不含 block 的任何 intent → INCOMPATIBLE（当请求非空）
    - 时长越界 → MISSING_REQUIREMENT
    """
    out: List[BlockCandidate] = []
    for b in all_blocks():
        reasons: List[str] = []
        missing: List[str] = []
        compatible = True

        # 1. 内容类型 → category 映射
        ct = req.content_type
        want = "product" if ct in ("product_video", "advertisement", "product") else \
            "knowledge" if ct in ("knowledge", "knowledge_video", "concept") else None
        if want and b.category != want:
            compatible = False
            reasons.append(f"内容类型 {ct!r} 需要 {want} 类 block，而 {b.id} 是 {b.category}")

        # 2. 语义意图匹配（请求非空才硬性约束）
        if req.semantic_intents:
            hit = any(si in b.semantic_intents for si in req.semantic_intents)
            if not hit:
                compatible = False
                reasons.append(f"语义意图 {req.semantic_intents[0]!r} 不在 {b.semantic_intents}")

        # 3. 时长区间（越界不否决，降级 MISSING_REQUIREMENT）
        if not (b.duration_min - 0.01 <= req.duration <= b.duration_max + 0.01):
            if compatible is True:
                compatible = None  # type: ignore[assignment]
            missing.append(f"建议时长 {b.duration_min}-{b.duration_max}s，请求 {req.duration}s")

        if compatible is False:
            status = "INCOMPATIBLE"
        elif compatible is None:
            status = "MISSING_REQUIREMENT"
        else:
            status = "SUPPORTED"
            if not reasons:
                reasons.append("意图/内容类型/时长全部匹配")
        out.append(BlockCandidate(
            block_id=b.id, name=b.name, category=b.category,
            compatibility=status, reasons=tuple(reasons),
            missing_requirements=tuple(missing),
        ))
    return out