# -*- coding: utf-8 -*-
"""2026-10-26 用户裁决回归锁：LLM 停止即时生效 + 进度动态可见 + SDK 内重试关闭。

用户四问：
1. 「10s 后重试实测超 5 分钟」——openai SDK 默认 max_retries=2 且遵守网关
   Retry-After 头，外层每次尝试实际 ≈ 6-8 分钟。锁：SDK 内重试必须点掉。
2. 「停止为什么还要把当前步骤完成」——取消标志只在节点边界检测，正在执行的
   LLM 重试循环不响应。锁：generate 在尝试前 + 每秒等待间隙检测取消，抛
   GenerationCancelled → worker 收口 CANCELLED（非 FAILED）。
3. 「换供应商救不了正在跑的节点」——provider 实例构造时固化 base_url/model。
   锁：重试前 _refresh_runtime_config 刷新当前生效配置，变了就换新端点。
4. 「执行中只有三个字，不知道在干什么」——get_run 注入 RUNNING 节点的动作
   说明 + llm_retry 实时快照。锁：两者都进响应体。
"""
import sys
import types

import pytest

from video_director import llm_provider as lp
from video_director import workflow_run as wr


# ---------------------------------------------------------------------------
# 1. SDK 内部重试点掉
# ---------------------------------------------------------------------------
class TestSdkInternalRetryDisabled:
    def test_openai_client_max_retries_zero(self, monkeypatch):
        """_ensure_client 构造 OpenAI 客户端时必须显式 max_retries=0。"""
        captured = {}

        class _FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_module = types.ModuleType("openai")
        fake_module.OpenAI = _FakeOpenAI
        monkeypatch.setitem(sys.modules, "openai", fake_module)

        p = lp.OpenAICompatibleProvider.__new__(lp.OpenAICompatibleProvider)
        p._api_key = "k"
        p._base_url = "https://example.com/v1"
        p._model = "m"
        p._client = None
        p._ensure_client()

        assert captured.get("max_retries") == 0


# ---------------------------------------------------------------------------
# 2. 停止即时生效（重试循环内检测取消）
# ---------------------------------------------------------------------------
class _AlwaysFail:
    def __init__(self):
        self.calls = 0

    def chat_completions_create(self, **kwargs):
        self.calls += 1
        raise TimeoutError("read timeout")


def _fake_client(factory):
    class _C:
        chat = types.SimpleNamespace()
    _C.chat.completions = types.SimpleNamespace(create=factory.chat_completions_create)
    return _C()


def _provider():
    p = lp.OpenAICompatibleProvider.__new__(lp.OpenAICompatibleProvider)
    p._api_key = "k"
    p._base_url = "https://example.com/v1"
    p._model = "m"
    return p


class TestCancelInterruptsRetry:
    def _wire(self, p, factory, monkeypatch):
        p._client = _fake_client(factory)

        def _rebuild(self):
            self._client = _fake_client(factory)

        monkeypatch.setattr(lp.OpenAICompatibleProvider, "_ensure_client", _rebuild)
        # 等待循环里每秒 sleep 一次；这里 noop 让测试快速跑
        monkeypatch.setattr(lp.time, "sleep", lambda s: None)

    def test_cancel_before_attempt_raises_generation_cancelled(self, monkeypatch):
        """尝试前检测：cancel hook 返回 True → GenerationCancelled（不发起请求）。"""
        p = _provider()
        factory = _AlwaysFail()
        factory.calls = 0
        self._wire(p, factory, monkeypatch)
        lp.set_cancel_check(lambda: True)
        try:
            with pytest.raises(lp.GenerationCancelled):
                p.generate("hi")
            assert factory.calls == 0  # 一次请求都没发
        finally:
            lp.set_cancel_check(None)

    def test_cancel_during_wait_raises(self, monkeypatch):
        """等待间隙检测：第一次失败后、重试等待期间取消 → 立即中断。"""
        p = _provider()
        factory = _AlwaysFail()
        factory.calls = 0
        self._wire(p, factory, monkeypatch)
        # 第一次尝试允许，进入等待后第二秒触发取消
        state = {"enabled": False}

        def _cancel_after_first():
            return state["enabled"]

        lp.set_cancel_check(_cancel_after_first)
        try:
            # sleep 是 noop，无法真实「等待期间」切换；改为每次等待循环第一次
            # 检查即触发（monkeypatch 后 loop 内第一个 if 就命中）。这里用一个
            # 在等待循环里被反复调用的 hook：第一次返回 False，之后 True。
            calls = {"n": 0}

            def _toggle():
                calls["n"] += 1
                return calls["n"] > 1

            lp.set_cancel_check(_toggle)
            with pytest.raises(lp.GenerationCancelled):
                p.generate("hi")
            # 第一次尝试（hook 第 1 次调用 False）→ 失败 → 进入等待循环
            # hook 第 2 次调用 True → 中断。请求只发了 1 次。
            assert calls["n"] >= 2
        finally:
            lp.set_cancel_check(None)

    def test_no_cancel_check_retries_normally(self, monkeypatch):
        """无取消钩子时照常重试（不误伤正常路径）。"""
        p = _provider()
        factory = _AlwaysFail()
        factory.calls = 0
        self._wire(p, factory, monkeypatch)
        lp.set_cancel_check(None)
        with pytest.raises(RuntimeError) as exc:
            p.generate("hi")
        assert factory.calls == lp.LLM_MAX_RETRIES  # 10 次全尝试
        # 结束时取消钩子保持 None
        assert "失败" in str(exc.value)


