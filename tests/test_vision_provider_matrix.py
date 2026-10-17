"""vision provider 可达性：配了 key 必须 READY。

背景（2026-10-16 实测）：`infer_provider` 对 vision 角色过去只返回
openai / openai-compatible，而 vision 只注册了 frame-vision-openai-compatible
→ `state_for_provider` 判定「Provider 未登记」返回 UNKNOWN → FrameVisionProvider
.available() 恒为 False。视觉理解链路整体不可达，配任何 key/base_url/模型都无效。
"""
from video_director import provider_matrix as pm
from video_director import vision


def test_vision_infers_registered_provider_and_ready():
    cfg = {"api_key": "sk-test", "base_url": "https://api.siliconflow.cn/v1",
           "model": "Qwen/Qwen2-VL-7B"}
    assert pm.infer_provider("vision", cfg) == "frame-vision-openai-compatible"
    st = pm.state("vision", "vision.frame_analyze", cfg)
    assert st.status == pm.READY, st


def test_vision_missing_without_key():
    cfg = {"api_key": "", "base_url": "https://api.openai.com/v1", "model": "gpt-4o"}
    st = pm.state("vision", "vision.frame_analyze", cfg)
    assert st.status == pm.MISSING, st


def test_frame_vision_provider_available_with_key():
    fp = vision.FrameVisionProvider(
        api_key="sk-test", base_url="https://api.openai.com/v1", model="gpt-4o")
    assert fp.available() is True