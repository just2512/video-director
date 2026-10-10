"""Regression coverage for per-instance audio clip timeline editing."""
from __future__ import annotations

import copy

from tests.fixtures.fixture_data import RECIPE_PROJECT
from video_director.editor import execute_command, load_composition, save_composition
from video_director.models import CompositionIR


def _audio_project(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    project_id = "audio-clip-editor-test"
    plan_dir = tmp_path / project_id / "plans"
    plan_dir.mkdir(parents=True)
    data = copy.deepcopy(RECIPE_PROJECT["composition"])
    data["project"]["duration"] = 10.0
    data["scenes"] = [{
        "id": "S01", "start": 0.0, "duration": 10.0, "text": "audio clip edit",
        "layers": [{"type": "image", "asset_id": "img1", "start": 0.0, "duration": 10.0}],
    }]
    data["audio"] = [
        {"type": "bgm", "asset_id": "bgm-one", "start": 1.0, "duration": 4.0,
         "source_in": 0.0, "volume": 0.7},
        {"type": "sfx", "asset_id": "sfx-one", "start": 5.0, "duration": 2.0,
         "source_in": 0.0, "volume": 0.9},
    ]
    save_composition(project_id, CompositionIR(**data))
    (plan_dir / "revisions.jsonl").write_text("", encoding="utf-8")
    return project_id, plan_dir


def _apply(project_id, command, target, payload, version=1):
    return execute_command(project_id, {
        "command": command, "targetId": target, "payload": payload,
        "expectedVersion": version,
    })


def test_move_audio_clip_changes_only_timeline_position_and_persists(tmp_path, monkeypatch):
    pid, plan_dir = _audio_project(tmp_path, monkeypatch)
    result = _apply(pid, "move_audio_clip", "audio#0", {"start": 3.25})
    assert result.status == "APPLIED", result.errors
    comp = load_composition(pid)
    assert comp.audio[0].start == 3.25
    assert comp.audio[0].duration == 4.0
    assert comp.audio[0].source_in == 0.0
    assert comp.audio[0].source_out is None
    assert result.composition_version == 2
    revisions = [line for line in (plan_dir / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(revisions) == 1


def test_trim_audio_clip_persists_exact_source_window_and_duration(tmp_path, monkeypatch):
    pid, _ = _audio_project(tmp_path, monkeypatch)
    result = _apply(pid, "trim_audio_clip", "audio#0", {
        "start": 2.0, "sourceIn": 1.5, "sourceOut": 4.0,
    })
    assert result.status == "APPLIED", result.errors
    track = load_composition(pid).audio[0]
    assert track.start == 2.0
    assert track.source_in == 1.5
    assert track.source_out == 4.0
    assert track.duration == 2.5


def test_audio_clip_edit_rejects_invalid_range_without_writing_revision(tmp_path, monkeypatch):
    pid, plan_dir = _audio_project(tmp_path, monkeypatch)
    result = _apply(pid, "trim_audio_clip", "audio#0", {
        "start": 2.0, "sourceIn": 4.0, "sourceOut": 4.01,
    })
    assert result.status == "REJECTED"
    assert result.errors[0]["code"] == "INVALID_SOURCE_RANGE"
    assert load_composition(pid).audio[0].duration == 4.0
    revisions = [line for line in (plan_dir / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert revisions == []


def test_audio_clip_edit_rejects_stale_index_or_version(tmp_path, monkeypatch):
    pid, _plan_dir = _audio_project(tmp_path, monkeypatch)
    missing = _apply(pid, "move_audio_clip", "audio#7", {"start": 3.0})
    assert missing.status == "REJECTED"
    assert missing.errors[0]["code"] == "NOT_FOUND"
    stale = _apply(pid, "move_audio_clip", "audio#0", {"start": 3.0}, version=2)
    assert stale.status == "REJECTED"
    assert stale.errors[0]["code"] == "CONFLICT"


def test_timeline_exposes_audio_clips_as_directly_editable_items():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    js = (root / "webui" / "static" / "js" / "timeline.js").read_text(encoding="utf-8")
    css = (root / "webui" / "static" / "css" / "timeline.css").read_text(encoding="utf-8")
    assert "_tlBindAudioClipDrag" in js
    assert 'data-audio-index="${cl.audioIndex}"' in js
    assert "move_audio_clip" in js
    assert "trim_audio_clip" in js
    assert ".ed-audio-clip::before" in css
