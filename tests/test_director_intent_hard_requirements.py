# -*- coding: utf-8 -*-
"""2026-11-28 硬性要求丢弃回归锁（第一批：确定性规则层）。

用户裁决：创作系统不能把「未识别」当成「用户没有要求」。这四个字段之前在
规则层静默丢弃，导致成片缺失水印 / 分辨率降级 / 时长不足 / 转场缺省硬切。
本测试直接锁住 brief 原句的解析结果，防止再次回归。

注意：全部走 resolve_rule_intents / 纯函数，不触发渲染引擎（无 ffmpeg 依赖）。
"""
from video_director import director_intent as dint


BRIEF = (
    "根据文案按顺序匹配视频素材，保留素材原声，素材衔接增加转场。"
    "视频右下角增加一行水印文字：'历史演绎，仅供娱乐'。"
    "4K画质，尺寸16:9，字幕与配音匹配，男士配音沉稳语速放缓，视频时长控制在40s以上。"
)


class TestResolutionQualityAndAspectSplit:
    """画质等级（4K）与画幅比例（16:9）必须分开解析，不能把 4K 降级成 1080p。"""

    def test_4k_16_9_combines_to_3840x2160(self):
        assert dint._resolve_resolution("4K画质，尺寸16:9") == "3840x2160"

    def test_4k_alone_defaults_16_9(self):
        assert dint._resolve_resolution("4K画质") == "3840x2160"

    def test_aspect_only_keeps_1080p_default(self):
        # 只给画幅时仍默认 1080p（回归兼容既有行为）
        assert dint._resolve_resolution("16:9") == "1920x1080"
        assert dint._resolve_resolution("9:16") == "1080x1920"

    def test_4k_vertical(self):
        assert dint._resolve_resolution("4K 竖屏") == "2160x3840"

    def test_1080p_explicit(self):
        assert dint._resolve_resolution("1080p") == "1920x1080"


class TestDurationEnglishUnit:
    """时长「40s」「40sec」要识别，且保留 40+ 下限语义。"""

    def test_english_s(self):
        assert dint._resolve_duration("40s以上") == "40+"

    def test_english_sec(self):
        assert dint._resolve_duration("40sec") == "40"

    def test_english_seconds(self):
        assert dint._resolve_duration("40 seconds以上") == "40+"

    def test_chinese_still_works(self):
        assert dint._resolve_duration("40秒以上") == "40+"


class TestWatermarkColonQuote:
    """水印「增加一行水印文字：'xxx'」必须提取出正文，不再静默 SKIPPED。"""

    def test_colon_quote(self):
        assert dint._resolve_text_field(
            "视频右下角增加一行水印文字：'历史演绎，仅供娱乐'",
            "watermark", ["水印", "署名", "版权", "加水印", "角标", "出品"],
        ) == "历史演绎，仅供娱乐"

    def test_colon_unquoted(self):
        assert dint._resolve_text_field(
            "右下角加水印内容：历史演绎",
            "watermark", ["水印", "署名", "版权", "加水印", "角标", "出品"],
        ) == "历史演绎"


class TestTransitionModeExtraction:
    """明确转场意图必须被提取并归一化，不再缺省硬切。"""

    def test_add_transition(self):
        assert dint._resolve_transition_mode("素材衔接增加转场") == "fade"

    def test_natural_transition(self):
        assert dint._resolve_transition_mode("自然衔接") == "fade"

    def test_hard_cut(self):
        assert dint._resolve_transition_mode("不要转场，硬切") == "cut"


class TestRuleResolveEndToEnd:
    """brief 原句整段解析，四个字段全部命中且值正确。"""

    def test_full_brief(self):
        out, _ = dint._rule_resolve(BRIEF)
        assert out["resolution"] == "3840x2160"
        assert out["duration"] == "40+"
        assert out["transition_mode"] == "fade"
        assert out["watermark"] == "历史演绎，仅供娱乐"

    def test_rule_intents_returns_raw_fields(self):
        r = dint.resolve_rule_intents(BRIEF)
        assert r["resolution"] == "3840x2160"
        assert str(r["duration"]).startswith("40")
        assert r["transition_mode"] == "fade"
        assert r["watermark"] == "历史演绎，仅供娱乐"

    def test_normalized_public_api(self):
        # resolve_director_intents 走 _normalize_value：transition_mode=fade 归一化后仍为 fade
        r = dint.resolve_director_intents(BRIEF)
        assert r.get("transition_mode") == "fade"
        assert r.get("resolution") == "3840x2160"
        assert r.get("watermark") == "历史演绎，仅供娱乐"
        assert r.get("duration") == "40+"