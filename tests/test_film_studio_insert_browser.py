"""Real-browser regression for selected-track insert mode and sync-lock ripple."""
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
<video id="video-before" src="../assets/a.mp4#t=0,4" at="0"></video>
<video id="video-later" src="../assets/c.mp4#t=0,2" at="4"></video>
</section>
<section id="audio-bed" data-label="BGM" sync-lock>
<audio id="audio-before" src="../assets/music.wav#t=0,3" at="0"></audio>
<audio id="audio-later" src="../assets/other.wav#t=0,4" at="3"></audio>
</section>
</body></html>'''


def test_insert_mode_ripples_target_and_sync_locked_tracks_in_real_studio(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    def fake_library_asset_clip(project_id, asset_id, at, still_duration):
        assert asset_id == "fixture-asset"
        return {
            "id": "inserted-asset", "tag": "video",
            "attrs": {"id": "inserted-asset", "src": "../assets/new.mp4#t=0,1",
                      "at": str(at), "volume": "1"},
        }

    monkeypatch.setattr(fd, "library_asset_clip", fake_library_asset_clip)
    project_id = "film-studio-insert-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    initial_text = film_path.read_text(encoding="utf-8")
    initial = fd.read_project_film(project_id)
    assert initial["exists"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.route("**/api/assets_library*", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps([{"asset_id": "fixture-asset", "type": "video",
                                  "description": "测试插入素材", "duration": 1}]),
            ))
            try:
                response = page.goto(f"http://127.0.0.1:{server.server_port}/",
                                     wait_until="domcontentloaded")
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
                }""", project_id)
                page.evaluate("window.filmStudioToggle()")
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="video-before"]').wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="video-before"]').click()
                page.evaluate("window.filmStudioSeek(2)")
                page.locator('[data-film-action="insert-mode"]:visible').click()
                assert page.locator('[data-film-action="insert-mode"]:visible').get_attribute("aria-pressed") == "true"
                page.evaluate("window.filmStudioLoadLibrary()")
                page.locator('[data-add-asset="fixture-asset"]').wait_for(state="visible")
                page.locator('[data-add-asset="fixture-asset"]').click()

                page.wait_for_function("""async (pid) => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(pid));
                  if (!response.ok) return false;
                  const doc = await response.json();
                  const state = window.filmStudioStore?.getSnapshot();
                  if (!state || state.busy || state.pendingTransaction !== null ||
                      doc.rev !== state.revision) return false;
                  const clips = (doc.tracks || []).flatMap(track => track.clips || []);
                  const inserted = clips.find(clip => clip.id === 'inserted-asset');
                  const laterVideo = clips.find(clip => clip.id === 'video-later');
                  const laterAudio = clips.find(clip => clip.id === 'audio-later');
                  const videoRight = clips.find(clip => String(clip.attrs.src).endsWith('#t=2,4'));
                  const audioRight = clips.find(clip => String(clip.attrs.src).endsWith('#t=2,3'));
                  return !!inserted && !!laterVideo && !!laterAudio && !!videoRight && !!audioRight
                    && Math.abs(Number(inserted.attrs.at) - 2) < 0.001
                    && Math.abs(Number(videoRight.attrs.at) - 3) < 0.001
                    && Math.abs(Number(laterVideo.attrs.at) - 5) < 0.001
                    && Math.abs(Number(audioRight.attrs.at) - 3) < 0.001
                    && Math.abs(Number(laterAudio.attrs.at) - 4) < 0.001;
                }""", arg=project_id, timeout=10000)

                persisted = fd.read_project_film(project_id)
                tracks = {track["id"]: track for track in persisted["doc"]["tracks"]}
                video = {clip["id"]: clip for clip in tracks["video-main"]["clips"]}
                audio = {clip["id"]: clip for clip in tracks["audio-bed"]["clips"]}
                assert video["inserted-asset"]["attrs"]["src"].endswith("#t=0,1")
                assert float(video["inserted-asset"]["attrs"]["at"]) == 2
                assert video["video-before"]["attrs"]["src"].endswith("#t=0,2")
                assert float(video["video-later"]["attrs"]["at"]) == 5
                assert audio["audio-before"]["attrs"]["src"].endswith("#t=0,2")
                audio_right_id = next(cid for cid in audio if cid not in {"audio-before", "audio-later"})
                assert audio[audio_right_id]["attrs"]["src"].endswith("#t=2,3")
                assert float(audio[audio_right_id]["attrs"]["at"]) == 3
                assert float(audio["audio-later"]["attrs"]["at"]) == 4

                # The synced insert is one FilmDocument history transaction:
                # one undo must restore both video/audio tracks and the exact
                # source bytes, not only move the visible clips back.
                page.evaluate("() => window.filmStudioUndo()")
                page.wait_for_function("""async pid => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(pid), {cache:'no-store'});
                  if (!response.ok) return false;
                  const doc = await response.json();
                  const state = window.filmStudioStore?.getSnapshot();
                  if (!state || state.busy || state.pendingTransaction !== null ||
                      doc.rev !== state.revision) return false;
                  const clips = (doc.tracks || []).flatMap(track => track.clips || []);
                  return !!doc.rev &&
                    !clips.some(clip => clip.id === 'inserted-asset') &&
                    clips.some(clip => clip.id === 'video-before' &&
                      String(clip.attrs.src).endsWith('#t=0,4') && Number(clip.attrs.at) === 0) &&
                    clips.some(clip => clip.id === 'video-later' &&
                      String(clip.attrs.src).endsWith('#t=0,2') && Number(clip.attrs.at) === 4) &&
                    clips.some(clip => clip.id === 'audio-before' &&
                      String(clip.attrs.src).endsWith('#t=0,3') && Number(clip.attrs.at) === 0) &&
                    clips.some(clip => clip.id === 'audio-later' &&
                      String(clip.attrs.src).endsWith('#t=0,4') && Number(clip.attrs.at) === 3);
                }""", arg=project_id, timeout=10000)
                assert film_path.read_text(encoding="utf-8") == initial_text
                restored = fd.read_project_film(project_id)
                assert restored["rev"] == initial["rev"]
                assert restored["doc"]["tracks"] == initial["doc"]["tracks"]
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                page.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)





def test_lift_and_ripple_delete_have_distinct_real_studio_semantics(tmp_path, monkeypatch):
    """A right-click Lift leaves the following video in place; Ripple closes the gap on all tracks."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    sample = SAMPLE.replace(
        '<video id="video-later" src="../assets/c.mp4#t=0,2" at="4"></video>',
        '<video id="video-later" src="../assets/c.mp4#t=0,2" at="4"></video>\n'
        '<video id="video-after" src="../assets/d.mp4#t=0,2" at="6"></video>',
    )
    project_id = "film-studio-lift-ripple-delete-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(sample, encoding="utf-8")
    initial_bytes = film_path.read_bytes()
    initial = fd.read_project_film(project_id)
    assert initial["exists"]

    def clips_by_id(document):
        return {
            clip["id"]: clip
            for track in document["tracks"]
            for clip in track["clips"]
        }

    def audio_track_signature(document):
        track = next(item for item in document["tracks"] if item["id"] == "audio-bed")
        return [(clip["id"], dict(clip["attrs"])) for clip in track["clips"]]

    initial_clips = clips_by_id(initial["doc"])
    assert float(initial_clips["video-after"]["attrs"]["at"]) == 6
    initial_audio = audio_track_signature(initial["doc"])

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            # Lift/Delete and Ripple Delete ask for confirmation; accept the dialog
            # explicitly so the browser test exercises the actual edit transaction.
            page.add_init_script("window.confirm = () => true;")
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
                for clip_id in ("video-before", "video-later", "video-after"):
                    page.locator(f'#fs-timeline .fs-clip[data-clip="{clip_id}"]').wait_for(
                        state="visible"
                    )

                def wait_for_consistent_state(*, later_present: bool, after_at: float):
                    page.wait_for_function(
                        """async ({projectId, laterPresent, afterAt}) => {
                          const response=await fetch(
                            '/api/editor/film?project_id='+encodeURIComponent(projectId),
                            {cache:'no-store'}
                          );
                          if(!response.ok)return false;
                          const doc=await response.json();
                          const state=window.filmStudioStore?.getSnapshot();
                          if(!state||state.busy||state.pendingTransaction!==null||
                             doc.rev!==state.revision)return false;
                          const clips=(doc.tracks||[]).flatMap(track=>track.clips||[]);
                          const later=clips.find(clip=>clip.id==='video-later');
                          const after=clips.find(clip=>clip.id==='video-after');
                          return (!!later===laterPresent)&&!!after&&
                            Math.abs(Number(after.attrs.at)-afterAt)<0.001;
                        }""",
                        arg={
                            "projectId": project_id,
                            "laterPresent": later_present,
                            "afterAt": after_at,
                        },
                        timeout=10000,
                    )

                # Lift delete: remove only the selected clip; preserve the two-second
                # hole before video-after and leave every audio clip byte-for-byte equivalent.
                page.locator('#fs-timeline .fs-clip[data-clip="video-later"]').click(
                    button="right"
                )
                menu = page.locator('.fs-context-menu[data-react-context-menu="true"]')
                menu.wait_for(state="visible", timeout=5000)
                menu.locator('[data-context-action="delete"]').click()
                wait_for_consistent_state(later_present=False, after_at=6)
                lifted = fd.read_project_film(project_id)
                lifted_clips = clips_by_id(lifted["doc"])
                assert "video-later" not in lifted_clips
                assert float(lifted_clips["video-after"]["attrs"]["at"]) == 6
                assert audio_track_signature(lifted["doc"]) == initial_audio
                assert film_path.read_bytes() != initial_bytes

                # One undo restores the complete source document, including exact HTML bytes.
                page.locator('[data-film-action="undo"]:visible').click()
                wait_for_consistent_state(later_present=True, after_at=6)
                assert film_path.read_bytes() == initial_bytes
                restored = fd.read_project_film(project_id)
                assert restored["rev"] != lifted["rev"]

                # Ripple delete: the same target removes its interval from all affected tracks.
                page.locator('#fs-timeline .fs-clip[data-clip="video-later"]').click(
                    button="right"
                )
                menu = page.locator('.fs-context-menu[data-react-context-menu="true"]')
                menu.wait_for(state="visible", timeout=5000)
                menu_bounds = menu.bounding_box()
                assert menu_bounds, "React context menu is not measurable"
                assert menu_bounds["x"] >= 0 and menu_bounds["y"] >= 0, menu_bounds
                assert menu_bounds["x"] + menu_bounds["width"] <= 1441, menu_bounds
                assert menu_bounds["y"] + menu_bounds["height"] <= 1001, menu_bounds
                menu.locator('[data-context-action="ripple"]').click()
                wait_for_consistent_state(later_present=False, after_at=4)

                ripple = fd.read_project_film(project_id)
                ripple_clips = clips_by_id(ripple["doc"])
                assert "video-later" not in ripple_clips
                assert float(ripple_clips["video-after"]["attrs"]["at"]) == 4
                assert ripple_clips["audio-later"]["attrs"]["src"].endswith("#t=0,1")
                assert float(ripple_clips["audio-later"]["attrs"]["at"]) == 3
                audio_track = next(
                    item for item in ripple["doc"]["tracks"] if item["id"] == "audio-bed"
                )
                right_audio = [
                    clip for clip in audio_track["clips"]
                    if str(clip["attrs"].get("src", "")).endswith("#t=3,4")
                ]
                assert len(right_audio) == 1, audio_track["clips"]
                assert float(right_audio[0]["attrs"]["at"]) == 4
                assert float(ripple_clips["video-before"]["attrs"]["at"]) == 0
                assert ripple["rev"] != restored["rev"]
                assert film_path.read_bytes() != initial_bytes

                # Undo must also restore all tracks together, not just the video selection.
                page.locator('[data-film-action="undo"]:visible').click()
                wait_for_consistent_state(later_present=True, after_at=6)
                assert film_path.read_bytes() == initial_bytes
                assert not page_errors, page_errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_overwrite_mode_replaces_only_the_target_track_in_real_studio(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    asset_insert_started = threading.Event()
    release_asset_insert = threading.Event()

    def fake_library_asset_clip(project_id, asset_id, at, still_duration):
        assert asset_id == "fixture-asset"
        asset_insert_started.set()
        if not release_asset_insert.wait(timeout=10):
            raise RuntimeError("test did not release the pending asset insert")
        return {
            "id": "overwrite-asset", "tag": "video",
            "attrs": {"id": "overwrite-asset", "src": "../assets/new.mp4#t=0,1",
                      "at": str(at), "volume": "1"},
        }

    monkeypatch.setattr(fd, "library_asset_clip", fake_library_asset_clip)
    project_id = "film-studio-overwrite-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    initial_text = film_path.read_text(encoding="utf-8")
    initial = fd.read_project_film(project_id)
    assert initial["exists"]
    audio_before = next(track for track in initial["doc"]["tracks"] if track["id"] == "audio-bed")

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.route("**/api/assets_library*", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps([{"asset_id": "fixture-asset", "type": "video",
                                  "description": "覆盖测试素材", "duration": 1}]),
            ))
            try:
                response = page.goto(f"http://127.0.0.1:{server.server_port}/",
                                     wait_until="domcontentloaded")
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
                }""", project_id)
                page.evaluate("window.filmStudioToggle()")
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="video-before"]').wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="video-before"]').click()
                page.evaluate("window.filmStudioSeek(2)")
                page.wait_for_function("""() => {
                  const slider = document.querySelector("#fs-playhead");
                  const label = document.querySelector("#fs-playhead-value");
                  const snapshot = window.filmStudioStore?.getSnapshot();
                  return !!slider && !!label && Math.abs(Number(slider.value) - 2) < 0.02
                    && label.textContent === "00:02.000" && snapshot?.playhead === 2
                    && Number(slider.max) >= 2;
                }""", timeout=2000)
                scrub_state = page.evaluate("""() => ({
                  value: document.querySelector("#fs-playhead")?.value,
                  max: document.querySelector("#fs-playhead")?.max,
                  label: document.querySelector("#fs-playhead-value")?.textContent,
                  playhead: window.filmStudioStore.getSnapshot().playhead,
                  duration: window.filmStudioStore.getSnapshot().duration,
                })""")
                assert abs(float(scrub_state["value"]) - 2) < 0.02, scrub_state
                assert scrub_state["label"] == "00:02.000", scrub_state
                assert scrub_state["playhead"] == 2, scrub_state
                assert abs(float(scrub_state["max"]) - scrub_state["duration"]) < 1e-9, scrub_state

                for action, state_key, active_text, inactive_text in [
                    ("toggle-snap", "snapEnabled", "吸附：关", "吸附：开"),
                    ("envelope-mode", "envelopeMode", "音量曲线：开", "音量曲线：关"),
                    ("slide-mode", "slideMode", "滑移修剪：开", "滑移修剪：关"),
                    ("slip-mode", "slipMode", "源区间滑移：开", "源区间滑移：关"),
                ]:
                    button = page.locator(f'[data-film-action="{action}"]:visible')
                    button.click()
                    page.wait_for_function(
                        "(key) => window.filmStudioStore.getSnapshot()[key] === "
                        "(key === 'snapEnabled' ? false : true)",
                        arg=state_key, timeout=2000,
                    )
                    on_state = page.evaluate("""(selector) => {
                      const button = Array.from(document.querySelectorAll(selector))
                    .find(candidate => candidate.getClientRects().length > 0);
                      const snapshot = window.filmStudioStore.getSnapshot();
                      return {
                        text: button?.textContent || '',
                        pressed: button?.getAttribute('aria-pressed'),
                        snapEnabled: snapshot.snapEnabled,
                        envelopeMode: snapshot.envelopeMode,
                        slideMode: snapshot.slideMode,
                        slipMode: snapshot.slipMode,
                      };
                    }""", f'[data-film-action="{action}"]')
                    assert (active_text in on_state["text"]), (action, on_state)
                    assert on_state["pressed"] == ("false" if state_key == "snapEnabled" else "true"), (action, on_state)
                    assert on_state[state_key] is (False if state_key == "snapEnabled" else True), (action, on_state)

                    button.click()
                    page.wait_for_function(
                        "(key) => window.filmStudioStore.getSnapshot()[key] === "
                        "(key === 'snapEnabled' ? true : false)",
                        arg=state_key, timeout=2000,
                    )
                    off_state = page.evaluate("""(selector) => {
                      const button = Array.from(document.querySelectorAll(selector))
                    .find(candidate => candidate.getClientRects().length > 0);
                      return {
                        text: button?.textContent || '',
                        pressed: button?.getAttribute('aria-pressed'),
                        snapshot: window.filmStudioStore.getSnapshot(),
                      };
                    }""", f'[data-film-action="{action}"]')
                    assert inactive_text in off_state["text"], (action, off_state)
                    assert off_state["pressed"] == ("true" if state_key == "snapEnabled" else "false"), (action, off_state)
                    assert off_state["snapshot"][state_key] is (True if state_key == "snapEnabled" else False), (action, off_state)

                page.locator('[data-film-action="overwrite-mode"]:visible').click()
                assert page.locator('[data-film-action="overwrite-mode"]:visible').get_attribute("aria-pressed") == "true"
                assert page.locator('[data-film-action="insert-mode"]:visible').get_attribute("aria-pressed") == "false"
                page.evaluate("window.filmStudioLoadLibrary()")
                page.locator('[data-add-asset="fixture-asset"]').wait_for(state="visible")
                dispatch_audit = page.evaluate("""() => {
                  const store = window.filmStudioStore;
                  if (!store || typeof store.dispatchTransaction !== "function") throw new Error("FilmStore dispatcher missing");
                  const original = store.dispatchTransaction.bind(store);
                  window.__filmDispatchAudit = [];
                  store.dispatchTransaction = (context, submitter) => {
                    window.__filmDispatchAudit.push({
                      kind: context.kind, baseRevision: context.baseRevision,
                      operationCount: context.operationCount
                    });
                    return original(context, submitter);
                  };
                  return true;
                }""")
                assert dispatch_audit is True
                page.locator('[data-add-asset="fixture-asset"]').click()
                assert asset_insert_started.wait(timeout=5), "asset insert HTTP handler was not reached"
                try:
                    pending = page.evaluate("""() => {
                      const snapshot = window.filmStudioStore?.getSnapshot();
                      const node = document.querySelector("#fs-react-film-store-state");
                      return {
                        busy: snapshot?.busy,
                        revision: snapshot?.revision,
                        transaction: snapshot?.pendingTransaction && {
                          kind: snapshot.pendingTransaction.kind,
                          baseRevision: snapshot.pendingTransaction.baseRevision,
                          operationCount: snapshot.pendingTransaction.operationCount,
                          operation: snapshot.pendingTransaction.operations[0],
                        },
                        domKind: node?.dataset.pendingTransactionKind,
                        domBase: node?.dataset.pendingTransactionBase,
                        domCount: node?.dataset.pendingTransactionOperationCount,
                      };
                    }""")
                    assert pending["busy"] is True, pending
                    assert pending["revision"] == initial["rev"], pending
                    assert pending["transaction"]["kind"] == "asset-overwrite", pending
                    assert pending["transaction"]["baseRevision"] == initial["rev"], pending
                    assert pending["transaction"]["operationCount"] == 1, pending
                    assert pending["transaction"]["operation"]["placement_mode"] == "overwrite", pending
                    assert pending["domKind"] == "asset-overwrite", pending
                    assert pending["domBase"] == initial["rev"], pending
                    assert pending["domCount"] == "1", pending
                    dispatch_audit = page.evaluate("window.__filmDispatchAudit")
                    assert dispatch_audit == [{
                      "kind": "asset-overwrite", "baseRevision": initial["rev"],
                      "operationCount": 1
                    }], dispatch_audit
                    concurrent_attempt = page.evaluate("""async () => {
                      const store = window.filmStudioStore;
                      let submitterRan = false;
                      try {
                        await store.dispatchTransaction({
                          kind: "concurrent-probe",
                          baseRevision: store.getSnapshot().revision,
                          operations: [],
                          operationCount: 0,
                        }, async () => {
                          submitterRan = true;
                          return true;
                        });
                        return {error: "", submitterRan, busy: store.getSnapshot().busy};
                      } catch (error) {
                        const snapshot = store.getSnapshot();
                        return {
                          error: String(error?.message || error),
                          submitterRan,
                          busy: snapshot.busy,
                          pendingKind: snapshot.pendingTransaction?.kind || "",
                        };
                      }
                    }""")
                    assert concurrent_attempt["error"] == "FILM_TRANSACTION_BUSY", concurrent_attempt
                    assert concurrent_attempt["submitterRan"] is False, concurrent_attempt
                    assert concurrent_attempt["busy"] is True, concurrent_attempt
                    assert concurrent_attempt["pendingKind"] == "asset-overwrite", concurrent_attempt
                finally:
                    release_asset_insert.set()

                page.wait_for_function("""async (pid) => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(pid));
                  if (!response.ok) return false;
                  const document = await response.json();
                  const clips = (document.tracks || []).flatMap(track => track.clips || []);
                  const left = clips.find(clip => clip.id === 'video-before');
                  const inserted = clips.find(clip => clip.id === 'overwrite-asset');
                  const later = clips.find(clip => clip.id === 'video-later');
                  const right = clips.find(clip => clip.id !== 'video-before' && clip.id !== 'video-later'
                    && clip.id !== 'overwrite-asset' && clip.attrs.src.endsWith('#t=3,4'));
                  return !!left && !!inserted && !!later && !!right
                    && String(left.attrs.src).endsWith('#t=0,2')
                    && Math.abs(Number(left.attrs.at)) < 0.001
                    && String(inserted.attrs.src).endsWith('#t=0,1')
                    && Math.abs(Number(inserted.attrs.at) - 2) < 0.001
                    && Math.abs(Number(right.attrs.at) - 3) < 0.001
                    && Math.abs(Number(later.attrs.at) - 4) < 0.001;
                }""", arg=project_id, timeout=10000)

                final = fd.read_project_film(project_id)
                tracks = {track["id"]: track for track in final["doc"]["tracks"]}
                video = {clip["id"]: clip for clip in tracks["video-main"]["clips"]}
                audio = {clip["id"]: clip for clip in tracks["audio-bed"]["clips"]}
                assert video["video-before"]["attrs"]["src"].endswith("#t=0,2")
                assert float(video["video-before"]["attrs"]["at"]) == 0
                assert video["overwrite-asset"]["attrs"]["src"].endswith("#t=0,1")
                assert float(video["overwrite-asset"]["attrs"]["at"]) == 2
                right_clip = next(clip for clip in video.values()
                                  if clip["id"] not in {"video-before", "video-later", "overwrite-asset"})
                assert right_clip["attrs"]["src"].endswith("#t=3,4")
                assert float(right_clip["attrs"]["at"]) == 3
                assert float(video["video-later"]["attrs"]["at"]) == 4
                assert tracks["audio-bed"] == audio_before
                assert float(audio["audio-before"]["attrs"]["at"]) == 0
                assert float(audio["audio-later"]["attrs"]["at"]) == 3

                page.wait_for_function(
                    """expected => {
                      const state = window.filmStudioStore?.getSnapshot();
                      return !!state && state.busy === false && state.pendingTransaction === null &&
                        state.revision === expected;
                    }""",
                    arg=final["rev"],
                    timeout=10000,
                )
                stale_transaction = page.evaluate("""async () => {
                  const store = window.filmStudioStore;
                  let submitterRan = false;
                  try {
                    await store.dispatchTransaction({
                      kind: "stale-revision-probe",
                      baseRevision: "definitely-stale-revision",
                      operations: [],
                      operationCount: 0,
                    }, async () => {
                      submitterRan = true;
                      return true;
                    });
                    return {error: "", submitterRan, busy: store.getSnapshot().busy};
                  } catch (error) {
                    const snapshot = store.getSnapshot();
                    return {
                      error: String(error?.code || error?.message || error),
                      submitterRan,
                      busy: snapshot.busy,
                      pending: snapshot.pendingTransaction,
                      revision: snapshot.revision,
                    };
                  }
                }""")
                assert stale_transaction["error"] == "FILM_REVISION_CONFLICT", stale_transaction
                assert stale_transaction["submitterRan"] is False, stale_transaction
                assert stale_transaction["busy"] is False, stale_transaction
                assert stale_transaction["pending"] is None, stale_transaction
                assert stale_transaction["revision"] == final["rev"], stale_transaction

                failed_transaction = page.evaluate("""async () => {
                  const store = window.filmStudioStore;
                  const baseRevision = store.getSnapshot().revision;
                  try {
                    await store.dispatchTransaction({
                      kind: "failure-cleanup-probe",
                      baseRevision,
                      operations: [],
                      operationCount: 0,
                    }, async () => {
                      throw new Error("EXPECTED_SUBMITTER_FAILURE");
                    });
                    return {error: "", busy: store.getSnapshot().busy};
                  } catch (error) {
                    const snapshot = store.getSnapshot();
                    return {
                      error: String(error?.message || error),
                      busy: snapshot.busy,
                      pending: snapshot.pendingTransaction,
                      revision: snapshot.revision,
                    };
                  }
                }""")
                assert failed_transaction["error"] == "EXPECTED_SUBMITTER_FAILURE", failed_transaction
                assert failed_transaction["busy"] is False, failed_transaction
                assert failed_transaction["pending"] is None, failed_transaction
                assert failed_transaction["revision"] == final["rev"], failed_transaction

                # A committed overwrite is still one undo unit. The prior
                # stale/busy/failure probes must not have polluted its history.
                page.evaluate("() => window.filmStudioUndo()")
                page.wait_for_function("""async pid => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(pid), {cache:'no-store'});
                  if (!response.ok) return false;
                  const doc = await response.json();
                  const state = window.filmStudioStore?.getSnapshot();
                  const clips = (doc.tracks || []).flatMap(track => track.clips || []);
                  return !!doc.rev && doc.rev === state?.revision &&
                    !clips.some(clip => clip.id === 'overwrite-asset') &&
                    clips.some(clip => clip.id === 'video-before' &&
                      String(clip.attrs.src).endsWith('#t=0,4') && Number(clip.attrs.at) === 0) &&
                    clips.some(clip => clip.id === 'video-later' &&
                      String(clip.attrs.src).endsWith('#t=0,2') && Number(clip.attrs.at) === 4) &&
                    clips.some(clip => clip.id === 'audio-before' &&
                      String(clip.attrs.src).endsWith('#t=0,3') && Number(clip.attrs.at) === 0) &&
                    clips.some(clip => clip.id === 'audio-later' &&
                      String(clip.attrs.src).endsWith('#t=0,4') && Number(clip.attrs.at) === 3);
                }""", arg=project_id, timeout=10000)
                assert film_path.read_text(encoding="utf-8") == initial_text
                restored = fd.read_project_film(project_id)
                assert restored["rev"] == initial["rev"]
                assert restored["doc"]["tracks"] == initial["doc"]["tracks"]
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                page.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


LIFT_RIPPLE_SAMPLE = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"><style>body{margin:0}</style></head>
<body>
<section id="video-main" data-label="主画面">
<video id="video-before" src="../assets/a.mp4#t=0,2" at="0"></video>
<video id="video-target" src="../assets/b.mp4#t=0,1" at="2"></video>
<video id="video-after" src="../assets/c.mp4#t=0,2" at="3"></video>
</section>
<section id="audio-bed" data-label="BGM">
<audio id="audio-long" src="../assets/voice.wav#t=0,6" at="0"></audio>
</section>
</body></html>'''


