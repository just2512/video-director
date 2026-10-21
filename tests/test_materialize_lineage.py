"""差距 8（仓库审计）：materialize 生成素材 100% 丢失 generation_prompt（血缘断裂）。

背景：_tool_material 的生成补位块把候选的 generation_prompt 传给
generation.generate 之后，建 AssetRef 时只带 id/type/file/source/license_note/
rights_status，prompt 字段一直是 None。而项目 manifest 是 AssetRef.model_dump()
逐行落盘的（director_agent 末段 _json_file），所以「这个画面凭什么生成」在
manifest 里无据可查，「按原 prompt 重生成」也拿不回原始提示词。

修复：生成补位把实际采用的 prompt 一路带进 AssetRef.prompt 与 manifest 行。
"""
from types import SimpleNamespace

import pytest

from video_director import director_agent as agent


def _state(gen_prompt="一杯手冲咖啡的特写，清晨窗光"):
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
                "generation_type": "image",
                "generation_prompt": gen_prompt,
                "selected_candidate_id": "c1",
            }],
        }]},
    }


def _fake_build():
    return lambda videos, images, asset_metadata=None: ({}, [])


def test_generated_asset_ref_carries_generation_prompt(tmp_path, monkeypatch):
    """修复主断言：生成补位后 AssetRef.prompt == 候选 generation_prompt。"""
    png = tmp_path / "gen.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    monkeypatch.setattr(agent.assets_mod, "build_manifest", _fake_build())
    monkeypatch.setattr(agent.matcher, "match_assets", lambda *a, **k: {})

    def fake_gen(project_id, prompt, gen_type, width=1080, height=1920, variant=None, **kw):
        return [SimpleNamespace(asset_id="any", file=str(png), type="image",
                                width=100, height=100)]

    monkeypatch.setattr("video_director.generation.generate", fake_gen)

    state = _state()
    out = agent._tool_material({"match_materials_to_script": True,
                               "width": 100, "height": 100}, state)

    assert out.get("ok") is True, out
    ref = state["assets"]["gen-s01-image"]
    # 血缘：AssetRef 上必须能看到原始 prompt，否则 manifest/model_dump 必然丢
    assert ref.prompt == "一杯手冲咖啡的特写，清晨窗光", (
        f"generation_prompt 未透传进 AssetRef: {ref.prompt!r}")
    # 项目 manifest 行就是 AssetRef.model_dump()：确认 prompt 会出现在行里
    row = ref.model_dump()
    assert row["prompt"] == "一杯手冲咖啡的特写，清晨窗光"
    assert row["id"] == "gen-s01-image" and row["source"] == "generated"


def test_generated_asset_prompt_falls_back_to_scene_text(tmp_path, monkeypatch):
    """候选无 generation_prompt → 用旁白文本兜底（血缘不丢，不是 None）。"""
    png = tmp_path / "gen.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    monkeypatch.setattr(agent.assets_mod, "build_manifest", _fake_build())
    monkeypatch.setattr(agent.matcher, "match_assets", lambda *a, **k: {})

    seen = {}

    def fake_gen(project_id, prompt, gen_type, width=1080, height=1920, variant=None, **kw):
        seen["prompt"] = prompt
        return [SimpleNamespace(asset_id="any", file=str(png), type="image",
                                width=100, height=100)]

    monkeypatch.setattr("video_director.generation.generate", fake_gen)

    # 候选仍在选中位（sel_id 匹配得上），只是 generation_prompt 为空串
    state = _state(gen_prompt="")
    out = agent._tool_material({"match_materials_to_script": True,
                               "width": 100, "height": 100}, state)

    assert out.get("ok") is True, out
    assert seen["prompt"] == "咖啡特写", f"应回落旁白文本，实得 {seen.get('prompt')!r}"
    assert state["assets"]["gen-s01-image"].prompt == "咖啡特写"


def test_generated_tuple_unpack_keeps_existing_consumers_working(tmp_path, monkeypatch):
    """回归锁：generated_files 3→4 元组后，回填/视觉审核消费点不得崩。"""
    png = tmp_path / "gen.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    monkeypatch.setattr(agent.assets_mod, "build_manifest", _fake_build())
    monkeypatch.setattr(agent.matcher, "match_assets", lambda *a, **k: {})
    monkeypatch.setattr("video_director.generation.generate",
                        lambda *a, **k: [SimpleNamespace(
                            asset_id="any", file=str(png), type="image",
                            width=100, height=100)])
    # 视觉审核段也会遍历 generated_files：把它打成不可用，走诚实释放路径
    def _boom(*a, **k):
        raise RuntimeError("vision 挂")

    monkeypatch.setattr("video_director.visual_qa.frame_vision_visual_qa", _boom)

    state = _state()
    out = agent._tool_material({"match_materials_to_script": True,
                               "width": 100, "height": 100}, state)

    assert out.get("ok") is True, out
    assert state["assignments"]["S01"] == "gen-s01-image"
    # 视觉审核不可用必须诚实记录，且不让整条链崩
    assert any("视觉审核不可用" in w for w in out.get("result", {}).get("warnings", []))


