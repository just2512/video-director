# -*- coding: utf-8 -*-
"""2026-10-20 用户裁决：LLM 调用统一 10 次重试 + 单次超时 ≥3 分钟。

三通道全覆盖：文本（llm_provider.OpenAICompatibleProvider）、
视觉（vision.FrameVisionProvider）、视觉QA（visual_qa.frame_vision_visual_qa）。
"""
import json
import time
from pathlib import Path

from video_director import llm_provider


class _Flaky:
    """前 N-1 次抛超时，第 N 次成功。"""

    def __init__(self, fail_times, exc_factory):
        self.calls = 0
        self.fail_times = fail_times
        self.exc_factory = exc_factory

    def chat_completions_create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc_factory()
        return _ok_response()


def _ok_response():
    class _Msg:
        class message:
            content = "好的"
    class _Resp:
        choices = [_Msg()]
    return _Resp()


class TestTextChannelRetry:
    """文本通道：10 次重试、可恢复错误重试、4xx 快速失败。"""

    def _provider(self):
        p = llm_provider.OpenAICompatibleProvider.__new__(
            llm_provider.OpenAICompatibleProvider)
        p._api_key = "k"
        p._base_url = "https://example.com/v1"
        p._model = "test-model"
        return p

    @staticmethod
    def _fake_client(flaky):
        """带 flaky.create 的假 client。"""
        class _C:
            chat = type("chat", (), {})()
        _C.chat.completions = type("completions", (), {
            "create": staticmethod(flaky.chat_completions_create)})()
        return _C()

    def _wire(self, p, flaky, monkeypatch):
        """client 接线 + 重建语义：生产代码重试时 _client=None 后调
        _ensure_client 重建——注入的重建函数返回同款 flaky client，
        成败节奏由 _Flaky 全局计数控制（跨 client 对象）。"""
        p._client = self._fake_client(flaky)

        def _rebuild(self):
            self._client = self._fake_client(flaky)

        p._fake_client = self._fake_client  # 实例上挂同款工厂
        monkeypatch.setattr(llm_provider.OpenAICompatibleProvider,
                            "_ensure_client", _rebuild)
        monkeypatch.setattr(llm_provider.time, "sleep", lambda s: None)

    def test_retry_until_success(self, monkeypatch):
        p = self._provider()
        flaky = _Flaky(fail_times=2, exc_factory=lambda: TimeoutError("read timeout"))
        self._wire(p, flaky, monkeypatch)
        out = p.generate("hi")
        assert out == "好的"
        # 2026-10-27 用户裁决 LLM_MAX_RETRIES 10→3：流式根修后 mock 非流式
        # 结构 → 流式 TypeError → 降级非流式。2 次网络失败 + 流式第 3 次
        # TypeError 降级 + 非流式第 4 次成功 = 4 次调用。
        assert flaky.calls == 4

    def test_gives_up_after_3(self, monkeypatch):
        # 2026-10-27 用户裁决契约翻转：重试 10 次 → 3 次（10 次太久）。
        p = self._provider()
        flaky = _Flaky(fail_times=99, exc_factory=lambda: TimeoutError("t"))
        self._wire(p, flaky, monkeypatch)
        try:
            p.generate("hi")
            raise AssertionError("应抛 RuntimeError")
        except RuntimeError as exc:
            assert "重试 3 次" in str(exc) or "3 次" in str(exc)
        # 2026-10-27 LLM_MAX_RETRIES 10→3。mock create 不区分 stream——流式
        # create 直接 raise TimeoutError（不进 TypeError 降级分支），每次尝试
        # 1 发：3 次尝试 = 3 发后放弃。
        assert flaky.calls == 3

    def test_4xx_no_retry(self, monkeypatch):
        p = self._provider()
        class _ApiErr(Exception):
            status_code = 401
        flaky = _Flaky(fail_times=99, exc_factory=_ApiErr)
        self._wire(p, flaky, monkeypatch)
        try:
            p.generate("hi")
            raise AssertionError("应抛 RuntimeError")
        except RuntimeError:
            pass  # 4xx 快速失败（错误文本随真实异常，mock 异常 str 为空）
        assert flaky.calls == 1  # 不重试

    def test_constants_meet_user_ruling(self):
        # 2026-10-27 用户裁决契约翻转：重试 10 → 3 次（10 次太久）。
        # 2026-10-25 用户裁决保持：固定 10s 重试间隔（不再递增）。
        assert llm_provider.LLM_MAX_RETRIES == 3
        assert llm_provider.LLM_RETRY_WAIT_S == 10.0
        # 单次尝试超时 120s：流式语义=相邻数据块间隔（长生成不再误杀）。
        assert llm_provider.LLM_ATTEMPT_TIMEOUT_S == 120

    def test_client_timeout_is_scalar_for_old_sdk_safety(self, monkeypatch):
        """回归锁（生产实录 2026-11-18）：openai 3.29.0（生产 venv）下 dict timeout
        会在真正发请求时炸 `'dict' object cannot be interpreted as an integer`。
        client 层只能用标量；dict 只允许出现在受支持的 per-call 上。"""
        import openai as openai_mod
        captured = {}

        def fake_openai(**kwargs):
            captured["timeout"] = kwargs.get("timeout")

            class _Stub:
                pass
            return _Stub()

        monkeypatch.setattr(openai_mod, "OpenAI", fake_openai)
        p = self._provider()
        p._client = None
        p._ensure_client()
        assert isinstance(captured["timeout"], float)
        assert captured["timeout"] == llm_provider.LLM_ATTEMPT_TIMEOUT_S

    def test_request_timeout_kwargs_version_aware(self):
        """per-call：新版给 dict（connect/read 拆分），旧版只给标量（不炸）。"""

        class _New:
            _supports_timeout_dict = True

        class _Old:
            _supports_timeout_dict = False

        new = llm_provider._request_timeout_kwargs(_New(), 10.0, 120.0)
        assert new == {"timeout": {"connect": 10.0, "read": 120.0, "write": 120.0, "pool": 10.0}}
        old = llm_provider._request_timeout_kwargs(_Old(), 10.0, 120.0)
        assert old == {"timeout": 120.0}
        assert isinstance(old["timeout"], float)

    def test_probe_timeout_dict_support_detects_old_sdk_typeerror(self, monkeypatch):
        """功能探测：openai 旧版在 timeout 归一化阶段抛 TypeError → 判不支持。
    （版本号不可靠——生产 venv openai 3.29.0 反而是「不支持 dict」的那一档。）"""
        import sys
        import types

        class _OldCompletions:
            def create(self, **kwargs):
                raise TypeError("'dict' object cannot be interpreted as an integer")

        class _OldClient:
            chat = types.SimpleNamespace(completions=_OldCompletions())

        def fake_openai(**kwargs):
            return _OldClient()

        fake = types.ModuleType("openai")
        fake.OpenAI = fake_openai
        monkeypatch.setitem(sys.modules, "openai", fake)
        assert not llm_provider._probe_timeout_dict_support()

    def test_probe_timeout_dict_support_accepts_new_sdk(self, monkeypatch):
        """新 SDK：dict 被接受、请求走到发请求阶段（网络错误）→ 判支持。"""
        import sys
        import types

        class _NewCompletions:
            def create(self, **kwargs):
                raise ConnectionError("unreachable (dict was accepted)")

        class _NewClient:
            chat = types.SimpleNamespace(completions=_NewCompletions())

        def fake_openai(**kwargs):
            return _NewClient()

        fake = types.ModuleType("openai")
        fake.OpenAI = fake_openai
        monkeypatch.setitem(sys.modules, "openai", fake)
        assert llm_provider._probe_timeout_dict_support()

    def test_generate_optional_times_out_fast(self, monkeypatch):
        """可选增强短耐心：generate 挂死时到点即抛 OptionalLLMUnavailable，
        不把主链 3×120s 重试拖进可选步骤（生产：匹配画面卡 6 分钟）。"""
        p = self._provider()

        class _Hung:
            def chat_completions_create(self, **kwargs):
                import time as _t
                _t.sleep(30)
                raise AssertionError("不应被消化为返回值")
        p._client = self._fake_client(_Hung())
        monkeypatch.setattr(llm_provider.time, "sleep", lambda s: None)

        t0 = time.time()
        try:
            p.generate_optional("hi", timeout_s=1.0)
            raise AssertionError("应抛 OptionalLLMUnavailable")
        except llm_provider.OptionalLLMUnavailable:
            pass
        elapsed = time.time() - t0
        assert elapsed < 10, f"短耐心应在 10s 内返回，实际 {elapsed:.1f}s"

    def test_generate_optional_passes_success_through(self, monkeypatch):
        p = self._provider()
        flaky = _Flaky(fail_times=0, exc_factory=lambda: TimeoutError("t"))
        p._client = self._fake_client(flaky)
        monkeypatch.setattr(llm_provider.time, "sleep", lambda s: None)
        assert p.generate_optional("hi", timeout_s=30.0) == "好的"

    def test_reasoning_never_reaches_the_caller_as_content(self, monkeypatch):
        """回归锁（生产 2026-11-18）：reasoning_content 是私有思考，绝不能作为
        返回值给调用方。分镜规划/素材匹配都期望 JSON——若把模型的自然语言推理
        文本当 content 返回，调用方 find("{") 失败，报「分镜节点没有返回合法
        JSON」。只吐 reasoning（content 全空）时必须按空 content 处理。"""

        class _Delta:
            def __init__(self, content=None, reasoning=None):
                self.content = content
                self.reasoning_content = reasoning

        class _Chunk:
            def __init__(self, delta):
                self.choices = [type("c", (), {"delta": delta})()]

        def fake_create(**kwargs):
            assert kwargs.get("stream") is True
            # 思考型模型：只有推理、没有任何可见内容
            return iter([_Chunk(_Delta(reasoning="让我想想分镜该怎么设计……"))])

        class _C:
            chat = type("chat", (), {})()
        _C.chat.completions = type("completions", (), {
            "create": staticmethod(fake_create)})()
        p = self._provider()
        p._client = _C()
        monkeypatch.setattr(llm_provider.time, "sleep", lambda s: None)
        # 不得把推理文本当返回值；3 次尝试后按 RuntimeError 收口
        try:
            p.generate("hi")
            raise AssertionError("只吐 reasoning 不应作为成功返回")
        except RuntimeError:
            pass

    def test_empty_stream_fallback_uses_short_patience(self, monkeypatch):
        """回归锁（生产 2026-11-18）：网关返回 0 chunk 的完全空流时，非流式兜底
        不得沿用 120s 读超时——实测慢网关上非流式单发 96~131s 且半数失败，把单次
        调用打成「流式 + 非流式 120s」再叠加 3 次重试 = 十几分钟卡死。兜底预算必须
        被压到 EMPTY_STREAM_FALLBACK_TIMEOUT_S。"""
        p = self._provider()
        seen_timeouts = []

        class _Completions:
            def create(self, **kwargs):
                seen_timeouts.append(kwargs.get("timeout"))
                # 流式一律空（0 chunk）；非流式也返回空 → 进重试
                return iter([])

        class _C:
            chat = type("chat", (), {})()
        _C.chat.completions = type("completions", (), {
            "create": staticmethod(_Completions().create)})()
        p._client = _C()
        p._supports_timeout_dict = True
        p._read_timeout_s = llm_provider.LLM_ATTEMPT_TIMEOUT_S
        monkeypatch.setattr(llm_provider.time, "sleep", lambda s: None)

        try:
            p.generate("hi")
            raise AssertionError("3 次全空后应抛 RuntimeError")
        except RuntimeError:
            pass

        # 第 1 发是流式（read=120s）；从第 2 发起是空流兜底的非流式，read 必须被压到 45s
        assert seen_timeouts, seen_timeouts
        fallback_reads = [
            t["read"] if isinstance(t, dict) else t
            for t in seen_timeouts[1:]
        ]
        assert fallback_reads, seen_timeouts
        for r in fallback_reads:
            assert r == llm_provider.EMPTY_STREAM_FALLBACK_TIMEOUT_S == 45.0, seen_timeouts

    def test_generate_prefers_visible_content_over_private_reasoning(self):
        """契约：reasoning_content 是私有思考绝不外流——只要有任何 visible
        content，返回值就只用 content（reasoning 不得混入，也不得进 on_delta 流）。"""

        class _Delta:
            def __init__(self, content=None, reasoning=None):
                self.content = content
                self.reasoning_content = reasoning

        class _Chunk:
            def __init__(self, delta):
                self.choices = [type("c", (), {"delta": delta})()]

        def fake_create(**kwargs):
            return iter([
                _Chunk(_Delta(reasoning="private reasoning must never be streamed")),
                _Chunk(_Delta(content="O")),
                _Chunk(_Delta(content="K")),
            ])

        class _C:
            chat = type("chat", (), {})()
        _C.chat.completions = type("completions", (), {
            "create": staticmethod(fake_create)})()
        p = self._provider()
        p._client = _C()
        assert p.generate("reply OK") == "OK"

        fragments = []
        assert p.generate_stream("reply OK", on_delta=fragments.append) == "OK"
        assert fragments == ["O", "K"]
        assert "private reasoning" not in "".join(fragments)

    def test_retry_drops_dead_client_connection(self, monkeypatch):
        """2026-10-25 生产事故回归锁：网关挂死时 httpx 连接池里的死连接
        必须在重试时被丢弃（self._client = None + 重建），不能复用。"""
        p = self._provider()
        calls = {"n": 0, "clients": []}

        def _make_client():
            client_id = object()

            class _C:
                chat = type("chat", (), {})()
            _C.chat.completions = type("completions", (), {
                "create": staticmethod(
                    lambda **kw: _attempt(client_id))})()
            _C._id = client_id
            return _C()

        def _attempt(client_id):
            calls["n"] += 1
            calls["clients"].append(client_id)
            if calls["n"] == 1:
                raise ConnectionError("网关挂死")
            return _ok_response()

        p._client = _make_client()
        # _ensure_client 记录重建：证明重试真的丢弃旧 client
        rebuilt = {"n": 0}
        monkeypatch.setattr(llm_provider.OpenAICompatibleProvider,
                            "_ensure_client",
                            lambda self: (rebuilt.__setitem__("n", rebuilt["n"] + 1),
                                          setattr(self, "_client", _make_client())))
        monkeypatch.setattr(llm_provider.time, "sleep", lambda s: None)
        out = p.generate("hi")
        assert out == "好的"
        assert rebuilt["n"] >= 1                     # 重试触发了 client 重建
        assert calls["clients"][0] is not calls["clients"][1]  # 两次尝试用不同 client

    def test_retry_state_snapshot(self, monkeypatch):
        """进程级重试状态：retry_state() 透出重试进度（诊断可查）。"""
        p = self._provider()
        flaky = _Flaky(fail_times=1, exc_factory=lambda: TimeoutError("t"))
        self._wire(p, flaky, monkeypatch)
        p.generate("hi")
        st = llm_provider.retry_state()
        assert st["attempt"] == 1 and "t" in st["last_error"]


class TestVisionChannelRetry:
    """视觉通道：同样 10 次重试（mock urlopen 层）。"""

    def test_vision_retries(self, monkeypatch, tmp_path):
        calls = {"n": 0}
        class _TimeoutErr(Exception):
            pass
        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            assert timeout == 180, f"单次超时应为180s, got {timeout}"
            if calls["n"] < 3:
                raise _TimeoutErr("read timed out")
            return _FakeCtx(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())
        class _FakeCtx:
            def __init__(self, data): self._d = data
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return self._d
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        from video_director import vision
        # 直接测 analyze 的 HTTP 段：绕过 provider_matrix 与抽帧
        from video_director.models import AssetRef
        a = AssetRef(id="v1", type="video", file=str(tmp_path / "x.mp4"), duration=5.0)
        monkeypatch.setattr(vision, "extract_frames", lambda *a, **k: ["f1.jpg"])
        monkeypatch.setattr(Path, "read_bytes", lambda self: b"x")
        monkeypatch.setattr("video_director.provider_matrix.require", lambda *a, **k: None)
        p = vision.FrameVisionProvider(api_key="k", base_url="https://example.com/v1", model="m")
        result = p.analyze(a, tmp_path)
        assert result["description"] == ""
        assert calls["n"] == 3
