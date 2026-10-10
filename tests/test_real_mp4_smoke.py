"""Repeatable end-to-end smoke test: real asset -> CompositionIR -> renderer -> decodable MP4."""
from __future__ import annotations

import subprocess
from pathlib import Path

from video_director import renderer
from video_director.models import (
    AssetRef,
    CompositionIR,
    Layer,
    LayerType,
    ProjectSpec,
    SceneIR,
)


def test_minimal_composition_renders_to_decodable_mp4(tmp_path: Path):
    ffmpeg = renderer._ffmpeg()
    still = tmp_path / "source.png"
    generated = subprocess.run(
        [
            ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
            "color=c=0x284b63:s=96x96:d=1", "-frames:v", "1", str(still),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert generated.returncode == 0, generated.stderr
    assert still.is_file() and still.stat().st_size > 100

    asset = AssetRef(
        id="smoke-still", type="image", file=str(still),
        width=96, height=96, source="generated",
    )
    layer = Layer(type=LayerType.image, asset_id=asset.id, duration=1.0)
    scene = SceneIR(
        id="smoke-scene", start=0.0, duration=1.0,
        text="Render smoke test", layers=[layer],
    )
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=1.0),
        scenes=[scene],
    )

    result = renderer.render_composition(
        composition, {asset.id: asset}, tmp_path / "render",
    )
    final_mp4 = Path(result.final_path)
    assert final_mp4.is_file(), "renderer returned a missing final MP4"
    assert final_mp4.stat().st_size > 1000, "final MP4 is unexpectedly small"

    decoded = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(final_mp4), "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    assert decoded.returncode == 0, decoded.stderr
