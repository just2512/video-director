"""Creative Candidate Set contracts."""

from pathlib import Path
from types import SimpleNamespace

from video_director import creative_candidates, mpt_semantics


def test_candidate_set_always_has_three_distinct_approaches():
    snapshot = creative_candidates.build_candidate_snapshot([
        {
            "id": "S01",
            "text": "团队正在办公室开会。",
            "visual_intent": "真实记录团队讨论",
        }
    ])
    row = snapshot["scenes"][0]
    assert len(row["candidates"]) == 3
    assert [c["approach"] for c in row["candidates"]] == ["direct", "broll", "concept"]
    assert row["selected_candidate_id"] == row["candidates"][0]["id"]


def test_llm_candidate_fields_are_normalized_without_duplicate_approaches():
    candidates = creative_candidates.normalize_scene_candidates(
        "S02",
        "产品正在桌面上展示。",
        [
            {
                "title": "直拍",
                "approach": "direct",
                "visual_intent": "产品桌面真实展示",
                "search_terms": ["product demo", "desk"],
                "source_preference": "user_asset",
            },
            {
                "title": "细节",
                "approach": "broll",
                "visual_intent": "产品局部特写",
                "search_terms": ["product close up"],
                "source_preference": "stock",
            },
            {
                "title": "重复直拍",
                "approach": "direct",
                "visual_intent": "重复方案",
            },
        ],
    )
    assert len(candidates) == 3
    assert {c["approach"] for c in candidates} == {"direct", "broll", "concept"}
    assert candidates[0]["search_terms"] == ["product demo", "desk"]


def test_candidate_is_carried_into_mpt_material_intent_snapshot():
    snapshot = mpt_semantics.material_intent_snapshot(
        [{"id": "S01", "text": "办公室会议", "duration": 5.0}],
        search_terms_by_scene={"S01": ["office meeting"]},
        candidate_sets_by_scene={
            "S01": [{
                "id": "S01-C2",
                "title": "细节补画面",
                "approach": "broll",
                "visual_intent": "局部细节",
                "search_terms": ["office close up"],
            }]
        },
        selected_candidate_by_scene={"S01": "S01-C2"},
    )
    row = snapshot["scenes"][0]
    assert row["selected_candidate_id"] == "S01-C2"
    assert row["candidates"][0]["approach"] == "broll"


def test_candidate_selection_command_changes_intent_only_and_persists_choice(monkeypatch, tmp_path):
    from video_director import config
    from video_director.editor import _cmd_set_scene_intent
    from video_director.models import Layer, LayerType, SceneIR, CompositionIR, ProjectSpec

    monkeypatch.setattr(config, "project_dir", lambda pid: tmp_path / pid)
    project_dir = tmp_path / "p1" / "plans"
    project_dir.mkdir(parents=True)
    candidates = creative_candidates.build_candidate_snapshot([
        {"id": "S01", "text": "办公室会议", "visual_intent": "当前视觉"}
    ])
    candidate = candidates["scenes"][0]["candidates"][1]
    (project_dir / "creative_candidates.json").write_text(
        __import__("json").dumps(candidates, ensure_ascii=False), encoding="utf-8"
    )
    intents = {
        "version": 1,
        "scenes": [{
            "scene_id": "S01",
            "search_terms": ["old query"],
            "selected_candidate_id": candidates["scenes"][0]["selected_candidate_id"],
            "candidates": candidates["scenes"][0]["candidates"],
            "selected_asset_id": "asset-old",
        }]
    }
    (project_dir / "material_intents.json").write_text(
        __import__("json").dumps(intents, ensure_ascii=False), encoding="utf-8"
    )
    (project_dir / "director_scene_plan.json").write_text(
        __import__("json").dumps({"scenes": [{
            "id": "S01", "text": "办公室会议",
            "visual_intent": "旧视觉",
            "selected_candidate_id": "S01-C1",
        }]}, ensure_ascii=False), encoding="utf-8"
    )

    layer = Layer(type=LayerType.image, asset_id="asset-old", start=0, duration=5)
    scene = SceneIR(
        id="S01", start=0, duration=5, text="办公室会议",
        layers=[layer], visual_intent="旧视觉"
    )
    ir = CompositionIR(project=ProjectSpec(width=1080, height=1920, fps=30, duration=5), scenes=[scene])

    changed, stale = _cmd_set_scene_intent(
        ir, "S01",
        {
            "candidateId": candidate["id"],
            "visualIntent": candidate["visual_intent"],
        },
        "p1",
    )
    assert changed == ["S01"]
    assert "S01:render" in stale
    assert ir.scenes[0].visual_intent == candidate["visual_intent"]
    assert ir.scenes[0].selected_candidate_id == candidate["id"]
    assert ir.scenes[0].layers[0].asset_id == "asset-old"


