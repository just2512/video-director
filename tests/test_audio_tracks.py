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



def test_locked_audio_track_rejects_all_clip_timing_and_property_edits(tmp_path, monkeypatch):
    pid, plan_dir = _project(tmp_path, monkeypatch)
    locked = _apply(pid, "set_track_locked", "audio-bgm", {"trackType": "audio", "locked": True}, version=1)
    assert locked.status == "APPLIED", locked.errors
    assert locked.composition_version == 2
    comp = load_composition(pid)
    lane = next(item for item in comp.audio_tracks if item.id == "audio-bgm")
    assert lane.locked is True
    original = comp.audio[0].model_dump(mode="json")

    attempts = [
        ("move_audio_clip", {"start": 2.0}),
        ("trim_audio_clip", {"start": 2.0, "sourceIn": 0.5, "sourceOut": 2.5}),
        ("split_audio_clip", {"at": 3.0}),
        ("set_audio_clip_properties", {"volume": 0.3}),
    ]
    for command, payload in attempts:
        result = _apply(pid, command, "audio#0", payload, version=2)
        assert result.status == "REJECTED", (command, result.status, result.errors)
        assert result.errors[0]["code"] == "TRACK_LOCKED", (command, result.errors)
        assert load_composition(pid).audio[0].model_dump(mode="json") == original
        assert result.composition_version == 2

    revisions = [line for line in (plan_dir / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(revisions) == 1, "rejected edits must not create revision history"


def test_locked_audio_destination_rejects_cross_track_move_without_mutation(tmp_path, monkeypatch):
    pid, _plan_dir = _project(tmp_path, monkeypatch)
    created = _apply(pid, "add_audio_track", "audio-tracks", {"name": "锁定目标"}, version=1)
    assert created.status == "APPLIED", created.errors
    lane_id = next(lane.id for lane in load_composition(pid).audio_tracks if lane.name == "锁定目标")
    locked = _apply(pid, "set_track_locked", lane_id, {"trackType": "audio", "locked": True}, version=2)
    assert locked.status == "APPLIED", locked.errors

    before = load_composition(pid).audio[1].model_dump(mode="json")
    result = _apply(pid, "move_audio_clip", "audio#1", {"start": 5.5, "trackId": lane_id}, version=3)
    assert result.status == "REJECTED"
    assert result.errors[0]["code"] == "TRACK_LOCKED"
    assert load_composition(pid).audio[1].model_dump(mode="json") == before


def test_legacy_audio_lane_defaults_to_unlocked_and_track_lock_controls_are_wired(tmp_path, monkeypatch):
    from pathlib import Path

    pid, _ = _project(tmp_path, monkeypatch)
    legacy = load_composition(pid).model_dump(mode="json")
    for lane in legacy.get("audio_tracks", []):
        lane.pop("locked", None)
    migrated = CompositionIR.model_validate(legacy)
    assert all(lane.locked is False for lane in migrated.audio_tracks)

    root = Path(__file__).resolve().parents[1]
    timeline = (root / "webui/static/js/timeline.js").read_text(encoding="utf-8")
    visual = (root / "webui/static/js/visual-overlay-editor.js").read_text(encoding="utf-8")
    audit = (root / "video_director/ui_action_audit.py").read_text(encoding="utf-8")
    assert "editorAudioTrackToggleLock(event" in timeline
    assert "if (lane && lane.locked)" in timeline
    assert "editorVisualTrackToggleLock(event" in timeline
    assert "if (lane && lane.locked)" in visual
    assert "set_track_locked" in timeline and "set_track_locked" in visual
    assert "editorAudioTrackToggleLock(" in audit
    assert "editorVisualTrackToggleLock(" in audit



def test_locked_audio_track_blocks_audio_bus_alias_commands_atomically(tmp_path, monkeypatch):
    pid, _ = _project(tmp_path, monkeypatch)
    locked = _apply(pid, "set_track_locked", "audio-bgm", {"trackType": "audio", "locked": True}, version=1)
    assert locked.status == "APPLIED", locked.errors
    before = {clip.id: clip.model_dump(mode="json") for clip in load_composition(pid).audio}

    commands = [
        ("set_audio_volume", "bgm", {"volume": 0.2}),
        ("set_audio_mute", "bgm", {"muted": True}),
        ("set_audio_fade", "bgm", {"fadeIn": 0.5}),
        ("add_audio_keyframe", "bgm", {"at": 2.0, "volume": 0.4}),
        ("audio_mix", "", {"tracks": {"sfx": {"volume": 0.25}, "bgm": {"volume": 0.1}}}),
    ]
    for command, target, payload in commands:
        result = _apply(pid, command, target, payload, version=2)
        assert result.status == "REJECTED", (command, result.status, result.errors)
        assert result.errors[0]["code"] == "TRACK_LOCKED", (command, result.errors)
        current = {clip.id: clip.model_dump(mode="json") for clip in load_composition(pid).audio}
        assert current == before, f"{command} partially mutated audio clips despite a locked lane"
        assert result.composition_version == 2


def test_add_edit_all_tracks_skips_locked_audio_clip_but_cuts_other_tracks(tmp_path, monkeypatch):
    pid, _ = _project(tmp_path, monkeypatch)
    locked = _apply(pid, "set_track_locked", "audio-bgm", {"trackType": "audio", "locked": True}, version=1)
    assert locked.status == "APPLIED", locked.errors

    result = _apply(pid, "add_edit_all_tracks", "timeline", {"at": 3.0}, version=2)
    assert result.status == "APPLIED", result.errors
    assert any("TRACK_LOCKED" in warning for warning in result.warnings), result.warnings

    comp = load_composition(pid)
    bgm = [clip for clip in comp.audio if clip.type.value == "bgm"]
    assert len(bgm) == 1
    assert (bgm[0].start, bgm[0].duration, bgm[0].source_in, bgm[0].source_out) == (1.0, 4.0, 0.0, 4.0)
    assert len(comp.scenes[0].layers) == 2, "unlocked main picture still receives the add edit"



def test_locked_original_audio_lane_rejects_original_bus_commands(tmp_path, monkeypatch):
    pid, _ = _project(tmp_path, monkeypatch)
    locked = _apply(pid, "set_track_locked", "audio-original", {"trackType": "audio", "locked": True}, version=1)
    assert locked.status == "APPLIED", locked.errors

    for command, target, payload in [
        ("set_original_audio_volume", "original", {"volume": 0.3}),
        ("set_original_audio", "original", {"mode": "mute"}),
        ("audio_mix", "", {"tracks": {"original": {"volume": 0.5}, "sfx": {"volume": 0.2}}}),
    ]:
        before = [clip.model_dump(mode="json") for clip in load_composition(pid).audio]
        result = _apply(pid, command, target, payload, version=2)
        assert result.status == "REJECTED", (command, result.status, result.errors)
        assert result.errors[0]["code"] == "TRACK_LOCKED", (command, result.errors)
        assert [clip.model_dump(mode="json") for clip in load_composition(pid).audio] == before
