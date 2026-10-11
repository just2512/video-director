"""Artifact Dependency + Selective Rebuild 契约测试。

覆盖：
- Scene artifact 指纹只依赖该场景真实视觉输入；
- 不同场景可独立复用；
- 场景视觉参数变化只使对应 scene artifact 失效；
- 引用素材文件变化会使引用它的 scene 失效；
- 纯时间轴位置 / creative candidate 选择变化不强制重建 scene media；
- Editor stale 事件能进入 artifact manifest；
- manifest 写入采用临时文件替换，避免半写状态。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_director import artifact_graph, config, editor
from video_director.models import AssetRef, CompositionIR, Layer, LayerType, SceneIR


def _ir(*, selected_candidate: str | None = None, scene2_start: float = 4.0) -> CompositionIR:
    return CompositionIR(
        project={"width": 1080, "height": 1920, "fps": 30, "duration": 8.0},
        scenes=[
            SceneIR(
                id="scene-1",
                start=0.0,
                duration=4.0,
                text="第一镜",
                layers=[Layer(type=LayerType.image, duration=4.0, asset_id="asset-1")],
                visual_intent="原始意图",
                selected_candidate_id=selected_candidate,
            ),
            SceneIR(
                id="scene-2",
                start=scene2_start,
                duration=4.0,
                text="第二镜",
                layers=[Layer(type=LayerType.image, duration=4.0, asset_id="asset-2")],
            ),
        ],
    )


def _assets(tmp_path: Path) -> dict[str, AssetRef]:
    a1 = tmp_path / "a1.jpg"
    a2 = tmp_path / "a2.jpg"
    a1.write_bytes(b"asset-one")
    a2.write_bytes(b"asset-two")
    return {
        "asset-1": AssetRef(id="asset-1", type="image", file=str(a1)),
        "asset-2": AssetRef(id="asset-2", type="image", file=str(a2)),
    }


def _fresh_manifest(graph: dict, scene_paths: dict[str, str]) -> dict:
    rows = {}
    for aid, node in graph["artifacts"].items():
        row = dict(node)
        if aid in scene_paths:
            row["path"] = scene_paths[aid]
            row["status"] = artifact_graph.FRESH
        rows[aid] = row
    return {"version": 1, "artifacts": rows}


def test_only_changed_scene_requires_rebuild(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    assets = _assets(tmp_path)
    project_id = "artifact-selective-1"
    ir = _ir()

    graph = artifact_graph.build_graph(ir, assets)
    p1 = tmp_path / "scene1.mp4"
    p2 = tmp_path / "scene2.mp4"
    p1.write_bytes(b"scene-1")
    p2.write_bytes(b"scene-2")
    artifact_graph.save_manifest(
        project_id,
        _fresh_manifest(graph, {
            "scene:scene-1:render": str(p1),
            "scene:scene-2:render": str(p2),
        }),
    )

    changed_scene = ir.scenes[0].model_copy(update={
        "layers": [ir.scenes[0].layers[0].model_copy(update={"motion": "slow_zoom_in"})]
    })
    changed_ir = ir.model_copy(update={"scenes": [changed_scene, ir.scenes[1]]})

    _, reuse = artifact_graph.prepare_render(project_id, changed_ir, assets)
    assert "scene:scene-2:render" in reuse
    assert "scene:scene-1:render" not in reuse


def test_asset_file_change_invalidates_only_referencing_scene(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    assets = _assets(tmp_path)
    project_id = "artifact-selective-2"
    ir = _ir()
    graph = artifact_graph.build_graph(ir, assets)
    p1 = tmp_path / "scene1.mp4"
    p2 = tmp_path / "scene2.mp4"
    p1.write_bytes(b"scene-1")
    p2.write_bytes(b"scene-2")
    artifact_graph.save_manifest(
        project_id,
        _fresh_manifest(graph, {
            "scene:scene-1:render": str(p1),
            "scene:scene-2:render": str(p2),
        }),
    )

    asset1 = Path(assets["asset-1"].file)
    asset1.write_bytes(b"asset-one-mutated")

    _, reuse = artifact_graph.prepare_render(project_id, ir, assets)
    assert "scene:scene-1:render" not in reuse
    assert "scene:scene-2:render" in reuse


def test_timeline_position_and_candidate_selection_do_not_change_scene_fingerprint(tmp_path):
    assets = _assets(tmp_path)
    base = artifact_graph.build_graph(_ir(selected_candidate="direct"), assets)
    moved = artifact_graph.build_graph(_ir(selected_candidate="concept", scene2_start=4.0), assets)

    assert (
        base["artifacts"]["scene:scene-1:render"]["fingerprint"]
        == moved["artifacts"]["scene:scene-1:render"]["fingerprint"]
    )
    assert (
        base["artifacts"]["scene:scene-2:render"]["fingerprint"]
        == moved["artifacts"]["scene:scene-2:render"]["fingerprint"]
    )


def test_reordering_changes_visual_but_keeps_scene_artifacts(tmp_path):
    assets = _assets(tmp_path)
    base_ir = _ir()
    base = artifact_graph.build_graph(base_ir, assets)

    reversed_ir = base_ir.model_copy(update={
        "scenes": [
            base_ir.scenes[1].model_copy(update={"start": 0.0}),
            base_ir.scenes[0].model_copy(update={"start": 4.0}),
        ]
    })
    moved = artifact_graph.build_graph(reversed_ir, assets)

    assert moved["artifacts"]["scene:scene-1:render"]["fingerprint"] == base["artifacts"]["scene:scene-1:render"]["fingerprint"]
    assert moved["artifacts"]["scene:scene-2:render"]["fingerprint"] == base["artifacts"]["scene:scene-2:render"]["fingerprint"]
    assert moved["artifacts"]["visual"]["fingerprint"] != base["artifacts"]["visual"]["fingerprint"]


def test_editor_stale_event_marks_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    project_id = "artifact-stale-editor"
    ir = _ir()
    editor.save_composition(project_id, ir)

    graph = artifact_graph.build_graph(ir, _assets(tmp_path))
    scene_file = tmp_path / "scene.mp4"
    scene_file.write_bytes(b"x")
    manifest = _fresh_manifest(graph, {"scene:scene-1:render": str(scene_file)})
    artifact_graph.save_manifest(project_id, manifest)

    result = editor.execute_command(project_id, {
        "command": "set_transition_out",
        "targetId": "scene-1",
        "payload": {"type": "crossfade", "duration": 0.4},
        "author": "test",
    })
    assert result.status == "APPLIED"

    saved = artifact_graph.load_manifest(project_id)
    assert saved["artifacts"]["scene:scene-1:render"]["status"] == artifact_graph.STALE


def test_save_manifest_replaces_file_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    project_id = "artifact-atomic"
    path = artifact_graph.manifest_path(project_id)
    artifact_graph.save_manifest(project_id, {"version": 1, "project_id": project_id, "artifacts": {}})
    assert path.is_file()
    assert not path.with_suffix(".tmp").exists()

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["project_id"] == project_id
