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
        <script>window.film={ready:Promise.resolve(),frame:function(t){
          document.body.dataset.frameTime=String(t);
          document.getElementById('title').textContent='Frame '+t;
        }};</script></body></html>""",
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
            var frame = document.getElementById('scene');
            window.addEventListener('message', function (event) {
              if (event.source === frame.contentWindow && event.data
                  && event.data.type === 'film-element-selected') window.__selection = event.data;
            });
            frame.addEventListener('load', function () {
              frame.contentWindow.postMessage({type:'film-inspect', enabled:true}, '*');
              frame.contentWindow.postMessage({type:'film-frame', time:2.5, frame:4}, '*');
            });
          </script>
        </body></html>""")
        page.locator("#scene").evaluate("(frame, source) => { frame.srcdoc = source; }", scene)
        page.wait_for_function("document.getElementById('scene').contentDocument.readyState === 'complete'", timeout=10000)
        page.frame_locator("#scene").locator("#title").click()
        page.wait_for_function("window.__selection !== null", timeout=10000)
        selection = page.evaluate("window.__selection")
        assert selection["selector"] == "#title"
        assert selection["textEditable"] is True
        assert selection["tag"] == "h1"
        assert selection["rect"]["width"] > 0

        page.evaluate("""() => document.getElementById('scene').contentWindow.postMessage({
          type:'film-overrides',
          overrides:[{selector:'#title',text:'Edited title',style:{
            color:'#ff0000',position:'absolute',left:'40px',top:'30px',width:'250px',height:'44px',fontSize:'32px'
          }}]
        }, '*')""")
        page.wait_for_function("""() => {
          const frame = document.getElementById('scene');
          try {
            const title = frame.contentDocument.querySelector('#title');
            return title && title.textContent === 'Edited title'
              && getComputedStyle(title).color === 'rgb(255, 0, 0)'
              && getComputedStyle(title).left === '40px'
              && getComputedStyle(title).fontSize === '32px';
          } catch (_) { return false; }
        }""", timeout=10000)
        browser.close()

