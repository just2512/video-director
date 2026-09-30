"""Frontend Unification 渲染消费测试：camera/pip/mask/morph → filtergraph。

验证四类视觉语义正确映射为 ffmpeg filter（纯函数断言，不跑真 ffmpeg——
确定性渲染的 filtergraph 正确性即可锁定「命令 → 视觉变化」闭环的
渲染侧表达，像素级验证属 render.verify 采样）。
"""

import json

import pytest

from video_director import visual_render as vr
from video_director.visual_semantics import (
    CameraKeyframe,
    CameraTrack,
    Mask,
)


# ---------------------------------------------------------------------------
# §14 camera → zoompan（推拉/横移/俯仰 → z/x/y 线性插值）
# ---------------------------------------------------------------------------

class TestCameraRender:
    def _cam(self, p0, p1, dur=4.0):
        return CameraTrack(id="cam-s1", keyframes=[
            CameraKeyframe(at=0, position=p0, target=[0, 0, 0]),
            CameraKeyframe(at=dur, position=p1, target=[0, 0, 0]),
        ])

    def test_push_in_zooms_in(self):
        # z 1.0 → 0.6（拉近）→ zoom 斜率 = 1/0.6 - 1/1.0 ≈ 0.6667（放大运动）
        expr = vr.camera_zoompan_expr(self._cam([0.5, 0.5, 1.0], [0.5, 0.5, 0.6]), 4, 1080, 1920, 30)
        assert expr is not None
        assert "zoompan" in expr and "0.6667" in expr  # 放大斜率

    def test_pull_out_zooms_out(self):
        expr = vr.camera_zoompan_expr(self._cam([0.5, 0.5, 0.6], [0.5, 0.5, 1.0]), 4, 1080, 1920, 30)
        assert expr is not None
        assert "zoompan" in expr

    def test_pan_left_shifts_x(self):
        # x 0.7 → 0.3（左移）：x 表达式项非零
        expr = vr.camera_zoompan_expr(self._cam([0.7, 0.5, 1.0], [0.3, 0.5, 1.0]), 4, 1080, 1920, 30)
        assert expr is not None
        assert "x='" in expr

    def test_no_motion_returns_none(self):
        # 静止（两帧 z 相同、x/y 相同）→ None（不注入滤镜）
        expr = vr.camera_zoompan_expr(self._cam([0.5, 0.5, 1.0], [0.5, 0.5, 1.0]), 4, 1080, 1920, 30)
        assert expr is None

    def test_single_keyframe_none(self):
        cam = CameraTrack(id="c", keyframes=[CameraKeyframe(at=0, position=[0.5, 0.5, 1.0])])
        assert vr.camera_zoompan_expr(cam, 4, 1080, 1920, 30) is None

    def test_apply_camera_filter_none_camera(self):
        assert vr.apply_camera_filter(None, 1080, 1920, 30, 4.0) is None

    def test_filter_is_deterministic(self):
        e1 = vr.camera_zoompan_expr(self._cam([0.5, 0.5, 1.0], [0.5, 0.5, 0.7]), 3, 720, 1280, 24)
        e2 = vr.camera_zoompan_expr(self._cam([0.5, 0.5, 1.0], [0.5, 0.5, 0.7]), 3, 720, 1280, 24)
        assert e1 == e2


# ---------------------------------------------------------------------------
# §63 mask → 几何裁剪（归一 → 像素 crop + feather boxblur）
# ---------------------------------------------------------------------------

class TestMaskRender:
    def test_rectangle_crop(self):
        m = Mask(id="m", shape="rectangle", x=0.5, y=0.5, width=0.4, height=0.4)
        f = vr.mask_crop_filter(m, 1080, 1920)
        assert f is not None and f.startswith("crop=432:768:")
        assert "scale=1080:1920" in f

    def test_feather_adds_boxblur(self):
        m = Mask(id="m", shape="rectangle", x=0.5, y=0.5, width=0.4, height=0.4, feather=16)
        f = vr.mask_crop_filter(m, 1080, 1920)
        assert "boxblur=" in f

    def test_zero_size_returns_none(self):
        m = Mask(id="m", shape="rectangle", x=0.5, y=0.5, width=0.0, height=0.0)
        assert vr.mask_crop_filter(m, 1080, 1920) is None

    def test_none_mask(self):
        assert vr.mask_crop_filter(None, 1080, 1920) is None

    def test_clamp_to_frame(self):
        # 越界中心 → clamp 到画幅内，不产生负 crop
        m = Mask(id="m", shape="rectangle", x=0.0, y=0.0, width=0.5, height=0.5)
        f = vr.mask_crop_filter(m, 1080, 1920)
        assert f is not None
        assert ":0:0," in f  # cx=0, cy=0


# ---------------------------------------------------------------------------
# §15 pip → scale + pad 到角落小窗
# ---------------------------------------------------------------------------

class TestPipRender:
    def test_pip_scales_and_positions(self):
        pip = {"transform": {"x": 0.78, "y": 0.22, "scale": 0.35}, "radius": 8}
        f = vr.pip_filter_expr(pip, 1080, 1920)
        assert f is not None
        assert "scale=378:672" in f
        assert "pad=1080:1920:" in f

    def test_pip_clamps_position(self):
        pip = {"transform": {"x": 0.0, "y": 0.0, "scale": 0.35}}
        p = vr.pip_transform_params(pip, 1080, 1920)
        assert p["x"] == 0 and p["y"] == 0

    def test_no_pip_returns_none(self):
        assert vr.pip_filter_expr(None, 1080, 1920) is None

    def test_pip_scale_bounds(self):
        # scale 越界 → clamp 到 [0.1, 1.0]
        pip = {"transform": {"x": 0.5, "y": 0.5, "scale": 2.0}}
        p = vr.pip_transform_params(pip, 1080, 1920)
        assert p["w"] <= 1080


# ---------------------------------------------------------------------------
# §16 morph → 过渡类型
# ---------------------------------------------------------------------------

class TestMorphRender:
    def test_morph_path_maps_to_transition(self):
        assert vr.morph_transition_type('{"from_block":"ui.button","to_block":"ui.arrow"}') == "morph"

    def test_no_morph_none(self):
        assert vr.morph_transition_type(None) is None
        assert vr.morph_transition_type("") is None


# ---------------------------------------------------------------------------
# 集成：render_composition 真实接线（命令 → IR → 渲染段有视觉后处理）
# ---------------------------------------------------------------------------

class TestRenderIntegration:
    def test_renderer_imports_visual_render(self):
        import video_director.renderer as r
        src = __import__('inspect').getsource(r.render_composition)
        assert "visual_render" in src, "render_composition 必须消费 visual_render"
        assert "apply_camera_filter" in src or "mask_crop_filter" in src or "pip_filter_expr" in src

    def test_morph_wired_to_transition(self):
        import video_director.renderer as r
        src = __import__('inspect').getsource(r.render_composition)
        assert 'continuity' in src and 'morph' in src