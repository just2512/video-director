from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image


def _write_html_film_project(tmp_path: Path, monkeypatch) -> tuple[str, str]:
    from video_director import config, film_document as fd

    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "html-scene-e2e"
    project = config.project_dir(project_id)
    (project / "scenes").mkdir(parents=True, exist_ok=True)
    (project / "assets").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 20), (255, 0, 0)).save(project / "assets" / "moving-red.png")
    film_html = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=160,height=90">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden}</style></head>
<body><section id="visual" data-label="网页镜头">
<iframe id="scene-opening" src="scenes/opening.html#t=0,1" at="0"
 style="left:0px;top:0px;width:160px;height:90px"></iframe>
</section>
<section id="moving-red-track" data-label="红色动态蒙版层">
<img id="moving-red" src="assets/moving-red.png#t=0,1" at="0"
 data-film-transform='{"x":0.125,"y":0.5,"scale":1,"rotation":0,"opacity":1,"anchor":"center","stage_width":160,"stage_height":90,"base_width":20,"base_height":20}'
 data-film-keyframes='[{"property":"x","keyframes":[{"at":0,"value":0.125,"easing":"linear"},{"at":1,"value":0.75,"easing":"linear"}]},{"property":"camera_x","keyframes":[{"at":0,"value":0.5,"easing":"linear"},{"at":1,"value":0.45,"easing":"linear"}]},{"property":"camera_scale","keyframes":[{"at":0,"value":1,"easing":"linear"},{"at":1,"value":1.1,"easing":"linear"}]}]'
 data-film-mask-keyframes='[{"at":0,"easing":"linear","value":[[0,0],[0.55,0],[0.55,1],[0,1]]},{"at":1,"easing":"linear","value":[[0.45,0],[1,0],[1,1],[0.45,1]]}]'
 style="left:10px;top:35px;width:20px;height:20px;opacity:1;object-fit:contain;transform-origin:50% 50%;rotate:0deg"/>
</section></body></html>'''
    scene_html = '''<!doctype html>
