# -*- coding: utf-8 -*-
"""2026-10-20 用户裁决：LLM 调用统一 10 次重试 + 单次超时 ≥3 分钟。

三通道全覆盖：文本（llm_provider.OpenAICompatibleProvider）、
视觉（vision.FrameVisionProvider）、视觉QA（visual_qa.frame_vision_visual_qa）。
"""
import json
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

    def test_retry_until_success(self, monkeypatch):
        p = self._provider()
        flaky = _Flaky(fail_times=3, exc_factory=lambda: TimeoutError("read timeout"))
        # 直接替换 client（绕过真实 OpenAI 构造）
        class _C:
            chat = type("chat", (), {})()
        p._client = _C
        monkeypatch.setattr(_C, "chat", type("chat", (), {
            "completions": type("completions", (), {
                "create": staticmethod(flaky.chat_completions_create)})()
        })(), raising=False)
        monkeypatch.setattr(llm_provider.time, "sleep", lambda s: None)
        out = p.generate("hi")
        assert out == "好的"
        assert flaky.calls == 4  # 3 次失败 + 1 次成功

    def test_gives_up_after_10(self, monkeypatch):
        p = self._provider()
        flaky = _Flaky(fail_times=99, exc_factory=lambda: TimeoutError("t"))
        class _C:
            chat = type("chat", (), {})()
        p._client = _C
        monkeypatch.setattr(_C, "chat", type("chat", (), {
            "completions": type("completions", (), {
                "create": staticmethod(flaky.chat_completions_create)})()
        })(), raising=False)
        monkeypatch.setattr(llm_provider.time, "sleep", lambda s: None)
        try:
            p.generate("hi")
            raise AssertionError("应抛 RuntimeError")
        except RuntimeError as exc:
            assert "重试 10 次" in str(exc) or "10 次" in str(exc)
        assert flaky.calls == 10

    def test_4xx_no_retry(self, monkeypatch):
        p = self._provider()
        class _ApiErr(Exception):
            status_code = 401
        flaky = _Flaky(fail_times=99, exc_factory=_ApiErr)
        class _C:
            chat = type("chat", (), {})()
        p._client = _C
        monkeypatch.setattr(_C, "chat", type("chat", (), {
            "completions": type("completions", (), {
                "create": staticmethod(flaky.chat_completions_create)})()
        })(), raising=False)
        monkeypatch.setattr(llm_provider.time, "sleep", lambda s: None)
        try:
            p.generate("hi")
            raise AssertionError("应抛 RuntimeError")
        except RuntimeError:
            pass  # 4xx 快速失败（错误文本随真实异常，mock 异常 str 为空）
        assert flaky.calls == 1  # 不重试

    def test_constants_meet_user_ruling(self):
        assert llm_provider.LLM_MAX_RETRIES == 10
        assert llm_provider.LLM_ATTEMPT_TIMEOUT_S >= 180


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
