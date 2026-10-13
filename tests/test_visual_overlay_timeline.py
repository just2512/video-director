"""True independent visual-track clips: persistence, rejection and real FFmpeg proof."""
from __future__ import annotations

import subprocess

import pytest
from pathlib import Path

from video_director import qc, renderer
from video_director.editor import execute_command, load_composition, save_composition
from video_director.models import (
    AssetRef, AudioTrack, AudioTrackType, CompositionIR, Layer, LayerType, ProjectSpec, SceneIR, VisualOverlayClip, VisualTrack,
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


def _linked_video_project(tmp_path, monkeypatch, *, locked_audio=False, include_unlinked=False):
    pid, plans = _editor_project(tmp_path, monkeypatch)
    comp = load_composition(pid)
    source = tmp_path / "linked-video.mp4"
    source.write_bytes(b"video source fixture; editor-command tests do not decode it")
    comp.assets.append(AssetRef(
        id="linked-video", type="video", file=str(source.resolve()),
        duration=8.0, width=320, height=180,
    ))
    comp.visual_overlays.append(VisualOverlayClip(
        id="linked-overlay", asset_id="linked-video", start=2.0, duration=4.0,
        source_in=1.0, track_id="overlay-main", source_audio_enabled=False,
    ))
    audio_lane = next(lane for lane in comp.audio_tracks if lane.id == "audio-original")
    if locked_audio:
        comp.audio_tracks = [
            lane.model_copy(update={"locked": True}) if lane.id == audio_lane.id else lane
            for lane in comp.audio_tracks
        ]
    comp.audio.append(AudioTrack(
        type=AudioTrackType.original, id="linked-audio",
        track_id=audio_lane.id, follows_scene_video=False,
        linked_visual_overlay_id="linked-overlay", asset_id="linked-video",
        start=2.0, duration=4.0, source_in=1.0, source_out=5.0,
        volume=0.42, fadeIn=0.5, fadeOut=0.75,
    ))
    if include_unlinked:
        comp.audio.append(AudioTrack(
            type=AudioTrackType.original, id="unlinked-audio",
            track_id=audio_lane.id, follows_scene_video=False,
            asset_id="linked-video", start=7.0, duration=1.0,
            source_in=0.0, source_out=1.0, volume=0.15,
        ))
    save_composition(pid, comp)
    return pid, plans


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


def test_timeline_ui_exposes_source_monitor_track_insert_overwrite_modes():
    root = Path(__file__).resolve().parents[1]
    markup = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    source_js = (root / "webui" / "static" / "js" / "source-monitor.js").read_text(encoding="utf-8")
    assert 'id="ed-source-operation"' in markup
    assert 'value="overlay"' in markup and 'value="overwrite"' in markup and 'value="insert"' in markup
    assert "placementMode" in source_js
    assert "Insert 只推后所选画面轨" in markup
    assert "sourceIn" in source_js and "sourceAudioEnabled" in source_js


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
    assert "editorVisualOverlayDetachAudio" in overlay_js and "拆分为独立原声轨" in overlay_js
    assert "editorVisualOverlayDetachAudio(" in (root / "video_director" / "ui_action_audit.py").read_text(encoding="utf-8")
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



def test_locked_visual_track_rejects_add_move_trim_split_property_and_delete(tmp_path, monkeypatch):
    pid, plans = _editor_project(tmp_path, monkeypatch)
    added = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "overlay-image", "start": 2.0, "duration": 3.0,
    }, 1)
    assert added.status == "APPLIED", added.errors
    locked = _apply(pid, "set_track_locked", "overlay-main", {"trackType": "visual", "locked": True}, 2)
    assert locked.status == "APPLIED", locked.errors
    assert locked.composition_version == 3
    original = load_composition(pid).visual_overlays[0].model_dump(mode="json")
    assert next(track for track in load_composition(pid).visual_tracks if track.id == "overlay-main").locked is True

    attempts = [
        ("move_visual_overlay", "overlay#0", {"start": 3.0}),
        ("trim_visual_overlay", "overlay#0", {"start": 2.5, "sourceIn": 0.0, "duration": 2.0}),
        ("split_visual_overlay", "overlay#0", {"at": 3.5}),
        ("set_visual_overlay", "overlay#0", {"opacity": 0.4}),
        ("remove_visual_overlay", "overlay#0", {}),
        ("add_visual_overlay", "overlays", {"assetId": "overlay-image", "start": 1.0, "duration": 1.0, "trackId": "overlay-main"}),
    ]
    for command, target, payload in attempts:
        result = _apply(pid, command, target, payload, version=3)
        assert result.status == "REJECTED", (command, result.status, result.errors)
        assert result.errors[0]["code"] == "TRACK_LOCKED", (command, result.errors)
        assert result.composition_version == 3, (command, result.composition_version)
        assert load_composition(pid).visual_overlays[0].model_dump(mode="json") == original

    revisions = [line for line in (plans / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(revisions) == 2, "only the add and lock commands should create revisions"


def test_legacy_visual_track_defaults_to_unlocked_and_lock_controls_are_accessible():
    import copy
    from pathlib import Path
    from tests.fixtures.fixture_data import RECIPE_PROJECT
    from video_director.models import CompositionIR

    comp = CompositionIR(**copy.deepcopy(RECIPE_PROJECT["composition"]))
    dumped = comp.model_dump(mode="json")
    for track in dumped.get("visual_tracks", []):
        track.pop("locked", None)
    migrated = CompositionIR.model_validate(dumped)
    assert all(track.locked is False for track in migrated.visual_tracks)

    root = Path(__file__).resolve().parents[1]
    timeline = (root / "webui/static/js/timeline.js").read_text(encoding="utf-8")
    visual_ui = (root / "webui/static/js/visual-overlay-editor.js").read_text(encoding="utf-8")
    audit = (root / "video_director/ui_action_audit.py").read_text(encoding="utf-8")
    assert 'aria-pressed="${t.locked ?' in timeline
    assert "editorVisualTrackToggleLock(event" in timeline
    assert "set_track_locked" in visual_ui
    assert "editorVisualTrackToggleLock(" in audit



def test_add_edit_all_tracks_skips_locked_visual_overlay_but_cuts_other_tracks(tmp_path, monkeypatch):
    pid, _ = _editor_project(tmp_path, monkeypatch)
    added = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "overlay-image", "start": 2.0, "duration": 4.0,
    }, 1)
    assert added.status == "APPLIED", added.errors
    locked = _apply(pid, "set_track_locked", "overlay-main", {"trackType": "visual", "locked": True}, 2)
    assert locked.status == "APPLIED", locked.errors

    result = _apply(pid, "add_edit_all_tracks", "timeline", {"at": 3.0}, 3)
    assert result.status == "APPLIED", result.errors
    assert any("TRACK_LOCKED" in warning for warning in result.warnings), result.warnings
    comp = load_composition(pid)
    assert len(comp.visual_overlays) == 1
    assert (comp.visual_overlays[0].start, comp.visual_overlays[0].duration) == (2.0, 4.0)
    assert len(comp.scenes[0].layers) == 2, "unlocked main picture still receives the add edit"


