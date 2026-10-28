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
                assert page.evaluate(
                    "document.activeElement === document.querySelector('#fs-timeline #fs-ruler-body')"
                ), "React time ruler must retain keyboard focus after seeking"
                page.keyboard.press("ArrowLeft")
                wait_for_ruler_seek(0.0)
                stale_reply = page.evaluate("""async () => {
                  const frame=document.querySelector('#fs-preview-composition');
                  const state=window.filmStudioStore.getSnapshot();
                  const token='stale-seek-'+Date.now();
                  const stale={type:'film-runtime:time',time:1,playing:false,
                    duration:state.duration,rev:state.revision,seekId:0};
                  const ack={type:'test:stale-seek-ack',token};
                  return await new Promise(resolve => {
                    const onMessage=event => {
                      if(event.source!==frame.contentWindow||event.data?.type!==ack.type||event.data?.token!==token)return;
                      window.removeEventListener('message',onMessage);
                      const current=window.filmStudioStore.getSnapshot();
                      const ruler=document.querySelector('#fs-timeline #fs-ruler-body');
                      resolve({playhead:Number(current.playhead),aria:Number(ruler?.getAttribute('aria-valuenow'))});
                    };
                    window.addEventListener('message',onMessage);
                    const target=JSON.stringify(window.location.origin);
                    frame.contentWindow.eval(
                      'parent.postMessage('+JSON.stringify(stale)+','+target+');'+
                      'parent.postMessage('+JSON.stringify(ack)+','+target+');'
                    );
                  });
                }""")
                assert stale_reply == {"playhead": 0, "aria": 0}, stale_reply
                assert page.evaluate(
                    "document.activeElement === document.querySelector('#fs-timeline #fs-ruler-body')"
                ), "React time ruler must retain keyboard focus for successive keyboard seeks"
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

                # A single pointer gesture must recover from an invalid overlapping
                # destination when the pointer returns to a valid cross-track position.
                # No stale error may survive this target transition.
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
                page.mouse.move(target_x, target_y, steps=8)
                page.wait_for_function(
                    """() => {
                      const state=window.filmStudioStore?.getSnapshot();
                      return !!state?.timelineGestureContext?.active &&
                        state.timelineGestureContext.targetTrackId==='video-secondary' &&
                        !state.timelineGestureContext.invalidTargetMessage &&
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


def test_react_clip_drag_is_discarded_when_revision_changes_mid_gesture(tmp_path, monkeypatch):
    """A concurrent FilmDocument edit invalidates an active pointer drag without clobbering it."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-stale-drag-browser"
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
</body></html>'''
    film_path.write_text(source_text, encoding="utf-8")
    original_bytes = film_path.read_bytes()
    original = fd.read_project_film(project_id)
    assert original["exists"]
    assert _clip_positions(project_id) == {"clip-a": 0.0, "clip-b": 6.0}

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

                clip = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]')
                clip.wait_for(state="visible")
                scale = float(page.evaluate("window.filmStudioStore.getSnapshot().pixelsPerSecond"))
                box = clip.bounding_box()
                assert box and scale > 0
                start_x = box["x"] + box["width"] / 2
                start_y = box["y"] + box["height"] / 2
                start_revision = original["rev"]

                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(start_x + 2 * scale, start_y, steps=8)
                page.wait_for_function(
                    """revision => {
                      const state=window.filmStudioStore?.getSnapshot();
                      return !!state && state.revision===revision &&
                        !!state.timelineGestureContext?.active &&
                        !!state.timelineGesturePreview?.['clip-a'];
                    }""",
                    arg=start_revision,
                    timeout=5000,
                )
                assert edit_requests == [], edit_requests
                assert film_path.read_bytes() == original_bytes

                # Simulate another editor/Agent committing while this pointer is still down.
                external = page.evaluate(
                    """async args => {
                      const response=await fetch('/api/editor/film/edit',{
                        method:'POST',headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({
                          project_id:args.projectId,base:args.revision,
                          ops:[{op:'move',clip:'clip-b',track_id:'video-main',at:7}]
                        })
                      });
                      return {status:response.status,body:await response.json()};
                    }""",
                    {"projectId": project_id, "revision": start_revision},
                )
                assert external["status"] == 200, external
                changed_revision = external["body"].get("rev")
                assert changed_revision and changed_revision != start_revision, external
                external_bytes = film_path.read_bytes()
                assert _clip_positions(project_id) == {"clip-a": 0.0, "clip-b": 7.0}

                page.wait_for_function(
                    """args => {
                      const state=window.filmStudioStore?.getSnapshot();
                      return !!state && state.revision===args.startRevision &&
                        state.pendingExternalRevision===args.changedRevision;
                    }""",
                    arg={"startRevision": start_revision, "changedRevision": changed_revision},
                    timeout=5000,
                )
                pending = page.evaluate("""() => {
                  const s=window.filmStudioStore.getSnapshot();
                  return {revision:s.revision,pendingExternalRevision:s.pendingExternalRevision,
                    gesture:!!s.timelineGestureContext,preview:!!s.timelineGesturePreview};
                }""")
                assert pending["revision"] == start_revision, pending
                assert pending["gesture"] and pending["preview"], pending

                page.mouse.up()
                page.wait_for_function(
                    """args => {
                      const state=window.filmStudioStore?.getSnapshot();
                      return !!state && !state.busy && state.pendingTransaction===null &&
                        state.revision===args.changedRevision &&
                        state.timelineGestureContext===null &&
                        state.timelineGesturePreview===null &&
                        !state.pendingExternalRevision;
                    }""",
                    arg={"changedRevision": changed_revision},
                    timeout=10000,
                )

                final = fd.read_project_film(project_id)
                assert final["rev"] == changed_revision, final
                assert film_path.read_bytes() == external_bytes
                assert _clip_positions(project_id) == {"clip-a": 0.0, "clip-b": 7.0}
                # Only the concurrent external edit may have reached the edit endpoint.
                assert len(edit_requests) == 1, edit_requests
                assert edit_requests[0].get("base") == start_revision, edit_requests
                operations = edit_requests[0].get("ops") or []
                assert len(operations) == 1 and operations[0].get("op") == "move", operations
                assert operations[0].get("clip") == "clip-b", operations
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
                  const divider=document.querySelector('#fs-side-divider').getBoundingClientRect();
                  const ai=document.querySelector('#fs-side-ai').getBoundingClientRect();
                  return {workbench:{left:workbench.left,right:workbench.right},
                    inspector:{left:inspector.left,right:inspector.right,top:inspector.top,bottom:inspector.bottom,height:inspector.height},
                    divider:{top:divider.top,bottom:divider.bottom},
                    ai:{left:ai.left,right:ai.right,top:ai.top,bottom:ai.bottom,height:ai.height}};
                }""")
                assert both["inspector"]["height"] > 80 and both["ai"]["height"] > 90, both
                assert both["inspector"]["left"] >= both["workbench"]["left"], both
                assert both["ai"]["right"] <= both["workbench"]["right"] + 1, both
                assert both["inspector"]["bottom"] <= both["divider"]["top"] + 8, both
                assert both["divider"]["bottom"] <= both["ai"]["top"] + 8, both

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
                  inspectorTop:document.querySelector('#fs-side-inspector').getBoundingClientRect().top,
                  inspectorBottom:document.querySelector('#fs-side-inspector').getBoundingClientRect().bottom,
                  dividerTop:document.querySelector('#fs-side-divider').getBoundingClientRect().top,
                  dividerBottom:document.querySelector('#fs-side-divider').getBoundingClientRect().bottom,
                  aiTop:document.querySelector('#fs-side-ai').getBoundingClientRect().top,
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
                assert after["inspectorBottom"] <= after["dividerTop"] + 8, after
                assert after["dividerBottom"] <= after["aiTop"] + 8, after
                assert fd.read_project_film(project_id)["rev"] == initial_revision
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_react_timeline_markers_persist_and_share_revisioned_undo_redo(tmp_path, monkeypatch):
    """The real React timeline adds, seeks, renames, undoes and redoes persistent markers."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-markers-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(FILM, encoding="utf-8")
    original_text = film_path.read_text(encoding="utf-8")
    before = fd.read_project_film(project_id)

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
                response = page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
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
                page.locator("[data-film-add-marker='true']").wait_for(state="visible")
                page.locator("#fs-timeline #fs-ruler-body .fs-ruler-mark[data-at='5']").click()
                page.wait_for_function(
                    "() => Math.abs(Number(window.filmStudioStore.getSnapshot().playhead)-5)<0.001"
                )

                with page.expect_response(
                    lambda response: response.request.method == "POST"
                    and response.url.endswith("/api/editor/film/edit")
                ) as add_response:
                    page.once("dialog", lambda dialog: dialog.accept("序章"))
                    page.locator("[data-film-add-marker='true']").click()
                add_request = json.loads(add_response.value.request.post_data or "{}")
                assert add_request.get("base") == before["rev"], add_request
                assert len(add_request.get("ops") or []) == 1, add_request
                add_operation = add_request["ops"][0]
                assert add_operation.get("op") == "marker_add", add_request
                assert add_operation.get("at") == 5, add_request
                assert add_operation.get("label") == "序章", add_request
                marker_id = add_operation["id"]

                marker_locator = page.locator(
                    f".fs-ruler-film-marker[data-film-timeline-marker-id='{marker_id}']"
                )
                marker_locator.wait_for(state="visible")
                persisted_add = fd.read_project_film(project_id)
                assert persisted_add["doc"]["markers"] == [
                    {"id": marker_id, "at": 5.0, "label": "序章"}
                ]
                assert len(edit_requests) == 1

                page.evaluate("window.filmStudioSeek(0)")
                page.wait_for_function(
                    "() => Math.abs(Number(window.filmStudioStore.getSnapshot().playhead))<0.001"
                )
                marker_locator.click()
                page.wait_for_function(
                    "() => Math.abs(Number(window.filmStudioStore.getSnapshot().playhead)-5)<0.001"
                )

                with page.expect_response(
                    lambda response: response.request.method == "POST"
                    and response.url.endswith("/api/editor/film/edit")
                ) as rename_response:
                    page.once("dialog", lambda dialog: dialog.accept("片头结束"))
                    marker_locator.click(button="right")
                rename_request = json.loads(rename_response.value.request.post_data or "{}")
                assert rename_request.get("base") == persisted_add["rev"], rename_request
                assert rename_request.get("ops") == [
                    {"op": "marker_set", "id": marker_id, "label": "片头结束"}
                ], rename_request
                renamed_text = fd.read_project_film(project_id)["text"]
                assert fd.read_project_film(project_id)["doc"]["markers"] == [
                    {"id": marker_id, "at": 5.0, "label": "片头结束"}
                ]
                assert len(edit_requests) == 2
                assert not page_errors, page_errors

                page.evaluate("window.filmStudioUndo()")
                page.wait_for_function(
                    "() => document.querySelector('.fs-ruler-film-marker')?.title.includes('序章')"
                )
                assert fd.read_project_film(project_id)["doc"]["markers"] == [
                    {"id": marker_id, "at": 5.0, "label": "序章"}
                ]

                page.evaluate("window.filmStudioUndo()")
                page.wait_for_function(
                    "() => !document.querySelector('.fs-ruler-film-marker') "
                    "&& window.filmStudioStore.getSnapshot().revision === " + json.dumps(before["rev"])
                )
                assert film_path.read_text(encoding="utf-8") == original_text

                page.evaluate("window.filmStudioRedo()")
                page.wait_for_function(
                    "() => document.querySelector('.fs-ruler-film-marker')?.title.includes('序章')"
                )
                page.evaluate("window.filmStudioRedo()")
                page.wait_for_function(
                    "() => document.querySelector('.fs-ruler-film-marker')?.title.includes('片头结束')"
                )
                assert fd.read_project_film(project_id)["text"] == renamed_text
                assert fd.read_project_film(project_id)["doc"]["markers"] == [
                    {"id": marker_id, "at": 5.0, "label": "片头结束"}
                ]
                assert len(edit_requests) == 2
                assert not page_errors, page_errors
            finally:
                page.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_react_speed_preset_commits_one_operation_and_undo_redo_restores_duration(
    tmp_path, monkeypatch
):
    """The speed chips must edit the authoritative film once and survive history replay."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-speed-preset-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    # Keep every other inspector field explicit so this preset produces exactly one op.
    film_source = FILM.replace(
        'id="clip-a" src="../assets/a.mp4#t=0,2" at="0"',
        'id="clip-a" src="../assets/a.mp4#t=0,2" at="0" '
        'speed="1" volume="1" fade="0,0" title="clip-a"',
    )
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(film_source, encoding="utf-8")
    original_text = film_path.read_text(encoding="utf-8")
    initial = fd.read_project_film(project_id)
    assert initial["exists"]
    initial_revision = initial["rev"]

    def clip_state():
        state = fd.read_project_film(project_id)
        return next(
            clip
            for track in state["doc"]["tracks"]
            for clip in track["clips"]
            if clip["id"] == "clip-a"
        )

    initial_clip = clip_state()
    assert abs(fd.clip_duration(initial_clip) - 2.0) < 0.001

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
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
                clip = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]')
                clip.wait_for(state="visible")
                page.wait_for_function(
                    "revision => window.filmStudioStore?.getSnapshot()?.revision === revision",
                    arg=initial_revision,
                    timeout=10000,
                )

                before_box = clip.bounding_box()
                assert before_box, "React timeline clip has no browser geometry"
                clip.click()
                speed_field = page.locator("#fs-field-speed")
                speed_field.wait_for(state="visible")
                preset = page.locator('[data-speed-preset="2"]')
                preset.wait_for(state="visible")
                edit_count_before = len(edit_requests)
                preset.click()
                page.wait_for_function(
                    """() => document.querySelector('#fs-field-speed')?.value === '2' &&
                      document.querySelector('[data-speed-preset="2"]')?.classList.contains('fs-speed-chip-active')"""
                )
                # A preset selection is a draft until the user explicitly applies it.
                assert len(edit_requests) == edit_count_before, edit_requests
                page.locator("#fs-apply-clip-inspector").click()
                page.wait_for_function(
                    """oldRevision => {
                      const state=window.filmStudioStore?.getSnapshot();
                      return !!state && !state.busy && state.pendingTransaction===null &&
                        state.revision!==oldRevision;
                    }""",
                    arg=initial_revision,
                    timeout=10000,
                )

                committed = fd.read_project_film(project_id)
                committed_revision = committed["rev"]
                committed_text = film_path.read_text(encoding="utf-8")
                committed_clip = clip_state()
                assert committed_revision != initial_revision
                assert float(committed_clip["attrs"]["speed"]) == 2.0
                assert abs(fd.clip_duration(committed_clip) - 1.0) < 0.001
                after_box = clip.bounding_box()
                assert after_box and after_box["width"] < before_box["width"] - 8, (
                    before_box, after_box
                )
                assert len(edit_requests) == edit_count_before + 1, edit_requests
                request = edit_requests[-1]
                assert request.get("base") == initial_revision, request
                assert request.get("ops") == [
                    {"op": "set", "clip": "clip-a", "field": "speed", "value": 2}
                ], request

                # Undo restores both the exact file bytes and the previous timeline duration.
                page.evaluate("() => window.filmStudioUndo()")
                page.wait_for_function(
                    """revision => {
                      const state=window.filmStudioStore?.getSnapshot();
                      return !!state && !state.busy && state.pendingTransaction===null &&
                        state.revision===revision;
                    }""",
                    arg=initial_revision,
                    timeout=10000,
                )
                assert film_path.read_text(encoding="utf-8") == original_text
                assert abs(fd.clip_duration(clip_state()) - 2.0) < 0.001
                undone_box = clip.bounding_box()
                assert undone_box and abs(undone_box["width"] - before_box["width"]) < 1

                # Redo restores the same committed revision and visual duration.
                page.evaluate("() => window.filmStudioRedo()")
                page.wait_for_function(
                    """revision => {
                      const state=window.filmStudioStore?.getSnapshot();
                      return !!state && !state.busy && state.pendingTransaction===null &&
                        state.revision===revision;
                    }""",
                    arg=committed_revision,
                    timeout=10000,
                )
                assert film_path.read_text(encoding="utf-8") == committed_text
                assert abs(fd.clip_duration(clip_state()) - 1.0) < 0.001
                redone_box = clip.bounding_box()
                assert redone_box and abs(redone_box["width"] - after_box["width"]) < 1
                assert abs(float(speed_field.input_value()) - 2.0) < 0.001
                assert not page_errors, page_errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_react_context_menu_fade_transition_is_one_reversible_two_clip_transaction(
    tmp_path, monkeypatch
):
    """The real React context menu creates a bounded fade-through-black handoff without moving clips."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-fade-transition-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    source_text = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#182030}</style></head>
<body><section id="video-main" data-label="主画面">
  <video id="clip-a" src="../assets/a.mp4#t=0,2" at="0"></video>
  <video id="clip-b" src="../assets/b.mp4#t=0,2" at="2"></video>
  <video id="clip-c" src="../assets/c.mp4#t=0,2" at="4"></video>
</section></body></html>'''
    film_path.write_text(source_text, encoding="utf-8")
    before_text = film_path.read_text(encoding="utf-8")
    before = fd.read_project_film(project_id)
    assert before["exists"]
    assert _clip_positions(project_id) == {"clip-a": 0.0, "clip-b": 2.0, "clip-c": 4.0}

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
                clip_a = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]')
                clip_a.wait_for(state="visible")
                clip_b = page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]')
                clip_b.wait_for(state="visible")
                page.mouse.click(
                    (clip_a.bounding_box() or {})["x"] + 12,
                    (clip_a.bounding_box() or {})["y"] + 12,
                    button="right",
                )
                menu_item = page.locator('.fs-context-menu [data-context-action="fade-transition"]')
                menu_item.wait_for(state="visible")
                menu_item.click()

                page.wait_for_function(
                    """revision => {
                      const s=window.filmStudioStore?.getSnapshot();
                      return !!s && !s.busy && s.pendingTransaction===null &&
                        s.revision!==revision;
                    }""",
                    arg=before["rev"],
                    timeout=10000,
                )
                after = fd.read_project_film(project_id)
                assert after["rev"] != before["rev"]
                assert _clip_positions(project_id) == {"clip-a": 0.0, "clip-b": 2.0, "clip-c": 4.0}
                clips = {
                    clip["id"]: clip
                    for track in after["doc"]["tracks"]
                    for clip in track["clips"]
                }
                assert [float(value) for value in clips["clip-a"]["attrs"]["fade"].split(",")] == [0.0, 0.5]
                assert [float(value) for value in clips["clip-b"]["attrs"]["fade"].split(",")] == [0.5, 0.0]
                assert "fade" not in clips["clip-c"]["attrs"] or clips["clip-c"]["attrs"]["fade"] in {"0,0", "0.000,0.000"}
                assert len(edit_requests) == 1, edit_requests
                ops = edit_requests[0].get("ops") or []
                assert len(ops) == 2, ops
                assert all(op.get("op") == "set" and op.get("field") == "fade" for op in ops), ops
                assert {op.get("clip") for op in ops} == {"clip-a", "clip-b"}, ops
                assert not page_errors, f"browser JavaScript errors: {page_errors}"

                page.evaluate("window.filmStudioUndo()")
                page.wait_for_function(
                    """revision => {
                      const state=window.filmStudioStore?.getSnapshot();
                      return !!state && !state.busy && state.pendingTransaction===null &&
                        state.revision===revision;
                    }""",
                    arg=before["rev"],
                    timeout=10000,
                )
                assert film_path.read_text(encoding="utf-8") == before_text
                assert fd.read_project_film(project_id)["rev"] == before["rev"]
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                page.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)




