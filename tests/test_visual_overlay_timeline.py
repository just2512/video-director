"""True independent visual-track clips: persistence, rejection and real FFmpeg proof."""
from __future__ import annotations

import subprocess

import pytest
from pathlib import Path

from video_director import qc, renderer
from video_director.editor import execute_command, load_composition, save_composition
from video_director.models import (
    AssetRef, CompositionIR, Layer, LayerType, ProjectSpec, SceneIR, VisualOverlayClip, VisualTrack,
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



def test_visual_track_ids_reject_inline_handler_payloads():
    with pytest.raises(ValueError, match="visual track id"):
        VisualTrack(id='track" onclick="alert(1)', name="unsafe", order=0)


def test_legacy_composition_migrates_to_default_visual_track():
    base = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=2.0),
        scenes=[SceneIR(
            id="S01", start=0.0, duration=2.0, text="legacy",
            layers=[Layer(type=LayerType.image, asset_id="base", duration=2.0)],
        )],
        assets=[AssetRef(id="base", type="image", file="/tmp/base.png", width=40, height=40)],
        visual_overlays=[VisualOverlayClip(
            id="legacy-overlay", asset_id="base", start=0.25, duration=1.0,
        )],
    )
    legacy = base.model_dump(mode="json")
    legacy.pop("visual_tracks", None)
    legacy["visual_overlays"][0].pop("track_id", None)
    migrated = CompositionIR.model_validate(legacy)
    assert [(track.id, track.name, track.order) for track in migrated.visual_tracks] == [
        ("overlay-main", "画面叠加", 0)
    ]
    assert migrated.visual_overlays[0].track_id == "overlay-main"


def test_visual_track_commands_persist_order_names_and_clip_membership(tmp_path, monkeypatch):
    pid, _ = _editor_project(tmp_path, monkeypatch)
    base = load_composition(pid)
    main_track = next(track for track in base.visual_tracks if track.id == "overlay-main")

    added_track = _apply(pid, "add_visual_track", "visual-tracks", {"name": "画中画 B"}, 1)
    assert added_track.status == "APPLIED", added_track.errors
    comp = load_composition(pid)
    extra = next(track for track in comp.visual_tracks if track.id != main_track.id)
    assert (extra.name, extra.order) == ("画中画 B", 1)

    duplicate = _apply(pid, "add_visual_track", "visual-tracks", {"name": "画中画 b"}, 2)
    assert duplicate.status == "REJECTED"
    assert duplicate.errors[0]["code"] == "DUPLICATE_TRACK_NAME"

    added = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "overlay-image", "start": 1.0, "duration": 2.0, "trackId": extra.id,
    }, 2)
    assert added.status == "APPLIED", added.errors
    assert load_composition(pid).visual_overlays[0].track_id == extra.id

    renamed = _apply(pid, "rename_visual_track", extra.id, {"name": "证据画面"}, 3)
    assert renamed.status == "APPLIED", renamed.errors
    assert next(t for t in load_composition(pid).visual_tracks if t.id == extra.id).name == "证据画面"

    fractional = _apply(pid, "reorder_visual_track", extra.id, {"toIndex": 0.5}, 4)
    assert fractional.status == "REJECTED"
    assert fractional.errors[0]["code"] == "INVALID_PAYLOAD"

    reordered = _apply(pid, "reorder_visual_track", extra.id, {"toIndex": 0}, 4)
    assert reordered.status == "APPLIED", reordered.errors
    orders = {t.id: t.order for t in load_composition(pid).visual_tracks}
    assert orders[extra.id] == 0 and orders[main_track.id] == 1

    occupied = _apply(pid, "remove_visual_track", extra.id, {}, 5)
    assert occupied.status == "REJECTED"
    assert occupied.errors[0]["code"] == "VISUAL_TRACK_NOT_EMPTY"
    assert next(t.id for t in load_composition(pid).visual_tracks if t.order == 0) == extra.id

    moved = _apply(pid, "move_visual_overlay", "overlay#0", {
        "start": 1.5, "trackId": main_track.id,
    }, 5)
    assert moved.status == "APPLIED", moved.errors
    clip = load_composition(pid).visual_overlays[0]
    assert (clip.start, clip.track_id) == (1.5, main_track.id)

    removed = _apply(pid, "remove_visual_track", extra.id, {}, 6)
    assert removed.status == "APPLIED", removed.errors
    remaining = load_composition(pid)
    assert [(t.id, t.order) for t in remaining.visual_tracks] == [(main_track.id, 0)]
    assert remaining.visual_overlays[0].track_id == main_track.id
    last_track = _apply(pid, "remove_visual_track", main_track.id, {}, 7)
    assert last_track.status == "REJECTED"
    assert last_track.errors[0]["code"] == "LAST_VISUAL_TRACK"