def test_source_track_overwrite_splits_intersections_and_preserves_other_tracks(tmp_path, monkeypatch):
    pid, _ = _editor_project(tmp_path, monkeypatch)
    ir = load_composition(pid)
    ir.visual_tracks.append(VisualTrack(id="visual-secondary", name="Secondary", order=1))
    ir.assets.append(AssetRef(id="old-source-video", type="video", file=str(tmp_path / "old-source.mp4"), duration=10))
    ir.visual_overlays = [
        VisualOverlayClip(
            id="old-video", asset_id="old-source-video", start=1.0, duration=4.0,
            source_in=0.5, track_id="overlay-main", presentation_mode="full_frame",
            source_audio_enabled=True, source_audio_volume=0.7,
        ),
        VisualOverlayClip(
            id="other-track-clip", asset_id="overlay-image", start=1.5, duration=2.0,
            source_in=0.25, track_id="visual-secondary", presentation_mode="picture_in_picture",
        ),
    ]
    save_composition(pid, ir)

    result = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "overlay-image", "start": 2.0, "duration": 1.5,
        "sourceIn": 0.0, "trackId": "overlay-main",
        "presentationMode": "full_frame", "placementMode": "overwrite",
    }, 1)
    assert result.status == "APPLIED", result.errors
    saved = load_composition(pid)
    main = sorted(
        (clip for clip in saved.visual_overlays if clip.track_id == "overlay-main"),
        key=lambda clip: clip.start,
    )
    assert [(clip.start, clip.duration) for clip in main] == [
        (1.0, 1.0), (2.0, 1.5), (3.5, 1.5),
    ]
    assert [(main[0].source_in, main[2].source_in)] == [(0.5, 3.0)]
    assert main[0].source_audio_enabled and main[2].source_audio_enabled
    assert main[0].source_audio_volume == pytest.approx(0.7)
    other = next(clip for clip in saved.visual_overlays if clip.id == "other-track-clip")
    assert (other.start, other.duration, other.source_in) == (1.5, 2.0, 0.25)
    assert saved.project.duration == 10.0


def test_source_track_insert_splits_crossing_clip_shifts_downstream_and_extends_duration(tmp_path, monkeypatch):
    pid, _ = _editor_project(tmp_path, monkeypatch)
    ir = load_composition(pid)
    ir.visual_tracks.append(VisualTrack(id="visual-secondary", name="Secondary", order=1))
    ir.assets.append(AssetRef(id="old-source-video", type="video", file=str(tmp_path / "old-source.mp4"), duration=10))
    ir.visual_overlays = [
        VisualOverlayClip(
            id="crossing-video", asset_id="old-source-video", start=2.0, duration=2.0,
            source_in=0.5, track_id="overlay-main", presentation_mode="full_frame",
            source_audio_enabled=True,
        ),
        VisualOverlayClip(
            id="downstream-clip", asset_id="overlay-image", start=6.0, duration=1.0,
            source_in=0.0, track_id="overlay-main", presentation_mode="full_frame",
        ),
        VisualOverlayClip(
            id="other-track-clip", asset_id="overlay-image", start=6.0, duration=1.0,
            source_in=0.0, track_id="visual-secondary", presentation_mode="picture_in_picture",
        ),
    ]
    save_composition(pid, ir)

    result = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "overlay-image", "start": 3.0, "duration": 1.25,
        "sourceIn": 0.0, "trackId": "overlay-main",
        "presentationMode": "full_frame", "placementMode": "insert",
    }, 1)
    assert result.status == "APPLIED", result.errors
    saved = load_composition(pid)
    main = sorted(
        (clip for clip in saved.visual_overlays if clip.track_id == "overlay-main"),
        key=lambda clip: clip.start,
    )
    assert [(clip.start, clip.duration) for clip in main] == [
        (2.0, 1.0), (3.0, 1.3), (4.3, 1.0), (7.3, 1.0),
    ]
    left = next(clip for clip in main if clip.id == "crossing-video")
    right = next(clip for clip in main if clip.id != "crossing-video" and clip.start == 4.3)
    assert left.source_in == 0.5
    assert right.source_in == 1.5 and right.source_audio_enabled
    other = next(clip for clip in saved.visual_overlays if clip.id == "other-track-clip")
    assert (other.start, other.duration) == (6.0, 1.0)
    assert saved.project.duration == pytest.approx(11.3)
    assert saved.scenes[0].duration == pytest.approx(11.3)
    assert saved.scenes[0].layers[-1].duration == pytest.approx(11.3)


