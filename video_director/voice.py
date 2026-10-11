"""MPT 吸收方案 §13-§17 音频域：TTSProvider Registry + VoiceProfile + NarrationConfig。

§13 Audio Architecture：TTS 是 Provider（不是复制 voice.py），多 Provider
按配置诚实枚举；§14 VoiceProfile 含 provider/voiceId/name/language/gender/
style/sampleArtifact；NarrationConfig 含 mode(auto/upload/silent)+
voice/rate/volume/pause/pronunciation；§15 试听是独立 Artifact 分级
（VoicePreview / NarrationPreview / Narration）。

Provider 诚实降级（§12）：
    edge-tts     免费零 key，默认可用（现有 narration.py 直接用）
    azure/gemini/elevenlabs 等     需 key，缺 key 不枚举、不伪造

不 copy MPT 的 voice.py 单体；只做 Registry + 契约。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

#: §14 VoiceProfile：provider 明确记录（统一 AudioTrack/VoiceArtifact 溯源）
#: §35 Voice Registry 指纹：supports_preview / supports_full_preview（能力声明，
#: UI 据此显示「试听/完整试听」，不先允许点击再报错）。
@dataclass(frozen=True)
class VoiceProfile:
    provider: str
    voice_id: str
    name: str
    language: str = "zh-CN"
    gender: str = "unknown"
    style: str = ""
    sampleArtifact: Optional[str] = None  # §15 试听后回填 preview 产物 id
    supports_preview: bool = True         # §35 单句试听（edge-tts 全支持）
    supports_full_preview: bool = False   # §35 完整旁白试听（生成整段旁白）

    def to_dict(self) -> dict:
        return {
            "provider": self.provider, "voiceId": self.voice_id, "name": self.name,
            "language": self.language, "gender": self.gender, "style": self.style,
            "sampleArtifact": self.sampleArtifact,
            "supports_preview": self.supports_preview,
            "supports_full_preview": self.supports_full_preview,
        }


#: §14 NarrationConfig：三态 mode + voice 参数
@dataclass(frozen=True)
class NarrationConfig:
    mode: str = "auto"           # auto | upload | silent（§16/§17/§12）
    voice: str = "zh-CN-XiaoxiaoNeural"
    provider: str = "edge-tts"
    rate: str = "+0%"
    volume: float = 1.0
    pause: float = 0.0           # 段间停顿（秒）
    pronunciation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "voice": self.voice, "provider": self.provider,
            "rate": self.rate, "volume": self.volume, "pause": self.pause,
            "pronunciation": self.pronunciation,
        }


#: §13 TTSProvider Registry：{provider: {label, requires_key, available}}
#: edge-tts 免费零 key；其余需 key（缺 key → supported=False 不枚举）。
TTS_PROVIDERS = {
    "edge-tts": {"label": "Edge TTS（免费，无需 key）", "requires_key": False},
    "azure": {"label": "Azure Speech", "requires_key": True},
    "gemini": {"label": "Google Gemini TTS", "requires_key": True},
    "elevenlabs": {"label": "ElevenLabs", "requires_key": True},
    "siliconflow": {"label": "SiliconFlow TTS", "requires_key": True},
    "kokoro": {"label": "Kokoro（本地）", "requires_key": False},
}

#: 现有 VOICES 白名单（edge-tts provider，从 webui.server.VOICES 语义迁移而来）。
#: gender 按中文名推断（晓=女，云=男，晓臻/曉雨=女 等），诚实标注 unknown 兜底。
_EDGE_VOICES: List[VoiceProfile] = [
    VoiceProfile("edge-tts", "zh-CN-XiaoxiaoNeural", "晓晓", gender="female", style="温柔"),
    VoiceProfile("edge-tts", "zh-CN-XiaoyiNeural", "晓伊", gender="female", style="活泼"),
    VoiceProfile("edge-tts", "zh-CN-YunjianNeural", "云健", gender="male", style="沉稳"),
    VoiceProfile("edge-tts", "zh-CN-YunxiNeural", "云希", gender="male", style="阳光"),
    VoiceProfile("edge-tts", "zh-CN-YunxiaNeural", "云夏", gender="male", style="清亮"),
    VoiceProfile("edge-tts", "zh-CN-YunyangNeural", "云扬", gender="male", style="新闻"),
    VoiceProfile("edge-tts", "zh-TW-HsiaoChenNeural", "曉臻", language="zh-TW", gender="female"),
    VoiceProfile("edge-tts", "zh-TW-YunJheNeural", "雲哲", language="zh-TW", gender="male"),
    VoiceProfile("edge-tts", "zh-TW-HsiaoYuNeural", "曉雨", language="zh-TW", gender="female"),
    VoiceProfile("edge-tts", "en-US-EmmaNeural", "Emma", language="en-US", gender="female"),
    VoiceProfile("edge-tts", "en-US-AndrewNeural", "Andrew", language="en-US", gender="male"),
    VoiceProfile("edge-tts", "en-US-BrianNeural", "Brian", language="en-US", gender="male"),
    VoiceProfile("edge-tts", "en-US-AvaMultilingualNeural", "Ava", language="en-US", gender="female"),
    VoiceProfile("edge-tts", "en-US-BrianMultilingualNeural", "Brian", language="en-US", gender="male"),
    VoiceProfile("edge-tts", "en-US-AndrewMultilingualNeural", "Andrew", language="en-US", gender="male"),
]


def list_providers() -> List[dict]:
    """§13：TTSProvider Registry + Provider Capability Matrix 状态。"""
    from video_director import provider_matrix
    out = []
    for pid, meta in TTS_PROVIDERS.items():
        st = provider_matrix.state_for_provider(
            pid, "tts", "tts.speak",
            {"api_key": "configured"} if pid == "edge-tts" else {},
        )
        out.append({
            "id": pid, "label": meta["label"],
            "requires_key": meta["requires_key"],
            "available": st.status == provider_matrix.READY,
            "status": st.status,
            "reason": st.reason,
        })
    return out


def list_voices(provider: str = "edge-tts") -> List[dict]:
    """§14：VoiceProfile 清单。非 edge-tts provider 需 key，缺 key 返回空（诚实）。"""
    import os
    if provider != "edge-tts":
        # 需 key 的 provider：缺 key 不枚举（不伪造可用音色）
        if not os.environ.get(f"{provider.upper()}_API_KEY"):
            return []
        return []  # 非 edge-tts 的具体 voice 目录尚未接入（诚实，不编造）
    return [v.to_dict() for v in _EDGE_VOICES]


def get_voice(voice_id: str) -> Optional[dict]:
    """§14：按 voiceId 取 VoiceProfile（试听/生成用）。"""
    for v in _EDGE_VOICES:
        if v.voice_id == voice_id:
            return v.to_dict()
    return None


def make_narration_config(mode: str = "auto", voice: str = "zh-CN-XiaoxiaoNeural",
                          **kwargs) -> NarrationConfig:
    """§14：构造 NarrationConfig（mode auto/upload/silent；silent 禁 fake voice）。"""
    if mode not in ("auto", "upload", "silent"):
        raise ValueError(f"mode {mode!r} 非法（auto|upload|silent）")
    cfg = NarrationConfig(mode=mode, voice=voice, **kwargs)
    return cfg


def is_silent_legal(cfg: NarrationConfig) -> dict:
    """§17：silent 模式合法性——No TTS No fake voice，但仍允许 subtitle/BGM/SFX/visual。"""
    return {
        "mode": cfg.mode,
        "tts_skipped": cfg.mode == "silent",
        "subtitle_allowed": True,
        "bgm_allowed": True,
        "sfx_allowed": True,
        "visual_allowed": True,
    }