"""Source Diversity / Source Usage Policy。

目标：
- source_preference：声明来源偏好，不变成隐藏数值。
- diversity_policy：有可替代项时避免同素材/同来源连续使用。
- reuse_policy：明确“不得复用”或“资源不足时允许复用”。
- 真实来源不足时放宽规则而不是制造 MISSING。
- 输出只包含 ACCEPT / DEFER / REJECT / UNKNOWN 与事实原因。

来源大类：
uploaded/local → 用户素材
stock          → 网络图库；provider 再细分
generated      → AI 生成
extracted      → 从已有媒体提取
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence


ACCEPT = "ACCEPT"
DEFER = "DEFER"
REJECT = "REJECT"
UNKNOWN = "UNKNOWN"

USER_FIRST = "user_first"
STOCK_FIRST = "stock_first"
GENERATED_FIRST = "generated_first"

REUSE_WHEN_NEEDED = "reuse_when_needed"
NEVER_REUSE = "never_reuse"

PREFER_UNIQUE_SOURCE = "prefer_unique_source"
ALLOW_REUSE = "allow_reuse"


@dataclass(frozen=True)
class SourcePolicy:
    source_preference: str = USER_FIRST
    reuse_policy: str = REUSE_WHEN_NEEDED
    diversity_policy: str = PREFER_UNIQUE_SOURCE
    avoid_consecutive_asset: bool = True

    def normalized(self) -> "SourcePolicy":
        return SourcePolicy(
            source_preference=(
                self.source_preference
                if self.source_preference in {
                    USER_FIRST, STOCK_FIRST, GENERATED_FIRST
                } else USER_FIRST
            ),
            reuse_policy=(
                self.reuse_policy
                if self.reuse_policy in {REUSE_WHEN_NEEDED, NEVER_REUSE}
                else REUSE_WHEN_NEEDED
            ),
            diversity_policy=(
                self.diversity_policy
                if self.diversity_policy in {PREFER_UNIQUE_SOURCE, ALLOW_REUSE}
                else PREFER_UNIQUE_SOURCE
            ),
            avoid_consecutive_asset=bool(self.avoid_consecutive_asset),
        )


@dataclass
class UsageState:
    used_assets: dict[str, int] = field(default_factory=dict)
    used_sources: dict[str, int] = field(default_factory=dict)
    used_providers: dict[str, int] = field(default_factory=dict)
    last_asset_id: str = ""
    last_source: str = ""
    last_provider: str = ""
    scene_count: int = 0

    def record(self, asset: Any) -> None:
        aid = asset_id(asset)
        source = source_kind(asset)
        provider = source_provider(asset)
        if aid:
            self.used_assets[aid] = self.used_assets.get(aid, 0) + 1
        if source:
            self.used_sources[source] = self.used_sources.get(source, 0) + 1
        if provider:
            self.used_providers[provider] = self.used_providers.get(provider, 0) + 1
        self.last_asset_id = aid
        self.last_source = source
        self.last_provider = provider
        self.scene_count += 1


@dataclass(frozen=True)
class UsageDecision:
    status: str
    reason: str
    source: str
    provider: str = ""


def asset_id(asset: Any) -> str:
    if isinstance(asset, Mapping):
        return str(asset.get("asset_id") or asset.get("assetId") or asset.get("id") or "")
    return str(getattr(asset, "asset_id", "") or getattr(asset, "id", "") or "")


def source_kind(asset: Any) -> str:
    if isinstance(asset, Mapping):
        raw = asset.get("source") or ""
        prov = asset.get("provider") or (asset.get("provenance") or {}).get("provider") or ""
    else:
        raw = getattr(asset, "source", "") or ""
        prov = getattr(asset, "provenance", None) or {}
        prov = prov.get("provider") if isinstance(prov, Mapping) else ""
    raw = str(raw).strip().lower()
    prov = str(prov).strip().lower()
    if raw in {"user", "uploaded", "local"}:
        return "uploaded"
    if raw in {"stock", "pexels", "pixabay", "coverr"}:
        return "stock"
    if raw in {"generated", "self-generated"}:
        return "generated"
    if raw == "extracted":
        return "extracted"
    if prov in {"pexels", "pixabay", "coverr"}:
        return "stock"
    return raw or "unknown"


def source_provider(asset: Any) -> str:
    if isinstance(asset, Mapping):
        raw = asset.get("provider") or ""
        prov = asset.get("provenance") or {}
        raw = raw or (prov.get("provider") if isinstance(prov, Mapping) else "")
    else:
        prov = getattr(asset, "provenance", None) or {}
        raw = (prov.get("provider") if isinstance(prov, Mapping) else "")
        raw = raw or getattr(asset, "provider", "") or ""
    return str(raw).strip().lower()


def preferred_source_order(preference: str) -> tuple[str, ...]:
    table = {
        USER_FIRST: ("uploaded", "stock", "generated", "extracted", "unknown"),
        STOCK_FIRST: ("stock", "uploaded", "generated", "extracted", "unknown"),
        GENERATED_FIRST: ("generated", "uploaded", "stock", "extracted", "unknown"),
    }
    return table.get(preference, table[USER_FIRST])


def _has_alternative(
    candidate: Any,
    candidates: Sequence[Any],
    policy: SourcePolicy,
    state: UsageState,
) -> bool:
    aid = asset_id(candidate)
    for other in candidates:
        oid = asset_id(other)
        if not oid or oid == aid:
            continue
        decision = decide(other, state, policy, candidates=candidates, allow_relax=True)
        if decision.status != REJECT:
            return True
    return False


def decide(
    candidate: Any,
    state: UsageState,
    policy: SourcePolicy | None = None,
    *,
    candidates: Sequence[Any] = (),
    allow_relax: bool = False,
) -> UsageDecision:
    policy = (policy or SourcePolicy()).normalized()
    aid = asset_id(candidate)
    source = source_kind(candidate)
    provider = source_provider(candidate)

    if not aid:
        return UsageDecision(UNKNOWN, "素材没有稳定 asset_id", source, provider)

    if (
        policy.reuse_policy == NEVER_REUSE
        and state.used_assets.get(aid, 0) > 0
    ):
        return UsageDecision(REJECT, "reuse_policy=never_reuse，素材已经使用过", source, provider)

    # Stock 内细分 provider：Pexels→Pixabay 比 Pexels→Pexels 更有实际意义。
    # 提前于 asset 连续检查——同一 asset 必同 provider，provider 级复用避免
    # 对 stock 用户更可操作（"换图库"比"换素材"更能解释决策）。
    if (
        source == "stock"
        and provider
        and state.last_provider == provider
        and not allow_relax
    ):
        other_provider = any(
            asset_id(other) != aid
            and source_kind(other) == "stock"
            and source_provider(other)
            and source_provider(other) != provider
            for other in candidates
        )
        if other_provider:
            return UsageDecision(
                DEFER,
                f"存在其他图库 Provider，避免连续使用 {provider}",
                source,
                provider,
            )

    if (
        policy.avoid_consecutive_asset
        and state.last_asset_id
        and aid == state.last_asset_id
        and _has_alternative(candidate, candidates, policy, state)
        and not allow_relax
    ):
        return UsageDecision(DEFER, "存在其他可用素材，避免同一素材连续出现", source, provider)

    if (
        policy.diversity_policy == PREFER_UNIQUE_SOURCE
        and state.last_source
        and source == state.last_source
    ):
        other_source = any(
            asset_id(other) != aid
            and source_kind(other) != source
            and decide(other, state, policy, candidates=(), allow_relax=True).status != REJECT
            for other in candidates
        )
        if other_source and not allow_relax:
            return UsageDecision(
                DEFER,
                f"存在其他来源，当前优先切换来源（{source}）",
                source,
                provider,
            )

    if (
        state.used_assets.get(aid, 0) > 0
        and policy.reuse_policy == REUSE_WHEN_NEEDED
    ):
        return UsageDecision(ACCEPT, "素材已使用过，允许按 reuse_when_needed 复用", source, provider)

    return UsageDecision(ACCEPT, "满足当前来源与复用规则", source, provider)


def choose(
    candidates: Sequence[Any],
    state: UsageState,
    policy: SourcePolicy | None = None,
    *,
    preferred_asset_id: str = "",
) -> tuple[Optional[Any], UsageDecision]:
    """保持语义主选项优先，只有冲突时才寻找替代项。"""
    policy = (policy or SourcePolicy()).normalized()
    ordered = list(candidates)
    if preferred_asset_id:
        ordered.sort(
            key=lambda item: (
                0 if asset_id(item) == preferred_asset_id else 1,
                preferred_source_order(policy.source_preference).index(
                    source_kind(item)
                ) if source_kind(item) in preferred_source_order(policy.source_preference)
                else 99,
            )
        )

    deferred: list[tuple[Any, UsageDecision]] = []
    preferred_deferred_reason = ""
    for item in ordered:
        decision = decide(item, state, policy, candidates=ordered)
        if decision.status == ACCEPT:
            if preferred_deferred_reason:
                # 首选因来源/复用规则被 defer，最终选了次选——决策原因说明偏离首选的依据，
                # 避免调用方只看到笼统的"满足规则"而无从解释为什么没选语义首选。
                decision = UsageDecision(
                    ACCEPT,
                    f"首选素材已使用；{preferred_deferred_reason}",
                    decision.source,
                    decision.provider,
                )
            return item, decision
        if decision.status == DEFER:
            deferred.append((item, decision))
            if preferred_asset_id and asset_id(item) == preferred_asset_id:
                preferred_deferred_reason = decision.reason

    if deferred and policy.reuse_policy == REUSE_WHEN_NEEDED:
        item, decision = deferred[0]
        return item, UsageDecision(
            ACCEPT,
            decision.reason + "；没有更合适的替代素材，允许按 reuse_when_needed 复用",
            decision.source,
            decision.provider,
        )

    if deferred:
        return None, deferred[0][1]

    return None, UsageDecision(
        REJECT,
        "所有候选均不满足当前来源/复用规则",
        "",
        "",
    )


def enforce_assignments(
    scenes: Sequence[Mapping[str, Any]],
    assets: Mapping[str, Any],
    assignments: Mapping[str, str],
    asset_order: Sequence[str],
    *,
    policy_by_scene: Optional[Mapping[str, Mapping[str, str]]] = None,
) -> tuple[dict[str, str], list[dict]]:
    """对已有语义匹配结果执行来源/复用政策；不重新做语义匹配。"""
    policy_by_scene = policy_by_scene or {}
    result: dict[str, str] = {}
    decisions: list[dict] = []
    state = UsageState()

    for scene in scenes:
        sid = str(scene.get("id") or "")
        raw_policy = policy_by_scene.get(sid) or {}
        policy = SourcePolicy(
            source_preference=str(raw_policy.get("source_preference") or USER_FIRST),
            reuse_policy=str(raw_policy.get("reuse_policy") or REUSE_WHEN_NEEDED),
            diversity_policy=str(raw_policy.get("diversity_policy") or PREFER_UNIQUE_SOURCE),
        ).normalized()

        preferred_id = str(assignments.get(sid) or "")
        candidate_ids = []
        if preferred_id:
            candidate_ids.append(preferred_id)
        candidate_ids.extend(str(a) for a in asset_order if a)
        seen = set()
        candidates = []
        for aid in candidate_ids:
            if aid in seen or aid not in assets:
                continue
            seen.add(aid)
            candidates.append(assets[aid])

        chosen, decision = choose(
            candidates,
            state,
            policy,
            preferred_asset_id=preferred_id,
        )
        if chosen is not None:
            aid = asset_id(chosen)
            result[sid] = aid
            state.record(chosen)
        elif preferred_id and policy.reuse_policy == REUSE_WHEN_NEEDED and preferred_id in assets:
            chosen = assets[preferred_id]
            aid = asset_id(chosen)
            result[sid] = aid
            state.record(chosen)
            decision = UsageDecision(
                ACCEPT,
                "无满足来源规则的替代素材，允许保留原语义匹配结果",
                source_kind(chosen),
                source_provider(chosen),
            )

        decisions.append({
            "scene_id": sid,
            "asset_id": result.get(sid, ""),
            "status": decision.status,
            "reason": decision.reason,
            "source": decision.source,
            "provider": decision.provider,
            "policy": {
                "source_preference": policy.source_preference,
                "reuse_policy": policy.reuse_policy,
                "diversity_policy": policy.diversity_policy,
            },
        })

    return result, decisions


def usage_report(
    usage_rows: Sequence[Mapping[str, Any]],
    assets: Mapping[str, Any],
) -> dict:
    """从真实 source-window 使用记录派生来源台账。"""
    counts: dict[str, int] = {}
    providers: dict[str, int] = {}
    assets_used: dict[str, int] = {}
    by_scene: list[dict] = []

    for row in usage_rows:
        aid = str(row.get("asset_id") or "")
        asset = assets.get(aid)
        if not asset:
            continue
        source = source_kind(asset)
        provider = source_provider(asset)
        assets_used[aid] = assets_used.get(aid, 0) + 1
        counts[source] = counts.get(source, 0) + 1
        if provider:
            providers[provider] = providers.get(provider, 0) + 1
        by_scene.append({
            "scene_id": str(row.get("scene_id") or ""),
            "asset_id": aid,
            "source": source,
            "provider": provider,
            "source_in": row.get("source_in"),
            "source_out": row.get("source_out"),
            "usage_index": row.get("usage_index"),
        })

    return {
        "source_counts": counts,
        "provider_counts": providers,
        "asset_counts": assets_used,
        "by_scene": by_scene,
    }


__all__ = [
    "ACCEPT", "DEFER", "REJECT", "UNKNOWN",
    "USER_FIRST", "STOCK_FIRST", "GENERATED_FIRST",
    "REUSE_WHEN_NEEDED", "NEVER_REUSE",
    "PREFER_UNIQUE_SOURCE", "ALLOW_REUSE",
    "SourcePolicy", "UsageState", "UsageDecision",
    "asset_id", "source_kind", "source_provider",
    "preferred_source_order", "decide", "choose",
    "enforce_assignments", "usage_report",
]