def test_unknown_source_track_placement_mode_rejects_without_revision(tmp_path, monkeypatch):
    pid, plans = _editor_project(tmp_path, monkeypatch)
    result = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "overlay-image", "start": 1.0, "duration": 1.0,
        "placementMode": "slide-ish",
    }, 1)
    assert result.status == "REJECTED"
    assert result.errors[0]["code"] == "INVALID_PARAMETER"
    assert load_composition(pid).visual_overlays == []
    assert not [line for line in (plans / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]


def test_real_ffmpeg_source_track_insert_preserves_pixels_and_linked_audio_timing(tmp_path, monkeypatch):
    ffmpeg = renderer._ffmpeg()
    base_path = tmp_path / "insert-base-blue.png"
    insert_path = tmp_path / "insert-green.png"
    video_path = tmp_path / "insert-red-with-sine.mp4"
    _make_still(ffmpeg, base_path, "blue")
    _make_still(ffmpeg, insert_path, "green")
    _make_video_with_audio(ffmpeg, video_path)

    pid = "source-track-insert-render"
    plans = tmp_path / pid / "plans"
    plans.mkdir(parents=True)
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=3.0),
        scenes=[SceneIR(
            id="S01", start=0.0, duration=3.0, text="source insert test",
            layers=[Layer(type=LayerType.image, asset_id="base-blue", duration=3.0)],
        )],
        assets=[
            AssetRef(id="base-blue", type="image", file=str(base_path), width=40, height=40),
            AssetRef(id="insert-green", type="image", file=str(insert_path), width=40, height=40),
            AssetRef(id="insert-red-video", type="video", file=str(video_path), duration=2.0, width=40, height=40, fps=10),
        ],
        visual_overlays=[VisualOverlayClip(
            id="existing-red", asset_id="insert-red-video", start=0.5, duration=2.0,
            source_in=0.0, track_id="overlay-main", presentation_mode="full_frame",
            source_audio_enabled=True, x=0.0, y=0.0, width=1.0, height=1.0,
        )],
    )
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    save_composition(pid, composition)
    plans.joinpath("revisions.jsonl").write_text("", encoding="utf-8")

    inserted = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "insert-green", "start": 1.5, "duration": 0.5,
        "sourceIn": 0.0, "trackId": "overlay-main",
        "presentationMode": "full_frame", "placementMode": "insert",
    }, 1)
    assert inserted.status == "APPLIED", inserted.errors
    saved = load_composition(pid)
    assert saved.project.duration == pytest.approx(3.5)
    red_clips = sorted(
        (clip for clip in saved.visual_overlays if clip.asset_id == "insert-red-video"),
        key=lambda clip: clip.start,
    )
    assert [(clip.start, clip.duration, clip.source_in) for clip in red_clips] == [
        (0.5, 1.0, 0.0), (2.0, 1.0, 1.0),
    ]
    assert all(clip.source_audio_enabled for clip in red_clips)

    result = renderer.render_composition(
        saved, {asset.id: asset for asset in saved.assets}, tmp_path / "source-track-insert-render",
    )
    output = Path(result.final_path)
    assert output.is_file() and output.stat().st_size > 1000
    blue_before = _pixel_at(ffmpeg, output, 0.2, 160, 90)
    red_before = _pixel_at(ffmpeg, output, 0.8, 160, 90)
    green_insert = _pixel_at(ffmpeg, output, 1.75, 160, 90)
    red_after = _pixel_at(ffmpeg, output, 2.5, 160, 90)
    blue_tail = _pixel_at(ffmpeg, output, 3.25, 160, 90)
    assert blue_before[2] > blue_before[0] * 1.5, blue_before
    assert red_before[0] > red_before[1] * 1.5, red_before
    assert green_insert[1] > green_insert[0] * 1.3, green_insert
    assert red_after[0] > red_after[1] * 1.5, red_after
    assert blue_tail[2] > blue_tail[0] * 1.5, blue_tail

    audio = renderer._verify_muxed_audio(str(output), expected_duration=3.5)
    assert audio.get("codec") == "aac", audio
    assert audio.get("full_decode") == "PASS", audio
    assert _audio_window_mean_volume(ffmpeg, output, 0.1, 0.2) < -65
    assert _audio_window_mean_volume(ffmpeg, output, 0.8, 0.2) > -35
    assert _audio_window_mean_volume(ffmpeg, output, 1.65, 0.2) < -65
    assert _audio_window_mean_volume(ffmpeg, output, 2.5, 0.2) > -35

    # Exercise Overwrite as an encoded media edit too: replace part of the first
    # red clip, then prove the source audio is absent inside the replaced window
    # and present again in both surviving source ranges.
    overwritten = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "insert-green", "start": 0.75, "duration": 0.5,
        "sourceIn": 0.0, "trackId": "overlay-main",
        "presentationMode": "full_frame", "placementMode": "overwrite",
    }, inserted.composition_version)
    assert overwritten.status == "APPLIED", overwritten.errors
    overwritten_ir = load_composition(pid)
    red_clips = sorted(
        (clip for clip in overwritten_ir.visual_overlays if clip.asset_id == "insert-red-video"),
        key=lambda clip: clip.start,
    )
    assert [(clip.start, clip.duration, clip.source_in) for clip in red_clips] == [
        (0.5, 0.3, 0.0), (1.3, 0.2, 0.8), (2.0, 1.0, 1.0),
    ]
    assert all(clip.source_audio_enabled for clip in red_clips)

    overwritten_result = renderer.render_composition(
        overwritten_ir,
        {asset.id: asset for asset in overwritten_ir.assets},
        tmp_path / "source-track-overwrite-render",
    )
    overwritten_mp4 = Path(overwritten_result.final_path)
    assert overwritten_mp4.is_file() and overwritten_mp4.stat().st_size > 1000
    red_left = _pixel_at(ffmpeg, overwritten_mp4, 0.6, 160, 90)
    assert red_left[0] > 1.5 * red_left[2], red_left
    overwrite_pixel = _pixel_at(ffmpeg, overwritten_mp4, 1.0, 160, 90)
    assert overwrite_pixel[1] > overwrite_pixel[0] * 1.3, overwrite_pixel
    restored_red = _pixel_at(ffmpeg, overwritten_mp4, 1.35, 160, 90)
    assert restored_red[0] > restored_red[1] * 1.5, restored_red
    existing_insert = _pixel_at(ffmpeg, overwritten_mp4, 1.75, 160, 90)
    assert existing_insert[1] > 1.3 * existing_insert[0], existing_insert
    overwrite_audio = renderer._verify_muxed_audio(str(overwritten_mp4), expected_duration=3.5)
    assert overwrite_audio.get("codec") == "aac", overwrite_audio
    assert overwrite_audio.get("full_decode") == "PASS", overwrite_audio
    # Probe 100ms into the replaced interval, away from exact AAC/seek boundaries.
    assert _audio_window_mean_volume(ffmpeg, overwritten_mp4, 0.9, 0.2) < -65
    assert _audio_window_mean_volume(ffmpeg, overwritten_mp4, 1.3, 0.15) > -35
    assert _audio_window_mean_volume(ffmpeg, overwritten_mp4, 2.5, 0.2) > -35


