"""Active variants, Transform and deterministic 2D keyframes migrate into film.html."""
from __future__ import annotations

import json

from video_director import config, film_document as fd
from video_director.models import CompositionIR


def test_active_variant_and_2d_keyframes_are_converted_not_only_warned(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "variant-transform-keyframes"
    project_root = config.project_dir(project_id)
    assets = project_root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "base.png").write_bytes(b"not-decoded-by-migration")
    (assets / "variant.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-base", "file": "assets/base.png"},
        {"id": "asset-variant", "file": "assets/variant.png"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "scenes": [{
            "id": "base-scene", "start": 0, "duration": 1, "text": "base",
            "layers": [{"type": "image", "asset_id": "asset-base", "start": 0, "duration": 1}],
        }],
        "variants": [{
            "id": "active-cut", "version": 2,
            "scenes": [{
                "id": "active-scene", "start": 0, "duration": 1, "text": "active",
                "layers": [{
                    "type": "image", "asset_id": "asset-variant", "start": 0, "duration": 1,
                    "fit_mode": "contain",
                    "transform": {"x": 0.4, "y": 0.6, "scale": 1.25, "rotation": 12,
                                  "opacity": 0.8, "anchor": "center"},
                    "keyframes": [
                        {"property": "x", "keyframes": [
                            {"at": 0, "value": 0.4, "easing": "linear"},
                            {"at": 1, "value": 0.6, "easing": "ease_in_out"},
                        ]},
                        {"property": "opacity", "keyframes": [
                            {"at": 0, "value": 0.8, "easing": "linear"},
                            {"at": 1, "value": 1.0, "easing": "ease_out"},
                        ]},
                    ],
                }],
            }],
        }],
        "active_variant_id": "active-cut",
    })
    result = fd.build_film_from_composition(project_id, ir)
    assert result["duration"] == 1
    main_track = next(track for track in result["document"]["tracks"] if track["id"] == "track-main")
    clip = main_track["clips"][0]
    assert clip["attrs"]["data-asset-id"] == "asset-variant"
    transform = json.loads(clip["attrs"]["data-film-transform"])
    assert transform["x"] == 0.4 and transform["y"] == 0.6
    assert transform["scale"] == 1.25 and transform["rotation"] == 12
    assert transform["opacity"] == 0.8 and transform["anchor"] == "center"
    assert "object-fit:contain" in clip["attrs"]["style"]
    channels = json.loads(clip["attrs"]["data-film-keyframes"])
    assert {item["property"] for item in channels} == {"x", "opacity"}
    assert any("active-cut" in warning for warning in result["warnings"])
    assert not any("keyframes:x" in warning or "keyframes:opacity" in warning for warning in result["warnings"])
