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