def test_react_context_menu_crossfade_overlaps_two_clips_in_one_reversible_transaction(
    tmp_path, monkeypatch
):
    """Real Chromium verifies overlapping visual crossfade, one transaction, undo and redo."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-crossfade-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    source_text = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"></head>
<body><section id="video-main" data-label="主画面">
  <video id="clip-a" src="../assets/a.mp4#t=0,2" at="0"></video>
  <video id="clip-b" src="../assets/b.mp4#t=0,2" at="2"></video>
  <video id="clip-c" src="../assets/c.mp4#t=0,2" at="4"></video>
</section></body></html>'''
    film_path.write_text(source_text, encoding="utf-8")
    before_text = film_path.read_text(encoding="utf-8")
    before = fd.read_project_film(project_id)
    assert before["exists"]

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
                clip_a = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]')
                clip_a.wait_for(state="visible")
                clip_a_box = clip_a.bounding_box()
                assert clip_a_box, "selected clip has no browser bounds"
                page.mouse.click(
                    clip_a_box["x"] + 12, clip_a_box["y"] + 12, button="right"
                )
                menu_item = page.locator('.fs-context-menu [data-context-action="crossfade-transition"]')
                menu_item.wait_for(state="visible")
                menu_item.click()
                page.wait_for_function(
                    """revision => {
                      const s=window.filmStudioStore?.getSnapshot();
                      const alert=document.querySelector('#film-studio-alert');
                      const rejected=!!alert&&alert.classList.contains('fs-error')&&!!alert.textContent;
                      return !!s && !s.busy && s.pendingTransaction===null &&
                        (s.revision!==revision || rejected);
                    }""",
                    arg=before["rev"],
                    timeout=10000,
                )
                observed = page.evaluate("""() => {
                  const s=window.filmStudioStore?.getSnapshot();
                  const alert=document.querySelector('#film-studio-alert');
                  return {
                    revision:s?.revision||'',
                    busy:!!s?.busy,
                    pending:!!s?.pendingTransaction,
                    status:alert?.textContent||'',
                    statusClass:alert?.className||'',
                  };
                }""")
                assert observed["revision"] != before["rev"], {
                    "observed": observed,
                    "edit_requests": edit_requests,
                    "page_errors": page_errors,
                }

                after = fd.read_project_film(project_id)
                after_text = film_path.read_text(encoding="utf-8")
                after_revision = after["rev"]
                assert after_revision != before["rev"]
                assert _clip_positions(project_id) == {"clip-a": 0.0, "clip-b": 1.5, "clip-c": 3.5}
                clips = {
                    clip["id"]: clip
                    for track in after["doc"]["tracks"]
                    for clip in track["clips"]
                }
                assert fd._parse_fade(clips["clip-a"]["attrs"].get("fade")) == (0.0, 0.0)
                assert fd._parse_fade(clips["clip-b"]["attrs"]["fade"]) == (0.5, 0.0)
                assert "fade" not in clips["clip-c"]["attrs"] or fd._parse_fade(clips["clip-c"]["attrs"]["fade"]) == (0.0, 0.0)
                outgoing_envelope = fd._parse_volume_envelope(clips["clip-a"]["attrs"]["volume_envelope"])
                assert [point["t"] for point in outgoing_envelope] == [0.0, 1.5, 2.0], outgoing_envelope
                assert [point["v"] for point in outgoing_envelope] == [1.0, 1.0, 0.0], outgoing_envelope
                assert len(edit_requests) == 1, edit_requests
                operations = edit_requests[0].get("ops") or []
                assert len(operations) == 4, operations
                assert {op.get("op") for op in operations} == {"set", "move"}, operations
                assert any(op.get("op") == "set" and op.get("clip") == "clip-b"
                           and op.get("field") == "fade" for op in operations), operations
                assert any(op.get("op") == "set" and op.get("clip") == "clip-a"
                           and op.get("field") == "volume_envelope" for op in operations), operations
                assert any(op.get("op") == "move" and op.get("clip") == "clip-b"
                           and op.get("track_id") == "video-main"
                           and abs(float(op.get("at", -1)) - 1.5) < 0.001
                           for op in operations), operations
                assert any(op.get("op") == "move" and op.get("clip") == "clip-c"
                           and op.get("track_id") == "video-main"
                           and abs(float(op.get("at", -1)) - 3.5) < 0.001
                           for op in operations), operations
                assert not page_errors, f"browser JavaScript errors: {page_errors}"

                page.evaluate("window.filmStudioUndo()")
                page.wait_for_function(
                    "revision => { const s=window.filmStudioStore?.getSnapshot(); "
                    "return !!s && !s.busy && s.pendingTransaction===null && s.revision===revision; }",
                    arg=before["rev"],
                    timeout=10000,
                )
                assert film_path.read_text(encoding="utf-8") == before_text

                page.evaluate("window.filmStudioRedo()")
                page.wait_for_function(
                    "revision => { const s=window.filmStudioStore?.getSnapshot(); "
                    "return !!s && !s.busy && s.pendingTransaction===null && s.revision===revision; }",
                    arg=after_revision,
                    timeout=10000,
                )
                assert film_path.read_text(encoding="utf-8") == after_text
                assert _clip_positions(project_id) == {"clip-a": 0.0, "clip-b": 1.5, "clip-c": 3.5}
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                page.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_fade_handoff_rejects_gaps_and_locked_clips_or_tracks_without_writing(
    tmp_path, monkeypatch
):
    """Fade handoff refuses unsupported adjacency/lock cases without a FilmDocument mutation."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-fade-transition-guard-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    source_text = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"></head>
