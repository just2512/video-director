from __future__ import annotations

import importlib
import json
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright

from video_director import config, director_history


def _start_server(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    monkeypatch.setattr(web_server, "COMPOSER_ROOT", storage)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread

def _wait_for_deferred_route(page, deferred, key, timeout_ms=2000):
    """Let Playwright dispatch the route callback after the request event fires."""
    attempts = max(1, timeout_ms // 20)
    for _ in range(attempts):
        route = deferred.get(key)
        if route is not None:
            return route
        # Drives the sync Playwright event loop; a blocking threading.Event.wait
        # would prevent its greenlet from running the pending route callback.
        page.wait_for_timeout(20)
    raise AssertionError(f"route handler did not capture {key!r} within {timeout_ms} ms")


def test_create_dialog_backdrop_is_inert_and_draft_survives_reload(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                response = page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                assert response and response.status == 200
                assert page.locator("#director-history-launch").is_visible()

                page.evaluate("openCreate()")
                page.locator("#cf-form").wait_for(state="visible")
                page.wait_for_function("""() => {
                  const output = document.getElementById('cf-output-requirements');
                  const template = document.getElementById('cf-template-gallery-btn');
                  const context = document.getElementById('cf-input-context-details');
                  const advanced = document.getElementById('cf-manual-options');
                  const more = document.getElementById('cf-more-settings');
                  return !!output && output.contains(template)
                    && !!context && !context.open
                    && !!advanced && !advanced.open
                    && more?.parentElement?.id === 'cf-essentials'
                    && document.getElementById('cf-style-presets')?.closest('#cf-output-requirements');
                }""", timeout=10000)
                assert page.get_by_text("素材已移到上面的「输入上下文」区。").count() == 0
                assert page.locator("#cf-enhance-status").text_content().find("权威文案区") >= 0
                assert page.evaluate("""() => {
                    const brief=document.getElementById('cf-director-brief');
                    const script=document.getElementById('cf-script');
                    const goalActions=document.getElementById('cf-quick-actions');
                    const scriptActions=document.getElementById('cf-script-actions');
                    return !!brief&&!!script&&!!goalActions&&!!scriptActions
                      && brief.closest('.panel')?.contains(goalActions)
                      && !!goalActions.querySelector('[onclick="cfGenerateScript()"]')
                      && !!goalActions.querySelector('[onclick="cfMakeGraphicPlan()"]')
                      && script.closest('.panel')?.contains(scriptActions)
                      && !!scriptActions.querySelector('[onclick="createEnhance()"]');
                }""")
                page.locator("#cf-director-brief").fill("持久化测试：保留我的创作目标")
                page.locator("#cf-input-context-details summary").click()
                page.locator("#cf-script").fill("持久化测试：保留我的权威文案")
                page.locator("#cf-platform").select_option("xhs")
                page.locator("#cf-input-context-details summary").click()
                assert page.locator("#cf-input-context-details").evaluate("(node) => !node.open")

                page.wait_for_function("""async () => {
                  const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                  const result = await response.json();
                  const fields = result?.draft?.data?.fields || {};
                  return fields['cf-director-brief'] === '持久化测试：保留我的创作目标'
                    && fields['cf-script'] === '持久化测试：保留我的权威文案'
                    && fields['cf-platform'] === 'xhs'
                    && result?.draft?.data?.explicit_fields?.includes('platform');
                }""", timeout=10000)

                assert page.evaluate("document.elementFromPoint(8,8)?.id === 'cf-mask'")
                page.mouse.move(8, 8)
                page.mouse.down()
                page.mouse.up()
                page.mouse.click(8, 8)
                page.keyboard.press("Escape")
                assert page.locator("#cf-mask").evaluate("(el) => el.style.display") == "flex"
                assert page.locator("#cf-director-brief").input_value() == "持久化测试：保留我的创作目标"

                page.locator("#cf-history-inline-launch").click()
                page.locator('#cf-history-draft button[data-history-action="resume-draft"]').wait_for()
                assert "持久化测试：保留我的创作目标" in page.locator("#cf-history-draft").inner_text()
                page.locator('#cf-history-draft button[data-history-action="resume-draft"]').click()
                page.wait_for_function("document.getElementById('cf-history-drawer') === null")

                page.locator('#cf-form > div:first-child button[onclick="closeCreate()"]').click()
                page.wait_for_function("document.getElementById('cf-mask')?.style.display === 'none'")
                saved = page.evaluate("""async () => {
                  const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                  return (await response.json()).draft?.data?.fields || {};
                }""")
                assert saved["cf-director-brief"] == "持久化测试：保留我的创作目标"
                assert saved["cf-script"] == "持久化测试：保留我的权威文案"
                assert saved["cf-platform"] == "xhs"
                assert page.evaluate("JSON.parse(localStorage.getItem('video-director:create-draft:v1') || 'null')?.data?.fields?.['cf-script']") == "持久化测试：保留我的权威文案"

                page.reload(wait_until="networkidle")
                page.evaluate("openCreate()")
                page.wait_for_function("""() =>
                  document.getElementById('cf-director-brief')?.value === '持久化测试：保留我的创作目标'
                  && document.getElementById('cf-script')?.value === '持久化测试：保留我的权威文案'
                """, timeout=10000)
                assert page.locator("#cf-input-context-details").evaluate("(node) => !node.open")
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_script_generation_and_polish_reveal_and_persist_content(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.route(
                    "**/api/script/generate",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps({"ok": True, "script": "AI 生成的权威文案"}),
                    ),
                )
                page.route(
                    "**/api/enhance",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps({"ok": True, "changed": True, "enhanced": "AI 润色后的文案"}),
                    ),
                )

                page.evaluate("openCreate()")
                page.locator("#cf-input-context-details").wait_for(state="attached")
                assert page.locator("#cf-input-context-details").evaluate("(node) => !node.open")
                page.locator("#cf-director-brief").fill("制作一条解释复利的短视频")
                page.locator('#cf-quick-actions button[onclick="cfGenerateScript()"]').click()

                page.wait_for_function("""() =>
                    document.getElementById('cf-script')?.value === 'AI 生成的权威文案'
                    && document.getElementById('cf-input-context-details')?.open
                """, timeout=10000)
                assert page.locator("#cf-script").is_visible()
                assert page.locator("#cf-script-gen-status").is_visible()

                page.locator('#cf-script-actions button[onclick="createEnhance()"]').click()
                page.wait_for_function("""() =>
                    document.getElementById('cf-enhance-box')?.style.display !== 'none'
                    && document.getElementById('cf-input-context-details')?.open
                """, timeout=10000)
                assert page.locator("#cf-enhanced").is_visible()
                assert page.locator("#cf-enhance-status").is_visible()

                page.locator('#cf-enhance-box button[onclick="createAcceptEnhanced()"]').click()
                page.wait_for_function("document.getElementById('cf-script')?.value === 'AI 润色后的文案'")
                page.wait_for_function("""async () => {
                    const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                    const payload = await response.json();
                    return payload?.draft?.data?.fields?.['cf-script'] === 'AI 润色后的文案';
                }""", timeout=10000)
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_switching_projects_clears_fields_and_context_from_previous_project(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def project_brief(route):
                body = json.loads(route.request.post_data or "{}")
                project_id = body.get("project_id")
                if project_id == "project-with-context":
                    payload = {
                        "ok": True,
                        "brief": "项目 A 的创作要求",
                        "bundle": {
                            "script": {"text": "项目 A 的权威文案"},
                            "assets": {"asset_refs": [{"asset_id": "asset-a", "type": "video", "file": "A.mp4"}]},
                            "references": [{"id": "reference-a", "report": {"visual_language": {"pace": "slow"}}}],
                            "articles": [{
                                "requested_url": "https://example.test/a",
                                "source_url": "https://example.test/a",
                                "title": "项目 A 文章",
                                "content": "项目 A 文章正文",
                                "content_chars": 8,
                                "content_sha256": "a" * 64,
                                "content_type": "text/html",
                            }],
                            "software_project": {"id": "repo-a", "root": "D:/project-a", "claims": [{"text": "项目 A 卖点"}]},
                        },
                        "spec_settings": {"platform": "xhs", "duration": 45, "style_intent": "knowledge-explainer"},
                    }
                elif project_id == "project-without-context":
                    payload = {
                        "ok": True,
                        "brief": "项目 B 的创作要求",
                        "bundle": {
                            "script": {"text": ""},
                            "assets": {"asset_refs": []},
                            "references": [],
                            "articles": [],
                            "software_project": None,
                        },
                        "spec_settings": {},
                    }
                else:
                    return route.fulfill(status=400, content_type="application/json", body=json.dumps({
                        "ok": False, "error": f"unexpected project id: {project_id}"
                    }))
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

            page.route("**/api/director/brief", project_brief)
            page.route(
                "**/api/project_detail?project_id=project-without-context",
                lambda route: route.fulfill(status=404, content_type="application/json", body='{"error":"no legacy script"}'),
            )
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")

            page.evaluate("openCreate('project-with-context')")
            page.wait_for_function("""() =>
                document.getElementById('cf-director-brief')?.value === '项目 A 的创作要求'
                && document.getElementById('cf-script')?.value === '项目 A 的权威文案'
                && document.getElementById('cf-article-url')?.value === 'https://example.test/a'
                && document.getElementById('cf-assets-summary')?.textContent?.includes('A.mp4')
            """, timeout=10000)
            assert page.locator("#cf-input-context-details").evaluate("(node) => node.open")
            assert page.locator("#cf-resolution").input_value() == "1080x1440"
            assert "所选风格由 StyleContract" in (page.locator("#cf-style-contract-detail").text_content() or "")
            assert page.locator('#cf-style-presets [data-studio-style="knowledge-explainer"]').evaluate("(node) => node.classList.contains('tab-active')")

            page.evaluate("openCreate('project-without-context')")
            page.wait_for_function("""() =>
                document.getElementById('cf-director-brief')?.value === '项目 B 的创作要求'
                && document.getElementById('cf-script')?.value === ''
                && document.getElementById('cf-article-url')?.value === ''
                && document.getElementById('cf-assets-summary')?.textContent === '未添加本地素材'
                && document.getElementById('cf-input-context-details')?.open === false
                && window.CF_DRAFT_CONTEXT?.assetRefs?.length === 0
                && window.CF_DRAFT_CONTEXT?.projectId === 'project-without-context'
                && window._cfReference === null
                && window._cfArticle === null
                && window._cfRepo === null
            """, timeout=10000)
            assert page.locator("#cf-article-result").inner_text() == ""
            assert page.locator("#cf-reference-result").inner_text() == ""
            assert page.locator("#cf-repo-result").inner_text() == ""
            assert page.locator("#cf-platform").input_value() == ""
            assert page.locator("#cf-duration").input_value() == ""
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_late_project_prefill_response_cannot_overwrite_newer_project(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            deferred: dict[str, object] = {}

            old_payload = {
                "ok": True,
                "brief": "旧项目 A",
                "bundle": {
                    "script": {"text": "旧项目 A 文案"},
                    "assets": {"asset_refs": [{"asset_id": "old-asset", "type": "video", "file": "old.mp4"}]},
                    "references": [], "articles": [], "software_project": None,
                },
                "spec_settings": {"platform": "xhs", "duration": 60},
            }
            new_payload = {
                "ok": True,
                "brief": "新项目 B",
                "bundle": {
                    "script": {"text": ""},
                    "assets": {"asset_refs": []},
                    "references": [], "articles": [], "software_project": None,
                },
                "spec_settings": {},
            }

            def project_brief(route):
                body = json.loads(route.request.post_data or "{}")
                project_id = body.get("project_id")
                if project_id == "project-slow":
                    deferred["old_route"] = route
                    return  # Keep A's real browser request paused until the test releases it.
                if project_id == "project-fast":
                    return route.fulfill(status=200, content_type="application/json", body=json.dumps(new_payload))
                return route.fulfill(status=400, content_type="application/json", body=json.dumps({
                    "ok": False, "error": f"unexpected project id: {project_id}"
                }))

            page.route("**/api/director/brief", project_brief)
            page.route(
                "**/api/project_detail?project_id=project-fast",
                lambda route: route.fulfill(status=404, content_type="application/json", body='{"error":"no legacy script"}'),
            )

            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("openCreate()")
                page.wait_for_function("!!window.CF_DEFAULTS_READY")

                with page.expect_request("**/api/director/brief") as slow_request:
                    page.evaluate("() => { window.__slowProjectPromise = openCreate('project-slow'); }")
                assert json.loads(slow_request.value.post_data or "{}").get("project_id") == "project-slow"
                old_route = _wait_for_deferred_route(page, deferred, "old_route")

                page.evaluate("openCreate('project-fast')")
                page.wait_for_function("""() =>
                    document.getElementById('cf-director-brief')?.value === '新项目 B'
                    && document.getElementById('cf-script')?.value === ''
                    && window.CF_DRAFT_CONTEXT?.projectId === 'project-fast'
                """, timeout=10000)
                assert page.locator("#cf-input-context-details").evaluate("(node) => !node.open")

                old_route.fulfill(status=200, content_type="application/json", body=json.dumps(old_payload))
                deferred.pop("old_route", None)
                page.evaluate("async () => await window.__slowProjectPromise")
                page.wait_for_function("""async () => {
                    const response=await fetch('/api/director/drafts/active',{cache:'no-store'});
                    const result=await response.json();
                    const data=result?.draft?.data||{};
                    return document.getElementById('cf-director-brief')?.value === '新项目 B'
                      && document.getElementById('cf-script')?.value === ''
                      && data.context?.project_id === 'project-fast'
                      && data.fields?.['cf-director-brief'] === '新项目 B';
                }""", timeout=10000)
                assert page.evaluate("window.CF_DRAFT_CONTEXT.projectId") == "project-fast"
                assert not errors
            finally:
                pending_route = deferred.pop("old_route", None)
                if pending_route is not None:
                    try:
                        pending_route.abort()
                    except Exception:
                        pass
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_late_asset_upload_response_is_discarded_after_project_switch(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        image = tmp_path / "project-a.png"
        image.write_bytes(b"fake-image-fixture")
        deferred: dict[str, object] = {}

        def hold_asset_upload(route):
            deferred["route"] = route

        def project_brief(route):
            body = json.loads(route.request.post_data or "{}")
            project_id = body.get("project_id")
            if project_id != "project-after-upload":
                return route.fulfill(status=400, content_type="application/json", body=json.dumps({
                    "ok": False, "error": f"unexpected project id: {project_id}"
                }))
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "ok": True,
                "brief": "项目 B 的创作目标",
                "bundle": {
                    "script": {"text": ""},
                    "assets": {"asset_refs": []},
                    "references": [], "articles": [], "software_project": None,
                },
                "spec_settings": {},
            }))

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.route("**/api/assets/upload", hold_asset_upload)
                page.route("**/api/director/brief", project_brief)
                page.route(
                    "**/api/project_detail?project_id=project-after-upload",
                    lambda route: route.fulfill(status=404, content_type="application/json", body='{"error":"no legacy script"}'),
                )
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("openCreate()")
                page.wait_for_function("!!window.CF_DEFAULTS_READY")
                page.locator("#cf-input-context-details summary").click()

                with page.expect_request("**/api/assets/upload") as upload_request:
                    page.locator("#cf-assets").set_input_files(str(image))
                assert "project-a.png" in (upload_request.value.post_data or "")
                page.wait_for_function("typeof window.CF_DRAFT_CONTEXT.assetUploadPromise?.then === 'function'")
                page.evaluate("""() => {
                    window.__oldAssetUploadPromise = window.CF_DRAFT_CONTEXT.assetUploadPromise;
                }""")

                page.evaluate("""() => {
                    window.__assetProjectSwitchConfirmMessages = [];
                    window.confirm = message => {
                      window.__assetProjectSwitchConfirmMessages.push(String(message));
                      return true;
                    };
                    const state = {settled: false, result: null, error: null};
                    window.__projectAfterUploadState = state;
                    // Do not make Playwright's evaluate call wait on the async UI workflow.
                    // Its API requests and routed upload must continue to be dispatched by
                    // the sync Playwright event loop while the test advances the browser.
                    window.__projectAfterUploadPromise = Promise.resolve(
                        openCreate('project-after-upload')
                    ).then(
                        result => { state.settled = true; state.result = result; },
                        error => { state.settled = true; state.error = String(error); }
                    );
                }""")
                page.wait_for_function("""() =>
                    document.getElementById('cf-director-brief')?.value === '项目 B 的创作目标'
                    && window.CF_DRAFT_CONTEXT?.projectId === 'project-after-upload'
                    && window.CF_DRAFT_CONTEXT?.assetRefs?.length === 0
                """, timeout=10000)

                delayed_route = _wait_for_deferred_route(page, deferred, "route")
                delayed_route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "ok": True,
                        "imported": [{
                            "asset_id": "asset-from-project-a",
                            "type": "image",
                            "file": "project-a.png",
                            "width": 640,
                            "height": 360,
                            "duration": None,
                            "commercial_status": "UNKNOWN",
                        }],
                    }),
                )
                deferred.pop("route", None)
                page.evaluate("""() => {
                    const state = {settled: false, result: null, error: null};
                    window.__oldAssetUploadState = state;
                    // Keep the sync Playwright event loop free to process the upload response.
                    Promise.resolve(window.__oldAssetUploadPromise).then(
                        result => { state.settled = true; state.result = result; },
                        error => { state.settled = true; state.error = String(error); }
                    );
                }""")
                page.wait_for_function(
                    "window.__oldAssetUploadState?.settled === true",
                    timeout=10000,
                )
                upload_state = page.evaluate("window.__oldAssetUploadState")
                assert upload_state["error"] is None, upload_state["error"]
                assert upload_state["result"] is False, upload_state
                page.wait_for_function("""async () => {
                    const response=await fetch('/api/director/drafts/active',{cache:'no-store'});
                    const result=await response.json();
                    const data=result?.draft?.data||{};
                    return window.CF_DRAFT_CONTEXT?.projectId === 'project-after-upload'
                      && window.CF_DRAFT_CONTEXT?.assetRefs?.length === 0
                      && data.context?.project_id === 'project-after-upload'
                      && (data.context?.asset_refs||[]).length === 0
                      && data.fields?.['cf-director-brief'] === '项目 B 的创作目标';
                }""", timeout=10000)
                page.wait_for_function(
                    "window.__projectAfterUploadState?.settled === true",
                    timeout=10000,
                )
                switch_state = page.evaluate("window.__projectAfterUploadState")
                assert switch_state["error"] is None, switch_state["error"]
                assert page.evaluate("window.__assetProjectSwitchConfirmMessages.length") == 1
                assert switch_state["result"] == {"ok": True, "project_id": "project-after-upload"}

                assert page.locator("#cf-assets-summary").text_content() == "未添加本地素材"
                assert not errors
            finally:
                pending_route = deferred.pop("route", None)
                if pending_route is not None:
                    try:
                        pending_route.abort()
                    except Exception:
                        pass
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_history_is_available_without_create_dialog_and_can_restore_snapshot(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        conversation = director_history.create_conversation(
            conversation_id="conv-create-history-browser",
            title="独立历史入口验收",
            initial_message="做一条介绍软件项目的视频",
            snapshot={
                "form": {
                    "fields": {
                        "cf-director-brief": "从历史恢复的创作目标",
                        "cf-script": "从历史恢复的权威文案",
                    },
                    "context": {"asset_refs": []},
                }
            },
        )
        director_history.append_message(
            conversation["conversation_id"], "assistant", "历史对话可以独立打开与恢复。"
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.locator("#director-history-launch").click()
                page.locator("#cf-history-drawer").wait_for(state="visible")
                page.wait_for_function("document.getElementById('cf-history-list')?.innerText.includes('独立历史入口验收')")
                page.locator('#cf-history-list button[data-conversation-id="conv-create-history-browser"]').click()
                page.wait_for_function("document.getElementById('cf-history-list')?.innerText.includes('历史对话可以独立打开与恢复。')")
                page.evaluate("""() => {
                    window.__historyRestoreConfirmMessages = [];
                    window.confirm = message => {
                        window.__historyRestoreConfirmMessages.push(String(message));
                        return true;
                    };
                }""")
                page.evaluate("""() => {
                    window.__historyRestoreTrace = {invoked: false};
                    window.addEventListener('unhandledrejection', event => {
                        window.__historyRestoreUnhandledRejection = String(
                            event.reason?.stack || event.reason || 'unknown rejection'
                        );
                    });
                    const list = document.getElementById('cf-history-list');
                    list.addEventListener('click', event => {
                        const button = event.target.closest('[data-history-action="restore"]');
                        if (button) window.__historyRestoreButtonId = button.dataset.conversationId || '';
                    }, true);
                    const original = window.cfHistoryRestoreSnapshot;
                    window.cfHistoryRestoreSnapshot = function(id) {
                        const trace = window.__historyRestoreTrace;
                        Object.assign(trace, {
                            invoked: true,
                            id: String(id || ''),
                            selectedId: String(window._cfHistorySelected?.conversation_id || '')
                        });
                        let pending;
                        try {
                            pending = original(id);
                        } catch (error) {
                            trace.settled = true;
                            trace.error = String(error?.stack || error);
                            return Promise.resolve(false);
                        }
                        trace.returnedPromise = !!(pending && typeof pending.then === 'function');
                        return Promise.resolve(pending).then(result => {
                            trace.settled = true;
                            trace.result = result === undefined ? 'undefined' : result;
                            return result;
                        }, error => {
                            trace.settled = true;
                            trace.error = String(error?.stack || error);
                            return false;
                        });
                    };
                }""")
                page.locator('#cf-history-list button[data-history-action="restore"]').click()

                page.locator("#cf-form").wait_for(state="visible")
                try:
                    page.wait_for_function(
                        "window.__historyRestoreTrace?.settled === true",
                        timeout=7000,
                    )
                except Exception as error:
                    diagnostic = page.evaluate("""() => ({
                        trace: window.__historyRestoreTrace,
                        buttonId: window.__historyRestoreButtonId || '',
                        selectedId: window._cfHistorySelected?.conversation_id || '',
                        confirms: window.__historyRestoreConfirmMessages || [],
                        rejection: window.__historyRestoreUnhandledRejection || '',
                        maskDisplay: document.getElementById('cf-mask')?.style.display || 'missing',
                        brief: document.getElementById('cf-director-brief')?.value || '',
                        script: document.getElementById('cf-script')?.value || '',
                        status: document.getElementById('cf-draft-status')?.textContent || ''
                    })""")
                    raise AssertionError(
                        f"History restore did not settle: {diagnostic}"
                    ) from error
                trace = page.evaluate("window.__historyRestoreTrace")
                assert trace["invoked"] is True, trace
                assert trace["id"] == "conv-create-history-browser", trace
                assert trace["selectedId"] == trace["id"], trace
                assert "error" not in trace, trace
                try:
                    page.wait_for_function("""() =>
                      document.getElementById('cf-director-brief')?.value === '从历史恢复的创作目标'
                      && document.getElementById('cf-script')?.value === '从历史恢复的权威文案'
                    """, timeout=10000)
                except Exception as error:
                    diagnostic = page.evaluate("""() => ({
                        trace: window.__historyRestoreTrace,
                        selected: window._cfHistorySelected || null,
                        buttonId: window.__historyRestoreButtonId || '',
                        confirms: window.__historyRestoreConfirmMessages || [],
                        fields: {
                            brief: document.getElementById('cf-director-brief')?.value ?? null,
                            script: document.getElementById('cf-script')?.value ?? null,
                            article: document.getElementById('cf-article-url')?.value ?? null,
                            repo: document.getElementById('cf-repo-root')?.value ?? null
                        },
                        context: window.CF_DRAFT_CONTEXT || null,
                        explicitFields: Array.from(window.CF?.explicitFields || []),
                        maskDisplay: document.getElementById('cf-mask')?.style.display || 'missing',
                        status: document.getElementById('cf-draft-status')?.textContent || ''
                    })""")
                    raise AssertionError(
                        f"History snapshot was not applied to the form: {diagnostic}; pageerrors={errors}"
                    ) from error
                assert page.locator("#cf-history-drawer").is_visible()
                assert page.locator("#cf-input-context-details").evaluate("(node) => node.open")
                assert page.evaluate("window.__historyRestoreConfirmMessages") == []
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)






def test_blank_persisted_draft_with_default_radio_does_not_prompt_on_history_restore(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        conversation = director_history.create_conversation(
            conversation_id="conv-blank-draft-default-radio",
            title="空白草稿恢复历史",
            snapshot={
                "form": {
                    "fields": {
                        "cf-director-brief": "历史记录中的目标",
                        "cf-script": "历史记录中的文案",
                    },
                    "context": {"asset_refs": []},
                }
            },
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                saved = page.evaluate("""async () => {
                    const response = await fetch('/api/director/drafts/active', {
                      method: 'POST',
                      headers: {'Content-Type':'application/json'},
                      body: JSON.stringify({
                        data: {
                          version: 1,
                          fields: {'cf-scope': 'single'},
                          explicit_fields: [],
                          context: {asset_refs: [], reference: null, article: null, repo: null,
                                    narration_upload: '', project_id: ''}
                        }
                      })
                    });
                    return await response.json();
                }""")
                assert saved["ok"] is True

                page.evaluate("openCreate()")
                page.locator("#cf-director-brief").wait_for(state="visible")
                page.locator("#cf-history-inline-launch").click()
                page.locator("#cf-history-list button[data-conversation-id='conv-blank-draft-default-radio']").click()
                page.wait_for_function(
                    "document.querySelector('#cf-history-list [data-history-action=\"restore\"]') !== null"
                )
                page.evaluate("""() => {
                    window.__blankDraftConfirmMessages = [];
                    window.confirm = message => {
                        window.__blankDraftConfirmMessages.push(String(message));
                        return true;
                    };
                }""")
                page.locator('#cf-history-list button[data-history-action="restore"]').click()
                page.wait_for_function("""() =>
                    document.getElementById('cf-director-brief')?.value === '历史记录中的目标'
                    && document.getElementById('cf-script')?.value === '历史记录中的文案'
                """, timeout=10000)
                assert page.evaluate("window.__blankDraftConfirmMessages") == []
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_history_restore_supports_legacy_director_inputs_snapshot(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        conversation = director_history.create_conversation(
            conversation_id="conv-legacy-director-inputs",
            title="旧版快照兼容",
            snapshot={
                "director_inputs": {
                    "brief": "旧版历史恢复的创作目标",
                    "platform": "xhs",
                    "duration": "45",
                    "style": "旧版纪录片风格",
                    "constraints": {"visual_source": "local"},
                    "script": {
                        "text": "旧版历史恢复的权威文案",
                        "source": "user",
                        "authority": "authoritative",
                        "editable": True,
                    },
                    "assets": {
                        "asset_refs": [
                            {
                                "asset_id": "asset-legacy-video",
                                "type": "video",
                                "file": "legacy-clip.mp4",
                            }
                        ]
                    },
                    "references": [
                        {"url": "https://example.test/legacy-reference", "title": "旧版参考视频"}
                    ],
                    "articles": [],
                    "software_project": {},
                },
                "request_options": {
                    "voice_mode": "upload",
                    "narration_upload": "staged-audio/legacy-narration.wav",
                },
            },
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.locator("#director-history-launch").click()
                page.locator("#cf-history-list button[data-conversation-id='conv-legacy-director-inputs']").click()
                page.wait_for_function(
                    "document.querySelector('#cf-history-list [data-history-action=\"restore\"]') !== null"
                )
                page.locator('#cf-history-list button[data-history-action="restore"]').click()
                page.wait_for_function("""() =>
                    document.getElementById('cf-director-brief')?.value === '旧版历史恢复的创作目标'
                    && document.getElementById('cf-script')?.value === '旧版历史恢复的权威文案'
                """, timeout=10000)
                page.wait_for_function("""async () => {
                    const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                    const data = (await response.json())?.draft?.data || {};
                    return data.context?.asset_refs?.[0]?.asset_id === 'asset-legacy-video'
                      && data.context?.reference?.url === 'https://example.test/legacy-reference';
                }""", timeout=10000)
                assert page.locator("#cf-input-context-details").evaluate("(node) => node.open")
                assert page.locator("#cf-platform").input_value() == "xhs"
                assert page.locator("#cf-duration").input_value() == "45"
                assert page.locator("#cf-style").input_value() == "旧版纪录片风格"
                assert page.locator("#cf-visual-source").input_value() == "local"
                assert page.locator("#cf-voice-mode").input_value() == "upload"
                assert page.locator("#cf-manual-options").evaluate("(node) => node.open")
                assert page.locator("#cf-voice-upload-wrap").is_visible()
                assert page.evaluate("window.CF_DRAFT_CONTEXT.narrationUpload") == "staged-audio/legacy-narration.wav"
                assert page.evaluate("window._cfNarrationUpload") == "staged-audio/legacy-narration.wav"
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)












def test_scope_change_during_draft_restore_preserves_other_saved_fields(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        director_history.save_draft(
            "active",
            {
                "version": 1,
                "fields": {
                    "cf-director-brief": "服务端原有创作目标",
                    "cf-script": "服务端原有权威文案",
                    "cf-platform": "xhs",
                    "cf-scope": "single",
                },
                "explicit_fields": [],
                "context": {"asset_refs": []},
            },
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            deferred: dict[str, object] = {}
            page.on("pageerror", lambda error: errors.append(str(error)))

            def hold_draft_read(route):
                if route.request.method == "GET" and "read" not in deferred:
                    deferred["read"] = route
                else:
                    route.continue_()

            page.route("**/api/director/drafts/active", hold_draft_read)
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("""() => {
                    window.__restorePromiseState = {done:false};
                    window.__restorePromise = openCreate().finally(() => { window.__restorePromiseState.done = true; });
                    return true;
                }""")
                read_route = _wait_for_deferred_route(page, deferred, "read")
                page.locator('input[name="cf-scope"][value="series"]').check()
                assert page.locator('input[name="cf-scope"][value="series"]').is_checked()
                page.wait_for_timeout(600)
                assert director_history.get_draft("active")["data"]["fields"]["cf-scope"] == "single"
                read_route.continue_()
                page.wait_for_function("window.__restorePromiseState?.done === true", timeout=10000)
                page.wait_for_function("""() =>
                    document.getElementById('cf-director-brief')?.value === '服务端原有创作目标'
                    && document.getElementById('cf-script')?.value === '服务端原有权威文案'
                    && document.getElementById('cf-platform')?.value === 'xhs'
                    && document.querySelector('input[name="cf-scope"]:checked')?.value === 'series'
                    && document.getElementById('cf-series-options')?.style.display !== 'none'
                """, timeout=10000)
                page.wait_for_function("""async () => {
                    const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                    const data = (await response.json())?.draft?.data || {};
                    return data.fields?.['cf-scope'] === 'series'
                      && data.fields?.['cf-director-brief'] === '服务端原有创作目标'
                      && data.fields?.['cf-script'] === '服务端原有权威文案';
                }""", timeout=10000)
                persisted = director_history.get_draft("active")
                assert persisted and persisted["data"]["fields"]["cf-scope"] == "series"
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_history_detail_ignores_late_response_from_previous_selection(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        director_history.create_conversation(
            conversation_id="conv-history-slow",
            title="较慢返回的旧对话",
            initial_message="旧选择",
        )
        director_history.create_conversation(
            conversation_id="conv-history-fast",
            title="较快返回的新对话",
            initial_message="新选择",
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            deferred: dict[str, object] = {}
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route(
                "**/api/director/conversations/conv-history-slow",
                lambda route: deferred.__setitem__("slow", route),
            )
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.locator("#director-history-launch").click()
                page.locator("#cf-history-list button[data-conversation-id='conv-history-slow']").wait_for(
                    state="visible"
                )
                # Trigger the slow request with an exact ID, and hold it until the
                # faster row selection has completed. The list stays interactive.
                page.evaluate("""() => {
                    window.__slowHistorySelection = {settled:false, result:null, error:null};
                    window.__slowHistoryPromise = Promise.resolve(
                        window.cfHistoryOpenConversation('conv-history-slow')
                    ).then(
                        result => {
                          window.__slowHistorySelection.settled=true;
                          window.__slowHistorySelection.result=result;
                        },
                        error => {
                          window.__slowHistorySelection.settled=true;
                          window.__slowHistorySelection.error=String(error);
                        }
                    );
                }""")
                slow_route = _wait_for_deferred_route(page, deferred, "slow")
                assert page.locator("#cf-history-list button[data-conversation-id='conv-history-fast']").is_visible()
                page.locator("#cf-history-list button[data-conversation-id='conv-history-fast']").click()
                page.wait_for_function("""() =>
                    document.querySelector('#cf-history-list')?.textContent.includes('较快返回的新对话')
                    && window._cfHistorySelected?.conversation_id === 'conv-history-fast'
                """, timeout=10000)

                slow_route.continue_()
                page.wait_for_timeout(150)
                assert page.locator("#cf-history-list").inner_text().find("较快返回的新对话") >= 0
                assert page.locator("#cf-history-list").inner_text().find("较慢返回的旧对话") < 0
                assert page.evaluate("window._cfHistorySelected?.conversation_id") == "conv-history-fast"
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_history_drawer_loads_all_pages_without_repeating_conversations(tmp_path, monkeypatch):
    monkeypatch.setattr(director_history.time, "time", lambda: 1700000000.0)
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        for index in range(53):
            director_history.create_conversation(
                conversation_id=f"conv-pagination-{index:03d}",
                title=f"历史分页 {index:03d}",
            )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.locator("#director-history-launch").click()
                page.wait_for_function(
                    "document.querySelectorAll('#cf-history-list [data-conversation-id]').length === 50"
                )
                assert page.locator("#cf-history-list [data-conversation-id='conv-pagination-000']").count() == 0
                assert page.locator('#cf-history-list [data-history-action="load-more"]').is_visible()

                page.locator('#cf-history-list [data-history-action="load-more"]').click()
                page.wait_for_function(
                    "document.querySelectorAll('#cf-history-list [data-conversation-id]').length === 53"
                )
                assert page.locator("#cf-history-list [data-conversation-id='conv-pagination-000']").is_visible()
                assert page.locator("#cf-history-list [data-conversation-id='conv-pagination-052']").is_visible()
                assert page.locator('#cf-history-list [data-history-action="load-more"]').count() == 0
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_draft_restore_merges_context_sources_independently_during_asset_upload(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        director_history.save_draft("active", {
            "version": 1,
            "fields": {
                "cf-director-brief": "已保存目标",
                "cf-script": "已保存权威文案",
                "cf-article-url": "https://example.test/saved-article",
                "cf-repo-root": "D:/saved-repo",
            },
            "explicit_fields": [],
            "context": {
                "asset_refs": [{"asset_id": "asset-saved", "type": "image", "file": "saved.png"}],
                "reference": {"url": "https://example.test/saved-video", "title": "已保存参考"},
                "article": {"source_url": "https://example.test/saved-article", "title": "已保存文章"},
                "repo": {"root": "D:/saved-repo", "name": "已保存仓库"},
                "narration_upload": "staged/saved-narration.wav",
            },
        })
        image = tmp_path / "new-upload.png"
        image.write_bytes(b"new-upload-fixture")
        deferred: dict[str, object] = {}
        upload_requests: list[dict] = []

        def draft_route(route):
            if route.request.method == "GET" and "read" not in deferred:
                deferred["read"] = route
            else:
                route.continue_()

        def hold_upload(route):
            deferred["upload"] = route
            upload_requests.append({"url": route.request.url, "body": route.request.post_data or ""})

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/director/drafts/active", draft_route)
            page.route("**/api/assets/upload", hold_upload)
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("""() => {
                    const state = {done:false};
                    window.__draftRestoreState = state;
                    window.__restorePromise = openCreate().finally(() => { state.done = true; });
                    window.__originalDraftContext = window.CF_DRAFT_CONTEXT;
                    return true;
                }""")
                read_route = _wait_for_deferred_route(page, deferred, "read")
                page.locator("#cf-input-context-details").evaluate("(node) => { node.open = true; }")
                page.locator("#cf-assets").set_input_files(str(image))
                page.wait_for_function(
                    "typeof window.CF_DRAFT_CONTEXT.assetUploadPromise?.then === 'function'",
                    timeout=10000,
                )
                upload_route = _wait_for_deferred_route(page, deferred, "upload")
                read_route.continue_()
                page.wait_for_function("window.__draftRestoreState?.done === true", timeout=10000)
                page.wait_for_function("""() => {
                    const context = window.CF_DRAFT_CONTEXT;
                    return context?.assetRefs?.some(item => item.asset_id === 'asset-saved')
                      && context?.reference?.url === 'https://example.test/saved-video'
                      && context?.article?.source_url === 'https://example.test/saved-article'
                      && context?.repo?.root === 'D:/saved-repo'
                      && context?.narrationUpload === 'staged/saved-narration.wav';
                }""", timeout=10000)
                assert page.evaluate("window.CF_DRAFT_CONTEXT === window.__originalDraftContext")
                assert page.locator("#cf-director-brief").input_value() == "已保存目标"
                assert page.locator("#cf-script").input_value() == "已保存权威文案"
                upload_route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "ok": True,
                        "imported": [{
                            "asset_id": "asset-new-upload",
                            "type": "image",
                            "file": "new-upload.png",
                            "width": 640,
                            "height": 480,
                            "duration": None,
                            "commercial_status": "UNKNOWN",
                        }],
                    }),
                )
                page.wait_for_function("""() => {
                    const ids = (window.CF_DRAFT_CONTEXT?.assetRefs || []).map(item => item.asset_id);
                    return ids.includes('asset-saved') && ids.includes('asset-new-upload');
                }""", timeout=10000)
                page.wait_for_function("""async () => {
                    const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                    const data = (await response.json())?.draft?.data || {};
                    return data.context?.asset_refs?.some(item => item.asset_id === 'asset-saved')
                      && data.context?.asset_refs?.some(item => item.asset_id === 'asset-new-upload')
                      && data.context?.reference?.url === 'https://example.test/saved-video'
                      && data.context?.article?.source_url === 'https://example.test/saved-article'
                      && data.context?.repo?.root === 'D:/saved-repo'
                      && data.context?.narration_upload === 'staged/saved-narration.wav';
                }""", timeout=10000)
                assert not errors
            finally:
                for key in ("read", "upload"):
                    pending = deferred.pop(key, None)
                    if pending is not None:
                        try:
                            pending.abort()
                        except Exception:
                            pass
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_pending_history_restore_is_cancelled_when_user_selects_another_conversation(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        director_history.save_draft("active", {
            "version": 1,
            "fields": {
                "cf-director-brief": "必须保留的当前草稿",
                "cf-script": "当前权威文案",
            },
            "explicit_fields": [],
            "context": {"asset_refs": []},
        })
        director_history.create_conversation(
            conversation_id="conv-restore-stale",
            title="慢速待恢复历史",
            initial_message="不得覆盖新选择",
            snapshot={"form": {"fields": {"cf-director-brief": "旧历史目标", "cf-script": "旧历史文案"}}},
        )
        director_history.create_conversation(
            conversation_id="conv-restore-newer",
            title="新选择历史",
            initial_message="保留当前查看",
            snapshot={"form": {"fields": {"cf-director-brief": "新历史目标", "cf-script": "新历史文案"}}},
        )
        deferred: dict[str, object] = {"armed":False}

        def hold_first_active_draft_read(route):
            if route.request.method == "GET" and deferred["armed"] and "read" not in deferred:
                deferred["read"] = route
            else:
                route.continue_()

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/director/drafts/active", hold_first_active_draft_read)
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.locator("#director-history-launch").click()
                page.locator("#cf-history-list button[data-conversation-id='conv-restore-stale']").click()
                page.wait_for_function(
                    "document.querySelector('#cf-history-list [data-history-action=\"restore\"]') !== null"
                )
                page.evaluate("""() => {
                    window.__historyRestoreConfirmMessages = [];
                    window.confirm = message => {
                      window.__historyRestoreConfirmMessages.push(String(message));
                      return true;
                    };
                }""")
                deferred["armed"] = True
                page.locator('#cf-history-list button[data-history-action="restore"]').click()
                read_route = _wait_for_deferred_route(page, deferred, "read")
                page.locator('#cf-history-list button[data-history-action="list"]').click()
                page.locator("#cf-history-list button[data-conversation-id='conv-restore-newer']").click()
                page.wait_for_function("""() =>
                    window._cfHistorySelected?.conversation_id === 'conv-restore-newer'
                    && document.querySelector('#cf-history-list')?.textContent.includes('新选择历史')
                """, timeout=10000)
                read_route.continue_()
                page.wait_for_function("""() =>
                    document.getElementById('cf-director-brief')?.value === '必须保留的当前草稿'
                    && document.getElementById('cf-script')?.value === '当前权威文案'
                """, timeout=10000)
                assert page.evaluate("window._cfHistorySelected?.conversation_id") == "conv-restore-newer"
                assert page.locator("#cf-history-list").inner_text().find("新选择历史") >= 0
                assert page.evaluate("window.__historyRestoreConfirmMessages") == []
                assert not errors
            finally:
                pending = deferred.pop("read", None)
                if pending is not None:
                    try:
                        pending.abort()
                    except Exception:
                        pass
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_history_drawer_does_not_resave_or_replace_active_progress_form(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        director_history.create_conversation(
            conversation_id="conv-during-active-progress",
            project_id="project-must-not-prefill",
            title="运行中不能替换",
            initial_message="保留正在执行的任务",
            snapshot={"form": {"fields": {"cf-director-brief": "历史目标"}}},
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            brief_requests: list[str] = []
            deferred_clear: dict[str, object] = {}
            def hold_draft_clear(route):
                deferred_clear["route"] = route
            page.route("**/api/director/drafts/active/clear", hold_draft_clear)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/workflows/run-history-guard", lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "status": "RUNNING", "steps": []}),
            ))
            page.route("**/api/director/brief", lambda route: (
                brief_requests.append(route.request.url) or route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"ok": True, "brief": "错误的项目回填", "bundle": {}}),
                )
            ))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("openCreate()")
                page.locator("#cf-director-brief").fill("正在执行的任务输入")
                page.locator("#cf-history-inline-launch").click()
                page.locator("#cf-history-list button[data-conversation-id='conv-during-active-progress']").click()
                page.wait_for_function(
                    "document.querySelector('#cf-history-list [data-history-action=\"restore\"]') !== null"
                )

                # Clear the active draft before entering progress mode, matching
                # production ordering. Hold the HTTP clear request so the pending
                # window is deterministic and can be tested independently.
                page.evaluate("() => { window.__clearDraftPromise = window.cfClearSavedDraft(); return true; }")
                clear_route = _wait_for_deferred_route(page, deferred_clear, "route")
                during_clear = page.evaluate("window.cfPersistCreateDraftNow()")
                assert during_clear["skipped"] is True
                clear_route.continue_()
                cleared = page.evaluate("() => window.__clearDraftPromise")
                assert cleared["ok"] is True
                page.evaluate("cfEnterProgressMode('run-history-guard')")
                page.locator("#cf-history-refresh").click()
                page.wait_for_function(
                    "document.querySelector('#cf-history-list button[data-conversation-id=\"conv-during-active-progress\"]') !== null"
                )

                active_draft = page.evaluate("""async () => {
                    const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                    return (await response.json())?.draft || null;
                }""")
                assert active_draft is None

                page.locator("#cf-history-list button[data-conversation-id='conv-during-active-progress']").click()
                page.wait_for_function(
                    "document.querySelector('#cf-history-list [data-open-project=\"project-must-not-prefill\"]') !== null"
                )
                page.locator('#cf-history-list button[data-history-action="restore"]').click()
                page.wait_for_function(
                    "document.querySelector('#cf-history-notice')?.textContent.includes('仍在执行') === true"
                )
                assert page.locator("#cf-form").evaluate("(node) => node.style.display") == "none"
                assert page.locator("#cf-progress").is_visible()
                assert page.locator("#cf-director-brief").input_value() == "正在执行的任务输入"

                page.locator('#cf-history-list [data-open-project="project-must-not-prefill"]').click()
                page.wait_for_function(
                    "document.querySelector('#cf-history-notice')?.textContent.includes('不能从历史记录切换项目') === true"
                )
                assert brief_requests == []
                assert page.locator("#cf-form").evaluate("(node) => node.style.display") == "none"
                assert page.locator("#cf-director-brief").input_value() == "正在执行的任务输入"
                active_draft_after = page.evaluate("""async () => {
                    const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                    return (await response.json())?.draft || null;
                }""")
                assert active_draft_after is None

                resume_result = page.evaluate("window.cfHistoryResumeDraft()")
                assert resume_result is False
                assert page.locator("#cf-history-drawer").is_visible()
                assert page.locator("#cf-history-notice").inner_text().find("暂时不能继续编辑") >= 0

                direct_save = page.evaluate("window.cfPersistCreateDraftNow()")
                assert direct_save["skipped"] is True
                page.locator("#cf-director-brief").evaluate("""node => {
                    node.value = '进度模式不得重新写入的内容';
                    node.dispatchEvent(new Event('input', {bubbles:true}));
                }""")
                page.wait_for_timeout(650)
                beacon_calls = page.evaluate("""() => {
                    window.__beforeUnloadBeacons = [];
                    navigator.sendBeacon = (url, body) => {
                      window.__beforeUnloadBeacons.push({url:String(url), body:String(body)});
                      return true;
                    };
                    window.dispatchEvent(new Event('beforeunload'));
                    return window.__beforeUnloadBeacons;
                }""")
                assert beacon_calls == []
                assert page.evaluate("localStorage.getItem('video-director:create-draft:v1')") is None
                active_draft_final = page.evaluate("""async () => {
                    const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                    return (await response.json())?.draft || null;
                }""")
                assert active_draft_final is None
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_cancel_and_edit_saves_draft_after_server_confirms_stop(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/workflows/cancel", lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ok": True, "status": "CANCELLED"}),
            ))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("openCreate()")
                page.locator("#cf-director-brief").fill("停止后必须重新保存的创作目标")
                page.locator("#cf-input-context-details summary").click()
                page.locator("#cf-script").fill("停止后必须重新保存的权威文案")
                cleared = page.evaluate("window.cfClearSavedDraft()")
                assert cleared["ok"] is True

                page.evaluate("cfEnterProgressMode('run-cancel-save')")
                assert page.locator("#cf-form").evaluate("(node) => node.style.display") == "none"
                page.locator('#cf-progress button[onclick="cfCancelAndEdit()"]').click()
                page.wait_for_function("""() =>
                    document.getElementById('cf-form')?.style.display !== 'none'
                    && document.getElementById('cf-progress') === null
                """, timeout=10000)
                page.wait_for_function("""async () => {
                    const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                    const draft = (await response.json())?.draft?.data || {};
                    return draft.fields?.['cf-director-brief'] === '停止后必须重新保存的创作目标'
                      && draft.fields?.['cf-script'] === '停止后必须重新保存的权威文案';
                }""", timeout=10000)
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_history_restore_preserves_explicitly_collapsed_advanced_settings(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        director_history.create_conversation(
            conversation_id="conv-modern-collapsed-advanced",
            title="保留历史设置的折叠状态",
            snapshot={
                "form": {
                    "fields": {
                        "cf-director-brief": "历史文案目标",
                        "cf-script": "历史权威文案",
                        "cf-voice-mode": "upload",
                    },
                    "explicit_fields": ["voice_mode"],
                    "context": {"asset_refs": [], "narration_upload": "audio/voice.wav"},
                    "ui": {
                        "disclosures": {
                            "cf-input-context-details": False,
                            "cf-manual-options": False,
                        }
                    },
                }
            },
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.locator("#director-history-launch").click()
                page.locator("#cf-history-list button[data-conversation-id='conv-modern-collapsed-advanced']").click()
                page.wait_for_function(
                    "document.querySelector('#cf-history-list [data-history-action=\"restore\"]') !== null"
                )
                page.locator('#cf-history-list button[data-history-action="restore"]').click()
                page.wait_for_function("""() =>
                    document.getElementById('cf-script')?.value === '历史权威文案'
                    && document.getElementById('cf-voice-mode')?.value === 'upload'
                """, timeout=10000)
                assert not page.locator("#cf-input-context-details").evaluate("(node) => node.open")
                assert not page.locator("#cf-manual-options").evaluate("(node) => node.open")
                assert not page.locator("#cf-voice-upload-wrap").is_visible()
                assert page.evaluate("window._cfNarrationUpload") == "audio/voice.wav"
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_direct_project_entry_confirms_before_replacing_saved_draft(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        director_history.save_draft(
            "active",
            {
                "version": 1,
                "fields": {
                    "cf-director-brief": "必须保留的草稿目标",
                    "cf-script": "必须保留的草稿文案",
                },
                "explicit_fields": [],
                "context": {"asset_refs": [], "project_id": "project-from-project-menu"},
            },
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            brief_requests: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/director/brief", lambda route: (
                brief_requests.append(route.request.url) or route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"ok": True, "brief": "错误覆盖内容", "bundle": {}}),
                )
            ))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("""() => {
                    window.__projectEntryConfirmMessages = [];
                    window.confirm = message => {
                      window.__projectEntryConfirmMessages.push(String(message));
                      return false;
                    };
                }""")
                result = page.evaluate("() => window.openCreate('project-from-project-menu')")
                assert result is False
                assert page.locator("#cf-mask").is_visible()
                assert page.locator("#cf-director-brief").input_value() == "必须保留的草稿目标"
                assert page.locator("#cf-script").input_value() == "必须保留的草稿文案"
                assert brief_requests == []
                assert page.evaluate("window.__projectEntryConfirmMessages.length") == 1
                assert "替换当前未提交草稿" in page.evaluate("window.__projectEntryConfirmMessages[0]")
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)






