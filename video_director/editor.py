"""Editor Command 层（REV-001/REV-002 + EDT 编辑契约）。

全局契约 §26-28：
    UI → Editor Command → Server Validation → Revision → Composition Update
    禁止 UI 直接 setComposition。expectedVersion 不匹配 → CONFLICT 拒绝（禁静默覆盖）。

失效局部性（§20/§21/REV-002）：
    修改只让实际依赖失效。改 Scene 03 的 motion → 只有 Scene 03 相关
    render/preview stale；旁白/其他场景/其他素材不 stale。

Revision 持久化：storage/composer/<pid>/plans/revisions.jsonl 逐条追加，
不覆盖历史（§21 Revision 不得覆盖历史版本）。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

from video_director import config
from video_director.models import (
    AudioTrack,
    AudioTrackType,
    CompositionIR,
    Layer,
    LayerType,
    SceneIR,
    SubtitleStyle,
    WatermarkLayer,
)
from video_director.registries.compiler import (
    CompileError,
    apply_motion_to_layer,
    instantiate_shot,
)
from video_director.visual_semantics import (
    KEYFRAME_PROPERTIES,
    CameraKeyframe,
    CameraTrack,
    ContinuityLink,
    ContinuityType,
    Keyframe,
    KeyframeTrack,
    Mask,
    Transform,
)


class EditorError(Exception):
    """编辑命令失败（全局契约 §33 Error Contract）。"""

    def __init__(self, code: str, message: str, recoverable: bool = True):
        self.code = code
        self.recoverable = recoverable
        super().__init__(f"[{code}] {message}")


# ---------------------------------------------------------------------------
# Revision 记录（§21）
# ---------------------------------------------------------------------------


def _revisions_path(project_id: str) -> Path:
    return config.project_dir(project_id) / "plans" / "revisions.jsonl"


def load_revisions(project_id: str) -> List[dict]:
    """读全部 revision 历史（前端 Versions/Compare 消费）。"""
    p = _revisions_path(project_id)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning(f"revisions.jsonl 损坏行跳过: {line[:60]}")
    return out


def load_composition(project_id: str) -> CompositionIR:
    """读项目 composition.json（Editor 的 server state 事实源）。"""
    p = config.project_dir(project_id) / "plans" / "composition.json"
    if not p.is_file():
        raise EditorError("NOT_FOUND", f"项目 {project_id} 无 composition.json", recoverable=False)
    try:
        return CompositionIR(**json.loads(p.read_text(encoding="utf-8")))
    except Exception as exc:
        raise EditorError("PERSISTENCE_ERROR", f"composition.json 解析失败: {exc}", recoverable=False) from exc


def save_composition(project_id: str, ir: CompositionIR) -> None:
    p = config.project_dir(project_id) / "plans" / "composition.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(json.loads(ir.model_dump_json()), ensure_ascii=False, indent=2), encoding="utf-8")


def composition_version(project_id: str) -> int:
    """当前 composition 版本 = revision 数 + 1（每次应用命令 +1）。"""
    return len(load_revisions(project_id)) + 1


def _append_revision(project_id: str, revision: dict) -> None:
    p = _revisions_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(revision, ensure_ascii=False) + "\n")




def _snapshot_for_revision(ir: "CompositionIR") -> dict:
    """§46：revision 内嵌 IR 快照（undo/redo 恢复点）。"""
    try:
        return json.loads(ir.model_dump_json())
    except Exception:  # noqa: BLE001 快照失败不阻断主命令
        return {}


def _undo_redo(project_id: str, action: str) -> CommandResult:
    """§46 Undo/Redo：快照恢复式（revision 带 ir_before/ir_after 后可回退）。

    undo：最后一个未撤销 revision → 恢复其 ir_before；
    redo：最近被撤销 revision → 恢复其 ir_after。
    旧 revision 无快照 → 诚实 UNDO_UNAVAILABLE（不猜不 fake）。
    """
    import copy as _copy
    revisions = load_revisions(project_id)
    undo_path = config.project_dir(project_id) / "plans" / "undo_stack.json"
    try:
        undone_ids = json.loads(undo_path.read_text(encoding="utf-8")) if undo_path.is_file() else []
        if not isinstance(undone_ids, list):
            undone_ids = []
    except (OSError, json.JSONDecodeError):
        undone_ids = []

    if action == "undo":
        candidates = [r for r in revisions if r.get("revision_id") not in undone_ids]
        if not candidates:
            return CommandResult(status="REJECTED", errors=[{
                "code": "NOTHING_TO_UNDO", "message": "没有可撤销的编辑",
                "recoverable": True}])
        target = candidates[-1]
        snap = target.get("ir_before")
        if not snap:
            return CommandResult(status="REJECTED", errors=[{
                "code": "UNDO_UNAVAILABLE",
                "message": f"revision {target.get('revision_id')} 无 IR 快照"
                           f"（历史记录早于 Undo 能力上线），无法撤销",
                "recoverable": True}])
        try:
            ir = CompositionIR(**_copy.deepcopy(snap))
        except Exception as exc:
            return CommandResult(status="REJECTED", errors=[{
                "code": "SNAPSHOT_INVALID", "message": f"快照恢复失败: {exc}",
                "recoverable": True}])
        save_composition(project_id, ir)
        undone_ids.append(target.get("revision_id"))
        undo_path.parent.mkdir(parents=True, exist_ok=True)
        undo_path.write_text(json.dumps(undone_ids[-200:], ensure_ascii=False), encoding="utf-8")
        return CommandResult(
            status="APPLIED", revision_id=f"undo-{target.get('revision_id')}",
            composition_version=composition_version(project_id),
            composition=json.loads(ir.model_dump_json()))
    # redo
    if not undone_ids:
        return CommandResult(status="REJECTED", errors=[{
            "code": "NOTHING_TO_REDO", "message": "没有可恢复的编辑", "recoverable": True}])
    redo_id = undone_ids.pop()
    target = next((r for r in revisions if r.get("revision_id") == redo_id), None)
    if target is None:
        return CommandResult(status="REJECTED", errors=[{
            "code": "REVISION_MISSING", "message": f"待恢复 revision {redo_id} 不存在",
            "recoverable": True}])
    snap = target.get("ir_after")
    if not snap:
        return CommandResult(status="REJECTED", errors=[{
            "code": "REDO_UNAVAILABLE",
            "message": f"revision {redo_id} 无 IR 快照，无法恢复", "recoverable": True}])
    try:
        ir = CompositionIR(**_copy.deepcopy(snap))
    except Exception as exc:
        return CommandResult(status="REJECTED", errors=[{
            "code": "SNAPSHOT_INVALID", "message": f"快照恢复失败: {exc}", "recoverable": True}])
    save_composition(project_id, ir)
    undo_path.parent.mkdir(parents=True, exist_ok=True)
    undo_path.write_text(json.dumps(undone_ids, ensure_ascii=False), encoding="utf-8")
    return CommandResult(
        status="APPLIED", revision_id=f"redo-{redo_id}",
        composition_version=composition_version(project_id),
        composition=json.loads(ir.model_dump_json()))
# ---------------------------------------------------------------------------
# 定位辅助
# ---------------------------------------------------------------------------


def _find_scene(ir: CompositionIR, target_id: str) -> Tuple[int, SceneIR]:
    for i, sc in enumerate(ir.scenes):
        if sc.id == target_id:
            return i, sc
    raise EditorError("NOT_FOUND", f"场景 {target_id} 不存在", recoverable=False)


def _find_layer(ir: CompositionIR, target_id: str) -> Tuple[SceneIR, int, Layer]:
    """layer id 格式 '<scene_id>#<layer_index>'（前端时间线渲染时生成）。"""
    if "#" not in target_id:
        raise EditorError("INVALID_TARGET", f"layer targetId 需为 '<scene>#<idx>' 格式: {target_id}")
    scene_id, idx_s = target_id.rsplit("#", 1)
    si, scene = _find_scene(ir, scene_id)
    try:
        idx = int(idx_s)
    except ValueError as exc:
        raise EditorError("INVALID_TARGET", f"layer index 非数值: {target_id}") from exc
    if idx < 0 or idx >= len(scene.layers):
        raise EditorError("NOT_FOUND", f"层 {target_id} 不存在", recoverable=False)
    return scene, idx, scene.layers[idx]


def _stale_of_scene(project_id: str, scene_id: str) -> List[str]:
    """场景级 stale 产物（REV-002 局部失效：只该场景的 render/preview）。"""
    return [f"{scene_id}:render", f"{scene_id}:preview"]


def _stale_of_layer(project_id: str, scene_id: str, layer_id: str) -> List[str]:
    return [f"{layer_id}:render", f"{scene_id}:preview"]


def _ensure_layer_unlocked(layer: Layer) -> None:
    """§13 Layer.locked：锁定的层拒绝改动（防误改）。"""
    if getattr(layer, "locked", None):
        raise EditorError("LAYER_LOCKED", f"层 {layer.asset_id or type(layer)} 已锁定，先解锁再改")


def _record_behavior_if_applicable(cmd_name: str, command: dict) -> None:
    """§三十二 Style DNA：换动效/换镜头/生成素材落 used（只记明确行为，不评分）。

    失败不阻断编辑命令（行为记录是旁路，best-effort）。
    """
    item_id = None
    if cmd_name == "replace_motion":
        item_id = (command.get("payload") or {}).get("motionRecipeId")
    elif cmd_name == "replace_recipe":
        item_id = (command.get("payload") or {}).get("recipeId")
    elif cmd_name == "generate_media":
        item_id = (command.get("payload") or {}).get("assetId")
    if not item_id:
        return
    try:
        from video_director import memory as vd_memory
        vd_memory.record_motion_behavior(
            item_id, "used",
            item_kind="motion" if cmd_name == "replace_motion" else
                     ("shot" if cmd_name == "replace_recipe" else "asset"),
            project_id=None,
        )
    except Exception as exc:  # noqa: BLE001 行为记录旁路，绝不影响编辑主流程
        logger.debug(f"style_dna: used 记录失败（忽略）: {exc}")


# ---------------------------------------------------------------------------
# Editor Command 主入口（§27）
# ---------------------------------------------------------------------------

#: 第一版命令集（全局契约 §29 Timeline Interaction + Task Card §11/§20）
SUPPORTED_COMMANDS = (
    "select",                    # 无副作用（前端选择态，服务端不记 revision）
    "trim",                      # layer 时长调整（视频层 source 区间变化）
    "split",                     # 场景在比例点分裂为两场景
    "move",                      # 场景重排
    "delete",                    # 删场景/层
    "duplicate",                 # 复制场景
    "replace_source",            # 换素材
    "replace_recipe",            # 换镜头（Shot Picker）
    "replace_motion",            # 换动效（Motion Gallery）
    "set_motion_parameter",      # 调动效参数（Inspector）
    "set_duration",              # 调场景时长
    "set_opacity",               # 层透明度（折叠为 motion opacity 近似）
    "set_style",                 # 项目级风格（§20）
    "set_transform",             # 层静态变换（scale/opacity，§20）
    "set_audio_volume",          # 音轨音量（§20 / §37 Audio Bus gain）
    "set_audio_mute",            # §37 Audio Bus：轨级静音（mute 一等操作）
    "set_audio_fade",            # §37 Audio Bus：轨级淡入/淡出秒数
    "audio_mix",                 # §37 Audio Bus：一次多轨混音（「原声 20%、BGM 10%、旁白正常」）
    "add_audio_keyframe",        # §37 Audio Bus：音轨关键帧（volume 渐变）
    "add_broll",                 # 加 B-roll 层（§20）
    "remove_broll",              # 删 B-roll 层（§20）
    "generate_media",            # 生成素材落地为层（§20/EDT-008）
    "replace_generated_media",   # 换生成素材层（§20）
    "lock",                      # 锁/解锁层（§13 Layer.locked）
    "batch",                     # 多选批量命令（§47 multi-select：delete/duplicate/lock 集合）
    # ---- MPT 吸收方案 §18/§19/§32：原声/水印/字幕样式（一等公民）----
    "set_original_audio",        # 原声 preserve/mute/duck（§18 OriginalAudioPolicy）
    "set_original_audio_volume", # 原声音量（§18）
    "set_watermark",             # 加水印/更新水印（§19 WatermarkLayer）
    "remove_watermark",          # 移除水印（§19）
    "set_subtitle_style",        # 字幕样式（§32 SubtitleStyle 入 IR）
    "set_subtitle_track",        # 字幕轨（§31/§33 SubtitleTrack：句级/逐词 cues 入 IR）,
    # ---- 视觉语义归一（§72 六 Contract 可执行化）----
    "set_visual_transform",       # 结构化层变换（x/y/scale/rotation/opacity/anchor）
    "add_keyframe",               # 层属性关键帧（§23 KeyframeTrack）
    "remove_keyframe",            # 删关键帧
    "apply_mask",                 # 几何蒙版（§20 Mask）
    "remove_mask",                # 移除蒙版
    # ---- Frontend Unification §14/§15/§16（camera/pip/morph 执行链）----
    "apply_camera",               # 摄影机运动 → Composition.camera_tracks（canonical §14）
    "configure_pip",              # 画中画层配置（PiP=Layer 结构化属性 §15）
    "apply_morph",                # 自有 Graphic Block 间形变（§16 MorphLink）
    # ---- Frontend Unification §7/§32A（GFX Plan → Composition 归一）----
    "apply_graphic_plan",         # Creative Plan（proposal）确认 → Composition IR（§7 分层）
    "set_graphic_block",          # 换视觉块（graphic 层 program.block_id）
    "set_graphic_layout",         # 换布局（graphic 层 program.layout）
    "set_scene_continuity",       # 连续性修改（Scene.continuity_in + Composition links）
    "set_transition_out",         # 转场确认（Scene.transition_out）
    "undo",
    "redo",
)


@dataclass
class CommandResult:
    status: str  # APPLIED | REJECTED
    revision_id: Optional[str] = None
    composition_version: int = 0
    changed_artifacts: List[str] = field(default_factory=list)
    reused_artifacts: List[str] = field(default_factory=list)
    stale_artifacts: List[str] = field(default_factory=list)
    errors: List[dict] = field(default_factory=list)
    composition: Optional[dict] = None  # 更新后的 IR（前端重渲染时间线用）


def execute_command(project_id: str, command: dict) -> CommandResult:
    """执行 Editor Command（校验 → 修改 → revision 落盘 → 返回结果）。

    expectedVersion 与服务端当前版本不一致 → CONFLICT（§27/§28）。
    AI/Gallery 的"应用"最终都走这里——前端不直接改 Composition。
    """
    cmd_name = command.get("command")
    if cmd_name not in SUPPORTED_COMMANDS:
        return CommandResult(
            status="REJECTED",
            errors=[{"code": "COMMAND_NOT_SUPPORTED", "message": f"命令 {cmd_name!r} 不支持"}],
        )
    if cmd_name == "select":
        # selection 是前端 transient 态（§35），不产生 revision
        return CommandResult(status="APPLIED", composition_version=composition_version(project_id))
    # §三十二 Style DNA：记录明确行为（used；不评分）。仅换动效/换镜头/加 B-roll 记 used
    _record_behavior_if_applicable(cmd_name, command)

    expected = command.get("expectedVersion")
    current = composition_version(project_id)
    if expected is not None and int(expected) != current:
        return CommandResult(
            status="REJECTED",
            errors=[{
                "code": "CONFLICT",
                "message": f"版本冲突：客户端 {expected} != 服务端 {current}，请刷新后重试",
                "recoverable": True,
            }],
        )

    if cmd_name in ("undo", "redo"):
        return _undo_redo(project_id, cmd_name)

    ir = load_composition(project_id)
    before_ir = ir.model_copy(deep=True)  # §46 undo 快照基线
    target_id = command.get("targetId") or ""
    payload = command.get("payload") or {}

    changed: List[str] = []
    stale: List[str] = []
    reused: List[str] = [sc.id for sc in ir.scenes]  # 默认全部复用，改动的从 reused 移除
    try:
        if cmd_name == "replace_motion":
            changed, stale = _cmd_replace_motion(ir, target_id, payload)
        elif cmd_name == "set_motion_parameter":
            changed, stale = _cmd_set_motion_parameter(ir, target_id, payload)
        elif cmd_name == "replace_recipe":
            changed, stale = _cmd_replace_recipe(ir, target_id, payload, project_id)
        elif cmd_name == "replace_source":
            changed, stale = _cmd_replace_source(ir, target_id, payload)
        elif cmd_name == "trim":
            changed, stale = _cmd_trim(ir, target_id, payload)
        elif cmd_name == "split":
            changed, stale = _cmd_split(ir, target_id, payload)
        elif cmd_name == "move":
            changed, stale = _cmd_move(ir, target_id, payload)
        elif cmd_name == "delete":
            changed, stale = _cmd_delete(ir, target_id, payload)
        elif cmd_name == "duplicate":
            changed, stale = _cmd_duplicate(ir, target_id, payload)
        elif cmd_name == "set_duration":
            changed, stale = _cmd_set_duration(ir, target_id, payload)
        elif cmd_name == "set_opacity":
            changed, stale = _cmd_set_opacity(ir, target_id, payload)
        elif cmd_name == "set_style":
            changed, stale = _cmd_set_style(ir, target_id, payload)
        elif cmd_name == "set_transform":
            changed, stale = _cmd_set_transform(ir, target_id, payload)
        elif cmd_name == "set_audio_volume":
            changed, stale = _cmd_set_audio_volume(ir, target_id, payload)
        elif cmd_name == "set_audio_mute":
            changed, stale = _cmd_set_audio_mute(ir, target_id, payload)
        elif cmd_name == "set_audio_fade":
            changed, stale = _cmd_set_audio_fade(ir, target_id, payload)
        elif cmd_name == "audio_mix":
            changed, stale = _cmd_audio_mix(ir, target_id, payload)
        elif cmd_name == "add_audio_keyframe":
            changed, stale = _cmd_add_audio_keyframe(ir, target_id, payload)
        elif cmd_name == "add_broll":
            changed, stale = _cmd_add_broll(ir, target_id, payload)
        elif cmd_name == "remove_broll":
            changed, stale = _cmd_remove_broll(ir, target_id, payload)
        elif cmd_name == "generate_media":
            changed, stale = _cmd_generate_media(ir, target_id, payload)
        elif cmd_name == "replace_generated_media":
            changed, stale = _cmd_replace_generated_media(ir, target_id, payload)
        elif cmd_name == "lock":
            changed, stale = _cmd_lock(ir, target_id, payload)
        elif cmd_name == "batch":
            changed, stale = _cmd_batch(ir, payload)
        elif cmd_name == "set_original_audio":
            changed, stale = _cmd_set_original_audio(ir, target_id, payload)
        elif cmd_name == "set_original_audio_volume":
            changed, stale = _cmd_set_original_audio_volume(ir, target_id, payload)
        elif cmd_name == "set_watermark":
            changed, stale = _cmd_set_watermark(ir, target_id, payload)
        elif cmd_name == "remove_watermark":
            changed, stale = _cmd_remove_watermark(ir, target_id, payload)
        elif cmd_name == "set_subtitle_style":
            changed, stale = _cmd_set_subtitle_style(ir, target_id, payload)
        elif cmd_name == "set_subtitle_track":
            changed, stale = _cmd_set_subtitle_track(ir, target_id, payload)
        elif cmd_name == "set_visual_transform":
            changed, stale = _cmd_set_visual_transform(ir, target_id, payload)
        elif cmd_name == "add_keyframe":
            changed, stale = _cmd_add_keyframe(ir, target_id, payload)
        elif cmd_name == "remove_keyframe":
            changed, stale = _cmd_remove_keyframe(ir, target_id, payload)
        elif cmd_name == "apply_mask":
            changed, stale = _cmd_apply_mask(ir, target_id, payload)
        elif cmd_name == "remove_mask":
            changed, stale = _cmd_remove_mask(ir, target_id, payload)
        elif cmd_name == "apply_camera":
            changed, stale = _cmd_apply_camera(ir, target_id, payload)
        elif cmd_name == "configure_pip":
            changed, stale = _cmd_configure_pip(ir, target_id, payload)
        elif cmd_name == "apply_morph":
            changed, stale = _cmd_apply_morph(ir, target_id, payload)
        elif cmd_name == "apply_graphic_plan":
            changed, stale = _cmd_apply_graphic_plan(ir, target_id, payload)
        elif cmd_name == "set_graphic_block":
            changed, stale = _cmd_set_graphic_block(ir, target_id, payload)
        elif cmd_name == "set_graphic_layout":
            changed, stale = _cmd_set_graphic_layout(ir, target_id, payload)
        elif cmd_name == "set_scene_continuity":
            changed, stale = _cmd_set_scene_continuity(ir, target_id, payload)
        elif cmd_name == "set_transition_out":
            changed, stale = _cmd_set_transition_out(ir, target_id, payload)
    except EditorError as exc:
        return CommandResult(status="REJECTED", errors=[{
            "code": exc.code, "message": str(exc), "recoverable": exc.recoverable,
        }])
    except CompileError as exc:
        return CommandResult(status="REJECTED", errors=[{
            "code": exc.code, "message": str(exc), "recoverable": True,
        }])

    for c in changed:
        if c in reused:
            reused.remove(c)

    # 保存 + revision 记录（§21：不覆盖历史，追加）
    # §46 Undo/Redo：revision 附 before/after IR 快照（undo 恢复点）。
    # 快照体积可控（Composition IR 纯 JSON），旧记录无快照时 undo 诚实报错。
    save_composition(project_id, ir)
    revision_id = f"rev-{uuid.uuid4().hex[:8]}"
    revision = {
        "revision_id": revision_id,
        "project_id": project_id,
        "base_version": current,
        "command": cmd_name,
        "target_id": target_id,
        "payload": payload,
        "changed_artifacts": changed,
        "stale_artifacts": stale,
        "reused_artifacts": reused,
        "author": command.get("author", "user"),
        "created_at": time.time(),
        "ir_before": _snapshot_for_revision(before_ir),
        "ir_after": _snapshot_for_revision(ir),
    }
    _append_revision(project_id, revision)
    logger.info(
        f"editor: {cmd_name} {target_id} → revision {revision_id} "
        f"(changed={changed}, stale={stale})"
    )
    return CommandResult(
        status="APPLIED",
        revision_id=revision_id,
        composition_version=current + 1,
        changed_artifacts=changed,
        reused_artifacts=reused,
        stale_artifacts=stale,
        composition=json.loads(ir.model_dump_json()),
    )


# ---------------------------------------------------------------------------
# 各命令实现（全部返回新对象风格：改的是 load_composition 出来的内存 IR，
# execute_command 统一 save）
# ---------------------------------------------------------------------------


def _cmd_replace_motion(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Motion Gallery 应用：Layer.motion_recipe_id 替换（CMP-002 复用）。"""
    scene, idx, layer = _find_layer(ir, target_id)
    _ensure_layer_unlocked(layer)
    motion_id = payload.get("motionRecipeId") or ""
    if not motion_id:
        raise EditorError("INVALID_PAYLOAD", "payload 缺 motionRecipeId")
    new_layer = apply_motion_to_layer(
        layer, motion_id,
        motion_version=payload.get("motionVersion"),
        duration=payload.get("duration"),
        parameters=payload.get("parameters"),
    )
    scene.layers[idx] = new_layer
    layer_id = f"{scene.id}#{idx}"
    return [layer_id], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)


