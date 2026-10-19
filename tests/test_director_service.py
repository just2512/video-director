"""Regression tests for the unified Director service facade."""
import pytest

from video_director.director_service import (
    DirectorServiceError,
    create_director_run,
)


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
            "constraints": {"visual_source": "local", "allow_generation": True},
        },
    })

    assert result["ok"] is True
    assert result["run_id"] == "run-test-1"
    assert result["project_id"].startswith("web-")
    assert captured["spec"]["auto_confirm"] is False
    assert captured["spec"]["width"] == 1080
    assert captured["spec"]["height"] == 1920
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
    monkeypatch.setattr(director_intent, "resolve_director_intents", lambda _brief: dict(parsed_intent))
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
