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
                # AddTrack inserts at index 0. Moving the selected group into that first row shifts both members up one track.
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

                # Cross-track preview renders a React ghost for each member in its translated destination row.
                cross_before = fd.read_project_film(project_id)
                cross_request_count = len(film_edit_requests)
                cross_video_before_left = clip.evaluate("(node) => node.style.left")
                cross_video_box = clip.bounding_box()
                target_destination_box = page.locator('.fs-track[data-track="' + cross_track_destination_id + '"]').bounding_box()
                assert cross_video_box and target_destination_box
                cross_start_x = cross_video_box["x"] + cross_video_box["width"] / 2
                cross_start_y = cross_video_box["y"] + cross_video_box["height"] / 2
                cross_target_y = target_destination_box["y"] + target_destination_box["height"] / 2
                page.mouse.move(cross_start_x, cross_start_y)
                page.mouse.down()
                page.mouse.move(cross_start_x + 36, cross_target_y, steps=10)
                page.wait_for_function("""destinationId => {
                  const videoGhost = document.querySelector('[data-gesture-ghost-for="clip-b"][data-gesture-target-track="' + destinationId + '"]');
                  const audioGhost = document.querySelector('[data-gesture-ghost-for="audio-1"][data-gesture-target-track="video-main"]');
                  const state = window.filmStudioStore?.getSnapshot();
                  return !!videoGhost && !!audioGhost &&
                    state?.timelineGesturePreview?.['clip-b']?.targetTrackId === destinationId &&
                    state?.timelineGesturePreview?.['audio-1']?.targetTrackId === 'video-main';
                }""", arg=cross_track_destination_id, timeout=5000)
                cross_video_ghost = page.locator('[data-gesture-ghost-for="clip-b"][data-gesture-target-track="audio-bed"]')
                assert cross_video_ghost.get_attribute("aria-hidden") == "true"
                assert cross_video_ghost.evaluate("(node) => node.style.left") != cross_video_before_left
                assert len(film_edit_requests) == cross_request_count
                assert fd.read_project_film(project_id)["rev"] == cross_before["rev"]

                page.mouse.up()
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state?.timelineGestureContext === null && state?.timelineGesturePreview === null;
                }""", timeout=10000)
                cross_after = fd.read_project_film(project_id)
                def located_track(document: dict, clip_id: str) -> str:
                    return next(track["id"] for track in document["doc"]["tracks"]
                      if any(item["id"] == clip_id for item in track["clips"]))
                assert located_track(cross_after, "clip-b") == cross_track_destination_id
                assert located_track(cross_after, "audio-1") == "video-main"
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
                    "() => document.querySelector('canvas[data-envelope-clip=\\\"audio-1\\\"]')?.dataset.reactEnvelopePainted === 'true'",
                    timeout=5000,
                )
                # The destination-track lock transaction must also release FilmStore before the envelope gesture.
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return !!state && state.busy === false && state.pendingTransaction === null &&
                    state.broken === false && !state.pendingExternalRevision;
                }""", timeout=10000)
                before_envelope_cancel = fd.read_project_film(project_id)
                cancelled_envelope = page.evaluate("""() => {
                  const canvas = document.querySelector('canvas[data-envelope-clip="audio-1"]');
                  if (!canvas) throw new Error('audio envelope canvas missing');
                  const rect = canvas.getBoundingClientRect();
                  const point = {bubbles:true,cancelable:true,pointerId:78,button:0,buttons:1,
                    clientX:Math.round(rect.left + rect.width * 0.55),
                    clientY:Math.round(rect.top + rect.height * 0.4)};
                  canvas.dispatchEvent(new PointerEvent('pointerdown', point));
                  const ownedAtStart = window.filmStudioOwnsEnvelopeGesturePointer?.(78) === true;
                  canvas.dispatchEvent(new PointerEvent('pointermove', {...point, clientX:point.clientX + 12, clientY:point.clientY + 8}));
                  const preview = window.filmStudioStore?.getSnapshot()?.timelineEnvelopePreview;
                  const previewStored = preview?.clipId === 'audio-1' && Array.isArray(preview.points) && preview.points.length >= 2;
                  canvas.dispatchEvent(new PointerEvent('pointercancel', {...point, buttons:0}));
                  const previewCleared = window.filmStudioStore?.getSnapshot()?.timelineEnvelopePreview === null;
                  document.dispatchEvent(new PointerEvent('pointermove', {...point, clientX:point.clientX + 24}));
                  document.dispatchEvent(new PointerEvent('pointerup', {...point, buttons:0}));
                  const selectedBeforeClick = window.filmStudioStore?.getSnapshot()?.selectedId;
                  canvas.dispatchEvent(new MouseEvent('click', {bubbles:true,cancelable:true,button:0}));
                  const selectedAfterClick = window.filmStudioStore?.getSnapshot()?.selectedId;
                  return {ownedAtStart, previewStored, previewCleared, selectedBeforeClick, selectedAfterClick};
                }""")
                assert cancelled_envelope["ownedAtStart"] is True, cancelled_envelope
                assert cancelled_envelope["previewStored"] is True, cancelled_envelope
                assert cancelled_envelope["previewCleared"] is True, cancelled_envelope
                assert cancelled_envelope["selectedBeforeClick"] == "clip-b", cancelled_envelope
                assert cancelled_envelope["selectedAfterClick"] == "clip-b", cancelled_envelope
                page.wait_for_function("() => window.filmStudioOwnsEnvelopeGesturePointer?.(78) !== true")

                # Inject deterministic peaks through the actual FilmStore field so the React waveform
                # canvas is exercised without network/audio-decoder variability in this fixture.
                waveform_fixture = page.evaluate("""() => {
                  const canvas = document.querySelector('canvas[data-waveform-clip="audio-1"]');
                  const store = window.filmStudioStore;
                  if (!canvas || !store) throw new Error('React waveform canvas or FilmStore missing');
                  const key = canvas.dataset.waveformKey;
                  if (!key) throw new Error('audio waveform source key missing');
                  const peaks = new Uint8Array(128);
                  for (let i = 0; i < peaks.length; i++) peaks[i] = (i % 16) * 16;
                  const current = store.getField('timelineWaveformSources') || {};
                  store.setField('timelineWaveformSources', {...current, [key]: {duration:20, peaks}});
                  return {key, width:canvas.width, height:canvas.height};
                }""")
                page.wait_for_function(
                    """key => {
                      const canvas = document.querySelector('canvas[data-waveform-clip="audio-1"]');
                      return !!canvas && canvas.dataset.reactWaveformPainted === 'true' &&
                        canvas.dataset.waveformKey === key;
                    }""",
                    arg=waveform_fixture["key"],
                    timeout=5000,
                )
                waveform_pixels = page.locator('canvas[data-waveform-clip="audio-1"]').evaluate("""canvas => {
                  const ctx = canvas.getContext('2d');
                  const data = ctx.getImageData(0,0,canvas.width,canvas.height).data;
                  let painted = 0;
                  for (let i = 3; i < data.length; i += 4) if (data[i] > 0) painted++;
                  return {painted,width:canvas.width,height:canvas.height,title:canvas.title};
                }""")
                assert waveform_pixels["painted"] > 0, waveform_pixels
                assert waveform_pixels["title"].startswith("素材波形"), waveform_pixels
                after_envelope_cancel = fd.read_project_film(project_id)
                assert after_envelope_cancel["rev"] == before_envelope_cancel["rev"]
                # The drag selected clip-b. Restore clip-a so the existing
                # external-write assertion observes the field that is changed next.
                page.evaluate("window.filmStudioSelectClip('clip-a')")
                page.wait_for_function(
                    "() => { const field = document.querySelector('#fs-field-speed'); return !!field && field.value === '2'; }"
                )

                # Edit cubic Bezier handles in the real React Inspector and verify FilmDocument persistence.
                page.locator("#fs-keyframe-property").select_option("x")
                easing_select = page.get_by_label("关键帧 1 缓动")
                easing_select.select_option("bezier")
                page.locator('[data-bezier-editor="keyframe-1"]').wait_for(state="visible")
                p1 = page.locator('[data-bezier-handle="p1"]')
                p1_box = p1.bounding_box()
                assert p1_box and p1_box["width"] > 0, p1_box
                p1_x = p1_box["x"] + p1_box["width"] / 2
                p1_y = p1_box["y"] + p1_box["height"] / 2
                page.mouse.move(p1_x, p1_y)
                page.mouse.down()
                page.mouse.move(p1_x - 10, p1_y - 7, steps=4)
                page.mouse.up()
                page.get_by_label("关键帧 1 y1").fill("0.33")
                page.wait_for_function(
                    """() => {
                      const field = document.querySelector('#fs-field-keyframes');
                      if (!field) return false;
                      try {
                        const channels = JSON.parse(field.value || '[]');
                        const channel = channels.find(item => item.property === 'x');
                        const easing = channel?.keyframes?.[0]?.easing;
                        return easing?.type === 'bezier' && Math.abs(Number(easing.y1) - 0.33) < 0.005;
                      } catch (_) { return false; }
                    }"""
                )
                page.locator("#fs-apply-clip-inspector").click()
                persisted_keyframes = None
                for _ in range(40):
                    doc_after_bezier = fd.read_project_film(project_id)["doc"]
                    clip_a = next(
                        item for track in doc_after_bezier["tracks"]
                        for item in track["clips"] if item["id"] == "clip-a"
                    )
                    try:
                        channels = json.loads(clip_a["attrs"]["data-film-keyframes"])
                        channel_x = next(item for item in channels if item["property"] == "x")
                        easing = channel_x["keyframes"][0]["easing"]
                        if isinstance(easing, dict) and easing.get("type") == "bezier" and abs(float(easing["y1"]) - 0.33) < 0.005:
                            persisted_keyframes = easing
                            break
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        pass
                    page.wait_for_timeout(100)
                if persisted_keyframes is None:
                    print("BEZIER_STORED=" + str(clip_a.get("attrs", {}).get("data-film-keyframes")))
                    print("BEZIER_LOCAL=" + page.locator("#fs-field-keyframes").input_value())
                    print("BEZIER_INSPECTOR_FEEDBACK=" + (page.locator("#fs-clip-inspector-feedback").inner_text() if page.locator("#fs-clip-inspector-feedback").count() else ""))
                    print("BEZIER_STUDIO_STATUS=" + (page.locator("#film-studio-alert").inner_text() if page.locator("#film-studio-alert").count() else ""))
                    print("BEZIER_EDIT_RESPONSES=" + repr(film_edit_responses))
                assert persisted_keyframes is not None, clip_a.get("attrs", {}).get("data-film-keyframes")
                assert 0 <= persisted_keyframes["x1"] <= persisted_keyframes["x2"] <= 1, persisted_keyframes
                assert 0 <= persisted_keyframes["y1"] <= 1 and 0 <= persisted_keyframes["y2"] <= 1, persisted_keyframes

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

                # Undo/redo, project switching and export are separate entry points.
                # The watcher can refresh the alert while the dirty draft is held, so
                # assert the hard gate (no history request) and preserve the active snapshot.
                before_undo_gate = page.evaluate("""() => {
                  const snapshot = window.filmStudioStore?.getSnapshot();
                  const alert = document.querySelector('#film-studio-alert');
                  window.__undoGateClickCount = 0;
                  document.addEventListener('click', event => {
                    if (event.target?.closest?.('[data-film-action="undo"]')) window.__undoGateClickCount += 1;
                  }, true);
                  return {
                    selectedId: snapshot?.selectedId,
                    selectedIds: snapshot?.selectedIds,
                    revision: snapshot?.revision,
                    alert: alert?.textContent || '',
                  };
                }""")
                assert before_undo_gate["selectedId"] == "clip-a", before_undo_gate
                assert before_undo_gate["selectedIds"] == ["clip-a"], before_undo_gate
                active_revision_before_undo = before_undo_gate["revision"]
                history_count_before_undo = len(film_history_requests)
                page.locator('[data-film-action="undo"]:visible').click()
                page.wait_for_function("window.__undoGateClickCount > 0", timeout=2000)
                page.wait_for_timeout(100)
                assert len(film_history_requests) == history_count_before_undo, {
                    "before": before_undo_gate, "history_requests": film_history_requests,
                }
                gate_state = page.evaluate("""() => ({
                  title: document.querySelector('#fs-field-title')?.value,
                  speed: document.querySelector('#fs-field-speed')?.value,
                  alert: document.querySelector('#film-studio-alert')?.textContent || '',
                  revision: window.filmStudioStore?.getSnapshot()?.revision,
                })""")
                assert gate_state["title"] == "Local unsaved draft", gate_state
                assert gate_state["speed"] == "2.5", gate_state
                assert gate_state["revision"] == active_revision_before_undo, gate_state
                assert fd.read_project_film(project_id)["rev"] == fd.revision_of(changed_again)
                assert ("再撤销/重做" in gate_state["alert"] or
                        "未提交的属性草稿" in gate_state["alert"] or
                        "暂存" in gate_state["alert"]), gate_state
                # Exercise the same delegated render action deterministically after
                # verifying the dirty-form gate above; a React toolbar rerender must
                # not make this assertion depend on which visible toolbar node won.
                render_status = page.evaluate("""() => {
                  window.filmStudioRender();
                  return document.querySelector('#film-studio-alert')?.textContent || '';
                }""")
                assert "避免输出旧 revision" in render_status, {
                    "status": render_status,
                    "render_requests": film_render_requests,
                }
                assert film_render_requests == [], film_render_requests
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
                page.locator('[data-film-action="reload"]:visible').click()
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
                # The inspector changing back is not enough: the authoritative timeline
                # surface must re-render all repaired IDs before further wheel input.
                page.wait_for_function(
                    """() => {
                      const snapshot = window.filmStudioStore?.getSnapshot();
                      const film = snapshot?.film;
                      const modelHasClips = ["clip-a","clip-b","audio-1"].every(id =>
                        (film?.tracks || []).some(track => (track.clips || []).some(clip => clip.id === id)));
                      const domHasClips = ["clip-a","clip-b","audio-1"].every(id =>
                        document.querySelector('#fs-timeline .fs-clip[data-clip="' + id + '"]'));
                      return modelHasClips && domHasClips;
                    }""",
                    timeout=5000,
                )
                assert fd.read_project_film(project_id)["rev"] == fd.revision_of(repaired)

                def ctrl_wheel(client_x: float, client_y: float, delta_y: float, count: int = 1) -> None:
                    page.mouse.move(client_x, client_y)
                    page.keyboard.down("Control")
                    try:
                        for _ in range(count):
                            page.mouse.wheel(0, delta_y)
                    finally:
                        page.keyboard.up("Control")

                # Use genuine Chromium input after React installs the non-passive zoom handler.
                page.wait_for_function(
                    "() => document.querySelector('#fs-timeline-scroll')?.dataset.reactZoomReady === 'true'",
                    timeout=10000,
                )
                host_before_zoom = page.evaluate("""() => {
                  const host = document.querySelector('#fs-timeline');
                  const surface = host?.querySelector('.fs-react-timeline-surface');
                  const scroll = document.querySelector('#fs-timeline-scroll');
                  return {
                    hostInlineWidth: host?.style.width || '',
                    surfaceWidth: surface ? surface.getBoundingClientRect().width : 0,
                    surfaceInlineWidth: surface?.style.width || '',
                    scrollWidth: scroll?.scrollWidth || 0,
                  };
                }""")
                assert host_before_zoom["hostInlineWidth"] == "", host_before_zoom
                assert host_before_zoom["surfaceWidth"] > 0, host_before_zoom
                assert host_before_zoom["scrollWidth"] >= host_before_zoom["surfaceWidth"], host_before_zoom
                scroll_box = page.locator("#fs-timeline-scroll").bounding_box()
                assert scroll_box, "timeline scroll box unavailable before zoom"
                wheel_client_x = scroll_box["x"] + max(120, min(200, scroll_box["width"] - 24))
                wheel_client_y = scroll_box["y"] + min(40, scroll_box["height"] / 2)
                ctrl_wheel(wheel_client_x, wheel_client_y, -100, count=4)
                page.wait_for_function(
                    """() => {
                      const clip = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                      return !!clip && Number.parseFloat(clip.style.width) > 420;
                    }"""
                )

                # Ctrl-wheel zoom is non-passive and keeps the same timeline time under the pointer.
                zoom_before = page.evaluate("""() => {
                  const scroll = document.querySelector('#fs-timeline-scroll');
                  const clip = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  if (!scroll || !clip) throw new Error('timeline scroll or clip-b missing before zoom');
                  const rect = scroll.getBoundingClientRect();
                  const localX = Math.max(120, Math.min(200, scroll.clientWidth - 24));
                  const width = Number.parseFloat(clip.style.width);
                  const pixelsPerSecond = width / 5;
                  return {
                    clientX: rect.left + localX, clientY: rect.top + Math.min(40, rect.height / 2),
                    localX, width, pixelsPerSecond,
                    storePixelsPerSecond: window.filmStudioStore?.getField("pixelsPerSecond"),
                    hostInlineWidth: document.querySelector('#fs-timeline')?.style.width || '',
                    surfaceWidth: document.querySelector('#fs-timeline .fs-react-timeline-surface')?.getBoundingClientRect().width || 0,
                    scrollWidth: scroll.scrollWidth,
                    anchorTime: (scroll.scrollLeft + localX - 132) / pixelsPerSecond
                  };
                }""")
                page.evaluate("""() => {
                  const scroll = document.querySelector('#fs-timeline-scroll');
                  window.__filmStudioWheelPrevented = false;
                  scroll.addEventListener("wheel", event => {
                    if (event.ctrlKey || event.metaKey) window.__filmStudioWheelPrevented = event.defaultPrevented;
                  }, {once:true});
                }""")
                ctrl_wheel(zoom_before["clientX"], zoom_before["clientY"], -100)
                page.wait_for_function(
                    "() => window.__filmStudioWheelPrevented === true",
                    timeout=5000,
                )
                page.wait_for_function(
                    """oldWidth => {
                      const clip = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                      return !!clip && Number.parseFloat(clip.style.width) > oldWidth + 1;
                    }""",
                    arg=zoom_before["width"],
                )
                zoom_after = page.evaluate("""() => {
                  const scroll = document.querySelector('#fs-timeline-scroll');
                  const clip = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  return {
                    width: Number.parseFloat(clip.style.width),
                    pixelsPerSecond: Number.parseFloat(clip.style.width) / 5,
                    storePixelsPerSecond: window.filmStudioStore?.getField("pixelsPerSecond"),
                    hostInlineWidth: document.querySelector('#fs-timeline')?.style.width || '',
                    surfaceWidth: document.querySelector('#fs-timeline .fs-react-timeline-surface')?.getBoundingClientRect().width || 0,
                    scrollWidth: scroll.scrollWidth,
                    scrollLeft: scroll.scrollLeft
                  };
                }""")
                assert abs(zoom_before["storePixelsPerSecond"] - zoom_before["pixelsPerSecond"]) < 0.02, zoom_before
                assert abs(zoom_after["storePixelsPerSecond"] - zoom_after["pixelsPerSecond"]) < 0.02, zoom_after
                assert zoom_before["hostInlineWidth"] == "" and zoom_after["hostInlineWidth"] == "", {
                    "before": zoom_before, "after": zoom_after,
                }
                assert zoom_after["surfaceWidth"] > zoom_before["surfaceWidth"], {
                    "before": zoom_before, "after": zoom_after,
                }
                assert zoom_after["scrollWidth"] >= zoom_after["surfaceWidth"], zoom_after
                assert abs(zoom_after["pixelsPerSecond"] / zoom_before["pixelsPerSecond"] - 1.25) < 0.02, zoom_after
                expected_scroll_left = max(
                    0,
                    132 + zoom_before["anchorTime"] * zoom_after["pixelsPerSecond"] - zoom_before["localX"],
                )
                assert abs(zoom_after["scrollLeft"] - expected_scroll_left) < 4, {
                    "before": zoom_before, "after": zoom_after, "expected_scroll_left": expected_scroll_left
                }

                # Reach the zoom cap so the timeline must scroll; then verify auto-scroll while a clip is held at the right edge.
                ctrl_wheel(zoom_before["clientX"], zoom_before["clientY"], -100, count=5)
                page.wait_for_function(
                    """() => {
                      const clip = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                      return !!clip && Number.parseFloat(clip.style.width) > 590;
                    }"""
                )
                auto_scroll_start = page.evaluate("""() => {
                  const scroll = document.querySelector('#fs-timeline-scroll');
                  const clip = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  const left = Number.parseFloat(clip.style.left);
                  const maxScroll = Math.max(0, scroll.scrollWidth - scroll.clientWidth);
                  // Place the clip inside the viewport while retaining rightward scroll headroom.
                  const clipOffset = Math.max(80, Math.min(220, scroll.clientWidth * 0.35));
                  scroll.scrollLeft = Math.max(0, Math.min(maxScroll, 132 + left - clipOffset));
                  const sr = scroll.getBoundingClientRect();
                  const cr = clip.getBoundingClientRect();
                  return {
                    scrollLeft: scroll.scrollLeft,
                    startX: Math.max(sr.left + 8, Math.min(cr.right - 10, cr.left + 20)),
                    edgeX: sr.right - 6,
                    y: cr.top + cr.height / 2
                  };
                }""")
                assert auto_scroll_start["startX"] < auto_scroll_start["edgeX"] - 24, auto_scroll_start
                page.mouse.move(auto_scroll_start["startX"], auto_scroll_start["y"])
                page.mouse.down()
                page.mouse.move(auto_scroll_start["edgeX"], auto_scroll_start["y"], steps=10)
                page.wait_for_function(
                    """oldScrollLeft => {
                      const scroll = document.querySelector('#fs-timeline-scroll');
                      return !!scroll && scroll.scrollLeft > oldScrollLeft + 8;
                    }""",
                    arg=auto_scroll_start["scrollLeft"],
                    timeout=5000,
                )
                page.mouse.up()
                for _ in range(30):
                    doc_after_auto_scroll = fd.read_project_film(project_id)["doc"]
                    moved_after_auto_scroll = next(
                        item for track in doc_after_auto_scroll["tracks"]
                        for item in track["clips"] if item["id"] == "clip-b"
                    )
                    if float(moved_after_auto_scroll["attrs"]["at"]) > 5.1:
                        break
                    page.wait_for_timeout(100)
                assert float(moved_after_auto_scroll["attrs"]["at"]) > 5.1, moved_after_auto_scroll
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


