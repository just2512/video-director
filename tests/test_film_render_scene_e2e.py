from __future__ import annotations

from pathlib import Path
import json
import subprocess

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
    Image.new("RGB", (20, 20), (0, 0, 255)).save(project / "assets" / "moving-blue.png")
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
</section>
<section id="moving-blue-track" data-label="非线性贝塞尔验证层">
<img id="moving-blue" src="assets/moving-blue.png#t=0,1" at="0"
 data-film-transform='{"x":0.125,"y":0.15,"scale":1,"rotation":0,"opacity":1,"anchor":"center","stage_width":160,"stage_height":90,"base_width":20,"base_height":20}'
 data-film-keyframes='[{"property":"x","keyframes":[{"at":0,"value":0.125,"easing":"linear"},{"at":1,"value":0.75,"easing":{"type":"bezier","x1":0.42,"y1":0,"x2":0.58,"y2":1}}]}]'
 style="left:10px;top:0px;width:20px;height:20px;opacity:1;object-fit:contain;transform-origin:50% 50%;rotate:0deg"/>
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
    # Four exported timestamps have exact corresponding browser-look samples; 0.99 remains a near-endpoint keyframe/mask check.
    sample_times = [0.0, 0.25, 0.5, 0.75, 0.99]
    look = film_render.look_project_film(project_id, expected_revision=revision, times=sample_times)
    assert look["ok"] is True
    assert look["revision"] == revision
    assert look["determinism_status"] == "PASS"
    assert Path(look["image_path"]).is_file()

    # The migrated FilmRuntime must apply frame(t)-driven Transform keyframes
    # to the actual rendered element, not merely persist the JSON attributes.
    with Image.open(look["image_path"]) as contact_sheet:
        sheet = contact_sheet.convert("RGB")
        # look sheets use at most three 640x360 tiles per row, with a 28px label band.
        assert sheet.width >= 1920 and sheet.height >= 776

        def red_centroid_xy(tile, *, minimum_pixels=200):
            pixels = [
                (x, y)
                for y in range(tile.height)
                for x in range(tile.width)
                if (lambda rgb: rgb[0] > 220 and rgb[1] < 60 and rgb[2] < 60)(tile.getpixel((x, y)))
            ]
            assert len(pixels) >= minimum_pixels, (
                f"red moving layer is absent from the sample ({len(pixels)} pixels)"
            )
            return (
                sum(x for x, _ in pixels) / len(pixels),
                sum(y for _, y in pixels) / len(pixels),
            )

        def red_centroid(tile):
            return red_centroid_xy(tile)[0]

        def blue_centroid_xy(tile, *, minimum_pixels=200):
            pixels = [
                (x, y)
                for y in range(tile.height)
                for x in range(tile.width)
                if (lambda rgb: rgb[2] > 220 and rgb[0] < 60 and rgb[1] < 80)(tile.getpixel((x, y)))
            ]
            assert len(pixels) >= minimum_pixels, (
                f"blue Bezier layer is absent from the sample ({len(pixels)} pixels)"
            )
            return (
                sum(x for x, _ in pixels) / len(pixels),
                sum(y for _, y in pixels) / len(pixels),
            )

        def bezier_progress(progress):
            # Match film-runtime.js filmBezierEase() for cubic-bezier(.42, 0, .58, 1).
            x = min(1.0, max(0.0, float(progress)))
            x1, y1, x2, y2 = 0.42, 0.0, 0.58, 1.0
            def cubic(u, first, second):
                inverse = 1.0 - u
                return 3 * inverse * inverse * u * first + 3 * inverse * u * u * second + u * u * u
            if x <= 0 or x >= 1:
                return x
            low, high = 0.0, 1.0
            for _ in range(28):
                middle = (low + high) * 0.5
                if cubic(middle, x1, x2) < x:
                    low = middle
                else:
                    high = middle
            return min(1.0, max(0.0, cubic((low + high) * 0.5, y1, y2)))

        first_tile = sheet.crop((0, 0, 640, 360))
        quarter_tile = sheet.crop((640, 0, 1280, 360))
        second_tile = sheet.crop((1280, 0, 1920, 360))
        # The fourth and fifth samples wrap to row two (cell height = 360 + 28).
        threequarter_tile = sheet.crop((0, 388, 640, 748))
        third_tile = sheet.crop((640, 388, 1280, 748))
        first_center, _ = red_centroid_xy(first_tile)
        quarter_center, _ = red_centroid_xy(quarter_tile)
        second_center, second_center_y = red_centroid_xy(second_tile)
        threequarter_center, _ = red_centroid_xy(threequarter_tile)
        third_center, _ = red_centroid_xy(third_tile)

        # This unmasked blue layer isolates actual Bezier interpolation from the
        # red layer's independently animated composition mask and camera transform.
        look_samples = [
            (0.0, first_tile),
            (0.25, quarter_tile),
            (0.5, second_tile),
            (0.75, threequarter_tile),
        ]
        blue_centers = {}
        for timestamp, tile in look_samples:
            blue_x, _ = blue_centroid_xy(tile)
            blue_center = blue_x / 4.0
            expected_x = (0.125 + (0.75 - 0.125) * bezier_progress(timestamp)) * 160.0
            assert abs(blue_center - expected_x) <= 1.5, {
                "source": "browser look", "timestamp": timestamp,
                "expected_bezier_center": expected_x, "observed_center": blue_center,
            }
            linear_center = (0.125 + (0.75 - 0.125) * timestamp) * 160.0
            if timestamp in (0.25, 0.75):
                assert abs(blue_center - linear_center) >= 5.0, {
                    "timestamp": timestamp, "bezier_center": blue_center,
                    "linear_center": linear_center,
                }
            blue_centers[timestamp] = blue_center

        assert quarter_center > first_center + 40, (
            f"Transform keyframes were not applied at t=0.25s: {first_center=}, {quarter_center=}"
        )
        assert second_center > first_center + 150, (
            f"Transform keyframes were not applied at t=0.5s: {first_center=}, {second_center=}"
        )
        assert threequarter_center > quarter_center + 40, (
            f"Transform keyframes were not applied at t=0.75s: {quarter_center=}, {threequarter_center=}"
        )
        assert third_center - first_center > 150, (
            f"Transform keyframes were not applied at t=0.99s: {first_center=}, {third_center=}"
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
        preview_scene_pixel = second_tile.getpixel((600, 320))
        assert first_offset < -2.0, f"left-half mask wasn't applied at t=0: offset={first_offset}"
        assert third_offset > 2.0, f"right-half mask wasn't applied at t={final_time}: offset={third_offset}"

    state = fd.read_project_film(project_id)
    result = film_render.render_project_film(project_id, state["rev"], fps=4)
    output = Path(result["mp4_path"])
    assert result["ok"] is True
    assert result["rendered_revision"] == revision
    assert result["current"] is True
    assert output.is_file() and output.stat().st_size > 0
    probe = prober.probe_media(str(output))
    assert probe.has_video_stream is True
    assert probe.duration is not None and abs(float(probe.duration) - 1.0) <= 0.15

    # Compare each encoded frame at 0.00/0.25/0.50/0.75 seconds to the
    # corresponding independently captured browser-look tile.
    exported_pattern = tmp_path / "exported-frame-%02d.png"
    extracted = subprocess.run(
        [
            renderer._ffmpeg(), "-y", "-loglevel", "error", "-i", str(output),
            "-vf", r"select=between(n\,0\,3)", "-vsync", "0", "-frames:v", "4",
            str(exported_pattern),
        ],
        capture_output=True, text=True, timeout=45,
    )
    assert extracted.returncode == 0, extracted.stderr[-2000:]

    expected_samples = [
        (0.0, first_tile),
        (0.25, quarter_tile),
        (0.5, second_tile),
        (0.75, threequarter_tile),
    ]
    for frame_index, (timestamp, look_tile) in enumerate(expected_samples):
        exported_frame_path = tmp_path / f"exported-frame-{frame_index + 1:02d}.png"
        assert exported_frame_path.is_file() and exported_frame_path.stat().st_size > 0, {
            "frame_index": frame_index, "timestamp": timestamp,
            "files": sorted(path.name for path in tmp_path.glob("exported-frame-*.png")),
        }
        with Image.open(exported_frame_path) as exported:
            encoded_frame = exported.convert("RGB")
            assert encoded_frame.size == (160, 90), encoded_frame.size
            encoded_center_x, encoded_center_y = red_centroid_xy(encoded_frame, minimum_pixels=40)
            encoded_blue_x, _ = blue_centroid_xy(encoded_frame, minimum_pixels=40)
            look_blue_raw_x, _ = blue_centroid_xy(look_tile)
            look_blue_x = look_blue_raw_x / (look_tile.width / encoded_frame.width)
            assert abs(encoded_blue_x - look_blue_x) <= 1.5, {
                "timestamp": timestamp, "look_bezier_center": look_blue_x,
                "encoded_bezier_center": encoded_blue_x,
            }
            look_center_raw_x, look_center_raw_y = red_centroid_xy(look_tile)
            look_center_x = look_center_raw_x / (look_tile.width / encoded_frame.width)
            look_center_y = look_center_raw_y / (look_tile.height / encoded_frame.height)
            assert abs(encoded_center_x - look_center_x) <= 3.5, {
                "timestamp": timestamp, "look_center_x": look_center_x, "encoded_center_x": encoded_center_x,
            }
            assert abs(encoded_center_y - look_center_y) <= 3.5, {
                "timestamp": timestamp, "look_center_y": look_center_y, "encoded_center_y": encoded_center_y,
            }
            look_scene_pixel = look_tile.getpixel((600, 320))
            encoded_scene_pixel = encoded_frame.getpixel((150, 80))
            assert max(abs(int(look_channel) - int(encoded_channel))
                       for look_channel, encoded_channel in zip(look_scene_pixel, encoded_scene_pixel)) <= 18, {
                "timestamp": timestamp, "look_scene_pixel": look_scene_pixel,
                "encoded_scene_pixel": encoded_scene_pixel,
            }




def test_adjacent_clip_fades_match_browser_look_and_exported_mp4_frames(tmp_path, monkeypatch):
    """Fade-out/fade-in opacity is identical in browser preview and actual MP4 frames."""
    pytest.importorskip("playwright.sync_api")
    from video_director import config, film_document as fd, film_render, prober, renderer

    try:
        renderer._ffmpeg()
    except Exception as exc:  # pragma: no cover - environment-dependent binary
        pytest.skip(f"FFmpeg is unavailable in this environment: {exc}")

    project_id, _ = _write_html_film_project(tmp_path, monkeypatch)
    project = config.project_dir(project_id)
    (project / "assets").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), (255, 0, 0)).save(project / "assets" / "fade-red.png")
    Image.new("RGB", (160, 90), (0, 0, 255)).save(project / "assets" / "fade-blue.png")

    film_path = project / fd.FILM_NAME
    film_path.write_text(
        """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=160,height=90">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#050505}</style></head>
<body><section id="video-main" data-label="淡化衔接">
  <img id="fade-red" src="assets/fade-red.png#t=0,1" at="0" fade="0,0.5"
       style="left:0px;top:0px;width:160px;height:90px;object-fit:fill"/>
  <img id="fade-blue" src="assets/fade-blue.png#t=0,1" at="1" fade="0.5,0"
       style="left:0px;top:0px;width:160px;height:90px;object-fit:fill"/>
</section></body></html>""",
        encoding="utf-8",
    )
    state = fd.read_project_film(project_id)
    assert state["exists"], state
    fade_clips = {clip["id"]: clip for track in state["doc"]["tracks"] for clip in track["clips"]}
    assert fd._parse_fade(fade_clips["fade-red"]["attrs"]["fade"]) == (0.0, 0.5)
    assert fd._parse_fade(fade_clips["fade-blue"]["attrs"]["fade"]) == (0.5, 0.0)
    revision = state["rev"]
    look_times = [0.25, 0.75, 1.25, 1.75]
    look = film_render.look_project_film(
        project_id, expected_revision=revision, times=look_times
    )
    assert look["ok"] is True, look
    assert look["revision"] == revision
    assert look["determinism_status"] == "PASS"
    with Image.open(look["image_path"]) as contact_sheet:
        sheet = contact_sheet.convert("RGB")
        tiles = [
            sheet.crop((0, 0, 640, 360)),
            sheet.crop((640, 0, 1280, 360)),
            sheet.crop((1280, 0, 1920, 360)),
            sheet.crop((0, 388, 640, 748)),
        ]
        look_pixels = [tile.getpixel((320, 180)) for tile in tiles]

    full_red, half_red, half_blue, full_blue = look_pixels
    assert full_red[0] > 220 and full_red[1] < 35 and full_red[2] < 35, full_red
    assert 70 < half_red[0] < full_red[0] - 45, half_red
    assert half_red[2] < 35, half_red
    assert full_blue[2] > 220 and full_blue[0] < 35 and full_blue[1] < 35, full_blue
    assert 70 < half_blue[2] < full_blue[2] - 45, half_blue
    assert half_blue[0] < 35, half_blue

    result = film_render.render_project_film(project_id, revision, fps=4)
    output = Path(result["mp4_path"])
    assert result["ok"] is True, result
    assert result["rendered_revision"] == revision
    assert result["current"] is True
    assert output.is_file() and output.stat().st_size > 0
    probe = prober.probe_media(str(output))
    assert probe.has_video_stream is True
    assert probe.duration is not None and abs(float(probe.duration) - 2.0) <= 0.15

    # Frames n=1,3,5,7 correspond to the exact preview times 0.25,0.75,1.25,1.75.
    pattern = tmp_path / "fade-export-%02d.png"
    extracted = subprocess.run(
        [
            renderer._ffmpeg(), "-y", "-loglevel", "error", "-i", str(output),
            "-vf", r"select=eq(n\,1)+eq(n\,3)+eq(n\,5)+eq(n\,7)",
            "-vsync", "0", "-frames:v", "4", str(pattern),
        ],
        capture_output=True, text=True, timeout=45,
    )
    assert extracted.returncode == 0, extracted.stderr[-2000:]

    for index, (timestamp, look_pixel) in enumerate(zip(look_times, look_pixels), start=1):
        frame_path = tmp_path / f"fade-export-{index:02d}.png"
        assert frame_path.is_file() and frame_path.stat().st_size > 0, {
            "timestamp": timestamp,
            "files": sorted(path.name for path in tmp_path.glob("fade-export-*.png")),
        }
        with Image.open(frame_path) as frame:
            exported = frame.convert("RGB")
            assert exported.size == (160, 90), exported.size
            exported_pixel = exported.getpixel((80, 45))
            assert max(abs(int(a) - int(b)) for a, b in zip(look_pixel, exported_pixel)) <= 20, {
                "timestamp": timestamp,
                "browser_look_pixel": look_pixel,
                "exported_mp4_pixel": exported_pixel,
            }



