"""The main picture, overlays, audio and timed graphics must cut on one frame clock."""
from __future__ import annotations

from pathlib import Path

from video_director.editor import execute_command, load_composition, save_composition
from video_director.models import (
    AssetRef,
    AudioLane,
    AudioTrack,
    AudioTrackType,
    CompositionIR,
    Layer,
    LayerType,
    ProjectSpec,
    SceneIR,
    SubtitleCue,
    SubtitleTrack,
    VisualOverlayClip,
    VisualTrack,
    WatermarkLayer,
)


def _project(tmp_path: Path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "frame-clock-contract"
    plans = storage / project_id / "plans"
    plans.mkdir(parents=True)

    base_image = tmp_path / "base.png"
    overlay_image = tmp_path / "overlay.png"
    sfx_file = tmp_path / "sfx.wav"
    for path in (base_image, overlay_image, sfx_file):
        path.write_bytes(b"placeholder; no rendering occurs in this command-contract test")

    ir = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=30, duration=10.0),
        scenes=[
            SceneIR(
                id="S01",
                start=0.0,
                duration=10.0,
                text="frame clock",
                layers=[
                    Layer(
                        type=LayerType.image,
                        asset_id="base-image",
                        start=0.0,
                        duration=10.0,
                    )
                ],
            )
        ],
        assets=[
            AssetRef(id="base-image", type="image", file=str(base_image), width=320, height=180),
            AssetRef(id="overlay-image", type="image", file=str(overlay_image), width=80, height=80),
            AssetRef(id="sfx", type="audio", file=str(sfx_file), duration=10.0),
        ],
        visual_tracks=[VisualTrack(id="overlay-main", name="Overlay")],
        visual_overlays=[
            VisualOverlayClip(
                id="overlay-a",
                asset_id="overlay-image",
                start=0.0,
                duration=10.0,
                source_in=0.0,
                track_id="overlay-main",
                presentation_mode="sticker",
            )
        ],
        audio_tracks=[AudioLane(id="audio-sfx", name="SFX")],
        audio=[
            AudioTrack(
                type=AudioTrackType.sfx,
                id="sfx-a",
                asset_id="sfx",
                track_id="audio-sfx",
                start=0.0,
                duration=10.0,
                source_in=0.0,
                source_out=10.0,
            )
        ],
        watermarks=[
            WatermarkLayer(
                id="wm-a",
                kind="text",
                text="frame clock",
                start=0.0,
                duration=10.0,
            )
        ],
        subtitle_track=SubtitleTrack(
            mode="word",
            source="manual",
            cues=[SubtitleCue(start=0.5, end=1.5, text="keep this semantic cue")],
        ),
    )
    save_composition(project_id, ir)
    (plans / "revisions.jsonl").write_text("", encoding="utf-8")
    return project_id, plans


def _run(project_id: str, command: str, target_id: str, at: float):
    return execute_command(project_id, {
        "command": command,
        "targetId": target_id,
        "payload": {"at": at},
        "expectedVersion": 1,
    })


