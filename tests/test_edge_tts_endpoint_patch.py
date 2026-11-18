"""edge-tts 端点自愈补丁契约（2026-10-13 独立性根修引入）。

锁三件事：
1. video_director 包 import 后，WSS_URL 不再指向被微软封禁的旧端点
2. DRM 日期解析 locale 无关（parse_rfc2616_date 替换为 email.utils 实现）
3. 上游 edge-tts 若已修（WSS_URL 已是新端点），补丁保持 no-op 不破坏
"""
import inspect


def test_edge_tts_endpoint_patch_active():
    import video_director  # 触发 __init__ 补丁
    import edge_tts.constants as etc

    wss = str(getattr(etc, "WSS_URL", ""))
    # 旧端点（微软 2026-10 起 403）必须已被换掉
    assert "speech.platform.bing.com" not in wss or "Ocp-Apim-Subscription-Key" in wss, (
        f"合成端点仍指向被封禁的旧 host: {wss}"
    )
    # 新端点形态（上游 #491 同款）或上游已自带修复
    assert ("api.msedgeservices.com" in wss) or ("Ocp-Apim" in wss), wss


def test_edge_tts_drm_date_parse_locale_independent():
    import video_director  # noqa: F401
    import edge_tts.drm as etd

    src = inspect.getsource(etd.DRM.parse_rfc2616_date)
    # 上游 strptime("%a, %d %b %Y") 在非英语 locale 下解析必败（如中文
    # 星期名）；补丁后必须用 email.utils.parsedate_to_datetime
    assert "strptime" not in src, "Date 解析仍依赖 locale 敏感的 strptime"
    # 功能核验：真实 RFC 2616 日期串能解析出时间戳
    ts = etd.DRM.parse_rfc2616_date("Mon, 12 Oct 2026 12:00:00 GMT")
    assert ts is not None and ts > 1_700_000_000


def test_narration_403_error_carries_root_cause():
    """403 上抛必须带补丁指引，不许只报节点名（2026-10-12 报障教训）。"""
    import video_director.narration as narr
    src = inspect.getsource(narr.synth_scene_audio)
    assert "403" in src, "narration 未对 403 提供根因提示"


def test_synth_scene_audio_retries_on_429(tmp_path, monkeypatch):
    """429 限流是瞬时故障，必须重试而不是直接炸掉 narration 节点（生产实录
    2026-11-18：edge-tts 429 让整个 narration 节点 FAILED）。"""
    import sys
    import time
    import types
    import video_director.narration as narr

    class _Calls:
        n = 0

    class _FakeCommunicate:
        def __init__(self, *a, **kw):
            _Calls.n += 1

        def stream(self):
            async def gen():
                raise Exception("429, message='Invalid response status'")
                yield  # pragma: no cover - 让函数成为 async generator
            return gen()

    fake_edge_tts = types.ModuleType("edge_tts")
    fake_edge_tts.Communicate = _FakeCommunicate
    monkeypatch.setitem(sys.modules, "edge_tts", fake_edge_tts)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    out = tmp_path / "scene.mp3"
    # 每次都 429 → 重试 3 次后抛出；关键是构造了 3 次（= 重试了 3 次）
    try:
        narr.synth_scene_audio("测试文案", "zh-CN-XiaoxiaoNeural", str(out), "+0%")
    except narr.NarrationError as exc:
        assert "429" in str(exc) or "Invalid response status" in str(exc)
    assert _Calls.n == 3, _Calls.n
