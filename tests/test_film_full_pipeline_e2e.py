from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PIL import Image


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

    generate("-f", "lavfi", "-i", "color=c=0x203050:s=160x90:r=10:d=1",
             "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(assets / "footage.mp4"))
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
<section id="title-track" data-label="标题"></section>
<section id="subtitle-track" data-label="字幕"></section>
<section id="still-track" data-label="贴图"></section>
<section id="video-track" data-label="主画面"></section>
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
                   "style": "left:4px;top:4px;width:20px;height:20px"}}},
        {"op": "insert", "track_id": "video-track", "clip": {"id": "footage", "tag": "video",
         "attrs": {"src": "assets/footage.mp4#t=0,1", "at": "0",
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
    reloaded = fd.read_project_film(project_id)
    video_track = next(track for track in reloaded["doc"]["tracks"] if track["id"] == "video-track")
    assert split["ok"] is True
    assert [clip["id"] for clip in video_track["clips"]] == ["footage", "footage-right"]
    assert fd.clip_duration(video_track["clips"][0]) == pytest.approx(0.5)
    assert fd.clip_duration(video_track["clips"][1]) == pytest.approx(0.5)
    assert reloaded["rev"] == split["rev"]

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
    assert status_json["clip_count"] == 8
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
    assert validation_json["assets_checked"] == 8

    diff = subprocess.run(
        [sys.executable, "-m", "video_director", "film", "diff",
         "--project-id", project_id, "--from-revision", initial["rev"]],
        cwd=repo_root, env=environment, capture_output=True, text=True, timeout=60
    )
    assert diff.returncode == 0, diff.stdout + diff.stderr
    assert "footage-right" in diff.stdout

    look = subprocess.run(
        [sys.executable, "-m", "video_director", "film", "look",
         "--project-id", project_id, "--revision", reloaded["rev"],
         "--times", "0,0.5"],
        cwd=repo_root, env=environment, capture_output=True, text=True, timeout=180
    )
    assert look.returncode == 0, look.stdout + look.stderr
    look_json = json.loads(look.stdout)
    assert look_json["determinism_status"] == "PASS"
    assert Path(look_json["image_path"]).is_file()

    render = subprocess.run(
        [sys.executable, "-m", "video_director", "film", "render",
         "--project-id", project_id, "--fps", "2"],
        cwd=repo_root, env=environment, capture_output=True, text=True, timeout=240
    )
    assert render.returncode == 0, render.stdout + render.stderr
    rendered = json.loads(render.stdout)
    output = Path(rendered["mp4_path"])
    assert rendered["ok"] and rendered["current"]
    assert rendered["rendered_revision"] == reloaded["rev"]
    assert rendered["frame_count"] == 2
    assert rendered["audio_tracks_mixed"] == 3
    assert output.is_file() and output.stat().st_size > 0
    probe = prober.probe_media(str(output))
    assert probe.has_video_stream and probe.has_audio_stream
    assert (probe.width, probe.height) == (160, 90)
    assert probe.duration is not None and abs(float(probe.duration) - 1.0) <= 0.15

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
            1 for pixel in title_crop.getdata()
            if min(pixel) > 220
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
