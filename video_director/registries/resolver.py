"""Resolver 层（RES-001/002/003）：创作意图 → 候选，无评分。

方案铁律（§16/§RES-001）：不返回 score/rating/probability/ranking，
只返回三态 compatibility + reasons + missing_requirements。
不兼容必须在选择时就拒绝（IG-MOTION Compatibility：不能让用户选了
之后到 Render 才失败）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from video_director.registries.recipes import (
    COMPILABLE_PROPERTIES,
    MotionRegistry,
    ShotRegistry,
)
from video_director.registries.templates import TemplateRegistry


@dataclass(frozen=True)
class Candidate:
    """三态候选（Task Card RES-001 Contract）。"""

    recipe_id: str
    version: str
    name: str
    compatibility: str  # SUPPORTED | MISSING_REQUIREMENT | INCOMPATIBLE
    reasons: Tuple[str, ...] = ()
    missing_requirements: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# RES-001 Shot Resolver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShotResolveRequest:
    semantic_intents: Tuple[str, ...]
    content_type: str
    style_id: Optional[str] = None
    duration: float = 4.0
    available_asset_types: Tuple[str, ...] = ()
    constraints: Tuple[str, ...] = ()
    scene_position: Optional[str] = None  # first | last | middle（约束判断用）


def resolve_shots(req: ShotResolveRequest) -> List[Candidate]:
    """意图/内容类型/风格/时长/素材 → 候选镜头（三态，无评分）。"""
    registry = ShotRegistry()
    out: List[Candidate] = []
    for recipe in registry.all():
        reasons: List[str] = []
        missing: List[str] = []
        compatible = True

        # 1. 意图匹配（不匹配 → INCOMPATIBLE）
        intent_hit = any(
            si in req.semantic_intents for si in recipe.semantic_intents
        )
        if req.semantic_intents and not intent_hit:
            compatible = False
            reasons.append(f"语义意图不含 {req.semantic_intents[0]!r}")

        # 2. 内容类型匹配
        if req.content_type and req.content_type not in recipe.compatible_content_types:
            compatible = False
            reasons.append(f"内容类型 {req.content_type!r} 不兼容")

        # 3. 风格偏好（仅影响 reasons，不否决）
        style_bonus = False
        for binding in recipe.motion_bindings:
            if req.style_id and req.style_id in binding.preferred_styles:
                style_bonus = True
                reasons.append(f"绑定风格含 {req.style_id}")
                break

        # 4. 时长区间（越界 → MISSING_REQUIREMENT 而非否决语义）
        if not (recipe.duration_min <= req.duration <= recipe.duration_max):
            if compatible:
                compatible = None  # type: ignore[assignment]  # 语义兼容但时长越界
                missing.append(
                    f"建议时长 {recipe.duration_min}-{recipe.duration_max}s，请求 {req.duration}s"
                )

        # 5. 槽位素材（required 槽位无对应类型素材 → MISSING_REQUIREMENT）
        for slot in recipe.slots:
            if slot.required and slot.type not in req.available_asset_types:
                if compatible is not False:
                    if compatible is True:
                        compatible = None
                    missing.append(f"必需槽位 {slot.id}({slot.type}) 无可用素材")

        # 6. 硬约束（first_scene_only/last_scene_only）
        for c in recipe.constraints:
            if c == "first_scene_only" and req.scene_position not in (None, "first"):
                compatible = False
                reasons.append("仅可用于首场景")
            if c == "last_scene_only" and req.scene_position not in (None, "last"):
                compatible = False
                reasons.append("仅可用于末场景")

        if compatible is False:
            status = "INCOMPATIBLE"
        elif compatible is None:
            status = "MISSING_REQUIREMENT"
        else:
            status = "SUPPORTED"
            if not reasons:
                reasons.append("意图/内容/时长/素材全部匹配")
        out.append(Candidate(
            recipe_id=recipe.id, version=recipe.version, name=recipe.name,
            compatibility=status, reasons=tuple(reasons),
            missing_requirements=tuple(missing),
        ))
    return out


# ---------------------------------------------------------------------------
# RES-002 Motion Resolver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MotionResolveRequest:
    layer_type: str
    semantic_intent: str
    style_id: Optional[str] = None
    duration: float = 0.5


def resolve_motions(req: MotionResolveRequest) -> List[Candidate]:
    """Layer 类型/语义意图 → 可用动效（不兼容明确拒绝）。"""
    registry = MotionRegistry()
    out: List[Candidate] = []
    for recipe in registry.all():
        reasons: List[str] = []
        missing: List[str] = []
        compatible = True

        # 1. Layer 兼容性（硬性：subtitle 不许用 camera 类等）
        if req.layer_type not in recipe.compatible_layers:
            compatible = False
            reasons.append(f"不兼容图层类型 {req.layer_type!r}")

        # 2. 语义意图
        if req.semantic_intent and req.semantic_intent not in recipe.semantic_intents:
            compatible = False
            reasons.append(f"语义意图不含 {req.semantic_intent!r}")

        # 3. 渲染器可编译性（选了就要能渲：不可编译属性 → INCOMPATIBLE）
        uncompilable = [
            ch.property for ch in recipe.channels
            if ch.property not in COMPILABLE_PROPERTIES
        ]
        if uncompilable and "renderer_preview_only" not in recipe.constraints:
            compatible = False
            reasons.append(f"渲染器暂不支持属性 {uncompilable}")
        elif uncompilable:
            # renderer_preview_only：预览可用，正式渲染受约束（诚实标注）
            compatible = None if compatible is True else compatible
            missing.append(f"正式渲染暂不支持 {uncompilable}（仅预览）")

        # 4. 时长区间
        if not (recipe.duration_min <= req.duration <= recipe.duration_max):
            if compatible is True:
                compatible = None
            missing.append(
                f"建议时长 {recipe.duration_min}-{recipe.duration_max}s，请求 {req.duration}s"
            )

        if compatible is False:
            status = "INCOMPATIBLE"
        elif compatible is None:
            status = "MISSING_REQUIREMENT"
        else:
            status = "SUPPORTED"
            if not reasons:
                reasons.append("图层/意图/时长全部匹配")
        out.append(Candidate(
            recipe_id=recipe.id, version=recipe.version, name=recipe.name,
            compatibility=status, reasons=tuple(reasons),
            missing_requirements=tuple(missing),
        ))
    return out


# ---------------------------------------------------------------------------
# RES-003 Template Resolver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TemplateResolveRequest:
    content_type: str
    ratio: str
    duration: float
    style_id: Optional[str] = None
    required_slot_types: Tuple[str, ...] = ()


def resolve_templates(req: TemplateResolveRequest) -> List[Candidate]:
    """内容类型/风格/画幅/时长/槽位 → 可实例化模板。"""
    registry = TemplateRegistry()
    out: List[Candidate] = []
    for tpl in registry.all():
        reasons: List[str] = []
        missing: List[str] = []
        compatible = True

        # 1. 画幅（不支持 → INCOMPATIBLE）
        if req.ratio not in tpl.supported_ratios:
            compatible = False
            reasons.append(f"不支持画幅 {req.ratio}")

        # 2. 风格
        tpl_style = tpl.style_contract.get("style_id")
        if req.style_id and tpl_style and req.style_id != tpl_style and compatible:
            # 风格不匹配不算否决，列为 MISSING_REQUIREMENT 提示用户
            compatible = None
            missing.append(f"模板风格 {tpl_style} 与请求 {req.style_id} 不同")

        # 3. 时长
        if tpl.duration_min is not None and req.duration < tpl.duration_min:
            if compatible is True:
                compatible = None
            missing.append(f"最短 {tpl.duration_min}s，请求 {req.duration}s")
        if tpl.duration_max is not None and req.duration > tpl.duration_max:
            if compatible is True:
                compatible = None
            missing.append(f"最长 {tpl.duration_max}s，请求 {req.duration}s")

        # 4. 必需槽位
        slot_types = {s.type for s in tpl.slots}
        for need in req.required_slot_types:
            if need not in slot_types:
                compatible = False
                reasons.append(f"缺少槽位类型 {need!r}")

        if compatible is False:
            status = "INCOMPATIBLE"
        elif compatible is None:
            status = "MISSING_REQUIREMENT"
        else:
            status = "SUPPORTED"
            if not reasons:
                reasons.append("画幅/时长/槽位全部匹配")
        out.append(Candidate(
            recipe_id=tpl.id, version=tpl.version, name=tpl.name,
            compatibility=status, reasons=tuple(reasons),
            missing_requirements=tuple(missing),
        ))
    return out