<body>
<section id="video-gap" data-label="有间隙">
  <video id="gap-current" src="../assets/a.mp4#t=0,2" at="0"></video>
  <video id="gap-next" src="../assets/b.mp4#t=0,2" at="3"></video>
</section>
<section id="video-locked-track" data-label="锁定轨道" locked>
  <video id="track-locked-current" src="../assets/c.mp4#t=0,2" at="0"></video>
  <video id="track-locked-next" src="../assets/d.mp4#t=0,2" at="2"></video>
</section>
<section id="video-locked-current" data-label="锁定片段">
  <video id="clip-locked-current" src="../assets/e.mp4#t=0,2" at="0" data-clip-locked></video>
  <video id="clip-locked-current-next" src="../assets/f.mp4#t=0,2" at="2"></video>
</section>
<section id="video-locked-next" data-label="下一片段锁定">
  <video id="clip-locked-next-current" src="../assets/g.mp4#t=0,2" at="0"></video>
  <video id="clip-locked-next" src="../assets/h.mp4#t=0,2" at="2" data-clip-locked></video>
</section>
<section id="video-crossfade-locked-successor" data-label="交叉淡化的后续片段锁定">
  <video id="crossfade-current" src="../assets/i.mp4#t=0,2" at="0"></video>
  <video id="crossfade-next" src="../assets/j.mp4#t=0,2" at="2"></video>
  <video id="crossfade-later-locked" src="../assets/k.mp4#t=0,2" at="4" data-clip-locked></video>
