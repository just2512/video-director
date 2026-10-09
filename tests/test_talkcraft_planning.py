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
