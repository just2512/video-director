"""视觉语义命令端到端测试（execute_command 全链：校验→修改→revision 落盘）。

验证 §72 六 Contract 不只是登记在 manifest，而是真实可执行：
    set_visual_transform / add_keyframe / remove_keyframe / apply_mask / remove_mask
走与既有编辑命令完全相同的 Command Bus 路径（版本冲突/revision/undo 链）。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from video_director import config, editor
from video_director.models import CompositionIR, Layer, LayerType, SceneIR


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """建一个最小项目并落盘 IR，返回 project_id。"""
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    ir = CompositionIR(
        project={"width": 1080, "height": 1920, "fps": 30, "duration": 5.0},
        scenes=[SceneIR(id="s1", start=0.0, duration=5.0, text="测试",
                        layers=[Layer(type=LayerType.image, duration=5.0)])],
    )
    pid = "p-vis-cmd-test"
    base = tmp_path / pid
    base.mkdir(parents=True, exist_ok=True)
    editor.save_composition(pid, ir)
    yield pid
    shutil.rmtree(tmp_path / pid, ignore_errors=True)


def _run(pid, cmd, payload=None, target="s1#0", expect="APPLIED"):
    result = editor.execute_command(pid, {
        "command": cmd, "targetId": target, "payload": payload or {},
    })
    assert result.status == expect, f"{cmd} → {result.status}: {result.errors}"
    return result


def _layer(pid):
    ir = editor.load_composition(pid)
    return ir.scenes[0].layers[0]


class TestVisualTransformCommand:
    def test_set_transform_writes_structured(self, project):
        r = _run(project, "set_visual_transform",
                 {"x": 0.3, "y": 0.7, "scale": 1.5, "rotation": 15, "opacity": 0.8})
        assert r.status == "APPLIED" and r.revision_id
        t = _layer(project).transform
        assert t is not None
        assert (t.x, t.y, t.scale, t.rotation, t.opacity) == (0.3, 0.7, 1.5, 15.0, 0.8)

    def test_transform_incremental_update(self, project):
        """只给 scale 时其余字段保留（增量更新非覆盖）。"""
        _run(project, "set_visual_transform", {"x": 0.2, "y": 0.8})
        _run(project, "set_visual_transform", {"scale": 2.0})
        t = _layer(project).transform
        assert (t.x, t.y, t.scale) == (0.2, 0.8, 2.0)

    def test_transform_rejects_out_of_range(self, project):
        r = editor.execute_command(project, {
            "command": "set_visual_transform", "targetId": "s1#0",
            "payload": {"scale": 99.0},
        })
        assert r.status == "REJECTED"
        assert any(e["code"] == "INVALID_PARAMETER" for e in r.errors)

    def test_transform_rejects_empty_payload(self, project):
        r = editor.execute_command(project, {
            "command": "set_visual_transform", "targetId": "s1#0", "payload": {},
        })
        assert r.status == "REJECTED"
        assert any(e["code"] == "INVALID_PAYLOAD" for e in r.errors)

    def test_opacity_backfills_legacy_param(self, project):
        """opacity 同时写 motion_instance.parameters（旧渲染链消费点兼容）。"""
        _run(project, "set_visual_transform", {"opacity": 0.5})
        lay = _layer(project)
        assert lay.transform.opacity == 0.5
        assert lay.motion_instance["parameters"]["opacity"] == 0.5


class TestKeyframeCommands:
    def test_add_keyframe_creates_track(self, project):
        _run(project, "add_keyframe", {"property": "opacity", "at": 0.0, "value": 0.0})
        _run(project, "add_keyframe", {"property": "opacity", "at": 2.0, "value": 1.0,
                                       "easing": "ease_out"})
        lay = _layer(project)
        assert len(lay.keyframes) == 1
        assert lay.keyframes[0].property == "opacity"
        assert [k.at for k in lay.keyframes[0].keyframes] == [0.0, 2.0]
        assert lay.keyframes[0].keyframes[1].easing.value == "ease_out"

    def test_add_out_of_order_sorts(self, project):
        """乱序添加自动按时间排序。"""
        _run(project, "add_keyframe", {"property": "scale", "at": 3.0, "value": 1.2})
        _run(project, "add_keyframe", {"property": "scale", "at": 1.0, "value": 1.0})
        assert [k.at for k in _layer(project).keyframes[0].keyframes] == [1.0, 3.0]

    def test_add_same_at_overwrites(self, project):
        """同 property 同 at → 覆盖。"""
        _run(project, "add_keyframe", {"property": "scale", "at": 1.0, "value": 1.0})
        _run(project, "add_keyframe", {"property": "scale", "at": 1.0, "value": 2.0})
        kfs = _layer(project).keyframes[0].keyframes
        assert len(kfs) == 1 and kfs[0].value == 2.0

    def test_add_rejects_bad_property(self, project):
        r = editor.execute_command(project, {
            "command": "add_keyframe", "targetId": "s1#0",
            "payload": {"property": "bogus", "at": 0, "value": 1},
        })
        assert r.status == "REJECTED"

    def test_remove_keyframe(self, project):
        _run(project, "add_keyframe", {"property": "opacity", "at": 0.0, "value": 0.0})
        _run(project, "add_keyframe", {"property": "opacity", "at": 2.0, "value": 1.0})
        _run(project, "remove_keyframe", {"property": "opacity", "at": 0.0})
        kfs = _layer(project).keyframes[0].keyframes
        assert [k.at for k in kfs] == [2.0]

    def test_remove_last_keyframe_drops_track(self, project):
        _run(project, "add_keyframe", {"property": "opacity", "at": 0.0, "value": 0.0})
        _run(project, "remove_keyframe", {"property": "opacity", "at": 0.0})
        assert _layer(project).keyframes == []

    def test_remove_missing_track_rejected(self, project):
        r = editor.execute_command(project, {
            "command": "remove_keyframe", "targetId": "s1#0",
            "payload": {"property": "opacity", "at": 0.0},
        })
        assert r.status == "REJECTED"
        assert any(e["code"] == "INVALID_TARGET" for e in r.errors)


class TestMaskCommands:
    def test_apply_mask(self, project):
        _run(project, "apply_mask", {"shape": "ellipse", "width": 0.6, "height": 0.6,
                                     "feather": 12, "inverted": False})
        m = _layer(project).mask
        assert m is not None
        assert m.shape.value == "ellipse"
        assert m.feather == 12.0

    def test_apply_mask_overwrites(self, project):
        _run(project, "apply_mask", {"shape": "rectangle"})
        _run(project, "apply_mask", {"shape": "ellipse"})
        assert _layer(project).mask.shape.value == "ellipse"

    def test_apply_polygon_requires_geometry(self, project):
        r = editor.execute_command(project, {
            "command": "apply_mask", "targetId": "s1#0",
            "payload": {"shape": "polygon"},
        })
        assert r.status == "REJECTED"

    def test_remove_mask(self, project):
        _run(project, "apply_mask", {"shape": "rectangle"})
        _run(project, "remove_mask", {})
        assert _layer(project).mask is None

    def test_remove_mask_without_mask_rejected(self, project):
        r = editor.execute_command(project, {
            "command": "remove_mask", "targetId": "s1#0", "payload": {},
        })
        assert r.status == "REJECTED"


class TestRevisionChain:
    def test_visual_command_produces_revision(self, project):
        """视觉语义命令走标准 revision 链（changed/stale/版本递增）。"""
        v0 = editor.composition_version(project)
        r = _run(project, "set_visual_transform", {"scale": 1.3})
        assert r.composition_version == v0 + 1
        assert r.changed_artifacts
        revs = editor.load_revisions(project)
        assert revs and revs[-1]["command"] == "set_visual_transform"

    def test_visual_command_undoable(self, project):
        """§46 undo 链对新命令同样生效。"""
        _run(project, "set_visual_transform", {"scale": 2.0})
        _run(project, "undo")
        assert _layer(project).transform is None

    def test_locked_layer_rejected(self, project):
        _run(project, "lock", {"locked": True})
        r = editor.execute_command(project, {
            "command": "set_visual_transform", "targetId": "s1#0",
            "payload": {"scale": 1.5},
        })
        assert r.status == "REJECTED"