def test_renderer_uses_visual_track_order_ahead_of_clip_z_index(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    ffmpeg = renderer._ffmpeg()
    base_path = tmp_path / "tracks-blue.png"
    red_path = tmp_path / "track-red.png"
    green_path = tmp_path / "track-green.png"
    _make_still(ffmpeg, base_path, "blue")
    _make_still(ffmpeg, red_path, "red")
    _make_still(ffmpeg, green_path, "green")

    main_track = VisualTrack(id="overlay-main", name="底层画面", order=0)
    upper_track = VisualTrack(id="vtrack-upper", name="证据层", order=1)
    assets = [
        AssetRef(id="base", type="image", file=str(base_path), width=40, height=40),
        AssetRef(id="red", type="image", file=str(red_path), width=40, height=40),
        AssetRef(id="green", type="image", file=str(green_path), width=40, height=40),
    ]
    overlays = [
        VisualOverlayClip(
            id="red-clip", asset_id="red", track_id=main_track.id,
            start=0.0, duration=2.0, x=0, y=0, width=1, height=1, z_index=100,
        ),
        VisualOverlayClip(
            id="green-clip", asset_id="green", track_id=upper_track.id,
            start=0.0, duration=2.0, x=0, y=0, width=1, height=1, z_index=-100,
        ),
    ]
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=2.0),
        scenes=[SceneIR(
            id="S01", start=0, duration=2.0, text="track render order",
            layers=[Layer(type=LayerType.image, asset_id="base", duration=2.0)],
        )],
        assets=assets, visual_tracks=[main_track, upper_track], visual_overlays=overlays,
    )
    rendered = renderer.render_composition(
        composition, {asset.id: asset for asset in assets}, tmp_path / "track-order-upper",
    )
    upper_pixel = _pixel_at(ffmpeg, Path(rendered.final_path), 1.0, 160, 90)
    assert upper_pixel[1] > upper_pixel[0] * 1.25, upper_pixel

    composition.visual_tracks = [
        upper_track.model_copy(update={"order": 0}),
        main_track.model_copy(update={"order": 1}),
    ]
    rendered_lower = renderer.render_composition(
        composition, {asset.id: asset for asset in assets}, tmp_path / "track-order-reversed",
    )
    lower_pixel = _pixel_at(ffmpeg, Path(rendered_lower.final_path), 1.0, 160, 90)
    assert lower_pixel[0] > lower_pixel[1] * 1.25, lower_pixel


