"""Selection time ranges and scene intent must stay attached to migrated clips."""
from __future__ import annotations

import json

from video_director import config, film_document as fd
from video_director.models import CompositionIR


def test_selection_and_scene_semantics_are_preserved_in_film_clip_metadata(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "selection-scene-metadata"
    project_root = config.project_dir(project_id)
    assets = project_root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-still", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")

    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 2},
        "scenes": [{
            "id": "scene-intent", "start": 0, "duration": 2, "text": "先给结论",
            "semantic_role": "hook",
            "intent": "先给出明确结论",
            "visual_intent": "标题先出现，再展示证据",
            "selected_candidate_id": "candidate-opening-2",
            "origin_recipe_id": "shot.medium-closeup",
            "origin_recipe_version": "1.2.0",
            "entrance_state": {"visible_layers": ["still"], "anchor_layer_id": "still"},
            "exit_state": {"visible_layers": ["still"], "anchor_layer_id": "still"},
            "continuity_out": {
                "id": "continuity-opening-next", "from_scene_id": "scene-intent",
                "to_scene_id": "scene-next", "type": "hard_cut",
            },
            "selections": [{
                "id": "selection-conclusion", "text": "先给结论", "start": 0.4, "end": 0.8,
            }],
            "layers": [{
                "id": "still", "type": "image", "asset_id": "asset-still",
                "start": 0, "duration": 2,
            }],
        }],
    })

    result = fd.build_film_from_composition(project_id, ir)
    track = next(item for item in result["document"]["tracks"] if item["id"] == "track-main")
    attrs = track["clips"][0]["attrs"]
    selections = json.loads(attrs["data-source-selections"])
    assert selections == [{
        "id": "selection-conclusion", "text": "先给结论", "start": 0.4, "end": 0.8,
    }]
    assert attrs["data-source-scene-semantic-role"] == "hook"
    assert attrs["data-source-scene-intent"] == "先给出明确结论"
    assert attrs["data-source-scene-visual-intent"] == "标题先出现，再展示证据"
    assert attrs["data-source-selected-candidate-id"] == "candidate-opening-2"
    assert attrs["data-source-origin-recipe-id"] == "shot.medium-closeup"
    assert attrs["data-source-origin-recipe-version"] == "1.2.0"
    assert json.loads(attrs["data-source-entrance-state"])["anchor_layer_id"] == "still"
    assert json.loads(attrs["data-source-exit-state"])["visible_layers"] == ["still"]
    assert json.loads(attrs["data-source-continuity-out"])["to_scene_id"] == "scene-next"
    assert not any("selections 语义锚点未映射" in warning for warning in result["warnings"])
    assert not any("创作 lineage 字段未映射" in warning for warning in result["warnings"])
