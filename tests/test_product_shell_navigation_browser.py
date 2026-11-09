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
                    wait_until="networkidle",
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
                    assert state["visiblePanes"] == [target]
                    assert state["createVisible"]
                    assert (state["pathname"], state["search"], state["hash"]) == ("/", "", "")
                    assert page.url == initial_url

                assert not page_errors, page_errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        model_config._cache = None