def test_visual_crossfade_overlap_matches_browser_look_and_exported_mp4_frames(tmp_path, monkeypatch):
    """The overlapped visual crossfade renders the same frame pixels in look and final MP4."""
    pytest.importorskip("playwright.sync_api")
    from video_director import config, film_document as fd, film_render, prober, renderer

    try:
        renderer._ffmpeg()
    except Exception as exc:  # pragma: no cover - environment-dependent binary
        pytest.skip(f"FFmpeg is unavailable in this environment: {exc}")

    project_id, _ = _write_html_film_project(tmp_path, monkeypatch)
    project = config.project_dir(project_id)
    (project / "assets").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), (255, 0, 0)).save(project / "assets" / "crossfade-red.png")
    Image.new("RGB", (160, 90), (0, 0, 255)).save(project / "assets" / "crossfade-blue.png")
    film_path = project / fd.FILM_NAME
    film_path.write_text(
        """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=160,height=90">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#050505}</style></head>
<body><section id="video-main" data-label="交叉淡化">
  <img id="crossfade-red" src="assets/crossfade-red.png#t=0,1" at="0"
       style="left:0px;top:0px;width:160px;height:90px;object-fit:fill"/>
  <img id="crossfade-blue" src="assets/crossfade-blue.png#t=0,1" at="0.5" fade="0.5,0"
       style="left:0px;top:0px;width:160px;height:90px;object-fit:fill"/>
</section></body></html>""",
        encoding="utf-8",
    )
    state = fd.read_project_film(project_id)
    assert state["exists"], state
    tracks = state["doc"]["tracks"]
    clips = {clip["id"]: clip for track in tracks for clip in track["clips"]}
    assert abs(float(clips["crossfade-red"]["attrs"]["at"])) < 0.001
    assert abs(float(clips["crossfade-blue"]["attrs"]["at"]) - 0.5) < 0.001
    assert fd._parse_fade(clips["crossfade-red"]["attrs"].get("fade")) == (0.0, 0.0)
    assert fd._parse_fade(clips["crossfade-blue"]["attrs"]["fade"]) == (0.5, 0.0)
    revision = state["rev"]

    look_times = [0.2, 0.7, 1.2, 1.4]
    look = film_render.look_project_film(project_id, expected_revision=revision, times=look_times)
    assert look["ok"] is True, look
    assert look["revision"] == revision
    assert look["determinism_status"] == "PASS"
    with Image.open(look["image_path"]) as contact_sheet:
        sheet = contact_sheet.convert("RGB")
        tiles = [
            sheet.crop((0, 0, 640, 360)),
            sheet.crop((640, 0, 1280, 360)),
            sheet.crop((1280, 0, 1920, 360)),
            sheet.crop((0, 388, 640, 748)),
        ]
        look_pixels = [tile.getpixel((320, 180)) for tile in tiles]

    full_red, blended, full_blue, blue_end = look_pixels
    assert full_red[0] > 220 and full_red[2] < 35, full_red
    assert blended[0] > blended[2] > 35, blended
    assert blended[0] < full_red[0] - 25 and blended[2] < full_blue[2] - 25, blended
    assert full_blue[2] > 220 and full_blue[0] < 35, full_blue
    assert blue_end[2] > 220 and blue_end[0] < 35, blue_end

    result = film_render.render_project_film(project_id, revision, fps=10)
    output = Path(result["mp4_path"])
    assert result["ok"] is True, result
    assert result["rendered_revision"] == revision
    assert result["current"] is True
    assert output.is_file() and output.stat().st_size > 0
    probe = prober.probe_media(str(output))
    assert probe.has_video_stream is True
    assert probe.duration is not None and abs(float(probe.duration) - 1.5) <= 0.15

    # Frames n=2,7,12,14 correspond exactly to the same 10fps sample times above.
    pattern = tmp_path / "crossfade-export-%02d.png"
    extracted = subprocess.run(
        [
            renderer._ffmpeg(), "-y", "-loglevel", "error", "-i", str(output),
            "-vf", r"select=eq(n\,2)+eq(n\,7)+eq(n\,12)+eq(n\,14)",
            "-vsync", "0", "-frames:v", "4", str(pattern),
        ],
        capture_output=True, text=True, timeout=45,
    )
    assert extracted.returncode == 0, extracted.stderr[-2000:]
    for index, (timestamp, look_pixel) in enumerate(zip(look_times, look_pixels), start=1):
        frame_path = tmp_path / f"crossfade-export-{index:02d}.png"
        assert frame_path.is_file() and frame_path.stat().st_size > 0, {
            "timestamp": timestamp,
            "files": sorted(path.name for path in tmp_path.glob("crossfade-export-*.png")),
        }
        with Image.open(frame_path) as frame:
            exported = frame.convert("RGB")
            assert exported.size == (160, 90), exported.size
            exported_pixel = exported.getpixel((80, 45))
            assert max(abs(int(a) - int(b)) for a, b in zip(look_pixel, exported_pixel)) <= 20, {
                "timestamp": timestamp,
                "browser_look_pixel": look_pixel,
                "exported_mp4_pixel": exported_pixel,
            }