def test_project_draft_with_changes_from_prefill_baseline_prompts_after_reload(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        director_history.save_draft(
            "active",
            {
                "version": 1,
                "fields": {
                    "cf-director-brief": "用户修改后的项目目标",
                    "cf-script": "用户修改后的权威文案",
                    "cf-platform": "xhs",
                    "cf-scope": "single",
                },
                "explicit_fields": [],
                "context": {
                    "asset_refs": [],
                    "project_id": "project-with-unsaved-draft",
                    "project_prefill_baseline": {
                        "fields": {
                            "cf-director-brief": "原项目目标",
                            "cf-script": "原项目文案",
                            "cf-platform": "xhs",
                            "cf-scope": "single",
                        },
                        "asset_refs": [],
                        "reference": None,
                        "article": None,
                        "repo": None,
                        "narration_upload": "",
                    },
                },
            },
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            brief_requests: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/director/brief", lambda route: (
                brief_requests.append(route.request.url) or route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"ok": True, "brief": "新项目目标", "bundle": {}}),
                )
            ))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("""() => {
                    window.__baselineConfirmMessages = [];
                    window.confirm = message => {
                      window.__baselineConfirmMessages.push(String(message));
                      return false;
                    };
                }""")
                result = page.evaluate("() => window.openCreate('different-project')")
                assert result is False
                assert page.locator("#cf-director-brief").input_value() == "用户修改后的项目目标"
                assert page.locator("#cf-script").input_value() == "用户修改后的权威文案"
                assert brief_requests == []
                assert page.evaluate("window.__baselineConfirmMessages.length") == 1
                assert "替换当前未提交草稿" in page.evaluate("window.__baselineConfirmMessages[0]")
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)






