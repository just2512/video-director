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

                # Shared typed timeline math is used by React rendering and the
                # gesture adapter. Zoom keeps the same time under the pointer.
                math = page.evaluate("""() => {
                  const m = window.filmStudioTimelineMath;
                  if (!m) return null;
                  return {
                    pixels: m.secondsToPixels(1.5, 48),
                    seconds: m.pixelsToSeconds(72, 48),
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

                # Keyframe markers select their clip and seek using clip-relative time.
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

                # Cross-track multi-selection moves as one two-operation FilmDocument transaction.
                audio_clip = page.locator('#fs-timeline .fs-clip[data-clip="audio-1"]')
                audio_clip.click(modifiers=["Control"])
                page.wait_for_function("""() => {
                  const video = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  const audio = document.querySelector('#fs-timeline .fs-clip[data-clip="audio-1"]');
                  return !!video && !!audio && video.classList.contains('fs-clip-selected') &&
                    audio.classList.contains('fs-clip-selected');
                }""")
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

                # Select the group while both members are editable; locking one member afterward
                # simulates the stale-selection case the drag preflight must reject.
                clip.click()
                audio_clip.click(modifiers=["Control"])
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

                # Select the same group first, then lock its source track. The group must still be
                # rejected before any move operation reaches the server.
                clip.click()
                audio_clip.click(modifiers=["Control"])
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
                destination_clip_after = next(
                    item for track in after_destination_reject["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-b"
                )
                assert destination_clip_after["track_id"] == destination_clip_before["track_id"]
                assert destination_clip_after["attrs"]["at"] == destination_clip_before["attrs"]["at"]
                page.evaluate("(id) => window.filmStudioToggleTrackLock(id)", destination_track_id)
                page.wait_for_function("""id => {
                  const button = document.querySelector('button[data-track-lock="' + id + '"]');
                  return !!button && button.textContent.trim() === '锁';
                }""", arg=destination_track_id)

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

                # The native envelope canvas must still be cancelled by the document fallback.
                envelope = page.locator('canvas[data-envelope-clip="audio-1"]')
                envelope.wait_for(state="visible")
                before_envelope_cancel = fd.read_project_film(project_id)
                page.evaluate("""() => {
                  const canvas = document.querySelector('canvas[data-envelope-clip="audio-1"]');
                  if (!canvas) throw new Error('audio envelope canvas missing');
                  const rect = canvas.getBoundingClientRect();
                  const point = {bubbles:true,cancelable:true,pointerId:78,button:0,buttons:1,
                    clientX:Math.round(rect.left + rect.width * 0.55),
                    clientY:Math.round(rect.top + rect.height * 0.4)};
                  canvas.dispatchEvent(new PointerEvent('pointerdown', point));
                  document.dispatchEvent(new PointerEvent('pointermove', {...point, clientX:point.clientX + 12}));
                  canvas.dispatchEvent(new PointerEvent('pointercancel', {...point, buttons:0}));
                  document.dispatchEvent(new PointerEvent('pointermove', {...point, clientX:point.clientX + 24}));
                  document.dispatchEvent(new PointerEvent('pointerup', {...point, buttons:0}));
                }""")
                page.wait_for_timeout(50)
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
                assert fd.read_project_film(project_id)["rev"] == fd.revision_of(repaired)

                # Move into the upper zoom range first; the measured wheel then creates horizontal overflow.
                page.evaluate("""() => {
                  const scroll = document.querySelector('#fs-timeline-scroll');
                  const rect = scroll.getBoundingClientRect();
                  const clientX = rect.left + Math.max(120, Math.min(200, scroll.clientWidth - 24));
                  for (let i = 0; i < 4; i++) {
                    scroll.dispatchEvent(new WheelEvent('wheel', {
                      bubbles: true, cancelable: true, ctrlKey: true,
                      deltaY: -100, clientX
                    }));
                  }
                }""")
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
                    clientX: rect.left + localX, localX, width, pixelsPerSecond,
                    scrollLeft: scroll.scrollLeft,
                    anchorTime: (scroll.scrollLeft + localX - 132) / pixelsPerSecond
                  };
                }""")
                zoom_prevented = page.evaluate("""clientX => {
                  const scroll = document.querySelector('#fs-timeline-scroll');
                  const event = new WheelEvent('wheel', {
                    bubbles: true, cancelable: true, ctrlKey: true,
                    deltaY: -100, clientX
                  });
                  scroll.dispatchEvent(event);
                  return event.defaultPrevented;
                }""", zoom_before["clientX"])
                assert zoom_prevented is True, "Ctrl-wheel must prevent browser page zoom while over the timeline"
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
                    scrollLeft: scroll.scrollLeft
                  };
                }""")
                assert abs(zoom_after["pixelsPerSecond"] / zoom_before["pixelsPerSecond"] - 1.25) < 0.02, zoom_after
                expected_scroll_left = max(
                    0,
                    132 + zoom_before["anchorTime"] * zoom_after["pixelsPerSecond"] - zoom_before["localX"],
                )
                assert abs(zoom_after["scrollLeft"] - expected_scroll_left) < 4, {
                    "before": zoom_before, "after": zoom_after, "expected_scroll_left": expected_scroll_left
                }

                # Reach the zoom cap so the timeline must scroll; then verify auto-scroll while a clip is held at the right edge.
                page.evaluate("""clientX => {
                  const scroll = document.querySelector('#fs-timeline-scroll');
                  for (let i = 0; i < 5; i++) {
                    scroll.dispatchEvent(new WheelEvent('wheel', {
                      bubbles: true, cancelable: true, ctrlKey: true,
                      deltaY: -100, clientX
                    }));
                  }
                }""", zoom_before["clientX"])
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
