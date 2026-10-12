"""True independent visual-track clips: persistence, rejection and real FFmpeg proof."""
from __future__ import annotations

import subprocess
from pathlib import Path

from video_director import renderer
from video_director.editor import execute_command, load_composition, save_composition
from video_director.models import (
    AssetRef, CompositionIR, Layer, LayerType, ProjectSpec, SceneIR, VisualOverlayClip,
)


def _editor_project(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    project_id = "visual-overlay-editor-test"
    plans = tmp_path / project_id / "plans"
    plans.mkdir(parents=True)
    image = tmp_path / "project-overlay.png"
    image.write_bytes(b"test fixture file; command tests only")
    comp = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=10.0),
        scenes=[SceneIR(
            id="S01", start=0.0, duration=10.0, text="overlay commands",
            layers=[Layer(type=LayerType.image, asset_id="base", duration=10.0)],
        )],
        assets=[AssetRef(id="overlay-image", type="image", file=str(image), width=40, height=40)],
    )
    save_composition(project_id, comp)
    (plans / "revisions.jsonl").write_text("", encoding="utf-8")
    return project_id, plans


def _apply(pid, command, target, payload, version):
    return execute_command(pid, {
        "command": command, "targetId": target, "payload": payload,
        "expectedVersion": version,
    })


def test_overlay_model_rejects_frame_overflow_and_invalid_timing():
    for params in (
        {"id": "bad-x", "asset_id": "asset", "start": 0, "duration": 1, "x": 0.8, "width": 0.3},
        {"id": "bad-y", "asset_id": "asset", "start": 0, "duration": 1, "y": 0.9, "height": 0.2},
        {"id": "bad-time", "asset_id": "asset", "start": -0.1, "duration": 1},
        {"id": "bad-opacity", "asset_id": "asset", "start": 0, "duration": 1, "opacity": 1.5},
    ):
        try:
            VisualOverlayClip(**params)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid visual overlay was accepted: {params}")


