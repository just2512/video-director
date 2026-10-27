"""Real Chromium acceptance for Studio pane resizing and layout persistence."""
from __future__ import annotations

import base64
import importlib
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright

from video_director import config, film_document as fd, store


def test_studio_resizable_layout_persists_after_reload(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "studio-layout-persistence-browser"
    store.create_project(project_id, "Studio 布局持久化回归", {"name": "Studio 布局持久化回归"})
    project_root = config.project_dir(project_id)
    assets = project_root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    still = assets / "layout.png"
    still.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
        "x8AAwMCAO+/lYkAAAAASUVORK5CYII="
    ))
    (project_root / fd.FILM_NAME).write_text(
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        '<section id="video-main" data-label="主画面">'
        '<img id="layout-clip" src="../assets/layout.png#t=0,1" at="0">'
        '</section></body></html>',
        encoding="utf-8",
    )
    assert fd.read_project_film(project_id)["exists"]

    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("window.confirm = () => true;")
            try:
                response = page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
                assert response and response.status == 200
                page.wait_for_function("() => typeof window.workspaceOpen === 'function'")
                opened = page.evaluate("(pid) => window.workspaceOpen(pid)", project_id)
                assert opened.get("ok") is True, opened
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="layout-clip"]').wait_for(state="visible")
                assert page.locator("#fs-side-inspector").is_visible()
                assert page.locator("#fs-side-ai").is_visible()

                read_layout = """() => {
                  const dock=document.querySelector('#fs-workspace-dock');
                  const panel=document.querySelector('#film-studio-panel');
                  const side=document.querySelector('#fs-side-workbench');
                  const timeline=document.querySelector('.fs-timeline-pane');
                  const n=(v,f)=>Number.isFinite(parseFloat(v))?parseFloat(v):f;
                  return {
                    left:n(getComputedStyle(dock).getPropertyValue('--fs-left'),24),
                    right:n(getComputedStyle(dock).getPropertyValue('--fs-right'),24),
                    share:n(getComputedStyle(side).getPropertyValue('--fs-inspector-share'),54),
                    timeline:timeline.getBoundingClientRect().height,
                    maxTimeline:Math.max(160,Math.min(innerHeight*.45,panel.getBoundingClientRect().height*.45)),
                    timelineVar:n(panel.style.getPropertyValue('--fs-timeline-height'),0),
                    saved:{
                      left:localStorage.getItem('film-studio-left-pane'),
                      right:localStorage.getItem('film-studio-right-pane'),
                      share:localStorage.getItem('film-studio-inspector-share'),
                      timeline:localStorage.getItem('film-studio-timeline-height')
                    }
                  };
                }"""
                initial = page.evaluate(read_layout)

                def drag(selector, dx=0, dy=0):
                    item = page.locator(selector)
                    item.wait_for(state="visible")
                    box = item.bounding_box()
                    assert box and box["width"] > 0 and box["height"] > 0, (selector, box)
                    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                    page.mouse.move(x, y)
                    page.mouse.down()
                    page.mouse.move(x + dx, y + dy, steps=8)
                    page.mouse.up()

                drag('#film-studio-panel .fs-pane-divider[data-divider="left"]', dx=64)
                drag('#film-studio-panel .fs-pane-divider[data-divider="right"]', dx=-64)
                drag("#fs-side-divider", dy=40)
                timeline_dy = -40 if initial["timeline"] < initial["maxTimeline"] - 20 else 40
                drag("#fs-timeline-divider", dy=timeline_dy)

                changed = page.evaluate(read_layout)
                assert abs(changed["left"] - initial["left"]) > .5, changed
                assert abs(changed["right"] - initial["right"]) > .5, changed
                assert abs(changed["share"] - initial["share"]) > .5, changed
                assert abs(changed["timeline"] - initial["timeline"]) > 5, changed
                for key, field in (("left", "left"), ("right", "right"), ("share", "share")):
                    assert changed["saved"][key] and abs(float(changed["saved"][key]) - changed[field]) < .1, changed
                assert changed["saved"]["timeline"] and abs(
                    float(changed["saved"]["timeline"]) - changed["timelineVar"]
                ) < 1, changed

                expected = {key: float(changed["saved"][key]) for key in ("left", "right", "share", "timeline")}
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function("() => typeof window.workspaceOpen === 'function'")
                reopened = page.evaluate("(pid) => window.workspaceOpen(pid)", project_id)
                assert reopened.get("ok") is True, reopened
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="layout-clip"]').wait_for(state="visible")
                restored = page.evaluate(read_layout)
                actual = {
                    "left": restored["left"], "right": restored["right"],
                    "share": restored["share"], "timeline": restored["timelineVar"]
                }
                for key, value in expected.items():
                    assert abs(actual[key] - value) < .2, {"dimension": key, "expected": value, "actual": actual}
                assert page.locator("#fs-side-inspector").is_visible()
                assert page.locator("#fs-side-ai").is_visible()
                assert not errors, f"browser JavaScript errors: {errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
