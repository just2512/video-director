    if tool == "system.capabilities.describe":
        return _result(tool, True, "OK",
                       result={"capability": describe_capability(payload.get("id", ""))})
    if tool == "system.context.inspect":
        return _result(tool, True, "OK",
                       result={"context": project_context.inspect_project_context(
                           project_id, payload.get("selection"))})
    if tool == "timeline.query":
        return _result(tool, True, "OK",
                       result={"timeline": _timeline_summary(project_id)})
    if tool == "visual.semantic_rerank":
        from video_director import visual_qa
        candidates = payload.get("candidates") or []
        out, meta = visual_qa.semantic_rerank(
            payload.get("sceneText", ""),
            candidates,
            limit=int(payload.get("limit") or 5),
        )
        return _result(tool, True, meta.get("status", "UNKNOWN"),
                       result={"scene_id": payload.get("sceneId"),
                               "candidates": out,
                               "provider": meta.get("provider"),
                               "reason": meta.get("reason", "")})
    if tool == "visual.qa":
        from video_director import visual_qa
        try:
            evidence = visual_qa.pegasus_visual_qa(
                payload.get("sceneId", ""),
                payload.get("sceneText", ""),
                payload.get("assetId", ""),
                payload.get("assetFile", ""),
            )
        except visual_qa.VisualAnalysisError as exc:
            # Provider 未配置/当前能力不可用时，UNKNOWN 是合法业务状态，
            # 不把“无法验证”伪装成 QA 通过，也不阻塞普通剪辑链。
            if exc.code in {"PROVIDER_MISSING", "PROVIDER_UNKNOWN"}:
                return _result(
                    tool, True, "UNKNOWN",
                    result={
                        "scene_id": payload.get("sceneId"),
                        "asset_id": payload.get("assetId"),
                        "verdict": "UNKNOWN",
                        "provider": "twelvelabs-pegasus",
                        "evidence": "",
                        "reason": str(exc),
                    },
                    verify={"verdict": "UNKNOWN", "evidence_present": False},
                )
            return _result(tool, False, exc.code, error=str(exc))
        evidence_dict = evidence.to_dict()
        try:
            visual_qa.save_scene_evidence(project_id, evidence)
        except Exception as persist_exc:
            logger.warning(f"visual QA 证据落盘失败（不阻断 QA 结果）: {persist_exc}")
            evidence_dict["persist_status"] = "UNKNOWN"
        return _result(tool, True, evidence.verdict,
                       result=evidence_dict,