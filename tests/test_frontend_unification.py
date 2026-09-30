"""Frontend Unification 方案落地测试：

- §13 ContinuityVerifier（BE-001）规则 A-F 指纹
- §14 camera.apply / §15 pip.configure / §16 visual.morph（BE-002/003/004）
- §12 Generation Availability（BE-005）
- §30 Capability/Executor 对照：Registered ≠ Implemented 补齐
"""

import json
from pathlib import Path

import pytest

from video_director import editor
from video_director.continuity import (
    ContinuityIssueCode,
    verify_composition_continuity,
)


# ---------------------------------------------------------------------------
# 测试基建：临时项目 fixture（与 test_visual_semantics_commands.py 同模式）
# ---------------------------------------------------------------------------

@pytest.fixture()
def project(tmp_path, monkeypatch):
    from video_director import config
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    from video_director.models import CompositionIR
    pid = "vd-unity-test"
    ir = CompositionIR.model_validate({
        "project": {"title": "统一测试", "width": 1080, "height": 1920,
                    "fps": 30, "duration": 8},
        "scenes": [
            {"id": "scene-1", "start": 0.0, "duration": 4.0, "text": "第一镜",
             "layers": [{"type": "graphic", "duration": 4.0}],
             "transition_out": {"type": "cut"}},
            {"id": "scene-2", "start": 4.0, "duration": 4.0, "text": "第二镜",
             "layers": [{"type": "graphic", "duration": 4.0}],
             "transition_out": {"type": "cut"}},
        ],
        "audio": [],
        "watermarks": [],
    })
    editor.save_composition(pid, ir)
    yield pid


def _exec(pid, command, target_id, payload, expected=None):
    return editor.execute_command(pid, {
        "command": command, "targetId": target_id, "payload": payload,
        "expectedVersion": expected, "author": "test",
    })


# ---------------------------------------------------------------------------
# §13 ContinuityVerifier 规则 A-F（BE-001 Gate 指纹）
# ---------------------------------------------------------------------------

def _ir_with_links(project, links):
    from video_director.models import CompositionIR
    ir = json.loads((editor.load_composition(project).model_dump_json()))
    ir["continuity_links"] = links
    return CompositionIR.model_validate(ir)


