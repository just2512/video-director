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
from video_director import mpt_semantics


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
        # 403 = 微软封禁旧端点（2026-10，上游 #490）；本包 __init__ 的
        # 端点补丁正常时不应出现。带真实原因上抛，别让 Director 只见
        # "narration 节点失败"却查不到为什么。
        hint = ""
        msg = str(exc)
        if "403" in msg:
            hint = ("（微软 TTS 接口 403 拒绝：端点补丁未生效或上游再变更，"
                    "见 video_director/__init__.py _patch_edge_tts_endpoint）")
        raise NarrationError(f"edge-tts 合成失败: {exc}{hint}") from exc


def _synth_scene_with_pauses(
    text: str,
    voice: str,
    scene_index: int,
    work_dir: Path,
    rate: str,
) -> Tuple[str, List[dict], float, List[dict]]:
    """MPT-compatible scene narration: TTS speech segments + exact silent gaps."""
    from video_director import renderer

    segments, pause_events = mpt_semantics.parse_pause_markup(text)
    if not segments:
        clean = mpt_semantics.remove_pause_markup(text)
        if not clean:
            return "", [], 0.0, []
        scene_mp3 = str(work_dir / f"scene-{scene_index:02d}.mp3")
        words = synth_scene_audio(clean, voice, scene_mp3, rate)
        dur = prober.get_audio_duration(scene_mp3)
        return scene_mp3, words, dur, []

    audio_parts: List[str] = []
    words_global: List[dict] = []
    cursor = 0.0
    pause_meta: List[dict] = []

    for part_index, segment in enumerate(segments, 1):
        if segment.text:
            speech_file = work_dir / f"scene-{scene_index:02d}-speech-{part_index:02d}.mp3"
            words = synth_scene_audio(segment.text, voice, str(speech_file), rate)
            speech_duration = prober.get_audio_duration(str(speech_file))
            audio_parts.append(str(speech_file))
            for w in words:
                words_global.append({
                    "text": w["text"],
                    "start": round(cursor + w["start"], 3),
                    "end": round(cursor + w["end"], 3),
                })
            cursor += speech_duration

        if segment.pause_after > 0:
            pause_file = work_dir / f"scene-{scene_index:02d}-pause-{part_index:02d}.mp3"
            renderer._run([
                renderer._ffmpeg(), "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                "-t", f"{segment.pause_after:.3f}",
                "-c:a", "libmp3lame", "-b:a", "128k", str(pause_file),
            ])
            actual_pause = prober.get_audio_duration(str(pause_file))
            audio_parts.append(str(pause_file))
            pause_meta.append({
                "after_text": segment.text,
                "requested": round(segment.pause_after, 3),
                "actual": round(actual_pause, 3),
            })
            cursor += actual_pause

    scene_mp3 = str(work_dir / f"scene-{scene_index:02d}.mp3")
    renderer._run([
        renderer._ffmpeg(), "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", _write_concat_list(audio_parts, work_dir),
        "-c:a", "libmp3lame", "-b:a", "128k",
        scene_mp3,
    ])
    actual_duration = prober.get_audio_duration(scene_mp3)
    return scene_mp3, words_global, actual_duration, pause_meta


def synth_narration(
    scene_texts: List[str],
    voice: str,
    work_dir: Path,
    rate: str = "+0%",
) -> Tuple[str, List[dict], List[dict]]:
    """逐场景合成旁白；MPT pause markup 会真实改变音频和 Narration Timeline。"""
    from video_director import renderer

    if not voice or voice == "silent":
        return _silent_narration(scene_texts, work_dir)

    scene_audios = []
    word_timeline = []
    narration_scenes = []
    pause_timeline = []
    cursor = 0.0

    for i, raw_text in enumerate(scene_texts):
        clean_text = mpt_semantics.remove_pause_markup(raw_text)
        scene_mp3, words_local, dur, pauses = _synth_scene_with_pauses(
            raw_text, voice, i + 1, work_dir, rate
        )

        for w in words_local:
            word_timeline.append({
                "text": w["text"],
                "start": round(cursor + w["start"], 3),
                "end": round(cursor + w["end"], 3),
            })

        narration_scenes.append({
            "id": f"S{i+1:02d}",
            "text": clean_text,
            "start": round(cursor, 3),
            "duration": round(dur, 3),
            "pause_events": pauses,
        })
        for p in pauses:
            pause_timeline.append({
                "scene_id": f"S{i+1:02d}",
                **p,
            })

        scene_audios.append(scene_mp3)
        cursor += dur
        logger.info(
            f"narration scene {i+1:02d}: {dur:.2f}s, "
            f"{len(words_local)} words, pauses={len(pauses)} <- {clean_text[:16]}"
        )

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
    """无配音模式：同样消费 MPT pause markup，保证静音时间轴与有声模式一致。"""
    from video_director import renderer

    narration_scenes = []
    cursor = 0.0
    for i, raw_text in enumerate(scene_texts):
        segments, _ = mpt_semantics.parse_pause_markup(raw_text)
        clean_text = mpt_semantics.remove_pause_markup(raw_text)
        speech_duration = max(2.0, len(clean_text) / 4.0)
        pause_duration = sum(
            max(0.0, float(seg.pause_after or 0.0))
            for seg in segments
        )
        dur = speech_duration + pause_duration
        narration_scenes.append({
            "id": f"S{i+1:02d}",
            "text": clean_text,
            "start": round(cursor, 3),
            "duration": round(dur, 3),
            "pause_events": [
                {"requested": round(seg.pause_after, 3)}
                for seg in segments if seg.pause_after > 0
            ],
        })
        cursor += dur

    total = max(cursor, 1.0)
    audio_out = str(work_dir / "narration.mp3")
    renderer._run([
        renderer._ffmpeg(), "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", f"{total:.3f}", "-c:a", "libmp3lame", "-b:a", "128k", audio_out,
    ])
    logger.info(
        f"narration silent mode: {len(scene_texts)} 场景 {total:.1f}s 静音轨（保留 pause 时间语义）"
    )
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