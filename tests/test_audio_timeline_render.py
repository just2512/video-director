"""Regression coverage for timeline-aware audio rendering."""
from __future__ import annotations

import copy
from types import SimpleNamespace

from tests.fixtures.fixture_data import RECIPE_PROJECT
from video_director.models import CompositionIR
from video_director import renderer


def _audio_composition() -> CompositionIR:
    data = copy.deepcopy(RECIPE_PROJECT["composition"])
    data["project"]["duration"] = 10.0
    data["scenes"] = [{
        "id": "S01", "start": 0.0, "duration": 10.0, "text": "audio timeline test",
        "layers": [{"type": "image", "asset_id": "img1", "start": 0.0, "duration": 10.0}],
    }]
    data["audio"] = [
        {"type": "narration", "start": 0.0, "duration": 10.0, "volume": 0.5,
         "fadeIn": 0.5, "fadeOut": 1.0},
        {"type": "bgm", "asset_id": "bgm-one", "start": 2.0, "duration": 4.0,
         "volume": 0.4, "fadeIn": 1.0, "fadeOut": 1.0},
        {"type": "bgm", "asset_id": "bgm-two", "start": 5.0, "duration": 8.0,
         "volume": 0.25},
        {"type": "ambience", "asset_id": "ambience-one", "start": 3.0, "duration": 3.0,
         "volume": 0.15},
        {"type": "dialogue", "asset_id": "dialogue-one", "start": 1.0, "duration": 2.0,
         "volume": 0.8},
        {"type": "sfx", "asset_id": "sfx-one", "start": 9.8, "duration": 1.0,
         "volume": 0.7, "fadeOut": 0.1},
    ]
    return CompositionIR(**data)


def test_mix_audio_respects_clip_start_duration_fades_and_all_bgm_tracks(tmp_path, monkeypatch):
    """SFX/dialogue remain at their authored time; looping tracks are clipped to the project."""
    ir = _audio_composition()
    ids = ("bgm-one", "bgm-two", "ambience-one", "dialogue-one", "sfx-one")
    assets = {
        aid: SimpleNamespace(id=aid, type="audio", file=f"/fake/{aid}.wav")
        for aid in ids
    }
    calls = []
    monkeypatch.setattr(renderer, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(renderer, "_run", lambda cmd: calls.append(list(cmd)))
    monkeypatch.setattr(renderer.os, "remove", lambda _path: None)

    out = renderer.mix_audio(
        "visual.mp4", ir, assets, "/fake/narration.wav", str(tmp_path / "mix.mp4")
    )

    assert out == str(tmp_path / "mix.mp4")
    assert len(calls) == 2, "first mix to a temporary audio file, then mux into video"
    command = calls[0]
    assert command.count("-stream_loop") == 3, "both BGM clips and ambience should loop to their own duration"
    graph = command[command.index("-filter_complex") + 1]

    # Narration respects its mix level and fade envelope.
    assert "volume=-6.02dB,atrim=0:10.000,afade=t=in:st=0:d=0.500,afade=t=out:st=9.000:d=1.000[nar]" in graph

    # Each BGM remains a separate positioned clip; longer-than-project tracks are clipped.
    assert "atrim=0:4.000,adelay=2000:all=1[bgm0]" in graph
    assert "atrim=0:5.000,adelay=5000:all=1[bgm1]" in graph
    assert "atrim=0:3.000,adelay=3000:all=1[ambience0]" in graph

    # One-shot sounds are not looped, and durations are bounded by the output project.
    assert "atrim=0:2.000,adelay=1000|1000[dialogue0]" in graph
    assert "atrim=0:0.200,afade=t=out:st=0.100:d=0.100,adelay=9800|9800[sfx0]" in graph

    # A late SFX alone must not make FFmpeg's -shortest mux cut off the silent video tail.
    assert "apad,atrim=0:10.000[aout]" in graph


def test_muted_narration_is_not_mixed(tmp_path, monkeypatch):
    ir = _audio_composition()
    ir.audio[0].muted = True
    calls = []
    monkeypatch.setattr(renderer, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(renderer, "_run", lambda cmd: calls.append(list(cmd)))
    monkeypatch.setattr(renderer.os, "remove", lambda _path: None)
    assets = {
        aid: SimpleNamespace(id=aid, type="audio", file=f"/fake/{aid}.wav")
        for aid in ("bgm-one", "bgm-two", "ambience-one", "dialogue-one", "sfx-one")
    }

    renderer.mix_audio("visual.mp4", ir, assets, "/fake/narration.wav", str(tmp_path / "mix.mp4"))
    graph = calls[0][calls[0].index("-filter_complex") + 1]
    assert "[nar]" not in graph
    assert "[bgm0]" in graph



def test_trimmed_audio_clips_render_from_their_source_window(tmp_path, monkeypatch):
    """A user-trimmed BGM/SFX is source-seeked, not silently looped from zero."""
    ir = _audio_composition()
    ir.audio[1] = ir.audio[1].model_copy(update={
        "start": 2.0, "duration": 3.0, "source_in": 1.0, "source_out": 4.0,
    })
    ir.audio[-1] = ir.audio[-1].model_copy(update={
        "start": 5.0, "duration": 1.0, "source_in": 2.0, "source_out": 3.0,
    })
    calls = []
    monkeypatch.setattr(renderer, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(renderer, "_run", lambda cmd: calls.append(list(cmd)))
    monkeypatch.setattr(renderer.os, "remove", lambda _path: None)
    assets = {
        aid: SimpleNamespace(id=aid, type="audio", file=f"/fake/{aid}.wav")
        for aid in ("bgm-one", "bgm-two", "ambience-one", "dialogue-one", "sfx-one")
    }

    renderer.mix_audio(
        "visual.mp4", ir, assets, "/fake/narration.wav", str(tmp_path / "trimmed.mp4")
    )
    command = calls[0]
    # The trimmed BGM input uses an explicit source window, rather than stream_loop.
    bgm_pos = command.index("/fake/bgm-one.wav")
    assert command[bgm_pos - 5:bgm_pos] == ["-ss", "1.000", "-t", "3.000", "-i"]
    # The other, untrimmed BGM still loops, preserving the existing loop-by-default behavior.
    assert command.count("-stream_loop") == 2  # bgm-two + ambience-one
    # SFX likewise starts at the trimmed source offset.
    sfx_pos = command.index("/fake/sfx-one.wav")
    assert command[sfx_pos - 5:sfx_pos] == ["-ss", "2.000", "-t", "1.000", "-i"]
    graph = command[command.index("-filter_complex") + 1]
    assert "atrim=0:3.000,adelay=2000:all=1[bgm0]" in graph
    assert "atrim=0:1.000" in graph
    # Existing test fixture carries a 0.1 s fade-out, so the adelay filter
    # follows the fade rather than being adjacent to atrim.
    assert "adelay=5000|5000[sfx0]" in graph