def test_split_visual_overlay_preserves_properties_and_rejects_boundary_splits(tmp_path, monkeypatch):
    pid, plans = _editor_project(tmp_path, monkeypatch)
    added = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "overlay-image", "start": 2.0, "duration": 3.0,
        "x": 0.6, "y": 0.1, "width": 0.3, "height": 0.3, "opacity": 0.7,
    }, 1)
    assert added.status == "APPLIED", added.errors
    original_id = load_composition(pid).visual_overlays[0].id

    boundary = _apply(pid, "split_visual_overlay", "overlay#0", {"at": 2.0}, 2)
    assert boundary.status == "REJECTED"
    assert boundary.errors[0]["code"] == "SPLIT_OUTSIDE_CLIP"
    boolean_time = _apply(pid, "split_visual_overlay", "overlay#0", {"at": True}, 2)
    assert boolean_time.status == "REJECTED"
    assert boolean_time.errors[0]["code"] == "INVALID_PAYLOAD"
    assert len(load_composition(pid).visual_overlays) == 1

    split = _apply(pid, "split_visual_overlay", "overlay#0", {"at": 3.25}, 2)
    assert split.status == "APPLIED", split.errors
    comp = load_composition(pid)
    left, right = comp.visual_overlays
    assert left.id == original_id
    assert left.start == 2.0 and left.duration == 1.25
    assert right.start == 3.25 and right.duration == 1.75
    assert left.start + left.duration == right.start
    assert left.start + left.duration + right.duration == 5.0
    assert left.asset_id == right.asset_id == "overlay-image"
    assert left.source_in == right.source_in == 0.0  # still images have no source clock
    assert (left.x, left.y, left.width, left.height, left.opacity) == (0.6, 0.1, 0.3, 0.3, 0.7)
    assert (right.x, right.y, right.width, right.height, right.opacity) == (0.6, 0.1, 0.3, 0.3, 0.7)
    assert left.id != right.id
    revisions = [line for line in (plans / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(revisions) == 2


def test_split_video_overlay_advances_source_clock_and_real_render_keeps_cut_continuous(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    ffmpeg = renderer._ffmpeg()
    base_path = tmp_path / "split-blue-base.png"
    overlay_path = tmp_path / "split-green-then-red.mp4"
    _make_still(ffmpeg, base_path, "blue")
    _make_two_color_video(ffmpeg, overlay_path)

    project_id = "split-video-overlay-render"
    plans = tmp_path / project_id / "plans"
    plans.mkdir(parents=True)
    base = AssetRef(id="split-base", type="image", file=str(base_path), width=40, height=40)
    overlay = AssetRef(
        id="split-source", type="video", file=str(overlay_path), duration=2.0, width=40, height=40, fps=10,
    )
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=3.0),
        scenes=[SceneIR(
            id="S01", start=0, duration=3.0, text="split overlay render",
            layers=[Layer(type=LayerType.image, asset_id="split-base", duration=3.0)],
        )],
        assets=[base, overlay],
        visual_overlays=[VisualOverlayClip(
            id="split-video-overlay", asset_id="split-source", start=0.5, duration=1.5,
            source_in=0.1, x=0.65, y=0.1, width=0.3, height=0.3, opacity=1.0,
        )],
    )
    save_composition(project_id, composition)
    (plans / "revisions.jsonl").write_text("", encoding="utf-8")

    result = _apply(project_id, "split_visual_overlay", "overlay#0", {"at": 1.25}, 1)
    assert result.status == "APPLIED", result.errors
    edited = load_composition(project_id)
    left, right = edited.visual_overlays
    assert (left.start, left.duration, left.source_in) == (0.5, 0.75, 0.1)
    assert (right.start, right.duration, right.source_in) == (1.25, 0.75, 0.85)

    asset_map = {asset.id: asset for asset in edited.assets}
    rendered = renderer.render_composition(edited, asset_map, tmp_path / "split-overlay-rendered")
    output = Path(rendered.final_path)
    assert output.is_file() and output.stat().st_size > 1000
    green = _pixel_at(ffmpeg, output, 0.8, 245, 40)
    red = _pixel_at(ffmpeg, output, 1.5, 245, 40)
    blue_after = _pixel_at(ffmpeg, output, 2.2, 245, 40)
    assert green[1] > green[2] * 1.5, green
    assert red[0] > red[1] * 1.3, red
    assert blue_after[2] > blue_after[0] * 1.5, blue_after
    decoded = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(output), "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    assert decoded.returncode == 0, decoded.stderr


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
    js_dir = root / "webui" / "static" / "js"
    timeline_js = (js_dir / "timeline.js").read_text(encoding="utf-8")
    overlay_js = (js_dir / "visual-overlay-editor.js").read_text(encoding="utf-8")
    js = timeline_js + "\n" + overlay_js
    assert "visual_overlays" in js
    assert "_tlBindVisualOverlayDrag" in timeline_js
    assert "editorVisualOverlayPick" in timeline_js
    assert "add_visual_overlay" in overlay_js and "move_visual_overlay" in overlay_js
    assert "trim_visual_overlay" in overlay_js and "editorVisualOverlayOpen" in js
    assert "split_visual_overlay" in overlay_js and "editorVisualOverlaySplit" in overlay_js
    assert "在播放头切分" in overlay_js
    assert "thumbnailUrl" in timeline_js and "ed-clip-thumb" in timeline_js
    assert "api/asset_thumbnail?asset_id=" in timeline_js
    assert "visual_tracks" in timeline_js and "data-visual-track-id" in timeline_js
    assert "presentationMode" in overlay_js and "full_frame" in overlay_js and "picture_in_picture" in overlay_js
    assert "source_audio_enabled" in overlay_js and "保留此视频素材自身的声音" in overlay_js and "保留视频源音轨" in overlay_js
    inspector_js = (js_dir / "inspector.js").read_text(encoding="utf-8")
    assert "editorPromoteLayerToTrack" in inspector_js
    assert "promote_layer_to_visual_track" in inspector_js
    assert "提升到独立画面轨道" in inspector_js
    assert "editorVisualTrackCreate" in timeline_js and "editorVisualTrackMenu" in timeline_js
    assert "trackId:nextTrackId" in overlay_js and "reorder_visual_track" in overlay_js


def test_overlay_presentation_mode_presets_and_source_audio_gate(tmp_path, monkeypatch):
    pid, _ = _editor_project(tmp_path, monkeypatch)
    added = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "overlay-image", "start": 1.0, "duration": 2.0,
        "presentationMode": "full_frame",
    }, 1)
    assert added.status == "APPLIED", added.errors
    saved = load_composition(pid)
    clip = saved.visual_overlays[0]
    assert clip.presentation_mode == "full_frame"
    assert (clip.x, clip.y, clip.width, clip.height) == (0.0, 0.0, 1.0, 1.0)
    assert clip.source_audio_enabled is False

    denied = _apply(pid, "set_visual_overlay", "overlay#0", {
        "source_audio_enabled": True,
    }, added.composition_version)
    assert denied.status == "REJECTED"
    assert denied.errors[0]["code"] == "INVALID_PARAMETER"

    sticker = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "overlay-image", "start": 4.0, "duration": 1.0,
        "presentationMode": "sticker",
    }, added.composition_version)
    assert sticker.status == "APPLIED", sticker.errors
    clips = load_composition(pid).visual_overlays
    assert clips[1].presentation_mode == "sticker"
    assert (clips[1].x, clips[1].y, clips[1].width, clips[1].height) == (0.4, 0.4, 0.2, 0.2)


