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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from video_director import artifact_graph, config, prober
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
    reused_artifacts: List[str] = field(default_factory=list)
    rebuilt_artifacts: List[str] = field(default_factory=list)
    artifact_manifest_path: Optional[str] = None


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
    """FFmpeg binary resolution. Kept as the module-level entry point because
    narration.py and the smoke tests call renderer._ffmpeg() directly; removing
    it broke them with NameError/AttributeError chains."""
    return prober._resolve_ffmpeg()


def _run_capture(cmd: List[str], timeout: int = 600) -> tuple[str, str]:
    """Run an FFmpeg/ffprobe command and preserve stderr for machine-readable probe output."""
    logger.debug(f"exec-capture: {' '.join(str(c) for c in cmd[:12])}{' ...' if len(cmd) > 12 else ''}")
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
        raise RenderError(f"媒体检查超时({timeout}s): {' '.join(map(str, cmd[:6]))}") from exc
    if completed.returncode != 0:
        raise RenderError(
            f"媒体检查失败(exit={completed.returncode}): "
            f"{(completed.stderr or '')[-1200:]}"
        )
    return completed.stdout or "", completed.stderr or ""


_AUDIO_LOUDNESS_TARGET_I = -16.0
_AUDIO_LOUDNESS_TARGET_TP = -1.5
_AUDIO_LOUDNESS_TARGET_LRA = 11.0


def _normalize_audio_loudness(
    mixed_path: str,
    normalized_path: str,
    ffmpeg_bin: Optional[str] = None,
) -> dict:
    """Apply EBU R128 two-pass loudness normalization to the completed timeline mix.

    Analysis runs against the mixed waveform (not individual tracks), so narration,
    music and sound effects retain their timeline gain/fade relationships. Truly silent
    or unmeasurable input uses FFmpeg's single-pass fallback rather than inventing
    measurements. The output is lossless PCM until the final MP4 AAC mux.
    """
    import json
    import math
    import re

    ff = ffmpeg_bin or prober._resolve_ffmpeg()
    analysis_filter = (
        f"loudnorm=I={_AUDIO_LOUDNESS_TARGET_I}:TP={_AUDIO_LOUDNESS_TARGET_TP}:"
        f"LRA={_AUDIO_LOUDNESS_TARGET_LRA}:print_format=json"
    )
    _, stderr = _run_capture([
        ff, "-hide_banner", "-nostats", "-i", mixed_path,
        "-map", "0:a:0", "-af", analysis_filter, "-f", "null", "-",
    ])
    match = re.search(r'\{\s*"input_i".*?\}', stderr, re.S)
    measured = None
    if match:
        try:
            candidate = json.loads(match.group(0))
            required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
            numeric = {key: float(candidate[key]) for key in required}
            if all(math.isfinite(value) for value in numeric.values()) and numeric["input_i"] > -99:
                measured = numeric
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            measured = None

    if measured:
        filter_expr = (
            f"loudnorm=I={_AUDIO_LOUDNESS_TARGET_I}:TP={_AUDIO_LOUDNESS_TARGET_TP}:"
            f"LRA={_AUDIO_LOUDNESS_TARGET_LRA}:"
            f"measured_I={measured['input_i']:.4f}:"
            f"measured_TP={measured['input_tp']:.4f}:"
            f"measured_LRA={measured['input_lra']:.4f}:"
            f"measured_thresh={measured['input_thresh']:.4f}:"
            f"offset={measured['target_offset']:.4f}:linear=true:print_format=summary"
        )
        mode = "two-pass"
    else:
        filter_expr = (
            f"loudnorm=I={_AUDIO_LOUDNESS_TARGET_I}:TP={_AUDIO_LOUDNESS_TARGET_TP}:"
            f"LRA={_AUDIO_LOUDNESS_TARGET_LRA}:print_format=summary"
        )
        mode = "single-pass-fallback"
        logger.warning("混音响度分析无有效测量值（静音或极低电平），使用单遍 loudnorm 保持可解码输出")

    _run([
        ff, "-y", "-hide_banner", "-loglevel", "warning",
        "-i", mixed_path, "-map", "0:a:0", "-af", filter_expr,
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", normalized_path,
    ])
    report = {
        "mode": mode,
        "target_i_lufs": _AUDIO_LOUDNESS_TARGET_I,
        "target_true_peak_dbtp": _AUDIO_LOUDNESS_TARGET_TP,
        "target_lra_lu": _AUDIO_LOUDNESS_TARGET_LRA,
    }
    if measured:
        report["measured_input_i_lufs"] = measured["input_i"]
        report["measured_input_tp_dbtp"] = measured["input_tp"]
        report["measured_input_lra_lu"] = measured["input_lra"]
    logger.info(
        f"audio loudness normalization: mode={mode}, "
        f"target={_AUDIO_LOUDNESS_TARGET_I} LUFS / {_AUDIO_LOUDNESS_TARGET_TP} dBTP"
    )
    return report


