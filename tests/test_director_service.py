"""Regression tests for the unified Director service facade."""
import pytest

from video_director.director_service import (
    DirectorServiceError,
    create_director_run,
)


@pytest.fixture(autouse=True)
def _stable_final_renderer(monkeypatch):
    from video_director import studio_catalog
    monkeypatch.setattr(studio_catalog, "full_video_engine_status", lambda engine_id="ffmpeg": {
        "id": "ffmpeg", "name": "FFmpeg", "scope": "full_video",
        "status": "PASS", "selectable": True, "detail": "fixture FFmpeg is executable",
        "path": "/fixture/ffmpeg",
    })


def test_empty_creation_is_rejected_before_workflow_side_effects():
    with pytest.raises(DirectorServiceError) as exc:
        create_director_run({})
    assert exc.value.code == "MISSING_SOURCE"
    assert exc.value.status_code == 400


def test_reference_analysis_returns_reusable_input_object(tmp_path, monkeypatch):
    from video_director import config, reference

    video = tmp_path / "reference.mp4"
    video.write_bytes(b"video-placeholder")
    monkeypatch.setattr(config, "project_dir", lambda project_id: tmp_path / project_id)
    monkeypatch.setattr(reference, "reference_analyzer", lambda video_path, output_dir, use_llm=True: {
        "video": video_path,
        "report_path": str(tmp_path / "reference_report.json"),
        "visual_language": {"pace": "slow"},
        "shot_grammar": [],
    })

    result = __import__(
        "video_director.director_service", fromlist=["analyze_reference_video"]
    ).analyze_reference_video(str(video), use_llm=False)
    assert result["ok"] is True
    assert result["reference"]["report"]["visual_language"]["pace"] == "slow"
    assert result["reference"]["report_path"].endswith("reference_report.json")
    assert "references" not in result
    assert result["reference"]["apply"] == ["pace", "density", "motion", "transition", "shot_grammar"]


@pytest.mark.parametrize("fps", [0, 121, "bad"])
def test_invalid_fps_is_rejected(fps, monkeypatch):
    from video_director import director_inputs
    monkeypatch.setattr(director_inputs, "build_bundle", lambda merged: {})
    monkeypatch.setattr(director_inputs, "build_plan", lambda bundle, prompt: {})
    with pytest.raises(DirectorServiceError) as exc:
        create_director_run({
            "brief": "test",
            "fps": fps,
            "director_inputs": {"brief": "test", "assets": {}},
        })
    assert exc.value.code == "INVALID_SETTINGS"


def test_reference_analysis_rejects_missing_video():
    from video_director.director_service import analyze_reference_video
    with pytest.raises(DirectorServiceError) as exc:
        analyze_reference_video("/definitely/missing/reference.mp4")
    assert exc.value.code == "REFERENCE_NOT_FOUND"


def test_create_rejects_path_like_project_id():
    with pytest.raises(DirectorServiceError) as exc:
        create_director_run({
            "project_id": "../outside",
            "brief": "制作视频",
            "director_inputs": {"brief": "制作视频"},
        })
    assert exc.value.code == "INVALID_PROJECT_ID"


def test_create_director_run_builds_the_same_bundle_and_workflow(monkeypatch):
    from video_director import director_inputs, workflow_run

    captured = {}

    def fake_build_bundle(merged):
        captured["merged"] = merged
        return {"brief": merged["brief"], "script": merged["director_inputs"]["script"]}

    def fake_build_plan(bundle, prompt):
        captured["prompt"] = prompt
        return {"prompt": prompt}

    def fake_start(spec, plan=None):
        captured["spec"] = spec
        captured["plan"] = plan
        return "run-test-1"

    monkeypatch.setattr(director_inputs, "build_bundle", fake_build_bundle)
    monkeypatch.setattr(director_inputs, "build_plan", fake_build_plan)
    monkeypatch.setattr(workflow_run, "run_director_create_async", fake_start)

    result = create_director_run({
        "brief": "制作一条短视频",
        "script": "这是用户文案",
        "platform": "douyin",
        "resolution": "1080x1920",
        "confirm_mode": "preview",
        "bgm_volume": 0,
        "narration_volume": 0,
        "director_inputs": {
            "brief": "制作一条短视频",
            "script": {"text": "这是用户文案"},
            "assets": {"asset_refs": [], "policy": "user_first", "required": False},
            "references": [],
            "articles": [],
            "software_project": {},
            "constraints": {"visual_source": "local", "allow_generation": True, "render_engine": "ffmpeg"},
        },
    })

    assert result["ok"] is True
    assert result["run_id"] == "run-test-1"
    assert result["project_id"].startswith("web-")
    assert captured["spec"]["auto_confirm"] is False
    assert captured["spec"]["width"] == 1080
    assert captured["spec"]["height"] == 1920
    assert captured["spec"]["platform"] == "douyin"
    assert captured["spec"]["_explicit_fields"] == []
    assert captured["spec"]["bgm_volume"] == 0
    assert captured["spec"]["narration_volume"] == 0
    assert captured["spec"]["visual_source"] == "local"
    assert captured["spec"]["director_inputs"] == result["director_inputs"]
    assert captured["prompt"] == "制作一条短视频"


