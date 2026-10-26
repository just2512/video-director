"""Real Chromium acceptance for timeline gestures, pointer-anchored zoom, and history."""
from __future__ import annotations

import importlib
import json
import os
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

from video_director import film_document as fd


FILM = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#182030}</style></head>
<body>
<section id="video-main" data-label="主画面">
  <video id="clip-a" src="../assets/a.mp4#t=0,2" at="0"></video>
  <video id="clip-b" src="../assets/b.mp4#t=0,2" at="6"></video>
  <video id="clip-c" src="../assets/c.mp4#t=0,2" at="12"></video>
</section>
</body></html>'''


def _clip_positions(project_id: str) -> dict[str, float]:
    state = fd.read_project_film(project_id)
    return {
        str(clip["id"]): float(clip["attrs"]["at"])
        for track in state["doc"]["tracks"]
        for clip in track["clips"]
    }


def test_react_multi_select_drag_is_one_transaction_and_undo_redo_keeps_ui_in_sync(
    tmp_path, monkeypatch
):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-timeline-history-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(FILM, encoding="utf-8")
    original_text = film_path.read_text(encoding="utf-8")
    initial = fd.read_project_film(project_id)
    assert initial["exists"]
    assert _clip_positions(project_id) == {"clip-a": 0.0, "clip-b": 6.0, "clip-c": 12.0}

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors: list[str] = []
            film_edit_requests: list[dict] = []

            def capture_edit(request):
                if request.method == "POST" and request.url.endswith("/api/editor/film/edit"):
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
                    "() => { const select=document.querySelector('#editor-project'); "
                    "return !!select && select.options.length > 0; }"
                )
                page.evaluate(
                    """pid => {
                      const select=document.querySelector('#editor-project');
                      let option=[...select.options].find(item=>item.value===pid);
                      if(!option){option=new Option(pid,pid);select.add(option);}
                      select.value=pid;
                    }""",
                    project_id,
                )
                page.evaluate("window.filmStudioToggle()")
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                for clip_id in ("clip-a", "clip-b", "clip-c"):
                    page.locator(f'#fs-timeline .fs-clip[data-clip="{clip_id}"]').wait_for(
                        state="visible"
                    )

                # The acceptance must exercise the compiled React timeline rather than
                # passing accidentally through the legacy DOM fallback.
                page.wait_for_function(
                    "() => typeof window.filmStudioReactTimeline?.renderSurface === 'function' "
                    "&& !!document.querySelector('.fs-react-timeline-surface')",
                    timeout=10000,
                )
                assert page.locator("#fs-static-timeline-toolbar").is_hidden()

                initial_lefts = page.evaluate(
                    """() => Object.fromEntries(['clip-a','clip-b','clip-c'].map(id => {
                      const node=document.querySelector('#fs-timeline .fs-clip[data-clip="'+id+'"]');
                      return [id, node ? getComputedStyle(node).left : null];
                    }))"""
                )
                assert all(initial_lefts[key] for key in ("clip-a", "clip-b", "clip-c"))
                before_text = film_path.read_text(encoding="utf-8")
                before = fd.read_project_film(project_id)
                before_revision = before["rev"]
                # Exercise the extracted React ruler in Chromium: keyboard, tick click,
                # and pointer drag must update only playhead UI, never film revision/content.
                ruler = page.locator("#fs-timeline #fs-ruler-body")
                ruler.wait_for(state="visible")
                ruler_state = page.evaluate(
                    """() => {
                      const state=window.filmStudioStore.getSnapshot();
                      return {playhead:Number(state.playhead),duration:Number(state.duration),
                        revision:state.revision,pixelsPerSecond:Number(state.pixelsPerSecond)};
                    }"""
                )
                ruler_start = float(ruler_state["playhead"])
                ruler_duration = float(ruler_state["duration"])
                ruler_revision = ruler_state["revision"]
                ruler_scale = float(ruler_state["pixelsPerSecond"])
                assert abs(ruler_start) < 0.001, ruler_state
                assert ruler_duration >= 5 and ruler_scale > 0, ruler_state
                ruler_edits_before = len(film_edit_requests)

                def wait_for_ruler_seek(expected: float) -> None:
                    page.wait_for_function(
                        """expected => {
                          const state=window.filmStudioStore?.getSnapshot();
                          const ruler=document.querySelector('#fs-timeline #fs-ruler-body');
                          return !!state && Math.abs(Number(state.playhead)-expected)<0.001 &&
                            Math.abs(Number(ruler?.getAttribute('aria-valuenow'))-expected)<0.001;
                        }""",
                        arg=expected,
                        timeout=5000,
                    )

                ruler.focus()
                page.keyboard.press("ArrowRight")
                wait_for_ruler_seek(1.0)
                page.keyboard.press("ArrowLeft")
                wait_for_ruler_seek(0.0)
                ruler.locator('.fs-ruler-mark[data-at="5"]').click()
                wait_for_ruler_seek(5.0)

                ruler_bounds = ruler.bounding_box()
                assert ruler_bounds
                ruler_y = ruler_bounds["y"] + ruler_bounds["height"] / 2
                page.mouse.move(ruler_bounds["x"] + 1, ruler_y)
                page.mouse.down()
                page.mouse.move(ruler_bounds["x"] + 5 * ruler_scale, ruler_y, steps=8)
                wait_for_ruler_seek(5.0)
                page.mouse.up()
                wait_for_ruler_seek(5.0)
                ruler.locator('.fs-ruler-mark[data-at="0"]').click()
                wait_for_ruler_seek(0.0)

                assert page.evaluate("window.filmStudioStore.getSnapshot().revision") == ruler_revision
                assert fd.read_project_film(project_id)["rev"] == before_revision
                assert film_path.read_text(encoding="utf-8") == before_text
                assert len(film_edit_requests) == ruler_edits_before, film_edit_requests


                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').click()

                # Playback updates must move the small keyframe playhead marker without
                # reinitializing the heavyweight inspector or losing its unsaved field draft.
                keyframe_marker = page.locator(
                    '#fs-inspector .fs-keyframe-graph .fs-keyframe-playhead'
                )
                keyframe_marker.wait_for(state="attached")
                keyframe_x_before = float(keyframe_marker.get_attribute("x1"))
                title_input = page.locator('#fs-inspector #fs-field-title')
                title_before = title_input.input_value()
                title_input.fill("temporary unsaved title")
                revision_before_inspector_seek = page.evaluate(
                    "window.filmStudioStore.getSnapshot().revision"
                )
                edits_before_inspector_seek = len(film_edit_requests)

                page.evaluate("window.filmStudioSeek(1)")
                page.wait_for_function(
                    """args => {
                      const state=window.filmStudioStore?.getSnapshot();
                      const line=document.querySelector(
                        '#fs-inspector .fs-keyframe-graph .fs-keyframe-playhead'
                      );
                      return !!state && Math.abs(Number(state.playhead)-1)<0.001 &&
                        !!line && Math.abs(Number(line.getAttribute('x1'))-args.x)>0.5;
                    }""",
                    arg={"x": keyframe_x_before},
                    timeout=5000,
                )
                assert title_input.input_value() == "temporary unsaved title"
                keyframe_x_at_one = float(keyframe_marker.get_attribute("x1"))
                assert keyframe_x_at_one != keyframe_x_before

                page.evaluate("window.filmStudioSeek(0)")
                page.wait_for_function(
                    """args => {
                      const state=window.filmStudioStore?.getSnapshot();
                      const line=document.querySelector(
                        '#fs-inspector .fs-keyframe-graph .fs-keyframe-playhead'
                      );
                      return !!state && Math.abs(Number(state.playhead))<0.001 &&
                        !!line && Math.abs(Number(line.getAttribute('x1'))-args.x)<0.001;
                    }""",
                    arg={"x": keyframe_x_before},
                    timeout=5000,
                )
                title_input.fill(title_before)
                assert page.evaluate(
                    "window.filmStudioStore.getSnapshot().revision"
                ) == revision_before_inspector_seek
                assert len(film_edit_requests) == edits_before_inspector_seek
                page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]').click(
                    modifiers=["Control"]
                )
                selected_ids = page.evaluate(
                    "window.filmStudioStore.getSnapshot().selectedIds"
                )
                assert set(selected_ids) == {"clip-a", "clip-b"}, selected_ids

                pixels_per_second = page.evaluate(
                    "window.filmStudioStore.getSnapshot().pixelsPerSecond"
                )
                assert isinstance(pixels_per_second, (int, float)) and pixels_per_second > 0
                clip = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]')
                bounds = clip.bounding_box()
                assert bounds
                start_x = bounds["x"] + bounds["width"] / 2
                start_y = bounds["y"] + bounds["height"] / 2
                edit_count_before = len(film_edit_requests)
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(start_x + pixels_per_second, start_y, steps=6)

                page.wait_for_function(
                    """() => {
                      const state=window.filmStudioStore?.getSnapshot();
                      const preview=state?.timelineGesturePreview;
                      return !!state?.timelineGestureContext?.active &&
                        !!preview?.['clip-a'] && !!preview?.['clip-b'] &&
                        preview['clip-a'].left != null && preview['clip-b'].left != null;
                    }""",
                    timeout=5000,
                )
                assert len(film_edit_requests) == edit_count_before, film_edit_requests
                assert film_path.read_text(encoding="utf-8") == before_text
                during = page.evaluate(
                    "window.filmStudioStore.getSnapshot().revision"
                )
                assert during == before_revision, (during, before_revision)

                page.mouse.up()
                page.wait_for_function(
                    """args => {
                      const state=window.filmStudioStore?.getSnapshot();
                      const clips=(state?.film?.tracks||[]).flatMap(track=>track.clips||[]);
                      const a=clips.find(clip=>clip.id==='clip-a');
                      const b=clips.find(clip=>clip.id==='clip-b');
                      return !!state && !state.busy && state.pendingTransaction===null &&
                        state.revision!==args.revision &&
                        Math.abs(Number(a?.at)-1)<0.001 &&
                        Math.abs(Number(b?.at)-7)<0.001 &&
                        state.timelineGestureContext===null &&
                        state.timelineGesturePreview===null;
                    }""",
                    arg={"revision": before_revision},
                    timeout=10000,
                )
                after_drag = fd.read_project_film(project_id)
                after_drag_text = film_path.read_text(encoding="utf-8")
                after_drag_revision = after_drag["rev"]
                assert after_drag_revision != before_revision
                assert _clip_positions(project_id) == {
                    "clip-a": 1.0, "clip-b": 7.0, "clip-c": 12.0
                }
                assert len(film_edit_requests) == edit_count_before + 1, film_edit_requests
                request = film_edit_requests[-1]
                ops = request.get("ops") or []
                assert request.get("base") == before_revision, request
                assert len(ops) == 2, request
                assert {op.get("clip") for op in ops} == {"clip-a", "clip-b"}, request
                assert all(op.get("op") == "move" for op in ops), request
                assert all(op.get("track_id") == "video-main" for op in ops), request
                committed_lefts = page.evaluate(
                    """() => Object.fromEntries(['clip-a','clip-b','clip-c'].map(id => {
                      const node=document.querySelector('#fs-timeline .fs-clip[data-clip="'+id+'"]');
                      return [id, node ? getComputedStyle(node).left : null];
                    }))"""
                )
                assert committed_lefts["clip-a"] != initial_lefts["clip-a"]
                assert committed_lefts["clip-b"] != initial_lefts["clip-b"]
                assert committed_lefts["clip-c"] == initial_lefts["clip-c"]
                state_after_commit = page.evaluate(
                    """() => {
                      const state=window.filmStudioStore.getSnapshot();
                      return {revision:state.revision,busy:state.busy,pending:state.pendingTransaction};
                    }"""
                )
                assert state_after_commit == {
                    "revision": after_drag_revision, "busy": False, "pending": None
                }

                # A single undo must restore every selected clip, the exact source bytes,
                # the revision shown in FilmStore, and the React-rendered geometry.
                page.evaluate("() => window.filmStudioUndo()")
                page.wait_for_function(
                    """revision => {
                      const state=window.filmStudioStore?.getSnapshot();
                      return !!state && !state.busy && state.pendingTransaction===null &&
                        state.revision===revision &&
                        state.timelineGestureContext===null &&
                        state.timelineGesturePreview===null;
                    }""",
                    arg=before_revision,
                    timeout=10000,
                )
                assert film_path.read_text(encoding="utf-8") == original_text
                assert _clip_positions(project_id) == {
                    "clip-a": 0.0, "clip-b": 6.0, "clip-c": 12.0
                }
                undone_lefts = page.evaluate(
                    """() => Object.fromEntries(['clip-a','clip-b','clip-c'].map(id => {
                      const node=document.querySelector('#fs-timeline .fs-clip[data-clip="'+id+'"]');
                      return [id, node ? getComputedStyle(node).left : null];
                    }))"""
                )
                assert undone_lefts == initial_lefts
                assert page.evaluate(
                    "window.filmStudioStore.getSnapshot().revision"
                ) == fd.read_project_film(project_id)["rev"]

                # Redo restores the entire multi-operation gesture as one history entry.
                page.evaluate("() => window.filmStudioRedo()")
                page.wait_for_function(
                    """revision => {
                      const state=window.filmStudioStore?.getSnapshot();
                      const clips=(state?.film?.tracks||[]).flatMap(track=>track.clips||[]);
                      const a=clips.find(clip=>clip.id==='clip-a');
                      const b=clips.find(clip=>clip.id==='clip-b');
                      return !!state && !state.busy && state.pendingTransaction===null &&
                        state.revision===revision &&
                        Math.abs(Number(a?.at)-1)<0.001 &&
                        Math.abs(Number(b?.at)-7)<0.001 &&
                        state.timelineGestureContext===null &&
                        state.timelineGesturePreview===null;
                    }""",
                    arg=after_drag_revision,
                    timeout=10000,
                )
                assert film_path.read_text(encoding="utf-8") == after_drag_text
                assert _clip_positions(project_id) == {
                    "clip-a": 1.0, "clip-b": 7.0, "clip-c": 12.0
                }
                redone_lefts = page.evaluate(
                    """() => Object.fromEntries(['clip-a','clip-b','clip-c'].map(id => {
                      const node=document.querySelector('#fs-timeline .fs-clip[data-clip="'+id+'"]');
                      return [id, node ? getComputedStyle(node).left : null];
                    }))"""
                )
                assert redone_lefts == committed_lefts
                assert page.evaluate(
                    "window.filmStudioStore.getSnapshot().revision"
                ) == fd.read_project_film(project_id)["rev"]
                assert len(film_edit_requests) == edit_count_before + 1, film_edit_requests
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                page.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_react_pointer_cancel_clears_snap_drop_and_preview_without_writing(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-pointer-cancel-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    source_text = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#182030}</style></head>
<body>
<section id="video-main" data-label="主画面">
  <video id="clip-a" src="../assets/a.mp4#t=0,2" at="0"></video>
  <video id="clip-b" src="../assets/b.mp4#t=0,2" at="6"></video>
</section>
<section id="video-secondary" data-label="备用画面"></section>
</body></html>'''
    film_path.write_text(source_text, encoding="utf-8")
    original_bytes = film_path.read_bytes()
    original = fd.read_project_film(project_id)
    assert original["exists"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors: list[str] = []
            edit_requests: list[dict] = []

            def capture_edit(request):
                if request.method == "POST" and request.url.endswith("/api/editor/film/edit"):
                    try:
                        edit_requests.append(json.loads(request.post_data or "{}"))
                    except json.JSONDecodeError:
                        edit_requests.append({"_invalid_json": True})

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
                    "() => { const select=document.querySelector('#editor-project'); "
                    "return !!select && select.options.length > 0; }"
                )
                page.evaluate(
                    """pid => {
                      const select=document.querySelector('#editor-project');
                      let option=[...select.options].find(item=>item.value===pid);
                      if(!option){option=new Option(pid,pid);select.add(option);}
                      select.value=pid;
                    }""",
                    project_id,
                )
                page.evaluate("window.filmStudioToggle()")
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.wait_for_function(
                    "() => typeof window.filmStudioReactTimeline?.renderSurface === 'function' "
                    "&& !!document.querySelector('.fs-react-timeline-surface')",
                    timeout=10000,
                )
                source_clip = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]')
                target_track = page.locator('#fs-timeline .fs-track[data-track="video-secondary"]')
                source_clip.wait_for(state="visible")
                target_track.wait_for(state="visible")

                scale = float(page.evaluate("window.filmStudioStore.getSnapshot().pixelsPerSecond"))
                source_box = source_clip.bounding_box()
                target_box = target_track.bounding_box()
                assert source_box and target_box and scale > 0
                start_x = source_box["x"] + source_box["width"] / 2
                start_y = source_box["y"] + source_box["height"] / 2
                target_x = start_x + 6 * scale
                target_y = target_box["y"] + target_box["height"] / 2
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(target_x, target_y, steps=8)

                page.wait_for_function(
                    """() => {
                      const s=window.filmStudioStore?.getSnapshot();
                      return !!s?.timelineGestureContext?.active &&
                        s.timelineGestureContext.targetTrackId==='video-secondary' &&
                        s.timelineDropTarget?.trackId==='video-secondary' &&
                        s.timelineGesturePreview?.['clip-a']?.targetTrackId==='video-secondary' &&
                        Math.abs(Number(s.timelineSnapGuide?.at)-6)<0.001;
                    }""",
                    timeout=5000,
                )
                assert edit_requests == []
                assert film_path.read_bytes() == original_bytes
                assert fd.read_project_film(project_id)["rev"] == original["rev"]

                # A second touch/pointer must not move, cancel, or commit this gesture.
                unrelated_release = page.evaluate("""() => {
                  const store=window.filmStudioStore;
                  const before=store.getSnapshot();
                  const gesture=before.timelineGestureContext;
                  if(!gesture?.active)throw new Error('expected an active timeline gesture');
                  const otherTrack=document.querySelector('#fs-timeline .fs-track[data-track="video-main"]');
                  const rect=otherTrack?.getBoundingClientRect();
                  if(!rect)throw new Error('source track row not found');
                  const wrongPointer=gesture.pointerId+1000;
                  const eventInit={
                    bubbles:true,cancelable:true,pointerId:wrongPointer,
                    clientX:rect.left+rect.width*0.5,clientY:rect.top+rect.height*0.5
                  };
                  const secondClip=document.querySelector('#fs-timeline .fs-clip[data-clip="clip-b"]');
                  if(!secondClip)throw new Error('second React clip not found');
                  secondClip.dispatchEvent(new PointerEvent('pointerdown',{
                    ...eventInit,button:0,pointerType:'touch',isPrimary:false
                  }));
                  const afterDown=store.getSnapshot();
                  document.dispatchEvent(new PointerEvent('pointermove',eventInit));
                  const afterMove=store.getSnapshot();
                  const afterMoveTrack=afterMove.timelineGestureContext?.targetTrackId??null;
                  document.dispatchEvent(new PointerEvent('pointercancel',eventInit));
                  const afterCancel=store.getSnapshot();
                  document.dispatchEvent(new PointerEvent('pointerup',eventInit));
                  const afterUp=store.getSnapshot();
                  return {
                    pointerId:gesture.pointerId,
                    afterDownPointerId:afterDown.timelineGestureContext?.pointerId??null,
                    afterDownTargetTrackId:afterDown.timelineGestureContext?.targetTrackId??null,
                    afterMoveTrack,
                    afterCancelPointerId:afterCancel.timelineGestureContext?.pointerId??null,
                    afterUpPointerId:afterUp.timelineGestureContext?.pointerId??null,
                    targetTrackId:afterUp.timelineGestureContext?.targetTrackId??null,
                    revision:afterUp.revision,
                    busy:afterUp.busy
                  };
                }""")
                assert unrelated_release["afterDownPointerId"] == unrelated_release["pointerId"], unrelated_release
                assert unrelated_release["afterDownTargetTrackId"] == "video-secondary", unrelated_release
                assert unrelated_release["afterMoveTrack"] == "video-secondary", unrelated_release
                assert unrelated_release["afterCancelPointerId"] == unrelated_release["pointerId"], unrelated_release
                assert unrelated_release["afterUpPointerId"] == unrelated_release["pointerId"], unrelated_release
                assert unrelated_release["targetTrackId"] == "video-secondary", unrelated_release
                assert unrelated_release["revision"] == original["rev"], unrelated_release
                assert unrelated_release["busy"] is False, unrelated_release
                assert edit_requests == []
                assert film_path.read_bytes() == original_bytes

                # Dispatch pointercancel to the React-owned clip with the actual active pointer ID.
                cancelled = page.evaluate("""() => {
                  const state=window.filmStudioStore.getSnapshot();
                  const pointerId=state.timelineGestureContext.pointerId;
                  const clip=document.querySelector('#fs-timeline .fs-clip[data-clip="clip-a"]');
                  if(!clip)throw new Error('React-owned source clip not found');
                  clip.dispatchEvent(new PointerEvent('pointercancel',{
                    bubbles:true,cancelable:true,pointerId,
                    clientX:window.__cancelPointerX||0,clientY:window.__cancelPointerY||0
                  }));
                  return pointerId;
                }""")
                assert isinstance(cancelled, int)
                page.wait_for_function(
                    """() => {
                      const s=window.filmStudioStore?.getSnapshot();
                      return !!s && !s.busy && s.pendingTransaction===null &&
                        s.timelineGestureContext===null &&
                        s.timelineGesturePreview===null &&
                        s.timelineSnapGuide===null &&
                        s.timelineDropTarget===null &&
                        s.timelineActiveDragIds===null;
                    }""",
                    timeout=5000,
                )
                page.mouse.up()
                assert page.locator('#fs-timeline .fs-track-drop-target, #fs-timeline .fs-track-drop-invalid').count() == 0
                assert page.locator('#fs-timeline #fs-snap-guide').count() == 0
                assert source_clip.get_attribute("class") is not None
                assert "fs-dragging" not in (source_clip.get_attribute("class") or "")
                assert page.locator('#fs-timeline [data-gesture-ghost-for="clip-a"]').count() == 0
                assert edit_requests == []
                assert film_path.read_bytes() == original_bytes
                assert fd.read_project_film(project_id)["rev"] == original["rev"]
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                page.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_react_cross_track_drag_commits_one_revision_and_undo_redo_restores_track_ownership(
    tmp_path, monkeypatch
):
    """A real pointer drag across tracks previews only, commits once, then undoes/redoes exactly."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-cross-track-transaction-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    source_text = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#182030}</style></head>
<body>
<section id="video-main" data-label="主画面">
  <video id="clip-a" src="../assets/a.mp4#t=0,2" at="0"></video>
  <video id="clip-b" src="../assets/b.mp4#t=0,2" at="6"></video>
</section>
<section id="video-secondary" data-label="辅助画面">
  <video id="clip-target" src="../assets/target.mp4#t=0,2" at="8"></video>
</section>
</body></html>'''
    film_path.write_text(source_text, encoding="utf-8")
    original_bytes = film_path.read_bytes()
    original = fd.read_project_film(project_id)
    assert original["exists"]
    assert _clip_positions(project_id) == {
        "clip-a": 0.0, "clip-b": 6.0, "clip-target": 8.0
    }

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors: list[str] = []
            edit_requests: list[dict] = []

            def capture_edit(request):
                if request.method == "POST" and request.url.endswith("/api/editor/film/edit"):
                    try:
                        edit_requests.append(json.loads(request.post_data or "{}"))
                    except json.JSONDecodeError:
                        edit_requests.append({"_invalid_json": True})

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
                    "() => { const select=document.querySelector('#editor-project'); "
                    "return !!select && select.options.length > 0; }"
                )
                page.evaluate(
                    """pid => {
                      const select=document.querySelector('#editor-project');
                      let option=[...select.options].find(item=>item.value===pid);
                      if(!option){option=new Option(pid,pid);select.add(option);}
                      select.value=pid;
                    }""",
                    project_id,
                )
                page.evaluate("window.filmStudioToggle()")
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.wait_for_function(
                    "() => typeof window.filmStudioReactTimeline?.renderSurface === 'function' "
                    "&& typeof window.filmStudioTimelineController?.buildMovePreview === 'function' "
                    "&& typeof window.filmStudioTimelineController?.buildMoveOperations === 'function' "
                    "&& typeof window.filmStudioTimelineController?.validateMoveTarget === 'function' "
                    "&& !!document.querySelector('.fs-react-timeline-surface')",
                    timeout=10000,
                )

                source_clip = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]')
                target_track = page.locator('#fs-timeline .fs-track[data-track="video-secondary"]')
                source_clip.wait_for(state="visible")
                target_track.wait_for(state="visible")
                pixels_per_second = float(
                    page.evaluate("window.filmStudioStore.getSnapshot().pixelsPerSecond")
                )
                source_box = source_clip.bounding_box()
                target_box = target_track.bounding_box()
                assert source_box and target_box and pixels_per_second > 0
                start_x = source_box["x"] + source_box["width"] / 2
                start_y = source_box["y"] + source_box["height"] / 2
                target_x = start_x + pixels_per_second
                target_y = target_box["y"] + target_box["height"] / 2
                before_revision = original["rev"]

                # The shared controller must reject the same unsafe move even when
                # called without the UI's pointer-target guard.
                invalid_move = page.evaluate("""pixelsPerSecond => {
                  const controller=window.filmStudioTimelineController;
                  const input={
                    gesture:{
                      members:[{id:'clip-a',originalAt:0,originalEnd:2,
                        trackId:'video-main',trackIndex:0}],
                      trackId:'video-main',originalTrackIndex:0,
                      trackOrder:['video-main','video-secondary'],
                      targetTrackId:'video-secondary'
                    },
                    tracks:[
                      {id:'video-main',attrs:{},clips:[
                        {id:'clip-a',at:0,end:2,attrs:{}},
                        {id:'clip-b',at:6,end:8,attrs:{}}
                      ]},
                      {id:'video-secondary',attrs:{},clips:[
                        {id:'clip-target',at:8,end:10,attrs:{}}
                      ]}
                    ],
                    targetTrackId:'video-secondary',minimumStart:0,
                    rawDeltaSeconds:7,candidates:[],pixelsPerSecond,
                    thresholdPixels:8,snapEnabled:false,frameRate:30
                  };
                  const preview=controller.buildMovePreview(input);
                  const operations=controller.buildMoveOperations(input);
                  return {
                    validation:controller.validateMoveTarget(input),
                    previewOperations:preview.operations,
                    operations
                  };
                }""", pixels_per_second)
                assert "时间重叠" in invalid_move["validation"], invalid_move
                assert invalid_move["previewOperations"] is None, invalid_move
                assert invalid_move["operations"] is None, invalid_move

                # A cross-track drop that would overlap an existing clip must be
                # rejected before persistence. The clip starts at 0 and is 2s long;
                # moving its start to 7s would overlap clip-target at 8s.
                invalid_target_x = start_x + 7 * pixels_per_second
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(invalid_target_x, target_y, steps=8)
                page.wait_for_function(
                    """() => {
                      const state=window.filmStudioStore?.getSnapshot();
                      return !!state?.timelineGestureContext?.active &&
                        state.timelineGestureContext.targetTrackId==='video-secondary' &&
                        state.timelineDropTarget?.trackId==='video-secondary' &&
                        state.timelineDropTarget.invalid===true &&
                        String(state.timelineGestureContext.invalidTargetMessage||'').includes('时间重叠');
                    }""",
                    timeout=5000,
                )
                page.mouse.up()
                page.wait_for_function(
                    """() => {
                      const state=window.filmStudioStore?.getSnapshot();
                      return !!state && !state.busy && state.pendingTransaction===null &&
                        state.timelineGestureContext===null && state.timelineGesturePreview===null;
                    }""",
                    timeout=10000,
                )
                assert edit_requests == [], edit_requests
                assert film_path.read_bytes() == original_bytes
                assert fd.read_project_film(project_id)["rev"] == before_revision

                # Move a real pointer into the other video track. React must render a
                # cross-track ghost and a valid drop target without touching film.html.
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(target_x, target_y, steps=8)
                page.wait_for_function(
                    """() => {
                      const state=window.filmStudioStore?.getSnapshot();
                      return !!state?.timelineGestureContext?.active &&
                        state.timelineGestureContext.targetTrackId==='video-secondary' &&
                        state.timelineDropTarget?.trackId==='video-secondary' &&
                        state.timelineDropTarget.invalid===false &&
                        state.timelineGesturePreview?.['clip-a']?.targetTrackId==='video-secondary';
                    }""",
                    timeout=5000,
                )
                page.locator(
                    '#fs-timeline [data-gesture-ghost-for="clip-a"]'
                    '[data-gesture-target-track="video-secondary"]'
                ).wait_for(state="visible")
                assert edit_requests == []
                assert film_path.read_bytes() == original_bytes
                assert fd.read_project_film(project_id)["rev"] == before_revision
                preview_state = page.evaluate(
                    """() => {
                      const state=window.filmStudioStore.getSnapshot();
                      const preview=state.timelineGesturePreview?.['clip-a'];
                      return {revision:state.revision,at:preview?.left,targetTrackId:preview?.targetTrackId};
                    }"""
                )
                assert preview_state["revision"] == before_revision
                assert preview_state["targetTrackId"] == "video-secondary"

                page.mouse.up()
                page.wait_for_function(
                    """args => {
                      const state=window.filmStudioStore?.getSnapshot();
                      const tracks=state?.film?.tracks||[];
                      const source=tracks.find(track=>track.id==='video-main');
                      const target=tracks.find(track=>track.id==='video-secondary');
                      const moved=target?.clips?.find(clip=>clip.id==='clip-a');
                      return !!state && !state.busy && state.pendingTransaction===null &&
                        state.timelineGestureContext===null && state.timelineGesturePreview===null &&
                        state.revision!==args.revision &&
                        !source?.clips?.some(clip=>clip.id==='clip-a') &&
                        !!moved && Math.abs(Number(moved.at)-1)<0.001;
                    }""",
                    arg={"revision": before_revision},
                    timeout=10000,
                )
                after = fd.read_project_film(project_id)
                after_bytes = film_path.read_bytes()
                after_revision = after["rev"]
                persisted = {
                    track["id"]: {clip["id"]: clip for clip in track["clips"]}
                    for track in after["doc"]["tracks"]
                }
                assert "clip-a" not in persisted["video-main"]
                assert abs(float(persisted["video-secondary"]["clip-a"]["attrs"]["at"]) - 1.0) < 0.001
                assert persisted["video-main"]["clip-b"]["attrs"]["at"] == "6"
                assert persisted["video-secondary"]["clip-target"]["attrs"]["at"] == "8"
                assert after_revision != before_revision
                assert len(edit_requests) == 1, edit_requests
                request = edit_requests[0]
                assert request.get("base") == before_revision, request
                operations = request.get("ops") or []
                assert len(operations) == 1, request
                assert operations[0].get("op") == "move", request
                assert operations[0].get("clip") == "clip-a", request
                assert operations[0].get("track_id") == "video-secondary", request
                assert abs(float(operations[0].get("at", -1)) - 1.0) < 0.001, request
                assert page.evaluate(
                    """() => {
                      const state=window.filmStudioStore.getSnapshot();
                      return {revision:state.revision,busy:state.busy,pending:state.pendingTransaction};
                    }"""
                ) == {"revision": after_revision, "busy": False, "pending": None}

                # One undo restores the original ownership and exact bytes.
                page.evaluate("() => window.filmStudioUndo()")
                page.wait_for_function(
                    """revision => {
                      const state=window.filmStudioStore?.getSnapshot();
                      return !!state && !state.busy && state.pendingTransaction===null &&
                        state.revision===revision && state.timelineGestureContext===null;
                    }""",
                    arg=before_revision,
                    timeout=10000,
                )
                assert film_path.read_bytes() == original_bytes
                assert _clip_positions(project_id) == {
                    "clip-a": 0.0, "clip-b": 6.0, "clip-target": 8.0
                }
                assert page.evaluate("window.filmStudioStore.getSnapshot().revision") == before_revision

                # Redo is the same single history entry, including cross-track ownership.
                page.evaluate("() => window.filmStudioRedo()")
                page.wait_for_function(
                    """revision => {
                      const state=window.filmStudioStore?.getSnapshot();
                      const target=(state?.film?.tracks||[]).find(track=>track.id==='video-secondary');
                      const moved=target?.clips?.find(clip=>clip.id==='clip-a');
                      return !!state && !state.busy && state.pendingTransaction===null &&
                        state.revision===revision && !!moved && Math.abs(Number(moved.at)-1)<0.001;
                    }""",
                    arg=after_revision,
                    timeout=10000,
                )
                assert film_path.read_bytes() == after_bytes
                assert _clip_positions(project_id) == {
                    "clip-a": 1.0, "clip-b": 6.0, "clip-target": 8.0
                }
                assert page.evaluate("window.filmStudioStore.getSnapshot().revision") == fd.read_project_film(project_id)["rev"]
                assert len(edit_requests) == 1, edit_requests
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                page.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_react_zoom_stays_anchored_and_drag_autoscroll_keeps_preview_synced(
    tmp_path, monkeypatch
):
    """Real wheel and pointer input must preserve the zoom anchor and keep drag preview in sync with edge scrolling."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-anchor-zoom-autoscroll-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    long_film = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#182030}</style></head>
