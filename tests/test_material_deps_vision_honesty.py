# -*- coding: utf-8 -*-
"""2026-10-17 生产事故回归测试。

事故：material_match 依赖图漏声明 narration（裸 KeyError）+ vision 配纯文本
模型 MiniMax-M3 假就绪（全部素材 400 白烧）+ doctor 只查 env 与运行时不同源
（用户配好了 doctor 却说未配置）。
"""
import json
from pathlib import Path

import pytest

from video_director import provider_matrix
from video_director.director_agent import _tool_material


def _vision_cfg(model: str) -> dict:
    return {
        "api_key": "sk-test",
        "base_url": "https://api-inference.modelscope.cn/v1",
        "model": model,
    }


class TestVisionTextOnlyBlacklist:
    """修 B：纯文本模型配进 vision 角色必须提前判不就绪。"""

    def test_minimax_m3_blocked(self):
        """事故现场模型：MiniMax/MiniMax-M3 → NOT_CONFIGURED，reason 写明纯文本。"""
        st = provider_matrix.state("vision", "vision.frame_analyze", _vision_cfg("MiniMax/MiniMax-M3"))
        assert st.status != provider_matrix.READY
        assert "纯文本" in (st.reason or "")

    def test_deepseek_chat_blocked(self):
        st = provider_matrix.state("vision", "vision.frame_analyze", _vision_cfg("deepseek-chat"))
        assert st.status != provider_matrix.READY

    def test_glm_4_air_blocked(self):
        st = provider_matrix.state("vision", "vision.frame_analyze", _vision_cfg("glm-4-air"))
        assert st.status != provider_matrix.READY

    def test_real_vision_models_pass(self):
        """真视觉模型不被误杀。"""
        for m in ("gpt-4o", "qwen2.5-vl-72b-instruct", "glm-4v-plus", "step-1v-8k"):
            st = provider_matrix.state("vision", "vision.frame_analyze", _vision_cfg(m))
            assert st.status == provider_matrix.READY, m

    def test_glm_4v_not_caught_by_glm4_pattern(self):
        """glm-4-[0-9]* 模式不得误杀 glm-4v*（视觉族）。"""
        st = provider_matrix.state("vision", "vision.frame_analyze", _vision_cfg("glm-4v-9b"))
        assert st.status == provider_matrix.READY

    def test_gpt4o_mini_default_not_blocked(self):
        """gpt-4o-mini 是 vision 角色的 default_model 且为真多模态——不得拦。"""
        st = provider_matrix.state("vision", "vision.frame_analyze", _vision_cfg("gpt-4o-mini"))
        assert st.status == provider_matrix.READY

    def test_vision_variant_not_blocked_by_text_prefix(self):
        """前缀通吃会误杀视觉变体：gpt-4-turbo-vision-preview / moonshot-v1-8k-vision 放行。"""
        for m in ("gpt-4-turbo-vision-preview", "moonshot-v1-8k-vision"):
            st = provider_matrix.state("vision", "vision.frame_analyze", _vision_cfg(m))
            assert st.status == provider_matrix.READY, m

    def test_text_variants_blocked(self):
        for m in ("gpt-4-turbo-2024-04-09", "moonshot-v1-8k", "GLM-4.5-Air"):
            st = provider_matrix.state("vision", "vision.frame_analyze", _vision_cfg(m))
            assert st.status != provider_matrix.READY, m

    def test_namespace_last_segment_matching(self):
        """ModelScope 风格 Org/Model：全名不命中时按末段匹配。"""
        st = provider_matrix.state("vision", "vision.frame_analyze", _vision_cfg("deepseek-ai/deepseek-v3"))
        assert st.status != provider_matrix.READY
        st2 = provider_matrix.state("vision", "vision.frame_analyze", _vision_cfg("OpenBMB/MiniCPM-V-2_6"))
        assert st2.status == provider_matrix.READY


class TestMaterialMatchGuard:
    """修 D：narration_scenes 缺失时给可定位的中文报错，不是裸 KeyError。"""

    def test_missing_narration_scenes_returns_actionable_error(self):
        state = {"project_id": "proj-x", "video_files": [], "image_files": []}
        result = _tool_material({}, state)
        assert result["ok"] is False
        assert "narration_scenes" not in str(result.get("error", "")) or "配音" in result["error"]
        assert "配音" in result["error"]

    def test_missing_keyerror_not_raised(self):
        """修复前：state['narration_scenes'] 直接 KeyError 崩掉整个节点。"""
        state = {"project_id": "proj-x"}
        try:
            result = _tool_material({}, state)
            assert result["ok"] is False
        except KeyError as exc:
            pytest.fail(f"裸 KeyError 仍然抛出: {exc}")


class TestDependencyGraph:
    """修 A：material_match 依赖必须含 narration。"""

    def test_material_match_depends_on_narration(self):
        import video_director.director_inputs as di
        src = Path(di.__file__).read_text(encoding="utf-8")
        assert '"material_match": ["scene_plan", "narration"]' in src, \
            "依赖表必须把 narration 声明为 material_match 的前置"


class TestDoctorVisionSameSource:
    """修 C：doctor vision 检测必须与运行时同源（model_config 优先）+ 可选 FAIL 不阻塞。"""

    def test_doctor_reads_model_config(self):
        from video_director import doctor
        src = Path(doctor.__file__).read_text(encoding="utf-8")
        assert "model_config.get_role_config" in src
        assert 'os.environ.get("COMPOSER_VISION_API_KEY")' in src  # env 兜底仍保留

    def test_optional_vision_fail_does_not_block(self):
        """D1：vision 误配 FAIL 不得阻塞主链（pipeline 等不依赖视觉）。"""
        from video_director import doctor
        vision_fail = doctor.CheckResult("Vision 增强（可选）", "FAIL",
                                         "模型 X 是纯文本模型", optional=True)
        assert doctor.has_blocking_failures([vision_fail]) is False
        real_fail = doctor.CheckResult("FFmpeg", "FAIL", "缺失")
        assert doctor.has_blocking_failures([real_fail]) is True
