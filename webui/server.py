        if parsed.path == "/api/graphics/plan":
            # GFX-019 Text → Graphic Plan（纯文案 → 可编辑创作方案，方案 §34 先看方案再渲染）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return
            script = (payload.get("script") or "").strip()
            if not script:
                self._send_json({"error": "请先输入文案"}, 400)
                return
            try:
                from video_director.graphics import director as vd_gfx_director
                td = payload.get("target_duration")
                plan = vd_gfx_director.build_graphic_plan(
                    script,
                    content_type=payload.get("content_type") or "knowledge",
                    style_id=payload.get("style_id") or "editorial",
                    target_duration=float(td) if td else None,
                    width=int(payload.get("width") or 1080),
                    height=int(payload.get("height") or 1920),
                    visual_engine=payload.get("visual_engine") or "native",
                    visual_provider=payload.get("visual_provider") or "huashu",
                    visual_grammar=payload.get("visual_grammar"),
                )
                self._send_json({"ok": True, "plan": plan})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return

        if parsed.path == "/api/graphics/preview":
            # GFX-013 Graphic Preview：plan → 确定性 HTML（iframe srcdoc 即时预览）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace") or "{}")
            except json.JSONDecodeError:
                self._send_json({"error": "请求体不是合法 JSON"}, 400)
                return