<body><section id="video-main" data-label="主画面">
  <video id="clip-a" src="../assets/a.mp4#t=0,2" at="0"></video>
  <video id="clip-b" src="../assets/b.mp4#t=0,2" at="30"></video>
  <video id="clip-c" src="../assets/c.mp4#t=0,2" at="60"></video>
</section></body></html>"""
    film_path.write_text(long_film, encoding="utf-8")
    original_bytes = film_path.read_bytes()
    initial = fd.read_project_film(project_id)
    assert initial["exists"]
    assert _clip_positions(project_id)["clip-b"] == 30.0

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors: list[str] = []
            edit_requests: list[dict] = []

            def capture_edit(request):
                if request.method == "POST" and request.url.endswith("/api/editor/film/edit"):
                    try:
                        edit_requests.append(json.loads(request.post_data or "{}"))
                    except json.JSONDecodeError:
                        edit_requests.append({"_invalid_json": True})

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
                    "() => { const select=document.querySelector('#editor-project'); "
                    "return !!select && select.options.length > 0; }"
                )
                page.evaluate(
                    """pid => {
                      const select=document.querySelector('#editor-project');
                      let option=[...select.options].find(item=>item.value===pid);
                      if(!option){option=new Option(pid,pid);select.add(option);}
                      select.value=pid;
                    }""",
                    project_id,
                )
                page.evaluate("window.filmStudioToggle()")
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                for clip_id in ("clip-a", "clip-b", "clip-c"):
                    page.locator(f'#fs-timeline .fs-clip[data-clip="{clip_id}"]').wait_for(
                        state="visible"
                    )
                page.wait_for_function(
                    "() => typeof window.filmStudioReactTimeline?.renderSurface === 'function' "
                    "&& document.querySelector('.fs-react-timeline-surface')?.dataset.reactTimelineSurface === 'true' "
                    "&& document.getElementById('fs-timeline-scroll')?.dataset.reactZoomReady === 'true'",
                    timeout=10_000,
                )

                # Use a real Ctrl+wheel event over the React timeline. Zoom must keep
                # the same timeline second beneath the pointer, not just scale the ruler.
                before_zoom = page.evaluate("""() => {
                  const scroll=document.getElementById('fs-timeline-scroll');
                  scroll.scrollLeft=220;
                  const rect=scroll.getBoundingClientRect();
                  const anchorX=rect.left+scroll.clientWidth*0.5;
                  const localX=anchorX-rect.left;
                  const pps=window.filmStudioStore.getSnapshot().pixelsPerSecond;
                  return {
                    anchorX,anchorY:rect.top+Math.min(80,rect.height/2),
                    scrollLeft:scroll.scrollLeft,pps,
                    anchorTime:(scroll.scrollLeft+localX-132)/pps,
                    scrollWidth:scroll.scrollWidth,clientWidth:scroll.clientWidth
                  };
                }""")
                assert before_zoom["scrollWidth"] > before_zoom["clientWidth"] + 200, before_zoom
                page.mouse.move(before_zoom["anchorX"], before_zoom["anchorY"])
                page.keyboard.down("Control")
                page.mouse.wheel(0, -120)
                page.keyboard.up("Control")
                page.wait_for_function(
                    "before => window.filmStudioStore.getSnapshot().pixelsPerSecond !== before.pps",
                    arg=before_zoom,
                    timeout=5000,
                )
                after_zoom = page.evaluate("""anchorX => {
                  const scroll=document.getElementById('fs-timeline-scroll');
                  const rect=scroll.getBoundingClientRect();
                  const localX=anchorX-rect.left;
                  const pps=window.filmStudioStore.getSnapshot().pixelsPerSecond;
                  return {
                    scrollLeft:scroll.scrollLeft,pps,
                    anchorTime:(scroll.scrollLeft+localX-132)/pps
                  };
                }""", before_zoom["anchorX"])
                assert after_zoom["pps"] != before_zoom["pps"], after_zoom
                assert abs(after_zoom["anchorTime"]-before_zoom["anchorTime"]) < 0.05, {
                    "before": before_zoom, "after": after_zoom
                }

                # Start a real React clip drag and hold the pointer against the
                # right edge. The scroll timer must replay the gesture using the
                # owning pointer ID; otherwise onMove rejects it and the preview
                # remains at an obsolete content offset while the viewport scrolls.
                scroll = page.locator("#fs-timeline-scroll")
                scroll_box = scroll.bounding_box()
                clip = page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]')
                clip_box = clip.bounding_box()
                assert scroll_box and clip_box, {"scroll": scroll_box, "clip": clip_box}
                start_x = clip_box["x"] + clip_box["width"] / 2
                start_y = clip_box["y"] + clip_box["height"] / 2
                target_x = scroll_box["x"] + scroll_box["width"] - 8
                assert scroll_box["x"] < start_x < target_x < 1440, {
                    "scroll": scroll_box, "clip": clip_box, "target_x": target_x
                }
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(target_x, start_y, steps=12)
                page.wait_for_function(
                    """() => {
                      const state=window.filmStudioStore?.getSnapshot();
                      const preview=state?.timelineGesturePreview?.['clip-b'];
                      return !!state?.timelineGestureContext?.active && Number.isFinite(Number(preview?.left));
                    }""",
                    timeout=5000,
                )
                baseline = page.evaluate("""() => {
                  const scroll=document.getElementById('fs-timeline-scroll');
                  const state=window.filmStudioStore.getSnapshot();
                  return {
                    scrollLeft:scroll.scrollLeft,
                    left:Number(state.timelineGesturePreview?.['clip-b']?.left),
                    revision:state.revision,
                    pps:state.pixelsPerSecond
                  };
                }""")
                assert baseline["left"] > 0, baseline
                page.wait_for_function(
                    """baseline => {
                      const scroll=document.getElementById('fs-timeline-scroll');
                      const state=window.filmStudioStore?.getSnapshot();
                      const preview=state?.timelineGesturePreview?.['clip-b'];
                      const left=Number(preview?.left);
                      const scrollDelta=scroll.scrollLeft-baseline.scrollLeft;
                      return scrollDelta >= 64 && Number.isFinite(left) &&
                        Math.abs((left-baseline.left)-scrollDelta) < 2;
                    }""",
                    arg=baseline,
                    timeout=5000,
                )
                during_drag = page.evaluate("""() => {
                  const scroll=document.getElementById('fs-timeline-scroll');
                  const state=window.filmStudioStore.getSnapshot();
                  return {
                    scrollLeft:scroll.scrollLeft,
                    left:Number(state.timelineGesturePreview?.['clip-b']?.left),
                    revision:state.revision
                  };
                }""")
                assert during_drag["revision"] == initial["rev"], during_drag
                assert edit_requests == [], edit_requests
                assert film_path.read_bytes() == original_bytes

                page.mouse.up()
                page.wait_for_function(
                    "revision => {const s=window.filmStudioStore?.getSnapshot();"
                    "return !!s && !s.busy && s.pendingTransaction === null && s.revision !== revision;}",
                    arg=initial["rev"],
                    timeout=10_000,
                )
                assert len(edit_requests) == 1, edit_requests
                operations = edit_requests[0].get("ops") or []
                assert len(operations) == 1 and operations[0].get("op") == "move", edit_requests
                assert operations[0].get("clip") == "clip-b", operations
                assert float(operations[0].get("at", 0)) > 30.0, operations

                final = fd.read_project_film(project_id)
                final_clip = next(
                    clip for track in final["doc"]["tracks"]
                    for clip in track["clips"] if clip["id"] == "clip-b"
                )
                assert float(final_clip["attrs"]["at"]) > 30.0, final_clip
                assert final["rev"] != initial["rev"]
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)




def test_react_dock_dividers_persist_layout_and_keep_inspector_and_ai_visible(tmp_path, monkeypatch):
    """Real Chromium pointer drags resize all dock seams and restore dimensions on reload."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root
    project_id = "film-studio-dock-layout-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(FILM, encoding="utf-8")
    initial_revision = fd.read_project_film(project_id)["rev"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            def open_studio():
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="domcontentloaded",
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.filmStudioToggle === 'function'")
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function(
                    "() => { const select=document.querySelector('#editor-project'); "
                    "return !!select && select.options.length > 0; }"
                )
                page.evaluate(
                    """pid => {
                      const select=document.querySelector('#editor-project');
                      let option=[...select.options].find(item=>item.value===pid);
                      if(!option){option=new Option(pid,pid);select.add(option);}
                      select.value=pid;
                    }""",
                    project_id,
                )
                page.evaluate("window.filmStudioToggle()")
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.wait_for_function("""() => {
                  const inspector=document.querySelector('#fs-side-inspector');
                  const ai=document.querySelector('#fs-side-ai');
                  return !!inspector && !!ai &&
                    inspector.getBoundingClientRect().height > 80 &&
                    ai.getBoundingClientRect().height > 90;
                }""", timeout=10_000)

            def drag(selector: str, dx: float = 0, dy: float = 0):
                bounds = page.locator(selector).bounding_box()
                assert bounds and bounds["width"] > 0 and bounds["height"] > 0, {
                    "selector": selector, "bounds": bounds
                }
                x = bounds["x"] + bounds["width"] / 2
                y = bounds["y"] + bounds["height"] / 2
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + dx, y + dy, steps=6)
                page.mouse.up()

            try:
                open_studio()
                assert page.locator(".fs-pane-divider[data-divider='left']").is_visible()
                assert page.locator(".fs-pane-divider[data-divider='right']").is_visible()
                both = page.evaluate("""() => {
                  const workbench=document.querySelector('#fs-side-workbench').getBoundingClientRect();
                  const inspector=document.querySelector('#fs-side-inspector').getBoundingClientRect();
                  const ai=document.querySelector('#fs-side-ai').getBoundingClientRect();
                  return {workbench:{left:workbench.left,right:workbench.right},
                    inspector:{left:inspector.left,right:inspector.right,height:inspector.height},
                    ai:{left:ai.left,right:ai.right,height:ai.height}};
                }""")
                assert both["inspector"]["height"] > 80 and both["ai"]["height"] > 90, both
                assert both["inspector"]["left"] >= both["workbench"]["left"], both
                assert both["ai"]["right"] <= both["workbench"]["right"] + 1, both

                drag(".fs-pane-divider[data-divider='left']", dx=56)
                drag(".fs-pane-divider[data-divider='right']", dx=-52)
                drag("#fs-side-divider", dy=34)
                drag("#fs-timeline-divider", dy=-32)

                page.wait_for_function("""() => [
                  'film-studio-left-pane','film-studio-right-pane',
                  'film-studio-inspector-share','film-studio-timeline-height'
                ].every(key => localStorage.getItem(key) !== null)""", timeout=5000)
                before = page.evaluate("""() => ({
                  left:Number(localStorage.getItem('film-studio-left-pane')),
                  right:Number(localStorage.getItem('film-studio-right-pane')),
                  share:Number(localStorage.getItem('film-studio-inspector-share')),
                  height:Number(localStorage.getItem('film-studio-timeline-height')),
                  cssLeft:parseFloat(getComputedStyle(document.querySelector('#fs-workspace-dock')).getPropertyValue('--fs-left')),
                  cssRight:parseFloat(getComputedStyle(document.querySelector('#fs-workspace-dock')).getPropertyValue('--fs-right')),
                  cssShare:parseFloat(getComputedStyle(document.querySelector('#fs-side-workbench')).getPropertyValue('--fs-inspector-share')),
                  cssHeight:parseFloat(getComputedStyle(document.querySelector('#film-studio-panel')).getPropertyValue('--fs-timeline-height'))
                })""")
                for key in ("left","right","share","height","cssLeft","cssRight","cssShare","cssHeight"):
                    assert before[key] > 0, {"field":key,"state":before}
                assert abs(before["left"]-before["cssLeft"]) < 0.1, before
                assert abs(before["right"]-before["cssRight"]) < 0.1, before
                assert abs(before["share"]-before["cssShare"]) < 0.1, before
                assert abs(before["height"]-before["cssHeight"]) <= 1.1, before

                page.reload(wait_until="domcontentloaded")
                open_studio()
                after = page.evaluate("""() => ({
                  left:Number(localStorage.getItem('film-studio-left-pane')),
                  right:Number(localStorage.getItem('film-studio-right-pane')),
                  share:Number(localStorage.getItem('film-studio-inspector-share')),
                  height:Number(localStorage.getItem('film-studio-timeline-height')),
                  cssLeft:parseFloat(getComputedStyle(document.querySelector('#fs-workspace-dock')).getPropertyValue('--fs-left')),
                  cssRight:parseFloat(getComputedStyle(document.querySelector('#fs-workspace-dock')).getPropertyValue('--fs-right')),
                  cssShare:parseFloat(getComputedStyle(document.querySelector('#fs-side-workbench')).getPropertyValue('--fs-inspector-share')),
                  cssHeight:parseFloat(getComputedStyle(document.querySelector('#film-studio-panel')).getPropertyValue('--fs-timeline-height')),
                  inspectorHeight:document.querySelector('#fs-side-inspector').getBoundingClientRect().height,
                  aiHeight:document.querySelector('#fs-side-ai').getBoundingClientRect().height
                })""")
                for key in ("left","right","share","height","cssLeft","cssRight","cssShare","cssHeight"):
                    assert after[key] > 0, {"field":key,"state":after}
                for key in ("left","right","share"):
                    assert abs(after[key]-before[key]) < 0.1, {"field":key,"before":before,"after":after}
                assert abs(after["height"]-before["height"]) <= 1.1, {"before":before,"after":after}
                assert abs(after["left"]-after["cssLeft"]) < 0.1, after
                assert abs(after["right"]-after["cssRight"]) < 0.1, after
                assert abs(after["share"]-after["cssShare"]) < 0.1, after
                assert abs(after["height"]-after["cssHeight"]) <= 1.1, after
                assert after["inspectorHeight"] > 80 and after["aiHeight"] > 90, after
                assert fd.read_project_film(project_id)["rev"] == initial_revision
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
