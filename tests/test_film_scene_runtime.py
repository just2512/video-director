from __future__ import annotations

from video_director.film_scene_runtime import (
    inject_scene_css,
    inject_scene_runtime,
)


def test_scene_bridge_is_injected_before_author_scripts():
    source = """<!doctype html><html><head><meta charset="utf-8"><script>window.authored = true;</script></head><body></body></html>"""
    result = inject_scene_runtime(source, "film-test", "scenes/title.html")
    assert result.index('data-vd-film-bridge="1"') < result.index("window.authored = true")
    assert "window.film.frame(t)" in result
    assert "film-frame:ready" in result


def test_scene_relative_media_paths_are_rewritten_inside_project_boundary():
    source = """<html><head><style>.card{background-image:url('../assets/card.png')}</style></head><body><img src="../assets/logo.png"><video poster="../assets/poster.jpg"></video><a href="#credits">credits</a><img src="data:image/png;base64,AAAA"><img src="https://example.com/remote.png"></body></html>"""
    result = inject_scene_runtime(source, "film-test", "scenes/title.html")
    assert "src=\"/api/editor/film/media?project_id=film-test&amp;src=assets%2Flogo.png\"" in result
    assert "poster=\"/api/editor/film/media?project_id=film-test&amp;src=assets%2Fposter.jpg\"" in result
    assert "url('/api/editor/film/media?project_id=film-test&src=assets%2Fcard.png')" in result
    assert 'href="#credits"' in result
    assert "data:image/png;base64,AAAA" in result
    assert "https://example.com/remote.png" in result


def test_css_rewriter_resolves_paths_relative_to_the_stylesheet():
    source = ".hero { background: url('../images/hero.webp'); mask: url(#shape); }"
    result = inject_scene_css(source, "film-test", "scenes/css/theme.css", "/media")
    assert "url('/media?project_id=film-test&src=scenes%2Fimages%2Fhero.webp')" in result
    assert "url(#shape)" in result


def test_bridge_contract_includes_exact_time_and_deterministic_primitives():
    source = inject_scene_runtime(
        "<html><head></head><body></body></html>",
        "film-test",
        "scenes/motion.html",
    )
    assert "NativeDate" in source
    assert "Math.random = function" in source
    assert "window.performance, 'now'" in source
    assert "animation.currentTime = t * 1000" in source
    assert "window.film.frame(t)" in source


