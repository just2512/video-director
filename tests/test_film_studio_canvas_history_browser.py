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
            # Emit pointer and postMessage traces from every frame into the top page.
            # The migrated scene iframe is sandboxed, so the parent cannot inspect
            # its DOM directly; this test-only trace makes the boundary observable.
            page.add_init_script("""(() => {
              const isTop = window === window.top;
              if (isTop) window.__vdCanvasTrace = [];
              const emit = (record) => {
                try { window.top.postMessage({type:'vd-canvas-test-trace', record}, '*'); } catch (_) {}
              };
              window.addEventListener('message', (event) => {
                const message = event.data || {};
                if (typeof message.type === 'string' && message.type.indexOf('film-') === 0) {
                  emit({event:'message', context:location.href, type:message.type,
                    origin:event.origin, sourceIsParent:event.source===window.parent,
                    enabled:message.enabled, clipId:message.clipId, selector:message.selector});
                }
              });
              document.addEventListener('pointerdown', (event) => {
                const target=event.target;
                emit({event:'pointerdown', context:location.href,
                  targetTag:target&&target.tagName, targetId:target&&target.id,
                  targetClass:target&&String(target.className||''),
                  pointerEvents:target&&getComputedStyle(target).pointerEvents,
                  x:event.clientX,y:event.clientY});
              }, true);
              if (isTop) window.addEventListener('message', (event) => {
                if (event.data&&event.data.type==='vd-canvas-test-trace'&&Array.isArray(window.__vdCanvasTrace)) {
                  window.__vdCanvasTrace.push(event.data.record);
                  if (window.__vdCanvasTrace.length>300) window.__vdCanvasTrace.shift();
                }
              });
            })();""")
            try:
                context.tracing.start(screenshots=True, snapshots=True, sources=True)
                tracing = True
                _open_studio(page, server.server_port, project_id)
                has_react_assets = page.evaluate(
                    "() => typeof window.filmStudioReactAssets?.render === 'function'"
                )
                if has_react_assets:
                    page.locator("#fs-assets-list [data-react-asset-list]").wait_for(state="attached", timeout=10000)
                    assert page.locator("#fs-assets-list [data-react-asset-clip]").count() == 1
                else:
                    assert page.locator("#fs-assets-list [data-react-asset-list]").count() == 0
                has_react_timeline = page.evaluate(
                    "() => typeof window.filmStudioReactTimeline?.renderSurface === 'function'"
                )
                if has_react_timeline:
                    page.locator('#fs-timeline [data-react-timeline-surface="true"]').wait_for(state="attached", timeout=10000)
                    assert page.locator("#fs-timeline [data-react-timeline-clip]").count() == 1
                else:
                    assert page.locator("#fs-timeline [data-react-timeline-clip]").count() == 0
                page.locator("#fs-static-toolbar").wait_for(state="attached", timeout=10000)
                has_react_toolbar = page.evaluate(
                    "() => typeof window.filmStudioReactToolbar?.render === 'function' && typeof window.filmStudioReactToolbar?.renderScrub === 'function'"
                )
                if has_react_toolbar:
                    page.locator('#fs-react-toolbar [data-film-action="undo"]').wait_for(state="visible", timeout=10000)
                    page.locator('#fs-react-timeline-toolbar [data-film-action="split"]').wait_for(state="visible", timeout=10000)
                    page.locator('#fs-react-scrub-row [data-film-action="play-toggle"]').wait_for(state="visible", timeout=10000)
                    assert not page.locator("#fs-static-toolbar").is_visible()
                    assert not page.locator("#fs-static-timeline-toolbar").is_visible()
                    assert page.locator("#fs-static-scrub-row").count() == 0
                    assert page.locator("#fs-element-inspect-toggle").count() == 1
                else:
                    assert page.locator("#fs-static-toolbar").is_visible()
                    assert page.locator("#fs-static-timeline-toolbar").is_visible()
                    assert page.locator("#fs-static-scrub-row").is_visible()
                    assert not page.locator("#fs-react-toolbar").is_visible()
                    assert not page.locator("#fs-react-timeline-toolbar").is_visible()
                    assert not page.locator("#fs-react-scrub-row").is_visible()
                has_react_ai = page.evaluate(
                    "() => typeof window.filmStudioReactAi?.render === 'function'"
                )
                if has_react_ai:
                    page.locator('#fs-react-ai-director [data-react-ai-chat-log="true"]').wait_for(
                        state="attached", timeout=10000
                    )
                    page.locator("#fs-side-inspector").wait_for(state="visible")
                    page.locator("#fs-side-ai").wait_for(state="visible")
                    page.locator("#fs-chat-input").fill("检查当前影片的开场")
                    page.route(
                        "**/api/editor/film/command",
                        lambda route: route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps({
                                "ok": True,
                                "no_op": True,
                                "message": "浏览器回归模拟：未生成修改提案",
                            }),
                        ),
                    )
                    page.locator("#fs-chat-input").press("Enter")
                    page.locator('#fs-chat-log [class*="fs-user"]').get_by_text(
                        "检查当前影片的开场", exact=True
                    ).wait_for(state="attached", timeout=10000)
                    page.locator("#fs-chat-log").get_by_text(
                        "浏览器回归模拟：未生成修改提案", exact=True
                    ).wait_for(state="attached", timeout=10000)
                    page.unroute("**/api/editor/film/command")
                    assert page.locator("#fs-side-inspector").is_visible()
                    assert page.locator("#fs-side-ai").is_visible()
                else:
                    assert page.locator("#fs-static-ai-director").is_visible()
                    assert not page.locator("#fs-react-ai-director").is_visible()

                # Exercise the actual React clip inspector write path before canvas inspection.
                # This is conditional only for direct-source test runs with the placeholder bundle;
                # the pre-publish smoke requires and builds the real React bundle.
                has_react_clip_inspector = page.evaluate(
                    "() => typeof window.filmStudioReactInspector?.renderClip === 'function'"
                )
                page.locator("#fs-timeline .fs-clip[data-clip='scene-clip']").click(position={"x": 40, "y": 10})
                if has_react_clip_inspector:
                    page.locator('#fs-inspector [data-react-clip-inspector="true"]').wait_for(
                        state="attached", timeout=10000
                    )
                    page.locator("#fs-field-title").fill("React inspector persisted")
                    page.locator("#fs-apply-clip-inspector").click()
                    page.wait_for_function(
                        "() => document.querySelector('#film-studio-alert')?.textContent.includes('已保存 1 项编辑')",
                        timeout=15000,
                    )
                    assert _clip(project_id)["attrs"].get("title") == "React inspector persisted"
                page.frame_locator("#fs-preview-composition").frame_locator("#scene-clip").locator("#title").wait_for(
                    state="attached", timeout=15000
                )
                page.locator("#fs-element-inspect-toggle").click()
                page.wait_for_function(
                    "() => document.querySelector('#fs-element-inspect-toggle')?.getAttribute('aria-pressed') === 'true'",
                    timeout=5000,
                )
                # Wait for the nested HTML scene to acknowledge Inspect mode before
                # clicking it; otherwise this click can race the two postMessage hops.
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('预览中的 HTML 镜头已进入元素选取模式')",
                    timeout=10000,
                )
                page.locator("#fs-preview-composition").wait_for(state="visible", timeout=10000)
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('预览中的 HTML 镜头已进入元素选取模式')",
                    timeout=10000,
                )
                assert page.locator("#fs-preview-composition").evaluate("(frame) => getComputedStyle(frame).display") != "none"
                pointer_mode = page.frame_locator("#fs-preview-composition").locator("#scene-clip").evaluate(
                    "(frame) => getComputedStyle(frame).pointerEvents"
                )
                assert pointer_mode == "auto", f"Inspect mode did not enable iframe hit-testing: {pointer_mode}"
                page.frame_locator("#fs-preview-composition").frame_locator("#scene-clip").locator("#title").click()
                try:
                    page.wait_for_function(
                        "() => document.querySelector('#film-studio-alert')?.textContent.includes('已选中')",
                        timeout=15000,
                    )
                except Exception as exc:
                    trace = page.evaluate("""() => {
                      const outer=document.querySelector('#fs-preview-composition');
                      const frame=outer&&outer.contentDocument&&outer.contentDocument.querySelector('#scene-clip');
                      const element=frame&&frame.contentDocument&&frame.contentDocument.querySelector('#title');
                      const computed=element&&getComputedStyle(element);
                      return {
                        trace:window.__vdCanvasTrace||[],
                        alert:document.querySelector('#film-studio-alert')?.textContent,
                        iframePointerEvents:frame&&getComputedStyle(frame).pointerEvents,
                        inlineStyle:element&&element.getAttribute('style'),
                        inlineLeft:element&&element.style.left,
                        computedLeft:computed&&computed.left,
                        viewport:frame&&frame.contentWindow?{width:frame.contentWindow.innerWidth,height:frame.contentWindow.innerHeight}:null
                      };
                    }""")
                    raise AssertionError("Canvas picker message chain did not reach Studio: " + json.dumps(trace, ensure_ascii=False)) from exc
                page.locator("#fs-element-text").wait_for(state="visible")
                page.locator("#fs-inspector [data-react-clip-inspector]").wait_for(state="attached", timeout=10000)
                assert page.locator("#fs-inspector #fs-field-at").count() == 1
                assert page.locator("#fs-inspector #fs-field-fade-in").count() == 1
                page.locator("#fs-apply-clip-inspector").wait_for(state="visible", timeout=10000)

                # React controlled inputs submit a typed payload through the same edit transaction.
                original_revision = fd.read_project_film(project_id)["rev"]
                page.locator("#fs-field-fade-in").fill("9")
                page.locator("#fs-apply-clip-inspector").click()
                page.locator("#fs-clip-inspector-feedback").wait_for(state="visible", timeout=5000)
                assert "不能超过片段时长" in page.locator("#fs-clip-inspector-feedback").inner_text()
                assert fd.read_project_film(project_id)["rev"] == original_revision
                page.locator("#fs-field-fade-in").fill("0")

                page.locator("#fs-field-at").fill("0.25")
                page.locator("#fs-field-title").fill("React title")
                page.locator("#fs-apply-clip-inspector").click()
                # The commit may finish before the browser receives the click result.
                # Wait on the authoritative revision displayed by the persisted FilmDocument.
                page.wait_for_function(
                    """(oldRevision) => {
                      const label=document.querySelector('#fs-project-label');
                      return !!label && !label.textContent.includes('rev '+oldRevision);
                    }""",
                    arg=original_revision,
                    timeout=15000,
                )
                persisted_clip = _clip(project_id)
                assert float(persisted_clip["attrs"]["at"]) == 0.25
                assert persisted_clip["attrs"]["title"] == "React title"

                page.locator("#fs-element-text").fill("Saved title")
                page.locator("#fs-element-left").fill("64")
                page.locator("#fs-element-top").fill("75")
                page.locator("#fs-element-width").fill("280")
                page.locator("#fs-element-height").fill("80")
                page.locator("#fs-element-color").fill("#ff0000")
                page.locator("#fs-element-background-enabled").check()
                page.locator("#fs-element-background-color").fill("#112233")
                page.locator("#fs-element-opacity").fill("0.75")
                page.locator("#fs-element-font-weight").select_option("500")
                page.locator("#fs-element-text-align").select_option("center")
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
                assert target["style"]["backgroundColor"] == "#112233"
                assert target["style"]["opacity"] == "0.75"
                assert target["style"]["fontWeight"] == "500"
                assert target["style"]["textAlign"] == "center"
                assert target["style"]["fontSize"] == "36.00px"
                assert target["style"]["rotate"] == "12.00deg"
                saved_revision = fd.read_project_film(project_id)["rev"]

                undo_selector = '#fs-react-toolbar [data-film-action="undo"]' if has_react_toolbar else '#fs-static-toolbar [data-film-action="undo"]'
                page.locator(undo_selector).click()
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('已撤销')",
                    timeout=15000,
                )
                assert not any(item.get("selector") == "#title" for item in _overrides(project_id))
                assert fd.read_project_film(project_id)["rev"] != saved_revision

                redo_selector = '#fs-react-toolbar [data-film-action="redo"]' if has_react_toolbar else '#fs-static-toolbar [data-film-action="redo"]'
                page.locator(redo_selector).click()
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
                page.frame_locator("#fs-preview-composition").frame_locator("#scene-clip").locator("#title").wait_for(
                    state="attached", timeout=15000
                )
                page.locator("#fs-element-inspect-toggle").click()
                page.wait_for_function(
                    "() => document.querySelector('#fs-element-inspect-toggle')?.getAttribute('aria-pressed') === 'true'",
                    timeout=5000,
                )
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('预览中的 HTML 镜头已进入元素选取模式')",
                    timeout=15000,
                )
                page.frame_locator("#fs-preview-composition").frame_locator("#scene-clip").locator("#title").evaluate(
                    "element => { const s=element.style; if(s.left!=='64px'||s.top!=='75px'||s.width!=='280px'||s.height!=='80px') throw new Error('saved overrides not applied before inspector-ready: '+JSON.stringify({left:s.left,top:s.top,width:s.width,height:s.height})); }"
                )
                pointer_mode_after_reload = page.frame_locator("#fs-preview-composition").locator("#scene-clip").evaluate(
                    "(frame) => JSON.stringify({pointerEvents:getComputedStyle(frame).pointerEvents,display:getComputedStyle(frame).display,playhead:document.querySelector('#fs-playhead')?.value})"
                )
                assert json.loads(pointer_mode_after_reload)["pointerEvents"] == "auto", (
                    f"Reloaded preview did not enable iframe hit-testing: {pointer_mode_after_reload}"
                )
                page.wait_for_function("""() => {
                  const outer=document.querySelector('#fs-preview-composition');
                  const scene=outer&&outer.contentDocument&&outer.contentDocument.querySelector('#scene-clip');
                  return !!outer&&getComputedStyle(outer).display!=='none'
                    &&!!scene&&getComputedStyle(scene).display!=='none';
                }""", timeout=10000)
                scene_title = page.frame_locator("#fs-preview-composition").frame_locator("#scene-clip").locator("#title")
                scene_title.click()
                try:
                    page.wait_for_function(
                        "() => document.querySelector('#film-studio-alert')?.textContent.includes('已选中')",
                        timeout=15000,
                    )
                except Exception as exc:
                    trace = page.evaluate("() => ({trace:window.__vdCanvasTrace||[], alert:document.querySelector('#film-studio-alert')?.textContent, iframePointerEvents:getComputedStyle(document.querySelector('#fs-preview-composition')?.contentDocument?.querySelector('#scene-clip')).pointerEvents})")
                    raise AssertionError("Canvas picker after reload did not reach Studio: " + json.dumps(trace, ensure_ascii=False)) from exc
                page.locator("#fs-element-text").wait_for(state="visible")
                assert page.locator("#fs-element-text").input_value() == "Saved title"
                assert page.locator("#fs-element-left").input_value() == "64.0"
                assert page.locator("#fs-element-top").input_value() == "75.0"
                assert page.locator("#fs-element-width").input_value() == "280.0"
                assert page.locator("#fs-element-font-size").input_value() == "36"
                assert fd.read_project_film(project_id)["rev"] == saved_revision

                # Clearing a previously applied fill must persist explicit transparency;
                # undo restores the filled background through the same revisioned history.
                page.locator("#fs-element-background-enabled").uncheck()
                page.locator("#fs-apply-element").click()
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('画布修改已保存到 film.html')",
                    timeout=15000,
                )
                cleared = next(item for item in _overrides(project_id) if item.get("selector") == "#title")
                assert cleared["style"]["backgroundColor"] == "transparent"
                undo_selector = '#fs-react-toolbar [data-film-action="undo"]' if has_react_toolbar else '#fs-static-toolbar [data-film-action="undo"]'
                page.locator(undo_selector).click()
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('已撤销')",
                    timeout=15000,
                )
                restored = next(item for item in _overrides(project_id) if item.get("selector") == "#title")
                assert restored["style"]["backgroundColor"] == "#112233"
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