def test_brief_intent_overrides_untouched_ui_defaults_but_not_manual_choices(monkeypatch):
    from video_director import director_inputs, director_intent, workflow_run

    parsed_intent = {
        "resolution": "1920x1080",
        "duration": "40+",
        "voice": "zh-CN-YunjianNeural",
        "voice_mode": "auto",
        "voice_rate": "-15%",
        "style": "cinematic-story",
        "bgm": "off",
        "bgm_volume": "0.4",
        "narration_volume": "0.8",
        "subtitle_mode": "off",
        "clip_duration": "3",
        "clip_speed": "0.9",
        "transition_mode": "fade",
        "watermark": "逆时钟认知",
        "_unresolved": "",
    }
    monkeypatch.setattr(director_intent, "resolve_rule_intents", lambda _brief: dict(parsed_intent))
    captured = {}

    def fake_bundle(merged):
        captured.setdefault("merged", []).append(merged)
        di = merged.get("director_inputs") or {}
        return {
            "brief": merged.get("brief", ""),
            "script": di.get("script", {}),
            "assets": {"asset_refs": []},
            "constraints": di.get("constraints") or {},
            "platform": di.get("platform", ""),
            "duration": di.get("duration", ""),
            "style": di.get("style", ""),
        }

    def fake_plan(bundle, prompt):
        return {"prompt": prompt, "bundle_style": bundle.get("style")}

    def fake_start(spec, plan=None):
        captured["spec"] = spec
        captured["plan"] = plan
        return "run-intent-regression"

    monkeypatch.setattr(director_inputs, "build_bundle", fake_bundle)
    monkeypatch.setattr(director_inputs, "build_plan", fake_plan)
    monkeypatch.setattr(workflow_run, "run_director_create_async", fake_start)

    def request(explicit_fields):
        return create_director_run({
            "brief": "横屏 16:9，40 秒以上，放慢配音，每个镜头 3 秒，加水印，不要背景音乐和字幕",
            "script": "",
            "resolution": "1080x1920",  # UI default, not user-chosen in this case
            "width": 1080,
            "height": 1920,
            "platform": "",
            "duration": "",
            "style": "",
            "voice": "zh-CN-XiaoxiaoNeural",
            "voice_mode": "auto",
            "voice_rate": "1.0",
            "bgm": "auto",
            "bgm_volume": 0.3,
            "narration_volume": 1.0,
            "sfx": "auto",
            "subtitle_mode": "plain",
            "fit_mode": "cover",
            "clip_duration": 5,
            "clip_speed": None,
            "transition_mode": "cut",
            "watermark": "",
            "confirm_mode": "preview",
            "_explicit_fields": explicit_fields,
            "director_inputs": {
                "brief": "横屏 16:9，40 秒以上，放慢配音，每个镜头 3 秒，加水印，不要背景音乐和字幕",
                "script": {"text": "", "source": "director", "authority": "generated"},
                "assets": {"asset_refs": [], "policy": "user_first", "required": False},
                "references": [],
                "articles": [],
                "software_project": {},
                "constraints": {"allow_generation": True, "allow_stock": True},
                "platform": "",
                "duration": "",
                "style": "",
            },
        })

    assert request([])["ok"] is True
    automatic = captured["spec"]
    assert (automatic["width"], automatic["height"]) == (1920, 1080)
    assert automatic["platform"] == ""
    assert automatic["voice"] == "zh-CN-YunjianNeural"
    assert automatic["voice_rate"] == "-15%"
    assert automatic["duration"] == "40+"
    assert automatic["_intent_duration_s"] == "40+"
    assert automatic["clip_duration"] == 3.0
    assert automatic["clip_speed"] == 0.9
    assert automatic["bgm"] == "off"
    assert automatic["bgm_volume"] == 0.4
    assert automatic["narration_volume"] == 0.8
    assert automatic["subtitle_mode"] == "off"
    assert automatic["transition_mode"] == "fade"
    assert automatic["watermark"] == "逆时钟认知"
    assert automatic["style_intent"] == "cinematic-story"

    assert request(["resolution", "voice_rate"])["ok"] is True
    manually_chosen = captured["spec"]
    assert manually_chosen["_explicit_fields"] == ["resolution", "voice_rate"]
    assert (manually_chosen["width"], manually_chosen["height"]) == (1080, 1920)
    assert manually_chosen["voice_rate"] == "1.0"
    assert manually_chosen["bgm"] == "off"
    assert manually_chosen["watermark"] == "逆时钟认知"


