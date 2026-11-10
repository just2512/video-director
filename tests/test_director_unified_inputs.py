import json
from pathlib import Path

import video_director.config as config
import video_director.director_inputs as di
import video_director.workflow_run as workflow_run


def test_unified_bundle_keeps_script_assets_reference_and_repo(monkeypatch):
    monkeypatch.setattr(
        di.asset_catalog,
        "get_asset",
        lambda asset_id: {
            "asset_id": asset_id,
            "type": "video",
            "source": "user",
            "file": f"/tmp/{asset_id}.mp4",
            "description": "我的素材",
            "commercial_status": "USER_SUPPLIED",
            "license_note": "",
        },
    )
    spec = {
        "brief": "用我的素材介绍这个软件，并参考样片节奏",
        "script": "这是我自己写的文案。",
        "video_paths": [],
        "image_paths": [],
        "director_inputs": {
            "brief": "用我的素材介绍这个软件，并参考样片节奏",
            "script": {
                "text": "这是我自己写的文案。",
                "source": "user",
                "authority": "authoritative",
            },
            "assets": {
                "asset_refs": [{"asset_id": "local-001"}],
                "policy": "user_first",
            },
            "references": [{
                "id": "ref-001",
                "project_id": "ref-project",
                "report": {
                    "visual_language": {
                        "pace": "fast",
                        "density": "high",
                        "motion": "pan",
                        "transition": "cut",
                    },
                    "shot_grammar": [{"duration": 1.2, "role": "demo", "motion": "pan"}],
                },
            }],
            "software_project": {
                "id": "repo-001",
                "root": "/tmp/my-app",
                "evidence_graph": {
                    "claims": [
                        {"id": "claim-1", "text": "支持批量导出", "status": "VERIFIED",
                         "evidence_refs": ["ev-1"]},
                        {"id": "claim-2", "text": "未经证实的功能", "status": "UNVERIFIED",
                         "evidence_refs": []},
                    ],
                    "evidence": [
                        {"id": "ev-1", "source_path": "README.md",
                         "evidence_type": "document"},
                    ],
                },
            },
            "constraints": {"visual_source": "auto"},
        },
    }

    bundle = di.build_bundle(spec)

    assert bundle["script"]["text"] == "这是我自己写的文案。"
    assert bundle["script"]["authority"] == "authoritative"
    assert bundle["assets"]["asset_refs"][0]["asset_id"] == "local-001"
    assert bundle["references"][0]["analysis"]["visual_language"]["pace"] == "fast"
    assert bundle["software_project"]["evidence_graph"]["claims"] == [
        {"id": "claim-1", "text": "支持批量导出", "evidence_refs": ["ev-1"]}
    ]


def test_director_plan_declares_all_node_io_and_sources(monkeypatch):
    bundle = {
        "version": di.VERSION,
        "brief": "做软件宣传视频",
        "script": {"text": "文案", "source": "user", "authority": "authoritative"},
        "assets": {"asset_refs": [{"asset_id": "asset-1"}]},
        "references": [{"id": "ref-1", "analysis": {}}],
        "software_project": {"id": "repo-1", "evidence_graph": {"claims": []}},
        "constraints": {},
    }

    plan = di.build_plan(bundle, "做软件宣传视频")

    assert set(plan["source_refs"]) == {
        "director:brief", "script:user", "asset_pool:user",
        "reference:style", "software_project:evidence",
    }
    assert len(plan["nodes"]) == 12
    for node in plan["nodes"]:
        assert node["inputs"]
        assert node["outputs"]
        assert "tool_calls" in node
        assert "verify" in node
        assert node["status"] == "PENDING"


def test_execution_brief_uses_reference_style_and_verified_project_claims():
    bundle = {
        "brief": "介绍我的软件",
        "script": {"text": "文案", "source": "user", "authority": "authoritative"},
        "assets": {"asset_refs": [{"asset_id": "asset-1", "description": "操作界面"}]},
        "references": [{
            "id": "ref-1",
            "analysis": {
                "visual_language": {
                    "pace": "fast", "density": "high",
                    "motion": "push", "transition": "cut",
                },
                "shot_grammar": [{"duration": 1.5}],
            },
        }],
        "software_project": {
            "id": "repo-1",
            "evidence_graph": {
                "claims": [{"text": "支持批量导出"}],
            },
        },
        "constraints": {"visual_source": "auto"},
        "creative_settings": {"platform": "douyin", "duration": "45", "style": "克制"},
    }
    brief = di.build_execution_brief(bundle)

    assert "用户素材池" in brief
    assert "参考视频风格约束" in brief
    assert "节奏=fast" in brief
    assert "软件项目 VERIFIED 卖点" in brief
    assert "支持批量导出" in brief


