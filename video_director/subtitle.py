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
# 分句（对照 MPT subtitle.py：按标点断句，句级字幕而非整段）
# ---------------------------------------------------------------------------
_SENTENCE_SPLIT = re.compile(r"([。！？!?；;，,、])")

def split_sentences(text: str) -> List[str]:
    """按中文/英文标点把一段旁白切成句子（保留句末标点）。空段过滤。

    超长句（无标点一口气 > 18 字）再按 14 字窗口硬切，避免撑爆画面。
    """
    raw = text.strip()
    if not raw:
        return []
    parts = _SENTENCE_SPLIT.split(raw)
    # parts 形如 [..., "标点", ...] 交替，重组为带标点的句子
    sentences: List[str] = []
    buf = ""
    for p in parts:
        buf += p
        if p and p in "。！？!?；;，,、":
            s = buf.strip()
            if s:
                sentences.append(s)
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())
    # 超长句硬切（>18 字且无更多标点，强制换行成多条）
    out: List[str] = []
    for s in sentences:
        if len(s) > 18:
            # 逐 14 字切窗口（中文按字计）
            for i in range(0, len(s), 14):
                chunk = s[i : i + 14].strip()
                if chunk:
                    out.append(chunk)
        else:
            out.append(s)
    return out

#: 普通字幕 ASS 样式（白字 + 黑描边 + 底部安全区，字号按画幅，对照 MPT 生产级）
ASS_PLAIN_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Microsoft YaHei,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,2.5,1,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

def _scene_sentence_times(scene: dict, sentences: List[str]) -> List[tuple]:
    """句级时间：按句子字符占比在场景 [start,end] 内线性分配。

    Args:
        scene: {start, duration, text}
        sentences: 该场景分句结果
    Returns:
        [(start, end), ...] 每条句子的时间戳
    """
    start, dur = float(scene["start"]), float(scene["duration"])
    text_len = max(1, len(str(scene.get("text", ""))))
    times = []
    cursor = start
    for s in sentences:
        # 每句时长 = 场景时长 × (句长 / 总长)，最后一句吃掉剩余
        seg = dur * (len(s) / text_len)
        times.append((round(cursor, 3), round(cursor + seg, 3)))
        cursor += seg
    if times:
        times[-1] = (times[-1][0], round(start + dur, 3))
    return times

def build_ass_plain(
    scenes: List[dict],
    width: int = 1080,
    height: int = 1920,
    font_size: int = 64,
) -> str:
    """普通字幕（句级 + 样式）ASS：按标点分句，白字黑描边，底部安全区。

    对照 MPT 字幕：一句一显示（非整段），超长句已硬切，字号按画幅传入。
    """
    header = ASS_PLAIN_HEADER.format(
        width=width, height=height, font_size=font_size,
        margin_v=max(40, int(height * 0.08)),
    )
    events: List[str] = []
    for sc in scenes:
        text = str(sc.get("text", "")).strip()
        if not text:
            continue
        sentences = split_sentences(text)
        times = _scene_sentence_times(sc, sentences)
        for (s_start, s_end), sent in zip(times, sentences):
            # ASS 文本内换行：>16 字手动折行（\N），视觉更协调
            disp = sent
            if len(disp) > 16:
                mid = len(disp) // 2
                # 找中点最近的标点/空格作自然断行，无则硬切
                disp = disp[:mid] + "\\N" + disp[mid:]
            events.append(
                f"Dialogue: 0,{_ass_time(s_start)},{_ass_time(s_end)},Sub,,0,0,0,,{disp}"
            )
    return header + "\n".join(events) + "\n"
def _ass_time(seconds: float) -> str:
    """秒 → ASS 时间戳 H:MM:SS.cc（百分秒）。"""
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


#: 双语字幕样式（中文主行 + 英文次行：英文约 70% 字号、更贴底、浅灰字带描边）
ASS_BILINGUAL_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Microsoft YaHei,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,2.5,1,2,60,60,{margin_v},1
Style: SubEn,Microsoft YaHei,{en_font_size},&H00E0E0E0,&H00E0E0E0,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,1.8,1,2,60,60,{en_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass_bilingual(
    scenes: List[dict],
    translations: dict,
    width: int = 1080,
    height: int = 1920,
    font_size: int = 64,
) -> str:
    """双语字幕 ASS：中文主行（句级，同 plain 的分句与断行规则）+ 英文次行。

    translations: {scene_id 或 索引(字符串/整数): 英文文本}；缺翻译的场景只出中文行。
    英文行按场景整段显示（场景起止时间），字号约主行 70%、位置在主行下方
    （更贴底的 MarginV）、浅灰字带描边，与主行视觉分层。
    """
    en_font_size = max(12, int(round(font_size * 0.7)))
    margin_v = max(40, int(height * 0.08))
    # 主行高约 font_size×1.35（含描边行距），英文行贴其下方，最低不下探出画
    en_margin_v = max(12, margin_v - int(font_size * 1.35))
    header = ASS_BILINGUAL_HEADER.format(
        width=width, height=height, font_size=font_size, margin_v=margin_v,
        en_font_size=en_font_size, en_margin_v=en_margin_v,
    )
    events: List[str] = []
    for i, sc in enumerate(scenes):
        text = str(sc.get("text", "")).strip()
        if not text:
            continue
        en = ""
        for key in (sc.get("id"), str(i), i):
            if key is not None and key in translations:
                en = str(translations[key]).strip()
                break
        # 译文来自 LLM 输出（边界输入）：换行折叠，剥掉 {}\\ 防 ASS 覆写标签注入
        en = re.sub(r"[\r\n]+", " ", en)
        en = re.sub(r"[{}\\]", "", en)
        if en:
            start, end = float(sc["start"]), float(sc["start"]) + float(sc["duration"])
            events.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},SubEn,,0,0,0,,{en}"
            )
        sentences = split_sentences(text)
        times = _scene_sentence_times(sc, sentences)
        for (s_start, s_end), sent in zip(times, sentences):
            # ASS 文本内换行：>16 字手动折行（\N），与 plain 同规则
            disp = sent
            if len(disp) > 16:
                mid = len(disp) // 2
                disp = disp[:mid] + "\\N" + disp[mid:]
            events.append(
                f"Dialogue: 0,{_ass_time(s_start)},{_ass_time(s_end)},Sub,,0,0,0,,{disp}"
            )
    return header + "\n".join(events) + "\n"


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
            # \kf 串：每词 {\kf<dur>}词 （dur 单位百分秒）；长句 >16 字在中点断行
            segs = []
            char_cnt = 0
            _mid = len(text) // 2
            _wrapped = False
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
                char_cnt += len(w.get("text", ""))
                if not _wrapped and char_cnt >= _mid and len(text) > 16:
                    segs.append(r"\N")
                    _wrapped = True
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