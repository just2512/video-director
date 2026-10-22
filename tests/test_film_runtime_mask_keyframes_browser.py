"""Real browser coverage for deterministic, seek-based animated mask rendering."""
from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import pytest


def test_film_runtime_interpolates_mask_keyframes_on_forward_and_backward_seek():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    import webui.server as web_server

    payload = {
        "exists": True,
        "rev": "mask-runtime-rev",
        "stage": {"width": 100, "height": 100},
        "duration": 1,
        "render_css": "",
        "tracks": [{
            "id": "track-mask",
            "attrs": {},
            "clips": [{
                "id": "mask-clip",
                "tag": "img",
                "at": 0,
                "end": 1,
                "duration": 1,
                "attrs": {
                    "id": "mask-clip",
                    "at": "0",
                    "style": (
                        "left:0px;top:0px;width:100px;height:100px;"
                        "object-fit:contain;clip-path:polygon(0% 0%, 50% 0%, "
                        "50% 100%, 0% 100%)"
                    ),
                    "data-film-mask-keyframes": (
                        '[{"at":0,"easing":"linear","value":'
                        '[[0,0],[0.5,0],[0.5,1],[0,1]]},'
                        '{"at":1,"easing":"linear","value":'
                        '[[0.5,0],[1,0],[1,1],[0.5,1]]}]'
                    ),
                    "data-film-transform": json.dumps({
                        "x": 0.25, "y": 0.5, "scale": 1, "rotation": 0, "opacity": 1,
                        "anchor": "center", "stage_width": 100, "stage_height": 100,
                        "base_width": 20, "base_height": 20,
                    }, separators=(",", ":")),
                    "data-film-keyframes": json.dumps([{
                        "property": "x",
                        "keyframes": [
                            {"at": 0, "value": 0.25, "easing": "linear"},
                            {"at": 1, "value": 0.75, "easing": "linear"},
                        ],
                    }], separators=(",", ":")),
                },
                "src_in": 0,
                "src_out": 1,
                "media_url": None,
            }],
        }],
    }

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 500, "height": 500})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/static/film-runtime.html",
                    wait_until="load",
                )
                assert response and response.status == 200
                page.evaluate("""() => {
                  window.__filmRuntimeMessages = [];
                  window.addEventListener('message', event => {
                    if (event.origin === window.location.origin) {
                      window.__filmRuntimeMessages.push(event.data);
                    }
                  });
                }""")
                page.evaluate(
                    "(data) => window.postMessage("
                    "{type:'film-runtime:load',data,rev:'mask-runtime-rev'},"
                    "window.location.origin)",
                    payload,
                )
                page.wait_for_function(
                    "() => window.__filmRuntimeMessages.some("
                    "m => m.type === 'film-runtime:loaded' && m.rev === 'mask-runtime-rev')",
                    timeout=10000,
                )

                def seek_and_read(time_value: float) -> dict:
                    page.evaluate(
                        """time => {
                          window.__filmRuntimeMessages = [];
                          window.postMessage(
                            {type:'film-runtime:seek-pause', time},
                            window.location.origin
                          );
                        }""",
                        time_value,
                    )
                    page.wait_for_function(
                        """time => window.__filmRuntimeMessages.some(message =>
                          message.type === 'film-runtime:paused'
                          && Math.abs(Number(message.time) - time) < 0.0001)""",
                        arg=time_value,
                        timeout=3000,
                    )
                    return page.eval_on_selector(
                        "#mask-clip",
                        """el => ({
                          mask: (el.style.clipPath.match(/\d+(?:\.\d+)?%/g) || [])
                            .map(value => Number.parseFloat(value)),
                          left: Number.parseFloat(el.style.left),
                          width: Number.parseFloat(el.style.width)
                        })""",
                    )

                at_zero = seek_and_read(0)
                at_one = seek_and_read(1)
                at_half = seek_and_read(0.5)
                at_zero_again = seek_and_read(0)

                assert at_zero["mask"] == [0, 0, 50, 0, 50, 100, 0, 100], at_zero
                assert at_one["mask"] == [50, 0, 100, 0, 100, 100, 50, 100], at_one
                assert at_half["mask"] == [25, 0, 75, 0, 75, 100, 25, 100], at_half
                assert at_zero_again == at_zero, "backward seek did not restore the exact earlier mask"
                assert at_zero["left"] == pytest.approx(15)
                assert at_half["left"] == pytest.approx(40)
                assert at_one["left"] == pytest.approx(65)
                assert at_zero["width"] == at_half["width"] == at_one["width"] == 20
                assert not errors, f"runtime JavaScript errors: {errors}"
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_film_runtime_keeps_composition_mask_stationary_while_clip_transform_moves():
    """A stage-space mask must not be transformed with the media clip."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    import webui.server as web_server

    payload = {
        "exists": True,
        "rev": "composition-mask-rev",
        "stage": {"width": 100, "height": 100},
        "duration": 1,
        "render_css": "",
        "tracks": [{
            "id": "track-mask",
            "attrs": {},
            "clips": [{
                "id": "mask-clip",
                "tag": "img",
                "at": 0,
                "end": 1,
                "duration": 1,
                "attrs": {
                    "id": "mask-clip",
                    "at": "0",
                    "style": (
                        "left:0px;top:0px;width:100px;height:100px;"
                        "object-fit:contain;clip-path:polygon(0% 0%, 50% 0%, "
                        "50% 100%, 0% 100%)"
                    ),
                    "data-film-mask-space": "composition",
                    "data-film-mask-keyframes": (
                        '[{"at":0,"easing":"linear","value":'
                        '[[0,0],[0.5,0],[0.5,1],[0,1]]},'
                        '{"at":1,"easing":"linear","value":'
                        '[[0.5,0],[1,0],[1,1],[0.5,1]]}]'
                    ),
                    "data-film-transform": json.dumps({
                        "x": 0.25, "y": 0.5, "scale": 1, "rotation": 0, "opacity": 1,
                        "anchor": "center", "stage_width": 100, "stage_height": 100,
                        "base_width": 20, "base_height": 20, "scale_x": 1.5, "scale_y": 0.75,
                    }, separators=(",", ":")),
                    "data-film-keyframes": json.dumps([
                        {"property": "x", "keyframes": [
                            {"at": 0, "value": 0.25, "easing": "linear"},
                            {"at": 1, "value": 0.75, "easing": "linear"},
                        ]},
                        {"property": "scale_x", "keyframes": [
                            {"at": 0, "value": 1.5, "easing": "linear"},
                            {"at": 1, "value": 2.0, "easing": "linear"},
                        ]},
                        {"property": "scale_y", "keyframes": [
                            {"at": 0, "value": 0.75, "easing": "linear"},
                            {"at": 1, "value": 1.0, "easing": "linear"},
                        ]},
                    ], separators=(",", ":")),
                },
                "src_in": 0,
                "src_out": 1,
                "media_url": None,
            }],
        }],
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 500, "height": 500})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/static/film-runtime.html",
                    wait_until="load",
                )
                assert response and response.status == 200
                page.evaluate("""() => {
                  window.__filmRuntimeMessages = [];
                  window.addEventListener('message', event => {
                    if (event.origin === window.location.origin) window.__filmRuntimeMessages.push(event.data);
                  });
                }""")
                page.evaluate(
                    "(data) => window.postMessage({type:'film-runtime:load',data,rev:'composition-mask-rev'},window.location.origin)",
                    payload,
                )
                page.wait_for_function(
                    "() => window.__filmRuntimeMessages.some(m => m.type === 'film-runtime:loaded' && m.rev === 'composition-mask-rev')",
                    timeout=10000,
                )

                def read_at(time_value: float) -> dict:
                    page.evaluate("""time => {
                      window.__filmRuntimeMessages = [];
                      window.postMessage({type:'film-runtime:seek-pause',time},window.location.origin);
                    }""", time_value)
                    page.wait_for_function(
                        "(time) => window.__filmRuntimeMessages.some(m => m.type === 'film-runtime:paused' && Math.abs(Number(m.time)-time)<0.0001)",
                        arg=time_value, timeout=3000,
                    )
                    return page.eval_on_selector("#mask-clip", """el => {
                      const host=el.parentElement;
                      return {
                        mask:(host.style.clipPath.match(/\\d+(?:\\.\\d+)?%/g)||[]).map(v=>Number.parseFloat(v)),
                        hostLeft:host.style.left,
                        hostWidth:Number.parseFloat(host.style.width),
                        localMask:el.style.clipPath,
                        left:Number.parseFloat(el.style.left),
                        width:Number.parseFloat(el.style.width),
                        height:Number.parseFloat(el.style.height)
                      };
                    }""")

                at_zero, at_half, at_one = read_at(0), read_at(0.5), read_at(1)
                assert at_zero["mask"] == [0, 0, 50, 0, 50, 100, 0, 100], at_zero
                assert at_half["mask"] == [25, 0, 75, 0, 75, 100, 25, 100], at_half
                assert at_one["mask"] == [50, 0, 100, 0, 100, 100, 50, 100], at_one
                assert [at_zero["left"], at_half["left"], at_one["left"]] == [10, 32.5, 55]
                assert [at_zero["width"], at_half["width"], at_one["width"]] == [30, 35, 40]
                assert [at_zero["height"], at_half["height"], at_one["height"]] == [15, 17.5, 20]
                assert at_zero["hostLeft"] == at_half["hostLeft"] == at_one["hostLeft"] == "0px"
                assert at_zero["hostWidth"] == at_half["hostWidth"] == at_one["hostWidth"] == 100
                assert at_zero["localMask"] == at_half["localMask"] == at_one["localMask"] == ""
                assert not errors, f"browser JavaScript errors: {errors}"
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_film_runtime_applies_visual_keyframes_without_explicit_transform_metadata():
    """Ordinary film.html clips can animate without first adding data-film-transform."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    import webui.server as web_server

    payload = {
        "exists": True,
        "rev": "implicit-keyframe-transform",
        "stage": {"width": 100, "height": 100},
        "duration": 1,
        "render_css": "",
        "tracks": [{
            "id": "track-visual",
            "attrs": {},
            "clips": [
                {
                    "id": "opacity-clip", "tag": "img", "at": 0, "end": 1, "duration": 1,
                    "attrs": {
                        "id": "opacity-clip", "at": "0",
                        "style": "position:absolute;left:20px;top:30px;width:20px;height:20px;opacity:1",
                        "data-film-keyframes": json.dumps([{
                            "property": "opacity",
                            "keyframes": [
                                {"at": 0, "value": 1, "easing": "linear"},
                                {"at": 1, "value": 0, "easing": "linear"},
                            ],
                        }], separators=(",", ":")),
                    },
                    "src_in": 0, "src_out": 1, "media_url": None,
                },
                {
                    "id": "position-clip", "tag": "img", "at": 0, "end": 1, "duration": 1,
                    "attrs": {
                        "id": "position-clip", "at": "0",
                        "style": "position:absolute;left:20px;top:30px;width:20px;height:20px",
                        "data-film-keyframes": json.dumps([{
                            "property": "x",
                            "keyframes": [
                                {"at": 0, "value": 0.25, "easing": "linear"},
                                {"at": 1, "value": 0.75, "easing": "linear"},
                            ],
                        }], separators=(",", ":")),
                    },
                    "src_in": 0, "src_out": 1, "media_url": None,
                },
            ],
        }],
    }

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 500, "height": 500})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/static/film-runtime.html",
                    wait_until="load",
                )
                assert response and response.status == 200
                page.evaluate("""() => {
                  window.__filmRuntimeMessages = [];
                  window.addEventListener('message', event => {
                    if (event.origin === window.location.origin) window.__filmRuntimeMessages.push(event.data);
                  });
                }""")
                page.evaluate(
                    "(data) => window.postMessage({type:'film-runtime:load',data,rev:'implicit-keyframe-transform'},window.location.origin)",
                    payload,
                )
                page.wait_for_function(
                    "() => window.__filmRuntimeMessages.some(m => m.type === 'film-runtime:loaded' && m.rev === 'implicit-keyframe-transform')",
                    timeout=10000,
                )

                def seek(time_value: float) -> dict:
                    page.evaluate(
                        """time => {
                          window.__filmRuntimeMessages = [];
                          window.postMessage({type:'film-runtime:seek-pause',time},window.location.origin);
                        }""",
                        time_value,
                    )
                    page.wait_for_function(
                        """time => window.__filmRuntimeMessages.some(m =>
                          m.type === 'film-runtime:paused' && Math.abs(Number(m.time)-time)<0.0001)""",
                        arg=time_value,
                        timeout=3000,
                    )
                    return page.evaluate("""() => ({
                      opacity:Number(document.getElementById('opacity-clip').style.opacity),
                      left:Number.parseFloat(document.getElementById('position-clip').style.left)
                    })""")

                start = seek(0)
                middle = seek(0.5)
                end = seek(1)
                assert start["opacity"] == pytest.approx(1)
                assert middle["opacity"] == pytest.approx(0.5)
                assert end["opacity"] == pytest.approx(0)
                assert start["left"] == pytest.approx(15)
                assert middle["left"] == pytest.approx(40)
                assert end["left"] == pytest.approx(65)
                assert not errors, f"runtime JavaScript errors: {errors}"
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
