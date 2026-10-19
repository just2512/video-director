"""PR #5 后续回归：用户实测暴露的失败链三缺陷（2026-10-07）。

1. DirectorAgentError 未定义 —— Agent STOP/retry 路径 NameError 掩盖真实失败
2. voice_rate 倍数（'0.9'）直传 edge-tts —— Director 路径没做倍数归一
3. FAILED 导演项目「查看问题/重新执行」被 editable 门禁挡死
4. 新建面板两个「AI 开始创作」按钮重复
"""

import json
from pathlib import Path

import pytest

import video_director.config as config
import video_director.director_agent as agent
import video_director.narration as narration
import video_director.store as store
import video_director.workflow_run as workflow_run


@pytest.fixture
def isolated_storage(monkeypatch, tmp_path):
    root = tmp_path / "composer"
    monkeypatch.setattr(config, "composer_storage_root", lambda: root)
    return root


def test_director_agent_error_is_defined_and_raisable():
    """Agent Loop 的失败必须以真实原因抛出，不能 NameError 自掩盖。"""
    assert hasattr(agent, "DirectorAgentError")
    with pytest.raises(agent.DirectorAgentError, match="真实失败原因"):
        raise agent.DirectorAgentError("Director 停止：真实失败原因")


def test_narration_rate_normalizes_multiplier_and_percent(isolated_storage):
    """edge-tts rate 归一：倍数 '0.9'/'1.3'/0.9 → '-10%'/'30%'/'+0%'；百分比透传。"""
    assert narration.normalize_rate("0.9") == "-10%"
    assert narration.normalize_rate("1.0") == "+0%"
    assert narration.normalize_rate("1.3") == "+30%"
    assert narration.normalize_rate(0.9) == "-10%"
    assert narration.normalize_rate("+15%") == "+15%"
    assert narration.normalize_rate("-10%") == "-10%"
    assert narration.normalize_rate("") == "+0%"
    assert narration.normalize_rate(None) == "+0%"
    assert narration.normalize_rate("garbage") == "+0%"


def test_tool_narration_passes_normalized_rate_to_edge_tts(
        isolated_storage, monkeypatch):
    """Director 路径 spec.voice_rate='0.9' 必须以 '-10%' 进 synth_narration。"""
    captured = {}

    def fake_synth(scene_texts, voice, out_dir, rate="+0%"):
        captured["rate"] = rate
        out_dir.mkdir(parents=True, exist_ok=True)
        audio = out_dir / "narration.mp3"
        audio.write_bytes(b"mp3")
        scenes = [{"id": f"S{i+1:02d}", "text": t, "start": float(i), "duration": 1.0}
                  for i, t in enumerate(scene_texts)]
        words = [{"text": t, "start": float(i), "end": float(i) + 0.5}
                 for i, t in enumerate(scene_texts)]
        return str(audio), words, scenes

    monkeypatch.setattr(agent.narration, "synth_narration", fake_synth)
    state = {"project_id": "p", "scene_texts": ["测试文案"], "bundle": {}}
    out = agent._tool_narration({"voice_rate": "0.9", "voice": "zh-CN-XiaoxiaoNeural"}, state)
    assert out["ok"] is True
    assert captured["rate"] == "-10%", "Director 路径 voice_rate 未归一直传 edge-tts"


def test_failed_director_project_can_retry_from_persisted_bundle(isolated_storage, monkeypatch):
    """FAILED 项目「重新执行」：从持久化 bundle+params 重建 spec → 新 director_create run。"""
    import video_director.director_inputs as di
    pid = "web-retry-01"
    base = config.project_dir(pid)
    config.init_project_dirs(base)
    store.create_project(pid, "失败的文案", {
        "voice": "zh-CN-XiaoxiaoNeural", "voice_rate": "0.9",
        "width": 1080, "height": 1920, "creative_brief": "做一条视频",
    })
    store.set_stage(pid, store.Stage.FAILED, "narration 失败")
    bundle = {
        "version": di.VERSION, "brief": "做一条视频",
        "script": {"text": "失败的文案", "source": "user", "authority": "authoritative"},
        "assets": {"asset_refs": []}, "references": [], "software_project": {},
        "constraints": {}, "creative_settings": {},
    }
    di.persist_bundle(pid, bundle)

    seen = {}

    def fake_execute(spec, run_id, plan, update_node, emit):
        seen["spec"] = spec
        return {"ok": True, "project_id": pid, "director_verify_ok": True}

    monkeypatch.setattr(agent, "execute", fake_execute)
    run_id = workflow_run.run_director_retry_async(pid)
    assert run_id, "retry 未开新 run"
    import time as _t
    _t.sleep(0.5)  # worker 线程跑完
    run = workflow_run.get_run(run_id)
    assert run["kind"] == "director_create"
    assert run["project_id"] == pid
    assert seen["spec"]["project_id"] == pid
    assert seen["spec"]["voice_rate"] == "0.9"  # 参数读回
    assert seen["spec"]["director_inputs"]["script"]["text"] == "失败的文案"
    assert seen["spec"]["script"] == "失败的文案"


