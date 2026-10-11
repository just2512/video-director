"""Browser regression for Studio -> Shotbook -> Timeline, retaining failure evidence."""
from __future__ import annotations

import base64
import importlib
import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from video_director.models import (
    AssetRef, CompositionIR, Layer, LayerType, ProjectSpec, SceneIR,
)
from video_director import editor, store, shotbook


def _test_composition(media_file: Path) -> CompositionIR:
    layer1 = Layer(type=LayerType.image, asset_id="e2e-still", start=0, duration=1.0)
    layer2 = Layer(type=LayerType.image, asset_id="e2e-still", start=0, duration=1.0)
    recipes = shotbook._recipes()
    recipe_id = recipes[0]["id"] if recipes else None
    scenes = [
        SceneIR(id="S001", start=0, duration=1.0, text="第一镜用于工作台定位",
                layers=[layer1], origin_recipe_id=recipe_id),
        SceneIR(id="S002", start=1.0, duration=1.0, text="第二镜测试逐镜预览",
                layers=[layer2], origin_recipe_id=recipe_id),
    ]
    asset = AssetRef(
        id="e2e-still", type="image", file=str(media_file), width=32, height=32,
        source="uploaded", rights_status="CLEAR",
        provenance={"source": "Playwright regression fixture"},
    )
    return CompositionIR(
        project=ProjectSpec(width=160, height=90, fps=10, duration=2.0),
        scenes=scenes, assets=[asset],
    )


def test_studio_to_timeline_browser_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    results_dir = Path(os.environ.get("VIDEO_DIRECTOR_TEST_RESULTS", str(tmp_path / "test-results")))
    results_dir.mkdir(parents=True, exist_ok=True)
    trace_path = results_dir / "studio-workspace-failure-trace.zip"
    screenshot_path = results_dir / "studio-workspace-failure.png"

    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = Path(os.environ["VIDEO_DIRECTOR_STORAGE_ROOT"])

    pid = "web-e2e-studio-flow"
    still = tmp_path / "e2e-still.png"
    still.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
        "lYkAAAAASUVORK5CYII="
    ))
    store.create_project(pid, "Playwright 工作台回归", {"name": "Playwright 工作台回归"})
    editor.save_composition(pid, _test_composition(still))

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = None
            tracing = False
            try:
                context.tracing.start(screenshots=True, snapshots=True, sources=True)
                tracing = True
                page = context.new_page()
                page_errors: list[str] = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))

                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle"
                )
                assert response and response.status == 200
                page.locator('button[onclick="openCreate()"]').click()
                page.locator("#cf-render-engine").wait_for(state="visible")
                page.wait_for_function(
                    "() => window.CF_STUDIO_CATALOG && document.querySelectorAll('[data-studio-style]').length > 0",
                    timeout=10000,
                )
                assert page.locator("#cf-panel").is_visible()
                assert page.locator("#cf-render-engine").is_enabled()
                assert page.locator("#cf-render-engine option").count() >= 1
                first_style = page.locator("[data-studio-style]").first
                selected_style = first_style.get_attribute("data-studio-style")
                assert selected_style
                first_style.click()
                assert page.locator("#cf-style").input_value() == selected_style
                assert page.locator("#cf-studio-template-list").inner_text().startswith("模板目录")
                usable_templates = page.locator("[data-studio-template]")
                assert usable_templates.count() > 0, "catalog must expose at least one genuinely instantiable built-in template"
                selected_template = usable_templates.first.evaluate(
                    "(el) => ({id: el.dataset.studioTemplate, version: el.dataset.templateVersion, text: el.innerText})"
                )
                usable_templates.first.click()
                page.locator("#tg-mask").wait_for(state="visible")
                page.wait_for_function(
                    "(key) => window.TG && window.TG.selectedKey === key",
                    arg=selected_template["id"] + "@" + selected_template["version"],
                )
                assert selected_template["text"] or selected_template["id"]
                assert page.locator("#tg-detail").inner_text().find("使用自己的素材") >= 0
                page.evaluate("templateGalleryClose()")

                page.locator('#cf-panel button:has-text("关闭")').first.click()
                page.wait_for_function(
                    "() => typeof workspaceOpen === 'function' && typeof shotbookLoad === 'function'"
                )
                opened = page.evaluate("(projectId) => workspaceOpen(projectId)", pid)
                assert opened.get("ok"), f"workspace did not open: {opened}"
                loaded = page.evaluate("() => shotbookLoad()")
                assert loaded, "Shotbook did not load through the real HTTP handler"
                page.locator("#shotbook-details").evaluate("(element) => { element.open = true; }")
                assert page.locator("#shotbook-list .sb-shot").count() == 2
                assert page.locator("#editor-timeline").count() == 1
                assert page.locator("#ed-preview-stage").count() == 1
                # 布局契约：Shotbook 与编辑器共享同一工作区（main 布局：#editor-body
                # 与 #editor-workspace 同属 pane-editor；不要求在 .ws-col 三栏列内）。
                assert page.locator("#shotbook-details").evaluate(
                    "(element) => !!element.closest('#editor-body') && !!document.getElementById('editor-workspace')"
                ), "Shotbook must live inside the shared editor workspace"

                page.locator(
                    '#shotbook-list [data-shotbook-action="locate"][data-scene-key="S002"]'
                ).click()
                page.wait_for_function(
                    "() => Math.abs((window.PV && window.PV.playhead || 0) - 1) < 0.02"
                )
                # 信任边界：生成分镜草案落盘（未逐镜确认）→ render_final 被服务端
                # 409 拦截。门语义=「保存过 Shotbook 的项目须确认后成片」——
                # 仅打开过工作台（纯 GET load）不落盘、不设门，legacy 项目兼容。
                gen = page.evaluate("""async (projectId) => {
                  const r = await fetch('/api/editor/shotbook', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({project_id: projectId, action: 'generate',
                                          base_version: 0})
                  });
                  return {status: r.status, body: await r.json()};
                }""", pid)
                assert gen["body"].get("ok"), gen
                response = page.evaluate("""async (projectId) => {
                  const result = await fetch('/api/workflows', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({project_id: projectId, kind: 'render_final'})
                  });
                  return {status: result.status, body: await result.json()};
                }""", pid)
                assert response["status"] == 409, response
                assert response["body"].get("code") == "SHOTBOOK_NOT_CONFIRMED", response
                assert not page_errors, "Browser runtime errors: " + " | ".join(page_errors)
            except BaseException:
                # Save browser evidence before leaving sync_playwright; don't let cleanup
                # errors replace the actual failing assertion/timeout.
                if page is not None:
                    try:
                        page.screenshot(path=str(screenshot_path), full_page=True)
                    except Exception:
                        pass
                if tracing:
                    try:
                        context.tracing.stop(path=str(trace_path))
                        tracing = False
                    except Exception:
                        pass
                raise
            else:
                if tracing:
                    context.tracing.stop()
                    tracing = False
            finally:
                try:
                    context.close()
                finally:
                    browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