def test_source_track_insert_extends_actual_tail_layer_when_scene_layers_are_unordered(tmp_path, monkeypatch):
    pid, _ = _editor_project(tmp_path, monkeypatch)
    ir = load_composition(pid)
    # SceneIR permits non-canonical list order but validates the time-sorted spans.
    # The latest segment is deliberately stored at index 0, not index -1.
    ir.scenes[0] = SceneIR(
        id="S01", start=0.0, duration=10.0, text="unordered scene layers",
        layers=[
            Layer(type=LayerType.image, asset_id="overlay-image", start=5.0, duration=5.0),
            Layer(type=LayerType.image, asset_id="overlay-image", start=0.0, duration=5.0),
        ],
    )
    save_composition(pid, ir)

    result = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "overlay-image", "start": 9.0, "duration": 2.0,
        "sourceIn": 0.0, "trackId": "overlay-main",
        "presentationMode": "full_frame", "placementMode": "insert",
    }, 1)
    assert result.status == "APPLIED", result.errors
    saved = load_composition(pid)
    assert saved.project.duration == pytest.approx(12.0)
    assert saved.scenes[0].duration == pytest.approx(12.0)
    layers = saved.scenes[0].layers
    assert [(layer.start, layer.duration) for layer in layers] == [(5.0, 7.0), (0.0, 5.0)]


def test_source_track_insert_preserves_single_frame_at_24_fps(tmp_path, monkeypatch):
    pid, _ = _editor_project(tmp_path, monkeypatch)
    ir = load_composition(pid)
    ir.project = ir.project.model_copy(update={"fps": 24})
    save_composition(pid, ir)

    one_frame = 1.0 / 24.0
    result = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "overlay-image", "start": one_frame, "duration": one_frame,
        "sourceIn": 0.0, "trackId": "overlay-main",
        "presentationMode": "full_frame", "placementMode": "insert",
    }, 1)
    assert result.status == "APPLIED", result.errors
    saved = load_composition(pid)
    clip = next(item for item in saved.visual_overlays if item.track_id == "overlay-main")
    assert clip.start == pytest.approx(0.041667, abs=1e-7)
    assert clip.duration == pytest.approx(0.041667, abs=1e-7)
    assert saved.project.duration == pytest.approx(10.041667, abs=1e-6)
    assert saved.scenes[0].duration == pytest.approx(10.041667, abs=1e-6)

def test_source_track_insert_half_frame_rounding_matches_browser_math_round(tmp_path, monkeypatch):
    """Server-side timeline snapping must match the browser at exact half-frame inputs."""
    pid, _ = _editor_project(tmp_path, monkeypatch)
    result = _apply(pid, "add_visual_overlay", "overlays", {
        "assetId": "overlay-image", "start": 1.25, "duration": 1.25,
        "sourceIn": 0.0, "trackId": "overlay-main",
        "presentationMode": "full_frame", "placementMode": "insert",
    }, 1)
    assert result.status == "APPLIED", result.errors

    saved = load_composition(pid)
    clip = next(c for c in saved.visual_overlays if c.asset_id == "overlay-image")
    # 1.25s at 10fps is exactly halfway between frames 12 and 13. The browser's
    # Math.round selects frame 13; Python's built-in round() would select frame 12.
    assert clip.start == pytest.approx(1.3)
    assert clip.duration == pytest.approx(1.3)
    assert saved.project.duration == pytest.approx(11.3)



def _save_detachable_source_audio_project(pid, base_path: Path, video_path: Path, enabled: bool) -> None:
    ir = load_composition(pid)
    base = AssetRef(id="source-audio-base", type="image", file=str(base_path), width=40, height=40)
    source = AssetRef(
        id="source-audio-video", type="video", file=str(video_path), duration=2.0,
        width=40, height=40, fps=10,
    )
    ir.project = ir.project.model_copy(update={"duration": 2.0})
    ir.scenes = [SceneIR(
        id="S01", start=0.0, duration=2.0, text="source audio detach",
        layers=[Layer(type=LayerType.image, asset_id=base.id, duration=2.0)],
    )]
    ir.assets = [base, source]
    ir.audio = []
    ir.visual_overlays = [VisualOverlayClip(
        id="overlay-with-detachable-audio",
        asset_id=source.id,
        start=0.5,
        duration=1.0,
        source_in=0.25,
        track_id="overlay-main",
        presentation_mode="full_frame",
        source_audio_enabled=enabled,
        source_audio_volume=0.7,
        x=0.0, y=0.0, width=1.0, height=1.0,
    )]
    save_composition(pid, ir)


