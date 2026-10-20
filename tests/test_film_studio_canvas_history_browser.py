"""Real Studio browser contract for canvas edits, undo/redo and reload persistence."""
from __future__ import annotations

import importlib
import json
import os
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

from video_director import film_document as fd


FILM = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#182030}</style></head>
<body><section id="scene-track" data-label="网页镜头">
<iframe id="scene-clip" src="scenes/title.html#t=0,5" at="0"
style="left:0px;top:0px;width:1920px;height:1080px"></iframe>
</section></body></html>'''


SCENE = '''<!doctype html>
<html><head><meta charset="utf-8"><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}
#title{position:fixed;left:40px;top:50px;width:250px;height:70px;color:#ffffff;
font:700 32px sans-serif}
</style></head><body><div id="title">Original title</div><script>
window.film={duration:5,ready:document.fonts.ready,frame:function(t){
 document.documentElement.dataset.frameTime=String(t);
}};
</script></body></html>'''


def _open_studio(page, port: int, project_id: str) -> None:
    response = page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
    assert response and response.status == 200
    page.locator("#tab-editor").click()
    page.locator("#pane-editor").wait_for(state="visible")
    page.wait_for_function(
        "() => { const select=document.querySelector('#editor-project'); return !!select && select.options.length > 0; }"
    )
    page.evaluate("""(pid) => {
      const select=document.querySelector('#editor-project');
      let option=[...select.options].find(item=>item.value===pid);
      if(!option){option=new Option(pid,pid);select.add(option);}
      select.value=pid;
    }""", project_id)
    page.evaluate("window.filmStudioToggle()")
    page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
    page.locator('#fs-timeline .fs-clip[data-clip="scene-clip"]').wait_for(state="visible")


def _clip(project_id: str) -> dict:
    state = fd.read_project_film(project_id)
    track = next(track for track in state["doc"]["tracks"] if track["id"] == "scene-track")
    return track["clips"][0]


def _overrides(project_id: str) -> list[dict]:
    value = _clip(project_id)["attrs"].get("overrides") or "[]"
    return json.loads(value)


def test_studio_canvas_edit_undo_redo_and_persistence_after_reload(tmp_path, monkeypatch):
    storage_root = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "studio-canvas-history-browser"
    project_root = storage_root / project_id
    (project_root / "scenes").mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(FILM, encoding="utf-8")
    (project_root / "scenes" / "title.html").write_text(SCENE, encoding="utf-8")
    assert fd.read_project_film(project_id)["exists"]

    results_dir = Path(os.environ.get("VIDEO_DIRECTOR_TEST_RESULTS", str(tmp_path / "test-results")))
    results_dir.mkdir(parents=True, exist_ok=True)
    trace_path = results_dir / "film-studio-canvas-history-trace.zip"

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            tracing = False
            page = context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            try:
                context.tracing.start(screenshots=True, snapshots=True, sources=True)
                tracing = True
                _open_studio(page, server.server_port, project_id)
                page.locator("#fs-element-inspect-toggle").click()
                page.frame_locator("#fs-preview-composition").frame_locator("#scene-clip").locator("#title").click()
                page.locator("#fs-element-text").wait_for(state="visible")

                page.locator("#fs-element-text").fill("Saved title")
                page.locator("#fs-element-left").fill("64")
                page.locator("#fs-element-top").fill("75")
                page.locator("#fs-element-width").fill("280")
                page.locator("#fs-element-height").fill("80")
                page.locator("#fs-element-color").fill("#ff0000")
                page.locator("#fs-element-font-size").fill("36")
                page.locator("#fs-element-rotate").fill("12")
                page.locator("#fs-apply-element").click()
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('画布修改已保存到 film.html')",
                    timeout=15000,
                )

                changed = _overrides(project_id)
                target = next(item for item in changed if item.get("selector") == "#title")
                assert target["text"] == "Saved title"
                assert target["style"]["left"] == "64.00px"
                assert target["style"]["top"] == "75.00px"
                assert target["style"]["width"] == "280.00px"
                assert target["style"]["height"] == "80.00px"
                assert target["style"]["color"] == "#ff0000"
                assert target["style"]["fontSize"] == "36.00px"
                assert target["style"]["rotate"] == "12.00deg"
                saved_revision = fd.read_project_film(project_id)["rev"]

                page.locator('[data-film-action="undo"]').click()
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('已撤销')",
                    timeout=15000,
                )
                assert not any(item.get("selector") == "#title" for item in _overrides(project_id))
                assert fd.read_project_film(project_id)["rev"] != saved_revision

                page.locator('[data-film-action="redo"]').click()
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('已重做')",
                    timeout=15000,
                )
                redone = next(item for item in _overrides(project_id) if item.get("selector") == "#title")
                assert redone["text"] == "Saved title"
                assert redone["style"]["left"] == "64.00px"

                # A true browser reload must rebuild the Inspector from the stored
                # film.html revision, not from stale in-memory selection state.
                page.reload(wait_until="domcontentloaded")
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.evaluate("""(pid) => {
                  const select=document.querySelector('#editor-project');
                  let option=[...select.options].find(item=>item.value===pid);
                  if(!option){option=new Option(pid,pid);select.add(option);}
                  select.value=pid;
                }""", project_id)
                page.evaluate("window.filmStudioToggle()")
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="scene-clip"]').wait_for(state="visible")
                page.locator("#fs-element-inspect-toggle").click()
                scene_title = page.frame_locator("#fs-preview-composition").frame_locator("#scene-clip").locator("#title")
                page.wait_for_function(
                    "() => document.querySelector('#fs-preview-composition')?.contentWindow != null",
                    timeout=15000,
                )
                scene_title.click()
                page.locator("#fs-element-text").wait_for(state="visible")
                assert page.locator("#fs-element-text").input_value() == "Saved title"
                assert page.locator("#fs-element-left").input_value() == "64.0"
                assert page.locator("#fs-element-top").input_value() == "75.0"
                assert page.locator("#fs-element-width").input_value() == "280.0"
                assert page.locator("#fs-element-font-size").input_value() == "36"
                assert fd.read_project_film(project_id)["rev"] == saved_revision
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            except Exception:
                if tracing:
                    context.tracing.stop(path=str(trace_path))
                    tracing = False
                raise
            finally:
                if tracing:
                    context.tracing.stop()
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
