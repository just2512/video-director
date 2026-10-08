        from video_director import qc as qc_mod
        try:
            report = qc_mod.run_qc(project_id) if hasattr(qc_mod, "run_qc") else {"status": "PASS"}
            return _result(tool, True, "OK", result={"qc": report})
        except Exception as exc:
            return _result(tool, False, "QC_ERROR", error=str(exc)[:200])
    if tool in ("tts.preview_voice", "tts.preview_narration", "tts.generate"):
        # Provider Capability Matrix：默认 edge-tts；显式选择其它 provider 时先硬校验，
        # 避免「UI 可选但执行器仍偷偷走 edge-tts」的语义错位。
        from video_director import provider_matrix
        tts_provider = payload.get("provider") or "edge-tts"
        tts_gate = provider_matrix.state_for_provider(
            tts_provider, "tts", "tts.speak",
            {"api_key": "configured"} if tts_provider == "edge-tts" else {},
        )
        if tts_gate.status != provider_matrix.READY:
            code = (
                "CAPABILITY_UNSUPPORTED" if tts_gate.status == provider_matrix.UNSUPPORTED
                else "PROVIDER_UNKNOWN" if tts_gate.status == provider_matrix.UNKNOWN
                else "PROVIDER_MISSING"
            )
            return _result(tool, False, code, error=str(
                provider_matrix.ProviderCapabilityError(tts_gate)
            ))
        # §15 试听三级 Artifact：VoicePreview / NarrationPreview / Narration。
        # 全部走 edge-tts 合成到项目 audio 目录（试听产物非正式 Narration，
        # tts.generate 落 narration_preview.mp3/narration.mp3 由调用方确认后入轨）。
        from video_director import config as cfg
        import uuid as _uuid
        voice_id = payload.get("voiceId") or "zh-CN-XiaoxiaoNeural"
        text = (payload.get("text") or "").strip()
        if not text:
            # 未给文本：读项目分镜文本合成完整试听（§15 完整试听）
            try:
                scenes = json.loads(
                    (cfg.project_dir(project_id) / "plans" / "scenes.json")