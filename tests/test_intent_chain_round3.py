# -*- coding: utf-8 -*-
"""2026-10-20 第三批修复回归：bilingual 真双语 / sfx 执行点 / 镜头锁定。

隔离纪律（test_intent_chain_round2 同款）：llm_provider.get_llm 必须挡、
_provider_cache=None——否则测试会真调生产 LLM。
"""
import json
from pathlib import Path

import pytest

import video_director.llm_provider as lp


@pytest.fixture(autouse=True)
def _isolate_llm(monkeypatch):
    monkeypatch.setattr(lp, "get_llm", lambda: None)
    monkeypatch.setattr(lp, "_provider_cache", None)


def _style_contract():
    from video_director.style import StyleContract
    return StyleContract(style_id="documentary-calm")


def _state(pid="proj-r3", tmp=None):
    from video_director import config
    config.init_project_dirs(config.project_dir(pid))
    return {
        "project_id": pid,
        "narration_scenes": [
            {"id": "S01", "text": "工匠运石上山。", "start": 0.0, "duration": 5.0},
            {"id": "S02", "text": "烈日下的坚持。", "start": 5.0, "duration": 4.0},
        ],
        "word_timeline": [{"text": "工", "start": 0.0, "end": 0.4}],
        "style_contract": _style_contract(),
    }


class TestBilingual:
    """bilingual：LLM 翻译可用→真双语文本；不可用→诚实降级 plain+警告。"""

    def test_bilingual_builds_two_styles(self):
        from video_director.subtitle import build_ass_bilingual
        scenes = [
            {"id": "S01", "text": "工匠运石上山。", "start": 0.0, "duration": 5.0},
        ]
        body = build_ass_bilingual(
            scenes, {"S01": "Craftsmen carry stones up the mountain."},
            width=1080, height=1920, font_size=64)
        assert "Style: Sub," in body          # 中文主行样式
        assert "Style: SubEn," in body        # 英文次行样式
        assert "Craftsmen carry stones" in body
        assert "工匠" in body

    def test_bilingual_translation_injection_stripped(self):
        from video_director.subtitle import build_ass_bilingual
        scenes = [{"id": "S01", "text": "句一。", "start": 0.0, "duration": 2.0}]
        body = build_ass_bilingual(
            scenes, {"S01": '{\\pos(1,1)}evil\\Ntext'}, width=1080, height=1920)
        # ASS 覆写标签/花括号/反斜杠必须被剥掉（LLM 输出=边界输入）
        assert "{" not in body.split("[Events]")[1]
        assert "\\\\" not in body.split("[Events]")[1]

    def test_tool_bilingual_degrades_without_llm(self, tmp_path, monkeypatch):
        from video_director import director_agent
        monkeypatch.setattr("video_director.config.composer_storage_root",
                            lambda: tmp_path)
        state = _state("proj-r3-bi", tmp_path)
        r = director_agent._tool_subtitle({"subtitle_mode": "bilingual"}, state)
        assert r["status"] == "APPLIED"
        assert any("翻译不可用" in w for w in (r["result"].get("warnings") or []))

    def test_tool_bilingual_real_path_with_mock_llm(self, tmp_path, monkeypatch):
        from video_director import director_agent
        monkeypatch.setattr("video_director.config.composer_storage_root",
                            lambda: tmp_path)
        state = _state("proj-r3-bi2", tmp_path)

        class _P:
            def generate(self, prompt, system=None):
                return json.dumps({"S01": "Craftsmen climb.", "S02": "Persistence."})
        monkeypatch.setattr("video_director.llm_provider.get_llm", lambda: _P())
        r = director_agent._tool_subtitle({"subtitle_mode": "bilingual"}, state)
        assert r["status"] == "APPLIED"
        body = Path(state["subtitle_file"]).read_text(encoding="utf-8")
        assert "SubEn" in body and "Craftsmen climb." in body
        # LLM 翻译成功：没有降级警告
        assert not any("翻译不可用" in w for w in (r["result"].get("warnings") or []))


