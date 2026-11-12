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

    # Semantic round-trip: a canonical serialization match must also preserve
    # the actual subtitle timeline contract after reopening the FilmDocument.
    reopened_track = next(track for track in reparsed["tracks"] if track["id"] == "track-subtitles")
    assert reopened_track["attrs"] == subtitle_track["attrs"]
    assert reopened_track["clips"] == subtitle_track["clips"]
    assert reopened_track["clips"][0]["attrs"]["src"] == "scenes/subtitles.html#t=0,3"
    reopened_html = (project_root / "scenes" / "subtitles.html").read_text(encoding="utf-8")
    assert reopened_html == html
    reopened_archive = json.loads(archive_path.read_text(encoding="utf-8"))
    assert reopened_archive == archived
