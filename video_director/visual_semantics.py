"""视觉语义归一：Transform / Keyframe / Mask / Camera / Continuity / Evidence 六 Contract。

这是「系统能力归一」之上再推进一步的「视觉语义归一」核心数据契约。
用途：把「AI 视频编辑器」从「功能归一」推向「视觉语义归一」——
    Transform（层变换）    Keyframe（关键帧）   Mask（几何蒙版）
    Camera（摄影机轨）     Continuity（镜头连续性） Evidence（项目证据图）

来源：`D:\\openworkplace\\content-wiki\\视觉语义归一.md`，Clean-room 吸收
Remotion Skills / ffmpeg-skill / Jianying Headless / Guizang / Onetake 的
「协议」重新设计自己的实现（§55 商业安全矩阵：仅 ffmpeg-skill MIT 可复制，
其余只学 schema 不抄代码）。

设计原则（延续 [[code-complete-not-system-complete]] / [[no-scoring-boundary]]）：
    - 全部 Pydantic v2，字段 snake_case（与 models.py 现状一致），
      JSON 边界的 camelCase 由 server.py 手写映射（与现有分层一致）。
    - 全部 Optional / 有默认值 → 旧 IR 不迁移即可加载（legacy loader 兼容）。
    - 零评分：不存 similarity / 概率 / 权重，只存结构化事实。
    - 诚实边界：Renderer 未实现的属性（如 3D camera perspective）保留 schema，
      渲染时诚实 UNAVAILABLE，不 fake success。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Anchor：九宫格锚点（Transform / Continuity / Mask 共用坐标锚定）
# ---------------------------------------------------------------------------

class Anchor(str, Enum):
    """九宫格锚点。Composition Space 里层/掩模/连续性锚到画幅的相对位置。

    WatermarkLayer.anchor 已用字符串九宫格，这里收敛为统一枚举，
    新 Transform/Continuity/Mask 复用，避免三套松散字符串漂移。
    """
    top_left = "top_left"
    top_center = "top_center"
    top_right = "top_right"
    center_left = "center_left"
    center = "center"
    center_right = "center_right"
    bottom_left = "bottom_left"
    bottom_center = "bottom_center"
    bottom_right = "bottom_right"


# ---------------------------------------------------------------------------
# ① Transform Contract（§25 / §七十① / §72.1）
# ---------------------------------------------------------------------------

class Transform(BaseModel):
    """层静态变换（§25：不再是 motion_instance.parameters 里的松散 dict）。

    坐标采用 Composition Space 归一（x/y ∈ [0,1]，轴锚在 anchor），
    前端 Viewport 用 CoordinateMapper 做 screenToComposition() 换算，
    Renderer 按项目画幅还原为像素。这样拖拽/Resize/旋转/Mask/Camera 共用一套坐标。
    """

    x: float = 0.5            # 锚点相对画幅 width 的归一水平位置 [0,1]
    y: float = 0.5            # 锚点相对画幅 height 的归一垂直位置 [0,1]
    scale: float = 1.0        # 缩放（>1 放大）
    rotation: float = 0.0     # 旋转角度（度）
    opacity: float = 1.0      # 不透明度 [0,1]
    anchor: Anchor = Anchor.center

    @model_validator(mode="after")
    def _check(self):
        if not (0.0 <= self.x <= 1.0):
            raise ValueError(f"transform: x {self.x} 越界 [0,1]")
        if not (0.0 <= self.y <= 1.0):
            raise ValueError(f"transform: y {self.y} 越界 [0,1]")
        if not (0.1 <= self.scale <= 5.0):
            raise ValueError(f"transform: scale {self.scale} 越界 [0.1,5.0]")
        if not (0.0 <= self.opacity <= 1.0):
            raise ValueError(f"transform: opacity {self.opacity} 越界 [0,1]")
        return self

    def with_update(self, **patch) -> "Transform":
        """返回新副本（不可变风格），不改原对象。"""
        return self.model_copy(update=patch)


# ---------------------------------------------------------------------------
# ② Keyframe Contract（§23 / §24 / §七十③ / §72.2）
# ---------------------------------------------------------------------------

class Easing(str, Enum):
    """关键帧缓动（Remotion/Jianying 协议收敛的最小集）。"""
    linear = "linear"
    ease_in = "ease_in"
    ease_out = "ease_out"
    ease_in_out = "ease_in_out"


#: §23 KeyframeTrack.property 合法值。offset_x/offset_y 是 FilmRuntime
#: 支持的设计空间像素偏移；其他未实现属性必须先扩展显式语义合同。
KEYFRAME_PROPERTIES = {
    "x", "y", "scale", "scale_x", "scale_y", "rotation", "opacity", "blur",
    "volume", "mask_path", "camera_x", "camera_y", "camera_scale",
    "offset_x", "offset_y",
}


class Keyframe(BaseModel):
    """§23：单条关键帧 {at, value, easing}。value 语义由 property 决定。"""
    at: float                 # 时间（相对层的秒，或相对轨道的秒）
    value: Any                # 数值（x/y/scale/rotation/... 的实际值）
    easing: Easing = Easing.linear

    @model_validator(mode="after")
    def _check(self):
        if self.at < 0:
            raise ValueError(f"keyframe: at {self.at} 为负")
        return self


class KeyframeTrack(BaseModel):
    """§23：一条属性的关键帧轨道（property + keyframes + 隐式 easing 默认）。"""
    property: str
    keyframes: List[Keyframe] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self):
        if self.property not in KEYFRAME_PROPERTIES:
            raise ValueError(
                f"keyframe_track: property {self.property!r} 非法，"
                f"应为 {sorted(KEYFRAME_PROPERTIES)}"
            )
        # 关键帧时间必须单调不减（§23 时间轴连续）
        for i in range(1, len(self.keyframes)):
            if self.keyframes[i].at < self.keyframes[i - 1].at:
                raise ValueError(
                    f"keyframe_track({self.property}): 关键帧 @{self.keyframes[i].at} "
                    f"早于前帧 @{self.keyframes[i - 1].at}"
                )
        return self


# ---------------------------------------------------------------------------
# ③ Mask Contract（§20 / §七十③ / §72.3）
# ---------------------------------------------------------------------------

class MaskShape(str, Enum):
    """§20 Mask 几何形状（rectangle/roundedRect/ellipse/polygon/path）。"""
    rectangle = "rectangle"
    rounded_rect = "rounded_rect"
    ellipse = "ellipse"
    polygon = "polygon"
    path = "path"


class Mask(BaseModel):
    """§20 几何蒙版（Mask ≠ Matte：Mask 是几何区域，Matte 是 alpha/segmentation）。

    参数对齐 §20：position/size/feather/invert/opacity/keyframes。
    geometry 存 polygon/path 的顶点（[x,y] 归一坐标列表）。
    """

    id: str
    shape: MaskShape = MaskShape.rectangle
    x: float = 0.5            # 蒙版中心归一水平位置 [0,1]
    y: float = 0.5            # 蒙版中心归一垂直位置 [0,1]
    width: float = 0.5        # 归一宽度 [0,1]
    height: float = 0.5       # 归一高度 [0,1]
    feather: float = 0.0      # 羽化（px，渲染按画幅换算）
    inverted: bool = False    # §20 invert
    opacity: float = 1.0      # 蒙版透明度
    animated: bool = False    # §20 keyframes 是否启用
    keyframes: List[KeyframeTrack] = Field(default_factory=list)
    geometry: List[List[float]] = Field(default_factory=list)  # polygon/path 顶点

    @model_validator(mode="after")
    def _check(self):
        if not (0.0 <= self.opacity <= 1.0):
            raise ValueError(f"mask({self.id}): opacity {self.opacity} 越界 [0,1]")
        if self.feather < 0:
            raise ValueError(f"mask({self.id}): feather {self.feather} 为负")
        if not (0.0 <= self.width <= 1.0) or not (0.0 <= self.height <= 1.0):
            raise ValueError(f"mask({self.id}): width/height 越界 [0,1]")
        if self.shape in (MaskShape.polygon, MaskShape.path) and len(self.geometry) < 3:
            raise ValueError(f"mask({self.id}): {self.shape.value} 需 geometry 至少 3 顶点")
        return self


# ---------------------------------------------------------------------------
# ④ Camera Contract（§十 / §十一 / §72.4）
# ---------------------------------------------------------------------------

class CameraProjection(str, Enum):
    """§十一 CameraTrack.projection（orthographic/perspective）。"""
    orthographic = "orthographic"
    perspective = "perspective"


class CameraKeyframe(BaseModel):
    """§十 CameraKey：摄影机关键帧 {at, position, target}。

    position/target 为 Composition Space 的三维坐标 [x, y, z]（透视 z 为深度，
    Renderer 未实现 perspective 时诚实 UNAVAILABLE，不 fake 3D）。
    """
    at: float
    position: List[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    target: List[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    easing: Easing = Easing.linear

    @model_validator(mode="after")
    def _check(self):
        if len(self.position) != 3 or len(self.target) != 3:
            raise ValueError("camera_keyframe: position/target 需 [x,y,z] 三元组")
        if self.at < 0:
            raise ValueError(f"camera_keyframe: at {self.at} 为负")
        return self


class CameraConstraint(BaseModel):
    """§十一 CameraTrack.constraints：摄影机运动约束（目标/禁区/时间）。

    当前定义 follow 目标约束，未来扩展 keep_in_frame / avoid_region。
    """
    kind: str = "follow"      # follow（跟随目标）
    target: str = ""          # 目标层 id 或语义锚点 id


class CameraTrack(BaseModel):
    """§十一 CameraTrack：摄影机轨道（keyframes/projection/followTarget/constraints）。"""
    id: str
    keyframes: List[CameraKeyframe] = Field(default_factory=list)
    projection: CameraProjection = CameraProjection.perspective
    follow_target: Optional[str] = None    # §十一 followTarget（跟拍目标层）
    constraints: List[CameraConstraint] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self):
        for i in range(1, len(self.keyframes)):
            if self.keyframes[i].at < self.keyframes[i - 1].at:
                raise ValueError(f"camera_track({self.id}): 关键帧时间非单调递增")
        return self


# ---------------------------------------------------------------------------
# ⑤ Continuity Contract（§29 / §三十 / §31 / §32 / §72.5）
# ---------------------------------------------------------------------------

class ContinuityType(str, Enum):
    """§29/§七十二 ContinuityLink.type 八值。

    carry/zoom_through/morph/pan_follow/camera_dive/materialize/gather/hard_cut
    """
    carry = "carry"
    zoom_through = "zoom_through"
    morph = "morph"
    pan_follow = "pan_follow"
    camera_dive = "camera_dive"
    materialize = "materialize"
    gather = "gather"
    hard_cut = "hard_cut"


class ContinuityLink(BaseModel):
    """§七十二 ContinuityLink：镜头间视觉延续（Onetake Carry 协议 Clean-room）。

    明确 source → target 的层/锚点/摄影机路径/形变路径，让
    ContinuityVerifier 能区分「intentional hard cut」与「unexplained replacement」。
    """

    id: str
    from_scene_id: str
    to_scene_id: str
    type: ContinuityType = ContinuityType.hard_cut
    source_layer_id: Optional[str] = None
    target_layer_id: Optional[str] = None
    source_anchor: Optional[Anchor] = None
    target_anchor: Optional[Anchor] = None
    camera_path: Optional[str] = None     # 摄影机路径 id（引用 CameraTrack.id）
    morph_path: Optional[str] = None      # 形变路径 id（引用 morph 定义）

    @model_validator(mode="after")
    def _check(self):
        if not self.from_scene_id or not self.to_scene_id:
            raise ValueError("continuity_link: from_scene_id/to_scene_id 必填")
        if self.from_scene_id == self.to_scene_id:
            raise ValueError("continuity_link: from/to 不能是同场景")
        return self


class CameraState(BaseModel):
    """§32 SceneState.camera：场景进入/退出时的摄影机状态快照。"""
    position: List[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    target: List[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])


class SpatialMap(BaseModel):
    """§32 SceneState.spatialMap：场景内层的空间关系（哪个层在哪个锚点）。

    用于 ContinuityVerifier 检查「Scene A exitState == Scene B entranceState」。
    """
    layer_anchors: Dict[str, Anchor] = Field(default_factory=dict)  # layer_id → Anchor


class SceneState(BaseModel):
    """§32 SceneState：场景进入/退出状态（visibleLayers/anchorLayerId/camera/spatialMap）。"""

    visible_layers: List[str] = Field(default_factory=list)
    anchor_layer_id: Optional[str] = None
    camera: Optional[CameraState] = None
    spatial_map: Optional[SpatialMap] = None


# ---------------------------------------------------------------------------
# ⑥ Evidence Graph Contract（§3 / §三十七 / §三十八 / §72.6）
# ---------------------------------------------------------------------------

class EvidenceType(str, Enum):
    """§3 EvidenceRef.evidenceType 五值（source/runtime/screenshot/demo/document）。"""
    source = "source"          # 源码证据（component/route/symbol）
    runtime = "runtime"        # 运行时真实打开
    screenshot = "screenshot"  # 真实截图
    demo = "demo"              # 演示录制
    document = "document"      # 文档证据


class EvidenceRef(BaseModel):
    """§3 证据引用：宣传片镜头 → 代码/运行时/截图证据的追溯节点。

    sourcePath 指向真实代码路径（§39 reuseMode 区分 REAL_COMPONENT /
    FAITHFUL_REBUILD / SCREEN_CAPTURE，这里用 evidence_type 表达证据形态）。
    """
    id: str
    project_snapshot_id: str = ""
    source_path: str = ""          # 代码文件路径（evidence_type=source/runtime）
    symbol: Optional[str] = None   # 组件/符号名
    route: Optional[str] = None    # 前端 route
    state: Optional[Dict[str, Any]] = None  # 触发时的真实状态
    artifact_id: Optional[str] = None       # 截图/录制产物 id
    evidence_type: EvidenceType = EvidenceType.source
    reuse_mode: str = "SCREEN_CAPTURE"  # §39 REAL_COMPONENT/FAITHFUL_REBUILD/SCREEN_CAPTURE

    @model_validator(mode="after")
    def _check(self):
        if self.reuse_mode not in ("REAL_COMPONENT", "FAITHFUL_REBUILD", "SCREEN_CAPTURE"):
            raise ValueError(f"evidence_ref({self.id}): reuse_mode {self.reuse_mode!r} 非法")
        if self.evidence_type in (EvidenceType.source, EvidenceType.runtime) and not self.source_path:
            raise ValueError(f"evidence_ref({self.id}): {self.evidence_type.value} 证据需 source_path")
        return self


class FeatureClaimStatus(str, Enum):
    """§3 FeatureClaim.status 三值（VERIFIED/UNVERIFIED/REJECTED）。"""
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    REJECTED = "REJECTED"


class FeatureClaim(BaseModel):
    """§3 功能声明：宣传片里的每条卖点都必须有证据链（不允许 AI 编造）。"""
    id: str
    text: str
    evidence_refs: List[str] = Field(default_factory=list)  # 引用 EvidenceRef.id
    status: FeatureClaimStatus = FeatureClaimStatus.UNVERIFIED


class ProjectEvidenceGraph(BaseModel):
    """§3/§七十二 ProjectEvidenceGraph：项目功能声明 + 证据的图。

    让 Repo→Promo 的每个 Storyboard Shot 都能追溯「哪个功能来自哪个组件/route/证据」。
    """
    project_snapshot_id: str = ""
    claims: List[FeatureClaim] = Field(default_factory=list)
    evidence: List[EvidenceRef] = Field(default_factory=list)

    def claim_by_id(self, claim_id: str) -> Optional[FeatureClaim]:
        for c in self.claims:
            if c.id == claim_id:
                return c
        return None

    def evidence_by_id(self, evidence_id: str) -> Optional[EvidenceRef]:
        for e in self.evidence:
            if e.id == evidence_id:
                return e
        return None

    def verified_claims(self) -> List[FeatureClaim]:
        """§三十八：只有 VERIFIED 的 claim 才能自动进入宣传片。"""
        return [c for c in self.claims if c.status == FeatureClaimStatus.VERIFIED]

    def to_dict(self) -> dict:
        return self.model_dump()


# ---------------------------------------------------------------------------
# StyleContract（§十六 Composition.styleContract）
# ---------------------------------------------------------------------------

class StyleContract(BaseModel):
    """§十六 Composition.styleContract：风格契约（style registry 引用 + 参数）。

    与 Variant.style（Optional[dict]）互补：Variant 存宽松 dict，这里给 Composition
    顶层一个结构化契约，Renderer 据此统一还原视觉风格，不再依赖松散字符串。
    """
    style_id: str = ""
    palette: Optional[Dict[str, str]] = None     # 语义色板（主/辅/强调/背景）
    typography: Optional[Dict[str, str]] = None  # 字体族/字重/字号比
    motion_temper: str = "calm"                  # calm | energetic | cinematic
    safe_zone: float = 0.9                       # 安全区占比 [0,1]

    @model_validator(mode="after")
    def _check(self):
        if self.motion_temper not in ("calm", "energetic", "cinematic"):
            raise ValueError(f"style_contract: motion_temper {self.motion_temper!r} 非法")
        if not (0.5 <= self.safe_zone <= 1.0):
            raise ValueError(f"style_contract: safe_zone {self.safe_zone} 越界 [0.5,1.0]")
        return self