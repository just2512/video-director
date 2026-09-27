"""Shot Recipe Registry（REG-003）+ Motion Registry（REG-004）加载层。

ShotRecipe/MotionRecipe 在 core.RegistryItem 基础上叠加领域字段：
    ShotRecipe: category/semantic_intents/compatible_content_types/duration/
                energy/slots/layer_plan/motion_bindings/audio_cues/constraints
    MotionRecipe: motion_type/semantic_intents/compatible_layers/duration/
                  channels(keyframes+easing)/parameters/constraints

Registry 只存知识定义（全局契约 §9-10）：无 ffmpeg 命令、无 Remotion 组件、
无绝对路径 —— core.validate_item 已显式拒收这些字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from video_director.registries.core import (
    RegistryItem,
    RegistryLoader,
    RegistryError,
    LayeredRegistryLoader,
)

# ---------------------------------------------------------------------------
# Shot Recipe（Task Card REG-003 Contract）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShotSlot:
    id: str
    type: str
    required: bool
    cardinality: str  # one | many
    semantic_role: str


@dataclass(frozen=True)
class LayerPlanItem:
    id: str
    layer_type: str
    z_index: int
    visible: bool = True
    source_slot: Optional[str] = None
    motion: Optional[str] = None


@dataclass(frozen=True)
class MotionBinding:
    semantic_intent: str
    preferred_styles: Tuple[str, ...]


@dataclass(frozen=True)
class ShotRecipe:
    """完整镜头配方（core 契约 + 领域字段）。"""

    item: RegistryItem
    category: str
    semantic_intents: Tuple[str, ...]
    compatible_content_types: Tuple[str, ...]
    duration_min: float
    duration_recommended: float
    duration_max: float
    energy: str
    slots: Tuple[ShotSlot, ...]
    layer_plan: Tuple[LayerPlanItem, ...]
    motion_bindings: Tuple[MotionBinding, ...]
    audio_cues: Tuple[dict, ...]
    constraints: Tuple[str, ...]

    @property
    def id(self) -> str:
        return self.item.id

    @property
    def version(self) -> str:
        return self.item.version

    @property
    def name(self) -> str:
        return self.item.name

    @property
    def commercial_status(self) -> str:
        return self.item.commercial_status

    @property
    def provenance_ref(self) -> Optional[str]:
        return self.item.provenance_ref


#: 层类型枚举（全局契约 §8；与 models.RENDERABLE 对齐并扩展）
LAYER_TYPES = (
    "video", "image", "person", "text", "subtitle", "title",
    "lower_third", "graphic", "shape", "svg", "icon", "chart",
    "background", "logo", "lottie", "pip", "card", "product", "screen",
)

#: 卡片允许的槽位类型
SLOT_TYPES = ("video", "image", "product", "person", "screen", "text",
              "graphic", "logo")


def shot_from_item(raw: dict, item: RegistryItem) -> ShotRecipe:
    """从原始 dict 构造 ShotRecipe（schema 深校验，非法即拒）。"""
    for key in ("category", "semantic_intents", "compatible_content_types",
                "duration", "energy", "slots", "layer_plan", "motion_bindings"):
        if key not in raw:
            raise RegistryError("INVALID_SCHEMA", f"{item.id}: shot 缺字段 {key}")
    dur = raw["duration"]
    if not all(k in dur for k in ("min", "recommended", "max")):
        raise RegistryError("INVALID_SCHEMA", f"{item.id}: duration 需 min/recommended/max")
    if not (dur["min"] <= dur["recommended"] <= dur["max"]):
        raise RegistryError("INVALID_SCHEMA", f"{item.id}: duration 区间非法")
    if raw["energy"] not in ("low", "medium", "high"):
        raise RegistryError("INVALID_SCHEMA", f"{item.id}: energy {raw['energy']!r} 非法")
    slots = []
    for s in raw["slots"]:
        if s.get("type") not in SLOT_TYPES:
            raise RegistryError(
                "INVALID_SCHEMA", f"{item.id}: slot 类型 {s.get('type')!r} 非法"
            )
        if s.get("cardinality") not in ("one", "many"):
            raise RegistryError(
                "INVALID_SCHEMA", f"{item.id}: slot cardinality 非法"
            )
        slots.append(ShotSlot(
            id=s["id"], type=s["type"], required=bool(s.get("required", False)),
            cardinality=s["cardinality"], semantic_role=s.get("semantic_role", "hero"),
        ))
    plan = []
    for lp in raw["layer_plan"]:
        if lp.get("layer_type") not in LAYER_TYPES:
            raise RegistryError(
                "INVALID_SCHEMA", f"{item.id}: layer_type {lp.get('layer_type')!r} 非法"
            )
        plan.append(LayerPlanItem(
            id=lp["id"], layer_type=lp["layer_type"], z_index=int(lp.get("z_index", 0)),
            visible=bool(lp.get("visible", True)), source_slot=lp.get("source_slot"),
            motion=lp.get("motion"),
        ))
    bindings = tuple(MotionBinding(
        semantic_intent=b["semantic_intent"],
        preferred_styles=tuple(b.get("preferred_styles") or ()),
    ) for b in raw["motion_bindings"])
    return ShotRecipe(
        item=item,
        category=raw["category"],
        semantic_intents=tuple(raw["semantic_intents"]),
        compatible_content_types=tuple(raw["compatible_content_types"]),
        duration_min=float(dur["min"]), duration_recommended=float(dur["recommended"]),
        duration_max=float(dur["max"]),
        energy=raw["energy"],
        slots=tuple(slots),
        layer_plan=tuple(plan),
        motion_bindings=bindings,
        audio_cues=tuple(raw.get("audio_cues") or ()),
        constraints=tuple(raw.get("constraints") or ()),
    )


class ShotRegistry:
    """镜头配方加载与查询（REG-003 API）。§39 三层加载（system/user/project）。"""

    def __init__(self):
        self._loader = LayeredRegistryLoader("shot_recipe", "shots")

    def all(self) -> List[ShotRecipe]:
        out = []
        for item in self._loader.load_all():
            raw = self._find_raw(item.id, item.version)
            out.append(shot_from_item(raw, item))
        return out

    def layer_of(self, item_id: str, version: str) -> str:
        return self._loader.layer_of(item_id, version)

    def _find_raw(self, item_id: str, version: str) -> dict:
        return self._loader.raw_of(item_id, version)

    def get(self, item_id: str, version: Optional[str] = None) -> ShotRecipe:
        item = self._loader.load(item_id, version)
        return shot_from_item(self._find_raw(item.id, item.version), item)

    def versions_of(self, item_id: str) -> List[str]:
        return self._loader.versions_of(item_id)


# ---------------------------------------------------------------------------
# Motion Recipe（Task Card REG-004 Contract）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MotionKeyframe:
    at: float  # 0.0-1.0 归一化进度
    value: object  # number 或 str（crop 百分比等）
    easing: Optional[dict] = None


@dataclass(frozen=True)
class MotionChannel:
    property: str  # opacity/x/y/scale/rotation/blur/crop
    keyframes: Tuple[MotionKeyframe, ...]
    interpolation: str


@dataclass(frozen=True)
class MotionRecipe:
    item: RegistryItem
    motion_type: str
    semantic_intents: Tuple[str, ...]
    compatible_layers: Tuple[str, ...]
    duration_min: float
    duration_recommended: float
    duration_max: float
    channels: Tuple[MotionChannel, ...]
    parameters: Tuple[dict, ...]
    constraints: Tuple[str, ...]

    @property
    def id(self) -> str:
        return self.item.id

    @property
    def version(self) -> str:
        return self.item.version

    @property
    def name(self) -> str:
        return self.item.name

    @property
    def commercial_status(self) -> str:
        return self.item.commercial_status

    @property
    def provenance_ref(self) -> Optional[str]:
        return self.item.provenance_ref


#: Motion channel 属性白名单（全局契约 §9：property/keyframe/easing，禁 shell）
MOTION_PROPERTIES = ("opacity", "x", "y", "scale", "scaleX", "scaleY",
                     "rotation", "blur", "crop", "mask", "clipPath")

#: easing type 白名单（方案 §7 EasingDefinition）
EASING_TYPES = ("linear", "quad", "cubic", "quart", "quint", "sine",
                "expo", "circ", "back", "elastic")

#: 本渲染器实际可编译的属性（resolver 层据此判 INCOMPATIBLE，选了不炸渲染）
COMPILABLE_PROPERTIES = ("opacity", "x", "y", "scale")


def motion_from_item(raw: dict, item: RegistryItem) -> MotionRecipe:
    for key in ("motion_type", "semantic_intents", "compatible_layers",
                "duration", "channels"):
        if key not in raw:
            raise RegistryError("INVALID_SCHEMA", f"{item.id}: motion 缺字段 {key}")
    dur = raw["duration"]
    if not (dur["min"] <= dur["recommended"] <= dur["max"]):
        raise RegistryError("INVALID_SCHEMA", f"{item.id}: duration 区间非法")
    channels = []
    for ch in raw["channels"]:
        if ch.get("property") not in MOTION_PROPERTIES:
            raise RegistryError(
                "INVALID_SCHEMA", f"{item.id}: motion 属性 {ch.get('property')!r} 不在白名单"
            )
        kfs = tuple(MotionKeyframe(
            at=float(k["at"]), value=k["value"],
            easing=k.get("easing"),
        ) for k in ch.get("keyframes") or [])
        if len(kfs) < 2:
            raise RegistryError(
                "INVALID_SCHEMA", f"{item.id}: channel {ch['property']} 至少 2 个关键帧"
            )
        for k in kfs:
            if not (0.0 <= k.at <= 1.0):
                raise RegistryError(
                    "INVALID_SCHEMA", f"{item.id}: keyframe at={k.at} 越界 [0,1]"
                )
            if k.easing and k.easing.get("type") not in EASING_TYPES:
                raise RegistryError(
                    "INVALID_SCHEMA", f"{item.id}: easing {k.easing} 不在白名单"
                )
        channels.append(MotionChannel(
            property=ch["property"], keyframes=kfs,
            interpolation=ch.get("interpolation", "linear"),
        ))
    return MotionRecipe(
        item=item,
        motion_type=raw["motion_type"],
        semantic_intents=tuple(raw["semantic_intents"]),
        compatible_layers=tuple(raw["compatible_layers"]),
        duration_min=float(dur["min"]), duration_recommended=float(dur["recommended"]),
        duration_max=float(dur["max"]),
        channels=tuple(channels),
        parameters=tuple(raw.get("parameters") or ()),
        constraints=tuple(raw.get("constraints") or ()),
    )


class MotionRegistry:
    """动效配方加载与查询（REG-004 API）。§39 三层加载（system/user/project）。"""

    def __init__(self):
        self._loader = LayeredRegistryLoader("motion_recipe", "motions")

    def all(self) -> List[MotionRecipe]:
        out = []
        for item in self._loader.load_all():
            raw = self._find_raw(item.id, item.version)
            out.append(motion_from_item(raw, item))
        return out

    def layer_of(self, item_id: str, version: str) -> str:
        return self._loader.layer_of(item_id, version)

    def _find_raw(self, item_id: str, version: str) -> dict:
        return self._loader.raw_of(item_id, version)

    def get(self, item_id: str, version: Optional[str] = None) -> MotionRecipe:
        item = self._loader.load(item_id, version)
        return motion_from_item(self._find_raw(item.id, item.version), item)

    def versions_of(self, item_id: str) -> List[str]:
        return self._loader.versions_of(item_id)
