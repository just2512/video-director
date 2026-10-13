"""Real MP4 regressions for canonical Layer.transform rendering."""
from __future__ import annotations

import subprocess
from pathlib import Path

from video_director import renderer
from video_director.models import AssetRef, Layer, LayerType


def _white_still(tmp_path: Path) -> AssetRef:
    ffmpeg = renderer._ffmpeg()
    path = tmp_path / "white.png"
    run = subprocess.run(
        [
            ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
            "color=c=white:s=64x64:r=10:d=1", "-frames:v", "1", str(path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert run.returncode == 0, run.stderr
    assert path.is_file() and path.stat().st_size > 100
    return AssetRef(id="white", type="image", file=str(path), width=64, height=64)


def _pixel_mean(mp4: Path, x: int, y: int) -> float:
    run = subprocess.run(
        [
            renderer._ffmpeg(), "-v", "error", "-i", str(mp4),
            "-vf", f"select=eq(n\\,0),crop=2:2:{x}:{y},format=rgb24",
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True, timeout=30,
    )
    assert run.returncode == 0, run.stderr.decode("utf-8", "replace")
    assert len(run.stdout) == 12, f"expected 2x2 RGB sample, got {len(run.stdout)} bytes"
    return sum(run.stdout) / len(run.stdout)


def test_canonical_transform_changes_actual_mp4_pixels_and_keeps_duration(tmp_path: Path):
    asset = _white_still(tmp_path)
    layer = Layer(
        type=LayerType.image,
        asset_id=asset.id,
        duration=1.0,
        transform={"x": 0.25, "y": 0.5, "scale": 0.5, "opacity": 1.0, "anchor": "center"},
    )
    out = tmp_path / "transformed.mp4"
    renderer.render_layer_segment(layer, asset, str(out), width=64, height=64, fps=10)

    assert out.is_file() and out.stat().st_size > 1024
    inside = _pixel_mean(out, 16, 32)
    outside = _pixel_mean(out, 48, 32)
    assert inside > 170, f"transformed white media should be visible at its normalized anchor: {inside}"
    assert outside < 70, f"pixels outside the transformed media should remain black: {outside}"


def test_canonical_opacity_is_applied_in_real_mp4(tmp_path: Path):
    asset = _white_still(tmp_path)
    layer = Layer(
        type=LayerType.image,
        asset_id=asset.id,
        duration=1.0,
        transform={"x": 0.5, "y": 0.5, "scale": 1.0, "opacity": 0.5, "anchor": "center"},
    )
    out = tmp_path / "opacity.mp4"
    renderer.render_layer_segment(layer, asset, str(out), width=64, height=64, fps=10)

    center = _pixel_mean(out, 32, 32)
    assert 75 < center < 190, f"50% opacity over black should render gray, got {center}"
