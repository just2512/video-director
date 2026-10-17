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
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

from video_director import config
from video_director.project_lock import project_lock, validate_project_id
from video_director.models import (
    AudioLane,
    AudioTrack,
    AudioTrackType,
    AssetRef,
    CompositionIR,
    Layer,
    LayerType,
    SceneIR,
    TransitionOut,
    SubtitleStyle,
    WatermarkLayer,
    VisualOverlayClip,
    VisualTrack,
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
    """Atomically replace the persisted IR so a crash cannot leave partial JSON."""
    p = config.project_dir(project_id) / "plans" / "composition.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
    # Re-validate at the persistence boundary so any command that creates an AudioTrack
    # without explicitly setting physical lane ownership receives the canonical role lane.
    persisted_ir = CompositionIR.model_validate(ir.model_dump(mode="python"))
    serialized = json.dumps(json.loads(persisted_ir.model_dump_json()), ensure_ascii=False, indent=2)
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(p)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _composition_version_path(project_id: str) -> Path:
    return config.project_dir(project_id) / "plans" / "composition_version.json"


def _write_composition_version(project_id: str, version: int) -> None:
    """Persist a monotonic optimistic-concurrency token, including undo/redo."""
    path = _composition_version_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"version": int(version)}))
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def composition_version(project_id: str) -> int:
    """Return a monotonic version; content rollback must not cause an ABA version."""
    revision_floor = len(load_revisions(project_id)) + 1
    try:
        stored = json.loads(_composition_version_path(project_id).read_text(encoding="utf-8"))
        persisted = int(stored.get("version", revision_floor))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        persisted = revision_floor
    return max(revision_floor, persisted)


def _append_revision(project_id: str, revision: dict) -> None:
    p = _revisions_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(revision, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())




def _snapshot_for_revision(ir: "CompositionIR") -> dict:
    """§46：revision 内嵌 IR 快照（undo/redo 恢复点）。"""
    try:
        return json.loads(ir.model_dump_json())
    except Exception:  # noqa: BLE001 快照失败不阻断主命令
        return {}


def _undo_stack_path(project_id: str) -> Path:
    return config.project_dir(project_id) / "plans" / "undo_stack.json"


def _read_undo_state(project_id: str, revisions: Optional[List[dict]] = None) -> dict:
    """Read undo state while accepting the legacy list-only format.

    Reconcile abandoned redo entries from the append-only revision log so a crash
    after appending an edit but before updating undo_stack.json cannot resurrect them.
    """
    path = _undo_stack_path(project_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, json.JSONDecodeError):
        raw = []
    if isinstance(raw, list):
        undone = [str(item) for item in raw if item]
        abandoned = []
    elif isinstance(raw, dict):
        undone = [str(item) for item in raw.get("undone", []) if item]
        abandoned = [str(item) for item in raw.get("abandoned", []) if item]
    else:
        undone, abandoned = [], []

    history = revisions if revisions is not None else load_revisions(project_id)
    for revision in history:
        abandoned.extend(str(item) for item in revision.get("abandoned_redo_ids", []) if item)
    abandoned_set = set(abandoned)
    return {
        "undone": [item for item in undone if item not in abandoned_set][-200:],
        "abandoned": sorted(abandoned_set)[-400:],
    }


