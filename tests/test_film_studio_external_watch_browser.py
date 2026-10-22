"""Real-browser regression for external Agent edits to film.html."""
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


def test_external_agent_write_hot_reloads_and_recovers_after_broken_document(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-external-watch-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    assert fd.read_project_film(project_id)["exists"]

    results_dir = Path(os.environ.get("VIDEO_DIRECTOR_TEST_RESULTS", str(tmp_path / "test-results")))
    results_dir.mkdir(parents=True, exist_ok=True)
    trace_path = results_dir / "film-studio-external-watch-trace.zip"

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            tracing = False
            page = context.new_page()
            page_errors: list[str] = []
            event_requests: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("request", lambda request: event_requests.append(request.url) if request.url.endswith("/api/events") else None)
            try:
                context.tracing.start(screenshots=True, snapshots=True, sources=True)
                tracing = True
                with page.expect_request("**/api/events", timeout=10000) as event_request:
                    response = page.goto(
                        f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded"
                    )
                assert response and response.status == 200
                assert event_request.value.url.endswith("/api/events")
                page.wait_for_function("typeof window.filmStudioToggle === 'function'")
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function(
                    "() => { const select = document.querySelector('#editor-project'); return !!select && select.options.length > 0; }"
                )
                page.evaluate("""(pid) => {
                  const select = document.querySelector('#editor-project');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                }""", project_id)
                page.evaluate("window.filmStudioToggle()")
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').wait_for(state="visible")
                page.evaluate("window.filmStudioSelectClip('clip-a')")
                page.wait_for_function(
                    "() => { const field = document.querySelector('#fs-field-speed'); return !!field && field.value === '2'; }"
                )

                changed = SAMPLE.replace('speed="2"', 'speed="2.5"')
                film_path.write_text(changed, encoding="utf-8")
                page.wait_for_function(
                    "() => { const field = document.querySelector('#fs-field-speed'); return !!field && field.value === '2.5'; }",
                    timeout=15000,
                )
                assert fd.read_project_film(project_id)["rev"] == fd.revision_of(changed)

                # An external revision must not overwrite edits still sitting in
                # the React Inspector's local form state. Reload is the explicit
                # user decision that discards the draft.
                page.locator("#fs-field-title").fill("Local unsaved draft")
                changed_again = changed.replace('speed="2.5"', 'speed="2.6"')
                film_path.write_text(changed_again, encoding="utf-8")
                page.wait_for_function(
                    "() => { const alert = document.querySelector('#film-studio-alert'); "
                    "return !!alert && alert.textContent.includes('未提交的属性草稿'); }",
                    timeout=15000,
                )
                assert page.locator("#fs-field-title").input_value() == "Local unsaved draft"
                assert page.locator("#fs-field-speed").input_value() == "2.5"
                assert film_path.read_text(encoding="utf-8") == changed_again

                # Undo/redo, project switching and export are separate entry
                # points that must not discard a dirty Inspector form either.
                page.locator('[data-film-action="undo"]:visible').click()
                page.wait_for_function(
                    "() => { const alert = document.querySelector('#film-studio-alert'); "
                    "return !!alert && alert.textContent.includes('再撤销/重做'); }",
                    timeout=5000,
                )
                assert page.locator("#fs-field-title").input_value() == "Local unsaved draft"
                page.locator('[data-film-action="render"]:visible').click()
                page.wait_for_function(
                    "() => { const alert = document.querySelector('#film-studio-alert'); "
                    "return !!alert && alert.textContent.includes('避免输出旧 revision'); }",
                    timeout=5000,
                )
                assert page.locator("#fs-field-title").input_value() == "Local unsaved draft"

                # Attempting a project switch is rejected before S.data is cleared.
                page.evaluate("""pid => {
                    const select = document.querySelector('#editor-project');
                    let option = [...select.options].find(item => item.value === pid);
                    if (!option) { option = new Option(pid, pid); select.add(option); }
                    select.value = pid;
                    select.dispatchEvent(new Event('change', {bubbles:true}));
                }""", "different-unsaved-project")
                page.wait_for_function(
                    "pid => document.querySelector('#editor-project').value === pid",
                    arg=project_id,
                    timeout=5000,
                )
                assert page.locator("#fs-field-title").input_value() == "Local unsaved draft"
                assert fd.read_project_film(project_id)["rev"] == fd.revision_of(changed_again)

                page.once("dialog", lambda dialog: dialog.accept())
                page.locator('[data-film-action="reload"]').click()
                page.wait_for_function(
                    "() => { const speed = document.querySelector('#fs-field-speed'); "
                    "const title = document.querySelector('#fs-field-title'); "
                    "return !!speed && speed.value === '2.6' && !!title && title.value === ''; }",
                    timeout=15000,
                )
                assert fd.read_project_film(project_id)["rev"] == fd.revision_of(changed_again)

                film_path.write_text("<html><body>partial write</body></html>", encoding="utf-8")
                page.wait_for_function(
                    "() => { const alert = document.querySelector('#film-studio-alert'); return !!alert && alert.textContent.includes('影片文件校验失败'); }",
                    timeout=15000,
                )
                assert film_path.read_text(encoding="utf-8") == "<html><body>partial write</body></html>"

                repaired = SAMPLE.replace('speed="2"', 'speed="2.75"')
                film_path.write_text(repaired, encoding="utf-8")
                page.wait_for_function(
                    "() => { const field = document.querySelector('#fs-field-speed'); return !!field && field.value === '2.75'; }",
                    timeout=15000,
                )
                assert fd.read_project_film(project_id)["rev"] == fd.revision_of(repaired)
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