def test_visual_crossfade_fades_out_unmuted_video_audio_in_real_mp4(tmp_path, monkeypatch):
    """A visual overlap must also fade the outgoing video's native audio in the exported MP4."""
    pytest.importorskip("playwright.sync_api")
    from array import array
    import math
    import sys

    from video_director import config, film_document as fd, film_render, renderer

    try:
        ffmpeg = renderer._ffmpeg()
    except Exception as exc:  # pragma: no cover - environment-dependent binary
        pytest.skip(f"FFmpeg is unavailable in this environment: {exc}")

    project_id, _ = _write_html_film_project(tmp_path, monkeypatch)
    project = config.project_dir(project_id)
    assets = project / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    def generate(*args: str) -> None:
        completed = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", *args],
            capture_output=True, text=True, timeout=60,
        )
        assert completed.returncode == 0, completed.stderr[-2000:]

    # WebM/VP8 + Opus keeps the outgoing source broadly decodable by Chromium.
    # The incoming visual clip is a still image, so no second audio source or
    # independently-seeking video element can mask the outgoing audio envelope.
    for name, color, frequency in (
        ("video-a.webm", "red", 440),
    ):
        generate(
            "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=10:d=2",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=2",
            "-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "100k",
            "-c:a", "libopus", "-b:a", "64k", "-shortest",
            str(assets / name),
        )

    film_path = project / fd.FILM_NAME
    film_path.write_text(
        """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=160,height=90">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#000}</style></head>
<body><section id="video-main" data-label="交叉淡化音频验收">
  <video id="outgoing" src="assets/video-a.webm#t=0,2" at="0"
    style="left:0px;top:0px;width:160px;height:90px;object-fit:cover"></video>
  <img id="incoming" src="assets/moving-blue.png#t=0,2" at="1.5" fade="0.5,0"
    style="left:0px;top:0px;width:160px;height:90px;object-fit:cover" />
</section></body></html>""",
        encoding="utf-8",
    )
    state = fd.read_project_film(project_id)
    assert state["exists"], state
    initial_revision = state["rev"]
    saved = fd.edit_project_film(
        project_id,
        initial_revision,
        [{
            "op": "set",
            "clip": "outgoing",
            "field": "volume_envelope",
            "value": [
                {"t": 0, "v": 1},
                {"t": 1.5, "v": 1},
                {"t": 2, "v": 0},
            ],
        }],
        require_base=True,
        source="crossfade-audio-e2e",
    )
    assert saved["ok"] is True, saved
    assert saved["base"] == initial_revision
    assert saved["rev"] != initial_revision
    state = fd.read_project_film(project_id)
    assert state["rev"] == saved["rev"]
    assert fd._parse_volume_envelope(
        next(c for t in state["doc"]["tracks"] for c in t["clips"] if c["id"] == "outgoing")["attrs"]["volume_envelope"]
    ) == [{"t": 0.0, "v": 1.0}, {"t": 1.5, "v": 1.0}, {"t": 2.0, "v": 0.0}]

    # Audio acceptance is independent of image frame rate; 2 fps avoids forcing Chromium
    # to seek every tenth of a second in the tiny WebM fixture while still rendering
    # the actual film document through the production browser/export path.
    result = film_render.render_project_film(project_id, state["rev"], fps=2)
    output = Path(result["mp4_path"])
    assert result["ok"] is True, result
    assert result["rendered_revision"] == state["rev"]
    assert result["current"] is True
    assert output.is_file() and output.stat().st_size > 0

    decoded = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(output), "-vn", "-ac", "1", "-ar", "48000",
         "-f", "f32le", "pipe:1"],
        capture_output=True, timeout=30,
    )
    assert decoded.returncode == 0, decoded.stderr.decode("utf-8", errors="replace")[-2000:]
    samples = array("f")
    samples.frombytes(decoded.stdout)
    if sys.byteorder != "little":
        samples.byteswap()

    def rms(start: float, end: float) -> float:
        chunk = samples[int(start * 48000):int(end * 48000)]
        assert chunk, (start, end, len(samples))
        return math.sqrt(sum(float(value) * float(value) for value in chunk) / len(chunk))

    before_fade = rms(0.6, 1.0)
    during_fade = rms(1.8, 1.9)
    tail_fade = rms(1.94, 1.99)
    assert before_fade > 0.02, {"before_fade_rms": before_fade}
    assert during_fade < before_fade * 0.55, {
        "before_fade_rms": before_fade, "during_fade_rms": during_fade,
        "hint": "The outgoing video's source audio must fade over the same 0.5s overlap.",
    }
    assert tail_fade < before_fade * 0.2, {
        "before_fade_rms": before_fade, "tail_fade_rms": tail_fade,
        "hint": "The outgoing video's audio should reach silence at its source out-point.",
    }

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



