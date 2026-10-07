"""Narration（旁白合成，V1 §9 + V2 §28）。

策略：TTS 原生时间戳优先（edge-tts WordBoundary，零模型下载零 key），
逐场景合成（避免跨场景词匹配脆弱），词级 offset 累加场景偏移还原全局时间轴。

输出统一：
- audio 文件（完整旁白）
- word_timeline [{text,start,end}] 全局秒
- narration_scenes [{id,text,start,duration}]（Narration Timeline = 唯一主时间轴）
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import List, Optional, Tuple

from loguru import logger

from video_director import prober


class NarrationError(Exception):
    pass


def normalize_rate(rate) -> str:
    """voice_rate 归一 → edge-tts 相对百分比（'+0%'/'-10%'/'30%' 形态）。

    兼容三种来源（2026-10-07 教训：Director 路径直传倍数 '0.9' 导致
    edge-tts `Invalid rate 0.9`）：
    - 倍数字符串/数字：'1.3'/0.9 → 相对百分比
    - 合法百分比：'+15%'/'-10%' → 透传
    - 空/非法值 → '+0%'
    """
    if rate is None:
        return "+0%"
    s = str(rate).strip()
    if not s:
        return "+0%"
    if s.endswith("%"):
        body = s[:-1]
        try:
            v = int(round(float(body)))
        except (TypeError, ValueError):
            return "+0%"
        return f"{v:+d}%"
    try:
        mult = float(s)
    except (TypeError, ValueError):
        return "+0%"
    return f"{int(round((mult - 1.0) * 100)):+d}%"


def _word_boundaries(text: str) -> List[Tuple[str, float]]:
    """粗分中文词边界（无 ASR 时的估算兜底）。

    返回 [(word, weight)]，weight 为相对时长占比（按字符数）。
    """
    import re
    # 按标点/空格切，保留每段字符数作为时长权重
    units = re.findall(r"[^，。！？；：、,.!?;:\s]+", text)
    words = []
    for u in units:
        # 进一步按 2-3 字一组切（中文词粒度近似）
        segs = [u[i:i+3] for i in range(0, len(u), 3)] or [u]
        for s in segs:
            words.append((s, len(s)))
    return words if words else [(text, len(text) or 1)]


def synth_scene_audio(
    text: str,
    voice: str,
    out_mp3: str,
    rate: str = "+0%",
) -> List[dict]:
    """单场景 edge-tts 合成，返回该场景内词级时间戳 [{text,start,end}]（场景内相对秒）。

    WordBoundary chunk 的 offset 单位为 100 纳秒（1e7/s）。
    """
    import edge_tts

    async def _run():
        communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
        words = []
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # offset/duration 单位为 100ns
                start = chunk["offset"] / 1e7
                dur = chunk["duration"] / 1e7
                words.append({
                    "text": chunk["text"],
                    "start": round(start, 3),
                    "end": round(start + dur, 3),
                })
            elif chunk["type"] == "error":
                raise NarrationError(f"edge-tts error: {chunk}")
        if not chunks:
            raise NarrationError("edge-tts 未返回任何音频数据")
        with open(out_mp3, "wb") as f:
            for c in chunks:
                f.write(c)
        return words

    try:
        return asyncio.run(_run())
    except NarrationError:
        raise
    except Exception as exc:
        raise NarrationError(f"edge-tts 合成失败: {exc}") from exc


def synth_narration(
    scene_texts: List[str],
    voice: str,
    work_dir: Path,
    rate: str = "+0%",
) -> Tuple[str, List[dict], List[dict]]:
    """逐场景合成旁白，拼接为单一音频，返回 (audio_path, word_timeline, narration_scenes)。

    - word_timeline: 全局秒 [{text,start,end}]
    - narration_scenes: [{id,text,start,duration}] 无缝衔接
    - §34/§17 无配音模式（voice 为空/silent）：不合成假旁白——
      每场景按估算阅读时长生成静音轨（scene timing），word_timeline 为空
      （字幕按场景时间轴而非词级）；返回路径指向静音文件（下游混音兼容）。
    """
    from video_director import renderer

    # §34 silent：Narration Track absent 语义（禁 fake voice）
    if not voice or voice == "silent":
        return _silent_narration(scene_texts, work_dir)

    scene_audios = []
    word_timeline = []
    narration_scenes = []
    cursor = 0.0

    for i, text in enumerate(scene_texts):
        scene_mp3 = str(work_dir / f"scene-{i+1:02d}.mp3")
        words_local = synth_scene_audio(text, voice, scene_mp3, rate)
        dur = prober.get_audio_duration(scene_mp3)

        # 全局偏移累加
        for w in words_local:
            word_timeline.append({
                "text": w["text"],
                "start": round(cursor + w["start"], 3),
                "end": round(cursor + w["end"], 3),
            })
        narration_scenes.append({
            "id": f"S{i+1:02d}",
            "text": text,
            "start": round(cursor, 3),
            "duration": round(dur, 3),
        })
        scene_audios.append(scene_mp3)
        cursor += dur
        logger.info(f"narration scene {i+1:02d}: {dur:.2f}s, {len(words_local)} words <- {text[:16]}")

    # 拼接为单一音频（concat demuxer + 重编码，保证无损衔接）
    audio_out = str(work_dir / "narration.mp3")
    renderer._run([
        renderer._ffmpeg(), "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", _write_concat_list(scene_audios, work_dir),
        "-c:a", "libmp3lame", "-b:a", "128k",
        audio_out,
    ])
    return audio_out, word_timeline, narration_scenes


def _silent_narration(scene_texts: List[str], work_dir: Path) -> Tuple[str, List[dict], List[dict]]:
    """§34 无配音模式：静音旁白轨（scene timing 估时；无词级时间轴，字幕按场景）。

    场景时长估算：中文 ~4 字/秒（与 estimate 语义一致）；最少 2s 保底。
    """
    from video_director import renderer
    narration_scenes = []
    cursor = 0.0
    for i, text in enumerate(scene_texts):
        dur = max(2.0, len(text or "") / 4.0)
        narration_scenes.append({"id": f"S{i+1:02d}", "text": text,
                                  "start": round(cursor, 3), "duration": round(dur, 3)})
        cursor += dur
    total = max(cursor, 1.0)
    audio_out = str(work_dir / "narration.mp3")
    renderer._run([
        renderer._ffmpeg(), "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
        "-t", f"{total:.3f}", "-c:a", "libmp3lame", "-b:a", "128k", audio_out,
    ])
    logger.info(f"narration silent mode: {len(scene_texts)} 场景 {total:.1f}s 静音轨（无假旁白）")
    return audio_out, [], narration_scenes


def _write_concat_list(audio_paths: List[str], work_dir: Path) -> str:
    list_file = str(work_dir / "narration.concat.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in audio_paths:
            f.write(f"file '{Path(p).as_posix()}'\n")
    return list_file


def estimate_word_timeline(scene_texts: List[str], scene_durations: List[float]) -> List[dict]:
    """Gate 1 无 TTS 时的词级估算（供字幕/对齐兜底），按场景字数比例分布。"""
    words = []
    cursor = 0.0
    for text, dur in zip(scene_texts, scene_durations):
        units = _word_boundaries(text)
        total_weight = sum(w for _, w in units) or 1
        t = 0.0
        for word, weight in units:
            wdur = dur * weight / total_weight
            words.append({
                "text": word,
                "start": round(cursor + t, 3),
                "end": round(cursor + t + wdur, 3),
            })
            t += wdur
        cursor += dur
    return words