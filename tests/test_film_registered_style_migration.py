"""Registered legacy styles must affect the authoritative FilmDocument export."""
from __future__ import annotations

import json

from video_director import config, film_document as fd
from video_director.models import CompositionIR


def test_registered_style_id_drives_subtitle_and_zoom_motion(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "registered-style-migration"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")

    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "style_id": "style.knowledge-explainer",
        "scenes": [{
            "id": "opening", "start": 0, "duration": 1, "text": "style",
            "layers": [{
                "type": "image", "asset_id": "asset-image", "start": 0, "duration": 1,
                "motion": "slow_zoom_in",
            }],
        }],
        "subtitle_track": {"cues": [{"start": 0, "end": 1, "text": "style maps here"}]},
    })

    result = fd.build_film_from_composition(project_id, ir)
    track = next(item for item in result["document"]["tracks"] if item["id"] == "track-main")
    channels = json.loads(track["clips"][0]["attrs"]["data-film-keyframes"])
    zoom = next(item for item in channels if item["property"] == "scale")
    assert zoom["keyframes"][0]["value"] == 1
    assert zoom["keyframes"][1]["value"] == 1.12
    subtitle_page = result["pages"]["scenes/subtitles.html"]
    assert "font-size:60px" in subtitle_page
    assert "bottom:18px;top:auto;" in subtitle_page
    warnings = "\n".join(result["warnings"])
    assert "已执行注册风格 knowledge-explainer" in warnings
    assert "transition_budget、bgm_volume、sfx_density 尚未由 Film Runtime 执行" in warnings
    assert "当前不会自动应用 Style Registry" not in warnings


def test_variant_registered_style_takes_precedence_over_base_style_id(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "registered-variant-style-migration"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")

    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "style_id": "documentary-calm",
        "scenes": [{
            "id": "base", "start": 0, "duration": 1, "text": "base",
            "layers": [{"type": "image", "asset_id": "asset-image", "start": 0, "duration": 1}],
        }],
        "variants": [{
            "id": "variant", "version": 3,
            "scenes": [{
                "id": "variant-scene", "start": 0, "duration": 1, "text": "variant",
                "layers": [{"type": "image", "asset_id": "asset-image", "start": 0, "duration": 1,
                            "motion": "slow_zoom_in"}],
            }],
            "style": {"style_id": "style.knowledge-explainer", "parameters": {}},
        }],
        "active_variant_id": "variant",
        "subtitle_track": {"cues": [{"start": 0, "end": 1, "text": "variant style"}]},
    })
    result = fd.build_film_from_composition(project_id, ir)
    track = next(item for item in result["document"]["tracks"] if item["id"] == "track-main")
    channels = json.loads(track["clips"][0]["attrs"]["data-film-keyframes"])
    assert next(item for item in channels if item["property"] == "scale")["keyframes"][1]["value"] == 1.12
    subtitle_page = result["pages"]["scenes/subtitles.html"]
    assert "font-size:60px" in subtitle_page
    assert "font-size:52px" not in subtitle_page
    warnings = "\n".join(result["warnings"])
    assert "变体 variant 的注册风格 knowledge-explainer 已执行" in warnings
    assert "变体 variant 的 variant.style 已保存在" not in warnings


def test_registered_style_supplies_default_image_motion_when_layer_has_no_preset(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "registered-style-default-motion"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "style_id": "knowledge-explainer",
        "scenes": [{
            "id": "opening", "start": 0, "duration": 1, "text": "default motion",
            "layers": [{"type": "image", "asset_id": "asset-image", "start": 0, "duration": 1}],
        }],
    })
    result = fd.build_film_from_composition(project_id, ir)
    track = next(item for item in result["document"]["tracks"] if item["id"] == "track-main")
    channels = json.loads(track["clips"][0]["attrs"]["data-film-keyframes"])
    scale = next(item for item in channels if item["property"] == "scale")
    assert scale["keyframes"][0]["value"] == 1
    assert scale["keyframes"][1]["value"] == 1.12
    assert any("已执行注册风格 knowledge-explainer" in warning for warning in result["warnings"])
