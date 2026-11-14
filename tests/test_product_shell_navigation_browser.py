from __future__ import annotations

import importlib
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright

from video_director import config, model_config


def test_top_level_navigation_keeps_one_active_pane_and_global_create_action(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)
    storage.mkdir(parents=True)
    model_config._cache = None

    import webui.server as web_server

    web_server = importlib.reload(web_server)
    monkeypatch.setattr(web_server, "COMPOSER_ROOT", storage)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            try:
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="domcontentloaded",
                )
                assert response and response.status == 200
                initial_url = page.url

                for target in ("home", "editor", "settings", "home"):
                    page.locator(f"#tab-{target}").click()
                    page.wait_for_function(
                        "(target) => window.UI?.route === target",
                        arg=target,
                    )
                    page.wait_for_function(
                        """(target) => {
                          const panes = ['home', 'editor', 'settings'];
                          const visible = panes.filter((name) => {
                            const node = document.getElementById('pane-' + name);
                            return node && getComputedStyle(node).display !== 'none';
                          });
                          return visible.length === 1 && visible[0] === target;
                        }""",
                        arg=target,
                    )

                    state = page.evaluate("""() => ({
                      route: window.UI.route,
                      activeTabs: ['home', 'editor', 'settings'].filter((name) =>
                        document.getElementById('tab-' + name)?.classList.contains('tab-active')
                      ),
                      currentTabs: ['home', 'editor', 'settings'].filter((name) =>
                        document.getElementById('tab-' + name)?.getAttribute('aria-current') === 'page'
                      ),
                      hiddenPanes: ['home', 'editor', 'settings'].filter((name) =>
                        document.getElementById('pane-' + name)?.getAttribute('aria-hidden') === 'true'
                      ),
                      visiblePanes: ['home', 'editor', 'settings'].filter((name) => {
                        const node = document.getElementById('pane-' + name);
                        return node && getComputedStyle(node).display !== 'none';
                      }),
                      createVisible: [...document.querySelectorAll('.topbar button')]
                        .some((button) => button.textContent.includes('新建项目') &&
                          getComputedStyle(button).display !== 'none'),
                      pathname: location.pathname,
                      search: location.search,
                      hash: location.hash,
                    })""")
                    assert state["route"] == target
                    assert state["activeTabs"] == [target]
                    assert state["currentTabs"] == [target]
                    assert state["hiddenPanes"] == [name for name in ("home", "editor", "settings") if name != target]
                    assert state["visiblePanes"] == [target]
                    assert state["createVisible"]
                    assert (state["pathname"], state["search"], state["hash"]) == ("/", "", "")
                    assert page.url == initial_url

                # The three top-level spaces are reachable in native tab order,
                # expose the current route to assistive technology, and activate by Enter.
                page.locator("#tab-editor").focus()
                page.keyboard.press("Shift+Tab")
                assert page.evaluate("document.activeElement?.id") == "tab-home"
                assert page.evaluate("document.activeElement?.matches(':focus-visible')") is True
                page.keyboard.press("Tab")
                assert page.evaluate("document.activeElement?.id") == "tab-editor"
                page.keyboard.press("Enter")
                page.wait_for_function("window.UI?.route === 'editor'")
                assert page.locator("#tab-editor").get_attribute("aria-current") == "page"
                assert page.locator("#pane-home").get_attribute("aria-hidden") == "true"
                assert page.locator("#pane-editor").get_attribute("aria-hidden") == "false"
                page.keyboard.press("Tab")
                assert page.evaluate("document.activeElement?.id") == "tab-settings"
                page.keyboard.press("Enter")
                page.wait_for_function("window.UI?.route === 'settings'")
                assert page.locator("#tab-settings").get_attribute("aria-current") == "page"
                assert page.locator("#pane-editor").get_attribute("aria-hidden") == "true"
                assert page.locator("#pane-settings").get_attribute("aria-hidden") == "false"

                assert not page_errors, page_errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        model_config._cache = None

