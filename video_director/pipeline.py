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
    if not video_files and not image_files:
        _fail("无任何视觉素材（SCRIPT_ONLY 模式第二阶段支持）")
        raise PipelineError("无任何视觉素材（SCRIPT_ONLY 模式第二阶段支持）")
    asset_map, order = assets_mod.build_manifest(video_files, image_files)
    with open(dirs["assets"] / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(
            [a.model_dump() for a in asset_map.values()], f, ensure_ascii=False, indent=2
        )

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
        for ev in sfx_plan:
            aid = ev.get("asset_id") or audio_mod.resolve(
                {"type": "sfx", "tags": ev.get("tags", [])}, lib
            )
            if aid is None:
                warnings.append(f"NO_SFX: 无匹配音效（intent={ev.get('intent')}），跳过")
                continue
            audio_assets[aid] = lib[aid]
            audio_tracks.append(audio_mod.build_sfx_track(
                aid, ev["start"], ev.get("duration", 0.7), ev.get("volume"),
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
) -> PipelineResult:
    """全自动成片（V1 §45）：文案→场景→TTS→词级对齐→素材理解→匹配→字幕→规划→渲染→QC。

    这是最终产品主入口（CLI/WebUI/Skill 共用）。TTS 用 edge-tts 原生词级时间戳，
    字幕与 Narration Timeline 同基准（V1 §23）。

    素材理解（视频理解方案六级链）：P1/P2 已有语义 → vision_provider（P3/P4）
    → manual_manifest（P5）→ 语义缺失时 matcher 顺序兜底并记 warning。
    planner 未显式注入时：有语义匹配 → ContentMatchPlanner；否则 RulePlanner。
    style_intent：用户一句风格描述（V2 §19），None 则 AUTO（V2 §18）。
    """
    scene_texts = split_script_scenes(script)
    base = config.project_dir(project_id)
    dirs = config.init_project_dirs(base)

    # 0. 任务状态机登记（跨会话历史 + WebUI 轮询；SVF 经验：配置持久化、崩溃无脏态）
    # + Style Contract 选择（V2 §17/§18：AUTO 或用户一句描述，无评分）
    style_contract, style_warnings = style_mod.select_style(script, style_intent)
    try:
        store.create_project(project_id, script, {
            "voice": voice, "voice_rate": voice_rate,
            "width": width, "height": height, "fps": fps,
            "bgm": bgm_asset_id, "manual_manifest": manual_manifest,
            "style_id": style_contract.style_id,
        })
    except Exception as exc:
        logger.warning(f"store.create_project 失败（不阻塞主流程）: {exc}")

    # 1. TTS + 词级对齐（TTSTimestampProvider 主路径，零额外模型）
    store.set_stage(project_id, store.Stage.TTS)
    narration_audio, word_timeline, narration_scenes = narration_mod.synth_narration(
        scene_texts, voice, dirs["audio"], rate=voice_rate
    )
    with open(dirs["alignment"] / "words.json", "w", encoding="utf-8") as f:
        json.dump(word_timeline, f, ensure_ascii=False, indent=2)
    store.set_stage(project_id, store.Stage.ALIGNMENT)

    # 2. 字幕（与主时间轴同基准）
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
    return result
