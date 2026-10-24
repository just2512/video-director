"""Real Chromium coverage for Film Studio's revision-bound AI proposal review."""
from __future__ import annotations

import importlib
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from video_director import film_document as fd


SAMPLE = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"><style>body{margin:0}</style></head>
<body>
<section id="video-main" data-label="主画面">
<video id="clip-a" src="../assets/a.mp4#t=2,12" at="0" speed="2" volume="1"></video>
<video id="clip-b" src="../assets/b.mp4#t=0,5" at="5"></video>
</section>
<section id="audio-bed" data-label="BGM">
<audio id="audio-1" src="../assets/music.wav#t=0,20" at="0" volume="0.2"></audio>
</section>
</body></html>'''


def test_ai_proposal_diff_is_readable_and_stale_revision_blocks_preview_apply(tmp_path, monkeypatch):
    """Proposal review shows concrete IDs/diff; later edits invalidate, but do not write the proposal."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-ai-proposal-stale-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    before = fd.read_project_film(project_id)
    assert before["exists"]

    operations = [{"op": "move", "clip": "clip-b", "track_id": "video-main", "at": 6.0}]
    candidate_doc, normalized = fd.apply_ops(before["doc"], operations)
    candidate_text = fd.serialize_film(candidate_doc)
    candidate_state = {
        "exists": True,
        "project_id": project_id,
        "text": candidate_text,
        "rev": fd.revision_of(candidate_text),
        "doc": candidate_doc,
    }
    candidate = fd.film_payload(project_id, candidate_state)
    proposal = {
        "id": "browser-stale-proposal",
        "project_id": project_id,
        "base_revision": before["rev"],
        "operations": normalized,
        "summary": "把第二个片段后移一秒",
        "candidate": candidate,
        "affected_clip_ids": ["clip-b"],
    }

    command_requests: list[dict] = []
    apply_requests: list[dict] = []

    def handle_command(route):
        command_requests.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "ok": True,
                "proposal": proposal,
                "message": "已生成并验证候选修改；尚未写入正式影片文档。",
            }, ensure_ascii=False),
        )

    def handle_apply(route):
        apply_requests.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=409, content_type="application/json", body=json.dumps({
            "ok": False,
            "code": "REVISION_CONFLICT",
            "error": "proposal must not be submitted once stale",
        }, ensure_ascii=False))

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
            page.route("**/api/editor/film/command", handle_command)
            page.route("**/api/editor/film/apply-proposal", handle_apply)
            try:
                page.set_default_navigation_timeout(10_000)
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="domcontentloaded",
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.filmStudioAsk === 'function'")
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
                page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]').wait_for(state="visible")

                page.evaluate("window.filmStudioSetSideMode('ai')")
                page.locator("#fs-chat-input").wait_for(state="visible")
                page.locator("#fs-chat-input").fill("把第二个片段后移一秒")
                page.locator('[data-film-action="ask"]:visible').click()
                page.locator('#fs-proposal-panel[data-react-ai-proposal="true"]').wait_for(state="visible", timeout=10_000)
                assert len(command_requests) == 1, command_requests
                assert command_requests[0]["base"] == before["rev"], command_requests
                assert command_requests[0]["project_id"] == project_id, command_requests

                details = page.locator('[data-react-proposal-details="true"]')
                details.locator("summary").click()
                rendered_diff = details.inner_text()
                assert "clip-b" in rendered_diff, rendered_diff
                assert "修改前：" in rendered_diff, rendered_diff
                assert "修改后：" in rendered_diff, rendered_diff
                assert "00:05" in rendered_diff and "00:06" in rendered_diff, rendered_diff
                assert fd.read_project_film(project_id)["rev"] == before["rev"], "proposal generation must not write FilmDocument"
                assert apply_requests == [], apply_requests

                # Make a legitimate edit through the production endpoint while the proposal is open.
                # This emits a revision_changed event and exercises the same stale guard as an external editor.
                edit_response = page.evaluate("""async ({projectId,base}) => {
                  const response = await fetch('/api/editor/film/edit', {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({
                      project_id:projectId,
                      base,
                      ops:[{op:'set',clip:'clip-a',field:'volume',value:'0.75'}]
                    })
                  });
                  return {status:response.status,body:await response.json()};
                }""", {"projectId": project_id, "base": before["rev"]})
                assert edit_response["status"] == 200, edit_response
                changed_revision = edit_response["body"]["rev"]
                assert changed_revision and changed_revision != before["rev"], edit_response

                stale_warning = page.locator("#fs-proposal-stale-warning")
                stale_warning.wait_for(state="visible", timeout=10_000)
                warning_text = stale_warning.inner_text()
                assert before["rev"] in warning_text, warning_text
                assert changed_revision in warning_text, warning_text
                assert page.locator('[data-film-action="preview-proposal"]:visible').is_disabled()
                assert page.locator('[data-film-action="apply-proposal"]:visible').is_disabled()
                assert page.locator('[data-film-action="reject-proposal"]:visible').is_enabled()
                assert apply_requests == [], apply_requests

                # Rejected/stale proposals remain read-only; only the manual edit changes revision.
                final = fd.read_project_film(project_id)
                assert final["rev"] == changed_revision, final
                final_clip = next(
                    clip for track in final["doc"]["tracks"]
                    for clip in track["clips"] if clip["id"] == "clip-b"
                )
                assert abs(float(final_clip["attrs"]["at"]) - 5.0) < 0.001, final_clip
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