def _make_video_with_audio(ffmpeg: str, path: Path):
    run = subprocess.run([
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=red:s=40x40:r=10:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2",
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-t", "2", str(path),
    ], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    assert path.is_file() and path.stat().st_size > 1000


def _audio_window_mean_volume(ffmpeg: str, path: Path, start: float, duration: float) -> float:
    """Measure a decoded audio window so timeline placement is tested, not just stream presence."""
    run = subprocess.run([
        ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(path),
        "-vn", "-af", "volumedetect", "-f", "null", "-",
    ], capture_output=True, text=True, timeout=45)
    assert run.returncode == 0, run.stderr
    line = next((line for line in run.stderr.splitlines() if "mean_volume:" in line), None)
    assert line is not None, run.stderr[-2000:]
    raw = line.split("mean_volume:", 1)[1].strip().split()[0]
    return float("-inf") if raw == "-inf" else float(raw)


def test_real_ffmpeg_mixes_opted_in_video_overlay_source_audio(tmp_path):
    ffmpeg = renderer._ffmpeg()
    base_path = tmp_path / "source-audio-base.png"
    overlay_path = tmp_path / "red-video-with-sine.mp4"
    _make_still(ffmpeg, base_path, "blue")
    _make_video_with_audio(ffmpeg, overlay_path)
    base = AssetRef(id="source-audio-base", type="image", file=str(base_path), width=40, height=40)
    overlay = AssetRef(id="source-audio-video", type="video", file=str(overlay_path), duration=2.0,
                       width=40, height=40, fps=10)
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=2.0),
        scenes=[SceneIR(
            id="S01", start=0, duration=2.0, text="base picture",
            layers=[Layer(type=LayerType.image, asset_id="source-audio-base", duration=2.0)],
        )],
        assets=[base, overlay],
        visual_overlays=[VisualOverlayClip(
            id="overlay-with-audio", asset_id="source-audio-video", start=0.5, duration=1.5,
            presentation_mode="full_frame", source_audio_enabled=True, source_audio_volume=0.7,
            x=0.0, y=0.0, width=1.0, height=1.0,
        )],
    )
    result = renderer.render_composition(
        composition, {"source-audio-base": base, "source-audio-video": overlay},
        tmp_path / "rendered-source-audio",
    )
    output = Path(result.final_path)
    assert output.is_file() and output.stat().st_size > 1000
    # The base still image has no audio; AAC can only come from the opted-in overlay.
    report = renderer._verify_muxed_audio(str(output), expected_duration=2.0)
    assert report.get("codec") == "aac", report
    assert report.get("full_decode") == "PASS", report
    # Full-frame presentation uses cover/crop: no base blue should leak through the
    # corner even though the portrait-ish source was encoded as a square.
    corner = _pixel_at(ffmpeg, output, 0.8, 5, 5)
    assert corner[0] > corner[2] * 1.5, corner
    assert composition.audio == [], "render-time derived clips must not mutate saved CompositionIR"
    probe = renderer.prober.probe_media(str(output))
    assert probe.has_audio_stream and probe.has_video_stream

    # Source audio must follow the overlay's timeline start, not leak to t=0.
    pre_roll_db = _audio_window_mean_volume(ffmpeg, output, 0.1, 0.25)
    active_db = _audio_window_mean_volume(ffmpeg, output, 0.8, 0.25)
    assert pre_roll_db < -65, f"source audio leaked before its timeline start: {pre_roll_db} dB"
    assert active_db > -35, f"source audio is missing during its timeline window: {active_db} dB"


