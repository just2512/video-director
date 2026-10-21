"""Referenced AssetRef provenance and project-level metadata survive migration."""
from __future__ import annotations

import json

from video_director import config, film_document as fd
from video_director.models import CompositionIR


def test_used_asset_provenance_and_project_metadata_are_written_to_film_attrs(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "asset-provenance-migration"
    project_root = config.project_dir(project_id)
    assets = project_root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "logo.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-logo", "file": "assets/logo.png", "type": "image"},
    ]), encoding="utf-8")

    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "style_id": "brand-editorial",
        "style_contract": {
            "style_id": "brand-editorial",
            "palette": {"accent": "#d02030", "background": "#f5f0e7"},
            "typography": {"font": "Brand Sans", "weight": "700"},
            "motion_temper": "calm",
            "safe_zone": 0.9,
        },
        "origin_template_id": "product-launch",
        "origin_template_version": "3.2.1",
        "references": {"references": [{
            "id": "ref-logo", "ref_type": "visual_reference", "target_asset_id": "asset-logo",
            "role": "brand identity", "note": "brand mark reference",
        }]},
        "assets": [{
            "id": "asset-logo", "type": "image", "file": "assets/logo.png",
            "prompt": "Minimal red brand mark on warm paper",
            "description": "Official launch brand logo",
            "keywords": ["brand", "logo"],
            "source": "stock",
            "license_note": "Use permitted in campaign media",
            "provenance": {"provider": "test-library", "source_url": "https://example.invalid/logo"},
            "rights_status": "CLEAR",
        }],
        "scenes": [{
            "id": "scene-logo", "start": 0, "duration": 1, "text": "launch",
            "layers": [{
                "id": "logo-layer", "type": "image", "asset_id": "asset-logo",
                "start": 0, "duration": 1,
            }],
        }],
    })

    result = fd.build_film_from_composition(project_id, ir)
    main_track = next(track for track in result["document"]["tracks"] if track["id"] == "track-main")
    clip_attrs = main_track["clips"][0]["attrs"]
    asset_metadata = json.loads(clip_attrs["data-source-asset-metadata"])
    assert asset_metadata["prompt"] == "Minimal red brand mark on warm paper"
    assert asset_metadata["license_note"] == "Use permitted in campaign media"
    assert asset_metadata["rights_status"] == "CLEAR"
    assert asset_metadata["provenance"]["provider"] == "test-library"

    track_attrs = main_track["attrs"]
    assert track_attrs["data-source-style-id"] == "brand-editorial"
    assert json.loads(track_attrs["data-source-style-contract"])["palette"]["accent"] == "#d02030"
    assert track_attrs["data-source-origin-template-id"] == "product-launch"
    assert track_attrs["data-source-origin-template-version"] == "3.2.1"
    assert json.loads(track_attrs["data-source-reference-graph"])["references"][0]["id"] == "ref-logo"
    assert not any("不会保存这些资产目录元数据" in warning for warning in result["warnings"])
    assert not any("lineage 未写入 film.html" in warning for warning in result["warnings"])
    assert any("不会自动应用 Style Registry" in warning for warning in result["warnings"])
