"""Real browser coverage for deterministic, seek-based animated mask rendering."""
from __future__ import annotations

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

                def seek_and_read(time_value: float) -> list[float]:
                    page.evaluate(
                        "(time) => window.postMessage("
                        "{type:'film-runtime:seek-pause',time},window.location.origin)",
                        time_value,
                    )
                    page.wait_for_function(
                        "() => !!document.querySelector('#mask-clip')?.style.clipPath",
                        timeout=3000,
                    )
                    return page.eval_on_selector(
                        "#mask-clip",
                        """el => (el.style.clipPath.match(/\\d+(?:\\.\\d+)?%/g) || [])
                          .map(value => Number.parseFloat(value))""",
                    )

                at_zero = seek_and_read(0)
                at_one = seek_and_read(1)
                at_half = seek_and_read(0.5)
                at_zero_again = seek_and_read(0)

                assert at_zero == [0, 0, 50, 0, 50, 100, 0, 100], at_zero
                assert at_one == [50, 0, 100, 0, 100, 100, 50, 100], at_one
                assert at_half == [25, 0, 75, 0, 75, 100, 25, 100], at_half
                assert at_zero_again == at_zero, "backward seek did not restore the exact earlier mask"
                assert not errors, f"runtime JavaScript errors: {errors}"
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
