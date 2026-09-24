"""Composition IR Renderer（V1 §19-§22 + V2 §41 BasicRenderer）。

三段式（video-use 三段式思想）：
1. 逐 layer 独立渲染为标准化 segment（统一分辨率/fps/codec/pixfmt，无音轨）
   - video: ffmpeg -ss/-t trim + setpts 变速 + scale/crop 画幅适配
   - image: ffmpeg -loop 1 + zoompan（motion）或静态帧
2. concat demuxer 拼接为 visual.mp4（重编码保证参数一致）
3. 音频合成：narration + BGM(ducking) + SFX → amix → master.mp4

LLM 永远不生成 FFmpeg 命令（V1 §21）：命令全部由本模块确定性构造。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from video_director import config, prober
from video_director.models import (
    AssetRef,
    AudioTrack,
    AudioTrackType,
    CompositionIR,
    Layer,
    LayerType,
    SceneIR,
)


class RenderError(Exception):
    pass


@dataclass
class RenderResult:
    visual_path: str
    master_path: str
    final_path: str
    segments: List[str]


# ---------------------------------------------------------------------------
# 命令执行
# ---------------------------------------------------------------------------

def _run(cmd: List[str], timeout: int = 600) -> str:
    """执行外部命令，失败抛 RenderError（精准日志）。"""
    logger.debug(f"exec: {' '.join(str(c) for c in cmd[:12])}{' ...' if len(cmd) > 12 else ''}")
    try:
        completed = subprocess.run(
            [str(c) for c in cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RenderError(f"ffmpeg 超时({timeout}s): {' '.join(map(str, cmd[:6]))}") from exc
    if completed.returncode != 0:
        raise RenderError(
            f"ffmpeg 失败(exit={completed.returncode}): "
            f"{(completed.stderr or '')[-800:]}"
        )
    return completed.stdout


def _ffmpeg() -> str:
    return prober._resolve_ffmpeg()


# ---------------------------------------------------------------------------
# 画幅适配（cover 模式：填充裁剪，V1 video.py _fit_clip_to_canvas 同语义）
# ---------------------------------------------------------------------------

def _cover_filter(src_w: int, src_h: int, dst_w: int, dst_h: int) -> str:
    """scale 保比例放大覆盖画布 + crop 居中裁剪（cover 语义）。"""
    return (
        f"scale={dst_w}:{dst_h}:force_original_aspect_ratio=increase,"
        f"crop={dst_w}:{dst_h}"
    )


# ---------------------------------------------------------------------------
# 单层渲染
# ---------------------------------------------------------------------------

def render_layer_segment(
    layer: Layer,
    asset: AssetRef,
    out_path: str,
    width: int,
    height: int,
    fps: int,
) -> str:
    """渲染单个 layer 为标准化 segment（无音轨）。

    -r 放输出侧：变速/trim 后重采样到目标 fps，保证 segment 时长精确。
    """
    ff = _ffmpeg()
    dst = (width, height)
    out_common = [
        "-an",
        "-r", str(fps),
        "-c:v", config.DEFAULT_VIDEO_CODEC,
        "-preset", "veryfast",
        "-crf", str(config.DEFAULT_CRF),
        "-pix_fmt", config.DEFAULT_PIXEL_FORMAT,
    ]

    if layer.type == LayerType.video:
        trim_len = layer.source_out - layer.source_in
        # -ss/-t 在 -i 前：输入侧只读 [source_in, source_in+trim_len] 的源
        vf_parts = []
        if abs(layer.speed - 1.0) > 0.001:
            factor = 1.0 / layer.speed
            vf_parts.append(f"setpts={factor:.6f}*PTS")
        vf_parts.append(_cover_filter(asset.width or dst[0], asset.height or dst[1], *dst))
        cmd = [
            ff, "-y", "-loglevel", "error",
            "-ss", f"{layer.source_in:.3f}",
            "-t", f"{trim_len:.3f}",
            "-i", asset.file,
            "-vf", ",".join(vf_parts),
            # 输出侧 -t 精确截断：变速 + -r 重采样会让每段漂移约 1-2 帧，
            # 累加后超 QC 时长容差，必须按预期时长精确收口（V1 §31）。
            "-t", f"{layer.duration:.3f}",
            *out_common,
            out_path,
        ]
        _run(cmd)
    elif layer.type == LayerType.image:
        dur = layer.duration
        frames = int(round(dur * fps))
        if layer.motion == "slow_zoom_in" and dur >= config.IMAGE_MOTION_MIN_DURATION:
            # zoompan 缓慢推近：1.0 → 1.12 倍线性，d=帧数
            zmax = 1.12
            vf = (
                f"scale={width * 2}:{height * 2},"
                f"zoompan=z='1+({zmax}-1)*on/{max(frames - 1, 1)}'"
                f":x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2'"
                f":d={frames}:s={width}x{height}:fps={fps}"
            )
            cmd = [
                ff, "-y", "-loglevel", "error",
                "-loop", "1", "-i", asset.file,
                "-frames:v", str(frames),
                "-vf", vf,
                *out_common,
                out_path,
            ]
        else:
            vf = _cover_filter(asset.width or dst[0], asset.height or dst[1], *dst)
            cmd = [
                ff, "-y", "-loglevel", "error",
                "-loop", "1", "-i", asset.file,
                "-frames:v", str(frames),
                "-vf", vf,
                *out_common,
                out_path,
            ]
        _run(cmd)
    else:
        raise RenderError(f"layer 类型 {layer.type} 不支持渲染（第一阶段）")

    # 校验产物
    probe = prober.probe_media(out_path)
    expect = layer.duration
    if probe.duration is None or abs(probe.duration - expect) > 0.15:
        raise RenderError(
            f"segment 时长 {probe.duration} 与预期 {expect} 偏差超 0.15s: {out_path}"
        )
    return out_path


# ---------------------------------------------------------------------------
# 拼接 + 音频
# ---------------------------------------------------------------------------

def _apply_fade(clip: str, out_path: str, fade_in: float, fade_out: float) -> str:
    """对单 clip 做淡入/淡出（重编码），不改时长。"""
    vf_parts = []
    if fade_in > 0:
        vf_parts.append(f"fade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        from video_director import prober
        dur = prober.probe_media(clip).duration or 0.0
        vf_parts.append(f"fade=t=out:st={max(0.0, dur - fade_out):.3f}:d={fade_out}")
    cmd = [
        _ffmpeg(), "-y", "-loglevel", "error", "-i", clip,
        "-vf", ",".join(vf_parts),
        "-c:v", config.DEFAULT_VIDEO_CODEC,
        "-preset", "veryfast", "-crf", str(config.DEFAULT_CRF),
        "-an", out_path,
    ]
    _run(cmd)
    return out_path


def splice_with_transitions(
    clips: List[str],
    transitions: List[str],
    out_path: str,
    width: int,
    height: int,
    fps: int,
) -> str:
    """场景级拼接：cut 用 concat；crossfade 用「淡出+淡入」连续过渡（方案 §60）。

    关键约束（V1 §20/§22）：转场不得移动旁白时间轴——xfade 重叠会缩短总时长
    导致 QC duration_vs_narration 失败。改用每 clip 独立淡入/淡出（不重叠），
    总时长严格 = 各场景之和 = 旁白时长。
    """
    if len(clips) == 1 or all(t in ("cut",) for t in transitions):
        return concat_segments(clips, out_path, width, height, fps)
    processed: List[str] = []
    for i, clip in enumerate(clips):
        p = clip
        fade_in = 0.4 if (i > 0 and transitions[i - 1] not in ("cut",)) else 0.0
        fade_out = 0.4 if (i < len(clips) - 1 and transitions[i] not in ("cut",)) else 0.0
        if fade_in > 0 or fade_out > 0:
            pp = f"{out_path}.f{i}.mp4"
            p = _apply_fade(clip, pp, fade_in, fade_out)
        processed.append(p)
    concat_segments(processed, out_path, width, height, fps)
    for i in range(len(clips)):
        Path(f"{out_path}.f{i}.mp4").unlink(missing_ok=True)
    return out_path


def concat_segments(segment_paths: List[str], out_path: str, width: int, height: int, fps: int) -> str:
    """concat demuxer + 重编码统一参数（segment 已标准化，此处流复制重封装拼接。

    由于所有 segment 由本 renderer 生成、参数完全一致，用 -c copy 拼接。
    """
    if not segment_paths:
        raise RenderError("无 segment 可拼接")
    list_file = out_path + ".concat.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in segment_paths:
            f.write(f"file '{Path(p).as_posix()}'\n")
    cmd = [
        _ffmpeg(), "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        "-video_track_timescale", "15360",
        out_path,
    ]
    _run(cmd)
    os.remove(list_file)
    return out_path


def mix_audio(
    visual_path: str,
    ir: CompositionIR,
    assets: Dict[str, AssetRef],
    narration_file: Optional[str],
    out_path: str,
) -> Optional[str]:
    """音频合成（V2 §27/§31/§32）。

    Narration 主音轨；BGM 循环对齐总长并全程应用固定 duck（有旁白时压低，
    第一版用恒定压低量近似 sidechain ducking）；SFX 在语义时间点 adelay 插入。
    无任何音频输入时返回 None（纯视觉片，调用方跳过混音）。
    """
    import math

    ff = _ffmpeg()
    inputs: List[str] = []
    filters: List[str] = []
    ref_labels: List[str] = []   # 进 amix 的标签（经 filter 加工后）
    idx = 0
    total = ir.project.duration

    # ---- narration（主音轨，不压低）----
    if narration_file:
        inputs += ["-i", narration_file]
        filters.append(f"[{idx}:a]aresample=44100,aformat=channel_layouts=mono[nar]")
        ref_labels.append("[nar]")
        idx += 1

    # ---- BGM（首个有效 bgm track；循环铺满总长 + duck + 用户音量）----
    bgm_track: Optional[AudioTrack] = None
    for t in ir.audio:
        if t.type == AudioTrackType.bgm and assets.get(t.asset_id or ""):
            bgm_track = t
            break
    if bgm_track is not None:
        duck_db = config.DUCKING_DB if narration_file else 0.0
        total_db = duck_db + 20 * math.log10(max(bgm_track.volume, 0.01))
        inputs += ["-stream_loop", "-1", "-i", assets[bgm_track.asset_id].file]
        filters.append(
            f"[{idx}:a]aresample=44100,aformat=channel_layouts=mono,"
            f"volume={total_db:.2f}dB,atrim=0:{total:.3f}[bgm]"
        )
        ref_labels.append("[bgm]")
        idx += 1

    # ---- SFX（语义时间点插入）----
    sfx_labels: List[str] = []
    for t in ir.audio:
        if t.type != AudioTrackType.sfx:
            continue
        a = assets.get(t.asset_id or "")
        if a is None:
            continue
        inputs += ["-i", a.file]
        delay_ms = int(t.start * 1000)
        vol_db = 20 * math.log10(max(t.volume, 0.01))
        label = f"sfx{len(sfx_labels)}"
        filters.append(
            f"[{idx}:a]aresample=44100,aformat=channel_layouts=mono,"
            f"volume={vol_db:.2f}dB,adelay={delay_ms}|{delay_ms}[{label}]"
        )
        sfx_labels.append(f"[{label}]")
        idx += 1

    if not ref_labels and not sfx_labels:
        return None  # 无任何音频

    # ---- amix 汇总（normalize=0：Narration 是主音轨，不得被均分衰减）----
    n_inputs = len(ref_labels) + len(sfx_labels)
    filters.append(
        f"{''.join(ref_labels)}{''.join(sfx_labels)}"
        f"amix=inputs={n_inputs}:duration=longest:dropout_transition=0:normalize=0,"
        f"alimiter=limit=0.97,atrim=0:{total:.3f}[aout]"
    )

    # 混音独立落盘，再与视觉流复制合并（避免一次命令里 -map 紊乱）
    audio_tmp = out_path + ".mix.m4a"
    _run([
        ff, "-y", "-loglevel", "error",
        *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[aout]",
        "-c:a", "aac", "-b:a", "192k",
        audio_tmp,
    ])

    _run([
        ff, "-y", "-loglevel", "error",
        "-i", visual_path,
        "-i", audio_tmp,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy",
        "-c:a", "copy",
        "-shortest",
        out_path,
    ])
    os.remove(audio_tmp)
    return out_path


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def render_composition(
    ir: CompositionIR,
    assets: Dict[str, AssetRef],
    work_dir: Path,
    narration_file: Optional[str] = None,
    subtitle_file: Optional[str] = None,
    title_card: Optional[str] = None,
    lower_third_name: Optional[str] = None,
    lower_third_role: Optional[str] = None,
    watermark: Optional[str] = None,
) -> RenderResult:
    """渲染完整 Composition IR。字幕最后烧录（V1 §20/§22 video-use Rule 1）。

    title_card：标题卡 mp4 路径（§16 Title Engine）。非空时插到片头
    （segments 首位，正文旁白时间轴不受影响）。
    lower_third_name：人物条名称（§17 Lower Third），非空时片头后叠加
    左下角身份条（名前+可选头衔，4s 渐入渐出，不改时长）。
    """
    seg_dir = work_dir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    w, h = ir.project.width, ir.project.height

    # 1. 逐场景渲染 → 场景 clip（场景内 layers 拼接），保留转场信息
    seq = 0
    scene_clips: List[str] = []
    scene_transitions: List[str] = []
    for scene in ir.scenes:
        scene_segs: List[str] = []
        for layer in scene.layers:
            asset = assets.get(layer.asset_id or "")
            if asset is None:
                raise RenderError(f"素材不存在: {layer.asset_id}")
            seq += 1
            seg_path = str(seg_dir / f"{seq:04d}.mp4")
            render_layer_segment(layer, asset, seg_path, w, h, ir.project.fps)
            scene_segs.append(seg_path)
            logger.info(f"segment {seq:04d} [{scene.id}/{layer.type.value}] {layer.duration:.2f}s")
        scene_clip = str(seg_dir / f"scene_{scene.id}.mp4")
        concat_segments(scene_segs, scene_clip, w, h, ir.project.fps)
        scene_clips.append(scene_clip)
        # 场景转出到下一场景的转场类型（最后一个场景的 transition_out 忽略）
        tr = getattr(scene.transition_out, "type", "cut") if len(scene_clips) < len(ir.scenes) else "cut"
        scene_transitions.append(tr)

    # 0. 片头标题卡（§16：MainTitle 入场动效，前置段不压缩正文）
    if title_card and os.path.isfile(title_card):
        scene_clips.insert(0, title_card)
        scene_transitions.insert(0, "cut")
        logger.info("segment 0000 [title-card] 前置")

    # 2. 场景间转场拼接（cut=concat，crossfade/fade=xfade）
    visual_path = str(work_dir / "visual.mp4")
    if len(scene_clips) == 1:
        shutil.move(scene_clips[0], visual_path)
    else:
        splice_with_transitions(
            scene_clips, scene_transitions[: len(scene_clips) - 1],
            visual_path, w, h, ir.project.fps,
        )

    # 3. 音频
    master_path = str(work_dir / "master.mp4")
    audio_result = mix_audio(visual_path, ir, assets, narration_file, master_path)
    if audio_result is None:
        master_path = visual_path  # 纯视觉

    # 4. 字幕烧录（必须最后，V1 §20）
    final_path = str(work_dir / "final.mp4")
    if subtitle_file and os.path.isfile(subtitle_file):
        subtitle_burn_in(master_path, subtitle_file, final_path)
    else:
        final_path = master_path

    # 5. 人物条叠加（§17 Lower Third：字幕之后、最终前；不改时长）
    if lower_third_name and lower_third_name.strip():
        from video_director import title_engine
        lt_out = str(work_dir / "final_lt.mp4")
        final_path = title_engine.render_lower_third_overlay(
            final_path, lower_third_name.strip(), lt_out,
            role=lower_third_role,
        )

    # 6. 水印（右下角，最后叠加，全程显示）
    if watermark and watermark.strip():
        from video_director import title_engine
        wm_out = str(work_dir / "final_watermark.mp4")
        final_path = title_engine.render_watermark_overlay(
            final_path, watermark.strip(), wm_out,
        )

    return RenderResult(
        visual_path=visual_path,
        master_path=master_path,
        final_path=final_path,
        segments=scene_clips,
    )


def subtitle_burn_in(video_in: str, srt_path: str, out_path: str) -> str:
    """ffmpeg subtitles filter 烧录（Windows 路径转义）。

    正确转义 = 反斜杠换正斜杠 + 冒号转 \\: + 整条路径用单引号包裹：
        subtitles='D\\:/path/to/master.srt'
    （实测：缺单引号会把冒号误解为 original_size option 分隔符）
    """
    esc = srt_path.replace("\\", "/").replace(":", "\\:")
    cmd = [
        _ffmpeg(), "-y", "-loglevel", "error",
        "-i", video_in,
        "-vf", f"subtitles='{esc}'",
        "-c:v", config.DEFAULT_VIDEO_CODEC,
        "-preset", "veryfast",
        "-crf", str(config.DEFAULT_CRF),
        "-c:a", "copy",
        out_path,
    ]
    _run(cmd)
    return out_path
