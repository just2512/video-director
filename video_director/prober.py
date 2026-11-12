"""媒体探测引擎（规格 §25：第一版只做 ffprobe，不做 AI 看视频）。

双引擎设计：
1. ffprobe.exe 优先（JSON 输出，精确）
2. ffmpeg -i stderr 解析兜底（Portable 包只带 ffmpeg.exe 不带 ffprobe.exe 时仍可用）

所有调用方只面对 probe_media() 一个入口，不感知底层引擎。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from loguru import logger


@dataclass
class ProbeResult:
    duration: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    has_video_stream: bool = False
    has_audio_stream: bool = False
    codec: Optional[str] = None  # video codec
    audio_codec: Optional[str] = None
    audio_sample_rate: Optional[int] = None
    audio_channels: Optional[int] = None
    engine: str = ""  # "ffprobe" | "ffmpeg-stderr"


class ProbeError(Exception):
    pass


def _bundled_bin_roots() -> List[str]:
    """随包 bin/ 候选目录（打包分发口径 pack_release.py）。

    - 打包安装：<app_root>/bin（release/video_director/prober.py → release/bin）
    - 开发检出：<app_root>/release/bin（release 目录已解包但源码在根）
    都不存在时为空列表（诚实降级，不编造路径）。
    """
    root = Path(__file__).resolve().parent.parent
    cands = [root / "bin", root / "release" / "bin"]
    return [str(c) for c in cands if c.is_dir()]


def _bundled_exe(name: str) -> Optional[str]:
    """随包 bin/ 下的可执行文件（win 补 .exe），不存在返回 None。"""
    exe_name = f"{name}.exe" if os.name == "nt" else name
    for root in _bundled_bin_roots():
        cand = Path(root) / exe_name
        if cand.is_file():
            return str(cand)
    return None


def _resolve_ffprobe() -> Optional[str]:
    """ffprobe 优先级：FFPROBE_EXE 环境变量 > 随包 bin/ > PATH > 同目录 ffmpeg 推测。"""
    env = os.environ.get("FFPROBE_EXE")
    if env and os.path.isfile(env):
        return env
    bundled = _bundled_exe("ffprobe")
    if bundled:
        return bundled
    which = shutil.which("ffprobe")
    if which:
        return which
    # ffmpeg 同目录通常有 ffprobe
    ffmpeg = shutil.which("ffmpeg") or os.environ.get("IMAGEIO_FFMPEG_EXE") or _bundled_exe("ffmpeg")
    if ffmpeg and os.path.isfile(ffmpeg):
        sibling = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
        if os.path.isfile(sibling):
            return sibling
    return None


def _resolve_ffmpeg() -> str:
    """ffmpeg 分辨率：显式环境变量 > 随包 bin/ > PATH > imageio_ffmpeg > 兜底。

    随包 bin/ 优先级高于 PATH：分发包自带经测试的 ffmpeg 是"准事实源"，
    用户机器上的随机 old/broken ffmpeg 不得遮蔽它；显式 IMAGEIO_FFMPEG_EXE
    仍最高（用户明确覆盖）。
    """
    env = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if env and os.path.isfile(env):
        return env
    bundled = _bundled_exe("ffmpeg")
    if bundled:
        return bundled
    which = shutil.which("ffmpeg")
    if which:
        return which
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe:
            return exe
    except Exception:
        pass
    return "ffmpeg"


def _probe_with_ffprobe(ffprobe: str, media: str) -> ProbeResult:
    cmd = [
        ffprobe,
        "-v", "error",
        "-show_entries",
        "format=duration:stream=index,codec_name,codec_type,width,height,avg_frame_rate",
        "-of", "json",
        media,
    ]
    completed = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
    )
    if completed.returncode != 0:
        raise ProbeError(f"ffprobe failed: {completed.stderr.strip()[:300]}")
    data = json.loads(completed.stdout or "{}")
    result = ProbeResult(engine="ffprobe")
    for stream in data.get("streams", []):
        ctype = stream.get("codec_type")
        if ctype == "video" and not result.has_video_stream:
            result.has_video_stream = True
            result.width = stream.get("width")
            result.height = stream.get("height")
            result.codec = stream.get("codec_name")
            rate = stream.get("avg_frame_rate", "0/0")
            if rate and "/" in rate:
                num, den = rate.split("/", 1)
                try:
                    if float(den) > 0:
                        result.fps = round(float(num) / float(den), 3)
                except ValueError:
                    pass
        elif ctype == "audio":
            result.has_audio_stream = True
            result.audio_codec = stream.get("codec_name")
            raw_sample_rate = stream.get("sample_rate")
            try:
                result.audio_sample_rate = int(raw_sample_rate) if raw_sample_rate else None
            except (TypeError, ValueError):
                result.audio_sample_rate = None
            try:
                result.audio_channels = int(stream.get("channels")) if stream.get("channels") else None
            except (TypeError, ValueError):
                result.audio_channels = None
    duration = (data.get("format") or {}).get("duration")
    if duration is not None:
        result.duration = round(float(duration), 3)
    return result


def _probe_with_ffmpeg_stderr(ffmpeg: str, media: str) -> ProbeResult:
    """ffmpeg -i 的 stderr 包含 Duration/Stream #0:0 行，可反向解析。"""
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", media],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    # -i 无输出文件时 exit code 必非 0，但要读 stderr
    err = completed.stderr or ""
    if "No such file" in err or "cannot open" in err.lower():
        raise ProbeError(f"media not found: {media}")
    result = ProbeResult(engine="ffmpeg-stderr")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", err)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        result.duration = round(h * 3600 + mi * 60 + s, 3)
    vm = re.search(
        r"Stream #0:\d+.*?:\s*Video:\s*(\S+).*?,\s*(\d+)x(\d+)", err
    )
    if vm:
        result.has_video_stream = True
        result.codec = vm.group(1)
        result.width, result.height = int(vm.group(2)), int(vm.group(3))
        fm = re.search(r"(\d+(?:\.\d+)?)\s*fps", err)
        if fm:
            result.fps = float(fm.group(1))
    audio_match = re.search(
        r"Stream #0:\d+(?:\\([^)]*\\))?:\\s*Audio:\\s*([^\\s,]+)(?:\\s+\\([^)]*\\))?,\\s*(\\d+)\\s*Hz,\\s*([^,\\r\\n]+)",
        err,
    )
    if audio_match:
        result.has_audio_stream = True
        result.audio_codec = audio_match.group(1)
        result.audio_sample_rate = int(audio_match.group(2))
        layout = audio_match.group(3).strip().lower()
        channel_layout = layout.split("(", 1)[0].strip()
        known_channels = {
            "mono": 1, "stereo": 2, "2.1": 3, "3.0": 3, "3.0(back)": 3,
            "4.0": 4, "quad": 4, "5.0": 5, "5.1": 6, "5.1(side)": 6,
            "5.1(back)": 6, "6.1": 7, "7.1": 8, "7.1(wide)": 8,
            "7.1(wide-side)": 8, "octagonal": 8,
        }
        channels_match = re.search(r"(\\d+)\\s+channels?", channel_layout)
        if channels_match:
            result.audio_channels = int(channels_match.group(1))
        else:
            result.audio_channels = known_channels.get(channel_layout)
    return result