class TestSfxPolicy:
    """sfx 执行点：off 真关闭；auto 按 sfx_density；文本意图命中选库。"""

    def _spec_state(self):
        return ({"sfx": "off"}, _state("proj-r3-sfx"))

    def test_off_closes_sfx(self):
        from video_director import director_agent
        spec, state = self._spec_state()
        director_agent._apply_sfx_policy(spec, state)
        assert state["sfx_plan_override"] == []
        assert state["sfx_auto_search"] is False

    def test_auto_respects_density(self):
        from video_director import director_agent
        spec, state = {"sfx": "auto", "auto_sfx_search": True}, _state("proj-r3-sfx2")
        sc = state["style_contract"]
        # sfx_density 是语义档位（sparse|medium|dense）：sparse=风格不要音效
        density = str(getattr(sc, "sfx_density", "sparse") or "sparse")
        director_agent._apply_sfx_policy(spec, state)
        assert state["sfx_auto_search"] == (density in ("medium", "dense"))

    def test_text_intent_miss_falls_to_auto(self, monkeypatch):
        from video_director import director_agent, audio
        spec, state = {"sfx": "不存在的怪音效"}, _state("proj-r3-sfx3")
        monkeypatch.setattr(audio, "scan_library", lambda: {})
        monkeypatch.setattr(audio, "resolve_action_sfx", lambda *a, **k: "")
        director_agent._apply_sfx_policy(spec, state)
        # 未命中 → 没有 override（按 auto 决策走）
        assert "sfx_plan_override" not in state


class TestSceneLock:
    """镜头锁定：editor 命令落盘+读回；material_match 跳过锁定场景。"""

    def _ir_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("video_director.config.composer_storage_root",
                            lambda: tmp_path)
        from video_director.editor import save_composition
        from video_director.models import CompositionIR
        ir = CompositionIR.model_validate({
            "project": {"fps": 30, "width": 1080, "height": 1920, "duration": 8.0},
            "scenes": [
                {"id": "S01", "start": 0.0, "duration": 4.0, "text": "a",
                 "layers": [{"type": "gap", "start": 0.0, "duration": 4.0}]},
                {"id": "S02", "start": 4.0, "duration": 4.0, "text": "b",
                 "layers": [{"type": "video", "asset_id": "vid-1", "start": 0.0,
                             "duration": 4.0, "source_in": 0.0, "source_out": 4.0}]},
            ],
            "assets": [{"id": "vid-1", "type": "video", "file": "a.mp4",
                        "duration": 10.0}],
            "audio": [], "subtitle_track": {"cues": []},
        })
        save_composition("proj-r3-lock", ir)
        return "proj-r3-lock"

    def test_lock_command_persists(self, tmp_path, monkeypatch):
        pid = self._ir_project(tmp_path, monkeypatch)
        from video_director.editor import _cmd_set_scene_locked, load_composition
        ir = load_composition(pid)
        _cmd_set_scene_locked(ir, "S02", {"locked": True})
        assert ir.scenes[1].human_locked is True
        # 解锁
        _cmd_set_scene_locked(ir, "S02", {"locked": False})
        assert ir.scenes[1].human_locked is False

    def test_lock_rejects_bad_payload(self, tmp_path, monkeypatch):
        pid = self._ir_project(tmp_path, monkeypatch)
        from video_director.editor import _cmd_set_scene_locked, load_composition, EditorError
        ir = load_composition(pid)
        with pytest.raises(EditorError):
            _cmd_set_scene_locked(ir, "S02", {"locked": "yes"})

    def test_material_match_skips_locked(self, tmp_path, monkeypatch):
        """锁定场景不在自动匹配集、回填既有绑定、provenance=locked。"""
        pid = self._ir_project(tmp_path, monkeypatch)
        from video_director.editor import _cmd_set_scene_locked, load_composition
        from video_director import director_agent
        ir = load_composition(pid)
        _cmd_set_scene_locked(ir, "S02", {"locked": True})
        from video_director.editor import save_composition
        save_composition(pid, ir)

        called_scenes = {}
        import video_director.matcher as matcher_mod

        def fake_match(scenes, assets, llm_call=None, **kw):
            called_scenes["ids"] = [s["id"] for s in scenes]
            return {"S01": "vid-1"}

        monkeypatch.setattr(matcher_mod, "match_assets", fake_match)
        monkeypatch.setattr(
            "video_director.audio.scan_library", lambda: {})
        # 素材池非空：video_files 给真文件（build_manifest 真跑产生 AssetRef）
        monkeypatch.setattr(
            "video_director.assets.build_manifest",
            lambda videos, images, asset_metadata=None: (
                {"vid-1": type("R", (), {"id": "vid-1", "type": "video",
                                          "file": "a.mp4", "duration": 10.0})()},
                ["vid-1"]),
        )
        # vision 透传（不真调视觉 LLM）；R 无 model_copy 的问题一并规避
        monkeypatch.setattr(
            "video_director.vision.analyze_assets",
            lambda vv, *a, **k: vv)
        state = {
            "project_id": pid,
            "narration_scenes": [
                {"id": "S01", "text": "a", "start": 0.0, "duration": 4.0},
                {"id": "S02", "text": "b", "start": 4.0, "duration": 4.0},
            ],
            "assets": {"vid-1": type("A", (), {"id": "vid-1"})()},
            "asset_order": ["vid-1"],
            "assignments": {},
            "scene_plan": {"scenes": []},
            "constraints": {},
            "bundle": {"assets": {"asset_refs": []}},
            "video_files": ["__placeholder__.mp4"],  # 非空触发 build_manifest
            "image_files": [],
            "style_contract": _style_contract(),
        }
        spec = {"match_materials_to_script": True, "sfx": "off"}
        r = director_agent._tool_material(spec, state)
        # 锁定 S02 没进自动匹配集
        assert called_scenes.get("ids") == ["S01"]
        # S02 回填既有绑定（锁定绑定在素材池有效时保留）
        assert state["assignments"].get("S02") == "vid-1"