def _verify_muxed_audio(output_path: str, expected_duration: float) -> dict:
    """Require an audio stream, bounded media duration, and full audio+video decode."""
    import json
    import math

    ff = prober._resolve_ffmpeg()
    ffprobe = prober._resolve_ffprobe()
    stream = {}
    probe_engine = "ffprobe"
    if ffprobe:
        stdout, _ = _run_capture([
            ffprobe, "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate,channels,duration:format=duration",
            "-of", "json", output_path,
        ])
        try:
            media = json.loads(stdout)
            streams = media.get("streams") or []
            if not streams:
                raise RenderError("最终 MP4 没有音频流")
            stream = streams[0]
            raw_duration = stream.get("duration") or (media.get("format") or {}).get("duration")
            audio_duration = float(raw_duration)
            channels = int(stream.get("channels") or 0)
            codec = str(stream.get("codec_name") or "")
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise RenderError(f"无法读取最终 MP4 音频流信息: {exc}") from exc
        if not codec or channels < 1:
            raise RenderError("最终 MP4 音频流缺少编码器或声道信息")
    else:
        # Portable distributions may ship ffmpeg without ffprobe. Use the project's
        # established ffmpeg-stderr probe fallback rather than disabling final export.
        try:
            probe = prober.probe_media(output_path)
        except Exception as exc:
            raise RenderError(f"无法探测最终 MP4（ffprobe 不可用且 ffmpeg 回退失败）: {exc}") from exc
        if not probe.has_audio_stream:
            raise RenderError("最终 MP4 没有音频流")
        audio_duration = float(probe.duration or 0.0)
        codec = "unknown (ffmpeg-stderr fallback)"
        channels = None
        probe_engine = probe.engine

    if not math.isfinite(audio_duration) or audio_duration <= 0:
        raise RenderError("最终 MP4 的音频时长无效")
    # AAC priming/padding may add a few milliseconds; a materially truncated track fails.
    tolerance = max(0.35, config.RENDER_DURATION_TOLERANCE)
    if abs(audio_duration - float(expected_duration)) > tolerance:
        raise RenderError(
            f"最终 MP4 音频/容器时长不匹配: actual={audio_duration:.3f}s, "
            f"expected={float(expected_duration):.3f}s, tolerance={tolerance:.3f}s"
        )

    # Decode every mapped video frame and every audio packet through EOF; stream presence alone is insufficient.
    _run([
        ff, "-hide_banner", "-v", "error", "-xerror", "-err_detect", "explode", "-i", output_path,
        "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-",
    ])
    report = {
        "codec": codec,
        "sample_rate": stream.get("sample_rate") if stream else None,
        "channels": channels,
        "audio_duration": audio_duration,
        "expected_duration": float(expected_duration),
        "probe_engine": probe_engine,
        "full_decode": "PASS",
    }
    logger.info(
        f"final mux validation: audio={codec}, channels={channels}, "
        f"duration={audio_duration:.3f}s, probe={probe_engine}, full audio+video decode=PASS"
    )
    return report

# ---------------------------------------------------------------------------
# 画幅适配（cover 模式：填充裁剪，V1 video.py _fit_clip_to_canvas 同语义）
# ---------------------------------------------------------------------------

def _cover_filter(src_w: int, src_h: int, dst_w: int, dst_h: int) -> str:
    """scale 保比例放大覆盖画布 + crop 居中裁剪（cover 语义）。"""
    return (
        f"scale={dst_w}:{dst_h}:force_original_aspect_ratio=increase,"
        f"crop={dst_w}:{dst_h}"
    )

def _contain_filter(src_w: int, src_h: int, dst_w: int, dst_h: int) -> str:
    """完整保留素材比例并用 pad 填充画布（contain 语义）。"""
    return (
        f"scale={dst_w}:{dst_h}:force_original_aspect_ratio=decrease,"
        f"pad={dst_w}:{dst_h}:(ow-iw)/2:(oh-ih)/2"
    )


