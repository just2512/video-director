"""Creative workflow contracts and timing/semantic/shot validation tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from video_director.creative_contracts import CharacterTiming, CreativePlan, SemanticBeat, SentenceTiming, ShotLayerPlan, ShotPlan
from video_director import creative_pipeline as cp


def test_multicharacter_word_is_explicitly_interpolated():
    result = cp.build_sentence_timing(
        "s1", "你好世界", 0, 1000,
        [{"text": "你好世界", "start": 0.1, "end": 0.9}],
        source="tts_word_boundary",
    )
    assert result.status == "PARTIAL"
    assert [c.text for c in result.characters] == list("你好世界")
    assert all(c.source == "word_interpolation" for c in result.characters)
    assert all(c.review_required for c in result.characters)
    assert result.characters[0].start_ms == 100
    assert result.characters[-1].end_ms == 900


def test_native_single_character_anchor_is_preserved():
    result = cp.build_sentence_timing(
        "s1", "甲乙", 0, 1000,
        [{"text": "甲", "start": 0.1, "end": 0.2},
         {"text": "乙", "start": 0.3, "end": 0.5}],
        source="tts_word_boundary",
    )
    assert result.status == "ALIGNED"
    assert [c.source for c in result.characters] == ["tts_native", "tts_native"]
    assert not any(c.review_required for c in result.characters)


def test_no_matching_word_anchor_is_missing_not_fabricated():
    result = cp.build_sentence_timing(
        "s1", "完全不同的稿子", 0, 1000,
        [{"text": "unrelated", "start": 0.1, "end": 0.8}], source="asr",
    )
    assert result.status == "MISSING"
    assert result.characters == []
    assert result.review_reasons


def test_interpolation_stays_inside_sentence_even_with_overlapping_anchors():
    result = cp.build_sentence_timing(
        "s1", "甲乙丙丁", 100, 102,
        [{"text": "甲", "start": 0.102, "end": 0.104},
         {"text": "丁", "start": 0.100, "end": 0.101}],
    )
    assert result.status == "PARTIAL"
    assert all(100 <= c.start_ms < c.end_ms <= 102 for c in result.characters)
    assert all(c.review_required for c in result.characters)


def test_punctuation_is_not_mistaken_for_a_speech_anchor():
    result = cp.build_sentence_timing(
        "s1", "你好，世界！", 0, 1000,
        [{"text": "你好", "start": 0.1, "end": 0.4},
         {"text": "世界", "start": 0.5, "end": 0.9}],
    )
    assert "".join(c.text for c in result.characters) == "你好世界"
    assert [c.index for c in result.characters] == [0, 1, 3, 4]


def test_sentence_contract_rejects_reversed_character_indices():
    with pytest.raises(ValidationError):
        SentenceTiming(
            sentence_id="s1", text="甲乙", start_ms=0, end_ms=1000,
            source="tts_word_boundary", status="ALIGNED",
            characters=[
                CharacterTiming(index=1, text="乙", start_ms=200, end_ms=300, source="tts_native"),
                CharacterTiming(index=0, text="甲", start_ms=100, end_ms=200, source="tts_native"),
            ],
        )


def test_initial_plan_is_honest_about_missing_alignment_and_layers(monkeypatch, tmp_path):
    monkeypatch.setattr(cp.config, "composer_storage_root", lambda: tmp_path / "composer")
    scene = SimpleNamespace(id="s1", text="一句解说", start=0.0, duration=2.0, transition_out=None)
    ir = SimpleNamespace(scenes=[scene], project=SimpleNamespace(fps=30))
    plan = cp.build_initial_plan("p1", ir, [], "missing", "", version=1)
    assert len(plan.semantic_beats) == 1
    assert plan.semantic_beats[0].timing_status == "MISSING"
    assert plan.shots[0].layers == []
    assert "未确认镜头意图" in plan.shots[0].unresolved_items
    report = cp.validate_creative_plan(plan, ir, "p1")
    assert report["verdict"] == "MISSING"
    codes = {item["code"] for item in report["issues"]}
    assert "BEAT_TIMING_MISSING" in codes
    assert "SHOT_LAYER_MISSING" in codes


def test_preflight_rejects_unregistered_shot_recipe(monkeypatch, tmp_path):
    monkeypatch.setattr(cp.config, "composer_storage_root", lambda: tmp_path / "composer")
    monkeypatch.setattr(cp, "_shot_registry", lambda: {"shot.valid": SimpleNamespace(id="shot.valid", slots=[], layer_plan=[])})
    monkeypatch.setattr(cp, "_motion_registry", lambda: {})
    scene = SimpleNamespace(id="s1", text="一句解说", start=0.0, duration=2.0, transition_out=None)
    ir = SimpleNamespace(scenes=[scene], project=SimpleNamespace(fps=30))
    plan = cp.build_initial_plan("p1", ir, [
        {"text": "句", "start": 0.1, "end": 0.2},
        {"text": "解", "start": 0.3, "end": 0.4},
    ], "tts_word_boundary", "", version=1)
    plan.shots[0].intent = "解释核心观点"
    plan.shots[0].layers = [ShotLayerPlan(role="主视觉", recipe_id="shot.not-registered")]
    plan.semantic_beats[0] = plan.semantic_beats[0].model_copy(update={
        "intent": ["解释"], "visual_need": ["强调"]
    })
    report = cp.validate_creative_plan(plan, ir, "p1")
    assert report["verdict"] == "REJECT"
    assert any(i["code"] == "SHOT_RECIPE_UNAVAILABLE" for i in report["issues"])


def test_plan_save_is_atomic_and_rejects_stale_version(monkeypatch, tmp_path):
    monkeypatch.setattr(cp.config, "composer_storage_root", lambda: tmp_path / "composer")
    scene = SimpleNamespace(id="s1", text="一句解说", start=0.0, duration=2.0, transition_out=None)
    ir = SimpleNamespace(scenes=[scene], project=SimpleNamespace(fps=30))
    plan = cp.build_initial_plan("p1", ir, [], "missing", "", version=1)
    saved = cp.save_plan(plan, expected_version=0)
    assert saved.version == 1
    assert cp.load_plan("p1").version == 1
    with pytest.raises(cp.CreativePlanError, match="版本冲突"):
        cp.save_plan(plan, expected_version=0)


def test_contracts_reject_unknown_fields_and_duplicate_ids():
    with pytest.raises(ValidationError):
        SemanticBeat(
            id="b1", sentence_id="s1", text="一句", start_ms=0, end_ms=1000,
            undocumented_field="must not silently disappear",
        )
    with pytest.raises(ValidationError):
        CreativePlan(
            project_id="p1", script_hash="x", character_timings=[], semantic_beats=[],
            shots=[
                ShotPlan(shot_id="s1", start_ms=0, end_ms=1000),
                ShotPlan(shot_id="s1", start_ms=1000, end_ms=2000),
            ],
        )
