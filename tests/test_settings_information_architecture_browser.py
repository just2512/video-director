from __future__ import annotations

import importlib
import json
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright

from video_director import config, model_config


def test_settings_categories_keep_local_credentials_read_only_until_explicit_save(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)
    storage.mkdir(parents=True)
    config_path = storage / "model_config.json"
    config_path.write_text(json.dumps({
        "llm": {"api_key": "fixture-local-llm-secret", "base_url": "https://llm.example/v1", "model": "fixture-model"},
        "hyperframes": {"api_key": "fixture-local-render-secret", "base_url": "https://render.example/v1", "model": "render-model"},
        "stock_pexels": {"api_key": "fixture-local-stock-secret", "base_url": "https://api.pexels.com", "model": "videos/search"},
    }), encoding="utf-8")
    before = config_path.read_bytes()
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
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                response = page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                assert response and response.status == 200
                page.evaluate("switchTab('settings')")
                page.wait_for_function("Array.isArray(window.MODEL_ROLES) && Array.isArray(window.RENDER_ROLES)")
                assert page.locator("#pane-settings").get_by_text("AI 模型").is_visible()
                assert page.locator("#pane-settings").get_by_text("语音与配音").is_visible()
                assert page.locator("#pane-settings").get_by_text("素材来源", exact=True).is_visible()
                assert page.locator("#pane-settings").get_by_text("渲染与输出服务").is_visible()
                assert page.locator("#render-roles #mc-hyperframes-key").count() == 1
                assert page.locator("#model-roles #mc-hyperframes-key").count() == 0
                assert page.locator("#mc-llm-key").input_value() == "（已保存）"
                assert "fixture-local-llm-secret" not in page.locator("#pane-settings").inner_text()
                assert config_path.read_bytes() == before

                page.locator("#mc-llm-model").fill("fixture-model-updated")
                page.locator("#pane-settings button[onclick="saveModelConfig('models')"]").click()
                page.wait_for_function("document.getElementById('llm-save-status')?.textContent === '已保存并立即生效'")
                saved = json.loads(config_path.read_text(encoding="utf-8"))
                assert saved["llm"]["model"] == "fixture-model-updated"
                assert saved["llm"]["api_key"] == "fixture-local-llm-secret"
                assert saved["hyperframes"]["api_key"] == "fixture-local-render-secret"
                assert saved["stock_pexels"]["api_key"] == "fixture-local-stock-secret"
                assert saved["hyperframes"]["model"] == "render-model"
                assert saved["stock_pexels"]["model"] == "videos/search"
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        model_config._cache = None
