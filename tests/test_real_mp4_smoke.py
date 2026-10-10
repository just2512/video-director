"""Actual media-renderer smoke test: generate source, render CompositionIR, decode MP4."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from video_director.models import AssetRef, CompositionIR, Layer, LayerType, ProjectSpec, SceneIR
from video_director.renderer import render_composition


def _run(command: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, (
        f"Command failed ({result.returncode}): {' '.join(command)}\n"
        f"stdout={result.stdout[-2000:]}\nstderr={result.stderr[-4000:]}"
    )
    return result


def test_real_mp4_render_smoke(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg, "CI must install FFmpeg; do not silently skip the real MP4 smoke test"
    assert ffprobe, "CI must install ffprobe with the FFmpeg package"

    source = tmp_path / "smoke-source.mp4"
    _run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10",
        "-t", "1.2", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ])

    asset = AssetRef(id="smoke-video", type="video", file=str(source),
                     duration=1.2, width=160, height=90, fps=10, source="uploaded",
                     rights_status="CLEAR", provenance={"source": "repeatable-test-fixture"})
    layer = Layer(type=LayerType.video, asset_id=asset.id, start=0, duration=1.2,
                  source_in=0, source_out=1.2, speed=1.0)
    scene = SceneIR(id="S001", start=0, duration=1.2, text="Render smoke test", layers=[layer])
    composition = CompositionIR(
        project=ProjectSpec(width=160, height=90, fps=10, duration=1.2),
        scenes=[scene], assets=[asset],
    )

    result = render_composition(composition, {asset.id: asset}, tmp_path / "render")
    output = Path(result.final_path)
    assert output.is_file() and output.stat().st_size > 1000

    probe = _run([
        ffprobe, "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,width,height,avg_frame_rate",
        "-of", "json", str(output),
    ])
    metadata = json.loads(probe.stdout)
    video = next((stream for stream in metadata.get("streams", [])
                  if stream.get("codec_type") == "video"), None)
    assert video is not None, f"MP4 has no video stream: {metadata}"
    assert (int(video["width"]), int(video["height"])) == (160, 90)
    duration = float(metadata["format"]["duration"])
    assert 1.0 <= duration <= 1.4, f"Unexpected output duration: {duration}"

    # Decode every frame instead of treating file existence or API success as render proof.
    _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(output),
          "-map", "0:v:0", "-f", "null", "-"])

    artifacts_dir = os.environ.get("VIDEO_DIRECTOR_TEST_RESULTS", "").strip()
    if artifacts_dir:
        artifacts = Path(artifacts_dir)
        artifacts.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output, artifacts / "real-mp4-render-smoke.mp4")
        (artifacts / "real-mp4-render-smoke.json").write_text(
            json.dumps({"path": str(output), "duration": duration, "video": video},
                       ensure_ascii=False, indent=2), encoding="utf-8"
        )
