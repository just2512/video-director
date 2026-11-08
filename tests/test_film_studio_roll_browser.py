"""Real-browser regression for the OpenFilm-inspired Film Studio roll-trim gesture."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import shutil
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading

import pytest
from playwright.sync_api import sync_playwright

from video_director import film_document as fd


SAMPLE = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"><style>body{margin:0}</style></head>
<body>
<section id="video-main" data-label="主画面">
<video id="clip-a" src="../assets/a.mp4#t=2,12" at="0" speed="2" volume="1" data-film-keyframes='[{"property":"opacity","keyframes":[{"at":0,"value":0,"easing":"linear"},{"at":1,"value":1,"easing":"ease_out"}]}]'></video>
<video id="clip-b" src="../assets/b.mp4#t=0,5" at="5"></video>
</section>
<section id="audio-bed" data-label="BGM">
<audio id="audio-1" src="../assets/music.wav#t=0,20" at="0" volume="0.2"></audio>
</section>
</body></html>'''


def _capture_history_request(request, captured: list[dict]) -> None:
    if request.method != "POST" or not request.url.endswith(("/api/editor/film/undo", "/api/editor/film/redo")):
        return
    try:
        payload = request.post_data_json
    except Exception as exc:
        payload = {"parse_error": str(exc)}
    captured.append({"action": request.url.rsplit("/", 1)[-1], "payload": payload})


def _capture_history_response(response, captured: list[dict]) -> None:
    if response.request.method != "POST" or not response.url.endswith(("/api/editor/film/undo", "/api/editor/film/redo")):
        return
    try:
        body = response.text()
    except Exception as exc:
        body = ""
    captured.append({"action": response.url.rsplit("/", 1)[-1], "status": response.status, "body": body})


def _assert_history_four_way(page, project_id: str, action: str, base_revision: str,
                             expected_revision: str, requests: list[dict], responses: list[dict]) -> None:
    assert len(requests) == 1, requests
    request = requests[0]
    payload = request.get("payload") or {}
    assert request.get("action") == action, request
    assert payload.get("project_id") == project_id, request
    assert payload.get("base") == base_revision, request
    assert len(responses) == 1, responses
    response = responses[0]
    body = json.loads(response["body"])
    assert response["action"] == action and response["status"] == 200, response
    assert body.get("action") == action and body.get("rev") == expected_revision, response
    store = page.evaluate("""() => {
      const state = window.filmStudioStore?.getSnapshot();
      return state ? {projectId:state.projectId,revision:state.revision,
        busy:state.busy,pendingTransaction:state.pendingTransaction} : null;
    }""")
    persisted = fd.read_project_film(project_id)
    assert store and store["projectId"] == project_id, store
    assert store["revision"] == body["rev"] == persisted["rev"] == expected_revision, {
        "request": request, "response": response, "store": store,
        "disk_revision": persisted["rev"]
    }
    assert store["busy"] is False and store["pendingTransaction"] is None, store


def test_film_studio_ctrl_drag_roll_trim_updates_persisted_timeline(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    # Use a real source file so the server-side Slip duration guard is exercised.
    asset_root = storage_root / "assets"
    asset_root.mkdir(parents=True, exist_ok=True)
    from video_director import renderer
    generated = subprocess.run(
        [renderer._ffmpeg(), "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=20",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", "-y",
         str(asset_root / "a.mp4")],
        capture_output=True, text=True, timeout=60,
    )
    assert generated.returncode == 0, generated.stderr
    # Clip-b starts with a 5-second selected source window; the file has more frames.
    shutil.copyfile(asset_root / "a.mp4", asset_root / "b.mp4")

    project_id = "film-studio-roll-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
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
                film_edit_requests: list[dict] = []
                film_edit_responses: list[dict] = []
                history_requests: list[dict] = []
                history_responses: list[dict] = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                def capture_film_edit_request(request):
                    if request.method == "POST" and request.url.endswith("/api/editor/film/edit"):
                        try:
                            film_edit_requests.append(request.post_data_json)
                        except Exception:
                            film_edit_requests.append({"raw": request.post_data})
                page.on("request", capture_film_edit_request)
                page.on("request", lambda request: _capture_history_request(request, history_requests))
                def capture_film_edit_response(response):
                    if response.request.method == "POST" and response.url.endswith("/api/editor/film/edit"):
                        try:
                            body = response.text()
                        except Exception as exc:
                            body = ""
                        film_edit_responses.append({"status": response.status, "body": body})
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded"
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.filmStudioToggle === 'function'")
                # The workspace Studio is mounted inside the editor tab; opening it
                # while the home tab is display:none correctly leaves it hidden.
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                # editorInit() repopulates the select asynchronously; wait before
                # inserting the test-only fixture project to avoid a lost selection.
                page.wait_for_function(
                    "() => { const select = document.querySelector('#editor-project'); return !!select && select.options.length > 0; }"
                )
                page.evaluate("""(pid) => {
                  const select = document.querySelector('#editor-project');
                  if (!select) throw new Error('project selector is missing');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                }""", project_id)
                page.evaluate("window.filmStudioToggle()")
                page.locator('#film-studio-panel.fs-studio-expanded').wait_for(state="visible")
                if os.environ.get("VIDEO_DIRECTOR_REQUIRE_REACT_STUDIO") == "1":
                    # This CI job has already built the bundle; fail if the page silently
                    # falls back to the legacy UI instead of using the React surfaces.
                    page.locator('#fs-react-ai-director [data-react-ai-chat-log="true"]').wait_for(state="visible")
                    assert page.evaluate("typeof window.filmStudioReactAi?.render === 'function'")
                    assert not page.locator("#fs-static-ai-director").is_visible()
                    assert not page.locator("#fs-static-timeline-toolbar").is_visible()
                    page.locator('#fs-react-timeline-toolbar [data-film-action="split"]').wait_for(state="visible")


                # The TypeScript gesture controller must return the exact geometry
                # and authoritative operation from one shared resolution for each
                # linked edit; pointer-up must not need to reconstruct a second op.
                page.wait_for_function("""() => {
                  const controller = window.filmStudioTimelineController;
                  return typeof controller?.resolveTrimGesture === 'function'
                    && typeof controller?.resolveRollGesture === 'function'
                    && typeof controller?.resolveSlideGesture === 'function'
                    && typeof controller?.resolveSlipGesture === 'function'
                    && typeof controller?.buildMovePreview === 'function';
                }""")
                gesture_contract = page.evaluate("""() => {
                  const controller = window.filmStudioTimelineController;
                  const common = {
                    candidates: [], pixelsPerSecond: 36, thresholdPixels: 8,
                    snapEnabled: false, frameRate: 30
                  };
                  const trim = controller.resolveTrimGesture({
                    clipId: 'clip-trim', edge: 'start', originalAt: 5, originalEnd: 10,
                    clipTag: 'video', sourceIn: 2, playbackSpeed: 2,
                    rawDeltaSeconds: -0.5, ...common
                  });
                  const roll = controller.resolveRollGesture({
                    leftClipId: 'clip-a', rightClipId: 'clip-b', activeClipId: 'clip-a',
                    rightAt: 5, leftDuration: 5, rightDuration: 5, rawDeltaSeconds: 0.5,
                    ...common
                  });
                  const slide = controller.resolveSlideGesture({
                    leftClipId: 'clip-left', centerClipId: 'clip-center', rightClipId: 'clip-right',
                    centerAt: 5, rightAt: 10, leftDuration: 5, centerDuration: 5, rightDuration: 5,
                    rawDeltaSeconds: 0.5, ...common
                  });
                  const slip = controller.resolveSlipGesture({
                    clipId: 'clip-slip', rawDeltaSeconds: 0.5, playbackSpeed: 2,
                    snapEnabled: false, frameRate: 30
                  });
                  const move = controller.buildMovePreview({
                    gesture: {
                      id: 'clip-move', trackId: 'track-a', originalTrackIndex: 0,
                      trackOrder: ['track-a', 'track-b'], targetTrackId: 'track-b',
                      members: [{id: 'clip-move', originalAt: 2, originalEnd: 4,
                        trackId: 'track-a', trackIndex: 0}]
                    },
                    targetTrackId: 'track-b',
                    tracks: [
                      {id: 'track-a', attrs: {}, clips: [{id: 'clip-move', at: 2, end: 4, attrs: {}}]},
                      {id: 'track-b', attrs: {}, clips: []}
                    ],
                    minimumStart: 2, rawDeltaSeconds: 0.5, ...common
                  });
                  return {
                    trim: trim && {operation: trim.operation, preview: trim.preview},
                    roll: roll && {operation: roll.operation, preview: roll.preview},
                    slide: slide && {operation: slide.operation, preview: slide.preview},
                    slip: slip && {operation: slip.operation, preview: slip.preview},
                    move: move && {operations: move.operations, preview: move.preview}
                  };
                }""")
                assert gesture_contract["trim"]["operation"] == {
                    "op": "trim", "clip": "clip-trim", "edge": "start", "at": 4.5
                }, gesture_contract
                assert gesture_contract["trim"]["preview"] == {"left": 162, "width": 198}, gesture_contract
                assert gesture_contract["roll"]["operation"] == {
                    "op": "roll", "clip": "clip-a", "other_clip": "clip-b", "delta": 0.5
                }, gesture_contract
                assert gesture_contract["roll"]["preview"]["clip-a"]["width"] == 198, gesture_contract
                assert gesture_contract["roll"]["preview"]["clip-b"] == {"left": 198, "width": 162}, gesture_contract
                assert gesture_contract["slide"]["operation"] == {
                    "op": "slide", "clip": "clip-center", "delta": 0.5
                }, gesture_contract
                assert gesture_contract["slide"]["preview"]["clip-center"]["left"] == 198, gesture_contract
                assert gesture_contract["slide"]["preview"]["clip-right"] == {"left": 378, "width": 162}, gesture_contract
                assert gesture_contract["slip"]["operation"] == {
                    "op": "slip", "clip": "clip-slip", "delta_source": 1
                }, gesture_contract
                assert gesture_contract["slip"]["preview"]["clip-slip"]["mode"] == "slip", gesture_contract
                assert gesture_contract["move"]["operations"] == [{
                    "op": "move", "clip": "clip-move", "track_id": "track-b", "at": 2.5
                }], gesture_contract
                assert gesture_contract["move"]["preview"]["clip-move"] == {
                    "left": 90, "targetTrackId": "track-b"
                }, gesture_contract

                # Attribute inspection and AI direction must be visible at the same
                # time; resizing their divider must persist the user-selected split.
                inspector = page.locator("#fs-side-inspector")
                ai_panel = page.locator("#fs-side-ai")
                side_divider = page.locator("#fs-side-divider")
                inspector.wait_for(state="visible")
                ai_panel.wait_for(state="visible")
                before_inspector_height = inspector.bounding_box()["height"]
                side_box = side_divider.bounding_box()
                assert side_box and before_inspector_height > 0
                page.mouse.move(side_box["x"] + side_box["width"] / 2,
                                side_box["y"] + side_box["height"] / 2)
                page.mouse.down()
                page.mouse.move(side_box["x"] + side_box["width"] / 2,
                                side_box["y"] + side_box["height"] / 2 + 28,
                                steps=4)
                page.mouse.up()
                after_inspector_height = inspector.bounding_box()["height"]
                assert after_inspector_height >= before_inspector_height + 12, "Inspector/AI seam should resize the inspector"
                saved_share = page.evaluate("localStorage.getItem('film-studio-inspector-share')")
                assert saved_share and float(saved_share) > 54, "Inspector split should persist the changed share"
                assert inspector.is_visible() and ai_panel.is_visible(), "AI and Inspector must not hide each other"

                # Exercise the actual horizontal dock seam, not only its DOM presence.
                timeline_pane = page.locator('.fs-timeline-pane')
                divider = page.locator('#fs-timeline-divider')
                divider.wait_for(state="visible")
                before_height = timeline_pane.bounding_box()["height"]
                divider_box = divider.bounding_box()
                assert divider_box and before_height > 0
                page.mouse.move(divider_box["x"] + divider_box["width"] / 2,
                                divider_box["y"] + divider_box["height"] / 2)
                page.mouse.down()
                page.mouse.move(divider_box["x"] + divider_box["width"] / 2,
                                divider_box["y"] + divider_box["height"] / 2 - 64,
                                steps=4)
                page.mouse.up()
                after_height = timeline_pane.bounding_box()["height"]
                assert after_height >= before_height + 30, "timeline seam should resize the timeline panel"
                saved_height = page.evaluate("localStorage.getItem('film-studio-timeline-height')")
                assert saved_height and abs(float(saved_height) - after_height) < 2
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').click()
                page.evaluate("window.filmStudioSeek(2)")
                page.locator('[data-film-action="split"]:visible').click()
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
                roll_document_before = fd.read_project_film(project_id)
                roll_bytes_before = film_path.read_bytes()
                roll_geometry_before = page.evaluate("""ids => Object.fromEntries(ids.map(id => {
                  const node = document.querySelector('#fs-timeline .fs-clip[data-clip="' + id + '"]');
                  return [id, node ? {left:node.style.left,width:node.style.width} : null];
                }))""", ["clip-a", right_id])
                roll_edit_count_before = len(film_edit_requests)
                page.keyboard.down("Control")
                try:
                    page.mouse.move(x, y)
                    page.mouse.down()
                    page.mouse.move(x + 18, y, steps=5)  # +0.5 s at 36 px/s
                    page.wait_for_function("""args => {
                      const ids = args.ids;
                      const state = window.filmStudioStore?.getSnapshot();
                      const preview = state?.timelineGesturePreview;
                      const left = document.querySelector('#fs-timeline .fs-clip[data-clip="' + ids[0] + '"]');
                      const right = document.querySelector('#fs-timeline .fs-clip[data-clip="' + ids[1] + '"]');
                      return !!preview?.[ids[0]]?.width && !!preview?.[ids[1]]?.left &&
                        left?.dataset.reactGesturePreview === 'true' &&
                        right?.dataset.reactGesturePreview === 'true' &&
                        left.style.width !== args.leftWidthBefore &&
                        right.style.left !== args.rightLeftBefore;
                    }""", arg={"ids":["clip-a",right_id],
                                "leftWidthBefore":roll_geometry_before["clip-a"]["width"],
                                "rightLeftBefore":roll_geometry_before[right_id]["left"]}, timeout=5000)
                    roll_preview = page.evaluate("""ids => ({
                      store:window.filmStudioStore?.getSnapshot()?.timelineGesturePreview,
                      left:document.querySelector('#fs-timeline .fs-clip[data-clip="' + ids[0] + '"]')?.style.width,
                      right:document.querySelector('#fs-timeline .fs-clip[data-clip="' + ids[1] + '"]')?.style.left
                    })""", ["clip-a", right_id])
                    assert roll_preview["store"] and "clip-a" in roll_preview["store"] and right_id in roll_preview["store"], roll_preview
                    assert roll_preview["store"]["clip-a"].get("mode") == "roll", roll_preview
                    assert fd.read_project_film(project_id)["rev"] == roll_document_before["rev"]
                    assert len(film_edit_requests) == roll_edit_count_before, film_edit_requests[roll_edit_count_before:]
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
                }""", arg={"pid": project_id, "rightId": right_id}, timeout=10000)

                roll_committed_requests = film_edit_requests[roll_edit_count_before:]
                assert len(roll_committed_requests) == 1, roll_committed_requests
                assert len(roll_committed_requests[0].get("ops") or []) == 1, roll_committed_requests
                assert roll_committed_requests[0]["ops"][0].get("op") == "roll", roll_committed_requests
                roll_delta = float(roll_committed_requests[0]["ops"][0]["delta"])
                assert abs(roll_delta - 0.5) < 0.001, roll_committed_requests
                persisted = fd.read_project_film(project_id)
                clips = {clip["id"]: clip for track in persisted["doc"]["tracks"] for clip in track["clips"]}
                assert clips["clip-a"]["attrs"]["src"].endswith("#t=2,7")
                assert clips[right_id]["attrs"]["src"].endswith("#t=7,12")
                assert abs(float(clips[right_id]["attrs"]["at"]) - (2 + roll_delta)) < 0.001
                def source_range(clip):
                    source = str(clip["attrs"]["src"])
                    assert "#t=" in source, source
                    start, end = source.rsplit("#t=", 1)[1].split(",", 1)
                    return float(start), float(end)
                assert source_range(clips["clip-a"])[1] == source_range(clips[right_id])[0]
                roll_document_after = fd.read_project_film(project_id)
                roll_bytes_after = film_path.read_bytes()

                # Roll is one history unit: undo restores the exact post-split document,
                # then redo restores the same source boundary and right-clip position.
                with page.expect_response(
                    lambda response: response.request.method == "POST"
                    and response.url.endswith("/api/editor/film/undo"),
                    timeout=10000,
                ) as undo_response:
                    page.evaluate("() => window.filmStudioUndo()")
                history_responses.append({
                    "action": "undo", "status": undo_response.value.status,
                    "body": undo_response.value.text(),
                })
                page.wait_for_function(
                    "revision => { const s=window.filmStudioStore?.getSnapshot(); "
                    "return !!s && !s.busy && s.pendingTransaction===null && s.revision===revision; }",
                    arg=roll_document_before["rev"], timeout=10000,
                )
                assert film_path.read_bytes() == roll_bytes_before
                assert fd.read_project_film(project_id)["rev"] == roll_document_before["rev"]
                with page.expect_response(
                    lambda response: response.request.method == "POST"
                    and response.url.endswith("/api/editor/film/redo"),
                    timeout=10000,
                ) as redo_response:
                    page.evaluate("() => window.filmStudioRedo()")
                history_responses.append({
                    "action": "redo", "status": redo_response.value.status,
                    "body": redo_response.value.text(),
                })
                page.wait_for_function(
                    "revision => { const s=window.filmStudioStore?.getSnapshot(); "
                    "return !!s && !s.busy && s.pendingTransaction===null && s.revision===revision; }",
                    arg=roll_document_after["rev"], timeout=10000,
                )
                assert film_path.read_bytes() == roll_bytes_after
                assert fd.read_project_film(project_id)["rev"] == roll_document_after["rev"]

                # Re-split the rolled right segment, then exercise Slide trim through
                # the actual clip-body modifier gesture (Alt+Shift drag).
                page.locator('#fs-timeline .fs-clip[data-clip="' + right_id + '"]').click()
                page.evaluate("window.filmStudioSeek(4)")
                page.keyboard.press("s")
                page.wait_for_function(
                    "(id) => [...document.querySelectorAll('#fs-timeline .fs-clip')].some(el => el.dataset.clip.startsWith(id + '-split-'))",
                    arg=right_id,
                    timeout=10000,
                )
                slide_right_id = page.evaluate("""(id) => {
                  const item = [...document.querySelectorAll('#fs-timeline .fs-clip')]
                    .find(el => el.dataset.clip.startsWith(id + '-split-'));
                  return item && item.dataset.clip;
                }""", right_id)
                assert slide_right_id, "second split should create a third source-contiguous segment"
                middle = page.locator('#fs-timeline .fs-clip[data-clip="' + right_id + '"]')
                box = middle.bounding_box()
                assert box and box["width"] >= 36 and box["height"] > 0
                x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                page.locator('[data-film-action="slide-mode"]:visible').click()
                assert page.locator('[data-film-action="slide-mode"]:visible').get_attribute("aria-pressed") == "true"
                slide_document_before = fd.read_project_film(project_id)
                slide_bytes_before = film_path.read_bytes()
                slide_clips_before = {
                    item["id"]: item
                    for track in slide_document_before["doc"]["tracks"]
                    for item in track["clips"]
                }
                slide_geometry_before = page.evaluate("""ids => Object.fromEntries(ids.map(id => {
                  const node = document.querySelector('#fs-timeline .fs-clip[data-clip="' + id + '"]');
                  return [id, node ? {left:node.style.left,width:node.style.width} : null];
                }))""", ["clip-a", right_id, slide_right_id])
                slide_edit_count_before = len(film_edit_requests)
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + 9, y, steps=3)  # +0.25 s at 36 px/s
                page.wait_for_function("""args => {
                  const ids = args.ids;
                  const state = window.filmStudioStore?.getSnapshot();
                  const preview = state?.timelineGesturePreview;
                  const left = document.querySelector('#fs-timeline .fs-clip[data-clip="' + ids[0] + '"]');
                  const center = document.querySelector('#fs-timeline .fs-clip[data-clip="' + ids[1] + '"]');
                  const right = document.querySelector('#fs-timeline .fs-clip[data-clip="' + ids[2] + '"]');
                  return !!preview?.[ids[0]]?.width && !!preview?.[ids[1]]?.left &&
                    !!preview?.[ids[2]]?.left && preview[ids[1]].mode === 'slide' &&
                    left?.style.width !== args.leftWidthBefore &&
                    center?.style.left !== args.centerLeftBefore &&
                    right?.style.left !== args.rightLeftBefore &&
                    center?.dataset.reactGesturePreview === 'true' &&
                    right?.dataset.reactGesturePreview === 'true';
                }""", arg={"ids":["clip-a",right_id,slide_right_id],
                            "leftWidthBefore":slide_geometry_before["clip-a"]["width"],
                            "centerLeftBefore":slide_geometry_before[right_id]["left"],
                            "rightLeftBefore":slide_geometry_before[slide_right_id]["left"]}, timeout=5000)
                slide_preview = page.evaluate("""ids => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return {
                    geometry:state?.timelineGesturePreview,
                    guide:state?.timelineSnapGuide,
                    fps:Number(state?.film?.fps || 30)
                  };
                }""", ["clip-a", right_id, slide_right_id])
                assert slide_preview["geometry"] and all(id in slide_preview["geometry"] for id in ["clip-a",right_id,slide_right_id]), slide_preview
                assert slide_preview["geometry"][right_id].get("mode") == "slide", slide_preview
                assert slide_preview["guide"] and slide_preview["guide"].get("kind") == "帧", slide_preview
                assert abs(float(slide_preview["guide"]["at"]) * slide_preview["fps"] -
                           round(float(slide_preview["guide"]["at"]) * slide_preview["fps"])) < 0.00001
                assert fd.read_project_film(project_id)["rev"] == slide_document_before["rev"]
                assert len(film_edit_requests) == slide_edit_count_before, film_edit_requests[slide_edit_count_before:]
                page.mouse.up()

                slide_committed_requests = film_edit_requests[slide_edit_count_before:]
                assert len(slide_committed_requests) == 1, slide_committed_requests
                assert len(slide_committed_requests[0].get("ops") or []) == 1, slide_committed_requests
                assert slide_committed_requests[0]["ops"][0].get("op") == "slide", slide_committed_requests
                slide_delta = float(slide_committed_requests[0]["ops"][0]["delta"])
                before_left_in, before_left_out = source_range(slide_clips_before["clip-a"])
                before_middle_in, before_middle_out = source_range(slide_clips_before[right_id])
                before_right_in, before_right_out = source_range(slide_clips_before[slide_right_id])
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-panel')?.getAttribute('data-busy') !== 'true'",
                    timeout=10000,
                )
                persisted = fd.read_project_film(project_id)
                clips = {clip["id"]: clip for track in persisted["doc"]["tracks"] for clip in track["clips"]}
                left_in, left_out = source_range(clips["clip-a"])
                middle_in, middle_out = source_range(clips[right_id])
                right_in, right_out = source_range(clips[slide_right_id])
                expected_middle_at = float(slide_clips_before[right_id]["attrs"]["at"]) + slide_delta
                expected_right_at = float(slide_clips_before[slide_right_id]["attrs"]["at"]) + slide_delta
                expected_source_shift = 2.0 * slide_delta

                # Slide preserves the middle clip's source window; the neighboring source endpoints move.
                assert abs(left_in - before_left_in) < 0.001
                assert abs(left_out - (before_left_out + expected_source_shift)) < 0.001, clips["clip-a"]
                assert abs(middle_in - before_middle_in) < 0.001, clips[right_id]
                assert abs(middle_out - before_middle_out) < 0.001, clips[right_id]
                assert abs(right_in - (before_right_in + expected_source_shift)) < 0.001, clips[slide_right_id]
                assert abs(right_out - before_right_out) < 0.001, clips[slide_right_id]
                assert abs(float(clips[right_id]["attrs"]["at"]) - expected_middle_at) < 0.001
                assert abs(float(clips[slide_right_id]["attrs"]["at"]) - expected_right_at) < 0.001
                assert abs(float(clips[right_id]["attrs"]["at"]) - float(slide_preview["guide"]["at"])) < 0.001
                assert abs(float(clips[right_id]["attrs"]["at"]) * slide_preview["fps"] -
                           round(float(clips[right_id]["attrs"]["at"]) * slide_preview["fps"])) < 0.00002
                assert abs(fd.clip_end(clips[slide_right_id]) - 5) < 0.001
                slide_document_after = fd.read_project_film(project_id)
                slide_bytes_after = film_path.read_bytes()

                page.locator('[data-film-action="slide-mode"]:visible').click()
                page.wait_for_function(
                    """() => document.querySelector('#fs-react-timeline-toolbar [data-film-action="slide-mode"]')
                      ?.getAttribute('aria-pressed') === 'false'""",
                    timeout=5000,
                )
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-panel')?.getAttribute('data-busy') !== 'true'",
                    timeout=15000,
                )
                # Undo/redo the preceding Slide gesture as one exact document transaction.
                page.evaluate("() => window.filmStudioUndo()")
                page.wait_for_function(
                    "revision => { const s=window.filmStudioStore?.getSnapshot(); "
                    "return !!s && !s.busy && s.pendingTransaction===null && s.revision===revision; }",
                    arg=slide_document_before["rev"], timeout=10000,
                )
                assert film_path.read_bytes() == slide_bytes_before
                page.evaluate("() => window.filmStudioRedo()")
                page.wait_for_function(
                    "revision => { const s=window.filmStudioStore?.getSnapshot(); "
                    "return !!s && !s.busy && s.pendingTransaction===null && s.revision===revision; }",
                    arg=slide_document_after["rev"], timeout=10000,
                )
                assert film_path.read_bytes() == slide_bytes_after

                # Slip the left clip without moving it or changing its timeline
                # duration. Use the explicit mode for this integration assertion;
                # Alt-drag remains a supported shortcut but is not required for discoverability.
                slip_mode = page.locator('[data-film-action="slip-mode"]:visible')
                slip_mode.click()
                assert slip_mode.get_attribute("aria-pressed") == "true"
                left_element = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]')
                left_box = left_element.bounding_box()
                assert left_box and left_box["width"] > 40
                x, y = left_box["x"] + left_box["width"] / 2, left_box["y"] + left_box["height"] / 2
                slip_document_before = fd.read_project_film(project_id)
                slip_bytes_before = film_path.read_bytes()
                slip_clip_before = next(
                    item for track in slip_document_before["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-a"
                )
                slip_original_src = str(slip_clip_before["attrs"]["src"])
                slip_original_duration = fd.clip_duration(slip_clip_before)
                slip_original_source_range = source_range(slip_clip_before)
                slip_geometry_before = left_element.evaluate("(node) => ({left:node.style.left,width:node.style.width})")
                slip_edit_count_before = len(film_edit_requests)
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + 9, y, steps=3)  # +0.25 timeline s × 2× speed = +0.5 source s
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const preview = state?.timelineGesturePreview?.['clip-a'];
                  const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-a"]');
                  return preview?.mode === 'slip' && typeof preview.deltaLabel === 'string' &&
                    node?.dataset.reactGesturePreview === 'true';
                }""", timeout=5000)
                slip_preview = page.evaluate("""() => ({
                  store:window.filmStudioStore?.getSnapshot()?.timelineGesturePreview?.['clip-a'],
                  geometry:(() => { const node=document.querySelector('#fs-timeline .fs-clip[data-clip="clip-a"]'); return node && {left:node.style.left,width:node.style.width}; })()
                })""")
                assert slip_preview["store"]["mode"] == "slip", slip_preview
                assert slip_preview["geometry"] == slip_geometry_before, slip_preview
                assert fd.read_project_film(project_id)["rev"] == slip_document_before["rev"]
                assert len(film_edit_requests) == slip_edit_count_before, film_edit_requests[slip_edit_count_before:]
                page.mouse.up()

                page.wait_for_function("""async (args) => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(args.pid));
                  if (!response.ok) return false;
                  const doc = await response.json();
                  const clips = (doc.tracks || []).flatMap(track => track.clips || []);
                  const left = clips.find(clip => clip.id === 'clip-a');
                  return !!left && String(left.attrs.src) !== args.originalSrc
                    && Math.abs(Number(left.attrs.at)) < 0.001;
                }""", arg={"pid": project_id, "originalSrc": slip_original_src}, timeout=10000)
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-panel')?.getAttribute('data-busy') === 'false'",
                    timeout=15000,
                )

                slip_committed_requests = film_edit_requests[slip_edit_count_before:]
                assert len(slip_committed_requests) == 1, slip_committed_requests
                assert len(slip_committed_requests[0].get("ops") or []) == 1, slip_committed_requests
                assert slip_committed_requests[0]["ops"][0].get("op") == "slip", slip_committed_requests
                persisted = fd.read_project_film(project_id)
                clips = {clip["id"]: clip for track in persisted["doc"]["tracks"] for clip in track["clips"]}
                slip_source_after = source_range(clips["clip-a"])
                slip_delta_source = float(slip_committed_requests[0]["ops"][0]["delta_source"])
                assert abs(float(clips["clip-a"]["attrs"]["at"])) < 0.001
                assert abs((slip_source_after[0] - slip_original_source_range[0]) - slip_delta_source) < 0.001, clips["clip-a"]
                assert abs((slip_source_after[1] - slip_original_source_range[1]) - slip_delta_source) < 0.001, clips["clip-a"]
                assert abs(fd.clip_duration(clips["clip-a"]) - slip_original_duration) < 0.001, clips["clip-a"]
                slip_document_after = fd.read_project_film(project_id)
                slip_bytes_after = film_path.read_bytes()

                page.locator('[data-film-action="slip-mode"]:visible').click()
                page.wait_for_function(
                    "() => document.querySelector('#fs-react-timeline-toolbar [data-film-action=\"slip-mode\"]')"
                    "?.getAttribute('aria-pressed') === 'false'", timeout=5000,
                )
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-panel')?.getAttribute('data-busy') !== 'true'",
                    timeout=10000,
                )
                # Slip undo restores the exact source range without moving the clip;
                # redo restores the exact same committed bytes and revision.
                page.evaluate("() => window.filmStudioUndo()")
                page.wait_for_function(
                    "revision => { const s=window.filmStudioStore?.getSnapshot(); "
                    "return !!s && !s.busy && s.pendingTransaction===null && s.revision===revision; }",
                    arg=slip_document_before["rev"], timeout=10000,
                )
                assert film_path.read_bytes() == slip_bytes_before
                undone_slip = fd.read_project_film(project_id)
                undone_clip = next(item for track in undone_slip["doc"]["tracks"] for item in track["clips"] if item["id"] == "clip-a")
                assert source_range(undone_clip) == slip_original_source_range
                assert fd.clip_duration(undone_clip) == pytest.approx(slip_original_duration)
                page.evaluate("() => window.filmStudioRedo()")
                page.wait_for_function(
                    "revision => { const s=window.filmStudioStore?.getSnapshot(); "
                    "return !!s && !s.busy && s.pendingTransaction===null && s.revision===revision; }",
                    arg=slip_document_after["rev"], timeout=10000,
                )
                assert film_path.read_bytes() == slip_bytes_after
                redone_slip = fd.read_project_film(project_id)
                redone_clip = next(item for track in redone_slip["doc"]["tracks"] for item in track["clips"] if item["id"] == "clip-a")
                assert source_range(redone_clip) == slip_source_after
                assert fd.clip_duration(redone_clip) == pytest.approx(slip_original_duration)

                # A regular edge trim is a visual-only preview until pointer-up, then one authoritative operation.
                trim_clip = page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]')
                trim_handle = trim_clip.locator('[data-trim="end"]')
                trim_handle.scroll_into_view_if_needed()
                trim_box = trim_handle.bounding_box()
                assert trim_box and trim_box["width"] > 0 and trim_box["height"] > 0
                trim_before = fd.read_project_film(project_id)
                clip_before = next(
                    clip for track in trim_before["doc"]["tracks"] for clip in track["clips"]
                    if clip["id"] == "clip-b"
                )
                trim_src_before = str(clip_before["attrs"]["src"])
                trim_end_before = fd.clip_end(clip_before)
                trim_scale = float(page.evaluate("window.filmStudioStore?.getSnapshot()?.pixelsPerSecond || 36"))
                trim_geometry_before = trim_clip.evaluate("(node) => node.style.width")
                trim_request_count = len(film_edit_requests)
                # Capture the exact resolver inputs/results used by the real React-owned pointer gesture.
                page.evaluate("""() => {
                  const controller = window.filmStudioTimelineController;
                  const resolve = controller?.resolveTrimGesture;
                  if (typeof resolve !== "function") throw new Error("React trim controller is missing");
                  window.__filmStudioTrimResolverCalls = [];
                  controller.resolveTrimGesture = input => {
                    const result = resolve(input);
                    window.__filmStudioTrimResolverCalls.push({
                      input: {
                        edge: input.edge, clipTag: input.clipTag, sourceIn: input.sourceIn,
                        playbackSpeed: input.playbackSpeed, originalAt: input.originalAt,
                        originalEnd: input.originalEnd, rawDeltaSeconds: input.rawDeltaSeconds,
                        pixelsPerSecond: input.pixelsPerSecond, snapEnabled: input.snapEnabled,
                        frameRate: input.frameRate
                      },
                      result: result ? {target: result.resolution.target, operation: result.operation} : null
                    });
                    return result;
                  };
                }""")
                tx = trim_box["x"] + trim_box["width"] / 2
                ty = trim_box["y"] + trim_box["height"] / 2
                page.mouse.move(tx, ty)
                page.mouse.down()
                page.mouse.move(tx + trim_scale * 0.5, ty, steps=5)
                page.wait_for_function("""args => {
                  const preview = window.filmStudioStore?.getSnapshot()?.timelineGesturePreview?.[args.id];
                  const node = document.querySelector('#fs-timeline .fs-clip[data-clip="' + args.id + '"]');
                  return typeof window.filmStudioTimelineController?.resolveTrimGesture === 'function'
                    && !!preview && typeof preview.width === 'number'
                    && node?.dataset.reactGesturePreview === 'true'
                    && node.style.width !== args.widthBefore;
                }""", arg={"id":"clip-b","widthBefore":trim_geometry_before}, timeout=5000)
                assert fd.read_project_film(project_id)["rev"] == trim_before["rev"], "drag preview must not write film.html"
                assert len(film_edit_requests) == trim_request_count, film_edit_requests[trim_request_count:]
                with page.expect_response(
                    lambda response: response.request.method == "POST"
                    and response.url.endswith("/api/editor/film/edit"),
                    timeout=10000,
                ) as trim_http_response:
                    page.mouse.up()
                film_edit_responses.append({
                    "status": trim_http_response.value.status,
                    "body": trim_http_response.value.text(),
                })
                expected_timeline_end = float(trim_end_before) + 0.5
                expected_source_out = float(trim_src_before.rsplit("#t=", 1)[1].split(",", 1)[1]) + 0.5
                page.wait_for_function("""args => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const node = document.querySelector('#fs-timeline .fs-clip[data-clip="' + args.id + '"]');
                  return !!state && !state.busy && state.pendingTransaction === null
                    && node?.dataset.reactGesturePreview !== 'true';
                }""", arg={"id":"clip-b"}, timeout=15000)
                trim_requests = film_edit_requests[trim_request_count:]
                resolver_calls = page.evaluate("window.__filmStudioTrimResolverCalls || []")
                assert resolver_calls, "no TypeScript trim resolver calls reached the real drag"
                assert resolver_calls[-1]["input"]["clipTag"] == "video", resolver_calls
                assert abs(float(resolver_calls[-1]["input"]["rawDeltaSeconds"]) - 0.5) < 0.08, resolver_calls
                assert abs(float(resolver_calls[-1]["result"]["operation"]["end"]) - expected_timeline_end) < 0.001, resolver_calls
                assert len(trim_requests) == 1, trim_requests
                trim_ops = trim_requests[0].get("ops") or []
                assert len(trim_ops) == 1 and trim_ops[0].get("op") == "trim", trim_requests
                assert trim_ops[0].get("edge") == "end", trim_ops
                assert abs(float(trim_ops[0].get("end", -1)) - expected_timeline_end) < 0.001, trim_ops
                trim_after = fd.read_project_film(project_id)
                clip_after = next(
                    clip for track in trim_after["doc"]["tracks"] for clip in track["clips"]
                    if clip["id"] == "clip-b"
                )
                assert clip_after["attrs"]["src"].endswith(f"#t=0,{expected_source_out:g}"), clip_after
                assert abs(fd.clip_end(clip_after) - expected_timeline_end) < 0.001, clip_after
                trim_request = trim_requests[0]
                assert trim_request.get("project_id") == project_id, trim_request
                assert trim_request.get("base") == trim_before["rev"], trim_request
                assert trim_request.get("require_base") is True, trim_request
                assert film_edit_responses, "Trim HTTP response was not captured"
                trim_response = film_edit_responses[-1]
                trim_response_body = __import__("json").loads(trim_response["body"])
                assert trim_response["status"] == 200, trim_response
                assert trim_response_body.get("rev") == trim_after["rev"], {
                    "request": trim_request, "response": trim_response,
                    "disk_revision": trim_after["rev"]
                }
                trim_store = page.evaluate("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state ? {projectId:state.projectId,revision:state.revision,
                    busy:state.busy,pendingTransaction:state.pendingTransaction} : null;
                }""")
                assert trim_store and trim_store["projectId"] == project_id, trim_store
                assert trim_store["revision"] == trim_response_body["rev"] == fd.read_project_film(project_id)["rev"]
                assert trim_store["busy"] is False and trim_store["pendingTransaction"] is None

                # Undo and redo the committed Trim as one exact history transaction.
                trim_after_text = film_path.read_text(encoding="utf-8")
                undo_request_count = len(history_requests)
                undo_response_count = len(history_responses)
                page.evaluate("() => window.filmStudioUndo()")
                page.wait_for_function(
                    """revision => {
                      const state = window.filmStudioStore?.getSnapshot();
                      return !!state && !state.busy && state.pendingTransaction === null
                        && state.revision === revision;
                    }""",
                    arg=trim_before["rev"], timeout=10000,
                )
                _assert_history_four_way(
                    page, project_id, "undo", trim_after["rev"], trim_before["rev"],
                    history_requests[undo_request_count:], history_responses[undo_response_count:],
                )
                assert film_path.read_text(encoding="utf-8") == trim_before["text"]

                redo_request_count = len(history_requests)
                redo_response_count = len(history_responses)
                page.evaluate("() => window.filmStudioRedo()")
                page.wait_for_function(
                    """revision => {
                      const state = window.filmStudioStore?.getSnapshot();
                      return !!state && !state.busy && state.pendingTransaction === null
                        && state.revision === revision;
                    }""",
                    arg=trim_after["rev"], timeout=10000,
                )
                _assert_history_four_way(
                    page, project_id, "redo", trim_before["rev"], trim_after["rev"],
                    history_requests[redo_request_count:], history_responses[redo_response_count:],
                )
                assert film_path.read_text(encoding="utf-8") == trim_after_text

                # Trimming back in must remain supported after extending the same source window.
                second_trim_box = trim_handle.bounding_box()
                assert second_trim_box and second_trim_box["width"] > 0
                second_before = fd.read_project_film(project_id)
                second_clip_before = next(
                    clip for track in second_before["doc"]["tracks"] for clip in track["clips"]
                    if clip["id"] == "clip-b"
                )
                second_source_out = float(str(second_clip_before["attrs"]["src"]).rsplit("#t=", 1)[1].split(",", 1)[1])
                second_end = fd.clip_end(second_clip_before)
                second_request_count = len(film_edit_requests)
                second_trim_geometry_before = trim_clip.evaluate("(node) => node.style.width")
                sx = second_trim_box["x"] + second_trim_box["width"] / 2
                sy = second_trim_box["y"] + second_trim_box["height"] / 2
                page.mouse.move(sx, sy)
                page.mouse.down()
                page.mouse.move(sx - trim_scale * 0.5, sy, steps=5)
                page.wait_for_function("""args => {
                  const preview = window.filmStudioStore?.getSnapshot()?.timelineGesturePreview?.[args.id];
                  const node = document.querySelector('#fs-timeline .fs-clip[data-clip="' + args.id + '"]');
                  return !!preview && typeof preview.width === 'number'
                    && node?.dataset.reactGesturePreview === 'true'
                    && node.style.width !== args.widthBefore;
                }""", arg={"id":"clip-b","widthBefore":second_trim_geometry_before}, timeout=5000)
                assert fd.read_project_film(project_id)["rev"] == second_before["rev"]
                assert len(film_edit_requests) == second_request_count
                page.mouse.up()
                expected_second_end = second_end - 0.5
                expected_second_source_out = second_source_out - 0.5
                page.wait_for_function("""args => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const node = document.querySelector('#fs-timeline .fs-clip[data-clip="' + args.id + '"]');
                  return !!state && !state.busy && state.pendingTransaction === null
                    && node?.dataset.reactGesturePreview !== 'true';
                }""", arg={"id":"clip-b"}, timeout=15000)
                second_requests = film_edit_requests[second_request_count:]
                assert len(second_requests) == 1, second_requests
                second_ops = second_requests[0].get("ops") or []
                assert len(second_ops) == 1 and second_ops[0].get("op") == "trim", second_requests
                assert second_ops[0].get("edge") == "end", second_ops
                assert abs(float(second_ops[0].get("end", -1)) - expected_second_end) < 0.001, second_ops
                second_after = fd.read_project_film(project_id)
                second_clip_after = next(
                    clip for track in second_after["doc"]["tracks"] for clip in track["clips"]
                    if clip["id"] == "clip-b"
                )
                assert second_clip_after["attrs"]["src"].endswith(f"#t=0,{expected_second_source_out:g}"), second_clip_after
                assert abs(fd.clip_end(second_clip_after) - expected_second_end) < 0.001, second_clip_after
                assert not page_errors, f"browser JavaScript errors: {page_errors}"

                # Same-project keyboard copy/paste must preserve the source range
                # and speed, allocate a new stable ID, and avoid track overlap.
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').click()
                keyframe_marker = page.locator('#fs-timeline [data-film-keyframe-marker="clip-a"][data-keyframe-at="1"]')
                keyframe_marker.wait_for(state="visible", timeout=5000)
                keyframe_marker.click()
                assert abs(float(page.locator("#fs-playhead").input_value()) - 1.0) < 0.001
                before_paste = fd.read_project_film(project_id)
                before_count = sum(len(track["clips"]) for track in before_paste["doc"]["tracks"])
                page.keyboard.press("Control+C")
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('已复制 1 个片段')",
                    timeout=5000,
                )
                page.keyboard.press("Control+V")
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('已粘贴 1 个片段')",
                    timeout=15000,
                )
                pasted = fd.read_project_film(project_id)
                assert sum(len(track["clips"]) for track in pasted["doc"]["tracks"]) == before_count + 1
                video_track = next(track for track in pasted["doc"]["tracks"] if track["id"] == "video-main")
                copied_source_range = source_range(clips["clip-a"])
                copies = [
                    clip for clip in video_track["clips"]
                    if clip["id"] != "clip-a"
                    and all(abs(actual - expected) < 0.001
                            for actual, expected in zip(source_range(clip), copied_source_range))
                ]
                assert len(copies) == 1
                duplicate = copies[0]
                assert duplicate["attrs"]["speed"] == "2"
                assert abs(fd.clip_duration(duplicate) - fd.clip_duration(clips["clip-a"])) < 0.001
                for existing in video_track["clips"]:
                    if existing["id"] == duplicate["id"]:
                        continue
                    assert not (float(duplicate["attrs"]["at"]) < fd.clip_end(existing) - 0.001
                                and fd.clip_end(duplicate) > float(existing["attrs"]["at"]) + 0.001)

                # The clip context menu must invoke the same undoable duplicate transaction.
                page.locator('#fs-timeline .fs-clip[data-clip="' + duplicate["id"] + '"]').click(button="right")
                page.locator('.fs-context-menu [data-context-action="duplicate"]').wait_for(state="visible", timeout=5000)
                page.locator('.fs-context-menu [data-context-action="duplicate"]').click()
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('已粘贴 1 个片段')",
                    timeout=15000,
                )
                duplicated_again = fd.read_project_film(project_id)
                assert sum(len(track["clips"]) for track in duplicated_again["doc"]["tracks"]) == before_count + 2


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


