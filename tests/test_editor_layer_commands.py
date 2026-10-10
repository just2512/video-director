"""Regression tests for actual layer-level edits and reloadable timeline state."""
from __future__ import annotations

import copy

from tests.fixtures.fixture_data import RECIPE_PROJECT
from video_director.editor import execute_command, load_composition, save_composition
from video_director.models import CompositionIR


def _project(tmp_path, monkeypatch, composition_data=None):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    pid = "timeline-editor-test"
    plans = tmp_path / pid / "plans"
    plans.mkdir(parents=True)
    data = copy.deepcopy(composition_data or RECIPE_PROJECT["composition"])
    comp = CompositionIR(**data)
    save_composition(pid, comp)
    (plans / "revisions.jsonl").write_text("", encoding="utf-8")
    return pid, plans


def _layer_comp():
    data = copy.deepcopy(RECIPE_PROJECT["composition"])
    data["scenes"][0]["layers"] = [
        {"type": "image", "asset_id": "img1", "start": 0.0, "duration": 2.0},
        {"type": "image", "asset_id": "img2", "start": 2.0, "duration": 3.0},
    ]
    return data


def _video_comp():
    data = copy.deepcopy(RECIPE_PROJECT["composition"])
    data["scenes"][0]["layers"] = [
        {"type": "video", "asset_id": "vid1", "start": 0.0, "duration": 5.0,
         "speed": 1.0, "source_in": 0.0, "source_out": 5.0}
    ]
    return data


def _apply(pid, command, target, payload):
    return execute_command(pid, {
        "command": command, "targetId": target, "payload": payload, "expectedVersion": 1
    })


def test_split_layer_at_playhead_preserves_video_source_and_scene_duration(tmp_path, monkeypatch):
    pid, plans = _project(tmp_path, monkeypatch, _video_comp())
    result = _apply(pid, "split_layer", "S01#0", {"at": 2.25})
    assert result.status == "APPLIED", result.errors
    comp = load_composition(pid)
    layers = comp.scenes[0].layers
    assert len(layers) == 2
    assert [(round(x.start, 3), round(x.duration, 3)) for x in layers] == [(0.0, 2.25), (2.25, 2.75)]
    assert [(round(x.source_in, 3), round(x.source_out, 3)) for x in layers] == [(0.0, 2.25), (2.25, 5.0)]
    assert round(sum(x.duration for x in layers), 3) == 5.0
    assert len([line for line in (plans / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]) == 1


def test_split_layer_rejects_playhead_outside_clip_without_mutating(tmp_path, monkeypatch):
    pid, _plans = _project(tmp_path, monkeypatch, _video_comp())
    result = _apply(pid, "split_layer", "S01#0", {"at": 0.0})
    assert result.status == "REJECTED"
    assert result.errors[0]["code"] == "SPLIT_OUTSIDE_CLIP"
    assert len(load_composition(pid).scenes[0].layers) == 1


def test_reorder_layer_preserves_duration_and_asset_source_ranges(tmp_path, monkeypatch):
    pid, _plans = _project(tmp_path, monkeypatch, _layer_comp())
    result = _apply(pid, "reorder_layer", "S01#0", {"toIndex": 1})
    assert result.status == "APPLIED", result.errors
    scene = load_composition(pid).scenes[0]
    assert [layer.asset_id for layer in scene.layers] == ["img2", "img1"]
    assert [(round(layer.start, 3), round(layer.duration, 3)) for layer in scene.layers] == [(0.0, 3.0), (3.0, 2.0)]
    assert round(scene.duration, 3) == 5.0


def test_trim_ripples_scene_and_keeps_composition_reloadable(tmp_path, monkeypatch):
    data = copy.deepcopy(RECIPE_PROJECT["composition"])
    data["scenes"][0]["layers"] = [
        {"type": "video", "asset_id": "vid1", "start": 0.0, "duration": 3.0,
         "speed": 1.0, "source_in": 0.0, "source_out": 3.0},
        {"type": "image", "asset_id": "img1", "start": 3.0, "duration": 2.0}
    ]
    pid, _plans = _project(tmp_path, monkeypatch, data)
    result = _apply(pid, "trim", "S01#0", {"sourceIn": 0.5, "sourceOut": 2.5})
    assert result.status == "APPLIED", result.errors
    comp = load_composition(pid)
    assert round(comp.scenes[0].layers[0].duration, 3) == 2.0
    assert round(comp.scenes[0].layers[1].start, 3) == 2.0
    assert round(comp.scenes[0].duration, 3) == 4.0
    assert round(comp.scenes[1].start, 3) == 4.0
    assert round(comp.project.duration, 3) == 9.0


def test_layer_edit_buttons_and_cross_scene_drop_no_longer_reorders_scene():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    js = (root / "webui" / "static" / "js" / "timeline.js").read_text(encoding="utf-8")
    html = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "editorSplitSelectedLayer" in js and "editorCmd('split_layer'" in js
    assert "reorder_layer" in js
    assert "跨分镜移动需要独立物理轨道" in js
    assert 'onclick="editorSplitSelectedLayer()" title="在播放头处切分选中的视频/图片片段"' in html
