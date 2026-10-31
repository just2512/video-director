"""Real-browser regression for editable subtitle timeline cues."""
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
from video_director.models import CompositionIR, SubtitleCue, SubtitleTrack
from tests.test_studio_browser_flow import _test_composition


def test_word_subtitle_cues_move_trim_preview_and_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))

    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = Path(os.environ["VIDEO_DIRECTOR_STORAGE_ROOT"])

    pid = "web-e2e-editable-subtitle-cues"
    still = tmp_path / "subtitle-cue-still.png"
    still.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
        "lYkAAAAASUVORK5CYII="
    ))
    store.create_project(pid, "可编辑字幕时间锚点回归", {"name": "可编辑字幕时间锚点回归"})
    base = _test_composition(still)
    composition = CompositionIR.model_validate({
        **json.loads(base.model_dump_json()),
        "subtitle_track": SubtitleTrack(
            mode="word",
            source="manual",
            cues=[
                SubtitleCue(start=0.2, end=0.45, text="第一词"),
                SubtitleCue(start=0.75, end=0.95, text="第二词"),
            ],
        ).model_dump(),
    })
    editor.save_composition(pid, composition)

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = None
            try:
                page = context.new_page()
                page_errors: list[str] = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle"
                )
                assert response and response.status == 200
                page.wait_for_function(
                    "() => typeof workspaceOpen === 'function' && typeof previewSeek === 'function'"
                )
                opened = page.evaluate("(projectId) => workspaceOpen(projectId, undefined, {legacyCompositionMode:true})", pid)
                assert opened and opened.get("ok"), f"workspace did not open: {opened}"
                api_composition = page.evaluate("""async (projectId) => {
                  const response = await fetch('/api/editor/composition?project_id=' + encodeURIComponent(projectId));
                  return {status: response.status, body: await response.json()};
                }""", pid)
                assert api_composition["status"] == 200, api_composition
                assert api_composition["body"]["subtitle_track"]["cues"] == [
                    {"start": 0.2, "end": 0.45, "text": "第一词"},
                    {"start": 0.75, "end": 0.95, "text": "第二词"},
                ], api_composition
                loaded = page.evaluate("() => shotbookLoad()")
                assert loaded, "Shotbook did not load the persisted CompositionIR"
                page.wait_for_function(
                    "() => ED.comp && ED.comp.subtitle_track && ED.comp.subtitle_track.cues.length === 2",
                    timeout=10000,
                )
                page.evaluate("() => { ED.timelineView = 'time'; editorRenderTimeline(); }")
                page.wait_for_selector("#editor-timeline .ed-subtitle-cue[data-subtitle-cue-index='0']")
                page.wait_for_selector("#editor-timeline .ed-subtitle-cue[data-subtitle-cue-index='1']")

                cues_before = page.evaluate("() => ED.comp.subtitle_track.cues.map(c => ({start:c.start,end:c.end}))")
                assert cues_before == [{"start": 0.2, "end": 0.45}, {"start": 0.75, "end": 0.95}], cues_before
                clip = page.locator("#editor-timeline .ed-subtitle-cue[data-subtitle-cue-index='0']")
                visual = clip.evaluate("(el) => ({left: el.style.left, width: el.style.width, label: el.textContent})")
                assert visual == {"left": "10%", "width": "12.5%", "label": "第一词"}, visual

                # A plain click seeks the same central preview clock to the cue's start.
                clip.click()
                page.wait_for_function("() => Math.abs(PV.playhead - 0.2) < 0.002")
                assert not page_errors, "browser errors after subtitle cue seek: " + " | ".join(page_errors)

                # Center drag moves the cue; persistence must go through Editor Command.
                box = clip.evaluate("""el => {
                  const r = el.getBoundingClientRect();
                  const body = el.closest('.ed-track-body').getBoundingClientRect();
                  return {x:r.x, y:r.y, w:r.width, h:r.height, bodyWidth:body.width};
                }""")
                delta_px = min(70, box["bodyWidth"] * 0.12)
                page.mouse.move(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
                page.mouse.down()
                page.mouse.move(box["x"] + box["w"] / 2 + delta_px, box["y"] + box["h"] / 2, steps=5)
                page.mouse.up()
                page.wait_for_function(
                    "() => ED.comp.subtitle_track.cues[0].start > 0.25",
                    timeout=10000,
                )
                moved = page.evaluate("() => ({start:ED.comp.subtitle_track.cues[0].start,end:ED.comp.subtitle_track.cues[0].end})")
                assert moved["end"] > moved["start"] and moved["start"] > 0.25, moved
                assert moved["end"] < 0.75, "moving cue unexpectedly crossed the next subtitle"
                # ED.comp is hydrated before command callers finish their synchronous
                # workspace redraw. Wait for the timeline DOM to reflect the persisted
                # cue geometry before starting the next pointer gesture; otherwise the
                # next drag can race the first command's redraw and lose pointer capture.
                page.wait_for_function(
                    """(expected) => {
                      const cue = document.querySelector("#editor-timeline .ed-subtitle-cue[data-subtitle-cue-index='0']");
                      const total = Number(ED.comp && ED.comp.duration) || 1;
                      if (!cue || !total) return false;
                      const left = Number.parseFloat(cue.style.left) / 100 * total;
                      const width = Number.parseFloat(cue.style.width) / 100 * total;
                      return Math.abs(left - expected.start) < 0.002 &&
                        Math.abs(left + width - expected.end) < 0.002;
                    }""",
                    arg=moved,
                    timeout=5000,
                )

                # Drag the right edge to trim the end, not merely stretch a CSS block.
                end_before = moved["end"]
                clip = page.locator("#editor-timeline .ed-subtitle-cue[data-subtitle-cue-index='0']")
                box = clip.evaluate("""el => {
                  const r = el.getBoundingClientRect();
                  return {right:r.right, y:r.y, h:r.height, width:r.width};
                }""")
                assert box["width"] > 20, box
                page.evaluate("""({right, y}) => {
                  const cue = document.querySelector("#editor-timeline .ed-subtitle-cue[data-subtitle-cue-index='0']");
                  if (!cue) throw new Error("subtitle cue DOM disappeared before trim");
                  const pointerId = 731;
                  cue.dispatchEvent(new PointerEvent("pointerdown", {
                    bubbles:true, cancelable:true, pointerId, pointerType:"mouse",
                    button:0, buttons:1, clientX:right - 2, clientY:y, isPrimary:true
                  }));
                  document.dispatchEvent(new PointerEvent("pointermove", {
                    bubbles:true, cancelable:true, pointerId, pointerType:"mouse",
                    button:0, buttons:1, clientX:right + 18, clientY:y, isPrimary:true
                  }));
                  document.dispatchEvent(new PointerEvent("pointerup", {
                    bubbles:true, cancelable:true, pointerId, pointerType:"mouse",
                    button:0, buttons:0, clientX:right + 18, clientY:y, isPrimary:true
                  }));
                }""", {"right":box["right"], "y":box["y"] + box["h"] / 2})
                page.wait_for_function(
                    "(oldEnd) => ED.comp.subtitle_track.cues[0].end > oldEnd + 0.025",
                    arg=end_before,
                    timeout=10000,
                )
                trimmed = page.evaluate("() => ({start:ED.comp.subtitle_track.cues[0].start,end:ED.comp.subtitle_track.cues[0].end})")
                assert trimmed["start"] == moved["start"], trimmed
                assert trimmed["end"] > end_before, trimmed
                assert trimmed["end"] < 0.75, "trim unexpectedly crossed the next subtitle"

                # Reopening the project proves the moved/trimmed anchor was persisted.
                reopened = page.evaluate("(projectId) => workspaceOpen(projectId, undefined, {legacyCompositionMode:true})", pid)
                assert reopened and reopened.get("ok"), reopened
                page.wait_for_function(
                    "(expected) => Math.abs(ED.comp.subtitle_track.cues[0].start - expected.start) < 0.002"
                    " && Math.abs(ED.comp.subtitle_track.cues[0].end - expected.end) < 0.002",
                    arg=trimmed,
                    timeout=10000,
                )
                assert not page_errors, "browser errors: " + " | ".join(page_errors)
            finally:
                try:
                    context.close()
                finally:
                    browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
