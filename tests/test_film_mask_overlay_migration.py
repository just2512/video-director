"""Static masks, locked timeline state and overlay presentation migrate into the film contract."""
from __future__ import annotations

import json
import pytest

from video_director import config, film_document as fd
from video_director.models import CompositionIR


def test_animated_mask_path_keyframes_migrate_to_film_runtime_contract(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "animated-mask-path-migration"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "assets": [{"id": "asset-image", "type": "image", "file": "assets/still.png"}],
        "scenes": [{
            "id": "animated-mask-scene", "start": 0, "duration": 1, "text": "mask",
            "layers": [{
                "type": "image", "asset_id": "asset-image", "start": 0, "duration": 1,
                "mask": {
                    "id": "animated-mask", "shape": "polygon", "animated": True,
                    "geometry": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]],
                    "keyframes": [{"property": "mask_path", "keyframes": [
                        {"at": 0, "value": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]], "easing": "linear"},
                        {"at": 1, "value": [[0.5, 0], [1, 0], [1, 1], [0.5, 1]], "easing": "ease_in_out"},
                    ]}],
                },
            }],
        }],
    })
    result = fd.build_film_from_composition(project_id, ir)
    track = next(item for item in result["document"]["tracks"] if item["id"] == "track-main")
    attrs = track["clips"][0]["attrs"]
    migrated = json.loads(attrs["data-film-mask-keyframes"])
    assert len(migrated) == 2
    assert migrated[0]["value"] == [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
    assert migrated[1]["value"][1] == [1.0, 0.0]
    assert "clip-path:polygon(" in attrs["style"]
    warnings = "\n".join(result["warnings"])
    assert "动态蒙版关键帧尚未迁移" not in warnings
    assert "Bezier/开放路径语义" not in warnings


@pytest.mark.parametrize(("shape", "mask", "expected"), [
    ("ellipse", {"id": "shape", "shape": "ellipse", "x": 0.5, "y": 0.5,
                 "width": 0.4, "height": 0.6}, "clip-path:ellipse(20% 30% at 50% 50%)"),
    ("polygon", {"id": "shape", "shape": "polygon", "geometry": [
        [0.1, 0.2], [0.9, 0.2], [0.7, 0.8], [0.2, 0.9],
    ]}, "clip-path:polygon(10% 20%, 90% 20%, 70% 80%, 20% 90%)"),
    ("rectangle", {"id": "shape", "shape": "rectangle", "x": 0.5, "y": 0.5,
                   "width": 0.6, "height": 0.4}, "clip-path:inset(30% 20% 30% 20%)"),
    ("path", {"id": "shape", "shape": "path", "geometry": [
        [0.1, 0.1], [0.9, 0.15], [0.75, 0.8], [0.25, 0.9],
    ]}, "clip-path:polygon(10% 10%, 90% 15%, 75% 80%, 25% 90%)"),
])
def test_css_representable_static_masks_and_overlay_z_order_are_migrated(tmp_path, monkeypatch, shape, mask, expected):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "mask-overlay-" + shape
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "assets": [{"id": "asset-image", "type": "image", "file": "assets/still.png"}],
        "scenes": [{
            "id": "masked-opening", "start": 0, "duration": 1, "text": "masked",
            "layers": [{
                "type": "image", "asset_id": "asset-image", "start": 0, "duration": 1,
                "mask": mask,
            }],
        }],
        "visual_tracks": [{
            "id": "overlay-main", "name": "画面叠加", "order": 0,
            "locked": True, "hidden": True,
        }],
        "visual_overlays": [{
            "id": "full-screen-card", "asset_id": "asset-image", "start": 0, "duration": 1,
            "presentation_mode": "full_frame", "fit_mode": "contain", "z_index": 8, "opacity": 0.7,
        }],
    })
    result = fd.build_film_from_composition(project_id, ir)
    base = next(track for track in result["document"]["tracks"] if track["id"] == "track-main")
    assert expected in base["clips"][0]["attrs"]["style"]
    overlay = next(track for track in result["document"]["tracks"] if track["id"] == "track-overlay-full-screen-card")
    assert overlay["attrs"]["data-render-z-index"] == "100001"
    assert "locked" in overlay["attrs"]
    assert overlay["attrs"]["data-source-track-hidden"] == "true"
    assert overlay["attrs"]["style"] == "z-index:100001"
    base_index = next(i for i, track in enumerate(result["document"]["tracks"]) if track["id"] == "track-main")
    overlay_index = next(i for i, track in enumerate(result["document"]["tracks"]) if track["id"] == "track-overlay-full-screen-card")
    assert overlay_index < base_index
    attrs = overlay["clips"][0]["attrs"]
    assert attrs["data-source-id"] == "full-screen-card"
    assert "left:0px" in attrs["style"] and "top:0px" in attrs["style"]
    assert "width:320px" in attrs["style"] and "height:180px" in attrs["style"]
    assert "z-index:8" in attrs["style"]
    assert attrs["opacity"] == "0.7"
    assert not any("presentation_mode" in warning for warning in result["warnings"])
    assert not any("mask:蒙版形状" in warning for warning in result["warnings"])
    if shape == "path":
        assert any("path 蒙版按 IR 中保存的归一顶点" in warning for warning in result["warnings"])