def test_frame_protocol_executes_scene_at_the_requested_local_time():
    from playwright.sync_api import sync_playwright

    scene = inject_scene_runtime(
        """<!doctype html><html><head></head><body><script>
        window.film = {
          ready: Promise.resolve(),
          frame: function (t) {
            document.body.dataset.frameTime = String(t);
            parent.postMessage({type: 'scene-drew', time: t}, '*');
          }
        };
        </script></body></html>""",
        "film-test",
        "scenes/motion.html",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.set_content("""<!doctype html><html><body>
          <iframe id="scene" sandbox="allow-scripts"></iframe>
          <script>
            window.__sceneLoaded = false;
            window.__sceneReply = null;
            window.__sceneDrew = null;
            var sceneFrame = document.getElementById('scene');
            sceneFrame.addEventListener('load', function () { window.__sceneLoaded = true; });
            window.addEventListener('message', function (event) {
              if (event.source !== sceneFrame.contentWindow || !event.data) return;
              if (event.data.type === 'film-frame:ready') window.__sceneReply = event.data;
              if (event.data.type === 'scene-drew') window.__sceneDrew = event.data;
            });
          </script>
        </body></html>""")
        page.locator("#scene").evaluate("(frame, source) => { frame.srcdoc = source; }", scene)
        page.wait_for_function("window.__sceneLoaded === true", timeout=10000)
        page.evaluate("""() => document.getElementById('scene').contentWindow.postMessage(
          {type:'film-frame', time:1.25, frame:7}, '*')""")
        page.wait_for_function("window.__sceneReply !== null", timeout=10000)
        reply = page.evaluate("window.__sceneReply")
        drew = page.evaluate("window.__sceneDrew")
        assert reply["supported"] is True
        assert reply["error"] is None
        assert reply["frame"] == 7
        assert reply["time"] == 1.25
        assert drew == {"type": "scene-drew", "time": 1.25}
        browser.close()


def test_scene_inspector_selects_html_elements_and_applies_safe_overrides():
    from playwright.sync_api import sync_playwright

    scene = inject_scene_runtime(
        """<!doctype html><html><head><style>body{margin:0}</style></head>
        <body><h1 id="title" style="position:absolute;left:12px;top:15px">Original title</h1>
        <script>
        window.film={ready:Promise.resolve(),frame:function(t){
          document.body.dataset.frameTime=String(t);
          document.getElementById('title').textContent='Frame '+t;
        }};
        window.addEventListener('message',function(event){
          if(event.data&&event.data.type==='film-overrides'){
            setTimeout(function(){
              var title=document.getElementById('title'),style=getComputedStyle(title);
              parent.postMessage({type:'scene-snapshot',text:title.textContent,color:style.color,
                left:style.left,fontSize:style.fontSize,frameTime:document.body.dataset.frameTime},'*');
            },0);
          }
        });
        </script></body></html>""",
        "film-test",
        "scenes/title.html",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.set_content("""<!doctype html><html><body>
          <iframe id="scene" sandbox="allow-scripts"></iframe>
          <script>
            window.__selection = null;
            window.__frameReply = null;
            window.__snapshot = null;
            window.__dragEnd = null;
            var frame = document.getElementById('scene');
            window.addEventListener('message', function (event) {
              if (event.source !== frame.contentWindow || !event.data) return;
              if (event.data.type === 'film-element-selected') window.__selection = event.data;
              if (event.data.type === 'film-frame:ready') window.__frameReply = event.data;
              if (event.data.type === 'scene-snapshot') window.__snapshot = event.data;
              if (event.data.type === 'film-element-drag-end') window.__dragEnd = event.data;
            });
            frame.addEventListener('load', function () {
              frame.contentWindow.postMessage({type:'film-inspect', enabled:true}, '*');
              frame.contentWindow.postMessage({type:'film-frame', time:2.5, frame:4}, '*');
            });
          </script>
        </body></html>""")
        page.locator("#scene").evaluate("(frame, source) => { frame.srcdoc = source; }", scene)
        page.wait_for_function("window.__frameReply !== null", timeout=10000)
        page.frame_locator("#scene").locator("#title").click()
        page.wait_for_function("window.__selection !== null", timeout=10000)
        selection = page.evaluate("window.__selection")
        assert selection["selector"] == "#title"
        assert selection["textEditable"] is True
        assert selection["tag"] == "h1"
        assert selection["rect"]["width"] > 0

        # A pointer gesture over the selected HTML element must produce a
        # persistent move/resize proposal rather than only a selection outline.
        box = page.frame_locator("#scene").locator("#title").bounding_box()
        assert box and box["width"] > 0 and box["height"] > 0
        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(start_x + 40, start_y + 25, steps=4)
        page.mouse.up()
        page.wait_for_function("window.__dragEnd !== null", timeout=10000)
        drag_end = page.evaluate("window.__dragEnd")
        assert drag_end["styles"]["position"] == "fixed"
        assert drag_end["styles"]["left"] == "52px"
        assert drag_end["styles"]["top"] == "40px"

        page.evaluate("""() => document.getElementById('scene').contentWindow.postMessage({
          type:'film-overrides',
          overrides:[{selector:'#title',text:'Edited title',style:{
            color:'#ff0000',position:'absolute',left:'40px',top:'30px',width:'250px',height:'44px',fontSize:'32px'
          }}]
        }, '*')""")
        page.wait_for_function("window.__snapshot !== null", timeout=10000)
        snapshot = page.evaluate("window.__snapshot")
        assert snapshot["text"] == "Edited title"
        assert snapshot["color"] == "rgb(255, 0, 0)"
        assert snapshot["left"] == "40px"
        assert snapshot["fontSize"] == "32px"
        assert snapshot["frameTime"] == "2.5"
        browser.close()


def test_scene_bridge_rejects_unbounded_request_animation_frame_loops():
    from playwright.sync_api import sync_playwright

    scene = inject_scene_runtime(
        """<!doctype html><html><head></head><body><script>
        window.film = { frame: function (t) {
          function tick() { requestAnimationFrame(tick); }
          requestAnimationFrame(tick);
        }};
        </script></body></html>""",
        "film-test",
        "scenes/loop.html",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.set_content("""<!doctype html><html><body>
          <iframe id="scene" sandbox="allow-scripts"></iframe>
          <script>
            window.__reply = null;
            var frame = document.getElementById('scene');
            window.addEventListener('message', function (event) {
              if (event.source === frame.contentWindow && event.data
                  && event.data.type === 'film-frame:ready') window.__reply = event.data;
            });
            frame.addEventListener('load', function () {
              frame.contentWindow.postMessage({type:'film-frame',time:1,frame:3}, '*');
            });
          </script>
        </body></html>""")
        page.locator("#scene").evaluate("(frame, source) => { frame.srcdoc = source; }", scene)
        page.wait_for_function("window.__reply !== null", timeout=10000)
        reply = page.evaluate("window.__reply")
        assert reply["supported"] is True
        assert "超过 8 轮" in reply["error"]
        browser.close()


def test_preview_seeks_are_coalesced_and_exact_frame_requests_are_serialized():
    from playwright.sync_api import sync_playwright

    scene = inject_scene_runtime(
        """<!doctype html><html><head></head><body><script>
        window.__drawing = false;
        window.__overlap = false;
        window.film = {frame: async function(t) {
          if (window.__drawing) window.__overlap = true;
          window.__drawing = true;
          await new Promise(function(resolve) { setTimeout(resolve, 30); });
          document.body.dataset.lastFrame = String(t);
          window.__drawing = false;
          parent.postMessage({type:'scene-drew',time:t,overlap:window.__overlap}, '*');
        }};
        </script></body></html>""",
        "film-test",
        "scenes/async.html",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.set_content("""<!doctype html><html><body>
          <iframe id="scene" sandbox="allow-scripts"></iframe>
          <script>
            window.__draws = [];
            window.__acks = [];
            var frame = document.getElementById('scene');
            window.addEventListener('message', function(event) {
              if (event.source !== frame.contentWindow || !event.data) return;
              if (event.data.type === 'scene-drew') window.__draws.push(event.data);
              if (event.data.type === 'film-frame:ready') window.__acks.push(event.data);
            });
            frame.addEventListener('load', function() {
              frame.contentWindow.postMessage({type:'film-time',time:1}, '*');
              setTimeout(function() {
                frame.contentWindow.postMessage({type:'film-time',time:2}, '*');
                frame.contentWindow.postMessage({type:'film-time',time:3}, '*');
                frame.contentWindow.postMessage({type:'film-frame',time:9,frame:17}, '*');
              }, 1);
            });
          </script>
        </body></html>""")
        page.locator("#scene").evaluate("(frame, source) => { frame.srcdoc = source; }", scene)
        page.wait_for_function("window.__acks.some(item => item.frame === 17)", timeout=15000)
        draws = page.evaluate("window.__draws")
        acknowledgements = page.evaluate("window.__acks")
        exact = next(item for item in acknowledgements if item["frame"] == 17)
        assert exact["time"] == 9
        assert exact["supported"] is True
        assert exact["error"] is None
        assert draws
        assert all(item["overlap"] is False for item in draws)
        assert draws[-1]["time"] == 9
        browser.close()

