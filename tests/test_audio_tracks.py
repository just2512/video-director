"""Adversarial contract tests for editable physical audio lanes."""
from __future__ import annotations

import copy

from tests.fixtures.fixture_data import RECIPE_PROJECT
from video_director.editor import execute_command, load_composition, save_composition
from video_director.models import CompositionIR


def _project(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    project_id = "audio-lane-contract-test"
    plan_dir = tmp_path / project_id / "plans"
    plan_dir.mkdir(parents=True)
    data = copy.deepcopy(RECIPE_PROJECT["composition"])
    data["project"]["duration"] = 10.0
    data["scenes"] = [{
        "id": "S01", "start": 0.0, "duration": 10.0, "text": "audio lanes",
        "layers": [{"type": "image", "asset_id": "img1", "start": 0.0, "duration": 10.0}],
    }]
    data["audio"] = [
        {"type": "bgm", "asset_id": "music", "start": 1.0, "duration": 4.0,
         "source_in": 0.0, "source_out": 4.0, "volume": 0.7},
        {"type": "sfx", "asset_id": "impact", "start": 5.0, "duration": 2.0,
         "source_in": 0.0, "source_out": 2.0, "volume": 0.9},
    ]
    save_composition(project_id, CompositionIR(**data))
    (plan_dir / "revisions.jsonl").write_text("", encoding="utf-8")
    return project_id, plan_dir


def _apply(pid, command, target, payload, version=1):
    return execute_command(pid, {
        "command": command, "targetId": target, "payload": payload,
        "expectedVersion": version,
    })


def test_legacy_audio_items_receive_default_physical_lanes_without_role_changes(tmp_path, monkeypatch):
    pid, _ = _project(tmp_path, monkeypatch)
    comp = load_composition(pid)
    lanes = {lane.id: lane for lane in comp.audio_tracks}
    assert comp.audio[0].track_id == "audio-bgm"
    assert comp.audio[1].track_id == "audio-sfx"
    assert lanes["audio-bgm"].name == "背景音乐"
    assert lanes["audio-sfx"].name == "音效"
    assert comp.audio[0].type.value == "bgm"
    assert comp.audio[1].type.value == "sfx"


def test_move_audio_clip_between_lanes_keeps_source_interval_and_mix_role(tmp_path, monkeypatch):
    pid, _ = _project(tmp_path, monkeypatch)
    created = _apply(pid, "add_audio_track", "audio-tracks", {"name": "补录音轨"})
    assert created.status == "APPLIED", created.errors
    lane_id = next(lane.id for lane in load_composition(pid).audio_tracks if lane.name == "补录音轨")
    moved = _apply(pid, "move_audio_clip", "audio#1", {"start": 5.5, "trackId": lane_id}, version=2)
    assert moved.status == "APPLIED", moved.errors
    comp = load_composition(pid)
    clip = comp.audio[1]
    assert clip.track_id == lane_id
    assert clip.type.value == "sfx"
    assert clip.start == 5.5 and clip.duration == 2.0
    assert clip.source_in == 0.0 and clip.source_out == 2.0
    assert clip.volume == 0.9


def test_audio_track_rename_rejects_duplicate_and_remove_rejects_nonempty_lane(tmp_path, monkeypatch):
    pid, plan_dir = _project(tmp_path, monkeypatch)
    duplicate = _apply(pid, "rename_audio_track", "audio-bgm", {"name": "音效"})
    assert duplicate.status == "REJECTED"
    assert duplicate.errors[0]["code"] == "DUPLICATE_TRACK_NAME"
    nonempty = _apply(pid, "remove_audio_track", "audio-bgm", {})
    assert nonempty.status == "REJECTED"
    assert nonempty.errors[0]["code"] == "AUDIO_TRACK_NOT_EMPTY"
    comp = load_composition(pid)
    assert len(comp.audio) == 2
    assert comp.audio[0].track_id == "audio-bgm"
    assert (plan_dir / "revisions.jsonl").read_text(encoding="utf-8") == ""


def test_empty_audio_track_can_be_renamed_and_removed_without_dangling_references(tmp_path, monkeypatch):
    pid, _ = _project(tmp_path, monkeypatch)
    created = _apply(pid, "add_audio_track", "audio-tracks", {"name": "临时"})
    assert created.status == "APPLIED", created.errors
    lane_id = next(lane.id for lane in load_composition(pid).audio_tracks if lane.name == "临时")
    renamed = _apply(pid, "rename_audio_track", lane_id, {"name": "旁白补录"}, version=2)
    assert renamed.status == "APPLIED", renamed.errors
    deleted = _apply(pid, "remove_audio_track", lane_id, {}, version=3)
    assert deleted.status == "APPLIED", deleted.errors
    comp = load_composition(pid)
    valid_ids = {lane.id for lane in comp.audio_tracks}
    assert all(lane.id != lane_id for lane in comp.audio_tracks)
    assert all(clip.track_id in valid_ids for clip in comp.audio)


def test_move_audio_clip_rejects_unknown_lane_without_mutation(tmp_path, monkeypatch):
    pid, plan_dir = _project(tmp_path, monkeypatch)
    result = _apply(pid, "move_audio_clip", "audio#0", {"start": 2.0, "trackId": "missing-lane"})
    assert result.status == "REJECTED"
    assert result.errors[0]["code"] == "NOT_FOUND"
    comp = load_composition(pid)
    assert comp.audio[0].track_id == "audio-bgm"
    assert comp.audio[0].start == 1.0
    assert (plan_dir / "revisions.jsonl").read_text(encoding="utf-8") == ""