def test_detach_overlay_source_audio_creates_editable_original_clip_without_changing_rendered_sound(tmp_path, monkeypatch):
    ffmpeg = renderer._ffmpeg()
    base_path = tmp_path / "detach-source-audio-base.png"
    video_path = tmp_path / "detach-source-audio-video.mp4"
    _make_still(ffmpeg, base_path, "blue")
    _make_video_with_audio(ffmpeg, video_path)
    pid, _ = _editor_project(tmp_path, monkeypatch)
    _save_detachable_source_audio_project(pid, base_path, video_path, enabled=True)

    original = load_composition(pid)
    assets = {asset.id: asset for asset in original.assets}
    inline_output = Path(renderer.render_composition(
        original.model_copy(deep=True), assets, tmp_path / "inline-source-audio",
    ).final_path)
    inline_report = renderer._verify_muxed_audio(str(inline_output), expected_duration=2.0)
    assert inline_report.get("codec") == "aac" and inline_report.get("full_decode") == "PASS", inline_report
    inline_active_db = _audio_window_mean_volume(ffmpeg, inline_output, 0.8, 0.25)
    inline_preroll_db = _audio_window_mean_volume(ffmpeg, inline_output, 0.1, 0.25)
    assert inline_preroll_db < -65, f"inline source audio leaked before clip start: {inline_preroll_db} dB"

    detached = _apply(pid, "separate_visual_overlay_source_audio", "overlay#0", {}, 1)
    assert detached.status == "APPLIED", detached.errors
    assert detached.composition_version == 2
    saved = load_composition(pid)
    clip = saved.visual_overlays[0]
    assert clip.source_audio_enabled is False
    assert len(saved.audio) == 1
    audio = saved.audio[0]
    assert audio.type == AudioTrackType.original
    assert audio.asset_id == clip.asset_id
    assert audio.track_id == "audio-original"
    assert (audio.start, audio.duration, audio.source_in, audio.source_out) == (0.5, 1.0, 0.25, 1.25)
    assert audio.volume == pytest.approx(0.7)
    assert audio.id and audio.id.startswith("audio-source-")

    detached_output = Path(renderer.render_composition(
        saved, {asset.id: asset for asset in saved.assets}, tmp_path / "detached-source-audio",
    ).final_path)
    report = renderer._verify_muxed_audio(str(detached_output), expected_duration=2.0)
    assert report.get("codec") == "aac" and report.get("full_decode") == "PASS", report
    detached_active_db = _audio_window_mean_volume(ffmpeg, detached_output, 0.8, 0.25)
    detached_preroll_db = _audio_window_mean_volume(ffmpeg, detached_output, 0.1, 0.25)
    assert detached_preroll_db < -65, f"detached source audio leaked before clip start: {detached_preroll_db} dB"
    assert abs(detached_active_db - inline_active_db) < 1.5, (
        f"detaching changed the rendered source-audio gain: inline={inline_active_db} dB, "
        f"detached={detached_active_db} dB"
    )
    assert len(saved.audio) == 1 and saved.visual_overlays[0].source_audio_enabled is False, (
        "rendering must not restore or duplicate inline source audio"
    )


def test_detach_overlay_source_audio_fails_closed_for_disabled_or_silent_source(tmp_path, monkeypatch):
    ffmpeg = renderer._ffmpeg()
    base_path = tmp_path / "detach-silent-base.png"
    silent_video = tmp_path / "detach-silent-video.mp4"
    _make_still(ffmpeg, base_path, "blue")
    _make_two_color_video(ffmpeg, silent_video)
    pid, _ = _editor_project(tmp_path, monkeypatch)
    _save_detachable_source_audio_project(pid, base_path, silent_video, enabled=False)

    disabled = _apply(pid, "separate_visual_overlay_source_audio", "overlay#0", {}, 1)
    assert disabled.status == "REJECTED"
    assert disabled.errors[0]["code"] == "SOURCE_AUDIO_NOT_ENABLED"
    assert load_composition(pid).audio == []

    ir = load_composition(pid)
    ir.visual_overlays[0] = ir.visual_overlays[0].model_copy(update={"source_audio_enabled": True})
    save_composition(pid, ir)
    silent = _apply(pid, "separate_visual_overlay_source_audio", "overlay#0", {}, 1)
    assert silent.status == "REJECTED"
    assert silent.errors[0]["code"] == "SOURCE_AUDIO_MISSING"
    saved = load_composition(pid)
    assert saved.version == 1 and saved.audio == []
    assert saved.visual_overlays[0].source_audio_enabled is True


def _add_existing_source_audio(pid: str, *, role: AudioTrackType = AudioTrackType.original, volume: float = 0.7, start: float = 0.5, duration: float = 1.0, source_in: float = 0.25, source_out: float = 1.25) -> None:
    ir = load_composition(pid)
    ir.audio.append(AudioTrack(
        type=role,
        id="existing-source-audio",
        track_id="audio-original" if role == AudioTrackType.original else "audio-dialogue",
        asset_id="source-audio-video",
        start=start,
        duration=duration,
        source_in=source_in,
        source_out=source_out,
        volume=volume,
    ))
    save_composition(pid, ir)


def test_detach_overlay_reuses_exact_matching_original_audio_without_duplicating(tmp_path, monkeypatch):
    ffmpeg = renderer._ffmpeg()
    base_path = tmp_path / "reuse-source-audio-base.png"
    video_path = tmp_path / "reuse-source-audio-video.mp4"
    _make_still(ffmpeg, base_path, "blue")
    _make_video_with_audio(ffmpeg, video_path)
    pid, _ = _editor_project(tmp_path, monkeypatch)
    _save_detachable_source_audio_project(pid, base_path, video_path, enabled=True)
    _add_existing_source_audio(pid)

    result = _apply(pid, "separate_visual_overlay_source_audio", "overlay#0", {}, 1)
    assert result.status == "APPLIED", result.errors
    assert result.composition_version == 2
    saved = load_composition(pid)
    # CompositionIR.version is the schema version; edit revisions are tracked
    # separately by result.composition_version / revisions.jsonl.
    assert saved.version == 1
    assert len(saved.audio) == 1
    assert saved.audio[0].id == "existing-source-audio"
    assert saved.audio[0].type == AudioTrackType.original
    assert saved.visual_overlays[0].source_audio_enabled is False


