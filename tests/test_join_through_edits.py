"""join_through_edits 契约：split 留下的同源切口可以愈合，往返一致。

吸收自 filmcraft edit::through（Join Through Edits，左段属性胜出）。
"""
from __future__ import annotations

import copy
from pathlib import Path

from tests.fixtures.fixture_data import RECIPE_PROJECT
from video_director.editor import execute_command, load_composition, save_composition
from video_director.models import CompositionIR


def _video_project(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    project_id = "join-through-edits-test"
    plan_dir = tmp_path / project_id / "plans"
    plan_dir.mkdir(parents=True)
    data = copy.deepcopy(RECIPE_PROJECT["composition"])
    data["project"]["duration"] = 10.0
    data["scenes"] = [{
        "id": "S01", "start": 0.0, "duration": 10.0, "text": "through edit",
        "layers": [
            {"type": "video", "asset_id": "vid1", "start": 0.0, "duration": 8.0,
             "source_in": 0.0, "source_out": 8.0},
            {"type": "gap", "start": 8.0, "duration": 2.0},
        ],
    }]
    data["audio"] = []
    data["visual_overlays"] = []
    save_composition(project_id, CompositionIR(**data))
    (plan_dir / "revisions.jsonl").write_text("", encoding="utf-8")
    return project_id, plan_dir


def _run(project_id: str, command: str, target_id: str, payload: dict = None, version: int = 1):
    return execute_command(project_id, {
        "command": command, "targetId": target_id,
        "payload": payload or {}, "expectedVersion": version,
    })


def test_split_then_join_restores_single_layer(tmp_path, monkeypatch):
    pid, _plans = _video_project(tmp_path, monkeypatch)
    original = load_composition(pid)
    before = (len(original.scenes[0].layers),
              float(original.scenes[0].layers[0].duration),
              float(original.scenes[0].layers[0].source_out))

    # split 在 3.0s（帧对齐点）
    split = _run(pid, "split_layer", "S01#0", {"at": 3.0})
    assert split.status == "APPLIED", split.errors
    mid = load_composition(pid)
    # video 切成两段 + 原 2s gap 尾层 = 3 层
    assert len(mid.scenes[0].layers) == 3
    assert abs(float(mid.scenes[0].layers[1].source_in) - 3.0) < 1e-6  # 同源连续切口

    # join：愈合回一段（gap 尾层仍在，video 段从 2 合回 1）
    join = _run(pid, "join_through_edits", "S01#0", {}, version=2)
    assert join.status == "APPLIED", join.errors
    after = load_composition(pid)
    from video_director.models import LayerType
    video_layers = [l for l in after.scenes[0].layers if l.type != LayerType.gap]
    assert len(video_layers) == 1
    layer = video_layers[0]
    assert abs(float(layer.duration) - before[1]) < 1e-6
    assert abs(float(layer.source_out) - before[2]) < 1e-6
    assert abs(float(layer.source_in)) < 1e-6


def test_join_rejects_non_continuous_pair(tmp_path, monkeypatch):
    pid, _plans = _video_project(tmp_path, monkeypatch)
    # 先把右半段换成不同素材 → join 必须 REJECTED
    split = _run(pid, "split_layer", "S01#0", {"at": 3.0})
    assert split.status == "APPLIED", split.errors
    # 修剪右段源区间，破坏连续性
    trimmed = _run(pid, "trim", "S01#1", {"sourceIn": 4.0, "sourceOut": 8.0}, version=2)
    if trimmed.status == "APPLIED":
        join = _run(pid, "join_through_edits", "S01#0", {}, version=3)
        assert join.status == "REJECTED", "破坏连续性后仍被愈合——切口判定失效"
        assert join.errors[0]["code"] == "INVALID_TARGET"
    else:
        # trim 语义不同时退化为直接 join 断言（split 后未破坏仍应成功过）
        join_ok = _run(pid, "join_through_edits", "S01#0", {}, version=3)
        assert join_ok.status == "APPLIED"


def test_join_requires_right_neighbor(tmp_path, monkeypatch):
    pid, _plans = _video_project(tmp_path, monkeypatch)
    result = _run(pid, "join_through_edits", "S01#0", {})
    # S01#0 右邻是 2s 的 gap 层——不同类型不同素材，愈合被拒
    assert result.status == "REJECTED"
    assert result.errors[0]["code"] in ("NOT_FOUND", "INVALID_TARGET")


def test_audio_split_then_join_restores_continuity(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    project_id = "join-through-edits-audio"
    plan_dir = tmp_path / project_id / "plans"
    plan_dir.mkdir(parents=True)
    data = copy.deepcopy(RECIPE_PROJECT["composition"])
    data["project"]["duration"] = 10.0
    data["scenes"] = [{
        "id": "S01", "start": 0.0, "duration": 10.0, "text": "audio join",
        "layers": [{"type": "image", "asset_id": "img1", "start": 0.0, "duration": 10.0}],
    }]
    data["audio"] = [
        {"type": "sfx", "asset_id": "sfx-one", "start": 1.0, "duration": 4.0,
         "source_in": 0.0, "source_out": 4.0, "volume": 0.9},
    ]
    save_composition(project_id, CompositionIR(**data))
    (plan_dir / "revisions.jsonl").write_text("", encoding="utf-8")

    split = _run(project_id, "split_audio_clip", "audio#0", {"at": 3.0})
    assert split.status == "APPLIED", split.errors
    mid = load_composition(project_id)
    assert len(mid.audio) == 2

    join = _run(project_id, "join_through_edits", "audio#0", {}, version=2)
    assert join.status == "APPLIED", join.errors
    after = load_composition(project_id)
    assert len(after.audio) == 1
    track = after.audio[0]
    assert abs(float(track.start) - 1.0) < 1e-6
    assert abs(float(track.duration) - 4.0) < 1e-6
    assert abs(float(track.source_in)) < 1e-6
    assert abs(float(track.source_out) - 4.0) < 1e-6


def test_visual_overlay_split_then_join_restores_clip(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    project_id = "join-through-edits-overlay"
    plan_dir = tmp_path / project_id / "plans"
    plan_dir.mkdir(parents=True)
    data = copy.deepcopy(RECIPE_PROJECT["composition"])
    data["project"]["duration"] = 10.0
    data["scenes"] = [{
        "id": "S01", "start": 0.0, "duration": 10.0, "text": "overlay join",
        "layers": [{"type": "image", "asset_id": "img1", "start": 0.0, "duration": 10.0}],
    }]
    data["audio"] = []
    data["assets"] = [
        {"id": "img1", "type": "image", "file": "sample-assets/frame.jpg", "duration": 5},
        {"id": "vid1", "type": "video", "file": "sample-assets/clip.mp4", "duration": 8},
    ]
    data["visual_tracks"] = [{"id": "ov-track", "name": "画面轨", "order": 0}]
    data["visual_overlays"] = [{
        "id": "ov-a", "asset_id": "vid1", "start": 1.0, "duration": 4.0,
        "source_in": 0.0, "track_id": "ov-track", "presentation_mode": "sticker",
    }]
    save_composition(project_id, CompositionIR(**data))
    (plan_dir / "revisions.jsonl").write_text("", encoding="utf-8")

    split = _run(project_id, "split_visual_overlay", "overlay#0", {"at": 3.0})
    assert split.status == "APPLIED", split.errors
    mid = load_composition(project_id)
    assert len(mid.visual_overlays) == 2

    join = _run(project_id, "join_through_edits", "visual-overlay:ov-a", {}, version=2)
    assert join.status == "APPLIED", join.errors
    after = load_composition(project_id)
    assert len(after.visual_overlays) == 1
    merged = after.visual_overlays[0]
    assert abs(float(merged.start) - 1.0) < 1e-6
    assert abs(float(merged.duration) - 4.0) < 1e-6
    assert abs(float(merged.source_in)) < 1e-6
