"""Real-browser regression for time-ranged WatermarkLayer in the live preview."""
from __future__ import annotations

import base64
import importlib
import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from video_director import editor, store
from video_director.models import CompositionIR, WatermarkLayer
from tests.test_studio_browser_flow import _test_composition


def test_timed_text_watermark_appears_and_disappears_on_shared_live_preview_clock(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))

    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = Path(os.environ["VIDEO_DIRECTOR_STORAGE_ROOT"])

    project_id = "web-e2e-live-watermark-parity"
    still = tmp_path / "watermark-live-preview-still.png"
    still.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
        "lYkAAAAASUVORK5CYII="
    ))
    store.create_project(project_id, "实时预览水印回归", {"name": "实时预览水印回归"})
    base = _test_composition(still)
    composition = CompositionIR.model_validate({
        **json.loads(base.model_dump_json()),
        "watermarks": [
            WatermarkLayer(
                id="wm-live-window",
                kind="text",
                text="时间水印",
                anchor="top_right",
                offset_x=12,
                offset_y=8,
                safe_area=16,
                opacity=0.7,
                scale=0.9,
                start=0.25,
                duration=0.75,
            ).model_dump()
        ],
    })
    editor.save_composition(project_id, composition)

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            try:
                page = context.new_page()
                page_errors: list[str] = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                response = page.goto(
                    # /api/events 是 SSE 长连接（心跳 + 实时进度），networkidle 永不满足。
                    # domcontentloaded + 后续 wait_for_function 才是正确的就绪判据。
                    f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded"
                )
                assert response and response.status == 200
                page.wait_for_function(
                    "() => typeof workspaceOpen === 'function' && typeof previewSeek === 'function'"
                )
                opened = page.evaluate("(id) => workspaceOpen(id, undefined, {legacyCompositionMode:true})", project_id)
                assert opened and opened.get("ok"), opened
                page.wait_for_function(
                    "() => ED.comp && ED.comp.watermarks && ED.comp.watermarks.some(w => w.id === 'wm-live-window')",
                    timeout=10000,
                )
                page.evaluate("() => previewSeek(0.5)")
                mark = page.locator(
                    "#ed-preview-stage .pv-live-watermark-text[data-watermark-id='wm-live-window']"
                )
                mark.wait_for(state="visible", timeout=10000)
                assert mark.text_content() == "时间水印"
                inline = mark.evaluate(
                    "(el) => ({opacity:el.style.opacity, fontSize:el.style.fontSize, top:el.style.top, right:el.style.right})"
                )
                assert inline["opacity"] == "0.7", inline
                assert inline["fontSize"].endswith("px") and float(inline["fontSize"][:-2]) > 0, inline
                assert inline["top"].startswith("calc("), inline
                assert inline["right"].startswith("calc("), inline

                # FFmpeg between(t,start,end) includes the end timestamp.
                page.evaluate("() => previewSeek(1.0)")
                page.locator(
                    "#ed-preview-stage .pv-live-watermark-text[data-watermark-id='wm-live-window']"
                ).wait_for(state="visible", timeout=5000)

                # Moving beyond the end of the window makes the mark disappear.
                page.evaluate("() => previewSeek(1.05)")
                page.wait_for_function(
                    "() => document.querySelectorAll('#ed-preview-stage .pv-live-watermark-text').length === 0",
                    timeout=5000,
                )
                assert page.evaluate("() => PV.mode") == "live"

                # Scrubbing back into the active window recreates the same watermark.
                page.evaluate("() => previewSeek(0.5)")
                page.locator(
                    "#ed-preview-stage .pv-live-watermark-text[data-watermark-id='wm-live-window']"
                ).wait_for(state="visible", timeout=5000)
                assert not page_errors, "browser errors: " + " | ".join(page_errors)
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
