# -*- coding: utf-8 -*-
"""2026-10-18 系统性修复回归测试：
- 自然语言 brief → 导演参数解析（能力目录驱动，不只水印/时长/分辨率三个字段）
- 分辨率默认优先源素材（16:9 素材不得默认成 9:16）
- lane 级静音（渲染跳过整条音轨）+ 轨道隐藏
"""
from pathlib import Path

import pytest

from video_director import director_intent
from video_director.director_intent import resolve_director_intents


class TestDirectorIntent:
    """能力目录驱动解析：覆盖全部可自然语言控制的导演参数，不限三个字段。"""

    def test_resolution_alias(self):
        assert resolve_director_intents("要横屏 16:9 的")["resolution"] == "1920x1080"
        assert resolve_director_intents("竖屏 9:16")["resolution"] == "1080x1920"

    def test_duration_seconds_and_minutes(self):
        """方向语义：「N+」=不少于、「N-」=不超过、无方向词=目标值。"""
        assert resolve_director_intents("时长控制在 40 秒以上")["duration"] == "40+"
        assert resolve_director_intents("不超过 30 秒")["duration"] == "30-"
        assert resolve_director_intents("做 1 分钟")["duration"] == "60"

    def test_voice_rate(self):
        assert resolve_director_intents("放慢配音速度")["voice_rate"] == "-15%"
        assert resolve_director_intents("语速快一点")["voice_rate"] == "+15%"

    def test_watermark(self):
        assert resolve_director_intents("加个水印「张三出品」")["watermark"] == "张三出品"

    def test_subtitle_switch(self):
        assert resolve_director_intents("不要字幕")["subtitle_mode"] == "off"
        assert resolve_director_intents("加双语字幕")["subtitle_mode"] == "bilingual"

    def test_voice_mode_silent(self):
        assert resolve_director_intents("静音，不要配音")["voice_mode"] == "silent"

    def test_bgm_off(self):
        assert resolve_director_intents("不要背景音乐")["bgm"] == "off"

    def test_bgm_volume(self):
        assert resolve_director_intents("背景音乐小声")["bgm_volume"] == "0.4"

    def test_no_llm_no_crash(self):
        """LLM 不可用也能规则层解析，不抛异常。"""
        r = resolve_director_intents("时长 30 秒，横屏")
        assert r["duration"] == "30"
        assert r["resolution"] == "1920x1080"

    def test_catalog_covers_all_consumed_spec_fields(self):
        """能力目录不得漏掉「用户能要求」的字段。"""
        fields = {i["field"] for i in director_intent.INTENT_FIELDS}
        for f in ("resolution", "duration", "voice_rate", "watermark", "subtitle_mode",
                  "style", "bgm", "bgm_volume", "narration_volume", "transition_mode"):
            assert f in fields, f


class TestValueNormalization:
    """意图值 → 系统真实契约的二次映射（防「识别了但下游 Invalid」）。"""

    def test_voice_freetext_to_real_id(self):
        """「温柔女声」必须落白名单 voice_id，不是字面文本。"""
        assert director_intent._normalize_voice("温柔女声") == "zh-CN-XiaoxiaoNeural"
        assert director_intent._normalize_voice("沉稳男声") == "zh-CN-YunjianNeural"
        # 已是合法 id 透传
        assert director_intent._normalize_voice("zh-CN-YunxiNeural") == "zh-CN-YunxiNeural"
        # 无法识别的性别词 → None（不传，用默认）
        assert director_intent._normalize_voice("外星人") is None

    def test_style_freetext_to_registry_id(self):
        """「电影感大片」→ cinematic-story；未注册风格保留原文作 style_intent。"""
        assert director_intent._normalize_style("电影感大片") == "cinematic-story"
        assert director_intent._normalize_style("赛博朋克") == "赛博朋克"  # 未注册：透传
        assert director_intent._normalize_style("documentary-calm") == "documentary-calm"

    def test_transition_and_fit_enum(self):
        assert director_intent._normalize_value("transition_mode", "淡入淡出") == "fade"
        assert director_intent._normalize_value("fit_mode", "完整显示留黑边") == "contain"

    def test_subtitle_mode_enum(self):
        assert director_intent._normalize_value("subtitle_mode", "中英双语") == "bilingual"
        assert director_intent._normalize_value("subtitle_mode", "不要") == "off"

    def test_clip_duration_not_total_duration(self):
        """「每个镜头 3 秒」是单镜头时长，不得误报成片总时长。"""
        r = resolve_director_intents("每个镜头 3 秒")
        assert r.get("clip_duration") == "3"
        assert "duration" not in r, f"单镜头语境被误报成总时长: {r}"

    def test_compound_clip_and_total_duration(self):
        """复合 brief：镜头时长与总时长独立提取（不互斥、不互吞）。"""
        r = resolve_director_intents("时长40秒以上，每个镜头3秒")
        assert r.get("clip_duration") == "3"
        assert r.get("duration", "").startswith("40"), r


