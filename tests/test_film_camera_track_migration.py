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



def test_scene_camera_overrides_global_camera_without_double_processing(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "scene-camera-overrides-global"
    raw = _camera_project(project_id).model_dump(mode="json")
    raw["scenes"][0]["camera"] = {
        "id": "scene-camera",
        "projection": "orthographic",
        "keyframes": [
            {"at": 0, "position": [0.2, 0.4, 1], "target": [0.2, 0.4, 0]},
            {"at": 1, "position": [0.3, 0.7, 1], "target": [0.3, 0.7, 0]},
        ],
    }
    # The global camera requests a different pan. SceneIR.camera is the explicit
    # override for this scene and must be the sole input to its migrated channels.
    ir = CompositionIR.model_validate(raw)

    result = fd.build_film_from_composition(project_id, ir)
    main_track = next(item for item in result["document"]["tracks"] if item["id"] == "track-main")
    channels = {
        channel["property"]: channel["keyframes"]
        for channel in json.loads(main_track["clips"][0]["attrs"]["data-film-keyframes"])
    }
    assert channels["camera_x"] == [
        {"at": 0.0, "value": 0.2, "easing": "linear"},
        {"at": 1.0, "value": 0.3, "easing": "linear"},
    ]
    assert channels["camera_y"] == [
        {"at": 0.0, "value": 0.4, "easing": "linear"},
        {"at": 1.0, "value": 0.7, "easing": "linear"},
    ]
    warnings = "\\n".join(result["warnings"])
    assert "场景 camera-scene 同时含 scene.camera 与全局 camera_tracks" in warnings
    assert "camera_tracks camera_x 与图层显式关键帧冲突" not in warnings
    assert "camera_tracks camera_y 与图层显式关键帧冲突" not in warnings

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

def test_unsupported_camera_follow_constraints_and_easing_are_not_approximated(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))

    cases = [
        (
            "camera-follow-blocked",
            {"follow_target": "camera-layer"},
            "含 follow_target/constraints",
        ),
        (
            "camera-constraints-blocked",
            {"constraints": [{"kind": "follow", "target": "camera-layer"}]},
            "含 follow_target/constraints",
        ),
        (
            "camera-easing-blocked",
            {"keyframes": [
                {"at": 0, "position": [0.5, 0.5, 1], "target": [0.5, 0.5, 0], "easing": "ease_in"},
                {"at": 1, "position": [0.75, 0.6, 1], "target": [0.75, 0.6, 0], "easing": "linear"},
            ]},
            "使用 ease_in 缓动",
        ),
    ]
    for project_id, override, expected_warning in cases:
        ir = _camera_project(project_id).model_dump(mode="json")
        ir["camera_tracks"][0].update(override)
        ir = CompositionIR.model_validate(ir)
        result = fd.build_film_from_composition(project_id, ir)
        warnings = "\\n".join(result["warnings"])
        assert expected_warning in warnings
        track = next(item for item in result["document"]["tracks"] if item["id"] == "track-main")
        channels = json.loads(track["clips"][0]["attrs"].get("data-film-keyframes", "[]"))
        assert not any(item["property"] in {"camera_x", "camera_y"} for item in channels)