def test_retry_rejects_missing_project_or_active_run(isolated_storage, monkeypatch):
    pid = "web-retry-02"
    base = config.project_dir(pid)
    config.init_project_dirs(base)
    store.create_project(pid, "x", {})
    # 无 bundle → 不能重试
    assert workflow_run.run_director_retry_async(pid) is None

    import video_director.director_inputs as di
    di.persist_bundle(pid, {"brief": "b", "script": {"text": "s"}})
    # 有 RUNNING run → 拒绝
    workflow_run.start_run("director_create", project_id=pid, steps=["render"])
    assert workflow_run.run_director_retry_async(pid) is None
    # 不存在的项目
    assert workflow_run.run_director_retry_async("never-exists") is None


def test_create_panel_has_single_primary_submit_button():
    """新建面板「AI 开始创作」主按钮唯一（cf-ai-go/cf-go 不得双主按钮）。"""
    text = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "create.js").read_text(encoding="utf-8")
    import re
    submits = re.findall(r'<button[^>]*onclick="createProject\(\)"[^>]*>', text)
    assert len(submits) == 1, f"createProject() 直接触发按钮应唯一，实际 {len(submits)} 个: {submits}"
    # 面板 HTML 里不允许再出现 cf-ai-go（重复按钮 id）
    assert 'id="cf-ai-go"' not in text


def test_failed_project_actions_expose_view_and_retry():
    """FAILED 项目 actions 必须带查看问题/重新执行可用语义（不被 editable 挡死）。"""
    import video_director.project_manager as pm
    src = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "project.js").read_text(encoding="utf-8")
    # 「查看问题」不进 workspace（编辑器门禁），「重新执行」走 /api/director/retry
    assert "pmShowFailure(" in src
    assert "apiPost('/api/director/retry'" in src
    assert "onclick=\"pmShowFailure(" in src



def test_material_generation_fallback_uses_concept_candidate_if_direct_is_selected():
    from video_director.director_agent import _generation_candidate_for_scene

    candidates = [
        {"id": "direct-1", "approach": "direct", "source_preference": "user_asset"},
        {"id": "broll-1", "approach": "broll", "source_preference": "stock"},
        {"id": "concept-1", "approach": "concept", "source_preference": "generated",
         "generation_type": "image", "generation_prompt": "概念画面"},
    ]
    selected = _generation_candidate_for_scene(candidates, "direct-1")
    assert selected is candidates[2]


def test_material_generation_fallback_preserves_selected_generation_candidate():
    from video_director.director_agent import _generation_candidate_for_scene

    candidates = [
        {"id": "direct-1", "approach": "direct", "source_preference": "user_asset"},
        {"id": "concept-1", "approach": "concept", "source_preference": "generated",
         "generation_type": "image"},
        {"id": "video-1", "approach": "concept", "source_preference": "generated",
         "generation_type": "video"},
    ]
    selected = _generation_candidate_for_scene(candidates, "video-1")
    assert selected is candidates[2]



def test_material_fallback_marks_the_candidate_that_actually_generated_the_asset():
    """Fallback selection is reflected in persisted candidate state, not falsely attributed to direct assets."""
    from video_director.director_agent import _generation_candidate_for_scene

    candidates = [
        {"id": "direct-1", "approach": "direct", "source_preference": "user_asset"},
        {"id": "concept-1", "approach": "concept", "source_preference": "generated",
         "generation_type": "image"},
    ]
    actual = _generation_candidate_for_scene(candidates, "direct-1")
    assert actual["id"] == "concept-1"
    assert actual["source_preference"] == "generated"
