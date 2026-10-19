"""Real-browser regression for the OpenFilm-inspired Film Studio roll-trim gesture."""
from __future__ import annotations

import importlib
import os
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

from video_director import film_document as fd


SAMPLE = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"><style>body{margin:0}</style></head>
<body>
<section id="video-main" data-label="主画面">
<video id="clip-a" src="../assets/a.mp4#t=2,12" at="0" speed="2" volume="1"></video>
<video id="clip-b" src="../assets/b.mp4#t=0,5" at="5"></video>
</section>
<section id="audio-bed" data-label="BGM">
<audio id="audio-1" src="../assets/music.wav#t=0,20" at="0" volume="0.2"></audio>
</section>
</body></html>'''


def test_film_studio_ctrl_drag_roll_trim_updates_persisted_timeline(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-roll-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    assert fd.read_project_film(project_id)["exists"]

    results_dir = Path(os.environ.get("VIDEO_DIRECTOR_TEST_RESULTS", str(tmp_path / "test-results")))
    results_dir.mkdir(parents=True, exist_ok=True)
    trace_path = results_dir / "film-studio-roll-trim-trace.zip"

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            tracing = False
            try:
                context.tracing.start(screenshots=True, snapshots=True, sources=True)
                tracing = True
                page = context.new_page()
                page_errors: list[str] = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded"
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.filmStudioToggle === 'function'")
                page.evaluate("""(pid) => {
                  const select = document.querySelector('#editor-project');
                  if (!select) throw new Error('project selector is missing');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                }""", project_id)
                page.evaluate("window.filmStudioToggle()")
                page.locator('#film-studio-panel.fs-studio-expanded').wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').click()
                page.evaluate("window.filmStudioSeek(2)")
                page.locator('[data-film-action="split"]').click()
                page.wait_for_function(
                    "() => [...document.querySelectorAll('#fs-timeline .fs-clip')].some(el => el.dataset.clip.startsWith('clip-a-split-'))",
                    timeout=10000,
                )
                right_id = page.evaluate("""() => {
                  const item = [...document.querySelectorAll('#fs-timeline .fs-clip')]
                    .find(el => el.dataset.clip.startsWith('clip-a-split-'));
                  return item && item.dataset.clip;
                }""")
                assert right_id, "split should create the right-hand clip on the actual timeline"

                handle = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"] [data-trim="end"]')
                handle.scroll_into_view_if_needed()
                bounds = handle.bounding_box()
                assert bounds and bounds["width"] > 0 and bounds["height"] > 0
                x = bounds["x"] + bounds["width"] / 2
                y = bounds["y"] + bounds["height"] / 2
                page.keyboard.down("Control")
                try:
                    page.mouse.move(x, y)
                    page.mouse.down()
                    page.mouse.move(x + 18, y, steps=5)  # +0.5 s at 36 px/s
                    page.mouse.up()
                finally:
                    page.keyboard.up("Control")

                page.wait_for_function("""async (args) => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(args.pid));
                  if (!response.ok) return false;
                  const doc = await response.json();
                  const clips = (doc.tracks || []).flatMap(track => track.clips || []);
                  const left = clips.find(clip => clip.id === 'clip-a');
                  const right = clips.find(clip => clip.id === args.rightId);
                  return !!left && !!right
                    && String(left.attrs.src).endsWith('#t=2,7')
                    && String(right.attrs.src).endsWith('#t=7,12')
                    && Math.abs(Number(right.attrs.at) - 2.5) < 0.001;
                }""", {"pid": project_id, "rightId": right_id}, timeout=10000)

                persisted = fd.read_project_film(project_id)
                clips = {clip["id"]: clip for track in persisted["doc"]["tracks"] for clip in track["clips"]}
                assert clips["clip-a"]["attrs"]["src"].endswith("#t=2,7")
                assert clips[right_id]["attrs"]["src"].endswith("#t=7,12")
                assert float(clips[right_id]["attrs"]["at"]) == 2.5
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            except Exception:
                if tracing:
                    context.tracing.stop(path=str(trace_path))
                    tracing = False
                raise
            finally:
                if tracing:
                    context.tracing.stop()
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