def _write_undo_state(project_id: str, state: dict) -> None:
    path = _undo_stack_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
    payload = {
        "undone": list(dict.fromkeys(state.get("undone", [])))[-200:],
        "abandoned": list(dict.fromkeys(state.get("abandoned", [])))[-400:],
    }
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _undo_redo(
    project_id: str, action: str, current_version: Optional[int] = None
) -> CommandResult:
    """Restore IR snapshots without allowing stale versions or abandoned redo branches."""
    import copy as _copy

    revisions = load_revisions(project_id)
    state = _read_undo_state(project_id, revisions)
    undone_ids = state["undone"]
    abandoned_ids = set(state["abandoned"])
    version = composition_version(project_id) if current_version is None else current_version

    if action == "undo":
        candidates = [
            revision for revision in revisions
            if revision.get("revision_id") not in undone_ids
            and revision.get("revision_id") not in abandoned_ids
            and revision.get("command") not in {"undo", "redo"}
        ]
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
        _write_composition_version(project_id, version + 1)
        save_composition(project_id, ir)
        state["undone"] = (undone_ids + [target.get("revision_id")])[-200:]
        _write_undo_state(project_id, state)
        return CommandResult(
            status="APPLIED", revision_id=f"undo-{target.get('revision_id')}",
            composition_version=version + 1,
            composition=json.loads(ir.model_dump_json()))

    if not undone_ids:
        return CommandResult(status="REJECTED", errors=[{
            "code": "NOTHING_TO_REDO", "message": "没有可恢复的编辑", "recoverable": True}])
    redo_id = undone_ids[-1]
    if redo_id in abandoned_ids:
        return CommandResult(status="REJECTED", errors=[{
            "code": "NOTHING_TO_REDO", "message": "该重做分支已被后续编辑替代",
            "recoverable": True}])
    target = next((revision for revision in revisions
                   if revision.get("revision_id") == redo_id), None)
    if target is None:
        return CommandResult(status="REJECTED", errors=[{
            "code": "REVISION_MISSING", "message": f"待恢复 revision {redo_id} 不存在",
            "recoverable": True}])
    snap = target.get("ir_after")
    if not snap:
        return CommandResult(status="REJECTED", errors=[{
            "code": "REDO_UNAVAILABLE",
            "message": f"revision {redo_id} 无快照，无法恢复", "recoverable": True}])
    try:
        ir = CompositionIR(**_copy.deepcopy(snap))
    except Exception as exc:
        return CommandResult(status="REJECTED", errors=[{
            "code": "SNAPSHOT_INVALID", "message": f"快照恢复失败: {exc}", "recoverable": True}])
    _write_composition_version(project_id, version + 1)
    save_composition(project_id, ir)
    state["undone"] = undone_ids[:-1]
    _write_undo_state(project_id, state)
    return CommandResult(
        status="APPLIED", revision_id=f"redo-{redo_id}",
        composition_version=version + 1,
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
#: 统一编辑切点契约：入口集中 snap 的例外名单。payload 的 at/start 不属于
#: 项目时间轴坐标系（或语义上必须保留原值）的命令在此登记。目前为空——
#: 所有用 at/start 的命令都是项目时间轴语义。
_NO_FRAME_SNAP_COMMANDS: frozenset = frozenset()

SUPPORTED_COMMANDS = (
    "select",                    # 无副作用（前端选择态，服务端不记 revision）
    "trim",                      # layer 时长调整（视频层 source 区间变化）
    "split_layer",               # 在播放头切分当前视觉片段
    "add_edit_all_tracks",         # FilmCraft-style Add Edit：一次事务切开播放头下所有可安全编辑的轨道
    "reorder_layer",             # 场景内部视觉片段重排
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
    "promote_layer_to_visual_track",  # 将主画面视频提升为可独立编辑的全局画面轨道片段
    "expand_main_timeline_to_visual_track",  # 一次性把全部兼容主画面片段展开为全局可编辑时间线
    "add_visual_overlay",
    "move_visual_overlay",
    "trim_visual_overlay",
    "split_visual_overlay",
    "set_visual_overlay",
    "separate_visual_overlay_source_audio",  # detach source sound into an editable original-audio clip
    "remove_visual_overlay",
    "paste_visual_overlay",       # 复制/粘贴独立画面片段
    "remove_audio_clip",          # 删除独立音频片段
    "paste_audio_clip",           # 复制/粘贴独立音频片段
    "add_visual_track",
    "rename_visual_track",
    "reorder_visual_track",
    "remove_visual_track",
    "add_audio_track",           # 创建独立、可命名的音频时间线轨道
    "rename_audio_track",         # 重命名音轨
    "remove_audio_track",         # 仅允许删除空音轨
    "set_track_locked",            # 音频/画面物理轨道锁定
    "add_audio_clip",            # 兼容旧 SFX 入口
    "add_audio_asset_clip",       # 把可信音频库中的素材加入任意物理音轨
    "move_audio_clip",           # §17 独立音频片段位置编辑
    "trim_audio_clip",           # §17 独立音频片段源区间裁剪
    "split_audio_clip",           # 在播放头切分单条音频片段，保留连续源区间
    "set_audio_clip_properties", # §17 独立音频片段混音/淡入淡出
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
    "move_watermark",             # 移动覆盖层时间位置（统一时间线）
    "trim_watermark",             # 修剪覆盖层显示区间（统一时间线）
    "remove_watermark",          # 移除水印（§19）
    "set_subtitle_style",        # 字幕样式（§32 SubtitleStyle 入 IR）
    "set_subtitle_track",        # 字幕轨（§31/§33 SubtitleTrack：句级/逐词 cues 入 IR）,
    # ---- 视觉语义归一（§72 六 Contract 可执行化）----
    "set_visual_transform",       # 结构化层变换（x/y/scale/rotation/opacity/anchor）
    "add_keyframe",               # 层属性关键帧（§23 KeyframeTrack）
    "remove_keyframe",            # 删关键帧
    "join_through_edits",         # 愈合同源连续切口（filmcraft Join Through Edits）
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
    "set_transition",             # 别名（#8 契约名，映射到 set_transition_out）
    "set_scene_intent",            # 采用创作候选后的 Scene 视觉意图
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
    warnings: List[str] = field(default_factory=list)
    errors: List[dict] = field(default_factory=list)
    composition: Optional[dict] = None  # 更新后的 IR（前端重渲染时间线用）


def _sync_candidate_views(project_id: str, ir: CompositionIR) -> None:
    """把 IR 中的 candidate selection 同步到派生计划文件；失败不回滚已成功的 IR Command。"""
    import json as _json

    base = config.project_dir(project_id)
    candidate_path = base / "plans" / "creative_candidates.json"
    if not candidate_path.is_file():
        return
    try:
        snapshot = _json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        logger.warning("candidate cache unreadable; IR selection remains authoritative")
        return

    selected_by_scene = {
        sc.id: getattr(sc, "selected_candidate_id", None)
        for sc in ir.scenes
    }
    for row in snapshot.get("scenes") or []:
        sid = row.get("scene_id")
        selected = selected_by_scene.get(sid)
        if selected:
            row["selected_candidate_id"] = selected
            for candidate in row.get("candidates") or []:
                candidate["selected"] = candidate.get("id") == selected

    tmp = candidate_path.with_suffix(".tmp")
    try:
        tmp.write_text(_json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(candidate_path)
    except OSError:
        logger.warning("candidate cache write failed; IR selection remains authoritative")

    intent_path = base / "plans" / "material_intents.json"
    if not intent_path.is_file():
        return
    try:
        intents = _json.loads(intent_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return

    for row in intents.get("scenes") or []:
        sid = row.get("scene_id")
        selected = selected_by_scene.get(sid)
        if not selected:
            continue
        row["selected_candidate_id"] = selected
        candidate_row = next(
            (x for x in snapshot.get("scenes") or [] if x.get("scene_id") == sid),
            None,
        )
        candidates = (candidate_row or {}).get("candidates") or []
        candidate = next((x for x in candidates if x.get("id") == selected), None)
        if candidate:
            row["search_terms"] = list(candidate.get("search_terms") or [])
            row["search_order"] = list(candidate.get("search_terms") or [])
            row["source_preference"] = candidate.get("source_preference") or row.get("source_preference")
            row["selection_reason"] = f"用户采用创作候选：{candidate.get('title') or selected}"

    tmp2 = intent_path.with_suffix(".tmp")
    try:
        tmp2.write_text(_json.dumps(intents, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp2.replace(intent_path)
    except OSError:
        logger.warning("material intent cache write failed; IR selection remains authoritative")

    plan_path = base / "plans" / "director_scene_plan.json"
    if plan_path.is_file():
        try:
            plan = _json.loads(plan_path.read_text(encoding="utf-8"))
            for row in plan.get("scenes") or []:
                sid = row.get("id") or row.get("scene_id")
                selected = selected_by_scene.get(sid)
                if not selected:
                    continue
                row["selected_candidate_id"] = selected
                scene = next((sc for sc in ir.scenes if sc.id == sid), None)
                if scene:
                    row["visual_intent"] = scene.visual_intent
            tmp3 = plan_path.with_suffix(".tmp")
            tmp3.write_text(_json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp3.replace(plan_path)
        except (OSError, _json.JSONDecodeError):
            logger.warning("scene plan cache write failed; IR selection remains authoritative")


def _execute_command_unlocked(project_id: str, command: dict) -> CommandResult:
    """执行 Editor Command（校验 → 修改 → revision 落盘 → 返回结果）。

    expectedVersion 与服务端当前版本不一致 → CONFLICT（§27/§28）。
    AI/Gallery 的"应用"最终都走这里——前端不直接改 Composition。
    """
    cmd_name = command.get("command")
    if cmd_name not in SUPPORTED_COMMANDS:
        return CommandResult(
            status="REJECTED",
            composition_version=composition_version(project_id),
            errors=[{"code": "COMMAND_NOT_SUPPORTED", "message": f"命令 {cmd_name!r} 不支持"}],
        )
    if cmd_name == "select":
        # selection 是前端 transient 态（§35），不产生 revision
        return CommandResult(status="APPLIED", composition_version=composition_version(project_id))
    # 统一编辑切点契约（filmcraft 整数 Tick 精神的防御层）：所有携带时间轴
    # payload 的命令在入口先 snap 到项目帧钟。各命令内部的 _project_frame_time
    # 保留（幂等）作为语义兜底；此集中层消灭"新命令忘 snap"的漏网类。
    # 素材源时间（sourceIn/sourceOut）不在此 snap——源时间轴与项目帧钟是
    # 两个坐标系，由各命令自行决定源窗口换算。
    payload_in = command.get("payload")
    if isinstance(payload_in, dict) and cmd_name not in _NO_FRAME_SNAP_COMMANDS:
        needs_snap = any(
            isinstance(payload_in.get(key), (int, float))
            and not isinstance(payload_in.get(key), bool)
            and math.isfinite(payload_in.get(key))
            for key in ("at", "start")
        )
        if needs_snap:
            ir_probe = load_composition(project_id)
            new_payload = dict(payload_in)
            for key in ("at", "start"):
                raw_time = new_payload.get(key)
                if (
                    isinstance(raw_time, (int, float)) and not isinstance(raw_time, bool)
                    and math.isfinite(raw_time)
                ):
                    new_payload[key] = _project_frame_time(ir_probe, float(raw_time))
            command["payload"] = new_payload
    expected = command.get("expectedVersion")
    current = composition_version(project_id)
    if expected is not None:
        # int(True) == 1 and int(1.5) == 1 in Python: coercing a malformed
        # optimistic-lock token can let a stale edit pass the version gate.
        if isinstance(expected, bool) or (
            isinstance(expected, float)
            and (not math.isfinite(expected) or not expected.is_integer())
        ):
            return CommandResult(
                status="REJECTED",
                composition_version=current,
                errors=[{
                    "code": "INVALID_PAYLOAD",
                    "message": "expectedVersion 必须是整数；不接受布尔值、非有限数或小数",
                    "recoverable": True,
                }],
            )
        try:
            expected_version = int(expected)
        except (TypeError, ValueError, OverflowError):
            return CommandResult(
                status="REJECTED",
                composition_version=current,
                errors=[{
                    "code": "INVALID_PAYLOAD",
                    "message": "expectedVersion 必须是整数",
                    "recoverable": True,
                }],
            )
        if expected_version != current:
            return CommandResult(
                status="REJECTED",
                composition_version=current,
                errors=[{
                    "code": "CONFLICT",
                    "message": f"版本冲突：客户端 {expected} != 服务端 {current}，请刷新后重试",
                    "recoverable": True,
                }],
            )

    if cmd_name in ("undo", "redo"):
        return _undo_redo(project_id, cmd_name, current_version=current)

    ir = load_composition(project_id)
    before_ir = ir.model_copy(deep=True)  # §46 undo 快照基线
    target_id = command.get("targetId") or ""
    payload = command.get("payload") or {}

    changed: List[str] = []
    stale: List[str] = []
    command_warnings: List[str] = []
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
        elif cmd_name == "split_layer":
            changed, stale = _cmd_split_layer(ir, target_id, payload)
        elif cmd_name == "add_edit_all_tracks":
            changed, stale, command_warnings = _cmd_add_edit_all_tracks(ir, target_id, payload)
        elif cmd_name == "reorder_layer":
            changed, stale = _cmd_reorder_layer(ir, target_id, payload)
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
        elif cmd_name == "promote_layer_to_visual_track":
            changed, stale = _cmd_promote_layer_to_visual_track(ir, target_id, payload, project_id)
        elif cmd_name == "expand_main_timeline_to_visual_track":
            changed, stale = _cmd_expand_main_timeline_to_visual_track(ir, target_id, payload, project_id)
        elif cmd_name == "add_visual_overlay":
            changed, stale = _cmd_add_visual_overlay(ir, target_id, payload, project_id)
        elif cmd_name == "move_visual_overlay":
            changed, stale = _cmd_move_visual_overlay(ir, target_id, payload)
        elif cmd_name == "trim_visual_overlay":
            changed, stale = _cmd_trim_visual_overlay(ir, target_id, payload)
        elif cmd_name == "split_visual_overlay":
            changed, stale = _cmd_split_visual_overlay(ir, target_id, payload)
        elif cmd_name == "set_visual_overlay":
            changed, stale = _cmd_set_visual_overlay(ir, target_id, payload)
        elif cmd_name == "separate_visual_overlay_source_audio":
            changed, stale = _cmd_separate_visual_overlay_source_audio(ir, target_id, payload, project_id)
        elif cmd_name == "remove_visual_overlay":
            changed, stale = _cmd_remove_visual_overlay(ir, target_id, payload)
        elif cmd_name == "paste_visual_overlay":
            changed, stale = _cmd_paste_visual_overlay(ir, target_id, payload, project_id)
        elif cmd_name == "add_visual_track":
            changed, stale = _cmd_add_visual_track(ir, target_id, payload)
        elif cmd_name == "rename_visual_track":
            changed, stale = _cmd_rename_visual_track(ir, target_id, payload)
        elif cmd_name == "reorder_visual_track":
            changed, stale = _cmd_reorder_visual_track(ir, target_id, payload)
        elif cmd_name == "remove_visual_track":
            changed, stale = _cmd_remove_visual_track(ir, target_id, payload)
        elif cmd_name == "add_audio_track":
            changed, stale = _cmd_add_audio_track(ir, target_id, payload)
        elif cmd_name == "rename_audio_track":
            changed, stale = _cmd_rename_audio_track(ir, target_id, payload)
        elif cmd_name == "remove_audio_track":
            changed, stale = _cmd_remove_audio_track(ir, target_id, payload)
        elif cmd_name == "set_track_locked":
            changed, stale = _cmd_set_track_locked(ir, target_id, payload)
        elif cmd_name == "add_audio_clip":
            changed, stale = _cmd_add_audio_clip(ir, target_id, payload)
        elif cmd_name == "add_audio_asset_clip":
            changed, stale = _cmd_add_audio_asset_clip(ir, target_id, payload)
        elif cmd_name == "move_audio_clip":
            changed, stale = _cmd_move_audio_clip(ir, target_id, payload)
        elif cmd_name == "trim_audio_clip":
            changed, stale = _cmd_trim_audio_clip(ir, target_id, payload)
        elif cmd_name == "split_audio_clip":
            changed, stale = _cmd_split_audio_clip(ir, target_id, payload)
        elif cmd_name == "remove_audio_clip":
            changed, stale = _cmd_remove_audio_clip(ir, target_id, payload)
        elif cmd_name == "paste_audio_clip":
            changed, stale = _cmd_paste_audio_clip(ir, target_id, payload, project_id)
        elif cmd_name == "set_audio_clip_properties":
            changed, stale = _cmd_set_audio_clip_properties(ir, target_id, payload)
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
        elif cmd_name == "move_watermark":
            changed, stale = _cmd_move_watermark(ir, target_id, payload)
        elif cmd_name == "trim_watermark":
            changed, stale = _cmd_trim_watermark(ir, target_id, payload)
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
        elif cmd_name == "join_through_edits":
            changed, stale = _cmd_join_through_edits(ir, target_id, payload)
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
        elif cmd_name == "set_transition":
            changed, stale = _cmd_set_transition_out(ir, target_id, payload)
        elif cmd_name == "set_scene_intent":
            changed, stale = _cmd_set_scene_intent(ir, target_id, payload, project_id)
    except EditorError as exc:
        return CommandResult(status="REJECTED", composition_version=current, errors=[{
            "code": exc.code, "message": str(exc), "recoverable": exc.recoverable,
        }])
    except CompileError as exc:
        return CommandResult(status="REJECTED", composition_version=current, errors=[{
            "code": exc.code, "message": str(exc), "recoverable": True,
        }])

    for c in changed:
        if c in reused:
            reused.remove(c)

    # 保存 + revision 记录；撤销后产生的新编辑会废弃已撤销的 redo 分支。
    undo_state = _read_undo_state(project_id)
    abandoned_redo_ids = list(undo_state["undone"])
    _write_composition_version(project_id, current + 1)
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
        "warnings": command_warnings,
        "author": command.get("author", "user"),
        "created_at": time.time(),
        "abandoned_redo_ids": abandoned_redo_ids,
        "ir_before": _snapshot_for_revision(before_ir),
        "ir_after": _snapshot_for_revision(ir),
    }
    _append_revision(project_id, revision)
    if abandoned_redo_ids:
        undo_state["undone"] = []
        undo_state["abandoned"] = sorted(set(undo_state["abandoned"]) | set(abandoned_redo_ids))
        _write_undo_state(project_id, undo_state)
    try:
        from video_director import artifact_graph
        artifact_graph.mark_stale(project_id, stale)
    except Exception as exc:  # noqa: BLE001 失效台账是旁路，不能阻断已成功的编辑命令
        logger.debug(f"artifact manifest stale 标记失败（忽略）: {exc}")
    if cmd_name == "set_scene_intent":
        _sync_candidate_views(project_id, ir)
    # Record Style DNA only after the edit passed validation and was persisted.
    # Rejected/conflicted commands must not leave a false "used" event.
    _record_behavior_if_applicable(cmd_name, command)
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
        warnings=command_warnings,
        composition=json.loads(ir.model_dump_json()),
    )


def execute_command(project_id: str, command: dict) -> CommandResult:
    """Serialize version check, composition read/write and revision append."""
    try:
        validate_project_id(project_id)
    except ValueError as exc:
        return CommandResult(status="REJECTED", errors=[{
            "code": "INVALID_PROJECT_ID", "message": str(exc), "recoverable": False,
        }])
    with project_lock(project_id):
        return _execute_command_unlocked(project_id, command)


# ---------------------------------------------------------------------------
# 各命令实现（全部返回新对象风格：改的是 load_composition 出来的内存 IR，
# execute_command 统一 save）
# ---------------------------------------------------------------------------



def _cmd_set_scene_intent(
    ir: CompositionIR,
    target_id: str,
    payload: dict,
    project_id: str,
) -> Tuple[List[str], List[str]]:
    """采用一个 Creative Candidate：改 Scene 视觉意图；选择状态随 IR 一起原子落盘。"""
    _, scene = _find_scene(ir, target_id)
    candidate_id = str(payload.get("candidateId") or "")
    visual_intent = str(payload.get("visualIntent") or "").strip()
    if not candidate_id or not visual_intent:
        raise EditorError("INVALID_PAYLOAD", "采用候选缺 candidateId 或 visualIntent")

    import json as _json
    from video_director import creative_candidates

    candidate_path = config.project_dir(project_id) / "plans" / "creative_candidates.json"
    if not candidate_path.is_file():
        raise EditorError("CANDIDATE_NOT_FOUND", "当前项目没有创作候选集")
    try:
        snapshot = _json.loads(candidate_path.read_text(encoding="utf-8"))
    except (_json.JSONDecodeError, OSError) as exc:
        raise EditorError("CANDIDATE_STATE_INVALID", "创作候选集无法读取") from exc

    candidate = creative_candidates.find_candidate(snapshot, scene.id, candidate_id)
    if candidate is None:
        raise EditorError("CANDIDATE_NOT_FOUND", f"候选 {candidate_id} 不存在")

    # 只有这里修改 IR；save_composition / revision 会作为同一 Command Transaction 落盘。
    scene.visual_intent = visual_intent
    scene.selected_candidate_id = candidate_id

    return [
        scene.id
    ], [
        f"{scene.id}:render",
        f"{scene.id}:preview",
        "creative_candidates",
        "material_intents",
    ]

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


def _find_layer_with_scene_index(ir: CompositionIR, target_id: str) -> Tuple[int, SceneIR, int, Layer]:
    """Like _find_layer, returning scene and layer indexes for stable edits."""
    if "#" not in target_id:
        raise EditorError("INVALID_TARGET", f"layer targetId 需为 '<scene>#<index>' 格式: {target_id}")
    scene_id, index_text = target_id.rsplit("#", 1)
    si = next((i for i, item in enumerate(ir.scenes) if item.id == scene_id), -1)
    if si < 0:
        raise EditorError("NOT_FOUND", f"场景 {scene_id} 不存在", recoverable=False)
    try:
        idx = int(index_text)
    except ValueError as exc:
        raise EditorError("INVALID_TARGET", f"layer index 非数值: {target_id}") from exc
    scene = ir.scenes[si]
    if idx < 0 or idx >= len(scene.layers):
        raise EditorError("NOT_FOUND", f"层 {target_id} 不存在", recoverable=False)
    return si, scene, idx, scene.layers[idx]


def _cmd_reorder_layer(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Reorder sequential visual segments inside their current scene.

    SceneIR currently requires layers to cover the scene contiguously. Cross-scene
    drag is rejected in the UI instead of silently invoking the scene-reorder command.
    """
    si, scene, idx, layer = _find_layer_with_scene_index(ir, target_id)
    _ensure_layer_unlocked(layer)
    raw_to_index = payload.get("toIndex", payload.get("targetIndex"))
    if isinstance(raw_to_index, bool):
        raise EditorError("INVALID_PAYLOAD", "reorder_layer 的 toIndex 不能是布尔值")
    if isinstance(raw_to_index, float) and not math.isfinite(raw_to_index):
        raise EditorError("INVALID_PAYLOAD", "reorder_layer 的 toIndex 必须是有限整数")
    if isinstance(raw_to_index, float) and not raw_to_index.is_integer():
        raise EditorError("INVALID_PAYLOAD", "reorder_layer 的 toIndex 必须是整数")
    try:
        to_index = int(raw_to_index)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PAYLOAD", "reorder_layer 需要整数 toIndex") from exc
    if to_index < 0 or to_index >= len(scene.layers):
        raise EditorError("INVALID_PAYLOAD", f"图层目标位置 {to_index} 越界")
    if to_index == idx:
        raise EditorError("NOOP", "图层已经位于目标位置")
    layers = list(scene.layers)
    layers.insert(to_index, layers.pop(idx))
    cursor = 0.0
    normalized = []
    for item in layers:
        normalized.append(item.model_copy(update={"start": round(cursor, 6)}))
        cursor += float(item.duration)
    scene_data = scene.model_dump()
    scene_data["layers"] = [item.model_dump() for item in normalized]
    ir.scenes[si] = SceneIR(**scene_data)
    # A ripple reorder changes linked source-audio timing as well as picture order.
    # Do not run the scene-level narration reflow: this command did not change scenes.
    _resync_audio_to_scenes(ir, sync_narration=False, match_original_by_source=True)
    stale = _stale_of_scene(project_id="", scene_id=scene.id)
    stale.extend(["project:audio:render", "project:audio-tracks", "project:preview"])
    return [f"{scene.id}#{to_index}"], list(dict.fromkeys(stale))


def _cmd_add_edit_all_tracks(
    ir: CompositionIR, target_id: str, payload: dict,
) -> Tuple[List[str], List[str], List[str]]:
    """FilmCraft-style Add Edit at the playhead, committed as one editor revision.

    Split every ordinary visual/audio/overlay/watermark clip that safely spans the
    requested time. Unsupported cases are reported as warnings rather than being
    silently ignored or having keyframes/source timing destroyed. Subtitle cues are
    semantic anchors, so a cue crossing the cut is retained and reported, not text-split.
    """
    if target_id not in ("", "timeline", "project"):
        raise EditorError("INVALID_TARGET", "add_edit_all_tracks 的 targetId 必须是 timeline")
    raw_at = payload.get("at")
    if isinstance(raw_at, bool):
        raise EditorError("INVALID_PAYLOAD", "播放头时间不能是布尔值")
    try:
        at = float(raw_at)
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PAYLOAD", "add_edit_all_tracks 需要数字 at") from exc
    project_duration = float(ir.project.duration)
    if not math.isfinite(at) or at < 0 or at > project_duration:
        raise EditorError("INVALID_PARAMETER", "播放头时间超出项目范围；项目未修改")
    # One exact frame boundary must be shared by all split handlers below.
    # Otherwise the base picture, overlays, audio clips, and watermarks can
    # land on different times for a sub-frame playhead position.
    at = _project_frame_time(ir, at)
    if at > project_duration + 1e-6:
        raise EditorError("INVALID_PARAMETER", "播放头对齐到项目帧后超出项目范围；项目未修改")

    changed: List[str] = []
    stale: List[str] = []
    warnings: List[str] = []
    min_video_piece = _project_frame_min_duration(ir)

    # Main picture: SceneIR still owns the base picture edit. A valid scene has
    # contiguous layers, therefore at most one base layer per scene spans this cut.
    base_candidates = []
    for scene in ir.scenes:
        for index, layer in enumerate(scene.layers):
            start = float(scene.start) + float(layer.start)
            end = start + float(layer.duration)
            if start + min_video_piece < at < end - min_video_piece:
                base_candidates.append((scene.id, index, layer.type.value))
                break
    for scene_id, index, layer_type in base_candidates:
        try:
            c, st = _cmd_split_layer(ir, f"{scene_id}#{index}", {"at": at})
            changed.extend(c)
            stale.extend(st)
        except EditorError as exc:
            warnings.append(f"主画面 {scene_id}（{layer_type}）：{str(exc)}")

    # Global visual overlays: process indices back-to-front because a split inserts
    # a second item. This is the same order-sensitive mutation discipline used by a
    # real timeline command and prevents later clips from being addressed by stale indices.
    overlay_candidates = [
        i for i, clip in enumerate(ir.visual_overlays)
        if float(clip.start) + 0.05 < at < float(clip.start) + float(clip.duration) - 0.05
    ]
    for index in reversed(overlay_candidates):
        clip_id = ir.visual_overlays[index].id
        track_id = ir.visual_overlays[index].track_id
        try:
            c, st = _cmd_split_visual_overlay(ir, f"overlay#{index}", {"at": at})
            changed.extend(c)
            stale.extend(st)
        except EditorError as exc:
            warnings.append(f"画面叠加轨 {track_id} / {clip_id}：{str(exc)}")

    # Audio: narration, looping BGM/ambience, and automated clips are deliberately
    # fail-closed in _cmd_split_audio_clip. Report each unsupported clip and continue
    # other tracks in this same single atomic revision.
    audio_candidates = [
        i for i, clip in enumerate(ir.audio)
        if float(clip.start) + 0.05 < at < float(clip.start) + float(clip.duration) - 0.05
    ]
    lane_names = {lane.id: lane.name for lane in ir.audio_tracks}
    for index in reversed(audio_candidates):
        clip = ir.audio[index]
        clip_id = clip.id or f"{clip.type.value}#{index}"
        lane_name = lane_names.get(clip.track_id or "", clip.type.value)
        try:
            c, st = _cmd_split_audio_clip(ir, f"audio#{index}", {"at": at})
            changed.extend(c)
            stale.extend(st)
        except EditorError as exc:
            warnings.append(f"音频轨 {lane_name} / {clip_id}：{str(exc)}")

    # Timed watermarks use the same timeline interval model. An unlimited watermark
    # is bounded by the project duration for this edit; its two halves remain adjacent.
    watermark_candidates = []
    for i, watermark in enumerate(ir.watermarks):
        start = float(watermark.start)
        end = project_duration if watermark.duration is None else min(
            project_duration, start + float(watermark.duration)
        )
        if start + 0.05 < at < end - 0.05:
            watermark_candidates.append((i, start, end))
    for index, start, end in reversed(watermark_candidates):
        watermark = ir.watermarks[index]
        left_duration = at - start
        right_duration = end - at
        ir.watermarks[index] = watermark.model_copy(update={"duration": left_duration})
        right = watermark.model_copy(update={
            "id": f"wm-{uuid.uuid4().hex[:12]}",
            "start": at,
            "duration": right_duration,
        })
        ir.watermarks.insert(index + 1, right)
        changed.extend([f"watermark:{watermark.id}", f"watermark:{right.id}"])
        stale.extend([
            f"watermark:{watermark.id}:render",
            f"watermark:{right.id}:render",
            "project:watermarks:render",
            "project:preview",
        ])

    # Subtitle cues / word timing are meaning-bearing anchors, not ordinary picture
    # clips. Never split a word/caption's text just because a visual edit was added.
    subtitle_track = ir.subtitle_track
    if subtitle_track and any(float(cue.start) < at < float(cue.end) for cue in subtitle_track.cues):
        warnings.append("字幕/字级时间锚点：有字幕片段跨越播放头，已保留原 cue，未拆分文字或时间锚点")

    changed = list(dict.fromkeys(changed))
    stale = list(dict.fromkeys(stale))
    warnings = list(dict.fromkeys(warnings))
    if not changed:
        message = "播放头位置没有可安全切分的剪辑，项目未修改。"
        if warnings:
            message += " 未切分原因：" + "；".join(warnings[:6])
        raise EditorError("NO_SPLITTABLE_CLIP_AT_PLAYHEAD", message)
    return changed, stale, warnings


def _cmd_split_layer(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Split one visual segment at an absolute timeline time, preserving source spans."""
    si, scene, idx, layer = _find_layer_with_scene_index(ir, target_id)
    _ensure_layer_unlocked(layer)
    raw_at = payload.get("at")
    if isinstance(raw_at, bool):
        raise EditorError("INVALID_PAYLOAD", "split_layer 的 at 不能是布尔值")
    try:
        at = float(raw_at)
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PAYLOAD", "split_layer 需要播放头绝对时间 at") from exc
    if not math.isfinite(at):
        raise EditorError("INVALID_PAYLOAD", "切分时间必须是有限数字")
    at = _project_frame_time(ir, at)
    layer_start = float(scene.start) + float(layer.start)
    offset = at - layer_start
    min_piece = _project_frame_min_duration(ir)
    if offset <= min_piece or layer.duration - offset <= min_piece:
        raise EditorError(
            "SPLIT_OUTSIDE_CLIP",
            f"播放头必须位于图层内部至少 {min_piece:.3f}s；当前片段 {layer_start:.3f}–{layer_start + layer.duration:.3f}s",
        )
    if layer.type == LayerType.video:
        source_cut = float(layer.source_in) + offset * float(layer.speed)
        if layer.source_out is None or not (layer.source_in < source_cut < layer.source_out):
            raise EditorError("INVALID_SOURCE_RANGE", "切分点超出视频素材源区间")
        left = layer.model_copy(update={"duration": offset, "source_out": source_cut})
        right = layer.model_copy(update={
            "start": layer.start + offset,
            "duration": layer.duration - offset,
            "source_in": source_cut,
        })
    else:
        left = layer.model_copy(update={"duration": offset})
        right = layer.model_copy(update={
            "start": layer.start + offset,
            "duration": layer.duration - offset,
        })
    layers = list(scene.layers)
    layers[idx:idx + 1] = [left, right]
    cursor = 0.0
    normalized = []
    for item in layers:
        normalized.append(item.model_copy(update={"start": round(cursor, 6)}))
        cursor += float(item.duration)
    scene_data = scene.model_dump()
    scene_data["layers"] = [item.model_dump() for item in normalized]
    ir.scenes[si] = SceneIR(**scene_data)
    return [f"{scene.id}#{idx}", f"{scene.id}#{idx + 1}"], _stale_of_scene(project_id="", scene_id=scene.id)


def _cmd_join_through_edits(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """愈合切口（filmcraft Join Through Edits 的 video-director 版）。

    split/add_edit_all_tracks 在同源素材内部切开时，两侧片段在素材时间轴上
    首尾相接（左段 source_out == 右段 source_in）。此命令把这样一对相邻段
    合回一段；左段属性胜出（filmcraft 语义），右段时间并入左段。

    targetId 支持 "S01#3"（主画面层）、"visual-overlay:<id>"、
    "audio#<index>"。切口容差 = 2ms + speed 取整补偿（filmcraft MEDIA_SLACK）。
    """
    if target_id.startswith("overlay#") or target_id.startswith("visual-overlay:"):
        # split_visual_overlay 用 overlay#<index>；前端选中态用 visual-overlay:<id>。
        # 两种都收——join 与 split 的 targetId 合同保持一致。
        if target_id.startswith("overlay#"):
            index = _visual_overlay_index(ir, target_id)
        else:
            overlay_id = target_id.split(":", 1)[1]
            matches = [i for i, c in enumerate(ir.visual_overlays) if c.id == overlay_id]
            if not matches:
                raise EditorError("NOT_FOUND", f"画面片段 {overlay_id!r} 不存在", recoverable=False)
            index = matches[0]
        clip = ir.visual_overlays[index]
        _ensure_visual_track_unlocked(ir, str(clip.track_id or "overlay-main"))
        track_id = str(clip.track_id or "overlay-main")
        same_track = sorted(
            [c for c in ir.visual_overlays if str(c.track_id or "overlay-main") == track_id],
            key=lambda c: float(c.start),
        )
        left = next((c for c in same_track if c.id != clip.id
                     and abs(float(c.start) + float(c.duration) - float(clip.start)) < 1e-6), None)
        right = next((c for c in same_track if c.id != clip.id
                      and abs(float(clip.start) + float(clip.duration) - float(c.start)) < 1e-6), None)
        if left is None and right is None:
            raise EditorError("NOT_FOUND", "相邻时间位置上没有可愈合的片段")
        a, b = (left, clip) if left is not None else (clip, right)
        speed_a = float(a.speed or 1.0)
        if a.asset_id != b.asset_id or abs(speed_a - float(b.speed or 1.0)) > 1e-9:
            raise EditorError("INVALID_TARGET", "两段不是同素材同速度，不能愈合")
        # VisualOverlayClip 无 source_out 字段：源终点按 source_in+duration×speed
        # 推导（与渲染层同式），连续性 = 左段推导终点对上右段 source_in。
        a_source_end = float(a.source_in) + float(a.duration) * speed_a
        if abs(float(b.source_in) - a_source_end) > 0.002 + speed_a * 1e-3:
            raise EditorError("INVALID_TARGET", "两段素材源区间不连续，不是切口")
        merged = a.model_copy(update={
            "duration": round(float(a.duration) + float(b.duration), 6),
        })
        ir.visual_overlays = [c for c in ir.visual_overlays if c.id not in (a.id, b.id)] + [merged]
        return [f"visual-overlay:{a.id}"], [f"visual-overlay:{a.id}:render", f"visual-overlay:{b.id}:render"]

    if target_id.startswith("audio#"):
        index = _audio_clip_index(ir, target_id)
        track = ir.audio[index]
        _ensure_audio_lane_unlocked(ir, str(track.track_id or f"audio-{track.type.value}"))
        same_type = sorted(
            [t for t in ir.audio if t.type == track.type and t.id != track.id],
            key=lambda t: float(t.start),
        )
        left = next((t for t in same_type
                     if abs(float(t.start) + float(t.duration) - float(track.start)) < 1e-6), None)
        right = next((t for t in same_type
                      if abs(float(track.start) + float(track.duration) - float(t.start)) < 1e-6), None)
        if left is None and right is None:
            raise EditorError("NOT_FOUND", "相邻时间位置上没有可愈合的音频片段")
        a, b = (left, track) if left is not None else (track, right)
        speed_a = float(getattr(a, "speed", 1.0) or 1.0)
        if (a.asset_id != b.asset_id or a.source_out is None
                or abs(float(b.source_in or 0.0) - float(a.source_out)) > 0.05):
            raise EditorError("INVALID_TARGET", "两段素材源区间不连续，不是切口")
        if abs(speed_a - float(getattr(b, "speed", 1.0) or 1.0)) > 1e-9:
            raise EditorError("INVALID_TARGET", "两段速度不同，不能愈合")
        merged = a.model_copy(update={
            "duration": round(float(a.duration) + float(b.duration), 6),
            "source_out": float(b.source_out) if b.source_out is not None else None,
        })
        merged_type = merged.type.value
        a_index = next((i for i, t in enumerate(ir.audio) if t.id == a.id), 0)
        b_index = next((i for i, t in enumerate(ir.audio) if t.id == b.id), a_index + 1)
        ir.audio = [t for t in ir.audio if t.id not in (a.id, b.id)] + [merged]
        # stale 键带 index（与其他音频命令同一契约：audio:<type>:<index>）
        return (
            [f"audio:{merged_type}:{a_index}"],
            [f"audio:{merged_type}:{a_index}:render", f"audio:{merged_type}:{b_index}:render",
             "project:audio-tracks"],
        )

    si, scene, idx, layer = _find_layer_with_scene_index(ir, target_id)
    _ensure_layer_unlocked(layer)
    if idx + 1 >= len(scene.layers):
        raise EditorError("NOT_FOUND", "该片段右侧没有相邻片段")
    right_l = scene.layers[idx + 1]
    if right_l.type != layer.type or right_l.asset_id != layer.asset_id:
        raise EditorError("INVALID_TARGET", "右侧片段不是同类型同素材，不能愈合")
    speed_l = float(layer.speed or 1.0)
    speed_r = float(right_l.speed or 1.0)
    if abs(speed_l - speed_r) > 1e-9:
        raise EditorError("INVALID_TARGET", "两段速度不同，不能愈合")
    if layer.type == LayerType.video:
        if layer.source_out is None or abs(float(right_l.source_in) - float(layer.source_out)) > 0.002 + speed_l * 1e-3:
            raise EditorError("INVALID_TARGET", "两段素材源区间不连续，不是切口")
        merged = layer.model_copy(update={
            "duration": float(layer.duration) + float(right_l.duration),
            "source_out": float(right_l.source_out),
        })
    else:
        merged = layer.model_copy(update={"duration": float(layer.duration) + float(right_l.duration)})
    layers = list(scene.layers)
    layers[idx:idx + 2] = [merged]
    cursor = 0.0
    normalized = []
    for item in layers:
        normalized.append(item.model_copy(update={"start": round(cursor, 6)}))
        cursor += float(item.duration)
    scene_data = scene.model_dump()
    scene_data["layers"] = [item.model_dump() for item in normalized]
    ir.scenes[si] = SceneIR(**scene_data)
    return [f"{scene.id}#{idx}"], _stale_of_scene(project_id="", scene_id=scene.id)


def _cmd_trim(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Trim a main-track video clip without persisting a broken scene.

    Default/right-edge trims ripple the following sequence and update scene length.
    Explicit left-edge trims preserve the old right edge, represent removed time as
    a renderable gap, and may extend only into an existing preceding gap.
    """
    si, scene, idx, layer = _find_layer_with_scene_index(ir, target_id)
    _ensure_layer_unlocked(layer)
    if layer.type != LayerType.video:
        raise EditorError("INVALID_TARGET", "trim 仅支持 video 层")
    raw_in = payload.get("sourceIn", layer.source_in)
    raw_out = payload.get("sourceOut", layer.source_out)
    if isinstance(raw_in, bool) or isinstance(raw_out, bool):
        raise EditorError("INVALID_PAYLOAD", "trim 的 sourceIn/sourceOut 不能是布尔值")
    try:
        new_in = float(raw_in)
        new_out = float(raw_out)
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PAYLOAD", "trim 的 sourceIn/sourceOut 必须是数字") from exc
    if not (math.isfinite(new_in) and math.isfinite(new_out) and new_in >= 0 and new_out > new_in):
        raise EditorError("INVALID_PAYLOAD", f"trim 区间非法: {new_in}–{new_out}")
    source_asset = next((asset for asset in ir.assets if asset.id == layer.asset_id), None)
    if source_asset is not None and source_asset.duration is not None:
        source_duration = float(source_asset.duration)
        source_tolerance = max(0.01, float(layer.speed) / max(1, int(ir.project.fps)))
        if math.isfinite(source_duration) and new_out > source_duration + source_tolerance:
            raise EditorError(
                "SOURCE_RANGE_OUT_OF_BOUNDS",
                f"裁剪终点 {new_out:.3f}s 超出素材时长 {source_duration:.3f}s",
            )
    new_duration = (new_out - new_in) / float(layer.speed)
    if not math.isfinite(new_duration) or new_duration <= 0.05:
        raise EditorError("INVALID_PAYLOAD", "裁剪后片段必须大于 0.05 秒")

    edge = payload.get("edge")
    if edge not in (None, "left", "right"):
        raise EditorError("INVALID_PAYLOAD", "trim 的 edge 只能是 left/right")

    if edge == "left":
        # A left-edge trim changes the clip's in-point, so the timeline edge must
        # move too. Keep the clip's right edge and downstream edits fixed; represent
        # the newly exposed empty interval as an explicit renderable gap. If the
        # gesture extends the in-point leftward, it may consume only an existing gap.
        if abs(new_out - float(layer.source_out)) > 0.05:
            raise EditorError(
                "INVALID_PAYLOAD",
                "左边缘修剪只能改变 sourceIn；sourceOut 必须保持不变",
            )
        delta = new_duration - float(layer.duration)
        layers = list(scene.layers)
        new_idx = idx

        if delta < -1e-6:
            gap_duration = -delta
            moved_layer = layer.model_copy(update={
                "start": round(float(layer.start) + gap_duration, 6),
                "source_in": new_in,
                "source_out": new_out,
                "duration": new_duration,
            })
            previous = layers[idx - 1] if idx > 0 else None
            if previous is not None and previous.type == LayerType.gap:
                layers[idx - 1] = previous.model_copy(update={
                    "duration": round(float(previous.duration) + gap_duration, 6),
                })
                layers[idx] = moved_layer
            else:
                gap = Layer(
                    type=LayerType.gap,
                    start=float(layer.start),
                    duration=round(gap_duration, 6),
                )
                layers[idx:idx + 1] = [gap, moved_layer]
                new_idx = idx + 1
        elif delta > 1e-6:
            previous = layers[idx - 1] if idx > 0 else None
            if previous is None or previous.type != LayerType.gap:
                raise EditorError(
                    "TRIM_BOUNDARY",
                    "无法向左扩展片段：左侧没有可消耗的空隙。先移动片段或插入空隙后再试。",
                )
            remaining = float(previous.duration) - delta
            if remaining < -1e-6:
                raise EditorError(
                    "TRIM_BOUNDARY",
                    "无法向左扩展片段：扩展范围超出左侧空隙。",
                )
            min_gap = max(0.05, 1.0 / max(1, int(ir.project.fps)))
            if 1e-6 < remaining < min_gap:
                raise EditorError(
                    "TRIM_BOUNDARY",
                    f"扩展后会留下小于一帧的空隙（{remaining:.4f}s）；请一次修剪到空隙边界。",
                )
            moved_layer = layer.model_copy(update={
                "start": round(float(layer.start) - delta, 6),
                "source_in": new_in,
                "source_out": new_out,
                "duration": new_duration,
            })
            if remaining <= 1e-6:
                layers[idx - 1:idx + 1] = [moved_layer]
                new_idx = idx - 1
            else:
                layers[idx - 1] = previous.model_copy(update={"duration": round(remaining, 6)})
                layers[idx] = moved_layer
        else:
            layers[idx] = layer.model_copy(update={
                "source_in": new_in,
                "source_out": new_out,
                "duration": new_duration,
            })

        scene_data = scene.model_dump()
        scene_data["layers"] = [item.model_dump() for item in layers]
        ir.scenes[si] = SceneIR(**scene_data)
        # The source window and global start of the original-audio clip are linked
        # to this video occurrence until explicitly detached by the user.
        # A picture-only left trim must update that audio, while BGM/SFX remain
        # independent clips on the same project clock.
        _resync_audio_to_scenes(ir, sync_narration=False)
        stale = _stale_of_scene(project_id="", scene_id=scene.id)
        stale.extend(["project:preview", "project:audio:render", "project:audio-tracks"])
        return [f"{scene.id}#{new_idx}"], list(dict.fromkeys(stale))

    delta = new_duration - float(layer.duration)
    new_scene_duration = float(scene.duration) + delta
    if new_scene_duration <= 0.3:
        raise EditorError("INVALID_PAYLOAD", "裁剪后场景时长不能小于 0.3 秒")
    layers = list(scene.layers)
    layers[idx] = layer.model_copy(update={
        "source_in": new_in, "source_out": new_out, "duration": new_duration,
    })
    for j in range(idx + 1, len(layers)):
        layers[j] = layers[j].model_copy(update={"start": float(layers[j].start) + delta})
    scene_data = scene.model_dump()
    scene_data["duration"] = new_scene_duration
    scene_data["layers"] = [item.model_dump() for item in layers]
    ir.scenes[si] = SceneIR(**scene_data)
    _renumber_starts(ir)
    _rescale_total(ir)
    _resync_audio_to_scenes(ir)
    return [f"{scene.id}#{idx}"], _stale_of_scene(project_id="", scene_id=scene.id)




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
    _resync_audio_to_scenes(ir)
    return [scene_a.id, scene_b.id], _stale_of_scene(project_id="", scene_id=scene.id)


def _cmd_move(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """move 场景重排：toIndex 目标位置，后重算全部 start（总时长不变）。"""
    si, scene = _find_scene(ir, target_id)
    raw_to = payload.get("toIndex", si)
    if isinstance(raw_to, bool):
        raise EditorError("INVALID_PAYLOAD", "move 的 toIndex 不能是布尔值")
    if isinstance(raw_to, float) and not math.isfinite(raw_to):
        raise EditorError("INVALID_PAYLOAD", "move 的 toIndex 必须是有限整数")
    if isinstance(raw_to, float) and not raw_to.is_integer():
        raise EditorError("INVALID_PAYLOAD", "move 的 toIndex 必须是整数")
    try:
        to = int(raw_to)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PAYLOAD", "move 需要整数 toIndex") from exc
    to = max(0, min(to, len(ir.scenes) - 1))
    ir.scenes.insert(to, ir.scenes.pop(si))
    _renumber_starts(ir)
    _resync_audio_to_scenes(ir)
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
    _resync_audio_to_scenes(ir)
    return [scene.id], [f"{scene.id}:render"]


def _cmd_duplicate(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """复制场景插到其后（总时长增加；CompositionIR 校验要求总长同步）。"""
    si, scene = _find_scene(ir, target_id)
    copy = scene.model_copy(update={"id": f"{scene.id}-copy{uuid.uuid4().hex[:3]}"})
    ir.scenes.insert(si + 1, copy)
    _renumber_starts(ir)
    _rescale_total(ir)
    _resync_audio_to_scenes(ir)
    return [copy.id], [f"{copy.id}:render"]


def _cmd_set_duration(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """调场景时长：layers 同步拉伸（保持铺满），总时长联动。"""
    si, scene = _find_scene(ir, target_id)
    raw_duration = payload.get("duration", scene.duration)
    if isinstance(raw_duration, bool):
        raise EditorError("INVALID_PAYLOAD", "场景 duration 不能是布尔值")
    try:
        new_dur = float(raw_duration)
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PAYLOAD", "场景 duration 必须是数字") from exc
    if not math.isfinite(new_dur) or new_dur <= 0.3:
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
    _resync_audio_to_scenes(ir)
    return [scene.id], _stale_of_scene(project_id="", scene_id=scene.id)


def _cmd_set_opacity(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """层透明度：写 motion_instance.parameters.opacity（渲染器 alpha 缩放）。

    与 replace_motion 不同：不换 recipe，只调透明度标量。
    """
    scene, idx, layer = _find_layer(ir, target_id)
    _ensure_layer_unlocked(layer)
    raw_opacity = payload.get("opacity", 1.0)
    if isinstance(raw_opacity, bool):
        raise EditorError("INVALID_PAYLOAD", "opacity 不能是布尔值")
    try:
        opacity = float(raw_opacity)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PAYLOAD", "opacity 必须是数字") from exc
    if not math.isfinite(opacity) or not (0.0 <= opacity <= 1.0):
        raise EditorError("INVALID_PAYLOAD", f"opacity 必须是有限数字且位于 [0,1]")
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


def _resync_audio_to_scenes(
    ir: CompositionIR, *, sync_narration: bool = True, match_original_by_source: bool = False,
) -> None:
    """场景结构编辑后的音频时间同步。

    original 轨不再简单按 asset_id 取第一个窗口，而是：
    - 同一素材的多个 original track 按“旧 timeline start”保留实例顺序；
    - 与该素材在新时间线中的多个 video layer 窗口按新 start 顺序一一对应；
    - 没有对应窗口的轨道视为孤儿并删除；
    narration 默认按场景顺序 1:1 重铺；主画面轨内部重排传 sync_narration=False，
    以免无关的视觉重排把全片旁白裁成第一镜时长。其他音轨只做时间范围裁剪。
    """
    from video_director.models import AudioTrackType

    if not ir.scenes:
        ir.audio = []
        return
    total = sum(sc.duration for sc in ir.scenes)

    asset_windows: dict = {}
    for sc in ir.scenes:
        for lay in sc.layers:
            if lay.type != LayerType.video or not getattr(lay, "asset_id", None):
                continue
            asset_windows.setdefault(lay.asset_id, []).append(
                (sc.start + lay.start, lay.duration, lay.source_in, lay.source_out)
            )
    for aid in asset_windows:
        asset_windows[aid].sort(key=lambda x: x[0])

    original_by_asset: dict = {}
    narration_tracks: list = []
    new_audio: list = []

    for t in ir.audio:
        ttype = t.type.value if hasattr(t.type, "value") else str(t.type)
        if ttype == "original":
            follows_scene_video = bool(getattr(t, "follows_scene_video", True))
            # Migration for pre-flag projects: source-audio clips detached by earlier
            # versions used the audio-source-* id and a physical original-audio lane.
            # Pydantic's fields-set lets us distinguish absent legacy JSON from an
            # explicit True; never infer over a user's explicitly saved choice.
            fields_set = getattr(t, "model_fields_set", set())
            if "follows_scene_video" not in fields_set and (
                str(getattr(t, "id", "") or "").startswith("audio-source-")
                or (
                    str(getattr(t, "track_id", "") or "").startswith("audio-original")
                    and getattr(t, "source_out", None) is not None
                )
            ):
                follows_scene_video = False
            if follows_scene_video and t.asset_id:
                original_by_asset.setdefault(t.asset_id, []).append(t)
            elif follows_scene_video:
                # The legacy project-wide original-audio policy may exist before a
                # video source is selected. An unbound policy is not an orphan clip:
                # preserve it so a later source-matching step can bind it.
                start = min(float(t.start), max(0.0, total - float(t.duration)))
                dur = min(float(t.duration), max(0.0, total - start))
                if dur > 0.01:
                    new_audio.append(t.model_copy(update={
                        "start": round(start, 3), "duration": round(dur, 3),
                    }))
            else:
                # This audio was explicitly detached or user-edited. It may refer to a
                # video visible only on an overlay track, so no SceneIR asset window is
                # required for it to remain a valid editable audio clip.
                start = min(float(t.start), max(0.0, total - float(t.duration)))
                dur = min(float(t.duration), max(0.0, total - start))
                if dur > 0.01:
                    new_audio.append(t.model_copy(update={
                        "start": round(start, 3), "duration": round(dur, 3),
                        "follows_scene_video": False,
                    }))
        elif ttype == "narration":
            if sync_narration:
                narration_tracks.append(t)
            else:
                # A picture-only reorder must leave the global narration mix untouched.
                new_audio.append(t)
        else:
            start = min(float(t.start), max(0.0, total - float(t.duration)))
            dur = min(float(t.duration), max(0.0, total - start))
            if dur > 0.01:
                new_audio.append(t.model_copy(update={
                    "start": round(start, 3), "duration": round(dur, 3)
                }))

    for aid, tracks in original_by_asset.items():
        wins = asset_windows.get(aid, [])
        tracks = sorted(tracks, key=lambda t: float(t.start or 0.0))
        if match_original_by_source:
            # A source may be reused several times in one project. Reorder changes
            # timeline order but not source ranges, so bind by source window first;
            # otherwise sorted-by-time pairing transfers volume/fades between clips.
            remaining = list(enumerate(wins))
            matched = []
            tolerance = max(0.05, 0.5 / max(1.0, float(ir.project.fps or 1.0)))
            for track in tracks:
                source_in = float(track.source_in or 0.0)
                source_out = (
                    float(track.source_out)
                    if track.source_out is not None
                    else source_in + float(track.duration) * float(getattr(track, "speed", 1.0) or 1.0)
                )
                exact = [
                    (index, window)
                    for index, window in remaining
                    if window[2] is not None
                    and abs(float(window[2]) - source_in) <= tolerance
                    and window[3] is not None
                    and abs(float(window[3]) - source_out) <= tolerance
                ]
                if exact:
                    # Duplicate uses of an identical source window are ambiguous in
                    # legacy data; retain nearest old timeline position deterministically.
                    chosen = min(exact, key=lambda item: abs(float(item[1][0]) - float(track.start)))
                elif remaining:
                    # Old projects may have incomplete source anchors. Keep the historic
                    # ordinal fallback for the unmatched remainder rather than dropping it.
                    chosen = remaining[0]
                else:
                    continue
                matched.append((track, chosen[1]))
                remaining = [item for item in remaining if item[0] != chosen[0]]
            pairs = matched
        else:
            pairs = [
                (track, wins[index])
                for index, track in enumerate(tracks)
                if index < len(wins)
            ]
        for track, window in pairs:
            w_start, w_dur, w_src_in, w_src_out = window
            # Audio gain/fades belong to the clip instance; only linked timing/source
            # fields follow the matching video occurrence.
            new_audio.append(track.model_copy(update={
                "start": round(float(w_start), 3),
                "duration": round(min(float(track.duration), float(w_dur)), 3),
                "source_in": round(float(w_src_in if w_src_in is not None else 0.0), 3),
                "source_out": round(float(w_src_out), 3) if w_src_out is not None else None,
            }))

    for i, sc in enumerate(ir.scenes):
        if i < len(narration_tracks):
            t = narration_tracks[i]
            new_audio.append(t.model_copy(update={
                "start": round(float(sc.start), 3),
                "duration": round(min(float(t.duration), float(sc.duration)), 3),
            }))

    # 保持音频输出稳定：按 timeline start、类型顺序重新排列，避免结构编辑后
    # 原声/旁白插入位置随机变化影响 renderer 的输入顺序。
    type_order = {
        AudioTrackType.narration.value: 0,
        AudioTrackType.dialogue.value: 1,
        AudioTrackType.original.value: 2,
        AudioTrackType.bgm.value: 3,
        AudioTrackType.sfx.value: 4,
        AudioTrackType.ambience.value: 5,
    }
    new_audio.sort(key=lambda t: (
        round(float(t.start or 0.0), 6),
        type_order.get(t.type.value if hasattr(t.type, "value") else str(t.type), 99),
    ))
    ir.audio = new_audio


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
        if isinstance(payload["scale"], bool):
            raise EditorError("INVALID_PARAMETER", "scale 不能是布尔值")
        try:
            scale = float(payload["scale"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise EditorError("INVALID_PARAMETER", "scale 必须是数字") from exc
        if not math.isfinite(scale) or not (0.1 <= scale <= 3.0):
            raise EditorError("INVALID_PARAMETER", f"scale {scale} 越界 [0.1,3.0]")
        transform["scale"] = scale
    if "opacity" in payload:
        if isinstance(payload["opacity"], bool):
            raise EditorError("INVALID_PARAMETER", "opacity 不能是布尔值")
        try:
            opacity = float(payload["opacity"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise EditorError("INVALID_PARAMETER", "opacity 必须是数字") from exc
        if not math.isfinite(opacity) or not (0.0 <= opacity <= 1.0):
            raise EditorError("INVALID_PARAMETER", f"opacity {opacity} 越界 [0,1]")
        transform["opacity"] = opacity
    inst = dict(layer.motion_instance or {})
    params = dict(inst.get("parameters") or {})
    params["transform"] = transform
    inst["parameters"] = params
    canonical = layer.transform
    if canonical is not None:
        try:
            canonical = Transform.model_validate({
                **canonical.model_dump(),
                "scale": transform["scale"],
                "opacity": transform["opacity"],
            })
        except ValueError as exc:
            raise EditorError("INVALID_PARAMETER", str(exc)) from exc
    scene.layers[idx] = layer.model_copy(update={
        "motion_instance": inst,
        "transform": canonical,
    })
    layer_id = f"{scene.id}#{idx}"
    return [layer_id], _stale_of_layer(project_id="", scene_id=scene.id, layer_id=layer_id)


def _visual_overlay_index(ir: CompositionIR, target_id: str) -> int:
    if not target_id.startswith("overlay#"):
        raise EditorError("INVALID_TARGET", "覆盖层 targetId 必须为 overlay#<index>")
    try:
        index = int(target_id.split("#", 1)[1])
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_TARGET", f"覆盖层索引非法：{target_id}") from exc
    if index < 0 or index >= len(ir.visual_overlays):
        raise EditorError("NOT_FOUND", f"覆盖层 {target_id} 不存在", recoverable=False)
    return index


def _visual_track_index(ir: CompositionIR, target_id: str) -> int:
    index = next((i for i, track in enumerate(ir.visual_tracks) if track.id == target_id), -1)
    if index < 0:
        raise EditorError("NOT_FOUND", f"画面轨道 {target_id!r} 不存在", recoverable=False)
    return index


def _ensure_visual_track_unlocked(ir: CompositionIR, track_id: str):
    track = ir.visual_tracks[_visual_track_index(ir, track_id)]
    if track.locked:
        raise EditorError("TRACK_LOCKED", f"画面轨道“{track.name}”已锁定，先解锁后再编辑")
    return track


def _cmd_add_visual_track(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    if target_id != "visual-tracks":
        raise EditorError("INVALID_TARGET", "add_visual_track 的 targetId 必须是 visual-tracks")
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 60:
        raise EditorError("INVALID_PAYLOAD", "轨道名称必须为 1–60 个字符")
    if any(item.name.casefold() == name.casefold() for item in ir.visual_tracks):
        raise EditorError("DUPLICATE_TRACK_NAME", f"画面轨道名称已存在：{name}")
    track = VisualTrack(
        id=f"vtrack-{uuid.uuid4().hex[:12]}",
        name=name,
        order=max((item.order for item in ir.visual_tracks), default=-1) + 1,
    )
    ir.visual_tracks.append(track)
    return [f"visual-track:{track.id}"], ["project:visual-tracks", "project:preview"]


def _cmd_rename_visual_track(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    index = _visual_track_index(ir, target_id)
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 60:
        raise EditorError("INVALID_PAYLOAD", "轨道名称必须为 1–60 个字符")
    if any(item.id != target_id and item.name.casefold() == name.casefold() for item in ir.visual_tracks):
        raise EditorError("DUPLICATE_TRACK_NAME", f"画面轨道名称已存在：{name}")
    ir.visual_tracks[index] = ir.visual_tracks[index].model_copy(update={"name": name})
    return [f"visual-track:{target_id}"], ["project:visual-tracks", "project:preview"]


def _cmd_reorder_visual_track(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    index = _visual_track_index(ir, target_id)
    raw = payload.get("toIndex")
    if isinstance(raw, bool):
        raise EditorError("INVALID_PAYLOAD", "toIndex 必须是整数")
    try:
        numeric_index = float(raw)
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PAYLOAD", "toIndex 必须是整数") from exc
    if not math.isfinite(numeric_index) or not numeric_index.is_integer():
        raise EditorError("INVALID_PAYLOAD", "toIndex 必须是整数")
    to_index = int(numeric_index)
    ordered = sorted(ir.visual_tracks, key=lambda item: item.order)
    if to_index < 0 or to_index >= len(ordered):
        raise EditorError("INVALID_PARAMETER", "目标轨道顺序越界")
    track = ordered.index(next(t for t in ir.visual_tracks if t.id == target_id))
    if track == to_index:
        raise EditorError("NOOP", "轨道已处于目标位置")
    ordered.insert(to_index, ordered.pop(track))
    ir.visual_tracks = [item.model_copy(update={"order": pos}) for pos, item in enumerate(ordered)]
    return [f"visual-track:{target_id}"], ["project:visual-tracks", "project:visual-overlays:render", "project:preview"]


def _cmd_remove_visual_track(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    index = _visual_track_index(ir, target_id)
    if len(ir.visual_tracks) <= 1:
        raise EditorError("LAST_VISUAL_TRACK", "项目至少需要保留一条画面轨道")
    clips = [clip for clip in ir.visual_overlays if clip.track_id == target_id]
    if clips and payload.get("deleteContents") is not True:
        raise EditorError(
            "VISUAL_TRACK_NOT_EMPTY",
            "该轨道仍有片段；确认同时删除轨道内容时，请传入 deleteContents=true",
        )
    clip_ids = {clip.id for clip in clips}
    linked_audio = [
        (i, clip) for i, clip in enumerate(ir.audio)
        if clip.linked_visual_overlay_id in clip_ids
    ]
    for _audio_index, audio_clip in linked_audio:
        _ensure_audio_lane_unlocked(ir, str(audio_clip.track_id or f"audio-{audio_clip.type.value}"))
    ir.visual_overlays = [clip for clip in ir.visual_overlays if clip.track_id != target_id]
    linked_indices = {i for i, _clip in linked_audio}
    ir.audio = [clip for i, clip in enumerate(ir.audio) if i not in linked_indices]
    ir.visual_tracks.pop(index)
    ordered = sorted(ir.visual_tracks, key=lambda item: item.order)
    ir.visual_tracks = [item.model_copy(update={"order": pos}) for pos, item in enumerate(ordered)]
    changed = [f"visual-track:{target_id}"] + [f"visual-overlay:{clip.id}" for clip in clips]
    changed.extend(f"audio:{clip.type.value}:{i}" for i, clip in linked_audio)
    stale = ["project:visual-tracks", "project:visual-overlays:render", "project:preview"]
    if clips:
        stale.append("project:timeline:render")
    if linked_audio:
        stale.extend(["project:audio:render", "project:audio-tracks"])
    return changed, list(dict.fromkeys(stale))


def _asset_ref_from_project_manifest(project_id: Optional[str], asset_id: str) -> Optional[AssetRef]:
    """从项目 assets/manifest.json 构造 AssetRef（项目本地上传素材的唯一登记处）。

    全局 asset_catalog（Library）只收录经 Library 注册的素材；项目里
    ``local-*`` 上传素材只写在项目 manifest。只查 Library 会让"移到播放头
    轨道""加画面叠加"对上传素材整体失能（Playwright 实测 ASSET_NOT_FOUND）。
    路径仍要求是存在的文件——渲染层会直接读它。
    """
    if not project_id:
        return None
    manifest_p = config.project_dir(project_id) / "assets" / "manifest.json"
    if not manifest_p.is_file():
        return None
    try:
        rows = json.loads(manifest_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    row = next((r for r in rows if isinstance(r, dict) and r.get("id") == asset_id), None)
    if row is None:
        return None
    file_value = str(row.get("file") or "")
    if not file_value:
        return None
    try:
        resolved = Path(file_value).resolve()
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    return AssetRef(
        id=asset_id,
        type=str(row.get("type") or "video"),
        file=str(resolved),
        duration=row.get("duration"),
        width=row.get("width"),
        height=row.get("height"),
        fps=row.get("fps"),
        source=row.get("source") or "uploaded",
    )


def _overlay_source_duration(ir: CompositionIR, asset_id: str, project_id: Optional[str] = None) -> Tuple[AssetRef, str, float]:
    asset = next((item for item in ir.assets if item.id == asset_id), None)
    if asset is None:
        # The editor asset chooser is backed by the trusted asset catalog, while
        # CompositionIR.assets may contain only sources already used by scenes.
        # Promote the selected catalog entry into the persisted IR instead of
        # exposing an add button that silently rejects legitimate library assets.
        from video_director import asset_catalog
        entry = asset_catalog.get_asset(asset_id)
        if not entry:
            # 第二回落：项目 manifest（上传素材不进 Library）
            manifest_asset = _asset_ref_from_project_manifest(project_id, asset_id)
            if manifest_asset is None:
                raise EditorError("ASSET_NOT_FOUND", f"项目素材库没有素材 {asset_id!r}", recoverable=False)
            if str(manifest_asset.type or "").lower() not in ("video", "image"):
                raise EditorError("ASSET_TYPE_MISMATCH", "独立画面覆盖层只接受视频或图片素材", recoverable=False)
            ir.assets.append(manifest_asset)
            media_type = str(manifest_asset.type or "").lower()
            resolved = str(manifest_asset.file)
            if media_type == "image":
                return manifest_asset, resolved, 0.0
            from video_director import prober
            try:
                probe = prober.probe_media(resolved)
                return manifest_asset, resolved, float(probe.duration or 0.0)
            except Exception as exc:
                raise EditorError("ASSET_NOT_FOUND", f"素材不可读：{asset_id!r}（{exc}）", recoverable=False) from exc
        media_type = str(entry.get("type") or "").lower()
        if media_type not in ("video", "image"):
            raise EditorError("ASSET_TYPE_MISMATCH", "独立画面覆盖层只接受视频或图片素材", recoverable=False)
        file_value = str(entry.get("file") or "")
        if not file_value:
            raise EditorError("ASSET_NOT_FOUND", f"素材 {asset_id!r} 没有可用文件", recoverable=False)
        catalog_path = Path(file_value).resolve()
        if not catalog_path.is_file():
            raise EditorError("ASSET_NOT_FOUND", f"素材文件不可用：{asset_id!r}", recoverable=False)
        asset = AssetRef(
            id=asset_id, type=media_type, file=str(catalog_path),
            duration=entry.get("duration"), width=entry.get("width"), height=entry.get("height"),
            fps=entry.get("fps"), source=entry.get("source") or "uploaded",
            license_note=entry.get("license_note"), provenance=entry.get("provenance"),
            rights_status=entry.get("rights_status"),
        )
        ir.assets.append(asset)
    media_type = str(asset.type or "").lower()
    if media_type not in ("video", "image"):
        raise EditorError("ASSET_TYPE_MISMATCH", "独立画面覆盖层只接受视频或图片素材", recoverable=False)
    candidate = Path(asset.file)
    if not candidate.is_file() and not candidate.is_absolute() and project_id:
        candidate = config.project_dir(project_id) / candidate
    candidate = candidate.resolve()
    if not candidate.is_file():
        raise EditorError("ASSET_NOT_FOUND", f"项目素材文件不可用：{asset_id!r}", recoverable=False)
    duration = float(asset.duration or 0.0)
    if media_type == "video" and (not math.isfinite(duration) or duration <= 0):
        from video_director import prober
        try:
            probe = prober.probe_media(str(candidate))
            duration = float(probe.duration or 0.0)
        except Exception as exc:
            raise EditorError("ASSET_PROBE_FAILED", f"无法读取视频素材时长：{exc}") from exc
    if media_type == "video" and (not math.isfinite(duration) or duration <= 0):
        raise EditorError("ASSET_PROBE_FAILED", "视频覆盖素材缺少有效时长")
    return asset, str(candidate), duration

def _cmd_promote_layer_to_visual_track(
    ir: CompositionIR, target_id: str, payload: dict, project_id: str,
) -> Tuple[List[str], List[str]]:
    """Lift a compatible main-picture clip into an independently editable global track.

    Supported plain video and still-image clips are replaced with an explicit gap of
    the same duration, preserving scene timing. Effects the overlay renderer cannot
    represent are rejected rather than silently discarded.
    """
    si, scene, idx, layer = _find_layer_with_scene_index(ir, target_id)
    _ensure_layer_unlocked(layer)
    if layer.type not in (LayerType.video, LayerType.image):
        raise EditorError("INVALID_TARGET", "只有视频或静态图片主画面片段可以提升到独立轨道")
    is_video = layer.type == LayerType.video
    media_label = "视频" if is_video else "图片"
    media_slug = "video" if is_video else "image"
    clip_speed = float(layer.speed or 1.0)
    if not math.isfinite(clip_speed) or not 0.25 <= clip_speed <= 4.0:
        raise EditorError("LIFT_UNSUPPORTED_SPEED", "当前独立轨道支持 0.25×–4×；请先调整到此范围")
    if not is_video and abs(clip_speed - 1.0) > 1e-6:
        raise EditorError("LIFT_UNSUPPORTED_SPEED", "静态图片不能应用视频播放速度")
    motion_instance = layer.motion_instance or {}
    motion_parameters = motion_instance.get("parameters") or {}
    legacy_transform = motion_parameters.get("transform") or {}
    static_transform_only = (
        not motion_instance
        or (
            set(motion_instance).issubset({"parameters"})
            and set(motion_parameters).issubset({"transform", "opacity"})
            and set(legacy_transform).issubset({"scale", "opacity"})
        )
    )
    source_fit_mode = str(getattr(layer, "fit_mode", "cover") or "cover")
    if source_fit_mode not in {"cover", "contain"}:
        raise EditorError(
            "LIFT_UNSUPPORTED_FIT_MODE",
            f"主画面适配方式 {source_fit_mode!r} 无法安全转换；仅支持 cover/contain",
        )
    if (
        layer.motion or layer.motion_recipe_id or layer.motion_version
        or not static_transform_only or layer.keyframes or layer.mask
    ):
        raise EditorError(
            "LIFT_UNSUPPORTED_EFFECT",
            "当前片段带有动效、关键帧、蒙版或非静态变换；这些效果尚不能安全转换为独立画面轨道",
        )

    # Preserve structured transform, or migrate the old scale/opacity dictionary
    # emitted by set_transform into the canonical Transform contract.
    lifted_transform = layer.transform
    if lifted_transform is None and legacy_transform:
        try:
            lifted_transform = Transform.model_validate({
                **Transform().model_dump(),
                **legacy_transform,
            })
        except ValueError as exc:
            raise EditorError("LIFT_INVALID_TRANSFORM", f"主画面静态变换无效：{exc}") from exc
    if getattr(scene, "camera", None) is not None:
        raise EditorError("LIFT_UNSUPPORTED_SCENE_CAMERA", "所在分镜使用了场景镜头运动，暂不能单独提升其中一段")
    transition = getattr(getattr(scene, "transition_out", None), "type", "cut")
    if transition not in ("cut", None) and float(layer.start) + float(layer.duration) >= float(scene.duration) - 0.45:
        raise EditorError("LIFT_UNSUPPORTED_TRANSITION", "该片段跨越分镜转场区域；请先将转场改为硬切再提升")

    requested_track_id = str(payload.get("trackId") or "").strip()
    if requested_track_id:
        _ensure_visual_track_unlocked(ir, requested_track_id)
    if requested_track_id:
        if not any(track.id == requested_track_id for track in ir.visual_tracks):
            raise EditorError("NOT_FOUND", f"画面轨道 {requested_track_id!r} 不存在", recoverable=False)
        track_id = requested_track_id
    else:
        # This UI action is called "promote to independent track": create a real
        # track in the same atomic revision instead of silently dropping the clip
        # into a generic overlay row. Keep existing overlays above it.
        names = {track.name.strip().casefold() for track in ir.visual_tracks}
        track_number = 1
        while f"独立{media_label} {track_number}".casefold() in names:
            track_number += 1
        track_id = f"lifted-{media_slug}-{uuid.uuid4().hex[:10]}"
        previous_tracks = sorted(ir.visual_tracks, key=lambda track: track.order)
        ir.visual_tracks = [
            track.model_copy(update={"order": index + 1})
            for index, track in enumerate(previous_tracks)
        ]
        ir.visual_tracks.append(VisualTrack(
            id=track_id, name=f"独立{media_label} {track_number}", order=0,
        ))
    if not layer.asset_id:
        raise EditorError("ASSET_NOT_FOUND", f"主画面{media_label}缺少 asset_id，不能提升", recoverable=False)

    asset, resolved_file, source_duration = _overlay_source_duration(ir, layer.asset_id, project_id)
    expected_media_type = "video" if is_video else "image"
    if str(asset.type).lower() != expected_media_type:
        raise EditorError(
            "ASSET_TYPE_MISMATCH",
            f"主画面片段是{media_label}，但引用素材类型为 {asset.type!r}",
            recoverable=False,
        )
    duration = float(layer.duration)
    # Still images do not have source-time windows or audio. Preserve their timeline
    # hold duration but use a neutral source_in for the global overlay renderer.
    source_in = float(layer.source_in) if is_video else 0.0
    probe = None
    if is_video:
        source_out = float(layer.source_out or 0.0)
        if (
            not math.isfinite(source_in) or not math.isfinite(source_out)
            or source_in < 0 or source_out <= source_in
            or source_out > source_duration + 0.05
            or abs((source_out - source_in) - duration * clip_speed) > 0.05
        ):
            raise EditorError("INVALID_SOURCE_RANGE", "视频源区间与播放速度换算后的输出时长不一致，不能提升")

        from video_director import prober
        try:
            probe = prober.probe_media(resolved_file)
        except Exception as exc:
            raise EditorError("ASSET_PROBE_FAILED", f"提升前无法验证视频流：{exc}") from exc
        if not getattr(probe, "has_video_stream", False):
            raise EditorError("ASSET_PROBE_FAILED", "素材没有可解码的视频流，不能提升")

        source_audio_enabled = False
    else:
        source_audio_enabled = False
    # A drag-and-drop into a global visual track may supply an explicit global
    # start. Without it, preserve the source layer's current absolute timeline time.
    raw_start = payload.get("start", float(scene.start) + float(layer.start))
    if isinstance(raw_start, bool):
        raise EditorError("INVALID_PARAMETER", "start 必须是全局时间线秒数，不能是布尔值")
    try:
        absolute_start = float(raw_start)
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PARAMETER", "start 必须是全局时间线秒数") from exc
    if (
        not math.isfinite(absolute_start) or absolute_start < 0
        or absolute_start + duration > float(ir.project.duration) + 0.001
    ):
        raise EditorError("INVALID_TIMELINE_RANGE", "片段提升后的全局时间区间超出项目")
    # 统一编辑切点契约（PR #82）：提升后的全局起点同样落到项目帧钟，
    # 避免主画面切分对齐了帧、而提升放置落在两帧之间。
    absolute_start = _project_frame_time(ir, absolute_start)
    if absolute_start + duration > float(ir.project.duration) + 1e-6:
        raise EditorError("INVALID_TIMELINE_RANGE", "片段提升后的全局起点按项目帧率对齐后超出项目")

    linked_source_indices = set()
    if is_video:
        # If the audio lane already follows this exact source clip, move that lane
        # with the picture and preserve its gain/fades. Leaving it behind while also
        # enabling the overlay's embedded audio would either desync or double-mix it.
        audio_match_tolerance = max(0.005, 0.5 / max(1, int(ir.project.fps)))
        source_start = float(scene.start) + float(layer.start)

        def _matches_original_audio(track, timeline_start: float) -> bool:
            if track.type != AudioTrackType.original or track.asset_id != layer.asset_id:
                return False
            return (
                abs(float(track.start) - timeline_start) <= audio_match_tolerance
                and abs(float(track.duration) - duration) <= audio_match_tolerance
                and abs(float(track.source_in or 0.0) - source_in) <= audio_match_tolerance
                and abs(
                    float(
                        track.source_out
                        if track.source_out is not None
                        else float(track.source_in or 0.0) + float(track.duration) * float(getattr(track, "speed", 1.0) or 1.0)
                    ) - source_out
                ) <= audio_match_tolerance
            )

        linked_source_indices = {
            index for index, track in enumerate(ir.audio)
            if bool(getattr(track, "follows_scene_video", False))
            and _matches_original_audio(track, source_start)
        }
        # An already-detached source clip at the destination must also be linked to
        # the promoted video. Merely suppressing embedded audio leaves that clip at
        # its previous playback rate and can desynchronize picture and sound.
        if not linked_source_indices:
            linked_source_indices = {
                index for index, track in enumerate(ir.audio)
                if _matches_original_audio(track, absolute_start)
            }
        if linked_source_indices:
            for audio_index in linked_source_indices:
                source_audio_track = ir.audio[audio_index]
                _ensure_audio_lane_unlocked(
                    ir, str(source_audio_track.track_id or f"audio-{source_audio_track.type.value}")
                )
            source_audio_enabled = False
        else:
            source_audio_enabled = bool(getattr(probe, "has_audio_stream", False))

    asset_index = next((i for i, item in enumerate(ir.assets) if item.id == layer.asset_id), None)
    resolved_asset = asset.model_copy(update={
        "file": resolved_file,
        "duration": source_duration or asset.duration,
    })
    if asset_index is None:
        ir.assets.append(resolved_asset)
    else:
        ir.assets[asset_index] = resolved_asset

    clip_id = f"overlay-{uuid.uuid4().hex[:12]}"
    clip = VisualOverlayClip(
        id=clip_id,
        asset_id=layer.asset_id,
        start=round(absolute_start, 3),
        duration=round(duration, 3),
        source_in=round(source_in, 3),
        speed=round(clip_speed, 6),
        track_id=track_id,
        presentation_mode="full_frame",
        fit_mode=source_fit_mode,
        source_audio_enabled=source_audio_enabled,
        source_audio_volume=1.0,
        x=0.0, y=0.0, width=1.0, height=1.0,
        opacity=1.0, z_index=0,
        transform=lifted_transform,
    )
    ir.visual_overlays.append(clip)
    if linked_source_indices:
        ir.audio = [
            audio_clip.model_copy(update={
                "start": round(absolute_start, 3),
                "duration": round(duration, 3),
                "source_in": round(source_in, 3),
                "source_out": round(source_out, 3),
                "speed": round(clip_speed, 6),
                "follows_scene_video": False,
                "linked_visual_overlay_id": clip_id,
            }) if audio_index in linked_source_indices else audio_clip
            for audio_index, audio_clip in enumerate(ir.audio)
        ]

    # Preserve the old timeline footprint explicitly. The renderer turns this into
    # a black video segment, so moving the promoted clip reveals a real gap rather
    # than silently stretching neighboring footage or changing scene duration.
    gap = Layer(type=LayerType.gap, start=layer.start, duration=layer.duration)
    updated_layers = list(scene.layers)
    updated_layers[idx] = gap
    scene_data = scene.model_dump()
    scene_data["layers"] = [item.model_dump() for item in updated_layers]
    ir.scenes[si] = SceneIR(**scene_data)

    key = f"visual-overlay:{clip.id}"
    stale = _stale_of_scene(project_id="", scene_id=scene.id)
    stale.extend([f"{key}:render", "project:visual-overlays:render", "project:preview"])
    return [key, f"{scene.id}#{idx}"], stale



def _cmd_expand_main_timeline_to_visual_track(
    ir: CompositionIR, target_id: str, payload: dict, project_id: str,
) -> Tuple[List[str], List[str]]:
    """Atomically expand all compatible main-picture clips onto one global track.

    Existing per-scene timing is preserved with explicit gap layers. The command
    intentionally fails closed for any non-media main layer; it must never silently
    discard a graphic/effect or leave a partially-converted project behind.
    """
    if target_id not in ("timeline:main", "project"):
        raise EditorError("INVALID_TARGET", "自由时间线展开的目标必须是主时间线")

    candidates: List[Tuple[str, int]] = []
    unsupported: List[str] = []
    for scene in ir.scenes:
        for index, layer in enumerate(scene.layers):
            if layer.type == LayerType.gap:
                continue
            if layer.type not in (LayerType.video, LayerType.image):
                unsupported.append(f"{scene.id}#{index}({layer.type.value})")
                continue
            candidates.append((scene.id, index))

    if unsupported:
        details = "、".join(unsupported[:6])
        more = f" 等 {len(unsupported)} 个片段" if len(unsupported) > 6 else ""
        raise EditorError(
            "FREE_TIMELINE_UNSUPPORTED_LAYER",
            f"展开已取消：以下主画面片段尚不能安全转入自由轨道：{details}{more}；项目未修改",
        )
    if not candidates:
        raise EditorError(
            "NO_MAIN_MEDIA_CLIPS",
            "主时间线没有可展开的视频或图片片段；项目未修改",
        )

    # Cheap preflight before any in-memory edits. More detailed checks (speed,
    # source range, effects, scene camera, transition and source probing) remain
    # in the single-clip command below; execute_command only persists after the
    # entire command succeeds, so a late rejection rolls back the whole batch.
    for scene_id, index in candidates:
        scene = next(scene for scene in ir.scenes if scene.id == scene_id)
        layer = scene.layers[index]
        try:
            _ensure_layer_unlocked(layer)
        except EditorError as exc:
            raise EditorError(
                exc.code, f"展开已取消（{scene_id}#{index}）：{exc}", recoverable=exc.recoverable
            ) from exc
        if not layer.asset_id:
            raise EditorError(
                "ASSET_NOT_FOUND",
                f"展开已取消：{scene_id}#{index} 缺少素材引用；项目未修改",
                recoverable=False,
            )

    existing_names = {track.name.strip().casefold() for track in ir.visual_tracks}
    name_number = 1
    while f"自由主画面 {name_number}".casefold() in existing_names:
        name_number += 1
    track_id = f"main-free-{uuid.uuid4().hex[:10]}"
    previous_tracks = sorted(ir.visual_tracks, key=lambda track: track.order)
    ir.visual_tracks = [
        track.model_copy(update={"order": index + 1})
        for index, track in enumerate(previous_tracks)
    ]
    ir.visual_tracks.append(VisualTrack(
        id=track_id, name=f"自由主画面 {name_number}", order=0,
    ))

    changed: List[str] = [f"visual-track:{track_id}"]
    stale: List[str] = ["project:timeline:render", "project:preview"]
    for scene_id, index in candidates:
        scene = next(scene for scene in ir.scenes if scene.id == scene_id)
        layer = scene.layers[index]
        absolute_start = float(scene.start) + float(layer.start)
        try:
            item_changed, item_stale = _cmd_promote_layer_to_visual_track(
                ir,
                f"{scene_id}#{index}",
                {"trackId": track_id, "start": absolute_start},
                project_id,
            )
        except EditorError as exc:
            raise EditorError(
                exc.code,
                f"展开已取消（{scene_id}#{index}）：{exc}；项目未修改",
                recoverable=exc.recoverable,
            ) from exc
        changed.extend(item_changed)
        stale.extend(item_stale)

    return list(dict.fromkeys(changed)), list(dict.fromkeys(stale))


def _overwrite_visual_track_range(
    ir: CompositionIR, track_id: str, start: float, duration: float,
) -> Dict[str, List[VisualOverlayClip]]:
    """Overwrite one visual lane and return each old clip's surviving segments."""
    end = round(start + duration, 6)
    updated: List[VisualOverlayClip] = []
    segments_by_old_id: Dict[str, List[VisualOverlayClip]] = {}
    for clip in ir.visual_overlays:
        clip_start = float(clip.start)
        clip_end = clip_start + float(clip.duration)
        if clip.track_id != track_id or clip_end <= start or clip_start >= end:
            updated.append(clip)
            segments_by_old_id[clip.id] = [clip]
            continue

        left_duration = round(min(clip_end, start) - clip_start, 6)
        right_start = max(clip_start, end)
        right_duration = round(clip_end - right_start, 6)
        survivors: List[VisualOverlayClip] = []
        if left_duration > 0.001:
            left = clip.model_copy(update={"duration": left_duration})
            updated.append(left)
            survivors.append(left)
        if right_duration > 0.001:
            right_patch = {
                "start": round(right_start, 6),
                "duration": right_duration,
                "source_in": round(float(clip.source_in) + (right_start - clip_start) * float(clip.speed), 6),
            }
            if left_duration > 0.001:
                right_patch["id"] = f"{clip.id}-right-{uuid.uuid4().hex[:8]}"
            right = clip.model_copy(update=right_patch)
            updated.append(right)
            survivors.append(right)
        segments_by_old_id[clip.id] = survivors

    ir.visual_overlays = updated
    return segments_by_old_id


def _insert_visual_track_gap(
    ir: CompositionIR, track_id: str, start: float, duration: float,
) -> Dict[str, List[VisualOverlayClip]]:
    """Insert a gap on one visual lane and return each old clip's new segments."""
    updated: List[VisualOverlayClip] = []
    segments_by_old_id: Dict[str, List[VisualOverlayClip]] = {}
    for clip in ir.visual_overlays:
        clip_start = float(clip.start)
        clip_end = clip_start + float(clip.duration)
        if clip.track_id != track_id or clip_end <= start:
            updated.append(clip)
            segments_by_old_id[clip.id] = [clip]
            continue

        if clip_start < start < clip_end:
            left_duration = round(start - clip_start, 6)
            right_duration = round(clip_end - start, 6)
            survivors: List[VisualOverlayClip] = []
            if left_duration > 0.001:
                left = clip.model_copy(update={"duration": left_duration})
                updated.append(left)
                survivors.append(left)
            if right_duration > 0.001:
                right = clip.model_copy(update={
                    "id": f"{clip.id}-right-{uuid.uuid4().hex[:8]}",
                    "start": round(start + duration, 6),
                    "duration": right_duration,
                    "source_in": round(float(clip.source_in) + (start - clip_start) * float(clip.speed), 6),
                })
                updated.append(right)
                survivors.append(right)
            segments_by_old_id[clip.id] = survivors
            continue

        # A clip beginning at the edit point is downstream material and moves too.
        shifted = clip.model_copy(update={"start": round(clip_start + duration, 6)})
        updated.append(shifted)
        segments_by_old_id[clip.id] = [shifted]

    ir.visual_overlays = updated
    return segments_by_old_id


def _sync_linked_audio_for_visual_timeline_edit(
    ir: CompositionIR,
    previous_clips: List[VisualOverlayClip],
    resulting_segments: Dict[str, List[VisualOverlayClip]],
) -> bool:
    """Keep detached source-audio clips aligned after visual insert/overwrite.

    Full-clip shifts preserve gain/fades/keyframes. Operations that would trim, split,
    or remove a keyframed linked source-audio clip fail closed rather than destroying
    its automation. Audio and picture changes are saved in one Composition revision.
    """
    previous_by_id = {clip.id: clip for clip in previous_clips}
    affected_audio: List[Tuple[int, AudioTrack, VisualOverlayClip, List[VisualOverlayClip]]] = []

    def _same_window(old_clip: VisualOverlayClip, new_clip: VisualOverlayClip) -> bool:
        return (
            abs(float(old_clip.duration) - float(new_clip.duration)) <= 1e-6
            and abs(float(old_clip.source_in) - float(new_clip.source_in)) <= 1e-6
            and abs(float(old_clip.speed) - float(new_clip.speed)) <= 1e-6
        )

    for audio_index, audio_clip in enumerate(ir.audio):
        linked_id = str(audio_clip.linked_visual_overlay_id or "")
        old_clip = previous_by_id.get(linked_id)
        if old_clip is None:
            continue
        segments = resulting_segments.get(linked_id, [old_clip])
        unchanged = (
            len(segments) == 1
            and segments[0].id == old_clip.id
            and abs(float(segments[0].start) - float(old_clip.start)) <= 1e-6
            and _same_window(old_clip, segments[0])
        )
        if unchanged:
            continue

        lane_id = str(audio_clip.track_id or f"audio-{audio_clip.type.value}")
        _ensure_audio_lane_unlocked(ir, lane_id)
        needs_source_window_edit = (
            len(segments) != 1
            or not segments
            or any(not _same_window(old_clip, segment) for segment in segments)
        )
        if needs_source_window_edit and getattr(audio_clip, "keyframes", None):
            raise EditorError(
                "LINKED_AUDIO_AUTOMATION_UNSUPPORTED",
                "插入/覆盖会切分或修剪关联原声音轨，而该轨带有音量关键帧；"
                "请先解除音画关联或移除自动化，再重试。项目未修改。",
            )
        affected_audio.append((audio_index, audio_clip, old_clip, segments))

    if not affected_audio:
        return False

    changed_by_index = {index: (audio, old, segments) for index, audio, old, segments in affected_audio}
    result: List[AudioTrack] = []
    for audio_index, audio_clip in enumerate(ir.audio):
        changed_entry = changed_by_index.get(audio_index)
        if changed_entry is None:
            result.append(audio_clip)
            continue
        _audio, old_clip, segments = changed_entry
        if not segments:
            # The linked picture was fully overwritten; remove its detached source audio too.
            continue

        full_window_shift = len(segments) == 1 and _same_window(old_clip, segments[0])
        old_source_end = float(old_clip.source_in) + float(old_clip.duration) * float(old_clip.speed)
        for segment_index, segment in enumerate(segments):
            source_in = round(
                float(audio_clip.source_in or 0.0)
                + (float(segment.source_in) - float(old_clip.source_in)), 6
            )
            segment_duration = float(segment.duration)
            patch = {
                "start": round(float(segment.start), 6),
                "duration": round(segment_duration, 6),
                "source_in": source_in,
                "source_out": round(source_in + segment_duration * float(segment.speed), 6),
                "speed": float(segment.speed),
                "linked_visual_overlay_id": segment.id,
                "follows_scene_video": False,
            }
            if not full_window_shift:
                # Fade boundaries follow the source window, not the global time
                # interval: Insert shifts the right clip's timeline end while its
                # source end remains unchanged.
                patch["fadeIn"] = (
                    min(max(0.0, float(audio_clip.fadeIn or 0.0)), segment_duration)
                    if abs(float(segment.source_in) - float(old_clip.source_in)) <= 1e-6 else 0.0
                )
                segment_source_end = float(segment.source_in) + segment_duration * float(segment.speed)
                patch["fadeOut"] = (
                    min(max(0.0, float(audio_clip.fadeOut or 0.0)), segment_duration)
                    if abs(segment_source_end - old_source_end) <= 1e-6 else 0.0
                )
                patch["keyframes"] = []
            if segment_index > 0:
                patch["id"] = f"audio-{uuid.uuid4().hex[:12]}"
            result.append(audio_clip.model_copy(update=patch))

    ir.audio = result
    return True


def _extend_project_for_visual_insert(ir: CompositionIR, duration: float) -> str:
    """Extend the project by an inserted clip and keep the scene timeline valid.

    Static stills and explicit gaps may safely extend in place. A moving video
    layer is never stretched beyond its source range; it receives an explicit
    black tail scene instead. Other tracks keep their timing by design.
    """
    old_total = float(ir.project.duration)
    amount = round(duration, 6)
    new_total = round(old_total + amount, 6)
    last_scene = ir.scenes[-1]
    # SceneIR validates layers in time order but preserves their input list order.
    # Locate the actual trailing layer by its timeline end; never assume [-1] is last.
    tail_index = max(
        range(len(last_scene.layers)),
        key=lambda index: float(last_scene.layers[index].start) + float(last_scene.layers[index].duration),
    )
    last_layer = last_scene.layers[tail_index]
    can_hold_still = (
        last_layer.type == LayerType.image
        and not any((
            last_layer.motion, last_layer.motion_recipe_id, last_layer.motion_version,
            last_layer.motion_instance, last_layer.transform, last_layer.keyframes, last_layer.mask,
        ))
    )
    if last_layer.type == LayerType.gap or can_hold_still:
        scene_data = last_scene.model_dump()
        scene_data["duration"] = round(float(last_scene.duration) + amount, 6)
        new_layers = list(last_scene.layers)
        new_layers[tail_index] = last_layer.model_copy(update={
            "duration": round(float(last_layer.duration) + amount, 6),
        })
        scene_data["layers"] = [layer.model_dump() for layer in new_layers]
        ir.scenes[-1] = SceneIR.model_validate(scene_data)
        extended_scene_id = last_scene.id
    else:
        tail = SceneIR(
            id=f"timeline-tail-{uuid.uuid4().hex[:10]}",
            start=old_total,
            duration=amount,
            text="插入操作延长的空白尾段",
            layers=[Layer(type=LayerType.gap, start=0.0, duration=amount)],
        )
        ir.scenes.append(tail)
        extended_scene_id = tail.id
    ir.project = ir.project.model_copy(update={"duration": new_total})
    return extended_scene_id


def _project_frame_time(ir: CompositionIR, value: float) -> float:
    """Snap a non-negative timeline time to the project's output-frame grid.

    Match browser Math.round for half-frame positions; Python round() is
    ties-to-even and can otherwise put an edit one frame away from the preview.
    """
    fps = max(1, int(ir.project.fps))
    frames = math.floor(float(value) * fps + 0.5)
    return round(frames / fps, 6)


def _project_frame_min_duration(ir: CompositionIR) -> float:
    """Minimum editable clip duration (at least 50 ms and one whole frame)."""
    fps = max(1, int(ir.project.fps))
    frames = max(1, math.ceil(0.05 * fps - 1e-9))
    return round(frames / fps, 6)


def _cmd_add_visual_overlay(
    ir: CompositionIR, target_id: str, payload: dict, project_id: str,
) -> Tuple[List[str], List[str]]:
    """Add an independent picture-in-picture clip from the project's trusted asset catalog."""
    if target_id != "overlays":
        raise EditorError("INVALID_TARGET", "add_visual_overlay 的 targetId 必须是 overlays")
    track_id = str(payload.get("trackId") or "overlay-main").strip()
    if not any(track.id == track_id for track in ir.visual_tracks):
        raise EditorError("NOT_FOUND", f"画面轨道 {track_id!r} 不存在", recoverable=False)
    _ensure_visual_track_unlocked(ir, track_id)
    asset_id = str(payload.get("assetId") or "").strip()
    if not asset_id:
        raise EditorError("INVALID_PAYLOAD", "必须先从项目素材库选择视频或图片")
    asset, resolved_file, source_duration = _overlay_source_duration(ir, asset_id, project_id)

    def number(key: str, default: float, minimum: float, maximum: Optional[float] = None) -> float:
        raw = payload.get(key, default)
        if isinstance(raw, bool):
            raise EditorError("INVALID_PARAMETER", f"{key} 必须是有限数字")
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise EditorError("INVALID_PARAMETER", f"{key} 必须是有限数字") from exc
        if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
            raise EditorError("INVALID_PARAMETER", f"{key} 超出允许范围")
        return value

    presentation_mode = str(payload.get("presentationMode") or payload.get("presentation_mode") or "picture_in_picture")
    if presentation_mode not in {"full_frame", "picture_in_picture", "sticker"}:
        raise EditorError("INVALID_PARAMETER", "presentationMode 必须是 full_frame、picture_in_picture 或 sticker")
    fit_mode = str(payload.get("fitMode") or payload.get("fit_mode") or "cover").strip()
    if fit_mode not in {"cover", "contain"}:
        raise EditorError("INVALID_PARAMETER", "fitMode 必须是 cover 或 contain")
    placement_mode = str(payload.get("placementMode") or payload.get("placement_mode") or "overlay").strip().lower()
    if placement_mode not in {"overlay", "overwrite", "insert"}:
        raise EditorError("INVALID_PARAMETER", "placementMode 必须是 overlay、overwrite 或 insert")
    presets = {
        "full_frame": (0.0, 0.0, 1.0, 1.0),
        "picture_in_picture": (0.65, 0.1, 0.3, 0.3),
        "sticker": (0.4, 0.4, 0.2, 0.2),
    }
    default_x, default_y, default_width, default_height = presets[presentation_mode]
    raw_audio_enabled = payload.get("sourceAudioEnabled", payload.get("source_audio_enabled", False))
    if not isinstance(raw_audio_enabled, bool):
        raise EditorError("INVALID_PARAMETER", "sourceAudioEnabled 必须是布尔值")
    source_audio_volume = number(
        "sourceAudioVolume",
        number("source_audio_volume", 1.0, 0.0, 2.0),
        0.0, 2.0,
    )
    if raw_audio_enabled and str(asset.type).lower() != "video":
        raise EditorError("INVALID_PARAMETER", "只有视频素材可以保留源音轨")
    total = float(ir.project.duration)
    fps = max(1, int(ir.project.fps))
    start = _project_frame_time(ir, number("start", 0.0, 0.0))
    if placement_mode == "insert":
        if start > total + 1e-6:
            raise EditorError("INVALID_PARAMETER", "插入点不能晚于当前项目末尾")
    elif start >= total:
        raise EditorError("INVALID_PARAMETER", "放置点必须在项目时长内")
    source_in = _project_frame_time(ir, number("sourceIn", 0.0, 0.0))
    speed = number("speed", 1.0, 0.25, 4.0)
    is_video_asset = str(asset.type).lower() == "video"
    if not is_video_asset and abs(speed - 1.0) > 1e-6:
        raise EditorError("INVALID_PARAMETER", "只有视频素材可以调整播放速度")
    remaining = max(0.05, total - start) if placement_mode == "insert" else total - start
    default_duration = min(3.0, remaining)
    if is_video_asset:
        default_duration = min(default_duration, (source_duration - source_in) / speed)
    duration = _project_frame_time(ir, number("duration", default_duration, 0.000001))
    if duration < (1.0 / fps) - 1e-6:
        raise EditorError("INVALID_PARAMETER", "源片段至少需要一个项目帧")
    if placement_mode != "insert" and start + duration > total + 1e-6:
        raise EditorError("INVALID_PARAMETER", "所选轨道的放置区间不能超出项目总时长；Insert 可延长时间线")
    if is_video_asset and source_in + duration * speed > source_duration + 0.05:
        raise EditorError("INVALID_SOURCE_RANGE", "视频源区间按播放速度换算后超出素材时长")

    x = number("x", default_x, 0.0, 1.0)
    y = number("y", default_y, 0.0, 1.0)
    box_width = number("width", default_width, 0.05, 1.0)
    box_height = number("height", default_height, 0.05, 1.0)
    opacity = number("opacity", 1.0, 0.0, 1.0)
    if x + box_width > 1.000001 or y + box_height > 1.000001:
        raise EditorError("INVALID_PARAMETER", "覆盖层的位置和尺寸不能超出画面边界")

    # Paths are resolved only from the known project AssetRef; never accept a
    # client-supplied path as a render input. Persist the project's canonical asset file.
    asset_index = next((i for i, item in enumerate(ir.assets) if item.id == asset_id), None)
    if asset_index is None:
        ir.assets.append(asset.model_copy(update={"file": resolved_file, "duration": source_duration or asset.duration}))
    else:
        ir.assets[asset_index] = asset.model_copy(update={"file": resolved_file, "duration": source_duration or asset.duration})
    raw_z_index = payload.get("zIndex", 0)
    if isinstance(raw_z_index, bool):
        raise EditorError("INVALID_PARAMETER", "zIndex 必须是整数")
    try:
        z_index_value = float(raw_z_index)
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PARAMETER", "zIndex 必须是整数") from exc
    if not math.isfinite(z_index_value) or not z_index_value.is_integer() or not -100 <= z_index_value <= 100:
        raise EditorError("INVALID_PARAMETER", "zIndex 必须是 -100 到 100 的整数")
    overlay = VisualOverlayClip(
        id=f"overlay-{uuid.uuid4().hex[:12]}", asset_id=asset_id,
        start=round(start, 6), duration=round(duration, 6), source_in=round(source_in, 6),
        speed=round(speed, 6), track_id=track_id, presentation_mode=presentation_mode, fit_mode=fit_mode,
        source_audio_enabled=raw_audio_enabled, source_audio_volume=source_audio_volume,
        x=x, y=y, width=box_width, height=box_height, opacity=opacity,
        z_index=int(z_index_value),
    )
    extended_scene_id = None
    linked_audio_changed = False
    if placement_mode in {"overwrite", "insert"}:
        previous_clips = list(ir.visual_overlays)
        if placement_mode == "overwrite":
            resulting_segments = _overwrite_visual_track_range(ir, track_id, start, duration)
        else:
            resulting_segments = _insert_visual_track_gap(ir, track_id, start, duration)
            extended_scene_id = _extend_project_for_visual_insert(ir, duration)
        linked_audio_changed = _sync_linked_audio_for_visual_timeline_edit(
            ir, previous_clips, resulting_segments,
        )
    ir.visual_overlays.append(overlay)
    key = f"visual-overlay:{overlay.id}"
    stale = [f"{key}:render", "project:visual-overlays:render", "project:preview"]
    if linked_audio_changed:
        stale.extend(["project:audio:render", "project:audio-tracks"])
    if extended_scene_id:
        stale.extend(_stale_of_scene(project_id="", scene_id=extended_scene_id))
        stale.append("project:timeline:render")
    return [key, f"visual-track:{track_id}"], list(dict.fromkeys(stale))


def _cmd_move_visual_overlay(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    index = _visual_overlay_index(ir, target_id)
    clip = ir.visual_overlays[index]
    _ensure_visual_track_unlocked(ir, str(clip.track_id or "overlay-main"))
    raw_start = payload.get("start")
    if isinstance(raw_start, bool):
        raise EditorError("INVALID_PAYLOAD", "move_visual_overlay 的 start 不能是布尔值")
    try:
        start = float(raw_start)
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PAYLOAD", "move_visual_overlay 需要数字 start") from exc
    if not math.isfinite(start) or start < 0:
        raise EditorError("INVALID_PARAMETER", "覆盖层的新位置超出项目时长")
    start = _project_frame_time(ir, start)
    # Legacy projects may contain off-frame clip durations. Normalize the end
    # boundary as well as the new start so moving an old clip cannot leave its
    # right edge between output frames.
    duration = _project_frame_time(ir, float(clip.duration))
    if duration < _project_frame_min_duration(ir) - 1e-6:
        raise EditorError("INVALID_PARAMETER", "覆盖层时长按项目帧率对齐后不足一帧")
    if start + duration > float(ir.project.duration) + 1e-6:
        raise EditorError("INVALID_PARAMETER", "覆盖层的新位置按项目帧率对齐后超出项目时长")
    source_asset = next((asset for asset in ir.assets if asset.id == clip.asset_id), None)
    if source_asset and str(getattr(source_asset.type, "value", source_asset.type)).lower() == "video":
        source_duration = float(source_asset.duration or 0.0)
        if source_duration > 0 and float(clip.source_in) + duration * float(clip.speed) > source_duration + 0.05:
            raise EditorError("INVALID_SOURCE_RANGE", "覆盖层时长按项目帧率与播放速度换算后超出视频素材时长")
    track_id = str(payload.get("trackId", clip.track_id) or "").strip()
    if not any(track.id == track_id for track in ir.visual_tracks):
        raise EditorError("NOT_FOUND", f"画面轨道 {track_id!r} 不存在", recoverable=False)
    _ensure_visual_track_unlocked(ir, track_id)

    linked = [
        (audio_index, audio_clip)
        for audio_index, audio_clip in enumerate(ir.audio)
        if audio_clip.linked_visual_overlay_id == clip.id
    ]
    for _audio_index, audio_clip in linked:
        _ensure_audio_lane_unlocked(ir, str(audio_clip.track_id or f"audio-{audio_clip.type.value}"))

    ir.visual_overlays[index] = clip.model_copy(update={
        "start": round(start, 6), "duration": round(duration, 6), "track_id": track_id,
    })
    changed = [f"visual-overlay:{clip.id}"]
    for audio_index, audio_clip in linked:
        ir.audio[audio_index] = audio_clip.model_copy(update={
            "start": round(start, 6),
            "duration": round(duration, 6),
            "source_out": round(float(audio_clip.source_in) + duration * float(clip.speed), 6),
            "speed": float(clip.speed),
            "fadeIn": min(max(0.0, float(audio_clip.fadeIn or 0.0)), duration),
            "fadeOut": min(max(0.0, float(audio_clip.fadeOut or 0.0)), duration),
        })
        changed.append(f"audio:{audio_clip.type.value}:{audio_index}")
    stale = [f"visual-overlay:{clip.id}:render", "project:visual-overlays:render", "project:preview"]
    if linked:
        stale.extend(["project:audio:render", "project:audio-tracks"])
    return changed, list(dict.fromkeys(stale))


def _cmd_trim_visual_overlay(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    index = _visual_overlay_index(ir, target_id)
    clip = ir.visual_overlays[index]
    _ensure_visual_track_unlocked(ir, str(clip.track_id or "overlay-main"))
    raw_values = (
        ("start", payload.get("start", clip.start)),
        ("sourceIn", payload.get("sourceIn", clip.source_in)),
        ("duration", payload.get("duration", clip.duration)),
    )
    if any(isinstance(value, bool) for _name, value in raw_values):
        raise EditorError("INVALID_PAYLOAD", "trim_visual_overlay 的 start/sourceIn/duration 不能是布尔值")
    try:
        start = float(raw_values[0][1])
        source_in = float(raw_values[1][1])
        duration = float(raw_values[2][1])
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PAYLOAD", "trim_visual_overlay 需要数字 start/sourceIn/duration") from exc
    if not all(math.isfinite(v) for v in (start, source_in, duration)):
        raise EditorError("INVALID_PARAMETER", "覆盖层裁剪参数必须为有限数字")
    if start < 0 or source_in < 0 or duration < 0.05:
        raise EditorError("INVALID_PARAMETER", "覆盖层裁剪区间无效或超出项目时长")
    start = _project_frame_time(ir, start)
    # Timeline edges follow the project frame grid. At non-1x playback, however,
    # source offsets are derived from timeline_delta * speed and need not land on
    # project-frame boundaries (e.g. 0.1s × 1.25 = 0.125 source seconds).
    source_in = (
        _project_frame_time(ir, source_in)
        if abs(float(clip.speed) - 1.0) <= 1e-6
        else round(source_in, 6)
    )
    duration = _project_frame_time(ir, duration)
    if duration < _project_frame_min_duration(ir) - 1e-6 or start + duration > float(ir.project.duration) + 1e-6:
        raise EditorError("INVALID_PARAMETER", "覆盖层裁剪区间按项目帧率对齐后无效或超出项目时长")
    if source_in != clip.source_in or duration != clip.duration:
        asset = next((item for item in ir.assets if item.id == clip.asset_id), None)
        if asset and asset.type == "video":
            source_duration = float(asset.duration or 0.0)
            if source_duration <= 0:
                from video_director import prober
                source_duration = float(prober.probe_media(asset.file).duration or 0.0)
            if source_in + duration * float(clip.speed) > source_duration + 0.05:
                raise EditorError("INVALID_SOURCE_RANGE", "覆盖层视频源区间按播放速度换算后超出素材时长")

    linked = [
        (audio_index, audio_clip)
        for audio_index, audio_clip in enumerate(ir.audio)
        if audio_clip.linked_visual_overlay_id == clip.id
    ]
    for _audio_index, audio_clip in linked:
        _ensure_audio_lane_unlocked(ir, str(audio_clip.track_id or f"audio-{audio_clip.type.value}"))
        if getattr(audio_clip, "keyframes", None):
            raise EditorError(
                "LINKED_AUDIO_AUTOMATION_UNSUPPORTED",
                "关联原声音轨包含关键帧自动化；请先打开原声音轨并解除链接，再修剪画面片段",
            )

    ir.visual_overlays[index] = clip.model_copy(update={
        "start": round(start, 6), "source_in": round(source_in, 6), "duration": round(duration, 6),
    })
    changed = [f"visual-overlay:{clip.id}"]
    for audio_index, audio_clip in linked:
        audio_update = {
            "start": round(start, 6),
            "source_in": round(source_in, 6),
            "source_out": round(source_in + duration * float(clip.speed), 6),
            "duration": round(duration, 6),
            "speed": float(clip.speed),
            "fadeIn": min(max(0.0, float(audio_clip.fadeIn or 0.0)), duration),
            "fadeOut": min(max(0.0, float(audio_clip.fadeOut or 0.0)), duration),
        }
        ir.audio[audio_index] = audio_clip.model_copy(update=audio_update)
        changed.append(f"audio:{audio_clip.type.value}:{audio_index}")
    stale = [f"visual-overlay:{clip.id}:render", "project:visual-overlays:render", "project:preview"]
    if linked:
        stale.extend(["project:audio:render", "project:audio-tracks"])
    return changed, list(dict.fromkeys(stale))


def _cmd_split_visual_overlay(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Split a global visual clip and all explicitly linked source-audio clips atomically."""
    index = _visual_overlay_index(ir, target_id)
    clip = ir.visual_overlays[index]
    _ensure_visual_track_unlocked(ir, str(clip.track_id or "overlay-main"))
    raw_at = payload.get("at")
    if isinstance(raw_at, bool):
        raise EditorError("INVALID_PAYLOAD", "split_visual_overlay 的 at 不能是布尔值")
    try:
        at = float(raw_at)
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PAYLOAD", "split_visual_overlay 需要数字 at") from exc

    if not math.isfinite(at):
        raise EditorError("INVALID_PARAMETER", "切分播放头必须是有限数字")
    start = _project_frame_time(ir, float(clip.start))
    end = _project_frame_time(ir, float(clip.start) + float(clip.duration))
    split_at = _project_frame_time(ir, at)
    minimum_half = _project_frame_min_duration(ir)
    if split_at <= start + minimum_half - 1e-6 or split_at >= end - minimum_half + 1e-6:
        raise EditorError(
            "SPLIT_OUTSIDE_CLIP",
            "切分播放头按项目帧率对齐后必须位于片段内部，且两侧各保留至少 0.05 秒",
        )
    left_duration = round(split_at - start, 6)
    right_duration = round(end - split_at, 6)
    if left_duration < minimum_half - 1e-6 or right_duration < minimum_half - 1e-6:
        raise EditorError("SPLIT_OUTSIDE_CLIP", "切分后两侧片段都必须至少保留 0.05 秒")

    asset = next((item for item in ir.assets if item.id == clip.asset_id), None)
    is_video = bool(asset and str(getattr(asset.type, "value", asset.type)).lower() == "video")
    source_in = (
        _project_frame_time(ir, float(clip.source_in))
        if abs(float(clip.speed) - 1.0) <= 1e-6
        else round(float(clip.source_in), 6)
    )
    right_source_in = round(source_in + left_duration * float(clip.speed), 6) if is_video else source_in
    if is_video and asset:
        source_duration = float(asset.duration or 0.0)
        if source_duration > 0 and right_source_in + right_duration * float(clip.speed) > source_duration + 0.05:
            raise EditorError("INVALID_SOURCE_RANGE", "切分后的右侧视频源区间超出素材时长")

    linked = [
        audio_clip for audio_clip in ir.audio
        if audio_clip.linked_visual_overlay_id == clip.id
    ]
    for audio_clip in linked:
        _ensure_audio_lane_unlocked(ir, str(audio_clip.track_id or f"audio-{audio_clip.type.value}"))
        if getattr(audio_clip, "keyframes", None):
            raise EditorError(
                "LINKED_AUDIO_AUTOMATION_UNSUPPORTED",
                "关联原声音轨包含关键帧自动化；请先解除链接再切分，避免丢失音量曲线",
            )

    left = clip.model_copy(update={
        "start": round(start, 6), "duration": left_duration, "source_in": source_in,
    })
    right = clip.model_copy(update={
        "id": f"overlay-{uuid.uuid4().hex[:12]}",
        "start": round(split_at, 6),
        "duration": right_duration,
        "source_in": right_source_in,
    })
    ir.visual_overlays[index] = left
    ir.visual_overlays.insert(index + 1, right)

    changed = [f"visual-overlay:{left.id}", f"visual-overlay:{right.id}"]
    stale = [
        f"visual-overlay:{left.id}:render",
        f"visual-overlay:{right.id}:render",
        "project:visual-overlays:render",
        "project:preview",
    ]
    if linked:
        updated_audio = []
        for audio_clip in ir.audio:
            if audio_clip.linked_visual_overlay_id != clip.id:
                updated_audio.append(audio_clip)
                continue
            left_audio_index = len(updated_audio)
            audio_source_in = source_in
            source_boundary = audio_source_in + left_duration * float(clip.speed)
            old_fade_in = max(0.0, float(audio_clip.fadeIn or 0.0))
            old_fade_out = max(0.0, float(audio_clip.fadeOut or 0.0))
            left_audio = audio_clip.model_copy(update={
                "start": round(start, 6),
                "duration": left_duration,
                "source_in": audio_source_in,
                "source_out": source_boundary,
                "speed": float(clip.speed),
                "linked_visual_overlay_id": left.id,
                "follows_scene_video": False,
                "fadeIn": min(old_fade_in, left_duration),
                "fadeOut": 0.0,
            })
            right_audio = audio_clip.model_copy(update={
                "id": f"audio-{uuid.uuid4().hex[:12]}",
                "start": round(split_at, 6),
                "duration": right_duration,
                "source_in": right_source_in,
                "source_out": right_source_in + right_duration * float(clip.speed),
                "speed": float(clip.speed),
                "linked_visual_overlay_id": right.id,
                "follows_scene_video": False,
                "fadeIn": 0.0,
                "fadeOut": min(old_fade_out, right_duration),
                "keyframes": [],
            })
            updated_audio.extend([left_audio, right_audio])
            changed.extend([
                f"audio:{left_audio.type.value}:{left_audio_index}",
                f"audio:{right_audio.type.value}:{left_audio_index + 1}",
            ])
        ir.audio = updated_audio
        stale.extend(["project:audio:render", "project:audio-tracks"])
    return changed, list(dict.fromkeys(stale))


def _cmd_set_visual_overlay(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    index = _visual_overlay_index(ir, target_id)
    clip = ir.visual_overlays[index]
    _ensure_visual_track_unlocked(ir, str(clip.track_id or "overlay-main"))
    if "fitMode" in payload and "fit_mode" not in payload:
        payload = {**payload, "fit_mode": payload["fitMode"]}
    allowed = {
        "x", "y", "width", "height", "opacity", "z_index", "presentation_mode", "fit_mode",
        "source_audio_enabled", "source_audio_volume", "transform", "speed",
    }
    patch = {key: payload[key] for key in allowed if key in payload}
    if "transform" in patch:
        raw_transform = patch["transform"]
        transform_fields = {"x", "y", "scale", "rotation", "opacity", "anchor"}
        if not isinstance(raw_transform, dict) or set(raw_transform) - transform_fields:
            raise EditorError("INVALID_PARAMETER", "transform 必须是合法的结构化静态变换对象")
        try:
            patch["transform"] = Transform.model_validate({
                **(clip.transform.model_dump() if clip.transform is not None else Transform().model_dump()),
                **raw_transform,
            })
        except ValueError as exc:
            raise EditorError("INVALID_PARAMETER", f"变换参数无效：{exc}") from exc
    if clip.transform is not None:
        legacy_geometry = {"x", "y", "width", "height", "opacity"}.intersection(patch)
        if legacy_geometry and "transform" not in patch:
            raise EditorError(
                "TRANSFORMED_OVERLAY_REQUIRES_TRANSFORM_EDIT",
                "该片段继承了主画面变换；请使用变换编辑字段，避免矩形布局覆盖原变换",
            )
        if "presentation_mode" in patch and patch["presentation_mode"] != "full_frame":
            raise EditorError(
                "TRANSFORMED_OVERLAY_REQUIRES_FULL_FRAME",
                "继承主画面变换的片段暂不支持切换为画中画/贴纸模式",
            )
    if "transform" in patch and set(patch).intersection({"x", "y", "width", "height", "opacity"}):
        raise EditorError("INVALID_PARAMETER", "不能在同一命令中混用矩形布局字段与结构化变换字段")
    if not patch:
        raise EditorError("INVALID_PAYLOAD", "没有可更新的覆盖层属性")
    linked = [
        (audio_index, audio_clip)
        for audio_index, audio_clip in enumerate(ir.audio)
        if audio_clip.linked_visual_overlay_id == clip.id
    ]
    speed_changed = False
    if "speed" in patch:
        if isinstance(patch["speed"], bool):
            raise EditorError("INVALID_PARAMETER", "speed 必须是 0.25–4 的数字，不能是布尔值")
        try:
            requested_speed = float(patch["speed"])
        except (TypeError, ValueError) as exc:
            raise EditorError("INVALID_PARAMETER", "speed 必须是 0.25–4 的数字") from exc
        if not math.isfinite(requested_speed) or not 0.25 <= requested_speed <= 4.0:
            raise EditorError("INVALID_PARAMETER", "speed 必须位于 0.25–4")
        source_asset = next((asset for asset in ir.assets if asset.id == clip.asset_id), None)
        is_video = source_asset is not None and str(getattr(source_asset.type, "value", source_asset.type)).lower() == "video"
        if not is_video and abs(requested_speed - 1.0) > 1e-6:
            raise EditorError("INVALID_PARAMETER", "只有视频素材可以调整播放速度")
        speed_changed = abs(requested_speed - float(clip.speed)) > 1e-6
        if speed_changed:
            if any(getattr(audio_clip, "keyframes", None) for _audio_index, audio_clip in linked):
                raise EditorError(
                    "LINKED_AUDIO_AUTOMATION_UNSUPPORTED",
                    "关联原声音轨包含关键帧；请先解除链接后再调整视频速度，避免改变自动化曲线",
                )
            patch["duration"] = _project_frame_time(
                ir, float(clip.duration) * float(clip.speed) / requested_speed,
            )
            if patch["duration"] < _project_frame_min_duration(ir) - 1e-6:
                raise EditorError("INVALID_PARAMETER", "调整速度后片段不足一帧")
    if linked and patch.get("source_audio_enabled") is True:
        raise EditorError(
            "SOURCE_AUDIO_ALREADY_LINKED",
            "该画面已使用独立关联原声音轨；请在原声音轨中调节或解除链接，不能再同时启用内嵌原声",
        )
    if linked and ("source_audio_volume" in patch or speed_changed):
        for _audio_index, audio_clip in linked:
            _ensure_audio_lane_unlocked(ir, str(audio_clip.track_id or f"audio-{audio_clip.type.value}"))
    if "presentation_mode" in patch and patch["presentation_mode"] not in {"full_frame", "picture_in_picture", "sticker"}:
        raise EditorError("INVALID_PARAMETER", "presentation_mode 不受支持")
    if "source_audio_enabled" in patch and not isinstance(patch["source_audio_enabled"], bool):
        raise EditorError("INVALID_PARAMETER", "source_audio_enabled 必须是布尔值")
    if patch.get("source_audio_enabled"):
        source_asset = next((asset for asset in ir.assets if asset.id == clip.asset_id), None)
        if source_asset is None or str(source_asset.type).lower() != "video":
            raise EditorError("INVALID_PARAMETER", "只有视频素材可以保留源音轨")
    if "presentation_mode" in patch and not any(key in patch for key in ("x", "y", "width", "height")):
        patch.update({
            "full_frame": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            "picture_in_picture": {"x": 0.65, "y": 0.1, "width": 0.3, "height": 0.3},
            "sticker": {"x": 0.4, "y": 0.4, "width": 0.2, "height": 0.2},
        }[patch["presentation_mode"]])
    try:
        updated = VisualOverlayClip(**{**clip.model_dump(), **patch})
    except Exception as exc:
        raise EditorError("INVALID_PARAMETER", f"覆盖层属性无效：{exc}") from exc
    if updated.start + updated.duration > float(ir.project.duration) + 1e-6:
        raise EditorError("INVALID_PARAMETER", "覆盖层时间区间超出项目时长")
    if "speed" in patch:
        source_asset = next((asset for asset in ir.assets if asset.id == updated.asset_id), None)
        if source_asset is not None and str(getattr(source_asset.type, "value", source_asset.type)).lower() == "video":
            source_duration = float(source_asset.duration or 0.0)
            if source_duration <= 0:
                from video_director import prober
                try:
                    source_duration = float(prober.probe_media(source_asset.file).duration or 0.0)
                except Exception as exc:
                    raise EditorError("ASSET_PROBE_FAILED", f"无法验证速度调整后的源区间：{exc}") from exc
            if float(updated.source_in) + float(updated.duration) * float(updated.speed) > source_duration + 0.05:
                raise EditorError("INVALID_SOURCE_RANGE", "速度调整后的源区间超出视频素材时长")
        elif abs(float(updated.speed) - 1.0) > 1e-6:
            raise EditorError("INVALID_PARAMETER", "只有视频素材可以调整播放速度")
    ir.visual_overlays[index] = updated
    key = f"visual-overlay:{clip.id}"
    changed = [key]
    stale = [f"{key}:render", "project:visual-overlays:render", "project:preview"]
    if linked and ("source_audio_volume" in patch or speed_changed):
        volume = float(updated.source_audio_volume)
        for audio_index, audio_clip in linked:
            audio_patch = {"volume": volume}
            if speed_changed:
                audio_patch.update({
                    "start": round(float(updated.start), 6),
                    "duration": round(float(updated.duration), 6),
                    "source_in": round(float(updated.source_in), 6),
                    "source_out": round(float(updated.source_in) + float(updated.duration) * float(updated.speed), 6),
                    "speed": float(updated.speed),
                    "fadeIn": min(max(0.0, float(audio_clip.fadeIn or 0.0)), float(updated.duration)),
                    "fadeOut": min(max(0.0, float(audio_clip.fadeOut or 0.0)), float(updated.duration)),
                })
            ir.audio[audio_index] = audio_clip.model_copy(update=audio_patch)
            changed.append(f"audio:{audio_clip.type.value}:{audio_index}")
        stale.extend(["project:audio:render", "project:audio-tracks"])
    return changed, list(dict.fromkeys(stale))



def _cmd_separate_visual_overlay_source_audio(
    ir: CompositionIR, target_id: str, payload: dict, project_id: str,
) -> Tuple[List[str], List[str]]:
    """Detach a video overlay's embedded source audio into an editable original-audio clip.

    The new audio clip preserves the overlay's global start, source_in, duration and
    gain. Turning off the embedded source-audio flag in the same revision guarantees
    that render_composition consumes exactly one copy of the source sound.
    """
    index = _visual_overlay_index(ir, target_id)
    clip = ir.visual_overlays[index]
    _ensure_visual_track_unlocked(ir, str(clip.track_id or "overlay-main"))
    if not clip.source_audio_enabled:
        raise EditorError(
            "SOURCE_AUDIO_NOT_ENABLED",
            "该视频片段当前没有启用内嵌源音频；先在片段设置中启用并保存，再执行拆分",
        )

    asset, resolved_file, source_duration = _overlay_source_duration(ir, clip.asset_id, project_id)
    if str(asset.type).lower() != "video":
        raise EditorError("ASSET_TYPE_MISMATCH", "只有视频素材可以拆分源音频")
    from video_director import prober
    try:
        probe = prober.probe_media(resolved_file)
    except Exception as exc:
        raise EditorError("ASSET_PROBE_FAILED", f"拆分前无法探测视频源音频：{exc}") from exc
    if not getattr(probe, "has_audio_stream", False):
        raise EditorError("SOURCE_AUDIO_MISSING", "该视频素材没有可解码的音频流，未修改项目")
    actual_duration = float(getattr(probe, "duration", 0.0) or source_duration or 0.0)
    if not math.isfinite(actual_duration) or actual_duration <= 0:
        raise EditorError("ASSET_PROBE_FAILED", "视频素材没有有效时长，无法拆分源音频")

    source_in = float(clip.source_in or 0.0)
    duration = float(clip.duration)
    source_out = source_in + duration * float(clip.speed)
    if (
        not math.isfinite(source_in) or not math.isfinite(duration)
        or source_in < 0 or duration <= 0
        or source_out > actual_duration + 0.05
    ):
        raise EditorError(
            "INVALID_SOURCE_RANGE",
            "视频片段的源区间超出可探测的源素材时长，未修改项目",
        )

    # Detect any already represented copy of this source sound, not just a
    # same-role original clip. Otherwise a dialogue clip could keep playing while
    # this command adds the same source window as original audio.
    overlapping = []
    for audio_index, audio_clip in enumerate(ir.audio):
        if audio_clip.asset_id != clip.asset_id:
            continue
        audio_start = float(audio_clip.start)
        audio_end = audio_start + float(audio_clip.duration)
        audio_source_in = float(audio_clip.source_in or 0.0)
        audio_source_out = (
            float(audio_clip.source_out)
            if audio_clip.source_out is not None
            else audio_source_in + float(audio_clip.duration) * float(getattr(audio_clip, "speed", 1.0) or 1.0)
        )
        overlaps_timeline = (
            max(audio_start, float(clip.start))
            < min(audio_end, float(clip.start) + duration) - 0.001
        )
        overlaps_source = (
            max(audio_source_in, source_in) < min(audio_source_out, source_out) - 0.001
        )
        if not (overlaps_timeline and overlaps_source):
            continue
        exact_window = (
            abs(audio_start - float(clip.start)) <= 0.001
            and abs(float(audio_clip.duration) - duration) <= 0.001
            and abs(float(getattr(audio_clip, "speed", 1.0)) - float(clip.speed)) <= 0.001
            and abs(audio_source_in - source_in) <= 0.001
            and abs(audio_source_out - source_out) <= 0.001
        )
        overlapping.append((audio_index, audio_clip, exact_window))

    if len(overlapping) > 1:
        raise EditorError(
            "SOURCE_AUDIO_DUPLICATE_CONFLICT",
            "时间线上已有多条与此视频原声重叠的音频片段；先检查重复声音，再拆分",
        )

    reused_audio_key = None
    if overlapping:
        audio_index, existing, exact_window = overlapping[0]
        if not exact_window or existing.type != AudioTrackType.original:
            raise EditorError(
                "SOURCE_AUDIO_DUPLICATE_CONFLICT",
                "已有音频片段与该视频原声的源区间及时间位置重叠；为避免重复发声或改变音频角色，请先处理已有片段",
            )
        if (
            existing.muted
            or abs(float(existing.volume) - float(clip.source_audio_volume)) > 0.001
            or float(existing.fadeIn or 0.0) > 0.001
            or float(existing.fadeOut or 0.0) > 0.001
            or bool(existing.ducking)
            or bool(existing.keyframes)
        ):
            raise EditorError(
                "SOURCE_AUDIO_DUPLICATE_CONFLICT",
                "同一源区间已有独立原声轨，但音量/淡入淡出/自动化与画面源音频不一致；为避免改变成片声音，未执行拆分",
            )
        ir.audio[audio_index] = existing.model_copy(update={"follows_scene_video": False})
        reused_audio_key = f"audio:original:{audio_index}"
    else:
        lane = next((item for item in ir.audio_tracks if item.id == "audio-original"), None)
        if lane is None:
            lane = next(
                (item for item in ir.audio_tracks if item.name.strip().casefold() == "原声"),
                None,
            )
        if lane is None:
            lane_name = "原声"
            existing_names = {item.name.strip().casefold() for item in ir.audio_tracks}
            suffix = 1
            while lane_name.casefold() in existing_names:
                lane_name = f"视频源原声 {suffix}"
                suffix += 1
            lane = AudioLane(
                id="audio-original",
                name=lane_name,
                order=max((item.order for item in ir.audio_tracks), default=-1) + 1,
            )
            ir.audio_tracks.append(lane)
        _ensure_audio_lane_unlocked(ir, lane.id)
        audio_id = f"audio-source-{uuid.uuid4().hex[:12]}"
        ir.audio.append(AudioTrack(
            type=AudioTrackType.original,
            id=audio_id,
            track_id=lane.id,
            asset_id=clip.asset_id,
            start=round(float(clip.start), 3),
            duration=round(duration, 3),
            source_in=round(source_in, 3),
            source_out=round(source_out, 3),
            speed=float(clip.speed),
            volume=float(clip.source_audio_volume),
            follows_scene_video=False,
        ))
        reused_audio_key = f"audio:original:{len(ir.audio) - 1}"

    asset_index = next((i for i, item in enumerate(ir.assets) if item.id == asset.id), None)
    resolved_asset = asset.model_copy(update={"file": resolved_file, "duration": actual_duration})
    if asset_index is None:
        ir.assets.append(resolved_asset)
    else:
        ir.assets[asset_index] = resolved_asset
    ir.visual_overlays[index] = clip.model_copy(update={"source_audio_enabled": False})

    changed = [f"visual-overlay:{clip.id}", reused_audio_key]
    stale = [
        f"visual-overlay:{clip.id}:render",
        "project:visual-overlays:render",
        "project:audio:render",
        "project:audio-tracks",
        "project:preview",
    ]
    return changed, stale


def _cmd_remove_visual_overlay(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    index = _visual_overlay_index(ir, target_id)
    clip = ir.visual_overlays[index]
    _ensure_visual_track_unlocked(ir, str(clip.track_id or "overlay-main"))
    linked = [
        (audio_index, audio_clip)
        for audio_index, audio_clip in enumerate(ir.audio)
        if audio_clip.linked_visual_overlay_id == clip.id
    ]
    # A linked clip is one edit unit. Refuse the whole delete if its sound lane is
    # locked; never remove the picture and leave an invalid/dangling audio reference.
    for _audio_index, audio_clip in linked:
        _ensure_audio_lane_unlocked(ir, str(audio_clip.track_id or f"audio-{audio_clip.type.value}"))
    ir.visual_overlays.pop(index)
    removed_indices = {audio_index for audio_index, _audio_clip in linked}
    ir.audio = [audio_clip for i, audio_clip in enumerate(ir.audio) if i not in removed_indices]
    changed = [f"visual-overlay:{clip.id}"]
    changed.extend(f"audio:{audio_clip.type.value}:{audio_index}" for audio_index, audio_clip in linked)
    stale = [f"visual-overlay:{clip.id}:render", "project:visual-overlays:render", "project:preview"]
    if linked:
        stale.extend(["project:audio:render", "project:audio-tracks"])
    return changed, list(dict.fromkeys(stale))



def _cmd_paste_visual_overlay(
    ir: CompositionIR, target_id: str, payload: dict, project_id: str,
) -> Tuple[List[str], List[str]]:
    """Paste a copied global visual clip onto any unlocked visual track."""
    if target_id != "overlays":
        raise EditorError("INVALID_TARGET", "paste_visual_overlay 的 targetId 必须是 overlays")
    from video_director.models import VisualOverlayClip

    raw_clip = payload.get("clip")
    if not isinstance(raw_clip, dict):
        raise EditorError("INVALID_PAYLOAD", "粘贴画面片段缺少 clip 对象")
    try:
        source = VisualOverlayClip.model_validate(raw_clip)
    except Exception as exc:
        raise EditorError("INVALID_PAYLOAD", f"复制的画面片段数据无效：{exc}") from exc

    # Only a source already present in this project is eligible for clipboard paste.
    known_asset = any(asset.id == source.asset_id for asset in ir.assets) or any(
        clip.asset_id == source.asset_id for clip in ir.visual_overlays
    )
    if not known_asset:
        raise EditorError("ASSET_NOT_FOUND", "复制的画面素材不属于当前项目，无法粘贴")
    track_id = str(payload.get("trackId") or source.track_id or "overlay-main").strip()
    if not any(track.id == track_id for track in ir.visual_tracks):
        raise EditorError("NOT_FOUND", f"目标画面轨道 {track_id!r} 不存在", recoverable=False)
    _ensure_visual_track_unlocked(ir, track_id)

    raw_start = payload.get("start")
    if isinstance(raw_start, bool):
        raise EditorError("INVALID_PAYLOAD", "start 不能是布尔值")
    try:
        start = float(raw_start)
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PAYLOAD", "粘贴位置 start 必须是数字") from exc
    if not math.isfinite(start) or start < 0:
        raise EditorError("INVALID_PARAMETER", "粘贴位置必须是有限非负数字")
    start = _project_frame_time(ir, start)
    duration = _project_frame_time(ir, float(source.duration))
    if duration < _project_frame_min_duration(ir) - 1e-6:
        raise EditorError("INVALID_PARAMETER", "复制片段按项目帧率对齐后不足一帧")
    if start + duration > float(ir.project.duration) + 1e-6:
        raise EditorError("INVALID_TIMELINE_RANGE", "粘贴后的画面片段超出项目时长")

    asset = next((item for item in ir.assets if item.id == source.asset_id), None)
    if asset and str(getattr(asset.type, "value", asset.type)).lower() == "video":
        source_duration = float(asset.duration or 0.0)
        if source_duration > 0 and float(source.source_in) + duration * float(source.speed) > source_duration + 0.05:
            raise EditorError("INVALID_SOURCE_RANGE", "复制片段的源区间超出视频素材时长")

    pasted = source.model_copy(update={
        "id": f"overlay-{uuid.uuid4().hex[:12]}",
        "start": round(start, 6),
        "duration": round(duration, 6),
        "track_id": track_id,
    })
    ir.visual_overlays.append(pasted)
    return [f"visual-overlay:{pasted.id}", f"visual-track:{track_id}"], [
        f"visual-overlay:{pasted.id}:render", "project:visual-overlays:render",
        "project:timeline:render", "project:preview",
    ]


def _audio_clip_index(ir: CompositionIR, target_id: str) -> int:
    """Resolve a per-instance audio clip target (audio#<index>).

    Track type alone is not an identity: a project may contain several BGM/SFX
    clips of the same type. The editor command's expectedVersion protects this
    index from stale-client writes.
    """
    if not target_id.startswith("audio#"):
        raise EditorError("INVALID_TARGET", "音频片段 targetId 必须为 audio#<index>")
    try:
        index = int(target_id.split("#", 1)[1])
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_TARGET", f"音频片段索引非法：{target_id}") from exc
    if index < 0 or index >= len(ir.audio):
        raise EditorError("NOT_FOUND", f"音频片段 {target_id} 不存在", recoverable=False)
    return index


def _audio_lane_index(ir: CompositionIR, target_id: str) -> int:
    index = next((i for i, lane in enumerate(ir.audio_tracks) if lane.id == target_id), -1)
    if index < 0:
        raise EditorError("NOT_FOUND", f"音频轨道 {target_id!r} 不存在", recoverable=False)
    return index


def _ensure_audio_lane_unlocked(ir: CompositionIR, lane_id: str):
    lane = ir.audio_tracks[_audio_lane_index(ir, lane_id)]
    if lane.locked:
        raise EditorError("TRACK_LOCKED", f"音频轨道“{lane.name}”已锁定，先解锁后再编辑")
    return lane


def _cmd_add_audio_track(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    from video_director.models import AudioLane

    if target_id != "audio-tracks":
        raise EditorError("INVALID_TARGET", "add_audio_track 的 targetId 必须是 audio-tracks")
    name = str(payload.get("name") or "").strip()
    if not name:
        number = 1
        while any(lane.name.casefold() == f"音轨 {number}".casefold() for lane in ir.audio_tracks):
            number += 1
        name = f"音轨 {number}"
    if len(name) > 60:
        raise EditorError("INVALID_PAYLOAD", "轨道名称最多 60 个字符")
    if any(lane.name.casefold() == name.casefold() for lane in ir.audio_tracks):
        raise EditorError("DUPLICATE_TRACK_NAME", f"音频轨道名称已存在：{name}")
    lane = AudioLane(
        id=f"atrack-{uuid.uuid4().hex[:12]}",
        name=name,
        order=max((item.order for item in ir.audio_tracks), default=-1) + 1,
    )
    ir.audio_tracks.append(lane)
    return [f"audio-track:{lane.id}"], ["project:audio-tracks", "project:preview"]


def _cmd_rename_audio_track(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    index = _audio_lane_index(ir, target_id)
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 60:
        raise EditorError("INVALID_PAYLOAD", "轨道名称必须为 1–60 个字符")
    if any(lane.id != target_id and lane.name.casefold() == name.casefold() for lane in ir.audio_tracks):
        raise EditorError("DUPLICATE_TRACK_NAME", f"音频轨道名称已存在：{name}")
    ir.audio_tracks[index] = ir.audio_tracks[index].model_copy(update={"name": name})
    return [f"audio-track:{target_id}"], ["project:audio-tracks"]


def _cmd_remove_audio_track(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    index = _audio_lane_index(ir, target_id)
    if len(ir.audio_tracks) <= 1:
        raise EditorError("LAST_AUDIO_TRACK", "项目至少需要保留一条音频轨道")
    clips = [(i, clip) for i, clip in enumerate(ir.audio) if clip.track_id == target_id]
    if clips and payload.get("deleteContents") is not True:
        raise EditorError(
            "AUDIO_TRACK_NOT_EMPTY",
            "该音轨仍有片段；确认同时删除轨道内容时，请传入 deleteContents=true",
        )
    linked_visual_ids = {clip.linked_visual_overlay_id for _i, clip in clips if clip.linked_visual_overlay_id}
    for overlay_id in linked_visual_ids:
        overlay_index = next((i for i, overlay in enumerate(ir.visual_overlays) if overlay.id == overlay_id), None)
        if overlay_index is not None:
            overlay = ir.visual_overlays[overlay_index]
            _ensure_visual_track_unlocked(ir, str(overlay.track_id or "overlay-main"))
            ir.visual_overlays[overlay_index] = overlay.model_copy(update={"source_audio_enabled": False})
    removed_indices = {i for i, _clip in clips}
    ir.audio = [clip for i, clip in enumerate(ir.audio) if i not in removed_indices]
    ir.audio_tracks.pop(index)
    ordered = sorted(ir.audio_tracks, key=lambda lane: lane.order)
    ir.audio_tracks = [lane.model_copy(update={"order": order}) for order, lane in enumerate(ordered)]
    stale = ["project:audio-tracks", "project:audio:render", "project:preview"]
    if linked_visual_ids:
        stale.extend(["project:visual-overlays:render", "project:timeline:render"])
    changed = [f"audio-track:{target_id}"] + [f"audio:{clip.type.value}:{i}" for i, clip in clips]
    changed.extend(f"visual-overlay:{overlay_id}" for overlay_id in linked_visual_ids)
    return changed, list(dict.fromkeys(stale))


def _cmd_set_track_locked(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Persist the lock state of an actual audio/visual lane in one undoable revision."""
    track_type = payload.get("trackType")
    locked = payload.get("locked")
    if not isinstance(locked, bool):
        raise EditorError("INVALID_PAYLOAD", "locked 必须是布尔值")
    if track_type == "audio":
        index = _audio_lane_index(ir, target_id)
        lane = ir.audio_tracks[index]
        if lane.locked == locked:
            raise EditorError("NOOP", "音频轨道已处于该锁定状态")
        ir.audio_tracks[index] = lane.model_copy(update={"locked": locked})
        key = f"audio-track:{target_id}"
        return [key], ["project:audio-tracks", "project:preview"]
    if track_type == "visual":
        index = _visual_track_index(ir, target_id)
        track = ir.visual_tracks[index]
        if track.locked == locked:
            raise EditorError("NOOP", "画面轨道已处于该锁定状态")
        ir.visual_tracks[index] = track.model_copy(update={"locked": locked})
        key = f"visual-track:{target_id}"
        return [key], ["project:visual-tracks", "project:visual-overlays:render", "project:preview"]
    raise EditorError("INVALID_PAYLOAD", "trackType 必须是 audio 或 visual")

def _ensure_audio_roles_unlocked(ir: CompositionIR, track_types):
    """Reject Audio Bus changes if any affected physical lane is locked."""
    roles = {str(value) for value in track_types}
    if "ambient" in roles or "ambience" in roles:
        roles.update({"ambient", "ambience"})
    defaults = {
        "narration": "audio-narration", "dialogue": "audio-dialogue",
        "original": "audio-original", "bgm": "audio-bgm", "sfx": "audio-sfx",
        "ambient": "audio-ambience", "ambience": "audio-ambience",
    }
    affected_ids = {defaults[role] for role in roles if role in defaults}
    for clip in ir.audio:
        if clip.type.value in roles:
            affected_ids.add(clip.track_id or defaults.get(clip.type.value, ""))
    for lane in ir.audio_tracks:
        if lane.id in affected_ids and lane.locked:
            raise EditorError("TRACK_LOCKED", f"音频轨道“{lane.name}”已锁定，音轨混音命令不能修改它")

def _cmd_add_audio_clip(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Add a real, user-selected SFX asset as an independently editable timeline clip.

    The write boundary resolves asset IDs from the trusted local audio library;
    free-form cue text and arbitrary filesystem paths are never rendered directly.
    """
    if target_id != "sfx":
        raise EditorError("INVALID_TARGET", "add_audio_clip 目前只接受 targetId=sfx")
    asset_id = str(payload.get("assetId") or "").strip()
    if not asset_id:
        raise EditorError("INVALID_PAYLOAD", "必须选择音效素材 assetId")

    from video_director import audio as vd_audio
    library = vd_audio.scan_library()
    asset = library.get(asset_id)
    if asset is None:
        raise EditorError("ASSET_NOT_FOUND", f"音效库不存在素材 {asset_id!r}", recoverable=False)
    if str(asset.type).lower() != "sfx":
        raise EditorError("ASSET_TYPE_MISMATCH", f"素材 {asset_id!r} 不是音效（SFX）", recoverable=False)
    if not asset.file or not Path(asset.file).is_file():
        raise EditorError("ASSET_NOT_FOUND", f"音效文件不可用：{asset_id!r}", recoverable=False)

    duration_from_asset = getattr(asset, "duration", None)
    if duration_from_asset is None:
        try:
            asset = vd_audio.ensure_audio_duration(asset)
            duration_from_asset = asset.duration
        except Exception as exc:
            raise EditorError("ASSET_PROBE_FAILED", f"无法读取音效时长：{exc}") from exc
    try:
        source_duration = float(duration_from_asset)
    except (TypeError, ValueError) as exc:
        raise EditorError("ASSET_PROBE_FAILED", "音效缺少有效时长") from exc
    if not math.isfinite(source_duration) or source_duration <= 0:
        raise EditorError("ASSET_PROBE_FAILED", "音效缺少有效时长")

    def _number(key: str, default: float, minimum: float, maximum: Optional[float] = None) -> float:
        raw = payload.get(key, default)
        if isinstance(raw, bool):
            raise EditorError("INVALID_PARAMETER", f"{key} 必须是有限数字")
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise EditorError("INVALID_PARAMETER", f"{key} 必须是有限数字") from exc
        if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
            raise EditorError("INVALID_PARAMETER", f"{key} 超出允许范围")
        return value

    total = float(ir.project.duration)
    start = _number("start", 0.0, 0.0)
    if start >= total:
        raise EditorError("INVALID_PARAMETER", "音效起点必须位于项目时间范围内")
    clip_duration = _number("duration", min(source_duration, total - start), 0.01, source_duration)
    if start + clip_duration > total + 1e-6:
        raise EditorError("INVALID_PARAMETER", "音效片段不能超出项目总时长")
    volume = _number("volume", 0.6, 0.0, 2.0)
    fade_in = _number("fadeIn", 0.0, 0.0, clip_duration)
    fade_out = _number("fadeOut", 0.0, 0.0, clip_duration)

    sfx_lane = next((lane for lane in ir.audio_tracks if lane.id == "audio-sfx"), None)
    if sfx_lane is None:
        from video_director.models import AudioLane
        lane_name = "音效"
        suffix = 2
        existing_names = {lane.name.casefold() for lane in ir.audio_tracks}
        while lane_name.casefold() in existing_names:
            lane_name = f"音效 {suffix}"
            suffix += 1
        sfx_lane = AudioLane(
            id="audio-sfx", name=lane_name,
            order=max((lane.order for lane in ir.audio_tracks), default=-1) + 1,
        )
        ir.audio_tracks.append(sfx_lane)
    _ensure_audio_lane_unlocked(ir, sfx_lane.id)

    if not any(ref.id == asset_id for ref in ir.assets):
        from video_director.models import AssetRef
        ir.assets.append(AssetRef(
            # The renderer re-resolves bytes by asset_id from the trusted library.
            # Do not persist or return this machine's absolute filesystem path.
            id=asset.id, type="audio", file=f"audio-library://{asset_id}", duration=source_duration,
            keywords=list(asset.keywords or []), source=asset.source,
            license_note=asset.license_note,
        ))

    index = len(ir.audio)
    ir.audio.append(AudioTrack(
        type=AudioTrackType.sfx, asset_id=asset_id, track_id=sfx_lane.id, start=round(start, 3),
        duration=round(clip_duration, 3), source_in=0.0,
        source_out=round(clip_duration, 3), volume=volume,
        fadeIn=fade_in, fadeOut=fade_out, muted=False,
    ))
    key = f"audio:sfx:{index}"
    return [key], [f"{key}:render", "project:audio:render"]


def _cmd_add_audio_asset_clip(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Add a trusted audio-library asset to a chosen physical audio lane.

    AudioTrackType remains the renderer's semantic role; track_id identifies the
    physical timeline lane. BGM/ambience can loop when source_out is None.
    """
    if target_id != "audio-tracks":
        raise EditorError("INVALID_TARGET", "add_audio_asset_clip 的 targetId 必须是 audio-tracks")
    asset_id = str(payload.get("assetId") or "").strip()
    track_id = str(payload.get("trackId") or "").strip()
    if not asset_id:
        raise EditorError("INVALID_PAYLOAD", "必须选择音频素材 assetId")
    lane_index = _audio_lane_index(ir, track_id)
    _ensure_audio_lane_unlocked(ir, track_id)

    from video_director import audio as vd_audio
    library = vd_audio.scan_library()
    asset = library.get(asset_id)
    if asset is None:
        raise EditorError("ASSET_NOT_FOUND", f"音频库不存在素材 {asset_id!r}", recoverable=False)
    if not asset.file or not Path(asset.file).is_file():
        raise EditorError("ASSET_NOT_FOUND", f"音频文件不可用：{asset_id!r}", recoverable=False)
    duration_value = getattr(asset, "duration", None)
    if duration_value is None:
        try:
            asset = vd_audio.ensure_audio_duration(asset)
            duration_value = asset.duration
        except Exception as exc:
            raise EditorError("ASSET_PROBE_FAILED", f"无法读取音频时长：{exc}") from exc
    try:
        source_duration = float(duration_value)
    except (TypeError, ValueError) as exc:
        raise EditorError("ASSET_PROBE_FAILED", "音频素材缺少有效时长") from exc
    if not math.isfinite(source_duration) or source_duration <= 0:
        raise EditorError("ASSET_PROBE_FAILED", "音频素材缺少有效时长")

    role = str(payload.get("role") or getattr(asset, "type", "sfx") or "sfx").strip().lower()
    role = {"ambient": "ambience", "voiceover": "dialogue", "voice": "dialogue"}.get(role, role)
    if role == "narration":
        raise EditorError("INVALID_PARAMETER", "TTS 主旁白由 narration 渲染链管理；额外录音请使用 dialogue/对白角色")
    if role == "original":
        raise EditorError("INVALID_PARAMETER", "original 用于视频源原声；独立音频文件请选择 dialogue、BGM、SFX 或 ambience")
    try:
        clip_type = AudioTrackType(role)
    except ValueError as exc:
        raise EditorError("INVALID_PARAMETER", "role 必须是 dialogue、bgm、sfx 或 ambience") from exc
    if clip_type not in (AudioTrackType.dialogue, AudioTrackType.bgm, AudioTrackType.sfx, AudioTrackType.ambience):
        raise EditorError("INVALID_PARAMETER", "不支持的独立音频片段角色")

    def number(key: str, default: float, minimum: float, maximum: Optional[float] = None) -> float:
        raw = payload.get(key, default)
        if isinstance(raw, bool):
            raise EditorError("INVALID_PARAMETER", f"{key} 必须是有限数字")
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise EditorError("INVALID_PARAMETER", f"{key} 必须是有限数字") from exc
        if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
            raise EditorError("INVALID_PARAMETER", f"{key} 超出允许范围")
        return value

    total = float(ir.project.duration)
    start = _project_frame_time(ir, number("start", 0.0, 0.0))
    if start >= total:
        raise EditorError("INVALID_TIMELINE_RANGE", "音频片段起点必须位于项目时间范围内")
    source_in = number("sourceIn", 0.0, 0.0)
    looping_role = clip_type in (AudioTrackType.bgm, AudioTrackType.ambience)
    duration_default = min(source_duration, total - start)
    duration = _project_frame_time(ir, number("duration", duration_default, 0.05, total - start))
    if duration < _project_frame_min_duration(ir) - 1e-6:
        raise EditorError("INVALID_PARAMETER", "音频片段按项目帧率对齐后不足一帧")
    if start + duration > total + 1e-6:
        raise EditorError("INVALID_TIMELINE_RANGE", "音频片段不能超出项目总时长")
    if source_in > source_duration:
        raise EditorError("INVALID_SOURCE_RANGE", "音频源入点超出素材时长")
    loop_to_duration = looping_role and abs(source_in) <= 1e-6
    source_out = None if loop_to_duration else source_in + duration
    if source_out is not None and source_out > source_duration + 1e-6:
        raise EditorError("INVALID_SOURCE_RANGE", "音频源区间超出素材时长；背景音乐/环境音可从源起点添加并循环延长")
    volume = number("volume", 1.0 if clip_type == AudioTrackType.dialogue else 0.6, 0.0, 2.0)
    fade_in = number("fadeIn", 0.0, 0.0, duration)
    fade_out = number("fadeOut", 0.0, 0.0, duration)

    if not any(ref.id == asset_id for ref in ir.assets):
        from video_director.models import AssetRef
        ir.assets.append(AssetRef(
            id=asset_id, type="audio", file=f"audio-library://{asset_id}", duration=source_duration,
            keywords=list(getattr(asset, "keywords", None) or []),
            source=getattr(asset, "source", "unknown"),
            license_note=getattr(asset, "license_note", None),
        ))
    clip = AudioTrack(
        type=clip_type, id=f"audio-clip-{uuid.uuid4().hex[:12]}",
        asset_id=asset_id, track_id=track_id, start=round(start, 6),
        duration=round(duration, 6), source_in=round(source_in, 6),
        source_out=None if loop_to_duration else round(float(source_out), 6),
        volume=volume, fadeIn=fade_in, fadeOut=fade_out, muted=False,
    )
    ir.audio.append(clip)
    index = len(ir.audio) - 1
    key = f"audio:{clip.type.value}:{index}"
    return [key, f"audio-track:{ir.audio_tracks[lane_index].id}"], [
        f"{key}:render", "project:audio:render", "project:audio-tracks", "project:preview",
    ]

def _cmd_move_audio_clip(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Move an audio clip in time and/or between physical lanes without changing mix role/source."""
    index = _audio_clip_index(ir, target_id)
    track = ir.audio[index]
    _ensure_audio_lane_unlocked(ir, str(track.track_id or f"audio-{track.type.value}"))
    raw_start = payload.get("start")
    if isinstance(raw_start, bool):
        raise EditorError("INVALID_PAYLOAD", "move_audio_clip 的 start 不能是布尔值")
    try:
        start = float(raw_start)
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PAYLOAD", "move_audio_clip 需要数字 start") from exc
    total = float(ir.project.duration)
    if not math.isfinite(start) or start < 0 or start >= total:
        raise EditorError("INVALID_PARAMETER", "音频片段起点必须位于项目时间范围内")
    # 统一编辑切点契约（PR #82）：放置/移动类命令与切分类命令共用同一
    # 项目帧时钟。旧实现按毫秒取整，会把音频起点留在两帧之间。
    start = _project_frame_time(ir, start)
    if start + float(track.duration) > total + 1e-6:
        raise EditorError("INVALID_TIMELINE_RANGE", "移动后的音频片段不能超出项目总时长")
    lane_id = str(payload.get("trackId", track.track_id or "") or "").strip()
    if not lane_id:
        lane_id = track.track_id or ""
    if not any(lane.id == lane_id for lane in ir.audio_tracks):
        raise EditorError("NOT_FOUND", f"目标音频轨道 {lane_id!r} 不存在", recoverable=False)
    _ensure_audio_lane_unlocked(ir, lane_id)
    # Direct manipulation of a linked sound clip deliberately detaches it from its
    # picture. This keeps the user's audio edit independent and clears the clip link.
    move_update = {
        "start": round(start, 6), "track_id": lane_id,
        "linked_visual_overlay_id": None,
    }
    if track.type == AudioTrackType.original:
        move_update["follows_scene_video"] = False
    ir.audio[index] = track.model_copy(update=move_update)
    key = f"audio:{track.type.value}:{index}"
    return [key], [f"{key}:render", "project:audio-tracks"]


def _cmd_trim_audio_clip(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Trim an audio instance by its source window and align that window on the timeline.

    payload: {start, sourceIn, sourceOut}; output duration is derived from the source
    window so the preview timeline and FFmpeg render consume the same interval.
    """
    index = _audio_clip_index(ir, target_id)
    track = ir.audio[index]
    _ensure_audio_lane_unlocked(ir, str(track.track_id or f"audio-{track.type.value}"))

    # BGM/ambience clips with source_out=None are intentionally looped by the
    # renderer to their requested timeline duration. Preserve that contract when
    # the user drags the right edge; materializing source_out would silently turn
    # the loop into a one-pass trim and could produce a shorter-than-timeline mix.
    if payload.get("loopToDuration") is True:
        if track.type not in (AudioTrackType.bgm, AudioTrackType.ambience):
            raise EditorError("INVALID_PAYLOAD", "仅背景音乐或环境音支持循环延长")
        raw_start = payload.get("start", track.start)
        raw_duration = payload.get("duration")
        if isinstance(raw_start, bool) or isinstance(raw_duration, bool):
            raise EditorError("INVALID_PAYLOAD", "循环片段的 start/duration 不能是布尔值")
        try:
            start = float(raw_start)
            duration = float(raw_duration)
        except (TypeError, ValueError) as exc:
            raise EditorError("INVALID_PAYLOAD", "循环片段需要数字 start/duration") from exc
        if not math.isfinite(start) or not math.isfinite(duration):
            raise EditorError("INVALID_PARAMETER", "循环片段的 start/duration 必须为有限数字")
        start = _project_frame_time(ir, start)
        duration = _project_frame_time(ir, duration)
        total = float(ir.project.duration)
        if start < 0 or start >= total:
            raise EditorError("INVALID_PARAMETER", "循环片段起点必须位于项目时间范围内")
        if duration < _project_frame_min_duration(ir):
            raise EditorError("INVALID_SOURCE_RANGE", "循环片段时长必须至少为一帧")
        if start + duration > total + 1e-6:
            raise EditorError("INVALID_TIMELINE_RANGE", "循环片段不能超出项目总时长")
        if abs(float(track.source_in or 0.0)) > 1e-6:
            raise EditorError("INVALID_SOURCE_RANGE", "当前循环片段不是从素材起点播放；请先恢复源入点或改为普通裁剪")
        ir.audio[index] = track.model_copy(update={
            "start": round(start, 6),
            "source_in": 0.0,
            "source_out": None,
            "duration": round(duration, 6),
            "linked_visual_overlay_id": None,
        })
        key = f"audio:{track.type.value}:{index}"
        return [key], [f"{key}:render", "project:audio:render", "project:preview"]

    raw_start = payload.get("start", track.start)
    raw_source_in = payload.get("sourceIn", payload.get("source_in", track.source_in or 0.0))
    source_out_raw = payload.get("sourceOut", payload.get("source_out"))
    if any(isinstance(value, bool) for value in (raw_start, raw_source_in, source_out_raw)):
        raise EditorError("INVALID_PAYLOAD", "trim_audio_clip 的 start/sourceIn/sourceOut 不能是布尔值")
    try:
        start = float(raw_start)
        source_in = float(raw_source_in)
        source_out = float(source_out_raw)
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PAYLOAD", "trim_audio_clip 需要 start/sourceIn/sourceOut 数字") from exc
    if not all(math.isfinite(value) for value in (start, source_in, source_out)):
        raise EditorError("INVALID_PARAMETER", "音频裁剪参数必须是有限数字")
    duration = (source_out - source_in) / max(0.25, float(getattr(track, "speed", 1.0) or 1.0))
    total = float(ir.project.duration)
    # 统一编辑切点契约（PR #82）：音频 trim 的起点/时长同样落到项目帧钟，
    # 与切分类命令一致；旧实现按毫秒取整会产生子帧边界。
    start = _project_frame_time(ir, start)
    duration = _project_frame_time(ir, duration)
    source_out = source_in + duration * max(0.25, float(getattr(track, "speed", 1.0) or 1.0))
    source_asset = next((asset for asset in ir.assets if asset.id == track.asset_id), None)
    source_duration = float(getattr(source_asset, "duration", 0.0) or 0.0) if source_asset else 0.0
    if source_duration > 0 and source_out > source_duration + 1e-6:
        raise EditorError(
            "INVALID_SOURCE_RANGE",
            "裁剪后的音频源区间超出素材时长；背景音乐/环境音若要循环延长，请保留循环模式",
        )
    if start < 0 or start >= total:
        raise EditorError("INVALID_PARAMETER", "裁剪后的音频起点必须位于项目时间范围内")
    if source_in < 0 or duration < 0.05:
        raise EditorError("INVALID_SOURCE_RANGE", "音频源区间必须非负且至少 0.05 秒")
    if start + duration > total + 1e-6:
        raise EditorError("INVALID_TIMELINE_RANGE", "裁剪后的音频片段不能超出项目总时长")
    trim_update = {
        "start": round(start, 6),
        "source_in": round(source_in, 6),
        "source_out": round(source_out, 6),
        "duration": round(duration, 6),
        "linked_visual_overlay_id": None,
    }
    if track.type == AudioTrackType.original:
        trim_update["follows_scene_video"] = False
    ir.audio[index] = track.model_copy(update=trim_update)
    key = f"audio:{track.type.value}:{index}"
    return [key], [f"{key}:render"]


def _cmd_split_audio_clip(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Split one editable audio clip at an absolute project playhead time.

    The two clips keep the same source sequence and mix settings. Fade-in remains
    on the left head, fade-out remains on the right tail. Looping BGM/ambience
    without an explicit source window is rejected because splitting it naïvely
    would restart the loop at the cut instead of preserving source continuity.
    """
    index = _audio_clip_index(ir, target_id)
    track = ir.audio[index]
    _ensure_audio_lane_unlocked(ir, str(track.track_id or f"audio-{track.type.value}"))
    if getattr(track, "keyframes", None):
        raise EditorError(
            "SPLIT_UNSUPPORTED_AUTOMATION",
            "该音频片段带有关键帧自动化；暂不能切分，避免丢失音量曲线",
        )
    if track.type == AudioTrackType.narration:
        raise EditorError(
            "SPLIT_UNSUPPORTED_NARRATION_PIPELINE",
            "旁白由独立渲染链路按单条旁白轨处理；当前版本暂不支持切分旁白，以免成片遗漏右段",
        )
    if track.type in (AudioTrackType.bgm, AudioTrackType.ambience) and track.source_out is None:
        raise EditorError(
            "SPLIT_LOOPING_AUDIO_REQUIRES_SOURCE_WINDOW",
            "背景音乐/环境音当前按循环模式播放。请先修剪出明确的源区间，再在播放头处分割",
        )

    raw_at = payload.get("at")
    if isinstance(raw_at, bool):
        raise EditorError("INVALID_PAYLOAD", "split_audio_clip 的 at 不能是布尔值")
    try:
        at = float(raw_at)
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PAYLOAD", "split_audio_clip 需要数字 at") from exc
    if not math.isfinite(at):
        raise EditorError("INVALID_PARAMETER", "切分播放头必须是有限时间")
    split_at = _project_frame_time(ir, at)
    start = float(track.start)
    duration = float(track.duration)
    end = start + duration
    minimum_half = 0.05
    if split_at <= start + minimum_half or split_at >= end - minimum_half:
        raise EditorError(
            "SPLIT_OUTSIDE_CLIP",
            "播放头按项目帧率对齐后必须位于音频片段内部，且切分两侧至少保留 0.05 秒",
        )

    # All clip types use the same project-frame boundary. Derive both halves
    # from that one timestamp; never round it independently to milliseconds.
    left_duration = split_at - start
    right_duration = end - split_at
    if left_duration < minimum_half or right_duration < minimum_half:
        raise EditorError(
            "SPLIT_OUTSIDE_CLIP",
            "切分后两侧片段都必须至少保留 0.05 秒",
        )

    source_in = float(track.source_in or 0.0)
    source_out = track.source_out
    if not math.isfinite(source_in) or source_in < 0:
        raise EditorError("INVALID_SOURCE_RANGE", "音频源起点必须为有限非负时间")
    if source_out is not None:
        source_out = float(source_out)
        if (
            not math.isfinite(source_out)
            or source_out <= source_in
            or abs((source_out - source_in) - duration * float(getattr(track, "speed", 1.0) or 1.0)) > 0.05
        ):
            raise EditorError(
                "INVALID_SOURCE_RANGE",
                "音频源区间与播放速度换算后的输出时长不一致，不能安全切分",
            )

    source_boundary = source_in + left_duration * float(getattr(track, "speed", 1.0) or 1.0)
    right_source_in = source_boundary
    left_source_out = source_boundary if source_out is not None else None
    fade_in = max(0.0, float(getattr(track, "fadeIn", 0.0) or 0.0))
    fade_out = max(0.0, float(getattr(track, "fadeOut", 0.0) or 0.0))
    left_update = {
        "duration": left_duration,
        "source_out": left_source_out,
        "linked_visual_overlay_id": None,
        "fadeIn": min(fade_in, left_duration),
        "fadeOut": 0.0,
    }
    right_update = {
        "id": f"audio-{uuid.uuid4().hex[:12]}",
        "start": split_at,
        "duration": right_duration,
        "source_in": right_source_in,
        "source_out": source_out,
        "linked_visual_overlay_id": None,
        "fadeIn": 0.0,
        "fadeOut": min(fade_out, right_duration),
        "keyframes": [],
    }
    if track.type == AudioTrackType.original:
        left_update["follows_scene_video"] = False
        right_update["follows_scene_video"] = False
    left = track.model_copy(update=left_update)
    right = track.model_copy(update=right_update)
    ir.audio[index] = left
    ir.audio.insert(index + 1, right)
    left_key = f"audio:{track.type.value}:{index}"
    right_key = f"audio:{track.type.value}:{index + 1}"
    return [left_key, right_key], [
        f"{left_key}:render",
        f"{right_key}:render",
        "project:audio:render",
        "project:preview",
    ]



def _cmd_remove_audio_clip(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Delete one independently editable audio clip, respecting lane locks."""
    index = _audio_clip_index(ir, target_id)
    clip = ir.audio[index]
    lane_id = str(clip.track_id or f"audio-{clip.type.value}")
    _ensure_audio_lane_unlocked(ir, lane_id)
    linked_overlay_id = clip.linked_visual_overlay_id
    changed = [f"audio:{clip.type.value}:{index}"]
    stale = ["project:audio:render", "project:audio-tracks", "project:preview"]
    if linked_overlay_id:
        overlay_index = next(
            (i for i, item in enumerate(ir.visual_overlays) if item.id == linked_overlay_id),
            None,
        )
        if overlay_index is not None:
            overlay = ir.visual_overlays[overlay_index]
            _ensure_visual_track_unlocked(ir, str(overlay.track_id or "overlay-main"))
            ir.visual_overlays[overlay_index] = overlay.model_copy(update={"source_audio_enabled": False})
            changed.append(f"visual-overlay:{linked_overlay_id}")
            stale.extend(["project:visual-overlays:render", "project:timeline:render"])
    ir.audio.pop(index)
    return changed, list(dict.fromkeys(stale))


def _cmd_paste_audio_clip(
    ir: CompositionIR, target_id: str, payload: dict, project_id: str,
) -> Tuple[List[str], List[str]]:
    """Paste a copied audio clip while keeping its mix role and editable parameters."""
    if target_id != "audio-tracks":
        raise EditorError("INVALID_TARGET", "paste_audio_clip 的 targetId 必须是 audio-tracks")
    from video_director.models import AudioTrack

    raw_clip = payload.get("clip")
    if not isinstance(raw_clip, dict):
        raise EditorError("INVALID_PAYLOAD", "粘贴音频片段缺少 clip 对象")
    try:
        source = AudioTrack.model_validate(raw_clip)
    except Exception as exc:
        raise EditorError("INVALID_PAYLOAD", f"复制的音频片段数据无效：{exc}") from exc
    if not source.asset_id:
        raise EditorError("ASSET_NOT_FOUND", "该音频片段未绑定可复用素材，不能复制粘贴")
    known_asset = any(asset.id == source.asset_id for asset in ir.assets) or any(
        clip.asset_id == source.asset_id for clip in ir.audio
    )
    if not known_asset:
        raise EditorError("ASSET_NOT_FOUND", "复制的音频素材不属于当前项目，无法粘贴")

    track_id = str(payload.get("trackId") or source.track_id or "").strip()
    if not track_id or not any(lane.id == track_id for lane in ir.audio_tracks):
        raise EditorError("NOT_FOUND", f"目标音频轨道 {track_id!r} 不存在", recoverable=False)
    _ensure_audio_lane_unlocked(ir, track_id)
    raw_start = payload.get("start")
    if isinstance(raw_start, bool):
        raise EditorError("INVALID_PAYLOAD", "start 不能是布尔值")
    try:
        start = float(raw_start)
    except (TypeError, ValueError) as exc:
        raise EditorError("INVALID_PAYLOAD", "粘贴位置 start 必须是数字") from exc
    if not math.isfinite(start) or start < 0:
        raise EditorError("INVALID_PARAMETER", "粘贴位置必须是有限非负数字")
    start = _project_frame_time(ir, start)
    duration = _project_frame_time(ir, float(source.duration))
    if duration < _project_frame_min_duration(ir) - 1e-6:
        raise EditorError("INVALID_PARAMETER", "复制音频片段按项目帧率对齐后不足一帧")
    if start + duration > float(ir.project.duration) + 1e-6:
        raise EditorError("INVALID_TIMELINE_RANGE", "粘贴后的音频片段超出项目时长")

    asset = next((item for item in ir.assets if item.id == source.asset_id), None)
    if asset and asset.duration is not None and source.source_out is not None:
        try:
            source_duration = float(asset.duration)
        except (TypeError, ValueError):
            source_duration = 0.0
        if source_duration > 0 and float(source.source_out) > source_duration + 0.05:
            raise EditorError("INVALID_SOURCE_RANGE", "复制音频片段的源区间超出素材时长")

    updates = {
        "id": f"audio-clip-{uuid.uuid4().hex[:12]}",
        "start": round(start, 6),
        "duration": round(duration, 6),
        "track_id": track_id,
        # A pasted source-audio clip is independent; never retain the source clip's linkage.
        "linked_visual_overlay_id": None,
    }
    if str(getattr(source.type, "value", source.type)) == "original":
        updates["follows_scene_video"] = False
    pasted = source.model_copy(update=updates)
    ir.audio.append(pasted)
    index = len(ir.audio) - 1
    key = f"audio:{pasted.type.value}:{index}"
    return [key, f"audio-track:{track_id}"], [
        f"{key}:render", "project:audio:render", "project:audio-tracks", "project:preview",
    ]


def _cmd_set_audio_clip_properties(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Edit one AudioTrack instance; unlike Audio Bus commands, never fans out by type."""
    index = _audio_clip_index(ir, target_id)
    track = ir.audio[index]
    _ensure_audio_lane_unlocked(ir, str(track.track_id or f"audio-{track.type.value}"))
    allowed = {"volume", "muted", "fadeIn", "fadeOut", "ducking"}
    unknown = set(payload) - allowed
    if unknown:
        raise EditorError("INVALID_PAYLOAD", f"音频片段属性不支持：{', '.join(sorted(unknown))}")
    if not payload:
        raise EditorError("INVALID_PAYLOAD", "至少需要一项音频片段属性")
    update = {}
    if "volume" in payload:
        raw = payload["volume"]
        if isinstance(raw, bool):
            raise EditorError("INVALID_PARAMETER", "音量必须是 0–2 的数字")
        try:
            volume = float(raw)
        except (TypeError, ValueError) as exc:
            raise EditorError("INVALID_PARAMETER", "音量必须是 0–2 的数字") from exc
        if not math.isfinite(volume) or not 0.0 <= volume <= 2.0:
            raise EditorError("INVALID_PARAMETER", "音量范围为 0–2")
        update["volume"] = volume
    for key in ("fadeIn", "fadeOut"):
        if key not in payload:
            continue
        raw = payload[key]
        if isinstance(raw, bool):
            raise EditorError("INVALID_PARAMETER", f"{key} 必须是非负秒数")
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise EditorError("INVALID_PARAMETER", f"{key} 必须是非负秒数") from exc
        if not math.isfinite(value) or value < 0 or value > float(track.duration):
            raise EditorError("INVALID_PARAMETER", f"{key} 必须在 0 到片段时长 {track.duration:.3f}s 之间")
        update[key] = value
    for key in ("muted", "ducking"):
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, bool):
            raise EditorError("INVALID_PARAMETER", f"{key} 必须为布尔值")
        if key == "ducking" and track.type.value != "original":
            raise EditorError("INVALID_PARAMETER", "只有原声音轨支持此处的旁白闪避开关")
        update[key] = value
    ir.audio[index] = track.model_copy(update=update)
    key = f"audio:{track.type.value}:{index}"
    return [key], [f"{key}:render"]


def _cmd_set_audio_volume(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """音轨音量（§20/§37）：targetId = narration|bgm|sfx|ambient，payload.volume ∈ [0,2]。

    可选 payload.assetId（曲目选择：music.add 应用时把 bgm 轨源指向选中资产）
    与 payload.ducking（§37 旁白闪避开关——volume 调整同时可开/关闪避）。
    """
    track_type = target_id or payload.get("track_type") or ""
    if track_type not in ("narration", "dialogue", "bgm", "sfx", "ambient", "ambience"):
        raise EditorError("INVALID_TARGET", f"set_audio_volume 目标音轨 {track_type!r} 非法")
    _ensure_audio_roles_unlocked(ir, {track_type})
    # §37 duck 语义：volume 不传（None）= 保留当前音量，只更新其他字段
    volume_raw = payload.get("volume")
    if isinstance(volume_raw, bool):
        raise EditorError("INVALID_PARAMETER", "set_audio_volume 的 volume 不能是布尔值")
    try:
        volume = float(volume_raw) if volume_raw is not None else None
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PARAMETER", "set_audio_volume 的 volume 必须是数字") from exc
    if volume is not None and (not math.isfinite(volume) or not (0.0 <= volume <= 2.0)):
        raise EditorError("INVALID_PARAMETER", f"volume {volume} 必须是有限数字且位于 [0,2]")
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
    _ensure_audio_roles_unlocked(ir, {track_type})
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
    _ensure_audio_roles_unlocked(ir, {track_type})
    raw_fade_in = payload.get("fadeIn", payload.get("fade_in", 0.0))
    raw_fade_out = payload.get("fadeOut", payload.get("fade_out", 0.0))
    if isinstance(raw_fade_in, bool) or isinstance(raw_fade_out, bool):
        raise EditorError("INVALID_PARAMETER", "淡入/淡出秒数不能是布尔值")
    try:
        fade_in = float(raw_fade_in or 0.0)
        fade_out = float(raw_fade_out or 0.0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PARAMETER", "淡入/淡出秒数必须是数字") from exc
    if not math.isfinite(fade_in) or not math.isfinite(fade_out) or fade_in < 0 or fade_out < 0:
        raise EditorError("INVALID_PARAMETER", "淡入/淡出秒数必须是有限的非负数字")
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
    invalid_roles = [name for name in tracks if name not in _VALID_AUDIO_TRACKS]
    if invalid_roles:
        raise EditorError("INVALID_TARGET", f"audio_mix 音轨 {invalid_roles[0]!r} 非法")
    _ensure_audio_roles_unlocked(ir, set(tracks))
    stale: List[str] = []
    for track_type, opts in tracks.items():
        if track_type not in _VALID_AUDIO_TRACKS:
            raise EditorError("INVALID_TARGET", f"audio_mix 音轨 {track_type!r} 非法")
        if not isinstance(opts, dict):
            raise EditorError("INVALID_PAYLOAD", f"audio_mix {track_type} 参数须为对象")
        update = {}
        if "volume" in opts:
            if isinstance(opts["volume"], bool):
                raise EditorError("INVALID_PAYLOAD", f"audio_mix {track_type} volume 不能是布尔值")
            try:
                v = float(opts["volume"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise EditorError("INVALID_PAYLOAD", f"audio_mix {track_type} volume 必须是数字") from exc
            if not math.isfinite(v) or not (0.0 <= v <= 2.0):
                raise EditorError("INVALID_PARAMETER", f"{track_type} volume 必须是有限数字且位于 [0,2]")
            update["volume"] = v
        if "muted" in opts:
            update["muted"] = bool(opts["muted"])
        if "ducking" in opts:
            update["ducking"] = bool(opts["ducking"])
        if "fadeIn" in opts:
            if isinstance(opts["fadeIn"], bool):
                raise EditorError("INVALID_PAYLOAD", f"audio_mix {track_type} fadeIn 不能是布尔值")
            try:
                fade_in = float(opts["fadeIn"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise EditorError("INVALID_PAYLOAD", f"audio_mix {track_type} fadeIn 必须是数字") from exc
            if not math.isfinite(fade_in) or fade_in < 0:
                raise EditorError("INVALID_PAYLOAD", f"audio_mix {track_type} fadeIn 必须是有限非负秒数")
            update["fadeIn"] = fade_in
        if "fadeOut" in opts:
            if isinstance(opts["fadeOut"], bool):
                raise EditorError("INVALID_PAYLOAD", f"audio_mix {track_type} fadeOut 不能是布尔值")
            try:
                fade_out = float(opts["fadeOut"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise EditorError("INVALID_PAYLOAD", f"audio_mix {track_type} fadeOut 必须是数字") from exc
            if not math.isfinite(fade_out) or fade_out < 0:
                raise EditorError("INVALID_PAYLOAD", f"audio_mix {track_type} fadeOut 必须是有限非负秒数")
            update["fadeOut"] = fade_out
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
    _ensure_audio_roles_unlocked(ir, {track_type})
    raw_at = payload.get("at", -1)
    if isinstance(raw_at, bool):
        raise EditorError("INVALID_PAYLOAD", "add_audio_keyframe 的 at 不能是布尔值")
    try:
        at = float(raw_at)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PAYLOAD", "add_audio_keyframe 的 at 必须是数字") from exc
    value = payload.get("volume", payload.get("value"))
    if not math.isfinite(at) or at < 0 or value is None:
        raise EditorError("INVALID_PAYLOAD", "add_audio_keyframe 需有限的非负 at 和 payload.volume")
    if isinstance(value, bool):
        raise EditorError("INVALID_PAYLOAD", "add_audio_keyframe 的音量不能是布尔值")
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
        if any(isinstance(payload.get(key), bool) for key in ("sourceOut", "duration") if key in payload):
            raise EditorError("INVALID_PAYLOAD", "add_broll 的 sourceOut/duration 不能是布尔值")
        raw_source_out = payload.get("sourceOut") or payload.get("duration") or 0
        try:
            source_out = float(raw_source_out)
        except (TypeError, ValueError, OverflowError) as exc:
            raise EditorError("INVALID_PAYLOAD", "add_broll 视频层的 sourceOut 必须是有限数字") from exc
        if not math.isfinite(source_out) or source_out <= 0:
            raise EditorError(
                "INVALID_PAYLOAD", "add_broll 视频层需有效的 sourceOut（源内秒数，可用素材库时长）"
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
    """多选批量命令（§47）：单 revision 原子执行。

    支持 delete / duplicate / lock，以及 OpenCut 风格的多场景移动。
    move 使用 anchorId：把选中的场景按当前相对顺序移动到锚点场景之前。
    持久化仍只有 Composition IR + Revision，不创建第二套时间线。
    """
    sub = payload.get("subcommand") or ""
    targets = [t for t in (payload.get("targets") or []) if t]
    if not targets:
        raise EditorError("INVALID_PAYLOAD", "batch 缺 targets")
    if sub not in ("delete", "duplicate", "lock", "move"):
        raise EditorError("INVALID_PAYLOAD", f"batch 不支持的 subcommand {sub!r}")

    if sub == "move":
        scene_targets = [t for t in targets if any(sc.id == t for sc in ir.scenes)]
        if not scene_targets:
            raise EditorError("NOOP", "批量移动没有有效场景")
        selected = set(scene_targets)
        anchor_id = payload.get("anchorId") or ""
        if anchor_id and anchor_id in selected:
            raise EditorError("NOOP", "不能把所选场景移动到所选集合内部")
        moving = [sc for sc in ir.scenes if sc.id in selected]
        remain = [sc for sc in ir.scenes if sc.id not in selected]
        if anchor_id:
            anchor_idx = next((i for i, sc in enumerate(remain) if sc.id == anchor_id), None)
            if anchor_idx is None:
                raise EditorError("NOT_FOUND", f"移动目标 {anchor_id} 不存在")
        else:
            anchor_idx = len(remain)
        ir.scenes = remain[:anchor_idx] + moving + remain[anchor_idx:]
        _renumber_starts(ir)
        _resync_audio_to_scenes(ir)
        stale = [f"{sc.id}:render" for sc in moving] + [f"{sc.id}:preview" for sc in moving]
        return [sc.id for sc in moving], stale

    changed_all: List[str] = []
    stale_all: List[str] = []
    for t in targets:
        try:
            if sub == "delete":
                c, s = _cmd_delete(ir, t, {})
            elif sub == "duplicate":
                c, s = _cmd_duplicate(ir, t, {})
            else:
                c, s = _cmd_lock(ir, t, {"locked": bool(payload.get("locked", True))})
        except EditorError as exc:
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
    """原声轨策略。支持全项目旧语义，也支持按视频片段建立多个 original track。"""
    _ensure_audio_roles_unlocked(ir, {"original"})
    mode = payload.get("mode") or "preserve"
    if mode not in ("preserve", "mute"):
        raise EditorError("INVALID_PAYLOAD", f"set_original_audio 的 mode {mode!r} 非法")
    temporal_fields = ("start", "duration", "sourceIn", "sourceOut", "fadeIn", "fadeOut")
    for field in temporal_fields:
        if field in payload and isinstance(payload[field], bool):
            raise EditorError("INVALID_PARAMETER", f"set_original_audio 的 {field} 不能是布尔值")
    _vol = payload.get("volume")
    if isinstance(_vol, bool):
        raise EditorError("INVALID_PARAMETER", "set_original_audio 的 volume 不能是布尔值")
    volume = float(_vol if _vol is not None else (0.8 if mode == "preserve" else 0.0))
    if not (0.0 <= volume <= 2.0):
        raise EditorError("INVALID_PARAMETER", f"original volume {volume} 越界 [0,2]")

    asset_id = payload.get("assetId")
    duck = bool(payload.get("duckAgainst") or payload.get("ducking", False))
    try:
        raw_fin = float(payload.get("fadeIn") or 0.0)
        raw_fout = float(payload.get("fadeOut") or 0.0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PARAMETER", "set_original_audio 的淡入/淡出必须是数字") from exc
    if not math.isfinite(raw_fin) or not math.isfinite(raw_fout):
        raise EditorError("INVALID_PARAMETER", "set_original_audio 的淡入/淡出必须是有限数字")
    fin = max(0.0, min(raw_fin, 10.0))
    fout = max(0.0, min(raw_fout, 10.0))

    # 新语义：显式 asset + start/duration/source range → 一条独立素材原声轨。
    # 这样 8 个用户视频可以各自保留现场声音，不再被单一 original 轨覆盖。
    if asset_id and any(k in payload for k in ("start", "duration", "sourceIn", "sourceOut")):
        try:
            start = float(payload.get("start", 0.0))
            duration = float(payload.get("duration", 0.0) or 0.0)
            source_in = max(0.0, float(payload.get("sourceIn", 0.0) or 0.0))
            raw_out = payload.get("sourceOut")
            source_out = None if raw_out in (None, "") else float(raw_out)
        except (TypeError, ValueError, OverflowError) as exc:
            raise EditorError("INVALID_PARAMETER", "片段原声 start/duration/sourceIn/sourceOut 必须是数字") from exc
        timing_values = [start, duration, source_in]
        if source_out is not None:
            timing_values.append(source_out)
        if not all(math.isfinite(value) for value in timing_values):
            raise EditorError("INVALID_PARAMETER", "片段原声时间参数必须是有限数字")
        if duration <= 0.0:
            raise EditorError("INVALID_PARAMETER", "片段原声 duration 必须 > 0")
        if source_out is not None and source_out <= source_in:
            raise EditorError("INVALID_PARAMETER", "片段原声 sourceOut 必须 > sourceIn")
        if start < 0.0 or start + duration > ir.project.duration + 0.05:
            raise EditorError("INVALID_PARAMETER",
                               f"原声时间段 {start:.3f}+{duration:.3f} 超出项目时长 {ir.project.duration:.3f}")

        # 同一素材、同一时间段重复应用 = 更新，而不是不断堆重复轨。
        for i, t in enumerate(ir.audio):
            if (t.type == AudioTrackType.original and t.asset_id == asset_id
                    and abs(t.start - start) < 1e-4):
                ir.audio[i] = t.model_copy(update={
                    "duration": duration, "source_in": source_in,
                    "source_out": source_out, "volume": volume,
                    "muted": mode == "mute", "ducking": duck,
                    "fadeIn": fin, "fadeOut": fout,
                    "follows_scene_video": False,
                })
                return [], ["audio:original:render"]

        ir.audio.append(AudioTrack(
            type=AudioTrackType.original, asset_id=asset_id,
            start=start, duration=duration, source_in=source_in,
            source_out=source_out, volume=volume, muted=(mode == "mute"),
            ducking=duck, fadeIn=fin, fadeOut=fout,
            follows_scene_video=False,
        ))
        return [], ["audio:original:render"]

    # 旧语义兼容：没有片段时间信息时仍维护唯一全局 original 轨。
    if not asset_id:
        for sc in ir.scenes:
            for lay in sc.layers:
                if lay.type == LayerType.video and lay.asset_id:
                    asset_id = lay.asset_id
                    break
            if asset_id:
                break
    for i, t in enumerate(ir.audio):
        if t.type == AudioTrackType.original:
            ir.audio[i] = t.model_copy(update={
                "volume": volume, "muted": mode == "mute", "ducking": duck,
                "fadeIn": fin, "fadeOut": fout, "asset_id": asset_id or t.asset_id,
            })
            return [], ["audio:original:render"]
    # 旧语义兼容：无显式 assetId 且无视频层时仍允许建轨（original policy
    # 可先于素材匹配设置；asset_id 后续由匹配链补上）。main 测试契约如此。
    ir.audio.append(AudioTrack(
        type=AudioTrackType.original, asset_id=asset_id,
        start=0.0, duration=ir.project.duration, volume=volume,
        muted=(mode == "mute"), ducking=duck, fadeIn=fin, fadeOut=fout,
    ))
    return [], ["audio:original:render"]


def _cmd_set_original_audio_volume(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """§18：original 轨音量。无 original 轨时新建（AI 首次调 volume 也应生效）。"""
    _ensure_audio_roles_unlocked(ir, {"original"})
    raw_volume = payload.get("volume", 1.0)
    if isinstance(raw_volume, bool):
        raise EditorError("INVALID_PARAMETER", "original volume 不能是布尔值")
    try:
        volume = float(raw_volume)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PARAMETER", "original volume 必须是数字") from exc
    if not math.isfinite(volume) or not (0.0 <= volume <= 2.0):
        raise EditorError("INVALID_PARAMETER", "original volume 必须是有限数字且位于 [0,2]")
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


def _watermark_index(ir: CompositionIR, target_id: str) -> int:
    """Resolve a timeline clip target (watermark#<index>) without falling back to kind."""
    import re

    match = re.fullmatch(r"watermark#(\d+)", str(target_id or ""))
    if not match:
        raise EditorError("INVALID_TARGET", "覆盖层时间线命令需要 targetId=watermark#<index>")
    index = int(match.group(1))
    if index < 0 or index >= len(ir.watermarks):
        raise EditorError("NOT_FOUND", f"覆盖层索引 {index} 不存在")
    return index


def _watermark_patch(current: WatermarkLayer, payload: dict) -> WatermarkLayer:
    """Patch only supplied fields, preserving timing/anchor/opacity not present in a legacy UI payload."""
    fields = {
        "id": "id", "kind": "kind", "text": "text", "assetId": "asset_id",
        "anchor": "anchor", "offsetX": "offset_x", "offsetY": "offset_y",
        "safeArea": "safe_area", "opacity": "opacity", "scale": "scale",
        "start": "start", "duration": "duration",
    }
    data = current.model_dump()
    for key, field_name in fields.items():
        if key in payload:
            data[field_name] = payload[key]
    return WatermarkLayer(**data)


def _cmd_set_watermark(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Add or patch an overlay; legacy calls still upsert by kind, timeline calls target one exact clip."""
    for field in ("start", "duration", "offsetX", "offsetY", "safeArea", "opacity", "scale"):
        if field in payload and isinstance(payload[field], bool):
            raise EditorError("INVALID_PAYLOAD", f"水印 {field} 不能是布尔值")
    kind = payload.get("kind") or "text"
    if kind == "text" and not (payload.get("text") or "").strip():
        raise EditorError("INVALID_PAYLOAD", "text 水印缺 text")
    if kind in ("image", "logo", "svg") and not payload.get("assetId"):
        raise EditorError("INVALID_PAYLOAD", f"{kind} 水印缺 assetId")

    if str(target_id or "").startswith("watermark#"):
        index = _watermark_index(ir, target_id)
        wm = _watermark_patch(ir.watermarks[index], payload)
        ir.watermarks[index] = wm
    elif payload.get("createNew"):
        used = {w.id for w in ir.watermarks}
        requested = str(payload.get("id") or "").strip()
        if requested and requested in used:
            raise EditorError("DUPLICATE_ID", f"覆盖层 id {requested!r} 已存在")
        stem = requested or f"wm-{kind}"
        wm_id, suffix = stem, 2
        while wm_id in used:
            wm_id, suffix = f"{stem}-{suffix}", suffix + 1
        data = {
            "id": wm_id,
            "kind": kind,
            "text": payload.get("text"),
            "asset_id": payload.get("assetId"),
            "anchor": payload.get("anchor", "bottom_right"),
            "offset_x": payload.get("offsetX", 0),
            "offset_y": payload.get("offsetY", 0),
            "safe_area": payload.get("safeArea", 32),
            "opacity": payload.get("opacity", 0.75),
            "scale": payload.get("scale", 1.0),
            "start": payload.get("start", 0.0),
            "duration": payload.get("duration"),
        }
        wm = WatermarkLayer(**data)
        ir.watermarks.append(wm)
    else:
        # Backward compatibility for older actions that treated set_watermark as upsert-by-kind.
        index = next((i for i, existing in enumerate(ir.watermarks) if existing.kind == kind), None)
        if index is None:
            return _cmd_set_watermark(ir, target_id, {**payload, "createNew": True})
        wm = _watermark_patch(ir.watermarks[index], payload)
        ir.watermarks[index] = wm

    return [f"watermark:{wm.id}"], ["project:render"]


def _cmd_move_watermark(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Move a timed overlay on the global timeline; dragging materializes its effective duration."""
    index = _watermark_index(ir, target_id)
    current = ir.watermarks[index]
    if isinstance(payload.get("start"), bool) or isinstance(payload.get("duration"), bool):
        raise EditorError("INVALID_PAYLOAD", "move_watermark 的 start/duration 不能是布尔值")
    try:
        start = float(payload["start"])
        duration = float(payload["duration"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PAYLOAD", "move_watermark 需要数值 start 和 duration") from exc
    total = float(ir.project.duration)
    if not (math.isfinite(start) and math.isfinite(duration)):
        raise EditorError("INVALID_PAYLOAD", "覆盖层时间必须是有限数值")
    if start < 0 or duration < 0.05 or start + duration > total + 0.01:
        raise EditorError("INVALID_RANGE", "覆盖层移动后必须位于成片区间内，且至少显示 0.05 秒")
    ir.watermarks[index] = WatermarkLayer(**{
        **current.model_dump(), "start": start, "duration": duration,
    })
    return [f"watermark:{current.id}"], ["project:render"]


def _cmd_trim_watermark(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Commit a timeline overlay's trimmed start/duration after local drag preview."""
    index = _watermark_index(ir, target_id)
    current = ir.watermarks[index]
    if isinstance(payload.get("start"), bool) or isinstance(payload.get("duration"), bool):
        raise EditorError("INVALID_PAYLOAD", "trim_watermark 的 start/duration 不能是布尔值")
    try:
        start = float(payload["start"])
        duration = float(payload["duration"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PAYLOAD", "trim_watermark 需要数值 start 和 duration") from exc
    total = float(ir.project.duration)
    if not (math.isfinite(start) and math.isfinite(duration)):
        raise EditorError("INVALID_PAYLOAD", "覆盖层时间必须是有限数值")
    if start < 0 or duration < 0.05 or start + duration > total + 0.01:
        raise EditorError("INVALID_RANGE", "覆盖层裁剪后必须位于成片区间内，且至少显示 0.05 秒")
    ir.watermarks[index] = WatermarkLayer(**{
        **current.model_dump(), "start": start, "duration": duration,
    })
    return [f"watermark:{current.id}"], ["project:render"]


def _cmd_remove_watermark(ir: CompositionIR, target_id: str, payload: dict) -> Tuple[List[str], List[str]]:
    """Remove one timeline overlay by index/id, or retain legacy kind-based removal."""
    if not ir.watermarks:
        raise EditorError("NOOP", "项目无水印")
    target = str(target_id or "")
    if target.startswith("watermark#"):
        index = _watermark_index(ir, target)
        removed = ir.watermarks.pop(index)
        return [f"watermark:{removed.id}"], ["project:render"]
    if target.startswith("wm-"):
        index = next((i for i, wm in enumerate(ir.watermarks) if wm.id == target), None)
        if index is None:
            raise EditorError("NOT_FOUND", f"覆盖层 {target!r} 不存在")
        removed = ir.watermarks.pop(index)
        return [f"watermark:{removed.id}"], ["project:render"]

    kind = target or payload.get("kind") or payload.get("id") or ""
    if kind:
        before = len(ir.watermarks)
        ir.watermarks = [w for w in ir.watermarks if w.kind != kind]
        removed = len(ir.watermarks) < before
    else:
        ir.watermarks = ir.watermarks[:-1]
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
        base = layer.transform
        if base is None:
            # Legacy set_transform projects may already hold scale/opacity only in
            # motion_instance.parameters. Seed the canonical contract from that data
            # before applying a positional/rotation patch, or such edits reset it.
            base_data = Transform().model_dump()
            legacy_params = (layer.motion_instance or {}).get("parameters") or {}
            legacy_transform = legacy_params.get("transform") or {}
            for key in ("scale", "opacity"):
                if key in legacy_transform:
                    base_data[key] = legacy_transform[key]
            base = Transform.model_validate(base_data)
        # model_copy(update=) 不重跑 validator → 用 model_validate 重建（越界 fail-closed）
        new_transform = Transform.model_validate({**base.model_dump(), **patch})
    except ValueError as exc:
        raise EditorError("INVALID_PARAMETER", str(exc))
    # Keep legacy consumers in sync for both scale-only and opacity-only edits.
    # Rendering and live preview consume Layer.transform as the canonical source.
    inst = dict(layer.motion_instance or {})
    if {"scale", "opacity"}.intersection(patch):
        params = dict(inst.get("parameters") or {})
        if "opacity" in patch:
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
    raw_at = payload.get("at")
    if isinstance(raw_at, bool):
        raise EditorError("INVALID_PAYLOAD", "add_keyframe 的 at 不能是布尔值")
    try:
        at = float(raw_at)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PAYLOAD", "add_keyframe 需数字 payload.at 和 payload.value") from exc
    if not math.isfinite(at) or at < 0:
        raise EditorError("INVALID_PAYLOAD", "add_keyframe 的 at 必须是有限的非负时间")
    if isinstance(payload.get("value"), bool):
        raise EditorError("INVALID_PAYLOAD", "add_keyframe 的 value 不能是布尔值")
    try:
        kf = Keyframe(at=at, value=payload["value"],
                      easing=payload.get("easing") or "linear")
    except KeyError as exc:
        raise EditorError("INVALID_PAYLOAD", "add_keyframe 需 payload.at / payload.value") from exc
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
    raw_at = payload.get("at", -1)
    if isinstance(raw_at, bool):
        raise EditorError("INVALID_PAYLOAD", "remove_keyframe 的 at 不能是布尔值")
    try:
        at = float(raw_at)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PAYLOAD", "remove_keyframe 的 at 必须是数字") from exc
    if not math.isfinite(at) or at < 0:
        raise EditorError("INVALID_PAYLOAD", "remove_keyframe 的 at 必须是有限的非负时间")
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
    raw_strength = payload.get("strength", 1.0)
    if isinstance(raw_strength, bool):
        raise EditorError("INVALID_PARAMETER", "camera.apply 的 strength 不能是布尔值")
    try:
        strength = float(raw_strength)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PARAMETER", "camera.apply 的 strength 必须是数字") from exc
    if not math.isfinite(strength) or not 0.0 < strength <= 1.0:
        raise EditorError("INVALID_PARAMETER", "camera.apply 的 strength 必须是有限数字且位于 (0,1]")
    track_id = f"cam-{scene.id}"
    raw_kfs = payload.get("keyframes")
    if raw_kfs:
        # 显式关键帧路径：逐个验证（fail-closed）
        if not isinstance(raw_kfs, list) or any(not isinstance(item, dict) for item in raw_kfs):
            raise EditorError("INVALID_PARAMETER", "camera.apply: keyframes 必须是对象数组")
        if any(isinstance(item.get("at"), bool) for item in raw_kfs):
            raise EditorError("INVALID_PARAMETER", "camera.apply: 关键帧 at 不能是布尔值")
        try:
            kfs = [CameraKeyframe.model_validate(k) for k in raw_kfs]
        except (KeyError, ValueError) as exc:
            raise EditorError("INVALID_PARAMETER", f"camera.apply: keyframes 非法: {exc}")
    else:
        raw_duration = payload.get("duration", scene.duration)
        if isinstance(raw_duration, bool):
            raise EditorError("INVALID_PARAMETER", "camera.apply 的 duration 不能是布尔值")
        try:
            camera_duration = float(raw_duration)
        except (TypeError, ValueError, OverflowError) as exc:
            raise EditorError("INVALID_PARAMETER", "camera.apply 的 duration 必须是数字") from exc
        if not math.isfinite(camera_duration) or camera_duration <= 0 or camera_duration > float(scene.duration) + 1e-6:
            raise EditorError("INVALID_PARAMETER", "camera.apply 的 duration 必须处于当前场景时长内")
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
            CameraKeyframe(at=camera_duration,
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
        if any(isinstance(crop.get(key), bool) for key in ("x", "y", "w", "h")):
            raise EditorError("INVALID_PARAMETER", "pip.configure: crop 坐标不能是布尔值")
        try:
            cx, cy, cw, ch = (float(crop[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise EditorError("INVALID_PARAMETER", f"pip.configure: crop 需 {{x,y,w,h}} 数值: {exc}")
        if min(cx, cy, cw, ch) < 0:
            raise EditorError("INVALID_PARAMETER", "pip.configure: crop 各值需 ≥0")
    # 结构化 pip 子对象（全部字段显式，渲染层按此 overlay）
    raw_radius = payload.get("radius", 0.0)
    if isinstance(raw_radius, bool):
        raise EditorError("INVALID_PARAMETER", "pip.configure 的 radius 不能是布尔值")
    try:
        radius = float(raw_radius)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PARAMETER", "pip.configure 的 radius 必须是数字") from exc
    if not math.isfinite(radius):
        raise EditorError("INVALID_PARAMETER", "pip.configure 的 radius 必须是有限数字")
    pip.update({
        "source": source,
        "fit": fit,
        "crop": crop,
        "radius": radius,
        "shadow": bool(payload.get("shadow", False)),
        "audio_policy": str(payload.get("audioPolicy") or "auto"),
    })
    params["pip"] = pip
    inst["parameters"] = params
    update: dict = {"motion_instance": inst or None}
    tf = payload.get("transform")
    if tf:
        if not isinstance(tf, dict):
            raise EditorError("INVALID_PARAMETER", "pip.configure: transform 必须是对象")
        if any(isinstance(tf.get(key), bool) for key in ("x", "y", "scale", "opacity") if key in tf):
            raise EditorError("INVALID_PARAMETER", "pip.configure: transform 数值不能是布尔值")
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
    raw_duration = payload.get("duration", 0.6)
    if isinstance(raw_duration, bool):
        raise EditorError("INVALID_PAYLOAD", "apply_morph 的 duration 不能是布尔值")
    try:
        morph_duration = float(raw_duration)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PAYLOAD", "apply_morph 的 duration 必须是数字") from exc
    if not math.isfinite(morph_duration) or morph_duration <= 0:
        raise EditorError("INVALID_PAYLOAD", "apply_morph 的 duration 必须是有限正数")
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
        "duration": morph_duration,
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
    si, scene = _find_scene(ir, target_id)
    ttype = str(payload.get("type") or "cut")
    raw_duration = payload.get("duration", 0.0)
    if isinstance(raw_duration, bool):
        raise EditorError("INVALID_PARAMETER", "转场时长不能是布尔值")
    try:
        duration = float(raw_duration or 0.0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorError("INVALID_PARAMETER", "转场时长必须是数字") from exc
    if not math.isfinite(duration) or duration < 0:
        raise EditorError("INVALID_PARAMETER", "转场时长必须是有限的非负数字")
    if ttype not in {"cut", "crossfade", "fade"}:
        raise EditorError("INVALID_PAYLOAD", f"不支持的转场类型 {ttype!r}")
    if ttype == "cut":
        duration = 0.0
    if duration > scene.duration / 2:
        raise EditorError("INVALID_PARAMETER", "转场时长不能超过当前场景时长的一半")
    # 最后一镜没有“下一个镜头”，写入没有实际边界的转场只会误导预览/渲染。
    if si >= len(ir.scenes) - 1 and ttype != "cut":
        raise EditorError("INVALID_TARGET", "最后一个场景不能设置转场")
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
