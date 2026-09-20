"""vd doctor（环境自检，借 jianying-headless doctor 思想）。

启动/生成前回答"为什么今天突然不能渲染"。三态输出：
    PASS  可用
    SKIP  可选能力未配置（不阻塞）
    FAIL  必需能力缺失（阻塞，生成前就该失败而不是渲染一半报错）

检查清单（按依赖必需性）：
    必需：FFmpeg / EdgeTTS 网络可达 / 磁盘空间
    可选：ffprobe（有 stderr 兜底）/ VisionProvider（视觉 LLM 未配置→SKIP）
          / faster-whisper（对齐 fallback）/ 音频库
"""

from __future__ import annotations

import shutil
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import List

from loguru import logger

from video_director import audio as audio_mod, config, prober

#: 可用磁盘下限（GB）——渲染 60s 1080p 成片约需 0.5GB，留 10 倍余量
MIN_FREE_GB = 5.0

#: EdgeTTS 端点连通性（synthesis endpoint 域名）
EDGE_TTS_HOST = "speech.platform.bing.com"
EDGE_TTS_PORT = 443


@dataclass
class CheckResult:
    name: str
    status: str  # PASS | SKIP | FAIL
    detail: str


def _resolve_ffmpeg_executable() -> str | None:
    """返回可执行 ffmpeg 的真实路径（含 PATH 兜底验证），找不到返回 None。"""
    exe = prober._resolve_ffmpeg()
    if exe and (Path(exe).is_file() or shutil.which(exe)):
        return exe
    return shutil.which("ffmpeg")


def _check_ffmpeg() -> CheckResult:
    exe = _resolve_ffmpeg_executable()
    if exe is None:
        return CheckResult("FFmpeg", "FAIL", "未找到可执行文件")
    import subprocess
    try:
        r = subprocess.run([exe, "-version"], capture_output=True, timeout=10)
        if r.returncode != 0:
            return CheckResult("FFmpeg", "FAIL", f"exit={r.returncode}")
        ver = (r.stdout or b"").decode(errors="replace").splitlines()[0][:60]
        return CheckResult("FFmpeg", "PASS", ver)
    except Exception as exc:
        return CheckResult("FFmpeg", "FAIL", str(exc)[:80])


def _check_ffprobe() -> CheckResult:
    exe = prober._resolve_ffprobe()
    if exe:
        return CheckResult("ffprobe", "PASS", Path(exe).name)
    return CheckResult("ffprobe", "SKIP", "未找到，将用 ffmpeg -i stderr 解析兜底")


def _check_edge_tts() -> CheckResult:
    try:
        with socket.create_connection((EDGE_TTS_HOST, EDGE_TTS_PORT), timeout=5):
            return CheckResult("TTS: EdgeTTS", "PASS", f"{EDGE_TTS_HOST} 可达")
    except Exception as exc:
        return CheckResult("TTS: EdgeTTS", "FAIL", f"网络不可达: {str(exc)[:60]}")


def _check_vision() -> CheckResult:
    import os
    if os.environ.get("COMPOSER_VISION_API_KEY"):
        return CheckResult("VisionProvider", "PASS",
                           os.environ.get("COMPOSER_VISION_MODEL", "gpt-4o"))
    return CheckResult("VisionProvider", "SKIP",
                       "未配置（可设 COMPOSER_VISION_API_KEY 激活；无它则依赖手工标注做内容匹配）")


def _check_whisper() -> CheckResult:
    try:
        import faster_whisper  # noqa: F401
        return CheckResult("Alignment: faster-whisper", "PASS", "已安装（fallback 可用）")
    except ImportError:
        return CheckResult("Alignment: faster-whisper", "SKIP",
                           "未安装（EdgeTTS 词级时间戳是主路径，不影响）")


def _check_audio_library() -> CheckResult:
    lib = audio_mod.scan_library()
    bgm = [a for a in lib.values() if a.type == "bgm"]
    sfx = [a for a in lib.values() if a.type == "sfx"]
    if bgm or sfx:
        return CheckResult("音频库", "PASS", f"BGM×{len(bgm)} SFX×{len(sfx)}（含许可台账）")
    return CheckResult("音频库", "SKIP", "空库（BGM/SFX 不可用，成片仅旁白+字幕）")


def _check_disk() -> CheckResult:
    try:
        usage = shutil.disk_usage(str(config.composer_storage_root()))
        free_gb = usage.free / 1024 ** 3
        ok = free_gb >= MIN_FREE_GB
        return CheckResult("磁盘空间", "PASS" if ok else "FAIL",
                           f"剩余 {free_gb:.1f}GB（下限 {MIN_FREE_GB}GB）")
    except Exception as exc:
        return CheckResult("磁盘空间", "FAIL", str(exc)[:60])


def run_doctor() -> List[CheckResult]:
    """跑全部检查，返回结果列表。含 FAIL 项时调用方应停止生成（capability gate）。"""
    results = [
        _check_ffmpeg(),
        _check_ffprobe(),
        _check_edge_tts(),
        _check_vision(),
        _check_whisper(),
        _check_audio_library(),
        _check_disk(),
    ]
    for r in results:
        logger.info(f"doctor [{r.status}] {r.name}: {r.detail}")
    return results


def format_doctor_report(results: List[CheckResult]) -> str:
    lines = ["AI Video Director 环境自检", "=" * 44]
    for r in results:
        lines.append(f"[{r.status:^4}] {r.name}: {r.detail}")
    fails = [r for r in results if r.status == "FAIL"]
    lines.append("=" * 44)
    if fails:
        lines.append(f"[X] {len(fails)} 项必需能力缺失，生成将被阻塞：")
        lines.extend(f"  - {r.name}: {r.detail}" for r in fails)
    else:
        lines.append("[OK] 必需能力全部就绪（SKIP 为可选能力，不阻塞）")
    return "\n".join(lines)


def has_blocking_failures(results: List[CheckResult]) -> bool:
    return any(r.status == "FAIL" for r in results)