def test_visual_overlay_commands_add_move_trim_update_delete_and_persist(tmp_path, monkeypatch):
    pid, plans = _editor_project(tmp_path, monkeypatch)
    added = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "overlay-image", "start": 2.0, "duration": 3.0,
        "x": 0.6, "y": 0.1, "width": 0.3, "height": 0.3,
    }, 1)
    assert added.status == "APPLIED", added.errors
    comp = load_composition(pid)
    assert len(comp.visual_overlays) == 1
    clip = comp.visual_overlays[0]
    assert (clip.start, clip.duration, clip.asset_id) == (2.0, 3.0, "overlay-image")
    assert added.composition_version == 2

    moved = _apply(pid, "move_visual_overlay", "overlay#0", {"start": 3.0}, 2)
    assert moved.status == "APPLIED", moved.errors
    assert load_composition(pid).visual_overlays[0].start == 3.0

    trimmed = _apply(pid, "trim_visual_overlay", "overlay#0", {
        "start": 3.5, "sourceIn": 0.0, "duration": 2.5,
    }, 3)
    assert trimmed.status == "APPLIED", trimmed.errors
    clip = load_composition(pid).visual_overlays[0]
    assert (clip.start, clip.duration, clip.source_in) == (3.5, 2.5, 0.0)

    updated = _apply(pid, "set_visual_overlay", "overlay#0", {
        "x": 0.5, "y": 0.2, "width": 0.4, "height": 0.4, "opacity": 0.7, "z_index": 2,
    }, 4)
    assert updated.status == "APPLIED", updated.errors
    clip = load_composition(pid).visual_overlays[0]
    assert (clip.x, clip.y, clip.width, clip.height, clip.opacity, clip.z_index) == (0.5, 0.2, 0.4, 0.4, 0.7, 2)

    removed = _apply(pid, "remove_visual_overlay", "overlay#0", {}, 5)
    assert removed.status == "APPLIED", removed.errors
    assert load_composition(pid).visual_overlays == []
    revisions = [line for line in (plans / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(revisions) == 5


def test_visual_overlay_commands_reject_unknown_assets_stale_versions_and_out_of_range_edits(tmp_path, monkeypatch):
    pid, plans = _editor_project(tmp_path, monkeypatch)
    missing = _apply(pid, "add_visual_overlay", "overlays", {"assetId": "../../outside.png"}, 1)
    assert missing.status == "REJECTED"
    assert missing.errors[0]["code"] == "ASSET_NOT_FOUND"
    assert not [line for line in (plans / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]

    added = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "overlay-image", "start": 1.0, "duration": 2.0,
    }, 1)
    assert added.status == "APPLIED", added.errors
    stale = _apply(pid, "move_visual_overlay", "overlay#0", {"start": 2.0}, 1)
    assert stale.status == "REJECTED" and stale.errors[0]["code"] == "CONFLICT"
    invalid = _apply(pid, "move_visual_overlay", "overlay#0", {"start": 9.0}, 2)
    assert invalid.status == "REJECTED"
    assert load_composition(pid).visual_overlays[0].start == 1.0



def test_visual_overlay_add_promotes_trusted_catalog_asset_into_composition(tmp_path, monkeypatch):
    from video_director import asset_catalog

    pid, plans = _editor_project(tmp_path, monkeypatch)
    # The asset is selectable from the editor catalog but has never been used by
    # a SceneIR layer, so it is intentionally absent from CompositionIR.assets.
    comp = load_composition(pid)
    comp.assets = []
    save_composition(pid, comp)
    external_overlay = tmp_path / "catalog-red.png"
    external_overlay.write_bytes(b"trusted catalog fixture")
    monkeypatch.setattr(asset_catalog, "get_asset", lambda asset_id: {
        "id": asset_id, "type": "image", "file": str(external_overlay),
        "name": "Catalog Red", "duration": None, "width": 40, "height": 40,
        "fps": None, "source": "uploaded", "license_note": "test fixture",
        "provenance": {"source": "test"}, "rights_status": "CLEAR",
    } if asset_id == "catalog-only" else None)

    result = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "catalog-only", "start": 1.0, "duration": 2.0,
    }, 1)
    assert result.status == "APPLIED", result.errors
    reloaded = load_composition(pid)
    assert any(asset.id == "catalog-only" and Path(asset.file).resolve() == external_overlay.resolve()
               for asset in reloaded.assets)
    assert reloaded.visual_overlays[0].asset_id == "catalog-only"


def _make_still(ffmpeg: str, path: Path, color: str):
    run = subprocess.run([
        ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
        f"color=c={color}:s=40x40:d=1", "-frames:v", "1", str(path),
    ], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert path.is_file() and path.stat().st_size > 50


def _pixel_at(ffmpeg: str, video: Path, at: float, x: int, y: int):
    run = subprocess.run([
        ffmpeg, "-v", "error", "-ss", f"{at:.3f}", "-i", str(video),
        "-frames:v", "1", "-vf", f"format=rgb24,crop=1:1:{x}:{y}:exact=1",
        "-f", "rawvideo", "-",
    ], capture_output=True, timeout=30)
    assert run.returncode == 0, run.stderr.decode("utf-8", errors="replace")
    assert len(run.stdout) == 3, f"expected 1 RGB pixel, got {len(run.stdout)} bytes"
    return tuple(run.stdout)


def test_real_ffmpeg_overlay_is_pixel_visible_only_inside_clip_window(tmp_path):
    ffmpeg = renderer._ffmpeg()
    base_path = tmp_path / "blue-base.png"
    overlay_path = tmp_path / "red-overlay.png"
    _make_still(ffmpeg, base_path, "blue")
    _make_still(ffmpeg, overlay_path, "red")

    base = AssetRef(id="base", type="image", file=str(base_path), width=40, height=40)
    overlay = AssetRef(id="overlay", type="image", file=str(overlay_path), width=40, height=40)
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=3.0),
        scenes=[SceneIR(
            id="S01", start=0, duration=3.0, text="base picture",
            layers=[Layer(type=LayerType.image, asset_id="base", duration=3.0)],
        )],
        assets=[base, overlay],
        visual_overlays=[VisualOverlayClip(
            id="overlay-one", asset_id="overlay", start=1.0, duration=1.0,
            x=0.65, y=0.1, width=0.3, height=0.3, opacity=1.0,
        )],
    )
    result = renderer.render_composition(
        composition, {"base": base, "overlay": overlay}, tmp_path / "rendered",
    )
    output = Path(result.final_path)
    assert output.is_file() and output.stat().st_size > 1000
    # The sampled point is inside the overlay image's content, not the transparent pad.
    before = _pixel_at(ffmpeg, output, 0.5, 245, 40)
    during = _pixel_at(ffmpeg, output, 1.5, 245, 40)
    after = _pixel_at(ffmpeg, output, 2.5, 245, 40)
    assert before[2] > before[0] * 1.5, before
    assert during[0] > during[2] * 1.5, during
    assert after[2] > after[0] * 1.5, after
    decoded = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(output), "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    assert decoded.returncode == 0, decoded.stderr



