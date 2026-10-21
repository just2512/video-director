"""Layer volume keyframes are converted to the deterministic media volume envelope."""
from __future__ import annotations

import json

import pytest

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
    assert envelope == [
        {"t": 0.0, "v": 0.2},
        {"t": 1.0, "v": 0.8, "easing": "ease_in_out"},
        {"t": 2.0, "v": 0.4},
    ]
    assert "data-film-keyframes" not in clip["attrs"]
    assert clip["attrs"]["data-source-id"] == "legacy-video-layer"
    assert not any("keyframes:volume" in warning for warning in result["warnings"])
    assert not any("按线性插值迁移" in warning for warning in result["warnings"])


def test_single_volume_keyframe_becomes_a_valid_constant_envelope():
    points, warnings = fd._film_volume_envelope([
        {"property": "volume", "keyframes": [
            {"at": 0.5, "value": 0.3, "easing": "linear"},
        ]},
    ], 2.0)
    assert points == [{"t": 0.0, "v": 0.3}, {"t": 2.0, "v": 0.3}]
    assert any("只有一个有效关键帧" in warning for warning in warnings)


def test_nonlinear_volume_keyframe_easing_is_preserved_as_executable_curve():
    points, warnings = fd._film_volume_envelope([
        {"property": "volume", "keyframes": [
            {"at": 0, "value": 0.2, "easing": "linear"},
            {"at": 1, "value": 0.8, "easing": "ease_in_out"},
            {"at": 2, "value": 0.4, "easing": "linear"},
        ]},
    ], 2.0)
    assert points == [
        {"t": 0.0, "v": 0.2},
        {"t": 1.0, "v": 0.8, "easing": "ease_in_out"},
        {"t": 2.0, "v": 0.4},
    ]
    assert not any("按线性插值迁移" in warning for warning in warnings)
    assert fd._volume_envelope_value(points, 0.5) == pytest.approx(0.5)
    assert fd._volume_envelope_value(points, 1.5) == pytest.approx(0.6)


def test_single_volume_keyframe_becomes_a_valid_constant_envelope():
    points, warnings = fd._film_volume_envelope([
        {"property": "volume", "keyframes": [{"at": 0.5, "value": 0.3, "easing": "linear"}]},
    ], 2.0)
    assert points == [{"t": 0.0, "v": 0.3}, {"t": 2.0, "v": 0.3}]
    assert any("只有一个有效关键帧" in warning for warning in warnings)


def test_trimming_nonlinear_volume_envelope_preserves_the_curve_subinterval():
    original = [
        {"t": 0.0, "v": 0.2},
        {"t": 1.0, "v": 0.8, "easing": "ease_in_out"},
        {"t": 2.0, "v": 0.4},
    ]
    attrs = {"volume_envelope": json.dumps(original)}
    fd._slice_volume_envelope(attrs, 0.25, 1.75)
    sliced = fd._parse_volume_envelope(attrs["volume_envelope"])
    for local_time in [0.0, 0.1, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5]:
        assert fd._volume_envelope_value(sliced, local_time) == pytest.approx(
            fd._volume_envelope_value(original, local_time + 0.25), abs=2e-6
        )


def test_ffmpeg_volume_expression_retains_nonlinear_easing_and_windowed_subcurves():
    from video_director.film_render import _volume_envelope_filter

    expression = _volume_envelope_filter(1.0, [
        {"t": 0.0, "v": 0.2},
        {"t": 1.0, "v": 0.8, "easing": "ease_in_out"},
    ])
    assert "ease_in_out" not in expression
    assert "if(lt(" in expression and "*(" in expression
    windowed = _volume_envelope_filter(1.0, [
        {"t": 0.0, "v": 0.2},
        {"t": 1.0, "v": 0.8, "easing": {"type":"window","preset":"ease_in_out","start":0.25,"end":0.75}},
    ])
    assert "0.25" in windowed and "0.75" in windowed


def test_window_easing_round_trips_through_volume_envelope_validation():
    value = [
        {"t": 0.0, "v": 0.2},
        {"t": 1.0, "v": 0.8, "easing": {"type":"window","preset":"ease_in_out","start":0.25,"end":0.75}},
    ]
    canonical = fd._normalize_volume_envelope(value)
    assert json.loads(canonical) == value


def test_runtime_volume_envelope_sampler_uses_shared_easing_contract():
    from pathlib import Path

    runtime = Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-runtime.js"
    source = runtime.read_text(encoding="utf-8")
    assert "function validVolumeEasing(easing)" in source
    assert "function volumeEaseAt(ratio,easing)" in source
    assert "easing.type==='window'" in source
    assert "filmEase(start+x*(end-start),preset)" in source
    assert "volumeEaseAt(ratio,right.easing||'linear')" in source
