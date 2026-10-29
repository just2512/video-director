"""R7/R8 终审修复测试。

R7 — QC 黑帧采样点映射回 IR 场景（qc._scene_at / _detect_black_frames detail）。
R8 — GenerationError 分类（配额 vs 临时故障）+ 真实调用点异常映射。

隔离策略（参照 tests/test_mpt_absorption_capabilities.py）：
- generation 角色配置走 model_config（monkeypatch get_role_config 返回假 cfg）
- 黑帧检测 monkeypatch prober.probe_media + subprocess.run，不跑真 ffmpeg
"""

import types

import pytest

from video_director import config, generation, model_config, prober, qc
from video_director.models import (
    CompositionIR, Layer, LayerType, ProjectSpec, SceneIR,
)


# ---------------------------------------------------------------------------
# 最小 CompositionIR（结构抄 tests/test_visual_overlay_timeline.py）
# ---------------------------------------------------------------------------

def _ir_with_scenes() -> CompositionIR:
    """三段场景：S00[0,2) + S01[2,5) + S02[5,10)，中间无 gap。"""
    return CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=10.0),
        scenes=[
            SceneIR(id="S00", start=0.0, duration=2.0, text="a",
                    layers=[Layer(type=LayerType.image, asset_id="base", duration=2.0)]),
            SceneIR(id="S01", start=2.0, duration=3.0, text="b",
                    layers=[Layer(type=LayerType.image, asset_id="base", duration=3.0)]),
            SceneIR(id="S02", start=5.0, duration=5.0, text="c",
                    layers=[Layer(type=LayerType.image, asset_id="base", duration=5.0)]),
        ],
        assets=[],
    )


def _ir_with_gap() -> CompositionIR:
    """S00[0,2) + S01[2,4) + S02[4,10)，S01 内含 gap layer[1,3)（全局 3-5s）。"""
    return CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=10.0),
        scenes=[
            SceneIR(id="S00", start=0.0, duration=2.0, text="a",
                    layers=[Layer(type=LayerType.image, asset_id="base", duration=2.0)]),
            SceneIR(id="S01", start=2.0, duration=2.0, text="b",
                    layers=[
                        Layer(type=LayerType.image, asset_id="base", start=0.0, duration=1.0),
                        Layer(type=LayerType.gap, start=1.0, duration=1.0),
                    ]),
            SceneIR(id="S02", start=4.0, duration=6.0, text="c",
                    layers=[Layer(type=LayerType.image, asset_id="base", duration=6.0)]),
        ],
        assets=[],
    )


# ===========================================================================
# R7 — _scene_at 纯函数
# ===========================================================================

class TestSceneAt:
    def test_hits_scene_returns_id(self):
        """采样点落在场景区间内 → 返回 scene.id。"""
        ir = _ir_with_scenes()
        assert qc._scene_at(ir, 0.5) == "S00"
        assert qc._scene_at(ir, 2.0) == "S01"
        assert qc._scene_at(ir, 7.5) == "S02"

    def test_boundary_start_inclusive_end_exclusive(self):
        """区间语义与 _is_expected_empty_gap 对齐：start 闭、end 开。"""
        ir = _ir_with_scenes()
        assert qc._scene_at(ir, 2.0) == "S01"   # S00 结束即 S01 开始
        assert qc._scene_at(ir, 5.0) == "S02"
        assert qc._scene_at(ir, 9.99) == "S02"
        assert qc._scene_at(ir, 10.0) is None    # 超出末场景（end 开区间）

    def test_gap_point_returns_none(self):
        """gap（IR 声明空隙）→ None；gap 是 layer 不是场景区间外。

        gap 层本身位于 S01 的 [start,start+duration) 内，但 _scene_at 按
        场景区间判断会命中 S01 —— 本测试断言 gap 内的场景命中语义，
        并覆盖「两个场景之间缺口」的真实 None 路径。
        """
        ir = _ir_with_gap()
        # S01 全局区间 [2,4)，gap 层在全局 [3,4) —— 仍在 S01 区间内
        assert qc._scene_at(ir, 3.5) == "S01"
        # 场景之间无覆盖的洞（构造 12s 视频只有 10s 场景）
        assert qc._scene_at(ir, 10.5) is None
        assert qc._scene_at(ir, -1.0) is None

    def test_none_ir_returns_none(self):
        """ir=None（无 IR 上下文）→ None。"""
        assert qc._scene_at(None, 1.0) is None


