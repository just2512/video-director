"""AI Director first-class regression tests.

Focus:
- natural-language targets
- real content/music execution routes
- generated visual adoption
- persistent Director plan
- fake workflow controls do not leak into AI context
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from video_director import config, director, editor, tool_executor, workflow_run


@pytest.fixture()
def pid(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path / "composer")
    project_id = "ai-dir-reg-01"
    base = config.project_dir(project_id)
    config.init_project_dirs(base)
    (base / "plans" / "composition.json").write_text(json.dumps({
        "project": {"title": "AI Director", "width": 1080, "height": 1920,
                    "fps": 30, "duration": 6.0},
        "scenes": [
            {"id": "scene-1", "start": 0.0, "duration": 3.0, "text": "第一段",
             "layers": [{"type": "image", "start": 0.0, "duration": 3.0,
                         "asset_id": "a1"}]},
            {"id": "scene-2", "start": 3.0, "duration": 3.0, "text": "第二段",
             "layers": [{"type": "image", "start": 0.0, "duration": 3.0,
                         "asset_id": "a2"}]},
        ],
        "audio": [], "watermarks": [],
    }), encoding="utf-8")
    (base / "assets" / "manifest.json").write_text("[]", encoding="utf-8")
    return project_id


def test_natural_scene_targets(pid):
    assert director._resolve_natural_target(pid, "第二段") == "scene-2"
    assert director._resolve_natural_target(pid, "Scene 02") == "scene-2"
    assert director._resolve_natural_target(pid, "最后一段") == "scene-2"
    assert director._resolve_natural_target(pid, "开头") == "scene-1"


def test_director_context_contains_timeline_and_hides_fake_workflow_actions(pid):
    ctx = director.build_contexts(pid)
    assert "timeline" in ctx
    assert [s["id"] for s in ctx["timeline"]["scenes"]] == ["scene-1", "scene-2"]
    action_ids = {a["id"] for a in ctx["actions"]["actions"]}
    assert "content.generate_script" in action_ids
    assert "workflow.start" not in action_ids
    assert "workflow.resume" not in action_ids
    assert "delivery.generate_metadata" not in action_ids


def test_content_generation_is_real_executor(monkeypatch, pid):
    class FakeProvider:
        def generate(self, prompt, system=""):
            return "这是可直接配音的文案。"

    import video_director.llm_provider as lp
    monkeypatch.setattr(lp, "get_llm", lambda: FakeProvider())
    r = tool_executor.execute_tool(pid, {
        "tool": "content.generate_script",
        "payload": {"topic": "为什么越忙越没钱", "platform": "douyin"},
    })
    assert r["ok"] is True
    assert r["result"]["artifactType"] == "ScriptArtifact"
    assert r["result"]["script"]


def test_natural_scene_target_reaches_editor_command(monkeypatch, pid):
    monkeypatch.setattr(director, "_llm_plan", lambda instruction, contexts: {
        "goal": "调整第二段时长",
        "steps": [{"action": "timeline.duration", "target": "第二段",
                   "params": {"duration": 2.0}}],
    })
    out = director.direct(pid, "把第二段改成 2 秒")
    assert out["verify_ok"] is True
    scene = next(s for s in editor.load_composition(pid).scenes if s.id == "scene-2")
    assert abs(scene.duration - 2.0) < 0.01


def test_generated_visual_is_adopted(monkeypatch, pid):
    class G:
        asset_id = "generated-1"
        variant = "A"
        type = "image"
        file = "/tmp/generated-1.png"
        width = 1080
        height = 1920
        input_fingerprint = "in"
        output_fingerprint = "out"
        provider_id = "test"

        def model_dump(self):
            return {
                "asset_id": self.asset_id, "variant": self.variant, "type": self.type,
                "file": self.file, "width": self.width, "height": self.height,
                "input_fingerprint": self.input_fingerprint,
                "output_fingerprint": self.output_fingerprint,
                "provider_id": self.provider_id,
            }

    import video_director.generation as gen
    monkeypatch.setattr(gen, "generate", lambda *args, **kwargs: [G()])
    monkeypatch.setattr(Path, "is_file", lambda self: True)

    original = editor.execute_command
    def fake_execute(project_id, command):
        if command["command"] == "replace_source":
            from video_director.editor import CommandResult
            ir = editor.load_composition(project_id)
            layer = ir.scenes[0].layers[0]
            ir.scenes[0].layers[0] = layer.model_copy(update={"asset_id": command["payload"]["assetId"]})
            editor.save_composition(project_id, ir)
            return CommandResult(status="APPLIED", revision_id="rev-test",
                                 composition_version=ir.version, composition=ir.model_dump())
        return original(project_id, command)

    monkeypatch.setattr(editor, "execute_command", fake_execute)
    r = tool_executor.execute_tool(pid, {
        "tool": "generation.image",
        "targetId": "scene-1",
        "payload": {"prompt": "真实生活场景"},
    })
    assert r["ok"] is True
    assert r["result"]["adopted"][0]["asset_id"] == "generated-1"


def test_music_match_uses_real_audio_command(monkeypatch, pid):
    called = {}

    import video_director.audio as audio_mod
    monkeypatch.setattr(audio_mod, "resolve_auto_bgm",
                        lambda style_id: "bgm-test")
    class R:
        status = "APPLIED"
        errors = []
        revision_id = "rev-music"

    def fake_execute(project_id, command):
        called["command"] = command
        return R()
    monkeypatch.setattr(editor, "execute_command", fake_execute)
    r = tool_executor.execute_tool(pid, {"tool": "music.match_video", "payload": {}})
    assert r["ok"] is True
    assert called["command"]["command"] == "set_audio_volume"
    assert called["command"]["targetId"] == "bgm"
    assert called["command"]["payload"]["assetId"] == "bgm-test"


def test_director_plan_is_persisted(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path / "composer")
    pid = "ai-dir-run"
    config.init_project_dirs(config.project_dir(pid))

    def fake_execute(spec, run_id):
        return {"ok": True, "project_id": pid, "pending_confirm": True}

    monkeypatch.setattr(workflow_run, "_execute_compose", fake_execute)
    plan = {"version": "director-create-v1", "goal": "做一条短视频",
            "nodes": [{"id": "script", "label": "文案", "description": "生成文案"}]}
    run_id = workflow_run.run_director_create_async(
        {"project_id": pid, "script": "测试", "confirm_mode": "preview"}, plan=plan)

    deadline = time.time() + 3
    run = workflow_run.get_run(run_id)
    while (run is None or run["status"] == "RUNNING") and time.time() < deadline:
        time.sleep(0.02)
        run = workflow_run.get_run(run_id)

    assert run is not None
    assert run["status"] == "WAITING_CONFIRMATION"
    assert run["result"]["director_plan"]["nodes"][0]["id"] == "script"
