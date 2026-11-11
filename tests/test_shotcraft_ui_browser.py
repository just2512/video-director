"""Real-browser DOM acceptance for Shotcraft's registered template picker.

This is deliberately an interaction test, not a static source/selector check:
it loads the real registry API, filters real candidates, changes selection,
checks the disabled preflight actions, and closes the dialog without creating a
project or rendering a sample.
"""
from __future__ import annotations

import importlib
import json
import threading
import time
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

def test_motion_gallery_uses_registry_capabilities_and_applies_only_supported_motion(tmp_path, monkeypatch):
    """Motion Gallery reads real Registry data, previews channels and applies through Editor Command."""
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)
    storage.mkdir(parents=True)
    model_config._cache = None

    project_id = "shotcraft-motion-gallery-browser"
    project_root = storage / project_id
    plans = project_root / "plans"
    assets = project_root / "assets"
    plans.mkdir(parents=True)
    assets.mkdir(parents=True)
    (assets / "still.png").write_bytes(b"test asset bytes; no image decoding required")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "motion-gallery-image", "file": "assets/still.png", "type": "image", "source": "user"}
    ]), encoding="utf-8")
    composition_path = plans / "composition.json"
    composition_path.write_text(json.dumps({
        "project": {"title": project_id, "width": 1080, "height": 1920, "fps": 30, "duration": 4.0},
        "assets": [
            {"id": "motion-gallery-image", "file": "assets/still.png", "type": "image"}
        ],
        "scenes": [{
            "id": "motion-gallery-scene", "start": 0.0, "duration": 4.0,
            "text": "动效库真实应用验收",
            "layers": [{
                "id": "motion-gallery-layer", "type": "image",
                "asset_id": "motion-gallery-image", "start": 0.0, "duration": 4.0,
            }],
        }],
        "audio": [], "watermarks": [],
    }, ensure_ascii=False), encoding="utf-8")

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
                    wait_until="domcontentloaded",
                )
                assert response and response.status == 200
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function(
                    "() => { const select=document.querySelector('#editor-project'); return !!select && select.options.length>0; }"
                )
                page.evaluate("""pid => {
                  const select = document.querySelector('#editor-project');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                  select.dispatchEvent(new Event('change', {bubbles:true}));
                }""", project_id)
                page.wait_for_function("(pid) => window.ED && ED.pid === pid", arg=project_id, timeout=15000)
                page.locator("#shotbook-details > summary").click()
                page.wait_for_function(
                    "() => typeof window.shotbookLoad === 'function' && "
                    "document.querySelector('#shotbook-list .sb-shot') !== null",
                    timeout=15000,
                )
                page.wait_for_function(
                    "() => document.querySelector('#shotbook-list [data-shotbook-action=\"motion-gallery\"]') !== null"
                )

                registry_count = page.evaluate("""async () => {
                  const response = await fetch('/api/registry/motions');
                  const payload = await response.json();
                  return Array.isArray(payload) ? payload.length : -1;
                }""")
                assert registry_count > 0

                export_before = page.evaluate("() => JSON.stringify(window.shotbookExportForFilmDirector())")
                composition_before = json.loads(composition_path.read_text(encoding="utf-8"))

                page.locator('#shotbook-list [data-shotbook-action="motion-gallery"]').click()
                page.locator("#sb-motion-gallery-mask").wait_for(state="visible")
                page.wait_for_function(
                    "() => document.querySelectorAll('#sb-motion-gallery-list .sb-mg-card').length > 0"
                )
                assert page.locator("#sb-motion-gallery-list .sb-mg-card").count() == registry_count

                # A text-only motion is visible but cannot be applied to the selected image layer.
                page.locator("#sb-motion-gallery-search").fill("motion.word-relay")
                page.wait_for_function(
                    "() => document.querySelectorAll('#sb-motion-gallery-list .sb-mg-card').length === 1"
                )
                page.locator('[data-sb-mg-select="motion.word-relay"]').click()
                assert page.locator("#sb-motion-gallery-apply").is_disabled()

                page.locator("#sb-motion-gallery-mask [data-sb-mg-close]").last.click()
                page.locator("#sb-motion-gallery-mask").wait_for(state="detached")
                export_after_cancel = page.evaluate("() => JSON.stringify(window.shotbookExportForFilmDirector())")
                composition_after_cancel = json.loads(composition_path.read_text(encoding="utf-8"))
                assert export_after_cancel == export_before
                assert composition_after_cancel == composition_before

                # A renderer-supported image motion exposes a real Registry-channel preview and applies
                # through the existing Editor Command API, changing the CompositionIR revision.
                page.locator('#shotbook-list [data-shotbook-action="motion-gallery"]').click()
                page.locator("#sb-motion-gallery-mask").wait_for(state="visible")
                page.locator("#sb-motion-gallery-search").fill("motion.fade-in")
                page.wait_for_function(
                    "() => document.querySelectorAll('#sb-motion-gallery-list .sb-mg-card').length === 1"
                )
                page.locator('[data-sb-mg-select="motion.fade-in"]').click()
                assert page.locator("#sb-motion-gallery-apply").is_enabled()
                preview_animation = page.locator("#sb-motion-gallery-preview-object").evaluate(
                    "(node) => node.style.animation"
                )
                assert "sb-motion-preview-motion-fade-in" in preview_animation
                page.locator("#sb-motion-gallery-apply").click()

                applied = None
                for _ in range(60):
                    current = json.loads(composition_path.read_text(encoding="utf-8"))
                    layer = current["scenes"][0]["layers"][0]
                    if layer.get("motion_recipe_id") == "motion.fade-in":
                        applied = current
                        break
                    time.sleep(0.25)
                assert applied is not None, json.loads(composition_path.read_text(encoding="utf-8"))
                assert applied["scenes"][0]["layers"][0]["motion_version"] == "1.0.0"
                assert not page_errors, page_errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        model_config._cache = None

