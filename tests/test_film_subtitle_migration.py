from __future__ import annotations

import json

from video_director import film_document as fd
from video_director.editor import composition_version
from video_director.models import CompositionIR


def test_composition_ir_subtitle_cues_and_supported_style_migrate_with_archival_warnings(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "subtitle-migration-contract"
    project_root = storage / project_id
    project_root.mkdir(parents=True)
    assets_root = project_root / "assets"
    assets_root.mkdir()
    (assets_root / "still.png").write_bytes(b"subtitle-migration-fixture")
    (project_root / "manifest.json").write_text(
        json.dumps({
            "assets": [{
                "id": "asset-still",
                "kind": "image",
                "path": "assets/still.png",
                "filename": "still.png",
                "mime_type": "image/png",
            }]
        }),
        encoding="utf-8",
    )

    ir = CompositionIR.model_validate({
        "version": 1,
        "project": {"width": 640, "height": 360, "fps": 30, "duration": 3},
        "assets": [{
            "id": "asset-still",
            "type": "image",
            "file": "assets/still.png",
            "source": "uploaded",
            "description": "Subtitle migration fixture",
        }],
        "scenes": [{
            "id": "scene-1",
            "start": 0,
            "duration": 3,
            "text": "Legacy narration remains source text, not auto-generated speech.",
            "layers": [{
                "id": "layer-1",
                "type": "image",
                "asset_id": "asset-still",
                "start": 0,
                "duration": 3,
            }],
        }],
        "subtitle_style": {
            "font": "Noto Sans CJK",
            "size": 1.2,
            "color": "#F0F0F0",
            "stroke_color": "#101010",
            "stroke_width": 3,
            "background": "#000000@0.4",
            "background_opacity": 0.4,
            "position": "top",
            "alignment": "left",
            "max_width": 0.8,
            "animation": "fade",
            "mode": "word",
        },
        "subtitle_track": {
            "mode": "word",
            "source": "manual",
            "style_id": "subtitle-brand",
            "cues": [
                {"start": 0.25, "end": 0.75, "text": "Hello"},
                {"start": 0.75, "end": 1.5, "text": "world"},
            ],
        },
    })

    result = fd.build_film_from_composition(project_id, ir)
    warnings = result["warnings"]
    assert any("subtitle_style.mode" in warning for warning in warnings)
    assert any("subtitle_track.style_id" in warning for warning in warnings)
    assert any("字幕对齐来源元数据未写入" in warning for warning in warnings)
    assert any("字幕 mode 元数据未写入" in warning for warning in warnings)

    assert "scenes/subtitles.html" in result["pages"]
    initialized = fd.initialize_project_film(
        project_id,
        result["document"],
        result["pages"],
        source_version=composition_version(project_id),
        warnings=warnings,
        selected_variant_id=result["selected_variant_id"],
    )
    assert initialized["ok"] is True
    document = initialized["document"]
    subtitle_track = next(track for track in document["tracks"] if track["id"] == "track-subtitles")
    subtitle_clip = subtitle_track["clips"][0]
    assert subtitle_clip["attrs"]["src"] == "scenes/subtitles.html#t=0,3"
    assert subtitle_track["attrs"]["data-render-z-index"] == "200002"
    assert subtitle_track["attrs"]["style"] == "z-index:200002"

    subtitle_page = project_root / "scenes" / "subtitles.html"
    assert subtitle_page.is_file()
    html = subtitle_page.read_text(encoding="utf-8")
    assert '"start":0.25,"end":0.75,"text":"Hello"' in html
    assert '"start":0.75,"end":1.5,"text":"world"' in html
    assert 'font-family:"Noto Sans CJK","WenQuanYi Micro Hei",sans-serif' in html
    assert "font-size:64px" in html
    assert "color:#F0F0F0" in html
    assert "-webkit-text-stroke:3px #101010" in html
    assert "background-color:rgba(0,0,0,0.4)" in html
    assert "text-align:left" in html
    assert "width:80%" in html
    assert "top:36px;bottom:auto" in html
    assert 'const animation="fade"' in html

    archive_path = project_root / result["source_archive"]["path"]
    archived = json.loads(archive_path.read_text(encoding="utf-8"))
    assert archived["subtitle_style"]["mode"] == "word"
    assert archived["subtitle_track"]["mode"] == "word"
    assert archived["subtitle_track"]["source"] == "manual"
    assert archived["subtitle_track"]["style_id"] == "subtitle-brand"

    serialized = fd.serialize_film(document)
    reparsed = fd.parse_film(serialized)
    assert fd.serialize_film(reparsed) == serialized


def test_project_spec_dimensions_rate_and_duration_have_explicit_runtime_semantics(tmp_path, monkeypatch):
    """ProjectSpec's executable subset must reach FilmDocument; the archive retains its source."""
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "project-spec-migration-contract"
    project_root = storage / project_id
    project_root.mkdir(parents=True)
    assets_root = project_root / "assets"
    assets_root.mkdir()
    (assets_root / "still.png").write_bytes(b"project-spec-fixture")
    (project_root / "manifest.json").write_text(
        json.dumps({"assets": [{
            "id": "asset-still", "kind": "image", "path": "assets/still.png",
            "filename": "still.png", "mime_type": "image/png",
        }]}),
        encoding="utf-8",
    )

    ir = CompositionIR.model_validate({
        "version": 1,
        "project": {"width": 1280, "height": 720, "fps": 24, "duration": 8},
        "assets": [{
            "id": "asset-still", "type": "image", "file": "assets/still.png",
            "source": "uploaded", "description": "ProjectSpec migration fixture",
        }],
        "scenes": [{
            "id": "scene-project-spec", "start": 0, "duration": 8,
            "text": "ProjectSpec contract",
            "layers": [{
                "id": "layer-project-spec", "type": "image", "asset_id": "asset-still",
                "start": 0, "duration": 8, "fit_mode": "contain",
            }],
        }],
    })

    result = fd.build_film_from_composition(project_id, ir)
    document = result["document"]
    assert document["stage"] == {"width": 1280, "height": 720}
    assert document["fps"] == 24
    assert result["duration"] == 8

    archive = json.loads(result["pages"][result["source_archive"]["path"]])
    assert archive["project"] == {"width": 1280, "height": 720, "fps": 24, "duration": 8}


def test_scene_and_video_layer_timing_source_range_and_fit_mode_map_to_film_clip(tmp_path, monkeypatch):
    """SceneIR/Layer timing and supported media properties must map to one Film clip."""
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "scene-layer-migration-contract"
    project_root = storage / project_id
    project_root.mkdir(parents=True)
    assets_root = project_root / "assets"
    assets_root.mkdir()
    (assets_root / "clip.mp4").write_bytes(b"scene-layer-fixture")
    (project_root / "manifest.json").write_text(
        json.dumps({"assets": [{
            "id": "asset-video", "kind": "video", "path": "assets/clip.mp4",
            "filename": "clip.mp4", "mime_type": "video/mp4",
        }]}),
        encoding="utf-8",
    )

    ir = CompositionIR.model_validate({
        "version": 1,
        "project": {"width": 1280, "height": 720, "fps": 24, "duration": 3.5},
        "assets": [{
            "id": "asset-video", "type": "video", "file": "assets/clip.mp4",
            "source": "uploaded", "description": "Scene and layer migration fixture",
        }],
        "scenes": [{
            "id": "scene-offset", "start": 0, "duration": 3.5,
            "text": "Scene timing contract",
            "layers": [{
                "id": "layer-prefix", "type": "video", "asset_id": "asset-video",
                "start": 0, "duration": 1.5, "source_in": 0, "source_out": 1.5,
            }, {
                "id": "layer-offset", "type": "video", "asset_id": "asset-video",
                "start": 1.5, "duration": 2, "source_in": 1, "source_out": 5,
                "speed": 2, "fit_mode": "contain",
            }],
        }],
    })

    result = fd.build_film_from_composition(project_id, ir)
    clips = [
        clip for track in result["document"]["tracks"] for clip in track["clips"]
        if clip["attrs"].get("data-source-id") == "layer-offset"
    ]
    assert len(clips) == 1
    attrs = clips[0]["attrs"]
    assert attrs["src"].endswith("#t=1,5")
    assert attrs["at"] == "1.5"
    assert attrs["speed"] == "2"
    assert attrs["data-source-scene-id"] == "scene-offset"
    assert "object-fit:contain" in attrs["style"]
    assert result["duration"] == 3.5


def test_asset_variant_audio_lane_and_mask_semantics_are_preserved_or_explicitly_rejected(tmp_path, monkeypatch):
    """Mapped fields reach Film clips; unsupported mask semantics stay archived with warnings."""
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "asset-variant-audio-mask-contract"
    project_root = storage / project_id
    assets_root = project_root / "assets"
    assets_root.mkdir(parents=True)
    (assets_root / "still.png").write_bytes(b"still-fixture")
    (assets_root / "music.wav").write_bytes(b"audio-fixture")
    (project_root / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "kind": "image", "path": "assets/still.png", "filename": "still.png", "mime_type": "image/png"},
        {"id": "asset-music", "kind": "audio", "path": "assets/music.wav", "filename": "music.wav", "mime_type": "audio/wav"},
    ]), encoding="utf-8")

    scenes = [{
        "id": "scene-variant", "start": 0, "duration": 3, "text": "variant semantic mapping",
        "layers": [
            {"id": "layer-mask-supported", "type": "image", "asset_id": "asset-image", "start": 0, "duration": 1.5,
             "mask": {"id": "mask-ellipse", "shape": "ellipse", "x": 0.5, "y": 0.5, "width": 0.7, "height": 0.8, "feather": 2, "opacity": 0.6}},
            {"id": "layer-mask-inverted", "type": "image", "asset_id": "asset-image", "start": 1.5, "duration": 1.5,
             "mask": {"id": "mask-inverted", "shape": "polygon", "geometry": [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]], "inverted": True}},
        ],
    }]
    ir = CompositionIR.model_validate({
        "version": 1,
        "project": {"width": 640, "height": 360, "fps": 30, "duration": 3},
        "assets": [
            {"id": "asset-image", "type": "image", "file": "assets/still.png", "source": "generated",
             "description": "generated still", "keywords": ["fixture", "asset-ref"],
             "provenance": {"provider": "fixture-provider", "model": "fixture-model"},
             "rights_status": "CLEAR", "license_note": "test-only"},
            {"id": "asset-music", "type": "audio", "file": "assets/music.wav", "source": "uploaded",
             "description": "music asset", "rights_status": "REVIEW"},
        ],
        "scenes": scenes,
        "audio_tracks": [{"id": "audio-custom", "name": "Custom BGM", "order": 3, "muted": True, "hidden": True}],
        "variants": [{
            "id": "variant-final", "version": 4, "scenes": scenes,
            "tracks": [{"id": "variant-bgm", "type": "bgm", "track_id": "audio-custom", "asset_id": "asset-music",
                        "start": 0, "duration": 3, "volume": 0.65, "fadeIn": 0.25}],
            "metadata": {"editor": "fixture", "approval": "reviewed"},
        }],
        "active_variant_id": "variant-final",
    })

    result = fd.build_film_from_composition(project_id, ir)
    clips = [clip for track in result["document"]["tracks"] for clip in track["clips"]]
    supported = next(clip for clip in clips if clip["attrs"].get("data-source-id") == "layer-mask-supported")
    inverted = next(clip for clip in clips if clip["attrs"].get("data-source-id") == "layer-mask-inverted")
    audio = next(clip for clip in clips if clip["attrs"].get("data-source-id") == "variant-bgm")

    image_metadata = json.loads(supported["attrs"]["data-source-asset-metadata"])
    assert image_metadata["source"] == "generated"
    assert image_metadata["provenance"] == {"provider": "fixture-provider", "model": "fixture-model"}
    assert image_metadata["rights_status"] == "CLEAR"
    assert "file" not in image_metadata, "raw filesystem paths must not leak into clip metadata"
    assert "clip-path:ellipse(" in supported["attrs"].get("style", "")
    assert "feather 羽化未映射" in " ".join(result["warnings"])
    assert "蒙版独立 opacity 未映射" in " ".join(result["warnings"])
    assert "inverted 反向蒙版不能用 CSS clip-path 无损表达" in " ".join(result["warnings"])
    assert "clip-path:" not in inverted["attrs"].get("style", "")

    assert audio["attrs"]["data-source-variant-id"] == "variant-final"
    assert audio["attrs"]["data-source-variant-version"] == "4"
    assert audio["attrs"]["data-source-track-id"] == "audio-custom"
    assert audio["attrs"]["muted"] == ""
    audio_metadata = json.loads(audio["attrs"]["data-source-asset-metadata"])
    assert audio_metadata["rights_status"] == "REVIEW"

    assert any(track["attrs"].get("data-source-variant-metadata") == '{"approval":"reviewed","editor":"fixture"}'
               for track in result["document"]["tracks"])
    archive = json.loads(result["pages"][result["source_archive"]["path"]])
    archived_layer = next(layer for scene in archive["scenes"] for layer in scene["layers"]
                          if layer["id"] == "layer-mask-inverted")
    assert archived_layer["mask"]["inverted"] is True
    assert archived_layer["mask"]["geometry"] == [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]]