def test_react_clip_pointercancel_discards_preview_without_writing_film(tmp_path, monkeypatch):
    """A cancelled React-owned clip drag clears preview and cannot commit after trailing pointer events."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-react-clip-pointercancel"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    before = fd.read_project_film(project_id)
    assert before["exists"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page_errors: list[str] = []
            film_edit_requests: list[dict] = []
            def capture_film_edit_request(request):
                if request.url.endswith("/api/editor/film/edit"):
                    try:
                        film_edit_requests.append(json.loads(request.post_data or "{}"))
                    except (TypeError, ValueError):
                        film_edit_requests.append({"_invalid_json": True})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("request", capture_film_edit_request)
            try:
                page.set_default_navigation_timeout(10_000)
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
                page.evaluate("""(pid) => {
                  const select = document.querySelector('#editor-project');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                  select.dispatchEvent(new Event('change', {bubbles: true}));
                }""", project_id)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                clip = page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]')
                clip.wait_for(state="visible", timeout=5000)
                box = clip.bounding_box()
                assert box, "clip-b bounding box unavailable"
                original = clip.evaluate("(node) => ({left:node.style.left,width:node.style.width})")
                before_cancel = fd.read_project_film(project_id)
                request_count_before = len(film_edit_requests)

                # Start/move with real Chromium mouse input, then deliver pointercancel
                # to the actual active pointer ID so React takes its normal cancellation path.
                page.evaluate("""() => {
                  window.__reactClipPointerId = null;
                  const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  node.addEventListener("pointerdown", event => {
                    window.__reactClipPointerId = event.pointerId;
                  }, {capture:true, once:true});
                }""")
                start_x = box["x"] + box["width"] * 0.5
                start_y = box["y"] + box["height"] * 0.5
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(start_x + 28, start_y, steps=6)
                page.wait_for_function(
                    """oldLeft => {
                      const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                      return !!node && node.dataset.reactGesturePreview === "true" && node.style.left !== oldLeft;
                    }""",
                    arg=original["left"],
                    timeout=5000,
                )
                pointer_id = page.evaluate("window.__reactClipPointerId")
                assert isinstance(pointer_id, int), pointer_id
                preview_left = clip.evaluate("(node) => node.style.left")
                assert preview_left != original["left"], {"before": original, "preview": preview_left}
                assert len(film_edit_requests) == request_count_before
                assert fd.read_project_film(project_id)["rev"] == before_cancel["rev"]

                page.evaluate("""({pointerId, x, y}) => {
                  const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  node.dispatchEvent(new PointerEvent("pointercancel", {
                    bubbles:true, cancelable:true, pointerId, pointerType:"mouse",
                    button:0, buttons:0, clientX:x, clientY:y
                  }));
                }""", {"pointerId": pointer_id, "x": start_x + 28, "y": start_y})
                page.wait_for_function(
                    """pointerId => {
                      const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                      const owns = window.filmStudioOwnsClipGesturePointer?.(pointerId) === true;
                      return !!node && node.dataset.reactGesturePreview === "false" && !owns;
                    }""",
                    arg=pointer_id,
                    timeout=5000,
                )
                page.wait_for_function(
                    """() => {
                      const state = window.filmStudioStore?.getSnapshot();
                      return !!state && state.timelineGestureContext === null &&
                        state.timelineActiveDragIds === null && state.timelineSnapGuide === null &&
                        state.timelineDropTarget === null && state.timelineGesturePreview === null;
                    }""",
                    timeout=5000,
                )
                # Later pointer events from the still-held mouse must not resurrect the cancelled drag.
                page.mouse.move(start_x + 48, start_y, steps=2)
                page.mouse.up()
                page.wait_for_timeout(50)
                after_cancel = fd.read_project_film(project_id)
                assert after_cancel["rev"] == before_cancel["rev"]
                assert len(film_edit_requests) == request_count_before, film_edit_requests
                cancelled_geometry = clip.evaluate("(node) => ({left:node.style.left,width:node.style.width})")
                assert cancelled_geometry == original, {"before": original, "after": cancelled_geometry}
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_react_timeline_context_menu_opens_and_dismisses_without_writing_film(tmp_path, monkeypatch):
    """Right-click menu has one React surface and Escape only intercepts an open menu."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-react-context-menu-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    before = fd.read_project_film(project_id)
    assert before["exists"]

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
            try:
                page.set_default_navigation_timeout(10_000)
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
                page.evaluate("""(pid) => {
                  const select = document.querySelector('#editor-project');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                  select.dispatchEvent(new Event('change', {bubbles: true}));
                }""", project_id)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                target = page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]')
                target.wait_for(state="visible", timeout=5000)
                bounds = target.bounding_box()
                assert bounds, "clip-b bounding box unavailable"
                before_menu = fd.read_project_film(project_id)

                # Gesture context is an all-or-nothing ID-only snapshot. A bad member or
                # duplicate member must reject the whole group rather than silently move a subset.
                context_validation = page.evaluate("""() => {
                  const store = window.filmStudioStore;
                  const member = {id:"clip-probe",originalAt:0,originalEnd:1,trackId:"track-a",trackIndex:0};
                  const base = {
                    id:"clip-probe",mode:"move",pointerId:17,startX:1,startY:1,startScrollLeft:0,
                    active:false,members:[member],trackOrder:["track-a"],trackId:"track-a",
                    originalAt:0,originalEnd:1,originalTrackIndex:0,targetTrackId:"track-a"
                  };
                  store.setField("timelineGestureContext",base);
                  const accepted = store.getField("timelineGestureContext");
                  store.setField("timelineGestureContext",{...base,pointerId:-1});
                  const invalidPointerRejected = store.getField("timelineGestureContext") === null;
                  store.setField("timelineGestureContext",{...base,members:[member,{id:"clip-other",originalAt:1,originalEnd:2,trackId:"track-a"}]});
                  const malformedMemberRejected = store.getField("timelineGestureContext") === null;
                  store.setField("timelineGestureContext",{...base,members:[member,{...member}]});
                  const duplicateMemberRejected = store.getField("timelineGestureContext") === null;
                  store.setField("timelineGestureContext",{
                    ...base,mode:"roll",leftClipId:"clip-left",rightClipId:"clip-right",
                    rightAt:1,leftDuration:1
                  });
                  const incompleteRollRejected = store.getField("timelineGestureContext") === null;
                  store.setField("timelineGestureContext",{...base,originalEnd:2});
                  const mismatchedBaselineRejected = store.getField("timelineGestureContext") === null;
                  store.setField("timelineGestureContext",{
                    ...base,mode:"trim-start",members:[member,{...member,id:"clip-extra"}]
                  });
                  const multiMemberTrimRejected = store.getField("timelineGestureContext") === null;
                  store.setField("timelineGestureContext",{
                    ...base,mode:"roll",leftClipId:"clip-left",rightClipId:"clip-right",
                    rightAt:1,leftDuration:1,rightDuration:1
                  });
                  const mismatchedRollIdentityRejected = store.getField("timelineGestureContext") === null;
                  store.setField("timelineGestureContext",{
                    ...base,mode:"slide",leftClipId:"clip-left",centerClipId:"clip-other",rightClipId:"clip-right",
                    centerAt:0,rightAt:1,leftDuration:1,centerDuration:1,rightDuration:1
                  });
                  const mismatchedSlideIdentityRejected = store.getField("timelineGestureContext") === null;
                  store.setField("timelineGestureContext",{
                    ...base,mode:"slide",leftClipId:"clip-left",centerClipId:"clip-probe",rightClipId:"clip-right",
                    centerAt:0,rightAt:1,leftDuration:1,centerDuration:1,rightDuration:1
                  });
                  const completeSlideAccepted = store.getField("timelineGestureContext")?.mode === "slide";
                  store.setField("timelineGestureContext",null);
                  return {
                    accepted:accepted ? accepted.members.map(item=>item.id) : null,
                    invalidPointerRejected,malformedMemberRejected,duplicateMemberRejected,
                    incompleteRollRejected,mismatchedBaselineRejected,multiMemberTrimRejected,
                    mismatchedRollIdentityRejected,mismatchedSlideIdentityRejected,completeSlideAccepted,
                    noDomRefs:accepted ? !Object.keys(accepted).some(key=>/element|canvas|row/i.test(key)) &&
                      accepted.members.every(item=>!Object.keys(item).some(key=>/element|canvas|row/i.test(key))) : false
                  };
                }""")
                assert context_validation == {
                    "accepted":["clip-probe"],
                    "invalidPointerRejected":True,
                    "malformedMemberRejected":True,
                    "duplicateMemberRejected":True,
                    "incompleteRollRejected":True,
                    "mismatchedBaselineRejected":True,
                    "multiMemberTrimRejected":True,
                    "mismatchedRollIdentityRejected":True,
                    "mismatchedSlideIdentityRejected":True,
                    "completeSlideAccepted":True,
                    "noDomRefs":True,
                }, context_validation

                page.mouse.click(bounds["x"] + 12, bounds["y"] + 8, button="right")
                menu = page.locator('.fs-context-menu[data-react-context-menu="true"]')
                menu.wait_for(state="visible", timeout=5000)
                assert page.locator(".fs-context-menu").count() == 1, "native and React menus must not coexist"
                expected_actions = {
                    "copy": "复制所选片段",
                    "duplicate": "重复所选片段",
                    "paste": "粘贴到播放头",
                    "split": "在播放头切分",
                    "delete": "删除所选片段",
                    "ripple": "波纹删除",
                    "inspector": "打开属性检查器",
                }
                for action, label in expected_actions.items():
                    item = menu.locator(f'[role="menuitem"][data-context-action="{action}"]')
                    assert item.is_visible(), action
                    assert item.inner_text() == label, action

                page.keyboard.press("Escape")
                menu.wait_for(state="detached", timeout=5000)
                assert fd.read_project_film(project_id)["rev"] == before_menu["rev"]

                # Once the menu is closed, Escape must bubble normally to the app.
                page.evaluate("""() => {
                  window.__escapeShouldPropagate = false;
                  window.addEventListener("keydown", event => {
                    if (event.key === "Escape") window.__escapeShouldPropagate = true;
                  }, {once:true});
                }""")
                page.keyboard.press("Escape")
                assert page.evaluate("window.__escapeShouldPropagate") is True
                assert fd.read_project_film(project_id)["rev"] == before_menu["rev"]

                # The AI panel must flag a proposal whose base revision no longer matches the live film.
                proposal_fixture = page.evaluate("""() => {
                  const bridge = window.filmStudioReactAi;
                  const current = String(window.filmStudioStore?.getSnapshot()?.revision || "");
                  if (!bridge || typeof bridge.render !== "function" || !current) {
                    throw new Error("React AI bridge or current FilmStore revision unavailable");
                  }
                  const proposal = {
                    summary: "测试过期提案", operationCount: 1,
                    baseRevision: current + "-stale",
                    operationPreviews: [{
                      id: "stale-preview-1", action: "修改速度", target: "clip-b",
                      detail: "将片段速度改为 1.5", before: "1", after: "1.5"
                    }],
                    affectedClips: ["clip-b"], affectedTruncatedCount: 0, truncatedCount: 0
                  };
                  bridge.render({currentRevision: current, messages: [], proposal});
                  return {current, proposal};
                }""")
                stale_warning = page.locator("#fs-proposal-stale-warning")
                stale_warning.wait_for(state="visible", timeout=5000)
                assert "当前影片 revision 已变化" in stale_warning.inner_text()
                context_proposal_panel = page.locator('#fs-proposal-panel[data-react-ai-proposal="true"]')
                context_proposal_panel.locator('details[data-react-proposal-details="true"] > summary').click()
                assert "clip-b" in context_proposal_panel.inner_text()
                assert context_proposal_panel.locator('[data-film-action="preview-proposal"]').is_disabled()
                assert context_proposal_panel.locator('[data-film-action="apply-proposal"]').is_disabled()
                assert not context_proposal_panel.locator('[data-film-action="reject-proposal"]').is_disabled()
                assert fd.read_project_film(project_id)["rev"] == before_menu["rev"]

                # A matching base revision restores preview/apply without writing anything.
                page.evaluate("""({current, proposal}) => {
                  window.filmStudioReactAi.render({currentRevision: current, messages: [], proposal: {...proposal, baseRevision: current}});
                }""", proposal_fixture)
                stale_warning.wait_for(state="detached", timeout=5000)
                assert not context_proposal_panel.locator('[data-film-action="preview-proposal"]').is_disabled()
                assert not context_proposal_panel.locator('[data-film-action="apply-proposal"]').is_disabled()
                assert fd.read_project_film(project_id)["rev"] == before_menu["rev"]
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_react_render_button_tracks_filestore_rendering_without_dom_mutation(tmp_path, monkeypatch):
    """Render progress is FilmStore state; the React button follows it without editing the film."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-react-render-button-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    before = fd.read_project_film(project_id)
    assert before["exists"]

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
            workflow_polls = {"count": 0}

            def mock_render_start(route):
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"ok": True, "run_id": "react-render-button-test"}),
                )

            def mock_render_status(route):
                workflow_polls["count"] += 1
                status = "RUNNING" if workflow_polls["count"] == 1 else "COMPLETED"
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"status": status, "result": {}}),
                )

            try:
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded"
                )
                assert response and response.status == 200
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function(
                    "() => { const select=document.querySelector('#editor-project'); return !!select && select.options.length > 0; }"
                )
                page.evaluate("""pid => {
                  const select = document.querySelector('#editor-project');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                  select.dispatchEvent(new Event('change', {bubbles:true}));
                }""", project_id)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').wait_for(state="visible")
                render_button = page.locator('#fs-react-toolbar [data-film-action="render"]')
                render_button.wait_for(state="visible", timeout=5000)
                assert not render_button.is_disabled()
                assert page.evaluate("window.filmStudioStore?.getSnapshot()?.rendering") is False

                page.route("**/api/editor/film/render", mock_render_start)
                page.route("**/api/workflows/react-render-button-test", mock_render_status)
                page.evaluate("() => { window.filmStudioRender(); return true; }")
                page.wait_for_function(
                    """() => window.filmStudioStore?.getSnapshot()?.rendering === true &&
                      document.querySelector('#fs-react-toolbar [data-film-action="render"]')?.disabled === true""",
                    timeout=5000,
                )
                assert render_button.is_disabled()
                assert render_button.get_attribute("aria-busy") == "true"
                page.wait_for_function(
                    """() => window.filmStudioStore?.getSnapshot()?.rendering === false &&
                      document.querySelector('#fs-react-toolbar [data-film-action="render"]')?.disabled === false""",
                    timeout=10000,
                )
                assert not render_button.is_disabled()
                assert render_button.get_attribute("aria-busy") == "false"
                assert fd.read_project_film(project_id)["rev"] == before["rev"]
                assert workflow_polls["count"] >= 2
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_stale_ai_proposal_is_rejected_after_external_agent_deletes_target_clip(tmp_path, monkeypatch):
    """An Agent's newer film.html must win over an older AI proposal targeting a deleted clip."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-stale-proposal-delete-target"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    initial = fd.read_project_film(project_id)
    assert initial["exists"]
    initial_revision = initial["rev"]

    def fake_plan(llm, state, message, selected_clip_id, summary_out=None,
                  selected_element=None, **kwargs):
        assert selected_clip_id == "clip-b"
        if summary_out is not None:
            summary_out["summary"] = "Change the target clip speed."
        return [{"op": "set", "clip": "clip-b", "field": "speed", "value": 1.5}]

    monkeypatch.setattr(fd, "plan_film_command", fake_plan)
    monkeypatch.setattr(web_server.vd_llm, "get_llm", lambda: object())

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                page.set_default_navigation_timeout(10_000)
                page.goto(base_url + "/", wait_until="domcontentloaded", timeout=10_000)
                print("STALE_PROPOSAL_CHECKPOINT=browser-ready", flush=True)

                # Create a real server-registered proposal through the browser's fetch path.
                created = page.evaluate(
                    """async ({url, body}) => {
                      const response = await fetch(url, {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify(body),
                        signal: AbortSignal.timeout(8000)
                      });
                      return {status: response.status, body: await response.json()};
                    }""",
                    {
                        "url": base_url + "/api/editor/film/command",
                        "body": {
                            "project_id": project_id,
                            "base": initial_revision,
                            "message": "Change clip-b speed.",
                            "selected_clip_id": "clip-b",
                        },
                    },
                )
                print("STALE_PROPOSAL_CHECKPOINT=proposal-request-returned", flush=True)
                assert created["status"] == 200, created
                proposal = created["body"]["proposal"]
                assert proposal["base_revision"] == initial_revision
                assert "clip-b" in proposal["affected_clip_ids"]
                assert fd.read_project_film(project_id)["rev"] == initial_revision, (
                    "Planning a proposal must not write film.html."
                )

                # Also create a proposal through the actual React Studio UI so the adapter's
                # external-revision poll (not only the React bridge) is tested end-to-end.
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
                  select.dispatchEvent(new Event('change', {bubbles:true}));
                }""", project_id)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                live_clip = page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]')
                live_clip.wait_for(state="visible", timeout=5000)
                live_clip.click()
                page.wait_for_function(
                    "() => window.filmStudioStore?.getSnapshot()?.selectedId === 'clip-b'",
                    timeout=5000,
                )
                chat_input = page.locator('#fs-react-ai-director #fs-chat-input')
                chat_input.fill("Change clip-b speed.")
                page.locator('#fs-react-ai-director [data-film-action="ask"]').click()
                page.wait_for_function(
                    """() => {
                      const panel = document.querySelector('#fs-proposal-panel[data-react-ai-proposal="true"]');
                      return !!panel && panel.textContent.includes("Change the target clip speed.");
                    }""",
                    timeout=15000,
                )
                live_proposal = page.locator('#fs-react-ai-director #fs-proposal-panel[data-react-ai-proposal="true"]')
                assert not live_proposal.locator('[data-film-action="apply-proposal"]').is_disabled()
                assert fd.read_project_film(project_id)["rev"] == initial_revision, (
                    "The proposal generated inside Studio must not write film.html."
                )

                # Simulate an Agent atomically rewriting film.html and deleting the target.
                before_agent_write = fd.read_project_film(project_id)
                updated_doc, _ = fd.apply_ops(
                    before_agent_write["doc"], [{"op": "remove", "clip": "clip-b"}]
                )
                agent_text = fd.serialize_film(updated_doc)
                fd.parse_film(agent_text)
                staged_path = film_path.with_name(film_path.name + ".agent.tmp")
                staged_path.write_text(agent_text, encoding="utf-8")
                staged_path.replace(film_path)
                after_agent_write = fd.read_project_film(project_id)
                agent_revision = after_agent_write["rev"]
                assert agent_revision != initial_revision
                assert all(
                    clip["id"] != "clip-b"
                    for track in after_agent_write["doc"]["tracks"]
                    for clip in track["clips"]
                )
                print("STALE_PROPOSAL_CHECKPOINT=external-delete-persisted", flush=True)

                # External revision polling must retain the actual UI proposal, mark it stale,
                # disable preview/apply, and allow inspection/rejection without touching film.html.
                page.wait_for_function(
                    "(expected) => window.filmStudioStore?.getSnapshot()?.revision === expected",
                    arg=agent_revision,
                    timeout=15000,
                )
                live_stale_warning = page.locator('#fs-react-ai-director #fs-proposal-stale-warning')
                live_stale_warning.wait_for(state="visible", timeout=10000)
                # Operation details are intentionally collapsed by default; open them
                # before asserting the affected clip remains inspectable on a stale proposal.
                live_proposal.locator('details[data-react-proposal-details="true"] > summary').click()
                assert not live_proposal.locator('[data-film-action="preview-proposal"]').is_enabled()
                assert not live_proposal.locator('[data-film-action="apply-proposal"]').is_enabled()
                assert live_proposal.locator('[data-film-action="reject-proposal"]').is_enabled()
                assert "Change the target clip speed." in live_proposal.inner_text()
                assert "clip-b" in live_proposal.inner_text()
                assert page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]').count() == 0
                assert fd.read_project_film(project_id)["rev"] == agent_revision

                # The server must reject the old proposal at the revision gate; client ops
                # are intentionally supplied to prove they cannot replace registered operations.
                applied = page.evaluate(
                    """async ({url, body}) => {
                      const response = await fetch(url, {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify(body),
                        signal: AbortSignal.timeout(8000)
                      });
                      return {status: response.status, body: await response.json()};
                    }""",
                    {
                        "url": base_url + "/api/editor/film/apply-proposal",
                        "body": {
                            "project_id": project_id,
                            "proposal_id": proposal["id"],
                            "base_revision": initial_revision,
                            "operations": [
                                {"op": "set", "clip": "clip-a", "field": "speed", "value": 9}
                            ],
                        },
                    },
                )
                print("STALE_PROPOSAL_CHECKPOINT=apply-request-returned", flush=True)
                assert applied["status"] == 409, applied
                assert applied["body"]["code"] == "REVISION_CONFLICT", applied

                persisted = fd.read_project_film(project_id)
                assert persisted["rev"] == agent_revision
                assert all(
                    clip["id"] != "clip-b"
                    for track in persisted["doc"]["tracks"]
                    for clip in track["clips"]
                )
                original_clip_a = next(
                    clip for track in initial["doc"]["tracks"]
                    for clip in track["clips"] if clip["id"] == "clip-a"
                )
                persisted_clip_a = next(
                    clip for track in persisted["doc"]["tracks"]
                    for clip in track["clips"] if clip["id"] == "clip-a"
                )
                assert persisted_clip_a["attrs"].get("speed") == original_clip_a["attrs"].get("speed")
                print("STALE_PROPOSAL_CHECKPOINT=all-conflict-assertions-passed", flush=True)
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_react_linked_edit_previews_commit_once_and_undo_restores_film(tmp_path, monkeypatch):
    """Roll/slide/slip preview only in React state, then submit one operation and undo cleanly."""
    from video_director import prober

    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root
    # This browser test validates the editor transaction contract, not FFprobe itself.
    # Source-bound validation is separately exercised with real-boundary contract tests.
    monkeypatch.setattr(prober, "probe_media", lambda _path: prober.ProbeResult(
        duration=24.0, has_video_stream=True, has_audio_stream=True,
    ))

    project_id = "film-studio-react-linked-edit-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    # The API resolves ../assets relative to the project folder. Supply a file
    # at that path because media_path_for_src enforces existence before probing.
    shared_assets = storage_root / "assets"
    shared_assets.mkdir(parents=True, exist_ok=True)
    (shared_assets / "source.mp4").write_bytes(b"linked-edit browser fixture; probe_media is stubbed below")
    linked_sample = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"><style>body{margin:0}</style></head>
<body>
<section id="video-main" data-label="主画面">
<video id="clip-left" src="../assets/source.mp4#t=0,5" at="0"></video>
<video id="clip-middle" src="../assets/source.mp4#t=5,10" at="5"></video>
<video id="clip-right" src="../assets/source.mp4#t=10,15" at="10"></video>
</section>
</body></html>'''
    film_path.write_text(linked_sample, encoding="utf-8")
    initial = fd.read_project_film(project_id)
    assert initial["exists"]

    def clip_in(snapshot, clip_id):
        return next(
            clip for track in snapshot["doc"]["tracks"]
            for clip in track["clips"] if clip["id"] == clip_id
        )

    def clip_geometry(page, clip_id):
        return page.locator(f'#fs-timeline .fs-clip[data-clip="{clip_id}"]').evaluate(
            """node => ({
              left: node.style.left,
              width: node.style.width,
              preview: node.dataset.reactGesturePreview,
              roll: node.dataset.rollDelta || null,
              slide: node.dataset.slideDelta || null,
              slip: node.dataset.slipDelta || null
            })"""
        )

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page_errors: list[str] = []
            film_edit_requests: list[dict] = []

            def capture_film_edit_request(request):
                if request.url.endswith("/api/editor/film/edit"):
                    try:
                        film_edit_requests.append(json.loads(request.post_data or "{}"))
                    except (TypeError, ValueError):
                        film_edit_requests.append({"_invalid_json": True})

            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("request", capture_film_edit_request)
            try:
                page.set_default_navigation_timeout(10_000)
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
                page.evaluate("""(pid) => {
                  const select = document.querySelector('#editor-project');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                  select.dispatchEvent(new Event('change', {bubbles: true}));
                }""", project_id)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                for clip_id in ("clip-left", "clip-middle", "clip-right"):
                    page.locator(f'#fs-timeline .fs-clip[data-clip="{clip_id}"]').wait_for(
                        state="visible", timeout=5000
                    )

                baseline = fd.read_project_film(project_id)
                baseline_revision = baseline["rev"]
                ids = ("clip-left", "clip-middle", "clip-right")
                baseline_doc = {clip_id: clip_in(baseline, clip_id) for clip_id in ids}
                baseline_geometry = {clip_id: clip_geometry(page, clip_id) for clip_id in ids}

                def begin_drag(selector, modifiers, delta_x=18):
                    handle = page.locator(selector)
                    bounds = handle.bounding_box()
                    assert bounds, {"selector": selector, "bounds": bounds}
                    x = bounds["x"] + bounds["width"] / 2
                    y = bounds["y"] + bounds["height"] / 2
                    for modifier in modifiers:
                        page.keyboard.down(modifier)
                    page.mouse.move(x, y)
                    page.mouse.down()
                    page.mouse.move(x + delta_x, y, steps=6)
                    return x, y, delta_x

                def finish_keys(modifiers):
                    for modifier in reversed(modifiers):
                        page.keyboard.up(modifier)

                def assert_no_write_during_preview(revision, request_count):
                    assert len(film_edit_requests) == request_count, film_edit_requests
                    assert fd.read_project_film(project_id)["rev"] == revision

                def assert_gesture_transients_cleared():
                    page.wait_for_function(
                        """() => {
                          const state = window.filmStudioStore?.getSnapshot();
                          return !!state &&
                            state.timelineGestureContext === null &&
                            state.timelineGesturePreview === null &&
                            state.timelineActiveDragIds === null &&
                            state.timelineSnapGuide === null &&
                            state.timelineDropTarget === null &&
                            state.timelineEnvelopePreview === null;
                        }""",
                        timeout=5000,
                    )

                def undo_to_baseline(clip_ids):
                    committed_revision = page.evaluate(
                        "() => window.filmStudioStore?.getSnapshot()?.revision || ''"
                    )
                    page.evaluate("window.filmStudioUndo()")
                    page.wait_for_function(
                        """({revision, ids}) => {
                          const store = window.filmStudioStore;
                          const currentRevision = store?.getSnapshot()?.revision || '';
                          if (!currentRevision || currentRevision === revision) return false;
                          return ids.every(id => {
                            const node = document.querySelector('#fs-timeline .fs-clip[data-clip="' + id + '"]');
                            return !!node && node.dataset.reactGesturePreview === 'false';
                          });
                        }""",
                        arg={"revision": committed_revision, "ids": list(clip_ids)},
                        timeout=10_000,
                    )
                    restored = fd.read_project_film(project_id)
                    for clip_id in ids:
                        expected = baseline_doc[clip_id]
                        actual = clip_in(restored, clip_id)
                        assert actual["attrs"]["src"] == expected["attrs"]["src"], {
                            "clip": clip_id, "expected": expected["attrs"]["src"], "actual": actual["attrs"]["src"]
                        }
                        assert float(actual["attrs"]["at"]) == float(expected["attrs"]["at"]), actual
                        assert abs(fd.clip_duration(actual) - fd.clip_duration(expected)) < 0.001, actual
                    for clip_id in clip_ids:
                        page.wait_for_function(
                            """({id, left, width}) => {
                              const node = document.querySelector('#fs-timeline .fs-clip[data-clip="' + id + '"]');
                              return !!node && node.style.left === left && node.style.width === width;
                            }""",
                            arg={
                                "id": clip_id,
                                "left": baseline_geometry[clip_id]["left"],
                                "width": baseline_geometry[clip_id]["width"],
                            },
                            timeout=10_000,
                        )

                # ROLL: Ctrl-drag the shared cut at clip-left's out point.
                edit_count = len(film_edit_requests)
                left_end = '#fs-timeline .fs-clip[data-clip="clip-left"] .fs-trim-handle[data-trim="end"]'
                begin_drag(left_end, ["Control"])
                page.wait_for_function(
                    """before => {
                      const left = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-left"]');
                      const right = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-middle"]');
                      return !!left && !!right &&
                        left.dataset.reactGesturePreview === "true" &&
                        right.dataset.reactGesturePreview === "true" &&
                        left.style.width !== before.leftWidth &&
                        right.style.left !== before.rightLeft;
                    }""",
                    arg={
                        "leftWidth": baseline_geometry["clip-left"]["width"],
                        "rightLeft": baseline_geometry["clip-middle"]["left"],
                    },
                    timeout=5000,
                )
                roll_preview = {clip_id: clip_geometry(page, clip_id) for clip_id in ids}
                assert roll_preview["clip-left"]["roll"] and roll_preview["clip-left"]["preview"] == "true", roll_preview
                roll_context = page.evaluate("""() => {
                  const context = window.filmStudioStore?.getSnapshot()?.timelineGestureContext;
                  return context ? {
                    mode: context.mode, id: context.id, pointerId: context.pointerId,
                    active: context.active, memberIds: context.members.map(member => member.id),
                    hasDomRef: Object.keys(context).some(key => /element|canvas|row/i.test(key)) ||
                      context.members.some(member => Object.keys(member).some(key => /element|canvas|row/i.test(key)))
                  } : null;
                }""")
                assert roll_context and roll_context["mode"] == "roll" and roll_context["active"] is True, roll_context
                assert roll_context["id"] == "clip-left" and roll_context["pointerId"] >= 0, roll_context
                assert set(roll_context["memberIds"]) == {"clip-left"}, roll_context
                assert roll_context["hasDomRef"] is False, roll_context
                assert_no_write_during_preview(baseline_revision, edit_count)
                page.mouse.up()
                finish_keys(["Control"])
                page.wait_for_function(
                    """old => {
                      const left = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-left"]');
                      const right = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-middle"]');
                      return !!left && !!right && left.dataset.reactGesturePreview === "false" &&
                        right.dataset.reactGesturePreview === "false" && left.style.width !== old.leftWidth;
                    }""",
                    arg={"leftWidth": baseline_geometry["clip-left"]["width"]},
                    timeout=10_000,
                )
                assert_gesture_transients_cleared()
                assert len(film_edit_requests) == edit_count + 1, film_edit_requests
                roll_ops = film_edit_requests[-1].get("ops") or []
                assert len(roll_ops) == 1 and roll_ops[0].get("op") == "roll", roll_ops
                assert roll_ops[0].get("clip") == "clip-left" and roll_ops[0].get("other_clip") == "clip-middle", roll_ops
                assert abs(float(roll_ops[0]["delta"]) - 0.5) < 0.08, roll_ops
                rolled = fd.read_project_film(project_id)
                assert abs(fd.clip_duration(clip_in(rolled, "clip-left")) - 5.5) < 0.08
                assert abs(float(clip_in(rolled, "clip-middle")["attrs"]["at"]) - 5.5) < 0.08
                undo_to_baseline(("clip-left", "clip-middle"))

                # SLIDE: Alt+Shift-drag the middle of the three contiguous, same-source clips.
                edit_count = len(film_edit_requests)
                middle_box = page.locator('#fs-timeline .fs-clip[data-clip="clip-middle"]').bounding_box()
                assert middle_box
                begin_drag(
                    '#fs-timeline .fs-clip[data-clip="clip-middle"]',
                    ["Alt", "Shift"],
                )
                page.wait_for_function(
                    """before => {
                      const left = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-left"]');
                      const middle = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-middle"]');
                      const right = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-right"]');
                      return !!left && !!middle && !!right &&
                        left.dataset.reactGesturePreview === "true" &&
                        middle.dataset.reactGesturePreview === "true" &&
                        right.dataset.reactGesturePreview === "true" &&
                        left.style.width !== before.leftWidth &&
                        middle.style.left !== before.middleLeft &&
                        right.style.left !== before.rightLeft;
                    }""",
                    arg={
                        "leftWidth": baseline_geometry["clip-left"]["width"],
                        "middleLeft": baseline_geometry["clip-middle"]["left"],
                        "rightLeft": baseline_geometry["clip-right"]["left"],
                    },
                    timeout=5000,
                )
                slide_preview = {clip_id: clip_geometry(page, clip_id) for clip_id in ids}
                assert slide_preview["clip-middle"]["slide"] and slide_preview["clip-middle"]["preview"] == "true", slide_preview
                slide_context = page.evaluate("window.filmStudioStore?.getSnapshot()?.timelineGestureContext || null")
                assert slide_context and slide_context["mode"] == "slide" and slide_context["active"] is True, slide_context
                assert slide_context["centerClipId"] == "clip-middle" and slide_context["members"][0]["id"] == "clip-middle", slide_context
                assert_no_write_during_preview(baseline_revision, edit_count)
                page.mouse.up()
                finish_keys(["Alt", "Shift"])
                page.wait_for_function(
                    """old => {
                      const middle = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-middle"]');
                      return !!middle && middle.dataset.reactGesturePreview === "false" && middle.style.left !== old.middleLeft;
                    }""",
                    arg={"middleLeft": baseline_geometry["clip-middle"]["left"]},
                    timeout=10_000,
                )
                assert_gesture_transients_cleared()
                assert len(film_edit_requests) == edit_count + 1, film_edit_requests
                slide_ops = film_edit_requests[-1].get("ops") or []
                assert len(slide_ops) == 1 and slide_ops[0].get("op") == "slide", slide_ops
                assert slide_ops[0].get("clip") == "clip-middle", slide_ops
                assert abs(float(slide_ops[0]["delta"]) - 0.5) < 0.08, slide_ops
                slid = fd.read_project_film(project_id)
                assert abs(float(clip_in(slid, "clip-middle")["attrs"]["at"]) - 5.5) < 0.08
                undo_to_baseline(ids)

                # SLIP: Alt-drag keeps clip time/width fixed and only offsets the source range.
                edit_count = len(film_edit_requests)
                middle_before_slip = clip_in(fd.read_project_film(project_id), "clip-middle")
                page.mouse.move(
                    middle_box["x"] + middle_box["width"] / 2,
                    middle_box["y"] + middle_box["height"] / 2,
                )
                begin_drag(
                    '#fs-timeline .fs-clip[data-clip="clip-middle"]',
                    ["Alt"],
                )
                page.wait_for_function(
                    """before => {
                      const middle = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-middle"]');
                      return !!middle && middle.dataset.reactGesturePreview === "true" &&
                        (middle.dataset.slipDelta || "").includes("源偏移 +0.50s") &&
                        middle.style.left === before.left && middle.style.width === before.width;
                    }""",
                    arg={
                        "left": baseline_geometry["clip-middle"]["left"],
                        "width": baseline_geometry["clip-middle"]["width"],
                    },
                    timeout=5000,
                )
                slip_preview = clip_geometry(page, "clip-middle")
                slip_context = page.evaluate("window.filmStudioStore?.getSnapshot()?.timelineGestureContext || null")
                assert slip_context and slip_context["mode"] == "slip" and slip_context["active"] is True, slip_context
                assert slip_context["members"][0]["id"] == "clip-middle" and slip_context["originalEnd"] == 10.0, slip_context
                assert_no_write_during_preview(baseline_revision, edit_count)
                page.mouse.up()
                finish_keys(["Alt"])
                page.wait_for_function(
                    """old => {
                      const middle = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-middle"]');
                      return !!middle && middle.dataset.reactGesturePreview === "false" &&
                        middle.style.left === old.left && middle.style.width === old.width;
                    }""",
                    arg={
                        "left": baseline_geometry["clip-middle"]["left"],
                        "width": baseline_geometry["clip-middle"]["width"],
                    },
                    timeout=10_000,
                )
                assert_gesture_transients_cleared()
                assert len(film_edit_requests) == edit_count + 1, film_edit_requests
                slip_ops = film_edit_requests[-1].get("ops") or []
                assert len(slip_ops) == 1 and slip_ops[0].get("op") == "slip", slip_ops
                assert slip_ops[0].get("clip") == "clip-middle", slip_ops
                assert abs(float(slip_ops[0]["delta_source"]) - 0.5) < 0.08, slip_ops
                slipped = fd.read_project_film(project_id)
                slipped_middle = clip_in(slipped, "clip-middle")
                assert slipped_middle["attrs"]["src"].endswith("#t=5.5,10.5"), slipped_middle
                assert float(slipped_middle["attrs"]["at"]) == 5.0, slipped_middle
                assert abs(fd.clip_duration(slipped_middle) - fd.clip_duration(middle_before_slip)) < 0.001
                undo_to_baseline(ids)

                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