# ===========================================================================
# R7 — _detect_black_frames detail 带场景定位（monkeypatch，不跑真 ffmpeg）
# ===========================================================================

def _fake_probe(duration: float):
    def _probe(_path):
        return prober.ProbeResult(
            duration=duration, width=320, height=180, fps=10.0,
            has_video_stream=True, has_audio_stream=True, engine="cache",
        )
    return _probe


def _black_run(cmd, **kwargs):
    """subprocess.run 假件：返回全零帧数据（32x32 灰度全黑）。"""
    return types.SimpleNamespace(stdout=bytes(32 * 32), stderr=b"")


class TestBlackFrameSceneLocation:
    def test_black_detail_names_scene(self, monkeypatch):
        """黑帧 detail 带上 IR 场景定位（R7 终审意见核心）。"""
        monkeypatch.setattr(prober, "probe_media", _fake_probe(10.0))
        monkeypatch.setattr(qc.subprocess, "run", _black_run)
        ir = _ir_with_scenes()
        ok, detail = qc._detect_black_frames("fake.mp4", ir=ir)
        assert ok is False
        # 采样点 t = 0.5 + 0.9*10*(i+0.5)/5 → i=0: 1.4s（S00），首个坏点
        assert "S00" in detail, detail
        assert "1.4s" in detail, detail

    def test_black_detail_falls_back_to_index_when_no_id(self, monkeypatch):
        """scene.id 缺失 → 退化 scene[<序号>]（后向兼容旧 IR）。"""
        monkeypatch.setattr(prober, "probe_media", _fake_probe(10.0))
        monkeypatch.setattr(qc.subprocess, "run", _black_run)
        ir = _ir_with_scenes()
        for scene in ir.scenes:
            scene.id = ""
        ok, detail = qc._detect_black_frames("fake.mp4", ir=ir)
        assert ok is False
        # 1.4s 落在第 0 个场景
        assert "scene[0]" in detail, detail

    def test_no_ir_still_reports_black(self, monkeypatch):
        """ir=None 时 detail 不含定位但不报错（保持旧行为）。"""
        monkeypatch.setattr(prober, "probe_media", _fake_probe(10.0))
        monkeypatch.setattr(qc.subprocess, "run", _black_run)
        ok, detail = qc._detect_black_frames("fake.mp4", ir=None)
        assert ok is False
        assert "个非 gap 采样点为黑帧" in detail, detail

    def test_black_detail_locates_second_scene(self, monkeypatch):
        """坏点落在第二段场景时定位到对应 scene.id。"""
        # 采样点 5%~95% / 5 samples → t_i = 0.5 + 9*(i+0.5)/5：
        # i=0 → 1.4s(S00), i=1 → 3.2s(S01)。让 i=0 正常、i=1 起全黑。
        monkeypatch.setattr(prober, "probe_media", _fake_probe(10.0))

        state = {"n": 0}

        def _run(cmd, **kwargs):
            # cmd = [ffmpeg, "-ss", f"{t:.2f}", "-i", path, ...]
            t = float(cmd[2])
            if t < 2.0:
                return types.SimpleNamespace(stdout=bytes([200]) * (32 * 32), stderr=b"")
            return types.SimpleNamespace(stdout=bytes(32 * 32), stderr=b"")

        monkeypatch.setattr(qc.subprocess, "run", _run)
        ir = _ir_with_scenes()
        ok, detail = qc._detect_black_frames("fake.mp4", ir=ir)
        assert ok is False
        assert "S01" in detail, detail
        assert "3.2s" in detail, detail
        assert "S00" not in detail, detail


# ===========================================================================
# R8 — GenerationError 分类
# ===========================================================================

