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
    AssetRef, CompositionIR, Layer, LayerType, ProjectSpec, SceneIR, VisualOverlayClip,
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
        visual_overlays=[VisualOverlayClip(
            id="e2e-overlay", asset_id="e2e-still", start=0.25, duration=0.5,
            source_in=0.0, x=0.6, y=0.1, width=0.3, height=0.3,
            opacity=0.8, z_index=1,
        )],
    )


def test_studio_to_timeline_browser_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    results_dir = Path(os.environ.get("VIDEO_DIRECTOR_TEST_RESULTS", str(tmp_path / "test-results")))
    results_dir.mkdir(parents=True, exist_ok=True)
    trace_path = results_dir / "studio-workspace-failure-trace.zip"

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
                context.tracing.start(screenshots=False, snapshots=True, sources=True)
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
                # The actual composition endpoint must round-trip persisted overlay
                # clips and expose the referenced source, not just an in-memory UI mock.
                api_composition = page.evaluate("""async (projectId) => {
                  const response = await fetch('/api/editor/composition?project_id=' + encodeURIComponent(projectId));
                  return {status: response.status, body: await response.json()};
                }""", pid)
                assert api_composition["status"] == 200, api_composition
                api_overlays = api_composition["body"]["visual_overlays"]
                assert len(api_overlays) == 1, api_composition
                api_overlay = api_overlays[0]
                assert {key: api_overlay.get(key) for key in (
                    "id", "asset_id", "start", "duration", "source_in", "x", "y",
                    "width", "height", "opacity", "z_index",
                )} == {
                    "id": "e2e-overlay", "asset_id": "e2e-still", "start": 0.25,
                    "duration": 0.5, "source_in": 0.0, "x": 0.6, "y": 0.1,
                    "width": 0.3, "height": 0.3, "opacity": 0.8, "z_index": 1,
                }, api_composition
                loaded = page.evaluate("() => shotbookLoad()")
                assert loaded, "Shotbook did not load through the real HTTP handler"
                page.locator("#shotbook-details").evaluate("(element) => { element.open = true; }")
                assert page.locator("#shotbook-list .sb-shot").count() == 2
                assert page.locator("#editor-timeline").count() == 1
                assert page.locator("#ed-preview-stage").count() == 1
                # Dual view must not merely render the physical timeline: the
                # playhead must react to pointer input on the real timeline element.
                dual_scrub = page.evaluate("""() => {
                  const oldView = ED.timelineView;
                  ED.timelineView = 'dual';
                  editorRenderTimeline();
                  const scroll = document.querySelector('#editor-timeline .ed-tl-scroll');
                  if (!scroll) return {hasPhysicalTimeline: false, before: 0, after: 0};
                  const rect = scroll.getBoundingClientRect();
                  const x = rect.left + rect.width * 0.75;
                  const y = rect.top + Math.min(12, rect.height / 2);
                  const before = window.PLAYHEAD.at;
                  scroll.dispatchEvent(new PointerEvent('pointerdown', {
                    bubbles: true, cancelable: true, pointerId: 7, button: 0,
                    clientX: x, clientY: y
                  }));
                  scroll.dispatchEvent(new PointerEvent('pointermove', {
                    bubbles: true, cancelable: true, pointerId: 7, buttons: 1,
                    clientX: x, clientY: y
                  }));
                  document.dispatchEvent(new PointerEvent('pointerup', {
                    bubbles: true, cancelable: true, pointerId: 7, button: 0,
                    clientX: x, clientY: y
                  }));
                  const after = window.PLAYHEAD.at;
                  const hasPhysicalTimeline = !!scroll.isConnected;
                  ED.timelineView = oldView;
                  editorRenderTimeline();
                  return {hasPhysicalTimeline, before, after};
                }""")
                assert dual_scrub["hasPhysicalTimeline"], dual_scrub
                assert 1.2 < dual_scrub["after"] < 1.8, dual_scrub

                # Zoom must change the actual shared time-axis width; clip geometry,
                # pointer-to-time mapping and playhead coordinates must stay aligned.
                zoom_geometry = page.evaluate("""() => {
                  const previousZoom = ED.zoom;
                  const previousView = ED.timelineView;
                  const result = [];
                  ED.timelineView = 'dual';
                  for (const zoom of [0.5, 1, 2]) {
                    ED.zoom = zoom;
                    editorRenderTimeline();
                    const scroll = document.querySelector('#editor-timeline .ed-tl-scroll');
                    const canvas = scroll && scroll.querySelector('.ed-tl-canvas');
                    const body = canvas && canvas.querySelector('.ed-track-body');
                    const playhead = document.getElementById('tl-playhead');
                    if (!scroll || !canvas || !body || !playhead) {
                      result.push({zoom, missing: true});
                      continue;
                    }
                    const cr = canvas.getBoundingClientRect();
                    const br = body.getBoundingClientRect();
                    const expectedLeft = br.left - cr.left +
                      (window.PLAYHEAD.at / (ED.comp.duration || 1)) * br.width;
                    const mapped = _tlPct({clientX: br.left + br.width * 0.75}, scroll);
                    result.push({
                      zoom,
                      axisWidth: br.width,
                      canvasWidth: cr.width,
                      viewportWidth: scroll.clientWidth,
                      playheadLeft: parseFloat(playhead.style.left),
                      expectedLeft,
                      mapped,
                      scrollable: scroll.scrollWidth > scroll.clientWidth
                    });
                  }
                  ED.zoom = previousZoom;
                  ED.timelineView = previousView;
                  editorRenderTimeline();
                  return result;
                }""")
                assert len(zoom_geometry) == 3, zoom_geometry
                assert all("missing" not in item for item in zoom_geometry), zoom_geometry
                base_axis = zoom_geometry[1]["axisWidth"]
                assert 0.45 < zoom_geometry[0]["axisWidth"] / base_axis < 0.55, zoom_geometry
                assert 0.98 < zoom_geometry[1]["axisWidth"] / base_axis < 1.02, zoom_geometry
                assert 1.9 < zoom_geometry[2]["axisWidth"] / base_axis < 2.1, zoom_geometry
                assert all(abs(item["playheadLeft"] - item["expectedLeft"]) < 2 for item in zoom_geometry), zoom_geometry
                assert all(abs(item["mapped"] - 0.75) < 0.01 for item in zoom_geometry), zoom_geometry
                assert zoom_geometry[2]["scrollable"], zoom_geometry

                # Real user-facing overlay lane: render, edit inspector, add from project
                # assets, delete, and drag a clip. Commands are captured at the same
                # frontend command boundary used by the real API so no disk data changes.
                overlay_ui = page.evaluate("""async () => {
                  const original = {
                    comp: ED.comp, view: ED.timelineView,
                    executeCommand: window.executeCommand,
                    refreshWorkspace: window.refreshWorkspace,
                    editorToast: window.editorToast
                  };
                  const commands = [];
                  try {
                    ED.comp = Object.assign({}, original.comp, {
                      duration: 5,
                      assets: {
                        "overlay-test-image": {id:"overlay-test-image", type:"image", name:"Test overlay image", file:"/fake.png", duration:1}
                      },
                      visual_overlays: [{
                        id:"overlay-test", asset_id:"overlay-test-image", start:1, duration:2, source_in:0,
                        x:0.65, y:0.1, width:0.3, height:0.3, opacity:1, z_index:0
                      }]
                    });
                    ED.timelineView = "dual";
                    window.executeCommand = async command => { commands.push(command); return {ok:true}; };
                    window.refreshWorkspace = async () => {};
                    window.editorToast = () => {};
                    editorRenderTimeline();
                    const trackExists = !!document.querySelector('.ed-visual-overlay-clip[data-visual-overlay-index="0"]');
                    const addButtonExists = !!document.querySelector(".ed-track-add");
                    editorVisualOverlayOpen(0);
                    document.getElementById("visual-overlay-x").value = "0.4";
                    document.getElementById("visual-overlay-y").value = "0.2";
                    document.getElementById("visual-overlay-z-index").value = "3";
                    await editorVisualOverlayApply(0);
                    editorVisualOverlayPick();
                    document.getElementById("visual-overlay-start").value = "0.5";
                    document.getElementById("visual-overlay-duration").value = "1";
                    await editorVisualOverlayCreate();
                    await editorVisualOverlayRemove(0);
                    editorRenderTimeline();
                    const clip = document.querySelector('.ed-visual-overlay-clip[data-visual-overlay-index="0"]');
                    const rect = clip.getBoundingClientRect();
                    const body = clip.closest(".ed-track-body").getBoundingClientRect();
                    const x = rect.left + rect.width / 2;
                    const y = rect.top + rect.height / 2;
                    clip.dispatchEvent(new PointerEvent("pointerdown", {bubbles:true, cancelable:true, pointerId:27, button:0, clientX:x, clientY:y}));
                    document.dispatchEvent(new PointerEvent("pointermove", {bubbles:true, cancelable:true, pointerId:27, buttons:1, clientX:x + body.width * 0.1, clientY:y}));
                    document.dispatchEvent(new PointerEvent("pointerup", {bubbles:true, cancelable:true, pointerId:27, button:0, clientX:x + body.width * 0.1, clientY:y}));
                    await Promise.resolve();
                    await new Promise(resolve => setTimeout(resolve, 0));
                    return {trackExists, addButtonExists, commands, dialogRemoved:!document.getElementById("visual-overlay-inspector-mask")};
                  } finally {
                    ED.comp = original.comp;
                    ED.timelineView = original.view;
                    window.executeCommand = original.executeCommand;
                    window.refreshWorkspace = original.refreshWorkspace;
                    window.editorToast = original.editorToast;
                    editorRenderTimeline();
                  }
                }""")
                assert overlay_ui["trackExists"], overlay_ui
                assert overlay_ui["addButtonExists"], overlay_ui
                overlay_commands = [command.get("command") for command in overlay_ui["commands"]]
                assert overlay_commands == [
                    "set_visual_overlay", "add_visual_overlay", "remove_visual_overlay", "move_visual_overlay"
                ], overlay_ui
                assert overlay_ui["commands"][0]["payload"]["z_index"] == 3, overlay_ui
                assert overlay_ui["commands"][1]["payload"]["start"] == 0.5, overlay_ui
                assert overlay_ui["dialogRemoved"], overlay_ui

                # Re-rendering the physical timeline must not attach another pair
                # of document-level layer-drag handlers. Count real registrations.
                layer_handler_count = page.evaluate("""() => {
                  const originalAdd = document.addEventListener;
                  const registrations = [];
                  document.addEventListener = function(type, listener, options) {
                    if (type === 'pointermove' || type === 'pointerup') registrations.push(type);
                    return originalAdd.call(this, type, listener, options);
                  };
                  const view = ED.timelineView;
                  ED.timelineView = 'dual';
                  editorRenderTimeline();
                  editorRenderTimeline();
                  document.addEventListener = originalAdd;
                  ED.timelineView = view;
                  editorRenderTimeline();
                  return {
                    guardBound: window.__tlLayerDragHandlers === true,
                    pointermove: registrations.filter(type => type === 'pointermove').length,
                    pointerup: registrations.filter(type => type === 'pointerup').length
                  };
                }""")
                assert layer_handler_count == {"guardBound": True, "pointermove": 0, "pointerup": 0}, layer_handler_count
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
                # Preserve the DOM/trace failure evidence without writing screenshots;
                # don't let cleanup errors replace the actual failing assertion/timeout.
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
