"""Alignment Engine（V1 §10 + V2 §29）。

AlignmentProvider 统一接口：TTS 原生时间戳 → faster-whisper → WhisperX 逐级 fallback。
业务模块不得直接依赖 WhisperX/faster-whisper，统一经此接口。

第一版实现：
- TTSTimestampProvider：直接用 narration.synth_narration 产出的词级时间戳（主路径）
- FasterWhisperProvider：旁白音频转写（edge-tts 失败或不提供词边界时的 fallback，惰性加载模型）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Tuple

from loguru import logger

from video_director import narration as narration_mod, prober


class AlignmentError(Exception):
    pass


class AlignmentProvider(ABC):
    """词级对齐统一接口（V2 §29）。"""

    @abstractmethod
    def align(
        self,
        audio_file: str,
        script_scenes: List[str],
    ) -> List[dict]:
        """返回全局词级时间轴 [{text,start,end}]。失败抛 AlignmentError。"""


class TTSTimestampProvider(AlignmentProvider):
    """TTS 原生时间戳（主路径，零额外模型）。

    旁白本来就由逐场景 edge-tts 产出词边界，alignment 直接复用，
    不重新走 ASR。此 provider 的 align 需要旁白已合成的词表，
    因此实际由 synth_narration 一并完成，这里提供一致性接口（幂等）。
    """

    def __init__(self, word_timeline: List[dict]):
        self._words = word_timeline

    def align(self, audio_file: str, script_scenes: List[str]) -> List[dict]:
        return self._words


class FasterWhisperProvider(AlignmentProvider):
    """faster-whisper 词级转写（fallback，惰性加载，HF_HOME 已重定向 D 盘）。"""

    def __init__(self, model_size: str = "small", device: str = "cpu", compute_type: str = "int8"):
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            logger.info(f"loading faster-whisper model={self._model_size} "
                        f"device={self._device} compute={self._compute_type}")
            self._model = WhisperModel(
                self._model_size, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def align(self, audio_file: str, script_scenes: List[str]) -> List[dict]:
        try:
            model = self._load()
        except Exception as exc:
            raise AlignmentError(f"faster-whisper 加载失败: {exc}") from exc
        segments, _info = model.transcribe(audio_file, word_timestamps=True, vad_filter=True)
        words = []
        for seg in segments:
            for w in seg.words:
                words.append({
                    "text": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                })
        if not words:
            raise AlignmentError("faster-whisper 未产出词级时间戳")
        return words


def build_aligned_narration_scenes(
    scene_texts: List[str],
    word_timeline: List[dict],
) -> List[dict]:
    """从词级时间轴恢复场景起止（复用 validator.build_narration_alignment 的匹配逻辑）。

    返回 [{id,text,start,duration}]，无缝衔接，匹配失败抛 AlignmentError。
    """
    try:
        from video_director.validator import build_narration_alignment
        return build_narration_alignment(scene_texts, word_timeline)
    except ValueError as exc:
        raise AlignmentError(f"场景在词时间轴中匹配失败: {exc}") from exc