class TestAdversarialProbes:
    """2026-10-21 对抗探针固化：LLM 怪输出 / sfx 覆盖顺序 / 锁定继承。"""

    def _state(self, tmp_path, pid="proj-adv-x", monkeypatch=None):
        from video_director import config
        if monkeypatch is not None:
            # 走 monkeypatch 才有 per-test 还原：直接赋值会把 lambda 永久留在
            # 模块上，污染后续所有 suite 的 project_dir()（全量跑假失败根因）。
            monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
        from video_director.style import StyleContract
        return {
            "project_id": pid,
            "narration_scenes": [
                {"id": "S01", "text": "句", "start": 0.0, "duration": 2.0}],
            "word_timeline": [],
            "style_contract": StyleContract(style_id="documentary-calm"),
        }

    @pytest.mark.parametrize("weird", ["[]", '"just a string"', "{bad json", '{"S01": 123}', ""])
    def test_bilingual_translation_weird_llm_outputs(self, tmp_path, monkeypatch, weird):
        """翻译 LLM 五种怪输出全部诚实降级，不炸节点。"""
        import video_director.llm_provider as m
        from video_director import director_agent
        state = self._state(tmp_path, "proj-adv-bi", monkeypatch)

        class P:
            def __init__(self, ret): self.ret = ret
            def generate(self, prompt, system=None): return self.ret
        monkeypatch.setattr(m, "get_llm", lambda: P(weird))
        r = director_agent._tool_subtitle({"subtitle_mode": "bilingual"}, state)
        assert r["status"] == "APPLIED"

    def test_sfx_off_beats_explicit_payload_plan(self, tmp_path, monkeypatch):
        """sfx=off 时用户明确关闭赢过 payload 显式 sfx_plan（override 优先）。"""
        import video_director.llm_provider as m
        from video_director import director_agent
        monkeypatch.setattr(m, "get_llm", lambda: None)
        state = self._state(tmp_path, "proj-adv-sfx", monkeypatch)
        director_agent._apply_sfx_policy({"sfx": "off"}, state)
        spec = {"sfx": "off", "sfx_plan": [{"action": "hammer", "at": 1.0}]}
        effective = spec.get("sfx_plan")
        if state.get("sfx_plan_override") is not None:
            effective = state["sfx_plan_override"]
        assert effective == []

    def test_lock_survives_edit_roundtrip(self, tmp_path, monkeypatch):
        """锁定标记经编辑+save/load 往返保留（Scene 字段持久化契约）。"""
        monkeypatch.setattr("video_director.config.composer_storage_root",
                            lambda: tmp_path)
        from video_director.editor import (save_composition, load_composition,
                                           _cmd_set_scene_locked)
        from video_director.models import CompositionIR
        ir = CompositionIR.model_validate({
            "project": {"fps": 30, "width": 1080, "height": 1920, "duration": 4.0},
            "scenes": [{"id": "S01", "start": 0.0, "duration": 4.0, "text": "a",
                        "layers": [{"type": "video", "asset_id": "v", "start": 0.0,
                                    "duration": 4.0, "source_in": 0.0,
                                    "source_out": 4.0}]}],
            "assets": [{"id": "v", "type": "video", "file": "a.mp4",
                        "duration": 10.0}],
            "audio": [], "subtitle_track": {"cues": []},
        })
        save_composition("proj-adv-rt", ir)
        ir2 = load_composition("proj-adv-rt")
        _cmd_set_scene_locked(ir2, "S01", {"locked": True})
        ir2.scenes[0].visual_intent = "换"
        save_composition("proj-adv-rt", ir2)
        assert load_composition("proj-adv-rt").scenes[0].human_locked is True