# ---------------------------------------------------------------------------
# 3. 换供应商在重试期间生效
# ---------------------------------------------------------------------------
class TestRuntimeConfigRefresh:
    def test_refresh_flips_base_url_and_model(self, monkeypatch):
        p = _provider()
        # 运行时配置走 get_llm_config()：环境变量注入新端点
        monkeypatch.setenv("VD_LLM_API_KEY", "newkey")
        monkeypatch.setenv("VD_LLM_BASE_URL", "https://new-gw.example.com/v1")
        monkeypatch.setenv("VD_LLM_MODEL", "new-model")
        lp.set_llm_config("", None, None)  # 清 runtime → 回退 env
        try:
            changed = p._refresh_runtime_config()
            assert changed is True
            assert p._base_url == "https://new-gw.example.com/v1"
            assert p._model == "new-model"
        finally:
            lp.reset_llm_cache()

    def test_refresh_no_change_noop(self, monkeypatch):
        p = _provider()
        monkeypatch.delenv("VD_LLM_API_KEY", raising=False)
        changed = p._refresh_runtime_config()
        assert changed is False  # 无 key → 不刷新（保持原配置重试）
        assert p._base_url == "https://example.com/v1"


# ---------------------------------------------------------------------------
# 4. RUNNING 节点进度动态可见
# ---------------------------------------------------------------------------
class TestLiveProgressInRun:
    def test_running_step_gets_action_detail(self, monkeypatch, tmp_path):
        """RUNNING 节点无 detail 时 get_run 注入动作说明（非三字「执行中」）。"""
        import video_director.config as cfg
        (tmp_path / "c").mkdir(parents=True)
        monkeypatch.setattr(cfg, "composer_storage_root", lambda: tmp_path / "c")
        run_id = wr.start_run("compose", project_id="proj-live",
                              steps=["scene_plan"])
        wr.step_update(run_id, "scene_plan", "RUNNING")
        try:
            run = wr.get_run(run_id)
            scene = next(s for s in run["steps"] if s["id"] == "scene_plan")
            assert scene["status"] == "RUNNING"
            # 动作说明注入（含关键词）
            assert "分镜" in scene["detail"] or "大模型" in scene["detail"]
        finally:
            wr.delete_run(run_id, allow_waiting=True)

    def test_llm_retry_snapshot_in_response(self, monkeypatch, tmp_path):
        """活跃重试状态进 get_run 响应（attempt>0 且未过期 → llm_retry 非空）。"""
        import video_director.config as cfg
        (tmp_path / "c").mkdir(parents=True)
        monkeypatch.setattr(cfg, "composer_storage_root", lambda: tmp_path / "c")
        run_id = wr.start_run("compose", project_id="proj-retry", steps=["a"])
        try:
            # 伪造近期的重试快照
            lp._last_retry_state.update({
                "attempt": 3, "max": 10, "last_error": "Request timed out",
                "ts": __import__("time").time(),
            })
            run = wr.get_run(run_id)
            assert run.get("llm_retry") and run["llm_retry"]["attempt"] == 3
            assert "Request timed out" in run["llm_retry"]["last_error"]
        finally:
            lp._last_retry_state.update({"attempt": 0, "last_error": "", "ts": 0.0})
            wr.delete_run(run_id, allow_waiting=True)


# ---------------------------------------------------------------------------
# 5. worker 捕获 GenerationCancelled → CANCELLED
# ---------------------------------------------------------------------------
class TestWorkerCancelledCloseout:
    def test_generation_cancelled_finishes_cancelled(self, monkeypatch, tmp_path):
        """_compose_worker 收口 GenerationCancelled 为 CANCELLED（非 FAILED）。"""
        import video_director.config as cfg
        (tmp_path / "c").mkdir(parents=True)
        monkeypatch.setattr(cfg, "composer_storage_root", lambda: tmp_path / "c")
        run_id = wr.start_run("director_create", project_id="proj-cc", steps=["a"])

        def _boom(run_id, spec):
            raise lp.GenerationCancelled("创作已停止，文本模型调用被中断")

        monkeypatch.setattr(wr, "_execute_director_create", _boom)
        try:
            wr._compose_worker(run_id, {"_run_kind": "director_create",
                                        "project_id": "proj-cc",
                                        "director_plan": {}})
            run = wr.get_run(run_id)
            assert run["status"] == "CANCELLED"
            assert "停止" in (run.get("error") or "")
            # 收口后取消钩子已清除（finally）
            assert lp._cancel_check is None
        finally:
            wr.delete_run(run_id, allow_waiting=True)
            lp.set_cancel_check(None)