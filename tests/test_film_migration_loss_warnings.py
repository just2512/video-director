"""Migration diagnostics must expose unsupported CompositionIR fields."""
from __future__ import annotations

import json
from pathlib import Path

from video_director import config, film_document as fd
from video_director.models import CompositionIR


def test_migration_maps_audio_fades_and_lane_mute_and_warns_for_unmapped_fields(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "migration-loss-diagnostics"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-needed-by-migration")
    (assets / "music.wav").write_bytes(b"not-needed-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "file": "assets/still.png"},
        {"id": "asset-music", "file": "assets/music.wav"},
    ]), encoding="utf-8")

    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 24, "duration": 1},
        "style_id": "old-style-id",
        "scenes": [{
            "id": "scene-opening", "start": 0, "duration": 1, "text": "开场",
            "intent": "先给出结论",
            "layers": [{
                "type": "image", "asset_id": "asset-image", "start": 0, "duration": 1,
                "motion": "slow_zoom_in", "locked": True,
            }],
        }],
        "audio_tracks": [{
            "id": "audio-bgm", "name": "自定义 BGM", "order": 0,
            "muted": True, "locked": True, "hidden": True,
        }],
        "audio": [{
            "id": "music-bed", "track_id": "audio-bgm", "type": "bgm",
            "asset_id": "asset-music", "start": 0, "duration": 1, "volume": 0.5,
            "fadeIn": 0.2, "fadeOut": 0.3, "ducking": True,
        }],
        "watermarks": [{"id": "brand-mark", "kind": "text", "text": "测试水印"}],
        "subtitle_style": {
            "font": "Brand Subtitle Font", "size": 1.4, "position": "top", "animation": "fade",
            "stroke_color": "#333333", "background": "#101010@0.5",
            "alignment": "right", "max_width": 0.7,
        },
        "variants": [{"id": "alternative"}],
        "active_variant_id": "alternative",
        "origin_template_id": "template-old",
        "camera_tracks": [{"id": "camera-main"}],
    })

    result = fd.build_film_from_composition(project_id, ir)
    warnings = "\n".join(result["warnings"])
    assert "24 FPS" in warnings
    assert "style_id" in warnings
    assert "variants" in warnings
    assert "camera_tracks" in warnings
    assert "水印" in warnings
    assert "ducking" in warnings
    assert "字幕样式字段 font" in warnings
    assert "模板来源 lineage" in warnings
    assert "字幕样式字段 size" not in warnings
    assert "字幕样式字段 stroke" not in warnings
    subtitle_html = fd._subtitle_page(320, 180, 1.0, [{"start": 0, "end": 1, "text": "字幕"}], {
        "size": 1.4, "position": "top", "animation": "fade", "stroke_color": "#333333",
        "background": "#101010@0.5", "alignment": "right", "max_width": 0.7,
    })
    assert "font-size:75px" in subtitle_html
    assert "top:18px" in subtitle_html and "text-align:right" in subtitle_html
    assert "-webkit-text-stroke:2px #333333" in subtitle_html
    assert "rgba(16,16,16,0.5)" in subtitle_html and "width:70%" in subtitle_html
    assert 'const animation="fade"' in subtitle_html

    visual_track = next(track for track in result["document"]["tracks"] if track["id"] == "track-locked-main-scene-opening-1")
    assert "locked" in visual_track["attrs"]
    image_clip = visual_track["clips"][0]
    transform = json.loads(image_clip["attrs"]["data-film-transform"])
    channels = json.loads(image_clip["attrs"]["data-film-keyframes"])
    assert transform["anchor"] == "center"
    assert transform["base_width"] == 320 and transform["base_height"] == 180
    assert any(channel["property"] == "scale" for channel in channels)
    assert "object-fit:cover" in image_clip["attrs"]["style"]

    audio_track = next(track for track in result["document"]["tracks"]
                       if track["id"] == "track-audio-music-bed")
    audio_clip = audio_track["clips"][0]
    assert audio_track["attrs"]["data-label"] == "自定义 BGM"
    assert audio_clip["attrs"]["muted"] == ""
    assert audio_clip["attrs"]["fade"] == "0.2,0.3"
