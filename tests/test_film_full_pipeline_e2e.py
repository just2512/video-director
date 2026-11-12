from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PIL import Image, ImageChops, ImageStat


def test_blank_film_to_multitrack_mp4_with_html_scene_and_audio_mix(tmp_path, monkeypatch):
    """Exercise the real document editor, browser runtime, QC look and FFmpeg muxer."""
    pytest.importorskip("playwright.sync_api")
    from video_director import config, film_document as fd, prober, renderer

    try:
        ffmpeg = renderer._ffmpeg()
    except Exception as exc:  # pragma: no cover - environment-dependent binary
        pytest.skip(f"FFmpeg is unavailable in this environment: {exc}")

    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "full-film-e2e"
    project = config.project_dir(project_id)
    assets = project / "assets"
    scenes = project / "scenes"
    assets.mkdir(parents=True, exist_ok=True)
    scenes.mkdir(parents=True, exist_ok=True)

    def generate(*args: str) -> None:
        completed = subprocess.run([ffmpeg, "-y", "-loglevel", "error", *args],
                                   capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stderr[-2000:]

    # Chromium builds on Linux are not guaranteed to ship an H.264 decoder;
    # VP8 is the broadest WebM baseline and keeps the browser seek path real.
    generate("-f", "lavfi", "-i", "color=c=0x203050:s=160x90:r=10:d=1",
             "-an", "-c:v", "libvpx", "-pix_fmt", "yuv420p", "-f", "webm",
             "-deadline", "realtime", "-cpu-used", "8", "-lag-in-frames", "0",
             "-auto-alt-ref", "0", "-g", "1", "-keyint_min", "1",
             str(assets / "footage.webm"))
    for name, frequency in (("narration", 440), ("music", 110), ("sfx", 880)):
        generate("-f", "lavfi", "-i",
                 f"sine=frequency={frequency}:sample_rate=44100:duration=1",
                 "-c:a", "pcm_s16le", str(assets / f"{name}.wav"))
    Image.new("RGB", (20, 20), (220, 40, 35)).save(assets / "still.png")

    (scenes / "title.html").write_text('''<!doctype html>
<html><head><meta charset="utf-8"><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}
#title{position:fixed;left:4px;top:1px;color:#ffffff;font:700 12px sans-serif}
</style></head><body><h1 id="title">Opening</h1><script>
window.film={duration:1,ready:document.fonts.ready,frame:function(t){
 document.getElementById('title').textContent=t<0.5?'OPENING':'THE NEXT SCENE';
 document.getElementById('title').style.opacity=String(t<0.5?1:0.85);
}};
</script></body></html>''', encoding="utf-8")
    (scenes / "subtitles.html").write_text('''<!doctype html>
<html><head><meta charset="utf-8"><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}
#caption{position:fixed;left:0;right:0;bottom:0;text-align:center;color:white;
font:700 10px sans-serif;text-shadow:0 1px 3px #000}
</style></head><body><div id="caption"></div><script>
(function(){
 function draw(t){
  document.getElementById('caption').textContent=t<0.5?'Narration subtitle':'Second subtitle';
  parent.postMessage({type:'film-time:ready',time:t},'*');
 }
 window.addEventListener('message',function(event){
  if(event.data&&event.data.type==='film-time')draw(Number(event.data.time)||0);
 });
})();
</script></body></html>''', encoding="utf-8")

    # Start with an empty authoritative film document: this is an actual insert/edit path,
    # not a test fixture that arrives pre-populated with its final timeline.
    empty_film = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=160,height=90">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#111}</style></head>
<body>
<!-- Track DOM order is the visual stacking order: base footage first, then stills and HTML cards. -->
<section id="video-track" data-label="主画面"></section>
<section id="still-track" data-label="贴图"></section>
<section id="title-track" data-label="标题"></section>
<section id="subtitle-track" data-label="字幕"></section>
<section id="narration-track" data-label="旁白"></section>
<section id="music-track" data-label="背景音乐"></section>
<section id="sfx-track" data-label="音效"></section>
</body></html>'''
    (project / fd.FILM_NAME).write_text(empty_film, encoding="utf-8")
    initial = fd.read_project_film(project_id)
    assert initial["exists"] and all(not track["clips"] for track in initial["doc"]["tracks"])

    insert_ops = [
        {"op": "insert", "track_id": "title-track", "clip": {"id": "title", "tag": "iframe",
         "attrs": {"src": "scenes/title.html#t=0,1", "at": "0",
                   "style": "left:38px;top:10px;width:110px;height:28px"}}},
        {"op": "insert", "track_id": "subtitle-track", "clip": {"id": "subtitles", "tag": "iframe",
         "attrs": {"src": "scenes/subtitles.html#t=0,1", "at": "0",
                   "style": "left:8px;top:64px;width:144px;height:20px"}}},
        {"op": "insert", "track_id": "still-track", "clip": {"id": "still", "tag": "img",
         "attrs": {"src": "assets/still.png#t=0,1", "at": "0",
                   "style": "left:4px;top:4px;width:20px;height:20px",
                   "data-film-keyframes": json.dumps([{
                       "property": "opacity",
                       "keyframes": [
                           {"at": 0, "value": 0.55, "easing": "linear"},
                           {"at": 1, "value": 1.0, "easing": {
                               "type": "bezier", "x1": 0.1, "y1": 0.1, "x2": 0.2, "y2": 0.9
                           }},
                       ],
                   }], separators=(",", ":"))}}},
        {"op": "insert", "track_id": "video-track", "clip": {"id": "footage", "tag": "video",
         "attrs": {"src": "assets/footage.webm#t=0,1", "at": "0",
                   "style": "left:0px;top:0px;width:160px;height:90px"}}},
        {"op": "insert", "track_id": "narration-track", "clip": {"id": "narration", "tag": "audio",
         "attrs": {"src": "assets/narration.wav#t=0,1", "at": "0", "volume": "0.9"}}},
        {"op": "insert", "track_id": "music-track", "clip": {"id": "music", "tag": "audio",
         "attrs": {"src": "assets/music.wav#t=0,1", "at": "0", "volume": "0.15"}}},
        {"op": "insert", "track_id": "sfx-track", "clip": {"id": "sfx", "tag": "audio",
         "attrs": {"src": "assets/sfx.wav#t=0,1", "at": "0", "volume": "0.35"}}},
    ]
    inserted = fd.edit_project_film(project_id, initial["rev"], insert_ops)
    assert inserted["ok"] is True

    # Perform a real source-aware edit and reload it from the persisted document.
    split = fd.edit_project_film(project_id, inserted["rev"], [
        {"op": "split", "clip": "footage", "at": 0.5, "new_id": "footage-right"}
    ])
    split_state = fd.read_project_film(project_id)
    split_video_track = next(track for track in split_state["doc"]["tracks"] if track["id"] == "video-track")
    assert split["ok"] is True
    assert [clip["id"] for clip in split_video_track["clips"]] == ["footage", "footage-right"]
    assert fd.clip_duration(split_video_track["clips"][0]) == pytest.approx(0.5)
    assert fd.clip_duration(split_video_track["clips"][1]) == pytest.approx(0.5)
    assert split_state["rev"] == split["rev"]

    # Verify the Agent-facing diff records the source-aware split before restoring
    # a single active decoder element for the full browser render.
    repo_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["VIDEO_DIRECTOR_STORAGE_ROOT"] = str(tmp_path / "composer")
    diff = subprocess.run(
        [sys.executable, "-m", "video_director", "film", "diff",
         "--project-id", project_id, "--from-revision", initial["rev"]],
        cwd=repo_root, env=environment, capture_output=True, text=True, timeout=60
    )
    assert diff.returncode == 0, diff.stdout + diff.stderr
    assert "footage-right" in diff.stdout

    joined = fd.edit_project_film(project_id, split["rev"], [
        {"op": "join", "clip": "footage", "other_clip": "footage-right"}
    ])
    assert joined["ok"] is True, joined
    reloaded = fd.read_project_film(project_id)
    video_track = next(track for track in reloaded["doc"]["tracks"] if track["id"] == "video-track")
    assert [clip["id"] for clip in video_track["clips"]] == ["footage"]
    assert fd.clip_duration(video_track["clips"][0]) == pytest.approx(1.0)
    assert reloaded["rev"] == joined["rev"]

    # CLI validation is part of the Agent contract and must see the same saved revision.
    repo_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["VIDEO_DIRECTOR_STORAGE_ROOT"] = str(tmp_path / "composer")
    status = subprocess.run(
        [sys.executable, "-m", "video_director", "film", "status",
         "--project-id", project_id], cwd=repo_root, env=environment,
        capture_output=True, text=True, timeout=60
    )
    assert status.returncode == 0, status.stdout + status.stderr
    status_json = json.loads(status.stdout)
    assert status_json["revision"] == reloaded["rev"]
    assert status_json["clip_count"] == 7
    assert status_json["track_count"] == 7

    validation = subprocess.run(
        [sys.executable, "-m", "video_director", "film", "validate",
         "--project-id", project_id], cwd=repo_root, env=environment,
        capture_output=True, text=True, timeout=60
    )
    assert validation.returncode == 0, validation.stdout + validation.stderr
    validation_json = json.loads(validation.stdout)
    assert validation_json["ok"] is True
    assert validation_json["revision"] == reloaded["rev"]
    assert validation_json["assets_checked"] == 7

    look = subprocess.run(
        [sys.executable, "-m", "video_director", "film", "look",
         "--project-id", project_id, "--revision", reloaded["rev"],
         "--times", "0,0.5"],
        cwd=repo_root, env=environment, capture_output=True, text=True, timeout=180
    )
    assert look.returncode == 0, look.stdout + look.stderr
    look_json = json.loads(look.stdout)
    assert look_json["determinism_status"] == "PASS"
    look_sheet_path = Path(look_json["image_path"])
    assert look_sheet_path.is_file()
    assert look_json["sample_times"] == [0.0, 0.5]

    render = subprocess.run(
        [sys.executable, "-m", "video_director", "film", "render",
         "--project-id", project_id, "--expected-revision", reloaded["rev"], "--fps", "2"],
        cwd=repo_root, env=environment, capture_output=True, text=True, timeout=240
    )
    assert render.returncode == 0, render.stdout + render.stderr
    rendered = json.loads(render.stdout)
    output = Path(rendered["mp4_path"])
    assert rendered["ok"] and rendered["current"]
    assert rendered["rendered_revision"] == reloaded["rev"]
    assert rendered["revision_status"] == "CURRENT"
    assert rendered["frame_count"] == 2
    assert rendered["audio_tracks_mixed"] == 3
    assert output.is_file() and output.stat().st_size > 0
    probe = prober.probe_media(str(output))
    assert probe.has_video_stream and probe.has_audio_stream
    assert (probe.width, probe.height) == (160, 90)
    assert probe.duration is not None and abs(float(probe.duration) - 1.0) <= 0.15

    # Compare the real browser-runtime frame from the look contact sheet at t=0.5
    # with frame 1 (t=0.5 at 2 fps) extracted from the encoded MP4.
    half_frame_path = tmp_path / "final-frame-half.png"
    generate("-i", str(output), "-vf", "select=eq(n\\,1)", "-fps_mode", "passthrough",
             "-frames:v", "1", "-y", str(half_frame_path))
    with Image.open(look_sheet_path) as contact_sheet_source:
        contact_sheet = contact_sheet_source.convert("RGB")
        assert contact_sheet.size == (1280, 388), contact_sheet.size
        preview_half = contact_sheet.crop((640, 0, 1280, 360)).resize(
            (160, 90), Image.Resampling.BOX
        )
    with Image.open(half_frame_path) as encoded_source:
        encoded_half = encoded_source.convert("RGB")
        assert encoded_half.size == (160, 90), encoded_half.size

    def cubic_bezier(t: float, first: float, second: float) -> float:
        inverse = 1.0 - t
        return 3 * inverse * inverse * t * first + 3 * inverse * t * t * second + t ** 3

    def bezier_ease_at_x(x: float, x1: float, y1: float, x2: float, y2: float) -> float:
        # Match the runtime's 28-step inversion of the cubic x curve.
        low, high = 0.0, 1.0
        for _ in range(28):
            middle = (low + high) * 0.5
            if cubic_bezier(middle, x1, x2) < x:
                low = middle
            else:
                high = middle
        return cubic_bezier((low + high) * 0.5, y1, y2)

    easing_progress = bezier_ease_at_x(0.5, 0.1, 0.1, 0.2, 0.9)
    expected_opacity = 0.55 + (1.0 - 0.55) * easing_progress
    assert expected_opacity > 0.88, expected_opacity  # a linear segment would be only 0.775
    background_pixel = preview_half.getpixel((150, 50))
    still_pixel_half = preview_half.getpixel((15, 15))
    expected_blend = tuple(round(
        background_pixel[channel] * (1.0 - expected_opacity) +
        (220, 40, 35)[channel] * expected_opacity
    ) for channel in range(3))
    assert max(abs(still_pixel_half[channel] - expected_blend[channel]) for channel in range(3)) <= 16, {
        "easing_progress": easing_progress,
        "expected_opacity": expected_opacity,
        "background": background_pixel,
        "preview_pixel": still_pixel_half,
        "expected_pixel": expected_blend,
    }

    # The browser preview and MP4 must agree across the solid interior of the still,
    # not just happen to produce a successful render result.
    preview_interior = preview_half.crop((9, 9, 20, 20))
    encoded_interior = encoded_half.crop((9, 9, 20, 20))
    mean_abs_pixel_difference = sum(
        ImageStat.Stat(ImageChops.difference(preview_interior, encoded_interior)).mean
    ) / 3.0
    assert mean_abs_pixel_difference <= 10.0, {
        "mean_abs_pixel_difference": mean_abs_pixel_difference,
        "preview_pixel": preview_half.getpixel((15, 15)),
        "encoded_pixel": encoded_half.getpixel((15, 15)),
    }

    # The actual rendered frames must contain each principal visual layer, not
    # merely produce a decodable file: the image is red and the video background blue.
    frame_path = tmp_path / "final-frame.png"
    generate("-ss", "0.1", "-i", str(output), "-frames:v", "1", "-y", str(frame_path))
    with Image.open(frame_path) as rendered_frame:
        frame = rendered_frame.convert("RGB")
        assert frame.size == (160, 90)
        still_pixel = frame.getpixel((10, 10))
        assert still_pixel[0] > still_pixel[1] + 80 and still_pixel[0] > still_pixel[2] + 50, (
            f"still layer is not visible at its expected position: {still_pixel}"
        )
        background_pixel = frame.getpixel((150, 50))
        assert background_pixel[2] > background_pixel[0] + 20, (
            f"video background is not visible outside overlays: {background_pixel}"
        )
        title_crop = frame.crop((35, 5, 155, 40))
        bright_pixels = sum(
            1
            for y in range(title_crop.height)
            for x in range(title_crop.width)
            if min(title_crop.getpixel((x, y))) > 220
        )
        assert bright_pixels >= 5, "title-card text was not visible in the encoded frame"

    qc_path = Path(rendered["qc_report_path"])
    assert rendered["qc_status"] == "PASS"
    assert rendered["audio_signal_status"] == "AUDIBLE"
    assert qc_path.is_file() and qc_path.stat().st_size > 0
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    assert qc["status"] == "PASS"
    assert qc["source_revision"] == reloaded["rev"]
    assert qc["current_revision"] == reloaded["rev"]
    assert qc["current_revision_match"] is True
    assert qc["revision_status"] == "CURRENT"
    assert qc["output"]["sha256"] and len(qc["output"]["sha256"]) == 64
    assert qc["output"]["size_bytes"] == output.stat().st_size
    assert qc["output"]["width"] == 160 and qc["output"]["height"] == 90
    assert qc["determinism"]["status"] == "PASS"
    assert len(qc["visual_samples"]) >= 1
    assert qc["audio"]["stream_present"] is True
    assert qc["audio"]["tracks_mixed"] == 3
    assert qc["audio"]["mean_volume_db"] is not None
    assert -60 < qc["audio"]["mean_volume_db"] < 0
    assert all(check["status"] == "PASS" for check in qc["checks"])


def test_real_public_media_full_project_render_and_performance_snapshot(tmp_path, monkeypatch):
    """Run the actual FilmDocument preview/render/QC path with licensed public footage.

    Source: Big Buck Bunny, Blender Foundation, CC BY 3.0.
    The immutable sample repository commit is pinned so the source cannot silently drift.
    """
    import hashlib
    import os
    import time
    import urllib.request

    from video_director import config, film_document as fd, film_render, prober, renderer

    source_url = (
        "https://raw.githubusercontent.com/chthomos/video-media-samples/"
        "997cb58f16bc3433652506910734be75bc64d768/"
        "big-buck-bunny-480p-30sec.mp4"
    )
    source_commit = "997cb58f16bc3433652506910734be75bc64d768"
    source_license = "Big Buck Bunny, Blender Foundation, CC BY 3.0"
    ffmpeg = renderer._ffmpeg()

    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "real-public-media-acceptance"
    project = config.project_dir(project_id)
    assets = project / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    source_path = tmp_path / "big-buck-bunny-480p-30sec.mp4"
    started = time.perf_counter()
    request = urllib.request.Request(
        source_url, headers={"User-Agent": "video-director-real-media-acceptance"}
    )
    digest = hashlib.sha256()
    downloaded = 0
    with urllib.request.urlopen(request, timeout=90) as response, source_path.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            downloaded += len(chunk)
            assert downloaded <= 32 * 1024 * 1024, "Pinned public sample exceeded the 32 MiB safety bound"
            digest.update(chunk)
            output.write(chunk)
    download_ms = round((time.perf_counter() - started) * 1000, 2)
    source_sha256 = digest.hexdigest()
    assert downloaded > 1024 * 1024, f"Public media download is unexpectedly small: {downloaded} bytes"

    started = time.perf_counter()
    source_probe = prober.probe_media(str(source_path))
    source_probe_ms = round((time.perf_counter() - started) * 1000, 2)
    assert source_probe.has_video_stream
    assert source_probe.has_audio_stream
    assert source_probe.width and source_probe.width >= 640
    assert source_probe.height and source_probe.height >= 360
    assert source_probe.duration and 25 <= float(source_probe.duration) <= 35

    # Decode every source video and audio packet, not merely container metadata.
    started = time.perf_counter()
    decoded = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-v", "error", "-xerror", "-err_detect", "explode",
            "-i", str(source_path), "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=180,
    )
    assert decoded.returncode == 0, decoded.stderr[-4000:]
    source_decode_ms = round((time.perf_counter() - started) * 1000, 2)

    # Keep the real scene content; normalize only to the browser-safe VP8/Vorbis
    # profile used by the hosted Chromium runtime (H.264 decode is not guaranteed).
    browser_media = assets / "big-buck-bunny-real-content.webm"
    started = time.perf_counter()
    transcoded = subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", "0", "-t", "8", "-i", str(source_path),
            "-vf", "scale=320:180:force_original_aspect_ratio=decrease,"
                   "pad=320:180:(ow-iw)/2:(oh-ih)/2,fps=12",
            "-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8",
            "-lag-in-frames", "0", "-auto-alt-ref", "0", "-g", "1",
            "-keyint_min", "1", "-pix_fmt", "yuv420p",
            "-c:a", "libvorbis", "-b:a", "64k", "-shortest",
            str(browser_media),
        ],
        capture_output=True, text=True, timeout=180,
    )
    assert transcoded.returncode == 0, transcoded.stderr[-4000:]
    transcode_ms = round((time.perf_counter() - started) * 1000, 2)
    browser_probe = prober.probe_media(str(browser_media))
    assert browser_probe.has_video_stream and browser_probe.has_audio_stream
    assert (browser_probe.width, browser_probe.height) == (320, 180)
    assert browser_probe.duration and abs(float(browser_probe.duration) - 8.0) <= 0.25

    film_html = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=320,height=180">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#101820}</style>
</head><body>
<section id="video-track" data-label="真实视频素材">
  <video id="big-buck-bunny" src="assets/big-buck-bunny-real-content.webm#t=0,8"
         at="0" style="left:0px;top:0px;width:320px;height:180px;object-fit:cover"></video>
</section>
<section id="audio-track" data-label="原始音轨">
  <audio id="big-buck-bunny-audio" src="assets/big-buck-bunny-real-content.webm#t=0,8"
         at="0" volume="1"></audio>
</section>
</body></html>'''
    (project / fd.FILM_NAME).write_text(film_html, encoding="utf-8")
    state = fd.read_project_film(project_id)
    assert state["exists"] is True
    revision = state["rev"]
    assert revision
    clips = [
        clip
        for track in state["doc"]["tracks"]
        for clip in track.get("clips", [])
    ]
    assert len(clips) == 2, {"tracks": state["doc"]["tracks"]}

    # Exercise Chromium frame capture, immutable resource snapshot, revision pinning,
    # FFmpeg publication and QC as a real project.
    started = time.perf_counter()
    look = film_render.look_project_film(
        project_id, expected_revision=revision, times=[0.5, 4.0, 7.5]
    )
    look_ms = round((time.perf_counter() - started) * 1000, 2)
    assert look["ok"] is True
    assert look["revision"] == revision
    assert look["determinism_status"] == "PASS"
    assert Path(look["image_path"]).is_file()

    started = time.perf_counter()
    rendered = film_render.render_project_film(project_id, revision, fps=12)
    render_ms = round((time.perf_counter() - started) * 1000, 2)
    output = Path(rendered["mp4_path"])
    assert rendered["ok"] and rendered["current"]
    assert rendered["rendered_revision"] == revision
    assert rendered["revision_status"] == "CURRENT"
    assert rendered["frame_count"] == 96
    assert rendered["audio_tracks_mixed"] == 1
    assert output.is_file() and output.stat().st_size > 0

    output_probe = prober.probe_media(str(output))
    assert output_probe.has_video_stream and output_probe.has_audio_stream
    assert (output_probe.width, output_probe.height) == (320, 180)
    assert output_probe.duration and abs(float(output_probe.duration) - 8.0) <= 0.25

    qc_path = Path(rendered["qc_report_path"])
    assert rendered["qc_status"] == "PASS"
    assert qc_path.is_file() and qc_path.stat().st_size > 0
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    assert qc["status"] == "PASS"
    assert qc["source_revision"] == revision
    assert qc["current_revision_match"] is True
    assert qc["output"]["sha256"] and len(qc["output"]["sha256"]) == 64
    assert qc["output"]["size_bytes"] == output.stat().st_size
    assert qc["audio"]["stream_present"] is True
    assert all(check["status"] == "PASS" for check in qc["checks"])

    report = {
        "schema_version": 1,
        "acceptance": "PASS",
        "scope": "GitHub-hosted real public media; not user-production media",
        "source": {
            "title": "Big Buck Bunny 480p 30s sample",
            "url": source_url,
            "repository_commit": source_commit,
            "license": source_license,
            "download_bytes": downloaded,
            "sha256": source_sha256,
            "probe": {
                "width": source_probe.width, "height": source_probe.height,
                "fps": source_probe.fps, "duration_seconds": source_probe.duration,
                "video": source_probe.has_video_stream, "audio": source_probe.has_audio_stream,
            },
        },
        "browser_profile": {
            "container": "WebM", "video_codec": "VP8", "audio_codec": "Vorbis",
            "width": browser_probe.width, "height": browser_probe.height,
            "fps": browser_probe.fps, "duration_seconds": browser_probe.duration,
        },
        "measurements_ms": {
            "download": download_ms, "source_probe": source_probe_ms,
            "full_source_av_decode": source_decode_ms, "browser_profile_transcode": transcode_ms,
            "chromium_look_3_frames": look_ms, "film_project_render_and_qc": render_ms,
        },
        "render": {
            "revision": revision, "frame_count": rendered["frame_count"],
            "audio_tracks_mixed": rendered["audio_tracks_mixed"],
            "output_bytes": output.stat().st_size,
            "output_sha256": qc["output"]["sha256"],
            "duration_seconds": output_probe.duration,
            "qc_status": rendered["qc_status"],
            "qc_checks": {item["name"]: item["status"] for item in qc["checks"]},
        },
    }
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    print("REAL_MEDIA_ACCEPTANCE_JSON=" + serialized)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write("## Real public-media Film Studio acceptance\n\n")
            summary.write("- Result: PASS\n")
            summary.write(f"- Source: {source_license}; pinned commit {source_commit}\n")
            summary.write(f"- Source media: {downloaded} bytes, SHA-256 {source_sha256}\n")
            summary.write(f"- Source probe: {source_probe.width}x{source_probe.height}, "
                          f"{source_probe.fps} fps, {source_probe.duration}s\n")
            summary.write(f"- Output: {output_probe.width}x{output_probe.height}, "
                          f"{output_probe.duration}s, {output.stat().st_size} bytes\n")
            summary.write(f"- Film revision: {revision}; QC: {rendered['qc_status']}\n")
            summary.write("- Timings (ms): " + ", ".join(
                f"{key}={value}" for key, value in report["measurements_ms"].items()
            ) + "\n\n")
            summary.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
