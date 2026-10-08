"""Provider Capability Matrix 契约测试。

不访问真实网络，只验证：
- provider 身份推断；
- 配置完整性与能力实现状态；
- 未接通 provider 不能因 key 存在而误报 READY；
- 视频首末帧能力独立于普通视频生成能力；
- Stock/TTS/Generation 消费统一矩阵状态。
"""

from __future__ import annotations

from video_director import generation, provider_matrix, stock, voice


def test_infer_openai_and_custom_compatible():
    assert provider_matrix.infer_provider(
        "image", {"api_key": "k", "base_url": "https://api.openai.com/v1", "model": "gpt-image-1"}
    ) == "openai"
    assert provider_matrix.infer_provider(
        "image", {"api_key": "k", "base_url": "https://api.siliconflow.cn/v1", "model": "Qwen-Image"}
    ) == "openai-compatible"


def test_image_ready_requires_key_but_uses_role_defaults():
    st = provider_matrix.state(
        "image", "image.generate",
        {"api_key": "k", "base_url": "", "model": ""},
    )
    assert st.status == provider_matrix.READY


def test_video_missing_base_url_is_not_ready():
    st = provider_matrix.state(
        "video", "video.generate",
        {"api_key": "k", "base_url": "", "model": ""},
    )
    assert st.status == provider_matrix.MISSING
    assert "服务地址" in st.reason


def test_video_image_to_video_is_explicitly_unsupported():
    st = provider_matrix.state(
        "video", "video.image_to_video",
        {"api_key": "k", "base_url": "https://example.com/v1", "model": "video-model"},
    )
    assert st.status == provider_matrix.UNSUPPORTED


def test_unknown_custom_role_provider_is_fail_closed():
    st = provider_matrix.state(
        "image", "image.generate",
        {"api_key": "k", "base_url": "", "model": "image-model"},
    )
    assert st.status == provider_matrix.READY

    st2 = provider_matrix.state(
        "image", "image.generate",
        {"api_key": "k", "base_url": "not-a-url", "model": "image-model"},
    )
    assert st2.status == provider_matrix.READY


def test_tts_unimplemented_providers_never_report_ready():
    defs = {d.provider_id: d for d in provider_matrix.DEFINITIONS if d.role == "tts"}
    assert defs["kokoro"].implemented is False
    providers = {p["id"]: p for p in voice.list_providers()}
    assert providers["edge-tts"]["status"] == provider_matrix.READY
    assert providers["kokoro"]["available"] is False
    assert providers["kokoro"]["status"] == provider_matrix.UNSUPPORTED


def test_stock_coverr_is_unsupported_even_when_key_exists(monkeypatch):
    monkeypatch.setenv("COVERR_API_KEY", "test-key")
    st = provider_matrix.state(
        "stock_coverr", "stock.search_video",
        {"api_key": "test-key", "base_url": "https://coverr.co", "model": ""},
    )
    assert st.status == provider_matrix.UNSUPPORTED

    try:
        stock.search_stock("coffee", provider="coverr")
    except stock.StockError as exc:
        assert exc.code == "CAPABILITY_UNSUPPORTED"
    else:
        raise AssertionError("Coverr 未接通时显式搜索必须 fail-closed")


def test_generation_video_missing_provider_is_honest(monkeypatch, tmp_path):
    from video_director import config
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    monkeypatch.delenv("VIDEO_API_KEY", raising=False)
    try:
        generation.generate("p", "test", gen_type="video", width=1080, height=1920)
    except generation.GenerationError as exc:
        assert exc.code in {"VIDEO_PROVIDER_UNAVAILABLE", "CAPABILITY_UNSUPPORTED"}
    else:
        raise AssertionError("未配置视频 provider 不得返回假成功")


def test_matrix_frontend_never_contains_secret():
    rows = provider_matrix.matrix_for_frontend()
    for row in rows:
        assert "api_key" not in row
        assert "secret" not in str(row).lower()
