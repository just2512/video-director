"""Four-way real-browser evidence for Film Studio project switching."""
from __future__ import annotations

import importlib
from http.server import ThreadingHTTPServer
import threading

from playwright.sync_api import sync_playwright

from video_director import film_document as fd


def test_project_switch_reconciles_request_response_store_and_disk(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_a = "film-studio-switch-four-way-a"
    project_b = "film-studio-switch-four-way-b"
    html_a = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
    <section id="video-main"><video id="project-a-clip" src="../assets/a.mp4#t=0,2" at="0"></video></section>
    </body></html>'''
    html_b = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
    <section id="video-main"><video id="project-b-clip" src="../assets/b.mp4#t=0,3" at="1"></video></section>
    </body></html>'''
    for pid, html in ((project_a, html_a), (project_b, html_b)):
        root = storage_root / pid
        root.mkdir(parents=True)
        (root / fd.FILM_NAME).write_text(html, encoding="utf-8")
    disk_a = fd.read_project_film(project_a)
    disk_b = fd.read_project_film(project_b)
    assert disk_a["exists"] and disk_b["exists"]
    assert disk_a["rev"] != disk_b["rev"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors: list[str] = []
            film_requests: list[str] = []
            film_responses: list[dict] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("request", lambda request: film_requests.append(request.url)
                    if request.method == "GET" and "/api/editor/film?project_id=" in request.url else None)

            def capture_response(response):
                if response.request.method != "GET" or "/api/editor/film?project_id=" not in response.url:
                    return
                try:
                    body = response.json()
                except Exception as exc:
                    body = {"parse_error": str(exc)}
                film_responses.append({"url": response.url, "status": response.status, "body": body})

            page.on("response", capture_response)
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
                    "() => !!document.querySelector('#editor-project')"
                )
                page.evaluate("""pid => {
                  const select = document.querySelector('#editor-project');
                  for (const value of [pid, 'film-studio-switch-four-way-b']) {
                    if (![...select.options].some(option => option.value === value)) {
                      select.add(new Option(value, value));
                    }
                  }
                  select.value = pid;
                  select.dispatchEvent(new Event('change', {bubbles:true}));
                }""", project_a)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="project-a-clip"]').wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="project-a-clip"]').click()
                selected_a = page.evaluate("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state ? {projectId:state.projectId,revision:state.revision,
                    selectedId:state.selectedId,selectedIds:[...(state.selectedIds||[])]} : null;
                }""")
                assert selected_a and selected_a["projectId"] == project_a, selected_a
                assert selected_a["selectedId"] == "project-a-clip", selected_a

                film_requests.clear()
                film_responses.clear()
                page.evaluate("""pid => {
                  const select = document.querySelector('#editor-project');
                  select.value = pid;
                  select.dispatchEvent(new Event('change', {bubbles:true}));
                }""", project_b)
                page.locator('#fs-timeline .fs-clip[data-clip="project-b-clip"]').wait_for(
                    state="visible", timeout=10000
                )
                page.wait_for_function(
                    """pid => {
                      const state = window.filmStudioStore?.getSnapshot();
                      return !!state && state.projectId === pid && state.revision &&
                        !state.busy && state.pendingTransaction === null &&
                        !state.pendingExternalRevision;
                    }""",
                    arg=project_b,
                    timeout=10000,
                )
                page.wait_for_function(
                    """pid => window.document.querySelector('#fs-project-label')?.textContent.includes(pid)""",
                    arg=project_b,
                )

                matching_requests = [url for url in film_requests if f"project_id={project_b}" in url]
                matching_responses = [
                    item for item in film_responses if f"project_id={project_b}" in item["url"]
                ]
                assert matching_requests, {"requests": film_requests}
                assert len(matching_requests) == 1, {"requests": matching_requests}
                assert len(matching_responses) == 1, {"responses": matching_responses}
                response_evidence = matching_responses[0]
                assert response_evidence["status"] == 200, response_evidence
                response_body = response_evidence["body"]
                assert response_body.get("project_id") == project_b, response_body
                assert response_body.get("rev") == disk_b["rev"], {
                    "http": response_body, "disk_revision": disk_b["rev"]
                }

                store = page.evaluate("""() => {
                  const state = window.filmStudioStore?.getSnapshot();
                  return state ? {
                    projectId:state.projectId,revision:state.revision,
                    selectedId:state.selectedId,selectedIds:[...(state.selectedIds||[])],
                    busy:state.busy,pendingTransaction:state.pendingTransaction,
                    pendingExternalRevision:state.pendingExternalRevision
                  } : null;
                }""")
                persisted = fd.read_project_film(project_b)
                assert store and store["projectId"] == project_b, store
                assert store["revision"] == response_body["rev"] == persisted["rev"], {
                    "request": matching_requests, "http": response_body,
                    "store": store, "disk_revision": persisted["rev"]
                }
                assert store["selectedId"] == "" and store["selectedIds"] == [], store
                assert store["busy"] is False and store["pendingTransaction"] is None, store
                assert store["pendingExternalRevision"] == "", store
                assert persisted["doc"]["tracks"] == disk_b["doc"]["tracks"]
                assert page.locator('#fs-timeline .fs-clip[data-clip="project-a-clip"]').count() == 0
                assert not page_errors, page_errors
            finally:
                page.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
