"""Composer Pipeline 编排（V1 §45 + V2 §49/§97 闭环）。

    script → scenes → assets → plan(IR) → validate → render → QC → PASS/REPAIR

Gate 1（无 TTS）：场景时长按字数确定性估算（config.SCRIPT_CPS）。
Gate 2 起 narration 模块提供真实时长替换 estimate。
修复循环上限 MAX_REPAIR_ROUNDS（禁止无限自循环）。
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from video_director import (
    assets as assets_mod,
    audio as audio_mod,
    config,
    matcher as matcher_mod,
    narration as narration_mod,
    planner as planner_mod,
    prober,
    qc as qc_mod,
    renderer,
    store,
    style as style_mod,
    subtitle as subtitle_mod,
    validator,
    vision as vision_mod,
)
from video_director.models import (
    AssetRef,
    AudioTrack,
    AudioTrackType,
    CompositionIR,
    MissingAssetError,
)


class PipelineError(Exception):
    pass


@dataclass
class PipelineResult:
    project_dir: str
    final_path: str
    ir: dict
    qc_report: dict
    narration_duration: float
    repair_rounds: int = 0
    warnings: List[str] = field(default_factory=list)
    #: auto_confirm=False 时，compose_auto 在 PLANNING 后暂停，不渲染。
    #: pending_confirm=True 表示这是一次「待确认」结果，final_path 为空，
    #: storyboard 字段含分镜书 dict，供调用方展示给用户。
    pending_confirm: bool = False
    storyboard: Optional[dict] = None
    project_id: Optional[str] = None


def split_script_scenes(script: str) -> List[str]:
    """文案 → 场景句子（按句号/问号/感叹号/换行切分，V1 §8）。

    不改变用户原文（V1 硬约束），标点保留。
    """
    # 统一换行符后按终止标点切句，保留标点
    text = script.strip()
    if not text:
        raise PipelineError("文案为空")
    # 按换行先分段，段内再按句末标点切
    sentences: List[str] = []
    for para in re.split(r"\n+", text):
        para = para.strip()
        if not para:
            continue
        # 中文句读切分
        parts = re.split(r"(?<=[。！？!?；;])", para)
        sentences.extend(p.strip() for p in parts if p.strip())
    # 合并过短句（<8 字）到前句，避免闪场景
    merged: List[str] = []
    for s in sentences:
        if merged and len(re.sub(r"[，。！？；：、,.!?;:\s]", "", s)) < 8:
            merged[-1] += s
        else:
            merged.append(s)
    if not merged:
        raise PipelineError("文案切分后无有效场景")
    return merged


def estimate_scene_durations(scene_texts: List[str]) -> List[dict]:
    """Gate 1 确定性时长估算：字数 / 4.5 字每秒，最少 2s。

    句间呼吸停顿（SCENE_GAP_PADDING）计入前一句的视觉时长而非留时间轴空洞：
    旁白静音期间画面继续播放，保证场景无缝衔接（CompositionIR 连续性校验的硬要求）。
    """
    scenes = []
    cursor = 0.0
    n = len(scene_texts)
    for i, text in enumerate(scene_texts):
        char_count = len(re.sub(r"[，。！？；：、,.!?;:\s]", "", text))
        dur = max(config.SCENE_MIN_DURATION, char_count / config.SCRIPT_CPS)
        if i < n - 1:
            dur += config.SCENE_GAP_PADDING  # 句间停顿由本场景视觉覆盖
        scenes.append({
            "id": f"S{i + 1:02d}",
            "text": text,
            "start": round(cursor, 3),
            "duration": round(dur, 3),
        })
        cursor += dur
    return scenes


def compose_project(
    script: str,
    video_files: List[str],
    image_files: List[str],
    project_id: str,
    planner: Optional[object] = None,
    narration_file: Optional[str] = None,
    narration_scenes: Optional[List[dict]] = None,
    bgm_asset_id: Optional[str] = None,
    bgm_volume: float = None,
    sfx_plan: Optional[List[dict]] = None,
    subtitle_entries: Optional[List[dict]] = None,
    subtitle_file: Optional[str] = None,
    width: int = None,
    height: int = None,
    fps: int = None,
    repair_enabled: bool = True,
) -> PipelineResult:
    """完整编排入口（CLI/WebUI/Skill 共用同一引擎）。

    narration_scenes: Gate 2 由对齐模块提供（真实时长）；None 则用 estimate。
    """
    pid = project_id or "manual"
    base = config.project_dir(pid)
    dirs = config.init_project_dirs(base)
    warnings: List[str] = []

    def _fail(stage_err: str):
        """失败落库（不阻塞 raise；SVF 经验：错误带全量上下文便于诊断）。"""
        try:
            store.set_stage(pid, store.Stage.FAILED, error=stage_err[:2000])
        except Exception:
            pass

    # 1. 文案落盘 + 场景化
    (dirs["input"] / "script.txt").write_text(script, encoding="utf-8")
    if narration_scenes:
        scenes = narration_scenes
    else:
        scenes = estimate_scene_durations(split_script_scenes(script))
    with open(dirs["plans"] / "scenes.json", "w", encoding="utf-8") as f:
        json.dump(scenes, f, ensure_ascii=False, indent=2)

    # 2. 资产清单（V1 §25：ffprobe）
    manifest_exists = (dirs["assets"] / "manifest.json").is_file()
    if not video_files and not image_files and not manifest_exists:
        _fail("无任何视觉素材（SCRIPT_ONLY 模式第二阶段支持）")
        raise PipelineError("无任何视觉素材（SCRIPT_ONLY 模式第二阶段支持）")
    if video_files or image_files:
        asset_map, order = assets_mod.build_manifest(video_files, image_files)
        with open(dirs["assets"] / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(
                [a.model_dump() for a in asset_map.values()], f, ensure_ascii=False, indent=2
            )
    else:
        # resume/draft 路径：素材已在 manifest（compose_auto(auto_confirm=False)
        # 暂停时落盘），从 manifest 重建资产表，不重复探测
        asset_list = json.loads(
            (dirs["assets"] / "manifest.json").read_text(encoding="utf-8")
        )
        asset_map = {}
        order = []
        for a in asset_list:
            ref = AssetRef(**a)
            asset_map[ref.id] = ref
            order.append(ref.id)
        logger.info(f"compose_project: 从 manifest 重建资产表 {len(order)} 项（resume 路径）")

    # 3. 规划（RulePlanner 默认；LLM 由调用方注入）
    store.set_stage(pid, store.Stage.PLANNING)
    active_planner = planner or planner_mod.RulePlanner()
    try:
        ir = active_planner.plan(
            scenes=scenes,
            assets=asset_map,
            asset_order=order,
            width=width or config.DEFAULT_RESOLUTION[0],
            height=height or config.DEFAULT_RESOLUTION[1],
            fps=fps or config.DEFAULT_FPS,
        )
    except MissingAssetError as exc:
        _save_error(dirs, "MISSING_ASSET", str(exc), scene_id=exc.scene_id)
        _fail(f"MISSING_ASSET: {exc}")
        raise

    # 4. 音频计划（BGM/SFX 一等公民，V2 §30）
    narration_duration = scenes[-1]["start"] + scenes[-1]["duration"]
    audio_assets: Dict[str, AssetRef] = {}
    audio_tracks: List[AudioTrack] = []

    if narration_file:
        audio_tracks.append(AudioTrack(
            type=AudioTrackType.narration,
            start=0.0,
            duration=narration_duration,
        ))
    if bgm_asset_id:
        lib = audio_mod.scan_library()
        if bgm_asset_id in lib:
            audio_assets[bgm_asset_id] = lib[bgm_asset_id]
            audio_tracks.append(audio_mod.build_bgm_track(
                bgm_asset_id, narration_duration,
                bgm_volume or config.BGM_DEFAULT_VOLUME,
            ))
        else:
            warnings.append(f"NO_BGM: 音频库无 {bgm_asset_id}，跳过 BGM（不随机替代）")
    elif bgm_asset_id is None:
        pass  # 未请求 BGM

    if sfx_plan:
        lib = audio_mod.scan_library()
        # guizang audio-cues：SFX 绑定动作发生点（action_id 驱动语义选择），
        # 旧格式 {start, tags/asset_id} 向后兼容
        for ev in audio_mod.normalize_sfx_events(sfx_plan):
            aid = ev.get("asset_id") or audio_mod.resolve_action_sfx(ev, lib) or (
                audio_mod.resolve({"type": "sfx", "tags": ev.get("tags") or []}, lib)
            )
            if aid is None:
                reason = ev.get("action_id") or ev.get("tags") or "?"
                warnings.append(f"NO_SFX: 无匹配音效（action={reason}），跳过（不随机替代）")
                continue
            audio_assets[aid] = lib[aid]
            audio_tracks.append(audio_mod.build_sfx_track(
                aid, ev["at"], ev.get("duration", 0.7), ev.get("volume"),
            ))

    ir.audio = audio_tracks
    render_assets = audio_mod.append_audio_assets(asset_map, audio_assets)

    # 5. 保存 IR（Composition IR 是源代码，MP4 是 build artifact，V2 §78）
    # style_id 由 compose_auto 选定后经 config 传递；此处读回写入 IR（可追溯）
    with open(dirs["plans"] / "composition.json", "w", encoding="utf-8") as f:
        json.dump(json.loads(ir.model_dump_json()), f, ensure_ascii=False, indent=2)

    # 6. 校验
    result = validator.validate_composition(
        ir, render_assets, narration_duration, subtitle_entries
    )
    if not result.ok:
        _save_error(dirs, "VALIDATION_" + result.verdict.value, json.dumps(result.to_dict()))
        _fail(f"VALIDATION_{result.verdict.value}: {result.to_dict()}")
        raise PipelineError(f"IR 校验失败: {result.to_dict()}")

    # 7. 渲染 + QC + 修复循环
    work = dirs["render"]
    repair_rounds = 0
    while True:
        try:
            store.set_stage(pid, store.Stage.RENDERING)
            render_out = renderer.render_composition(
                ir, render_assets, work, narration_file, subtitle_file
            )
            store.set_stage(pid, store.Stage.QC)
            report = qc_mod.run_qc(
                ir, render_out.final_path, render_assets, narration_duration,
                expect_audio=bool(audio_tracks),
            )
            report.save(str(dirs["qc"] / "deterministic.json"))
            if report.ok:
                # 输出 final
                final_out = dirs["output"] / "final.mp4"
                if Path(render_out.final_path) != final_out:
                    shutil.copyfile(render_out.final_path, final_out)
                return PipelineResult(
                    project_dir=str(base),
                    final_path=str(final_out),
                    ir=json.loads(ir.model_dump_json()),
                    qc_report=report.to_dict(),
                    narration_duration=narration_duration,
                    repair_rounds=repair_rounds,
                    warnings=warnings,
                )
            # QC 失败 → 修复
            if not repair_enabled or repair_rounds >= config.MAX_REPAIR_ROUNDS:
                _save_error(dirs, "QC_FAILED", report.to_dict())
                _fail(f"QC_FAILED: {json.dumps(report.to_dict(), ensure_ascii=False)}")
                raise PipelineError(f"QC 失败（已修复 {repair_rounds} 轮）: {report.to_dict()}")
            repair_rounds += 1
            logger.warning(f"QC 未过，进入修复轮 {repair_rounds}/{config.MAX_REPAIR_ROUNDS}")
            ir = _repair(ir, report, asset_map, order)
            # 修复后的 IR 必须重新校验再渲染（禁止带伤进渲染器）
            recheck = validator.validate_composition(
                ir, render_assets, narration_duration, subtitle_entries
            )
            if not recheck.ok:
                _save_error(dirs, "VALIDATION_" + recheck.verdict.value,
                            json.dumps(recheck.to_dict()))
                _fail(f"VALIDATION_{recheck.verdict.value}: {recheck.to_dict()}")
                raise PipelineError(f"修复后 IR 校验失败: {recheck.to_dict()}")
            with open(dirs["plans"] / f"composition.r{repair_rounds}.json", "w", encoding="utf-8") as f:
                json.dump(json.loads(ir.model_dump_json()), f, ensure_ascii=False, indent=2)
        except MissingAssetError as exc:
            _save_error(dirs, "MISSING_ASSET", str(exc), scene_id=exc.scene_id)
            _fail(f"MISSING_ASSET: {exc}")
            raise
        except renderer.RenderError as exc:
            _save_error(dirs, "RENDER_FAILED", str(exc))
            _fail(f"RENDER_FAILED: {exc}")
            raise


def _repair(
    ir: CompositionIR,
    report: qc_mod.QCReport,
    asset_map: Dict[str, AssetRef],
    order: List[str],
) -> CompositionIR:
    """确定性修复（V1 §33 Repair 简化版）：按 QC 失败项针对性改 IR。

    第一版策略：时长类失败 → 重新 RulePlan；其余失败原样重试（上限兜底）。
    LLM Repair Planner 是 Gate 5 扩展位。
    """
    failed = [c["check"] for c in report.checks if c["result"] == "FAIL"]
    if any(k in failed for k in ("duration_vs_narration", "resolution", "fps")):
        # 交给 RulePlanner 重新规划（确定性）
        scenes = [
            {"id": s.id, "text": s.text, "start": s.start, "duration": s.duration}
            for s in ir.scenes
        ]
        new_ir = planner_mod.RulePlanner().plan(
            scenes=scenes, assets=asset_map, asset_order=order,
            width=ir.project.width, height=ir.project.height, fps=ir.project.fps,
        )
        new_ir.audio = ir.audio
        return new_ir
    return ir  # 其余项重试（例如瞬态 ffmpeg 失败）


def _save_error(dirs: dict, code: str, detail: str, scene_id: str = None):
    err = {"code": code, "detail": detail}
    if scene_id:
        err["scene_id"] = scene_id
    with open(dirs["qc"] / "error.json", "w", encoding="utf-8") as f:
        json.dump(err, f, ensure_ascii=False, indent=2)
    logger.error(f"composer error {code}: {str(detail)[:300]}")


# ---------------------------------------------------------------------------
# 人工确认环节（auto_confirm=False 的暂停/续跑路径）
# ---------------------------------------------------------------------------

def _compose_to_pending(
    script: str,
    project_id: str,
    dirs: dict,
    narration_audio: str,
    narration_scenes: list,
    sub_file,
    sub_entries,
    active_planner,
    merged_assets,
    merged_order,
    style_contract,
    style_warnings: list,
    width,
    height,
    fps,
    bgm_asset_id,
    bgm_volume,
    sfx_plan,
    doctor_results,
    video_files: list,
    image_files: list,
) -> PipelineResult:
    """auto_confirm=False：跑到规划完成，生成分镜书，落 AWAITING_CONFIRM 态。

    返回 pending_confirm=True 的 PipelineResult：
    - final_path 为空字符串（未渲染）
    - ir 含规划后的 CompositionIR dict
    - storyboard 含分镜书 dict（也同时落盘 plans/storyboard.json）
    - project_id 带回（resume_after_confirm 要用）
    """
    from video_director import confirm as confirm_mod

    if merged_assets is None or merged_order is None:
        # 用户自注入 planner 的分支：没有 merged_assets，无法生成分镜书细节
        # 降级为最小分镜书（仅场景列表，无素材细节）
        merged_assets = {}
        merged_order = []
        if not active_planner:
            raise PipelineError("auto_confirm=False 需要 planner，但未注入")

    # 规划一次（与 compose_project 内部同款 plan 调用，幂等）
    store.set_stage(project_id, store.Stage.PLANNING)
    ir = active_planner.plan(
        scenes=narration_scenes,
        assets=merged_assets,
        asset_order=merged_order,
        width=width or config.DEFAULT_RESOLUTION[0],
        height=height or config.DEFAULT_RESOLUTION[1],
        fps=fps or config.DEFAULT_FPS,
    )
    style_mod.apply_to_ir(ir, style_contract, config)

    # IR 落盘（与 compose_project 一致：composition.json 是渲染唯一输入）
    with open(dirs["plans"] / "composition.json", "w", encoding="utf-8") as f:
        json.dump(json.loads(ir.model_dump_json()), f, ensure_ascii=False, indent=2)

    # 匹配结果（若 ContentMatchPlanner 路径）从 planner 里取
    assignments: dict = {}
    if hasattr(active_planner, "_assignments"):
        assignments = dict(active_planner._assignments)

    # 生成分镜书
    storyboard = confirm_mod.build_storyboard(
        ir=ir,
        scenes=narration_scenes,
        assignments=assignments,
        style=style_contract,
        assets=merged_assets,
    )
    confirm_mod.save_storyboard(storyboard, dirs["plans"] / "storyboard.json")

    # Style 契约落盘（与 auto_confirm=True 路径一致，resume 时不重做）
    (dirs["plans"] / "style.json").write_text(
        json.dumps({
            "style_id": style_contract.style_id,
            "reason": style_contract.reason,
            "contract": {
                "subtitle_position": style_contract.subtitle_position,
                "subtitle_font_size": style_contract.subtitle_font_size,
                "image_motion_intensity": style_contract.image_motion_intensity,
                "transition_budget": style_contract.transition_budget,
                "bgm_volume": style_contract.bgm_volume,
                "sfx_density": style_contract.sfx_density,
            },
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 文案落盘（resume 时 compose_project 会再写一次，幂等）
    (dirs["input"] / "script.txt").write_text(script, encoding="utf-8")

    # 场景表落盘（resume_after_confirm 要读回 narration_scenes）
    with open(dirs["plans"] / "scenes.json", "w", encoding="utf-8") as f:
        json.dump(narration_scenes, f, ensure_ascii=False, indent=2)

    # 资产清单落盘（resume/draft 渲染的素材来源——缺失会导致 draft 找不到素材）。
    # 与 compose_project 的 manifest 写法一致；音频库资产并入（BGM/SFX 引用）。
    merged_for_manifest = dict(merged_assets or {})
    try:
        from video_director import audio as _audio_mod
        merged_for_manifest = _audio_mod.append_audio_assets(
            merged_for_manifest, _audio_mod.scan_library()
        )
    except Exception as exc:
        logger.warning(f"音频库并入 manifest 失败（不阻塞）: {exc}")
    if merged_for_manifest:
        with open(dirs["assets"] / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(
                [a.model_dump() for a in merged_for_manifest.values()],
                f, ensure_ascii=False, indent=2,
            )

    # 状态机：PLANNING → AWAITING_CONFIRM（暂停点，不渲染）
    store.set_stage(project_id, store.Stage.AWAITING_CONFIRM)
    try:
        store.save_storyboard_snapshot(project_id, storyboard)
    except Exception as exc:
        logger.warning(f"store.save_storyboard_snapshot 失败（不阻塞）: {exc}")

    return PipelineResult(
        project_dir=str(config.project_dir(project_id)),
        final_path="",  # 未渲染
        ir=json.loads(ir.model_dump_json()),
        qc_report={},   # 未 QC
        narration_duration=narration_scenes[-1]["start"] + narration_scenes[-1]["duration"],
        warnings=list(style_warnings),
        pending_confirm=True,
        storyboard=storyboard,
        project_id=project_id,
    )


def _edits_change_narration(edits: Optional[dict]) -> bool:
    """判断 edits 是否改变旁白（删段/重排/改文案需重合成旁白+字幕）。"""
    if not edits:
        return False
    if edits.get("remove") or edits.get("reorder"):
        return True
    return any(
        isinstance(v, dict) and "text" in v
        for k, v in edits.items() if k not in ("remove", "reorder")
    )


def resume_after_confirm(project_id: str, edits: Optional[dict] = None) -> PipelineResult:
    """人工确认后从 RENDERING 继续 → QC → final。

    流程：
    1. 读 plans/storyboard.json + plans/composition.json
    2. 若 edits 不为空：应用 edits 到分镜书；若 edits 涉及改素材/删场景，
       重规划（用原 planner 重新 plan）。第一版简化：有 edits 则尝试重规划
       （改 assignments 后跑 ContentMatchPlanner），无 edits 直接用原 IR。
    3. store.confirm_project(approved=True) → RENDERING
    4. 从 RENDERING+QC 继续：调用 compose_project（幂等：planner/字幕/
       narration 都从项目目录里读回，IR 重规划后幂等覆盖 composition.json）
    5. 收尾：store.finish_project + runlog

    约束：不改 compose_project 签名。resume 里若要重规划，把 planner 注入
    compose_project，让其内部重新 plan——这会覆盖 composition.json，幂等。
    """
    from video_director import confirm as confirm_mod

    base = config.project_dir(project_id)
    dirs = config.init_project_dirs(base)

    # 读回项目元数据
    proj = store.get_project(project_id)
    if proj is None:
        raise PipelineError(f"项目不存在: {project_id}")
    if proj["stage"] != store.Stage.AWAITING_CONFIRM.value:
        raise PipelineError(
            f"项目 {project_id} 当前 stage={proj['stage']}，非 AWAITING_CONFIRM，"
            f"不能 resume（需先 compose_auto(auto_confirm=False)）"
        )

    # 读回分镜书 + 风格契约 + 原参数
    storyboard_path = dirs["plans"] / "storyboard.json"
    storyboard = confirm_mod.load_storyboard(storyboard_path)

    style_path = dirs["plans"] / "style.json"
    style_data = {}
    if style_path.is_file():
        try:
            style_data = json.loads(style_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"style.json 读回失败: {exc}")

    # 读回原始参数
    params = {}
    try:
        params = json.loads(proj["params_json"]) if proj["params_json"] else {}
    except json.JSONDecodeError:
        params = {}
    script = proj["script"]
    voice = params.get("voice", "zh-CN-XiaoxiaoNeural")
    voice_rate = params.get("voice_rate", "+0%")
    width = params.get("width")
    height = params.get("height")
    fps = params.get("fps")
    bgm_asset_id = params.get("bgm")
    manual_manifest = params.get("manual_manifest")
    style_id = params.get("style_id") or style_data.get("style_id")
    style_contract = style_mod.get_style(style_id) if style_id else None
    if style_contract is None:
        # 兜底：用文案重选一次风格
        style_contract, _ = style_mod.select_style(script, None)

    # 应用 edits 到分镜书（即使重规划，分镜书也要反映最终状态）
    effective_edits = edits or {}
    if effective_edits:
        storyboard = confirm_mod.apply_storyboard_edits(storyboard, effective_edits)
        confirm_mod.save_storyboard(storyboard, storyboard_path)

    # 标记确认通过 → RENDERING
    store.confirm_project(project_id, approved=True, edits=effective_edits or None)

    # 重新规划（若有 edits）：
    # - 若 edits 改了 assignments（asset 字段），构造新 ContentMatchPlanner
    # - 若无 edits，用原 planner——但原 planner 无法从 IR 反序列化，
    #   简化：无 edits 时 RulePlanner 用 narration_scenes + assets 重跑一次
    #   （幂等：IR 结果与之前一致，compose_project 会覆盖 composition.json）
    # 读回 narration_scenes（compose_project 要用）
    scenes_path = dirs["plans"] / "scenes.json"
    if not scenes_path.is_file():
        raise PipelineError(f"scenes.json 不存在: {scenes_path}（项目数据不完整）")
    narration_scenes = json.loads(scenes_path.read_text(encoding="utf-8"))

    # 读回 asset manifest（若存在）
    manifest_path = dirs["assets"] / "manifest.json"
    merged_assets = {}
    merged_order = []
    if manifest_path.is_file():
        asset_list = json.loads(manifest_path.read_text(encoding="utf-8"))
        for a in asset_list:
            ref = AssetRef(**a)
            merged_assets[ref.id] = ref
            merged_order.append(ref.id)

    # 构造 planner：若 edits 涉及 asset 改动，用 ContentMatchPlanner
    # 带新 assignments；否则用 RulePlanner 重跑（幂等）
    active_planner = None
    if effective_edits:
        # 从分镜书 scenes 里提新 assignments（主素材 = 每场景 assets_used 第一条）。
        # 防御：只信 manifest 里真实存在的 asset_id（分镜书展示层可能有
        # 语义增强的别名 id，不进 planner，否则 KeyError）。
        new_assignments: Dict[str, str] = {}
        for sc in storyboard.get("scenes", []):
            used = sc.get("assets_used") or []
            if used and used[0].get("asset_id"):
                aid = str(used[0]["asset_id"])
                if aid in merged_assets:
                    new_assignments[sc["scene_id"]] = aid
        if new_assignments and merged_assets:
            active_planner = planner_mod.ContentMatchPlanner(new_assignments)
            logger.info(f"resume: edits 应用，ContentMatchPlanner 重规划（{len(new_assignments)} 场景）")
        else:
            logger.warning("resume: edits 无有效素材改动，用 RulePlanner 重规划")
    if active_planner is None:
        active_planner = planner_mod.RulePlanner()

    # 读回 narration_audio + 字幕文件；改稿涉及删段/改文案时重合成旁白+字幕
    narration_audio_path = dirs["audio"] / "narration.mp3"
    narration_changed = _edits_change_narration(effective_edits)
    if narration_changed:
        texts = [str(sc.get("text", "")) for sc in storyboard.get("scenes", [])]
        narration_audio, word_timeline, narration_scenes = narration_mod.synth_narration(
            texts, voice, dirs["audio"], rate=voice_rate
        )
        narration_file = narration_audio
        with open(dirs["alignment"] / "words.json", "w", encoding="utf-8") as f:
            json.dump(word_timeline, f, ensure_ascii=False, indent=2)
        with open(scenes_path, "w", encoding="utf-8") as f:
            json.dump(narration_scenes, f, ensure_ascii=False, indent=2)
        sub_file = dirs["render"] / "master.srt"
        sub_file.write_text(
            subtitle_mod.build_srt_from_scenes(narration_scenes), encoding="utf-8"
        )
        sub_entries = subtitle_mod.srt_entries(narration_scenes)
        sub_file_arg = str(sub_file)
        logger.info(
            f"resume: 旁白重合成（{len(texts)} 场景，删段/改文案生效）"
        )
    else:
        narration_file = str(narration_audio_path) if narration_audio_path.is_file() else None
        sub_file = dirs["render"] / "master.srt"
        sub_entries = subtitle_mod.srt_entries(narration_scenes) if sub_file.is_file() else None
        sub_file_arg = str(sub_file) if sub_file.is_file() else None

    # 复用 compose_project 完成规划→渲染→QC
    result = compose_project(
        script=script,
        video_files=[],   # 资产已在 manifest 里，不重复探测
        image_files=[],
        project_id=project_id,
        planner=active_planner,
        narration_file=narration_file,
        narration_scenes=narration_scenes,
        bgm_asset_id=bgm_asset_id,
        bgm_volume=style_contract.bgm_volume if style_contract else None,
        sfx_plan=None,
        subtitle_entries=sub_entries,
        subtitle_file=sub_file_arg,
        width=width,
        height=height,
        fps=fps,
    )

    store.finish_project(project_id, result.final_path, result.narration_duration,
                         result.qc_report)
    # Run Manifest（与 compose_auto 一致）
    from video_director import runlog
    try:
        script_file = dirs["input"] / "script.txt"
        manifest = runlog.build_run_manifest(
            project_id=project_id,
            workflow="script-media-compose-resumed",
            script_path=str(script_file),
            asset_paths=[],
            providers={
                "tts": "edge-tts",
                "alignment": "edge-tts-word-boundaries",
                "vision": "none",
                "llm": "none",
            },
            verification={
                "doctor": "PASS",
                "deterministic_qc": result.qc_report.get("verdict", "?"),
                "visual_qc": "SKIPPED",
            },
            artifact_path=result.final_path,
            warnings=result.warnings,
        )
        runlog.save_run_manifest(base, manifest)
    except Exception as exc:
        logger.warning(f"run manifest 写入失败（不阻塞交付）: {exc}")
    return result


# ---------------------------------------------------------------------------
# Gate 2 高层入口：文案 → TTS → 对齐 → 字幕 → 成片
# ---------------------------------------------------------------------------

def compose_auto(
    script: str,
    video_files: List[str],
    image_files: List[str],
    project_id: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
    voice_rate: str = "+0%",
    planner: Optional[object] = None,
    vision_provider=None,
    manual_manifest: Optional[str] = None,
    llm_call=None,
    style_intent: Optional[str] = None,
    bgm_asset_id: Optional[str] = None,
    bgm_volume: float = None,
    sfx_plan: Optional[List[dict]] = None,
    width: int = None,
    height: int = None,
    fps: int = None,
    auto_confirm: bool = True,
    subtitle_mode: str = "plain",
) -> PipelineResult:
    """全自动成片（V1 §45）：文案→场景→TTS→词级对齐→素材理解→匹配→字幕→规划→渲染→QC。

    这是最终产品主入口（CLI/WebUI/Skill 共用）。TTS 用 edge-tts 原生词级时间戳，
    字幕与 Narration Timeline 同基准（V1 §23）。

    素材理解（视频理解方案六级链）：P1/P2 已有语义 → vision_provider（P3/P4）
    → manual_manifest（P5）→ 语义缺失时 matcher 顺序兜底并记 warning。
    planner 未显式注入时：有语义匹配 → ContentMatchPlanner；否则 RulePlanner。
    style_intent：用户一句风格描述（V2 §19），None 则 AUTO（V2 §18）。

    auto_confirm（2026-09-20 新增，默认 True 保持向后兼容）：
    - True：规划完直接渲染（原行为不变，"傻瓜式一键到底"）
    - False：规划完成后暂停，生成分镜书写入 plans/storyboard.json，
      store.set_stage AWAITING_CONFIRM，返回 pending_confirm=True 的
      PipelineResult（final_path 为空、storyboard 含分镜书 dict）。
      调用方（WebUI/CLI）展示分镜书给用户，用户确认后调
      resume_after_confirm(project_id, edits) 从 RENDERING 继续。
    """
    scene_texts = split_script_scenes(script)
    base = config.project_dir(project_id)
    dirs = config.init_project_dirs(base)

    # 0. 任务状态机登记（跨会话历史 + WebUI 轮询；SVF 经验：配置持久化、崩溃无脏态）
    # + Style Contract 选择（V2 §17/§18：AUTO 或用户一句描述，无评分）
    style_contract, style_warnings = style_mod.select_style(script, style_intent)
    # BGM AI 自动配乐（bgm_asset_id="auto"：风格契约 → 中文标签语义匹配选曲）
    if bgm_asset_id == "auto":
        auto_bgm = audio_mod.resolve_auto_bgm(style_contract.style_id)
        if auto_bgm:
            bgm_asset_id = auto_bgm
            logger.info(f"bgm auto: 风格 {style_contract.style_id} → {auto_bgm}")
        else:
            bgm_asset_id = None
            style_warnings.append("BGM 自动匹配：音频库为空，未配乐")
    try:
        store.create_project(project_id, script, {
            "voice": voice, "voice_rate": voice_rate,
            "width": width, "height": height, "fps": fps,
            "bgm": bgm_asset_id, "manual_manifest": manual_manifest,
            "style_id": style_contract.style_id,
            "subtitle_mode": subtitle_mode,
        })
    except Exception as exc:
        logger.warning(f"store.create_project 失败（不阻塞主流程）: {exc}")

    # 0.5 Capability Gate（jianying-headless：能力检查必须在 Plan 前面——
    # 环境必需能力缺失时生成前失败，而不是渲染一半报错）
    from video_director import doctor
    doctor_results = doctor.run_doctor()
    if doctor.has_blocking_failures(doctor_results):
        fail_detail = "; ".join(f"{r.name}: {r.detail}" for r in doctor_results if r.status == "FAIL")
        try:
            store.set_stage(project_id, store.Stage.FAILED, error=f"CAPABILITY_MISSING: {fail_detail}")
        except Exception:
            pass
        raise PipelineError(f"CAPABILITY_MISSING（环境自检未过，禁止盲目继续）: {fail_detail}")

    # 1. TTS + 词级对齐（TTSTimestampProvider 主路径，零额外模型）
    store.set_stage(project_id, store.Stage.TTS)
    narration_audio, word_timeline, narration_scenes = narration_mod.synth_narration(
        scene_texts, voice, dirs["audio"], rate=voice_rate
    )
    with open(dirs["alignment"] / "words.json", "w", encoding="utf-8") as f:
        json.dump(word_timeline, f, ensure_ascii=False, indent=2)
    store.set_stage(project_id, store.Stage.ALIGNMENT)

    # 2. 字幕（与主时间轴同基准；karaoke 模式用词级时间戳生成 ASS 逐词高亮）
    if subtitle_mode == "karaoke":
        sub_file = dirs["render"] / "master.ass"
        sub_file.write_text(
            subtitle_mod.build_ass_karaoke(
                narration_scenes, word_timeline,
                width=width or config.DEFAULT_RESOLUTION[0],
                height=height or config.DEFAULT_RESOLUTION[1],
            ), encoding="utf-8"
        )
    else:
        sub_file = dirs["render"] / "master.srt"
        sub_file.write_text(
            subtitle_mod.build_srt_from_scenes(narration_scenes), encoding="utf-8"
        )
    sub_entries = subtitle_mod.srt_entries(narration_scenes)

    # 3. 素材语义理解（六级优先链）+ 内容匹配
    store.set_stage(project_id, store.Stage.MATCHING)
    active_planner = planner
    if active_planner is None:
        video_assets, video_order = assets_mod.build_manifest(video_files, [])
        image_assets, image_order = (
            assets_mod.build_manifest([], image_files) if image_files else ({}, [])
        )
        # P5 自动探测：素材同目录的 assets_semantic.json（手工标注 fallback，
        # 与 CLI/WebUI/Skill 全入口一致生效）
        resolved_manual = manual_manifest
        if resolved_manual is None and video_files:
            sibling = Path(video_files[0]).parent / "assets_semantic.json"
            if sibling.is_file():
                resolved_manual = str(sibling)
                logger.info(f"auto-detected manual manifest: {sibling}")
        enhanced = vision_mod.analyze_assets(
            video_assets, dirs["assets"], resolved_manual, vision_provider
        )
        merged_assets = {**enhanced, **image_assets}
        has_semantics = any(a.description or a.keywords for a in enhanced.values())
        if has_semantics:
            assignments = matcher_mod.match_assets(
                narration_scenes, merged_assets, llm_call
            )
            with open(dirs["assets"] / "match_assignments.json", "w", encoding="utf-8") as f:
                json.dump(assignments, f, ensure_ascii=False, indent=2)
            active_planner = planner_mod.ContentMatchPlanner(assignments)
            logger.info(f"planner: ContentMatchPlanner（语义匹配 {len(assignments)} 场景）")
        else:
            active_planner = planner_mod.RulePlanner()
            logger.warning("planner: 素材无语义，RulePlanner 顺序分配（降级）")
        merged_order = video_order + image_order
        # Style Contract 应用（转场预算/图片动效强度；IR style_id 写入在 plan 后）
        style_mod.apply_to_ir(None, style_contract, config)  # config 层预算先行
    else:
        merged_assets = None
        merged_order = None

    # 4. 复用 compose_project 完成规划/渲染/QC
    #
    # auto_confirm=False 分支（2026-09-20 新增）：规划完成后暂停，不渲染。
    # 在此处自己跑一次 plan()（复用 active_planner），生成分镜书，落库
    # AWAITING_CONFIRM 态，返回 pending PipelineResult。resume_after_confirm
    # 接管后续渲染/QC（复用 compose_project 的渲染段——通过再次调用
    # compose_project 且 planner=已确定 的方式，compose_project 内部会
    # 重新跑一次 plan()，幂等无副作用；narration_scenes 已传入故 Gate 2
    # 真实时长不变）。
    if not auto_confirm:
        return _compose_to_pending(
            script=script,
            project_id=project_id,
            dirs=dirs,
            narration_audio=narration_audio,
            narration_scenes=narration_scenes,
            sub_file=sub_file,
            sub_entries=sub_entries,
            active_planner=active_planner,
            merged_assets=merged_assets,
            merged_order=merged_order,
            style_contract=style_contract,
            style_warnings=style_warnings,
            width=width,
            height=height,
            fps=fps,
            bgm_asset_id=bgm_asset_id,
            bgm_volume=bgm_volume,
            sfx_plan=sfx_plan,
            doctor_results=doctor_results,
            video_files=video_files,
            image_files=image_files,
        )

    result = compose_project(
        script=script,
        video_files=video_files,
        image_files=image_files,
        project_id=project_id,
        planner=active_planner,
        narration_file=narration_audio,
        narration_scenes=narration_scenes,
        bgm_asset_id=bgm_asset_id,
        bgm_volume=(bgm_volume if bgm_volume is not None else style_contract.bgm_volume),
        sfx_plan=sfx_plan,
        subtitle_entries=sub_entries,
        subtitle_file=str(sub_file),
        width=width,
        height=height,
        fps=fps,
    )
    result.warnings.extend(style_warnings)
    # Style 生效断言材料：把风格选择理由附到产物
    (dirs["plans"] / "style.json").write_text(
        json.dumps({
            "style_id": style_contract.style_id,
            "reason": style_contract.reason,
            "contract": {
                "subtitle_position": style_contract.subtitle_position,
                "subtitle_font_size": style_contract.subtitle_font_size,
                "image_motion_intensity": style_contract.image_motion_intensity,
                "transition_budget": style_contract.transition_budget,
                "bgm_volume": style_contract.bgm_volume,
                "sfx_density": style_contract.sfx_density,
            },
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    store.finish_project(project_id, result.final_path, result.narration_duration,
                         result.qc_report)
    # Run Manifest（jianying-headless 运行审计：providers/版本/输入hash/验证结果）
    from video_director import runlog
    try:
        script_file = dirs["input"] / "script.txt"
        manifest = runlog.build_run_manifest(
            project_id=project_id,
            workflow="script-media-compose",
            script_path=str(script_file),
            asset_paths=video_files + image_files,
            providers={
                "tts": "edge-tts",
                "alignment": "edge-tts-word-boundaries",
                "vision": ("frame-vision" if any(
                    r.status == "PASS" and "Vision" in r.name for r in doctor_results
                ) else "manual-manifest" if manual_manifest else "none"),
                "llm": "none",
            },
            verification={
                "doctor": "PASS",
                "deterministic_qc": result.qc_report.get("verdict", "?"),
                "visual_qc": "SKIPPED（第二阶段）",
            },
            artifact_path=result.final_path,
            warnings=result.warnings,
        )
        runlog.save_run_manifest(base, manifest)
    except Exception as exc:
        logger.warning(f"run manifest 写入失败（不阻塞交付）: {exc}")
    return result
