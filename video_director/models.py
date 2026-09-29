"""Composition IR 数据契约（V2 §6-§9 + Hypit 语义锚点思想）。

IR 是 AI 规划与渲染之间唯一契约：
    LLM → Plan JSON → Validator → Composition IR → Renderer

结构（V2 §9 示例）：
    Project(project/fps/duration)
    Scene(start/duration/layers/selections/transition_out)
    Layer(type/asset_id/start/duration/speed/...)
    AudioTrack(type/asset_id/start/duration/volume/ducking)
    Selection(语义锚点：text + 输出时间轴区间)

Narration Timeline 仍是主时间轴（V2 §10）：所有 Layer/Audio 挂在旁白长度上，
转场与动画不得移动旁白和字幕时间。

向后映射：V1 Timeline/SceneTimeline/TimelineItem → SceneIR/Layer(type=video|image)。
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator, field_validator

from video_director import config


class LayerType(str, Enum):
    """V2 §8：Layer 是未来扩展的核心。

    Graphics 域（§11 新增）：dom/svg/canvas/html/chart/diagram/ui/webgl/
    webgpu/lottie/three_scene/code/table/map/data_viz —— 这些由 Graphics
    Runtime 渲染，不由 FFmpeg media renderer 渲染（§3 Renderer 分离）。
    第一版 media renderer 只实现 video/image 渲染，其余 schema 预留，
    出现即诚实 REJECT（诚实降级而非静默忽略）。
    """

    video = "video"
    image = "image"
    # ---- 以下为预留（media 第一阶段不渲染）----
    text = "text"
    title = "title"
    shape = "shape"
    svg = "svg"
    icon = "icon"
    chart = "chart"
    background = "background"
    logo = "logo"
    lottie = "lottie"
    person = "person"
    pip = "pip"
    lower_third = "lower_third"
    # ---- Graphics 域（§11：可执行视觉程序，由 Graphics Runtime 消费）----
    graphic = "graphic"
    dom = "dom"
    canvas = "canvas"
    html = "html"
    diagram = "diagram"
    ui = "ui"
    webgl = "webgl"
    webgpu = "webgpu"
    three_scene = "three_scene"
    code = "code"
    table = "table"
    map = "map"
    data_viz = "data_viz"


#: 第一版 BasicRenderer（FFmpeg media renderer）实际支持的 Layer 类型
RENDERABLE_LAYER_TYPES = {LayerType.video, LayerType.image}

#: Graphics 域 Layer 类型（§11：交由 Graphics Runtime，非 media renderer）
GRAPHICS_LAYER_TYPES = {
    LayerType.graphic, LayerType.dom, LayerType.svg, LayerType.canvas, LayerType.html,
    LayerType.chart, LayerType.diagram, LayerType.ui, LayerType.webgl,
    LayerType.webgpu, LayerType.lottie, LayerType.three_scene,
    LayerType.code, LayerType.table, LayerType.map, LayerType.data_viz,
}


class AudioTrackType(str, Enum):
    """V2 §27：声音四分。Narration 主音轨，其余背景/事件/环境。

    MPT 吸收方案 §十八：original（素材原声）必须成为一等公民——
    "保留现场声音"是独立音轨类型，不是渲染开关。
    六类型对齐方案原文：narration / dialogue / original / bgm / sfx / ambience。
    """

    narration = "narration"
    dialogue = "dialogue"
    original = "original"
    bgm = "bgm"
    sfx = "sfx"
    ambience = "ambience"
    # 兼容别名：历史 IR / 旧代码使用 ambient；加载时归一到 ambience
    ambient = "ambience"


class AssetSource(str, Enum):
    """§10 Asset 统一：7 来源枚举（系统能力归一方案）。

    与 Provider 区分：source=来源大类，provider=具体提供方。
    例 source=stock+provider=pexels；source=generated+provider=seedance；
    source=uploaded+provider=None。
    """
    local = "local"
    uploaded = "uploaded"
    stock = "stock"
    generated = "generated"
    extracted = "extracted"
    graphic = "graphic"
    hybrid = "hybrid"


#: 旧 source 松散值 → AssetSource 归一映射（兼容历史 JSON，不爆炸迁移）
ASSET_SOURCE_ALIASES = {
    "user": "uploaded",               # 用户本地导入
    "self-generated": "generated",     # 自产音频合成
    "generated": "generated",
    "pexels": "stock",
    "pixabay": "stock",
    "coverr": "stock",
    "stock": "stock",
    "shotcraft-assets": "stock",
}


def normalize_asset_source(value: str) -> str:
    """把旧松散 source 值归一到 AssetSource 枚举值（未知值原样保留）。"""
    if not value:
        return AssetSource.local.value
    return ASSET_SOURCE_ALIASES.get(value, value)


class ReferenceType(str, Enum):
    """§17 Asset Reference Graph：引用类型。

    首帧/末帧/参考图/参考视频/风格/连续性/遮罩 —— 统一 generation 的
    style:/asset:/first_frame 松散引用为结构化节点，指向 Asset/Artifact。
    """
    visual_reference = "visual_reference"
    style_reference = "style_reference"
    first_frame = "first_frame"
    last_frame = "last_frame"
    generation_reference = "generation_reference"
    continuity_reference = "continuity_reference"
    mask_reference = "mask_reference"


class Reference(BaseModel):
    """§17：一条引用节点（指向 Asset 或 Artifact，与 §10 Asset 统一）。"""
    id: str
    ref_type: ReferenceType
    target_asset_id: Optional[str] = None     # 指向 Asset
    target_artifact_id: Optional[str] = None  # 指向 Artifact
    role: str = ""                            # 语义角色（人物/产品/视觉风格...）
    note: str = ""


class ReferenceGraph(BaseModel):
    """§17：Asset/Artifact 引用图。让 Generation/Continuity/Style DNA 共用一套底层。"""
    references: List[Reference] = Field(default_factory=list)

    def of_type(self, t: ReferenceType) -> List[Reference]:
        return [r for r in self.references if r.ref_type == t]

    def first_frame_ref(self) -> Optional[Reference]:
        for r in self.references:
            if r.ref_type == ReferenceType.first_frame:
                return r
        return None

    def to_dict(self) -> dict:
        return {"references": [r.model_dump() for r in self.references]}


def reference_from_marker(marker: str) -> Reference:
    """§17：把旧松散引用标记（asset:<id> / style:<id>）升级为结构化 Reference。"""
    if marker.startswith("asset:"):
        aid = marker.split(":", 1)[1]
        return Reference(id=f"ref-asset-{aid}", ref_type=ReferenceType.generation_reference,
                         target_asset_id=aid)
    if marker.startswith("style:"):
        sid = marker.split(":", 1)[1]
        return Reference(id=f"ref-style-{sid}", ref_type=ReferenceType.style_reference, role=sid)
    return Reference(id=f"ref-{marker[:16]}", ref_type=ReferenceType.visual_reference, note=marker)


class MissingAssetError(Exception):
    """素材不足（V1 §29 / V2 §92：诚实上报，禁止编造素材）。"""

    def __init__(self, scene_id: str, required: float, available: float, message: str = ""):
        self.scene_id = scene_id
        self.required = required
        self.available = available
        super().__init__(
            message
            or f"scene {scene_id}: 素材不足，需要 {required:.2f}s，可用 {available:.2f}s"
        )


class AssetRef(BaseModel):
    """资产清单条目（ffprobe 规范化 + V2 §22 统一描述）。"""

    id: str
    type: str  # "video" | "image" | "audio"
    file: str
    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    # 上游契约字段（V1 §7）：生成提示词阶段顺便产生的语义
    prompt: Optional[str] = None
    description: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    # §10 Asset 统一：来源大类（AssetSource 枚举值）+ 溯源/权利（canonical 字段）
    source: str = "uploaded"
    license_note: Optional[str] = None
    provenance: Optional[dict] = None    # §10 Provenance（Provider/Creator/SourcePage 等）
    rights_status: Optional[str] = None  # §10 RightsStatus（CLEAR/UNKNOWN/BLOCKED/REVIEW）

    @field_validator("source", mode="before")
    @classmethod
    def _norm_source(cls, v):
        return normalize_asset_source(v or "uploaded")


class Selection(BaseModel):
    """语义锚点（Hypit 思想，V2 补充文档 §10/§二十）。

    AI 编排 SFX/文字强调/B-roll 时对准 Selection("减少错误") 而不是秒数。
    start/end 为输出时间轴（绝对秒），由词级时间轴解析而来。
    """

    id: str
    text: str
    start: float
    end: float

    @model_validator(mode="after")
    def _check_range(self):
        if self.end <= self.start:
            raise ValueError(f"selection {self.id}: end {self.end} <= start {self.start}")
        return self


class Layer(BaseModel):
    """场景内单层视觉（V2 §9）。start 相对场景起点，duration 为输出时长。

    video: source_in/source_out 为源内秒，duration = (source_out - source_in) / speed
    image: duration 即停留时长，可带 motion（V2 §14，>2s 默认轻微 motion）
    """

    type: LayerType
    asset_id: Optional[str] = None
    start: float = 0.0
    duration: float
    speed: float = 1.0
    source_in: float = 0.0
    source_out: Optional[float] = None
    motion: Optional[str] = None  # slow_zoom_in / slow_zoom_out / pan_left / pan_right
    # text 层预留字段（第一阶段不渲染）
    text: Optional[str] = None
    animation: Optional[str] = None
    # ---- Shotcraft Motion lineage（CMP-002，Optional 向后兼容）----
    # motion_recipe_id：Motion Gallery 应用动效后写入（如 motion.scale-punch）
    # motion_version：版本钉死（registry 升 1.1.0 旧项目仍用 1.0.0）
    # motion_instance：MotionInstance 实例参数 {start_offset, duration, parameters}
    motion_recipe_id: Optional[str] = None
    motion_version: Optional[str] = None
    motion_instance: Optional[dict] = None
    # ---- EDT-006 Timeline：lock 防止误改（§13 Layer.locked）----
    locked: Optional[bool] = None

    @model_validator(mode="after")
    def _check_layer(self):
        if self.duration <= 0:
            raise ValueError(f"layer: duration must be positive, got {self.duration}")
        if self.start < 0:
            raise ValueError(f"layer: start {self.start} 为负")
        if self.type == LayerType.video:
            if self.source_out is None:
                raise ValueError("video layer requires source_out")
            span = self.source_out - self.source_in
            if span <= 0:
                raise ValueError(
                    f"layer: source_in {self.source_in} >= source_out {self.source_out}"
                )
            if self.speed <= 0:
                raise ValueError("layer: speed must be positive")
            implied = span / self.speed
            if abs(implied - self.duration) > 0.05:
                raise ValueError(
                    f"layer: duration {self.duration} 与 (source_out-source_in)/speed="
                    f"{implied:.3f} 不一致（容差 0.05s）"
                )
        return self


class TransitionOut(BaseModel):
    """转场（V2 §25 Registry：默认 cut，效果转场克制使用）。"""

    type: str = config.TRANSITION_DEFAULT
    duration: float = 0.0


class SceneIR(BaseModel):
    """IR 场景：旁白区间 + 视觉 layers + 语义锚点。

    Shot lineage（CMP-001）：origin_recipe_id/version 记录该场景由哪张
    镜头配方实例化（Shot Picker 换镜头时更新），Registry lineage 可追溯。
    """

    id: str
    start: float
    duration: float
    text: str
    layers: List[Layer]
    selections: List[Selection] = Field(default_factory=list)
    transition_out: TransitionOut = Field(default_factory=TransitionOut)
    origin_recipe_id: Optional[str] = None
    origin_recipe_version: Optional[str] = None
    # ---- §6 Canonical：语义角色 + Beat 细分（空 beats=旧格式单视觉层段）----
    semantic_role: Optional[str] = None   # hook/context/problem/evidence/turn/resolution/cta
    beats: List["Beat"] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_scene(self):
        if self.duration <= 0:
            raise ValueError(f"scene {self.id}: duration must be positive")
        if not self.layers:
            raise ValueError(f"scene {self.id}: 无视觉层")
        # layers 必须无缝铺满场景（排序后逐段衔接）
        cursor = 0.0
        for layer in sorted(self.layers, key=lambda x: x.start):
            if abs(layer.start - cursor) > config.ITEM_DURATION_TOLERANCE:
                raise ValueError(
                    f"scene {self.id}: layer@{layer.start:.3f} 与前层末尾 {cursor:.3f} "
                    f"存在 gap/overlap"
                )
            cursor = layer.start + layer.duration
        if abs(cursor - self.duration) > config.ITEM_DURATION_TOLERANCE:
            raise ValueError(
                f"scene {self.id}: layers 总长 {cursor:.3f} 与场景时长 {self.duration} 不符"
            )
        # selections 必须落在场景区间内
        for sel in self.selections:
            if sel.start < self.start - 0.01 or sel.end > self.start + self.duration + 0.01:
                raise ValueError(
                    f"scene {self.id}: selection {sel.id} 超出场景区间"
                )
        return self


class ProjectSpec(BaseModel):
    """V2 §9 project 节。"""

    width: int = config.DEFAULT_RESOLUTION[0]
    height: int = config.DEFAULT_RESOLUTION[1]
    fps: int = config.DEFAULT_FPS
    duration: float


class WatermarkLayer(BaseModel):
    """MPT 吸收方案 §十九：水印是一等公民（IR 字段而非渲染参数）。

    支持文本/图片/Logo；anchor 九宫格 + 偏移；全程或区间显示。
    AI Tool：watermark.add / watermark.update / watermark.remove。
    """

    id: str = "wm-1"
    kind: str = "text"  # text | image | logo | svg
    text: Optional[str] = None        # kind=text 时必填
    asset_id: Optional[str] = None    # kind=image/logo 时必填（项目素材）
    anchor: str = "bottom_right"     # 九宫格 top_left/top_right/bottom_left/bottom_right/center/...
    offset_x: int = 0                # 像素偏移（相对 anchor）
    offset_y: int = 0
    safe_area: int = 32              # §19 safeArea：距边安全距离（px，anchor 内缩）
    opacity: float = 0.75
    scale: float = 1.0                # 字号/图片缩放系数
    start: float = 0.0
    duration: Optional[float] = None  # None = 全程

    @model_validator(mode="after")
    def _check_watermark(self):
        if not 0.0 <= self.opacity <= 1.0:
            raise ValueError(f"watermark: opacity {self.opacity} 越界 [0,1]")
        if self.scale <= 0:
            raise ValueError(f"watermark: scale {self.scale} 非法")
        if self.start < 0:
            raise ValueError(f"watermark: start {self.start} 为负")
        if not (0 <= self.safe_area <= 200):
            raise ValueError(f"watermark: safe_area {self.safe_area} 越界 [0,200]")
        if self.kind == "text" and not (self.text or "").strip():
            raise ValueError("watermark(kind=text) 缺 text")
        if self.kind in ("image", "logo", "svg") and not self.asset_id:
            raise ValueError(f"watermark(kind={self.kind}) 缺 assetId")
        return self


class SubtitleStyle(BaseModel):
    """MPT 吸收方案 §三十一/§三十二：字幕是 Timeline Track 的样式契约，
    不再只是导出设置。字段对齐 MPT（font/size/color/stroke/background/
    position/animation）+ 本系统 Subtitle Registry 的样式白名单。"""

    font: Optional[str] = None        # None = 系统默认 CJK 字体
    size: float = 1.0                 # 相对基准字号的缩放（>1 放大）
    color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: int = 2
    background: Optional[str] = None  # None = 无底色；如 "#000000@0.5"
    background_opacity: float = 0.5   # background 存在时的底色透明度
    position: str = "bottom"          # bottom | center | top
    alignment: str = "center"         # left | center | right（§32 alignment）
    max_width: float = 0.9            # 相对画幅宽（§32 maxWidth）
    animation: str = "none"           # none | pop_spring | fade
    mode: str = "sentence"           # sentence | word（逐词）

    @model_validator(mode="after")
    def _check_style(self):
        if self.size <= 0:
            raise ValueError(f"subtitle_style: size {self.size} 非法")
        if self.position not in ("bottom", "center", "top"):
            raise ValueError(f"subtitle_style: position {self.position!r} 非法")
        if self.alignment not in ("left", "center", "right"):
            raise ValueError(f"subtitle_style: alignment {self.alignment!r} 非法")
        if not (0.3 <= self.max_width <= 1.0):
            raise ValueError(f"subtitle_style: max_width {self.max_width} 越界 [0.3,1.0]")
        if self.animation not in ("none", "pop_spring", "fade"):
            raise ValueError(f"subtitle_style: animation {self.animation!r} 非法")
        if self.mode not in ("sentence", "word"):
            raise ValueError(f"subtitle_style: mode {self.mode!r} 非法")
        if self.stroke_width < 0 or self.stroke_width > 12:
            raise ValueError(f"subtitle_style: stroke_width {self.stroke_width} 越界 [0,12]")
        return self


class AudioTrack(BaseModel):
    """V2 §30/§58：BGM/SFX 是正式时间轴资源。

    MPT 吸收方案 §十八：original 轨绑定素材源（asset_id 指向视频素材，
    渲染时从源视频抽取音轨按音量混入）。source_in/source_out 为源内区间
    （original 轨取其素材在时间线上的对应源区间；其他类型忽略）。
    """

    type: AudioTrackType
    asset_id: Optional[str] = None
    start: float = 0.0
    duration: float
    source_in: float = 0.0
    source_out: Optional[float] = None
    volume: float = 1.0
    fadeIn: float = 0.0        # §18/§35：淡入秒数
    fadeOut: float = 0.0       # §18/§35：淡出秒数
    ducking: bool = False  # BGM 在 narration 下自动压低

    @model_validator(mode="after")
    def _check_track(self):
        if self.duration <= 0:
            raise ValueError(f"audio track({self.type}): duration must be positive")
        if not 0 <= self.volume <= 2.0:
            raise ValueError(f"audio track({self.type}): volume {self.volume} 非法")
        if self.start < 0:
            raise ValueError(f"audio track({self.type}): start {self.start} 为负")
        return self


class SubtitleCue(BaseModel):
    """§33 单条字幕（句级或逐词）。word 逐词时 text 为单词，sentence 为整句。"""

    start: float
    end: float
    text: str


class SubtitleTrack(BaseModel):
    """§31 字幕 = Timeline Track（不再是 Video Export Setting）。

    §33 Word Timing：cues 可为句级（mode=sentence）或逐词（mode=word）。
    source 指明对齐来源（tts_word_boundary / asr / manual），lineage 可溯。
    """

    mode: str = "sentence"              # sentence | word
    source: str = "tts_word_boundary"   # tts_word_boundary | asr | manual
    cues: List[SubtitleCue] = Field(default_factory=list)
    style_id: Optional[str] = None      # Subtitle Registry 样式引用（与 subtitle_style 并存）

    @model_validator(mode="after")
    def _check_cues(self):
        if self.mode not in ("sentence", "word"):
            raise ValueError(f"subtitle track mode {self.mode!r} 非法")
        for c in self.cues:
            if c.end <= c.start:
                raise ValueError(f"subtitle cue ({c.start}-{c.end}) end <= start")
        return self


class SemanticAnchor(BaseModel):
    """§6：Beat 内的语义锚点（Hypit 语义：词/段/时刻/概念/配方）。"""
    id: str
    kind: str = ""   # word | segment | moment | concept | recipe
    label: str = ""


class Beat(BaseModel):
    """§6：Scene 内部最小创作单元（一句话一个视觉动作的最小粒度）。

    空 beats = 旧格式（Scene.layers 就是唯一视觉层段，等价单 Beat）。
    """
    id: str
    role: str = ""                          # reveal | explanation | emphasis | hook...
    semantic_anchors: List[SemanticAnchor] = Field(default_factory=list)
    layers: List["Layer"] = Field(default_factory=list)
    audio: List["AudioTrack"] = Field(default_factory=list)


class Variant(BaseModel):
    """§6：项目的版本化创作（scenes/tracks/style；Project.active_variant_id 指向）。"""
    id: str
    version: int = 1
    scenes: List["SceneIR"] = Field(default_factory=list)
    tracks: List["AudioTrack"] = Field(default_factory=list)
    style: Optional[dict] = None   # StyleContract（style registry 引用 + 参数）
    metadata: dict = Field(default_factory=dict)


class CompositionIR(BaseModel):
    """最终 Composition IR：AI 规划唯一产物，渲染唯一输入。

    Template lineage（CMP-003）：origin_template_id/version 记录该 Composition
    由哪个模板实例化（模板试用/编辑器打开可追溯）；改 IR 不反写 TemplateDefinition。
    """

    version: int = 1
    project: ProjectSpec
    style_id: Optional[str] = None
    scenes: List[SceneIR]
    audio: List[AudioTrack] = Field(default_factory=list)
    # ---- MPT 吸收方案 §十九/§三十一：水印与字幕样式是一等 IR 字段 ----
    watermarks: List[WatermarkLayer] = Field(default_factory=list)
    subtitle_style: Optional[SubtitleStyle] = None
    # ---- §31/§33：字幕是 Timeline Track（句级/逐词 cues + 对齐来源 lineage）----
    subtitle_track: Optional["SubtitleTrack"] = None
    origin_template_id: Optional[str] = None
    origin_template_version: Optional[str] = None
    # ---- §6 Canonical：Variant 版本化（空 variants=旧格式单 Variant，scenes/audio 直接）----
    active_variant_id: Optional[str] = None
    variants: List[Variant] = Field(default_factory=list)
    # ---- §17 Asset Reference Graph：首帧/末帧/参考图/风格/连续性/遮罩引用 ----
    references: Optional[ReferenceGraph] = None

    @model_validator(mode="after")
    def _check_continuity(self):
        if not self.scenes:
            raise ValueError("IR 无场景")
        if abs(self.scenes[0].start) > 0.01:
            raise ValueError(f"IR 首场景起点 {self.scenes[0].start} 必须为 0")
        for i in range(1, len(self.scenes)):
            prev, cur = self.scenes[i - 1], self.scenes[i]
            if abs(prev.start + prev.duration - cur.start) > 0.01:
                raise ValueError(
                    f"场景 {prev.id} 与 {cur.id} gap/overlap："
                    f"{prev.start + prev.duration:.3f} -> {cur.start:.3f}"
                )
        last_end = self.scenes[-1].start + self.scenes[-1].duration
        if abs(last_end - self.project.duration) > 0.01:
            raise ValueError(
                f"IR 总长 {last_end:.3f} 与 project.duration {self.project.duration} 不一致"
            )
        return self

    # ------------------------------------------------------------------
    # 便捷读取
    # ------------------------------------------------------------------
    def canonical_variant(self) -> "Variant":
        """§6：返回当前 active Variant。兼容旧 IR（无 variants 时把 scenes/audio
        包装成默认单 Variant），让新代码稳定拿到 Canonical 结构。"""
        if self.variants and self.active_variant_id:
            for v in self.variants:
                if v.id == self.active_variant_id:
                    return v
        return Variant(id=self.active_variant_id or "v1", version=self.version,
                       scenes=self.scenes, tracks=self.audio)

    def narration_tracks(self) -> List[AudioTrack]:
        return [t for t in self.audio if t.type == AudioTrackType.narration]

    def unrenderable_layers(self) -> List[Layer]:
        """超出第一阶段渲染能力的层（诚实暴露，禁止静默忽略）。"""
        return [l for scene in self.scenes for l in scene.layers
                if l.type not in RENDERABLE_LAYER_TYPES]