<html><head><meta charset="utf-8"><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden}
#background{position:fixed;inset:0;background:#112233}
#title{position:fixed;left:8px;top:8px;color:#ffffff;font:700 14px sans-serif}
</style></head><body>
<div id="background"></div><h1 id="title">Opening</h1>
<script>
window.film={duration:1,ready:document.fonts.ready,frame:function(t){
  document.getElementById('background').style.backgroundColor=t<0.25?'rgb(17,34,51)':'rgb(51,34,17)';
  document.getElementById('title').textContent=t<0.25?'Opening':'Second beat';
}};
</script></body></html>'''
    (project / fd.FILM_NAME).write_text(film_html, encoding="utf-8")
    (project / "scenes" / "opening.html").write_text(scene_html, encoding="utf-8")
    state = fd.read_project_film(project_id)
    assert state["exists"]
    return project_id, state["rev"]


def test_film_render_accepts_frame_driven_html_scene_and_pins_revision(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from video_director import film_document as fd, film_render, prober, renderer

    try:
        renderer._ffmpeg()
    except Exception as exc:  # pragma: no cover - environment-dependent binary
        pytest.skip(f"FFmpeg is unavailable in this environment: {exc}")

    project_id, revision = _write_html_film_project(tmp_path, monkeypatch)
    look = film_render.look_project_film(project_id, expected_revision=revision, times=[0.0, 0.5, 0.99])
    assert look["ok"] is True
    assert look["revision"] == revision
    assert look["determinism_status"] == "PASS"
    assert Path(look["image_path"]).is_file()

    # The migrated FilmRuntime must apply frame(t)-driven Transform keyframes
    # to the actual rendered element, not merely persist the JSON attributes.
    with Image.open(look["image_path"]) as contact_sheet:
        sheet = contact_sheet.convert("RGB")
        assert sheet.width >= 1920 and sheet.height >= 360

        def red_centroid(tile):
            pixels = [
                (x, y)
                for y in range(tile.height)
                for x in range(tile.width)
                if (lambda rgb: rgb[0] > 220 and rgb[1] < 60 and rgb[2] < 60)(tile.getpixel((x, y)))
            ]
            assert len(pixels) >= 200, f"red moving layer is absent from the contact sheet ({len(pixels)} pixels)"
            return sum(x for x, _ in pixels) / len(pixels)

        first_tile = sheet.crop((0, 0, 640, 360))
        second_tile = sheet.crop((640, 0, 1280, 360))
        third_tile = sheet.crop((1280, 0, 1920, 360))
        first_center = red_centroid(first_tile)
        second_center = red_centroid(second_tile)
        third_center = red_centroid(third_tile)
        assert second_center - first_center > 150, (
            f"Transform keyframes were not applied at t=0.5s: {first_center=}, {second_center=}"
        )
        assert third_center - first_center > 150, (
            f"Transform keyframes were not applied at t=1s: {first_center=}, {third_center=}"
        )
        # The mask is left-half at t=0 and right-half at t=1. Normalize measured
        # centroids back to the 160px composition space and relative to the known
        # transform centers; opposite-signed offsets prove frame(t) applied the
        # dynamic polygon rather than merely saving its keyframes.
        first_offset = first_center / 4.0 - 20.0
        final_time = 0.99
        final_x = 0.125 + (0.75 - 0.125) * final_time
        final_camera_x = 0.5 + (0.45 - 0.5) * final_time
        final_camera_scale = 1.0 + (1.1 - 1.0) * final_time
        expected_final_center = (
            0.5 + (final_x - final_camera_x) * final_camera_scale
        ) * 160.0
        third_offset = third_center / 4.0 - expected_final_center
        assert first_offset < -2.0, f"left-half mask wasn't applied at t=0: offset={first_offset}"
        assert third_offset > 2.0, f"right-half mask wasn't applied at t={final_time}: offset={third_offset}"

    state = fd.read_project_film(project_id)
    result = film_render.render_project_film(project_id, state["rev"], fps=2)
    output = Path(result["mp4_path"])
    assert result["ok"] is True
    assert result["rendered_revision"] == revision
    assert result["current"] is True
    assert output.is_file() and output.stat().st_size > 0
    probe = prober.probe_media(str(output))
    assert probe.has_video_stream is True
    assert probe.duration is not None and abs(float(probe.duration) - 1.0) <= 0.15


def test_offline_render_guard_blocks_external_scene_network_requests():
    from video_director.film_render import (
        FilmRenderError,
        _install_offline_network_policy,
        _raise_for_external_scene_resources,
    )

    class FakePage:
        def route(self, pattern, handler):
            self.pattern = pattern
            self.handler = handler

    class FakeRequest:
        def __init__(self, url):
            self.url = url

    class FakeRoute:
        def __init__(self, url):
            self.request = FakeRequest(url)
            self.action = None

        def continue_(self):
            self.action = "continue"

        def abort(self):
            self.action = "abort"

    page = FakePage()
    blocked = _install_offline_network_policy(page, "http://127.0.0.1:4567")
    local = FakeRoute("http://127.0.0.1:4567/media?src=assets%2Ffont.woff2")
    external = FakeRoute("https://fonts.example.test/font.css")
    data_url = FakeRoute("data:image/png;base64,AAAA")
    page.handler(local)
    page.handler(external)
    page.handler(data_url)
    assert page.pattern == "**/*"
    assert local.action == "continue"
    assert external.action == "abort"
    assert data_url.action == "continue"
    assert blocked == ["https://fonts.example.test/font.css"]
    with pytest.raises(FilmRenderError) as raised:
        _raise_for_external_scene_resources(blocked)
    assert raised.value.code == "EXTERNAL_SCENE_RESOURCE"


def test_offline_scene_renderer_fails_for_missing_project_local_css_or_media_resources():
    from video_director.film_render import (
        FilmRenderError,
        _install_scene_resource_failure_monitor,
        _raise_for_scene_resource_failures,
    )

    class FakePage:
        def __init__(self):
            self.handlers = {}

        def on(self, event, handler):
            self.handlers[event] = handler

    class FakeRequest:
        def __init__(self, url, resource_type="stylesheet"):
            self.url = url
            self.resource_type = resource_type

    class FakeResponse:
        def __init__(self, url, status, resource_type="stylesheet"):
            self.url = url
            self.status = status
            self.request = FakeRequest(url, resource_type)

    base_url = "http://127.0.0.1:4567"
    page = FakePage()
    failures = _install_scene_resource_failure_monitor(page, base_url)
    page.handlers["response"](FakeResponse(base_url + "/media?src=assets%2Ftheme.css", 404))
    page.handlers["response"](FakeResponse(base_url + "/media?src=assets%2Ffont.woff2", 200))
    page.handlers["response"](FakeResponse(base_url + "/assets/missing-style.css", 404, "stylesheet"))
    page.handlers["response"](FakeResponse("https://remote.example/font.css", 404))
    page.handlers["requestfailed"](FakeRequest(base_url + "/media?src=assets%2Fbackground.webp", "image"))

    assert len(failures) == 3
    with pytest.raises(FilmRenderError) as error:
        _raise_for_scene_resource_failures(failures)
    assert error.value.code == "SCENE_RESOURCE_FAILED"
    assert "theme.css" in str(error.value)
    assert "missing-style.css" in str(error.value)
    assert "background.webp" in str(error.value)
