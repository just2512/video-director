"""Source Diversity / Source Usage Policy regression tests."""

from video_director.models import AssetRef
from video_director.source_usage_policy import (
    ACCEPT, DEFER, NEVER_REUSE, PREFER_UNIQUE_SOURCE, REUSE_WHEN_NEEDED,
    SourcePolicy, UsageState, choose, decide, enforce_assignments,
)


def _asset(aid, source, provider=""):
    provenance = {"provider": provider} if provider else None
    return AssetRef(id=aid, type="video", file=f"/tmp/{aid}.mp4",
                    source=source, provenance=provenance, duration=4.0)


def test_same_asset_is_deferred_when_alternative_exists():
    a = _asset("a", "uploaded")
    b = _asset("b", "uploaded")
    state = UsageState()
    state.record(a)
    d = decide(a, state, SourcePolicy(), candidates=[a, b])
    assert d.status == "DEFER"
    chosen, cd = choose([a, b], state, SourcePolicy(), preferred_asset_id="a")
    assert chosen.id == "b"
    assert cd.status == ACCEPT


def test_unique_stock_provider_is_preferred_when_available():
    a = _asset("a", "stock", "pexels")
    b = _asset("b", "stock", "pixabay")
    state = UsageState()
    state.record(a)
    chosen, decision = choose([a, b], state, SourcePolicy(), preferred_asset_id="a")
    assert chosen.id == "b"
    assert "Provider" in decision.reason


def test_reuse_when_needed_relaxes_only_when_no_alternative_exists():
    a = _asset("a", "uploaded")
    state = UsageState()
    state.record(a)
    chosen, decision = choose(
        [a], state,
        SourcePolicy(reuse_policy=REUSE_WHEN_NEEDED),
        preferred_asset_id="a",
    )
    assert chosen.id == "a"
    assert decision.status == ACCEPT
    assert "允许" in decision.reason


def test_never_reuse_rejects_used_asset():
    a = _asset("a", "uploaded")
    state = UsageState()
    state.record(a)
    d = decide(a, state, SourcePolicy(reuse_policy=NEVER_REUSE), candidates=[a])
    assert d.status == "REJECT"


def test_assignment_policy_preserves_semantic_choice_when_no_conflict():
    assets = {"a": _asset("a", "uploaded"), "b": _asset("b", "stock", "pexels")}
    scenes = [{"id": "S01", "text": "一"}, {"id": "S02", "text": "二"}]
    assignments = {"S01": "a", "S02": "b"}
    result, decisions = enforce_assignments(
        scenes, assets, assignments, ["a", "b"],
        policy_by_scene={
            "S01": {"source_preference": "user_first", "reuse_policy": "reuse_when_needed",
                     "diversity_policy": PREFER_UNIQUE_SOURCE},
            "S02": {"source_preference": "stock_first", "reuse_policy": "reuse_when_needed",
                     "diversity_policy": PREFER_UNIQUE_SOURCE},
        },
    )
    assert result == assignments
    assert all(x["status"] == ACCEPT for x in decisions)


def test_matcher_enforces_policy_after_semantic_assignment(monkeypatch):
    from video_director import matcher
    a = _asset("a", "uploaded")
    b = _asset("b", "uploaded")
    assets = {"a": a, "b": b}
    scenes = [{"id": "S01", "text": "甲"}, {"id": "S02", "text": "乙"}]

    monkeypatch.setattr(matcher, "deterministic_match", lambda *args, **kwargs: "a")
    result = matcher.match_assets(scenes, assets, llm_call=None)
    assert result["S01"] == "a"
    assert result["S02"] == "b"
