"""End-to-end generated-asset adoption and FilmDocument reload recovery in Chromium."""
from __future__ import annotations

import hashlib
import importlib
import json
import threading
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from video_director import config, film_document as fd, project_manifest, store


FILM_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=320,height=180">
<meta name="film-fps" content="30">
</head>
<body>
<section id="video-main" data-label="主画面">
  <img id="base-still" src="assets/base.png#t=0,1" at="0">
</section>
<section id="audio-main" data-label="静音验收音轨">
  <audio id="silent-bed" src="assets/silence.wav#t=0,5" at="0" volume="1"></audio>
</section>
</body>
</html>
"""


def test_generated_project_asset_is_manually_adopted_and_survives_full_page_reload(
    tmp_path, monkeypatch
):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "web-film-generated-asset-adoption-e2e"
    project_root = config.project_dir(project_id)
    config.init_project_dirs(project_root)
    store.create_project(project_id, "生成素材手动采用回归", {"name": "生成素材手动采用回归"})

    Image.new("RGB", (32, 18), (120, 90, 60)).save(
        project_root / "assets" / "base.png"
    )
    silent_audio_path = project_root / "assets" / "silence.wav"
    with wave.open(str(silent_audio_path), "wb") as silent_audio:
        silent_audio.setnchannels(1)
        silent_audio.setsampwidth(2)
        silent_audio.setframerate(44100)
        silent_audio.writeframes(b"\x00\x00" * (44100 * 5))
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(FILM_HTML, encoding="utf-8")
    initial = fd.read_project_film(project_id)
    assert initial["exists"] is True and initial["rev"]

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
                page.wait_for_function(
                    "typeof window.workspaceOpen === 'function' && "
                    "typeof window.filmStudioLoadLibrary === 'function'"
                )

                opened = page.evaluate("(pid) => workspaceOpen(pid)", project_id)
                assert opened and opened.get("ok") and opened.get("mode") == "film", opened
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="base-still"]').wait_for(
                    state="visible"
                )

                # Use the real generation HTTP route with the deterministic Graphics
                # Runtime provider. This does not configure or call an external model.
                generated = page.evaluate(
                    """async (projectId) => {
                      const response = await fetch('/api/editor/generate', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                          project_id: projectId,
                          type: 'graphic',
                          prompt: '用于真实素材入库和手动采用验收的视觉卡',
                          width: 160,
                          height: 90
                        })
                      });
                      return {httpStatus: response.status, body: await response.json()};
                    }""",
                    project_id,
                )
                assert generated["httpStatus"] == 200, generated
                assert generated["body"].get("ok") is True, generated
                results = generated["body"].get("results") or []
                assert results, generated
                generated_asset = results[0]
                asset_id = generated_asset["asset_id"]
                assert generated_asset["type"] == "image"
                assert generated_asset["provider_id"] == "graphics-runtime"

                manifest = project_manifest.read_project_manifest(project_id)
                entry = next(item for item in manifest if item["id"] == asset_id)
                generated_file = Path(entry["file"])
                assert generated_file.is_file()
                assert entry["source"] == "generated"

                # Generation registers an asset; it must not silently edit FilmDocument.
                still_before_adoption = fd.read_project_film(project_id)
                assert still_before_adoption["rev"] == initial["rev"]
                assert not any(
                    clip["attrs"].get("data-asset-id") == asset_id
                    for track in still_before_adoption["doc"]["tracks"]
                    for clip in track["clips"]
                )

                page.evaluate("() => window.filmStudioLoadLibrary()")
                add_button = page.locator(f'[data-add-asset="{asset_id}"]')
                add_button.wait_for(state="visible")
                add_button.click()

                page.wait_for_function(
                    """async ({projectId, assetId}) => {
                      const response = await fetch(
                        '/api/editor/film?project_id=' + encodeURIComponent(projectId),
                        {cache:'no-store'}
                      );
                      if (!response.ok) return false;
                      const document = await response.json();
                      const state = window.filmStudioStore?.getSnapshot();
                      if (!state || state.busy || state.pendingTransaction !== null ||
                          state.revision !== document.rev) return false;
                      const clips = (document.tracks || []).flatMap(track => track.clips || []);
                      return clips.some(clip =>
                        clip.attrs && clip.attrs['data-asset-id'] === assetId
                      );
                    }""",
                    arg={"projectId": project_id, "assetId": asset_id},
                    timeout=15000,
                )

                adopted = fd.read_project_film(project_id)
                assert adopted["rev"] != initial["rev"]
                adopted_clips = [
                    clip
                    for track in adopted["doc"]["tracks"]
                    for clip in track["clips"]
                ]
                asset_clip = next(
                    clip for clip in adopted_clips
                    if clip["attrs"].get("data-asset-id") == asset_id
                )
                assert asset_clip["tag"] == "img"
                assert asset_clip["attrs"]["src"].startswith("assets/generated/")
                assert (project_root / asset_clip["attrs"]["src"].split("#", 1)[0]).is_file()

                # A full document reload must recover the committed FilmDocument
                # revision and manually adopted asset through the normal project entry.
                response = page.reload(wait_until="domcontentloaded")
                assert response and response.status == 200
                page.wait_for_function("typeof window.workspaceOpen === 'function'")
                reopened = page.evaluate("(pid) => workspaceOpen(pid)", project_id)
                assert reopened and reopened.get("ok") and reopened.get("mode") == "film", reopened
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.wait_for_function(
                    """async ({projectId, assetId, revision}) => {
                      const response = await fetch(
                        '/api/editor/film?project_id=' + encodeURIComponent(projectId),
                        {cache:'no-store'}
                      );
                      if (!response.ok) return false;
                      const document = await response.json();
                      const state = window.filmStudioStore?.getSnapshot();
                      const clips = (document.tracks || []).flatMap(track => track.clips || []);
                      return document.rev === revision && state &&
                        state.revision === revision &&
                        clips.some(clip =>
                          clip.attrs && clip.attrs['data-asset-id'] === assetId
                        );
                    }""",
                    arg={
                        "projectId": project_id,
                        "assetId": asset_id,
                        "revision": adopted["rev"],
                    },
                    timeout=15000,
                )
                assert not page_errors, f"browser JavaScript errors: {page_errors}"

            finally:
                page.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    # Carry the manually adopted generated still through the actual
    # Film renderer. A real silent WAV track exercises the report's
    # WARNING semantics without blocking a technically valid MP4.
    from video_director import film_render, prober

    rendered = film_render.render_project_film(
        project_id, expected_revision=adopted["rev"], fps=2
    )
    output = Path(rendered["mp4_path"])
    qc_path = Path(rendered["qc_report_path"])
    assert rendered["ok"] is True
    assert rendered["current"] is True
    assert rendered["rendered_revision"] == adopted["rev"]
    assert rendered["revision_status"] == "CURRENT"
    assert rendered["qc_status"] == "WARNING"
    assert rendered["audio_signal_status"] in {"SILENT", "NEAR_SILENT"}
    assert output.is_file() and output.stat().st_size > 0
    assert qc_path.is_file() and qc_path.stat().st_size > 0

    probe = prober.probe_media(str(output))
    assert probe.has_video_stream is True
    assert probe.has_audio_stream is True
    assert (probe.width, probe.height) == (320, 180)
    assert probe.duration is not None and abs(float(probe.duration) - 5.0) <= 0.25

    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    assert qc["status"] == "WARNING"
    assert qc["source_revision"] == adopted["rev"]
    assert qc["current_revision"] == adopted["rev"]
    assert qc["current_revision_match"] is True
    assert qc["revision_status"] == "CURRENT"
    assert qc["output"]["size_bytes"] == output.stat().st_size
    assert qc["output"]["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert qc["audio"]["stream_present"] is True
    assert qc["audio"]["status"] in {"SILENT", "NEAR_SILENT"}
    assert qc["warnings"]
    audio_check = next(
        check for check in qc["checks"] if check["name"] == "audio_signal"
    )
    assert audio_check["status"] == "WARNING"
    assert all(
        check["status"] in {"PASS", "WARNING"} for check in qc["checks"]
    )
