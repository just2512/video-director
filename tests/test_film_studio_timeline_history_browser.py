"""Real Chromium acceptance for one-gesture multi-select move, undo, and redo."""
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

                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').click()
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