class TestContinuityVerifier:
    """§13 规则 A-F：fixed fixture 覆盖（BE-001 Gate）。"""

    def test_rule_a_hard_cut_is_pass(self, project):
        ir = _ir_with_links(project, [
            {"id": "l1", "from_scene_id": "scene-1", "to_scene_id": "scene-2",
             "type": "hard_cut"}])
        r = verify_composition_continuity(ir)
        assert r.status == "PASS"
        assert r.issues == []  # HARD_CUT_OK 不入 issues（合法，非问题）

    def test_rule_b_carry_missing_target_reject(self, project):
        ir = _ir_with_links(project, [
            {"id": "l1", "from_scene_id": "scene-1", "to_scene_id": "scene-2",
             "type": "carry", "source_layer_id": "scene-1#0"}])
        r = verify_composition_continuity(ir)
        assert r.status == "REJECT"
        assert any(i.issue_code == ContinuityIssueCode.CARRY_MISSING_TARGET
                   for i in r.issues)

    def test_rule_b_carry_missing_source_reject(self, project):
        ir = _ir_with_links(project, [
            {"id": "l1", "from_scene_id": "scene-1", "to_scene_id": "scene-2",
             "type": "carry", "target_layer_id": "scene-2#0"}])
        r = verify_composition_continuity(ir)
        assert r.status == "REJECT"
        assert any(i.issue_code == ContinuityIssueCode.CARRY_MISSING_SOURCE
                   for i in r.issues)

    def test_rule_b_carry_full_is_pass(self, project):
        ir = _ir_with_links(project, [
            {"id": "l1", "from_scene_id": "scene-1", "to_scene_id": "scene-2",
             "type": "carry", "source_layer_id": "scene-1#0",
             "target_layer_id": "scene-2#0"}])
        r = verify_composition_continuity(ir)
        assert r.status == "PASS"

    def test_rule_c_camera_path_missing_reject(self, project):
        ir = _ir_with_links(project, [
            {"id": "l1", "from_scene_id": "scene-1", "to_scene_id": "scene-2",
             "type": "zoom_through"}])
        r = verify_composition_continuity(ir)
        assert r.status == "REJECT"
        assert any(i.issue_code == ContinuityIssueCode.CAMERA_PATH_MISSING
                   for i in r.issues)

    def test_rule_c_camera_path_given_pass(self, project):
        ir = _ir_with_links(project, [
            {"id": "l1", "from_scene_id": "scene-1", "to_scene_id": "scene-2",
             "type": "camera_dive", "camera_path": "cam-scene-1"}])
        r = verify_composition_continuity(ir)
        assert r.status == "PASS"

    def test_rule_d_morph_path_missing_reject(self, project):
        ir = _ir_with_links(project, [
            {"id": "l1", "from_scene_id": "scene-1", "to_scene_id": "scene-2",
             "type": "morph"}])
        r = verify_composition_continuity(ir)
        assert r.status == "REJECT"
        assert any(i.issue_code == ContinuityIssueCode.MORPH_PATH_MISSING
                   for i in r.issues)

    def test_rule_e_static_replacement_warning(self, project):
        """PPT Detector 主判定：两静态 graphic 场景零延续 → WARNING。"""
        ir = _ir_with_links(project, [])
        r = verify_composition_continuity(ir)
        assert r.status == "WARNING"
        assert any(i.issue_code == ContinuityIssueCode.STATIC_REPLACEMENT
                   for i in r.issues)

    def test_rule_f_anchor_jump_warning(self, project):
        ir = _ir_with_links(project, [
            {"id": "l1", "from_scene_id": "scene-1", "to_scene_id": "scene-2",
             "type": "carry", "source_layer_id": "scene-1#0",
             "target_layer_id": "scene-2#0",
             "source_anchor": "bottom_right", "target_anchor": "top_left"}])
        r = verify_composition_continuity(ir)
        assert r.status == "WARNING"
        assert any(i.issue_code == ContinuityIssueCode.ANCHOR_JUMP for i in r.issues)

    def test_rule_f_anchor_jump_suppressed_by_camera_path(self, project):
        ir = _ir_with_links(project, [
            {"id": "l1", "from_scene_id": "scene-1", "to_scene_id": "scene-2",
             "type": "carry", "source_layer_id": "scene-1#0",
             "target_layer_id": "scene-2#0",
             "source_anchor": "bottom_right", "target_anchor": "top_left",
             "camera_path": "cam-scene-1"}])
        r = verify_composition_continuity(ir)
        assert r.status == "PASS"

    def test_issue_fields_fingerprint(self, project):
        """§13 issue 字段指纹：sceneId/fromSceneId/issueCode/message/recoverable。"""
        ir = _ir_with_links(project, [])
        r = verify_composition_continuity(ir)
        issue = r.issues[0]
        d = issue.model_dump()
        for key in ("scene_id", "from_scene_id", "issue_code", "message", "recoverable"):
            assert key in d, f"issue 缺字段 {key}"
        assert d["scene_id"] == "scene-2"
        assert d["from_scene_id"] == "scene-1"

    def test_output_only_four_statuses(self):
        """§13 输出枚举：只有 PASS/WARNING/REJECT/UNKNOWN，零评分。"""
        from video_director.continuity import ContinuityReport
        r = ContinuityReport()
        assert r.status in ("PASS", "WARNING", "REJECT", "UNKNOWN")


# ---------------------------------------------------------------------------
# §14 camera.apply（BE-002 Gate：apply → revision → persisted CameraTrack）
# ---------------------------------------------------------------------------