def test_film_render_marks_old_revision_when_document_changes_during_export(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from video_director import film_document as fd, film_render, renderer

    try:
        renderer._ffmpeg()
    except Exception as exc:  # pragma: no cover - environment-dependent binary
        pytest.skip(f"FFmpeg is unavailable in this environment: {exc}")

    project_id, revision = _write_html_film_project(tmp_path, monkeypatch)
    original_audio_specs = film_render._audio_specs
    external_change_done = False

    def change_film_after_frames(pid, doc, duration):
        nonlocal external_change_done
        specs = original_audio_specs(pid, doc, duration)
        if not external_change_done:
            current = fd.read_project_film(project_id)
            assert current["rev"] == revision
            fd.edit_project_film(
                project_id,
                current["rev"],
                [{"op": "set", "clip": "moving-red", "field": "title",
                  "value": "由外部 Agent 在导出期间修改"}],
            )
            external_change_done = True
        return specs

    monkeypatch.setattr(film_render, "_audio_specs", change_film_after_frames)
    result = film_render.render_project_film(
        project_id, fd.read_project_film(project_id)["rev"], fps=2
    )

    current = fd.read_project_film(project_id)
    output = Path(result["mp4_path"])
    assert external_change_done is True
    assert result["ok"] is True
    assert result["rendered_revision"] == revision
    assert result["current_revision"] == current["rev"]
    assert result["current_revision"] != revision
    assert result["current"] is False
    assert output.name == f"film-final-{revision}.mp4"
    assert output.is_file() and output.stat().st_size > 0
    assert any("导出期间影片文档已变化" in warning for warning in result["warnings"])

    qc_path = Path(result["qc_report_path"])
    report = json.loads(qc_path.read_text(encoding="utf-8"))
    assert report["source_revision"] == revision
    assert report["current_revision"] == current["rev"]
    assert report["current_revision_match"] is False
    assert any("rendered_revision" in warning or "导出期间影片文档已变化" in warning
               for warning in report["warnings"])

@pytest.mark.parametrize("expected_revision", ["", "   ", None])
def test_film_render_rejects_missing_or_blank_expected_revision_before_side_effects(
    tmp_path, monkeypatch, expected_revision,
):
    from video_director import config, film_render

    project_id, _revision = _write_html_film_project(tmp_path, monkeypatch)
    project = config.project_dir(project_id)
    with pytest.raises(film_render.FilmRenderError) as raised:
        film_render.render_project_film(project_id, expected_revision, fps=2)
    assert raised.value.code == "INVALID_REVISION"
    assert not (project / "render").exists(), "invalid revision must fail before creating export artifacts"

