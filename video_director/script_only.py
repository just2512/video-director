"""SCRIPT_ONLY 纯文案模式（V2 第二阶段：零素材成片）。

    文案 → 每场景「纯色背景卡 + 场景文字」→ TTS 旁白 + 字幕 → FFmpeg 合成 MP4

不依赖任何视频/图片素材：视觉 = lavfi color 源 + drawtext（每行一个 filter，
y 递增可控），音频 = edge-tts 旁白，字幕 = subtitles filter 烧录。

与 pipeline.compose_auto 的关系：
    compose_auto 需要素材匹配链（assets/vision/matcher）；本模块绕开素材链，
    把 narration_scenes 直接映射为 Layer(type=text) 的 CompositionIR 再渲染。
    text 层对 validator 仍是"第一阶段不可渲染"——本模块自带渲染能力集
    is_renderable()/unrenderable_layers()，不改动 models.RENDERABLE_LAYER_TYPES
    （零签名破坏，向后兼容）。

零外部依赖：仅标准库 + 包内模块 + loguru；中文排版用逐字折行，不依赖 PIL。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from loguru import logger

from video_director import (
    config,
    narration as narration_mod,
    prober,
    qc as qc_mod,
    renderer,
    store,
    style as style_mod,
    subtitle as subtitle_mod,
)
from video_director.models import (
    AudioTrack,
    AudioTrackType,
    CompositionIR,
    Layer,
    LayerType,
    ProjectSpec,
    SceneIR,
)
from video_director.style import StyleContract


class ScriptOnlyError(Exception):
    """SCRIPT_ONLY 模式结构性失败（能力缺失/渲染失败/QC 拒绝）。"""


#: 纯色背景卡（style 无关的固定深蓝灰）
BG_COLOR = "0x1a1a2e"
#: 中文候选字体（探测顺序：微软雅黑 → 雅黑粗体 → 黑体）
_CJK_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
)
#: 文字区域左右安全边距（像素）
TEXT_MARGIN_X = 80
#: 行距系数（行高 = fontsize + fontsize*0.42）
LINE_SPACING_RATIO = 0.42


# ---------------------------------------------------------------------------
# 渲染能力判断（只服务本模块，不改 models）
# ---------------------------------------------------------------------------

#: SCRIPT_ONLY 渲染能力集：text（本模式）+ 第一阶段的 video/image
_RENDERABLE = {LayerType.video, LayerType.image, LayerType.text, LayerType.gap}


def is_renderable(layer: Layer) -> bool:
    """本模块的层可渲染判断（text 纳入；不触碰 models.RENDERABLE_LAYER_TYPES）。"""
    return layer.type in _RENDERABLE


def unrenderable_layers(ir: CompositionIR) -> List[Layer]:
    """超出本模块渲染能力的层（诚实暴露，禁止静默忽略）。"""
    return [l for scene in ir.scenes for l in scene.layers if not is_renderable(l)]


# ---------------------------------------------------------------------------
# 字体探测 + Windows 路径转义
# ---------------------------------------------------------------------------

def find_cjk_font() -> Optional[str]:
    """按候选顺序探测中文字体，全部缺失返回 None（调用方降级为纯色背景）。"""
    for cand in _CJK_FONT_CANDIDATES:
        if Path(cand).is_file():
            return cand
    return None


def _esc_path(path: str) -> str:
    """FFmpeg filter 参数内 Windows 路径转义（与 renderer.subtitle_burn_in 同款）。"""
    return path.replace("\\", "/").replace(":", "\\:")


def _path_arg(path: str) -> str:
    """filter 选项值：转义 + 单引号包裹。"""
    return f"'{_esc_path(path)}'"


# ---------------------------------------------------------------------------
# 中文逐字折行（零依赖：不依赖 PIL，按显示宽度估算）
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[str]:
    """ASCII 词（含内部连字符/撇号）聚为单 token 不切断，其余逐字。"""
    return re.findall(r"[A-Za-z0-9]+(?:['\-][A-Za-z0-9]+)*|.", text)


def _display_units(token: str) -> float:
    """显示宽度估算（全角=1.0，ASCII≈0.6），单位=一个全角字符宽。"""
    if token and token[0].isascii() and token[0].isalnum():
        return max(1.0, len(token) * 0.6)
    return 1.0


def wrap_text(text: str, width: int, font_size: int) -> List[str]:
    """按显示宽度逐字折行：每行不超过 (width-2*margin)/font_size 个全角字符。

    尊重原文硬换行；ASCII 词不切断；超宽 token（长英文串）硬切兜底。
    """
    max_units = max(4.0, (width - TEXT_MARGIN_X * 2) / font_size)
    lines: List[str] = []
    for para in re.split(r"\n+", text.strip()):
        if not para:
            continue
        cur, cur_u = "", 0.0
        for token in _tokenize(para):
            u = _display_units(token)
            if cur and cur_u + u > max_units:
                lines.append(cur)
                cur, cur_u = "", 0.0
            while u > max_units and len(token) > 1:  # 超宽 token 硬切
                cut = max(1, int(len(token) * max_units / u))
                lines.append(token[:cut])
                token = token[cut:]
                u = _display_units(token)
            cur += token
            cur_u += u
        if cur:
            lines.append(cur)
    return lines


# ---------------------------------------------------------------------------
# IR 构建
# ---------------------------------------------------------------------------

def build_text_scenes(
    narration_scenes: List[dict],
    style: StyleContract,
    width: int = config.DEFAULT_RESOLUTION[0],
    height: int = config.DEFAULT_RESOLUTION[1],
    fps: int = config.DEFAULT_FPS,
) -> CompositionIR:
    """narration_scenes [{id,text,start,duration}] → CompositionIR。

    每场景一个 SceneIR，场景内单个 Layer(type=text, text=场景文案)；
    纯色背景由渲染层统一处理（BG_COLOR），不进 IR。
    """
    if not narration_scenes:
        raise ScriptOnlyError("narration_scenes 为空，无法构建场景")
    total = narration_scenes[-1]["start"] + narration_scenes[-1]["duration"]
    if total <= 0:
        raise ScriptOnlyError(f"旁白总长非法: {total}")

    scenes = [
        SceneIR(
            id=s["id"],
            start=s["start"],
            duration=s["duration"],
            text=s["text"],
            layers=[Layer(
                type=LayerType.text,
                text=s["text"],
                start=0.0,
                duration=s["duration"],
            )],
        )
        for s in narration_scenes
    ]
    ir = CompositionIR(
        project=ProjectSpec(width=width, height=height, fps=fps, duration=total),
        style_id=style.style_id,
        scenes=scenes,
        audio=[AudioTrack(
            type=AudioTrackType.narration,
            start=0.0,
            duration=total,
        )],
    )
    bad = unrenderable_layers(ir)
    if bad:
        raise ScriptOnlyError(f"IR 含不可渲染层: {[l.type.value for l in bad]}")
    logger.info(
        f"script_only IR: {len(scenes)} scenes, {total:.2f}s, "
        f"style={style.style_id}, {width}x{height}@{fps}"
    )
    return ir


# ---------------------------------------------------------------------------
# 单层渲染：纯色背景 + drawtext 文字卡
# ---------------------------------------------------------------------------

def _emit_line_files(lines: List[str], out_dir: Path, seq: int) -> List[str]:
    """每行写一个 utf-8 txt（drawtext textfile 方式，绕开命令行转义）。"""
    paths: List[str] = []
    for i, line in enumerate(lines):
        p = out_dir / f"{seq:04d}.line{i}.txt"
        p.write_text(line, encoding="utf-8")
        paths.append(str(p))
    return paths


def _drawtext_vf(
    lines: List[str],
    txt_paths: List[str],
    font_path: str,
    font_size: int,
    height: int,
) -> str:
    """每行一个 drawtext（x 逐行自动居中，y 按行高递增，垂直居中于画布）。"""
    spacing = max(8, round(font_size * LINE_SPACING_RATIO))
    line_h = font_size + spacing
    block_h = len(lines) * line_h - spacing
    y0 = max(0.0, (height - block_h) / 2)
    parts = []
    for i, txt in enumerate(txt_paths):
        parts.append(
            f"drawtext=fontfile={_path_arg(font_path)}"
            f":textfile={_path_arg(txt)}"
            f":fontcolor=white:fontsize={font_size}"
            f":x=(w-text_w)/2:y={y0 + i * line_h:.1f}"
        )
    return ",".join(parts)


def render_text_layer(
    scene_text: str,
    duration: float,
    out_dir,
    seq: int,
    width: int,
    height: int,
    fps: int,
    font_size: int = 64,
) -> str:
    """渲染单个 text 层为标准化 segment：纯色背景 + 居中场景文字（无音轨）。

    - 输入：lavfi color 源（时长给足余量）
    - 文字：drawtext textfile（utf-8 txt，避免命令行转义），每行一个 filter
    - 帧数：-frames:v N（N=round(duration*fps)），段长 = N/fps，偏差 ≤ 半帧
    - 输出参数与 renderer 一致（h264/yuv420p/crf），保证 concat -c copy 可拼
    - 字体全部缺失时降级为纯色背景卡并 logger.warning（诚实降级）
    返回 segment 路径。
    """
    if duration <= 0:
        raise ScriptOnlyError(f"render_text_layer: duration 非法 {duration}")
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    out_path = str(d / f"{seq:04d}.mp4")
    # probe_media 依赖 store 缓存表，独立调用时幂等初始化
    try:
        store.init_db()
    except Exception as exc:
        logger.warning(f"store.init_db 失败（继续渲染）: {exc}")

    font_path = find_cjk_font()
    vf_parts: List[str] = []
    if font_path is None:
        logger.warning(
            "未找到中文字体（msyh/msyhbd/simhei 均缺失），"
            "该场景降级为纯色背景卡（无文字）"
        )
    elif scene_text.strip():
        lines = wrap_text(scene_text, width, font_size)
        txt_paths = _emit_line_files(lines, d, seq)
        vf_parts.append(_drawtext_vf(lines, txt_paths, font_path, font_size, height))

    frames = max(1, round(duration * fps))
    cmd = [
        renderer._ffmpeg(), "-y", "-loglevel", "error",
        "-f", "lavfi", "-i",
        f"color=c={BG_COLOR}:s={width}x{height}:r={fps}:d={duration + 1.0:.3f}",
    ]
    if vf_parts:
        cmd += ["-vf", ",".join(vf_parts)]
    cmd += [
        "-an",
        "-frames:v", str(frames),
        "-r", str(fps),
        "-c:v", config.DEFAULT_VIDEO_CODEC,
        "-preset", "veryfast",
        "-crf", str(config.DEFAULT_CRF),
        "-pix_fmt", config.DEFAULT_PIXEL_FORMAT,
        out_path,
    ]
    try:
        renderer._run(cmd)
    except renderer.RenderError as exc:
        raise ScriptOnlyError(f"text segment 渲染失败: {exc}") from exc

    expect = frames / fps
    probe = prober.probe_media(out_path)
    if probe.duration is None or abs(probe.duration - expect) > 0.15:
        raise ScriptOnlyError(
            f"segment 时长 {probe.duration} 与预期 {expect:.3f}s 偏差超 0.15s: {out_path}"
        )
    logger.info(f"script_only segment {seq:04d}: {probe.duration:.3f}s, {len(lines) if font_path else 0} 行文字")
    return out_path


# ---------------------------------------------------------------------------
# 主入口：文案 → final.mp4
# ---------------------------------------------------------------------------

@dataclass
class ScriptOnlyResult:
    """compose_script_only 结果（字段与 pipeline.PipelineResult 对齐）。"""

    project_dir: str
    final_path: str
    ir: dict
    qc_report: dict
    narration_duration: float
    warnings: List[str] = field(default_factory=list)


def compose_script_only(
    script: str,
    project_id: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
    style_intent: Optional[str] = None,
    width: int = config.DEFAULT_RESOLUTION[0],
    height: int = config.DEFAULT_RESOLUTION[1],
    fps: int = config.DEFAULT_FPS,
) -> ScriptOnlyResult:
    """SCRIPT_ONLY 全链路：文案 → TTS → text 场景卡 → 混音 → 字幕 → QC。

    复用 narration/style/subtitle/renderer.concat_segments/renderer.mix_audio/
    renderer.subtitle_burn_in/qc.run_qc/store/doctor/runlog，零素材依赖。
    doctor FAIL 时 raise（生成前失败，不渲染一半才报错）。
    """
    from video_director import doctor, pipeline, runlog

    pid = project_id or "script-only"
    base = config.project_dir(pid)
    dirs = config.init_project_dirs(base)
    warnings: List[str] = []

    def _fail(stage_err: str):
        try:
            store.set_stage(pid, store.Stage.FAILED, error=stage_err[:2000])
        except Exception:
            pass

    try:
        store.init_db()
        style_contract, style_warnings = style_mod.select_style(script, style_intent)
        warnings.extend(style_warnings)
        try:
            store.create_project(pid, script, {
                "voice": voice, "mode": "script_only",
                "width": width, "height": height, "fps": fps,
                "style_id": style_contract.style_id,
            })
        except Exception as exc:
            logger.warning(f"store.create_project 失败（不阻塞主流程）: {exc}")

        # Capability Gate：必需能力缺失在生成前失败
        doctor_results = doctor.run_doctor()
        if doctor.has_blocking_failures(doctor_results):
            fail_detail = "; ".join(
                f"{r.name}: {r.detail}" for r in doctor_results if r.status == "FAIL"
            )
            _fail(f"CAPABILITY_MISSING: {fail_detail}")
            raise ScriptOnlyError(
                f"CAPABILITY_MISSING（环境自检未过，禁止盲目继续）: {fail_detail}"
            )

        # 1. 场景化 + TTS（复用 pipeline 切分 + narration 合成）
        scene_texts = pipeline.split_script_scenes(script)
        store.set_stage(pid, store.Stage.TTS)
        narration_audio, word_timeline, narration_scenes = narration_mod.synth_narration(
            scene_texts, voice, dirs["audio"]
        )
        (dirs["input"] / "script.txt").write_text(script, encoding="utf-8")
        with open(dirs["plans"] / "scenes.json", "w", encoding="utf-8") as f:
            json.dump(narration_scenes, f, ensure_ascii=False, indent=2)
        with open(dirs["alignment"] / "words.json", "w", encoding="utf-8") as f:
            json.dump(word_timeline, f, ensure_ascii=False, indent=2)
        store.set_stage(pid, store.Stage.ALIGNMENT)

        # 2. IR（text 场景卡）
        store.set_stage(pid, store.Stage.PLANNING)
        ir = build_text_scenes(
            narration_scenes, style_contract, width=width, height=height, fps=fps
        )
        with open(dirs["plans"] / "composition.json", "w", encoding="utf-8") as f:
            json.dump(json.loads(ir.model_dump_json()), f, ensure_ascii=False, indent=2)
        (dirs["plans"] / "style.json").write_text(
            json.dumps({
                "style_id": style_contract.style_id,
                "reason": style_contract.reason,
                "contract": {
                    "subtitle_position": style_contract.subtitle_position,
                    "subtitle_font_size": style_contract.subtitle_font_size,
                    "bgm_volume": style_contract.bgm_volume,
                },
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 3. 逐场景渲染 text segment → concat
        store.set_stage(pid, store.Stage.RENDERING)
        narration_duration = narration_scenes[-1]["start"] + narration_scenes[-1]["duration"]
        seg_dir = dirs["render"] / "segments"
        segments: List[str] = []
        try:
            for i, scene in enumerate(ir.scenes):
                layer = scene.layers[0]
                segments.append(render_text_layer(
                    layer.text or "", layer.duration, seg_dir, i + 1,
                    width, height, fps, style_contract.subtitle_font_size,
                ))
            visual_path = renderer.concat_segments(
                segments, str(dirs["render"] / "visual.mp4"), width, height, fps
            )
        except renderer.RenderError as exc:
            _fail(f"RENDER_FAILED: {exc}")
            raise ScriptOnlyError(f"RENDER_FAILED: {exc}") from exc

        # 4. 混音（narration 主音轨）+ 字幕烧录
        try:
            master_path = renderer.mix_audio(
                visual_path, ir, {}, narration_audio, str(dirs["render"] / "master.mp4")
            )
        except renderer.RenderError as exc:
            _fail(f"AUDIO_MIX_FAILED: {exc}")
            raise ScriptOnlyError(f"AUDIO_MIX_FAILED: {exc}") from exc
        srt_path = dirs["render"] / "master.srt"
        srt_path.write_text(
            subtitle_mod.build_srt_from_scenes(narration_scenes), encoding="utf-8"
        )
        try:
            final_path = renderer.subtitle_burn_in(
                master_path, str(srt_path), str(dirs["render"] / "final.mp4")
            )
        except renderer.RenderError as exc:
            _fail(f"SUBTITLE_FAILED: {exc}")
            raise ScriptOnlyError(f"SUBTITLE_FAILED: {exc}") from exc

        # 5. QC（确定性）+ 交付
        store.set_stage(pid, store.Stage.QC)
        report = qc_mod.run_qc(
            ir, final_path, {}, narration_duration, expect_audio=True
        )
        report.save(str(dirs["qc"] / "deterministic.json"))
        if not report.ok:
            _fail(f"QC_FAILED: {json.dumps(report.to_dict(), ensure_ascii=False)}")
            raise ScriptOnlyError(f"QC 失败: {report.to_dict()}")
        final_out = dirs["output"] / "final.mp4"
        if Path(final_path) != final_out:
            import shutil
            shutil.copyfile(final_path, final_out)
        try:
            store.finish_project(pid, str(final_out), narration_duration, report.to_dict())
        except Exception as exc:
            logger.warning(f"store.finish_project 失败（不阻塞交付）: {exc}")

        result = ScriptOnlyResult(
            project_dir=str(base),
            final_path=str(final_out),
            ir=json.loads(ir.model_dump_json()),
            qc_report=report.to_dict(),
            narration_duration=narration_duration,
            warnings=warnings,
        )
        try:
            manifest = runlog.build_run_manifest(
                project_id=pid,
                workflow="script-only-compose",
                script_path=str(dirs["input"] / "script.txt"),
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
                },
                artifact_path=result.final_path,
                warnings=result.warnings,
            )
            runlog.save_run_manifest(base, manifest)
        except Exception as exc:
            logger.warning(f"run manifest 写入失败（不阻塞交付）: {exc}")
        return result
    except ScriptOnlyError:
        raise
    except Exception as exc:
        _fail(f"SCRIPT_ONLY_FAILED: {exc}")
        logger.exception(f"compose_script_only 未预期失败: {exc}")
        raise ScriptOnlyError(f"SCRIPT_ONLY_FAILED: {exc}") from exc