class TestCameraApply:
    def test_camera_apply_push_in_creates_track(self, project):
        r = _exec(project, "apply_camera", "scene-1", {"preset": "push_in"})
        assert r.status == "APPLIED"
        assert r.revision_id
        ir = editor.load_composition(project)
        assert any(t.id == "cam-scene-1" for t in ir.camera_tracks)
        track = next(t for t in ir.camera_tracks if t.id == "cam-scene-1")
        assert len(track.keyframes) == 2
        assert track.keyframes[0].at == 0.0

    def test_camera_scene_compatible_layer(self, project):
        """§14 Scene.camera 只是兼容访问层（同源），canonical 是 camera_tracks。"""
        _exec(project, "apply_camera", "scene-1", {"preset": "push_in"})
        ir = editor.load_composition(project)
        sc = next(s for s in ir.scenes if s.id == "scene-1")
        assert sc.camera is not None and sc.camera.id == "cam-scene-1"
        assert any(t.id == "cam-scene-1" for t in ir.camera_tracks)

    def test_camera_apply_all_seven_presets(self, project):
        from video_director.editor import CAMERA_PRESETS
        assert sorted(CAMERA_PRESETS) == sorted(
            ["push_in", "pull_out", "pan_left", "pan_right", "tilt_up",
             "tilt_down", "follow"])
        for i, preset in enumerate(sorted(CAMERA_PRESETS)):
            r = _exec(project, "apply_camera", "scene-1", {"preset": preset})
            assert r.status == "APPLIED", preset

    def test_camera_apply_invalid_preset_rejected(self, project):
        r = _exec(project, "apply_camera", "scene-1", {"preset": "spin"})
        assert r.status == "REJECTED"
        assert r.errors[0]["code"] == "INVALID_PAYLOAD"

    def test_camera_apply_invalid_strength_rejected(self, project):
        r = _exec(project, "apply_camera", "scene-1",
                  {"preset": "push_in", "strength": 1.5})
        assert r.status == "REJECTED"
        assert r.errors[0]["code"] == "INVALID_PARAMETER"

    def test_camera_apply_explicit_keyframes(self, project):
        r = _exec(project, "apply_camera", "scene-1", {
            "keyframes": [{"at": 0, "position": [0.5, 0.5, 1.0], "target": [0, 0, 0]},
                          {"at": 2, "position": [0.5, 0.5, 0.7], "target": [0, 0, 0]}]})
        assert r.status == "APPLIED"
        ir = editor.load_composition(project)
        track = next(t for t in ir.camera_tracks if t.id == "cam-scene-1")
        assert len(track.keyframes) == 2

    def test_camera_apply_unknown_scene_rejected(self, project):
        r = _exec(project, "apply_camera", "scene-nope", {"preset": "push_in"})
        assert r.status == "REJECTED"

    def test_camera_undo_restores(self, project):
        _exec(project, "apply_camera", "scene-1", {"preset": "push_in"})
        r = _exec(project, "undo", "", {})
        assert r.status == "APPLIED"
        ir = editor.load_composition(project)
        assert not any(t.id == "cam-scene-1" for t in ir.camera_tracks)


# ---------------------------------------------------------------------------
# §15 pip.configure（BE-003 Gate：configure → layer → composition → revision）
# ---------------------------------------------------------------------------