def test_add_edit_all_tracks_snaps_picture_overlay_audio_and_watermark_to_one_frame(tmp_path, monkeypatch):
    project_id, plans = _project(tmp_path, monkeypatch)
    # 1.017s is between frames at 30 fps. Browser preview and renderer must agree
    # on frame 31 (1.033333s), not split each representation at its own rounding.
    result = _run(project_id, "add_edit_all_tracks", "timeline", 1.017)
    assert result.status == "APPLIED", result.errors
    assert result.composition_version == 2

    comp = load_composition(project_id)
    expected_cut = 31 / 30
    scene = comp.scenes[0]
    assert abs(scene.layers[0].duration - expected_cut) < 1e-6
    assert abs(scene.layers[1].start - expected_cut) < 1e-6

    left_overlay, right_overlay = comp.visual_overlays
    assert abs((left_overlay.start + left_overlay.duration) - expected_cut) < 1e-6
    assert abs(right_overlay.start - expected_cut) < 1e-6

    left_audio, right_audio = comp.audio
    assert abs((left_audio.start + left_audio.duration) - expected_cut) < 1e-6
    assert abs(right_audio.start - expected_cut) < 1e-6
    assert abs((left_audio.source_out or 0) - (right_audio.source_in or 0)) < 1e-6

    left_watermark, right_watermark = comp.watermarks
    assert abs((left_watermark.start + (left_watermark.duration or 0)) - expected_cut) < 1e-6
    assert abs(right_watermark.start - expected_cut) < 1e-6

    # Word-level text remains a semantic anchor; only clips split, not meaning.
    assert [(cue.start, cue.end, cue.text) for cue in comp.subtitle_track.cues] == [
        (0.5, 1.5, "keep this semantic cue")
    ]
    revisions = [line for line in (plans / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(revisions) == 1


def test_direct_main_picture_split_uses_project_frame_boundary(tmp_path, monkeypatch):
    project_id, _plans = _project(tmp_path, monkeypatch)
    result = _run(project_id, "split_layer", "S01#0", 1.017)
    assert result.status == "APPLIED", result.errors
    scene = load_composition(project_id).scenes[0]
    expected_cut = 31 / 30
    assert abs(scene.layers[0].duration - expected_cut) < 1e-6
    assert abs(scene.layers[1].start - expected_cut) < 1e-6


def test_direct_audio_split_uses_project_frame_boundary(tmp_path, monkeypatch):
    project_id, _plans = _project(tmp_path, monkeypatch)
    result = _run(project_id, "split_audio_clip", "audio#0", 1.017)
    assert result.status == "APPLIED", result.errors
    comp = load_composition(project_id)
    expected_cut = 31 / 30
    left, right = comp.audio
    assert abs((left.start + left.duration) - expected_cut) < 1e-6
    assert abs(right.start - expected_cut) < 1e-6
    assert abs((left.source_out or 0) - (right.source_in or 0)) < 1e-6


def test_move_trim_and_promote_snaps_to_project_frame(tmp_path, monkeypatch):
    """移动/修剪/提升类命令与切分共用同一项目帧钟（对抗审查 2026-10-14）。"""
    project_id, _plans = _project(tmp_path, monkeypatch)

    # trim_audio_clip: 输出时长对齐到帧钟，source_out 由对齐后时长反推。
    # （夹具音频全长 10s 等于项目总长，先裁短才能移动。）
    trimmed = execute_command(project_id, {
        "command": "trim_audio_clip", "targetId": "audio#0",
        "payload": {"start": 0.5, "sourceIn": 0.0, "sourceOut": 2.017},
        "expectedVersion": 1,
    })
    assert trimmed.status == "APPLIED", trimmed.errors
    track = load_composition(project_id).audio[0]
    expected_duration = round(61 / 30, 6)
    assert abs(track.duration - expected_duration) < 1e-6
    assert abs(track.source_out - expected_duration) < 1e-6

    # move_audio_clip: 1.017s 对齐到第 31 帧（1.033333s），而不是毫秒取整。
    moved = execute_command(project_id, {
        "command": "move_audio_clip", "targetId": "audio#0",
        "payload": {"start": 1.017}, "expectedVersion": 2,
    })
    assert moved.status == "APPLIED", moved.errors
    expected_start = 31 / 30
    assert abs(load_composition(project_id).audio[0].start - expected_start) < 1e-6

    # promote_layer_to_visual_track: 不传 start 时原位提升（时长=片长的
    # 主画面层无法在非零位置放置，显式 start 的 snap 路径由 move/trim 已锁）。
    promoted = execute_command(project_id, {
        "command": "promote_layer_to_visual_track", "targetId": "S01#0",
        "payload": {}, "expectedVersion": 3,
    })
    assert promoted.status == "APPLIED", promoted.errors
    overlay = load_composition(project_id).visual_overlays[-1]
    assert abs(overlay.start - 0.0) < 1e-6
