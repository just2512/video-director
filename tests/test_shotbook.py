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


def test_character_timing_survives_overlapping_word_timestamps():
    """B1：重叠词时间戳（ASR 常见形态）不得产出倒流锚点。

    TalkCraft timestamps_cpu.py 的教训：重叠/乱序 token 会让插值锚点时间倒流；
    保存端强制起点单调——生成端必须同契约，否则 generate 出的草案
    自己过不了 save 校验，用户被自己没碰过的字段卡死。
    """
    anchors = expand_character_timing("重叠词", 0.0, 2.0, [
        {"text": "重叠", "start": 0.0, "end": 2.0},
        {"text": "词", "start": 0.5, "end": 1.0},
    ])
    starts = [a["start"] for a in anchors]
    assert starts == sorted(starts), f"重叠词时间戳导致起点倒流: {anchors}"
    assert all(a["start"] <= a["end"] for a in anchors)
    # 倒流位置的字退化为场景估算（诚实标注），不伪造词级精度
    assert anchors[-1]["method"] == "scene_estimate"


def test_character_timing_survives_out_of_order_tokens():
    """B1b：token 时序乱序（ASR 输出与稿序不一致）同样不得倒流。"""
    anchors = expand_character_timing("数据出来了", 0.0, 2.0, [
        {"text": "数据", "start": 1.8, "end": 2.6},
        {"text": "出来了", "start": 0.5, "end": 1.2},
    ])
    starts = [a["start"] for a in anchors]
    assert starts == sorted(starts), f"乱序 token 导致起点倒流: {anchors}"
    assert all(a["start"] <= a["end"] for a in anchors)


def test_recipe_filter_domain_matches_visual_intent_not_role():
    """B2：配方过滤值域契约。

    Registry 的 semantic_intents 值域（opening/data_highlight/…）与
    semantic_role（hook/…/neutral）零相交，与 visual_intent
    （emphasize/transition/…）相交——前端过滤必须用 visual_intent，
    用 semantic_role 是永远空转的死分支。
    """
    from video_director.shotbook import _recipes

    recipes = _recipes()
    assert recipes, "Shot Registry 应加载内置配方"
    all_intents = set()
    for recipe in recipes:
        all_intents.update(recipe["semantic_intents"])
    # 值域事实：与 role 零相交（如果将来相交，此测试提醒重审过滤逻辑）
    assert not (all_intents & set(SEMANTIC_ROLES)), (
        "semantic_intents 与 SEMANTIC_ROLES 出现交集——过滤语义需要重新裁决"
    )
    # 与 visual_intent 至少相交（过滤有真实命中面）
    assert all_intents & set(VISUAL_INTENTS), (
        "semantic_intents 与 VISUAL_INTENTS 零相交——过滤失去意义"
    )
