"""Real-browser regression for external Agent edits to film.html."""
from __future__ import annotations

import importlib
import json
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
<video id="clip-a" src="../assets/a.mp4#t=2,12" at="0" speed="2" volume="1" data-film-keyframes='[{"property":"x","keyframes":[{"at":1,"value":0.25,"easing":"linear"}]}]'></video>
<video id="clip-b" src="../assets/b.mp4#t=0,5" at="5"></video>
</section>
<section id="audio-bed" data-label="BGM">
<audio id="audio-1" src="../assets/music.wav#t=0,20" at="0" volume="0.2"></audio>
</section>
</body></html>'''


def test_open_film_project_selection_uses_one_studio_reload_path(tmp_path, monkeypatch):
    """A project selector change must not launch a second concurrent FilmDocument load."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-project-shell-single-load"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    assert fd.read_project_film(project_id)["exists"]

    broken_project_id = "film-studio-project-shell-broken-document"
    broken_project_root = storage_root / broken_project_id
    broken_project_root.mkdir(parents=True)
    (broken_project_root / fd.FILM_NAME).write_text("<!doctype html><section", encoding="utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            # Pause the Studio's background revision poll for this focused count;
            # this test measures project-switch requests, not the independent watcher.
            page.add_init_script("""(() => {
              const nativeSetInterval = window.setInterval.bind(window);
              window.setInterval = (callback, delay, ...args) =>
                nativeSetInterval(callback, typeof delay === "number" && delay >= 500 ? 60000 : delay, ...args);
              window.__filmDocumentReadUrls = [];
              window.__compositionReadUrls = [];
              const nativeFetch = window.fetch.bind(window);
              window.fetch = (input, init) => {
                const url = typeof input === "string" ? input :
                  (input && typeof input.url === "string" ? input.url : String(input));
                if (url.includes("/api/editor/film?project_id=")) window.__filmDocumentReadUrls.push(url);
                if (url.includes("/api/editor/composition?project_id=")) window.__compositionReadUrls.push(url);
                return nativeFetch(input, init);
              };
            })();""")
            try:
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded"
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.filmStudioToggle === 'function'")
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function(
                    "() => { const select = document.querySelector('#editor-project'); return !!select && select.options.length > 0; }"
                )
                page.evaluate("""pid => {
                  const select = document.querySelector('#editor-project');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                  select.dispatchEvent(new Event('change', {bubbles:true}));
                }""", project_id)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').wait_for(state="visible", timeout=10000)
                initial_reads = page.evaluate("window.__filmDocumentReadUrls.slice()")
                assert len(initial_reads) == 2, {"initial FilmDocument reads": initial_reads}
                assert all(f"project_id={project_id}" in url for url in initial_reads), initial_reads
                legacy_reads = page.evaluate("window.__compositionReadUrls.slice()")
                assert legacy_reads == [], {
                    "migrated FilmDocument projects must not enter the legacy CompositionIR loader": legacy_reads
                }

                # Now Studio is open. A second selector change goes through the same
                # wrapper and must again perform one authority check + one Studio reload.
                page.evaluate("window.__filmDocumentReadUrls.length = 0")
                page.evaluate("""() => {
                  const select = document.querySelector('#editor-project');
                  select.dispatchEvent(new Event('change', {bubbles:true}));
                }""")
                page.wait_for_function(
                    "() => window.__filmDocumentReadUrls.length >= 2", timeout=5000
                )
                page.wait_for_timeout(350)
                reads = page.evaluate("window.__filmDocumentReadUrls.slice()")
                assert len(reads) == 2, {"reads": reads}
                assert all(
                    f"project_id={project_id}" in url for url in reads
                ), reads
                page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]').wait_for(state="visible")
                assert fd.read_project_film(project_id)["exists"]

                # Existing but malformed film.html must stay in the Studio's broken-document
                # gate and must never cause a legacy CompositionIR read.
                page.evaluate("""pid => {
                  const select = document.querySelector('#editor-project');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                  select.dispatchEvent(new Event('change', {bubbles:true}));
                }""", broken_project_id)
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('影片文档无效')",
                    timeout=10000,
                )
                assert "校验失败" in page.locator("#fs-timeline").inner_text()
                assert page.locator("#fs-timeline .fs-clip[data-clip]").count() == 0
                assert page.evaluate("window.__compositionReadUrls.slice()") == []
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
            film_edit_responses: list[str] = []
            film_edit_requests: list[dict] = []
            film_render_requests: list[str] = []
            film_history_requests: list[str] = []
            def capture_film_edit_request(request):
                if request.url.endswith("/api/editor/film/edit"):
                    try:
                        film_edit_requests.append(json.loads(request.post_data or "{}"))
                    except (TypeError, ValueError):
                        film_edit_requests.append({"_invalid_json": True})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("request", lambda request: event_requests.append(request.url) if request.url.endswith("/api/events") else None)
            page.on("request", capture_film_edit_request)
            page.on("request", lambda request: film_render_requests.append(request.url) if request.url.endswith("/api/editor/film/render") else None)
            page.on("request", lambda request: film_history_requests.append(request.url) if request.url.endswith(("/api/editor/film/undo", "/api/editor/film/redo")) else None)
            page.on("response", lambda response: film_edit_responses.append(f"{response.status} {response.url}") if response.url.endswith("/api/editor/film/edit") else None)

            # When a browser wait fails, emit the exact predicate and observable
            # editor state into the CI log; otherwise the runner's short summary
            # can hide the failing wait behind a generic 30-second timeout.
            native_wait_for_function = page.wait_for_function
            def wait_for_function_with_diagnostics(expression, *args, **kwargs):
                try:
                    return native_wait_for_function(expression, *args, **kwargs)
                except Exception as error:
                    try:
                        state = page.evaluate("""() => {
                          const scroll = document.querySelector("#fs-timeline-scroll");
                          const alert = document.querySelector("#film-studio-alert");
                          const store = window.filmStudioStore;
                          return {
                            url: location.href,
                            readyState: document.readyState,
                            studioClass: document.querySelector("#film-studio-panel")?.className || null,
                            timelineReady: !!document.querySelector("#fs-timeline"),
                            reactZoomReady: scroll?.dataset.reactZoomReady || null,
                            scroll: scroll ? {left: scroll.scrollLeft, width: scroll.scrollWidth, clientWidth: scroll.clientWidth} : null,
                            zoomScale: store?.getField("pixelsPerSecond") ?? null,
                            revision: store?.getSnapshot()?.revision ?? null,
                            selectedIds: store?.getSnapshot()?.selectedIds ?? null,
                            envelopeMode: store?.getField("envelopeMode") ?? null,
                            envelopeGesture: store?.getSnapshot()?.timelineEnvelopeGesture ?? null,
                            envelopePreview: store?.getSnapshot()?.timelineEnvelopePreview ?? null,
                            envelopeCanvas: (() => {
                              const canvas = document.querySelector('canvas[data-envelope-clip="audio-1"]');
                              if (!canvas) return null;
                              const r = canvas.getBoundingClientRect();
                              const x = r.left + r.width * 0.5, y = r.top + r.height * 0.9;
                              const hit = document.elementFromPoint(x, y);
                              return {
                                rect: {left:r.left,top:r.top,width:r.width,height:r.height},
                                pointerEvents: getComputedStyle(canvas).pointerEvents,
                                zIndex: getComputedStyle(canvas).zIndex,
                                hitIsCanvas: hit === canvas,
                                hitTag: hit?.tagName || null,
                                hitClass: String(hit?.className || ""),
                                hitEnvelopeClip: hit?.getAttribute?.("data-envelope-clip") || null
                              };
                            })(),
                            envelopePointerTrace: window.__envelopePointerTrace || null,
                            film: (() => {
                              const film = store?.getSnapshot()?.film;
                              return film ? {
                                exists: film.exists, revision: film.rev, duration: film.duration,
                                tracks: (film.tracks || []).map(track => ({
                                  id: track.id,
                                  clips: (track.clips || []).map(clip => clip.id)
                                }))
                              } : null;
                            })(),
                            trackRows: [...document.querySelectorAll("#fs-timeline .fs-track")].map(node => ({
                              id: node.getAttribute("data-track"),
                              clips: node.querySelectorAll(".fs-clip[data-clip]").length
                            })),
                            timelineHTML: document.querySelector("#fs-timeline")?.innerHTML.slice(0, 1200) ?? null,
                            clips: [...document.querySelectorAll("#fs-timeline .fs-clip[data-clip]")].map(node => ({
                              id: node.getAttribute("data-clip"), left: node.style.left, width: node.style.width,
                              dragging: node.classList.contains("fs-dragging")
                            })),
                            speed: document.querySelector("#fs-field-speed")?.value ?? null,
                            alert: alert?.textContent || null
                          };
                        }""")
                    except Exception as snapshot_error:
                        state = {"snapshotError": str(snapshot_error)}
                    print("EXTERNAL_WATCH_WAIT_FAILURE=" + json.dumps({
                        "predicate": str(expression)[:1200],
                        "timeout": kwargs.get("timeout"),
                        "error": str(error),
                        "state": state,
                    }, ensure_ascii=False, default=str), flush=True)
                    raise
            page.wait_for_function = wait_for_function_with_diagnostics
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
                  select.dispatchEvent(new Event('change', {bubbles: true}));
                }""", project_id)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').wait_for(state="visible")

                # React mode buttons are projections of FilmStore; adapter logic may change state but not their DOM attributes.
                mode_doc_before = fd.read_project_film(project_id)
                mode_controls = page.evaluate("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const button = selector => document.querySelector('#fs-react-timeline-toolbar ' + selector);
                  return {
                    snapEnabled: state?.snapEnabled,
                    insertMode: state?.insertMode,
                    overwriteMode: state?.overwriteMode,
                    snapPressed: button('[data-film-action="toggle-snap"]')?.getAttribute('aria-pressed'),
                    snapText: button('[data-film-action="toggle-snap"]')?.textContent.trim(),
                  };
                }""")
                assert mode_controls["snapPressed"] == str(mode_controls["snapEnabled"]).lower(), mode_controls
                snap_button = page.locator('#fs-react-timeline-toolbar [data-film-action="toggle-snap"]')
                snap_button.click()
                page.wait_for_function(
                    """expected => {
                      const state = window.filmStudioStore?.getSnapshot();
                      const button = document.querySelector('#fs-react-timeline-toolbar [data-film-action="toggle-snap"]');
                      return state?.snapEnabled === expected &&
                        button?.getAttribute('aria-pressed') === String(expected) &&
                        button?.textContent.trim() === (expected ? '吸附：开' : '吸附：关');
                    }""",
                    arg=not mode_controls["snapEnabled"],
                    timeout=5000,
                )
                snap_button.click()
                page.wait_for_function(
                    """expected => {
                      const state = window.filmStudioStore?.getSnapshot();
                      const button = document.querySelector('#fs-react-timeline-toolbar [data-film-action="toggle-snap"]');
                      return state?.snapEnabled === expected &&
                        button?.getAttribute('aria-pressed') === String(expected);
                    }""",
                    arg=mode_controls["snapEnabled"],
                    timeout=5000,
                )
                insert_button = page.locator('#fs-react-timeline-toolbar [data-film-action="insert-mode"]')
                overwrite_button = page.locator('#fs-react-timeline-toolbar [data-film-action="overwrite-mode"]')
                insert_button.click()
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const button = document.querySelector('#fs-react-timeline-toolbar [data-film-action="insert-mode"]');
                  return state?.insertMode === true && state?.overwriteMode === false &&
                    button?.getAttribute('aria-pressed') === 'true' && button.textContent.trim() === '插入编辑：开';
                }""", timeout=5000)
                overwrite_button.click()
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const insert = document.querySelector('#fs-react-timeline-toolbar [data-film-action="insert-mode"]');
                  const overwrite = document.querySelector('#fs-react-timeline-toolbar [data-film-action="overwrite-mode"]');
                  return state?.insertMode === false && state?.overwriteMode === true &&
                    insert?.getAttribute('aria-pressed') === 'false' && insert.textContent.trim() === '插入编辑：关' &&
                    overwrite?.getAttribute('aria-pressed') === 'true' && overwrite.textContent.trim() === '覆盖编辑：开';
                }""", timeout=5000)
                overwrite_button.click()
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const button = document.querySelector('#fs-react-timeline-toolbar [data-film-action="overwrite-mode"]');
                  return state?.overwriteMode === false && button?.getAttribute('aria-pressed') === 'false';
                }""", timeout=5000)
                assert fd.read_project_film(project_id)["rev"] == mode_doc_before["rev"], (
                    "Toolbar mode toggles are transient UI state; FilmDocument revision must not change."
                )

                # Playback label and pressed state are React projections of the shared runtime state.
                playback_doc_before = fd.read_project_film(project_id)
                play_button = page.locator('#fs-react-scrub-row #fs-play-toggle')
                initial_playing = page.evaluate("window.filmStudioStore?.getSnapshot()?.playing")
                page.wait_for_function(
                    """expected => {
                      const state = window.filmStudioStore?.getSnapshot();
                      const button = document.querySelector('#fs-react-scrub-row #fs-play-toggle');
                      return state?.playing === expected &&
                        button?.getAttribute('aria-pressed') === String(expected) &&
                        button?.textContent.trim() === (expected ? '暂停' : '播放');
                    }""",
                    arg=initial_playing,
                    timeout=5000,
                )
                page.evaluate("window.filmStudioTogglePlayback()")
                page.wait_for_function(
                    """expected => {
                      const state = window.filmStudioStore?.getSnapshot();
                      const button = document.querySelector('#fs-react-scrub-row #fs-play-toggle');
                      return state?.playing === expected &&
                        button?.getAttribute('aria-pressed') === String(expected) &&
                        button?.textContent.trim() === (expected ? '暂停' : '播放');
                    }""",
                    arg=not initial_playing,
                    timeout=5000,
                )
                page.evaluate("window.filmStudioSeek(0)")
                page.wait_for_function(
                    """() => {
                      const state = window.filmStudioStore?.getSnapshot();
                      const button = document.querySelector('#fs-react-scrub-row #fs-play-toggle');
                      return state?.playing === false && button?.getAttribute('aria-pressed') === 'false' &&
                        button?.textContent.trim() === '播放';
                    }""",
                    timeout=5000,
                )
                assert fd.read_project_film(project_id)["rev"] == playback_doc_before["rev"]
                page.wait_for_function(
                    "() => window.filmStudioStore?.getSnapshot()?.playhead === 0",
                    timeout=5000,
                )

                # The React playhead consumes FilmStore state; seeking changes geometry but not film revision.
                playhead_doc_before = fd.read_project_film(project_id)
                page.evaluate("window.filmStudioSeek(2)")
                page.wait_for_function("""() => {
                  const store = window.filmStudioStore;
                  const snapshot = store?.getSnapshot();
                  const line = document.querySelector('#fs-timeline #fs-playhead-line');
                  const math = window.filmStudioTimelineMath;
                  const scale = store?.getField('pixelsPerSecond');
                  return snapshot?.playhead === 2 && !!line && !!math &&
                    Math.abs(parseFloat(line.style.left) - (132 + math.clipLeft(2, scale))) < 0.1;
                }""", timeout=5000)
                assert fd.read_project_film(project_id)["rev"] == playhead_doc_before["rev"]
                page.evaluate("window.filmStudioSeek(0)")
                page.wait_for_function(
                    """() => window.filmStudioStore?.getSnapshot()?.playhead === 0 &&
                      Math.abs(parseFloat(document.querySelector('#fs-timeline #fs-playhead-line')?.style.left || '0') - 132) < 0.1""",
                    timeout=5000,
                )

                # Shared typed timeline math is used by React rendering and the
                # gesture adapter. Zoom keeps the same time under the pointer.
                math = page.evaluate("""() => {
                  const m = window.filmStudioTimelineMath;
                  if (!m) return null;
                  return {
                    pixels: m.secondsToPixels(1.5, 48),
                    seconds: m.pixelsToSeconds(72, 48),
                    dragDelta: m.dragDeltaSeconds(30, -10, 40),
                    snapTie: m.resolveSnapCandidate(10, [
                      {at: 10.125, kind: "first"},
                      {at: 9.875, kind: "equal-distance-later"},
                      {at: 10.3, kind: "outside-threshold"},
                    ], 40, 5),
                    noSnap: m.resolveSnapCandidate(10, [{at: 10.3, kind: "outside"}], 40, 5),
                    moveSnap: (() => { const r = m.resolveMoveGesture(5, 1, [{at: 0, kind: "head"}, {at: 6.1, kind: "move-snap"}], 36, 8, true); return {targetAt: Number(r.targetAt.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    moveZero: (() => { const r = m.resolveMoveGesture(2, -4, [{at: 0, kind: "head"}], 36, 8, true); return {targetAt: Number(r.targetAt.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    moveNoSnap: (() => { const r = m.resolveMoveGesture(5, 1, [{at: 6.1, kind: "move-snap"}], 36, 8, false); return {targetAt: Number(r.targetAt.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    slipPositive: m.resolveSlipGesture(1.25, 2).sourceDeltaSeconds,
                    slipNegative: m.resolveSlipGesture(-0.5, 0.5).sourceDeltaSeconds,
                    slipInvalidRate: m.resolveSlipGesture(1, Number.NaN).sourceDeltaSeconds,
                    envelopeInsert: (() => { const r=m.resolveEnvelopePointerDown([{t:0,v:1},{t:4,v:1}],4,100,40,50,20,0,false); return {kind:r.kind,index:r.index,added:r.added,points:r.points}; })(),
                    envelopeHit: (() => { const r=m.resolveEnvelopePointerDown([{t:0,v:1},{t:2,v:0.5},{t:4,v:1}],4,100,40,50,20,0,false); return {kind:r.kind,index:r.index,added:r.added}; })(),
                    envelopeDelete: (() => { const r=m.resolveEnvelopePointerDown([{t:0,v:1},{t:2,v:0.5},{t:4,v:1}],4,100,40,50,0,2,false); return {kind:r.kind,points:r.points}; })(),
                    envelopeEndpointDelete: (() => { const r=m.resolveEnvelopePointerDown([{t:0,v:1},{t:4,v:1}],4,100,40,0,36,2,false); return {kind:r.kind,points:r.points}; })(),
                    envelopeMove: m.moveEnvelopeKeyframe([{t:0,v:1},{t:2,v:0.5},{t:4,v:1}],1,4,100,40,90,40),
                    envelopeNormalize: m.normalizeEnvelopePoints([{t:0,v:1},{t:1.0004,v:0.2},{t:1.0003,v:0.8},{t:3,v:1}]),
                    trimStartSnap: (() => { const r = m.resolveTrimStart(0, 5, 1, [{at: 1.2, kind: "trim-snap"}], 36, 8, true); return {target: Number(r.target.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    trimStartClamp: (() => { const r = m.resolveTrimStart(2, 5, -9, [{at: 0, kind: "head"}], 36, 8, true); return {target: Number(r.target.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    trimEndSnap: (() => { const r = m.resolveTrimEnd(5, 10, -1, [{at: 8.85, kind: "trim-snap"}], 36, 8, true); return {target: Number(r.target.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    trimEndNoSnap: (() => { const r = m.resolveTrimEnd(5, 10, -1, [{at: 8.85, kind: "trim-snap"}], 36, 8, false); return {target: Number(r.target.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    rollSnap: (() => { const r = m.resolveRollGesture(10, -1, 5, 4, [{at: 8.9, kind: "roll-snap"}], 36, 8, true); return {targetAt: Number(r.targetAt.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    rollClamp: (() => { const r = m.resolveRollGesture(10, -20, 5, 4, [{at: 0, kind: "head"}], 36, 8, true); return {targetAt: Number(r.targetAt.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    slideSnap: (() => { const r = m.resolveSlideGesture(10, -1, 2, 3, 4, [{at: 8.9, kind: "slide-snap"}], 36, 8, true); return {targetAt: Number(r.targetAt.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    slideClamp: (() => { const r = m.resolveSlideGesture(10, 9, 2, 3, 4, [{at: 20, kind: "outside"}], 36, 8, true); return {targetAt: Number(r.targetAt.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    left: m.clipLeft(-1, 48),
                    unknownWidth: m.clipWidth(null, 48, 24, 2),
                    frame: m.snapToFrame(1.01, 30),
                    zoom: m.zoomScale(48, 1),
                    anchor: m.zoomAnchorTime(400, 150, 50, 132),
                    scroll: m.zoomScrollLeft(8, 150, 75, 132),
                  };
                }""")
                assert math == {
                    "pixels": 72,
                    "seconds": 1.5,
                    "dragDelta": 0.5,
                    "snapTie": {"at": 10.125, "kind": "first"},
                    "noSnap": None,
                    "moveSnap": {"targetAt": 6.1, "deltaSeconds": 1.1, "snapKind": "move-snap"},
                    "moveZero": {"targetAt": 0, "deltaSeconds": -2, "snapKind": "head"},
                    "moveNoSnap": {"targetAt": 6, "deltaSeconds": 1, "snapKind": None},
                    "slipPositive": 2.5,
                    "slipNegative": -0.25,
                    "slipInvalidRate": 1,
                    "envelopeInsert": {"kind":"insert","index":1,"added":True,"points":[{"t":0,"v":1},{"t":2,"v":0.5},{"t":4,"v":1}]},
                    "envelopeHit": {"kind":"edit","index":1,"added":False},
                    "envelopeDelete": {"kind":"remove","points":[{"t":0,"v":1},{"t":4,"v":1}]},
                    "envelopeEndpointDelete": {"kind":"none","points":[{"t":0,"v":1},{"t":4,"v":1}]},
                    "envelopeMove": [{"t":0,"v":1},{"t":3.6,"v":0},{"t":4,"v":1}],
                    "envelopeNormalize": [{"t":0,"v":1},{"t":1,"v":0.8},{"t":3,"v":1}],
                    "trimStartSnap": {"target": 1.2, "deltaSeconds": 1.2, "snapKind": "trim-snap"},
                    "trimStartClamp": {"target": 2.04, "deltaSeconds": 0.04, "snapKind": None},
                    "trimEndSnap": {"target": 8.85, "deltaSeconds": -1.15, "snapKind": "trim-snap"},
                    "trimEndNoSnap": {"target": 9, "deltaSeconds": -1, "snapKind": None},
                    "rollSnap": {"targetAt": 8.9, "deltaSeconds": -1.1, "snapKind": "roll-snap"},
                    "rollClamp": {"targetAt": 5.04, "deltaSeconds": -4.96, "snapKind": None},
                    "slideSnap": {"targetAt": 8.9, "deltaSeconds": -1.1, "snapKind": "slide-snap"},
                    "slideClamp": {"targetAt": 12.96, "deltaSeconds": 2.96, "snapKind": None},
                    "left": 0,
                    "unknownWidth": 96,
                    "frame": 1,
                    "zoom": 60,
                    "anchor": 8.36,
                    "scroll": 582,
                }

                # The legacy Director surfaces must remain usable after film.html
                # takes timeline authority; moving DOM nodes must not create a second editor.
                page.locator("#fs-mode-director").click()
                page.locator("#fs-director-planning-host").wait_for(state="visible")
                assert page.locator("#director-plan-card").evaluate("(node) => node.parentElement.id") == "fs-director-planning-host"
                assert page.locator("#director-control-grid").evaluate("(node) => node.parentElement.id") == "fs-director-planning-host"
                assert page.locator("#director-bar-card").evaluate("(node) => node.parentElement.id") == "fs-director-planning-host"
                assert page.locator("#director-input").is_visible()
                page.locator("#fs-mode-clips").click()
                assert page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').is_visible()
                assert page.locator("#fs-director-planning-host").is_hidden()

                with page.expect_response("**/api/director/context") as director_context_response:
                    page.evaluate("window.filmStudioSelectClip('clip-a')")
                director_context = director_context_response.value.json()
                assert director_context["context"]["authority"] == "film.html"
                assert director_context["context"]["revision"] == fd.read_project_film(project_id)["rev"]
                assert director_context["context"]["selection"]["id"] == "clip-a"
                assert director_context["context"]["selection"]["ids"] == ["clip-a"]
                store_state = page.evaluate("""() => {
                  const store = window.filmStudioStore;
                  const snapshot = store && store.getSnapshot();
                  const node = document.querySelector("#fs-react-film-store-state");
                  return {
                    projectId: snapshot?.projectId,
                    revision: snapshot?.revision,
                    selectedId: snapshot?.selectedId,
                    selectedIds: snapshot?.selectedIds,
                    domRevision: node?.dataset.filmRevision,
                    domSelectedId: node?.dataset.selectedClip,
                    transactionKind: node?.dataset.pendingTransactionKind,
                  };
                }""")
                assert store_state["projectId"] == project_id, store_state
                assert store_state["revision"] == fd.read_project_film(project_id)["rev"], store_state
                assert store_state["domRevision"] == store_state["revision"], store_state
                assert store_state["selectedId"] == "clip-a", store_state
                assert store_state["selectedIds"] == ["clip-a"], store_state
                assert store_state["domSelectedId"] == "clip-a", store_state
                assert store_state["transactionKind"] == "", store_state
                page.wait_for_function(
                    "() => { const field = document.querySelector('#fs-field-speed'); return !!field && field.value === '2'; }"
                )

                # Selection classes are React projections of FilmStore, not native classList writes.
                selection_before = fd.read_project_film(project_id)
                page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]').click()
                page.wait_for_function("""() => {
                  const store = window.filmStudioStore?.getSnapshot();
                  const a = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-a"]');
                  const b = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  return store?.selectedId === "clip-b" && store.selectedIds.includes("clip-b") &&
                    !!a && !!b && !a.classList.contains("fs-clip-selected") &&
                    b.classList.contains("fs-clip-selected");
                }""")
                assert fd.read_project_film(project_id)["rev"] == selection_before["rev"]
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').click()
                page.wait_for_function("""() => {
                  const store = window.filmStudioStore?.getSnapshot();
                  const a = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-a"]');
                  return store?.selectedId === "clip-a" && !!a && a.classList.contains("fs-clip-selected");
                }""")

                # React owns keyframe marker events: seek only, no parent clip drag or FilmDocument write.
                keyframe_before = fd.read_project_film(project_id)
                keyframe_clip_before = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').evaluate(
                    "(node) => ({left:node.style.left,width:node.style.width})"
                )
                page.locator('button[data-film-keyframe-marker="clip-a"][data-keyframe-at="1"]').click()
                page.wait_for_function(
                    """() => {
                      const slider = document.querySelector('#fs-playhead');
                      const monitor = document.querySelector('#fs-monitor-time');
                      return !!slider && Math.abs(Number(slider.value) - 1) < 0.02 &&
                        !!monitor && monitor.textContent.trim() === '00:01.000';
                    }"""
                )
                assert page.locator('#fs-field-speed').input_value() == '2'
                keyframe_after = fd.read_project_film(project_id)
                assert keyframe_after["rev"] == keyframe_before["rev"], {
                    "before": keyframe_before["rev"], "after": keyframe_after["rev"]
                }
                keyframe_clip_after = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').evaluate(
                    "(node) => ({left:node.style.left,width:node.style.width})"
                )
                assert keyframe_clip_after == keyframe_clip_before, {
                    "before": keyframe_clip_before, "after": keyframe_clip_after
                }
                assert page.locator('[data-react-gesture-preview="true"]').count() == 0

                # The React ruler must route seek through the shared playhead bridge.
                page.locator('#fs-ruler-body .fs-ruler-mark[data-at="5"]').click()
                page.wait_for_function(
                    """() => {
                      const slider = document.querySelector('#fs-playhead');
                      const monitor = document.querySelector('#fs-monitor-time');
                      return !!slider && Math.abs(Number(slider.value) - 5) < 0.02 &&
                        !!monitor && monitor.textContent.trim() === '00:05.000';
                    }"""
                )
                assert page.locator("#fs-monitor-time").inner_text() == "00:05.000"

                # Dragging the React ruler continuously seeks; it must not create a FilmDocument edit.
                ruler_body = page.locator("#fs-ruler-body[data-react-scrub-ready='true']")
                ruler_bounds = ruler_body.bounding_box()
                assert ruler_bounds, "React ruler body is not available for scrub"
                pixels_per_second = page.evaluate("window.filmStudioStore.getField('pixelsPerSecond')")
                scrub_click_before = fd.read_project_film(project_id)
                scrub_click_count_before = len(film_edit_requests)
                ruler_body.locator('.fs-ruler-mark[data-at="5"]').click()
                page.wait_for_function("window.filmStudioStore?.getSnapshot()?.playhead === 5", timeout=5000)
                assert fd.read_project_film(project_id)["rev"] == scrub_click_before["rev"]
                assert len(film_edit_requests) == scrub_click_count_before

                scrub_before = fd.read_project_film(project_id)
                scrub_edit_count_before = len(film_edit_requests)
                scrub_y = ruler_bounds["y"] + ruler_bounds["height"] / 2
                scrub_start_x = ruler_bounds["x"] + pixels_per_second * 1.0
                scrub_end_x = ruler_bounds["x"] + pixels_per_second * 4.0
                page.mouse.move(scrub_start_x, scrub_y)
                page.mouse.down()
                page.mouse.move(scrub_end_x, scrub_y, steps=8)
                page.wait_for_function(
                    """() => {
                      const slider = document.querySelector('#fs-playhead');
                      const monitor = document.querySelector('#fs-monitor-time');
                      return !!slider && Math.abs(Number(slider.value) - 4) < 0.15 &&
                        !!monitor && monitor.textContent.trim() === '00:04.000';
                    }""",
                    timeout=5000,
                )
                page.mouse.up()
                assert fd.read_project_film(project_id)["rev"] == scrub_before["rev"]
                assert len(film_edit_requests) == scrub_edit_count_before

                # Keyboard seeking uses 1-second steps, Shift uses 0.1-second steps, and no edit transaction.
                ruler_body.focus()
                page.keyboard.press("ArrowRight")
                page.wait_for_function("""() => {
                  const slider = document.querySelector('#fs-playhead');
                  const monitor = document.querySelector('#fs-monitor-time');
                  return !!slider && Math.abs(Number(slider.value) - 5) < 0.02 &&
                    !!monitor && monitor.textContent.trim() === '00:05.000' &&
                    document.querySelector('#fs-ruler-body')?.getAttribute('aria-valuenow') === '5';
                }""", timeout=5000)
                page.keyboard.press("Shift+ArrowLeft")
                page.wait_for_function(
                    "() => Math.abs(Number(document.querySelector('#fs-playhead')?.value) - 4.9) < 0.02",
                    timeout=5000,
                )
                page.keyboard.press("Shift+ArrowRight")
                page.wait_for_function(
                    "() => Math.abs(Number(document.querySelector('#fs-playhead')?.value) - 5) < 0.02",
                    timeout=5000,
                )
                assert fd.read_project_film(project_id)["rev"] == scrub_before["rev"]
                assert len(film_edit_requests) == scrub_edit_count_before

                # Exercise React-owned track controls against the authoritative FilmDocument.
                def track_attrs(track_id: str) -> dict:
                    document = fd.read_project_film(project_id)["doc"]
                    track = next(item for item in document["tracks"] if item["id"] == track_id)
                    return track["attrs"]

                lock_button = page.locator('button[data-track-lock="video-main"]')
                lock_button.click()
                page.wait_for_function(
                    "() => document.querySelector('button[data-track-lock=\"video-main\"]')?.textContent.trim() === '🔒'"
                )
                assert "locked" in track_attrs("video-main")
                lock_button.click()
                page.wait_for_function(
                    "() => document.querySelector('button[data-track-lock=\"video-main\"]')?.textContent.trim() === '锁'"
                )
                assert "locked" not in track_attrs("video-main")

                sync_button = page.locator('button[data-track-flag="sync-lock"][data-track-key="video-main"]')
                sync_button.click()
                page.wait_for_function(
                    "() => document.querySelector('button[data-track-flag=\"sync-lock\"][data-track-key=\"video-main\"]')?.textContent.trim() === '⇄'"
                )
                assert "sync-lock" in track_attrs("video-main")
                sync_button.click()
                page.wait_for_function(
                    "() => document.querySelector('button[data-track-flag=\"sync-lock\"][data-track-key=\"video-main\"]')?.textContent.trim() === '同步'"
                )
                assert "sync-lock" not in track_attrs("video-main")

                hide_button = page.locator('button[data-track-flag="hidden"][data-track-key="video-main"]')
                hide_button.click()
                page.wait_for_function(
                    "() => document.querySelector('button[data-track-flag=\"hidden\"][data-track-key=\"video-main\"]')?.textContent.trim() === '显'"
                )
                assert "hidden" in track_attrs("video-main")
                hide_button.click()
                page.wait_for_function(
                    "() => document.querySelector('button[data-track-flag=\"hidden\"][data-track-key=\"video-main\"]')?.textContent.trim() === '隐'"
                )
                assert "hidden" not in track_attrs("video-main")

                mute_button = page.locator('button[data-track-flag="muted"][data-track-key="audio-bed"]')
                mute_button.click()
                page.wait_for_function(
                    "() => document.querySelector('button[data-track-flag=\"muted\"][data-track-key=\"audio-bed\"]')?.textContent.trim() === '🔊'"
                )
                assert "muted" in track_attrs("audio-bed")
                mute_button.click()
                page.wait_for_function(
                    "() => document.querySelector('button[data-track-flag=\"muted\"][data-track-key=\"audio-bed\"]')?.textContent.trim() === '🔇'"
                )
                assert "muted" not in track_attrs("audio-bed")

                # Exercise a real React-owned pointer gesture in Chromium, not only a static contract.
                clip = page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]')
                box = clip.bounding_box()
                assert box and box["width"] >= 20, box
                original_left = clip.evaluate("(element) => element.style.left")
                start_x = box["x"] + box["width"] * 0.5
                start_y = box["y"] + box["height"] * 0.5
                # clip-b is five seconds long, so one fifth of its width is one timeline second.
                delta_x = box["width"] / 5
                edit_count_before_gesture = len(film_edit_requests)
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(start_x + delta_x, start_y, steps=6)
                page.wait_for_function(
                    """({clipId, oldLeft}) => {
                      const node = document.querySelector('#fs-timeline .fs-clip[data-clip="' + clipId + '"]');
                      return !!node && node.dataset.reactGesturePreview === "true" &&
                        node.classList.contains("fs-dragging") && node.style.left !== oldLeft;
                    }""",
                    arg={"clipId": "clip-b", "oldLeft": original_left},
                    timeout=5000,
                )
                preview_left = clip.evaluate("(element) => element.style.left")
                assert preview_left != original_left, {"before": original_left, "preview": preview_left}
                page.mouse.up()
                page.wait_for_function(
                    """oldLeft => {
                      const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                      return !!node && node.style.left !== oldLeft;
                    }""",
                    arg=original_left,
                    timeout=10000,
                )
                moved_document = fd.read_project_film(project_id)
                moved_clip = next(
                    item for track in moved_document["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-b"
                )
                assert abs(float(moved_clip["attrs"]["at"]) - 6.0) < 0.15, moved_clip
                page.wait_for_function(
                    """() => document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]')?.dataset.reactGesturePreview === "false" """
                )
                assert clip.evaluate("(element) => element.style.left") != original_left
                gesture_requests = film_edit_requests[edit_count_before_gesture:]
                assert len(gesture_requests) == 1, gesture_requests
                assert len(gesture_requests[0].get("ops") or []) == 1, gesture_requests
                assert gesture_requests[0]["ops"][0].get("op") == "move", gesture_requests
                moved_left = clip.evaluate("(element) => element.style.left")

                # A completed drag is exactly one history step: undo and redo
                # must restore both authoritative film time and rendered geometry.
                page.evaluate("window.filmStudioUndo()")
                page.wait_for_function(
                    "(expected) => { const node = document.querySelector('#fs-timeline .fs-clip[data-clip=\"clip-b\"]'); return !!node && node.style.left === expected; }",
                    arg=original_left,
                    timeout=10000,
                )
                undone_document = fd.read_project_film(project_id)
                undone_clip = next(
                    item for track in undone_document["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-b"
                )
                assert abs(float(undone_clip["attrs"]["at"]) - 5.0) < 0.001, undone_clip

                page.evaluate("window.filmStudioRedo()")
                page.wait_for_function(
                    "(expected) => { const node = document.querySelector('#fs-timeline .fs-clip[data-clip=\"clip-b\"]'); return !!node && node.style.left === expected; }",
                    arg=moved_left,
                    timeout=10000,
                )
                redone_document = fd.read_project_film(project_id)
                redone_clip = next(
                    item for track in redone_document["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-b"
                )
                assert abs(float(redone_clip["attrs"]["at"]) - 6.0) < 0.15, redone_clip

                # Trim-start preview geometry must be React-owned and transient; only pointerup submits a trim op.
                trim_clip = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]')
                trim_handle = trim_clip.locator('.fs-trim-handle[data-trim="start"]')
                trim_box = trim_handle.bounding_box()
                assert trim_box, "clip-a trim-start handle unavailable"
                trim_clip_box_before = trim_clip.evaluate("(node) => ({left:node.style.left,width:node.style.width})")
                trim_document_before = fd.read_project_film(project_id)
                trim_edit_count_before = len(film_edit_requests)
                trim_x = trim_box["x"] + trim_box["width"] / 2
                trim_y = trim_box["y"] + trim_box["height"] / 2
                trim_delta_x = 18
                page.mouse.move(trim_x, trim_y)
                page.mouse.down()
                page.mouse.move(trim_x + trim_delta_x, trim_y, steps=6)
                page.wait_for_function(
                    """({oldLeft, oldWidth}) => {
                      const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-a"]');
                      return !!node && node.dataset.reactGesturePreview === "true" &&
                        node.style.left !== oldLeft && node.style.width !== oldWidth;
                    }""",
                    arg=trim_clip_box_before,
                    timeout=5000,
                )
                trim_preview = trim_clip.evaluate("(node) => ({left:node.style.left,width:node.style.width})")
                assert trim_preview != trim_clip_box_before, {"before":trim_clip_box_before,"preview":trim_preview}
                transient_state = page.evaluate("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return {
                    gesture: state?.timelineGesturePreview,
                    snapGuide: state?.timelineSnapGuide,
                    dropTarget: state?.timelineDropTarget,
                    activeDragIds: state?.timelineActiveDragIds
                  };
                }""")
                assert transient_state["gesture"] and "clip-a" in transient_state["gesture"], transient_state
                assert isinstance(transient_state["gesture"]["clip-a"].get("left"), (int, float)), transient_state
                assert isinstance(transient_state["gesture"]["clip-a"].get("width"), (int, float)), transient_state
                assert "clip-a" in transient_state["activeDragIds"], transient_state
                assert len(film_edit_requests) == trim_edit_count_before
                assert fd.read_project_film(project_id)["rev"] == trim_document_before["rev"]
                page.mouse.up()
                page.wait_for_function(
                    """() => document.querySelector('#fs-timeline .fs-clip[data-clip=\\"clip-a\\"]')?.dataset.reactGesturePreview === 'false' """,
                    timeout=10000,
                )
                cleared_transient_state = page.evaluate("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return {
                    gesture: state?.timelineGesturePreview,
                    snapGuide: state?.timelineSnapGuide,
                    dropTarget: state?.timelineDropTarget,
                    activeDragIds: state?.timelineActiveDragIds
                  };
                }""")
                assert cleared_transient_state == {
                    "gesture": None, "snapGuide": None, "dropTarget": None, "activeDragIds": None
                }, cleared_transient_state
                trim_document_after = fd.read_project_film(project_id)
                trim_clip_after = next(
                    item for track in trim_document_after["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-a"
                )
                assert float(trim_clip_after["attrs"]["at"]) > float(next(
                    item for track in trim_document_before["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-a"
                )["attrs"]["at"]) + 0.1, trim_clip_after
                trim_requests = film_edit_requests[trim_edit_count_before:]
                assert len(trim_requests) == 1, trim_requests
                assert len(trim_requests[0].get("ops") or []) == 1, trim_requests
                assert trim_requests[0]["ops"][0].get("op") == "trim", trim_requests
                assert trim_requests[0]["ops"][0].get("edge") == "start", trim_requests
                page.evaluate("window.filmStudioUndo()")
                page.wait_for_function(
                    """({left,width}) => {
                      const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-a"]');
                      return !!node && node.style.left === left && node.style.width === width;
                    }""",
                    arg=trim_clip_box_before,
                    timeout=10000,
                )
                trim_undone_doc = fd.read_project_film(project_id)
                trim_undone_clip = next(
                    item for track in trim_undone_doc["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-a"
                )
                assert abs(float(trim_undone_clip["attrs"]["at"])) < 0.001, trim_undone_clip

                # Re-establish the intended anchor after the trim test selected clip-a.
                clip.click()
                page.wait_for_function("""() => {
                  const video = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  const audio = document.querySelector('#fs-timeline .fs-clip[data-clip="audio-1"]');
                  return !!video && !!audio && video.classList.contains('fs-clip-selected') &&
                    !audio.classList.contains('fs-clip-selected');
                }""")
                # Cross-track multi-selection moves as one two-operation FilmDocument transaction.
                audio_clip = page.locator('#fs-timeline .fs-clip[data-clip="audio-1"]')
                audio_clip.click(modifiers=["Control"])
                page.wait_for_function("""() => {
                  const video = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  const audio = document.querySelector('#fs-timeline .fs-clip[data-clip="audio-1"]');
                  return !!video && !!audio && video.classList.contains('fs-clip-selected') &&
                    audio.classList.contains('fs-clip-selected');
                }""")
                # AddTrack inserts at index 0. Create two empty rows: a one-row translation
                # collides with stationary clip-a, while the two-row translation is valid.
                page.evaluate("window.prompt = () => 'Cross-track Preview Companion'")
                page.evaluate("window.filmStudioAddTrack()")
                page.wait_for_function("""() => [...document.querySelectorAll('#fs-timeline .fs-track-label')]
                  .some(label => label.textContent.trim() === 'Cross-track Preview Companion')""", timeout=10000)
                cross_track_companion_id = page.evaluate("""() => {
                  const label = [...document.querySelectorAll('#fs-timeline .fs-track-label')]
                    .find(node => node.textContent.trim() === 'Cross-track Preview Companion');
                  return label?.closest('.fs-track')?.getAttribute('data-track') || null;
                }""")
                assert cross_track_companion_id
                page.evaluate("window.prompt = () => 'Cross-track Preview Destination'")
                page.evaluate("window.filmStudioAddTrack()")
                page.wait_for_function("""() => [...document.querySelectorAll('#fs-timeline .fs-track-label')]
                  .some(label => label.textContent.trim() === 'Cross-track Preview Destination')""", timeout=10000)
                cross_track_destination_id = page.evaluate("""() => {
                  const label = [...document.querySelectorAll('#fs-timeline .fs-track-label')]
                    .find(node => node.textContent.trim() === 'Cross-track Preview Destination');
                  return label?.closest('.fs-track')?.getAttribute('data-track') || null;
                }""")
                assert cross_track_destination_id
                page.wait_for_function("""() => {
                  const video = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  const audio = document.querySelector('#fs-timeline .fs-clip[data-clip="audio-1"]');
                  return !!video && !!audio && video.classList.contains('fs-clip-selected') &&
                    audio.classList.contains('fs-clip-selected');
                }""")

                # A one-row translation would move audio-1 to video-main and overlap clip-a.
                # The UI must reject it before the FilmDocument write path is called.
                cross_before = fd.read_project_film(project_id)
                cross_request_count = len(film_edit_requests)
                cross_video_before_left = clip.evaluate("(node) => node.style.left")
                cross_video_box = clip.bounding_box()
                target_destination_box = page.locator('.fs-track[data-track="' + cross_track_destination_id + '"]').bounding_box()
                target_companion_box = page.locator('.fs-track[data-track="' + cross_track_companion_id + '"]').bounding_box()
                assert cross_video_box and target_destination_box and target_companion_box
                cross_start_x = cross_video_box["x"] + cross_video_box["width"] / 2
                cross_start_y = cross_video_box["y"] + cross_video_box["height"] / 2
                cross_companion_y = target_companion_box["y"] + target_companion_box["height"] / 2
                page.mouse.move(cross_start_x, cross_start_y)
                page.mouse.down()
                page.mouse.move(cross_start_x + 36, cross_companion_y, steps=10)
                page.wait_for_function("""targetId => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const gesture = state?.timelineGestureContext;
                  const row = document.querySelector('.fs-track[data-track="' + targetId + '"]');
                  return gesture?.targetTrackId === targetId &&
                    String(gesture?.invalidTargetMessage || '').includes('时间重叠') &&
                    row?.classList.contains('fs-track-drop-invalid');
                }""", arg=cross_track_companion_id, timeout=5000)
                assert len(film_edit_requests) == cross_request_count
                assert fd.read_project_film(project_id)["rev"] == cross_before["rev"]
                page.mouse.up()
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state?.timelineGestureContext === null && state?.timelineGesturePreview === null;
                }""", timeout=10000)
                assert len(film_edit_requests) == cross_request_count
                assert fd.read_project_film(project_id)["rev"] == cross_before["rev"]

                # Leaving all track rows must clear the stale target and refuse commit.
                outside_track_scroll = page.locator("#fs-timeline-scroll").bounding_box()
                assert outside_track_scroll
                outside_track_x = min(1400, outside_track_scroll["x"] + outside_track_scroll["width"] * 0.7)
                outside_track_y = max(2, outside_track_scroll["y"] - 8)
                assert page.evaluate(
                    "([x,y]) => !document.elementFromPoint(x,y)?.closest('.fs-track')",
                    [outside_track_x, outside_track_y],
                ), {"x": outside_track_x, "y": outside_track_y}
                outside_before = fd.read_project_film(project_id)
                outside_requests_before = len(film_edit_requests)
                outside_box = clip.bounding_box()
                assert outside_box
                page.mouse.move(outside_box["x"] + outside_box["width"] / 2, outside_box["y"] + outside_box["height"] / 2)
                page.mouse.down()
                page.mouse.move(outside_track_x, outside_track_y, steps=10)
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return String(state?.timelineGestureContext?.invalidTargetMessage || '').includes('有效轨道') &&
                    state?.timelineDropTarget === null;
                }""", timeout=5000)
                assert len(film_edit_requests) == outside_requests_before
                page.mouse.up()
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state?.timelineGestureContext === null && state?.timelineGesturePreview === null &&
                    state?.timelineDropTarget === null;
                }""", timeout=10000)
                assert len(film_edit_requests) == outside_requests_before
                assert fd.read_project_film(project_id)["rev"] == outside_before["rev"]

                # A two-row translation moves the members to the two empty rows.
                cross_video_box = clip.bounding_box()
                target_destination_box = page.locator('.fs-track[data-track="' + cross_track_destination_id + '"]').bounding_box()
                assert cross_video_box and target_destination_box
                cross_start_x = cross_video_box["x"] + cross_video_box["width"] / 2
                cross_start_y = cross_video_box["y"] + cross_video_box["height"] / 2
                cross_target_y = target_destination_box["y"] + target_destination_box["height"] / 2
                page.mouse.move(cross_start_x, cross_start_y)
                page.mouse.down()
                page.mouse.move(cross_start_x + 36, cross_target_y, steps=10)
                page.wait_for_function("""targets => {
                  const videoGhost = document.querySelector('[data-gesture-ghost-for="clip-b"][data-gesture-target-track="' + targets.destinationId + '"]');
                  const audioGhost = document.querySelector('[data-gesture-ghost-for="audio-1"][data-gesture-target-track="' + targets.companionId + '"]');
                  const state = window.filmStudioStore?.getSnapshot();
                  return !!videoGhost && !!audioGhost &&
                    state?.timelineGesturePreview?.['clip-b']?.targetTrackId === targets.destinationId &&
                    state?.timelineGesturePreview?.['audio-1']?.targetTrackId === targets.companionId &&
                    !state?.timelineGestureContext?.invalidTargetMessage;
                }""", arg={"destinationId": cross_track_destination_id, "companionId": cross_track_companion_id}, timeout=5000)
                cross_video_ghost = page.locator('[data-gesture-ghost-for="clip-b"][data-gesture-target-track="' + cross_track_destination_id + '"]')
                assert cross_video_ghost.get_attribute("aria-hidden") == "true"
                assert cross_video_ghost.evaluate("(node) => node.style.left") != cross_video_before_left
                assert len(film_edit_requests) == cross_request_count
                assert fd.read_project_film(project_id)["rev"] == cross_before["rev"]

                with page.expect_response(
                    lambda response: response.url.endswith("/api/editor/film/edit") and response.request.method == "POST",
                    timeout=10000,
                ) as cross_edit_response_info:
                    page.mouse.up()
                cross_edit_response = cross_edit_response_info.value
                assert cross_edit_response.status == 200, cross_edit_response.text()
                page.wait_for_function("""expectedRevision => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state?.timelineGestureContext === null && state?.timelineGesturePreview === null &&
                    state?.revision !== expectedRevision;
                }""", arg=cross_before["rev"], timeout=10000)
                cross_after = fd.read_project_film(project_id)
                def located_track(document: dict, clip_id: str) -> str:
                    return next(track["id"] for track in document["doc"]["tracks"]
                      if any(item["id"] == clip_id for item in track["clips"]))
                assert located_track(cross_after, "clip-b") == cross_track_destination_id
                assert located_track(cross_after, "audio-1") == cross_track_companion_id
                cross_requests = film_edit_requests[cross_request_count:]
                assert len(cross_requests) == 1, cross_requests
                cross_ops = cross_requests[0].get("ops") or []
                assert len(cross_ops) == 2 and all(op.get("op") == "move" for op in cross_ops), cross_requests
                assert {op.get("clip") for op in cross_ops} == {"clip-b", "audio-1"}, cross_ops
                page.evaluate("window.filmStudioUndo()")
                page.wait_for_function("""() => {
                  const video = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  const audio = document.querySelector('#fs-timeline .fs-clip[data-clip="audio-1"]');
                  return !!video && !!audio &&
                    video.closest('.fs-track')?.getAttribute('data-track') === 'video-main' &&
                    audio.closest('.fs-track')?.getAttribute('data-track') === 'audio-bed';
                }""", timeout=10000)
                cross_undone = fd.read_project_film(project_id)
                assert located_track(cross_undone, "clip-b") == "video-main"
                assert located_track(cross_undone, "audio-1") == "audio-bed"

                group_before = fd.read_project_film(project_id)
                group_video_before = next(
                    item for track in group_before["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-b"
                )
                group_audio_before = next(
                    item for track in group_before["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "audio-1"
                )
                video_box = clip.bounding_box()
                audio_box = audio_clip.bounding_box()
                assert video_box and audio_box, {"video": video_box, "audio": audio_box}
                group_start_x = audio_box["x"] + audio_box["width"] * 0.5
                group_start_y = audio_box["y"] + audio_box["height"] * 0.5
                group_delta_x = video_box["width"] / 5
                group_lefts_before = page.evaluate("""() => Object.fromEntries(
                  ['clip-b','audio-1'].map(id => [
                    id, document.querySelector('#fs-timeline .fs-clip[data-clip="' + id + '"]').style.left
                  ])
                )""")
                group_request_start = len(film_edit_requests)
                page.mouse.move(group_start_x, group_start_y)
                page.mouse.down()
                page.mouse.move(group_start_x + group_delta_x, group_start_y, steps=8)
                page.mouse.up()
                page.wait_for_function("""before => {
                  const video = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  const audio = document.querySelector('#fs-timeline .fs-clip[data-clip="audio-1"]');
                  return !!video && !!audio && video.style.left !== before['clip-b'] &&
                    audio.style.left !== before['audio-1'];
                }""", arg=group_lefts_before, timeout=10000)
                group_after = fd.read_project_film(project_id)
                group_video_after = next(
                    item for track in group_after["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-b"
                )
                group_audio_after = next(
                    item for track in group_after["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "audio-1"
                )
                assert abs(float(group_video_after["attrs"]["at"]) - 7.0) < 0.15, group_video_after
                assert abs(float(group_audio_after["attrs"]["at"]) - 1.0) < 0.15, group_audio_after
                group_requests = film_edit_requests[group_request_start:]
                assert len(group_requests) == 1, group_requests
                group_ops = group_requests[0].get("ops") or []
                assert len(group_ops) == 2 and all(op.get("op") == "move" for op in group_ops), group_requests

                # One undo restores both tracks; a grouped gesture must not produce per-clip history.
                page.evaluate("window.filmStudioUndo()")
                page.wait_for_function("""() => {
                  const video = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  const audio = document.querySelector('#fs-timeline .fs-clip[data-clip="audio-1"]');
                  return !!video && !!audio &&
                    Math.abs(Number.parseFloat(video.style.left) - 6 * 36) < 1 &&
                    Math.abs(Number.parseFloat(audio.style.left)) < 1;
                }""", timeout=10000)
                group_undone = fd.read_project_film(project_id)
                undone_video = next(
                    item for track in group_undone["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-b"
                )
                undone_audio = next(
                    item for track in group_undone["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "audio-1"
                )
                assert abs(float(undone_video["attrs"]["at"]) - 6.0) < 0.001, undone_video
                assert abs(float(undone_audio["attrs"]["at"]) - 0.0) < 0.001, undone_audio

                def wait_for_group_selection():
                    page.wait_for_function("""() => {
                      const video = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                      const audio = document.querySelector('#fs-timeline .fs-clip[data-clip="audio-1"]');
                      return !!video && !!audio && video.classList.contains('fs-clip-selected') &&
                        audio.classList.contains('fs-clip-selected');
                    }""")

                # The cross-track drag above leaves both members selected. Locking one afterward
                # simulates stale selection and must be rejected by the drag preflight.
                wait_for_group_selection()
                page.evaluate("window.filmStudioSetClipLock('audio-1', true)")
                page.wait_for_function("""() => {
                  const clip = document.querySelector('#fs-timeline .fs-clip[data-clip="audio-1"]');
                  return !!clip && clip.querySelector('.fs-clip-lock-mark');
                }""")
                wait_for_group_selection()

                def assert_locked_group_drag_is_atomic():
                    before_blocked = fd.read_project_film(project_id)
                    locked_requests_before = len(film_edit_requests)
                    box = clip.bounding_box()
                    assert box, "clip-b bounding box unavailable"
                    start_x = box["x"] + box["width"] * 0.5
                    start_y = box["y"] + box["height"] * 0.5
                    page.mouse.move(start_x, start_y)
                    page.mouse.down()
                    page.mouse.move(start_x + max(24, box["width"] / 5), start_y, steps=6)
                    page.mouse.up()
                    page.wait_for_function("""() => {
                      const alert = document.querySelector('#film-studio-alert');
                      return !!alert && alert.textContent.includes('多选移动包含锁定');
                    }""")
                    after_blocked = fd.read_project_film(project_id)
                    assert after_blocked["rev"] == before_blocked["rev"]
                    assert len(film_edit_requests) == locked_requests_before, film_edit_requests[locked_requests_before:]
                    after_video = next(item for track in after_blocked["doc"]["tracks"] for item in track["clips"] if item["id"] == "clip-b")
                    after_audio = next(item for track in after_blocked["doc"]["tracks"] for item in track["clips"] if item["id"] == "audio-1")
                    before_video = next(item for track in before_blocked["doc"]["tracks"] for item in track["clips"] if item["id"] == "clip-b")
                    before_audio = next(item for track in before_blocked["doc"]["tracks"] for item in track["clips"] if item["id"] == "audio-1")
                    assert after_video["attrs"]["at"] == before_video["attrs"]["at"]
                    assert after_audio["attrs"]["at"] == before_audio["attrs"]["at"]

                assert_locked_group_drag_is_atomic()
                page.evaluate("window.filmStudioSetClipLock('audio-1', false)")
                page.wait_for_function("""() => {
                  const clip = document.querySelector('#fs-timeline .fs-clip[data-clip="audio-1"]');
                  return !!clip && !clip.querySelector('.fs-clip-lock-mark');
                }""")

                # Reuse the still-selected group, then lock its source track. The group must be
                # rejected before any move operation reaches the server.
                wait_for_group_selection()
                page.evaluate("window.filmStudioToggleTrackLock('audio-bed')")
                page.wait_for_function("""() => {
                  const button = document.querySelector('button[data-track-lock="audio-bed"]');
                  return !!button && button.textContent.trim() === '🔒';
                }""")
                wait_for_group_selection()
                assert_locked_group_drag_is_atomic()
                page.evaluate("window.filmStudioToggleTrackLock('audio-bed')")
                page.wait_for_function("""() => {
                  const button = document.querySelector('button[data-track-lock="audio-bed"]');
                  return !!button && button.textContent.trim() === '锁';
                }""")

                # Dropping a clip onto a locked destination track is rejected before POST /edit.
                page.evaluate("""() => {
                  window.prompt = () => 'Locked destination';
                }""")
                page.evaluate("window.filmStudioAddTrack()")
                page.wait_for_function("""() => [...document.querySelectorAll('#fs-timeline .fs-track-label')]
                  .some(label => label.textContent.trim() === 'Locked destination')""")
                destination_track_id = page.evaluate("""() => {
                  const label = [...document.querySelectorAll('#fs-timeline .fs-track-label')]
                    .find(node => node.textContent.trim() === 'Locked destination');
                  return label?.closest('.fs-track')?.getAttribute('data-track') || null;
                }""")
                assert destination_track_id
                page.evaluate("(id) => window.filmStudioToggleTrackLock(id)", destination_track_id)
                page.wait_for_function("""id => {
                  const button = document.querySelector('button[data-track-lock="' + id + '"]');
                  return !!button && button.textContent.trim() === '🔒';
                }""", arg=destination_track_id)

                clip.click()
                before_destination_reject = fd.read_project_film(project_id)
                destination_requests_before = len(film_edit_requests)
                destination_track_before = next(
                    track["id"] for track in before_destination_reject["doc"]["tracks"]
                    if any(item["id"] == "clip-b" for item in track["clips"])
                )
                destination_clip_before = next(
                    item for track in before_destination_reject["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-b"
                )
                source_box = clip.bounding_box()
                target_row = page.locator(
                    '#fs-timeline .fs-track[data-track="' + destination_track_id + '"]'
                )
                target_box = target_row.bounding_box()
                assert source_box and target_box, {"source": source_box, "target": target_box}
                source_x = source_box["x"] + source_box["width"] * 0.5
                source_y = source_box["y"] + source_box["height"] * 0.5
                target_y = target_box["y"] + target_box["height"] * 0.72
                page.mouse.move(source_x, source_y)
                page.mouse.down()
                page.mouse.move(source_x, target_y, steps=8)
                page.wait_for_function("""id => {
                  const row = document.querySelector('#fs-timeline .fs-track[data-track="' + id + '"]');
                  return !!row && row.classList.contains('fs-track-drop-invalid');
                }""", arg=destination_track_id, timeout=5000)
                page.mouse.up()
                page.wait_for_function("""() => {
                  const alert = document.querySelector('#film-studio-alert');
                  return !!alert && alert.textContent.includes('目标轨道') &&
                    alert.textContent.includes('已锁定');
                }""")
                after_destination_reject = fd.read_project_film(project_id)
                assert after_destination_reject["rev"] == before_destination_reject["rev"]
                assert len(film_edit_requests) == destination_requests_before, film_edit_requests[destination_requests_before:]
                destination_track_after = next(
                    track["id"] for track in after_destination_reject["doc"]["tracks"]
                    if any(item["id"] == "clip-b" for item in track["clips"])
                )
                destination_clip_after = next(
                    item for track in after_destination_reject["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-b"
                )
                assert destination_track_after == destination_track_before
                assert destination_clip_after["attrs"]["at"] == destination_clip_before["attrs"]["at"]
                page.evaluate("(id) => window.filmStudioToggleTrackLock(id)", destination_track_id)
                page.wait_for_function("""id => {
                  const button = document.querySelector('button[data-track-lock="' + id + '"]');
                  return !!button && button.textContent.trim() === '锁';
                }""", arg=destination_track_id)

                # Wait for the prior lock operation's HTTP response and FilmStore cleanup before starting a new pointer gesture.
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return !!state && state.busy === false && state.pendingTransaction === null &&
                    state.broken === false && !state.pendingExternalRevision;
                }""", timeout=10000)
                # React pointercancel must clear the active gesture without committing.
                before_cancel = fd.read_project_film(project_id)
                cancelled = page.evaluate("""() => {
                  const clip = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  if (!clip) throw new Error('clip-b missing before cancellation');
                  const base = {bubbles:true,cancelable:true,pointerId:77,button:0,buttons:1,clientX:Math.round(clip.getBoundingClientRect().x + 8),clientY:Math.round(clip.getBoundingClientRect().y + 8)};
                  clip.dispatchEvent(new PointerEvent('pointerdown', base));
                  const ownedAtStart = window.filmStudioOwnsClipGesturePointer?.(77) === true;
                  clip.dispatchEvent(new PointerEvent('pointercancel', {...base, buttons:0}));
                  return {ownedAtStart};
                }""")
                assert cancelled["ownedAtStart"] is True, cancelled
                page.wait_for_function("""() => {
                  const clip = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  return !!clip && !clip.classList.contains('fs-dragging') &&
                    window.filmStudioOwnsClipGesturePointer?.(77) !== true;
                }""")
                after_cancel = fd.read_project_film(project_id)
                assert after_cancel["rev"] == before_cancel["rev"]

                # React owns envelope pointer lifecycle; a cancelled gesture must not commit,
                # and clicking the envelope surface must not leak selection to its parent clip.
                envelope = page.locator('canvas[data-envelope-clip="audio-1"]')
                envelope.wait_for(state="visible")
                page.wait_for_function(
                    "() => [...document.querySelectorAll('canvas[data-envelope-clip]')].some(node => node.dataset.envelopeClip === 'audio-1' && node.dataset.reactEnvelopePainted === 'true')",
                    timeout=5_000,
                )

                # Enable the real Studio tool, then use real Chromium pointer input on the React canvas.
                page.locator('[data-film-action="envelope-mode"]:visible').click()
                page.wait_for_function(
                    "() => window.filmStudioStore?.getField('envelopeMode') === true",
                    timeout=5_000,
                )
                box = envelope.bounding_box()
                assert box and box["width"] > 80 and box["height"] > 16, box
                before_doc = fd.read_project_film(project_id)
                original = next(
                    clip for track in before_doc["doc"]["tracks"]
                    for clip in track["clips"] if clip["id"] == "audio-1"
                )
                assert "volume_envelope" not in original["attrs"], original
                revision_before = before_doc["rev"]
                edit_count_before = len(film_edit_requests)

                start_x = box["x"] + box["width"] * 0.50
                start_y = box["y"] + box["height"] * 0.90
                pointer_target = page.evaluate("""() => {
                  const canvas = document.querySelector('canvas[data-envelope-clip="audio-1"]');
                  if (!canvas) return {canvasFound:false};
                  const rect = canvas.getBoundingClientRect();
                  const x = rect.left + rect.width * 0.5;
                  const y = rect.top + rect.height * 0.9;
                  const hit = document.elementFromPoint(x, y);
                  window.__envelopePointerTrace = [];
                  for (const type of ["pointerdown","pointermove","pointerup","pointercancel"]) {
                    canvas.addEventListener(type, event => {
                      window.__envelopePointerTrace.push({
                        type:event.type, pointerId:event.pointerId, button:event.button,
                        x:event.clientX, y:event.clientY, isPrimary:event.isPrimary
                      });
                    }, true);
                  }
                  return {
                    canvasFound:true,
                    rect:{left:rect.left,top:rect.top,width:rect.width,height:rect.height},
                    pointerEvents:getComputedStyle(canvas).pointerEvents,
                    hitIsCanvas:hit===canvas,
                    hitTag:hit?.tagName || null,
                    hitClass:String(hit?.className || "")
                  };
                }""")
                assert pointer_target["canvasFound"] and pointer_target["pointerEvents"] == "auto" and pointer_target["hitIsCanvas"], pointer_target
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const gesture = state?.timelineEnvelopeGesture;
                  const preview = state?.timelineEnvelopePreview;
                  return gesture?.clipId === "audio-1" && gesture.points.length === 3 &&
                    gesture.changed === true && !Object.prototype.hasOwnProperty.call(gesture, "canvas") &&
                    preview?.clipId === "audio-1" && preview.points.length === 3;
                }""", timeout=5_000)
                inserted_point = page.evaluate("""() => {
                  const point = window.filmStudioStore?.getSnapshot()?.timelineEnvelopeGesture?.points?.[1];
                  return point ? {t:point.t,v:point.v} : null;
                }""")
                assert inserted_point and 0 <= inserted_point["v"] <= 1, inserted_point

                # Moving the inserted point changes only the React preview. No request or disk revision yet.
                page.mouse.move(start_x + 22, start_y - 8, steps=5)
                page.wait_for_function("""before => {
                  const point = window.filmStudioStore?.getSnapshot()?.timelineEnvelopeGesture?.points?.[1];
                  return !!point && Number.isFinite(point.t) && Number.isFinite(point.v) &&
                    (Math.abs(point.t - before.t) > 0.001 || Math.abs(point.v - before.v) > 0.001);
                }""", arg=inserted_point, timeout=5_000)
                preview = page.evaluate("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const point = state?.timelineEnvelopeGesture?.points?.[1];
                  return point ? {t:point.t,v:point.v} : null;
                }""")
                assert preview and preview["t"] > 0 and 0 <= preview["v"] <= 1, preview
                assert abs(preview["t"] - inserted_point["t"]) > 0.001, {"before":inserted_point,"after":preview}
                assert abs(preview["v"] - inserted_point["v"]) > 0.001, {"before":inserted_point,"after":preview}
                assert len(film_edit_requests) == edit_count_before
                assert fd.read_project_film(project_id)["rev"] == revision_before

                with page.expect_response("**/api/editor/film/edit") as edit_response:
                    page.mouse.up()
                assert edit_response.value.status == 200
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state?.timelineEnvelopeGesture === null &&
                    state?.timelineEnvelopePreview === null &&
                    state?.busy === false && state?.pendingTransaction === null;
                }""", timeout=10_000)
                assert len(film_edit_requests) == edit_count_before + 1, film_edit_requests
                operations = film_edit_requests[-1].get("ops") or []
                assert len(operations) == 1, operations
                operation = operations[0]
                assert operation.get("op") == "set", operation
                assert operation.get("clip") == "audio-1" and operation.get("field") == "volume_envelope", operation
                saved = fd.read_project_film(project_id)
                assert saved["rev"] != revision_before
                saved_clip = next(
                    clip for track in saved["doc"]["tracks"]
                    for clip in track["clips"] if clip["id"] == "audio-1"
                )
                points = json.loads(saved_clip["attrs"]["volume_envelope"])
                assert len(points) == 3, points
                assert 0 < points[1]["t"] < fd.clip_duration(saved_clip), points
                assert 0 <= points[1]["v"] <= 1, points
                assert abs(points[1]["t"] - round(preview["t"], 3)) <= 0.002, {"saved":points,"preview":preview}
                assert abs(points[1]["v"] - round(preview["v"], 3)) <= 0.002, {"saved":points,"preview":preview}
                assert not page_errors, f"browser JavaScript errors: {page_errors}"

                # The one pointer gesture is exactly one undo unit.
                with page.expect_response("**/api/editor/film/undo") as undo_response:
                    page.evaluate("window.filmStudioUndo()")
                assert undo_response.value.status == 200
                undone = fd.read_project_film(project_id)
                undone_clip = next(
                    clip for track in undone["doc"]["tracks"]
                    for clip in track["clips"] if clip["id"] == "audio-1"
                )
                assert "volume_envelope" not in undone_clip["attrs"], undone_clip
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
