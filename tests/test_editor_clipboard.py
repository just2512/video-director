"""Regression coverage for the native multi-track clip clipboard commands."""
from pathlib import Path

import pytest

from video_director import audio as vd_audio
from video_director import editor
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
    VisualOverlayClip,
    VisualTrack,
)


ROOT = Path(__file__).resolve().parents[1]


def _composition():
    assets = [
        AssetRef(id="base-image", type="image", file="/tmp/base.png", duration=10),
        AssetRef(id="music-asset", type="audio", file="audio-library://music-asset", duration=5),
        AssetRef(id="overlay-video", type="video", file="/tmp/overlay.mp4", duration=8),
    ]
    return CompositionIR(
        project=ProjectSpec(width=1080, height=1920, fps=30, duration=10),
        scenes=[
            SceneIR(
                id="S1",
                start=0,
                duration=10,
                text="test scene",
                layers=[
                    Layer(type=LayerType.image, asset_id="base-image", start=0, duration=10),
                ],
            ),
        ],
        assets=assets,
        audio_tracks=[
            AudioLane(id="audio-main", name="背景音乐", order=0),
            AudioLane(id="audio-voice-2", name="旁白 2", order=1),
        ],
        audio=[
            AudioTrack(
                type=AudioTrackType.bgm,
                id="audio-original-1",
                asset_id="music-asset",
                track_id="audio-main",
                start=0,
                duration=3,
                source_in=0,
                source_out=3,
                volume=0.4,
            ),
        ],
        visual_tracks=[
            VisualTrack(id="visual-main", name="主画面", order=0),
            VisualTrack(id="visual-pip", name="画中画", order=1),
        ],
        visual_overlays=[
            VisualOverlayClip(
                id="overlay-original-1",
                asset_id="overlay-video",
                start=0,
                duration=2,
                source_in=1,
                track_id="visual-main",
            ),
        ],
    )


def test_add_audio_asset_clip_can_populate_a_second_voiceover_lane_and_loop_music(tmp_path, monkeypatch):
    ir = _composition()
    music_file = tmp_path / "library-music.mp3"
    music_file.write_bytes(b"fake audio fixture")
    library_asset = AssetRef(
        id="library-music", type="bgm", file=str(music_file), duration=4, keywords=["music"]
    )
    monkeypatch.setattr(vd_audio, "scan_library", lambda: {library_asset.id: library_asset})

    editor._cmd_add_audio_asset_clip(
        ir, "audio-tracks",
        {"assetId": "library-music", "trackId": "audio-voice-2", "role": "dialogue",
         "start": 2, "duration": 3, "volume": 1.0},
    )
    voice = ir.audio[-1]
    assert voice.type == AudioTrackType.dialogue
    assert voice.track_id == "audio-voice-2"
    assert voice.start == 2 and voice.duration == 3
    assert voice.source_out == 3
    assert any(asset.id == "library-music" and asset.file == "audio-library://library-music" for asset in ir.assets)

    editor._cmd_add_audio_asset_clip(
        ir, "audio-tracks",
        {"assetId": "library-music", "trackId": "audio-main", "role": "bgm",
         "start": 4, "duration": 6},
    )
    looping = ir.audio[-1]
    assert looping.type == AudioTrackType.bgm
    assert looping.start == 4 and looping.duration == 6
    assert looping.source_out is None


def test_looped_bgm_right_trim_preserves_looping_and_uses_project_frames():
    ir = _composition()
    ir.audio[0] = ir.audio[0].model_copy(update={"source_in": 0, "source_out": None, "duration": 3})

    editor._cmd_trim_audio_clip(
        ir, "audio#0", {"start": 0, "sourceIn": 0, "duration": 8, "loopToDuration": True}
    )

    assert ir.audio[0].duration == 8
    assert ir.audio[0].source_in == 0
    assert ir.audio[0].source_out is None


def test_explicit_audio_trim_cannot_extend_past_source_duration():
    ir = _composition()
    with pytest.raises(editor.EditorError, match="超出素材时长"):
        editor._cmd_trim_audio_clip(
            ir, "audio#0", {"start": 0, "sourceIn": 0, "sourceOut": 6}
        )

def test_paste_audio_clip_keeps_media_settings_but_uses_destination_lane_and_time():
    ir = _composition()
    source = ir.audio[0].model_dump(mode="json")

    changed, stale = editor._cmd_paste_audio_clip(
        ir,
        "audio-tracks",
        {"clip": source, "start": 4, "trackId": "audio-voice-2"},
        "test-project",
    )

    pasted = ir.audio[-1]
    assert len(ir.audio) == 2
    assert pasted.id and pasted.id != ir.audio[0].id
    assert pasted.asset_id == "music-asset"
    assert pasted.track_id == "audio-voice-2"
    assert pasted.start == 4
    assert pasted.duration == 3
    assert pasted.volume == 0.4
    assert pasted.linked_visual_overlay_id is None
    assert "project:audio:render" in stale
    assert any(key.startswith("audio:bgm:") for key in changed)


