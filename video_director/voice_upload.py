"""MPT 吸收方案 §16 上传配音 + §12 已有输入则跳过生成。

§16 管线：用户拖入 voice.wav → Import → Probe → ASR → Word Timing →
Narration Track。**不强行经过 TTS**（§12：上传配音时 TTS = SKIP，
Timeline 仍然存在，BGM/SFX 照常工作）。

复用现有能力：
    - prober.probe_media（时长/流探测）
    - alignment.FasterWhisperProvider（ASR → 场景对齐；模型不可用时
      用时长均分估算 word timing，诚实标注 source）
    - narration.estimate_word_timeline（估算兜底）

诚实边界：ASR 模型未下载 → word timing 用估算（source="estimated"），
明确告知调用方；绝不伪造 ASR 精度。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from loguru import logger

from video_director import config, prober


class VoiceUploadError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


def import_uploaded_voice(project_id: str, audio_path: str,
                         scene_texts: Optional[List[str]] = None) -> dict:
    """§16：上传配音进入项目（Import → Probe → ASR/估算 → Narration）。

    返回 {narration_file, duration, narration_scenes, word_timeline, source}。
    - scene_texts 提供时按文本对齐 ASR；否则按静音切段估算
    - ASR 不可用 → 时长均分估算（source="estimated"，诚实标注）
    """
    p = Path(audio_path)
    if not p.is_file():
        raise VoiceUploadError("FILE_NOT_FOUND", f"配音文件不存在: {audio_path}")

    # Probe（Import 后第一步：真实流校验）
    probe = prober.probe_media(str(p))
    if not probe.has_audio_stream:
        raise VoiceUploadError("NO_AUDIO_STREAM", f"文件不含音频流: {p.name}")
    duration = probe.duration or 0.0
    if duration <= 0:
        raise VoiceUploadError("INVALID_DURATION", f"配音时长无效: {duration}")

    # 落到项目 audio 目录（项目内引用统一走项目目录）
    base = config.project_dir(project_id)
    audio_dir = base / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    dst = audio_dir / ("narration_uploaded" + p.suffix.lower())
    if p != dst:
        import shutil
        shutil.copyfile(str(p), str(dst))

    # ASR → word timing（§16）：FasterWhisper 可用才用；不可用/失败诚实估算
    source = "estimated"
    word_timeline: List[dict] = []
    narration_scenes: List[dict] = []
    asr_available = False
    if scene_texts:
        try:
            import faster_whisper  # noqa: F401
            asr_available = True
        except ImportError:
            asr_available = False
    if scene_texts and asr_available:
        try:
            from video_director.alignment import FasterWhisperProvider, AlignmentError
            provider = FasterWhisperProvider()
            # align 返回全局词级时间轴；场景化走 build_aligned_narration_scenes
            words = provider.align(str(dst), scene_texts)
            from video_director.alignment import build_aligned_narration_scenes
            narration_scenes = build_aligned_narration_scenes(scene_texts, words)
            word_timeline = words
            source = "asr"
        except Exception as exc:
            logger.info(f"voice_upload: ASR 对齐失败，用时长估算（诚实降级）: {exc}")
            narration_scenes = _estimate_scenes(scene_texts, duration)
            word_timeline = _estimate_words(narration_scenes)
    elif scene_texts:
        logger.info("voice_upload: faster-whisper 未安装，用时long估算（source=estimated）")
        narration_scenes = _estimate_scenes(scene_texts, duration)
        word_timeline = _estimate_words(narration_scenes)
    else:
        # 无文本：单场景整段
        narration_scenes = [{"id": "S01", "text": "", "start": 0.0,
                             "duration": round(duration, 3)}]
        word_timeline = []

    logger.info(f"voice_upload: {p.name} → {dst.name} ({duration:.1}s, source={source})")
    return {
        "narration_file": str(dst),
        "duration": round(duration, 3),
        "narration_scenes": narration_scenes,
        "word_timeline": word_timeline,
        "source": source,           # asr | estimated（诚实）
        "tts_skipped": True,          # §12：上传配音时 TTS = SKIP
    }


def _estimate_scenes(scene_texts: List[str], total: float) -> List[dict]:
    """按字数比例把总时长均分到各场景（无 ASR 时的诚实估算）。"""
    weights = [max(len(t), 1) for t in scene_texts] or [1]
    total_w = sum(weights)
    scenes = []
    cursor = 0.0
    for i, (text, w) in enumerate(zip(scene_texts, weights)):
        dur = total * w / total_w
        scenes.append({"id": f"S{i+1:02d}", "text": text,
                       "start": round(cursor, 3), "duration": round(dur, 3)})
        cursor += dur
    return scenes


def _estimate_words(narration_scenes: List[dict]) -> List[dict]:
    """场景展开词级时间轴（复用 narration.estimate_word_timeline 逻辑）。"""
    from video_director.narration import estimate_word_timeline
    return estimate_word_timeline(
        [s["text"] for s in narration_scenes],
        [s["duration"] for s in narration_scenes],
    )


def silent_mode_legal() -> dict:
    """§17：无配音（mode=silent）合法工作流——No TTS No fake voice。"""
    return {
        "mode": "silent",
        "tts_skipped": True,
        "subtitle_allowed": True,   # 字幕可手工提供
        "bgm_allowed": True,
        "sfx_allowed": True,
        "visual_allowed": True,
        "legal_workflows": ["Image Story", "Music Video",
                            "Silent Product Demo", "Visual Quote"],
    }