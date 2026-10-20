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
            "id": "active-cut", "version": 2, "metadata": {"author": "legacy-agent"},
            "scenes": [{
                "id": "active-scene", "start": 0, "duration": 1, "text": "active",
                "transition_out": {"type": "dissolve", "duration": 0.25},
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
                        {"property": "camera_x", "keyframes": [
                            {"at": 0, "value": 0.5, "easing": "linear"},
                            {"at": 1, "value": 0.25, "easing": "ease_in_out"},
                        ]},
                        {"property": "camera_y", "keyframes": [
                            {"at": 0, "value": 0.5, "easing": "linear"},
                            {"at": 1, "value": 0.6, "easing": "ease_out"},
                        ]},
                        {"property": "camera_scale", "keyframes": [
                            {"at": 0, "value": 1.0, "easing": "linear"},
                            {"at": 1, "value": 1.2, "easing": "ease_in_out"},
                        ]},
                    ],
                }],
            }],
        }],
        "active_variant_id": "active-cut",
    })
    result = fd.build_film_from_composition(project_id, ir)
    assert result["duration"] == 1
    assert result["selected_variant_id"] == "active-cut"
    main_track = next(track for track in result["document"]["tracks"] if track["id"] == "track-main")
    clip = main_track["clips"][0]
    assert clip["attrs"]["data-asset-id"] == "asset-variant"
    assert clip["attrs"]["data-source-variant-version"] == "2"
    assert clip["attrs"]["data-source-variant-id"] == "active-cut"
    transform = json.loads(clip["attrs"]["data-film-transform"])
    assert transform["x"] == 0.4 and transform["y"] == 0.6
    assert transform["scale"] == 1.25 and transform["rotation"] == 12
    assert transform["opacity"] == 0.8 and transform["anchor"] == "center"
    assert "object-fit:contain" in clip["attrs"]["style"]
    channels = json.loads(clip["attrs"]["data-film-keyframes"])
    assert {item["property"] for item in channels} == {
        "x", "opacity", "camera_x", "camera_y", "camera_scale",
    }
    assert any("active-cut" in warning for warning in result["warnings"])
    warnings_text = "\n".join(result["warnings"])
    assert "metadata 字段未映射" in warnings_text
    assert "转场 type=dissolve" in warnings_text
    assert not any(
        f"keyframes:{name}" in warning
        for warning in result["warnings"]
        for name in ("x", "opacity", "camera_x", "camera_y", "camera_scale")
    )

    from video_director import editor as vd_editor
    monkeypatch.setattr(vd_editor, "composition_version", lambda _project_id: 7)
    initialized = fd.initialize_project_film(
        project_id, result["document"], result["pages"], 7, result["warnings"],
        selected_variant_id=result["selected_variant_id"],
    )
    assert initialized["migration"]["selected_variant_id"] == "active-cut"


def test_active_variant_duration_includes_audio_extending_past_final_scene(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "variant-audio-tail-duration"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"image source not decoded by migration")
    (assets / "music.wav").write_bytes(b"audio source not decoded by migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-still", "file": "assets/still.png", "type": "image"},
        {"id": "asset-music", "file": "assets/music.wav", "type": "audio"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "scenes": [{
            "id": "fallback-scene", "start": 0, "duration": 1, "text": "fallback",
            "layers": [{"type": "image", "asset_id": "asset-still", "start": 0, "duration": 1}],
        }],
        "variants": [{
            "id": "active-cut",
            "scenes": [{
                "id": "active-scene", "start": 0, "duration": 1, "text": "active",
                "layers": [{"type": "image", "asset_id": "asset-still", "start": 0, "duration": 1}],
            }],
            "tracks": [{
                "id": "long-bgm", "type": "bgm", "asset_id": "asset-music",
                "start": 0, "duration": 3, "volume": 0.4,
            }],
        }],
        "active_variant_id": "active-cut",
    })
    result = fd.build_film_from_composition(project_id, ir)
    assert result["duration"] == 3
    audio_track = next(track for track in result["document"]["tracks"]
                       if any(clip["tag"] == "audio" for clip in track["clips"]))
    audio_clip = audio_track["clips"][0]
    assert audio_clip["attrs"]["data-asset-id"] == "asset-music"
    assert audio_clip["attrs"]["at"] == "0"
    assert "与旧 project.duration" in "\n".join(result["warnings"])
