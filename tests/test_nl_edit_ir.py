"""差距 6：自然语言改 IR（数值类编辑：放大/缩小/加长/缩短）。

报告二点名的例子「把产品放大一点」此前不可解析（只支持删/换/改文案）。
链路：parse_edit_instruction（NL→edits）→ apply_storyboard_edits（分镜书记录
+ 时长修正）→ compose_project._apply_scale_hints（IR 图层 transform.scale）。
"""
from video_director import confirm
from video_director import pipeline as pipeline_mod


def _sb():
    return {
        "scene_count": 3,
        "total_duration": 12.0,
        "scenes": [
            {"scene_id": "S01", "text": "开场", "start": 0.0, "duration": 4.0,
             "assets_used": [{"asset_id": "a1", "type": "video", "duration": 4.0}]},
            {"scene_id": "S02", "text": "产品", "start": 4.0, "duration": 4.0,
             "assets_used": [{"asset_id": "a2", "type": "video", "duration": 4.0}]},
            {"scene_id": "S03", "text": "结尾", "start": 8.0, "duration": 4.0,
             "assets_used": [{"asset_id": "a3", "type": "video", "duration": 4.0}]},
        ],
    }


def test_parse_zoom_in_instruction():
    edits = confirm.parse_edit_instruction("把第2段的产品放大一点", _sb())
    assert edits == {"S02": {"scale": 1.25}}, edits


def test_parse_zoom_out_with_factor():
    edits = confirm.parse_edit_instruction("第2段缩小2倍", _sb())
    assert "S02" in edits and edits["S02"]["scale"] < 1.0, edits


def test_parse_duration_delta():
    assert confirm.parse_edit_instruction("第3段加长2秒", _sb()) == {
        "S03": {"duration_delta": 2.0}}
    assert confirm.parse_edit_instruction("第3段缩短1.5秒", _sb()) == {
        "S03": {"duration_delta": -1.5}}


def test_apply_edits_records_scale_and_duration():
    sb = confirm.apply_storyboard_edits(_sb(), {
        "S02": {"scale": 1.5}, "S03": {"duration_delta": 2.0}})
    s2 = next(sc for sc in sb["scenes"] if sc["scene_id"] == "S02")
    s3 = next(sc for sc in sb["scenes"] if sc["scene_id"] == "S03")
    assert s2["scale_hint"] == 1.5
    assert s3["duration"] == 6.0, s3
    # 时间轴重算（S03 加长 2s → 总时长 14）
    assert sb["total_duration"] == 14.0
    assert sb["scenes"][-1]["start"] == 8.0


def test_scale_hint_lands_in_ir_layers():
    """_apply_scale_hints 把 narration_scenes 的 scale_hint 写进 IR 主图层 transform。"""
    from video_director.models import CompositionIR
    from video_director.visual_semantics import Transform

    ir = CompositionIR.model_validate({
        "project": {"duration": 8, "fps": 30, "width": 1080, "height": 1920},
        "scenes": [
            {"id": "S01", "start": 0, "duration": 4, "text": "开场",
             "layers": [{"type": "video", "asset_id": "a1", "start": 0, "duration": 4,
                         "source_in": 0.0, "source_out": 4.0}]},
            {"id": "S02", "start": 4, "duration": 4, "text": "产品",
             "layers": [{"type": "video", "asset_id": "a2", "start": 0, "duration": 4,
                         "source_in": 0.0, "source_out": 4.0,
                         "transform": {"x": 0.5, "y": 0.5, "scale": 1.0,
                                       "rotation": 0, "opacity": 1.0,
                                       "anchor": "center"}}]},
        ],
        "audio": [],
    })
    narration_scenes = [
        {"id": "S01", "text": "开场", "start": 0.0, "duration": 4.0},
        {"id": "S02", "text": "产品", "start": 4.0, "duration": 4.0, "scale_hint": 1.5},
    ]
    applied = pipeline_mod._apply_scale_hints(ir, narration_scenes)
    assert applied == 1
    assert ir.scenes[1].layers[0].transform.scale == 1.5
    # 已有 transform 的其他字段保留
    assert ir.scenes[1].layers[0].transform.x == 0.5
    # 无 hint 的场景不动
    assert ir.scenes[0].layers[0].transform is None


def test_llm_parser_keeps_numeric_and_text_edits_in_the_same_scene():
    """LLM parsing must not drop scale/duration when it also returns text edits."""
    import json

    from video_director.confirm import parse_edit_instruction_llm, apply_storyboard_edits

    prompts = []

    def llm_call(prompt):
        prompts.append(prompt)
        return json.dumps({
            "S02": {
                "text": "产品近景",
                "scale": 1.6,
                "duration_delta": 2.0,
            }
        }, ensure_ascii=False)

    edits = parse_edit_instruction_llm(
        "第二段产品放大，并把旁白改为产品近景，同时延长两秒",
        _sb(),
        llm_call,
    )
    assert len(prompts) == 1
    assert '"scale"' in prompts[0] and '"duration_delta"' in prompts[0]
    assert '{"remove"' in prompts[0] and '{{' not in prompts[0], prompts[0]
    assert '空对象 {}' in prompts[0]
    assert edits == {
        "S02": {
            "text": "产品近景",
            "scale": 1.6,
            "duration_delta": 2.0,
        }
    }, edits

    preview = apply_storyboard_edits(_sb(), edits)
    scene = next(item for item in preview["scenes"] if item["scene_id"] == "S02")
    assert scene["text"] == "产品近景"
    assert scene["scale_hint"] == 1.6
    assert scene["duration"] == 6.0
    assert preview["total_duration"] == 14.0


def test_llm_parser_rejects_non_finite_or_out_of_contract_numeric_edits():
    """Model output is untrusted: invalid numeric values are ignored, valid text remains."""
    import json

    from video_director.confirm import parse_edit_instruction_llm

    edits = parse_edit_instruction_llm(
        "第二段修改",
        _sb(),
        lambda _prompt: json.dumps({
            "S02": {
                "text": "保留有效文案",
                "asset": "invented-asset-id",
                "scale": 10 ** 1000,
                "duration_delta": float("nan"),
            },
            "S99": {"scale": 1.5, "duration_delta": 1.0},
        }, ensure_ascii=False),
    )
    assert edits == {"S02": {"text": "保留有效文案"}}, edits