# ---------------------------------------------------------------------------
# 差距 9（仓库审计）：voice.change 下拉只试听无提交（选择静默丢失）。
# 根因不是缺按钮：voice.change 的 capability 误指只读的 tts.list_voices，
# 前端「应用音色」会拿到音色列表并返回 ok=True，音色却一个字节都没落盘——
# 比静默丢失更糟的假成功。修法=新增写能力 tts.set_voice 并改指，前端补按钮。
# ---------------------------------------------------------------------------

@pytest.fixture
def voice_project(monkeypatch, tmp_path):
    """隔离 DB 与项目根目录的真实项目（音色落盘要看 SQLite）。"""
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "root"))
    from video_director import store
    store.init_db()
    store.create_project("voice-test", "", {"title": "保留我"})
    return store


def test_set_voice_tool_writes_project_params(voice_project):
    """tts.set_voice 必须把 voice 写进 params_json，且不动其他键。"""
    from video_director import tool_executor as te
    r = te.execute_tool("voice-test", {"tool": "tts.set_voice",
                                       "payload": {"voiceId": "zh-CN-YunxiNeural"}})
    assert r["ok"] is True and r["status"] == "OK", r
    params = voice_project.read_project_params("voice-test")
    assert params["voice"] == "zh-CN-YunxiNeural"
    # 读-改-写不能把同项目的其他 params 清掉
    assert params["title"] == "保留我", "其他 params 被覆写丢失"


def test_set_voice_rejects_unknown_voice(voice_project):
    """假 voice id 必须拒绝，不能把垃圾值写进 params（渲染期才炸更难查）。"""
    from video_director import tool_executor as te
    r = te.execute_tool("voice-test", {"tool": "tts.set_voice",
                                       "payload": {"voiceId": "no-such-voice"}})
    assert r["ok"] is False and r["status"] == "VOICE_NOT_FOUND", r
    assert "voice" not in voice_project.read_project_params("voice-test")


def test_set_voice_requires_voice_id(voice_project):
    from video_director import tool_executor as te
    r = te.execute_tool("voice-test", {"tool": "tts.set_voice", "payload": {}})
    assert r["ok"] is False and r["status"] == "MISSING_REQUIREMENT", r


def test_voice_change_action_persists_voice(voice_project):
    """回归锁：voice.change 走 Action 通路必须真落盘，不再假成功。

    此前 capability=tts.list_voices（只读）→ ok=True 但 params_json 全空。
    """
    from video_director.action_service import execute_action_request
    r = execute_action_request({
        "project_id": "voice-test", "action": "voice.change",
        "target": "project", "params": {"voice_id": "zh-CN-YunjianNeural"},
    })
    assert r["ok"] is True, r
    assert r["capability"] == "tts.set_voice", "capability 仍指向只读能力"
    # result 必须是写入结果，不是音色列表（列表=假成功的旧症状）
    assert r["result"].get("voice") == "zh-CN-YunjianNeural"
    assert "voices" not in r["result"], "仍返回音色列表=未真正写入"
    assert voice_project.read_project_params("voice-test")["voice"] == "zh-CN-YunjianNeural"


def test_voice_change_capability_is_not_readonly():
    """契约：写语义动作的 capability 不得落在只读集合里。"""
    from video_director import action_registry as ar
    from video_director import tool_executor as te
    spec = ar.get_action("voice.change")
    assert spec is not None
    assert spec.capability == "tts.set_voice"
    assert spec.capability not in te._READONLY, "写动作被当成只读 tool"
    assert te._is_tts_voice(spec.capability), "写动作没路由到 _execute_tts_voice"


def test_voice_change_alias_voice_param_also_persists(voice_project):
    """action_registry 的 params["voice"] → payload["voice_id"] 适配仍生效。"""
    from video_director.action_service import execute_action_request
    r = execute_action_request({
        "project_id": "voice-test", "action": "voice.change",
        "target": "project", "params": {"voice": "zh-CN-YunxiaNeural"},
    })
    assert r["ok"] is True, r
    assert voice_project.read_project_params("voice-test")["voice"] == "zh-CN-YunxiaNeural"


def test_set_voice_capability_registered_as_command():
    """新能力必须在清单里，且 kind=command（非只读 query）。"""
    from video_director.capability_registry import loader
    cap = loader.get_capability("tts.set_voice")
    assert cap is not None, "tts.set_voice 未注册到 capability manifest"
    assert cap.kind == "command", f"kind={cap.kind}，应为 command"
    assert "voiceId" in cap.inputSchema or cap.inputSchema, cap.inputSchema

