"""Migrated clip locks preserve layer order while all edit operations reject the locked clip."""
from __future__ import annotations

import json

import pytest

from video_director import config
from video_director import film_document as fd
from video_director.models import CompositionIR


def _migrated_locked_document(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "clip-lock-semantics"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "bg.png").write_bytes(b"image")
    (assets / "fg.png").write_bytes(b"image")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-bg", "file": "assets/bg.png", "type": "image"},
        {"id": "asset-fg", "file": "assets/fg.png", "type": "image"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 2},
        "assets": [
            {"id": "asset-bg", "file": "assets/bg.png", "type": "image"},
            {"id": "asset-fg", "file": "assets/fg.png", "type": "image"},
        ],
        "scenes": [{
            "id": "opening", "start": 0, "duration": 2, "text": "opening",
            "layers": [
                {"id": "background", "type": "image", "asset_id": "asset-bg",
                 "start": 0, "duration": 2},
                {"id": "foreground-lock", "type": "image", "asset_id": "asset-fg",
                 "start": 0, "duration": 2, "locked": True},
            ],
        }],
    })
    return project_id, fd.build_film_from_composition(project_id, ir)


def test_locked_layer_keeps_render_stack_order_and_physical_track_boundaries(tmp_path, monkeypatch):
    project_id, result = _migrated_locked_document(tmp_path, monkeypatch)
    tracks = result["document"]["tracks"]
    main = next(track for track in tracks if track["id"] == "track-main")
    locked_track = next(track for track in tracks if track["id"].startswith("track-layer-main-opening-2"))
    locked_clip = locked_track["clips"][0]
    assert "locked" not in locked_track["attrs"]
    assert "data-clip-locked" in locked_clip["attrs"]
    assert locked_track["attrs"]["data-render-z-index"] == "1"
    assert main["clips"][0]["attrs"]["data-asset-id"] == "asset-bg"
    assert locked_clip["attrs"]["data-asset-id"] == "asset-fg"
    assert tracks.index(locked_track) > tracks.index(main)
    # z-index, not incidental insertion order, preserves the foreground layer.
    assert int(locked_track["attrs"]["data-render-z-index"]) > int(main["attrs"]["data-render-z-index"])


@pytest.mark.parametrize("operation", [
    {"op": "set", "clip": "main-opening-2", "field": "at", "value": 0.5},
    {"op": "move", "clip": "main-opening-2", "at": 0.5},
    {"op": "remove", "clip": "main-opening-2"},
    {"op": "split", "clip": "main-opening-2", "at": 1},
    {"op": "trim", "clip": "main-opening-2", "edge": "start", "at": 0.5},
    {"op": "slip", "clip": "main-opening-2", "delta_source": 0.1},
])
def test_clip_level_lock_rejects_direct_timeline_mutations(tmp_path, monkeypatch, operation):
    _, result = _migrated_locked_document(tmp_path, monkeypatch)
    with pytest.raises(fd.FilmError, match="片段 main-opening-2 已锁定"):
        fd.apply_ops(result["document"], [operation])


def test_ripple_delete_refuses_to_shift_a_locked_clip_and_keeps_input_unchanged():
    document = {
        "stage": {"width": 320, "height": 180},
        "head_html": "<head></head>",
        "tracks": [{
            "id": "track-main", "attrs": {"id": "track-main", "data-label": "Main"},
            "clips": [
                {"id": "target", "tag": "video",
                 "attrs": {"id": "target", "src": "/media/a.mp4#t=0,1", "at": "0"}},
                {"id": "locked-later", "tag": "img",
                 "attrs": {"id": "locked-later", "src": "/media/a.png#t=0,1", "at": "2",
                           "data-clip-locked": "", "data-asset-id": "asset-still"}},
            ],
        }],
    }
    before = json.dumps(document, sort_keys=True)
    with pytest.raises(fd.FilmError, match="CLIP_LOCKED"):
        fd.apply_ops(document, [{"op": "ripple_delete", "clip": "target", "scope": "all"}])
    assert json.dumps(document, sort_keys=True) == before



def test_clip_lock_can_be_explicitly_removed_then_normal_edits_resume(tmp_path, monkeypatch):
    _, result = _migrated_locked_document(tmp_path, monkeypatch)
    unlocked, normalized = fd.apply_ops(result["document"], [
        {"op": "clip_lock", "clip": "main-opening-2", "locked": False},
        {"op": "set", "clip": "main-opening-2", "field": "at", "value": 0.5},
    ])
    clip = next(clip for track in unlocked["tracks"] for clip in track["clips"]
                if clip["id"] == "main-opening-2")
    assert "data-clip-locked" not in clip["attrs"]
    assert clip["attrs"]["at"] == "0.5"
    assert normalized[0] == {"op": "clip_lock", "clip": "main-opening-2", "locked": False}
    round_trip = fd.parse_film(fd.serialize_film(unlocked))
    round_trip_clip = next(item for track in round_trip["tracks"] for item in track["clips"]
                           if item["id"] == "main-opening-2")
    assert "data-clip-locked" not in round_trip_clip["attrs"]


def test_clip_lock_requires_explicit_boolean_and_does_not_bypass_track_lock():
    document = {
        "stage": {"width": 320, "height": 180},
        "head_html": "<head></head>",
        "tracks": [{
            "id": "track-main", "attrs": {"id": "track-main"},
            "clips": [{"id": "clip-a", "tag": "img",
                       "attrs": {"id": "clip-a", "src": "/media/a.png#t=0,1", "at": "0",
                                 "data-clip-locked": ""}}],
        }],
    }
    with pytest.raises(fd.FilmError, match="必须是布尔值"):
        fd.apply_ops(document, [{"op": "clip_lock", "clip": "clip-a", "locked": "false"}])

    document["tracks"][0]["attrs"]["locked"] = ""
    with pytest.raises(fd.FilmError, match="轨道 track-main 已锁定"):
        fd.apply_ops(document, [{"op": "clip_lock", "clip": "clip-a", "locked": False}])
