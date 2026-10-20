"""Composition watermarks become visible HTML overlay tracks with preserved placement/time fields."""
from __future__ import annotations

import json
from video_director import config, film_document as fd
from video_director.models import CompositionIR


def test_text_and_image_watermarks_migrate_to_overlay_tracks(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "watermark-migration"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "logo.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-logo", "file": "assets/logo.png", "type": "image"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 2},
        "scenes": [{
            "id": "opening", "start": 0, "duration": 2, "text": "opening",
            "layers": [{"type": "image", "asset_id": "asset-logo", "start": 0, "duration": 2}],
        }],
        "watermarks": [
            {"id": "text-mark", "kind": "text", "text": "© Example",
             "anchor": "bottom_right", "offset_x": 10, "offset_y": 4,
             "safe_area": 12, "opacity": 0.6, "scale": 1.25, "start": 0.25, "duration": 1},
            {"id": "brand-logo", "kind": "image", "asset_id": "asset-logo",
             "anchor": "top_left", "offset_x": -2, "offset_y": 3,
             "safe_area": 10, "opacity": 0.5, "scale": 0.75, "start": 0, "duration": 2},
        ],
    })
    result = fd.build_film_from_composition(project_id, ir)
    text_clip = next(t["clips"][0] for t in result["document"]["tracks"]
                     if t["id"] == "track-watermark-text-mark-1")
    image_clip = next(t["clips"][0] for t in result["document"]["tracks"]
                      if t["id"] == "track-watermark-brand-logo-2")
    text_page = result["pages"]["scenes/watermark-text-mark-1.html"]
    image_page = result["pages"]["scenes/watermark-brand-logo-2.html"]
    assert text_clip["tag"] == "iframe"
    assert text_clip["attrs"]["at"] == "0.25"
    assert text_clip["attrs"]["src"].endswith("#t=0,1")
    assert "© Example" in text_page and "font-size:30px" in text_page
    assert "right:2px" in text_page and "bottom:8px" in text_page
    assert "opacity:0.6" in text_page and "24px 基准字号" in "\n".join(result["warnings"])
    assert image_clip["attrs"]["at"] == "0"
    assert "opacity:0.5" in image_page and "scale(0.75)" in image_page
    assert "/api/editor/film/media?project_id=watermark-migration&amp;src=assets%2Flogo.png" in image_page
    assert all("z-index:100000" in t["attrs"]["style"] for t in result["document"]["tracks"]
               if t["id"].startswith("track-watermark-"))