def test_lift_delete_keeps_gap_but_ripple_delete_closes_all_tracks_and_undo_restores(
    tmp_path, monkeypatch
):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-lift-ripple-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(LIFT_RIPPLE_SAMPLE, encoding="utf-8")
    initial_text = film_path.read_text(encoding="utf-8")
    assert fd.read_project_film(project_id)["exists"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors: list[str] = []
            edit_requests: list[dict] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            def capture_edit(request):
                if request.method == "POST" and request.url.endswith("/api/editor/film/edit"):
                    try:
                        edit_requests.append(request.post_data_json)
                    except Exception:
                        edit_requests.append({"raw": request.post_data})

            page.on("request", capture_edit)
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
                page.evaluate("""(pid) => {
                  const select = document.querySelector('#editor-project');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                }""", project_id)
                page.evaluate("window.filmStudioToggle()")
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                target = page.locator('#fs-timeline .fs-clip[data-clip="video-target"]')
                target.wait_for(state="visible", timeout=5000)
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return !!state && state.busy === false && state.pendingTransaction === null
                    && state.broken === false && !state.pendingExternalRevision;
                }""", timeout=10000)
                assert film_path.read_text(encoding="utf-8") == initial_text, (
                    "Opening Studio changed film.html before the first edit"
                )
                page.evaluate("window.confirm = () => true")

                # Lift: remove only the selected clip and retain the following clip's original time.
                box = target.bounding_box()
                assert box, "target clip bounds missing"
                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, button="right")
                menu = page.locator('.fs-context-menu[data-react-context-menu="true"]')
                menu.wait_for(state="visible", timeout=5000)
                delete_item = menu.locator('[data-context-action="delete"]')
                ripple_item = menu.locator('[data-context-action="ripple"]')
                assert delete_item.inner_text() == "删除并保留空隙"
                assert ripple_item.inner_text() == "波纹删除（收拢时间线）"
                assert "后续内容时间不变" in (delete_item.get_attribute("title") or "")
                assert "所有受影响轨道" in (ripple_item.get_attribute("title") or "")
                remove_count = len(edit_requests)
                delete_item.click()
                page.wait_for_function("""async (pid) => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(pid));
                  if (!response.ok) return false;
                  const doc = await response.json();
                  const state = window.filmStudioStore?.getSnapshot();
                  if (!state || state.busy || state.pendingTransaction !== null || doc.rev !== state.revision) return false;
                  const clips = (doc.tracks || []).flatMap(track => track.clips || []);
                  const target = clips.find(clip => clip.id === 'video-target');
                  const later = clips.find(clip => clip.id === 'video-after');
                  const audio = clips.find(clip => clip.id === 'audio-long');
                  return !target && !!later && Number(later.attrs.at) === 3
                    && !!audio && String(audio.attrs.src).endsWith('#t=0,6')
                    && Number(audio.attrs.at) === 0;
                }""", arg=project_id, timeout=10000)
                assert len(edit_requests) == remove_count + 1, edit_requests[remove_count:]
                remove_ops = edit_requests[-1].get("ops") or []
                assert len(remove_ops) == 1 and remove_ops[0].get("op") == "remove", remove_ops

                # Undo lift to compare ripple behavior against exactly the same source document.
                # Wait for the Store's external-revision gate, clear transient selection,
                # and assert that the real Undo endpoint accepted the command.
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return !!state && !state.busy && state.pendingTransaction === null
                    && !state.pendingExternalRevision;
                }""", timeout=10000)
                page.evaluate("""() => {
                  const store = window.filmStudioStore;
                  store.setField("selectedId", "");
                  store.setField("selectedIds", []);
                  store.setField("canvasElementSelection", null);
                }""")
                with page.expect_response(
                    lambda response: response.request.method == "POST"
                    and response.url.endswith("/api/editor/film/undo"),
                    timeout=10000,
                ) as undo_response:
                    page.evaluate("() => window.filmStudioUndo()")
                assert undo_response.value.status == 200
                undo_payload = undo_response.value.json()
                assert undo_payload.get("action") == "undo", undo_payload
                page.wait_for_function("""async (pid) => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(pid), {cache:'no-store'});
                  if (!response.ok) return false;
                  const doc = await response.json();
                  const state = window.filmStudioStore?.getSnapshot();
                  return !!doc.rev && !!state && !state.busy && state.pendingTransaction === null
                    && !state.pendingExternalRevision && doc.rev === state.revision
                    && (doc.tracks || []).some(track =>
                      (track.clips || []).some(clip => clip.id === 'video-target'));
                }""", arg=project_id, timeout=10000)
                assert film_path.read_text(encoding="utf-8") == initial_text

                target = page.locator('#fs-timeline .fs-clip[data-clip="video-target"]')
                box = target.bounding_box()
                assert box, "restored target clip bounds missing"
                before_ripple_text = film_path.read_text(encoding="utf-8")
                ripple_count = len(edit_requests)
                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, button="right")
                menu.wait_for(state="visible", timeout=5000)
                menu_bounds = menu.bounding_box()
                assert menu_bounds, "React context menu is not measurable"
                assert menu_bounds["x"] >= 0 and menu_bounds["y"] >= 0, menu_bounds
                assert menu_bounds["x"] + menu_bounds["width"] <= 1441, menu_bounds
                assert menu_bounds["y"] + menu_bounds["height"] <= 1001, menu_bounds
                menu.locator('[data-context-action="ripple"]').click()

                page.wait_for_function("""async (pid) => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(pid));
                  if (!response.ok) return false;
                  const doc = await response.json();
                  const state = window.filmStudioStore?.getSnapshot();
                  if (!state || state.busy || state.pendingTransaction !== null || doc.rev !== state.revision) return false;
                  const clips = (doc.tracks || []).flatMap(track => track.clips || []);
                  const target = clips.find(clip => clip.id === 'video-target');
                  const before = clips.find(clip => clip.id === 'video-before');
                  const after = clips.find(clip => clip.id === 'video-after');
                  const audioLeft = clips.find(clip => clip.id === 'audio-long');
                  const audioRight = clips.find(clip => clip.id.startsWith('audio-long-ripple-'));
                  return !target && !!before && Number(before.attrs.at) === 0
                    && !!after && Math.abs(Number(after.attrs.at) - 2) < 0.001
                    && !!audioLeft && String(audioLeft.attrs.src).endsWith('#t=0,2')
                    && Number(audioLeft.attrs.at) === 0
                    && !!audioRight && String(audioRight.attrs.src).endsWith('#t=3,6')
                    && Math.abs(Number(audioRight.attrs.at) - 2) < 0.001;
                }""", arg=project_id, timeout=10000)
                assert len(edit_requests) == ripple_count + 1, edit_requests[ripple_count:]
                ripple_ops = edit_requests[-1].get("ops") or []
                assert len(ripple_ops) == 1, ripple_ops
                assert ripple_ops[0].get("op") == "ripple_delete", ripple_ops
                assert ripple_ops[0].get("clip") == "video-target", ripple_ops
                assert ripple_ops[0].get("scope") == "all", ripple_ops

                ripple_doc = fd.read_project_film(project_id)
                tracks = {track["id"]: track for track in ripple_doc["doc"]["tracks"]}
                video = {clip["id"]: clip for clip in tracks["video-main"]["clips"]}
                audio_by_id = {clip["id"]: clip for clip in tracks["audio-bed"]["clips"]}
                right_id = next(clip_id for clip_id in audio_by_id if clip_id.startswith("audio-long-ripple-"))
                assert "video-target" not in video
                assert float(video["video-after"]["attrs"]["at"]) == 2
                assert audio_by_id["audio-long"]["attrs"]["src"].endswith("#t=0,2")
                assert audio_by_id[right_id]["attrs"]["src"].endswith("#t=3,6")
                assert float(audio_by_id[right_id]["attrs"]["at"]) == 2

                # Undo restores the exact pre-ripple bytes through the same server history API.
                page.wait_for_function("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return !!state && !state.busy && state.pendingTransaction === null
                    && !state.pendingExternalRevision;
                }""", timeout=10000)
                page.evaluate("""() => {
                  const store = window.filmStudioStore;
                  store.setField("selectedId", "");
                  store.setField("selectedIds", []);
                  store.setField("canvasElementSelection", null);
                }""")
                with page.expect_response(
                    lambda response: response.request.method == "POST"
                    and response.url.endswith("/api/editor/film/undo"),
                    timeout=10000,
                ) as undo_response:
                    page.evaluate("() => window.filmStudioUndo()")
                assert undo_response.value.status == 200
                undo_payload = undo_response.value.json()
                assert undo_payload.get("action") == "undo", undo_payload
                page.wait_for_function("""async (pid) => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(pid), {cache:'no-store'});
                  if (!response.ok) return false;
                  const doc = await response.json();
                  const state = window.filmStudioStore?.getSnapshot();
                  const clips = (doc.tracks || []).flatMap(track => track.clips || []);
                  return !!doc.rev && !!state && !state.busy && state.pendingTransaction === null
                    && !state.pendingExternalRevision && doc.rev === state.revision
                    && clips.some(clip => clip.id === 'video-target')
                    && clips.some(clip => clip.id === 'audio-long' && String(clip.attrs.src).endsWith('#t=0,6'));
                }""", arg=project_id, timeout=10000)
                assert film_path.read_text(encoding="utf-8") == before_ripple_text
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                page.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_overwrite_rejects_external_revision_change_while_asset_resolution_is_pending(
    tmp_path, monkeypatch
):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    asset_insert_started = threading.Event()
    release_asset_insert = threading.Event()

    def fake_library_asset_clip(project_id, asset_id, at, still_duration):
        assert asset_id == "fixture-asset"
        asset_insert_started.set()
        if not release_asset_insert.wait(timeout=20):
            raise RuntimeError("test did not release the pending overwrite")
        return {
            "id": "stale-overwrite-asset",
            "tag": "video",
            "attrs": {
                "id": "stale-overwrite-asset",
                "src": "../assets/new.mp4#t=0,1",
                "at": str(at),
                "volume": "1",
            },
        }

    monkeypatch.setattr(fd, "library_asset_clip", fake_library_asset_clip)
    project_id = "film-studio-overwrite-external-conflict"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    initial_text = film_path.read_text(encoding="utf-8")
    initial = fd.read_project_film(project_id)
    assert initial["exists"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors: list[str] = []
            insert_responses: list[dict] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            def capture_insert_response(response):
                if response.url.endswith("/api/editor/film/insert-asset"):
                    try:
                        body = response.json()
                    except Exception:
                        body = {}
                    insert_responses.append({"status": response.status, "body": body})

            page.on("response", capture_insert_response)
            page.route(
                "**/api/assets_library*",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        [{"asset_id": "fixture-asset", "type": "video",
                          "description": "覆盖冲突测试素材", "duration": 1}]
                    ),
                ),
            )
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
                    "() => { const select = document.querySelector('#editor-project'); "
                    "return !!select && select.options.length > 0; }"
                )
                page.evaluate("""(pid) => {
                  const select = document.querySelector('#editor-project');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                }""", project_id)
                page.evaluate("window.filmStudioToggle()")
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="video-before"]').wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="video-before"]').click()
                page.evaluate("window.filmStudioSeek(2)")
                page.locator('[data-film-action="overwrite-mode"]:visible').click()
                page.evaluate("window.filmStudioLoadLibrary()")
                page.locator('[data-add-asset="fixture-asset"]').wait_for(state="visible")
                page.locator('[data-add-asset="fixture-asset"]').click()

                assert asset_insert_started.wait(timeout=5), "asset insert handler was not reached"
                try:
                    pending = page.evaluate("""() => {
                      const state = window.filmStudioStore?.getSnapshot();
                      return {
                        busy: state?.busy,
                        revision: state?.revision,
                        transaction: state?.pendingTransaction && {
                          kind: state.pendingTransaction.kind,
                          baseRevision: state.pendingTransaction.baseRevision,
                          operationCount: state.pendingTransaction.operationCount,
                        },
                      };
                    }""")
                    assert pending["busy"] is True, pending
                    assert pending["revision"] == initial["rev"], pending
                    assert pending["transaction"] == {
                        "kind": "asset-overwrite",
                        "baseRevision": initial["rev"],
                        "operationCount": 1,
                    }, pending

                    external_text = initial_text.replace(
                        'id="video-later" src="../assets/c.mp4#t=0,2" at="4"',
                        'id="video-later" src="../assets/c.mp4#t=0,2" at="5"',
                        1,
                    )
                    assert external_text != initial_text, "external edit fixture did not match"
                    film_path.write_text(external_text, encoding="utf-8")
                    external = fd.read_project_film(project_id)
                    assert external["exists"] is True
                    assert external["rev"] != initial["rev"]

                    # Exercise the actual external-watcher/store path while the
                    # insert transaction is busy. The pending revision must be
                    # deferred rather than replacing the timeline mid-transaction.
                    page.wait_for_function(
                        """expected => {
                          const state = window.filmStudioStore?.getSnapshot();
                          return !!state && state.busy === true &&
                            state.pendingExternalRevision === expected;
                        }""",
                        arg=external["rev"],
                        timeout=10_000,
                    )
                finally:
                    release_asset_insert.set()

                page.wait_for_function(
                    """expected => {
                      const state = window.filmStudioStore?.getSnapshot();
                      return !!state && state.busy === false &&
                        state.pendingTransaction === null &&
                        state.pendingExternalRevision === "" &&
                        state.revision === expected;
                    }""",
                    arg=external["rev"],
                    timeout=15_000,
                )

                assert insert_responses, "overwrite API response was not observed"
                assert insert_responses[-1]["status"] == 409, insert_responses
                assert insert_responses[-1]["body"].get("code") == "REVISION_CONFLICT", insert_responses
                assert insert_responses[-1]["body"].get("ok") is False, insert_responses

                # A stale overwrite must neither remove/split source clips nor
                # write its generated asset over the external editor's revision.
                assert film_path.read_text(encoding="utf-8") == external_text
                persisted = fd.read_project_film(project_id)
                assert persisted["rev"] == external["rev"]
                assert persisted["doc"]["tracks"] == external["doc"]["tracks"]
                clips = [
                    clip
                    for track in persisted["doc"]["tracks"]
                    for clip in track["clips"]
                ]
                assert not any(clip["id"] == "stale-overwrite-asset" for clip in clips)
                video = next(track for track in persisted["doc"]["tracks"] if track["id"] == "video-main")
                later = next(clip for clip in video["clips"] if clip["id"] == "video-later")
                assert float(later["attrs"]["at"]) == 5
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                release_asset_insert.set()
                page.close()
                browser.close()
    finally:
        release_asset_insert.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
