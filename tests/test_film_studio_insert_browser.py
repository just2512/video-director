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

    monkeypatch.setattr(web_server.fd, "library_asset_clip", fake_library_asset_clip)
    project_id = "film-studio-insert-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    assert fd.read_project_film(project_id)["exists"]

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
                page.locator('[data-film-action="insert-mode"]').click()
                assert page.locator('[data-film-action="insert-mode"]').get_attribute("aria-pressed") == "true"
                page.evaluate("window.filmStudioLoadLibrary()")
                page.locator('[data-add-asset="fixture-asset"]').wait_for(state="visible")
                page.locator('[data-add-asset="fixture-asset"]').click()

                page.wait_for_function("""async (pid) => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(pid));
                  if (!response.ok) return false;
                  const doc = await response.json();
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
                }""", project_id, timeout=10000)

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
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                page.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
