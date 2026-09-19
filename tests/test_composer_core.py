"""Composer 单元测试。

Gate 1 覆盖（V1 §42 测试矩阵 + V2 §87）：
- 变速双层规则（Case 01-04：5s/7s/10s/15s 目标 vs 8s 源）
- 图片补时 / 纯图片场景（Case 05/06）
- MISSING_ASSET 诚实上报（Case 07）
- IR schema 连续性校验
- Validator 业务规则
- 文案切分
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 项目根入 sys.path（测试从任意 cwd 运行）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from video_director import config, planner as planner_mod, validator as validator_mod
from video_director.models import (
    AssetRef,
    AudioTrack,
    AudioTrackType,
    CompositionIR,
    Layer,
    LayerType,
    MissingAssetError,
    ProjectSpec,
    SceneIR,
    Selection,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def make_video_asset(aid: str, duration: float = 8.0) -> AssetRef:
    return AssetRef(
        id=aid, type="video", file=f"videos/{aid}.mp4",
        duration=duration, width=720, height=1280, fps=25,
    )


def make_image_asset(aid: str) -> AssetRef:
    return AssetRef(
        id=aid, type="image", file=f"images/{aid}.jpg",
        width=720, height=1280,
    )


def make_scenes(durations: list) -> list:
    scenes, cursor = [], 0.0
    for i, d in enumerate(durations):
        scenes.append({
            "id": f"S{i+1:02d}", "text": f"场景{i+1}文案",
            "start": round(cursor, 3), "duration": round(d, 3),
        })
        cursor += d
    return scenes


# ---------------------------------------------------------------------------
# 变速双层规则（V1 Case 01-04 + V2 §12）
# ---------------------------------------------------------------------------

class TestFitVideo:
    P = planner_mod.RulePlanner()

    def test_case01_8s_to_5s_speedup(self):
        # 8/5 = 1.6 在 absolute 内（preferred 外但允许）
        take, speed, out = self.P._fit_video(8.0, 5.0)
        assert abs(speed - 1.6) < 0.01
        assert abs(out - 5.0) < 0.05

    def test_case02_8s_to_7s_mild_speedup(self):
        take, speed, out = self.P._fit_video(8.0, 7.0)
        assert abs(speed - 8/7) < 0.01
        assert abs(out - 7.0) < 0.05

    def test_case03_8s_to_10s_no_slowmo_with_image(self):
        # required = 0.8 < 0.90 且 有图片兜底：不慢放，0.9x 播完交补时
        take, speed, out = self.P._fit_video(8.0, 10.0, image_fallback=True)
        assert speed == config.SPEED_PREFERRED_MIN
        assert abs(out - 8.0 / 0.9) < 0.05  # ≈8.89s，剩余 ~1.11s 交图片

    def test_case03_no_image_slowmo_covers(self):
        # 无图片兜底，required=0.8 >= absolute_min：允许慢放覆盖，不误报
        take, speed, out = self.P._fit_video(8.0, 10.0, image_fallback=False)
        assert abs(speed - 0.8) < 0.01
        assert abs(out - 10.0) < 0.05

    def test_case04_8s_to_15s_never_extreme(self):
        take, speed, out = self.P._fit_video(8.0, 15.0, image_fallback=True)
        assert speed == config.SPEED_PREFERRED_MIN  # 0.9x
        assert abs(out - 8.0 / 0.9) < 0.05          # 不是 0.53x 慢放

    def test_extreme_speedup_clipped(self):
        # required = 8/3 = 2.67 > 1.70：1.7x + 裁剪
        take, speed, out = self.P._fit_video(8.0, 3.0)
        assert speed == config.SPEED_ABSOLUTE_MAX
        assert abs(out - 3.0) < 0.05
        assert abs(take - 1.7 * 3.0) < 0.05


# ---------------------------------------------------------------------------
# 场景规划（补时/纯图/MISSING）
# ---------------------------------------------------------------------------

class TestRulePlanner:

    def test_video_plus_image_backfill(self):
        """8s 视频 + 图片池，目标 10s：视频 0.9x ≈8.89s + 图片补 ~1.11s。"""
        assets = {"v1": make_video_asset("v1"), "img1": make_image_asset("img1")}
        order = ["v1", "img1"]
        scenes = make_scenes([10.0])
        ir = planner_mod.RulePlanner().plan(scenes, assets, order)
        scene = ir.scenes[0]
        types = [l.type for l in scene.layers]
        assert LayerType.video in types and LayerType.image in types
        total = sum(l.duration for l in scene.layers)
        assert abs(total - 10.0) < 0.1

    def test_pure_image_scene(self):
        """Case 05：只有图片，图片占满 7.5s。"""
        assets = {"img1": make_image_asset("img1")}
        ir = planner_mod.RulePlanner().plan(make_scenes([7.5]), assets, ["img1"])
        assert len(ir.scenes[0].layers) == 1
        layer = ir.scenes[0].layers[0]
        assert layer.type == LayerType.image
        assert abs(layer.duration - 7.5) < 0.01

    def test_image_motion_default(self):
        """>2s 图片默认 slow_zoom_in（V2 §14）。"""
        assets = {"img1": make_image_asset("img1")}
        ir = planner_mod.RulePlanner().plan(make_scenes([5.0]), assets, ["img1"])
        assert ir.scenes[0].layers[0].motion == "slow_zoom_in"

    def test_missing_asset_honest(self):
        """Case 07 语义（V1 §16 循环复用激活后）：单素材长场景应循环覆盖
        （'最后允许重复素材'档），而非报错。真正素材不足（零可用素材）
        的报错路径由 compose_project 的空资产检查承担。"""
        assets = {"v1": make_video_asset("v1")}  # 只有 8s 视频
        ir = planner_mod.RulePlanner().plan(make_scenes([20.0]), assets, ["v1"])
        # 单视频循环 2.5 次铺满 20s：层数 > 1 且总时长 = 20
        layers = ir.scenes[0].layers
        assert len(layers) >= 2, "长场景应循环复用素材而非单层"
        total = sum(l.duration for l in layers)
        assert abs(total - 20.0) < 0.1
        # 每层 source 区间都必须在素材时长内（循环后游标归零合法）
        for l in layers:
            assert l.source_out <= 8.0 + 0.05

    def test_scene_continuity_guaranteed(self):
        assets = {f"v{i}": make_video_asset(f"v{i}") for i in range(1, 4)}
        order = [f"v{i}" for i in range(1, 4)]
        scenes = make_scenes([5.0, 7.0, 3.0])
        ir = planner_mod.RulePlanner().plan(scenes, assets, order)
        assert abs(ir.scenes[0].start) < 0.01
        for i in range(1, len(ir.scenes)):
            prev, cur = ir.scenes[i-1], ir.scenes[i]
            assert abs(prev.start + prev.duration - cur.start) < 0.01


# ---------------------------------------------------------------------------
# IR schema 校验
# ---------------------------------------------------------------------------

class TestIRSchema:

    def _ir(self, scenes: list) -> CompositionIR:
        return CompositionIR(
            project=ProjectSpec(duration=scenes[-1].start + scenes[-1].duration),
            scenes=scenes,
        )

    def test_scene_gap_rejected(self):
        s1 = SceneIR(id="S01", start=0.0, duration=3.0, text="a",
                     layers=[Layer(type=LayerType.image, duration=3.0, asset_id="i")])
        s2 = SceneIR(id="S02", start=3.5, duration=2.0, text="b",
                     layers=[Layer(type=LayerType.image, duration=2.0, asset_id="i")])
        with pytest.raises(Exception):
            self._ir([s1, s2])

    def test_layer_duration_consistency(self):
        with pytest.raises(Exception):
            Layer(type=LayerType.video, duration=5.0, asset_id="v",
                  speed=1.5, source_in=0.0, source_out=5.0)  # 5/1.5=3.33≠5

    def test_selection_range(self):
        with pytest.raises(Exception):
            Selection(id="sel1", text="x", start=5.0, end=3.0)

    def test_unrenderable_layer_detected(self):
        ir = CompositionIR(
            project=ProjectSpec(duration=2.0),
            scenes=[SceneIR(id="S01", start=0.0, duration=2.0, text="a", layers=[
                Layer(type=LayerType.text, duration=2.0, text="hello"),
            ])],
        )
        assert len(ir.unrenderable_layers()) == 1


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class TestValidator:

    def _valid_ir(self) -> CompositionIR:
        assets = {"v1": make_video_asset("v1")}
        return planner_mod.RulePlanner().plan(make_scenes([5.0]), assets, ["v1"])

    def test_pass_on_valid(self):
        ir = self._valid_ir()
        assets = {"v1": make_video_asset("v1")}
        res = validator_mod.validate_composition(ir, assets, 5.0)
        assert res.ok, res.to_dict()

    def test_duration_mismatch_rejected(self):
        ir = self._valid_ir()
        assets = {"v1": make_video_asset("v1")}
        res = validator_mod.validate_composition(ir, assets, narration_duration=9.0)
        assert not res.ok
        assert any(i.code == "TOTAL_DURATION_MISMATCH" for i in res.issues)

    def test_speed_out_of_absolute_range(self):
        assets = {"v1": make_video_asset("v1")}
        ir = planner_mod.RulePlanner().plan(make_scenes([5.0]), assets, ["v1"])
        # 篡改 speed 超绝对边界（绕过构造校验用 object.__setattr__）
        for scene in ir.scenes:
            for layer in scene.layers:
                if layer.type == LayerType.video:
                    object.__setattr__(layer, "speed", 2.5)
        res = validator_mod.validate_composition(ir, assets, 5.0)
        assert any(i.code == "SPEED_OUT_OF_RANGE" for i in res.issues)

    def test_audio_narration_coverage(self):
        ir = self._valid_ir()
        ir.audio = [AudioTrack(type=AudioTrackType.narration, duration=5.0)]
        assets = {"v1": make_video_asset("v1")}
        res = validator_mod.validate_composition(ir, assets, 5.0)
        assert res.ok, res.to_dict()

    def test_no_narration_track_skips_coverage(self):
        """Gate 1 纯视觉（无 narration 轨）不应触发覆盖校验。"""
        ir = self._valid_ir()  # audio=[]
        assets = {"v1": make_video_asset("v1")}
        res = validator_mod.validate_composition(ir, assets, 5.0)
        assert res.ok, res.to_dict()


# ---------------------------------------------------------------------------
# 文案切分
# ---------------------------------------------------------------------------

class TestScriptSplit:

    def test_split_by_sentence(self):
        from video_director.pipeline import split_script_scenes
        script = "这是第一句话的内容足够长。这是第二句话的内容也不短！这是第三句话的内容同样达标？"
        scenes = split_script_scenes(script)
        assert len(scenes) == 3

    def test_short_sentence_merged(self):
        from video_director.pipeline import split_script_scenes
        script = "这是一个比较长的句子，信息量足够。短。再来一个正常长度的句子结尾。"
        scenes = split_script_scenes(script)
        assert all(len(s) >= 8 for s in scenes[1:])

    def test_empty_script_rejected(self):
        from video_director.pipeline import split_script_scenes
        with pytest.raises(Exception):
            split_script_scenes("   \n  ")

    def test_estimate_durations_seamless(self):
        """句间停顿计入前场景时长：场景必须无缝（IR 连续性校验前提）。"""
        from video_director.pipeline import estimate_scene_durations
        scenes = estimate_scene_durations(["第一句话内容。", "第二句话内容。", "第三句话内容。"])
        assert scenes[0]["start"] == 0.0
        for i in range(1, len(scenes)):
            prev, cur = scenes[i-1], scenes[i]
            assert abs(prev["start"] + prev["duration"] - cur["start"]) < 0.001


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_transition_rules(self):
        from video_director import registry
        assert registry.is_known_transition("cut")
        assert not registry.is_known_transition("dissolve_warp_xxx")
        assert registry.is_implemented_transition("cut")
        assert not registry.is_implemented_transition("crossfade")  # 第一版未实现
        assert not registry.is_major_effect_transition("cut")
        assert registry.is_major_effect_transition("crossfade")

    def test_motion_rules(self):
        from video_director import registry
        assert registry.is_implemented_motion(None)     # 无 motion 合法
        assert registry.is_implemented_motion("slow_zoom_in")
        assert not registry.is_implemented_motion("ken_burns")  # 预留未实现