"""Visual track identity/order/lock metadata stays explicit during overlay migration."""
from __future__ import annotations

import json

from video_director import config, film_document as fd
from video_director.models import CompositionIR


def test_visual_source_track_order_identity_and_lock_are_preserved(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "visual-track-metadata"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "overlay.mp4").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-overlay", "file": "assets/overlay.mp4", "type": "video"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 2},
        "scenes": [{
            "id": "base", "start": 0, "duration": 2, "text": "base",
            "layers": [{"type": "image", "asset_id": "asset-overlay", "start": 0, "duration": 2}],
        }],
        "visual_tracks": [
            {"id": "top", "name": "Top video", "order": 0, "locked": True},
            {"id": "lower", "name": "Lower video", "order": 1, "hidden": True},
        ],
        "visual_overlays": [
            {"id": "later", "asset_id": "asset-overlay", "track_id": "lower", "start": 1, "duration": 1, "z_index": 4},
            {"id": "earlier", "asset_id": "asset-overlay", "track_id": "top", "start": 0, "duration": 1, "z_index": 2},
        ],
    })
    result = fd.build_film_from_composition(project_id, ir)
    overlay_tracks = [track for track in result["document"]["tracks"] if track["id"].startswith("track-overlay-")]
    assert [track["clips"][0]["id"] for track in overlay_tracks] == ["overlay-earlier", "overlay-later"]
    first = overlay_tracks[0]
    second = overlay_tracks[1]
    assert first["attrs"]["data-label"] == "Top video"
    assert first["attrs"]["locked"] == ""
    assert first["clips"][0]["attrs"]["data-source-track-id"] == "top"
    assert first["clips"][0]["attrs"]["style"].endswith("z-index:2")
    assert second["attrs"]["data-label"] == "Lower video"
    assert second["attrs"]["data-source-track-hidden"] == "true"
    assert "hidden" not in second["attrs"]  # source hidden means timeline folded, not render-suppressed
    assert second["clips"][0]["attrs"]["data-source-track-order"] == "1"
    assert any("时间线折叠而不是画面隐藏" in warning for warning in result["warnings"])
    assert not any("组联动编辑尚未迁移" in warning for warning in result["warnings"])


def test_source_visual_grouping_warning_depends_on_actual_same_lane_membership(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "visual-track-group-warning"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "overlay.mp4").write_bytes(b"migration does not decode media")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-overlay", "file": "assets/overlay.mp4", "type": "video"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 2},
        "scenes": [{
            "id": "base", "start": 0, "duration": 2, "text": "base",
            "layers": [{"type": "image", "asset_id": "asset-overlay", "start": 0, "duration": 2}],
        }],
        "visual_tracks": [{"id": "top", "name": "Top video", "order": 0}],
        "visual_overlays": [
            {"id": "one", "asset_id": "asset-overlay", "track_id": "top", "start": 0, "duration": 1},
            {"id": "two", "asset_id": "asset-overlay", "track_id": "top", "start": 1, "duration": 1},
        ],
    })
    result = fd.build_film_from_composition(project_id, ir)
    overlay_tracks = [
        track for track in result["document"]["tracks"]
        if track["id"] in {"track-overlay-one", "track-overlay-two"}
    ]
    assert len(overlay_tracks) == 2
    assert all(track["attrs"]["data-source-track-id"] == "top" for track in overlay_tracks)
    assert any("组联动编辑尚未迁移" in warning for warning in result["warnings"])
