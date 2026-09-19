"""字幕生成（V1 §23 + V2 §29）。

字幕时间基于最终输出 Timeline（与 Narration Timeline 同一时间基准，V1 §23）。
输入 WordTimeline → 输出 master.srt（句子级字幕，第一版）。
"""

from __future__ import annotations

import re
from typing import List, Optional


def time_to_srt(seconds: float) -> str:
    """秒 → SRT 时间戳 HH:MM:SS,mmm。"""
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt_from_words(
    word_timeline: List[dict],
    scene_texts: List[str],
    scene_durations: List[float],
) -> str:
    """词级时间轴 + 场景 → 句子级 SRT。

    第一版按场景整句成一条字幕（场景起止即字幕起止，与主时间轴同基准）。
    后续 word_by_word 模式由 MPT 字幕引擎扩展。
    """
    lines: List[str] = []
    idx = 1
    cursor = 0.0
    for i, (text, durl) in enumerate(zip(scene_texts, scene_durations)):
        start, end = cursor, cursor + durl
        # 字幕内换行优化：过长句子按逗号断行
        display = text
        if len(text) > 20:
            parts = re.split(r"(?<=，|,|；|;)", text)
            display = "\n".join(p.strip() for p in parts if p.strip())
        lines.append(f"{idx}")
        lines.append(f"{time_to_srt(start)} --> {time_to_srt(end)}")
        lines.append(display)
        lines.append("")
        idx += 1
        cursor += durl
    return "\n".join(lines)


def build_srt_from_scenes(scenes: List[dict]) -> str:
    """直接由 narration/scene 结构生成 SRT（scenes 带 start/duration/text）。"""
    lines: List[str] = []
    for i, s in enumerate(scenes):
        start, end = s["start"], s["start"] + s["duration"]
        display = s["text"]
        if len(display) > 20:
            parts = re.split(r"(?<=，|,|；|;)", display)
            display = "\n".join(p.strip() for p in parts if p.strip())
        lines.append(f"{i + 1}")
        lines.append(f"{time_to_srt(start)} --> {time_to_srt(end)}")
        lines.append(display)
        lines.append("")
    return "\n".join(lines)


def srt_entries(scenes: List[dict]) -> List[dict]:
    """字幕条目（供 validator 边界检查），[{start,end,text}]。"""
    return [
        {"start": s["start"], "end": s["start"] + s["duration"], "text": s["text"]}
        for s in scenes
    ]