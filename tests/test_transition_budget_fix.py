# -*- coding: utf-8 -*-
"""转场预算同源 + 视觉 LLM 截断修复回归（2026-10-22）。

生产实锚 web-8fd0ff30/run-d1812b798424：用户显式 crossfade，5 场景执行点写满
4 处 crossfade 进 IR，校验点漏传第五参回落全局默认上限 3 →
TRANSITION_BUDGET_EXCEEDED 恒 REJECT，执行与校验自相矛盾且 retry 不可能过。

三根因：
1. director_agent._tool_composition 校验预算与执行预算必须同源
2. vision.py max_tokens=500 截断（「无 JSON」假阴性 + 空内容假阴性）
3. pipeline 修复后重检同样漏传预算（同病同修）
"""
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 根因 1：校验预算必须与执行预算同源
# ---------------------------------------------------------------------------

def _ir_5_scenes_with_crossfades():
    """5 场景 IR，前 4 个场景 transition_out=crossfade（写满全部 4 边界）。"""
    from video_director.models import CompositionIR, SceneIR, Layer, LayerType, TransitionOut
    scenes = [
        SceneIR(
            id=f"S{i:02d}", start=i * 3.0, duration=3.0, text=f"第{i}镜",
            layers=[Layer(type=LayerType.gap, start=0.0, duration=3.0)],
            transition_out=TransitionOut(type="crossfade", duration=0.5) if i < 4
            else TransitionOut(),
        )
        for i in range(5)
    ]
    return CompositionIR.model_validate({
        "project": {"fps": 30, "width": 1080, "height": 1920, "duration": 15.0},
        "scenes": [json.loads(s.model_dump_json()) for s in scenes],
        "assets": [], "audio": [], "subtitle_track": {"cues": []},
    })


class TestValidatorTransitionBudget:
    """validate_composition 第五参语义：显式写满边界合法，默认上限保留。"""

    def test_explicit_full_boundaries_pass(self):
        """用户显式 crossfade 写满 4 边界，校验预算=边界数 → PASS。"""
        from video_director import validator
        ir = _ir_5_scenes_with_crossfades()
        check = validator.validate_composition(
            ir, {}, 15.0, subtitle_entries=None, transition_budget=4)
        assert check.ok, check.to_dict()
        assert not any(
            i.code == "TRANSITION_BUDGET_EXCEEDED" for i in check.issues)

    def test_default_budget_still_rejects_full_boundaries(self):
        """同一 IR 用默认预算（全局上限 3）→ 仍 REJECT（上限语义不放宽）。"""
        from video_director import config, validator
        ir = _ir_5_scenes_with_crossfades()
        check = validator.validate_composition(ir, {}, 15.0, subtitle_entries=None)
        assert not check.ok
        assert any(
            i.code == "TRANSITION_BUDGET_EXCEEDED"
            and "超上限 3" in i.message for i in check.issues)
        assert config.TRANSITION_MAX_MAJOR_EFFECTS == 3  # 全局默认未被污染


class TestCompositionValidationBudgetSameSource:
    """_tool_composition 调用点参数同源：用户显式时校验收到边界数。

    完整链路走真 RulePlanner（确定性）+ 图片素材（plan 不读文件、
    无 video 层则不触发 probe_media）。
    """

    def _state(self):
        from video_director.models import AssetRef
        from video_director import style as style_mod
        assets = {}
        for i in range(5):
            assets[f"img-{i}"] = AssetRef(
                id=f"img-{i}", type="image",
                file=f"D:/nonexistent/img-{i}.png", width=64, height=64)
        return {
            "project_id": "proj-budget-src",
            "narration_scenes": [
                {"id": f"S{i:02d}", "text": f"第{i}镜", "start": i * 3.0,
                 "duration": 3.0}
                for i in range(5)
            ],
            "assets": assets,
            "asset_order": [f"img-{i}" for i in range(5)],
            "style_contract": style_mod.get_style("documentary-calm")
            or style_mod.StyleContract(style_id="documentary-calm"),
            "assignments": {},
        }

    def test_explicit_crossfade_passes_validation(self, tmp_path, monkeypatch):
        """5 场景 + 用户显式 crossfade：端到端校验 PASS，不再 REJECT。"""
        monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
        from video_director import director_agent
        from video_director.models import TransitionOut
        state = self._state()
        r = director_agent._tool_composition(
            {"transition_mode": "crossfade"}, state)
        assert r["status"] == "APPLIED", r
        ir = state["ir"]
        crossfades = [sc for sc in ir.scenes
                      if sc.transition_out.type == "crossfade"]
        assert len(crossfades) == 4  # 写满 4 边界且校验放行

    def test_explicit_crossfade_passes_boundary_budget_to_validator(
            self, tmp_path, monkeypatch):
        """捕获 validate_composition 收到的 transition_budget：显式时=边界数。"""
        monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
        from video_director import director_agent, validator
        captured = {}

        real = validator.validate_composition

        def _capture(ir, assets, narration_duration, subtitle_entries=None,
                     transition_budget=None):
            captured["budget"] = transition_budget
            return real(ir, assets, narration_duration,
                        subtitle_entries=subtitle_entries,
                        transition_budget=transition_budget)

        monkeypatch.setattr(
            "video_director.director_agent.validator.validate_composition",
            _capture)
        state = self._state()
        r = director_agent._tool_composition(
            {"transition_mode": "crossfade"}, state)
        assert r["status"] == "APPLIED", r
        assert captured["budget"] == 4  # len(ir.scenes)-1，显式指令写满边界合法

    def test_implicit_mode_passes_style_budget_to_validator(
            self, tmp_path, monkeypatch):
        """未显式指定：校验预算=风格预算（与提案预算同源，不再回落全局默认）。"""
        monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
        from video_director import director_agent, validator
        captured = {}

        real = validator.validate_composition

        def _capture(ir, assets, narration_duration, subtitle_entries=None,
                     transition_budget=None):
            captured["budget"] = transition_budget
            return real(ir, assets, narration_duration,
                        subtitle_entries=subtitle_entries,
                        transition_budget=transition_budget)

        monkeypatch.setattr(
            "video_director.director_agent.validator.validate_composition",
            _capture)
        state = self._state()
        r = director_agent._tool_composition({"transition_mode": "cut"}, state)
        assert r["status"] == "APPLIED", r
        # documentary-calm 风格预算 = 1
        assert captured["budget"] == state["style_contract"].transition_budget == 1