def test_create_ui_no_longer_splits_creation_into_parallel_modes():
    path = Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "create.js"
    text = path.read_text(encoding="utf-8")
    assert "const directorInputs =" in text
    assert "await apiPost('/api/director/create'" in text
    assert "_cfReferenceBrief" not in text
    assert "_cfRepoBrief" not in text
    assert "briefParts" not in text
    # There must be no fallback from Create to the old generic workflow endpoint.
    assert "await apiPost('/api/workflows', {kind: 'compose'" not in text


def test_create_ui_accepts_reference_or_software_project_without_script_or_asset():
    path = Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "create.js"
    text = path.read_text(encoding="utf-8")
    assert "const hasReference = !!window._cfReference;" in text
    assert "const hasArticle = !!window._cfArticle;" in text
    assert "const hasSoftwareProject = !!window._cfRepo;" in text
    assert "const hasDirectorSource = !!(brief.trim() || script.trim() || hasFiles || hasReference || hasArticle || hasSoftwareProject);" in text


def test_agent_loop_never_calls_compose_auto(monkeypatch, tmp_path):
    from video_director import director_agent as agent, director_inputs as di

    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path / "composer")
    monkeypatch.setattr(di, "attach_library_assets", lambda project_id, bundle: ([], []))
    monkeypatch.setattr(di, "materialize_reference", lambda project_id, bundle: [])
    monkeypatch.setattr(di, "materialize_software_project_artifacts", lambda project_id, bundle: [])
    monkeypatch.setattr(agent, "_choose_next", lambda plan, current, result, retries:
                        ((next(n["id"] for n in plan["nodes"] if n["status"] == "PENDING"
                               and all(next(x for x in plan["nodes"] if x["id"] == d)["status"] in ("DONE", "SKIPPED")
                                       for d in n.get("dependencies", []))),
                          "deterministic test decision")))

    def fail_compose(*args, **kwargs):
        raise AssertionError("Director creation must not call pipeline.compose_auto/_execute_compose")

    monkeypatch.setattr(workflow_run, "_execute_compose", fail_compose)

    def fake_tool(spec, state):
        if state["bundle"]["script"].get("text") == "":
            state["bundle"]["script"]["text"] = "测试文案"
        return {
            "ok": True, "tool": "test.tool", "status": "APPLIED",
            "result": {}, "verify": {"ok": True, "checks": []},
        }

    for node_id in ("script", "scene_plan", "narration", "material_match",
                    "subtitle", "music", "composition", "watermark",
                    "preview", "verify", "render"):
        monkeypatch.setitem(agent._TOOLS, node_id, fake_tool)

    bundle = {
        "version": di.VERSION,
        "brief": "测试 Director",
        "script": {"text": "测试文案", "source": "user", "authority": "authoritative"},
        "assets": {"asset_refs": []},
        "references": [],
        "software_project": {},
        "constraints": {"visual_source": "auto", "allow_stock": False},
        "creative_settings": {},
    }
    plan = di.build_plan(bundle, "测试 Director")
    out = agent.execute(
        {"project_id": "agent-loop-test", "director_inputs": bundle,
         "script": "测试文案", "confirm_mode": "direct"},
        "run-test",
        plan,
        update_node=workflow_run._director_update_node,
        emit=lambda *args, **kwargs: None,
    )

    assert out["director_verify_ok"] is True
    assert [n["id"] for n in plan["nodes"] if n["status"] == "DONE"] == [
        "understand", "script", "scene_plan", "narration", "material_match",
        "subtitle", "music", "composition", "watermark", "preview",
        "verify", "render",
    ]
    for node in plan["nodes"]:
        assert node["tool_calls"], node["id"]
        assert node["verify"], node["id"]


def test_workflow_director_wrapper_never_falls_back_to_compose(monkeypatch, tmp_path):
    from video_director import workflow_run, director_agent, director_inputs as di

    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path / "composer")
    pid = "wrapper-agent-test"
    bundle = {
        "version": di.VERSION,
        "brief": "测试",
        "script": {"text": "测试文案", "source": "user", "authority": "authoritative"},
        "assets": {"asset_refs": []},
        "references": [],
        "software_project": {},
        "constraints": {},
        "creative_settings": {},
    }
    plan = di.build_plan(bundle, "测试")

    def fail_compose(*args, **kwargs):
        raise AssertionError("_execute_compose must not run for Director creation")

    seen = {}
    monkeypatch.setattr(workflow_run, "_execute_compose", fail_compose)

    def fake_agent(spec, run_id, plan, update_node, emit):
        seen["plan"] = plan
        return {"ok": True, "project_id": pid, "pending_confirm": True,
                "director_plan": plan, "director_verify_ok": True}

    monkeypatch.setattr(director_agent, "execute", fake_agent)
    out = workflow_run._execute_director_create(
        {"project_id": pid, "director_inputs": bundle, "script": "测试文案"},
        "run-wrapper",
    )
    assert out["project_id"] == pid
    assert seen["plan"]["source_refs"] == ["director:brief", "script:user"]


