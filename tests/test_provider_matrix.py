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


def test_custom_openai_compatible_endpoint_is_declared_supported():
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


def test_tts_implemented_providers_and_honest_registry():
    """2026-10-26 用户裁决契约翻转：azure/elevenlabs 已实现（真实 REST 执行器
    voice.synth_preview）——配了 key 即 READY；gemini/siliconflow/kokoro 无
    真实执行器，从 Registry 删除（挂着假名字比没有更糟）。"""
    defs = {d.provider_id: d for d in provider_matrix.DEFINITIONS if d.role == "tts"}
    assert defs["azure"].implemented is True
    assert defs["elevenlabs"].implemented is True
    assert "gemini" not in defs and "siliconflow" not in defs and "kokoro" not in defs
    providers = {p["id"]: p for p in voice.list_providers()}
    assert providers["edge-tts"]["status"] == provider_matrix.READY
    # azure/elevenlabs 未配 key → MISSING（诚实，非 UNSUPPORTED）
    assert providers["azure"]["available"] is False
    assert providers["elevenlabs"]["available"] is False


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
    from video_director import config, model_config
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    monkeypatch.delenv("VIDEO_API_KEY", raising=False)
    # model_config 模块级 _cache 会在测试间残留生产配置（含 video key），
    # 必须清掉才能真实验证「未配置」态（测试隔离缺陷，2026-10-18 根修）。
    monkeypatch.setattr(model_config, "_cache", None)
    try:
        generation.generate("p", "test", gen_type="video", width=1080, height=1920)
    except generation.GenerationError as exc:
        assert exc.code in {"VIDEO_PROVIDER_UNAVAILABLE", "CAPABILITY_UNSUPPORTED"}
    else:
        raise AssertionError("未配置视频 provider 不得返回假成功")


def test_matrix_frontend_never_contains_secret():
    # 前端只能拿到 provider/capability 元数据，不能拿到本机密钥。
    rows = provider_matrix.matrix_for_frontend()
    for row in rows:
        assert "api_key" not in row
        assert "secret" not in str(row).lower()


def test_matrix_frontend_tts_three_real_providers(monkeypatch):
    """2026-10-26 契约翻转：TTS 前端矩阵只有三个真实 provider——
    edge-tts READY、azure/elevenlabs 配 key 才 READY、假 provider 已删。"""
    rows = provider_matrix.matrix_for_frontend()
    tts = {r["provider"]: r for r in rows if r["role"] == "tts"}
    assert tts["edge-tts"]["operations"]["tts.speak"] == provider_matrix.READY
    assert set(tts.keys()) == {"edge-tts", "azure", "elevenlabs"}


def test_matrix_frontend_reports_stock_states(monkeypatch, tmp_path):
    from video_director import config, model_config
    # stock key 已迁移到 model_config.json 文件配置（文件 > env）——仅 delenv
    # 删不掉文件配置。隔离到 tmp_path + 清缓存，真实验证「未配置」态。
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    monkeypatch.setattr(model_config, "_cache", None)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    rows = provider_matrix.matrix_for_frontend()
    stock_rows = {r["provider"]: r for r in rows if r["role"] == "stock"}
    assert stock_rows["pexels"]["operations"]["stock.search_video"] == provider_matrix.MISSING
    assert stock_rows["coverr"]["operations"]["stock.search_video"] == provider_matrix.UNSUPPORTED