def test_source_audio_opt_in_fails_closed_when_video_has_no_audio(tmp_path):
    ffmpeg = renderer._ffmpeg()
    base_path = tmp_path / "silent-overlay-base.png"
    silent_video = tmp_path / "silent-overlay-source.mp4"
    _make_still(ffmpeg, base_path, "blue")
    _make_two_color_video(ffmpeg, silent_video)
    base = AssetRef(id="silent-base", type="image", file=str(base_path), width=40, height=40)
    overlay = AssetRef(id="silent-video", type="video", file=str(silent_video), duration=2.0,
                       width=40, height=40, fps=10)
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=2.0),
        scenes=[SceneIR(
            id="S01", start=0, duration=2.0, text="base picture",
            layers=[Layer(type=LayerType.image, asset_id="silent-base", duration=2.0)],
        )],
        assets=[base, overlay],
        visual_overlays=[VisualOverlayClip(
            id="silent-overlay-with-audio-enabled", asset_id="silent-video", start=0.0, duration=1.0,
            source_audio_enabled=True, presentation_mode="picture_in_picture",
        )],
    )
    with pytest.raises(renderer.RenderError, match="没有音频流"):
        renderer.render_composition(
            composition, {"silent-base": base, "silent-video": overlay},
            tmp_path / "rendered-silent-source",
        )
    assert composition.audio == [], "a failed render must not mutate the saved project audio timeline"


def test_visual_overlay_rejects_unknown_presentation_and_invalid_source_gain():
    with pytest.raises(ValueError, match="presentation_mode"):
        VisualOverlayClip(
            id="bad-mode", asset_id="asset", start=0, duration=1,
            presentation_mode="magic-mode",
        )
    with pytest.raises(ValueError, match="source_audio_volume"):
        VisualOverlayClip(
            id="bad-volume", asset_id="asset", start=0, duration=1,
            source_audio_volume=2.1,
        )