@pytest.mark.parametrize("value", ["1920", "1080x1920x30", "0x1080", "1080x0"])
def test_invalid_resolution_is_rejected(value, monkeypatch):
    from video_director import director_inputs
    monkeypatch.setattr(director_inputs, "build_bundle", lambda merged: {})
    monkeypatch.setattr(director_inputs, "build_plan", lambda bundle, prompt: {})
    with pytest.raises(DirectorServiceError) as exc:
        create_director_run({
            "brief": "test",
            "resolution": value,
            "director_inputs": {"brief": "test", "assets": {}},
        })
    assert exc.value.code == "INVALID_RESOLUTION"


def test_create_rejects_unavailable_final_video_engine_before_workflow_side_effects(monkeypatch):
    from video_director import director_inputs, studio_catalog, workflow_run

    monkeypatch.setattr(studio_catalog, "full_video_engine_status", lambda engine_id="ffmpeg": {
        "id": "ffmpeg", "name": "FFmpeg", "scope": "full_video",
        "status": "MISSING", "selectable": False, "detail": "FFmpeg 不可执行",
    })
    monkeypatch.setattr(
        director_inputs, "build_bundle",
        lambda _merged: pytest.fail("engine gate must run before bundle construction"),
    )
    monkeypatch.setattr(
        workflow_run, "run_director_create_async",
        lambda *_args, **_kwargs: pytest.fail("engine gate must run before workflow creation"),
    )

    with pytest.raises(DirectorServiceError) as exc:
        create_director_run({
            "brief": "制作一条短视频",
            "director_inputs": {
                "brief": "制作一条短视频",
                "assets": {"asset_refs": []},
                "constraints": {"render_engine": "ffmpeg"},
            },
        })
    assert exc.value.code == "RENDER_ENGINE_UNAVAILABLE"
    assert exc.value.status_code == 503
    assert "FFmpeg 不可执行" in str(exc.value)


def test_create_rejects_graphics_provider_as_final_video_engine(monkeypatch):
    from video_director import studio_catalog, director_inputs, workflow_run

    monkeypatch.setattr(
        studio_catalog, "full_video_engine_status",
        lambda engine_id: {
            "id": engine_id, "name": engine_id, "scope": "full_video",
            "status": "MISSING", "selectable": False,
            "detail": "Playwright Chromium 仅为图形层引擎",
        },
    )
    monkeypatch.setattr(
        director_inputs, "build_bundle",
        lambda _merged: pytest.fail("unsupported renderer must be rejected before bundle construction"),
    )
    monkeypatch.setattr(
        workflow_run, "run_director_create_async",
        lambda *_args, **_kwargs: pytest.fail("unsupported renderer must not launch a workflow"),
    )

    with pytest.raises(DirectorServiceError) as exc:
        create_director_run({
            "brief": "制作一条短视频",
            "director_inputs": {
                "brief": "制作一条短视频",
                "assets": {"asset_refs": []},
                "constraints": {"render_engine": "browser"},
            },
        })
    assert exc.value.code == "RENDER_ENGINE_UNAVAILABLE"
    assert exc.value.status_code == 503




def test_director_agent_persists_project_name_and_type_in_project_params(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from video_director import config, director_agent, director_inputs, store

    storage = tmp_path / "composer"
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)
    project_id = "web-metadata-agent"
    config.init_project_dirs(config.project_dir(project_id))
    bundle = {
        "brief": "制作知识科普视频",
        "script": {"text": "权威文案"},
        "assets": {"asset_refs": []},
        "references": [],
        "articles": [],
        "software_project": {},
        "constraints": {},
    }
    monkeypatch.setattr(director_inputs, "build_bundle", lambda _spec: bundle)
    monkeypatch.setattr(director_inputs, "persist_bundle", lambda *_args: None)
    monkeypatch.setattr(director_inputs, "persist_plan", lambda *_args: None)
    monkeypatch.setattr(director_inputs, "attach_library_assets", lambda *_args: ([], []))
    monkeypatch.setattr(director_inputs, "materialize_reference", lambda *_args: [])
    monkeypatch.setattr(director_inputs, "materialize_software_project_artifacts", lambda *_args: [])
    monkeypatch.setattr(director_agent, "_restore_checkpoint_state", lambda *_args: False)
    monkeypatch.setattr(director_agent, "_ensure_reference_reports", lambda *_args: None)
    monkeypatch.setattr(
        director_agent.style_mod,
        "select_style",
        lambda *_args: (SimpleNamespace(style_id="fixture-style"), []),
    )

    director_agent.execute(
        {
            "project_id": project_id,
            "project_name": "复利知识科普",
            "project_type": "知识科普",
            "brief": "制作知识科普视频",
            "script": "权威文案",
            "confirm_mode": "direct",
        },
        "run-metadata-agent",
        {"nodes": []},
        lambda *_args, **_kwargs: None,
        lambda *_args, **_kwargs: None,
    )

    row = store.get_project(project_id)
    params = json.loads(row["params_json"])
    assert params["name"] == "复利知识科普"
    assert params["project_type"] == "知识科普"
