"""Real Chromium acceptance for legacy Composition -> visual migration preview -> FilmDocument."""
from __future__ import annotations

import base64
import importlib
import json
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

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
        camera_tracks=[{"id": "camera-main"}],
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
    initialize_request_started = threading.Event()
    release_initialize_request = threading.Event()
    initialize_delay_consumed = [False]
    initialize_request_count = [0]
    initialize_request_lock = threading.Lock()
    original_do_post = web_server.Handler.do_POST

    def delay_first_film_initialize(handler):
        if handler.path.split("?", 1)[0] == "/api/editor/film/initialize":
            with initialize_request_lock:
                initialize_request_count[0] += 1
                should_delay = not initialize_delay_consumed[0]
                if should_delay:
                    initialize_delay_consumed[0] = True
            if should_delay:
                initialize_request_started.set()
                if not release_initialize_request.wait(timeout=10):
                    handler.send_error(504, "test did not release delayed initialization")
                    return
        original_do_post(handler)

    monkeypatch.setattr(web_server.Handler, "do_POST", delay_first_film_initialize)
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
                migration_report = page.locator("#fs-timeline .fs-migration-report")
                migration_report.wait_for(state="visible")
                assert "迁移提示" in migration_report.inner_text()
                warning_items = migration_report.locator("li")
                assert warning_items.count() >= 1
                assert "camera_tracks" in migration_report.inner_text()
                archive_preview = page.locator("#fs-timeline .fs-migration-archive")
                assert "migration/source-composition.json" in archive_preview.inner_text()
                archive_digest = archive_preview.locator("small").inner_text()
                assert "SHA-256：" in archive_digest

                # Hold project A's initialization response while the user opens
                # project B. The response belongs to A and must never replace B's
                # active Studio state, even if A was initialized on the server.
                page.evaluate("""() => {
                  const state = {settled: false, result: null, error: null};
                  window.__migrationInitializeState = state;
                  Promise.resolve(window.filmStudioInitialize(false)).then(
                    result => { state.settled = true; state.result = result; },
                    error => { state.settled = true; state.error = String(error); }
                  );
                }""")
                assert initialize_request_started.wait(timeout=5), "film initialization request did not reach the server"
                page.wait_for_function(
                    "() => document.querySelector('#fs-initialize-button')?.disabled === true",
                    timeout=3000,
                )
                duplicate_attempt = page.evaluate("() => window.filmStudioInitialize(false)")
                assert duplicate_attempt is False, duplicate_attempt
                with initialize_request_lock:
                    assert initialize_request_count[0] == 1, (
                        "a second initialization call for the same project/load generation "
                        "must be coalesced before reaching the server"
                    )

                opened_target = page.evaluate("(pid) => workspaceOpen(pid)", target_project_id)
                assert opened_target.get("ok") is True, opened_target
                page.wait_for_function(
                    "(pid) => window.filmStudioStore?.getSnapshot()?.projectId === pid",
                    arg=target_project_id,
                    timeout=10000,
                )
                page.wait_for_function(
                    "() => !!document.querySelector('#fs-timeline .fs-clip[data-clip=switch-clip]')",
                    timeout=10000,
                )
                release_initialize_request.set()
                page.wait_for_function(
                    "() => window.__migrationInitializeState?.settled === true",
                    timeout=10000,
                )
                initialize_state = page.evaluate("window.__migrationInitializeState")
                assert initialize_state["error"] is None, initialize_state["error"]
                assert initialize_state["result"] is False, initialize_state
                assert page.evaluate("() => window.filmStudioStore.getSnapshot().projectId") == target_project_id
                assert page.locator("#fs-project-label").inner_text().startswith(target_project_id)
                assert page.locator("#fs-timeline .fs-clip[data-clip=switch-clip]").count() == 1

                # Return to A after its request has settled. Depending on the
                # server-side warning gate, the first request may have created
                # the document or left the migration preview untouched.
                reopened_after_initialize = page.evaluate("(pid) => workspaceOpen(pid)", project_id)
                assert reopened_after_initialize.get("ok") is True, reopened_after_initialize
                if not fd.read_project_film(project_id)["exists"]:
                    page.locator("#fs-initialize-button").wait_for(state="visible")
                    page.locator("#fs-initialize-button").click()
                page.wait_for_function(
                    "() => !!document.querySelector('#fs-timeline .fs-clip[data-clip]')",
                    timeout=10000,
                )
                initialized = fd.read_project_film(project_id)
                assert initialized["exists"] is True
                assert film_path.exists()
                migration_record_path = project_root / fd.MIGRATION_NAME
                migration_record = json.loads(migration_record_path.read_text(encoding="utf-8"))
                archive_path = project_root / "migration" / "source-composition.json"
                assert archive_path.is_file()
                archive_bytes = archive_path.read_bytes()
                persisted_snapshot = archive_bytes.decode("utf-8")
                archive_sha256 = __import__("hashlib").sha256(archive_bytes).hexdigest()
                assert migration_record["source_archive"] == {
                    "path": "migration/source-composition.json",
                    "sha256": archive_sha256,
                    "status": "written",
                }
                assert migration_record["source_snapshot_sha256"] == archive_sha256
                assert archive_digest.split("SHA-256：", 1)[1] == archive_sha256
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
                selected_clip_before_external = page.evaluate(
                    "() => window.filmStudioStore?.getSnapshot()?.selectedId || \"\""
                )
                assert selected_clip_before_external, "Inspector click must establish a canonical selected clip"
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
                saved_clip = next(
                    clip for clip in saved_clips
                    if clip.get("attrs", {}).get("title") == saved_title
                )
                assert saved_clip["id"] == selected_clip_before_external

                # Continue the same project through a real external Agent write.
                # The watcher must journal it, Studio must reload that revision,
                # and a fresh browser session must reopen the same FilmDocument
                # without changing the immutable CompositionIR source archive.
                external_doc, _ = fd.apply_ops(manual_state["doc"], [{
                    "op": "set", "clip": saved_clip["id"], "field": "title",
                    "value": saved_title + " 外部 Agent 修改",
                }])
                external_text = fd.serialize_film(external_doc)
                film_path.write_text(external_text, encoding="utf-8")
                external_revision = fd.revision_of(external_text)
                page.wait_for_function(
                    """expected => {
                      const state = window.filmStudioStore?.getSnapshot();
                      return state?.projectId === "unified-studio-migration-preview-browser" &&
                        state?.revision === expected &&
                        document.querySelector("#film-studio-alert")?.textContent.includes("修改来源：外部文件");
                    }""",
                    arg=external_revision,
                    timeout=15000,
                )
                selected_after_external = page.evaluate("""() => ({
                  selectedId:window.filmStudioStore?.getSnapshot()?.selectedId || "",
                  selectedIds:window.filmStudioStore?.getSnapshot()?.selectedIds || []
                })""")
                assert selected_after_external["selectedId"] == saved_clip["id"], selected_after_external
                assert saved_clip["id"] in selected_after_external["selectedIds"], selected_after_external
                history_deadline = time.monotonic() + 5.0
                history = fd._history_read(project_id)
                while not any(
                    entry.get("source") == "external_file"
                    and entry.get("after_rev") == external_revision
                    for entry in history["past"]
                ) and time.monotonic() < history_deadline:
                    time.sleep(0.02)
                    history = fd._history_read(project_id)
                assert history["past"] and history["past"][-1]["source"] == "external_file", {
                    "history": history,
                    "watcher_state": web_server._FILM_REVISION_WATCHER._projects.get(project_id),
                    "external_revision": external_revision,
                }

                page.reload(wait_until="domcontentloaded")
                page.wait_for_function("typeof window.workspaceOpen === 'function'")
                reopened_external = page.evaluate("(pid) => workspaceOpen(pid)", project_id)
                assert reopened_external.get("ok") is True, reopened_external
                page.wait_for_function(
                    """expected => {
                      const state = window.filmStudioStore?.getSnapshot();
                      return state?.projectId === expected.projectId &&
                        state?.revision === expected.revision &&
                        state?.film?.exists === true;
                    }""",
                    arg={"projectId": project_id, "revision": external_revision},
                    timeout=15000,
                )
                assert archive_path.read_text(encoding="utf-8") == persisted_snapshot
                assert __import__("hashlib").sha256(archive_path.read_bytes()).hexdigest() == archive_sha256
                manual_state = fd.read_project_film(project_id)
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
                page.locator('[data-film-action="render"]:visible').click()
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
                qc_href = page.locator("#fs-qc-download").get_attribute("href")
                assert mp4_href and mp4_href.startswith("/file?path="), mp4_href
                assert qc_href and qc_href.startswith("/file?path="), qc_href
                mp4_response = page.request.get(f"http://127.0.0.1:{server.server_port}{mp4_href}")
                qc_response = page.request.get(f"http://127.0.0.1:{server.server_port}{qc_href}")
                assert mp4_response.status == 200
                mp4_bytes = mp4_response.body()
                assert mp4_bytes
                assert qc_response.status == 200
                qc_report = json.loads(qc_response.body().decode("utf-8"))
                assert qc_report["status"] == "PASS", qc_report
                assert qc_report["revision_status"] == "CURRENT", qc_report
                assert qc_report["current_revision_match"] is True, qc_report
                assert qc_report["source_revision"] == export_revision, qc_report
                assert qc_report["current_revision"] == export_revision, qc_report
                output = qc_report["output"]
                assert output["size_bytes"] == len(mp4_bytes), output
                assert output["sha256"] == __import__("hashlib").sha256(mp4_bytes).hexdigest(), output
                assert output["video_codec"], output
                assert output["width"] == 160 and output["height"] == 90, output
                assert output["fps"] == manual_state["doc"].get("fps", 30), output
                assert abs(output["duration_seconds"] - 1.0) <= 0.25, output
                assert qc_report["determinism"]["status"] == "PASS", qc_report
                assert qc_report["determinism"]["samples_checked"] >= 1, qc_report
                assert qc_report["visual_samples"], qc_report
                assert all(
                    sample["width"] == output["width"]
                    and sample["height"] == output["height"]
                    and sample["sha256"]
                    for sample in qc_report["visual_samples"]
                ), qc_report["visual_samples"]
                assert qc_report["checks"], qc_report
                assert all(check["status"] == "PASS" for check in qc_report["checks"]), qc_report["checks"]
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
                release_initialize_request.set()
                context.close()
                browser.close()
    finally:
        release_initialize_request.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_film_preview_rechecks_authority_after_concurrent_initialization(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "film-preview-authority-race"
    store.create_project(project_id, "Film preview authority race", {"name": "Film preview authority race"})
    ir = CompositionIR(
        project=ProjectSpec(width=160, height=90, fps=10, duration=1.0),
        scenes=[SceneIR(
            id="legacy-scene", start=0, duration=1.0, text="legacy",
            layers=[Layer(type=LayerType.image, duration=1.0)],
        )],
    )
    editor.save_composition(project_id, ir)
    film_html = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=640,height=360">'
        '<meta name="film-fps" content="30"></head><body>'
        '<section id="main" data-label="主画面">'
        '<video id="film-clip" src="assets/still.mp4#t=0,1" at="0"></video>'
        '</section></body></html>'
    )
    film_path = config.project_dir(project_id) / fd.FILM_NAME
    original_snapshot = editor.load_composition_snapshot
    initialized = []

    def initialize_between_authority_probe_and_snapshot(pid):
        if pid == project_id and not initialized:
            film_path.write_text(film_html, encoding="utf-8")
            initialized.append(True)
        return original_snapshot(pid)

    monkeypatch.setattr(editor, "load_composition_snapshot", initialize_between_authority_probe_and_snapshot)

    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/api/editor/film?project_id={project_id}",
            timeout=10,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        assert initialized == [True]
        assert payload["exists"] is True
        assert payload["project_id"] == project_id
        assert payload["rev"] == fd.revision_of(film_html)
        assert "migration_available" not in payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
