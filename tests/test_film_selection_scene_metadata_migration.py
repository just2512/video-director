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


def test_scene_text_planning_lock_and_transition_are_not_silently_executed(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "scene-source-only-semantics"
    project_root = config.project_dir(project_id)
    assets = project_root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-still", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")

    raw = {
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 3.45},
        "scenes": [{
            "id": "scene-source-only", "start": 0, "duration": 3.45,
            "text": "这段旁白不得自动变成字幕",
            "human_locked": True,
            "transition_out": {"type": "fade", "duration": 0.4},
            "layers": [
                {
                    "id": "scene-base-layer", "type": "image", "asset_id": "asset-still",
                    "start": 0, "duration": 2,
                },
                {
                    "id": "scene-leading-layer", "type": "image", "asset_id": "asset-still",
                    "start": 2, "duration": 0.25,
                },
                {
                    "id": "scene-source-layer", "type": "image", "asset_id": "asset-still",
                    "start": 2.25, "duration": 1.2,
                },
            ],
        }],
    }
    ir = CompositionIR.model_validate(raw)
    result = fd.build_film_from_composition(project_id, ir)

    # Overlapping or non-leading scene layers are emitted onto dedicated Film
    # tracks. Locate the source layer by provenance across the whole document,
    # rather than assuming every scene layer remains on track-main.
    source_clips = [
        clip
        for track in result["document"]["tracks"]
        for clip in track["clips"]
        if clip["attrs"].get("data-source-id") == "scene-source-layer"
    ]
    assert len(source_clips) == 1, source_clips
    attrs = source_clips[0]["attrs"]
    assert attrs["at"] == "2.25"
    # Film clip timing is derived from the layer offset and duration, not a scene clip.
    assert attrs["src"].endswith("#t=0,1.2")
    assert "data-clip-locked" not in attrs
    assert "transition" not in attrs

    snapshot = json.loads(result["pages"]["migration/source-composition.json"])
    scene = snapshot["scenes"][0]
    assert scene["text"] == raw["scenes"][0]["text"]
    assert scene["human_locked"] is True
    assert scene["transition_out"] == {"type": "fade", "duration": 0.4}
    # scene.text 现在自动合成为字幕轨（基本功能），不再产生「字幕未生成」警告。
    subtitle_tracks = [t for t in result["document"]["tracks"] if t["id"] == "track-subtitles"]
    assert len(subtitle_tracks) == 1
    assert "scenes/subtitles.html" in result["pages"]
    warnings = "\\n".join(result["warnings"])
    assert "没有自动生成字幕或配音" not in warnings
    assert "不等同于 Film 图层编辑锁" in warnings
    assert "尚未编译为 Film 转场" in warnings


def test_scene_text_synthesizes_subtitle_cues_with_aligned_timing(tmp_path, monkeypatch):
    """字幕是基本功能：scenes 的 text+start+duration 必须合成对齐的字幕 cue。"""
    import re
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "subtitle-synth-timing"
    assets = config.project_dir(project_id) / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-still", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")
    raw = {
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 9},
        "scenes": [
            {"id": "s1", "start": 0, "duration": 4, "text": "第一句旁白",
             "layers": [{"type": "image", "asset_id": "asset-still", "start": 0, "duration": 4}]},
            {"id": "s2", "start": 4, "duration": 5, "text": "第二句旁白",
             "layers": [{"type": "image", "asset_id": "asset-still", "start": 0, "duration": 5}]},
        ],
    }
    ir = CompositionIR.model_validate(raw)
    result = fd.build_film_from_composition(project_id, ir)
    page = result["pages"]["scenes/subtitles.html"]
    match = re.search(r"const cues=(\[.*?\]);", page)
    assert match, "字幕页必须内嵌 cues 数据"
    cues = json.loads(match.group(1))
    assert len(cues) == 2
    assert cues[0]["start"] == 0 and cues[0]["end"] == 4 and cues[0]["text"] == "第一句旁白"
    assert cues[1]["start"] == 4 and cues[1]["end"] == 9 and cues[1]["text"] == "第二句旁白"
    assert any(t["id"] == "track-subtitles" for t in result["document"]["tracks"])