def test_promote_main_video_layer_to_independent_track_renders_real_gap_and_keeps_source_audio(tmp_path, monkeypatch):
    ffmpeg = renderer._ffmpeg()
    video_path = tmp_path / "main-red-video-with-sine.mp4"
    base_path = tmp_path / "base-green.png"
    _make_video_with_audio(ffmpeg, video_path)
    _make_still(ffmpeg, base_path, "green")

    project_id = "promote-main-video-test"
    plans = tmp_path / project_id / "plans"
    plans.mkdir(parents=True)
    video_asset = AssetRef(
        id="main-video", type="video", file=str(video_path), duration=2.0,
        width=40, height=40, fps=10,
    )
    base_asset = AssetRef(id="base-image", type="image", file=str(base_path), width=40, height=40)
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=4.0),
        scenes=[SceneIR(
            id="S01", start=0.0, duration=4.0, text="promote main footage",
            layers=[
                Layer(
                    type=LayerType.video, asset_id="main-video",
                    start=0.0, duration=2.0, source_in=0.0, source_out=2.0, speed=1.0,
                ),
                Layer(type=LayerType.image, asset_id="base-image", start=2.0, duration=2.0),
            ],
        )],
        assets=[video_asset, base_asset],
    )
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    save_composition(project_id, composition)
    (plans / "revisions.jsonl").write_text("", encoding="utf-8")

    lifted = _apply(project_id, "promote_layer_to_visual_track", "S01#0", {}, 1)
    assert lifted.status == "APPLIED", lifted.errors

    undone = execute_command(
        project_id, {"command": "undo", "expectedVersion": lifted.composition_version},
    )
    assert undone.status == "APPLIED", undone.errors
    restored = load_composition(project_id)
    assert restored.scenes[0].layers[0].type == LayerType.video
    assert restored.visual_overlays == []

    redone = execute_command(
        project_id, {"command": "redo", "expectedVersion": undone.composition_version},
    )
    assert redone.status == "APPLIED", redone.errors

    saved = load_composition(project_id)
    gap = saved.scenes[0].layers[0]
    clip = saved.visual_overlays[0]
    assert gap.type == LayerType.gap and gap.asset_id is None
    assert (gap.start, gap.duration) == (0.0, 2.0)
    assert saved.scenes[0].duration == 4.0
    assert (clip.start, clip.duration, clip.source_in) == (0.0, 2.0, 0.0)
    assert clip.presentation_mode == "full_frame"
    assert clip.source_audio_enabled is True
    lifted_track = next(track for track in saved.visual_tracks if track.id == clip.track_id)
    assert (lifted_track.name, lifted_track.order) == ("独立视频 1", 0)
    assert next(track for track in saved.visual_tracks if track.id == "overlay-main").order == 1
    assert saved.audio == [], "source audio remains a derived render input, not a duplicate stored track"

    # Reposition the lifted clip away from the gap, then verify the encoded MP4
    # shows black in the uncovered gap, red video at the new position, and green
    # footage after the lifted clip leaves. This tests rendered output, not just IR.
    moved = _apply(project_id, "move_visual_overlay", "overlay#0", {"start": 0.5}, redone.composition_version)
    assert moved.status == "APPLIED", moved.errors
    saved = load_composition(project_id)
    assets = {asset.id: asset for asset in saved.assets}
    result = renderer.render_composition(saved, assets, tmp_path / "promote-render")
    output = Path(result.final_path)
    assert output.is_file() and output.stat().st_size > 1000
    gap_pixel = _pixel_at(ffmpeg, output, 0.2, 10, 10)
    clip_pixel = _pixel_at(ffmpeg, output, 0.8, 10, 10)
    base_pixel = _pixel_at(ffmpeg, output, 2.8, 10, 10)
    assert max(gap_pixel) < 35, f"uncovered timeline region should be black, got {gap_pixel}"
    assert clip_pixel[0] > clip_pixel[1] * 1.5, f"lifted red clip was not rendered at its new position: {clip_pixel}"
    assert base_pixel[1] > base_pixel[0] * 1.4, f"the following base picture was altered: {base_pixel}"
    audio_report = renderer._verify_muxed_audio(str(output), expected_duration=4.0)
    assert audio_report.get("codec") == "aac", audio_report
    assert audio_report.get("full_decode") == "PASS", audio_report
    pre_roll_db = _audio_window_mean_volume(ffmpeg, output, 0.1, 0.25)
    active_db = _audio_window_mean_volume(ffmpeg, output, 0.8, 0.25)
    assert pre_roll_db < -65, f"lifted source audio leaked before the clip start: {pre_roll_db} dB"
    assert active_db > -35, f"lifted source audio is missing from the clip window: {active_db} dB"

    # Move the lifted footage to cover the end of the project. Black pixels in
    # the now-uncovered explicit gap must not fail output QC, while all sampled
    # non-gap intervals remain required to contain real visual content.
    moved_again = _apply(
        project_id, "move_visual_overlay", "overlay#0", {"start": 2.0},
        moved.composition_version,
    )
    assert moved_again.status == "APPLIED", moved_again.errors
    qc_ir = load_composition(project_id)
    qc_result = renderer.render_composition(
        qc_ir, {asset.id: asset for asset in qc_ir.assets}, tmp_path / "promote-render-qc",
    )
    qc_ok, qc_detail = qc._detect_black_frames(qc_result.final_path, ir=qc_ir)
    assert qc_ok, qc_detail
    assert "个 IR 明确声明的空隙点" in qc_detail, qc_detail
    assert "个非 gap 采样点无意外黑帧" in qc_detail, qc_detail