def test_failed_project_prefill_restores_the_previous_draft_and_persists_it(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        director_history.save_draft(
            "active",
            {
                "version": 1,
                "fields": {
                    "cf-director-brief": "切换失败后必须恢复的旧目标",
                    "cf-script": "切换失败后必须恢复的旧文案",
                    "cf-platform": "xhs",
                },
                "explicit_fields": [],
                "context": {
                    "asset_refs": [{"asset_id": "old-asset", "type": "video", "file": "old.mp4"}],
                    "project_id": "previous-project",
                    "project_prefill_baseline": {
                        "fields": {
                            "cf-director-brief": "原项目目标",
                            "cf-script": "原项目文案",
                            "cf-platform": "xhs",
                        },
                        "asset_refs": [],
                        "reference": None,
                        "article": None,
                        "repo": None,
                        "narration_upload": "",
                    },
                },
            },
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            brief_requests: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/director/brief", lambda route: (
                brief_requests.append(route.request.url) or route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"ok": False, "error": "项目服务暂时不可用"}),
                )
            ))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("""() => {
                    window.__failedProjectConfirmMessages = [];
                    window.confirm = message => {
                      window.__failedProjectConfirmMessages.push(String(message));
                      return true;
                    };
                }""")
                result = page.evaluate("() => window.openCreate('unavailable-project')")
                assert result["ok"] is False
                assert "暂时不可用" in result["error"]
                assert page.locator("#cf-director-brief").input_value() == "切换失败后必须恢复的旧目标"
                assert page.locator("#cf-script").input_value() == "切换失败后必须恢复的旧文案"
                assert page.locator("#cf-platform").input_value() == "xhs"
                assert page.evaluate("window.CF_DRAFT_CONTEXT.projectId") == "previous-project"
                active = page.evaluate("""async () => {
                    const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                    return (await response.json())?.draft?.data || {};
                }""")
                assert active["fields"]["cf-director-brief"] == "切换失败后必须恢复的旧目标"
                assert active["fields"]["cf-script"] == "切换失败后必须恢复的旧文案"
                assert active["context"]["project_id"] == "previous-project"
                assert active["context"]["asset_refs"][0]["asset_id"] == "old-asset"
                assert len(brief_requests) == 1
                assert page.locator("#cf-status").inner_text().find("已恢复切换前的草稿") >= 0
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_confirmed_project_switch_clears_old_dirty_markers_before_next_project(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            requests: list[str] = []
            confirms: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def project_brief(route):
                project_id = json.loads(route.request.post_data or "{}").get("project_id")
                requests.append(project_id)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "ok": True,
                        "brief": f"{project_id} 的目标",
                        "bundle": {
                            "script": {"text": f"{project_id} 的权威文案", "source": "user"},
                            "assets": {"asset_refs": []},
                            "references": [],
                            "articles": [],
                            "software_project": None,
                        },
                        "spec_settings": {},
                    }),
                )

            page.route("**/api/director/brief", project_brief)
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("openCreate()")
                page.locator("#cf-director-brief").fill("需要由项目 A 替换的旧草稿")
                page.evaluate("""() => {
                    window.confirm = message => {
                      window.__projectSwitchConfirms = (window.__projectSwitchConfirms || []);
                      window.__projectSwitchConfirms.push(String(message));
                      return true;
                    };
                }""")
                page.evaluate("() => window.openCreate('project-A')")
                page.wait_for_function("""() =>
                    document.getElementById('cf-director-brief')?.value === 'project-A 的目标'
                    && window.CF_DRAFT_CONTEXT?.projectId === 'project-A'
                    && window.CF_DRAFT_CONTEXT?.projectPrefillBaseline?.fields?.['cf-director-brief'] === 'project-A 的目标'
                """, timeout=10000)
                assert page.evaluate("window.__projectSwitchConfirms.length") == 1

                page.evaluate("""() => {
                    window.confirm = message => {
                      window.__projectSwitchConfirms.push(String(message));
                      return false;
                    };
                }""")
                page.evaluate("() => window.openCreate('project-B')")
                page.wait_for_function("""() =>
                    document.getElementById('cf-director-brief')?.value === 'project-B 的目标'
                    && window.CF_DRAFT_CONTEXT?.projectId === 'project-B'
                """, timeout=10000)
                assert page.evaluate("window.__projectSwitchConfirms.length") == 1
                assert requests == ["project-A", "project-B"]
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_cleared_submitted_draft_does_not_prompt_from_stale_project_context(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            confirm_messages: list[str] = []
            brief_requests: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/director/brief", lambda route: (
                brief_requests.append(route.request.url) or route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "ok": True,
                        "brief": "已打开目标项目",
                        "bundle": {
                            "script": {"text": "目标项目的权威文案", "source": "user"},
                            "assets": {"asset_refs": []},
                            "references": [],
                            "articles": [],
                            "software_project": {},
                        },
                        "spec_settings": {},
                    }),
                )
            ))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("openCreate()")
                page.locator("#cf-director-brief").fill("提交后已清理的旧草稿")
                page.evaluate("""() => {
                    window.CF_DRAFT_CONTEXT.projectId = 'completed-project';
                    window.CF.explicitFields = new Set(['voice_mode']);
                }""")
                cleared = page.evaluate("window.cfClearSavedDraft()")
                assert cleared["ok"] is True
                page.evaluate("""() => {
                    window.confirm = message => {
                      window.__staleContextConfirmMessages = (window.__staleContextConfirmMessages || []);
                      window.__staleContextConfirmMessages.push(String(message));
                      return true;
                    };
                }""")

                page.evaluate("() => window.openCreate('next-project-after-completion')")
                page.wait_for_function("""() =>
                    document.getElementById('cf-director-brief')?.value === '已打开目标项目'
                    && document.getElementById('cf-script')?.value === '目标项目的权威文案'
                """, timeout=10000)
                assert page.evaluate("window.__staleContextConfirmMessages || []") == []
                assert len(brief_requests) == 1
                active_draft = page.evaluate("""async () => {
                    const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                    return (await response.json())?.draft || null;
                }""")
                assert active_draft is not None
                assert active_draft["data"]["context"]["project_id"] == "next-project-after-completion"
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_open_associated_project_closes_history_drawer_before_create_panel(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        director_history.create_conversation(
            conversation_id="conv-associated-project-navigation",
            project_id="project-from-history",
            title="从历史打开项目",
            initial_message="继续编辑已有项目",
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.route("**/api/director/brief", lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "ok": True,
                        "brief": "从历史关联项目回填的创作目标",
                        "bundle": {
                            "script": {"text": "项目原有权威文案", "source": "user"},
                            "assets": {"asset_refs": []},
                            "references": [],
                            "articles": [],
                            "software_project": {},
                        },
                        "spec_settings": {},
                    }),
                ))
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.locator("#director-history-launch").click()
                page.locator("#cf-history-list button[data-conversation-id='conv-associated-project-navigation']").click()
                page.wait_for_function(
                    "document.querySelector('#cf-history-list [data-open-project=\"project-from-history\"]') !== null"
                )
                page.locator('#cf-history-list [data-open-project="project-from-history"]').click()
                page.wait_for_function("""() =>
                    document.getElementById('cf-history-drawer') === null
                    && document.getElementById('cf-mask')?.style.display === 'flex'
                    && document.getElementById('cf-director-brief')?.value === '从历史关联项目回填的创作目标'
                    && document.getElementById('cf-script')?.value === '项目原有权威文案'
                """, timeout=10000)
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_open_associated_project_preserves_active_draft_when_switch_is_cancelled(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        director_history.create_conversation(
            conversation_id="conv-project-switch-preserve-draft",
            project_id="project-must-not-open",
            title="打开项目时保护当前草稿",
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            brief_requests: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/director/brief", lambda route: (
                brief_requests.append(route.request.url) or route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"ok": True, "brief": "错误地覆盖了当前草稿", "bundle": {}}),
                )
            ))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("openCreate()")
                page.locator("#cf-director-brief").fill("必须保留的当前创作目标")
                page.locator("#cf-input-context-details summary").click()
                page.locator("#cf-script").fill("必须保留的当前权威文案")
                page.locator("#cf-history-inline-launch").click()
                page.locator("#cf-history-list button[data-conversation-id='conv-project-switch-preserve-draft']").click()
                page.wait_for_function(
                    "document.querySelector('#cf-history-list [data-open-project=\"project-must-not-open\"]') !== null"
                )
                page.evaluate("""() => {
                    window.__projectSwitchConfirmMessages = [];
                    window.confirm = message => {
                        window.__projectSwitchConfirmMessages.push(String(message));
                        return false;
                    };
                }""")
                page.locator('#cf-history-list [data-open-project="project-must-not-open"]').click()
                page.wait_for_function("window.__projectSwitchConfirmMessages.length === 1")
                assert page.locator("#cf-history-drawer").is_visible()
                assert page.locator("#cf-director-brief").input_value() == "必须保留的当前创作目标"
                assert page.locator("#cf-script").input_value() == "必须保留的当前权威文案"
                assert brief_requests == []
                assert "打开关联项目" in page.evaluate("window.__projectSwitchConfirmMessages[0]")
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_sfx_duplicate_labels_are_disambiguated_and_select_options_are_safe(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/sfx", lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps([
                    {"id": "sfx-camera-shutter-001", "label": "相机快门"},
                    {"id": "sfx-camera-shutter-soft", "label": "相机快门"},
                    {"id": "sfx-data-tech-001", "label": "数据科技"},
                ], ensure_ascii=False),
            ))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("openCreate()")
                page.wait_for_function("""() =>
                    [...document.querySelectorAll('#cf-sfx option')]
                      .filter(option => option.value.startsWith('sfx-')).length === 3
                """, timeout=10000)
                options = page.locator("#cf-sfx option").evaluate_all("""nodes =>
                    nodes.filter(option => option.value.startsWith('sfx-')).map(option => ({
                        value: option.value, label: option.textContent
                    }))
                """)
                assert {row["value"] for row in options} == {
                    "sfx-camera-shutter-001",
                    "sfx-camera-shutter-soft",
                    "sfx-data-tech-001",
                }
                labels = [row["label"] for row in options]
                assert len(labels) == len(set(labels)), labels
                assert any(label == "相机快门 · camera shutter 001" for label in labels), labels
                assert any(label == "相机快门 · camera shutter soft" for label in labels), labels

                unsafe_label = '<img src=x onerror="window.__sfxInjected=true">'
                page.evaluate("""label => {
                    window.__sfxInjected = false;
                    window._rebuildSelect('cf-sfx', [['safe-id', label]]);
                }""", unsafe_label)
                assert page.locator("#cf-sfx img").count() == 0
                assert page.locator("#cf-sfx option").first.text_content() == unsafe_label
                assert page.evaluate("window.__sfxInjected") is False
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_active_draft_with_only_explicit_settings_is_resumable_from_history(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("openCreate()")
                page.locator("#cf-input-context-details").wait_for(state="attached")
                page.locator("#cf-platform").select_option("xhs")
                page.wait_for_function("""async () => {
                    const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                    const payload = await response.json();
                    return payload?.draft?.data?.explicit_fields?.includes('platform');
                }""", timeout=10000)

                page.locator("#cf-history-inline-launch").click()
                draft_card = page.locator("#cf-history-draft")
                draft_card.locator('button[data-history-action="resume-draft"]').wait_for()
                assert "平台" in draft_card.inner_text()
                assert "已调整" in draft_card.inner_text()

                draft_card.locator('button[data-history-action="resume-draft"]').click()
                page.wait_for_function("document.getElementById('cf-history-drawer') === null")
                assert page.locator("#cf-mask").evaluate("(node) => node.style.display === 'flex'")
                assert page.locator("#cf-platform").input_value() == "xhs"
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_app_html_escape_prevents_dynamic_text_and_attribute_injection(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                result = page.evaluate("""() => {
                    const payload = String.fromCharCode(39) + '" autofocus onfocus="window.__escapeInjected=true"><img src=x onerror="window.__escapeInjected=true">&';
                    window.__escapeInjected = false;
                    const host = document.createElement('div');
                    host.innerHTML =
                      '<input id="escaped-attribute" value="' + esc(payload) + '">' +
                      '<div id="escaped-text">' + esc(payload) + '</div>';
                    document.body.appendChild(host);
                    return {
                      payload,
                      inputValue: host.querySelector('#escaped-attribute').value,
                      textContent: host.querySelector('#escaped-text').textContent,
                      injectedNodes: host.querySelectorAll('img').length,
                      escaped: esc(payload),
                      injected: window.__escapeInjected
                    };
                }""")
                assert result["inputValue"] == result["payload"]
                assert result["textContent"] == result["payload"]
                assert result["injectedNodes"] == 0
                assert result["injected"] is False
                assert "&quot;" in result["escaped"]
                assert "&#39;" in result["escaped"]
                assert "&lt;img" in result["escaped"]
                assert "&amp;" in result["escaped"]
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_history_restore_confirms_before_replacing_active_draft(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("openCreate()")
                page.locator("#cf-director-brief").fill("当前未提交的创作要求")
                page.locator("#cf-input-context-details summary").click()
                page.locator("#cf-script").fill("当前未提交的权威文案")
                page.wait_for_function("""async () => {
                    const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                    const data = (await response.json())?.draft?.data?.fields || {};
                    return data['cf-director-brief'] === '当前未提交的创作要求'
                      && data['cf-script'] === '当前未提交的权威文案';
                }""", timeout=10000)

                page.evaluate("""() => {
                    window._cfHistorySelected = {
                      conversation_id: 'conv-history-restore-protection',
                      snapshot: {form: {fields: {
                        'cf-director-brief': '历史对话中的创作要求',
                        'cf-script': '历史对话中的权威文案'
                      }}}
                    };
                    window.confirm = () => false;
                }""")
                page.evaluate("window.cfHistoryRestoreSnapshot('conv-history-restore-protection')")
                assert page.locator("#cf-director-brief").input_value() == "当前未提交的创作要求"
                assert page.locator("#cf-script").input_value() == "当前未提交的权威文案"

                # A recording/project-only draft must be protected too, even with blank text.
                page.locator("#cf-director-brief").fill("")
                page.locator("#cf-script").fill("")
                page.evaluate("""() => {
                    window.CF_DRAFT_CONTEXT.narrationUpload = "voice-upload.wav";
                    window.CF_DRAFT_CONTEXT.projectId = "current-project";
                    window.CF.explicitFields.clear();
                }""")
                page.evaluate("window.cfPersistCreateDraftNow()")
                page.wait_for_function("""async () => {
                    const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                    const context = (await response.json())?.draft?.data?.context || {};
                    return context.narration_upload === "voice-upload.wav"
                      && context.project_id === "current-project";
                }""", timeout=10000)
                page.evaluate("window.confirm = () => false")
                page.evaluate("window.cfHistoryRestoreSnapshot('conv-history-restore-protection')")
                assert page.locator("#cf-director-brief").input_value() == ""
                assert page.locator("#cf-script").input_value() == ""
                assert "当前草稿保持不变" in (page.locator("#cf-draft-status").text_content() or "")

                # Unsubmitted article and repository inputs are form data too; protect them
                # even before import/scan has produced a context object.
                page.evaluate("""() => {
                    window.CF_DRAFT_CONTEXT.narrationUpload = "";
                    window.CF_DRAFT_CONTEXT.projectId = "";
                    window.CF.explicitFields.clear();
                }""")
                page.locator("#cf-article-url").fill("https://example.test/unsubmitted-article")
                page.locator("#cf-repo-root").fill("D:/Projects/unsubmitted")
                page.evaluate("window.cfPersistCreateDraftNow()")
                page.wait_for_function("""async () => {
                    const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                    const fields = (await response.json())?.draft?.data?.fields || {};
                    return fields['cf-article-url'] === "https://example.test/unsubmitted-article"
                      && fields['cf-repo-root'] === "D:/Projects/unsubmitted";
                }""", timeout=10000)
                page.evaluate("window.confirm = () => false")
                page.evaluate("window.cfHistoryRestoreSnapshot('conv-history-restore-protection')")
                assert page.locator("#cf-article-url").input_value() == "https://example.test/unsubmitted-article"
                assert page.locator("#cf-repo-root").input_value() == "D:/Projects/unsubmitted"
                assert "当前草稿保持不变" in (page.locator("#cf-draft-status").text_content() or "")

                page.evaluate("window.confirm = () => true")
                page.evaluate("window.cfHistoryRestoreSnapshot('conv-history-restore-protection')")
                assert page.locator("#cf-director-brief").input_value() == "历史对话中的创作要求"
                assert page.locator("#cf-script").input_value() == "历史对话中的权威文案"
                assert page.locator("#cf-article-url").input_value() == ""
                assert page.locator("#cf-repo-root").input_value() == ""
                assert page.locator("#cf-article-result").inner_text() == ""
                assert page.locator("#cf-repo-result").inner_text() == ""
                assert "原记录不会被覆盖" in (page.locator("#cf-draft-status").text_content() or "")
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_restored_settings_only_draft_prompts_before_history_overwrite(tmp_path, monkeypatch):
    server, thread = _start_server(tmp_path, monkeypatch)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                saved = page.evaluate("""async () => {
                    const response = await fetch('/api/director/drafts/active', {
                      method: 'POST',
                      headers: {'Content-Type':'application/json'},
                      body: JSON.stringify({data: {
                        version: 1,
                        fields: {
                          'cf-visual-source': 'local',
                          'cf-stop-at': 'audio',
                          'cf-fps': '60',
                          'cf-confirm': 'direct',
                          'cf-match-materials': false
                        },
                        explicit_fields: [],
                        context: {asset_refs: [], reference: null, article: null, repo: null,
                                  narration_upload: '', project_id: ''}
                      }})
                    });
                    return await response.json();
                }""")
                assert saved["ok"] is True

                page.evaluate("openCreate()")
                page.wait_for_function("""() => {
                    return document.getElementById('cf-visual-source')?.value === 'local'
                      && document.getElementById('cf-stop-at')?.value === 'audio'
                      && document.getElementById('cf-fps')?.value === '60'
                      && document.getElementById('cf-confirm')?.value === 'direct'
                      && document.getElementById('cf-match-materials')?.checked === false;
                }""", timeout=10000)

                page.evaluate("""() => {
                    window._cfHistorySelected = {
                      conversation_id: 'conv-history-settings-only',
                      snapshot: {form: {fields: {
                        'cf-director-brief': '历史记录的创作目标'
                      }}}
                    };
                    window.__historyRestoreConfirmMessages = [];
                    window.confirm = message => {
                        window.__historyRestoreConfirmMessages.push(String(message));
                        return false;
                    };
                }""")
                page.evaluate("window.cfHistoryRestoreSnapshot('conv-history-settings-only')")
                assert page.evaluate("window.__historyRestoreConfirmMessages.length") == 1
                assert page.locator("#cf-visual-source").input_value() == "local"
                assert page.locator("#cf-stop-at").input_value() == "audio"
                assert page.locator("#cf-fps").input_value() == "60"
                assert page.locator("#cf-confirm").input_value() == "direct"
                assert page.locator("#cf-match-materials").is_checked() is False
                assert "当前草稿保持不变" in (page.locator("#cf-draft-status").text_content() or "")
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
