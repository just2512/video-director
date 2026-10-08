        fp_src += "|ref:" + reference
    if first_frame:
        fp_src += "|first:" + str(first_frame)[:80]
    if last_frame:
        fp_src += "|last:" + str(last_frame)[:80]
    if model:
        fp_src += "|model:" + str(model)
    input_fp = hashlib.sha256(fp_src.encode("utf-8")).hexdigest()[:16]
    results: List[GenerationResult] = []
    manifest = _load_manifest(project_id)

    if gen_type == "image":
        results = _generate_image_or_fallback(
            project_id, prompt, width, height, variants, assets_dir, input_fp, manifest,
            model=model)
    elif gen_type == "video":
        # §29：首末帧先问 Provider Capability Matrix，不再由生成器自己猜协议。
        if first_frame or last_frame:
            try:
                from video_director import provider_matrix
                provider_matrix.require("video", "video.image_to_video",
                                       _role_cfg("video"))
            except provider_matrix.ProviderCapabilityError as exc:
                raise GenerationError(
                    "FIRST_FRAME_UNSUPPORTED" if exc.state.status == provider_matrix.UNSUPPORTED
                    else "VIDEO_PROVIDER_UNAVAILABLE",
                    str(exc),
                ) from exc
        results = _generate_video(
            project_id, prompt, width, height, variants, assets_dir, input_fp, manifest,
            model=model)
    elif gen_type == "motion":