def test_promote_main_video_uses_global_drop_position_and_rejects_out_of_range(tmp_path, monkeypatch):
    ffmpeg = renderer._ffmpeg()
    video_path = tmp_path / "drop-position-video.mp4"
    base_path = tmp_path / "drop-position-base.png"
    _make_video_with_audio(ffmpeg, video_path)
    _make_still(ffmpeg, base_path, "green")

    project_id = "promote-main-video-drop-position"
    plans = tmp_path / project_id / "plans"
    plans.mkdir(parents=True)
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=10.0),
        scenes=[SceneIR(
            id="S01", start=0.0, duration=10.0, text="drop position",
            layers=[
                Layer(type=LayerType.video, asset_id="source", start=0.0, duration=2.0,
                      source_in=0.0, source_out=2.0, speed=1.0),
                Layer(type=LayerType.image, asset_id="base", start=2.0, duration=8.0),
            ],
        )],
        assets=[
            AssetRef(id="source", type="video", file=str(video_path), duration=2.0, width=40, height=40, fps=10),
            AssetRef(id="base", type="image", file=str(base_path), duration=8.0, width=40, height=40),
        ],
        visual_tracks=[VisualTrack(id="overlay-main", name="画面叠加", order=0)],
    )
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    save_composition(project_id, composition)
    (plans / "revisions.jsonl").write_text("", encoding="utf-8")

    rejected = _apply(project_id, "promote_layer_to_visual_track", "S01#0", {
        "trackId": "overlay-main", "start": 9.0,
    }, 1)
    assert rejected.status == "REJECTED"
    assert rejected.errors[0]["code"] == "INVALID_TIMELINE_RANGE"
    saved_after_reject = load_composition(project_id)
    assert saved_after_reject.scenes[0].layers[0].type == LayerType.video
    assert saved_after_reject.visual_overlays == []
    assert [(track.id, track.order) for track in saved_after_reject.visual_tracks] == [("overlay-main", 0)]

    applied = _apply(project_id, "promote_layer_to_visual_track", "S01#0", {
        "trackId": "overlay-main", "start": 3.0,
    }, 1)
    assert applied.status == "APPLIED", applied.errors
    saved = load_composition(project_id)
    assert saved.visual_overlays[0].start == 3.0
    assert saved.visual_overlays[0].track_id == "overlay-main"
    assert saved.scenes[0].layers[0].type == LayerType.gap
    assert saved.scenes[0].layers[0].duration == 2.0
    assert [(track.id, track.order) for track in saved.visual_tracks] == [("overlay-main", 0)]


