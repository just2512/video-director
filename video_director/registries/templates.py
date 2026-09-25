"""Template Registry（REG-005）+ 实例化（CMP-003 前置）。

Template = 可实例化的 Composition Grammar（全局契约 §11）：
    Style + Scene Grammar + Layer Grammar + Recipe bindings + Editable Slots
Template ≠ MP4 —— 试用→instantiate→Composition 独立存在；
改 Composition 不反写 TemplateDefinition；Template 升级不漂移旧项目
（项目钉死 originTemplateId/Version）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from video_director.registries.core import (
    RegistryItem,
    RegistryLoader,
    RegistryError,
)

#: 画幅白名单（与 studio.PLATFORMS 对齐）
SUPPORTED_RATIOS = ("16:9", "9:16", "1:1", "4:5", "3:4")


@dataclass(frozen=True)
class TemplateSlot:
    id: str
    label: str
    type: str  # text/image/video/product/person/logo/audio/color
    required: bool
    editable: bool


@dataclass(frozen=True)
class TemplateLayer:
    id: str
    type: str
    slot_id: Optional[str] = None
    motion_recipe_id: Optional[str] = None


@dataclass(frozen=True)
class TemplateScene:
    id: str
    duration: float
    semantic_role: str  # hook/intro/body/feature/proof/transition/outro
    shot_recipe_id: Optional[str] = None
    layers: Tuple[TemplateLayer, ...] = ()
    transition: Optional[str] = None


@dataclass(frozen=True)
class TemplateDefinition:
    item: RegistryItem
    category: str
    supported_ratios: Tuple[str, ...]
    duration_min: Optional[float]
    duration_recommended: Optional[float]
    duration_max: Optional[float]
    style_contract: dict
    slots: Tuple[TemplateSlot, ...]
    scenes: Tuple[TemplateScene, ...]
    required_capabilities: Tuple[str, ...]
    recipe_bindings: Tuple[dict, ...]
    runtime: dict

    @property
    def id(self) -> str:
        return self.item.id

    @property
    def version(self) -> str:
        return self.item.version

    @property
    def name(self) -> str:
        return self.item.name


#: 模板槽位类型白名单
TEMPLATE_SLOT_TYPES = ("text", "image", "video", "product", "person",
                      "logo", "audio", "color")

#: 场景语义角色白名单（方案 §11 TemplateScene.semanticRole）
SCENE_ROLES = ("hook", "intro", "body", "feature", "proof", "transition", "outro")


def template_from_item(raw: dict, item: RegistryItem) -> TemplateDefinition:
    for key in ("category", "supported_ratios", "slots", "scenes"):
        if key not in raw:
            raise RegistryError("INVALID_SCHEMA", f"{item.id}: template 缺字段 {key}")
    for r in raw["supported_ratios"]:
        if r not in SUPPORTED_RATIOS:
            raise RegistryError("INVALID_SCHEMA", f"{item.id}: ratio {r!r} 不支持")
    slots = []
    for s in raw["slots"]:
        if s.get("type") not in TEMPLATE_SLOT_TYPES:
            raise RegistryError(
                "INVALID_SCHEMA", f"{item.id}: slot type {s.get('type')!r} 非法"
            )
        slots.append(TemplateSlot(
            id=s["id"], label=s.get("label", s["id"]), type=s["type"],
            required=bool(s.get("required", False)), editable=bool(s.get("editable", True)),
        ))
    scenes = []
    for sc in raw["scenes"]:
        if sc.get("semantic_role") not in SCENE_ROLES:
            raise RegistryError(
                "INVALID_SCHEMA", f"{item.id}: scene role {sc.get('semantic_role')!r} 非法"
            )
        layers = tuple(TemplateLayer(
            id=l["id"], type=l["type"], slot_id=l.get("slot_id"),
            motion_recipe_id=l.get("motion_recipe_id"),
        ) for l in sc.get("layers") or ())
        scenes.append(TemplateScene(
            id=sc["id"], duration=float(sc["duration"]),
            semantic_role=sc["semantic_role"],
            shot_recipe_id=sc.get("shot_recipe_id"),
            layers=layers, transition=sc.get("transition"),
        ))
    dur = raw.get("duration") or {}
    return TemplateDefinition(
        item=item,
        category=raw["category"],
        supported_ratios=tuple(raw["supported_ratios"]),
        duration_min=dur.get("min"), duration_recommended=dur.get("recommended"),
        duration_max=dur.get("max"),
        style_contract=raw.get("style_contract") or {},
        slots=tuple(slots),
        scenes=tuple(scenes),
        required_capabilities=tuple(raw.get("required_capabilities") or ()),
        recipe_bindings=tuple(raw.get("recipe_bindings") or ()),
        runtime=raw.get("runtime") or {"renderer": "ffmpeg", "deterministic": True},
    )


class TemplateRegistry:
    """模板注册表加载与查询（REG-005 API）。"""

    def __init__(self):
        self._loader = RegistryLoader("template", "templates")

    def all(self) -> List[TemplateDefinition]:
        import json
        out = []
        for item in self._loader.load_all():
            raw = self._find_raw(item.id, item.version)
            out.append(template_from_item(raw, item))
        return out

    def _find_raw(self, item_id: str, version: str) -> dict:
        import json
        for path in sorted(self._loader._root.rglob("*.json")):
            for data in json.loads(path.read_text(encoding="utf-8")):
                if data.get("id") == item_id and str(data.get("version")) == version:
                    return data
        raise RegistryError("NOT_FOUND", f"{item_id}@{version} 原始定义缺失")

    def get(self, item_id: str, version: Optional[str] = None) -> TemplateDefinition:
        item = self._loader.load(item_id, version)
        return template_from_item(self._find_raw(item.id, item.version), item)

    def versions_of(self, item_id: str) -> List[str]:
        return self._loader.versions_of(item_id)