</section>
<section id="video-crossfade-custom-envelope" data-label="交叉淡化保留自定义音量曲线">
  <video id="crossfade-envelope-current" src="../assets/l.mp4#t=0,2" at="0"
    volume_envelope='[{"t":0,"v":1},{"t":2,"v":0.5}]'></video>
  <video id="crossfade-envelope-next" src="../assets/m.mp4#t=0,2" at="2"></video>
</section>
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
                page.wait_for_function(
                    "projectId => window.filmStudioStore?.getSnapshot()?.projectId === projectId "
                    "&& window.filmStudioStore.getSnapshot().revision === " + json.dumps(original["rev"]),
                    arg=project_id,
                    timeout=10000,
                )
                rejected = page.evaluate("""async ids => {
                  const out=[];
                  for (const id of ids) {
                    out.push({
                      id,
                      fade:await window.filmStudioApplyFadeTransition(id),
                      crossfade:await window.filmStudioApplyCrossfadeTransition(id)
                    });
                  }
                  return out;
                }""", [
                    "gap-current",                 # next clip starts after a gap
                    "track-locked-current",        # track is locked
                    "clip-locked-current",         # selected clip is locked
                    "clip-locked-next-current",    # next clip is locked
                    "gap-next",                    # no next clip in this track
                ])
                assert all(item["fade"] is False and item["crossfade"] is False for item in rejected), rejected
                blocked_by_locked_successor = page.evaluate(
                    "id => window.filmStudioApplyCrossfadeTransition(id)",
                    "crossfade-current",
                )
                assert blocked_by_locked_successor is False
                blocked_by_existing_envelope = page.evaluate(
                    "id => window.filmStudioApplyCrossfadeTransition(id)",
                    "crossfade-envelope-current",
                )
                assert blocked_by_existing_envelope is False
                assert film_path.read_bytes() == original_bytes
                assert fd.read_project_film(project_id)["rev"] == original["rev"]
                assert edit_requests == [], edit_requests
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                page.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_react_typed_gesture_start_controller_validates_modes_and_context_in_chromium(
    tmp_path, monkeypatch
):
    """Exercise the built TypeScript gesture controller in a real Chromium page."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root
    project_id = "film-studio-typed-gesture-start-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(FILM, encoding="utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
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
                    "() => { const s=document.querySelector('#editor-project'); "
                    "return !!s && s.options.length>0; }"
                )
                page.evaluate(
                    """pid => {
                      const s=document.querySelector('#editor-project');
                      let o=[...s.options].find(item=>item.value===pid);
                      if(!o){o=new Option(pid,pid);s.add(o);}
                      s.value=pid;
                    }""",
                    project_id,
                )
                page.evaluate("window.filmStudioToggle()")
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.wait_for_function(
                    "() => !!window.filmStudioTimelineController && "
                    "typeof window.filmStudioTimelineController.createGestureStart==='function'"
                )
                result = page.evaluate(
                    """() => {
                      const c=window.filmStudioTimelineController;
                      const clip=(id,at,end,attrs={})=>({id,at,end,duration:end-at,tag:"video",attrs});
                      const pointer={pointerId:7,startX:100,startY:20,startScrollLeft:0};
                      const base=[{id:"v1",attrs:{},clips:[
                        clip("a",0,2,{src:"a.mp4#t=1.25,3.25",speed:1.5}),clip("b",6,8)
                      ]}];
                      const move=c.createGestureStart({tracks:base,clipId:"a",mode:"move",selectedIds:["a","b"],...pointer});
                      const locked=c.createGestureStart({tracks:[
                        {id:"locked",attrs:{locked:"true"},clips:[clip("a",0,2)]}
                      ],clipId:"a",mode:"move",selectedIds:["a"],...pointer});
                      const roll=c.createGestureStart({tracks:[
                        {id:"v1",attrs:{},clips:[clip("a",0,2),clip("b",2,4)]}
                      ],clipId:"a",mode:"roll",trimEdge:"end",selectedIds:["a"],...pointer});
                      const slide=c.createGestureStart({tracks:[
                        {id:"v1",attrs:{},clips:[clip("l",0,2),clip("c",2,4),clip("r",4,6)]}
                      ],clipId:"c",mode:"slide",selectedIds:["c"],...pointer});
                      const invalidMode=c.createGestureStart({
                        tracks:base,clipId:"a",mode:"unknown-runtime-mode",selectedIds:["a"],...pointer
                      });
                      return {
                        modes:[
                          c.resolveGestureStartMode({clipTag:"video"}),
                          c.resolveGestureStartMode({clipTag:"video",trimEdge:"start"}),
                          c.resolveGestureStartMode({clipTag:"video",trimEdge:"end",ctrlKey:true}),
                          c.resolveGestureStartMode({clipTag:"video",altKey:true,shiftKey:true}),
                          c.resolveGestureStartMode({clipTag:"video",altKey:true}),
                          c.resolveGestureStartMode({clipTag:"img",slideMode:true})
                        ],
                        move:move.ok?{ok:true,members:move.memberIds,sourceIn:move.context.originalSourceIn,
                          speed:move.context.originalSpeed,end:move.context.originalEnd}:move,
                        locked,roll:roll.ok?{ok:true,left:roll.context.leftClipId,
                          right:roll.context.rightClipId,rightAt:roll.context.rightAt}:roll,
                        slide:slide.ok?{ok:true,left:slide.context.leftClipId,
                          center:slide.context.centerClipId,right:slide.context.rightClipId,
                          centerAt:slide.context.centerAt,rightAt:slide.context.rightAt}:slide,
                        invalidMode
                      };
                    }"""
                )
                assert result["modes"] == ["move","trim-start","roll","slide","slip","move"]
                assert result["move"] == {
                    "ok": True, "members": ["a","b"], "sourceIn": 1.25, "speed": 1.5, "end": 2
                }, result["move"]
                assert result["locked"]["ok"] is False, result["locked"]
                assert result["locked"]["error"]["code"] == "MOVE_MEMBER_BLOCKED", result["locked"]
                assert result["roll"] == {
                    "ok":True,"left":"a","right":"b","rightAt":2
                }, result["roll"]
                assert result["slide"] == {
                    "ok":True,"left":"l","center":"c","right":"r","centerAt":2,"rightAt":4
                }, result["slide"]
                assert result["invalidMode"] == {
                    "ok":False,"error":{"code":"INVALID_GESTURE_MODE"}
                }, result["invalidMode"]
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_react_timeline_rejects_gesture_without_typed_controller_instead_of_legacy_fallback(
    tmp_path, monkeypatch
):
    """Fail closed if React owns the timeline but its typed controller is unavailable."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root
    project_id = "film-studio-missing-typed-gesture-controller"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(FILM, encoding="utf-8")
    original_text = film_path.read_text(encoding="utf-8")
    original = fd.read_project_film(project_id)
    edit_requests: list[dict] = []

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})

            def capture_edit(request):
                if request.method == "POST" and request.url.endswith("/api/editor/film/edit"):
                    try:
                        edit_requests.append(json.loads(request.post_data or "{}"))
                    except json.JSONDecodeError:
                        edit_requests.append({"_invalid_json": True})

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
                    "&& !!document.querySelector('.fs-react-timeline-surface') "
                    "&& typeof window.filmStudioTimelineController?.createGestureStart === 'function' "
                    "&& typeof window.filmStudioBeginClipGesture === 'function'"
                )
                assert page.locator("#fs-static-timeline-toolbar").is_hidden()
                result = page.evaluate(
                    """() => {
                      const originalController=window.filmStudioTimelineController;
                      const originalBridge=window.filmStudioReactTimeline;
                      const attempt=(pointerId)=>{
                        const event={
                          button:0,pointerId,clientX:110,clientY:100,
                          ctrlKey:false,altKey:false,shiftKey:false,
                          defaultPrevented:false,stopped:false,
                          preventDefault(){this.defaultPrevented=true;},
                          stopPropagation(){this.stopped=true;}
                        };
                        const accepted=window.filmStudioBeginClipGesture(event,{clipId:"clip-a"});
                        return {accepted:accepted===true,defaultPrevented:event.defaultPrevented,stopped:event.stopped};
                      };
                      window.filmStudioTimelineController=undefined;
                      const missingController=attempt(777);
                      window.filmStudioTimelineController=originalController;
                      window.filmStudioReactTimeline={};
                      const missingBridge=attempt(778);
                      window.filmStudioReactTimeline=originalBridge;
                      const snapshot=window.filmStudioStore.getSnapshot();
                      return {
                        missingController,missingBridge,
                        contextPresent:!!snapshot.timelineGestureContext,
                        notice:document.body.innerText.includes(
                          "React 时间线的类型化控制器未就绪或桥接接口缺失；本次操作已拒绝，未回退到旧版 DOM 手势。"
                        )
                      };
                    }"""
                )
                expected = {"accepted":False,"defaultPrevented":True,"stopped":True}
                assert result["missingController"] == expected, result
                assert result["missingBridge"] == expected, result
                assert result["contextPresent"] is False, result
                assert result["notice"] is True, result
                assert edit_requests == [], edit_requests
                assert film_path.read_text(encoding="utf-8") == original_text
                assert fd.read_project_film(project_id)["rev"] == original["rev"]
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
