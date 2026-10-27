"""Real Chromium acceptance for the resizable Studio dock and saved panel layout."""
from __future__ import annotations

import importlib
import os
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

from video_director import film_document as fd


LAYOUT_FILM = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1280,height=720">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden}</style>
</head>
<body>
<section id="picture" data-label="画面">
  <video id="layout-clip-a" src="../assets/a.mp4#t=0,5" at="0"></video>
  <video id="layout-clip-b" src="../assets/b.mp4#t=0,5" at="5"></video>
</section>
<section id="sound" data-label="音频">
  <audio id="layout-audio" src="../assets/music.wav#t=0,10" at="0" volume="0.2"></audio>
</section>
</body>
</html>"""


def _open_studio_project(page, project_id: str) -> None:
    page.wait_for_function("typeof window.filmStudioToggle === 'function'")
    page.locator("#tab-editor").click()
    page.locator("#pane-editor").wait_for(state="visible")
    page.wait_for_function(
        "() => { const select=document.querySelector('#editor-project'); return !!select && select.options.length>0; }"
    )
    page.evaluate(
        """pid => {
          const select=document.querySelector('#editor-project');
          let option=[...select.options].find(item=>item.value===pid);
          if(!option){option=new Option(pid,pid);select.add(option);}
          select.value=pid;
          select.dispatchEvent(new Event('change',{bubbles:true}));
        }""",
        project_id,
    )
    page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
    page.locator('#fs-timeline .fs-clip[data-clip="layout-clip-a"]').wait_for(state="visible")
    page.wait_for_function(
        "pid => window.filmStudioStore?.getField('projectId') === pid",
        project_id,
    )


def _layout_state(page) -> dict:
    return page.evaluate(
        """() => {
          const dock=document.querySelector('#fs-workspace-dock');
          const pane=document.querySelector('#fs-side-workbench');
          const inspector=document.querySelector('#fs-side-inspector');
          const divider=document.querySelector('#fs-side-divider');
          const ai=document.querySelector('#fs-side-ai');
          const rect=el => {const r=el.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height};};
          return {
            savedLeft: Number(localStorage.getItem('film-studio-left-pane')),
            appliedLeft: parseFloat(getComputedStyle(dock).getPropertyValue('--fs-left')),
            savedShare: Number(localStorage.getItem('film-studio-inspector-share')),
            appliedShare: parseFloat(getComputedStyle(pane).getPropertyValue('--fs-inspector-share')),
            inspectorVisible: !!inspector && inspector.getClientRects().length>0 && getComputedStyle(inspector).display!=='none',
            aiVisible: !!ai && ai.getClientRects().length>0 && getComputedStyle(ai).display!=='none',
            inspector: rect(inspector), divider: rect(divider), ai: rect(ai),
            projectId: window.filmStudioStore?.getField('projectId') || ''
          };
        }"""
    )


def test_resizable_dock_and_inspector_ai_split_persist_across_reload(tmp_path, monkeypatch):
    """Drag the real separators in Chromium and verify their geometry survives reload."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-layout-persistence-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(LAYOUT_FILM, encoding="utf-8")
    assert fd.read_project_film(project_id)["exists"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            try:
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="domcontentloaded",
                )
                assert response and response.status == 200
                _open_studio_project(page, project_id)

                before = page.evaluate(
                    """() => ({
                      left:parseFloat(getComputedStyle(document.querySelector('#fs-workspace-dock')).getPropertyValue('--fs-left')),
                      share:parseFloat(getComputedStyle(document.querySelector('#fs-side-workbench')).getPropertyValue('--fs-inspector-share'))
                    })"""
                )
                left_divider = page.locator('.fs-pane-divider[data-divider="left"]')
                left_box = left_divider.bounding_box()
                assert left_box, "left dock separator has no browser geometry"
                start_x = left_box["x"] + left_box["width"] / 2
                start_y = left_box["y"] + left_box["height"] / 2
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(start_x + 42, start_y, steps=8)
                page.mouse.up()
                page.wait_for_function(
                    "old => Math.abs(parseFloat(getComputedStyle(document.querySelector('#fs-workspace-dock')).getPropertyValue('--fs-left'))-old)>1",
                    before["left"],
                )
                page.wait_for_function(
                    "() => Number.isFinite(Number(localStorage.getItem('film-studio-left-pane')))"
                )

                side_divider = page.locator("#fs-side-divider")
                side_box = side_divider.bounding_box()
                side_pane = page.locator("#fs-side-workbench").bounding_box()
                assert side_box and side_pane, "inspector/AI separator has no browser geometry"
                divider_x = side_box["x"] + side_box["width"] / 2
                divider_y = side_box["y"] + side_box["height"] / 2
                target_y = side_pane["y"] + side_pane["height"] * 0.40
                page.mouse.move(divider_x, divider_y)
                page.mouse.down()
                page.mouse.move(divider_x, target_y, steps=8)
                page.mouse.up()
                page.wait_for_function(
                    "old => Math.abs(parseFloat(getComputedStyle(document.querySelector('#fs-side-workbench')).getPropertyValue('--fs-inspector-share'))-old)>2",
                    before["share"],
                )
                page.wait_for_function(
                    "() => Number.isFinite(Number(localStorage.getItem('film-studio-inspector-share')))"
                )

                resized = _layout_state(page)
                assert resized["projectId"] == project_id, resized
                assert resized["inspectorVisible"] and resized["aiVisible"], resized
                assert abs(resized["appliedLeft"] - resized["savedLeft"]) < 0.1, resized
                assert abs(resized["appliedShare"] - resized["savedShare"]) < 0.1, resized
                assert resized["inspector"]["bottom"] <= resized["divider"]["top"] + 8, resized
                assert resized["divider"]["bottom"] <= resized["ai"]["top"] + 8, resized
                assert resized["savedLeft"] != before["left"], resized
                assert resized["savedShare"] != before["share"], resized

                # A full reload proves the saved values are reapplied, rather than
                # merely retained as transient inline styles in the current document.
                page.reload(wait_until="domcontentloaded")
                _open_studio_project(page, project_id)
                restored = _layout_state(page)
                assert restored["projectId"] == project_id, restored
                assert restored["inspectorVisible"] and restored["aiVisible"], restored
                assert abs(restored["appliedLeft"] - resized["savedLeft"]) < 0.1, restored
                assert abs(restored["appliedShare"] - resized["savedShare"]) < 0.1, restored
                assert restored["inspector"]["bottom"] <= restored["divider"]["top"] + 8, restored
                assert restored["divider"]["bottom"] <= restored["ai"]["top"] + 8, restored
                assert not page_errors, page_errors
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
