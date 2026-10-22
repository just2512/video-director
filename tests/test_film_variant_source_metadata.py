"""Variant style and metadata are archived on the final FilmDocument track."""
from __future__ import annotations

import json

from video_director import config, film_document as fd
from video_director.models import CompositionIR


def test_active_variant_style_and_metadata_are_preserved_in_film_track_attrs(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "variant-source-metadata"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-still", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "scenes": [{
            "id": "base-scene", "start": 0, "duration": 1, "text": "base",
            "layers": [{"id": "base-layer", "type": "image", "asset_id": "asset-still", "start": 0, "duration": 1}],
        }],
        "variants": [{
            "id": "active-look", "version": 4,
            "scenes": [{
                "id": "active-scene", "start": 0, "duration": 1, "text": "active",
                "layers": [{"id": "active-layer", "type": "image", "asset_id": "asset-still", "start": 0, "duration": 1}],
            }],
            "style": {"style_id": "brand-editorial", "parameters": {"accent": "#d02030"}},
            "metadata": {"reviewed_by": "test", "candidate_notes": ["主画面应更克制"]},
        }],
        "active_variant_id": "active-look",
    })
    result = fd.build_film_from_composition(project_id, ir)
    main = next(track for track in result["document"]["tracks"] if track["id"] == "track-main")
    attrs = main["attrs"]
    assert json.loads(attrs["data-source-variant-style"])["style_id"] == "brand-editorial"
    assert json.loads(attrs["data-source-variant-metadata"])["reviewed_by"] == "test"
    assert any("尚未编译为实际 CSS" in warning for warning in result["warnings"])
    assert any("未被当作渲染指令执行" in warning for warning in result["warnings"])