def test_react_bezier_control_drag_persists_after_browser_reload(tmp_path, monkeypatch):
    storage_root = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "studio-bezier-control-browser"
    project_root = storage_root / project_id
    scene_root = project_root / "scenes"
    scene_root.mkdir(parents=True)
    keyframes = json.dumps([{
        "property": "opacity",
        "keyframes": [
            {"at": 0, "value": 1, "easing": {
                "type": "bezier", "x1": 0.2, "y1": 0.1, "x2": 0.8, "y2": 0.9
            }},
            {"at": 4, "value": 0.25, "easing": "linear"},
        ],
    }], separators=(",", ":"))
    film = FILM.replace(
        'at="0"\nstyle=',
        'at="0" data-film-keyframes=\'' + keyframes + '\'\nstyle=',
        1,
    )
    assert film != FILM
    (project_root / fd.FILM_NAME).write_text(film, encoding="utf-8")
    (scene_root / "title.html").write_text(SCENE, encoding="utf-8")
    assert fd.read_project_film(project_id)["exists"]

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            try:
                _open_studio(page, server.server_port, project_id)
                page.locator("#fs-timeline .fs-clip[data-clip='scene-clip']").click(
                    position={"x": 40, "y": 10}
                )
                page.locator('#fs-inspector [data-react-clip-inspector="true"]').wait_for(
                    state="attached", timeout=10000
                )
                graph = page.locator(".fs-keyframe-bezier-graph")
                handle = page.locator('[data-bezier-handle="p1"]')
                graph.wait_for(state="visible", timeout=10000)
                handle.wait_for(state="visible", timeout=10000)
                control_x = page.locator('[aria-label="关键帧 1 x1"]')
                control_x.wait_for(state="visible", timeout=10000)
                assert abs(float(control_x.input_value()) - 0.2) < 0.001

                # Drag the real SVG handle (x1=.2 to x1=.42) rather than
                # setting React state or asserting that the control exists.
                graph_box = graph.bounding_box()
                handle_box = handle.bounding_box()
                assert graph_box and handle_box, {"graph": graph_box, "handle": handle_box}
                start_x = handle_box["x"] + handle_box["width"] / 2
                start_y = handle_box["y"] + handle_box["height"] / 2
                target_x = graph_box["x"] + graph_box["width"] * 0.436
                target_y = graph_box["y"] + graph_box["height"] * 0.82
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(target_x, target_y, steps=8)
                page.mouse.up()
                page.wait_for_function(
                    """() => {
                      const input = document.querySelector('[aria-label="关键帧 1 x1"]');
                      return !!input && Math.abs(Number(input.value) - 0.42) < 0.025;
                    }""",
                    timeout=10000,
                )

                original_revision = fd.read_project_film(project_id)["rev"]
                page.locator("#fs-apply-clip-inspector").click()
                page.wait_for_function(
                    """oldRevision => {
                      const label = document.querySelector('#fs-project-label');
                      return !!label && !label.textContent.includes('rev ' + oldRevision);
                    }""",
                    arg=original_revision,
                    timeout=15000,
                )
                saved = fd.read_project_film(project_id)
                saved_clip = next(
                    item for track in saved["doc"]["tracks"]
                    for item in track["clips"] if item["id"] == "scene-clip"
                )
                saved_channels = json.loads(saved_clip["attrs"]["data-film-keyframes"])
                opacity = next(channel for channel in saved_channels if channel["property"] == "opacity")
                easing = opacity["keyframes"][0]["easing"]
                assert easing["type"] == "bezier"
                assert abs(float(easing["x1"]) - 0.42) < 0.025, easing
                assert abs(float(easing["y1"]) - 0.1) < 0.025, easing
                assert abs(float(easing["x2"]) - 0.8) < 0.025, easing
                assert abs(float(easing["y2"]) - 0.9) < 0.025, easing
                assert opacity["keyframes"][1]["value"] == 0.25
                saved_revision = saved["rev"]

                # A true browser reload must rebuild the control from the persisted film.html.
                page.reload(wait_until="domcontentloaded")
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function(
                    "() => { const select = document.querySelector('#editor-project'); return !!select && select.options.length > 0; }"
                )
                page.evaluate("""pid => {
                  const select = document.querySelector('#editor-project');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                }""", project_id)
                page.evaluate("window.filmStudioToggle()")
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="scene-clip"]').wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="scene-clip"]').click(
                    position={"x": 40, "y": 10}
                )
                page.locator('#fs-inspector [data-react-clip-inspector="true"]').wait_for(
                    state="attached", timeout=10000
                )
                control_x_after_reload = page.locator('[aria-label="关键帧 1 x1"]')
                control_x_after_reload.wait_for(state="visible", timeout=10000)
                assert abs(float(control_x_after_reload.input_value()) - 0.42) < 0.025
                assert fd.read_project_film(project_id)["rev"] == saved_revision
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
