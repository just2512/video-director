"""Regression coverage for timeline-aware audio rendering."""
from __future__ import annotations

import copy
from types import SimpleNamespace

from tests.fixtures.fixture_data import RECIPE_PROJECT
from video_director.models import CompositionIR
from video_director import renderer


def _patch_audio_postmix(monkeypatch):
    monkeypatch.setattr(
        renderer, "_normalize_audio_loudness",
        lambda _mixed, _normalized, _ffmpeg_bin=None: {"mode": "two-pass"},
    )
    monkeypatch.setattr(
        renderer, "_verify_muxed_audio",
        lambda _output, _duration: {"codec": "aac", "full_decode": "PASS"},
    )


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
    _patch_audio_postmix(monkeypatch)
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
    _patch_audio_postmix(monkeypatch)
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
    _patch_audio_postmix(monkeypatch)
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


def test_mix_audio_uses_normalized_pcm_and_reencodes_aac(tmp_path, monkeypatch):
    ir = _audio_composition()
    calls = []
    normalized = []
    verified = []
    monkeypatch.setattr(renderer, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(renderer, "_run", lambda cmd: calls.append(list(cmd)))
    monkeypatch.setattr(renderer.os, "remove", lambda _path: None)

    def normalize(source, destination, ffmpeg_bin=None):
        normalized.append((source, destination, ffmpeg_bin))
        return {"mode": "two-pass", "target_i_lufs": -16.0}

    def verify(path, duration):
        verified.append((path, duration))
        return {"codec": "aac", "full_decode": "PASS"}

    monkeypatch.setattr(renderer, "_normalize_audio_loudness", normalize)
    monkeypatch.setattr(renderer, "_verify_muxed_audio", verify)
    output = tmp_path / "normalized.mp4"
    result = renderer.mix_audio(
        "visual.mp4", ir, {}, "/fake/narration.wav", str(output)
    )

    assert result == str(output)
    assert len(calls) == 2
    assert calls[0][-1].endswith(".mix.wav")
    assert normalized == [(str(output) + ".mix.wav", str(output) + ".normalized.wav", "ffmpeg")]
    first_input = calls[1].index("-i")
    second_input = calls[1].index("-i", first_input + 1)
    assert calls[1][second_input + 1] == str(output) + ".normalized.wav"
    assert calls[1][calls[1].index("-c:a") + 1] == "aac"
    assert calls[1][calls[1].index("-b:a") + 1] == "192k"
    assert verified == [(str(output), ir.project.duration)]


def test_real_ffmpeg_loudness_normalization_mux_and_full_decode(tmp_path, monkeypatch):
    """Exercise actual loudnorm, MP4 mux, ffprobe stream check and EOF decode."""
    import json
    import math
    import re
    import subprocess
    import pytest

    try:
        ffmpeg = renderer._ffmpeg()
        ffprobe = renderer.prober._resolve_ffprobe()
    except Exception as exc:
        pytest.skip(f"FFmpeg unavailable in this environment: {exc}")
    if not ffprobe:
        pytest.skip("ffprobe unavailable in this environment")

    narration = tmp_path / "narration.wav"
    visual = tmp_path / "visual.mp4"
    output = tmp_path / "final.mp4"
    subprocess.run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-af", "volume=0.12", "-c:a", "pcm_s16le", str(narration),
    ], check=True, capture_output=True, text=True)
    subprocess.run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=320x180:r=25:d=2",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(visual),
    ], check=True, capture_output=True, text=True)

    ir = _audio_composition()
    ir.project.duration = 2.0
    ir.audio = [ir.audio[0].model_copy(update={
        "start": 0.0, "duration": 2.0, "volume": 1.0,
        "fadeIn": 0.0, "fadeOut": 0.0, "muted": False,
    })]
    normalize_reports = []
    verify_reports = []
    original_normalize = renderer._normalize_audio_loudness
    original_verify = renderer._verify_muxed_audio

    def capture_normalize(*args, **kwargs):
        report = original_normalize(*args, **kwargs)
        normalize_reports.append(report)
        return report

    def capture_verify(*args, **kwargs):
        report = original_verify(*args, **kwargs)
        verify_reports.append(report)
        return report

    monkeypatch.setattr(renderer, "_normalize_audio_loudness", capture_normalize)
    monkeypatch.setattr(renderer, "_verify_muxed_audio", capture_verify)
    result = renderer.mix_audio(str(visual), ir, {}, str(narration), str(output))

    assert result == str(output)
    assert output.is_file() and output.stat().st_size > 0
    assert normalize_reports and normalize_reports[0]["mode"] == "two-pass"
    assert normalize_reports[0]["target_i_lufs"] == -16.0
    assert verify_reports and verify_reports[0]["full_decode"] == "PASS"
    assert verify_reports[0]["codec"] == "aac"
    assert math.isclose(verify_reports[0]["audio_duration"], 2.0, abs_tol=0.35)

    # Measure the final AAC stream again, rather than asserting only filter arguments.
    target = (
        f"loudnorm=I={renderer._AUDIO_LOUDNESS_TARGET_I}:"
        f"TP={renderer._AUDIO_LOUDNESS_TARGET_TP}:"
        f"LRA={renderer._AUDIO_LOUDNESS_TARGET_LRA}:print_format=json"
    )
    _, stderr = renderer._run_capture([
        ffmpeg, "-hide_banner", "-nostats", "-i", str(output),
        "-map", "0:a:0", "-af", target, "-f", "null", "-",
    ])
    match = re.search(r'\{\s*"input_i".*?\}', stderr, re.S)
    assert match, stderr[-1200:]
    measured = json.loads(match.group(0))
    integrated_lufs = float(measured["input_i"])
    assert math.isfinite(integrated_lufs)
    assert -18.0 <= integrated_lufs <= -14.0, integrated_lufs


def test_loudness_normalization_uses_explicit_fallback_for_unmeasurable_audio(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        renderer, "_run_capture",
        lambda _cmd, timeout=600: ("", '''{
          "input_i": "-inf",
          "input_tp": "-inf",
          "input_lra": "0.00",
          "input_thresh": "-70.00",
          "target_offset": "inf"
        }'''),
    )
    monkeypatch.setattr(renderer, "_run", lambda cmd: calls.append(list(cmd)))

    report = renderer._normalize_audio_loudness(
        str(tmp_path / "silent.wav"), str(tmp_path / "normalized.wav"), "ffmpeg"
    )
    assert report["mode"] == "single-pass-fallback"
    assert len(calls) == 1
    filter_expr = calls[0][calls[0].index("-af") + 1]
    assert "loudnorm=I=-16.0:TP=-1.5:LRA=11.0" in filter_expr
    assert "measured_I=" not in filter_expr
    assert calls[0][-1].endswith("normalized.wav")