def _static_transform(layer: Layer) -> Optional[dict]:
    """读取 set_transform 写入的静态变换 {scale, opacity}（常值，无动画）。

    无 → None。§20 set_transform：静态缩放/透明度（x/y 偏移暂不支持）。
    """
    inst = layer.motion_instance or {}
    tr = (inst.get("parameters") or {}).get("transform") or {}
    if not tr:
        return None
    scale = float(tr.get("scale", 1.0))
    opacity = float(tr.get("opacity", 1.0))
    if scale == 1.0 and opacity == 1.0:
        return None
    return {"scale": scale, "opacity": opacity}


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

    Motion Recipe 渲染（Shotcraft CMP-002/REN-001）：Layer.motion_recipe_id
    非空时按 registries.motion_compiler 编译产物构造滤镜——
    - scale 通道 → zoompan 动态缩放（on 方言）
    - opacity 通道 → geq alpha 动画（T 方言）
    动画不改变 segment 时长（Narration Timeline 铁律）。
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

    # Motion Recipe 编译（无 recipe → None 走原逻辑）
    motion_comp = None
    if getattr(layer, "motion_recipe_id", None):
        from video_director.registries import motion_compiler
        try:
            motion_comp = motion_compiler.compile_layer_motion(layer, width, height, fps=fps)
        except motion_compiler.MotionCompileError as exc:
            # 编译失败显式抛（validator/resolver 应已拦截，这里双保险）
            raise RenderError(f"motion 编译失败 [{exc.code}]: {exc}") from exc

    if layer.type == LayerType.video:
        trim_len = layer.source_out - layer.source_in
        # -ss/-t 在 -i 前：输入侧只读 [source_in, source_in+trim_len] 的源
        vf_parts = []
        if abs(layer.speed - 1.0) > 0.001:
            factor = 1.0 / layer.speed
            vf_parts.append(f"setpts={factor:.6f}*PTS")
        fit_mode = getattr(layer, "fit_mode", "cover") or "cover"
        fit_filter = _contain_filter if fit_mode == "contain" else _cover_filter
        vf_parts.append(fit_filter(asset.width or dst[0], asset.height or dst[1], *dst))
        if motion_comp:
            # video 层仅支持 opacity（geq alpha 折叠进 YUV 会丢动画，需黑底合成）
            if motion_comp["opacity_expr"] != "1.0":
                _render_video_layer_alpha_composite(
                    layer, asset, out_path, width, height, fps, motion_comp
                )
                return _verify_segment_duration(out_path, layer.duration)
            # 若 opacity 无动画，其余通道（scale/x/y）本渲染器对 video 层不支持——
            # 但"无任何视觉变化"的纯参数动效（如仅调 stagger）应无副作用跳过。
            if motion_comp["scale_expr"] != "1.0" or motion_comp["overlay_x_expr"] != "0" or motion_comp["overlay_y_expr"] != "0":
                raise RenderError(
                    f"[MOTION_LAYER_UNSUPPORTED] video 层动效 {motion_comp['motion_id']} "
                    "含 scale/x/y 通道，本渲染器暂不支持（仅 opacity）"
                )
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
        st = _static_transform(layer)
        # 静态变换（§20 set_transform）：scale 常值 zoompan + opacity 常值 alpha
        if st is not None and (st["opacity"] != 1.0 or st["scale"] != 1.0):
            zoom_expr = f"{st['scale']:.4f}" if st["scale"] != 1.0 else None
            if st["opacity"] != 1.0:
                _render_image_layer_alpha_composite(
                    asset, out_path, width, height, fps, frames,
                    {"opacity_expr": f"{st['opacity']:.4f}"},
                    zoom_expr=zoom_expr,
                )
            else:
                # 仅静态缩放：常值 zoompan（居中）
                vf = (
                    f"scale={width * 2}:{height * 2},"
                    f"zoompan=z='({zoom_expr})'"
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
                _run(cmd)
            return _verify_segment_duration(out_path, layer.duration)
        # Motion Recipe 优先（Gallery 应用的动效），旧 motion 枚举兜底
        if motion_comp and motion_comp["scale_expr"] != "1.0":
            # zoompan 动态缩放：z=编译表达式（on 方言），x/y 居中
            vf = (
                f"scale={width * 2}:{height * 2},"
                f"zoompan=z='({motion_comp['scale_expr']})'"
                f":x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2'"
                f":d={frames}:s={width}x{height}:fps={fps}"
            )
            if motion_comp["opacity_expr"] != "1.0":
                # alpha 动画须黑底合成（YUV 折叠会丢动画）→ 走 filter_complex
                _render_image_layer_alpha_composite(
                    asset, out_path, width, height, fps, frames, motion_comp
                )
                return _verify_segment_duration(out_path, layer.duration)
            cmd = [
                ff, "-y", "-loglevel", "error",
                "-loop", "1", "-i", asset.file,
                "-frames:v", str(frames),
                "-vf", vf,
                *out_common,
                out_path,
            ]
        elif motion_comp and motion_comp["opacity_expr"] != "1.0":
            # 仅 opacity 动画（无 scale）：黑底合成
            _render_image_layer_alpha_composite(
                asset, out_path, width, height, fps, frames, motion_comp,
                zoom_expr=None,
            )
            return _verify_segment_duration(out_path, layer.duration)
        elif layer.motion == "slow_zoom_in" and dur >= config.IMAGE_MOTION_MIN_DURATION:
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

    return _verify_segment_duration(out_path, layer.duration)


def _verify_segment_duration(out_path: str, expect: float) -> str:
    """segment 时长校验（原 render_layer_segment 尾部逻辑抽出复用）。"""
    probe = prober.probe_media(out_path)
    if probe.duration is None or abs(probe.duration - expect) > 0.15:
        raise RenderError(
            f"segment 时长 {probe.duration} 与预期 {expect} 偏差超 0.15s: {out_path}"
        )
    return out_path


def _render_image_layer_alpha_composite(
    asset: AssetRef,
    out_path: str,
    width: int,
    height: int,
    fps: int,
    frames: int,
    motion_comp: dict,
    zoom_expr: Optional[str] = None,
) -> str:
    """image 层 opacity 动画 → 黑底 filter_complex 合成（alpha 可见）。

    geq 产 alpha 动画后直接 format=yuv420p 会把 alpha 静默折叠（YAVG 不变，
    动画丢失）——必须 overlay 到黑色画布上让 alpha 混合为亮度变化。
    zoom_expr 非空时 fg 先做 zoompan 缩放再 geq。
    """
    ff = _ffmpeg()
    fg_chain = f"scale={width * 2}:{height * 2}"
    if zoom_expr:
        fg_chain += (
            f",zoompan=z='({zoom_expr})'"
            f":x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2'"
            f":d={frames}:s={width}x{height}:fps={fps}"
        )
    else:
        fg_chain += f",scale={width}:{height}"
    fg_chain += (
        f",format=yuva420p,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)'"
        f":a='255*({motion_comp['opacity_expr']})'"
    )
    graph = (
        f"[0:v]{fg_chain}[fg];"
        f"color=c=black:s={width}x{height}:r={fps}[bg];"
        f"[bg][fg]overlay=shortest=1,format={config.DEFAULT_PIXEL_FORMAT}[v]"
    )
    cmd = [
        ff, "-y", "-loglevel", "error",
        "-loop", "1", "-i", asset.file,
        "-filter_complex", graph,
        "-map", "[v]",
        "-frames:v", str(frames),
        "-an",
        "-c:v", config.DEFAULT_VIDEO_CODEC,
        "-preset", "veryfast",
        "-crf", str(config.DEFAULT_CRF),
        "-pix_fmt", config.DEFAULT_PIXEL_FORMAT,
        out_path,
    ]
    _run(cmd)
    return out_path


def _render_video_layer_alpha_composite(
    layer: Layer,
    asset: AssetRef,
    out_path: str,
    width: int,
    height: int,
    fps: int,
    motion_comp: dict,
) -> str:
    """video 层 opacity 动画 → 黑底 filter_complex 合成（trim/变速后 alpha）。"""
    ff = _ffmpeg()
    trim_len = layer.source_out - layer.source_in
    fg_chain = []
    if abs(layer.speed - 1.0) > 0.001:
        fg_chain.append(f"setpts={1.0 / layer.speed:.6f}*PTS")
    fg_chain.append(_cover_filter(asset.width or width, asset.height or height, width, height))
    fg_chain.append(
        f"format=yuva420p,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)'"
        f":a='255*({motion_comp['opacity_expr']})'"
    )
    graph = (
        f"[0:v]{','.join(fg_chain)}[fg];"
        f"color=c=black:s={width}x{height}:r={fps}[bg];"
        f"[bg][fg]overlay=shortest=1,format={config.DEFAULT_PIXEL_FORMAT}[v]"
    )
    cmd = [
        ff, "-y", "-loglevel", "error",
        "-ss", f"{layer.source_in:.3f}",
        "-t", f"{trim_len:.3f}",
        "-i", asset.file,
        "-filter_complex", graph,
        "-map", "[v]",
        "-t", f"{layer.duration:.3f}",
        "-an",
        "-r", str(fps),
        "-c:v", config.DEFAULT_VIDEO_CODEC,
        "-preset", "veryfast",
        "-crf", str(config.DEFAULT_CRF),
        "-pix_fmt", config.DEFAULT_PIXEL_FORMAT,
        out_path,
    ]
    _run(cmd)
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
        # morph 过渡与 crossfade 同类（非 cut 即有淡入淡出，不压缩旁白轴）
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
    """按 Composition 音轨时间合成。

    Narration、original、BGM、ambience、dialogue、SFX 各自消费 start/duration、
    mute、volume 与 fade；BGM/环境音可循环，但只在定义的区间内发声。
    混音尾部补静音至项目总长，避免仅靠近片尾的短音效触发 -shortest 截断视频。
    无任何有效音频输入时返回 None（纯视觉片，调用方跳过混音）。
    """
    import math

    ff = _ffmpeg()
    inputs: List[str] = []
    filters: List[str] = []
    ref_labels: List[str] = []   # 进 amix 的标签（经 filter 加工后）
    idx = 0
    total = ir.project.duration

    # ---- narration（AudioTrack 控制音量、静音、fade 和时间位置）----
    def gain_filter(volume: float, extra_db: float = 0.0) -> str:
        """Render zero as silence, otherwise apply a linear gain in dB."""
        value = max(0.0, float(volume))
        if value == 0.0:
            return "volume=0"
        return f"volume={20 * math.log10(value) + extra_db:.2f}dB"

    narration_track = next((t for t in ir.audio if t.type == AudioTrackType.narration), None)
    if narration_file and not (narration_track and getattr(narration_track, "muted", False)):
        nar_start = max(0.0, float(narration_track.start)) if narration_track else 0.0
        nar_requested = float(narration_track.duration) if narration_track else total
        nar_duration = min(nar_requested, max(0.0, total - nar_start))
        if nar_duration > 0.01:
            inputs += ["-i", narration_file]
            nar_volume = float(narration_track.volume) if narration_track else 1.0
            chain = (
                f"[{idx}:a]aresample=44100,aformat=channel_layouts=mono,"
                f"{gain_filter(nar_volume)},atrim=0:{nar_duration:.3f}"
            )
            fade_in = min(float(getattr(narration_track, "fadeIn", 0.0) or 0.0), nar_duration) if narration_track else 0.0
            fade_out = min(float(getattr(narration_track, "fadeOut", 0.0) or 0.0), nar_duration) if narration_track else 0.0
            if fade_in > 0:
                chain += f",afade=t=in:st=0:d={fade_in:.3f}"
            if fade_out > 0:
                chain += f",afade=t=out:st={max(0.0, nar_duration - fade_out):.3f}:d={fade_out:.3f}"
            if nar_start > 0:
                chain += f",adelay={int(round(nar_start * 1000))}:all=1"
            chain += "[nar]"
            filters.append(chain)
            ref_labels.append("[nar]")
            idx += 1

    # ---- original：源区间截取，start/duration 决定输出时间位置 ----
    original_tracks = [
        t for t in ir.audio
        if t.type == AudioTrackType.original
        and not getattr(t, "muted", False)
        and assets.get(t.asset_id or "")
    ]
    for oi, original_track in enumerate(original_tracks):
        src = assets.get(original_track.asset_id or "")
        if not src or src.type != "video" or not os.path.isfile(src.file):
            continue
        track_start = max(0.0, float(original_track.start or 0.0))
        remaining = max(0.0, total - track_start)
        if remaining <= 0.01:
            continue
        src_in = max(0.0, float(getattr(original_track, "source_in", 0.0) or 0.0))
        src_out = getattr(original_track, "source_out", None)
        span = max(0.0, float(src_out) - src_in) if src_out is not None else 0.0
        track_dur = min(float(original_track.duration), span or float(original_track.duration), remaining)
        if track_dur <= 0.01:
            continue
        inputs += ["-ss", f"{src_in:.3f}", "-t", f"{track_dur:.3f}", "-i", src.file]
        duck_db = config.DUCKING_DB if (narration_file and original_track.ducking) else 0.0
        chain_o = (
            f"[{idx}:a]aresample=44100,aformat=channel_layouts=mono,"
            f"{gain_filter(original_track.volume, duck_db)}"
        )
        fin_o = min(float(getattr(original_track, "fadeIn", 0.0) or 0.0), track_dur)
        fout_o = min(float(getattr(original_track, "fadeOut", 0.0) or 0.0), track_dur)
        chain_o += f",atrim=0:{track_dur:.3f}"
        if fin_o > 0:
            chain_o += f",afade=t=in:st=0:d={fin_o:.3f}"
        if fout_o > 0:
            chain_o += f",afade=t=out:st={max(0.0, track_dur - fout_o):.3f}:d={fout_o:.3f}"
        delay_ms = int(round(track_start * 1000))
        if delay_ms:
            chain_o += f",adelay={delay_ms}:all=1"
        chain_o += f"[orig{oi}]"
        filters.append(chain_o)
        ref_labels.append(f"[orig{oi}]")
        idx += 1

    # ---- BGM / 环境音：尊重每条轨的 start、duration、mute、gain、fade ----
    continuous_tracks = [
        t for t in ir.audio
        if t.type in (AudioTrackType.bgm, AudioTrackType.ambience)
        and not getattr(t, "muted", False)
        and assets.get(t.asset_id or "")
    ]
    continuous_index = {AudioTrackType.bgm: 0, AudioTrackType.ambience: 0}
    for track in continuous_tracks:
        asset = assets.get(track.asset_id or "")
        if not asset:
            continue
        track_start = max(0.0, float(track.start or 0.0))
        remaining = max(0.0, total - track_start)
        src_in = max(0.0, float(getattr(track, "source_in", 0.0) or 0.0))
        src_out = getattr(track, "source_out", None)
        has_source_window = src_in > 0 or src_out is not None
        source_span = max(0.0, float(src_out) - src_in) if src_out is not None else float(track.duration)
        clip_duration = min(float(track.duration), source_span, remaining)
        if clip_duration <= 0.01:
            continue
        if has_source_window:
            inputs += ["-ss", f"{src_in:.3f}", "-t", f"{clip_duration:.3f}", "-i", asset.file]
        else:
            # Default BGM/ambience loops to its requested timeline duration.
            inputs += ["-stream_loop", "-1", "-i", asset.file]
        duck_db = config.DUCKING_DB if (narration_file and track.type == AudioTrackType.bgm) else 0.0
        chain = (
            f"[{idx}:a]aresample=44100,aformat=channel_layouts=mono,"
            f"{gain_filter(track.volume, duck_db)}"
        )
        fade_in = min(float(getattr(track, "fadeIn", 0.0) or 0.0), clip_duration)
        fade_out = min(float(getattr(track, "fadeOut", 0.0) or 0.0), clip_duration)
        if fade_in > 0:
            chain += f",afade=t=in:st=0:d={fade_in:.3f}"
        if fade_out > 0:
            chain += f",afade=t=out:st={max(0.0, clip_duration - fade_out):.3f}:d={fade_out:.3f}"
        chain += f",atrim=0:{clip_duration:.3f}"
        delay_ms = int(round(track_start * 1000))
        if delay_ms:
            chain += f",adelay={delay_ms}:all=1"
        prefix = "bgm" if track.type == AudioTrackType.bgm else "ambience"
        label = f"{prefix}{continuous_index[track.type]}"
        continuous_index[track.type] += 1
        chain += f"[{label}]"
        filters.append(chain)
        ref_labels.append(f"[{label}]")
        idx += 1

    # ---- SFX / 对白：一次性片段按 duration 截断，并放到 start 时间点 ----
    positioned_labels: List[str] = []
    positioned_index = {AudioTrackType.sfx: 0, AudioTrackType.dialogue: 0}
    for track in ir.audio:
        if track.type not in (AudioTrackType.sfx, AudioTrackType.dialogue):
            continue
        if getattr(track, "muted", False):
            continue
        asset = assets.get(track.asset_id or "")
        if asset is None:
            continue
        track_start = max(0.0, float(track.start or 0.0))
        remaining = max(0.0, total - track_start)
        src_in = max(0.0, float(getattr(track, "source_in", 0.0) or 0.0))
        src_out = getattr(track, "source_out", None)
        source_span = max(0.0, float(src_out) - src_in) if src_out is not None else float(track.duration)
        clip_duration = min(float(track.duration), source_span, remaining)
        if clip_duration <= 0.01:
            continue
        if src_in > 0 or src_out is not None:
            inputs += ["-ss", f"{src_in:.3f}", "-t", f"{clip_duration:.3f}", "-i", asset.file]
        else:
            inputs += ["-i", asset.file]
        chain = (
            f"[{idx}:a]aresample=44100,aformat=channel_layouts=mono,"
            f"{gain_filter(track.volume)},atrim=0:{clip_duration:.3f}"
        )
        fade_in = min(float(getattr(track, "fadeIn", 0.0) or 0.0), clip_duration)
        fade_out = min(float(getattr(track, "fadeOut", 0.0) or 0.0), clip_duration)
        if fade_in > 0:
            chain += f",afade=t=in:st=0:d={fade_in:.3f}"
        if fade_out > 0:
            chain += f",afade=t=out:st={max(0.0, clip_duration - fade_out):.3f}:d={fade_out:.3f}"
        delay_ms = int(round(track_start * 1000))
        if delay_ms:
            chain += f",adelay={delay_ms}|{delay_ms}"
        prefix = "sfx" if track.type == AudioTrackType.sfx else "dialogue"
        label = f"{prefix}{positioned_index[track.type]}"
        positioned_index[track.type] += 1
        chain += f"[{label}]"
        filters.append(chain)
        positioned_labels.append(f"[{label}]")
        idx += 1

    if not ref_labels and not positioned_labels:
        return None  # 无任何音频

    # ---- amix 汇总（normalize=0：Narration 是主音轨，不得被均分衰减）----
    n_inputs = len(ref_labels) + len(positioned_labels)
    filters.append(
        f"{''.join(ref_labels)}{''.join(positioned_labels)}"
        f"amix=inputs={n_inputs}:duration=longest:dropout_transition=0:normalize=0,"
        f"alimiter=limit=0.97,apad,atrim=0:{total:.3f}[aout]"
    )

    # Mix to lossless PCM first; normalize the complete mix in two passes, then encode AAC once.
    audio_tmp = out_path + ".mix.wav"
    normalized_tmp = out_path + ".normalized.wav"
    try:
        _run([
            ff, "-y", "-hide_banner", "-loglevel", "error",
            *inputs,
            "-filter_complex", ";".join(filters),
            "-map", "[aout]",
            "-c:a", "pcm_s24le", "-ar", "44100", "-ac", "2",
            audio_tmp,
        ])
        loudness_report = _normalize_audio_loudness(audio_tmp, normalized_tmp, ff)

        _run([
            ff, "-y", "-hide_banner", "-loglevel", "error",
            "-i", visual_path,
            "-i", normalized_tmp,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            out_path,
        ])
        mux_report = _verify_muxed_audio(out_path, total)
        logger.info(f"final audio contract: loudness={loudness_report}; mux={mux_report}")
        return out_path
    finally:
        for temp_path in (audio_tmp, normalized_tmp):
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError as exc:
                logger.warning(f"无法清理临时音频文件 {temp_path}: {exc}")


# ---------------------------------------------------------------------------
# Overlay asset resolution
# ---------------------------------------------------------------------------

def _resolve_watermark_asset(wm, assets: Dict[str, AssetRef]):
    """Resolve a watermark's project asset ID to its verified on-disk file.

    WatermarkLayer.asset_id is an IR reference, not inherently a filesystem path.
    Preserve backward compatibility for legacy records that already store a path,
    but fail explicitly instead of silently dropping an unresolved image/logo.
    """
    if getattr(wm, "kind", "text") == "text":
        return wm
    asset_id = str(getattr(wm, "asset_id", "") or "").strip()
    if not asset_id:
        raise RenderError(f"覆盖层 {getattr(wm, 'id', '')} 缺少素材引用")
    asset = assets.get(asset_id)
    candidate = Path(asset.file) if asset is not None else Path(asset_id)
    if not candidate.is_file():
        raise RenderError(
            f"覆盖层 {getattr(wm, 'id', '')} 的素材无法解析: {asset_id}"
        )
    return wm.model_copy(update={"asset_id": str(candidate.resolve())})


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def composite_visual_overlays(
    base_path: str,
    ir: CompositionIR,
    assets: Dict[str, AssetRef],
    out_path: str,
) -> str:
    """Composite independent global video/image clips over the base picture.

    Main SceneIR layers remain sequential. These clips live on a separate
    global track, can overlap each other, and are enabled only in their own
    timeline interval. Audio is deliberately excluded; mix_audio runs after
    this stage so the final audio is mixed exactly once.
    """
    track_order = {track.id: int(track.order) for track in getattr(ir, "visual_tracks", []) or []}
    overlays = sorted(
        list(getattr(ir, "visual_overlays", []) or []),
        key=lambda item: (
            track_order.get(getattr(item, "track_id", "overlay-main"), 0),
            int(item.z_index), float(item.start), str(item.id),
        ),
    )
    if not overlays:
        return base_path

    ff = _ffmpeg()
    width, height = int(ir.project.width), int(ir.project.height)
    fps = max(1, int(ir.project.fps))
    total = float(ir.project.duration)
    command = [ff, "-y", "-loglevel", "error", "-i", str(base_path)]
    filters = ["[0:v]settb=AVTB,format=rgba[base0]"]
    current = "base0"

    for index, overlay in enumerate(overlays, start=1):
        asset = assets.get(overlay.asset_id)
        if asset is None:
            raise RenderError(
                f"visual overlay {overlay.id}: asset not found: {overlay.asset_id}"
            )
        if not asset.file or not Path(asset.file).is_file():
            raise RenderError(
                f"visual overlay {overlay.id}: asset file unavailable: {asset.file}"
            )
        media_type = str(asset.type or "").lower()
        if media_type not in {"image", "video"}:
            raise RenderError(
                f"visual overlay {overlay.id}: unsupported asset type {media_type!r}; expected image/video"
            )

        start = float(overlay.start)
        duration = float(overlay.duration)
        source_in = float(overlay.source_in)
        if start < 0 or duration <= 0 or start + duration > total + 0.01:
            raise RenderError(f"visual overlay {overlay.id}: invalid output time range")
        if media_type == "video":
            probe = prober.probe_media(str(asset.file))
            if probe.duration is not None and source_in + duration > float(probe.duration) + 0.05:
                raise RenderError(
                    f"visual overlay {overlay.id}: source window "
                    f"{source_in:.3f}–{source_in + duration:.3f}s exceeds source duration "
                    f"{float(probe.duration):.3f}s"
                )
            command.extend(["-ss", f"{source_in:.6f}", "-t", f"{duration:.6f}", "-i", str(asset.file)])
        else:
            command.extend([
                "-loop", "1", "-framerate", str(fps), "-t", f"{duration:.6f}",
                "-i", str(asset.file),
            ])

        box_w = max(2, int(width * float(overlay.width)) // 2 * 2)
        box_h = max(2, int(height * float(overlay.height)) // 2 * 2)
        x_px = max(0, min(width - box_w, int(round(width * float(overlay.x)))))
        y_px = max(0, min(height - box_h, int(round(height * float(overlay.y)))))
        end = min(total, start + duration)
        overlay_label = f"ov{index}"
        composed_label = f"comp{index}"
        filters.append(
            f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS+{start:.6f}/TB,"
            f"scale={box_w}:{box_h}:force_original_aspect_ratio=decrease,format=rgba,"
            f"pad={box_w}:{box_h}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
            f"format=rgba,colorchannelmixer=aa={float(overlay.opacity):.6f}[{overlay_label}]"
        )
        filters.append(
            f"[{current}][{overlay_label}]overlay=x={x_px}:y={y_px}:"
            f"eof_action=pass:shortest=0:enable='between(t,{start:.6f},{end:.6f})'"
            f"[{composed_label}]"
        )
        current = composed_label

    filters.append(f"[{current}]format=yuv420p[vout]")
    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-an",
        "-c:v", config.DEFAULT_VIDEO_CODEC,
        "-preset", "veryfast", "-crf", str(config.DEFAULT_CRF),
        "-pix_fmt", config.DEFAULT_PIXEL_FORMAT,
        "-t", f"{total:.6f}", "-movflags", "+faststart", str(out_path),
    ])
    _run(command, timeout=900)
    decoded = subprocess.run(
        [ff, "-v", "error", "-i", str(out_path), "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if decoded.returncode != 0:
        raise RenderError(
            f"visual overlay output failed full decode: {(decoded.stderr or '')[-1200:]}"
        )
    return str(out_path)


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
    project_id: Optional[str] = None,
    artifact_kind: str = "final",
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

    # Step 2：计算 artifact dependency graph；只有指纹一致且文件存在时才复用。
    artifact_graph_state = None
    artifact_reuse = {}
    if project_id:
        artifact_graph_state, artifact_reuse = artifact_graph.prepare_render(
            project_id,
            ir,
            assets,
            runtime={
                "width": w,
                "height": h,
                "fps": ir.project.fps,
                "kind": artifact_kind,
                "narration_file": narration_file,
                "subtitle_file": subtitle_file,
                "title_card": title_card,
                "lower_third_name": lower_third_name,
                "lower_third_role": lower_third_role,
                "watermark": watermark,
            },
        )

    scene_paths = {}
    reused_artifacts = []
    rebuilt_artifacts = []

    # 1. 逐场景渲染 → 场景 clip（场景内 layers 拼接），保留转场信息
    seq = 0
    scene_clips: List[str] = []
    scene_transitions: List[str] = []
    for scene in ir.scenes:
        scene_reuse_id = f"scene:{scene.id}:render"
        reused_scene = artifact_reuse.get(scene_reuse_id)
        if reused_scene:
            scene_clip = reused_scene
            scene_clips.append(scene_clip)
            scene_paths[scene_reuse_id] = scene_clip
            reused_artifacts.append(scene_reuse_id)
            logger.info(f"artifact reuse [{scene.id}] -> {scene_clip}")
            tr = getattr(scene.transition_out, "type", "cut") if len(scene_clips) < len(ir.scenes) else "cut"
            if len(ir.scenes) > 1 and len(scene_clips) < len(ir.scenes):
                from video_director import visual_render as _vr
                for link in getattr(ir, "continuity_links", []) or []:
                    if (link.from_scene_id == scene.id
                            and link.to_scene_id == ir.scenes[len(scene_clips)].id
                            and link.type.value == "morph"):
                        tr = "morph"
                        break
            scene_transitions.append(tr)
            continue

        rebuilt_artifacts.append(scene_reuse_id)
        scene_segs: List[str] = []
        for layer in scene.layers:
            asset = assets.get(layer.asset_id or "")
            if asset is None:
                raise RenderError(f"素材不存在: {layer.asset_id}")
            seq += 1
            seg_path = str(seg_dir / f"{seq:04d}.mp4")
            render_layer_segment(layer, asset, seg_path, w, h, ir.project.fps)
            # 层视觉后处理（§15/§63 Frontend Unification）：mask 几何裁剪 + pip 小窗缩放
            from video_director import visual_render as _vr2
            post_filters: List[str] = []
            if getattr(layer, "mask", None) is not None:
                mf = _vr2.mask_crop_filter(layer.mask, w, h)
                if mf:
                    post_filters.append(mf)
            pip_cfg = ((layer.motion_instance or {}).get("parameters") or {}).get("pip")
            if pip_cfg:
                pf = _vr2.pip_filter_expr(pip_cfg, w, h)
                if pf:
                    post_filters.append(pf)
            if post_filters:
                post_path = str(seg_dir / f"{seq:04d}_post.mp4")
                _run([
                    _ffmpeg(), "-y", "-loglevel", "error", "-i", seg_path,
                    "-vf", ",".join(post_filters),
                    "-c:v", config.DEFAULT_VIDEO_CODEC, "-preset", "veryfast",
                    "-crf", str(config.DEFAULT_CRF), "-an", post_path,
                ])
                seg_path = post_path
                logger.info(f"segment {seq:04d} visual post [mask/pip] applied")
            scene_segs.append(seg_path)
            logger.info(f"segment {seq:04d} [{scene.id}/{layer.type.value}] {layer.duration:.2f}s")
        scene_clip = str(seg_dir / f"scene_{scene.id}.mp4")
        concat_segments(scene_segs, scene_clip, w, h, ir.project.fps)
        # 场景级 camera（§14/Frontend Unification）：scene.camera → zoompan 运动
        # （camera_tracks canonical 与 scene.camera 兼容层同源，读 scene.camera）
        if getattr(scene, "camera", None) is not None:
            from video_director import visual_render as vr
            cam_expr = vr.apply_camera_filter(scene.camera, w, h, ir.project.fps, scene.duration)
            if cam_expr:
                cam_out = str(seg_dir / f"scene_{scene.id}_cam.mp4")
                _run([
                    _ffmpeg(), "-y", "-loglevel", "error", "-i", scene_clip,
                    "-vf", cam_expr,
                    "-c:v", config.DEFAULT_VIDEO_CODEC, "-preset", "veryfast",
                    "-crf", str(config.DEFAULT_CRF), "-an", cam_out,
                ])
                scene_clip = cam_out
                logger.info(f"scene {scene.id} camera applied: {scene.camera.id}")
        scene_clips.append(scene_clip)
        scene_paths[scene_reuse_id] = scene_clip
        # 场景转出到下一场景的转场类型（最后一个场景的 transition_out 忽略）
        tr = getattr(scene.transition_out, "type", "cut") if len(scene_clips) < len(ir.scenes) else "cut"
        # morph continuity（§16）：相邻场景间的 morph 链 → 'morph' 过渡
        if len(ir.scenes) > 1 and len(scene_clips) < len(ir.scenes):
            from video_director import visual_render as _vr
            for link in getattr(ir, "continuity_links", []) or []:
                if (link.from_scene_id == scene.id
                        and link.to_scene_id == ir.scenes[len(scene_clips)].id
                        and link.type.value == "morph"):
                    tr = "morph"
                    break
        scene_transitions.append(tr)

    # 0. 片头标题卡（§16：MainTitle 入场动效，前置段不压缩正文）
    if title_card and os.path.isfile(title_card):
        scene_clips.insert(0, title_card)
        scene_transitions.insert(0, "cut")
        logger.info("segment 0000 [title-card] 前置")

    # 2. 场景间转场拼接（cut=concat，crossfade/fade=xfade）
    visual_path = str(work_dir / "visual.mp4")
    if len(scene_clips) == 1:
        # Scene artifact 是可复用缓存，不能 move 走，否则 manifest 记录的缓存路径会失效。
        shutil.copyfile(scene_clips[0], visual_path)
    else:
        splice_with_transitions(
            scene_clips, scene_transitions[: len(scene_clips) - 1],
            visual_path, w, h, ir.project.fps,
        )

    # Parallel global visual track is composited over the main picture, never
    # concatenated as another full-screen scene. It becomes the input to preview,
    # audio mixing, subtitles and final MP4, keeping edit and export semantics aligned.
    if getattr(ir, "visual_overlays", None):
        overlay_path = str(work_dir / "visual_overlays.mp4")
        visual_path = composite_visual_overlays(visual_path, ir, assets, overlay_path)

    # 3. 音频：可选择把视频叠加片段的源音轨并入同一混音链。
    # 使用临时 IR 副本，避免渲染时向用户已保存的 CompositionIR 偷写音轨。
    render_audio_ir = ir
    overlay_audio_tracks = []
    for overlay in getattr(ir, "visual_overlays", []) or []:
        if not getattr(overlay, "source_audio_enabled", False):
            continue
        source_asset = assets.get(overlay.asset_id)
        if source_asset is None or str(source_asset.type).lower() != "video" or not source_asset.file or not os.path.isfile(source_asset.file):
            raise RenderError(f"visual overlay {overlay.id}: 源音轨已启用，但视频素材不可用")
        try:
            source_probe = prober.probe_media(str(source_asset.file))
        except Exception as exc:
            raise RenderError(f"visual overlay {overlay.id}: 无法检查视频源音轨：{exc}") from exc
        if not source_probe.has_audio_stream:
            raise RenderError(f"visual overlay {overlay.id}: 已请求保留源音轨，但该视频没有音频流")
        overlay_audio_tracks.append(AudioTrack(
            type=AudioTrackType.original,
            asset_id=overlay.asset_id,
            start=float(overlay.start),
            duration=float(overlay.duration),
            source_in=float(overlay.source_in),
            source_out=float(overlay.source_in) + float(overlay.duration),
            volume=float(overlay.source_audio_volume),
            muted=False,
        ))
    if overlay_audio_tracks:
        render_audio_ir = ir.model_copy(update={"audio": [*ir.audio, *overlay_audio_tracks]})
    master_path = str(work_dir / "master.mp4")
    audio_result = mix_audio(visual_path, render_audio_ir, assets, narration_file, master_path)
    if audio_result is None:
        master_path = visual_path  # 纯视觉

    # 4. 字幕烧录（必须最后，V1 §20；§32 用 IR subtitle_style 样式）
    final_path = str(work_dir / "final.mp4")
    if subtitle_file and os.path.isfile(subtitle_file):
        subtitle_burn_in(master_path, subtitle_file, final_path,
                         style=getattr(ir, "subtitle_style", None))
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

    # 6. 水印（§19：读 ir.watermarks IR 水印层；旧 watermark 参数向后兼容）
    #    优先级：ir.watermarks > watermark 参数。逐条叠加。
    wm_list = list(ir.watermarks) if getattr(ir, "watermarks", None) else []
    if watermark and watermark.strip() and not wm_list:
        from video_director.models import WatermarkLayer
        wm_list.append(WatermarkLayer(kind="text", text=watermark.strip(),
                                      anchor="bottom_right", opacity=0.75, scale=1.0))
    for i, wm in enumerate(wm_list):
        from video_director import title_engine
        render_wm = _resolve_watermark_asset(wm, assets)
        wm_out = str(work_dir / f"final_watermark_{i}.mp4")
        final_path = title_engine.render_watermark_layer(final_path, render_wm, wm_out)

    artifact_manifest_path = None
    if project_id and artifact_graph_state is not None:
        artifact_manifest_path = artifact_graph.record_render(
            project_id,
            artifact_graph_state,
            scene_paths=scene_paths,
            visual_path=visual_path,
            master_path=master_path,
            final_path=final_path,
        )

    return RenderResult(
        visual_path=visual_path,
        master_path=master_path,
        final_path=final_path,
        segments=scene_clips,
        reused_artifacts=reused_artifacts,
        rebuilt_artifacts=rebuilt_artifacts,
        artifact_manifest_path=artifact_manifest_path,
    )


def subtitle_burn_in(video_in: str, srt_path: str, out_path: str, style=None) -> str:
    """ffmpeg subtitles filter 烧录（Windows 路径转义）。

    正确转义 = 反斜杠换正斜杠 + 冒号转 \\: + 整条路径用单引号包裹：
        subtitles='D\\:/path/to/master.srt'
    （实测：缺单引号会把冒号误解为 original_size option 分隔符）

    style：SubtitleStyle（§32 IR）→ force_style 覆盖字/色/描边/位置。
    """
    esc = srt_path.replace("\\", "/").replace(":", "\\:")
    vf = f"subtitles='{esc}'"
    if style is not None:
        fs_parts = []
        color = getattr(style, "color", None) or "#FFFFFF"
        stroke = getattr(style, "stroke_color", None) or "#000000"
        stroke_w = int(getattr(style, "stroke_width", 2) or 2)
        pos = {"bottom": "2", "center": "5", "top": "8"}.get(
            getattr(style, "position", "bottom"), "2")
        # ASS 对齐：1=左 2=底中 5=中 8=顶中（§32 position 映射）
        fs_parts.append(f"PrimaryColour={_hex_to_ass(color)}")
        fs_parts.append(f"OutlineColour={_hex_to_ass(stroke)}")
        fs_parts.append(f"Outline={stroke_w}")
        fs_parts.append(f"Alignment={pos}")
        fs_parts.append(f"BorderStyle=1")
        if getattr(style, "size", None):
            fs_parts.append(f"FontSize={max(12, int(18 * float(style.size)))}")
        vf = f"subtitles='{esc}':force_style='{','.join(fs_parts)}'"
    cmd = [
        _ffmpeg(), "-y", "-loglevel", "error",
        "-i", video_in,
        "-vf", vf,
        "-c:v", config.DEFAULT_VIDEO_CODEC,
        "-preset", "veryfast",
        "-crf", str(config.DEFAULT_CRF),
        "-c:a", "copy",
        out_path,
    ]
    _run(cmd)
    return out_path


def _hex_to_ass(hex_color: str) -> str:
    """#RRGGBB → ASS &HAABBGGRR（蓝绿红序）。"""
    c = (hex_color or "#FFFFFF").lstrip("#")
    if len(c) != 6:
        c = "FFFFFF"
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H00{b}{g}{r}"
