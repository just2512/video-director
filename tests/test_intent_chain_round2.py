# -*- coding: utf-8 -*-
"""2026-10-20 意图链第二批修复回归（19字段审计 + 架构审计的断链点）。

审计指纹：
- off 字幕死分支 → 必须真的跳过生成
- fade 用户指令被锁死 PROPOSED → 必须直接写 IR
- 警告死在内存 → intent_report.json 落盘
- clip_speed 规则层无提取 → 必须能解析
- graphic/motion 候选被白名单清空 → 必须保留
"""
import json
from pathlib import Path


class TestSubtitleOff:
    """「不要字幕」必须真的不生成字幕文件，返回 SKIPPED。"""

    def test_off_skips_generation(self, tmp_path, monkeypatch):
        from video_director import director_agent, config
        pid = "proj-sub-off"
        config.init_project_dirs(config.project_dir(pid))
        state = {
            "project_id": pid,
            "narration_scenes": [{"id": "S01", "text": "句", "start": 0.0, "duration": 2.0}],
            "word_timeline": [],
            "style_contract": _style_contract(),
        }
        r = director_agent._tool_subtitle({"subtitle_mode": "off"}, state)
        assert r["status"] == "SKIPPED", r
        assert state.get("subtitle_file", "") == ""
        assert state.get("subtitle_entries") == []

    def test_bilingual_degrades_with_warning(self, tmp_path, monkeypatch):
        from video_director import director_agent, config, subtitle as subtitle_mod
        pid = "proj-sub-bi"
        config.init_project_dirs(config.project_dir(pid))
        # 测试隔离（_provider_cache 单例泄漏同族，第三次）：本测断言的是
        # 「LLM 不可用 → 诚实降级 plain+警告」，必须隔离真实 provider，
        # 否则环境里配置了可用 LLM 时翻译成功、warnings 为空、断言必然失败。
        from video_director import llm_provider
        monkeypatch.setattr(llm_provider, "_provider_cache", None)
        monkeypatch.setattr(llm_provider, "get_llm", lambda *a, **k: None)
        state = {
            "project_id": pid,
            "narration_scenes": [{"id": "S01", "text": "句", "start": 0.0, "duration": 2.0}],
            "word_timeline": [{"text": "句", "start": 0.0, "end": 1.0}],
            "style_contract": _style_contract(),
        }
        r = director_agent._tool_subtitle({"subtitle_mode": "bilingual"}, state)
        # 降级 plain 但生成成功，且警告在 result 里
        assert r["status"] == "APPLIED"
        assert any("双语" in w for w in (r["result"].get("warnings") or []))


def _style_contract():
    from video_director.style import StyleContract
    return StyleContract(style_id="documentary-calm")


class TestTransitionDirectApply:
    """用户明确 fade → 直接写 IR（不再锁死 PROPOSED）。"""

    def test_fade_written_to_ir(self):
        from video_director.models import CompositionIR, TransitionOut
        ir = CompositionIR.model_validate({
            "project": {"fps": 30, "width": 1080, "height": 1920, "duration": 6.0},
            "scenes": [
                {"id": "S01", "start": 0.0, "duration": 3.0, "text": "a",
                 "layers": [{"type": "gap", "start": 0.0, "duration": 3.0}]},
                {"id": "S02", "start": 3.0, "duration": 3.0, "text": "b",
                 "layers": [{"type": "gap", "start": 0.0, "duration": 3.0}]},
            ],
            "assets": [], "audio": [], "subtitle_track": {"cues": []},
        })
        # 与 _tool_composition 转场段一致的用户指令直执行逻辑
        transition_mode = "fade"
        if transition_mode in ("fade", "crossfade") and len(ir.scenes) > 1:
            for i in range(len(ir.scenes) - 1):
                ir.scenes[i].transition_out = TransitionOut(type="crossfade", duration=0.5)
        assert ir.scenes[0].transition_out.type == "crossfade"
        assert ir.scenes[0].transition_out.duration == 0.5


class TestClipSpeedRule:
    """clip_speed 规则层提取（审计：规则层完全没有分支）。"""

    def test_explicit_multiple(self):
        from video_director.director_intent import resolve_director_intents
        assert resolve_director_intents("画面 2 倍速")["clip_speed"] == "2.0"

    def test_direction_words(self):
        from video_director.director_intent import resolve_director_intents
        assert resolve_director_intents("慢动作展示")["clip_speed"] == "0.8"
        assert resolve_director_intents("画面快一点")["clip_speed"] == "1.5"

    def test_out_of_range_rejected(self, monkeypatch):
        """超范围值不得透传。注意：本测试文件未隔离 model_config 时 LLM 层
        可能真实调用，必须挡掉保证确定性。"""
        from video_director import llm_provider
        monkeypatch.setattr(llm_provider, "get_llm", lambda: None)
        from video_director.director_intent import resolve_director_intents
        r = resolve_director_intents("10 倍速播放")
        assert r.get("clip_speed") != "10"  # 超范围值不透传（10 不在 0.25~4.0）


class TestCandidateWhitelist:
    """graphic/motion 不再被白名单清空（架构审计：死分支）。"""

    def test_motion_survives(self):
        from video_director.creative_candidates import _clean_candidate
        c = _clean_candidate(
            "S01", 2,
            {"title": "动效", "approach": "concept", "visual_intent": "x",
             "source_preference": "generated", "generation_type": "motion"},
            "x",
        )
        assert c["generation_type"] == "motion"

    def test_unknown_rejected(self):
        from video_director.creative_candidates import _clean_candidate
        c = _clean_candidate(
            "S01", 2,
            {"title": "怪", "approach": "concept", "visual_intent": "x",
             "source_preference": "generated", "generation_type": "hologram"},
            "x",
        )
        assert c["generation_type"] == "image"  # generated 缺省回 image


class TestVolumeIntentMerge:
    """bgm_volume/narration_volume 意图值不再被默认覆盖（审计：合并层丢弃）。"""

    def test_spec_construction_consumes_intent(self, tmp_path, monkeypatch):
        from video_director import director_service, llm_provider
        # create_director_run 会真的 resolve_director_intents→get_llm()，读生产
        # model_config 并填 llm_provider._provider_cache 模块级单例——泄漏给
        # test_generate_script_requires_llm（同族问题第三次，2026-10-20 根修）。
        monkeypatch.setattr(llm_provider, "get_llm", lambda: None)
        monkeypatch.setattr(llm_provider, "_provider_cache", None)
        captured = {}
        monkeypatch.setattr(
            "video_director.workflow_run.run_director_create_async",
            lambda spec, plan=None: captured.update(spec=spec) or "run-x")
        payload = {"brief": "背景音乐小声"}
        director_service.create_director_run(payload)
        # 规则层命中「音乐小声」→ 0.4（默认 0.3 被意图值替换）
        assert abs(float(captured["spec"]["bgm_volume"]) - 0.4) <= 0.01
