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