CROSS_TRACK_SAMPLE = """<!doctype html>
<html><head><meta charset="utf-8"><style>body{margin:0}</style></head>
<body>
<section id="video-main" data-label="主画面">
  <video id="clip-a" src="../assets/a.mp4#t=0,5" at="0"></video>
</section>
<section id="video-alt" data-label="备用画面">
  <video id="clip-c" src="../assets/a.mp4#t=0,5" at="0"></video>
</section>
</body></html>"""


@pytest.mark.parametrize(
    "target_scenario", ["valid-target", "locked-target", "overlap-target"]
)
def test_film_studio_cross_track_drag_shows_react_ghost_and_commits_target_track(tmp_path, monkeypatch, target_scenario):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    asset_root = storage_root / "assets"
    asset_root.mkdir(parents=True, exist_ok=True)
    from video_director import renderer
    generated = subprocess.run(
        [renderer._ffmpeg(), "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=20",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", "-y",
         str(asset_root / "a.mp4")],
        capture_output=True, text=True, timeout=60,
    )
    assert generated.returncode == 0, generated.stderr

    target_locked = target_scenario == "locked-target"
    project_id = "film-studio-cross-track-browser" + ("" if target_scenario == "valid-target" else "-" + target_scenario)
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_source = CROSS_TRACK_SAMPLE
    if target_locked:
        film_source = film_source.replace(
            '<section id="video-alt" data-label="备用画面">',
            '<section id="video-alt" data-label="备用画面" locked>',
        )
        assert film_source != CROSS_TRACK_SAMPLE
    (project_root / fd.FILM_NAME).write_text(film_source, encoding="utf-8")
    before = fd.read_project_film(project_id)
    assert before["exists"] and before["doc"]["tracks"]

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
                film_edit_requests: list[dict] = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))

                def capture_film_edit_request(request):
                    if request.method == "POST" and request.url.endswith("/api/editor/film/edit"):
                        try:
                            film_edit_requests.append(request.post_data_json)
                        except Exception:
                            film_edit_requests.append({"raw": request.post_data})

                page.on("request", capture_film_edit_request)
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
                  if (!select) throw new Error('project selector is missing');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                }""", project_id)
                page.evaluate("window.filmStudioToggle()")
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                if os.environ.get("VIDEO_DIRECTOR_REQUIRE_REACT_STUDIO") == "1":
                    page.locator('#fs-react-timeline-toolbar [data-film-action="split"]').wait_for(state="visible")
                    assert not page.locator("#fs-static-timeline-toolbar").is_visible()

                source_clip = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]')
                target_track = page.locator('#fs-timeline .fs-track[data-track="video-alt"]')
                target_body = target_track.locator(".fs-track-body")
                source_clip.wait_for(state="visible")
                target_body.wait_for(state="visible")
                target_body.scroll_into_view_if_needed()
                # Resolve geometry only after scrolling has settled, so the pointer
                # starts on clip-a and finishes inside the alternate video track.
                source_box = source_clip.bounding_box()
                target_box = target_body.bounding_box()
                assert source_box and target_box and source_box["width"] > 0 and target_box["height"] > 0
                scale = float(page.evaluate(
                    "window.filmStudioStore?.getSnapshot()?.pixelsPerSecond || 36"
                ))
                start_x = source_box["x"] + source_box["width"] * 0.55
                start_y = source_box["y"] + source_box["height"] / 2
                target_y = target_box["y"] + target_box["height"] / 2
                # A valid move lands at 5s (touching clip-c's end); the overlap case
                # deliberately lands at 2s inside clip-c's [0,5) interval.
                target_delta_seconds = 2 if target_scenario == "overlap-target" else 5
                target_x = start_x + target_delta_seconds * scale
                request_count = len(film_edit_requests)
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(target_x, target_y, steps=12)

                if target_scenario != "valid-target":
                    expected_invalid_reason = "已锁定" if target_locked else "时间重叠"
                    page.wait_for_function("""(expectedReason) => {
                      const state = window.filmStudioStore?.getSnapshot();
                      const drop = state?.timelineDropTarget;
                      const row = document.querySelector('#fs-timeline .fs-track[data-track="video-alt"]');
                      const message = state?.timelineGestureContext?.invalidTargetMessage || '';
                      return drop?.trackId === 'video-alt' && drop.invalid === true
                        && row?.classList.contains('fs-track-drop-invalid')
                        && message.includes(expectedReason);
                    }""", arg=expected_invalid_reason, timeout=8000)
                    invalid_state = page.evaluate("""() => ({
                      message:window.filmStudioStore?.getSnapshot()?.timelineGestureContext?.invalidTargetMessage || '',
                      drop:window.filmStudioStore?.getSnapshot()?.timelineDropTarget,
                      ghosts:[...document.querySelectorAll('#fs-timeline [data-gesture-ghost-for="clip-a"]')]
                        .map(node => node.getAttribute('data-gesture-target-track')),
                    })""")
                    assert expected_invalid_reason in invalid_state['message'], invalid_state
                    assert invalid_state['drop'] == {"trackId": "video-alt", "invalid": True}, invalid_state
                    assert 'video-alt' not in invalid_state['ghosts'], invalid_state
                    assert fd.read_project_film(project_id)['rev'] == before['rev'], "invalid preview must not write FilmDocument"
                    assert len(film_edit_requests) == request_count, film_edit_requests[request_count:]

                    page.mouse.up()
                    page.wait_for_function("""() => {
                      const state = window.filmStudioStore?.getSnapshot();
                      return !!state && state.timelineGestureContext === null
                        && state.timelineGesturePreview === null
                        && state.timelineDropTarget === null
                        && state.pendingTransaction === null;
                    }""", timeout=5000)
                    persisted = fd.read_project_film(project_id)
                    source_track = next(track for track in persisted["doc"]["tracks"] if track["id"] == "video-main")
                    target_track_doc = next(track for track in persisted["doc"]["tracks"] if track["id"] == "video-alt")
                    assert persisted["rev"] == before["rev"], persisted
                    assert any(clip["id"] == "clip-a" for clip in source_track["clips"])
                    assert not any(clip["id"] == "clip-a" for clip in target_track_doc["clips"])
                    assert not film_edit_requests[request_count:], film_edit_requests[request_count:]
                    assert not page_errors, f"browser JavaScript errors: {page_errors}"
                    return

                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const preview = state?.timelineGesturePreview?.['clip-a'];
                  const drop = state?.timelineDropTarget;
                  const ghost = document.querySelector(
                    '#fs-timeline [data-gesture-ghost-for="clip-a"][data-gesture-target-track="video-alt"]'
                  );
                  const row = document.querySelector('#fs-timeline .fs-track[data-track="video-alt"]');
                  return preview?.targetTrackId === 'video-alt'
                    && Math.abs(Number(preview.left) - 180) < 1
                    && drop?.trackId === 'video-alt' && drop.invalid === false
                    && !!ghost && row?.classList.contains('fs-track-drop-target');
                }""", timeout=8000)

                preview_state = page.evaluate("""() => ({
                  preview:window.filmStudioStore?.getSnapshot()?.timelineGesturePreview?.['clip-a'],
                  drop:window.filmStudioStore?.getSnapshot()?.timelineDropTarget,
                  ghost:[...document.querySelectorAll('#fs-timeline [data-gesture-ghost-for="clip-a"]')]
                    .map(node => ({track:node.getAttribute('data-gesture-target-track'),left:node.style.left}))
                })""")
                assert preview_state["preview"]["targetTrackId"] == "video-alt", preview_state
                assert preview_state["drop"] == {"trackId": "video-alt", "invalid": False}, preview_state
                assert preview_state["ghost"] and preview_state["ghost"][0]["track"] == "video-alt", preview_state
                assert fd.read_project_film(project_id)["rev"] == before["rev"], "drag preview must not write FilmDocument"
                assert len(film_edit_requests) == request_count, film_edit_requests[request_count:]

                page.mouse.up()
                page.wait_for_function("""async (pid) => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(pid));
                  if (!response.ok) return false;
                  const doc = await response.json();
                  const tracks = doc.tracks || [];
                  const source = tracks.find(track => track.id === 'video-main');
                  const target = tracks.find(track => track.id === 'video-alt');
                  return !!source && !!target
                    && !(source.clips || []).some(clip => clip.id === 'clip-a')
                    && (target.clips || []).some(clip =>
                      clip.id === 'clip-a' && Math.abs(Number(clip.attrs.at) - 5) < 0.001
                    );
                }""", arg=project_id, timeout=15000)

                committed_requests = film_edit_requests[request_count:]
                assert len(committed_requests) == 1, committed_requests
                operations = committed_requests[0].get("ops") or []
                assert operations == [{
                    "op": "move", "clip": "clip-a", "track_id": "video-alt", "at": 5
                }], committed_requests

                persisted = fd.read_project_film(project_id)
                source_track = next(track for track in persisted["doc"]["tracks"] if track["id"] == "video-main")
                target_track_doc = next(track for track in persisted["doc"]["tracks"] if track["id"] == "video-alt")
                assert not any(clip["id"] == "clip-a" for clip in source_track["clips"])
                moved = next(clip for clip in target_track_doc["clips"] if clip["id"] == "clip-a")
                assert abs(float(moved["attrs"]["at"]) - 5) < 0.001, moved
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return !!state && state.pendingTransaction === null
                    && state.timelineGesturePreview === null
                    && state.timelineDropTarget === null;
                }""", timeout=5000)
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