def test_agent_next_decision_reads_previous_tool_result(monkeypatch):
    from video_director import director_agent as agent
    seen = {}

    class Provider:
        def generate(self, prompt, system=None):
            seen["prompt"] = prompt
            return '{"next":"subtitle","reason":"根据上一节点结果继续"}'

    import video_director.llm_provider as lp
    monkeypatch.setattr(lp, "get_llm", lambda: Provider())

    plan = {
        "nodes": [
            {"id": "narration", "status": "DONE", "dependencies": []},
            {"id": "subtitle", "status": "PENDING", "dependencies": ["narration"]},
            {"id": "music", "status": "PENDING", "dependencies": ["narration"]},
        ]
    }
    choice, reason = agent._choose_next(
        plan,
        "narration",
        {"ok": True, "tool": "narration.synthesize",
         "status": "APPLIED", "result": {"audio": "/tmp/narration.mp3"}},
        0,
    )
    assert choice == "subtitle"
    assert "narration.synthesize" in seen["prompt"]
    assert "/tmp/narration.mp3" in seen["prompt"]
    assert "根据上一节点结果继续" in reason


def test_agent_preserves_existing_narration_upload_mode(monkeypatch, tmp_path):
    from video_director import director_agent as agent

    calls = {}

    def fake_import(project_id, path, scene_texts=None):
        calls["args"] = (project_id, path, scene_texts)
        p = tmp_path / "uploaded.mp3"
        p.write_bytes(b"audio")
        return {
            "narration_file": str(p),
            "word_timeline": [{"text": "测试", "start": 0.0, "end": 0.5}],
            "narration_scenes": [{"id": "S01", "text": "测试", "start": 0.0, "duration": 0.5}],
        }

    import video_director.voice_upload as vu
    monkeypatch.setattr(vu, "import_uploaded_voice", fake_import)
    state = {"project_id": "p", "scene_texts": ["测试"]}
    out = agent._tool_narration(
        {"voice_mode": "upload", "narration_upload": "/tmp/staged.mp3"},
        state,
    )
    assert out["ok"] is True
    assert out["tool"] == "narration.upload"
    assert calls["args"][1] == "/tmp/staged.mp3"
    assert state["narration_file"].endswith("uploaded.mp3")


def test_agent_preserves_online_visual_source(monkeypatch, tmp_path):
    from video_director import director_agent as agent
    from video_director.models import AssetRef

    local_video = tmp_path / "local.mp4"
    local_video.write_bytes(b"local")
    stock_video = tmp_path / "stock.mp4"
    stock_video.write_bytes(b"stock")

    monkeypatch.setattr(agent.assets_mod, "build_manifest", lambda videos, images, asset_metadata=None: (
        {"v": AssetRef(id="v", type="video", file=str(videos[0]), duration=1.0,
                       width=100, height=100)} if videos else {},
        ["v"] if videos else [],
    ))
    monkeypatch.setattr(agent.matcher, "match_assets", lambda *args, **kwargs:
                        {"S01": "v"})
    class StockResult:
        def get(self, key, default=None):
            return {
                "files": [str(stock_video)],
                "metadata": {},
                "warnings": [],
            }.get(key, default)
    import video_director.stock as stock
    monkeypatch.setattr(stock, "auto_download_for_scenes", lambda *args, **kwargs: StockResult())

    state = {
        "project_id": "p",
        "video_files": [str(local_video)],
        "image_files": [],
        "constraints": {"visual_source": "online", "allow_stock": True},
        "narration_scenes": [{"id": "S01", "text": "测试", "start": 0.0, "duration": 1.0}],
    }
    out = agent._tool_material({"match_materials_to_script": True, "width": 100, "height": 100}, state)
    assert out["ok"] is True
    assert state["video_files"] == [str(stock_video)]



def test_create_form_tracks_explicit_fields_without_nonexistent_dom_guard():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "create.js").read_text(encoding="utf-8")
    assert "function cfMarkExplicitControl(event)" in source
    assert "if (CF.state !== 'editing') return;" in source
    # Modal focus management legitimately queries #cf-panel in its own helpers.
    # The explicit-field tracker must remain independent of that modal DOM.
    tracker = source.split("function cfMarkExplicitControl(event)", 1)[1].split(
        "document.addEventListener('input', cfMarkExplicitControl", 1
    )[0]
    assert "getElementById('cf-panel')" not in tracker
    assert "_explicit_fields: Array.from(CF.explicitFields || [])" in source
    for control in (
        "cf-platform", "cf-resolution", "cf-duration", "cf-voice-rate",
        "cf-subtitle", "cf-bgm", "cf-watermark", "cf-lower-name",
    ):
        assert f'id="{control}"' in source
        assert f"'{control}':" in source
