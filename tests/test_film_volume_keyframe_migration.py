"""Layer volume keyframes are converted to the deterministic media volume envelope."""
from __future__ import annotations

import json

from video_director import config, film_document as fd
from video_director.models import CompositionIR


def test_video_layer_volume_keyframes_migrate_to_runtime_envelope(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "video-layer-volume-keyframes"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "shot.mp4").write_bytes(b"migration does not decode media")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-shot", "file": "assets/shot.mp4", "type": "video"},
    ]), encoding="utf-8")

    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 2},
        "scenes": [{
            "id": "shot-scene", "start": 0, "duration": 2, "text": "原声",
            "layers": [{
                "id": "legacy-video-layer", "type": "video", "asset_id": "asset-shot", "start": 0,
                "duration": 2, "source_in": 0, "source_out": 2,
                "keyframes": [{
                    "property": "volume", "keyframes": [
                        {"at": 0, "value": 0.2, "easing": "linear"},
                        {"at": 1, "value": 0.8, "easing": "ease_in_out"},
                        {"at": 2, "value": 0.4, "easing": "linear"},
                    ],
                }],
            }],
        }],
    })
    result = fd.build_film_from_composition(project_id, ir)
    track = next(track for track in result["document"]["tracks"] if track["id"] == "track-main")
    clip = track["clips"][0]
    envelope = json.loads(clip["attrs"]["volume_envelope"])
    assert envelope == [{"t": 0.0, "v": 0.2}, {"t": 1.0, "v": 0.8}, {"t": 2.0, "v": 0.4}]
    assert "data-film-keyframes" not in clip["attrs"]
    assert clip["attrs"]["data-source-id"] == "legacy-video-layer"
    assert not any("keyframes:volume" in warning for warning in result["warnings"])
    assert any(
        "缓动 ease_in_out" in warning and "按线性插值迁移" in warning
        for warning in result["warnings"]
    )


def test_single_volume_keyframe_becomes_a_valid_constant_envelope():
    points, warnings = fd._film_volume_envelope([
        {"property": "volume", "keyframes": [
            {"at": 0.5, "value": 0.3, "easing": "linear"},
        ]},
    ], 2.0)
    assert points == [{"t": 0.0, "v": 0.3}, {"t": 2.0, "v": 0.3}]
    assert any("只有一个有效关键帧" in warning for warning in warnings)


def test_nonlinear_volume_keyframe_easing_is_reported_explicitly():
    points, warnings = fd._film_volume_envelope([
        {"property": "volume", "keyframes": [
            {"at": 0, "value": 0.2, "easing": "linear"},
            {"at": 1, "value": 0.8, "easing": "ease_in_out"},
            {"at": 2, "value": 0.4, "easing": "linear"},
        ]},
    ], 2.0)
    assert points == [{"t": 0.0, "v": 0.2}, {"t": 1.0, "v": 0.8}, {"t": 2.0, "v": 0.4}]
    assert any(
        "缓动 ease_in_out" in warning and "按线性插值迁移" in warning
        for warning in warnings
    )


def test_single_volume_keyframe_becomes_a_valid_constant_envelope():
    points, warnings = fd._film_volume_envelope([
        {"property": "volume", "keyframes": [{"at": 0.5, "value": 0.3, "easing": "linear"}]},
    ], 2.0)
    assert points == [{"t": 0.0, "v": 0.3}, {"t": 2.0, "v": 0.3}]
    assert any("只有一个有效关键帧" in warning for warning in warnings)


def test_nonlinear_volume_keyframe_easing_is_reported_not_silently_claimed_as_exact():
    points, warnings = fd._film_volume_envelope([
        {"property": "volume", "keyframes": [
            {"at": 0.0, "value": 0.2, "easing": "linear"},
            {"at": 1.0, "value": 0.8, "easing": "ease_in_out"},
            {"at": 2.0, "value": 0.4, "easing": "linear"},
        ]},
    ], 2.0)
    assert points == [{"t": 0.0, "v": 0.2}, {"t": 1.0, "v": 0.8}, {"t": 2.0, "v": 0.4}]
    assert any("缓动 ease_in_out" in warning and "按线性插值迁移" in warning for warning in warnings)
