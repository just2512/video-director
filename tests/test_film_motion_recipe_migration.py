"""Version-pinned MotionRecipe instances migrate into deterministic FilmRuntime keyframes."""
from __future__ import annotations

import json

from video_director import config, film_document as fd
from video_director.models import CompositionIR


def test_motion_recipe_instance_maps_opacity_and_pixel_offset_without_losing_identity(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "motion-recipe-migration"
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
            "id": "motion-scene", "start": 0, "duration": 1, "text": "动效",
            "layers": [{
                "id": "legacy-motion-layer", "type": "image", "asset_id": "asset-still",
                "start": 0, "duration": 1,
                "motion_recipe_id": "motion.fade-slide", "motion_version": "1.0.0",
                "motion_instance": {"start_offset": 0.2, "duration": 0.5, "parameters": {"offsetPx": 65}},
            }],
        }],
    })
    result = fd.build_film_from_composition(project_id, ir)
    clip = next(track for track in result["document"]["tracks"] if track["id"] == "track-main")["clips"][0]
    assert clip["attrs"]["data-source-id"] == "legacy-motion-layer"
    assert clip["attrs"]["data-motion-recipe-id"] == "motion.fade-slide"
    assert clip["attrs"]["data-motion-recipe-version"] == "1.0.0"
    assert json.loads(clip["attrs"]["data-motion-instance"])["parameters"]["offsetPx"] == 65
    channels = json.loads(clip["attrs"]["data-film-keyframes"])
    mapped = {channel["property"]: channel["keyframes"] for channel in channels}
    assert {"opacity", "offset_y"}.issubset(mapped)
    assert [point["at"] for point in mapped["offset_y"]] == [0.0, 0.2, 0.7]
    assert [point["value"] for point in mapped["offset_y"]] == [65.0, 65.0, 0.0]
    assert mapped["opacity"][0]["value"] == 0.0
    assert not any("无法解析" in warning for warning in result["warnings"])



def test_missing_motion_recipe_is_reported_without_aborting_other_migration(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "missing-motion-recipe"
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
            "id": "motion-scene", "start": 0, "duration": 1, "text": "动效",
            "layers": [{
                "id": "legacy-unknown-recipe", "type": "image", "asset_id": "asset-still",
                "start": 0, "duration": 1,
                "motion_recipe_id": "motion.does-not-exist", "motion_version": "99.0.0",
                "motion_instance": {"start_offset": 0.2, "duration": 0.5, "parameters": {"custom": 7}},
            }],
        }],
    })
    result = fd.build_film_from_composition(project_id, ir)
    clip = next(track for track in result["document"]["tracks"] if track["id"] == "track-main")["clips"][0]
    assert clip["attrs"]["data-source-id"] == "legacy-unknown-recipe"
    assert clip["attrs"]["data-motion-recipe-id"] == "motion.does-not-exist"
    assert clip["attrs"]["data-motion-recipe-version"] == "99.0.0"
    assert json.loads(clip["attrs"]["data-motion-instance"])["parameters"]["custom"] == 7
    assert any("motion.does-not-exist@99.0.0 无法解析" in warning for warning in result["warnings"])
    assert clip["attrs"]["src"].endswith("#t=0,1")
