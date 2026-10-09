from video_director.shotbook import (
    SEMANTIC_ROLES, SHOT_TYPES, VISUAL_INTENTS, expand_character_timing,
)


def test_character_timing_uses_token_interpolation_when_matching_word_tokens():
    anchors = expand_character_timing("你好", 0.0, 1.0, [{"text": "你好", "start": 0.2, "end": 0.8}])
    assert [a["char"] for a in anchors] == ["你", "好"]
    assert anchors[0]["start"] == 0.2
    assert anchors[-1]["end"] == 0.8
    assert all(a["method"] == "token_interpolation" for a in anchors)


def test_character_timing_falls_back_to_scene_estimate_for_unmatched_text():
    anchors = expand_character_timing("甲乙丙", 2.0, 5.0, [{"text": "xyz", "start": 2.1, "end": 4.9}])
    assert len(anchors) == 3
    assert anchors[0]["start"] == 2.0
    assert anchors[-1]["end"] == 5.0
    assert any(a["method"] == "scene_estimate" for a in anchors)


def test_character_timing_ignores_whitespace_and_nonpositive_tokens():
    anchors = expand_character_timing(" A B ", 0.0, 1.0, [
        {"text": "A", "start": 0.2, "end": 0.5},
        {"text": "ignored", "start": 0.7, "end": 0.6},
    ])
    assert [a["char"] for a in anchors] == ["A", "B"]
    assert all(a["start"] <= a["end"] for a in anchors)


def test_frontend_choices_remain_registry_domains():
    assert "hook" in SEMANTIC_ROLES
    assert "prove" in VISUAL_INTENTS
    assert "document" in SHOT_TYPES


def test_character_timing_keeps_monotonic_order_with_repeat_characters():
    anchors = expand_character_timing(
        "哈哈哈哈", 0.0, 2.0,
        [{"text": "哈哈", "start": 0.1, "end": 0.8},
         {"text": "哈哈", "start": 1.0, "end": 1.7}],
    )
    assert len(anchors) == 4
    assert all(a["start"] <= a["end"] for a in anchors)
    assert [a["start"] for a in anchors] == sorted(a["start"] for a in anchors)


def test_motion_registry_exposes_only_renderer_supported_layer_contracts():
    from video_director.shotbook import _motion_recipes

    motions = _motion_recipes()
    assert motions, "Motion Registry should load the project's built-in motion recipes"
    for motion in motions:
        assert set(motion["supported_for"]) <= set(motion["compatible_layers"])
        if motion["preview_only"]:
            assert not motion["supported_for"]
        if "video" in motion["supported_for"]:
            assert set(motion["properties"]) <= {"opacity"}
        if "image" in motion["supported_for"]:
            assert set(motion["properties"]) <= {"scale", "opacity"}


def test_pipeline_timing_sidecar_persists_provenance(tmp_path):
    import json
    from video_director.pipeline import _persist_narration_timing

    plans = tmp_path / "plans"
    words = [{"text": "你好", "start": 0.2, "end": 0.8}]
    _persist_narration_timing({"plans": plans}, "asr", words, 1.25)
    payload = json.loads((plans / "narration_timing.json").read_text(encoding="utf-8"))
    assert payload["source"] == "asr"
    assert payload["duration"] == 1.25
    assert payload["word_timeline"] == words
