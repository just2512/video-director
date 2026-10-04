import json
from pathlib import Path

import video_director.director_inputs as di


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
