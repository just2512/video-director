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

    # ---- original（§18：素材原声轨，从 source video 抽取音轨）----
    # original 轨 asset_id 指向一个视频素材；渲染时 ffmpeg 抽其音轨按音量混入，
    # 有旁白时 duck（§18 OriginalAudioPolicy.duckAgainst=narration）。
    original_track: Optional[AudioTrack] = None
    for t in ir.audio:
        if t.type == AudioTrackType.original and assets.get(t.asset_id or ""):
            original_track = t
            break
    if original_track is not None and getattr(original_track, "muted", False):
        original_track = None  # §37 Audio Bus mute：静音轨不进混音
    if original_track is not None:
        # 只有 video 素材才有可抽的原声；image/audio 源不匹配 original 语义
        src = assets[original_track.asset_id]
        if src.type == "video" and os.path.isfile(src.file):
            duck_db = config.DUCKING_DB if (narration_file and original_track.ducking) else 0.0
            vol_db = 20 * math.log10(max(original_track.volume, 0.01)) + duck_db
            ss = original_track.source_in or 0.0
            dur = original_track.source_out or original_track.duration
            inputs += ["-ss", f"{ss:.3f}", "-t", f"{dur:.3f}", "-i", src.file]
            # §18 OriginalAudioPolicy fadeIn/fadeOut
            chain_o = (f"[{idx}:a]aresample=44100,aformat=channel_layouts=mono,"
                       f"volume={vol_db:.2f}dB")
            fin_o = float(getattr(original_track, "fadeIn", 0.0) or 0.0)
            fout_o = float(getattr(original_track, "fadeOut", 0.0) or 0.0)
            if fin_o > 0:
                chain_o += f",afade=t=in:st=0:d={min(fin_o, total):.2f}"
            if fout_o > 0:
                chain_o += f",afade=t=out:st={max(0.0, total - fout_o):.2f}:d={min(fout_o, total):.2f}"
            chain_o += f",atrim=0:{total:.3f}[orig]"
            filters.append(chain_o)
            ref_labels.append("[orig]")
            idx += 1

    # ---- BGM（首个有效 bgm track；循环铺满总长 + duck + 用户音量）----
    bgm_track: Optional[AudioTrack] = None
    for t in ir.audio:
        if t.type == AudioTrackType.bgm and assets.get(t.asset_id or ""):
            bgm_track = t
            break
    if bgm_track is not None and getattr(bgm_track, "muted", False):
        bgm_track = None  # §37 Audio Bus mute
    if bgm_track is not None:
        duck_db = config.DUCKING_DB if narration_file else 0.0
        total_db = duck_db + 20 * math.log10(max(bgm_track.volume, 0.01))
        inputs += ["-stream_loop", "-1", "-i", assets[bgm_track.asset_id].file]
        # §35 BGM fade：fadeIn/fadeOut（秒）非零时接 afade（先淡入再淡出）
        chain = (f"[{idx}:a]aresample=44100,aformat=channel_layouts=mono,"
                 f"volume={total_db:.2f}dB")
        fin = float(getattr(bgm_track, "fadeIn", 0.0) or 0.0)
        fout = float(getattr(bgm_track, "fadeOut", 0.0) or 0.0)
        if fin > 0:
            chain += f",afade=t=in:st=0:d={min(fin, total):.2f}"
        if fout > 0:
            chain += f",afade=t=out:st={max(0.0, total - fout):.2f}:d={min(fout, total):.2f}"
        chain += f",atrim=0:{total:.3f}[bgm]"
        filters.append(chain)
        ref_labels.append("[bgm]")
        idx += 1

    # ---- SFX（语义时间点插入）----
    sfx_labels: List[str] = []
    for t in ir.audio:
        if t.type != AudioTrackType.sfx:
            continue
        if getattr(t, "muted", False):
            continue  # §37 Audio Bus mute
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
        wm_out = str(work_dir / f"final_watermark_{i}.mp4")
        final_path = title_engine.render_watermark_layer(final_path, wm, wm_out)

    return RenderResult(
        visual_path=visual_path,
        master_path=master_path,
        final_path=final_path,
        segments=scene_clips,
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
