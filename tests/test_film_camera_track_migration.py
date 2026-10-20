"""Conservative migration for orthographic 2D CameraTrack pans."""
from __future__ import annotations

import json

from video_director import config, film_document as fd
from video_director.models import CompositionIR


def _camera_project(project_id, projection="orthographic"):
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "camera-still", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")
    return CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "scenes": [{
            "id": "camera-scene", "start": 0, "duration": 1, "text": "camera",
            "layers": [{"id": "camera-layer", "type": "image", "asset_id": "camera-still",
                        "start": 0, "duration": 1}],
        }],
        "camera_tracks": [{
            "id": "camera-pan", "projection": projection,
            "keyframes": [
                {"at": 0, "position": [0.5, 0.5, 1], "target": [0.5, 0.5, 0]},
                {"at": 1, "position": [0.75, 0.6, 1], "target": [0.75, 0.6, 0]},
            ],
        }],
    })


def test_orthographic_camera_pan_maps_to_deterministic_film_keyframes(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "camera-pan-migration"
    ir = _camera_project(project_id)
    result = fd.build_film_from_composition(project_id, ir)
    track = next(item for item in result["document"]["tracks"] if item["id"] == "track-main")
    attrs = track["clips"][0]["attrs"]
    channels = {item["property"]: item["keyframes"] for item in json.loads(attrs["data-film-keyframes"])}
    assert channels["camera_x"] == [
        {"at": 0.0, "value": 0.5, "easing": "linear"},
        {"at": 1.0, "value": 0.75, "easing": "linear"},
    ]
    assert channels["camera_y"] == [
        {"at": 0.0, "value": 0.5, "easing": "linear"},
        {"at": 1.0, "value": 0.6, "easing": "linear"},
    ]
    assert not any("camera_tracks camera-pan" in warning for warning in result["warnings"])


def test_perspective_camera_is_not_falsely_reported_as_migrated(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "camera-perspective-warning"
    ir = _camera_project(project_id, projection="perspective")
    result = fd.build_film_from_composition(project_id, ir)
    warnings = "\n".join(result["warnings"])
    assert "camera_tracks camera-pan" in warnings
    assert "透视投影" in warnings
    track = next(item for item in result["document"]["tracks"] if item["id"] == "track-main")
    channels = json.loads(track["clips"][0]["attrs"].get("data-film-keyframes", "[]"))
    assert not any(item["property"] in {"camera_x", "camera_y"} for item in channels)