def test_detach_overlay_rejects_overlapping_dialogue_or_mismatched_original_audio(tmp_path, monkeypatch):
    ffmpeg = renderer._ffmpeg()
    base_path = tmp_path / "conflict-source-audio-base.png"
    video_path = tmp_path / "conflict-source-audio-video.mp4"
    _make_still(ffmpeg, base_path, "blue")
    _make_video_with_audio(ffmpeg, video_path)

    for role, volume in (
        (AudioTrackType.dialogue, 0.7),
        (AudioTrackType.original, 0.35),
    ):
        case_root = tmp_path / f"case-{role.value}-{volume}"
        case_root.mkdir(parents=True)
        monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(case_root))
        pid, _ = _editor_project(case_root, monkeypatch)
        # Keep the actual generated media outside each isolated project directory.
        _save_detachable_source_audio_project(pid, base_path, video_path, enabled=True)
        _add_existing_source_audio(pid, role=role, volume=volume)

        result = _apply(pid, "separate_visual_overlay_source_audio", "overlay#0", {}, 1)
        assert result.status == "REJECTED", result.errors
        assert result.errors[0]["code"] == "SOURCE_AUDIO_DUPLICATE_CONFLICT"
        saved = load_composition(pid)
        assert saved.version == 1
        assert len(saved.audio) == 1
        assert saved.audio[0].volume == pytest.approx(volume)
        assert saved.visual_overlays[0].source_audio_enabled is True



def _promotable_main_video_with_original_audio(
    tmp_path, monkeypatch, *, original_start: float, original_duration: float,
    original_source_in: float, original_source_out: float,
):
    ffmpeg = renderer._ffmpeg()
    video_path = tmp_path / "reused-main-source.mp4"
    base_path = tmp_path / "reused-main-base.png"
    _make_video_with_audio(ffmpeg, video_path)
    _make_still(ffmpeg, base_path, "green")
    pid, _ = _editor_project(tmp_path, monkeypatch)
    ir = load_composition(pid)
    ir.project = ir.project.model_copy(update={"duration": 6.0})
    ir.scenes = [SceneIR(
        id="S01", start=0.0, duration=6.0, text="reused source audio",
        layers=[
            Layer(type=LayerType.video, asset_id="reused-video", start=0.0, duration=2.0,
                  source_in=0.0, source_out=2.0, speed=1.0),
            Layer(type=LayerType.image, asset_id="reused-base", start=2.0, duration=4.0),
        ],
    )]
    ir.assets = [
        AssetRef(id="reused-video", type="video", file=str(video_path), duration=2.0,
                 width=40, height=40, fps=10),
        AssetRef(id="reused-base", type="image", file=str(base_path), width=40, height=40),
    ]
    ir.audio = [AudioTrack(
        type=AudioTrackType.original, id="later-original-audio", track_id="audio-original",
        asset_id="reused-video", start=original_start, duration=original_duration,
        source_in=original_source_in, source_out=original_source_out, volume=0.7,
    )]
    save_composition(pid, ir)
    return ffmpeg, pid


def test_promoting_reused_video_keeps_source_audio_when_existing_original_is_elsewhere(tmp_path, monkeypatch):
    ffmpeg, pid = _promotable_main_video_with_original_audio(
        tmp_path, monkeypatch,
        original_start=3.0, original_duration=1.0,
        original_source_in=0.25, original_source_out=1.25,
    )
    result = _apply(pid, "promote_layer_to_visual_track", "S01#0", {"start": 0.0}, 1)
    assert result.status == "APPLIED", result.errors
    saved = load_composition(pid)
    assert saved.visual_overlays[0].source_audio_enabled is True, (
        "an original-audio clip at 3–4s must not mute the promoted video's distinct 0–2s source window"
    )

    rendered = renderer.render_composition(
        saved, {asset.id: asset for asset in saved.assets}, tmp_path / "promoted-reused-source-audio",
    )
    output = Path(rendered.final_path)
    report = renderer._verify_muxed_audio(str(output), expected_duration=6.0)
    assert report.get("codec") == "aac" and report.get("full_decode") == "PASS", report
    promoted_window_db = _audio_window_mean_volume(ffmpeg, output, 0.8, 0.25)
    existing_window_db = _audio_window_mean_volume(ffmpeg, output, 3.3, 0.25)
    assert promoted_window_db > -35, f"promoted clip source audio was unexpectedly silent: {promoted_window_db} dB"
    assert existing_window_db > -38, f"existing original clip stopped rendering: {existing_window_db} dB"


def test_promoting_video_suppresses_exactly_duplicated_original_audio_window(tmp_path, monkeypatch):
    _, pid = _promotable_main_video_with_original_audio(
        tmp_path, monkeypatch,
        original_start=0.0, original_duration=2.0,
        original_source_in=0.0, original_source_out=2.0,
    )
    result = _apply(pid, "promote_layer_to_visual_track", "S01#0", {"start": 0.0}, 1)
    assert result.status == "APPLIED", result.errors
    saved = load_composition(pid)
    assert saved.visual_overlays[0].source_audio_enabled is False, (
        "an exact original-audio duplicate must remain single-mixed"
    )

def test_linked_audio_model_rejects_dangling_and_non_original_links(tmp_path, monkeypatch):
    pid, _plans = _linked_video_project(tmp_path, monkeypatch)
    comp = load_composition(pid)
    payload = comp.model_dump(mode="json")
    payload["audio"][0]["linked_visual_overlay_id"] = "missing-overlay"
    with pytest.raises(ValueError, match="references missing visual overlay"):
        CompositionIR.model_validate(payload)

    payload = comp.model_dump(mode="json")
    payload["audio"][0]["type"] = "bgm"
    with pytest.raises(ValueError, match="only original-audio clips"):
        CompositionIR.model_validate(payload)


