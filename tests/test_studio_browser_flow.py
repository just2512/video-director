"""Browser regression through the shipped WebUI: Studio controls, shared workspace,
Shotbook per-shot navigation, and the server-side final-render confirmation gate.
"""
from __future__ import annotations

import base64
import importlib
import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from video_director.models import (
    AssetRef, CompositionIR, Layer, LayerType, ProjectSpec, SceneIR,
)
from video_director import editor, store, shotbook


def _test_composition(media_file: Path) -> CompositionIR:
    layer1 = Layer(type=LayerType.image, asset_id="e2e-still", start=0, duration=1.0)
    layer2 = Layer(type=LayerType.image, asset_id="e2e-still", start=0, duration=1.0)
    recipe_rows = shotbook._recipes()
    recipe = recipe_rows[0]["id"] if recipe_rows else None
    scene1 = SceneIR(id="S001", start=0, duration=1.0, text="第一镜用于工作台定位", layers=[layer1],
                     origin_recipe_id=recipe)
    scene2 = SceneIR(id="S002", start=1.0, duration=1.0, text="第二镜测试逐镜预览", layers=[layer2],
                     origin_recipe_id=recipe)
    asset = AssetRef(
        id="e2e-still", type="image", file=str(media_file), width=32, height=32,
        source="uploaded", rights_status="CLEAR",
        provenance={"source": "Playwright regression fixture"},
    )
    return CompositionIR(
        project=ProjectSpec(width=160, height=90, fps=10, duration=2.0),
        scenes=[scene1, scene2], assets=[asset],
    )


def test_studio_to_timeline_browser_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    results_dir = Path(os.environ.get("VIDEO_DIRECTOR_TEST_RESULTS", str(tmp_path / "test-results")))
    results_dir.mkdir(parents=True, exist_ok=True)
    trace_path = results_dir / "studio-workspace-failure-trace.zip"
    screenshot_path = results_dir / "studio-workspace-failure.png"

    # Import after setting the per-test storage root: the HTTP handler uses COMPOSER_ROOT.
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = Path(os.environ["VIDEO_DIRECTOR_STORAGE_ROOT"])

    pid = "web-e2e-studio-flow"
    still = tmp_path / "e2e-still.png"
    # Valid tiny PNG fixture; the Shotbook thumbnail route may honestly return 404
    # until the asset is registered in the library, but the composition remains editable.
    still.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
        "lYkAAAAASUVORK5CYII="
    ))
    store.create_project(pid, "Playwright 工作台回归", {"name": "Playwright 工作台回归"})
    editor.save_composition(pid, _test_composition(still))

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = None
    context = None
    page = None
    page_errors: list[str] = []
    tracing = False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            tracing = True
            page = context.new_page()
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            response = page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
            assert response and response.status == 200

            page.locator('button[onclick="openCreate()"]').click()
            page.locator("#cf-render-engine").wait_for(state="visible")
            page.wait_for_function(
                "() => window.CF_STUDIO_CATALOG && document.querySelectorAll('[data-studio-style]').length > 0",
                timeout=10000,
            )
            assert page.locator("#cf-render-engine option").count() >= 1
            assert page.locator("#cf-render-engine").is_enabled()
            first_style = page.locator("[data-studio-style]").first
            selected_style = first_style.get_attribute("data-studio-style")
            assert selected_style
            first_style.click()
            assert page.locator("#cf-style").input_value() == selected_style
            assert page.locator("#cf-studio-template-list").inner_text().startswith("模板目录")
            # Metadata-only templates must not be offered as selectable choices.
            assert page.locator("[data-studio-template]").count() == 0

            page.locator('#cf-panel button:has-text("关闭")').first.click()
            page.wait_for_function("() => typeof workspaceOpen === 'function' && typeof shotbookLoad === 'function'")
            opened = page.evaluate("(pid) => workspaceOpen(pid)", pid)
            assert opened.get("ok"), f"workspace did not open: {opened}"
            loaded = page.evaluate("() => shotbookLoad()")
            assert loaded, "Shotbook did not load through the real HTTP handler"
            page.locator("#shotbook-details").evaluate("(el) => { el.open = true; }")
            assert page.locator("#shotbook-list .sb-shot").count() == 2
            assert page.locator("#editor-timeline").count() == 1
            assert page.locator("#ed-preview-stage").count() == 1
            assert page.locator("#shotbook-details").evaluate(
                "(el) => !!el.closest('.ws-col')"
            ), "Shotbook must live inside the shared workspace column"

            page.locator('[data-shotbook-action="locate"][data-scene-key="S002"]').click()
            page.wait_for_function("() => Math.abs((window.PV && window.PV.playhead || 0) - 1) < 0.02")
            # Even if the UI tried to bypass the button, the server rejects final render without attestation.
            response = page.evaluate("""async (projectId) => {
              const r = await fetch('/api/workflows', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({project_id: projectId, kind: 'render_final'})
              });
              return {status:r.status, body:await r.json()};
            }""", pid)
            assert response["status"] == 409, response
            assert response["body"].get("code") == "SHOTBOOK_NOT_CONFIRMED", response
            assert not page_errors, "Browser runtime errors: " + " | ".join(page_errors)

            context.tracing.stop()
            tracing = False
    except BaseException:
        if page is not None:
            try:
                page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception:
                pass
        if context is not None and tracing:
            try:
                context.tracing.stop(path=str(trace_path))
                tracing = False
            except Exception:
                pass
        raise
    finally:
        if context is not None and tracing:
            try:
                context.tracing.stop()
            except Exception:
                pass
        if browser is not None:
            browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