class TestPipelineRecheckBudget:
    """旧链 pipeline 修复后重检（640 行）预算必须与首轮校验（577 行）同源。"""

    def test_recheck_source_matches_first_validation(self):
        """静态断言：重检调用必须带 transition_budget（防回归的文本锁）。"""
        src = Path("video_director/pipeline.py").read_text(encoding="utf-8")
        recheck = src[src.index("recheck = validator.validate_composition"):]
        recheck = recheck[:recheck.index(")")]
        assert "transition_budget=transition_budget" in recheck, (
            "修复后重检漏传 transition_budget 会回落全局默认 3——"
            "与首轮校验上限语义分叉")


# ---------------------------------------------------------------------------
# 根因 2：vision max_tokens 截断
# ---------------------------------------------------------------------------

class _FakeCtx:
    def __init__(self, data: bytes):
        self._d = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._d


class TestVisionMaxTokens:
    """vision.FrameVisionProvider：1500 上限 + 截断时诚实报错。"""

    def _provider(self, monkeypatch, tmp_path, responder):
        """构建绕过 provider_matrix/抽帧的 FrameVisionProvider，返回 (p, payload_box)。"""
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        from video_director import vision
        payload_box = {}

        def fake_urlopen(req, timeout=None):
            payload_box["payload"] = json.loads(req.data.decode())
            return _FakeCtx(responder())

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        from video_director.models import AssetRef
        a = AssetRef(id="v1", type="video",
                     file=str(tmp_path / "x.mp4"), duration=5.0)
        monkeypatch.setattr(vision, "extract_frames", lambda *a, **k: ["f1.jpg"])
        monkeypatch.setattr(Path, "read_bytes", lambda self: b"x")
        monkeypatch.setattr("video_director.provider_matrix.require",
                            lambda *a, **k: None)
        p = vision.FrameVisionProvider(
            api_key="k", base_url="https://example.com/v1", model="m")
        return p, a, payload_box

    def test_max_tokens_raised_to_1500(self, tmp_path, monkeypatch):
        """请求 payload 的 max_tokens 必须=1500（500 会截断四字段全量输出）。"""
        ok_resp = json.dumps({"choices": [{"message": {"content": json.dumps({
            "description": "d", "tags": ["t"],
            "narrative_concepts": ["c"], "usable_for": ["hook"]})},
            "finish_reason": "stop"}]})
        p, a, box = self._provider(
            monkeypatch, tmp_path, lambda: ok_resp.encode())
        p.analyze(a, tmp_path)
        assert box["payload"]["max_tokens"] == 1500

    def test_truncation_error_mentions_max_tokens(self, tmp_path, monkeypatch):
        """finish_reason=length 且 JSON 被掐断 → 报「截断」而非「无 JSON」。"""
        # 合法 JSON 开头但 rfind("}") <= find("{")：截断的典型形态
        truncated = '{"description": "工匠在烈日下搬运石块，画面'
        resp = json.dumps({"choices": [{"message": {"content": truncated},
                                        "finish_reason": "length"}]})
        p, a, _box = self._provider(
            monkeypatch, tmp_path, lambda: resp.encode())
        from video_director.vision import VisionError
        with pytest.raises(VisionError) as exc_info:
            p.analyze(a, tmp_path)
        msg = str(exc_info.value)
        assert "截断" in msg and "finish_reason=length" in msg
        assert "无 JSON" not in msg

    def test_non_length_garbage_still_reports_no_json(self, tmp_path, monkeypatch):
        """非截断的垃圾输出（finish_reason=stop）保留「无 JSON」原报错。"""
        resp = json.dumps({"choices": [{"message": {"content": "乱七八糟"},
                                        "finish_reason": "stop"}]})
        p, a, _box = self._provider(
            monkeypatch, tmp_path, lambda: resp.encode())
        from video_director.vision import VisionError
        with pytest.raises(VisionError) as exc_info:
            p.analyze(a, tmp_path)
        assert "无 JSON" in str(exc_info.value)
