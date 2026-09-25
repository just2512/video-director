"""Composition Compiler（CMP-001/002/003）：Registry 定义 → Composition IR。

三条编译路径（方案 §47 最终 Capability Map）：
    CMP-001  ShotRecipe → Scene + Layers（带 origin_recipe_id/version）
    CMP-002  MotionRecipe → MotionInstance → Layer.motion_recipe_id
    CMP-003  TemplateDefinition → Composition（带 origin_template_id/version）

原则（全局契约 §6/§25）：
- 编译产物是 IR 实例，Registry 定义只读，互不反写
- 版本钉死：实例记录用哪个 version，Registry 升级不漂移旧项目
- 编译失败显式抛错（诚实暴露），不静默降级成"好像成功"
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from video_director import config
from video_director.models import (
    AudioTrack,
    AudioTrackType,
    CompositionIR,
    Layer,
    LayerType,
    ProjectSpec,
    SceneIR,
    TransitionOut,
)
from video_director.registries.core import RegistryError
from video_director.registries.recipes import (
    MotionRegistry,
    ShotRegistry,
    ShotRecipe,
)
from video_director.registries.templates import TemplateDefinition, TemplateRegistry


class CompileError(Exception):
    """编译失败（RECIPE_NOT_FOUND/MISSING_REQUIRED_SLOT/...）。

    code 精确分类，供 API 层返回结构化错误（全局契约 §33 Error Contract）。
    """

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


# ---------------------------------------------------------------------------
# CMP-002 Motion → Layer（先实现：CMP-001 的 layer_plan 引用它）
# ---------------------------------------------------------------------------


def apply_motion_to_layer(
    layer: Layer,
    motion_recipe_id: str,
    motion_version: Optional[str] = None,
    start_offset: float = 0.0,
    duration: Optional[float] = None,
    parameters: Optional[dict] = None,
) -> Layer:
    """把 MotionRecipe 实例化到 Layer（返回新 Layer，不可变风格）。

    失败模式（Task Card CMP-002 Failure）：
        MOTION_NOT_FOUND / LAYER_INCOMPATIBLE / INVALID_PARAMETER /
        DURATION_OUT_OF_RANGE
    """
    registry = MotionRegistry()
    try:
        recipe = registry.get(motion_recipe_id, motion_version)
    except RegistryError as exc:
        raise CompileError("MOTION_NOT_FOUND", str(exc)) from exc

    # 图层兼容性（IG-MOTION Compatibility：编译层再校验一次，双保险）
    layer_type_name = layer.type.value if hasattr(layer.type, "value") else str(layer.type)
    if layer_type_name not in recipe.compatible_layers:
        raise CompileError(
            "LAYER_INCOMPATIBLE",
            f"{recipe.id} 不兼容图层 {layer_type_name}（支持: {list(recipe.compatible_layers)}）",
        )

    # 参数校验（白名单 + min/max）
    params = dict(parameters or {})
    for spec in recipe.parameters:
        name = spec.get("name")
        if name in params:
            try:
                val = float(params[name])
            except (TypeError, ValueError) as exc:
                raise CompileError(
                    "INVALID_PARAMETER", f"{recipe.id} 参数 {name}={params[name]!r} 非数值"
                ) from exc
            if "min" in spec and val < spec["min"]:
                raise CompileError(
                    "INVALID_PARAMETER", f"{recipe.id} 参数 {name}={val} 低于下限 {spec['min']}"
                )
            if "max" in spec and val > spec["max"]:
                raise CompileError(
                    "INVALID_PARAMETER", f"{recipe.id} 参数 {name}={val} 超上限 {spec['max']}"
                )
            params[name] = val

    inst_duration = recipe.duration_recommended if duration is None else float(duration)
    if not (recipe.duration_min <= inst_duration <= recipe.duration_max):
        raise CompileError(
            "DURATION_OUT_OF_RANGE",
            f"{recipe.id} 实例时长 {inst_duration}s 超出 {recipe.duration_min}-"
            f"{recipe.duration_max}s",
        )

    # 不可变更新：构造新 Layer（pydantic model_copy 保持其余字段）
    instance = {
        "recipe_id": recipe.id,
        "recipe_version": recipe.version,
        "start_offset": float(start_offset),
        "duration": inst_duration,
        "parameters": params,
    }
    return layer.model_copy(update={
        "motion_recipe_id": recipe.id,
        "motion_version": recipe.version,
        "motion_instance": instance,
    })


# ---------------------------------------------------------------------------
# CMP-001 ShotRecipe → Scene
# ---------------------------------------------------------------------------


def instantiate_shot(
    recipe_id: str,
    recipe_version: Optional[str],
    scene_id: str,
    scene_text: str,
    scene_start: float,
    scene_duration: float,
    slot_bindings: Dict[str, str],
    assets: Dict[str, "AssetRefLike"],
    width: Optional[int] = None,
    height: Optional[int] = None,
    fps: Optional[int] = None,
) -> SceneIR:
    """ShotRecipe → SceneIR（Task Card CMP-001）。

    slot_bindings: {slot_id: asset_id}；必需槽位缺失 → MISSING_REQUIRED_SLOT。
    layer_plan 里的 motion 引用按 CMP-002 实例化到对应 Layer。
    """
    registry = ShotRegistry()
    try:
        recipe: ShotRecipe = registry.get(recipe_id, recipe_version)
    except RegistryError as exc:
        raise CompileError("RECIPE_NOT_FOUND", str(exc)) from exc

    # 必需槽位检查
    for slot in recipe.slots:
        if slot.required and slot.id not in slot_bindings:
            raise CompileError(
                "MISSING_REQUIRED_SLOT", f"{recipe.id} 必需槽位 {slot.id}({slot.type}) 未绑定"
            )
    # 素材型槽位绑定的素材必须存在（text 槽位传文本值，跳过存在性检查）
    for slot_id, asset_id in slot_bindings.items():
        slot_type = next((s.type for s in recipe.slots if s.id == slot_id), None)
        if slot_type not in ("text",) and asset_id not in assets:
            raise CompileError("INVALID_ASSET", f"槽位 {slot_id} 绑定的素材 {asset_id} 不存在")

    # layer_plan → 可渲染 Layers。
    # IR 契约（models.SceneIR 校验）：layers 是时间轴顺序层，必须无缝铺满场景；
    # 第一阶段仅 video/image 可渲染（text/shape/bg 进 IR 会被 validator REJECT）。
    # 策略：只编译「有素材绑定的 video/image 层」，多个素材层均分场景时长；
    # 纯文本/背景计划项诚实跳过（渲染能力就绪前不伪造）。
    visual_plans = [
        p for p in recipe.layer_plan
        if p.layer_type in ("video", "image") and p.source_slot and slot_bindings.get(p.source_slot)
    ]
    if not visual_plans:
        raise CompileError(
            "EMPTY_SCENE",
            f"{recipe.id} 无可编译的视觉层（必需槽位未绑定素材或层类型不可渲染）",
        )

    layers: List[Layer] = []
    n = len(visual_plans)
    # 均分时长：层依次铺满（无缝衔接，符合 SceneIR 校验）
    seg = scene_duration / n
    for i, plan in enumerate(visual_plans):
        asset_id = slot_bindings[plan.source_slot]
        asset = assets[asset_id]
        start = i * seg
        if plan.layer_type == "video":
            span = min(asset.duration or seg, seg)
            layer = Layer(
                type=LayerType.video, asset_id=asset_id,
                start=start, duration=seg,
                source_in=0.0, source_out=span,
                speed=span / seg if span > 0 else 1.0,
            )
        else:  # image
            layer = Layer(
                type=LayerType.image, asset_id=asset_id,
                start=start, duration=seg,
            )
        # CMP-002：layer_plan.motion 引用 → MotionInstance
        if plan.motion:
            try:
                layer = apply_motion_to_layer(layer, plan.motion)
            except CompileError as exc:
                # 编译失败显式传播（不静默丢弃动效）
                raise CompileError(
                    "MOTION_COMPILE_FAILED", f"{recipe.id}/{plan.id}: {exc}"
                ) from exc
        layers.append(layer)

    return SceneIR(
        id=scene_id,
        start=scene_start,
        duration=scene_duration,
        text=scene_text,
        layers=layers,
        origin_recipe_id=recipe.id,
        origin_recipe_version=recipe.version,
        transition_out=TransitionOut(type="crossfade") if recipe.category != "transition" else TransitionOut(type="cut"),
    )


# ---------------------------------------------------------------------------
# CMP-003 Template → Composition
# ---------------------------------------------------------------------------


def instantiate_template(
    template_id: str,
    template_version: Optional[str],
    ratio: str,
    slot_values: Dict[str, str],
    assets: Dict[str, "AssetRefLike"],
    project_id: str,
    text_assets: Optional[Dict[str, str]] = None,
) -> CompositionIR:
    """TemplateDefinition → Composition（Task Card CMP-003）。

    - ratio → 分辨率（与 studio.PLATFORMS 同源映射）
    - scene.duration 按模板定义（模板是"语法"，场景时长来自模板）
    - slot_values: {slot_id: asset_id 或文本值}
    - origin_template_id/version 写入 IR（lineage，模板升级不漂移）

    实例化后 Composition 独立存在：改 IR 不反写模板定义。
    """
    registry = TemplateRegistry()
    try:
        tpl: TemplateDefinition = registry.get(template_id, template_version)
    except RegistryError as exc:
        raise CompileError("TEMPLATE_NOT_FOUND", str(exc)) from exc

    if ratio not in tpl.supported_ratios:
        raise CompileError(
            "RATIO_NOT_SUPPORTED", f"{tpl.id} 不支持画幅 {ratio}（支持: {list(tpl.supported_ratios)}）"
        )

    # ratio → 分辨率
    from video_director import studio
    ratio_wh = {
        "16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080),
        "4:5": (1080, 1350), "3:4": (1080, 1440),
    }
    width, height = ratio_wh[ratio]

    # 必需槽位检查
    for slot in tpl.slots:
        if slot.required and slot.id not in slot_values:
            raise CompileError(
                "MISSING_REQUIRED_SLOT",
                f"{tpl.id} 必需槽位 {slot.label}({slot.id}) 未填",
            )

    scenes: List[SceneIR] = []
    cursor = 0.0
    for tsc in tpl.scenes:
        scene_id = f"{project_id[:12]}-{tsc.id}"
        # 槽位绑定：素材型槽位（值在 assets）绑 asset_id；
        # 文本型槽位（值是文本）满足 text 类必需槽位（instantiate_shot 的
        # required 检查接受：slot_values 提供了值即视为满足，不要求素材存在）
        slot_bindings: Dict[str, str] = {}
        scene_text_parts: List[str] = []
        for lay in tsc.layers:
            if not (lay.slot_id and lay.slot_id in slot_values):
                continue
            val = slot_values[lay.slot_id]
            if val in assets:
                # 素材槽位
                slot_bindings[lay.slot_id] = val
            else:
                # 文本槽位：值是文本；作为 text 槽位的绑定值传入
                slot_bindings[lay.slot_id] = val
                scene_text_parts.append(val)
        scene_text = "。".join(scene_text_parts) or (tsc.semantic_role or "")
        scene_duration = tsc.duration
        if tsc.shot_recipe_id:
            # recipe 槽位与模板槽位名不同源：按类型语义绑定。
            # recipe 必需槽位按 slot.type 从模板槽值池取：
            #   text 槽位 ← 第一个非素材（文本）值
            #   image/video/product/person/screen 槽位 ← 第一个素材值
            shot_recipe = ShotRegistry().get(tsc.shot_recipe_id)
            text_pool = [
                slot_values[lay.slot_id] for lay in tsc.layers
                if lay.slot_id and lay.slot_id in slot_values
                and slot_values[lay.slot_id] not in assets
            ]
            # 素材池：本场景素材槽优先；纯文本场景兜底用全模板素材
            # （标题场景以 hero_image 作图底，符合"图底标题卡"语义）
            scene_asset_pool = [
                slot_values[lay.slot_id] for lay in tsc.layers
                if lay.slot_id and lay.slot_id in slot_values
                and slot_values[lay.slot_id] in assets
            ]
            global_asset_pool = [
                v for v in slot_values.values() if v in assets
            ]
            asset_pool = scene_asset_pool or global_asset_pool
            recipe_bindings: Dict[str, str] = {}
            for rs in shot_recipe.slots:
                if rs.type == "text":
                    if text_pool:
                        recipe_bindings[rs.id] = text_pool[0]
                elif asset_pool:
                    recipe_bindings[rs.id] = asset_pool[0]
            scene = instantiate_shot(
                recipe_id=tsc.shot_recipe_id,
                recipe_version=None,
                scene_id=scene_id,
                scene_text=scene_text,
                scene_start=cursor,
                scene_duration=scene_duration,
                slot_bindings=recipe_bindings,
                assets=assets,
            )
        else:
            # 无 recipe 绑定的场景：用第一个可用素材做单层铺满
            first_asset = next(iter(slot_bindings.values()), None)
            if first_asset is None:
                raise CompileError(
                    "EMPTY_SCENE",
                    f"模板场景 {tsc.id} 无 shot_recipe 也无可用素材槽位",
                )
            fa = assets[first_asset]
            if fa.type == "video":
                span = min(fa.duration or scene_duration, scene_duration)
                base = Layer(
                    type=LayerType.video, asset_id=first_asset,
                    start=0.0, duration=scene_duration,
                    source_in=0.0, source_out=span,
                    speed=span / scene_duration if span > 0 else 1.0,
                )
            else:
                base = Layer(
                    type=LayerType.image, asset_id=first_asset,
                    start=0.0, duration=scene_duration,
                )
            scene = SceneIR(
                id=scene_id, start=cursor, duration=scene_duration,
                text=scene_text, layers=[base],
            )
        # 模板层的 motion_recipe_id 应用（覆盖 shot recipe 默认）
        for tlay in tsc.layers:
            if tlay.motion_recipe_id:
                for i, ilayer in enumerate(scene.layers):
                    try:
                        scene.layers[i] = apply_motion_to_layer(
                            ilayer, tlay.motion_recipe_id
                        )
                        break
                    except CompileError as exc:
                        if exc.code != "LAYER_INCOMPATIBLE":
                            raise
        scenes.append(scene)
        cursor += scene_duration

    total = cursor
    return CompositionIR(
        project=ProjectSpec(width=width, height=height,
                             fps=config.DEFAULT_FPS, duration=total),
        style_id=tpl.style_contract.get("style_id"),
        scenes=scenes,
        origin_template_id=tpl.id,
        origin_template_version=tpl.version,
    )


# ---------------------------------------------------------------------------
# 类型别名（避免循环 import：assets.AssetRef 只需鸭子类型）
# ---------------------------------------------------------------------------
from typing import Protocol


class AssetRefLike(Protocol):
    duration: Optional[float]
    type: str
