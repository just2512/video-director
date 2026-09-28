"""MPT 吸收方案 §98 Regression Fixture：fixtures/ai-control/ 14 组数据契约。

§98 列出的 fixture 集（对齐方案原文目录名）：
    preserve-original-audio / add-watermark / lower-bgm / subtitle-style /
    local-video / local-image / pexels / pixabay / coverr / uploaded-voice /
    silent-video / generated-image / generated-video / 4k-output

每组是一个确定性 fixture dict：输入状态 + 期望副作用（IR 断言点）。
pytest 消费（tests/test_mpt_closeout.py 同名 class 扩展），不依赖网络。
"""

from __future__ import annotations

#: §98 fixture 集合：name → {setup, expect}
AI_CONTROL_FIXTURES = {
    # ---- §18 原声 ----
    "preserve-original-audio": {
        "tool": "audio.original.preserve",
        "payload": {"mode": "preserve", "volume": 0.8, "duckAgainst": "narration"},
        "expect": {"audio_type": "original", "volume": 0.8, "ducking": True},
    },
    # ---- §19 水印 ----
    "add-watermark": {
        "tool": "watermark.add",
        "payload": {"kind": "text", "text": "品牌名", "anchor": "top_right",
                     "opacity": 0.75},
        "expect": {"watermark_count": 1, "anchor": "top_right", "kind": "text"},
    },
    # ---- §34 BGM ----
    "lower-bgm": {
        "tool": "music.volume",
        "payload": {"type": "bgm", "volume": 0.3},
        "expect": {"audio_type": "bgm", "volume": 0.3},
    },
    # ---- §32 字幕样式 ----
    "subtitle-style": {
        "tool": "subtitle.update_style",
        "payload": {"color": "#FFFFFF", "strokeColor": "#000000",
                     "position": "bottom"},
        "expect": {"subtitle_style": {"color": "#FFFFFF",
                                        "stroke_color": "#000000",
                                        "position": "bottom"}},
    },
    # ---- §86 本地素材 ----
    "local-video": {
        "tool": "layer.add",
        "payload": {"assetId": "clip", "assetType": "video", "sourceOut": 3.0},
        "expect": {"layer_added": True, "asset_type": "video"},
    },
    "local-image": {
        "tool": "layer.add",
        "payload": {"assetId": "frame", "assetType": "image"},
        "expect": {"layer_added": True, "asset_type": "image"},
    },
    # ---- §25 Stock 商业安全三 Provider ----
    "pexels": {
        "provider": "pexels",
        "expect": {"commercial_status": "CLEAR", "requires_attribution": True},
    },
    "pixabay": {
        "provider": "pixabay",
        "expect": {"commercial_status": "CLEAR",
                   "standalone_export_allowed": False},
    },
    "coverr": {
        "provider": "coverr",
        "expect": {"commercial_status": "REVIEW"},  # §25：默认 REVIEW 不放行
    },
    # ---- §16 上传配音 ----
    "uploaded-voice": {
        "tool": "voice_upload",
        "expect": {"tts_skipped": True, "source_in": ("asr", "estimated")},
    },
    # ---- §17 无配音 ----
    "silent-video": {
        "mode": "silent",
        "expect": {"tts_skipped": True, "bgm_allowed": True,
                   "subtitle_allowed": True},
    },
    # ---- §90 生成 ----
    "generated-image": {
        "tool": "generation.image",
        "payload": {"prompt": "测试配图"},
        "expect": {"asset_count": 3, "origin": "generated", "variants": ["A", "B", "C"]},
    },
    "generated-video": {
        "tool": "generation.video",
        "payload": {"prompt": "夜景"},
        "expect": {"asset_count": 3, "origin": "generated"},
    },
    # ---- §43/§44 4K ----
    "4k-output": {
        "profile": "4k-landscape",
        "expect": {"width": 3840, "height": 2160, "is_4k": True},
    },
}


def fixture_names() -> list:
    """§98：14 组 fixture 名（目录形态的软件等价物——pytest 数据驱动）。"""
    return sorted(AI_CONTROL_FIXTURES.keys())


def get_fixture(name: str) -> dict:
    if name not in AI_CONTROL_FIXTURES:
        raise KeyError(f"§98 fixture {name!r} 不存在（可用: {fixture_names()}）")
    return AI_CONTROL_FIXTURES[name]