class TestLaneMutedAndHidden:
    """lane 级静音 + 轨道隐藏命令。"""

    def _project(self, tmp_path, project_id="proj-lane"):
        from video_director.editor import save_composition
        from video_director.models import CompositionIR
        ir = CompositionIR.model_validate({
            "project": {"fps": 30, "width": 1080, "height": 1920, "duration": 10.0},
            "scenes": [{"id": "S1", "start": 0.0, "duration": 10.0, "text": "x",
                        "layers": [{"type": "gap", "start": 0.0, "duration": 10.0}]}],
            "assets": [{"id": "b", "type": "audio", "file": "b.mp3", "duration": 10.0}],
            "audio": [{"id": "bgm-1", "type": "bgm", "asset_id": "b",
                       "start": 0.0, "duration": 10.0, "volume": 1.0}],
            "audio_tracks": [{"id": "audio-bgm", "name": "背景音乐", "order": 0, "locked": False}],
        })
        save_composition(project_id, ir)
        return project_id

    def test_set_track_muted_marks_lane(self, tmp_path):
        from video_director.editor import _cmd_set_track_muted, load_composition
        pid = self._project(tmp_path)
        ir = load_composition(pid)
        _cmd_set_track_muted(ir, "audio-bgm", {"trackType": "audio", "muted": True})
        assert ir.audio_tracks[0].muted is True

    def test_set_track_hidden_marks_lane(self, tmp_path):
        from video_director.editor import _cmd_set_track_hidden, load_composition
        pid = self._project(tmp_path)
        ir = load_composition(pid)
        _cmd_set_track_hidden(ir, "audio-bgm", {"trackType": "audio", "hidden": True})
        assert ir.audio_tracks[0].hidden is True


class TestResolutionPrefersSource:
    """分辨率默认优先源素材（16:9 不得默认 9:16）。"""

    def test_service_probes_source_when_no_resolution(self, tmp_path, monkeypatch):
        from video_director import director_service
        # 造一个假素材文件路径，probe 返回 1920x1080
        fake = tmp_path / "src.mp4"
        fake.write_bytes(b"x")
        monkeypatch.setattr("video_director.prober.probe_media",
                            lambda p: type("P", (), {"width": 1920, "height": 1080})())
        # 不显式给 resolution/platform，直接测解析逻辑路径：
        # director_service 里在 resolution 空时 probe bundle 视频素材的宽高。
        payload = {
            "brief": "测试",
            "director_inputs": {
                "brief": "测试",
                "assets": {"asset_refs": [{"type": "video", "file": str(fake)}]},
            },
            "confirm_mode": "preview",
        }
        # run_director_create_async 会真的启线程；改为直接验证构造 spec 的代码路径
        # —— 用 monkeypatch 挡住 workflow 启动，只检查 spec 的 width/height。
        captured = {}
        monkeypatch.setattr("video_director.workflow_run.run_director_create_async",
                            lambda spec, plan=None: captured.update(spec) or "run-x")
        director_service.create_director_run(payload)
        assert captured.get("width") == 1920
        assert captured.get("height") == 1080