def _cmd_set_motion_parameter(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Inspector 参数调整：重建 motion_instance（duration/parameters）。"""
    scene, idx, layer = _find_layer(ir, target_id)
    _ensure_layer_unlocked(layer)
    if not layer.motion_recipe_id:
        raise EditorError("INVALID_PAYLOAD", f"层 {target_id} 无 motion，先应用动效")
    params = dict(layer.motion_instance or {}).get("parameters") or {}
    params.update(payload.get("parameters") or {})
    new_layer = apply_motion_to_layer(
        layer, layer.motion_recipe_id, motion_version=layer.motion_version,
        duration=payload.get("duration", (layer.motion_instance or {}).get("duration")),
        parameters=params,
    )
    scene.layers[idx] = new_layer
    layer_id = f"{scene.id}#{idx}"
    return [layer_id], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)


def _cmd_replace_recipe(ir: CompositionIR, target_id: str, payload: dict, project_id: str) -> Tuple[List[str], List[str]]:
    """Shot Picker 换镜头：场景按新 recipe 重建（受时长保持约束）。"""
    si, scene = _find_scene(ir, target_id)
    recipe_id = payload.get("recipeId") or ""
    if not recipe_id:
        raise EditorError("INVALID_PAYLOAD", "payload 缺 recipeId")
    # 新场景素材沿用旧场景的素材（换镜头不换素材；换素材走 replace_source）
    old_assets: Dict[str, str] = {}
    for lay in scene.layers:
        if lay.asset_id:
            old_assets[lay.asset_id] = lay.asset_id
    # 从项目 manifest 取 asset 类型
    manifest_p = config.project_dir(project_id) / "assets" / "manifest.json"
    type_by_id = {}
    if manifest_p.is_file():
        for a in json.loads(manifest_p.read_text(encoding="utf-8")):
            type_by_id[a["id"]] = a["type"]
    # 槽位绑定：recipe 槽位按类型取旧素材
    from video_director.registries.recipes import ShotRegistry
    recipe = ShotRegistry().get(recipe_id, payload.get("recipeVersion"))
    slot_bindings = {}
    for slot in recipe.slots:
        for aid, _ in old_assets.items():
            want = slot.type
            have = type_by_id.get(aid, "video")
            if want in ("product", "person", "screen") and have == "video":
                slot_bindings[slot.id] = aid
                break
            if want == "image" and have == "image":
                slot_bindings[slot.id] = aid
                break
            if want == "text":
                slot_bindings[slot.id] = scene.text
                break
    if not slot_bindings:
        raise EditorError(
            "MISSING_REQUIREMENT",
            f"新镜头 {recipe_id} 需要的素材类型在场景中不可用（{[s.type for s in recipe.slots]}）",
        )
    new_scene = instantiate_shot(
        recipe_id=recipe_id, recipe_version=payload.get("recipeVersion"),
        scene_id=scene.id, scene_text=scene.text,
        scene_start=scene.start, scene_duration=scene.duration,
        slot_bindings=slot_bindings,
        assets={aid: type(aid) for aid in []} or _assets_like_from_manifest(project_id, list(old_assets.keys())),
    )
    ir.scenes[si] = new_scene
    return [scene.id], _stale_of_scene(project_id, scene.id)


def _assets_like_from_manifest(project_id: str, asset_ids: List[str]) -> Dict[str, object]:
    """从 manifest 构造 compiler 需要的 assets 鸭子对象（id→{type,duration}）。"""
    manifest_p = config.project_dir(project_id) / "assets" / "manifest.json"
    out = {}
    if manifest_p.is_file():
        for a in json.loads(manifest_p.read_text(encoding="utf-8")):
            if a["id"] in asset_ids:
                out[a["id"]] = type("A", (), {"type": a["type"], "duration": a.get("duration")})()
    return out


def _cmd_replace_source(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """换素材：layer.asset_id 替换（同类型素材）。"""
    scene, idx, layer = _find_layer(ir, target_id)
    _ensure_layer_unlocked(layer)
    new_asset = payload.get("assetId") or ""
    if not new_asset:
        raise EditorError("INVALID_PAYLOAD", "payload 缺 assetId")
    # 素材存在性由 API 层查 manifest；此处只做 IR 内一致性（类型可由 payload 指定）
    updated = layer.model_copy(update={"asset_id": new_asset})
    scene.layers[idx] = updated
    layer_id = f"{scene.id}#{idx}"
    return [layer_id], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)


def _cmd_trim(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """trim 层：视频层改 source 区间（in/out），时长联动。"""
    scene, idx, layer = _find_layer(ir, target_id)
    _ensure_layer_unlocked(layer)
    if layer.type != LayerType.video:
        raise EditorError("INVALID_TARGET", f"trim 仅支持 video 层（image 用 set_duration）")
    new_in = float(payload.get("sourceIn", layer.source_in))
    new_out = float(payload.get("sourceOut", layer.source_out))
    if new_out <= new_in:
        raise EditorError("INVALID_PAYLOAD", f"trim 区间非法: {new_in} >= {new_out}")
    span = new_out - new_in
    updated = layer.model_copy(update={
        "source_in": new_in, "source_out": new_out,
        "duration": span / layer.speed,
    })
    scene.layers[idx] = updated
    layer_id = f"{scene.id}#{idx}"
    return [layer_id], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)


def _cmd_split(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """split 场景：在比例点（默认 0.5）分为两场景，layers 按比例切分时长。

    Narration Timeline 约束：场景切分不改变总时长与相邻场景衔接
    （后场景 start 重算）。
    """
    si, scene = _find_scene(ir, target_id)
    ratio = float(payload.get("ratio", 0.5))
    if not (0.05 < ratio < 0.95):
        raise EditorError("INVALID_PAYLOAD", f"split 比例 {ratio} 越界 (0.05,0.95)")
    d1 = scene.duration * ratio
    d2 = scene.duration - d1
    # 层按 start 比例切：前半层归 A，后半层归 B（每层重算 start/duration）
    layers_a, layers_b = [], []
    cut = scene.duration * ratio
    for lay in scene.layers:
        lay_end = lay.start + lay.duration
        if lay_end <= cut + 1e-6:
            layers_a.append(lay)
        elif lay.start >= cut - 1e-6:
            layers_b.append(lay.model_copy(update={"start": lay.start - cut}))
        else:
            # 跨切点的层：切成两半
            a_dur = cut - lay.start
            if lay.type == LayerType.video:
                span = lay.source_out - lay.source_in
                a_span = span * (a_dur / lay.duration)
                layers_a.append(lay.model_copy(update={"duration": a_dur, "source_out": lay.source_in + a_span}))
                layers_b.append(lay.model_copy(update={
                    "start": 0.0, "duration": lay.duration - a_dur,
                    "source_in": lay.source_in + a_span,
                }))
            else:
                layers_a.append(lay.model_copy(update={"duration": a_dur}))
                layers_b.append(lay.model_copy(update={"start": 0.0, "duration": lay.duration - a_dur}))
    scene_a = scene.model_copy(update={
        "id": f"{scene.id}a", "duration": d1, "layers": layers_a,
        "transition_out": scene.transition_out,
    })
    scene_b = scene.model_copy(update={
        "id": f"{scene.id}b", "duration": d2, "layers": layers_b,
    })
    ir.scenes[si] = scene_a
    ir.scenes.insert(si + 1, scene_b)
    _renumber_starts(ir)
    return [scene_a.id, scene_b.id], _stale_of_scene(project_id="", scene_id=scene.id)


def _cmd_move(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """move 场景重排：toIndex 目标位置，后重算全部 start（总时长不变）。"""
    si, scene = _find_scene(ir, target_id)
    to = int(payload.get("toIndex", si))
    to = max(0, min(to, len(ir.scenes) - 1))
    ir.scenes.insert(to, ir.scenes.pop(si))
    _renumber_starts(ir)
    return [scene.id], [f"{scene.id}:render", f"{scene.id}:preview"]


def _cmd_delete(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """删除场景或层（targetId 带 # 为层）。删除后重排 start 保持连续。"""
    if "#" in target_id:
        scene, idx, layer = _find_layer(ir, target_id)
        if len(scene.layers) <= 1:
            raise EditorError("INVALID_PAYLOAD", f"场景 {scene.id} 只剩一层，删除请直接删场景")
        del scene.layers[idx]
        # 层删除后必须重铺（SceneIR 校验要求无缝铺满）：剩余层按比例拉伸
        _respread_layers(scene)
        layer_id = f"{scene.id}#{idx}"
        return [layer_id], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)
    si, scene = _find_scene(ir, target_id)
    if len(ir.scenes) <= 1:
        raise EditorError("INVALID_PAYLOAD", "只剩一个场景，不能删除")
    del ir.scenes[si]
    _renumber_starts(ir)
    _rescale_total(ir)
    return [scene.id], [f"{scene.id}:render"]


