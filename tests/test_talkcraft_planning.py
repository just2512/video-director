"""Regression tests for the speech-to-shot planning contract."""
import json

from video_director import editor, talkcraft
from video_director.models import CompositionIR, Layer, LayerType, ProjectSpec, SceneIR


def test_character_timing_from_word_windows_is_explicitly_interpolated():
    words = [
        {"text": "你", "start": 0.0, "end": 0.1, "timing_source": "interpolated_from_word"},
        {"text": "好", "start": 0.1, "end": 0.2, "timing_source": "interpolated_from_word"},
        {"text": "世", "start": 0.3, "end": 0.4, "timing_source": "interpolated_from_word"},
        {"text": "界", "start": 0.4, "end": 0.5, "timing_source": "interpolated_from_word"},
    ]
    chars, coverage = talkcraft._align_scene_chars("你好世界", 0, 1, words)
    assert coverage == 1.0
    assert [c["text"] for c in chars] == list("你好世界")
    assert all(c["timing_source"] == "interpolated_from_word" for c in chars)
    assert chars[0]["start"] == 0.0
    assert chars[-1]["end"] == 0.5


def test_missing_word_anchors_are_not_claimed_as_alignment():
    chars, coverage = talkcraft._align_scene_chars("没有可用锚点", 1.0, 2.0, [])
    assert coverage == 0.0
    assert chars
    assert all(c["timing_source"] == "estimated_from_scene" for c in chars)
    assert all(c["review_required"] for c in chars)


def test_semantic_heuristics_are_hints_not_implicit_approval():
    sem, needs, reasons = talkcraft._semantic_suggestions("我叫小明，收入每月 3000 元")
    assert "自我介绍" in sem
    assert "数据" in sem
    assert "身份" in needs
    assert "量化" in needs
    assert isinstance(reasons, list)


def test_artifact_writer_atomically_writes_json(tmp_path):
    path = tmp_path / "plans" / "nested.json"
    value = {"text": "中文", "revision": 2}
    talkcraft._atomic_json(path, value)
    assert json.loads(path.read_text(encoding="utf-8")) == value
    assert list(path.parent.glob("*.tmp")) == []


def test_recipe_manifest_is_json_serializable():
    recipes = talkcraft._list_recipes()
    assert recipes
    json.dumps(recipes, ensure_ascii=False)
    assert all("id" in item and "compatible_layers" in item for item in recipes)


def test_set_scene_intent_uses_scene_contract_and_revision_command_name():
    layer = Layer(type=LayerType.image, duration=2.0)
    scene = SceneIR(
        id="s1", start=0.0, duration=2.0, text="这是一个测试场景",
        layers=[layer],
    )
    ir = CompositionIR(project=ProjectSpec(duration=2.0), scenes=[scene])
    changed, stale = editor._cmd_set_scene_intent(
        ir, "s1",
        {"intent": "解释问题", "visualIntent": "展示一张证据图", "semanticRole": "evidence"},
    )
    assert ir.scenes[0].intent == "解释问题"
    assert ir.scenes[0].visual_intent == "展示一张证据图"
    assert ir.scenes[0].semantic_role == "evidence"
    assert changed == ["s1:intent"]
    assert "s1:render" in stale
    assert "set_scene_intent" in editor.SUPPORTED_COMMANDS


def test_set_scene_intent_rejects_unknown_roles():
    layer = Layer(type=LayerType.image, duration=1.0)
    scene = SceneIR(id="s1", start=0.0, duration=1.0, text="测试", layers=[layer])
    ir = CompositionIR(project=ProjectSpec(duration=1.0), scenes=[scene])
    try:
        editor._cmd_set_scene_intent(ir, "s1", {"semanticRole": "invented-role"})
    except editor.EditorError as exc:
        assert exc.code == "INVALID_PAYLOAD"
    else:
        raise AssertionError("invalid semantic role was accepted")



def test_sentence_spans_keep_source_offsets_and_do_not_split_commas():
    spans = talkcraft._sentence_spans("收入下降了，但是支出没变。先不要急着辞职！")
    assert [item["text"] for item in spans] == ["收入下降了，但是支出没变。", "先不要急着辞职！"]
    assert spans[0]["start_index"] == 0
    assert spans[0]["end_index"] == len("收入下降了，但是支出没变。")
    assert "但是" in spans[0]["text"]


def test_character_alignment_keeps_original_script_index_through_punctuation():
    words = [
        {"text": "先", "start": 0.0, "end": 0.1, "timing_source": "interpolated_from_word"},
        {"text": "走", "start": 0.1, "end": 0.2, "timing_source": "interpolated_from_word"},
        {"text": "第", "start": 0.3, "end": 0.4, "timing_source": "interpolated_from_word"},
        {"text": "一", "start": 0.4, "end": 0.5, "timing_source": "interpolated_from_word"},
        {"text": "步", "start": 0.5, "end": 0.6, "timing_source": "interpolated_from_word"},
    ]
    chars, coverage = talkcraft._align_scene_chars("先走，第一步。", 0, 1, words)
    by_index = {char["script_index"]: char for char in chars}
    assert coverage == 1.0
    assert by_index[0]["text"] == "先"
    assert by_index[1]["text"] == "走"
    assert 2 not in by_index
    assert by_index[3]["text"] == "第"
    assert by_index[3]["start"] == 0.3


def test_shotbook_is_a_readable_generated_view(tmp_path):
    root = tmp_path
    (root / "plans").mkdir()
    (root / "plans" / "semantic_beats.json").write_text(json.dumps({
        "project_id": "demo", "input_signature": "abc",
        "beats": [{
            "id": "beat-s1-01", "scene_id": "s1", "text": "收入下降了。",
            "sem": ["数据"], "need": ["量化"], "entities": ["收入"],
            "weight": "main", "start": 0.1, "end": 0.9,
            "timing_status": "ALIGNED_FROM_WORDS", "review_status": "REVIEWED"
        }]
    }, ensure_ascii=False), encoding="utf-8")
    (root / "plans" / "shot_plan.json").write_text(json.dumps({
        "project_id": "demo", "input_signature": "abc", "shots": [{
            "scene_id": "s1", "start": 0, "end": 1, "intent": "解释收入变化",
            "visual_intent": "展示收入变化", "review_status": "REVIEWED",
            "recipe_id": "motion.fade", "recipe_status": "PLANNED_NOT_APPLIED",
            "transition_out": "cut", "layers": [{"index": 0, "type": "image", "asset_id": "asset-1",
            "start": 0, "duration": 1, "motion_recipe_id": None}]
        }]
    }, ensure_ascii=False), encoding="utf-8")
    talkcraft._sync_shotbook(root)
    body = (root / "plans" / "SHOTBOOK.md").read_text(encoding="utf-8")
    assert "s1" in body
    assert "收入下降了。" in body
    assert "motion.fade" in body
    assert "语义：数据" in body