def test_move_and_trim_visual_overlay_keep_linked_audio_timing_and_source_window(tmp_path, monkeypatch):
    pid, _plans = _linked_video_project(tmp_path, monkeypatch)
    moved = _apply(pid, "move_visual_overlay", "overlay#0", {"start": 3.0}, 1)
    assert moved.status == "APPLIED", moved.errors
    comp = load_composition(pid)
    clip = comp.visual_overlays[0]
    audio = next(track for track in comp.audio if track.id == "linked-audio")
    assert clip.start == audio.start == 3.0
    assert clip.duration == audio.duration == 4.0
    assert audio.linked_visual_overlay_id == clip.id
    assert audio.volume == 0.42

    trimmed = _apply(pid, "trim_visual_overlay", "overlay#0", {
        "start": 4.0, "sourceIn": 2.0, "duration": 2.5,
    }, 2)
    assert trimmed.status == "APPLIED", trimmed.errors
    comp = load_composition(pid)
    clip = comp.visual_overlays[0]
    audio = next(track for track in comp.audio if track.id == "linked-audio")
    assert (clip.start, clip.source_in, clip.duration) == (4.0, 2.0, 2.5)
    assert (audio.start, audio.source_in, audio.source_out, audio.duration) == (4.0, 2.0, 4.5, 2.5)
    assert audio.linked_visual_overlay_id == clip.id
    assert (audio.volume, audio.fadeIn, audio.fadeOut) == (0.42, 0.5, 0.75)


def test_linked_audio_lock_rejects_visual_move_without_partial_mutation(tmp_path, monkeypatch):
    pid, plans = _linked_video_project(tmp_path, monkeypatch, locked_audio=True)
    before = load_composition(pid)
    result = _apply(pid, "move_visual_overlay", "overlay#0", {"start": 3.0}, 1)
    assert result.status == "REJECTED"
    assert result.errors[0]["code"] == "TRACK_LOCKED"
    after = load_composition(pid)
    assert after.visual_overlays[0].start == before.visual_overlays[0].start == 2.0
    assert next(track for track in after.audio if track.id == "linked-audio").start == 2.0
    assert (plans / "revisions.jsonl").read_text(encoding="utf-8") == ""


def test_split_visual_overlay_splits_linked_audio_source_window_and_fades(tmp_path, monkeypatch):
    pid, _plans = _linked_video_project(tmp_path, monkeypatch)
    result = _apply(pid, "split_visual_overlay", "overlay#0", {"at": 4.0}, 1)
    assert result.status == "APPLIED", result.errors
    comp = load_composition(pid)
    assert len(comp.visual_overlays) == 2
    left_clip, right_clip = sorted(comp.visual_overlays, key=lambda clip: clip.start)
    assert (left_clip.start, left_clip.duration, left_clip.source_in) == (2.0, 2.0, 1.0)
    assert (right_clip.start, right_clip.duration, right_clip.source_in) == (4.0, 2.0, 3.0)

    assert len(comp.audio) == 2
    left_audio, right_audio = sorted(comp.audio, key=lambda track: track.start)
    assert (left_audio.start, left_audio.duration, left_audio.source_in, left_audio.source_out) == (2.0, 2.0, 1.0, 3.0)
    assert (right_audio.start, right_audio.duration, right_audio.source_in, right_audio.source_out) == (4.0, 2.0, 3.0, 5.0)
    assert left_audio.linked_visual_overlay_id == left_clip.id
    assert right_audio.linked_visual_overlay_id == right_clip.id
    assert left_audio.fadeIn == 0.5 and left_audio.fadeOut == 0.0
    assert right_audio.fadeIn == 0.0 and right_audio.fadeOut == 0.75
    assert left_audio.volume == right_audio.volume == 0.42


def test_remove_visual_overlay_removes_only_its_linked_audio(tmp_path, monkeypatch):
    pid, _plans = _linked_video_project(tmp_path, monkeypatch, include_unlinked=True)
    result = _apply(pid, "remove_visual_overlay", "overlay#0", {}, 1)
    assert result.status == "APPLIED", result.errors
    comp = load_composition(pid)
    assert comp.visual_overlays == []
    assert [track.id for track in comp.audio] == ["unlinked-audio"]
    assert comp.audio[0].linked_visual_overlay_id is None


def test_direct_audio_move_or_split_detaches_from_visual_clip(tmp_path, monkeypatch):
    pid, _plans = _linked_video_project(tmp_path, monkeypatch)
    moved = _apply(pid, "move_audio_clip", "audio#0", {"start": 3.0}, 1)
    assert moved.status == "APPLIED", moved.errors
    comp = load_composition(pid)
    assert comp.visual_overlays[0].start == 2.0
    assert comp.audio[0].start == 3.0
    assert comp.audio[0].linked_visual_overlay_id is None

    # Use a separate project so the source-window split is exercised against the
    # original linked geometry, not a side effect of the explicit move above.
    pid2, _plans2 = _linked_video_project(tmp_path / "split", monkeypatch)
    split = _apply(pid2, "split_audio_clip", "audio#0", {"at": 4.0}, 1)
    assert split.status == "APPLIED", split.errors
    comp2 = load_composition(pid2)
    assert len(comp2.audio) == 2
    assert all(track.linked_visual_overlay_id is None for track in comp2.audio)
    assert len(comp2.visual_overlays) == 1
    assert comp2.visual_overlays[0].id == "linked-overlay"

def test_linked_overlay_audio_volume_stays_editable_without_duplicate_embedded_sound(tmp_path, monkeypatch):
    pid, plans = _linked_video_project(tmp_path, monkeypatch)
    enable_embedded = _apply(pid, "set_visual_overlay", "overlay#0", {
        "source_audio_enabled": True,
    }, 1)
    assert enable_embedded.status == "REJECTED"
    assert enable_embedded.errors[0]["code"] == "SOURCE_AUDIO_ALREADY_LINKED"
    assert (plans / "revisions.jsonl").read_text(encoding="utf-8") == ""

    volume = _apply(pid, "set_visual_overlay", "overlay#0", {
        "source_audio_volume": 0.25,
    }, 1)
    assert volume.status == "APPLIED", volume.errors
    comp = load_composition(pid)
    clip = comp.visual_overlays[0]
    audio = next(track for track in comp.audio if track.id == "linked-audio")
    assert clip.source_audio_enabled is False
    assert clip.source_audio_volume == 0.25
    assert audio.volume == 0.25
    assert audio.linked_visual_overlay_id == clip.id



