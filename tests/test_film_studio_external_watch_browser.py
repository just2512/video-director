"""Real-browser regression for external Agent edits to film.html."""
from __future__ import annotations

import importlib
import json
import os
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import quote as quote_for_test

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


SNAP_GESTURE_SAMPLE = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"><style>body{margin:0}</style></head>
<body>
<section id="video-main" data-label="主画面">
<video id="clip-left" src="../assets/shared.mp4#t=0,5" at="0"></video>
<video id="clip-center" src="../assets/shared.mp4#t=5,10" at="5"></video>
<video id="clip-right" src="../assets/shared.mp4#t=10,15" at="10"></video>
</section>
<section id="audio-bed" data-label="BGM">
<audio id="snap-audio" src="../assets/music.wav#t=0,2" at="6" volume="0.2"></audio>
</section>
</body></html>'''


def test_react_roll_and_slide_snap_previews_use_resolved_candidates(tmp_path, monkeypatch):
    """A resolved snap candidate renders in React for both roll and slide gestures."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-roll-slide-snap-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(SNAP_GESTURE_SAMPLE, encoding="utf-8")
    initial = fd.read_project_film(project_id)
    assert initial["exists"]

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

            def capture_edit(request):
                if request.url.endswith("/api/editor/film/edit"):
                    try:
                        film_edit_requests.append(json.loads(request.post_data or "{}"))
                    except json.JSONDecodeError:
                        film_edit_requests.append({"_invalid_json": True})

            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("request", capture_edit)
            try:
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="domcontentloaded",
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.filmStudioToggle === 'function'")
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function(
                    "() => { const select=document.querySelector('#editor-project'); return !!select && select.options.length>0; }"
                )
                page.evaluate("""pid => {
                  const select=document.querySelector('#editor-project');
                  let option=[...select.options].find(item=>item.value===pid);
                  if(!option){option=new Option(pid,pid);select.add(option);}
                  select.value=pid;
                  select.dispatchEvent(new Event('change',{bubbles:true}));
                }""", project_id)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                for clip_id in ("clip-left", "clip-center", "clip-right", "snap-audio"):
                    page.locator('#fs-timeline .fs-clip[data-clip="' + clip_id + '"]').wait_for(state="visible")

                # Mode classes are projected from FilmStore on the React surface;
                # normal React mode changes must not mutate the legacy outer panel.
                mode_revision_before = fd.read_project_film(project_id)["rev"]
                envelope_button = page.locator('#fs-react-timeline-toolbar [data-film-action="envelope-mode"]')
                envelope_button.click()
                page.wait_for_function("""() => {
                  const surface = document.querySelector('.fs-react-timeline-surface');
                  const panel = document.querySelector('#film-studio-panel');
                  const title = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-center"] .fs-clip-title');
                  const button = document.querySelector('#fs-react-timeline-toolbar [data-film-action="envelope-mode"]');
                  return surface?.classList.contains('fs-envelope-mode') &&
                    surface.dataset.envelopeMode === 'true' &&
                    !panel?.classList.contains('fs-envelope-mode') &&
                    button?.getAttribute('aria-pressed') === 'true' &&
                    !!title && getComputedStyle(title).pointerEvents === 'none';
                }""", timeout=5000)
                envelope_button.click()
                page.wait_for_function("""() => {
                  const surface = document.querySelector('.fs-react-timeline-surface');
                  const title = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-center"] .fs-clip-title');
                  const button = document.querySelector('#fs-react-timeline-toolbar [data-film-action="envelope-mode"]');
                  return !surface?.classList.contains('fs-envelope-mode') &&
                    surface?.dataset.envelopeMode === 'false' &&
                    button?.getAttribute('aria-pressed') === 'false' &&
                    !!title && getComputedStyle(title).pointerEvents !== 'none';
                }""", timeout=5000)

                slide_button = page.locator('#fs-react-timeline-toolbar [data-film-action="slide-mode"]')
                slide_button.click()
                page.wait_for_function("""() => {
                  const surface = document.querySelector('.fs-react-timeline-surface');
                  const panel = document.querySelector('#film-studio-panel');
                  const clip = document.querySelector('#fs-timeline .fs-clip[data-kind="video"]');
                  const button = document.querySelector('#fs-react-timeline-toolbar [data-film-action="slide-mode"]');
                  return surface?.classList.contains('fs-slide-mode') &&
                    surface.dataset.slideMode === 'true' &&
                    !panel?.classList.contains('fs-slide-mode') &&
                    button?.getAttribute('aria-pressed') === 'true' &&
                    !!clip && getComputedStyle(clip).cursor === 'ew-resize';
                }""", timeout=5000)
                slide_button.click()
                page.wait_for_function("""() => {
                  const surface = document.querySelector('.fs-react-timeline-surface');
                  const button = document.querySelector('#fs-react-timeline-toolbar [data-film-action="slide-mode"]');
                  return !surface?.classList.contains('fs-slide-mode') &&
                    surface?.dataset.slideMode === 'false' &&
                    button?.getAttribute('aria-pressed') === 'false';
                }""", timeout=5000)

                slip_button = page.locator('#fs-react-timeline-toolbar [data-film-action="slip-mode"]')
                slip_button.click()
                page.wait_for_function("""() => {
                  const surface = document.querySelector('.fs-react-timeline-surface');
                  const panel = document.querySelector('#film-studio-panel');
                  const clip = document.querySelector('#fs-timeline .fs-clip[data-kind="audio"]');
                  const button = document.querySelector('#fs-react-timeline-toolbar [data-film-action="slip-mode"]');
                  return surface?.classList.contains('fs-slip-mode') &&
                    surface.dataset.slipMode === 'true' &&
                    !panel?.classList.contains('fs-slip-mode') &&
                    button?.getAttribute('aria-pressed') === 'true' &&
                    !!clip && getComputedStyle(clip).cursor === 'ew-resize';
                }""", timeout=5000)
                slip_button.click()
                page.wait_for_function("""() => {
                  const surface = document.querySelector('.fs-react-timeline-surface');
                  const button = document.querySelector('#fs-react-timeline-toolbar [data-film-action="slip-mode"]');
                  return !surface?.classList.contains('fs-slip-mode') &&
                    surface?.dataset.slipMode === 'false' &&
                    button?.getAttribute('aria-pressed') === 'false';
                }""", timeout=5000)
                assert not page.locator("#film-studio-panel").evaluate(
                    "(node) => ['fs-envelope-mode','fs-slide-mode','fs-slip-mode'].some(name => node.classList.contains(name))"
                )
                assert fd.read_project_film(project_id)["rev"] == mode_revision_before

                # If no clip/playhead/head target is inside the snap zone, move preview falls back to the frame grid.
                frame_clip = page.locator('#fs-timeline .fs-clip[data-clip="clip-right"]')
                frame_clip.click()
                frame_box = frame_clip.bounding_box()
                assert frame_box, "clip-right bounding box unavailable for frame snap test"
                frame_doc_before = fd.read_project_film(project_id)
                frame_clip_before = next(
                    item for track in frame_doc_before["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-right"
                )
                frame_left_before = frame_clip.evaluate("(node) => node.style.left")
                frame_edit_count = len(film_edit_requests)
                frame_x = frame_box["x"] + frame_box["width"] * 0.5
                frame_y = frame_box["y"] + frame_box["height"] * 0.5
                page.mouse.move(frame_x, frame_y)
                page.mouse.down()
                page.mouse.move(frame_x + 16, frame_y, steps=6)
                page.wait_for_function("""oldLeft => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const guide = state?.timelineSnapGuide;
                  const preview = state?.timelineGesturePreview?.['clip-right'];
                  const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-right"]');
                  const fps = Number(state?.film?.fps || 30);
                  return guide?.kind === '帧' && preview?.left != null &&
                    node?.dataset.reactGesturePreview === 'true' &&
                    node.style.left !== oldLeft &&
                    Math.abs(guide.at * fps - Math.round(guide.at * fps)) < 0.00001;
                }""", arg=frame_left_before, timeout=5000)
                frame_preview_data = page.evaluate("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-right"]');
                  const fps = Number(state?.film?.fps || 30);
                  return {guide:state?.timelineSnapGuide, left:node?.style.left, fps};
                }""")
                assert fd.read_project_film(project_id)["rev"] == frame_doc_before["rev"]
                assert len(film_edit_requests) == frame_edit_count
                page.mouse.up()
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state?.timelineGestureContext === null &&
                    state?.timelineGesturePreview === null &&
                    state?.timelineSnapGuide === null;
                }""", timeout=10000)
                page.wait_for_function("""() => {
                  const film = window.filmStudioStore?.getSnapshot()?.film;
                  const clips = (film?.tracks || []).flatMap(track => track.clips || []);
                  return Number(clips.find(clip => clip.id === "clip-right")?.at) > 10;
                }""", timeout=10000)
                frame_doc_after = fd.read_project_film(project_id)
                frame_clip_after = next(
                    item for track in frame_doc_after["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-right"
                )
                frame_requests = film_edit_requests[frame_edit_count:]
                assert len(frame_requests) == 1, frame_requests
                assert len(frame_requests[0].get("ops") or []) == 1, frame_requests
                assert frame_requests[0]["ops"][0].get("op") == "move", frame_requests
                assert abs(float(frame_clip_after["attrs"]["at"]) - frame_preview_data["guide"]["at"]) < 0.001, frame_clip_after
                assert abs(frame_preview_data["guide"]["at"] * frame_preview_data["fps"] - round(frame_preview_data["guide"]["at"] * frame_preview_data["fps"])) < 0.00001
                assert float(frame_clip_after["attrs"]["at"]) > float(frame_clip_before["attrs"]["at"]), frame_clip_after
                page.evaluate("window.filmStudioUndo()")
                page.wait_for_function("""oldLeft => {
                  const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-right"]');
                  const film = window.filmStudioStore?.getSnapshot()?.film;
                  const clips = (film?.tracks || []).flatMap(track => track.clips || []);
                  const clip = clips.find(item => item.id === "clip-right");
                  return !!node && node.style.left === oldLeft && Math.abs(Number(clip?.at) - 10) < 0.001;
                }""", arg=frame_left_before, timeout=10000)
                frame_doc_undone = fd.read_project_film(project_id)
                frame_clip_undone = next(
                    item for track in frame_doc_undone["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-right"
                )
                assert abs(float(frame_clip_undone["attrs"]["at"]) - float(frame_clip_before["attrs"]["at"])) < 0.001

                # Trim-end must use the same fps fallback as move when no explicit snap target is near.
                trim_handle = page.locator('#fs-timeline .fs-clip[data-clip="clip-right"] .fs-trim-handle[data-trim="end"]')
                trim_box = trim_handle.bounding_box()
                assert trim_box, "clip-right trim-end handle unavailable"
                trim_before = fd.read_project_film(project_id)
                trim_before_clip = next(
                    item for track in trim_before["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-right"
                )
                trim_end_before = fd.clip_end(trim_before_clip)
                trim_width_before = page.locator('#fs-timeline .fs-clip[data-clip="clip-right"]').evaluate("(node) => node.style.width")
                trim_request_count = len(film_edit_requests)
                trim_x = trim_box["x"] + trim_box["width"] / 2
                trim_y = trim_box["y"] + trim_box["height"] / 2
                page.mouse.move(trim_x, trim_y)
                page.mouse.down()
                page.mouse.move(trim_x - 13, trim_y, steps=6)
                page.wait_for_function(
                    """oldWidth => {
                      const state = window.filmStudioStore?.getSnapshot();
                      const guide = state?.timelineSnapGuide;
                      const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-right"]');
                      const preview = state?.timelineGesturePreview?.['clip-right'];
                      const fps = Number(state?.film?.fps || 30);
                      return guide?.kind === '帧' && preview?.width != null &&
                        node?.dataset.reactGesturePreview === 'true' && node.style.width !== oldWidth &&
                        Math.abs(guide.at * fps - Math.round(guide.at * fps)) < 0.00001;
                    }""",
                    arg=trim_width_before,
                    timeout=5000,
                )
                trim_preview = page.evaluate("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return {guide:state?.timelineSnapGuide,fps:Number(state?.film?.fps || 30)};
                }""")
                assert fd.read_project_film(project_id)["rev"] == trim_before["rev"]
                assert len(film_edit_requests) == trim_request_count
                page.mouse.up()
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state?.timelineGestureContext === null &&
                    state?.timelineGesturePreview === null &&
                    state?.timelineSnapGuide === null;
                }""", timeout=10000)
                trim_after = fd.read_project_film(project_id)
                trim_after_clip = next(
                    item for track in trim_after["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-right"
                )
                assert abs(fd.clip_end(trim_after_clip) - trim_preview["guide"]["at"]) < 0.001, trim_after_clip
                assert abs(fd.clip_end(trim_after_clip) * trim_preview["fps"] - round(fd.clip_end(trim_after_clip) * trim_preview["fps"])) < 0.00002
                trim_requests = film_edit_requests[trim_request_count:]
                assert len(trim_requests) == 1, trim_requests
                assert len(trim_requests[0].get("ops") or []) == 1, trim_requests
                assert trim_requests[0]["ops"][0].get("op") == "trim", trim_requests
                assert trim_requests[0]["ops"][0].get("edge") == "end", trim_requests
                page.evaluate("window.filmStudioUndo()")
                page.wait_for_function("""expectedEnd => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const clips = (state?.film?.tracks || []).flatMap(track => track.clips || []);
                  const clip = clips.find(item => item.id === 'clip-right');
                  return !!clip && Math.abs(Number(clip.end) - expectedEnd) < 0.001;
                }""", arg=trim_end_before, timeout=10000)
                trim_undone = fd.read_project_film(project_id)
                trim_undone_clip = next(
                    item for track in trim_undone["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-right"
                )
                assert abs(fd.clip_end(trim_undone_clip) - trim_end_before) < 0.001

                # Slide also falls back to the frame grid when the explicit neighbours are outside the 8 px snap zone.
                slide_button = page.locator('#fs-react-timeline-toolbar [data-film-action="slide-mode"]')
                if slide_button.get_attribute("aria-pressed") == "true":
                    slide_button.click()
                    page.wait_for_function("""() => document.querySelector('#fs-react-timeline-toolbar [data-film-action="slide-mode"]')?.getAttribute('aria-pressed') === 'false'""", timeout=5000)
                slide_button.click()
                page.wait_for_function("""() => document.querySelector('#fs-react-timeline-toolbar [data-film-action="slide-mode"]')?.getAttribute('aria-pressed') === 'true'""", timeout=5000)
                slide_before = fd.read_project_film(project_id)
                slide_clips_before = {
                    item["id"]: item
                    for track in slide_before["doc"]["tracks"]
                    for item in track["clips"]
                }
                slide_geometry_before = page.evaluate("""() => Object.fromEntries(
                  ['clip-left','clip-center','clip-right'].map(id => {
                    const node = document.querySelector('#fs-timeline .fs-clip[data-clip="' + id + '"]');
                    return [id,{left:node?.style.left,width:node?.style.width}];
                  })
                )""")
                slide_request_count = len(film_edit_requests)
                slide_center = page.locator('#fs-timeline .fs-clip[data-clip="clip-center"]')
                slide_box = slide_center.bounding_box()
                assert slide_box, "clip-center unavailable for frame-slide test"
                slide_x = slide_box["x"] + slide_box["width"] / 2
                slide_y = slide_box["y"] + slide_box["height"] / 2
                page.mouse.move(slide_x, slide_y)
                page.mouse.down()
                page.mouse.move(slide_x + 13, slide_y, steps=6)
                page.wait_for_function("""before => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const guide = state?.timelineSnapGuide;
                  const preview = state?.timelineGesturePreview || {};
                  const clips = ['clip-left','clip-center','clip-right'].map(id =>
                    document.querySelector('#fs-timeline .fs-clip[data-clip="' + id + '"]'));
                  const fps = Number(state?.film?.fps || 30);
                  return guide?.kind === '帧' && preview['clip-left']?.width != null &&
                    preview['clip-center']?.left != null && preview['clip-right']?.left != null &&
                    clips.every((node,index) => node?.dataset.reactGesturePreview === 'true' &&
                      (node.style.left !== before[['clip-left','clip-center','clip-right'][index]].left ||
                       node.style.width !== before[['clip-left','clip-center','clip-right'][index]].width)) &&
                    Math.abs(guide.at * fps - Math.round(guide.at * fps)) < 0.00001;
                }""", arg=slide_geometry_before, timeout=5000)
                slide_preview = page.evaluate("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return {guide:state?.timelineSnapGuide,fps:Number(state?.film?.fps || 30)};
                }""")
                assert fd.read_project_film(project_id)["rev"] == slide_before["rev"]
                assert len(film_edit_requests) == slide_request_count
                page.mouse.up()
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state?.timelineGestureContext === null &&
                    state?.timelineGesturePreview === null &&
                    state?.timelineSnapGuide === null;
                }""", timeout=10000)
                slide_after = fd.read_project_film(project_id)
                slide_clips_after = {
                    item["id"]: item
                    for track in slide_after["doc"]["tracks"]
                    for item in track["clips"]
                }
                assert abs(float(slide_clips_after["clip-center"]["attrs"]["at"]) - slide_preview["guide"]["at"]) < 0.001
                assert abs(float(slide_clips_after["clip-left"]["attrs"]["at"]) - float(slide_clips_before["clip-left"]["attrs"]["at"])) < 0.001
                assert abs(fd.clip_end(slide_clips_after["clip-left"]) - slide_preview["guide"]["at"]) < 0.001
                assert abs(fd.clip_end(slide_clips_after["clip-right"]) - fd.clip_end(slide_clips_before["clip-right"])) < 0.001
                assert abs(float(slide_clips_after["clip-center"]["attrs"]["at"]) * slide_preview["fps"] - round(float(slide_clips_after["clip-center"]["attrs"]["at"]) * slide_preview["fps"])) < 0.00002
                slide_requests = film_edit_requests[slide_request_count:]
                assert len(slide_requests) == 1, slide_requests
                assert len(slide_requests[0].get("ops") or []) == 1, slide_requests
                assert slide_requests[0]["ops"][0].get("op") == "slide", slide_requests
                page.evaluate("window.filmStudioUndo()")
                page.wait_for_function("""expected => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const clips = (state?.film?.tracks || []).flatMap(track => track.clips || []);
                  const center = clips.find(item => item.id === 'clip-center');
                  return !!center && Math.abs(Number(center.at) - expected) < 0.001;
                }""", arg=float(slide_clips_before["clip-center"]["attrs"]["at"]), timeout=10000)
                slide_undone = fd.read_project_film(project_id)
                slide_clips_undone = {
                    item["id"]: item
                    for track in slide_undone["doc"]["tracks"]
                    for item in track["clips"]
                }
                for clip_id in ("clip-left","clip-center","clip-right"):
                    assert slide_clips_undone[clip_id]["attrs"] == slide_clips_before[clip_id]["attrs"], clip_id
                slide_button.click()
                page.wait_for_function("""() => document.querySelector('#fs-react-timeline-toolbar [data-film-action="slide-mode"]')?.getAttribute('aria-pressed') === 'false'""", timeout=5000)

                # Ctrl-drag the left clip's end through a shared-source cut to 6s,
                # where the separate audio clip supplies the snap candidate.
                left_end = page.locator('#fs-timeline .fs-clip[data-clip="clip-left"] .fs-trim-handle[data-trim="end"]')
                bounds = left_end.bounding_box()
                assert bounds, "roll trim handle unavailable"
                before_roll = fd.read_project_film(project_id)
                roll_request_count = len(film_edit_requests)
                x, y = bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2
                page.keyboard.down("Control")
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + 36, y, steps=8)
                page.wait_for_function("""() => {
                  const state=window.filmStudioStore?.getSnapshot();
                  const guide=state?.timelineSnapGuide;
                  const preview=state?.timelineGesturePreview?.['clip-left'];
                  return guide?.kind === '片段入点' && Math.abs(guide.at - 6) < 0.001 &&
                    preview?.mode === 'roll' &&
                    document.querySelector('#fs-snap-guide')?.dataset.snapKind === '片段入点';
                }""", timeout=5000)
                assert len(film_edit_requests) == roll_request_count
                assert fd.read_project_film(project_id)["rev"] == before_roll["rev"]
                page.mouse.up()
                page.keyboard.up("Control")
                page.wait_for_function("""() => {
                  const state=window.filmStudioStore?.getSnapshot();
                  return state?.timelineGestureContext === null &&
                    state?.timelineGesturePreview === null &&
                    state?.timelineSnapGuide === null;
                }""", timeout=10000)
                after_roll = fd.read_project_film(project_id)
                roll_requests = film_edit_requests[roll_request_count:]
                assert after_roll["rev"] != before_roll["rev"]
                assert len(roll_requests) == 1, roll_requests
                roll_ops = roll_requests[0].get("ops") or []
                assert len(roll_ops) == 1 and roll_ops[0].get("op") == "roll", roll_ops
                assert abs(float(roll_ops[0].get("delta")) - 1.0) < 0.05, roll_ops
                page.evaluate("window.filmStudioUndo()")
                page.wait_for_function("""() => {
                  const state=window.filmStudioStore?.getSnapshot();
                  const node=document.querySelector('#fs-timeline .fs-clip[data-clip="clip-left"]');
                  // Transient gesture state is cleared at pointerup; wait for the actual
                  // undo render as well, which restores the implicit 5s source range to 180px.
                  return state?.timelineGesturePreview === null &&
                    state?.timelineSnapGuide === null &&
                    !!node && node.style.width === "180px";
                }""", timeout=10000)
                undone_roll = fd.read_project_film(project_id)
                left_after_undo = next(
                    clip for track in undone_roll["doc"]["tracks"]
                    for clip in track["clips"] if clip["id"] == "clip-left"
                )
                # The original source range is encoded in src="#t=0,5"; undo must
                # restore that range without materializing a new explicit end attribute.
                assert "#t=0,5" in str(left_after_undo["attrs"].get("src", "")), left_after_undo
                if "end" in left_after_undo["attrs"]:
                    assert abs(float(left_after_undo["attrs"]["end"]) - 5.0) < 0.001, left_after_undo

                # Alt+Shift slide the center clip through the same 6s candidate.
                page.locator('#fs-timeline .fs-clip[data-clip="clip-center"]').click()
                page.evaluate("window.filmStudioSetSlideMode(true)")
                center = page.locator('#fs-timeline .fs-clip[data-clip="clip-center"]')
                # A click under slide mode must still seek; the subsequent actual
                # pointer drag preserves that playhead as its snap anchor.
                page.evaluate("window.filmStudioSeek(1)")
                center.click()
                page.wait_for_function(
                    "() => Math.abs((window.filmStudioStore?.getSnapshot()?.playhead ?? -1) - 5) < 0.001",
                    timeout=5000,
                )
                center_box = center.bounding_box()
                assert center_box, "slide clip unavailable"
                before_slide = fd.read_project_film(project_id)
                slide_request_count = len(film_edit_requests)
                x, y = center_box["x"] + center_box["width"] / 2, center_box["y"] + center_box["height"] / 2
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + 36, y, steps=8)
                page.wait_for_function("""() => {
                  const state=window.filmStudioStore?.getSnapshot();
                  const guide=state?.timelineSnapGuide;
                  const preview=state?.timelineGesturePreview?.['clip-center'];
                  return guide?.kind === '片段入点' && Math.abs(guide.at - 6) < 0.001 &&
                    preview?.mode === 'slide' &&
                    document.querySelector('#fs-snap-guide')?.dataset.snapKind === '片段入点';
                }""", timeout=5000)
                assert len(film_edit_requests) == slide_request_count
                assert fd.read_project_film(project_id)["rev"] == before_slide["rev"]
                page.mouse.up()
                page.wait_for_function("""() => {
                  const state=window.filmStudioStore?.getSnapshot();
                  return state?.timelineGestureContext === null &&
                    state?.timelineGesturePreview === null &&
                    state?.timelineSnapGuide === null;
                }""", timeout=10000)
                after_slide = fd.read_project_film(project_id)
                slide_requests = film_edit_requests[slide_request_count:]
                assert after_slide["rev"] != before_slide["rev"]
                assert len(slide_requests) == 1, slide_requests
                slide_ops = slide_requests[0].get("ops") or []
                assert len(slide_ops) == 1 and slide_ops[0].get("op") == "slide", slide_ops
                assert abs(float(slide_ops[0].get("delta")) - 1.0) < 0.05, slide_ops
                assert not page_errors, page_errors
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_studio_export_result_exposes_pinned_revision_mp4_and_qc_links(tmp_path, monkeypatch):
    """A stale but completed export stays accessible and is not mislabeled as current."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-render-result-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    before = fd.read_project_film(project_id)
    assert before["exists"]

    render_requests: list[dict] = []
    status_requests = 0
    run_id = "render-result-ui-contract"
    mp4_path = str(project_root / "film-final-pinned-revision.mp4")
    qc_path = str(project_root / "film-qc-pinned-revision.json")

    def handle_render(route):
        payload = json.loads(route.request.post_data or "{}")
        render_requests.append(payload)
        route.fulfill(
            status=202,
            content_type="application/json",
            body=json.dumps({
                "ok": True,
                "run_id": run_id,
                "project_id": project_id,
                "expected_revision": before["rev"],
            }),
        )

    def handle_workflow(route):
        nonlocal status_requests
        status_requests += 1
        if status_requests == 1:
            response = {
                "run_id": run_id,
                "status": "RUNNING",
                "current": "render",
                "result": {},
            }
        else:
            response = {
                "run_id": run_id,
                "status": "COMPLETED",
                "current": None,
                "result": {
                    "ok": True,
                    "verified": True,
                    "mp4_path": mp4_path,
                    "qc_report_path": qc_path,
                    "rendered_revision": before["rev"],
                    "current_revision": "external-revision-after-render",
                    "current": False,
                    "width": 1920,
                    "height": 1080,
                    "duration": 5.0,
                    "frame_count": 150,
                    "qc_status": "PASS",
                    "determinism_status": "PASS",
                    "audio_signal_status": "AUDIBLE",
                    "warnings": ["导出期间影片文档已变化"],
                },
            }
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(response, ensure_ascii=False),
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
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.route("**/api/editor/film/render", handle_render)
            page.route("**/api/workflows/" + run_id, handle_workflow)
            try:
                page.set_default_navigation_timeout(10_000)
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="domcontentloaded",
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.filmStudioToggle === 'function'")
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function(
                    "() => { const select=document.querySelector('#editor-project'); return !!select && select.options.length>0; }"
                )
                page.evaluate("""pid => {
                  const select=document.querySelector('#editor-project');
                  let option=[...select.options].find(item=>item.value===pid);
                  if(!option){option=new Option(pid,pid);select.add(option);}
                  select.value=pid;
                  select.dispatchEvent(new Event('change',{bubbles:true}));
                }""", project_id)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').wait_for(state="visible")

                render_button = page.locator('[data-film-action="render"]:visible')
                assert render_button.count() == 1
                render_button.click()
                page.wait_for_function(
                    "() => window.filmStudioStore?.getField('rendering') === true",
                    timeout=5000,
                )
                page.wait_for_function(
                    """() => [...document.querySelectorAll('[data-film-action="render"]')]
                      .some(button => button.getClientRects().length > 0 && button.disabled)""",
                    timeout=5_000,
                )
                assert render_button.is_disabled(), "React toolbar must disable a duplicate export while rendering"
                page.locator("#fs-render-result").wait_for(state="visible", timeout=10_000)
                page.wait_for_function(
                    "() => window.filmStudioStore?.getField('rendering') === false",
                    timeout=5000,
                )

                assert render_requests == [{
                    "project_id": project_id,
                    "expected_revision": before["rev"],
                }], render_requests
                assert status_requests >= 2, status_requests
                assert render_button.is_enabled()

                result_text = page.locator("#fs-render-result-text").inner_text()
                assert "1920×1080" in result_text, result_text
                assert "5.00 秒" in result_text, result_text
                assert before["rev"] in result_text, result_text
                assert "QC PASS" in result_text, result_text

                mp4_link = page.locator("#fs-render-download")
                assert mp4_link.is_visible()
                assert mp4_link.inner_text() == "打开该版本 MP4（文档已更新）"
                assert mp4_link.get_attribute("href") == "/file?path=" + quote_for_test(mp4_path, safe="")

                qc_link = page.locator("#fs-qc-download")
                assert qc_link.is_visible()
                assert qc_link.inner_text() == "打开 QC 记录（PASS）"
                assert qc_link.get_attribute("href") == "/file?path=" + quote_for_test(qc_path, safe="")

                alert = page.locator("#film-studio-alert").inner_text()
                assert "导出期间影片文档发生变化" in alert, alert
                assert before["rev"] in alert, alert
                assert "不是当前 revision" in alert, alert
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
              window.__filmDocumentReadBodies = [];
              window.__compositionReadUrls = [];
              const nativeFetch = window.fetch.bind(window);
              window.fetch = (input, init) => {
                const url = typeof input === "string" ? input :
                  (input && typeof input.url === "string" ? input.url : String(input));
                if (url.includes("/api/editor/film?project_id=")) window.__filmDocumentReadUrls.push(url);
                if (url.includes("/api/editor/composition?project_id=")) window.__compositionReadUrls.push(url);
                const request = nativeFetch(input, init);
                if (url.includes("/api/editor/film?project_id=")) {
                  request.then(response => response.clone().json()
                    .then(body => window.__filmDocumentReadBodies.push({url, body}))
                    .catch(error => window.__filmDocumentReadBodies.push({url, parseError:String(error)})));
                }
                return request;
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
                # The authority preflight payload is passed into the mounted Studio;
                # opening the same project must not perform a redundant second GET.
                assert len(initial_reads) == 1, {"initial FilmDocument reads": initial_reads}
                assert all(f"project_id={project_id}" in url for url in initial_reads), initial_reads
                legacy_reads = page.evaluate("window.__compositionReadUrls.slice()")
                assert legacy_reads == [], {
                    "migrated FilmDocument projects must not enter the legacy CompositionIR loader": legacy_reads
                }

                # Studio is already open on this revision. The selector route still performs
                # one authoritative preflight, then reuses the known document result.
                page.evaluate("window.__filmDocumentReadUrls.length = 0")
                page.evaluate("""() => {
                  const select = document.querySelector('#editor-project');
                  select.dispatchEvent(new Event('change', {bubbles:true}));
                }""")
                page.wait_for_function(
                    "() => window.__filmDocumentReadUrls.length >= 1", timeout=5000
                )
                page.wait_for_timeout(350)
                reads = page.evaluate("window.__filmDocumentReadUrls.slice()")
                assert len(reads) == 1, {"reads": reads}
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

                # Switching from a migrated project to a confirmed legacy project must
                # replace, not retain, the previous FilmDocument timeline. The same
                # exists:false authority result is reused by the legacy loader and Studio.
                legacy_project_id = "film-studio-project-shell-legacy-only"
                legacy_root = storage_root / legacy_project_id
                (legacy_root / "plans").mkdir(parents=True, exist_ok=True)
                legacy_assets = legacy_root / "assets"
                legacy_assets.mkdir(parents=True, exist_ok=True)
                (legacy_assets / "still.png").write_bytes(b"test asset not decoded during migration")
                (legacy_assets / "manifest.json").write_text(json.dumps([
                    {"id": "legacy-asset-1", "file": "assets/still.png", "type": "image"}
                ]), encoding="utf-8")
                (legacy_root / "plans" / "composition.json").write_text(json.dumps({
                    "project": {"title": legacy_project_id, "width": 1080, "height": 1920,
                                "fps": 30, "duration": 4.0},
                    "assets": [
                        {"id": "legacy-asset-1", "file": "assets/still.png", "type": "image"}
                    ],
                    "scenes": [
                        {"id": "legacy-scene-1", "start": 0.0, "duration": 4.0,
                         "text": "legacy", "layers": [
                             {"id": "legacy-layer-1", "type": "image", "asset_id": "legacy-asset-1",
                              "start": 0.0, "duration": 4.0}
                         ]}
                    ],
                    "audio": [], "watermarks": []
                }), encoding="utf-8")
                page.evaluate("""() => {
                  window.__filmDocumentReadUrls.length = 0;
                  window.__filmDocumentReadBodies.length = 0;
                  window.__compositionReadUrls.length = 0;
                }""")
                page.evaluate("""pid => {
                  const select = document.querySelector('#editor-project');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                  select.dispatchEvent(new Event('change', {bubbles:true}));
                }""", legacy_project_id)
                page.wait_for_function(
                    """(pid) => window.__filmDocumentReadBodies.some(item =>
                      item.url.includes('project_id=' + encodeURIComponent(pid)) && !!item.body)""",
                    arg=legacy_project_id,
                    timeout=10000,
                )
                legacy_authority = page.evaluate("""pid =>
                  window.__filmDocumentReadBodies.find(item =>
                    item.url.includes('project_id=' + encodeURIComponent(pid)))?.body || null
                """, legacy_project_id)
                assert legacy_authority and legacy_authority.get("ok") is True, legacy_authority
                assert legacy_authority.get("exists") is False, legacy_authority
                assert legacy_authority.get("migration_available") is True, legacy_authority
                page.wait_for_function(
                    "() => document.querySelector('#fs-timeline .fs-migrate-action') !== null",
                    timeout=10000,
                )
                assert page.locator("#film-studio-panel.fs-studio-expanded").is_visible()
                assert "初始化影片文档" in page.locator("#fs-timeline").inner_text()
                assert page.locator('#fs-timeline .fs-clip[data-clip]').count() == 0
                assert page.locator("#fs-project-label").inner_text() == legacy_project_id
                legacy_film_reads = page.evaluate("window.__filmDocumentReadUrls.slice()")
                assert len(legacy_film_reads) == 1, {
                    "known exists:false response should be reused for the Studio migration screen": legacy_film_reads
                }
                assert f"project_id={legacy_project_id}" in legacy_film_reads[0], legacy_film_reads
                legacy_composition_reads = page.evaluate("window.__compositionReadUrls.slice()")
                assert legacy_composition_reads and all(
                    f"project_id={legacy_project_id}" in url for url in legacy_composition_reads
                ), legacy_composition_reads
                assert page.locator("#fs-project-label").inner_text() == legacy_project_id
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_stale_selector_authority_response_cannot_enter_legacy_loader(tmp_path, monkeypatch):
    """A delayed exists:false response for an old selection cannot open CompositionIR for a new selection."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    stale_project_id = "film-studio-stale-legacy-authority"
    (storage_root / stale_project_id).mkdir(parents=True)
    current_project_id = "film-studio-current-film-authority"
    current_root = storage_root / current_project_id
    current_root.mkdir(parents=True)
    (current_root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    assert fd.read_project_film(current_project_id)["exists"]

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
            page.add_init_script("""(() => {
              window.__filmDocumentReadUrls = [];
              window.__filmDocumentReadBodies = [];
              window.__compositionReadUrls = [];
              window.__filmAuthorityDelayByProject = {};
              const nativeFetch = window.fetch.bind(window);
              window.fetch = (input, init) => {
                const url = typeof input === "string" ? input :
                  (input && typeof input.url === "string" ? input.url : String(input));
                let filmProjectId = null;
                try {
                  const parsed = new URL(url, window.location.origin);
                  if (parsed.pathname === "/api/editor/film" && parsed.searchParams.has("project_id")) {
                    filmProjectId = parsed.searchParams.get("project_id");
                  }
                } catch (_) {}
                if (filmProjectId !== null) window.__filmDocumentReadUrls.push(url);
                if (url.includes("/api/editor/composition?project_id=")) window.__compositionReadUrls.push(url);
                const request = nativeFetch(input, init);
                if (filmProjectId !== null) {
                  request.then(response => response.clone().json()
                    .then(body => window.__filmDocumentReadBodies.push({projectId:filmProjectId,url,body}))
                    .catch(error => window.__filmDocumentReadBodies.push({projectId:filmProjectId,url,parseError:String(error)})));
                  const delay = Number(window.__filmAuthorityDelayByProject[filmProjectId]) || 0;
                  if (delay > 0) {
                    return new Promise((resolve, reject) => {
                      request.then(response => setTimeout(() => resolve(response), delay), reject);
                    });
                  }
                }
                return request;
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
                page.evaluate("""ids => {
                  const select = document.querySelector('#editor-project');
                  for (const pid of ids) {
                    if (![...select.options].some(option => option.value === pid)) {
                      select.add(new Option(pid, pid));
                    }
                  }
                  // The first (legacy) authority response resolves while the second
                  // (FilmDocument) response is still pending.
                  window.__filmAuthorityDelayByProject = {
                    [ids[0]]: 180,
                    [ids[1]]: 650
                  };
                }""", [stale_project_id, current_project_id])

                page.evaluate("""pid => {
                  const select = document.querySelector('#editor-project');
                  select.value = pid;
                  select.dispatchEvent(new Event('change', {bubbles:true}));
                }""", stale_project_id)
                page.wait_for_function(
                    "(pid) => window.__filmDocumentReadUrls.some(url => new URL(url, location.origin).searchParams.get('project_id') === pid)",
                    arg=stale_project_id,
                    timeout=5000,
                )
                page.evaluate("""pid => {
                  const select = document.querySelector('#editor-project');
                  select.value = pid;
                  select.dispatchEvent(new Event('change', {bubbles:true}));
                }""", current_project_id)

                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible", timeout=10000)
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').wait_for(state="visible", timeout=10000)
                page.wait_for_function(
                    "(pid) => window.__filmDocumentReadBodies.some(item => item.projectId === pid && item.body && item.body.exists === true)",
                    arg=current_project_id,
                    timeout=5000,
                )
                composition_reads = page.evaluate("window.__compositionReadUrls.slice()")
                assert composition_reads == [], composition_reads
                authority_bodies = page.evaluate("window.__filmDocumentReadBodies.slice()")
                stale_body = next(item["body"] for item in authority_bodies if item["projectId"] == stale_project_id)
                current_body = next(item["body"] for item in authority_bodies if item["projectId"] == current_project_id)
                assert stale_body["ok"] is True and stale_body["exists"] is False, stale_body
                assert current_body["ok"] is True and current_body["exists"] is True, current_body
                assert page.locator("#fs-project-label").inner_text().startswith(current_project_id)
                assert page.locator("#film-studio-panel").get_attribute("data-project-id") == current_project_id
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
                            transientTimeline: {
                              snapGuide: store?.getSnapshot()?.timelineSnapGuide ?? null,
                              gesturePreview: store?.getSnapshot()?.timelineGesturePreview ?? null,
                              dropTarget: store?.getSnapshot()?.timelineDropTarget ?? null,
                              activeDragIds: store?.getSnapshot()?.timelineActiveDragIds ?? null,
                              clipPreview: [...document.querySelectorAll("#fs-timeline .fs-clip[data-clip]")].map(node => ({
                                id: node.getAttribute("data-clip"),
                                reactPreview: node.dataset.reactGesturePreview || null,
                                left: node.style.left,
                                width: node.style.width
                              }))
                            },
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
                    frameSnap: (() => { const r=m.resolveSnapCandidate(10.14, [], 36, 8, 30); return r ? {at:Number(r.at.toFixed(6)),kind:r.kind} : null; })(),
                    framePriority: m.resolveSnapCandidate(10.11, [{at:10.3,kind:"clip-edge"}], 36, 8, 30),
                    moveFrame: (() => { const r=m.resolveMoveGesture(10,0.14,[],36,8,true,30); return {targetAt:Number(r.targetAt.toFixed(6)),deltaSeconds:Number(r.deltaSeconds.toFixed(6)),snapKind:r.snapCandidate?.kind??null}; })(),
                    moveFrameNoSnap: (() => { const r=m.resolveMoveGesture(10,0.14,[],36,8,false,30); return {targetAt:Number(r.targetAt.toFixed(6)),deltaSeconds:Number(r.deltaSeconds.toFixed(6)),snapKind:r.snapCandidate?.kind??null}; })(),
                    slipFrame: m.resolveSlipGesture(0.21,1,true,30).sourceDeltaSeconds,
                    slipNoSnap: m.resolveSlipGesture(0.21,1,false,30).sourceDeltaSeconds,
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
                    trimStartFrame: (() => { const r=m.resolveTrimStart(5,10,0.14,[],36,8,true,30); return {target:Number(r.target.toFixed(6)),deltaSeconds:Number(r.deltaSeconds.toFixed(6)),snapKind:r.snapCandidate?.kind??null}; })(),
                    trimEndFrame: (() => { const r=m.resolveTrimEnd(5,10,-0.14,[],36,8,true,30); return {target:Number(r.target.toFixed(6)),deltaSeconds:Number(r.deltaSeconds.toFixed(6)),snapKind:r.snapCandidate?.kind??null}; })(),
                    trimEndSnap: (() => { const r = m.resolveTrimEnd(5, 10, -1, [{at: 8.85, kind: "trim-snap"}], 36, 8, true); return {target: Number(r.target.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    trimEndNoSnap: (() => { const r = m.resolveTrimEnd(5, 10, -1, [{at: 8.85, kind: "trim-snap"}], 36, 8, false); return {target: Number(r.target.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    rollSnap: (() => { const r = m.resolveRollGesture(10, -1, 5, 4, [{at: 8.9, kind: "roll-snap"}], 36, 8, true); return {targetAt: Number(r.targetAt.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    rollClamp: (() => { const r = m.resolveRollGesture(10, -20, 5, 4, [{at: 0, kind: "head"}], 36, 8, true); return {targetAt: Number(r.targetAt.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    rollFrame: (() => { const r=m.resolveRollGesture(10,-0.14,5,4,[],36,8,true,30); return {targetAt:Number(r.targetAt.toFixed(6)),deltaSeconds:Number(r.deltaSeconds.toFixed(6)),snapKind:r.snapCandidate?.kind??null}; })(),
                    slideSnap: (() => { const r = m.resolveSlideGesture(10, -1, 2, 3, 4, [{at: 8.9, kind: "slide-snap"}], 36, 8, true); return {targetAt: Number(r.targetAt.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    slideClamp: (() => { const r = m.resolveSlideGesture(10, 9, 2, 3, 4, [{at: 20, kind: "outside"}], 36, 8, true); return {targetAt: Number(r.targetAt.toFixed(3)), deltaSeconds: Number(r.deltaSeconds.toFixed(3)), snapKind: r.snapCandidate?.kind ?? null}; })(),
                    slideFrame: (() => { const r=m.resolveSlideGesture(10,-0.14,2,3,4,[],36,8,true,30); return {targetAt:Number(r.targetAt.toFixed(6)),deltaSeconds:Number(r.deltaSeconds.toFixed(6)),snapKind:r.snapCandidate?.kind??null}; })(),
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
                    "frameSnap": {"at": 10.133333, "kind": "帧"},
                    "framePriority": {"at": 10.3, "kind": "clip-edge"},
                    "moveFrame": {"targetAt": 10.133333, "deltaSeconds": 0.133333, "snapKind": "帧"},
                    "moveFrameNoSnap": {"targetAt": 10.14, "deltaSeconds": 0.14, "snapKind": None},
                    "slipFrame": 0.2,
                    "slipNoSnap": 0.21,
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
                    "trimStartFrame": {"target": 5.133333, "deltaSeconds": 0.133333, "snapKind": "帧"},
                    "trimEndFrame": {"target": 9.866667, "deltaSeconds": -0.133333, "snapKind": "帧"},
                    "trimEndSnap": {"target": 8.85, "deltaSeconds": -1.15, "snapKind": "trim-snap"},
                    "trimEndNoSnap": {"target": 9, "deltaSeconds": -1, "snapKind": None},
                    "rollSnap": {"targetAt": 8.9, "deltaSeconds": -1.1, "snapKind": "roll-snap"},
                    "rollClamp": {"targetAt": 5.04, "deltaSeconds": -4.96, "snapKind": None},
                    "rollFrame": {"targetAt": 9.866667, "deltaSeconds": -0.133333, "snapKind": "帧"},
                    "slideSnap": {"targetAt": 8.9, "deltaSeconds": -1.1, "snapKind": "slide-snap"},
                    "slideClamp": {"targetAt": 12.96, "deltaSeconds": 2.96, "snapKind": None},
                    "slideFrame": {"targetAt": 9.866667, "deltaSeconds": -0.133333, "snapKind": "帧"},
                    "left": 0,
                    "unknownWidth": 96,
                    "frame": 1,
                    "zoom": 60,
                    "anchor": 8.36,
                    "scroll": 582,
                }

                # A film-backed project keeps film.html as the only editing authority:
                # the plan card remains in Studio, while CompositionIR-writing controls stay
                # in the hidden legacy editor. No second command surface may be mounted.
                page.locator("#fs-mode-director").click()
                page.locator("#fs-director-planning-host").wait_for(state="visible")
                assert page.locator("#director-plan-card").evaluate("(node) => node.parentElement.id") == "fs-director-planning-host"
                assert page.locator("#director-control-grid").evaluate("(node) => node.parentElement.id") == "editor-body"
                assert page.locator("#director-bar-card").evaluate("(node) => node.parentElement.id") == "editor-body"
                assert page.locator("#director-input").is_hidden()
                assert page.locator("#fs-director-authority-note").is_visible()
                assert page.locator("#fs-director-legacy-note").is_hidden()
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

                # Snapping a trim edge to the playhead is an actual React/FilmStore
                # transient state: no request or revision change until pointerup,
                # then one trim transaction and cleanup of both guide and geometry.
                page.evaluate("window.filmStudioSeek(4.9)")
                snap_trim = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]')
                snap_handle = snap_trim.locator('.fs-trim-handle[data-trim="end"]')
                snap_box = snap_handle.bounding_box()
                assert snap_box, "clip-a trim-end handle unavailable for snap test"
                snap_before = fd.read_project_film(project_id)
                snap_edit_count = len(film_edit_requests)
                snap_bounds_before = snap_trim.evaluate("(node) => ({left:node.style.left,width:node.style.width})")
                snap_x = snap_box["x"] + snap_box["width"] / 2
                snap_y = snap_box["y"] + snap_box["height"] / 2
                page.mouse.move(snap_x, snap_y)
                page.mouse.down()
                # The end is at about 5.0s and the playhead is 4.9s. Drag left 6px:
                # cross the 5px activation threshold while staying within 8px of the playhead.
                page.mouse.move(snap_x - 6, snap_y, steps=4)
                page.wait_for_function(
                    """() => {
                      const state = window.filmStudioStore?.getSnapshot();
                      const guide = document.querySelector('#fs-snap-guide');
                      const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-a"]');
                      return state?.timelineSnapGuide?.kind === "播放头" &&
                        guide?.dataset.snapKind === "播放头" &&
                        !!node && node.dataset.reactGesturePreview === "true";
                    }""",
                    timeout=5000,
                )
                snap_state = page.evaluate("""() => ({
                  guide: window.filmStudioStore?.getSnapshot()?.timelineSnapGuide,
                  preview: window.filmStudioStore?.getSnapshot()?.timelineGesturePreview,
                  clip: (() => {
                    const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-a"]');
                    return node ? {left:node.style.left,width:node.style.width} : null;
                  })()
                })""")
                assert snap_state["guide"]["kind"] == "播放头", snap_state
                assert snap_state["preview"]["clip-a"]["width"] != float(snap_bounds_before["width"].removesuffix("px")), snap_state
                assert len(film_edit_requests) == snap_edit_count
                assert fd.read_project_film(project_id)["rev"] == snap_before["rev"]
                page.mouse.up()
                page.wait_for_function(
                    """() => {
                      const state = window.filmStudioStore?.getSnapshot();
                      return state?.timelineSnapGuide === null &&
                        state?.timelineGesturePreview === null &&
                        state?.timelineActiveDragIds === null;
                    }""",
                    timeout=10000,
                )
                snap_after = fd.read_project_film(project_id)
                snap_requests = film_edit_requests[snap_edit_count:]
                assert snap_after["rev"] != snap_before["rev"]
                assert len(snap_requests) == 1, snap_requests
                assert len(snap_requests[0].get("ops") or []) == 1, snap_requests
                snap_op = snap_requests[0]["ops"][0]
                assert snap_op.get("op") == "trim" and snap_op.get("edge") == "end", snap_op
                assert abs(float(snap_op.get("end")) - 4.9) < 0.05, snap_op
                page.evaluate("window.filmStudioUndo()")
                page.wait_for_function(
                    """() => {
                      const state = window.filmStudioStore?.getSnapshot();
                      const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-a"]');
                      return state?.timelineSnapGuide === null &&
                        state?.timelineGesturePreview === null &&
                        !!node && node.dataset.reactGesturePreview === "false";
                    }""",
                    timeout=10000,
                )
                snap_undone = fd.read_project_film(project_id)
                snap_undone_clip = next(
                    item for track in snap_undone["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "clip-a"
                )
                assert abs(float(snap_undone_clip["attrs"]["at"])) < 0.001, snap_undone_clip

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

                # External Agent writes a new revision while a normal clip move is active.
                # Pointerup must discard the local preview, reload the external file, and never POST the stale move.
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return !!state && state.busy === false && state.pendingTransaction === null &&
                    state.timelineGestureContext === null && state.timelineGesturePreview === null &&
                    state.timelineActiveDragIds === null && !state.pendingExternalRevision;
                }""", timeout=10_000)
                move_clip = page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]')
                move_clip.click()
                page.wait_for_function("""() => {
                  const ids = window.filmStudioStore?.getSnapshot()?.selectedIds || [];
                  return ids.length === 1 && ids[0] === "clip-b";
                }""", timeout=5_000)
                move_box = move_clip.bounding_box()
                assert move_box and move_box["width"] > 50, move_box
                move_before = fd.read_project_film(project_id)
                move_revision_before = move_before["rev"]
                move_request_count_before = len(film_edit_requests)
                move_x = move_box["x"] + move_box["width"] * 0.5
                move_y = move_box["y"] + move_box["height"] * 0.5
                page.mouse.move(move_x, move_y)
                page.mouse.down()
                page.mouse.move(move_x + 24, move_y, steps=5)
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const node = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  return state?.timelineGestureContext?.id === "clip-b" &&
                    state.timelineGestureContext.active === true &&
                    !!state.timelineGesturePreview?.["clip-b"] &&
                    !!node && node.dataset.reactGesturePreview === "true";
                }""", timeout=5_000)

                move_external_before = film_path.read_text(encoding="utf-8")
                move_tag_start = move_external_before.index('id="clip-b"')
                move_tag_end = move_external_before.index(">", move_tag_start)
                move_at_marker = move_external_before.index('at="', move_tag_start, move_tag_end)
                move_at_start = move_at_marker + len('at="')
                move_at_end = move_external_before.index('"', move_at_start, move_tag_end)
                move_at_original = float(move_external_before[move_at_start:move_at_end])
                move_at_external = f"{move_at_original + 0.25:.3f}".rstrip("0").rstrip(".")
                move_external_after = (
                    move_external_before[:move_at_start] + move_at_external +
                    move_external_before[move_at_end:]
                )
                assert move_external_after != move_external_before
                expected_move_external_revision = fd.revision_of(move_external_after)
                assert expected_move_external_revision != move_revision_before
                film_path.write_text(move_external_after, encoding="utf-8")

                page.wait_for_function("""expected => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state?.pendingExternalRevision === expected &&
                    state?.timelineGestureContext?.id === "clip-b" &&
                    state?.timelineGestureContext?.active === true &&
                    !!state?.timelineGesturePreview?.["clip-b"];
                }""", arg=expected_move_external_revision, timeout=15_000)
                assert len(film_edit_requests) == move_request_count_before, film_edit_requests
                assert fd.read_project_film(project_id)["rev"] == expected_move_external_revision

                page.mouse.up()
                page.wait_for_function("""expected => {
                  const state = window.filmStudioStore?.getSnapshot();
                  const clip = (state?.film?.tracks || []).flatMap(track => track.clips || [])
                    .find(item => item.id === "clip-b");
                  return state?.revision === expected && !state.pendingExternalRevision &&
                    state.timelineGestureContext === null && state.timelineGesturePreview === null &&
                    state.timelineActiveDragIds === null && state.busy === false &&
                    state.pendingTransaction === null && !!clip &&
                    Math.abs(Number(clip.at) - 0) >= 0;
                }""".replace("${0}", "expectedAt"), arg={"expectedRevision":expected_move_external_revision,"expectedAt":Number(move_at_external)}, timeout=15_000)
                assert len(film_edit_requests) == move_request_count_before, film_edit_requests
                move_after = fd.read_project_film(project_id)
                assert move_after["rev"] == expected_move_external_revision
                move_clip_after = next(
                    clip for track in move_after["doc"]["tracks"]
                    for clip in track["clips"] if clip["id"] == "clip-b"
                )
                assert abs(float(move_clip_after["attrs"]["at"]) - float(move_at_external)) < 0.001, move_clip_after

                # React owns envelope pointer lifecycle; a cancelled gesture must not commit,
                # and clicking the envelope surface must not leak selection to its parent clip.
                envelope = page.locator('canvas[data-envelope-clip="audio-1"]')
                envelope.wait_for(state="visible")
                # The audio track may be just below the viewport in a tall multi-track Studio.
                envelope.scroll_into_view_if_needed()
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

                # React's real envelope handler must store only an ID/numeric snapshot.
                # A cancelled pointer clears both context and preview and never writes film.html.
                cancelled_envelope = page.evaluate("""() => {
                  const canvas = document.querySelector('canvas[data-envelope-clip="audio-1"]');
                  if (!canvas) throw new Error("envelope canvas missing");
                  const rect = canvas.getBoundingClientRect();
                  const base = {
                    bubbles:true,cancelable:true,pointerId:78,button:0,buttons:1,
                    clientX:Math.round(rect.left + rect.width * 0.5),
                    clientY:Math.round(rect.top + rect.height * 0.9)
                  };
                  canvas.dispatchEvent(new PointerEvent("pointerdown", base));
                  const state = window.filmStudioStore?.getSnapshot();
                  const gesture = state?.timelineEnvelopeGesture;
                  const captured = {
                    ownedAtStart: window.filmStudioOwnsEnvelopeGesturePointer?.(78) === true,
                    clipId: gesture?.clipId ?? null,
                    pointerId: gesture?.pointerId ?? null,
                    keys: gesture ? Object.keys(gesture).sort() : [],
                    rectKeys: gesture?.rect ? Object.keys(gesture.rect).sort() : [],
                    previewClipId: state?.timelineEnvelopePreview?.clipId ?? null
                  };
                  canvas.dispatchEvent(new PointerEvent("pointercancel", {...base,buttons:0}));
                  return captured;
                }""")
                assert cancelled_envelope["ownedAtStart"] is True, cancelled_envelope
                assert cancelled_envelope["clipId"] == "audio-1" and cancelled_envelope["pointerId"] == 78, cancelled_envelope
                assert cancelled_envelope["keys"] == [
                    "active","changed","clipId","duration","index","pointerId","points","rect"
                ], cancelled_envelope
                assert cancelled_envelope["rectKeys"] == ["height","left","top","width"], cancelled_envelope
                assert cancelled_envelope["previewClipId"] == "audio-1", cancelled_envelope
                page.wait_for_function(
                    """() => {
                      const state = window.filmStudioStore?.getSnapshot();
                      return state?.timelineEnvelopeGesture === null &&
                        state?.timelineEnvelopePreview === null &&
                        window.filmStudioOwnsEnvelopeGesturePointer?.(78) !== true;
                    }""",
                    timeout=5_000,
                )
                assert fd.read_project_film(project_id)["rev"] == revision_before
                assert len(film_edit_requests) == edit_count_before

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

                # External file revision arrives while a second envelope gesture is still active.
                # The pointerup must discard stale preview rather than write over the Agent's file.
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return !!state && state.busy === false && state.pendingTransaction === null &&
                    state.timelineEnvelopeGesture === null && state.timelineEnvelopePreview === null &&
                    !state.pendingExternalRevision;
                }""", timeout=10_000)
                envelope = page.locator('canvas[data-envelope-clip="audio-1"]')
                envelope.scroll_into_view_if_needed()
                conflict_box = envelope.bounding_box()
                assert conflict_box and conflict_box["width"] > 80, conflict_box
                before_conflict = fd.read_project_film(project_id)
                conflict_revision_before = before_conflict["rev"]
                conflict_edit_count_before = len(film_edit_requests)
                conflict_x = conflict_box["x"] + conflict_box["width"] * 0.45
                conflict_y = conflict_box["y"] + conflict_box["height"] * 0.85
                page.mouse.move(conflict_x, conflict_y)
                page.mouse.down()
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state?.timelineEnvelopeGesture?.clipId === "audio-1" &&
                    state.timelineEnvelopeGesture.changed === true &&
                    state.timelineEnvelopePreview?.clipId === "audio-1";
                }""", timeout=5_000)
                page.mouse.move(conflict_x + 18, conflict_y - 7, steps=4)

                external_before = film_path.read_text(encoding="utf-8")
                clip_start = external_before.index('id="clip-b"')
                tag_end = external_before.index(">", clip_start)
                at_marker = external_before.index('at="', clip_start, tag_end)
                at_start = at_marker + len('at="')
                at_end = external_before.index('"', at_start, tag_end)
                external_at = float(external_before[at_start:at_end])
                next_at = f"{external_at + 0.25:.3f}".rstrip("0").rstrip(".")
                external_after = external_before[:at_start] + next_at + external_before[at_end:]
                assert external_after != external_before
                expected_external_revision = fd.revision_of(external_after)
                assert expected_external_revision != conflict_revision_before
                film_path.write_text(external_after, encoding="utf-8")

                # The changed file becomes pending, but must not overwrite the still-active gesture.
                page.wait_for_function("""expected => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state?.pendingExternalRevision === expected &&
                    state?.timelineEnvelopeGesture?.clipId === "audio-1" &&
                    state?.timelineEnvelopePreview?.clipId === "audio-1";
                }""", arg=expected_external_revision, timeout=15_000)
                assert len(film_edit_requests) == conflict_edit_count_before
                assert fd.read_project_film(project_id)["rev"] == expected_external_revision

                page.mouse.up()
                page.wait_for_function("""expected => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state?.revision === expected && !state.pendingExternalRevision &&
                    state.timelineEnvelopeGesture === null &&
                    state.timelineEnvelopePreview === null &&
                    state.busy === false && state.pendingTransaction === null;
                }""", arg=expected_external_revision, timeout=15_000)
                assert len(film_edit_requests) == conflict_edit_count_before, film_edit_requests
                after_conflict = fd.read_project_film(project_id)
                assert after_conflict["rev"] == expected_external_revision
                conflict_clip_b = next(
                    clip for track in after_conflict["doc"]["tracks"]
                    for clip in track["clips"] if clip["id"] == "clip-b"
                )
                conflict_audio = next(
                    clip for track in after_conflict["doc"]["tracks"]
                    for clip in track["clips"] if clip["id"] == "audio-1"
                )
                assert abs(float(conflict_clip_b["attrs"]["at"]) - float(next_at)) < 0.001, conflict_clip_b
                assert "volume_envelope" not in conflict_audio["attrs"], conflict_audio
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_legacy_director_controls_remain_available_only_without_film_document(tmp_path, monkeypatch):
    """Initial planning keeps the CompositionIR controls until film.html exists."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-legacy-planning-browser"
    (storage_root / project_id).mkdir(parents=True)
    # A project directory without film.html must enter the explicit migration/planning path.
    # The test intentionally does not invent a second timeline or any client-side mock state.

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            try:
                page.set_default_timeout(10_000)
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded"
                )
                assert response and response.status == 200
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.locator("#editor-project").wait_for(state="visible")
                page.evaluate("""pid => {
                  const select = document.querySelector('#editor-project');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                }""", project_id)
                page.locator("#film-studio-toggle").click()
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.wait_for_function(
                    "() => document.querySelector('#fs-timeline-meta')?.textContent === '迁移预览'",
                    timeout=5000,
                )

                page.locator('[data-film-action="director-mode"]').click()
                page.wait_for_function("""() => {
                  const host = document.querySelector('#fs-director-planning-host');
                  const legacy = document.querySelector('#fs-director-legacy-note');
                  const authority = document.querySelector('#fs-director-authority-note');
                  const controls = document.querySelector('#director-control-grid');
                  const bar = document.querySelector('#director-bar-card');
                  return !!host && getComputedStyle(host).display !== 'none' &&
                    !!legacy && getComputedStyle(legacy).display !== 'none' &&
                    !!authority && getComputedStyle(authority).display === 'none' &&
                    host.contains(controls) && host.contains(bar);
                }""", timeout=5000)
                assert page.locator("#fs-director-planning-host #director-control-grid").count() == 1
                assert page.locator("#fs-director-planning-host #director-bar-card").count() == 1
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


def test_studio_displays_source_after_external_file_revision_reload(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-source-label-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    initial = fd.read_project_film(project_id)
    assert initial["exists"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            try:
                page.set_default_timeout(10_000)
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
                  select.dispatchEvent(new Event('change', {bubbles:true}));
                }""", project_id)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]').wait_for(state="visible")

                # Film-backed projects keep workflow status in the Director tab but cannot
                # expose the legacy CompositionIR command bar/control grid beside film.html AI.
                page.locator('[data-film-action="director-mode"]').click()
                page.wait_for_function("""() => {
                  const host = document.querySelector('#fs-director-planning-host');
                  const authority = document.querySelector('#fs-director-authority-note');
                  const legacy = document.querySelector('#fs-director-legacy-note');
                  const aiRoot = document.querySelector('#fs-react-ai-director');
                  return !!host && getComputedStyle(host).display !== 'none' &&
                    !!authority && getComputedStyle(authority).display !== 'none' &&
                    !!legacy && getComputedStyle(legacy).display === 'none' &&
                    !!aiRoot && getComputedStyle(aiRoot).display !== 'none' &&
                    !host.contains(document.querySelector('#director-control-grid')) &&
                    !host.contains(document.querySelector('#director-bar-card'));
                }""", timeout=5000)
                assert page.locator("#fs-director-planning-host #director-control-grid").count() == 0
                assert page.locator("#fs-director-planning-host #director-bar-card").count() == 0
                assert page.locator("#fs-react-ai-director").is_visible()

                external = SAMPLE.replace(
                    'id="clip-b" src="../assets/b.mp4#t=0,5" at="5"',
                    'id="clip-b" src="../assets/b.mp4#t=0,5" at="5.5"',
                )
                assert external != SAMPLE
                film_path.write_text(external, encoding="utf-8")
                expected_revision = fd.revision_of(external)
                page.wait_for_function(
                    """expected => {
                      const state = window.filmStudioStore?.getSnapshot();
                      const alert = document.querySelector('#film-studio-alert');
                      const clip = document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                      return state?.revision === expected && !!clip &&
                        !!alert && alert.textContent.includes('修改来源：外部文件 / Agent 直接改写');
                    }""",
                    arg=expected_revision,
                    timeout=15_000,
                )
                assert fd.read_project_film(project_id)["rev"] == expected_revision
                history = fd._history_read(project_id)
                assert history["past"][-1]["source"] == "external_file"
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server_thread.join(timeout=5)


