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
                page.locator("#cf-director-brief").fill("持久化测试：保留我的创作目标")
                page.locator("#cf-input-context-details summary").click()
                page.locator("#cf-script").fill("持久化测试：保留我的权威文案")
                page.locator("#cf-platform").select_option("xhs")

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
                page.mouse.click(8, 8)
                assert page.locator("#cf-mask").evaluate("(el) => el.style.display") == "flex"
                assert page.locator("#cf-director-brief").input_value() == "持久化测试：保留我的创作目标"

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

                page.locator('#cf-quick-actions button[onclick="createEnhance()"]').click()
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
                project_id = (route.request.post_data_json or {}).get("project_id")
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
                else:
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
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

            page.route("**/api/director/brief", project_brief)
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
            assert page.locator('#cf-style-presets [data-studio-style="knowledge-explainer"]').evaluate("(node) => node.classList.contains('tab-active')")

            # Reuse the mounted dialog for a different project with no optional data.
            page.evaluate("openCreate('project-without-context')")
            page.wait_for_function("""() =>
                document.getElementById('cf-director-brief')?.value === '项目 B 的创作要求'
                && document.getElementById('cf-script')?.value === ''
                && document.getElementById('cf-article-url')?.value === ''
                && document.getElementById('cf-assets-summary')?.textContent === '未添加本地素材'
                && document.getElementById('cf-input-context-details')?.open === false
                && window.CF_DRAFT_CONTEXT?.assetRefs?.length === 0
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
            try:
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                page.evaluate("openCreate()")
                page.wait_for_function("!!window.CF_DEFAULTS_READY")

                page.evaluate("""() => {
                  const originalPost=window.apiPost.bind(window);
                  window.__releaseOldProject=null;
                  window.apiPost=async (path,payload) => {
                    if(path==='/api/director/brief'&&payload?.project_id==='project-slow'){
                      return await new Promise(resolve=>{
                        window.__releaseOldProject=()=>resolve({
                          ok:true,brief:'旧项目 A',
                          bundle:{
                            script:{text:'旧项目 A 文案'},
                            assets:{asset_refs:[{asset_id:'old-asset',type:'video',file:'old.mp4'}]},
                            references:[],articles:[],software_project:null
                          },
                          spec_settings:{platform:'xhs',duration:60}
                        });
                      });
                    }
                    if(path==='/api/director/brief'&&payload?.project_id==='project-fast'){
                      return {
                        ok:true,brief:'新项目 B',
                        bundle:{
                          script:{text:''},
                          assets:{asset_refs:[]},
                          references:[],articles:[],software_project:null
                        },
                        spec_settings:{}
                      };
                    }
                    return originalPost(path,payload);
                  };
                  const originalGet=window.apiGet.bind(window);
                  window.apiGet=async path=>{
                    if(path==='/api/project_detail?project_id=project-fast')return {error:'no legacy script'};
                    return originalGet(path);
                  };
                  window.__slowProjectPromise=window.openCreate('project-slow');
                }""")
                page.wait_for_function("typeof window.__releaseOldProject === 'function'", timeout=10000)
                page.evaluate("openCreate('project-fast')")

                page.wait_for_function("""() =>
                  document.getElementById('cf-director-brief')?.value === '新项目 B'
                  && document.getElementById('cf-script')?.value === ''
                  && window.CF_DRAFT_CONTEXT?.projectId === 'project-fast'
                """, timeout=10000)
                assert page.locator("#cf-input-context-details").evaluate("(node) => !node.open")

                page.evaluate("window.__releaseOldProject()")
                page.evaluate("async () => await window.__slowProjectPromise")
                page.wait_for_timeout(250)
                assert page.locator("#cf-director-brief").input_value() == "新项目 B"
                assert page.locator("#cf-script").input_value() == ""
                assert page.evaluate("window.CF_DRAFT_CONTEXT.projectId") == "project-fast"

                saved = page.evaluate("""async () => {
                  const response=await fetch('/api/director/drafts/active',{cache:'no-store'});
                  const result=await response.json();
                  return result?.draft?.data || {};
                }""")
                assert saved.get("context",{}).get("project_id") == "project-fast"
                assert saved.get("fields",{}).get("cf-director-brief") == "新项目 B"
                assert not errors
            finally:
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
                page.locator('#cf-history-list button[data-history-action="restore"]').click()

                page.locator("#cf-form").wait_for(state="visible")
                page.wait_for_function("""() =>
                  document.getElementById('cf-director-brief')?.value === '从历史恢复的创作目标'
                  && document.getElementById('cf-script')?.value === '从历史恢复的权威文案'
                """, timeout=10000)
                assert page.locator("#cf-history-drawer").is_visible()
                assert page.locator("#cf-input-context-details").evaluate("(node) => node.open")
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