def test_home_create_resume_then_settings_round_trip_in_one_browser_journey(tmp_path, monkeypatch):
    """Cover the 07D integrated journey, not only the isolated W4/W5 lanes."""
    import json

    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)
    storage.mkdir(parents=True)
    config_path = storage / "model_config.json"
    config_path.write_text(json.dumps({
        "llm": {
            "api_key": "journey-local-llm-secret",
            "base_url": "https://llm.example/v1",
            "model": "journey-model",
        },
        "image": {
            "api_key": "journey-local-image-secret",
            "base_url": "https://image.example/v1",
            "model": "journey-image",
        },
    }), encoding="utf-8")
    config_before_ui = config_path.read_bytes()
    model_config._cache = None

    import webui.server as web_server

    web_server = importlib.reload(web_server)
    monkeypatch.setattr(web_server, "COMPOSER_ROOT", storage)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="domcontentloaded",
                )
                assert response and response.status == 200
                assert page.locator("#pane-home").is_visible()

                # Use the global product action rather than calling openCreate()
                # directly: this verifies the home-to-create entry point.
                create_action = page.locator(".topbar button").filter(has_text="新建项目")
                assert create_action.count() == 1
                create_action.click()
                page.locator("#cf-form").wait_for(state="visible")
                page.locator("#cf-project-name").fill("跨流程验收项目")
                page.locator("#cf-project-type").fill("知识科普")
                page.locator("#cf-director-brief").fill("验证首页创建、草稿恢复和设置流程串联。")

                page.wait_for_function("""async () => {
                  const response = await fetch('/api/director/drafts/active', {cache:'no-store'});
                  const result = await response.json();
                  const fields = result?.draft?.data?.fields || {};
                  return fields['cf-project-name'] === '跨流程验收项目'
                    && fields['cf-project-type'] === '知识科普'
                    && fields['cf-director-brief'] === '验证首页创建、草稿恢复和设置流程串联。';
                }""", timeout=10000)
                page.locator('#cf-form > div:first-child button[onclick="closeCreate()"]').click()
                page.wait_for_function("document.getElementById('cf-mask')?.style.display === 'none'")

                # A browser reload is the real recovery boundary for the draft.
                page.reload(wait_until="domcontentloaded")
                page.locator(".topbar button").filter(has_text="新建项目").click()
                page.locator("#cf-form").wait_for(state="visible")
                page.wait_for_function("""() =>
                  document.getElementById('cf-project-name')?.value === '跨流程验收项目'
                  && document.getElementById('cf-project-type')?.value === '知识科普'
                  && document.getElementById('cf-director-brief')?.value === '验证首页创建、草稿恢复和设置流程串联。'
                """, timeout=10000)
                page.locator('#cf-form > div:first-child button[onclick="closeCreate()"]').click()
                page.wait_for_function("document.getElementById('cf-mask')?.style.display === 'none'")

                # Continue in the same browser profile into settings and prove
                # that viewing/saving/importing/exporting never loses local keys.
                page.evaluate("switchTab('settings')")
                page.wait_for_function("Array.isArray(window.MODEL_ROLES) && Array.isArray(window.RENDER_ROLES)")
                assert page.locator("#pane-settings").get_by_text("AI 模型").is_visible()
                assert page.locator("#pane-settings").get_by_text("语音与配音").is_visible()
                assert page.locator("#pane-settings").get_by_text("素材来源", exact=True).is_visible()
                assert page.locator("#pane-settings").get_by_text("渲染与输出服务").is_visible()
                assert config_path.read_bytes() == config_before_ui
                assert "journey-local-llm-secret" not in page.locator("#pane-settings").inner_text()

                # audit §3：API 字段默认折叠，展开后才可编辑（渐进披露）。
                page.locator("#pane-settings details", has_text="AI 模型").first.evaluate(
                    "(node) => { node.open = true; }")
                page.locator("#mc-llm-model").fill("journey-model-updated")
                page.locator("#pane-settings button").filter(has_text="保存模型配置").click()
                page.wait_for_function(
                    "document.getElementById('llm-save-status')?.textContent === '已保存并立即生效'"
                )
                saved = json.loads(config_path.read_text(encoding="utf-8"))
                assert saved["llm"]["model"] == "journey-model-updated"
                assert saved["llm"]["api_key"] == "journey-local-llm-secret"
                assert saved["image"]["api_key"] == "journey-local-image-secret"

                config_section = page.locator("#pane-settings details.settings-section").filter(has_text="配置管理")
                config_section.locator("summary").click()
                with page.expect_download() as download_info:
                    page.get_by_role("button", name="导出设置").click()
                download_path = tmp_path / "journey-settings-export.json"
                download_info.value.save_as(download_path)
                exported_text = download_path.read_text(encoding="utf-8")
                assert "journey-local-llm-secret" not in exported_text
                assert "journey-local-image-secret" not in exported_text

                partial_import = {
                    "ok": True,
                    "version": 1,
                    "models": [{
                        "role": "image",
                        "base_url": "https://image-imported.example/v2",
                        "model": "journey-image-imported",
                    }],
                }
                page.locator("#settings-import-file").set_input_files({
                    "name": "journey-partial-settings.json",
                    "mimeType": "application/json",
                    "buffer": json.dumps(partial_import).encode("utf-8"),
                })
                page.wait_for_function("""() =>
                  (document.getElementById('settings-config-status')?.textContent || '')
                    .includes('已导入 1 个角色')
                """, timeout=10000)
                saved = json.loads(config_path.read_text(encoding="utf-8"))
                assert saved["llm"]["api_key"] == "journey-local-llm-secret"
                assert saved["image"]["api_key"] == "journey-local-image-secret"
                assert saved["image"]["base_url"] == "https://image-imported.example/v2"
                assert saved["image"]["model"] == "journey-image-imported"

                for target in ("editor", "home"):
                    page.locator(f"#tab-{target}").click()
                    page.wait_for_function("(target) => window.UI?.route === target", arg=target)
                    visible = page.evaluate("""() =>
                      ['home', 'editor', 'settings'].filter((name) => {
                        const node = document.getElementById('pane-' + name);
                        return node && getComputedStyle(node).display !== 'none';
                      })
                    """)
                    assert visible == [target]

                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        model_config._cache = None