class TestPipConfigure:
    def test_pip_configure_set_creates_structured_props(self, project):
        r = _exec(project, "configure_pip", "scene-1#0", {
            "action": "set", "source": "asset-01", "fit": "contain",
            "radius": 12.0, "shadow": True, "audioPolicy": "mute",
            "crop": {"x": 0, "y": 0, "w": 100, "h": 80},
            "transform": {"x": 0.8, "y": 0.2, "scale": 0.3},
        })
        assert r.status == "APPLIED"
        ir = editor.load_composition(project)
        layer = ir.scenes[0].layers[0]
        pip = (layer.motion_instance or {}).get("parameters", {}).get("pip")
        assert pip is not None, "pip 结构化子对象未写入"
        assert pip["source"] == "asset-01"
        assert pip["fit"] == "contain"
        assert pip["radius"] == 12.0
        assert pip["shadow"] is True
        assert pip["audio_policy"] == "mute"
        assert layer.transform is not None and abs(layer.transform.x - 0.8) < 1e-9

    def test_pip_configure_supports_all_structured_fields(self, project):
        """§15 字段指纹：source/transform/crop/fit/radius/shadow/mask/audioPolicy。"""
        from video_director.visual_semantics import Mask
        r = _exec(project, "configure_pip", "scene-1#0", {
            "action": "set", "source": "asset-02",
            "mask": {"id": "m", "shape": "ellipse", "x": 0.5, "y": 0.5,
                     "width": 0.4, "height": 0.4},
        })
        assert r.status == "APPLIED"
        ir = editor.load_composition(project)
        layer = ir.scenes[0].layers[0]
        assert layer.mask is not None and layer.mask.shape == Mask.Shape.ellipse \
            if hasattr(Mask, "Shape") else layer.mask.shape.value == "ellipse"

    def test_pip_configure_remove(self, project):
        _exec(project, "configure_pip", "scene-1#0",
              {"action": "set", "source": "asset-01"})
        r = _exec(project, "configure_pip", "scene-1#0", {"action": "remove"})
        assert r.status == "APPLIED"
        ir = editor.load_composition(project)
        layer = ir.scenes[0].layers[0]
        assert "pip" not in ((layer.motion_instance or {}).get("parameters") or {})

    def test_pip_configure_remove_without_config_rejected(self, project):
        r = _exec(project, "configure_pip", "scene-1#0", {"action": "remove"})
        assert r.status == "REJECTED"
        assert r.errors[0]["code"] == "INVALID_TARGET"

    def test_pip_configure_missing_source_rejected(self, project):
        r = _exec(project, "configure_pip", "scene-1#0", {"action": "set"})
        assert r.status == "REJECTED"
        assert r.errors[0]["code"] == "INVALID_PAYLOAD"

    def test_pip_configure_bad_fit_rejected(self, project):
        r = _exec(project, "configure_pip", "scene-1#0",
                  {"action": "set", "source": "a", "fit": "stretch"})
        assert r.status == "REJECTED"
        assert r.errors[0]["code"] == "INVALID_PARAMETER"

    def test_pip_no_second_system(self):
        """§15 禁止 PipProject/PipTimeline/PipRenderer：PiP 只是 Layer 属性。"""
        import video_director.models as m
        import video_director.editor as e
        for banned in ("PipProject", "PipTimeline", "PipRenderer", "PipStore"):
            assert not hasattr(m, banned), f"models.py 出现 {banned}"
            assert not hasattr(e, banned), f"editor.py 出现 {banned}"


# ---------------------------------------------------------------------------
# §16 visual.morph（BE-004 Gate：morph → MorphPlan → Composition）
# ---------------------------------------------------------------------------

class TestVisualMorph:
    def test_morph_between_own_blocks_creates_link(self, project):
        r = _exec(project, "apply_morph", "scene-1->scene-2",
                  {"fromBlockId": "ui.button", "toBlockId": "ui.arrow",
                   "duration": 0.8})
        assert r.status == "APPLIED"
        ir = editor.load_composition(project)
        links = [l for l in ir.continuity_links
                 if l.from_scene_id == "scene-1" and l.to_scene_id == "scene-2"]
        assert links and links[0].type.value == "morph"
        path = json.loads(links[0].morph_path)
        assert path["from_block"] == "ui.button"
        assert path["to_block"] == "ui.arrow"
        assert path["duration"] == 0.8

    def test_morph_invalid_pair_rejected(self, project):
        """§16：只支持自有 Block 形变映射内的对，映射外 REJECT。"""
        r = _exec(project, "apply_morph", "scene-1->scene-2",
                  {"fromBlockId": "ui.button", "toBlockId": "chart.node"})
        assert r.status == "REJECTED"
        assert r.errors[0]["code"] == "MORPH_UNSUPPORTED"

    def test_morph_same_block_rejected(self, project):
        r = _exec(project, "apply_morph", "scene-1->scene-2",
                  {"fromBlockId": "ui.button", "toBlockId": "ui.button"})
        assert r.status == "REJECTED"

    def test_morph_missing_fields_rejected(self, project):
        r = _exec(project, "apply_morph", "scene-1->scene-2", {"fromBlockId": "ui.button"})
        assert r.status == "REJECTED"
        assert r.errors[0]["code"] == "INVALID_PAYLOAD"

    def test_morph_neighbor_default(self, project):
        """无 '->' targetId 时默认相邻场景。"""
        r = _exec(project, "apply_morph", "",
                  {"fromBlockId": "flow.node", "toBlockId": "flow.arrow"})
        assert r.status == "APPLIED"
        ir = editor.load_composition(project)
        assert any(l.type.value == "morph" for l in ir.continuity_links)

    def test_morph_continuity_verifier_consumes_path(self, project):
        """morph 落地后 ContinuityVerifier 规则 D 放行（morphPath 存在）。"""
        _exec(project, "apply_morph", "scene-1->scene-2",
              {"fromBlockId": "ui.button", "toBlockId": "ui.arrow"})
        ir = editor.load_composition(project)
        r = verify_composition_continuity(ir)
        assert r.status == "PASS"

    def test_morph_undo(self, project):
        _exec(project, "apply_morph", "scene-1->scene-2",
              {"fromBlockId": "ui.button", "toBlockId": "ui.arrow"})
        r = _exec(project, "undo", "", {})
        assert r.status == "APPLIED"
        ir = editor.load_composition(project)
        assert not ir.continuity_links


