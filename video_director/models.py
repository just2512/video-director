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

from pydantic import BaseModel, Field, model_validator

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
    shape = "shape"
    svg = "svg"
    icon = "icon"
    chart = "chart"
    background = "background"
    logo = "logo"
    lottie = "lottie"
    person = "person"
    # ---- Graphics 域（§11：可执行视觉程序，由 Graphics Runtime 消费）----
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
    LayerType.dom, LayerType.svg, LayerType.canvas, LayerType.html,
    LayerType.chart, LayerType.diagram, LayerType.ui, LayerType.webgl,
    LayerType.webgpu, LayerType.lottie, LayerType.three_scene,
    LayerType.code, LayerType.table, LayerType.map, LayerType.data_viz,
}


class AudioTrackType(str, Enum):
    """V2 §27：声音四分。Narration 主音轨，其余背景/事件/环境。"""

    narration = "narration"
    bgm = "bgm"
    sfx = "sfx"
    ambient = "ambient"


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
    # 许可台账（V2 §38）：外部素材从第一天记录来源
    source: str = "user"
    license_note: Optional[str] = None


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


class AudioTrack(BaseModel):
    """V2 §30/§58：BGM/SFX 是正式时间轴资源。"""

    type: AudioTrackType
    asset_id: Optional[str] = None
    start: float = 0.0
    duration: float
    volume: float = 1.0
    ducking: bool = False  # BGM 在 narration 下自动压低

    @model_validator(mode="after")
    def _check_track(self):
        if self.duration <= 0:
            raise ValueError(f"audio track({self.type}): duration must be positive")
        if not 0 <= self.volume <= 2.0:
            raise ValueError(f"audio track({self.type}): volume {self.volume} 非法")
        return self


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
    origin_template_id: Optional[str] = None
    origin_template_version: Optional[str] = None

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
    def narration_tracks(self) -> List[AudioTrack]:
        return [t for t in self.audio if t.type == AudioTrackType.narration]

    def unrenderable_layers(self) -> List[Layer]:
        """超出第一阶段渲染能力的层（诚实暴露，禁止静默忽略）。"""
        return [l for scene in self.scenes for l in scene.layers
                if l.type not in RENDERABLE_LAYER_TYPES]