def _cmd_duplicate(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """复制场景插到其后（总时长增加；CompositionIR 校验要求总长同步）。"""
    si, scene = _find_scene(ir, target_id)
    copy = scene.model_copy(update={"id": f"{scene.id}-copy{uuid.uuid4().hex[:3]}"})
    ir.scenes.insert(si + 1, copy)
    _renumber_starts(ir)
    _rescale_total(ir)
    return [copy.id], [f"{copy.id}:render"]


def _cmd_set_duration(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """调场景时长：layers 同步拉伸（保持铺满），总时长联动。"""
    si, scene = _find_scene(ir, target_id)
    new_dur = float(payload.get("duration", scene.duration))
    if new_dur <= 0.3:
        raise EditorError("INVALID_PAYLOAD", f"时长 {new_dur}s 过短（≥0.3s）")
    factor = new_dur / scene.duration
    new_layers = []
    for lay in scene.layers:
        new_start = lay.start * factor
        new_lay_dur = lay.duration * factor
        if lay.type == LayerType.video:
            span = lay.source_out - lay.source_in
            new_span = span * factor
            new_layers.append(lay.model_copy(update={
                "start": new_start, "duration": new_lay_dur,
                "source_out": lay.source_in + new_span,
            }))
        else:
            new_layers.append(lay.model_copy(update={"start": new_start, "duration": new_lay_dur}))
    ir.scenes[si] = scene.model_copy(update={"duration": new_dur, "layers": new_layers})
    _renumber_starts(ir)
    _rescale_total(ir)
    return [scene.id], _stale_of_scene(project_id="", scene_id=scene.id)


def _cmd_set_opacity(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """层透明度：写 motion_instance.parameters.opacity（渲染器 alpha 缩放）。

    与 replace_motion 不同：不换 recipe，只调透明度标量。
    """
    scene, idx, layer = _find_layer(ir, target_id)
    _ensure_layer_unlocked(layer)
    opacity = float(payload.get("opacity", 1.0))
    if not (0.0 <= opacity <= 1.0):
        raise EditorError("INVALID_PAYLOAD", f"opacity {opacity} 越界 [0,1]")
    inst = dict(layer.motion_instance or {})
    inst["parameters"] = {**(inst.get("parameters") or {}), "opacity": opacity}
    scene.layers[idx] = layer.model_copy(update={"motion_instance": inst})
    layer_id = f"{scene.id}#{idx}"
    return [layer_id], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)


# ---------------------------------------------------------------------------
# IR 一致性维护（split/move/delete/duplicate/set_duration 后调用）
# ---------------------------------------------------------------------------


def _renumber_starts(ir: CompositionIR) -> None:
    """场景连续重排 start（CompositionIR 校验要求无缝）。"""
    cursor = 0.0
    for sc in ir.scenes:
        sc.start = cursor
        cursor += sc.duration


def _rescale_total(ir: CompositionIR) -> None:
    """总时长 = 场景和（删除/复制后同步 project.duration）。"""
    ir.project.duration = sum(sc.duration for sc in ir.scenes)


def _respread_layers(scene: SceneIR) -> None:
    """层删除后按剩余层比例重铺（保持铺满约束）。"""
    if not scene.layers:
        return
    total = sum(l.duration for l in scene.layers)
    factor = scene.duration / total if total > 0 else 1.0
    cursor = 0.0
    for i, lay in enumerate(scene.layers):
        new_dur = lay.duration * factor
        if lay.type == LayerType.video:
            span = lay.source_out - lay.source_in
            new_span = span * factor
            scene.layers[i] = lay.model_copy(update={
                "start": cursor, "duration": new_dur, "source_out": lay.source_in + new_span,
            })
        else:
            scene.layers[i] = lay.model_copy(update={"start": cursor, "duration": new_dur})
        cursor += new_dur


# ---------------------------------------------------------------------------
# §20 第一版命令补全（set_style/set_transform/set_audio_volume/add_broll/
# remove_broll/generate_media/replace_generated_media/lock）
# ---------------------------------------------------------------------------


def _cmd_set_style(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """项目级风格（§20）。styleId 变化影响整体 render，不改场景结构。"""
    style_id = payload.get("styleId")
    if not style_id:
        raise EditorError("INVALID_PAYLOAD", "payload 缺 styleId")
    ir.style_id = style_id
    return [], [f"project:{ir.project.width}x{ir.project.height}:render"]


def _cmd_set_transform(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """层静态变换（§20）：scale/opacity（x/y 偏移本渲染器暂不支持，诚实拒绝）。"""
    scene, idx, layer = _find_layer(ir, target_id)
    _ensure_layer_unlocked(layer)
    for key in payload:
        if key not in ("scale", "opacity"):
            raise EditorError(
                "INVALID_PAYLOAD",
                f"set_transform 仅支持 scale/opacity（收到 {key!r}；x/y 偏移需 PIP 背景，本渲染器暂不支持）",
            )
    transform = {"scale": 1.0, "opacity": 1.0}
    existing = ((layer.motion_instance or {}).get("parameters") or {}).get("transform") or {}
    transform.update(existing)
    if "scale" in payload:
        scale = float(payload["scale"])
        if not (0.1 <= scale <= 3.0):
            raise EditorError("INVALID_PARAMETER", f"scale {scale} 越界 [0.1,3.0]")
        transform["scale"] = scale
    if "opacity" in payload:
        opacity = float(payload["opacity"])
        if not (0.0 <= opacity <= 1.0):
            raise EditorError("INVALID_PARAMETER", f"opacity {opacity} 越界 [0,1]")
        transform["opacity"] = opacity
    inst = dict(layer.motion_instance or {})
    params = dict(inst.get("parameters") or {})
    params["transform"] = transform
    inst["parameters"] = params
    scene.layers[idx] = layer.model_copy(update={"motion_instance": inst})
    layer_id = f"{scene.id}#{idx}"
    return [layer_id], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)


def _cmd_set_audio_volume(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """音轨音量（§20/§37）：targetId = narration|bgm|sfx|ambient，payload.volume ∈ [0,2]。

    可选 payload.assetId（曲目选择：music.add 应用时把 bgm 轨源指向选中资产）
    与 payload.ducking（§37 旁白闪避开关——volume 调整同时可开/关闪避）。
    """
    track_type = target_id or payload.get("track_type") or ""
    if track_type not in ("narration", "dialogue", "bgm", "sfx", "ambient", "ambience"):
        raise EditorError("INVALID_TARGET", f"set_audio_volume 目标音轨 {track_type!r} 非法")
    # §37 duck 语义：volume 不传（None）= 保留当前音量，只更新其他字段
    volume_raw = payload.get("volume")
    volume = float(volume_raw) if volume_raw is not None else None
    if volume is not None and not (0.0 <= volume <= 2.0):
        raise EditorError("INVALID_PARAMETER", f"volume {volume} 越界 [0,2]")
    asset_id = payload.get("assetId")
    has_duck = "ducking" in payload
    changed = False
    for i, t in enumerate(ir.audio):
        if t.type.value == track_type:
            update = {}
            if volume is not None:
                update["volume"] = volume
            if asset_id is not None:
                update["asset_id"] = asset_id
            if has_duck:
                update["ducking"] = bool(payload["ducking"])
            if update:
                ir.audio[i] = t.model_copy(update=update)
                changed = True
            else:
                changed = True  # 命中但无需更新（幂等）
    if not changed:
        # 允许为尚不存在的音轨预置音量（下次加轨沿用）
        from video_director.models import AudioTrack, AudioTrackType
        kwargs = {}
        if asset_id is not None:
            kwargs["asset_id"] = asset_id
        if has_duck:
            kwargs["ducking"] = bool(payload["ducking"])
        ir.audio.append(AudioTrack(
            type=AudioTrackType(track_type), start=0.0,
            duration=ir.project.duration, volume=volume if volume is not None else 1.0,
            **kwargs,
        ))
    return [], [f"audio:{track_type}:render"]


_VALID_AUDIO_TRACKS = ("narration", "dialogue", "bgm", "sfx", "ambient",
                      "ambience", "original")


def _cmd_set_audio_mute(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§37 Audio Bus：轨级静音（muted=True 一等操作，区别于 volume=0——
    静音保留原 gain，取消静音即恢复；渲染层对 muted 轨整轨跳过）。"""
    track_type = target_id or payload.get("track_type") or ""
    if track_type not in _VALID_AUDIO_TRACKS:
        raise EditorError("INVALID_TARGET", f"set_audio_mute 目标音轨 {track_type!r} 非法")
    muted = bool(payload.get("muted", True))
    changed = False
    for i, t in enumerate(ir.audio):
        if t.type.value == track_type:
            ir.audio[i] = t.model_copy(update={"muted": muted})
            changed = True
    if not changed:
        from video_director.models import AudioTrack, AudioTrackType
        ir.audio.append(AudioTrack(
            type=AudioTrackType(track_type), asset_id=None,
            start=0.0, duration=ir.project.duration, muted=muted,
        ))
    return [], [f"audio:{track_type}:render"]


def _cmd_set_audio_fade(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§37 Audio Bus：轨级淡入/淡出（fadeIn/fadeOut 秒）。"""
    track_type = target_id or payload.get("track_type") or ""
    if track_type not in _VALID_AUDIO_TRACKS:
        raise EditorError("INVALID_TARGET", f"set_audio_fade 目标音轨 {track_type!r} 非法")
    fade_in = float(payload.get("fadeIn", payload.get("fade_in", 0.0)) or 0.0)
    fade_out = float(payload.get("fadeOut", payload.get("fade_out", 0.0)) or 0.0)
    if fade_in < 0 or fade_out < 0:
        raise EditorError("INVALID_PARAMETER", "淡入/淡出秒数不能为负")
    changed = False
    for i, t in enumerate(ir.audio):
        if t.type.value == track_type:
            ir.audio[i] = t.model_copy(update={"fadeIn": fade_in, "fadeOut": fade_out})
            changed = True
    if not changed:
        from video_director.models import AudioTrack, AudioTrackType
        ir.audio.append(AudioTrack(
            type=AudioTrackType(track_type), asset_id=None,
            start=0.0, duration=ir.project.duration,
            fadeIn=fade_in, fadeOut=fade_out,
        ))
    return [], [f"audio:{track_type}:render"]


def _cmd_audio_mix(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§37 Audio Bus：一次多轨混音。payload = {tracks: {narration: {volume, muted,
    ducking, fadeIn, fadeOut}, bgm: {...}, original: {...}, sfx: {...}}}。

    「原声 20%、BGM 10%、旁白正常」= {"tracks": {"original": {"volume": 0.2},
    "bgm": {"volume": 0.1}, "narration": {"volume": 1.0}}}——一个标准 Audio
    Mix 操作，AI 和 UI 调同一命令（§37：不再分别调四套 API）。
    """
    tracks = payload.get("tracks") or {}
    if not isinstance(tracks, dict) or not tracks:
        raise EditorError("INVALID_PAYLOAD", "audio_mix 需要 tracks 映射（如 {bgm: {volume: 0.1}}）")
    stale: List[str] = []
    for track_type, opts in tracks.items():
        if track_type not in _VALID_AUDIO_TRACKS:
            raise EditorError("INVALID_TARGET", f"audio_mix 音轨 {track_type!r} 非法")
        if not isinstance(opts, dict):
            raise EditorError("INVALID_PAYLOAD", f"audio_mix {track_type} 参数须为对象")
        update = {}
        if "volume" in opts:
            v = float(opts["volume"])
            if not (0.0 <= v <= 2.0):
                raise EditorError("INVALID_PARAMETER", f"{track_type} volume {v} 越界 [0,2]")
            update["volume"] = v
        if "muted" in opts:
            update["muted"] = bool(opts["muted"])
        if "ducking" in opts:
            update["ducking"] = bool(opts["ducking"])
        if "fadeIn" in opts:
            update["fadeIn"] = float(opts["fadeIn"])
        if "fadeOut" in opts:
            update["fadeOut"] = float(opts["fadeOut"])
        if not update:
            continue
        changed = False
        for i, t in enumerate(ir.audio):
            if t.type.value == track_type:
                ir.audio[i] = t.model_copy(update=update)
                changed = True
        if not changed:
            from video_director.models import AudioTrack, AudioTrackType
            base = {"type": AudioTrackType(track_type), "asset_id": None,
                    "start": 0.0, "duration": ir.project.duration}
            ir.audio.append(AudioTrack(**base, **update))
        stale.append(f"audio:{track_type}:render")
    if not stale:
        raise EditorError("INVALID_PAYLOAD", "audio_mix 没有任何有效参数")
    return [], stale


def _cmd_add_audio_keyframe(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§37 Audio Bus：音轨关键帧（targetId=narration|dialogue|bgm|sfx|original|ambient）。

    payload={at, volume} → 该轨在 at 秒把音量渐变到 volume（KeyframeTrack
    property=volume，渲染层按时间插值消费）。§37 原文：每轨都有
    gain/mute/fade/ducking/keyframe——keyframe 即此命令。
    """
    track_type = target_id or payload.get("track_type") or ""
    if track_type not in _VALID_AUDIO_TRACKS:
        raise EditorError("INVALID_TARGET",
                          f"add_audio_keyframe 目标音轨 {track_type!r} 非法")
    at = float(payload.get("at", -1))
    value = payload.get("volume", payload.get("value"))
    if at < 0 or value is None:
        raise EditorError("INVALID_PAYLOAD", "add_audio_keyframe 需 payload.at / payload.volume")
    value = float(value)
    if not (0.0 <= value <= 2.0):
        raise EditorError("INVALID_PARAMETER", f"volume {value} 越界 [0,2]")
    from video_director.models import AudioTrack, AudioTrackType
    from video_director.visual_semantics import Keyframe, KeyframeTrack
    kf = Keyframe(at=at, value=value)
    changed = False
    for i, t in enumerate(ir.audio):
        if t.type.value == track_type:
            tracks = [kt for kt in (t.keyframes or [])
                      if kt.property != "volume" or True]  # 保留其他 property
            vol_tracks = [kt for kt in tracks if kt.property == "volume"]
            if vol_tracks:
                new_kfs = sorted([*vol_tracks[0].keyframes, kf], key=lambda k: k.at)
                merged = vol_tracks[0].model_copy(update={"keyframes": new_kfs})
                tracks = [merged if kt.property == "volume" else kt for kt in tracks]
            else:
                tracks.append(KeyframeTrack(property="volume", keyframes=[kf]))
            ir.audio[i] = t.model_copy(update={"keyframes": tracks})
            changed = True
    if not changed:
        from video_director.models import AudioTrack, AudioTrackType
        ir.audio.append(AudioTrack(
            type=AudioTrackType(track_type), asset_id=None,
            start=0.0, duration=ir.project.duration,
            keyframes=[KeyframeTrack(property="volume", keyframes=[kf])]))
    return [], [f"audio:{track_type}:render"]


def _cmd_add_broll(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """加 B-roll 层（§20）：scene 内追加一视觉层，复用 _respread_layers 重铺。

    video 层需 payload.sourceOut（源内可用秒）；image 层无需。
    """
    si, scene = _find_scene(ir, target_id)
    asset_id = payload.get("assetId") or ""
    if not asset_id:
        raise EditorError("INVALID_PAYLOAD", "add_broll 缺 assetId")
    asset_type = payload.get("assetType") or "image"
    if asset_type == "video":
        try:
            source_out = float(payload.get("sourceOut") or payload.get("duration") or 0)
        except (TypeError, ValueError):
            source_out = 0.0
        if source_out <= 0:
            raise EditorError(
                "INVALID_PAYLOAD", "add_broll 视频层需 sourceOut（源内秒数，可用素材库时长）"
            )
        broll = Layer(type=LayerType.video, asset_id=asset_id, start=scene.duration,
                      duration=source_out, source_in=0.0, source_out=source_out)
    else:
        broll = Layer(type=LayerType.image, asset_id=asset_id, start=scene.duration, duration=1.0)
    scene.layers.append(broll)
    _respread_layers(scene)
    broll_id = f"{scene.id}#{len(scene.layers) - 1}"
    return [broll_id], _stale_of_scene(project_id="", scene_id=scene.id)


def _cmd_remove_broll(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """删 B-roll 层（§20）：删 targetId 命中的层（同 delete 语义）。"""
    scene, idx, layer = _find_layer(ir, target_id)
    _ensure_layer_unlocked(layer)
    if len(scene.layers) <= 1:
        raise EditorError("INVALID_PAYLOAD", f"场景 {scene.id} 只剩一层，不能删")
    del scene.layers[idx]
    _respread_layers(scene)
    layer_id = f"{scene.id}#{idx}"
    return [layer_id], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)


def _cmd_generate_media(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """生成素材落地为层（EDT-008）：已生成的 asset 插为 scene 内视觉层（§31 lineage）。"""
    si, scene = _find_scene(ir, target_id)
    asset_id = payload.get("assetId") or ""
    if not asset_id:
        raise EditorError("INVALID_PAYLOAD", "generate_media 缺 assetId（先经 /api/generation 产出）")
    asset_type = payload.get("assetType") or "image"
    return _cmd_add_broll(ir, target_id, {"assetId": asset_id, "assetType": asset_type})


def _cmd_replace_generated_media(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """换生成素材层（§20）：替换生成层的 asset，保留层时长/动效。"""
    return _cmd_replace_source(ir, target_id, payload)


def _cmd_lock(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """锁/解锁层（§13 Layer.locked）。"""
    scene, idx, layer = _find_layer(ir, target_id)
    lock = bool(payload.get("locked", not getattr(layer, "locked", None)))
    scene.layers[idx] = layer.model_copy(update={"locked": lock})
    layer_id = f"{scene.id}#{idx}"
    return [layer_id], []


def _cmd_batch(ir: CompositionIR, payload: dict) -> Tuple[List[str], List[str]]:
    """多选批量命令（§47 multi-select）：对选中集合逐条执行，单 revision 原子记录。

    payload = {subcommand: "delete"|"duplicate"|"lock", targets: [...],
               lock: bool}（lock 用）。
    各子命令按 id 定位（delete/duplicate 内部重查 id，不受重排影响）。
    """
    sub = payload.get("subcommand") or ""
    targets = [t for t in (payload.get("targets") or []) if t]
    if not targets:
        raise EditorError("INVALID_PAYLOAD", "batch 缺 targets")
    if sub not in ("delete", "duplicate", "lock"):
        raise EditorError("INVALID_PAYLOAD", f"batch 不支持的 subcommand {sub!r}")
    changed_all: List[str] = []
    stale_all: List[str] = []
    for t in targets:
        try:
            if sub == "delete":
                c, s = _cmd_delete(ir, t, {})
            elif sub == "duplicate":
                c, s = _cmd_duplicate(ir, t, {})
            else:  # lock
                c, s = _cmd_lock(ir, t, {"locked": bool(payload.get("locked", True))})
        except EditorError as exc:
            # 集合中单个失败：余下目标继续（集合语义），但整体标记该错误
            if exc.code in ("NOT_FOUND", "INVALID_PAYLOAD") and exc.recoverable:
                continue
            raise
        changed_all.extend(c)
        stale_all.extend(s)
    if not changed_all:
        raise EditorError("NOOP", "批量操作无有效目标（可能已删除或锁定）")
    return changed_all, stale_all


# ---------------------------------------------------------------------------
# MPT 吸收方案 §18 Original Audio（一等公民）
# ---------------------------------------------------------------------------


def _cmd_set_original_audio(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§18：original 轨 policy（preserve/mute/duck）。original 是独立音轨
    类型，不是渲染开关。绑定素材源：若无显式 assetId，取首场景首视频层的源。"""
    mode = payload.get("mode") or "preserve"
    if mode not in ("preserve", "mute"):
        raise EditorError("INVALID_PAYLOAD", f"set_original_audio 的 mode {mode!r} 非法")
    _vol = payload.get("volume")
    volume = float(_vol if _vol is not None else (0.8 if mode == "preserve" else 0.0))
    if not (0.0 <= volume <= 2.0):
        raise EditorError("INVALID_PARAMETER", f"original volume {volume} 越界 [0,2]")
    asset_id = payload.get("assetId")
    if not asset_id:
        # 缺显式源 → 取第一个视频层（original 语义：素材原声）
        for sc in ir.scenes:
            for lay in sc.layers:
                if lay.type == LayerType.video and lay.asset_id:
                    asset_id = lay.asset_id
                    break
            if asset_id:
                break
    duck = bool(payload.get("duckAgainst") or payload.get("ducking", False))
    # §18 OriginalAudioPolicy fadeIn/fadeOut（0-10s，诚实边界）
    fin = max(0.0, min(float(payload.get("fadeIn") or 0.0), 10.0))
    fout = max(0.0, min(float(payload.get("fadeOut") or 0.0), 10.0))
    # 幂等：已有 original 轨则更新，否则新建
    for i, t in enumerate(ir.audio):
        if t.type == AudioTrackType.original:
            ir.audio[i] = t.model_copy(update={"volume": volume, "ducking": duck,
                                                "fadeIn": fin, "fadeOut": fout,
                                                "asset_id": asset_id or t.asset_id})
            return [], ["audio:original:render"]
    ir.audio.append(AudioTrack(
        type=AudioTrackType.original, asset_id=asset_id,
        start=0.0, duration=ir.project.duration, volume=volume, ducking=duck,
        fadeIn=fin, fadeOut=fout,
    ))
    return [], ["audio:original:render"]


def _cmd_set_original_audio_volume(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§18：original 轨音量。无 original 轨时新建（AI 首次调 volume 也应生效）。"""
    volume = float(payload.get("volume", 1.0))
    if not (0.0 <= volume <= 2.0):
        raise EditorError("INVALID_PARAMETER", f"original volume {volume} 越界 [0,2]")
    for i, t in enumerate(ir.audio):
        if t.type == AudioTrackType.original:
            ir.audio[i] = t.model_copy(update={"volume": volume})
            return [], ["audio:original:render"]
    # 未有 original 轨：至少要有素材源才能建原声轨（诚实：无源不凭空造）
    asset_id = None
    for sc in ir.scenes:
        for lay in sc.layers:
            if lay.type == LayerType.video and lay.asset_id:
                asset_id = lay.asset_id
                break
        if asset_id:
            break
    if not asset_id:
        raise EditorError("MISSING_REQUIREMENT", "项目无视频素材，无法建立原声轨")
    ir.audio.append(AudioTrack(
        type=AudioTrackType.original, asset_id=asset_id,
        start=0.0, duration=ir.project.duration, volume=volume,
    ))
    return [], ["audio:original:render"]


# ---------------------------------------------------------------------------
# MPT 吸收方案 §19 Watermark（一等公民）
# ---------------------------------------------------------------------------


def _cmd_set_watermark(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§19：添加/更新水印（WatermarkLayer 入 IR，渲染器读 ir.watermarks）。"""
    kind = payload.get("kind") or "text"
    text = payload.get("text")
    asset_id = payload.get("assetId")
    anchor = payload.get("anchor") or "bottom_right"
    opacity = float(payload.get("opacity", 0.75))
    scale = float(payload.get("scale", 1.0))
    start = float(payload.get("start", 0.0))
    duration = payload.get("duration")
    safe_area = int(payload.get("safeArea", 32))
    # 校验：kind=text 需 text；kind=image/logo 需 assetId
    if kind == "text" and not (text or "").strip():
        raise EditorError("INVALID_PAYLOAD", "text 水印缺 text")
    if kind in ("image", "logo", "svg") and not asset_id:
        raise EditorError("INVALID_PAYLOAD", f"{kind} 水印缺 assetId")
    wm = WatermarkLayer(
        kind=kind, text=text or None, asset_id=asset_id,
        anchor=anchor, opacity=opacity, scale=scale, start=start,
        duration=duration, safe_area=safe_area,
    )
    # 幂等：同 kind 已存在则更新第一条，否则追加
    for i, w in enumerate(ir.watermarks):
        if w.kind == kind:
            ir.watermarks[i] = wm
            return [f"watermark:{kind}"], ["project:render"]
    ir.watermarks.append(wm)
    return [f"watermark:{kind}"], ["project:render"]


def _cmd_remove_watermark(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§19：移除水印。targetId 可为 kind（text/image/logo/svg）或 index。"""
    if not ir.watermarks:
        raise EditorError("NOOP", "项目无水印")
    kind = target_id or payload.get("kind") or payload.get("id") or ""
    removed = False
    if kind:
        before = len(ir.watermarks)
        ir.watermarks = [w for w in ir.watermarks if w.kind != kind]
        removed = len(ir.watermarks) < before
    else:
        ir.watermarks = ir.watermarks[:-1]  # 移除最后一条
        removed = True
    if not removed:
        raise EditorError("NOT_FOUND", f"水印 {kind!r} 不存在")
    return ["watermark"], ["project:render"]


# ---------------------------------------------------------------------------
# MPT 吸收方案 §32 Subtitle Style（一等 IR 字段）
# ---------------------------------------------------------------------------


def _cmd_set_subtitle_style(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§32：字幕样式入 IR（SubtitleStyle），不是导出设置。合并式更新。"""
    existing = ir.subtitle_style or SubtitleStyle()
    data = existing.model_dump()
    for key in ("font", "size", "color", "strokeColor", "strokeWidth",
                "background", "backgroundOpacity", "position", "alignment",
                "maxWidth", "animation", "mode"):
        if key in payload and payload[key] is not None:
            data[key] = payload[key]
    ir.subtitle_style = SubtitleStyle(**data)
    return [], ["subtitle:render"]


# ---------------------------------------------------------------------------
# MPT 吸收方案 §31/§33 Subtitle Track（word timing → Track 入 IR）
# ---------------------------------------------------------------------------


def _cmd_set_subtitle_track(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§31/§33：字幕轨入 IR。

    payload.mode = sentence | word；cues=[{start,end,text}] 直接给或经
    subtitle.generate 能力（ToolExecutor 从项目 words.json 读取后传入）。
    source 记录对齐来源（§33 lineage：tts_word_boundary|asr|manual|estimated）。
    """
    from video_director.models import SubtitleCue, SubtitleTrack

    mode = payload.get("mode") or "sentence"
    source = payload.get("source") or "manual"
    cues_data = payload.get("cues")
    if not cues_data:
        # editor 命令层无 project_id 入参（IR 不含），不能猜文件路径——
        # 直接调用必须显式给 cues（诚实拒绝，不猜）
        raise EditorError(
            "INVALID_PAYLOAD",
            "set_subtitle_track 需 payload.cues=[{start,end,text}]（或经 subtitle.generate 能力，"
            "由 ToolExecutor 从项目 words.json 读取后传入）")
    cues = []
    for c in cues_data:
        try:
            cues.append(SubtitleCue(start=float(c["start"]), end=float(c["end"]),
                                    text=str(c.get("text", ""))))
        except (KeyError, TypeError, ValueError) as exc:
            raise EditorError("INVALID_PAYLOAD", f"cue 非法 {c!r}: {exc}")
    try:
        ir.subtitle_track = SubtitleTrack(mode=mode, source=source, cues=cues)
    except ValueError as exc:
        raise EditorError("INVALID_PAYLOAD", str(exc))
    return [f"subtitle_track:{len(cues)}"], ["subtitle:render"]


# ---------------------------------------------------------------------------
# 视觉语义归一命令（§72 六 Contract 可执行化：set_visual_transform /
# add_keyframe / remove_keyframe / apply_mask / remove_mask）
# ---------------------------------------------------------------------------


def _cmd_set_visual_transform(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§72.1 结构化层变换：写 Layer.transform（替代 motion_instance.parameters 松散 dict）。

    payload 可含 x/y/scale/rotation/opacity/anchor 任意子集（增量更新，不覆盖未给字段）。
    """
    scene, idx, layer = _find_layer(ir, target_id)
    _ensure_layer_unlocked(layer)
    patch = {k: payload[k] for k in ("x", "y", "scale", "rotation", "opacity", "anchor")
             if k in payload}
    if not patch:
        raise EditorError("INVALID_PAYLOAD",
                          "set_visual_transform 需 x/y/scale/rotation/opacity/anchor 至少一项")
    try:
        base = layer.transform or Transform()
        # model_copy(update=) 不重跑 validator → 用 model_validate 重建（越界 fail-closed）
        new_transform = Transform.model_validate({**base.model_dump(), **patch})
    except ValueError as exc:
        raise EditorError("INVALID_PARAMETER", str(exc))
    # 静态 opacity 兼容旧渲染链（motion_instance.parameters.opacity 消费点保留）
    inst = dict(layer.motion_instance or {})
    if "opacity" in patch:
        params = dict(inst.get("parameters") or {})
        params["opacity"] = float(new_transform.opacity)
        params["transform"] = {"scale": new_transform.scale, "opacity": new_transform.opacity}
        inst["parameters"] = params
    scene.layers[idx] = layer.model_copy(update={"transform": new_transform,
                                                 "motion_instance": inst or None})
    layer_id = f"{scene.id}#{idx}"
    return [layer_id], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)


def _cmd_add_keyframe(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§72.2 层关键帧：payload={property, at, value, easing?} → Layer.keyframes。

    同 property 同 at 已存在 → 覆盖（编辑语义）；同 property 其他 at → 追加后按时间排序。
    """
    scene, idx, layer = _find_layer(ir, target_id)
    _ensure_layer_unlocked(layer)
    prop = payload.get("property") or ""
    if prop not in KEYFRAME_PROPERTIES:
        raise EditorError(
            "INVALID_PAYLOAD",
            f"add_keyframe: property {prop!r} 非法，应为 {sorted(KEYFRAME_PROPERTIES)}")
    try:
        kf = Keyframe(at=float(payload["at"]), value=payload["value"],
                      easing=payload.get("easing") or "linear")
    except KeyError:
        raise EditorError("INVALID_PAYLOAD", "add_keyframe 需 payload.at / payload.value")
    except ValueError as exc:
        raise EditorError("INVALID_PARAMETER", str(exc))
    tracks = list(layer.keyframes)
    track_idx = next((i for i, t in enumerate(tracks) if t.property == prop), None)
    if track_idx is None:
        tracks.append(KeyframeTrack(property=prop, keyframes=[kf]))
    else:
        existing = [k for k in tracks[track_idx].keyframes if abs(k.at - kf.at) > 1e-6]
        existing.append(kf)
        existing.sort(key=lambda k: k.at)
        tracks[track_idx] = KeyframeTrack(property=prop, keyframes=existing)
    scene.layers[idx] = layer.model_copy(update={"keyframes": tracks})
    layer_id = f"{scene.id}#{idx}"
    return [f"{layer_id}:{prop}"], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)


def _cmd_remove_keyframe(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§72.2 删关键帧：payload={property, at}。property 无剩余关键帧 → 整轨删除。"""
    scene, idx, layer = _find_layer(ir, target_id)
    _ensure_layer_unlocked(layer)
    prop = payload.get("property") or ""
    at = float(payload.get("at", -1))
    tracks = [t for t in layer.keyframes if t.property != prop]
    keep_track = next((t for t in layer.keyframes if t.property == prop), None)
    if keep_track is not None:
        remaining = [k for k in keep_track.keyframes if abs(k.at - at) > 1e-6]
        if remaining:
            tracks.append(KeyframeTrack(property=prop, keyframes=remaining))
    if keep_track is None:
        raise EditorError("INVALID_TARGET", f"层 {target_id} 无 {prop!r} 关键帧轨")
    scene.layers[idx] = layer.model_copy(update={"keyframes": tracks})
    layer_id = f"{scene.id}#{idx}"
    return [f"{layer_id}:{prop}"], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)


def _cmd_apply_mask(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§72.3 几何蒙版：payload={shape, x?, y?, width?, height?, feather?, inverted?, geometry?}。

    同层已有 mask → 覆盖（编辑语义）。Mask ≠ Matte：抠像走 matting.generate 能力。
    """
    scene, idx, layer = _find_layer(ir, target_id)
    _ensure_layer_unlocked(layer)
    data = {"id": payload.get("maskId") or f"mask-{scene.id}-{idx}"}
    for k in ("shape", "x", "y", "width", "height", "feather",
              "inverted", "opacity", "animated", "keyframes", "geometry"):
        if k in payload:
            data[k] = payload[k]
    try:
        mask = Mask.model_validate(data)
    except ValueError as exc:
        raise EditorError("INVALID_PARAMETER", str(exc))
    scene.layers[idx] = layer.model_copy(update={"mask": mask})
    layer_id = f"{scene.id}#{idx}"
    return [f"{layer_id}:mask"], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)


def _cmd_remove_mask(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§72.3 移除蒙版（无 mask 时诚实报错，不静默成功）。"""
    scene, idx, layer = _find_layer(ir, target_id)
    _ensure_layer_unlocked(layer)
    if layer.mask is None:
        raise EditorError("INVALID_TARGET", f"层 {target_id} 无蒙版")
    scene.layers[idx] = layer.model_copy(update={"mask": None})
    layer_id = f"{scene.id}#{idx}"
    return [f"{layer_id}:mask"], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)


# ---------------------------------------------------------------------------
# Frontend Unification §14/§15/§16：camera / pip / morph 执行链
# （§17 Visual Operations：共享 Coordinate Space / Command Bus / Revision，
#   只语义不同，不再各造系统）
# ---------------------------------------------------------------------------

#: §14 第一性原理最小完整集合：摄影机运动预设 → CameraKeyframe 映射。
#: Renderer 消费 orthographic 2D 投影（zoompan）；perspective 诚实 UNAVAILABLE。
CAMERA_PRESETS = {
    #   preset:      (start_pos, end_pos, start_target, end_target)  z=深度（1.0=中性）
    "push_in":   ((0.5, 0.5, 1.0), (0.5, 0.5, 0.6), (0.5, 0.5, 0.0), (0.5, 0.5, 0.0)),
    "pull_out":  ((0.5, 0.5, 0.6), (0.5, 0.5, 1.0), (0.5, 0.5, 0.0), (0.5, 0.5, 0.0)),
    "pan_left":  ((0.7, 0.5, 1.0), (0.3, 0.5, 1.0), (0.5, 0.5, 0.0), (0.5, 0.5, 0.0)),
    "pan_right": ((0.3, 0.5, 1.0), (0.7, 0.5, 1.0), (0.5, 0.5, 0.0), (0.5, 0.5, 0.0)),
    "tilt_up":   ((0.5, 0.6, 1.0), (0.5, 0.4, 1.0), (0.5, 0.5, 0.0), (0.5, 0.5, 0.0)),
    "tilt_down": ((0.5, 0.4, 1.0), (0.5, 0.6, 1.0), (0.5, 0.5, 0.0), (0.5, 0.5, 0.0)),
    "follow":    ((0.5, 0.5, 1.0), (0.5, 0.5, 1.0), (0.5, 0.5, 0.0), (0.5, 0.5, 0.0)),
}


def _cmd_apply_camera(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§14 camera.apply：摄影机运动 → Composition.camera_tracks（canonical）。

    payload={preset, strength?（0-1 强度，默认 1）, duration?} 或 {keyframes: [...]}。
    target_id=场景 id → 写该场景内嵌 camera（兼容访问层）+ Composition.camera_tracks。
    Scene.camera 只是访问层（§14），唯一正式状态是 Composition.camera_tracks。
    """
    si, scene = _find_scene(ir, target_id)
    strength = float(payload.get("strength", 1.0))
    if not 0.0 < strength <= 1.0:
        raise EditorError("INVALID_PARAMETER", f"camera.apply: strength 需 ∈(0,1]，got {strength}")
    track_id = f"cam-{scene.id}"
    raw_kfs = payload.get("keyframes")
    if raw_kfs:
        # 显式关键帧路径：逐个验证（fail-closed）
        try:
            kfs = [CameraKeyframe.model_validate(k) for k in raw_kfs]
        except (KeyError, ValueError) as exc:
            raise EditorError("INVALID_PARAMETER", f"camera.apply: keyframes 非法: {exc}")
    else:
        preset = str(payload.get("preset") or "")
        if preset not in CAMERA_PRESETS:
            raise EditorError(
                "INVALID_PAYLOAD",
                f"camera.apply: preset {preset!r} 非法，应为 {sorted(CAMERA_PRESETS)} "
                "或提供 keyframes 数组")
        (s_pos, e_pos, s_tgt, e_tgt) = CAMERA_PRESETS[preset]
        mid = [(a + (b - a) * strength) for a, b in zip(s_pos, e_pos)]
        kfs = [
            CameraKeyframe(at=0.0, position=list(s_pos), target=list(s_tgt)),
            CameraKeyframe(at=float(payload.get("duration", scene.duration)),
                           position=mid, target=list(e_tgt)),
        ]
    try:
        track = CameraTrack(id=track_id, keyframes=kfs,
                           follow_target=payload.get("followTarget"))
    except ValueError as exc:
        raise EditorError("INVALID_PARAMETER", str(exc))
    # canonical：Composition.camera_tracks（同 id 覆盖 = 编辑语义）
    tracks = [t for t in ir.camera_tracks if t.id != track_id]
    tracks.append(track)
    ir.camera_tracks = tracks
    # 兼容访问层：Scene.camera 指向同一 track 数据（非第二份状态——同源对象序列化）
    scene_dict = scene.model_dump()
    scene_dict["camera"] = track.model_dump()
    ir.scenes[si] = scene.model_validate(scene_dict)
    return [f"{scene.id}:camera"], [f"{scene.id}:render", f"{scene.id}:preview"]


#: §15 PiP fit 模式（contain=完整可见留边 / cover=填满裁切）
PIP_FIT_MODES = ("contain", "cover")


def _cmd_configure_pip(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§15 pip.configure：画中画 = 普通 Layer 的结构化属性（非另一系统）。

    payload={action: set|remove, source?, crop?{x,y,w,h}, fit?, radius?, shadow?,
             mask?, transform?{x,y,scale}, audioPolicy?}
    写入 Layer.motion_instance.parameters.pip（结构化子对象）+ Layer.transform。
    渲染层按 pip 子对象 overlay 合成（PiP 只是层的一种视觉呈现方式）。
    """
    scene, idx, layer = _find_layer(ir, target_id)
    _ensure_layer_unlocked(layer)
    action = payload.get("action") or "set"
    inst = dict(layer.motion_instance or {})
    params = dict(inst.get("parameters") or {})
    pip = dict(params.get("pip") or {})
    if action == "remove":
        if not pip:
            raise EditorError("INVALID_TARGET", f"层 {target_id} 无画中画配置")
        params.pop("pip", None)
        inst["parameters"] = params
        scene.layers[idx] = layer.model_copy(update={"motion_instance": inst or None})
        layer_id = f"{scene.id}#{idx}"
        return [f"{layer_id}:pip"], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)
    if action != "set":
        raise EditorError("INVALID_PAYLOAD", f"pip.configure: action 需 set|remove，got {action!r}")
    source = payload.get("source")
    if not (source or "").strip():
        raise EditorError("INVALID_PAYLOAD", "pip.configure: source 必填（素材 id 或路径）")
    fit = payload.get("fit") or "contain"
    if fit not in PIP_FIT_MODES:
        raise EditorError("INVALID_PARAMETER", f"pip.configure: fit 需 {PIP_FIT_MODES}，got {fit!r}")
    crop = payload.get("crop")
    if crop is not None:
        try:
            cx, cy, cw, ch = (float(crop[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError) as exc:
            raise EditorError("INVALID_PARAMETER", f"pip.configure: crop 需 {{x,y,w,h}} 数值: {exc}")
        if min(cx, cy, cw, ch) < 0:
            raise EditorError("INVALID_PARAMETER", "pip.configure: crop 各值需 ≥0")
    # 结构化 pip 子对象（全部字段显式，渲染层按此 overlay）
    pip.update({
        "source": source,
        "fit": fit,
        "crop": crop,
        "radius": float(payload.get("radius", 0.0)),
        "shadow": bool(payload.get("shadow", False)),
        "audio_policy": str(payload.get("audioPolicy") or "auto"),
    })
    params["pip"] = pip
    inst["parameters"] = params
    update: dict = {"motion_instance": inst or None}
    tf = payload.get("transform")
    if tf:
        try:
            base = layer.transform or Transform()
            update["transform"] = Transform.model_validate(
                {**base.model_dump(), **{k: tf[k] for k in ("x", "y", "scale", "opacity") if k in tf}})
        except ValueError as exc:
            raise EditorError("INVALID_PARAMETER", f"pip.configure: transform 非法: {exc}")
    if payload.get("mask"):
        try:
            update["mask"] = Mask.model_validate(payload["mask"])
        except ValueError as exc:
            raise EditorError("INVALID_PARAMETER", f"pip.configure: mask 非法: {exc}")
    scene.layers[idx] = layer.model_copy(update=update)
    layer_id = f"{scene.id}#{idx}"
    return [f"{layer_id}:pip"], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)


#: §16 自有 Graphic Block 间 morph 映射（源块 → 可形变目标块）。
#: 只支持系统自有 Block（结构化 GraphicProgram），不允许任意 HTML/DOM。
MORPHABLE_BLOCKS = {
    "ui.button": ("ui.arrow", "flow.node"),
    "ui.arrow": ("ui.button", "flow.node"),
    "flow.node": ("flow.arrow", "ui.card"),
    "ui.card": ("flow.node", "metric.card"),
    "metric.card": ("ui.card", "chart.node"),
    "chart.node": ("metric.card", "flow.node"),
}


def _cmd_apply_morph(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§16 visual.morph：自有 Graphic Block 间结构化形变。

    payload={fromBlockId, toBlockId, duration?, easing?}。
    落地为相邻场景间 ContinuityLink(type=morph, morph_path)（§72.5 复用：
    morph 是连续性的一种），morph_path 记录 from→to 映射。确定性编译
    （GraphicProgram→HTML），不引入任意 HTML/JS。
    """
    from_block = str(payload.get("fromBlockId") or "")
    to_block = str(payload.get("toBlockId") or "")
    if not from_block or not to_block:
        raise EditorError("INVALID_PAYLOAD", "apply_morph: fromBlockId/toBlockId 必填")
    if from_block == to_block:
        raise EditorError("INVALID_PAYLOAD", "apply_morph: from/to 不能同块")
    targets = MORPHABLE_BLOCKS.get(from_block)
    if targets is None or to_block not in targets:
        raise EditorError(
            "MORPH_UNSUPPORTED",
            f"apply_morph: {from_block!r} → {to_block!r} 不在自有 Block 形变映射内"
            f"（支持: {from_block!r} → {list(targets) if targets else []}）")
    # 相邻场景定位：targetId 可为 "<sceneA>-><sceneB>" 或缺省取相邻两场景
    if "->" in target_id:
        from_scene_id, to_scene_id = target_id.split("->", 1)
        _find_scene(ir, from_scene_id)
        _find_scene(ir, to_scene_id)
    else:
        if len(ir.scenes) < 2:
            raise EditorError("INVALID_TARGET", "apply_morph: 需两个场景（或 targetId='A->B'）")
        si = _find_scene(ir, target_id)[0] if target_id else 0
        if si >= len(ir.scenes) - 1:
            si = len(ir.scenes) - 2
        from_scene_id, to_scene_id = ir.scenes[si].id, ir.scenes[si + 1].id
    link_id = f"morph-{from_scene_id}-{to_scene_id}"
    morph_path = {
        "from_block": from_block, "to_block": to_block,
        "duration": float(payload.get("duration", 0.6)),
        "easing": str(payload.get("easing") or "ease_in_out"),
    }
    try:
        link = ContinuityLink(
            id=link_id, from_scene_id=from_scene_id, to_scene_id=to_scene_id,
            type=ContinuityType.morph, morph_path=json.dumps(morph_path),
        )
    except ValueError as exc:
        raise EditorError("INVALID_PARAMETER", str(exc))
    # 同 from→to 已有 morph 链 → 覆盖（编辑语义）
    links = [l for l in ir.continuity_links
             if not (l.from_scene_id == from_scene_id and l.to_scene_id == to_scene_id
                     and l.type == ContinuityType.morph)]
    links.append(link)
    ir.continuity_links = links
    return [f"{from_scene_id}->{to_scene_id}:morph"], [
        f"{to_scene_id}:render", f"{to_scene_id}:preview"]


# ---------------------------------------------------------------------------
# Frontend Unification §7/§32A：GFX Plan → Composition 归一
# 分层：Graphic Plan = proposal（前端可临时改）；Composition = 正式事实源。
# 禁止 GFX.plan → save 直通持久化——确认/进入工作台必须经 Command。
# ---------------------------------------------------------------------------

def _cmd_apply_graphic_plan(ir: CompositionIR, target_id: str,
                            payload: dict) -> Tuple[List[str], List[str]]:
    """§7 apply_graphic_plan：Creative Plan（proposal）确认 → Composition IR。

    payload={plan: {scenes: [...], content_type, style_id}}。
    每场景更新一个 graphic 层：block/layout/source 进结构化
    parameters.graphic 子对象。plan 场景数与 Composition 不一致 → fail-closed。
    """
    plan = payload.get("plan") or {}
    scenes = plan.get("scenes") or []
    if not scenes:
        raise EditorError("INVALID_PAYLOAD", "apply_graphic_plan: plan.scenes 为空")
    if len(scenes) != len(ir.scenes):
        raise EditorError(
            "INVALID_PAYLOAD",
            f"apply_graphic_plan: plan 有 {len(scenes)} 场景，Composition "
            f"有 {len(ir.scenes)}——先在创作页确认分镜数一致")
    changed: List[str] = []
    for i, sc in enumerate(scenes):
        scene = ir.scenes[i]
        graphic_params = {
            "block_id": sc.get("block_id") or "kb.title",
            "block_name": sc.get("block_name") or sc.get("block_id") or "",
            "layout": sc.get("layout") or "center",
            "source": sc.get("source") or "Generated Graphic",
            "motion_id": sc.get("motion_id") or "graphics.fade-up",
            "content_type": plan.get("content_type") or "knowledge",
            "style_id": plan.get("style_id") or "editorial",
            "narration": sc.get("narration") or sc.get("text") or scene.text,
            "visual_intent": sc.get("visual_intent") or "",
        }
        layer_data = {
            "type": "graphic", "duration": scene.duration,
            "motion_recipe_id": graphic_params["motion_id"],
            "motion_instance": {"parameters": {"graphic": graphic_params}},
        }
        gi = None
        for j, l in enumerate(scene.layers):
            t = (l.type.value if hasattr(l.type, "value") else str(l.type))
            if t == "graphic":
                gi = j
                break
        if gi is None:
            # 无 graphic 层：替换首个视觉层（plan 是该场景的视觉表达，非叠加）；
            # 场景只有视觉层时整体替换，保留 start 继承排布校验
            from video_director.models import Layer
            if scene.layers:
                layer_data["start"] = scene.layers[0].start
                scene.layers[0] = Layer.model_validate(layer_data)
            else:
                layer_data["start"] = 0.0
                scene.layers.append(Layer.model_validate(layer_data))
        else:
            layer_data["start"] = scene.layers[gi].start
            scene.layers[gi] = scene.layers[gi].model_validate({
                **scene.layers[gi].model_dump(), **layer_data,
                "duration": scene.duration})
        changed.append(f"{scene.id}:graphic")
    stale = [f"{s.id}:render" for s in ir.scenes] + [f"{s.id}:preview" for s in ir.scenes]
    return changed, stale


def _find_graphic_layer(scene) -> Tuple[int, object]:
    """定位场景内唯一 graphic 层（无 → INVALID_TARGET）。"""
    for j, l in enumerate(scene.layers):
        t = (l.type.value if hasattr(l.type, "value") else str(l.type))
        if t == "graphic":
            return j, l
    raise EditorError("INVALID_TARGET", f"场景 {scene.id} 无 graphic 层")


def _cmd_set_graphic_block(ir: CompositionIR, target_id: str,
                           payload: dict) -> Tuple[List[str], List[str]]:
    """§7 换视觉块：payload={blockId, blockName?, motionId?}（motion 跟随新块）。"""
    si, scene = _find_scene(ir, target_id)
    gi, layer = _find_graphic_layer(scene)
    _ensure_layer_unlocked(layer)
    block_id = payload.get("blockId") or ""
    if not block_id:
        raise EditorError("INVALID_PAYLOAD", "set_graphic_block: blockId 必填")
    inst = dict(layer.motion_instance or {})
    params = dict(inst.get("parameters") or {})
    graphic = dict(params.get("graphic") or {})
    graphic["block_id"] = block_id
    if payload.get("blockName"):
        graphic["block_name"] = payload["blockName"]
    if payload.get("motionId"):
        graphic["motion_id"] = payload["motionId"]
    params["graphic"] = graphic
    inst["parameters"] = params
    update = {"motion_instance": inst or None}
    if payload.get("motionId"):
        update["motion_recipe_id"] = payload["motionId"]
    scene.layers[gi] = layer.model_validate({**layer.model_dump(), **update})
    return [f"{scene.id}:graphic"], [f"{scene.id}:render", f"{scene.id}:preview"]


def _cmd_set_graphic_layout(ir: CompositionIR, target_id: str,
                            payload: dict) -> Tuple[List[str], List[str]]:
    """§7 换布局：payload={layout}。"""
    si, scene = _find_scene(ir, target_id)
    gi, layer = _find_graphic_layer(scene)
    _ensure_layer_unlocked(layer)
    layout = payload.get("layout") or ""
    if not layout:
        raise EditorError("INVALID_PAYLOAD", "set_graphic_layout: layout 必填")
    inst = dict(layer.motion_instance or {})
    params = dict(inst.get("parameters") or {})
    graphic = dict(params.get("graphic") or {})
    graphic["layout"] = layout
    params["graphic"] = graphic
    inst["parameters"] = params
    scene.layers[gi] = layer.model_validate({**layer.model_dump(),
                                             "motion_instance": inst or None})
    return [f"{scene.id}:graphic"], [f"{scene.id}:render", f"{scene.id}:preview"]


_SCENE_CONTINUITY_TYPES = tuple(t.value for t in ContinuityType)


def _cmd_set_transition_out(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """编辑器转场确认：只修改 Scene.transition_out，不重建场景/图层。"""
    if not target_id:
        raise EditorError("INVALID_TARGET", "set_transition_out 缺少场景 targetId")
    _, scene = _find_scene(ir, target_id)
    ttype = str(payload.get("type") or "cut")
    duration = float(payload.get("duration", 0.0) or 0.0)
    if duration < 0:
        raise EditorError("INVALID_PARAMETER", "转场时长不能为负")
    if ttype not in {"cut", "crossfade", "fade"}:
        raise EditorError("INVALID_PAYLOAD", f"不支持的转场类型 {ttype!r}")
    if ttype == "cut":
        duration = 0.0
    if duration > scene.duration / 2:
        raise EditorError("INVALID_PARAMETER", "转场时长不能超过当前场景时长的一半")
    from video_director.models import TransitionOut
    scene.transition_out = TransitionOut(type=ttype, duration=duration)
    return [f"{scene.id}:transition"], [f"{scene.id}:render", f"{scene.id}:preview"]


def _cmd_set_scene_continuity(ir: CompositionIR, target_id: str,
                               payload: dict) -> Tuple[List[str], List[str]]:
    """§7 连续性修改：payload={type, sourceLayerId?, targetLayerId?}。

    写 Scene.continuity_in（声明式）+ Composition.continuity_links（canonical）。
    carry 需 source/target layer id（ContinuityVerifier 规则 B 会 REJECT 缺失）。
    """
    si, scene = _find_scene(ir, target_id)
    if si == 0:
        raise EditorError("INVALID_TARGET", "首场景无「从上一镜延续」")
    prev = ir.scenes[si - 1]
    ctype = payload.get("type") or ""
    if ctype not in _SCENE_CONTINUITY_TYPES:
        raise EditorError(
            "INVALID_PAYLOAD",
            f"set_scene_continuity: type {ctype!r} 非法，应为 {list(_SCENE_CONTINUITY_TYPES)}")
    link_data = {
        "id": f"cont-{prev.id}-{scene.id}",
        "from_scene_id": prev.id, "to_scene_id": scene.id,
        "type": ctype,
    }
    if payload.get("sourceLayerId"):
        link_data["source_layer_id"] = payload["sourceLayerId"]
    if payload.get("targetLayerId"):
        link_data["target_layer_id"] = payload["targetLayerId"]
    try:
        link = ContinuityLink.model_validate(link_data)
    except ValueError as exc:
        raise EditorError("INVALID_PARAMETER", str(exc))
    scene_data = scene.model_dump()
    scene_data["continuity_in"] = link.model_dump()
    ir.scenes[si] = scene.model_validate(scene_data)
    links = [l for l in ir.continuity_links
             if not (l.from_scene_id == prev.id and l.to_scene_id == scene.id)]
    links.append(link)
    ir.continuity_links = links
    return [f"{prev.id}->{scene.id}:continuity"], [f"{scene.id}:preview"]
