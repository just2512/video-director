        elif route == "/api/capabilities":
            try:
                caps = vd_caps.registry()
                data = []
                for name, cap in caps.items():
                    data.append({
                        "name": name,
                        "status": cap.status,
                        "providers": [
                            {"name": p.name, "available": p.available,
                             "requires_api_key": p.requires_api_key}
                            for p in (cap.providers or [])
                        ],
                        "supports": list(cap.supports or []),
                        "fallback_tools": list(cap.fallback_tools or []),
                    })
                # §12/§31.1 Generation Availability：AI 生图/生视频是否可用
                # 前端据此 disable 按钮 + 显示原因（不先允许点击再 503）
                try:
                    from video_director import model_config as _mc
                    from video_director import provider_matrix as _pm
                    data.append({
                        "name": "provider.matrix",
                        "status": "AVAILABLE",
                        "matrix": _pm.matrix_for_frontend(),
                        "providers": [],
                        "supports": [],
                        "fallback_tools": [],
                    })
                    data.append({
                        "name": "generation.availability",
                        "status": "AVAILABLE",
                        "generation": {
                            "image": {
                                "available": _mc.is_role_enabled("image"),
                                "reason": "" if _mc.is_role_enabled("image")
                                else "未配置图片生成服务（设置 → 模型里填写生图服务）",
                            },
                            "video": {
                                "available": _mc.is_role_enabled("video"),
                                "reason": "" if _mc.is_role_enabled("video")
                                else "未配置视频生成服务（设置 → 模型里填写生视频服务）",
                            },
                            # §十八 艺术动画：Huashu Procedural Motion 本地确定性
                            # 渲染，无外部 provider；grammar 清单随可用性下发，
                            # 前端不用再发第二个请求（零新端点）。
                            "motion": {
                                "available": True,
                                "reason": "",
                                "grammars": _motion_grammars(),
                            },
                        },
                        "providers": [],
                        "supports": [],
                        "fallback_tools": [],
                    })
                except Exception:
                    logger.debug("generation availability 读取失败（忽略）")
                self._send_json(data)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
        elif route == "/api/continuity":
            # §13 ContinuityVerifier（PPT Detector）：审阅页连续性检查
            try:
                from video_director import continuity as vd_continuity
                from video_director import editor as vd_editor_mod
                qs = parse_qs(parsed.query)
                pid = (qs.get("project_id") or [""])[0]
                if not pid:
                    self._send_json({"error": "缺少 project_id"}, 400)