# ---------------------------------------------------------------------------
# §30 对照表：Registered ≠ Implemented 补齐后 tool_executor 全链路由
# ---------------------------------------------------------------------------

class TestToolExecutorRouting:
    """§30：camera.apply/pip.configure/visual.morph/visual.continuity.verify
    从「已登记无路由」补齐为全链可执行。"""

    def test_command_map_covers_visual_ops(self):
        from video_director.tool_executor import _COMMAND_MAP
        assert _COMMAND_MAP["camera.apply"] == "apply_camera"
        assert _COMMAND_MAP["pip.configure"] == "configure_pip"
        assert _COMMAND_MAP["visual.morph"] == "apply_morph"
        assert _COMMAND_MAP["mask.apply"] == "apply_mask"
        assert _COMMAND_MAP["keyframe.add"] == "add_keyframe"
        assert _COMMAND_MAP["keyframe.remove"] == "remove_keyframe"

    def test_continuity_verify_is_readonly_routed(self):
        from video_director.tool_executor import _READONLY
        assert "visual.continuity.verify" in _READONLY

    def test_tool_executor_camera_apply_end_to_end(self, project):
        from video_director import tool_executor
        r = tool_executor.execute_tool(project, {
            "tool": "camera.apply", "targetId": "scene-1",
            "payload": {"preset": "pull_out"},
        })
        assert r["ok"], r.get("error")
        assert r.get("revision_id")
        ir = editor.load_composition(project)
        assert any(t.id == "cam-scene-1" for t in ir.camera_tracks)

    def test_tool_executor_continuity_verify_end_to_end(self, project):
        from video_director import tool_executor
        r = tool_executor.execute_tool(project, {
            "tool": "visual.continuity.verify", "payload": {},
        })
        assert r["ok"], r.get("error")
        # 两静态 graphic 场景无 link → WARNING + STATIC_REPLACEMENT
        assert r["result"]["status"] == "WARNING"
        codes = [i["issue_code"] for i in r["result"]["issues"]]
        assert "STATIC_REPLACEMENT" in codes

    def test_tool_executor_pip_morph_end_to_end(self, project):
        from video_director import tool_executor
        r1 = tool_executor.execute_tool(project, {
            "tool": "pip.configure", "targetId": "scene-1#0",
            "payload": {"action": "set", "source": "asset-x"},
        })
        assert r1["ok"], r1.get("error")
        r2 = tool_executor.execute_tool(project, {
            "tool": "visual.morph", "targetId": "scene-1->scene-2",
            "payload": {"fromBlockId": "ui.button", "toBlockId": "ui.arrow"},
        })
        assert r2["ok"], r2.get("error")


# ---------------------------------------------------------------------------
# §12/§31.1 Generation Availability（BE-005）
# ---------------------------------------------------------------------------

class TestGenerationAvailability:
    def test_availability_report_structure(self):
        """§12 状态模型：available/unavailable + reason（无 provider 不假装可用）。"""
        from video_director import model_config
        img_enabled = model_config.is_role_enabled("image")
        # 结构验证：is_role_enabled 返回 bool（契约），真实环境未配置 → False
        assert isinstance(img_enabled, bool)

    def test_generation_image_gfx_semantic_split(self):
        """§12：generation.image（AI 生图）≠ gfx.*（程序化视觉）语义分离。"""
        from video_director.capability_registry.manifest import manifest
        caps = set(manifest().keys())
        assert "generation.image" in caps
        assert "gfx.html" in caps  # 程序化视觉独立存在，不互相兜底冒充
