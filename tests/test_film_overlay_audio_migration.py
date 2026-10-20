"""Overlay source-audio control is carried into Film Runtime's actual audio-mix gate."""
from __future__ import annotations

import json

from video_director import config, film_document as fd
from video_director.models import CompositionIR


def test_video_overlay_source_audio_disabled_is_muted_and_enabled_preserves_volume(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "overlay-audio-control"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "intro.mp4").write_bytes(b"video source not decoded during migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-video", "file": "assets/intro.mp4", "type": "video"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 2},
        "assets": [{"id": "asset-video", "type": "video", "file": "assets/intro.mp4"}],
        "scenes": [{
            "id": "base", "start": 0, "duration": 2, "text": "base",
            "layers": [{"type": "video", "asset_id": "asset-video", "start": 0, "duration": 2, "source_in": 0, "source_out": 2}],
        }],
        "visual_overlays": [
            {"id": "silent", "asset_id": "asset-video", "start": 0, "duration": 1,
             "source_audio_enabled": False, "source_audio_volume": 1},
            {"id": "audible", "asset_id": "asset-video", "start": 1, "duration": 1,
             "source_audio_enabled": True, "source_audio_volume": 0.35},
        ],
    })
    result = fd.build_film_from_composition(project_id, ir)
    silent = next(t["clips"][0] for t in result["document"]["tracks"] if t["id"] == "track-overlay-silent")
    audible = next(t["clips"][0] for t in result["document"]["tracks"] if t["id"] == "track-overlay-audible")
    assert "muted" in silent["attrs"]
    assert "muted" not in audible["attrs"]
    assert audible["attrs"]["volume"] == "0.35"
    assert not any("source_audio_enabled" in warning for warning in result["warnings"])
