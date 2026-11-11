"""Real-browser DOM acceptance for Shotcraft's registered template picker.

This is deliberately an interaction test, not a static source/selector check:
it loads the real registry API, filters real candidates, changes selection,
checks the disabled preflight actions, and closes the dialog without creating a
project or rendering a sample.
"""
from __future__ import annotations

import importlib
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright

from video_director import config, model_config


def test_template_gallery_uses_real_registry_filters_and_cancels_cleanly(tmp_path, monkeypatch):
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
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            try:
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="networkidle",
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.templateGalleryOpen === 'function'")

                opened = page.evaluate("() => window.templateGalleryOpen()")
                assert opened is True
                page.locator("#tg-mask").wait_for(state="visible")
                page.wait_for_function(
                    "() => Array.isArray(window.TG?.templates) && window.TG.templates.length > 0"
                )

                registry_state = page.evaluate("""() => ({
                  templates: window.TG.templates.map(item => ({
                    id: item.id,
                    version: item.version,
                    category: item.category || "",
                    ratios: item.supported_ratios || [],
                  })),
                  visible: window.TG.visible.map(item => item.id + "@" + item.version),
                  cards: [...document.querySelectorAll("#tg-list .tg-template-card")].length,
                  selected: document.querySelector("#tg-list .tg-template-card[aria-pressed='true']")
                    ?.textContent || "",
                  renderDisabled: document.getElementById("tg-render")?.disabled,
                  instantiateDisabled: document.getElementById("tg-instantiate")?.disabled,
                })""")
                assert registry_state["templates"]
                assert registry_state["cards"] == len(registry_state["visible"])
                assert registry_state["selected"]
                # No user asset has been supplied: neither sample rendering nor
                # project instantiation may be enabled by registry metadata alone.
                assert registry_state["renderDisabled"] is True
                assert registry_state["instantiateDisabled"] is True

                categories = sorted({item["category"] for item in registry_state["templates"] if item["category"]})
                if categories:
                    category = categories[0]
                    page.locator("#tg-category").select_option(label=category)
                    page.wait_for_function(
                        "(category) => window.TG.visible.length > 0 && "
                        "window.TG.visible.every(item => item.category === category)",
                        arg=category,
                    )
                    assert page.locator("#tg-count").inner_text().startswith("显示 ")

                ratios = page.evaluate("() => [...new Set(window.TG.visible.flatMap(item => item.supported_ratios || []))].sort()")
                if ratios:
                    ratio = ratios[0]
                    page.locator("#tg-ratio-filter").select_option(label=ratio)
                    page.wait_for_function(
                        "(ratio) => window.TG.visible.every(item => (item.supported_ratios || []).includes(ratio))",
                        arg=ratio,
                    )
                    filtered = page.evaluate(
                        "() => [...document.querySelectorAll('#tg-list .tg-template-card')]"
                        ".map(node => node.textContent)"
                    )
                    assert len(filtered) == page.locator("#tg-list .tg-template-card").count()

                cards = page.locator("#tg-list .tg-template-card")
                if cards.count() > 1:
                    cards.nth(1).focus()
                    page.keyboard.press("Enter")
                    assert cards.nth(1).get_attribute("aria-pressed") == "true"
                else:
                    assert cards.first.get_attribute("aria-pressed") == "true"

                page.locator("#tg-mask .tg-header button").click()
                page.locator("#tg-mask").wait_for(state="detached")
                assert not page_errors, page_errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        model_config._cache = None