def probe_media(media: str) -> ProbeResult:
    """探测媒体文件。视频必须带视频流；图片只解析分辨率。失败抛 ProbeError。

    跨运行缓存（SVF v1.2.2 经验）：同文件（path+mtime+size 未变）直接返回缓存，
    免 ffmpeg 子进程开销。
    """
    from video_director import store

    store.init_db()  # fresh storage 下 probe_cache 表可能未建（幂等）
    cached = store.get_cached_probe(media)
    if cached is not None and cached.get("duration") is not None:
        return ProbeResult(
            duration=cached["duration"], width=cached["width"],
            height=cached["height"], fps=cached["fps"],
            has_video_stream=cached["has_video"],
            has_audio_stream=cached["has_audio"],
            engine="cache",
        )

    if not os.path.isfile(media):
        raise ProbeError(f"media not found: {media}")

    ffprobe = _resolve_ffprobe()
    result: Optional[ProbeResult] = None
    if ffprobe:
        try:
            r = _probe_with_ffprobe(ffprobe, media)
            if r.has_video_stream or r.duration is not None:
                result = r
        except (ProbeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            logger.warning(f"ffprobe failed for {media}: {exc}; fallback to ffmpeg-stderr")
    if result is None:
        result = _probe_with_ffmpeg_stderr(_resolve_ffmpeg(), media)
        if not result.has_video_stream and result.duration is None:
            raise ProbeError(f"无法从媒体解析任何信息: {media}")
    store.save_probe(media, result)
    return result


def probe_image(media: str) -> ProbeResult:
    """探测图片：只需要分辨率。ffmpeg -i 对图片 duration 通常为 N/A。"""
    result = probe_media(media)
    if not result.width or not result.height:
        raise ProbeError(f"图片无分辨率信息: {media}")
    return result


def get_audio_duration(audio_file: str) -> float:
    """探测音频时长（旁白主时间轴的长度来源）。"""
    result = probe_media(audio_file)
    if result.duration is None:
        raise ProbeError(f"audio has no duration: {audio_file}")
    return result.duration
