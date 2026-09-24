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


# ---------------------------------------------------------------------------
# 卡拉OK字幕（方案 §15 Subtitle Engine：Karaoke 逐词高亮，ASS \kf 实现）
# ---------------------------------------------------------------------------
def _ass_time(seconds: float) -> str:
    """秒 → ASS 时间戳 H:MM:SS.cc（百分秒）。"""
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


#: 卡拉OK默认样式（ASS Style 行参数；与普通字幕的烧录位置保持一致：底部安全区）
ASS_KARAOKE_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Microsoft YaHei,{font_size},&H00FFFFFF,&H0000C6FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,2.4,0,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass_karaoke(
    scenes: List[dict],
    word_timeline: List[dict],
    width: int = 1080,
    height: int = 1920,
    font_size: int = 64,
) -> str:
    """词级时间轴 → ASS 卡拉OK字幕（每句一条 Dialogue，\\kf 逐词高亮）。

    scenes: [{start, duration, text}]（narration_scenes，与主时间轴同基准）
    word_timeline: [{text, start, end}]（全局秒，edge-tts 词级时间戳）。
    词时间戳按场景区间归组；某词缺失时间则按句内均分兜底。

    \\kf 语义：SecondaryColour（橙）随朗读进度填充为 PrimaryColour（白），
    libass/ffmpeg subtitles 滤镜原生支持。
    """
    header = ASS_KARAOKE_HEADER.format(
        width=width, height=height, font_size=font_size,
        margin_v=max(40, int(height * 0.08)),
    )
    events: List[str] = []
    for s in scenes:
        start, end = float(s["start"]), float(s["start"]) + float(s["duration"])
        text = str(s.get("text", "")).strip()
        if not text:
            continue
        # 收集本句的词级时间（词的 start 落在 [start, end) 区间）
        words = [w for w in word_timeline
                 if start <= float(w.get("start", -1)) < end]
        if words:
            # \kf 串：每词 {\kf<dur>}词 （dur 单位百分秒）
            segs = []
            for j, w in enumerate(words):
                w_start = float(w["start"])
                w_end = float(w["end"])
                # 首词与句起点对齐（词间 gap 归前词，避免高亮空转）
                if j == 0:
                    w_start = min(w_start, start)
                    prev_end = w_end
                else:
                    w_start = words[j - 1]["end"]
                    w_end = max(w_end, w_start + 0.05)
                dur_cs = max(1, int(round((w_end - w_start) * 100)))
                segs.append(r"{\kf%d}%s" % (dur_cs, w.get("text", "")))
            karaoke_text = "".join(segs)
        else:
            # 无词级时间（兜底）：整句均分 \kf
            n = max(1, len(text))
            per_cs = max(1, int(round((end - start) * 100 / n)))
            karaoke_text = "".join(
                r"{\kf%d}%s" % (per_cs, ch) for ch in text
            )
        events.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Karaoke,,0,0,0,,"
            f"{karaoke_text}"
        )
    return header + "\n".join(events) + "\n"