def _make_two_color_video(ffmpeg: str, path: Path):
    run = subprocess.run([
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=green:s=40x40:r=10:d=1",
        "-f", "lavfi", "-i", "color=c=red:s=40x40:r=10:d=1",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
        "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "2", str(path),
    ], capture_output=True, text=True, timeout=45)
    assert run.returncode == 0, run.stderr
    assert path.is_file() and path.stat().st_size > 100


def test_real_ffmpeg_video_overlay_honors_source_in_and_output_window(tmp_path):
    ffmpeg = renderer._ffmpeg()
    base_path = tmp_path / "blue-base-video-overlay.png"
    overlay_path = tmp_path / "green-then-red-overlay.mp4"
    _make_still(ffmpeg, base_path, "blue")
    _make_two_color_video(ffmpeg, overlay_path)
    base = AssetRef(id="base-video-test", type="image", file=str(base_path), width=40, height=40)
    overlay = AssetRef(
        id="moving-overlay-source", type="video", file=str(overlay_path), duration=2.0,
        width=40, height=40, fps=10,
    )
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=3.0),
        scenes=[SceneIR(
            id="S01", start=0, duration=3.0, text="base picture",
            layers=[Layer(type=LayerType.image, asset_id="base-video-test", duration=3.0)],
        )],
        assets=[base, overlay],
        visual_overlays=[VisualOverlayClip(
            id="video-overlay-window", asset_id="moving-overlay-source",
            start=1.0, duration=0.75, source_in=1.1, x=0.65, y=0.1,
            width=0.3, height=0.3, opacity=1.0, z_index=1,
        )],
    )
    result = renderer.render_composition(
        composition, {"base-video-test": base, "moving-overlay-source": overlay}, tmp_path / "rendered-video-overlay",
    )
    output = Path(result.final_path)
    assert output.is_file() and output.stat().st_size > 1000
    before = _pixel_at(ffmpeg, output, 0.5, 245, 40)
    during = _pixel_at(ffmpeg, output, 1.25, 245, 40)
    after = _pixel_at(ffmpeg, output, 2.0, 245, 40)
    assert before[2] > before[0] * 1.5, before
    assert during[0] > during[2] * 1.5, during
    assert after[2] > after[0] * 1.5, after
    decoded = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(output), "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    assert decoded.returncode == 0, decoded.stderr


def test_timeline_ui_exposes_visual_overlay_commands_and_track():
    root = Path(__file__).resolve().parents[1]
    js = (root / "webui" / "static" / "js" / "timeline.js").read_text(encoding="utf-8")
    assert "visual_overlays" in js
    assert "_tlBindVisualOverlayDrag" in js
    assert "add_visual_overlay" in js and "move_visual_overlay" in js
    assert "trim_visual_overlay" in js and "editorVisualOverlayOpen" in js