def test_paste_visual_clip_keeps_transform_and_uses_destination_track_and_time():
    ir = _composition()
    source = ir.visual_overlays[0].model_dump(mode="json")
    source["x"] = 0.2
    source["y"] = 0.25
    source["width"] = 0.4
    source["height"] = 0.35

    changed, stale = editor._cmd_paste_visual_overlay(
        ir,
        "overlays",
        {"clip": source, "start": 4, "trackId": "visual-pip"},
        "test-project",
    )

    pasted = ir.visual_overlays[-1]
    assert len(ir.visual_overlays) == 2
    assert pasted.id != ir.visual_overlays[0].id
    assert pasted.asset_id == "overlay-video"
    assert pasted.track_id == "visual-pip"
    assert pasted.start == 4
    assert pasted.source_in == 1
    assert pasted.x == 0.2 and pasted.y == 0.25
    assert pasted.width == 0.4 and pasted.height == 0.35
    assert "project:visual-overlays:render" in stale
    assert any(key.startswith("visual-overlay:") for key in changed)


def test_paste_clips_reject_a_locked_destination_track():
    ir = _composition()
    ir.audio_tracks[1] = ir.audio_tracks[1].model_copy(update={"locked": True})
    with pytest.raises(editor.EditorError, match="已锁定"):
        editor._cmd_paste_audio_clip(
            ir,
            "audio-tracks",
            {"clip": ir.audio[0].model_dump(mode="json"), "start": 4, "trackId": "audio-voice-2"},
            "test-project",
        )

    ir = _composition()
    ir.visual_tracks[1] = ir.visual_tracks[1].model_copy(update={"locked": True})
    with pytest.raises(editor.EditorError, match="已锁定"):
        editor._cmd_paste_visual_overlay(
            ir,
            "overlays",
            {"clip": ir.visual_overlays[0].model_dump(mode="json"), "start": 4, "trackId": "visual-pip"},
            "test-project",
        )


def test_audio_clip_delete_updates_linked_picture_source_audio_state():
    ir = _composition()
    ir.audio.append(
        AudioTrack(
            type=AudioTrackType.original,
            id="linked-source-audio",
            asset_id="overlay-video",
            track_id="audio-main",
            start=0,
            duration=2,
            source_in=1,
            source_out=3,
            linked_visual_overlay_id="overlay-original-1",
            volume=0.8,
        )
    )
    ir.visual_overlays[0] = ir.visual_overlays[0].model_copy(update={"source_audio_enabled": True})

    changed, stale = editor._cmd_remove_audio_clip(ir, "audio#1", {})

    assert len(ir.audio) == 1
    assert ir.visual_overlays[0].source_audio_enabled is False
    assert "visual-overlay:overlay-original-1" in changed
    assert "project:visual-overlays:render" in stale


def test_populated_audio_track_requires_explicit_delete_and_removes_contents_when_confirmed():
    ir = _composition()
    with pytest.raises(editor.EditorError, match="deleteContents=true"):
        editor._cmd_remove_audio_track(ir, "audio-main", {})

    editor._cmd_remove_audio_track(ir, "audio-main", {"deleteContents": True})

    assert all(lane.id != "audio-main" for lane in ir.audio_tracks)
    assert ir.audio == []
    assert [lane.order for lane in ir.audio_tracks] == [0]


def test_populated_visual_track_requires_explicit_delete_and_removes_contents_when_confirmed():
    ir = _composition()
    with pytest.raises(editor.EditorError, match="deleteContents=true"):
        editor._cmd_remove_visual_track(ir, "visual-main", {})

    editor._cmd_remove_visual_track(ir, "visual-main", {"deleteContents": True})

    assert all(track.id != "visual-main" for track in ir.visual_tracks)
    assert ir.visual_overlays == []
    assert [track.order for track in ir.visual_tracks] == [0]


def test_clipboard_frontend_is_loaded_and_uses_shared_command_service():
    clipboard = ROOT / "webui" / "static" / "js" / "clip-clipboard.js"
    index = ROOT / "webui" / "static" / "index.html"
    source = clipboard.read_text(encoding="utf-8")
    html = index.read_text(encoding="utf-8")
    asset_picker = ROOT / "webui" / "static" / "js" / "audio-track-asset-picker.js"
    asset_picker_source = asset_picker.read_text(encoding="utf-8")

    assert '/static/js/clip-clipboard.js' in html
    assert '/static/js/audio-track-asset-picker.js' in html
    assert "add_audio_asset_clip" in asset_picker_source
    assert "/api/audio_library" in asset_picker_source
    assert "paste_audio_clip" in source and "paste_visual_overlay" in source
    assert "remove_audio_clip" in source and "remove_visual_overlay" in source
    assert "window.executeCommand" in source
    assert "Ctrl+V" in source
    assert "editor:timeline-rendered" in source
