# -*- coding: utf-8 -*-
"""2026-10-25 生产事故回归锁：意图解析增强层 15s 短耐心。

/api/director/create 的 HTTP 线程同步调 resolve_director_intents——
网关挂死时 _llm_resolve 若共享主链 10 次重试（10×130s），创建请求
20+ 分钟不返回 run_id，前端「点了没反应」。增强层必须独立快速失败。
"""
import time
from pathlib import Path

import pytest

from video_director import director_intent as dint


class TestIntentLlmFastFail:
    def test_llm_resolve_call_times_out_fast(self):
        """generate 挂死 >15s → _llm_resolve_call 15s 内返回 None（降规则层）。"""

        class _Hung:
            def generate(self, prompt, system=None):
                time.sleep(60)  # 模拟网关挂死
                return "{}"

        t0 = time.time()
        out = dint._llm_resolve_call(_Hung(), "p", "s")
        elapsed = time.time() - t0
        assert out is None
        assert elapsed < 20, f"15s 超时应在 20s 内返回，实际 {elapsed:.1f}s"

    def test_llm_resolve_call_success_passthrough(self):
        class _Fast:
            def generate(self, prompt, system=None):
                return '  {"voice": "温柔女声"}  '

        out = dint._llm_resolve_call(_Fast(), "p", "s")
        assert out == '{"voice": "温柔女声"}'

    def test_llm_resolve_call_exception_returns_none(self):
        class _Boom:
            def generate(self, prompt, system=None):
                raise RuntimeError("LLM generate 失败（重试 10 次后）: x")

        out = dint._llm_resolve_call(_Boom(), "p", "s")
        assert out is None

    def test_llm_resolve_hung_gateway_degrades_to_rule(self):
        """端到端：provider 挂死时 resolve_director_intents 在 ~15s 内用
        规则层结果返回（不阻塞、不炸、_unresolved 如实记录）。"""

        class _Hung:
            def generate(self, prompt, system=None):
                time.sleep(60)
                return "{}"

        import video_director.llm_provider as lp
        orig = lp.get_llm
        lp.get_llm = lambda: _Hung()
        try:
            t0 = time.time()
            # 「40秒以上」是规则层可解析的确定性意图
            r = dint.resolve_director_intents("总时长40秒以上")
            elapsed = time.time() - t0
            assert elapsed < 25, f"网关挂死时整体应在 25s 内返回，实际 {elapsed:.1f}s"
            assert r.get("duration") == "40+", "规则层意图必须仍然生效"
        finally:
            lp.get_llm = orig

    def test_llm_resolve_no_provider_rule_only(self):
        import video_director.llm_provider as lp
        orig = lp.get_llm
        lp.get_llm = lambda: None
        try:
            r = dint.resolve_director_intents("总时长40秒以上")
            assert r.get("duration") == "40+"
        finally:
            lp.get_llm = orig

class TestRetryPathUsesRuleLayerOnly:
    """2026-10-27 用户报障「意图 15s 超时一直报」：retry 路径重建 spec 时
    与 create 同分层——同步只跑规则层（毫秒级），LLM 泛化层由 understand
    节点的 merge_llm_intents 后台补齐。锁：retry spec 重建代码不再引用
    resolve_director_intents（同步 LLM 版），引用 resolve_rule_intents。
    """

    def test_retry_spec_rebuild_uses_rule_layer(self):
        root = Path(__file__).resolve().parents[1]
        src = (root / "video_director" / "workflow_run.py").read_text(encoding="utf-8")
        # retry 重建 spec 的意图解析必须走规则层（不烧同步 LLM 15s）
        assert "director_intent.resolve_rule_intents(" in src
        assert "director_intent.resolve_director_intents(" not in src

    def test_rule_layer_has_no_llm_call(self):
        """规则层纯函数：无 provider 引用（毫秒级返回）。"""
        from video_director import director_intent as dint
        r = dint.resolve_rule_intents("总时长40秒以上，加字幕，慢动作")
        assert str(r.get("duration") or "").startswith("40")
        assert r.get("subtitle_mode") in ("on", "plain")  # 规则层字幕口径
        assert r.get("clip_speed") == "0.8"
