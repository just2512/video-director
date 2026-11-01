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
    original_is_file = Path.is_file

    def generated_asset_exists(path):
        # Only the mocked provider output exists; keep project authority probes
        # real so a missing film.html is not mistaken for an active Film project.
        if path == Path(G.file):
            return True
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", generated_asset_exists)

    source_version = editor.composition_version(pid)
    original = editor.execute_command
    def fake_execute(project_id, command):
        if command["command"] == "replace_source":
            assert command["expectedVersion"] == source_version
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
    assert r["ok"] is True, r
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

    def fake_director_create(spec, run_id):
        return {
            "ok": True,
            "project_id": pid,
            "pending_confirm": True,
            "director_plan": plan,
            "director_verify_ok": True,
        }

    monkeypatch.setattr(workflow_run, "_execute_director_create", fake_director_create)
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


def test_mpt_ui_surface_is_real_not_only_backend():
    root = Path(__file__).parents[1]
    create = (root / "webui" / "static" / "js" / "create.js").read_text(encoding="utf-8")
    inspector = (root / "webui" / "static" / "js" / "inspector.js").read_text(encoding="utf-8")
    index = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="cf-director-brief"' in create
    assert 'id="cf-voice-volume"' in create
    assert 'id="sub-font"' in inspector
    assert 'id="sub-animation"' in inspector
    assert 'id="director-plan-card"' in index


def test_bgm_semantics_do_not_claim_ai_generation():
    """§十一：BGM「自动匹配」必须表达「选现有音乐」，不能误导为 AI 生成音乐。"""
    root = Path(__file__).parents[1]
    create = (root / "webui" / "static" / "js" / "create.js").read_text(encoding="utf-8")
    assert "AI 自动匹配" not in create
    assert "匹配现有音乐" in create



def test_director_context_refresh_clears_selection_cache_when_nothing_is_selected():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "library.js").read_text(encoding="utf-8")
    start = source.index("async function directorRefreshContext(){")
    end = source.index("async function directorLoadCapCount(){", start)
    function_body = source[start:end]
    assert "const ctx = getSelectionContext();" in function_body
    assert "await apiPost('/api/director/context', {project_id: ED.pid, selection: ctx});" in function_body
    assert "if (ctx.type !== 'none')" not in function_body

def test_generated_visual_adoption_rejects_stale_composition_revision(monkeypatch, pid):
    class G:
        asset_id = "generated-stale-1"
        variant = "A"
        type = "image"
        file = "/tmp/generated-stale-1.png"
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
    from video_director.editor import CommandResult

    monkeypatch.setattr(gen, "generate", lambda *args, **kwargs: [G()])
    original_is_file = Path.is_file
    monkeypatch.setattr(
        Path, "is_file",
        lambda path: True if path == Path(G.file) else original_is_file(path),
    )
    source_version = editor.load_composition_snapshot(pid)[1]
    captured = {}

    def reject_stale_adoption(project_id, command):
        captured.update(command)
        return CommandResult(
            status="REJECTED",
            composition_version=source_version + 1,
            errors=[{
                "code": "CONFLICT",
                "message": "生成期间项目时间线已变化，请重新生成或手动采用素材",
                "recoverable": True,
            }],
        )

    monkeypatch.setattr(editor, "execute_command", reject_stale_adoption)
    result = tool_executor.execute_tool(pid, {
        "tool": "generation.image",
        "targetId": "scene-1",
        "payload": {"prompt": "并发修改后不得覆盖用户选择"},
    })

    assert captured["expectedVersion"] == source_version
    assert result["ok"] is False
    assert result["status"] == "REJECTED"
    assert result["result"]["results"][0]["asset_id"] == "generated-stale-1"
    assert "CONFLICT" in result["error"]


