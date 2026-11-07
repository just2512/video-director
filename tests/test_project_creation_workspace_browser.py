from __future__ import annotations

import importlib
import json
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright

from video_director import config, model_config


def test_project_creation_uses_full_workspace_without_touching_local_api_keys(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)
    storage.mkdir(parents=True)
    config_path = storage / "model_config.json"
    config_path.write_text(json.dumps({
        "llm": {"api_key": "fixture-local-llm-secret", "base_url": "https://llm.example/v1", "model": "fixture-model"},
        "image": {"api_key": "fixture-local-image-secret", "base_url": "https://image.example/v1", "model": "fixture-image"},
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
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            try:
                response = page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                assert response and response.status == 200
                page.evaluate("openCreate()")
                page.locator("#cf-form").wait_for(state="visible")
                geometry = page.evaluate("""() => {
                  const mask = document.getElementById('cf-mask');
                  const panel = document.getElementById('cf-panel');
                  const m = mask.getBoundingClientRect(), p = panel.getBoundingClientRect();
                  return {maskWidth:m.width,maskHeight:m.height,panelWidth:p.width,panelHeight:p.height,
                    viewportWidth:innerWidth,viewportHeight:innerHeight,
                    heading:panel.querySelector('h1')?.textContent || ''};
                }""")
                assert geometry["maskWidth"] == geometry["viewportWidth"]
                assert geometry["maskHeight"] == geometry["viewportHeight"]
                assert geometry["panelWidth"] == geometry["viewportWidth"]
                assert geometry["panelHeight"] == geometry["viewportHeight"]
                assert "创建视频项目" in geometry["heading"]
                assert config_path.read_bytes() == before

                page.evaluate("closeCreate()")
                assert config_path.read_bytes() == before
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        model_config._cache = None