def test_promote_main_image_preserves_static_transform_in_real_mp4(tmp_path, monkeypatch):
    ffmpeg = renderer._ffmpeg()
    source_path = tmp_path / "transformed-main-red.png"
    _make_still(ffmpeg, source_path, "red")
    project_id = "promote-transformed-main-image"
    plans = tmp_path / project_id / "plans"
    plans.mkdir(parents=True)
    asset = AssetRef(
        id="main-red", type="image", file=str(source_path), width=64, height=64,
    )
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=2.0),
        scenes=[SceneIR(
            id="S01", start=0.0, duration=2.0, text="preserve transformed main image",
            layers=[Layer(
                type=LayerType.image, asset_id="main-red", start=0.0, duration=2.0,
                transform={"x": 0.25, "y": 0.5, "scale": 0.5, "rotation": 0.0,
                           "opacity": 0.75, "anchor": "center"},
            )],
        )],
        assets=[asset],
    )
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    save_composition(project_id, composition)
    (plans / "revisions.jsonl").write_text("", encoding="utf-8")

    promoted = _apply(project_id, "promote_layer_to_visual_track", "S01#0", {}, 1)
    assert promoted.status == "APPLIED", promoted.errors
    saved = load_composition(project_id)
    overlay = saved.visual_overlays[0]
    assert overlay.transform is not None
    assert overlay.transform.model_dump() == {
        "x": 0.25, "y": 0.5, "scale": 0.5, "rotation": 0.0,
        "opacity": 0.75, "anchor": "center",
    }
    assert saved.scenes[0].layers[0].type == LayerType.gap

    assets = {item.id: item for item in saved.assets}
    result = renderer.render_composition(saved, assets, tmp_path / "transform-lift-render")
    output = Path(result.final_path)
    assert output.is_file() and output.stat().st_size > 1000
    inside = _pixel_at(ffmpeg, output, 0.5, 80, 90)
    outside = _pixel_at(ffmpeg, output, 0.5, 240, 90)
    assert inside[0] > 110, f"transformed foreground should remain visible at inherited anchor: {inside}"
    assert max(outside) < 35, f"pixels outside the inherited transform should reveal the black gap: {outside}"


def test_promote_main_image_migrates_legacy_scale_opacity_to_transform(tmp_path, monkeypatch):
    ffmpeg = renderer._ffmpeg()
    source_path = tmp_path / "legacy-transform-main.png"
    _make_still(ffmpeg, source_path, "red")
    project_id = "promote-legacy-transform-main"
    plans = tmp_path / project_id / "plans"
    plans.mkdir(parents=True)
    asset = AssetRef(id="legacy-main", type="image", file=str(source_path), width=64, height=64)
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=2.0),
        scenes=[SceneIR(
            id="S01", start=0.0, duration=2.0, text="migrate legacy transform",
            layers=[Layer(
                type=LayerType.image, asset_id="legacy-main", duration=2.0,
                motion_instance={"parameters": {"transform": {"scale": 0.5, "opacity": 0.7}}},
            )],
        )],
        assets=[asset],
    )
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    save_composition(project_id, composition)
    (plans / "revisions.jsonl").write_text("", encoding="utf-8")

    promoted = _apply(project_id, "promote_layer_to_visual_track", "S01#0", {}, 1)
    assert promoted.status == "APPLIED", promoted.errors
    transform = load_composition(project_id).visual_overlays[0].transform
    assert transform is not None
    assert transform.scale == 0.5 and transform.opacity == 0.7
    assert transform.x == 0.5 and transform.y == 0.5 and transform.rotation == 0.0



def test_promote_main_video_preserves_static_transform_in_real_mp4(tmp_path, monkeypatch):
    ffmpeg = renderer._ffmpeg()
    source_path = tmp_path / "transformed-main-red.mp4"
    _make_video_with_audio(ffmpeg, source_path)
    project_id = "promote-transformed-main-video"
    plans = tmp_path / project_id / "plans"
    plans.mkdir(parents=True)
    asset = AssetRef(
        id="main-red-video", type="video", file=str(source_path), width=40, height=40,
    )
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=10, duration=2.0),
        scenes=[SceneIR(
            id="S01", start=0.0, duration=2.0, text="preserve transformed main video",
            layers=[Layer(
                type=LayerType.video, asset_id="main-red-video", start=0.0,
                duration=2.0, speed=1.0, source_in=0.0, source_out=2.0,
                transform={"x": 0.25, "y": 0.5, "scale": 0.5, "rotation": 0.0,
                           "opacity": 1.0, "anchor": "center"},
            )],
        )],
        assets=[asset],
    )
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    save_composition(project_id, composition)
    (plans / "revisions.jsonl").write_text("", encoding="utf-8")

    promoted = _apply(project_id, "promote_layer_to_visual_track", "S01#0", {}, 1)
    assert promoted.status == "APPLIED", promoted.errors
    saved = load_composition(project_id)
    transform = saved.visual_overlays[0].transform
    assert transform is not None
    assert transform.model_dump() == {
        "x": 0.25, "y": 0.5, "scale": 0.5, "rotation": 0.0,
        "opacity": 1.0, "anchor": "center",
    }

    assets = {item.id: item for item in saved.assets}
    result = renderer.render_composition(saved, assets, tmp_path / "transform-lift-video-render")
    output = Path(result.final_path)
    assert output.is_file() and output.stat().st_size > 1000
    inside = _pixel_at(ffmpeg, output, 0.5, 80, 90)
    outside = _pixel_at(ffmpeg, output, 0.5, 240, 90)
    assert inside[0] > 110, f"transformed video foreground should remain visible: {inside}"
    assert max(outside) < 35, f"pixels outside the inherited transform should reveal the base gap: {outside}"