def test_mask_keyframes_with_different_vertex_counts_are_rejected_explicitly():
    tracks = [{
        "property": "mask_path",
        "keyframes": [
            {"at": 0, "value": [[0, 0], [1, 0], [1, 1], [0, 1]]},
            {"at": 1, "value": [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]]},
        ],
    }]
    mapped, warnings = fd._film_mask_keyframes(tracks, 1.0)
    assert mapped == []
    assert any("顶点数量不同" in warning for warning in warnings)


def test_inverted_animated_mask_is_not_silently_applied_as_positive_polygon(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "inverted-animated-mask"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "assets": [{"id": "asset-image", "type": "image", "file": "assets/still.png"}],
        "scenes": [{
            "id": "inverted-mask-scene", "start": 0, "duration": 1, "text": "mask",
            "layers": [{
                "type": "image", "asset_id": "asset-image", "start": 0, "duration": 1,
                "mask": {
                    "id": "inverted-animated-mask", "shape": "polygon", "inverted": True,
                    "animated": True, "geometry": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]],
                    "keyframes": [{"property": "mask_path", "keyframes": [
                        {"at": 0, "value": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]]},
                        {"at": 1, "value": [[0.1, 0], [0.6, 0], [0.6, 1], [0.1, 1]]},
                    ]}],
                },
            }],
        }],
    })
    result = fd.build_film_from_composition(project_id, ir)
    track = next(item for item in result["document"]["tracks"] if item["id"] == "track-main")
    attrs = track["clips"][0]["attrs"]
    assert "data-film-mask-keyframes" not in attrs
    assert "clip-path:" not in attrs.get("style", "")
    warnings = "\n".join(result["warnings"])
    assert "inverted 动态蒙版无法由当前 Runtime 无损表达" in warnings
    archived = json.loads(result["pages"]["migration/source-composition.json"])
    assert archived["scenes"][0]["layers"][0]["mask"]["inverted"] is True
    assert archived["scenes"][0]["layers"][0]["mask"]["keyframes"]


def test_animated_mask_stays_in_composition_space_when_layer_transform_changes(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "mask-transform-coordinate-conflict"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")
    source_mask = {
        "id": "mask-coordinate-conflict",
        "shape": "polygon",
        "x": 0.5, "y": 0.5, "width": 0.5, "height": 0.5,
        "animated": True,
        "geometry": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.9], [0.1, 0.9]],
        "keyframes": [{
            "property": "mask_path",
            "keyframes": [
                {"at": 0, "value": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.9], [0.1, 0.9]]},
                {"at": 1, "value": [[0.2, 0.1], [0.6, 0.1], [0.6, 0.9], [0.2, 0.9]]},
            ],
        }],
    }
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "assets": [{"id": "asset-image", "type": "image", "file": "assets/still.png"}],
        "scenes": [{
            "id": "masked-scene", "start": 0, "duration": 1, "text": "mask",
            "layers": [{
                "type": "image", "asset_id": "asset-image", "start": 0, "duration": 1,
                "transform": {"x": 0.25, "y": 0.5, "scale": 1, "rotation": 0, "opacity": 1},
                "mask": source_mask,
            }],
        }],
    })

    result = fd.build_film_from_composition(project_id, ir)
    track = next(item for item in result["document"]["tracks"] if item["id"] == "track-main")
    attrs = track["clips"][0]["attrs"]
    assert attrs["data-film-mask-space"] == "composition"
    assert "data-film-mask-keyframes" in attrs
    assert "clip-path:polygon(" in attrs.get("style", "")
    assert not any("坐标系冲突；未写入近似关键帧" in warning for warning in result["warnings"])
    archived = json.loads(result["pages"]["migration/source-composition.json"])
    archived_mask = archived["scenes"][0]["layers"][0]["mask"]
    assert archived_mask["keyframes"] == source_mask["keyframes"]
    assert archived_mask["geometry"] == source_mask["geometry"]


def test_visual_track_order_precedes_each_overlay_local_z_index(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "overlay-track-order-beats-local-z"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")
    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "assets": [{"id": "asset-image", "type": "image", "file": "assets/still.png"}],
        "scenes": [{
            "id": "base-scene", "start": 0, "duration": 1, "text": "base",
            "layers": [{"type": "image", "asset_id": "asset-image", "start": 0, "duration": 1}],
        }],
        "visual_tracks": [
            {"id": "overlay-back", "name": "背景叠加", "order": 0},
            {"id": "overlay-front", "name": "前景叠加", "order": 1},
        ],
        "visual_overlays": [
            {"id": "back-high-local-z", "asset_id": "asset-image", "track_id": "overlay-back",
             "start": 0, "duration": 1, "z_index": 100000},
            {"id": "front-low-local-z", "asset_id": "asset-image", "track_id": "overlay-front",
             "start": 0, "duration": 1, "z_index": -100000},
        ],
    })
    result = fd.build_film_from_composition(project_id, ir)
    back = next(track for track in result["document"]["tracks"]
                if track["id"] == "track-overlay-back-high-local-z")
    front = next(track for track in result["document"]["tracks"]
                 if track["id"] == "track-overlay-front-low-local-z")
    back_z = int(back["attrs"]["data-render-z-index"])
    front_z = int(front["attrs"]["data-render-z-index"])
    assert front_z > back_z, (
        "visual_tracks.order must order different physical Film tracks before "
        f"overlay-local z_index: back={back_z}, front={front_z}"
    )
    assert 100001 <= back_z < front_z < 200002, (
        "all overlay tracks must remain below subtitles/watermarks"
    )
