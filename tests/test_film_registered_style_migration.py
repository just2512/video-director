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
    (assets / "music.wav").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "file": "assets/still.png", "type": "image"},
        {"id": "asset-music", "file": "assets/music.wav", "type": "audio"},
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
    assert "-webkit-line-clamp:2" in subtitle_page
    assert "bottom:18px;top:auto;" in subtitle_page
    warnings = "\n".join(result["warnings"])
    assert "已执行注册风格 knowledge-explainer" in warnings
    assert "bgm_volume 仅作为未显式设置音量的 BGM 片段默认值" in warnings
    assert "transition_budget、sfx_density 尚未由 Film Runtime 执行" in warnings
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


def test_style_contract_safe_zone_is_compiled_into_subtitle_safe_margins(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "style-contract-safe-zone"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "style_contract": {"style_id": "safe-zone-test", "safe_zone": 0.9},
        "scenes": [{
            "id": "opening", "start": 0, "duration": 1, "text": "safe zone",
            "layers": [{"type": "image", "asset_id": "asset-image", "start": 0, "duration": 1}],
        }],
        "subtitle_track": {"cues": [{"start": 0, "end": 1, "text": "stay in safe area"}]},
    })
    result = fd.build_film_from_composition(project_id, ir)
    subtitle_html = result["pages"]["scenes/subtitles.html"]
    assert "bottom:9px;top:auto;" in subtitle_html
    warnings = "\n".join(result["warnings"])
    assert "已应用 motion_temper, safe_zone subtitle margins" in warnings
    assert "safe_zone 及未识别" not in warnings


def test_unregistered_variant_style_applies_only_whitelisted_parameters(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "custom-variant-style-safe-parameters"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "scenes": [{
            "id": "base", "start": 0, "duration": 1, "text": "base",
            "layers": [{"type": "image", "asset_id": "asset-image", "start": 0, "duration": 1}],
        }],
        "variants": [{
            "id": "brand-look", "version": 1,
            "scenes": [{
                "id": "brand-scene", "start": 0, "duration": 1, "text": "brand",
                "layers": [{"type": "image", "asset_id": "asset-image", "start": 0, "duration": 1}],
            }],
            "style": {
                "style_id": "brand-local-unregistered",
                "parameters": {
                    "subtitle_position": "top",
                    "subtitle_font_size": 48,
                    "subtitle_max_lines": 1,
                    "image_motion_intensity": 1.15,
                    "bgm_volume": 0.25,
                    "palette_tokens": {"accent": "#d02030"},
                },
            },
        }],
        "active_variant_id": "brand-look",
        "audio": [{"type": "bgm", "id": "custom-style-bgm", "asset_id": "asset-music", "start": 0, "duration": 1}],
        "subtitle_track": {"cues": [{"start": 0, "end": 1, "text": "custom safe mapping"}]},
    })
    result = fd.build_film_from_composition(project_id, ir)
    track = next(item for item in result["document"]["tracks"] if item["id"] == "track-main")
    channels = json.loads(track["clips"][0]["attrs"]["data-film-keyframes"])
    scale = next(item for item in channels if item["property"] == "scale")
    assert scale["keyframes"][1]["value"] == 1.15
    subtitle_page = result["pages"]["scenes/subtitles.html"]
    assert "font-size:48px" in subtitle_page
    assert "-webkit-line-clamp:1" in subtitle_page
    assert "top:18px;bottom:auto;" in subtitle_page
    bgm_track = next(
        item for item in result["document"]["tracks"]
        if item.get("attrs", {}).get("data-label") == "bgm"
        or any(clip["attrs"].get("data-role") == "bgm" for clip in item["clips"])
    )
    assert bgm_track["clips"][0]["attrs"]["volume"] == "0.25"
    warnings = "\n".join(result["warnings"])
    assert "已应用受支持参数：bgm_volume, image_motion_intensity, subtitle_font_size, subtitle_max_lines, subtitle_position" in warnings
    assert "palette_tokens" in warnings


def test_registered_style_bgm_volume_is_default_only_when_volume_was_not_explicit(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "registered-style-bgm-volume"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "music.wav").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "file": "assets/still.png", "type": "image"},
        {"id": "asset-music", "file": "assets/music.wav", "type": "audio"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 2},
        "style_id": "knowledge-explainer",
        "scenes": [{
            "id": "opening", "start": 0, "duration": 2, "text": "bgm volume",
            "layers": [{"type": "image", "asset_id": "asset-image", "start": 0, "duration": 2}],
        }],
        "audio": [
            {"type": "bgm", "id": "implicit-volume", "asset_id": "asset-music", "start": 0, "duration": 1},
            {"type": "bgm", "id": "explicit-volume", "asset_id": "asset-music", "start": 1, "duration": 1, "volume": 0.7},
        ],
    })
    result = fd.build_film_from_composition(project_id, ir)
    audio_by_id = {
        clip["attrs"].get("data-source-id"): clip
        for track in result["document"]["tracks"] for clip in track["clips"]
        if clip["tag"] == "audio"
    }
    assert audio_by_id["implicit-volume"]["attrs"]["volume"] == "0.16"
    assert audio_by_id["explicit-volume"]["attrs"]["volume"] == "0.7"


def test_subtitle_renderer_rejects_fractional_or_boolean_line_limits():
    import pytest
    with pytest.raises(fd.FilmError, match="必须是整数"):
        fd._subtitle_page(320, 180, 1, [{"start": 0, "end": 1, "text": "line"}], {"max_lines": 1.5})
    with pytest.raises(fd.FilmError, match="有限数字"):
        fd._subtitle_page(320, 180, 1, [{"start": 0, "end": 1, "text": "line"}], {"max_lines": False})
