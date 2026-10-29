"""Real Chromium acceptance for legacy Composition -> visual migration preview -> FilmDocument."""
from __future__ import annotations

import base64
import importlib
import json
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright

from video_director import config, editor, film_document as fd, store
from video_director.models import AssetRef, CompositionIR, Layer, LayerType, ProjectSpec, SceneIR


def test_workspace_open_previews_migration_in_studio_before_creating_film_html(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "unified-studio-migration-preview-browser"
    store.create_project(project_id, "统一入口迁移预览回归", {"name": "统一入口迁移预览回归"})
    project_root = config.project_dir(project_id)
    assets = project_root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    still = assets / "migration-still.png"
    still.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
        "lYkAAAAASUVORK5CYII="
    ))
    composition = CompositionIR(
        project=ProjectSpec(width=160, height=90, fps=10, duration=1.0),
        scenes=[
            SceneIR(
                id="migration-scene", start=0, duration=1.0, text="迁移前候选预览",
                layers=[Layer(type=LayerType.image, asset_id="migration-still", start=0, duration=1.0)],
            )
        ],
        assets=[
            AssetRef(
                id="migration-still", type="image", file=str(still), width=1, height=1,
                source="uploaded", rights_status="CLEAR",
                provenance={"source": "Chromium migration preview acceptance"},
            )
        ],
    )
    editor.save_composition(project_id, composition)
    film_path = project_root / fd.FILM_NAME
    assert not film_path.exists()

    # A second, already-initialized FilmDocument proves that the same selector
    # can change project authority without leaving the fullscreen Studio.
    target_project_id = "unified-studio-switch-target"
    store.create_project(target_project_id, "Studio 项目切换目标", {"name": "Studio 项目切换目标"})
    target_root = config.project_dir(target_project_id)
    target_assets = target_root / "assets"
    target_assets.mkdir(parents=True, exist_ok=True)
    (target_assets / "switch-still.png").write_bytes(still.read_bytes())
    target_film_path = target_root / fd.FILM_NAME
    target_film_path.write_text(
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        '<section id="video-main" data-label="主画面">'
        '<img id="switch-clip" src="../assets/switch-still.png#t=0,1" at="0">'
        '</section></body></html>',
        encoding="utf-8",
    )
    assert fd.read_project_film(target_project_id)["exists"]

    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page_errors: list[str] = []
            director_command_requests: list[dict] = []

            def handle_director_command(route):
                request_body = json.loads(route.request.post_data or "{}")
                current = fd.read_project_film(project_id)
                clips = [clip for track in current["doc"]["tracks"] for clip in track["clips"]]
                assert clips, "the initialized film should expose a stable clip ID"
                target_clip_id = clips[0]["id"]
                candidate_doc, operations = fd.apply_ops(current["doc"], [{
                    "op": "set", "clip": target_clip_id, "field": "title",
                    "value": "导演提案候选标题",
                }])
                candidate_text = fd.serialize_film(candidate_doc)
                candidate_state = {
                    "exists": True,
                    "project_id": project_id,
                    "text": candidate_text,
                    "rev": fd.revision_of(candidate_text),
                    "doc": candidate_doc,
                }
                proposal = {
                    "id": "migration-continuity-director-proposal",
                    "project_id": project_id,
                    "base_revision": current["rev"],
                    "operations": operations,
                    "summary": "初始化后继续使用导演能力",
                    "candidate": fd.film_payload(project_id, candidate_state),
                    "affected_clip_ids": [target_clip_id],
                }
                director_command_requests.append(request_body)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "ok": True,
                        "proposal": proposal,
                        "message": "候选提案已生成，尚未写入正式影片。",
                    }, ensure_ascii=False),
                )

            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.add_init_script("""(() => {
              window.__filmRuntimeLoadedRevs = [];
              window.__legacyCompositionReadCount = 0;
              const nativeFetch = window.fetch.bind(window);
              window.fetch = (input, init) => {
                const url = typeof input === "string" ? input :
                  (input && typeof input.url === "string" ? input.url : String(input));
                if (url.includes("/api/editor/composition?project_id=")) {
                  window.__legacyCompositionReadCount += 1;
                }
                return nativeFetch(input, init);
              };
              window.addEventListener('message', event => {
                if (event.data && event.data.type === 'film-runtime:loaded') {
                  window.__filmRuntimeLoadedRevs.push(String(event.data.rev || ''));
                }
              });
              window.confirm = () => true;
            })();""")
            page.route("**/api/editor/film/command", handle_director_command)
            try:
                response = page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
                assert response and response.status == 200
                page.wait_for_function(
                    "() => typeof window.workspaceOpen === 'function' && typeof window.filmStudioToggle === 'function'"
                )
                opened = page.evaluate("(pid) => workspaceOpen(pid)", project_id)
                assert opened.get("ok") is True, opened
                assert opened.get("mode") == "migration", opened
                assert page.evaluate("window.__legacyCompositionReadCount") == 0, (
                    "default legacy-project open must not load a hidden CompositionIR editor"
                )
                reopened = page.evaluate("(pid) => workspaceOpen(pid)", project_id)
                assert reopened.get("ok") is True, reopened
                assert reopened.get("mode") == "migration", reopened
                assert page.locator("#film-studio-panel.fs-studio-expanded").count() == 1
                assert page.evaluate("window.__legacyCompositionReadCount") == 0

                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator("#fs-initialize-button").wait_for(state="visible")
                page.wait_for_function(
                    "() => (window.__filmRuntimeLoadedRevs || []).some(rev => rev.startsWith('migration-preview:'))",
                    timeout=10000,
                )
                frame = page.locator("#fs-preview-composition")
                assert frame.is_visible()
                assert frame.evaluate("(node) => getComputedStyle(node).display") == "block"
                assert page.locator("#fs-monitor-stage .fs-monitor-placeholder").evaluate(
                    "(node) => getComputedStyle(node).display"
                ) == "none"
                runtime = page.frame_locator("#fs-preview-composition")
                runtime.locator(".fs-runtime-track").first.wait_for(state="visible", timeout=10000)

                # The candidate is rendered from validated in-memory data; it isn't a film.html write.
                assert not film_path.exists()
                assert fd.read_project_film(project_id)["exists"] is False
                assert page.locator("#fs-timeline .fs-migrate-action").is_visible()

                page.locator("#fs-initialize-button").click()
                page.wait_for_function(
                    "() => !!document.querySelector('#fs-timeline .fs-clip[data-clip]')",
                    timeout=10000,
                )
                initialized = fd.read_project_film(project_id)
                assert initialized["exists"] is True
                assert film_path.exists()
                assert page.locator("#film-studio-panel.fs-studio-expanded").is_visible()
                assert page.locator("#fs-timeline .fs-clip[data-clip]").count() >= 1
                # The project switcher is moved into the fullscreen header as the
                # same DOM node, so there must never be a hidden duplicate selector.
                assert page.locator("#fs-project-switch-host #editor-project-switch-row").count() == 1
                assert page.locator("#editor-project-switch-home #editor-project-switch-row").count() == 0
                assert page.locator("#editor-project").count() == 1

                # Moving the shared selector must not weaken draft protection:
                # a changed Inspector value blocks switching and remains intact.
                page.locator("#fs-timeline .fs-clip[data-clip]").first.click()
                title_input = page.locator("#fs-field-title")
                title_input.wait_for(state="visible")
                original_title = title_input.input_value()
                draft_title = original_title + " 未提交草稿"
                title_input.fill(draft_title)
                page.locator("#editor-project").select_option(target_project_id)
                page.wait_for_function(
                    "(pid) => document.querySelector('#editor-project').value === pid",
                    arg=project_id,
                    timeout=10000,
                )
                assert page.evaluate("() => window.filmStudioStore.getSnapshot().projectId") == project_id
                assert "有未提交草稿" in page.locator("#film-studio-alert").inner_text()
                assert title_input.input_value() == draft_title
                title_input.fill(original_title)

                # Save a real Inspector edit after initialization; then continue
                # in the Director tab and export from this same Studio workspace.
                saved_title = original_title + " 已保存的手工剪辑"
                title_input.fill(saved_title)
                page.locator("#fs-apply-clip-inspector").click()
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('已保存 1 项编辑')",
                    timeout=15000,
                )
                manual_state = fd.read_project_film(project_id)
                saved_clips = [clip for track in manual_state["doc"]["tracks"] for clip in track["clips"]]
                assert any(clip.get("attrs", {}).get("title") == saved_title for clip in saved_clips)
                director_base = manual_state["rev"]

                page.locator('[data-film-action="director-mode"]').click()
                page.locator("#fs-director-authority-note").wait_for(state="visible")
                page.locator("#fs-director-prompt-review").click()
                director_prompt = page.locator("#fs-director-film-prompt").input_value()
                assert "导演审查" in director_prompt and "当前 revision" in director_prompt
                page.locator('[data-film-action="director-propose"]').click()
                page.locator('#fs-proposal-panel[data-react-ai-proposal="true"]').wait_for(
                    state="visible", timeout=10000
                )
                assert len(director_command_requests) == 1, director_command_requests
                director_request = director_command_requests[0]
                assert director_request["project_id"] == project_id, director_request
                assert director_request["base"] == director_base, director_request
                assert director_request["message"].find(director_prompt) >= 0, director_request
                assert director_request["stream"] is True, director_request
                assert fd.read_project_film(project_id)["rev"] == director_base, (
                    "director proposal generation must not write film.html"
                )
                page.locator('[data-film-action="reject-proposal"]:visible').click()
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('已拒绝提案')",
                    timeout=10000,
                )

                export_revision = fd.read_project_film(project_id)["rev"]
                page.locator('[data-film-action="render"]').click()
                page.locator("#fs-render-result").wait_for(state="visible", timeout=120000)
                page.wait_for_function(
                    "() => document.querySelector('#fs-render-result-text')?.textContent.includes('QC PASS')",
                    timeout=120000,
                )
                result_text = page.locator("#fs-render-result-text").inner_text()
                assert export_revision in result_text, result_text
                assert page.locator("#fs-render-download").is_visible()
                assert page.locator("#fs-qc-download").is_visible()
                assert fd.read_project_film(project_id)["rev"] == export_revision
                mp4_href = page.locator("#fs-render-download").get_attribute("href")
                assert mp4_href and mp4_href.startswith("/file?path="), mp4_href
                mp4_response = page.request.get(f"http://127.0.0.1:{server.server_port}{mp4_href}")
                assert mp4_response.status == 200
                assert len(mp4_response.body()) > 0
                assert not page_errors, f"browser JavaScript errors: {page_errors}"

                page.locator("#editor-project").select_option(target_project_id)
                page.wait_for_function(
                    "(pid) => window.filmStudioStore?.getSnapshot()?.projectId === pid",
                    arg=target_project_id,
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => !!document.querySelector('#fs-timeline .fs-clip[data-clip=switch-clip]')",
                    timeout=10000,
                )
                assert page.locator("#film-studio-panel.fs-studio-expanded").is_visible()
                assert page.locator("#fs-project-switch-host #editor-project-switch-row").count() == 1
                assert page.locator("#editor-project").count() == 1
                assert fd.read_project_film(target_project_id)["exists"]
                assert not page_errors, f"browser JavaScript errors: {page_errors}"

                page.evaluate("() => window.filmStudioClose()")
                page.wait_for_function(
                    "() => !document.querySelector('#film-studio-panel')?.classList.contains('fs-studio-expanded')",
                    timeout=5000,
                )
                assert page.locator("#editor-project-switch-home #editor-project-switch-row").count() == 1
                assert page.locator("#fs-project-switch-host #editor-project-switch-row").count() == 0
                assert page.locator("#editor-project").count() == 1
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
