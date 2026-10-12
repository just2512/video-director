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


def test_split_audio_clip_preserves_source_continuity_fades_and_revision(tmp_path, monkeypatch):
    pid, plan_dir = _audio_project(tmp_path, monkeypatch)
    comp = load_composition(pid)
    comp.audio[1] = comp.audio[1].model_copy(update={
        "source_in": 1.0, "source_out": 3.0, "fadeIn": 0.8, "fadeOut": 0.7,
    })
    save_composition(pid, comp)

    result = _apply(pid, "split_audio_clip", "audio#1", {"at": 6.0})
    assert result.status == "APPLIED", result.errors
    assert result.composition_version == 2

    comp = load_composition(pid)
    left, right = comp.audio[1], comp.audio[2]
    assert left.start == 5.0 and left.duration == 1.0
    assert left.source_in == 1.0 and left.source_out == 2.0
    assert left.fadeIn == 0.8 and left.fadeOut == 0.0
    assert right.start == 6.0 and right.duration == 1.0
    assert right.source_in == 2.0 and right.source_out == 3.0
    assert right.fadeIn == 0.0 and right.fadeOut == 0.7
    assert right.type == left.type and right.asset_id == left.asset_id
    revisions = [line for line in (plan_dir / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(revisions) == 1
    assert '"command": "split_audio_clip"' in revisions[0]


def test_split_audio_clip_keeps_nonlooping_source_window_when_source_out_is_implicit(tmp_path, monkeypatch):
    pid, _ = _audio_project(tmp_path, monkeypatch)
    result = _apply(pid, "split_audio_clip", "audio#1", {"at": 6.25})
    assert result.status == "APPLIED", result.errors
    left, right = load_composition(pid).audio[1:]
    assert (left.start, left.duration, left.source_in, left.source_out) == (5.0, 1.25, 0.0, None)
    assert (right.start, right.duration, right.source_in, right.source_out) == (6.25, 0.75, 1.25, None)


def test_split_looping_bgm_without_explicit_source_window_is_rejected_without_revision(tmp_path, monkeypatch):
    pid, plan_dir = _audio_project(tmp_path, monkeypatch)
    result = _apply(pid, "split_audio_clip", "audio#0", {"at": 3.0})
    assert result.status == "REJECTED"
    assert result.errors[0]["code"] == "SPLIT_LOOPING_AUDIO_REQUIRES_SOURCE_WINDOW"
    comp = load_composition(pid)
    assert len(comp.audio) == 2
    assert comp.audio[0].start == 1.0 and comp.audio[0].duration == 4.0
    assert (plan_dir / "revisions.jsonl").read_text(encoding="utf-8") == ""


def test_split_narration_track_is_rejected_until_renderer_supports_segmented_narration(tmp_path, monkeypatch):
    pid, plan_dir = _audio_project(tmp_path, monkeypatch)
    comp = load_composition(pid)
    comp.audio[0] = comp.audio[0].model_copy(update={"type": "narration"})
    save_composition(pid, comp)

    result = _apply(pid, "split_audio_clip", "audio#0", {"at": 3.0})
    assert result.status == "REJECTED"
    assert result.errors[0]["code"] == "SPLIT_UNSUPPORTED_NARRATION_PIPELINE"
    assert len(load_composition(pid).audio) == 2
    assert (plan_dir / "revisions.jsonl").read_text(encoding="utf-8") == ""


def test_split_audio_clip_rejects_playhead_outside_interior_and_stale_version(tmp_path, monkeypatch):
    pid, plan_dir = _audio_project(tmp_path, monkeypatch)
    too_early = _apply(pid, "split_audio_clip", "audio#1", {"at": 5.05})
    assert too_early.status == "REJECTED"
    assert too_early.errors[0]["code"] == "SPLIT_OUTSIDE_CLIP"
    stale = _apply(pid, "split_audio_clip", "audio#1", {"at": 6.0}, version=2)
    assert stale.status == "REJECTED"
    assert stale.errors[0]["code"] == "CONFLICT"
    assert len(load_composition(pid).audio) == 2
    assert (plan_dir / "revisions.jsonl").read_text(encoding="utf-8") == ""


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



def test_set_audio_clip_properties_updates_only_the_selected_instance(tmp_path, monkeypatch):
    pid, _plan_dir = _audio_project(tmp_path, monkeypatch)
    result = _apply(pid, "set_audio_clip_properties", "audio#1", {
        "volume": 0.25, "muted": True, "fadeIn": 0.2, "fadeOut": 0.4,
    })
    assert result.status == "APPLIED", result.errors
    comp = load_composition(pid)
    assert comp.audio[0].volume == 0.7 and comp.audio[0].muted is False
    assert comp.audio[1].volume == 0.25 and comp.audio[1].muted is True
    assert comp.audio[1].fadeIn == 0.2 and comp.audio[1].fadeOut == 0.4


def test_set_audio_clip_properties_rejects_nonfinite_or_out_of_range_values(tmp_path, monkeypatch):
    pid, plan_dir = _audio_project(tmp_path, monkeypatch)
    result = _apply(pid, "set_audio_clip_properties", "audio#1", {"volume": 2.1})
    assert result.status == "REJECTED"
    assert result.errors[0]["code"] == "INVALID_PARAMETER"
    assert load_composition(pid).audio[1].volume == 0.9
    revisions = [line for line in (plan_dir / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert revisions == []


def test_audio_clip_inspector_is_connected_to_per_instance_command():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    js = (root / "webui" / "static" / "js" / "timeline.js").read_text(encoding="utf-8")
    assert "editorAudioClipOpen" in js
    assert "set_audio_clip_properties" in js
    assert "audio-clip-muted" in js and "audio-clip-fade-in" in js



def _fake_sfx(tmp_path, asset_id="sfx-whoosh", asset_type="sfx", duration=0.8):
    from video_director.models import AssetRef

    audio_file = tmp_path / (asset_id + ".wav")
    audio_file.write_bytes(b"test audio fixture")
    return AssetRef(
        id=asset_id, type=asset_type, file=str(audio_file), duration=duration,
        keywords=["whoosh", "transition"], source="uploaded",
        license_note="test fixture",
    )


def test_add_audio_clip_resolves_library_asset_and_persists_editable_sfx(tmp_path, monkeypatch):
    from video_director import audio as vd_audio

    pid, plan_dir = _audio_project(tmp_path, monkeypatch)
    sfx = _fake_sfx(tmp_path)
    monkeypatch.setattr(vd_audio, "scan_library", lambda: {sfx.id: sfx})
    result = _apply(pid, "add_audio_clip", "sfx", {
        "assetId": sfx.id, "start": 2.5, "duration": 0.5,
        "volume": 0.35, "fadeIn": 0.1, "fadeOut": 0.15,
    })
    assert result.status == "APPLIED", result.errors
    comp = load_composition(pid)
    clip = comp.audio[-1]
    assert clip.type.value == "sfx"
    assert clip.asset_id == sfx.id
    assert clip.start == 2.5 and clip.duration == 0.5
    assert clip.source_in == 0.0 and clip.source_out == 0.5
    assert clip.volume == 0.35 and clip.fadeIn == 0.1 and clip.fadeOut == 0.15
    audio_ref = next(asset for asset in comp.assets if asset.id == sfx.id)
    assert audio_ref.type == "audio"
    assert audio_ref.file == "audio-library://sfx-whoosh"
    assert str(tmp_path) not in audio_ref.file

    # The frontend receives a name and an audio-preview URL, never the disk path.
    from types import SimpleNamespace
    from webui.server import _editor_asset_preview_map
    preview_map = _editor_asset_preview_map(SimpleNamespace(scenes=[], audio=comp.audio))
    assert preview_map[sfx.id]["file_url"] == "/api/audio_preview?asset_id=sfx-whoosh"
    assert preview_map[sfx.id]["name"]
    assert str(tmp_path) not in repr(preview_map)

    revisions = [line for line in (plan_dir / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(revisions) == 1


def test_add_audio_clip_rejects_path_injection_unknown_asset_and_wrong_type(tmp_path, monkeypatch):
    from video_director import audio as vd_audio

    pid, plan_dir = _audio_project(tmp_path, monkeypatch)
    sfx = _fake_sfx(tmp_path)
    bgm = _fake_sfx(tmp_path, "bgm-not-sfx", asset_type="bgm")
    monkeypatch.setattr(vd_audio, "scan_library", lambda: {sfx.id: sfx, bgm.id: bgm})

    unknown = _apply(pid, "add_audio_clip", "sfx", {
        "assetId": str(sfx.file), "start": 1.0, "duration": 0.2,
    })
    wrong_type = _apply(pid, "add_audio_clip", "sfx", {
        "assetId": bgm.id, "start": 1.0, "duration": 0.2,
    })
    assert unknown.status == "REJECTED"
    assert unknown.errors[0]["code"] == "ASSET_NOT_FOUND"
    assert wrong_type.status == "REJECTED"
    assert wrong_type.errors[0]["code"] == "ASSET_TYPE_MISMATCH"
    assert len(load_composition(pid).audio) == 2
    revisions = [line for line in (plan_dir / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert revisions == []


def test_add_audio_clip_rejects_invalid_timing_volume_and_stale_version(tmp_path, monkeypatch):
    from video_director import audio as vd_audio

    pid, plan_dir = _audio_project(tmp_path, monkeypatch)
    sfx = _fake_sfx(tmp_path)
    monkeypatch.setattr(vd_audio, "scan_library", lambda: {sfx.id: sfx})

    invalid = _apply(pid, "add_audio_clip", "sfx", {
        "assetId": sfx.id, "start": 9.8, "duration": 0.5, "volume": 0.3,
    })
    assert invalid.status == "REJECTED"
    assert invalid.errors[0]["code"] == "INVALID_PARAMETER"
    bad_volume = _apply(pid, "add_audio_clip", "sfx", {
        "assetId": sfx.id, "start": 2.0, "duration": 0.5, "volume": 3.0,
    })
    assert bad_volume.status == "REJECTED"
    assert bad_volume.errors[0]["code"] == "INVALID_PARAMETER"
    stale = _apply(pid, "add_audio_clip", "sfx", {
        "assetId": sfx.id, "start": 2.0, "duration": 0.5, "volume": 0.3,
    }, version=2)
    assert stale.status == "REJECTED"
    assert stale.errors[0]["code"] == "CONFLICT"
    assert len(load_composition(pid).audio) == 2
    revisions = [line for line in (plan_dir / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert revisions == []

def test_audio_clip_inspector_exposes_safe_split_at_playhead_action():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    js = (root / "webui" / "static" / "js" / "timeline.js").read_text(encoding="utf-8")
    css = (root / "webui" / "static" / "css" / "timeline.css").read_text(encoding="utf-8")
    assert "editorAudioClipSplit" in js
    assert 'id="audio-clip-split-at-playhead"' in js
    assert "split_audio_clip" in js
    assert "循环背景音需先裁出明确源区间" in js
    assert "旁白由独立渲染链路处理，当前版本暂不能切分" in js
    assert ".audio-clip-split-row" in css