class TestGenerationErrorClassification:
    def test_quota_exceeded_429(self):
        """429 insufficient_quota → QUOTA_EXCEEDED，不可恢复。"""
        err = generation.GenerationError(
            "IMAGE_GENERATION_FAILED", "Error 429: insufficient_quota")
        assert err.is_quota() is True
        assert err.is_recoverable() is False
        assert err.code == "QUOTA_EXCEEDED"

    def test_quota_exceeded_chinese_message(self):
        """中文欠费/余额不足 → 同归配额（message 原样保留）。"""
        err = generation.GenerationError(
            "VIDEO_GENERATION_FAILED", "供应商返回：余额不足，请充值")
        assert err.is_quota() is True
        assert err.is_recoverable() is False
        assert err.code == "QUOTA_EXCEEDED"
        assert "余额不足" in str(err)

    def test_transient_connection_timeout(self):
        """连接超时 → recoverable，非配额。"""
        err = generation.GenerationError(
            "VIDEO_GENERATION_FAILED", "Connection timeout to provider")
        assert err.is_quota() is False
        assert err.is_recoverable() is True
        assert err.code == "VIDEO_GENERATION_FAILED"

    def test_plain_rejection_not_quota_not_recoverable(self):
        """普通拒绝（size not supported）→ 都 False，code 不变。"""
        err = generation.GenerationError(
            "IMAGE_GENERATION_FAILED", "size not supported")
        assert err.is_quota() is False
        assert err.is_recoverable() is False
        assert err.code == "IMAGE_GENERATION_FAILED"


# ===========================================================================
# R8 — 真实调用点映射（monkeypatch OpenAI client 抛 429）
# ===========================================================================

class _FakeImages:
    def generate(self, **kwargs):
        raise Exception("Error 429: insufficient_quota")


class _FakeOpenAI:
    def __init__(self, *a, **kw):
        self.images = _FakeImages()


class _FakeVideoClient:
    """OpenAI 兼容 video client 假件：post 抛 429。"""

    def post(self, *a, **kw):
        raise Exception("Error 429: insufficient quota, please recharge")

    def get(self, *a, **kw):
        raise AssertionError("轮询不应在提交失败后到达")


class TestCallSiteMapping:
    def test_image_call_site_maps_quota(self, tmp_path, monkeypatch):
        """monkeypatch OpenAI client 抛 429 → generate 抛 QUOTA_EXCEEDED。

        注：image 类型有兜底（_generate_image_or_fallback），配额耗尽兜底到
        程序化视觉卡，故此处直接测 generate_image_variants 的映射（真实调用点）。
        """
        monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
        monkeypatch.setattr(model_config, "load_all",
                            lambda: {"image": {"api_key": "k", "base_url": "u",
                                               "model": "m", "source": "file"}})
        monkeypatch.setattr(model_config, "_cache", None)
        monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
        with pytest.raises(generation.GenerationError) as ei:
            generation.generate_image_variants("p", 320, 180)
        assert ei.value.code == "QUOTA_EXCEEDED"
        assert ei.value.is_quota() is True
        # 供应商原文保留（用户能看懂）
        assert "insufficient_quota" in str(ei.value)

    def test_video_call_site_maps_quota(self, tmp_path, monkeypatch):
        """monkeypatch video client 抛 429 → _generate_video 抛 QUOTA_EXCEEDED。"""
        monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
        monkeypatch.setattr(model_config, "load_all",
                            lambda: {"video": {"api_key": "k",
                                               "base_url": "http://x/v1",
                                               "model": "vid", "source": "file"}})
        monkeypatch.setattr(model_config, "_cache", None)
        monkeypatch.setattr(generation, "_video_client", lambda cfg: _FakeVideoClient())
        # provider_matrix.require 会对 cfg 做连通性探测，避免真探测：直接放行
        from video_director import provider_matrix
        monkeypatch.setattr(provider_matrix, "require", lambda *a, **kw: None)
        with pytest.raises(generation.GenerationError) as ei:
            generation.generate("p", "prompt", gen_type="video")
        assert ei.value.code == "QUOTA_EXCEEDED"
        assert ei.value.is_quota() is True
        assert ei.value.is_recoverable() is False