def test_inspector_candidate_actions_use_existing_action_and_tools():
    inspector = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "inspector.js").read_text(encoding="utf-8")
    dispatcher = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "action-dispatcher.js").read_text(encoding="utf-8")
    assert 'data-action="creative.select_candidate"' in inspector
    assert 'data-action="asset.search_stock"' in inspector
    assert 'data-action="ui.generate_scene_visual"' in inspector
    assert "creative.select_candidate" not in dispatcher or "creative.select_candidate" in inspector


def test_action_registry_and_command_contract_are_registered():
    registry = (Path(__file__).resolve().parents[1] / "video_director" / "action_registry.py").read_text(encoding="utf-8")
    editor = (Path(__file__).resolve().parents[1] / "video_director" / "editor.py").read_text(encoding="utf-8")
    executor = (Path(__file__).resolve().parents[1] / "video_director" / "tool_executor.py").read_text(encoding="utf-8")
    manifest = (Path(__file__).resolve().parents[1] / "video_director" / "capability_registry" / "manifest.py").read_text(encoding="utf-8")
    validator = (Path(__file__).resolve().parents[1] / "video_director" / "capability_registry" / "validator.py").read_text(encoding="utf-8")
    assert "creative.select_candidate" in registry
    assert '"set_scene_intent"' in editor
    assert '"creative.candidate.select": "set_scene_intent"' in executor
    assert '"creative.candidate.select"' in manifest
    assert '"CreativeCandidateSelectRequest"' in validator


def test_committed_ir_can_sync_candidate_views_without_changing_asset():
    from video_director import config
    from video_director.editor import _sync_candidate_views
    from video_director.models import Layer, LayerType, SceneIR, CompositionIR, ProjectSpec

    import json
    monkeypatch = __import__("pytest").MonkeyPatch()
    try:
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            monkeypatch.setattr(config, "project_dir", lambda pid: root / pid)
            plans = root / "p1" / "plans"
            plans.mkdir(parents=True)
            snapshot = creative_candidates.build_candidate_snapshot([
                {"id": "S01", "text": "办公室会议"}
            ])
            selected = snapshot["scenes"][0]["candidates"][1]
            snapshot["scenes"][0]["selected_candidate_id"] = selected["id"]
            for item in snapshot["scenes"][0]["candidates"]:
                item["selected"] = item["id"] == selected["id"]
            (plans / "creative_candidates.json").write_text(
                json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
            )
            (plans / "material_intents.json").write_text(
                json.dumps({"version": 1, "scenes": [{
                    "scene_id": "S01",
                    "search_terms": ["old"],
                    "selected_candidate_id": "S01-C1",
                    "selected_asset_id": "asset-old",
                }]}, ensure_ascii=False), encoding="utf-8"
            )
            layer = Layer(type=LayerType.image, asset_id="asset-old", start=0, duration=5)
            scene = SceneIR(
                id="S01", start=0, duration=5, text="办公室会议",
                layers=[layer], visual_intent=selected["visual_intent"],
                selected_candidate_id=selected["id"],
            )
            ir = CompositionIR(
                project=ProjectSpec(width=1080, height=1920, fps=30, duration=5),
                scenes=[scene],
            )
            _sync_candidate_views("p1", ir)
            out = json.loads((plans / "creative_candidates.json").read_text(encoding="utf-8"))
            assert out["scenes"][0]["selected_candidate_id"] == selected["id"]
            assert out["scenes"][0]["candidates"][1]["selected"] is True
            intent = json.loads((plans / "material_intents.json").read_text(encoding="utf-8"))
            assert intent["scenes"][0]["selected_candidate_id"] == selected["id"]
            assert intent["scenes"][0]["search_terms"] == selected["search_terms"]
            assert ir.scenes[0].layers[0].asset_id == "asset-old"
    finally:
        monkeypatch.undo()
