"""差距 1（仓库审计）：分镜选中 generated 候选时，导演必须真正触发生成补位。

背景：此前 _tool_material 只做「已有素材匹配 + 网络素材补足」，分镜提的
generation_type=image/video 候选永远停留在 INTENT_ONLY，从不调 generation
——「分镜说应该生成什么」和「系统真的生成」之间是断的。
"""
from types import SimpleNamespace

from video_director import director_agent as agent
from video_director.models import AssetRef


def _state(tmp_path, gen_type="image"):
    return {
        "project_id": "p",
        "video_files": [],
        "image_files": [],
        "constraints": {"visual_source": "auto", "allow_stock": True},
        "narration_scenes": [{"id": "S01", "text": "咖啡特写", "start": 0.0, "duration": 1.0}],
        "scene_plan": {"scenes": [{
            "id": "S01", "selected_candidate_id": "c1",
            "candidates": [{
                "id": "c1", "approach": "concept", "visual_intent": "x",
                "search_terms": ["coffee"], "source_preference": "generated",
                "generation_type": gen_type, "generation_prompt": "一杯咖啡",
                "selected_candidate_id": "c1",
            }],
        }]},
    }


def test_missing_scene_with_generated_candidate_triggers_generation(tmp_path, monkeypatch):
    png = tmp_path / "gen.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    calls = {}

    def fake_build(videos, images, asset_metadata=None):
        if images:
            return ({"genid": AssetRef(id="genid", type="image", file=str(images[0]),
                                       width=100, height=100)}, ["genid"])
        return ({}, [])

    monkeypatch.setattr(agent.assets_mod, "build_manifest", fake_build)
    monkeypatch.setattr(agent.matcher, "match_assets",
                        lambda *a, **k: {})  # 恒无匹配 → S01 一直 missing

    def fake_gen(project_id, prompt, gen_type, width=1080, height=1920, variant=None, **kw):
        calls["gen_type"] = gen_type
        calls["prompt"] = prompt
        return [SimpleNamespace(asset_id="any", file=str(png), type="image",
                                width=100, height=100)]

    monkeypatch.setattr("video_director.generation.generate", fake_gen)

    state = _state(tmp_path)
    out = agent._tool_material({"match_materials_to_script": True,
                                "width": 100, "height": 100}, state)

    assert out.get("ok") is True, out
    assert calls.get("gen_type") == "image", "generation 未被触发"
    assert calls.get("prompt") == "一杯咖啡"
    assert state["assignments"]["S01"] == "genid", "生成物未回填 assignment"
    assert state["assets"]["genid"].file == str(png)


def test_generation_failure_degrades_honestly_without_throw(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.assets_mod, "build_manifest",
                        lambda v, img, asset_metadata=None: ({}, []))
    monkeypatch.setattr(agent.matcher, "match_assets", lambda *a, **k: {})

    def fake_gen_fail(*a, **kw):
        raise RuntimeError("生图 API 挂")

    monkeypatch.setattr("video_director.generation.generate", fake_gen_fail)

    state = _state(tmp_path)
    out = agent._tool_material({"match_materials_to_script": True,
                                "width": 100, "height": 100}, state)

    # 全失败必须诚实标记 NEEDS_HUMAN：不抛异常，不假装有素材
    assert "S01" not in state.get("assignments", {})
    cand = state["scene_plan"]["scenes"][0]["candidates"][0]
    assert cand["status"] == "NEEDS_HUMAN"
    assert any("NEEDS_HUMAN" in w for w in out.get("result", {}).get("warnings", []))


def test_video_route_fails_then_falls_back_to_image(tmp_path, monkeypatch):
    """差距 2：首选 video 失败 → 自动降级 image/graphic，不是重试到死。"""
    png = tmp_path / "gen.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    def fake_build(videos, images, asset_metadata=None):
        if images:
            return ({"genid": AssetRef(id="genid", type="image", file=str(images[0]),
                                       width=100, height=100)}, ["genid"])
        return ({}, [])

    monkeypatch.setattr(agent.assets_mod, "build_manifest", fake_build)
    monkeypatch.setattr(agent.matcher, "match_assets", lambda *a, **k: {})

    tried = []

    def fake_gen(project_id, prompt, gen_type, width=1080, height=1920, variant=None, **kw):
        tried.append(gen_type)
        if gen_type == "video":
            raise RuntimeError("VIDEO_GENERATION_FAILED: 视频 API 挂")
        return [SimpleNamespace(asset_id="any", file=str(png), type="image",
                                width=100, height=100)]

    monkeypatch.setattr("video_director.generation.generate", fake_gen)

    state = _state(tmp_path, gen_type="video")
    out = agent._tool_material({"match_materials_to_script": True,
                                "width": 100, "height": 100}, state)

    assert "video" in tried, "首选 video 路线未尝试"
    assert "image" in tried, "video 失败后未降级 image"
    assert state["assignments"]["S01"] == "genid"
    assert out.get("ok") is True


def test_non_generated_candidate_never_triggers_generation(tmp_path, monkeypatch):
    """候选 generation_type 为空（直叙/细节补画面）绝不触发生成。"""
    calls = {"n": 0}
    monkeypatch.setattr(agent.assets_mod, "build_manifest",
                        lambda v, img, asset_metadata=None: ({}, []))
    monkeypatch.setattr(agent.matcher, "match_assets", lambda *a, **k: {})

    def fake_gen(*a, **kw):
        calls["n"] += 1
        return []

    monkeypatch.setattr("video_director.generation.generate", fake_gen)

    state = _state(tmp_path)
    # 把候选 generation_type 抹掉（直叙候选："" 不生成）
    state["scene_plan"]["scenes"][0]["candidates"][0]["generation_type"] = ""
    agent._tool_material({"match_materials_to_script": True,
                          "width": 100, "height": 100}, state)
    assert calls["n"] == 0