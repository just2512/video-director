"""视觉语义归一六 Contract 单元测试。

覆盖 Transform/Keyframe/Mask/Camera/Continuity/Evidence 的：
    - 字段指纹（与方案 §72 对齐的字段名/枚举值）
    - 边界校验（fail-closed：越界/非法枚举/时间倒序必须报错）
    - 向后兼容（旧 IR 无新字段可加载，None/空默认）
    - 查重审计回归（CompositionIR 顶层新结构与旧结构共存）
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from video_director import visual_semantics as vs
from video_director.models import (
    CompositionIR,
    Layer,
    LayerType,
    ReferenceType,
    SceneIR,
)


def _ir(**scene_extra) -> CompositionIR:
    """最小合法 IR（1 场景 1 图层）。"""
    scene = SceneIR(
        id="s1", start=0.0, duration=5.0, text="测试",
        layers=[Layer(type=LayerType.image, duration=5.0)],
        **scene_extra,
    )
    return CompositionIR(
        project={"width": 1080, "height": 1920, "fps": 30, "duration": 5.0},
        scenes=[scene],
    )


# ---------------------------------------------------------------------------
# ① Transform Contract（§25）
# ---------------------------------------------------------------------------

class TestTransform:
    def test_fields_fingerprint(self):
        """§72.1 字段指纹：x/y/scale/rotation/opacity/anchor。"""
        t = vs.Transform(x=0.3, y=0.7, scale=1.2, rotation=15.0, opacity=0.9)
        assert t.x == 0.3 and t.y == 0.7
        assert t.scale == 1.2 and t.rotation == 15.0
        assert t.opacity == 0.9
        assert t.anchor == vs.Anchor.center

    def test_defaults_are_identity(self):
        """默认=恒等变换（旧层无 transform 语义不变）。"""
        t = vs.Transform()
        assert (t.x, t.y, t.scale, t.rotation, t.opacity) == (0.5, 0.5, 1.0, 0.0, 1.0)

    def test_bounds_fail_closed(self):
        """越界必须 fail-closed。"""
        with pytest.raises(ValidationError):
            vs.Transform(x=1.5)
        with pytest.raises(ValidationError):
            vs.Transform(opacity=1.5)
        with pytest.raises(ValidationError):
            vs.Transform(scale=0.01)

    def test_with_update_immutable(self):
        """with_update 返回新副本，不改原对象。"""
        t = vs.Transform()
        t2 = t.with_update(scale=2.0)
        assert t.scale == 1.0 and t2.scale == 2.0

    def test_nine_anchors(self):
        """九宫格 anchor 完整。"""
        vals = {a.value for a in vs.Anchor}
        assert vals == {
            "top_left", "top_center", "top_right",
            "center_left", "center", "center_right",
            "bottom_left", "bottom_center", "bottom_right",
        }


# ---------------------------------------------------------------------------
# ② Keyframe Contract（§23）
# ---------------------------------------------------------------------------

class TestKeyframe:
    def test_track_property_enum(self):
        """§23 property 枚举含 x/y/scale/rotation/opacity/volume/mask_path/camera_*。"""
        assert "mask_path" in vs.KEYFRAME_PROPERTIES
        assert "camera_x" in vs.KEYFRAME_PROPERTIES
        assert "volume" in vs.KEYFRAME_PROPERTIES

    def test_keyframe_shape(self):
        """§23 Keyframe {at, value, easing}。"""
        k = vs.Keyframe(at=1.0, value=0.5, easing="ease_out")
        assert k.easing == vs.Easing.ease_out
        assert k.at == 1.0

    def test_rejects_unknown_property(self):
        with pytest.raises(ValidationError, match="非法"):
            vs.KeyframeTrack(property="bogus")

    def test_rejects_backwards_time(self):
        """时间倒序 fail-closed（jianying 协议：严格递增）。"""
        with pytest.raises(ValidationError, match="早于前帧"):
            vs.KeyframeTrack(property="opacity", keyframes=[
                {"at": 2.0, "value": 1.0}, {"at": 1.0, "value": 0.5},
            ])

    def test_easing_enum(self):
        assert {e.value for e in vs.Easing} == {"linear", "ease_in", "ease_out", "ease_in_out"}


# ---------------------------------------------------------------------------
# ③ Mask Contract（§20）
# ---------------------------------------------------------------------------

class TestMask:
    def test_shapes(self):
        """§20 五种形状。"""
        assert {s.value for s in vs.MaskShape} == {
            "rectangle", "rounded_rect", "ellipse", "polygon", "path",
        }

    def test_defaults(self):
        m = vs.Mask(id="m1")
        assert m.shape == vs.MaskShape.rectangle
        assert (m.x, m.y, m.width, m.height) == (0.5, 0.5, 0.5, 0.5)
        assert m.feather == 0.0 and m.inverted is False

    def test_polygon_requires_geometry(self):
        """polygon/path 无顶点 fail-closed。"""
        with pytest.raises(ValidationError, match="geometry"):
            vs.Mask(id="m1", shape="polygon")

    def test_opacity_bounds(self):
        with pytest.raises(ValidationError):
            vs.Mask(id="m1", opacity=1.5)


# ---------------------------------------------------------------------------
# ④ Camera Contract（§十/§十一）
# ---------------------------------------------------------------------------

class TestCamera:
    def test_camera_keyframe_shape(self):
        """§十 CameraKey {at, position, target, easing}。"""
        ck = vs.CameraKeyframe(at=0.0, position=[0, 0, 10], target=[0, 0, 0])
        assert ck.position == [0.0, 0.0, 10.0]

    def test_projection_enum(self):
        assert {p.value for p in vs.CameraProjection} == {"orthographic", "perspective"}

    def test_track_monotonic_time(self):
        with pytest.raises(ValidationError, match="单调"):
            vs.CameraTrack(id="c1", keyframes=[
                {"at": 3.0, "position": [0, 0, 0], "target": [0, 0, 0]},
                {"at": 1.0, "position": [1, 1, 0], "target": [0, 0, 0]},
            ])

    def test_follow_target(self):
        """§十一 followTarget。"""
        ct = vs.CameraTrack(id="c1", follow_target="s1#0")
        assert ct.follow_target == "s1#0"
        assert ct.constraints == []


# ---------------------------------------------------------------------------
# ⑤ Continuity Contract（§29/§三十/§31/§32）
# ---------------------------------------------------------------------------

class TestContinuity:
    def test_type_enum_eight_values(self):
        """§七十二 type 八值。"""
        assert {t.value for t in vs.ContinuityType} == {
            "carry", "zoom_through", "morph", "pan_follow",
            "camera_dive", "materialize", "gather", "hard_cut",
        }

    def test_link_requires_both_scenes(self):
        with pytest.raises(ValidationError, match="必填"):
            vs.ContinuityLink(id="cl", from_scene_id="", to_scene_id="b")

    def test_link_rejects_self_loop(self):
        with pytest.raises(ValidationError, match="同场景"):
            vs.ContinuityLink(id="cl", from_scene_id="a", to_scene_id="a")

    def test_scene_state_shape(self):
        """§32 SceneState {visibleLayers, anchorLayerId, camera, spatialMap}。"""
        st = vs.SceneState(visible_layers=["s1#0"], anchor_layer_id="s1#0",
                           spatial_map=vs.SpatialMap(layer_anchors={"s1#0": "center"}))
        assert st.camera is None
        assert st.spatial_map.layer_anchors == {"s1#0": vs.Anchor.center}


# ---------------------------------------------------------------------------
# ⑥ Evidence Graph Contract（§3/§三十八）
# ---------------------------------------------------------------------------

class TestEvidence:
    def test_evidence_type_enum(self):
        assert {e.value for e in vs.EvidenceType} == {
            "source", "runtime", "screenshot", "demo", "document",
        }

    def test_claim_status_enum(self):
        assert {s.value for s in vs.FeatureClaimStatus} == {
            "VERIFIED", "UNVERIFIED", "REJECTED",
        }

    def test_source_evidence_requires_path(self):
        """source/runtime 证据缺 source_path fail-closed。"""
        with pytest.raises(ValidationError, match="source_path"):
            vs.EvidenceRef(id="e1", evidence_type="source")

    def test_reuse_mode_enum(self):
        """§39 reuseMode 三值。"""
        with pytest.raises(ValidationError, match="reuse_mode"):
            vs.EvidenceRef(id="e1", reuse_mode="FAKE")

    def test_only_verified_enters_promo(self):
        """§三十八：只有 VERIFIED claim 自动进宣传片。"""
        g = vs.ProjectEvidenceGraph(claims=[
            {"id": "c1", "text": "已验证功能", "status": "VERIFIED"},
            {"id": "c2", "text": "未验证功能", "status": "UNVERIFIED"},
        ])
        verified = g.verified_claims()
        assert len(verified) == 1 and verified[0].id == "c1"

    def test_evidence_lookup(self):
        g = vs.ProjectEvidenceGraph(evidence=[
            {"id": "e1", "evidence_type": "source", "source_path": "src/x.tsx"},
        ])
        assert g.evidence_by_id("e1").source_path == "src/x.tsx"
        assert g.evidence_by_id("nope") is None


# ---------------------------------------------------------------------------
# IR 集成（§十六/§31/§七十⑤）
# ---------------------------------------------------------------------------

class TestIRIntegration:
    def test_layer_holds_transform_keyframes_mask(self):
        """Layer 挂 transform/keyframes/mask（后向兼容默认空）。"""
        ir = _ir()
        layer = ir.scenes[0].layers[0]
        assert layer.transform is None
        assert layer.keyframes == []
        assert layer.mask is None

    def test_layer_with_full_visual_semantics(self):
        """层可带完整视觉语义结构（构造时解析嵌套模型）。"""
        ir = _ir()
        scene = ir.scenes[0]
        new_layer = Layer.model_validate({
            **scene.layers[0].model_dump(),
            "transform": {"x": 0.4, "y": 0.6, "scale": 1.1},
            "keyframes": [{"property": "opacity",
                           "keyframes": [{"at": 0, "value": 0.0}, {"at": 1, "value": 1.0}]}],
            "mask": {"id": "m1", "shape": "ellipse", "width": 0.6, "height": 0.6},
        })
        scene.layers[0] = new_layer
        layer = ir.scenes[0].layers[0]
        assert layer.transform.scale == 1.1
        assert layer.keyframes[0].property == "opacity"
        assert layer.mask.shape == vs.MaskShape.ellipse

    def test_scene_contract_fields(self):
        """§31 Scene：intent/visualIntent/entrance/exit/continuityIn/Out/camera。"""
        ir = _ir(intent="强调核心卖点", visual_intent="产品特写推近",
                 entrance_state={"visible_layers": ["s1#0"]},
                 exit_state=vs.SceneState(
                     visible_layers=["s1#0"],
                     spatial_map=vs.SpatialMap(layer_anchors={"s1#0": "center"})))
        sc = ir.scenes[0]
        assert sc.intent == "强调核心卖点"
        assert sc.entrance_state.visible_layers == ["s1#0"]
        assert sc.exit_state.spatial_map.layer_anchors == {"s1#0": vs.Anchor.center}
        assert sc.continuity_in is None

    def test_composition_top_level_fields(self):
        """§十六 Composition 顶层：styleContract/cameraTracks/continuityLinks/assets。"""
        ir = _ir()
        ir.style_contract = vs.StyleContract(style_id="editorial", motion_temper="cinematic")
        ir.camera_tracks = [vs.CameraTrack(id="cam1", follow_target="s1#0")]
        ir.continuity_links = [vs.ContinuityLink(
            id="cl1", from_scene_id="s1", to_scene_id="s2", type="carry")]
        assert ir.style_contract.motion_temper == "cinematic"
        assert ir.camera_tracks[0].follow_target == "s1#0"
        assert ir.continuity_links[0].type == vs.ContinuityType.carry
        assert ir.assets == []

    def test_style_contract_fail_closed(self):
        with pytest.raises(ValidationError):
            vs.StyleContract(motion_temper="bogus")

    def test_legacy_ir_json_still_loads(self):
        """旧 IR JSON（无任何新字段）加载不爆炸（legacy loader 兼容）。"""
        import json
        legacy = {
            "version": 1,
            "project": {"width": 1080, "height": 1920, "fps": 30, "duration": 5.0},
            "scenes": [{
                "id": "s1", "start": 0.0, "duration": 5.0, "text": "旧项目",
                "layers": [{"type": "image", "duration": 5.0}],
            }],
        }
        ir = CompositionIR.model_validate(json.loads(json.dumps(legacy)))
        assert ir.scenes[0].layers[0].transform is None
        assert ir.style_contract is None
        assert ir.continuity_links == []

    def test_reference_type_enum_intact(self):
        """§17 ReferenceGraph 与新 Contract 共存不冲突。"""
        assert ReferenceType.first_frame.value == "first_frame"
        assert ReferenceType.mask_reference.value == "mask_reference"


# ---------------------------------------------------------------------------
# 能力族登记（§71）
# ---------------------------------------------------------------------------

class TestCapabilityFamilies:
    def test_visual_family(self):
        from video_director.capability_registry.manifest import manifest
        caps = manifest()
        for cid in ("visual.concept", "visual.storyboard", "visual.continuity.plan",
                    "visual.continuity.verify", "camera.apply", "keyframe.add",
                    "keyframe.remove", "mask.apply", "matting.generate",
                    "pip.configure", "visual.morph"):
            assert cid in caps, f"缺 {cid}"

    def test_subtitle_family_extended(self):
        from video_director.capability_registry.manifest import manifest
        caps = manifest()
        for cid in ("subtitle.word_token", "subtitle.highlight", "subtitle.karaoke",
                    "subtitle.kinetic", "subtitle.layout", "subtitle.safe_zone"):
            assert cid in caps, f"缺 {cid}"

    def test_gfx_family_split(self):
        """§七十③ GFX 拆分注册（不再一揽子）。"""
        from video_director.capability_registry.manifest import manifest
        caps = manifest()
        for cid in ("gfx.html", "gfx.svg", "gfx.canvas", "gfx.lottie",
                    "gfx.chart", "gfx.data_viz", "gfx.three", "gfx.webgpu"):
            assert cid in caps, f"缺 {cid}"

    def test_promo_family(self):
        from video_director.capability_registry.manifest import manifest
        caps = manifest()
        for cid in ("promo.repo_discovery", "promo.project_scan",
                    "promo.feature_discovery", "promo.runtime_launch",
                    "promo.component_trace", "promo.evidence_graph",
                    "promo.product_brief", "promo.concept", "promo.storyboard",
                    "promo.render", "promo.verify"):
            assert cid in caps, f"缺 {cid}"

    def test_agent_family(self):
        from video_director.capability_registry.manifest import manifest
        caps = manifest()
        for cid in ("agent.mcp", "agent.cli", "agent.context",
                    "agent.job_watch", "agent.tool_discovery"):
            assert cid in caps, f"缺 {cid}"

    def test_kind_inference(self):
        from video_director.capability_registry.manifest import manifest
        caps = manifest()
        assert caps["keyframe.add"].kind == "command"
        assert caps["visual.continuity.verify"].kind == "query"
        assert caps["promo.repo_discovery"].kind == "query"
        assert caps["promo.render"].kind == "command"
