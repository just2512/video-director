"""MPT fingerprint-level semantic fusion tests."""

from __future__ import annotations

from types import SimpleNamespace

from video_director import mpt_semantics


def test_pause_markup_matches_mpt_boundaries():
    segments, events = mpt_semantics.parse_pause_markup(
        "开头[pause: 1s][停顿: 2s]中间[pause: 50ms]结尾"
    )
    assert [s.text for s in segments] == ["开头", "中间", "结尾"]
    assert segments[0].pause_after == 3.0
    assert segments[1].pause_after == 0.1
    assert [e.duration for e in events] == [1.0, 2.0, 0.1]


def test_invalid_pause_is_removed_but_following_text_survives():
    segments, _ = mpt_semantics.parse_pause_markup(
        "前文[pause: nope]后文[pause: 12s]"
    )
    assert [s.text for s in segments] == ["前文", "后文"]
    assert segments[0].pause_after == 0.0
    assert segments[1].pause_after == 10.0
    assert mpt_semantics.remove_pause_markup(
        "前文[pause: nope]后文"
    ) == "前文 后文"


def test_search_terms_preserve_order_and_remove_duplicates():
    terms = mpt_semantics.normalize_search_terms(
        ["office meeting", "team meeting", "office meeting", "  AI  assistant  "],
        amount=8,
    )
    assert terms == ["office meeting", "team meeting", "AI assistant"]


def test_material_usage_is_source_window_level():
    video_layer = SimpleNamespace(
        type="video", asset_id="v1", source_in=1.25, source_out=4.75
    )
    image_layer = SimpleNamespace(type="image", asset_id="i1")
    scene = SimpleNamespace(id="S01", layers=[video_layer, image_layer])
    ir = SimpleNamespace(scenes=[scene])
    usage = mpt_semantics.material_usage_from_ir(ir)
    assert [u.__dict__ for u in usage] == [{
        "asset_id": "v1",
        "source_in": 1.25,
        "source_out": 4.75,
        "scene_id": "S01",
        "usage_index": 1,
        "search_term": "",
        "selection_source": "planner",
    }]


def test_artifact_fingerprint_is_order_independent_for_mapping_keys():
    a = mpt_semantics.build_artifact_fingerprint({
        "clip_speed": 1.2,
        "nested": {"b": 2, "a": 1},
    })
    b = mpt_semantics.build_artifact_fingerprint({
        "nested": {"a": 1, "b": 2},
        "clip_speed": 1.2,
    })
    assert a == b


def test_material_intent_snapshot_keeps_scene_to_query_contract():
    snapshot = mpt_semantics.material_intent_snapshot(
        [{"id": "S01", "text": "团队在办公室开会。", "duration": 5.0}],
        search_terms_by_scene={"S01": ["office meeting", "team discussion"]},
        max_clip_duration=5,
        selected_assets={"S01": "v1"},
        selection_reasons={"S01": "场景语义匹配"},
    )
    scene = snapshot["scenes"][0]
    assert scene["search_order"] == ["office meeting", "team discussion"]
    assert scene["target_duration"] == 5.0
    assert scene["max_clip_duration"] == 5
    assert scene["selected_asset_id"] == "v1"
