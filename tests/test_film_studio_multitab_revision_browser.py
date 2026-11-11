"""Real two-tab Chromium regression for stale FilmDocument revision writes."""
from __future__ import annotations

import importlib
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
import time

from playwright.sync_api import sync_playwright

from video_director import film_document as fd


FILM = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"></head>
<body>
<section id="video-main" data-label="主画面">
  <video id="clip-a" src="../assets/a.mp4#t=0,5" at="0"></video>
  <video id="clip-b" src="../assets/b.mp4#t=0,5" at="5"></video>
</section>
</body></html>"""


def _open_project_tab(page, base_url: str, project_id: str) -> None:
    response = page.goto(base_url, wait_until="domcontentloaded")
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
          select.dispatchEvent(new Event('change',{bubbles:true}));
        }""",
        project_id,
    )
    page.evaluate("window.filmStudioToggle()")
    page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
    page.wait_for_function(
        "() => typeof window.filmStudioToggleTrackLock === 'function' "
        "&& !!document.querySelector('.fs-react-timeline-surface')",
        timeout=10000,
    )


def test_two_real_browser_tabs_reject_stale_same_project_revision_without_overwrite(
    tmp_path, monkeypatch
):
    """Hold tab B's real edit request until tab A commits a newer revision."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-two-tab-stale-revision"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(FILM, encoding="utf-8")
    initial = fd.read_project_film(project_id)
    assert initial["exists"]
    initial_revision = initial["rev"]
    track_id = initial["doc"]["tracks"][0]["id"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            tab_a = context.new_page()
            tab_b = context.new_page()
            page_errors: list[str] = []
            held_routes = []
            stale_requests: list[dict] = []
            stale_responses: list[dict] = []
            for page in (tab_a, tab_b):
                page.on("pageerror", lambda error: page_errors.append(str(error)))

            base_url = f"http://127.0.0.1:{server.server_port}/"
            try:
                _open_project_tab(tab_a, base_url, project_id)
                _open_project_tab(tab_b, base_url, project_id)
                revisions = [
                    tab_a.evaluate("window.filmStudioStore.getField('film').rev"),
                    tab_b.evaluate("window.filmStudioStore.getField('film').rev"),
                ]
                assert revisions == [initial_revision, initial_revision], revisions

                def hold_tab_b_edit(route):
                    payload = json.loads(route.request.post_data or "{}")
                    stale_requests.append(payload)
                    held_routes.append(route)

                def capture_stale_response(response):
                    if response.url.endswith("/api/editor/film/edit"):
                        try:
                            stale_responses.append({
                                "status": response.status,
                                "body": response.json(),
                            })
                        except Exception as exc:
                            stale_responses.append({
                                "status": response.status,
                                "body_error": str(exc),
                            })

                tab_b.on("response", capture_stale_response)
                tab_b.route("**/api/editor/film/edit", hold_tab_b_edit)

                # Start a genuine UI transaction from tab B, but hold its HTTP
                # request so it retains the revision both tabs originally read.
                tab_b.evaluate(
                    """trackId => {
                      window.__staleEditPromise = window.filmStudioToggleTrackLock(trackId);
                    }""",
                    track_id,
                )
                deadline = time.monotonic() + 8
                while not held_routes and time.monotonic() < deadline:
                    tab_b.wait_for_timeout(50)
                assert len(held_routes) == 1, stale_requests
                stale_payload = stale_requests[0]
                assert stale_payload.get("project_id") == project_id, stale_payload
                assert stale_payload.get("base") == initial_revision, stale_payload
                assert stale_payload.get("ops"), stale_payload

                # Tab A commits first, advancing the authoritative document.
                committed = tab_a.evaluate(
                    "trackId => window.filmStudioToggleTrackLock(trackId)",
                    track_id,
                )
                assert committed is True
                after_a = fd.read_project_film(project_id)
                assert after_a["rev"] != initial_revision
                bytes_after_a = film_path.read_bytes()
                locked_track = next(
                    track for track in after_a["doc"]["tracks"] if track["id"] == track_id
                )
                assert "locked" in locked_track.get("attrs", {}), locked_track

                # Now let tab B's stale request reach the real API/server.
                held_routes[0].continue_()
                stale_result = tab_b.evaluate(
                    "async () => await window.__staleEditPromise"
                )
                assert stale_result is False, stale_result
                assert stale_responses, "tab B's real edit response was not captured"
                response = stale_responses[-1]
                assert response["status"] == 409, response
                assert response["body"].get("code") == "REVISION_CONFLICT", response

                # A rejected stale write must not mutate the newer committed bytes.
                assert film_path.read_bytes() == bytes_after_a
                final = fd.read_project_film(project_id)
                assert final["rev"] == after_a["rev"]
                final_track = next(
                    track for track in final["doc"]["tracks"] if track["id"] == track_id
                )
                assert "locked" in final_track.get("attrs", {}), final_track
                assert page_errors == [], page_errors
            finally:
                for route in held_routes:
                    try:
                        route.abort()
                    except Exception:
                        pass
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
