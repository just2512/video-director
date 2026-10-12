"""QC（V1 §32 Level 1 + V2 §44-§46：Deterministic QC + Audio QC）。

Deterministic QC：ffprobe 检查最终产物（时长/分辨率/fps/流/codec）。
Audio QC：电平/混音完整性（第一版检查轨存在与响度上限）。
Visual QC（AI 看片）第二阶段接入，本模块不越界。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from loguru import logger

from video_director import config, prober
from video_director.models import AssetRef, CompositionIR, LayerType


class QCError(Exception):
    pass


@dataclass
class QCReport:
    verdict: str  # PASS / REJECT
    checks: List[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict == "PASS"

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "checks": self.checks}

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


def _check(report: QCReport, name: str, ok: bool, detail: str):
    report.checks.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})
    if not ok:
        report.verdict = "REJECT"
    logger.debug(f"QC {name}: {'PASS' if ok else 'FAIL'} — {detail}")


def run_qc(
    ir: CompositionIR,
    final_path: str,
    assets: Dict[str, AssetRef],
    narration_duration: float,
    expect_subtitle: bool = True,
    expect_audio: bool = True,
) -> QCReport:
    """对最终产物跑确定性 QC 全清单（V2 §45）。"""
    report = QCReport(verdict="PASS")

    # 1. 文件存在
    _check(report, "final_exists", os.path.isfile(final_path),
           final_path if os.path.isfile(final_path) else "final.mp4 不存在")

    if not os.path.isfile(final_path):
        return report

    probe = prober.probe_media(final_path)

    # 2. 视频流
    _check(report, "video_stream", probe.has_video_stream,
           f"codec={probe.codec}, {probe.width}x{probe.height}@{probe.fps}")

    # 3. 分辨率
    _check(report, "resolution",
           probe.width == ir.project.width and probe.height == ir.project.height,
           f"期望 {ir.project.width}x{ir.project.height}，实际 {probe.width}x{probe.height}")

    # 4. 时长 = 旁白主时间轴（渲染层公差，见 config.RENDER_DURATION_TOLERANCE 注释）
    if probe.duration is not None:
        _check(report, "duration_vs_narration",
               abs(probe.duration - narration_duration) <= config.RENDER_DURATION_TOLERANCE,
               f"final {probe.duration:.3f}s vs 旁白 {narration_duration:.3f}s "
               f"(容差 {config.RENDER_DURATION_TOLERANCE:.2f}s)")

    # 5. fps（25/30 都接受，concat 后容器时间基可能微小漂移）
    _check(report, "fps", probe.fps is not None and probe.fps >= 24,
           f"fps={probe.fps}")

    # 6. 音轨（预期有旁白时）
    if expect_audio:
        _check(report, "audio_stream", probe.has_audio_stream,
               "存在音轨" if probe.has_audio_stream else "缺少音轨（narration 预期存在）")

    # 7. 黑帧抽检。只允许 CompositionIR 中明确留下的空隙出现黑画面；
    #    若此时有不透明全画幅片段覆盖，仍须检验最终帧，防止渲染漏片被 gap 掩盖。
    blackframe_ok, black_detail = _detect_black_frames(final_path, ir=ir)
    _check(report, "no_black_frames", blackframe_ok, black_detail)

    return report


def _is_expected_empty_gap(ir: Optional[CompositionIR], at: float) -> bool:
    """True only when the timeline explicitly contains an uncovered gap at this global time.

    An opaque full-frame visual clip makes that point testable again: if its rendered
    image is black/missing, QC must still catch it rather than masking the failure.
    """
    if ir is None:
        return False
    in_gap = any(
        layer.type == LayerType.gap
        and scene.start + layer.start - 0.001 <= at < scene.start + layer.start + layer.duration - 0.001
        for scene in ir.scenes
        for layer in scene.layers
    )
    if not in_gap:
        return False
    for clip in ir.visual_overlays:
        if (
            clip.presentation_mode == "full_frame"
            and clip.opacity >= 0.98
            and clip.x <= 0.001 and clip.y <= 0.001
            and clip.width >= 0.999 and clip.height >= 0.999
            and clip.start <= at < clip.start + clip.duration
        ):
            return False
    return True


def _detect_black_frames(
    video_path: str,
    samples: int = 5,
    ir: Optional[CompositionIR] = None,
) -> tuple:
    """确定性黑帧检测：均匀采样 N 帧，Python 端计算灰度均值。

    均值 < 8（0-255 域）判黑帧。避开首尾 5% 容易出 fade 边缘的区域。
    """
    import subprocess

    probe = prober.probe_media(video_path)
    dur = probe.duration or 0
    if dur <= 0:
        return False, "无法探测时长"
    w, h = 32, 32  # 缩到 32x32 灰度足以判断黑帧
    bad, checked, expected_gaps = 0, 0, 0
    for i in range(samples):
        # 采样点落在 5%~95% 区间
        t = dur * 0.05 + (dur * 0.9) * (i + 0.5) / samples
        if _is_expected_empty_gap(ir, t):
            expected_gaps += 1
            continue
        cmd = [
            prober._resolve_ffmpeg(), "-ss", f"{t:.2f}", "-i", video_path,
            "-frames:v", "1",
            "-vf", f"scale={w}:{h},format=gray",
            "-f", "rawvideo", "-",
        ]
        try:
            completed = subprocess.run(
                cmd, capture_output=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            continue
        data = completed.stdout
        if len(data) < w * h:
            continue  # 帧读取失败跳过该点
        mean = sum(data) / len(data)
        checked += 1
        if mean < 8:
            bad += 1
    if checked == 0:
        if expected_gaps:
            return False, f"{expected_gaps} 个采样点均位于 IR 声明的 gap；没有非 gap 样本，不能证明画面可用"
        return False, "无有效采样帧"
    if bad:
        suffix = f"；另有 {expected_gaps} 个抽样点落在 IR 声明的空隙内" if expected_gaps else ""
        return False, f"{bad}/{checked} 个非 gap 采样点为黑帧(灰度均值<8){suffix}"
    suffix = f"；跳过 {expected_gaps} 个 IR 明确声明的空隙点" if expected_gaps else ""
    return True, f"{checked} 个非 gap 采样点无意外黑帧{suffix}"
