

def list_providers() -> List[dict]:
    """§13：TTSProvider Registry + Provider Capability Matrix 状态。"""
    from video_director import provider_matrix
    out = []
    for pid, meta in TTS_PROVIDERS.items():
        st = provider_matrix.state_for_provider(
            pid, "tts", "tts.speak",
            {"api_key": "configured"} if pid == "edge-tts" else {}
        )
        status = st.status
        reason = st.reason
        out.append({
            "id": pid, "label": meta["label"],
            "requires_key": meta["requires_key"],
            "available": status == provider_matrix.READY,
            "status": status,
            "reason": reason,
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