def test_promote_main_video_honors_drop_start_on_existing_track(tmp_path, monkeypatch):
    ffmpeg = renderer._ffmpeg()
    video_path = tmp_path / "drop-start.mp4"
    base_path = tmp_path / "drop-base.png"
    _make_video_with_audio(ffmpeg, video_path)
    _make_still(ffmpeg, base_path, "green")

    project_id = "promote-main-video-drop-start"
    plans = tmp_path / project_id / "plans"
    plans.mkdir(parents=True)
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=10.0),
        scenes=[SceneIR(
            id="S01", start=0.0, duration=10.0, text="drop start",
            layers=[
                Layer(type=LayerType.video, asset_id="source", start=0.0, duration=2.0,
                      source_in=0.0, source_out=2.0, speed=1.0),
                Layer(type=LayerType.image, asset_id="base", start=2.0, duration=8.0),
            ],
        )],
        assets=[
            AssetRef(id="source", type="video", file=str(video_path), duration=2.0, width=40, height=40, fps=10),
            AssetRef(id="base", type="image", file=str(base_path), duration=8.0, width=40, height=40),
        ],
        visual_tracks=[VisualTrack(id="overlay-main", name="画面叠加", order=0)],
    )
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    save_composition(project_id, composition)
    (plans / "revisions.jsonl").write_text("", encoding="utf-8")

    rejected = _apply(project_id, "promote_layer_to_visual_track", "S01#0", {
        "trackId": "overlay-main", "start": 9.0,
    }, 1)
    assert rejected.status == "REJECTED"
    assert rejected.errors[0]["code"] == "INVALID_TIMELINE_RANGE"
    after_reject = load_composition(project_id)
    assert after_reject.scenes[0].layers[0].type == LayerType.video
    assert after_reject.visual_overlays == []
    assert [(track.id, track.order) for track in after_reject.visual_tracks] == [("overlay-main", 0)]

    applied = _apply(project_id, "promote_layer_to_visual_track", "S01#0", {
        "trackId": "overlay-main", "start": 3.0,
    }, 1)
    assert applied.status == "APPLIED", applied.errors
    saved = load_composition(project_id)
    assert saved.visual_overlays[0].start == 3.0
    assert saved.visual_overlays[0].track_id == "overlay-main"
    assert saved.scenes[0].layers[0].type == LayerType.gap
    assert saved.scenes[0].layers[0].duration == 2.0
    assert [(track.id, track.order) for track in saved.visual_tracks] == [("overlay-main", 0)]


def test_promote_main_video_layer_rejects_speed_without_mutating_project(tmp_path, monkeypatch):
    ffmpeg = renderer._ffmpeg()
    video_path = tmp_path / "sped-up-video.mp4"
    _make_video_with_audio(ffmpeg, video_path)
    project_id = "promote-main-video-speed-test"
    plans = tmp_path / project_id / "plans"
    plans.mkdir(parents=True)
    asset = AssetRef(id="main-video", type="video", file=str(video_path), duration=2.0, width=40, height=40)
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=1.0),
        scenes=[SceneIR(
            id="S01", start=0.0, duration=1.0, text="unsupported speed",
            layers=[Layer(
                type=LayerType.video, asset_id="main-video",
                start=0.0, duration=1.0, source_in=0.0, source_out=2.0, speed=2.0,
            )],
        )],
        assets=[asset],
    )
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    save_composition(project_id, composition)
    (plans / "revisions.jsonl").write_text("", encoding="utf-8")

    rejected = _apply(project_id, "promote_layer_to_visual_track", "S01#0", {}, 1)
    assert rejected.status == "REJECTED"
    assert rejected.errors[0]["code"] == "LIFT_UNSUPPORTED_SPEED"
    saved = load_composition(project_id)
    assert saved.scenes[0].layers[0].type == LayerType.video
    assert saved.visual_overlays == []
    assert saved.project.duration == 1.0
