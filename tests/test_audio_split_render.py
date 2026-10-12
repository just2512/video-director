"""Real FFmpeg regression for source-contiguous audio clip splits."""
from __future__ import annotations

import copy
import math
import subprocess
from array import array
from pathlib import Path
from types import SimpleNamespace

from tests.fixtures.fixture_data import RECIPE_PROJECT
from video_director import renderer
from video_director.models import CompositionIR


def _run_checked(command: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, f"command failed: {command!r}\n{result.stderr}"
    return result


def _rms(samples: array, start: int, end: int) -> float:
    window = samples[start:end]
    assert window, f"empty decoded audio window: {start}:{end}"
    return math.sqrt(sum(value * value for value in window) / len(window))


def test_split_sfx_source_windows_mux_to_decodable_contiguous_mp4(tmp_path: Path, monkeypatch):
    """Two split halves must render from adjacent source windows, not restart at zero."""
    ffmpeg = renderer._ffmpeg()
    video = tmp_path / "silent-video.mp4"
    source = tmp_path / "continuous-tone.wav"
    output = tmp_path / "split-audio.mp4"

    _run_checked([
        ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=black:s=96x64:r=10:d=2", "-an", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-t", "2", str(video),
    ])
    _run_checked([
        ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
        "sine=frequency=997:sample_rate=44100:duration=2",
        "-c:a", "pcm_s16le", str(source),
    ])

    data = copy.deepcopy(RECIPE_PROJECT["composition"])
    data["project"]["duration"] = 2.0
    data["scenes"] = [{
        "id": "S01", "start": 0.0, "duration": 2.0, "text": "split audio render",
        "layers": [{
            "type": "image", "asset_id": "img1", "start": 0.0, "duration": 2.0,
        }],
    }]
    data["audio"] = [
        {
            "type": "sfx", "asset_id": "split-tone", "start": 0.0, "duration": 1.0,
            "source_in": 0.0, "source_out": 1.0, "volume": 1.0,
        },
        {
            "type": "sfx", "asset_id": "split-tone", "start": 1.0, "duration": 1.0,
            "source_in": 1.0, "source_out": 2.0, "volume": 1.0,
        },
    ]
    composition = CompositionIR(**data)
    assets = {
        "split-tone": SimpleNamespace(id="split-tone", type="audio", file=str(source)),
    }

    calls: list[list[str]] = []
    actual_run = renderer._run

    def capture_and_run(command):
        calls.append(list(command))
        return actual_run(command)

    monkeypatch.setattr(renderer, "_run", capture_and_run)
    result = renderer.mix_audio(str(video), composition, assets, None, str(output))
    final_mp4 = Path(result)
    assert final_mp4.is_file() and final_mp4.stat().st_size > 1000

    mix_commands = [cmd for cmd in calls if "-filter_complex" in cmd and str(source) in cmd]
    assert mix_commands, "renderer did not execute the audio mix command"
    command = mix_commands[0]
    source_positions = [i for i, arg in enumerate(command) if arg == str(source)]
    assert len(source_positions) == 2, f"expected two source windows, got {source_positions}"
    assert command[source_positions[0] - 5:source_positions[0]] == [
        "-ss", "0.000", "-t", "1.000", "-i",
    ]
    assert command[source_positions[1] - 5:source_positions[1]] == [
        "-ss", "1.000", "-t", "1.000", "-i",
    ]

    decoded_pcm = tmp_path / "decoded.f32"
    _run_checked([
        ffmpeg, "-y", "-v", "error", "-i", str(final_mp4), "-map", "0:a:0",
        "-ac", "1", "-ar", "44100", "-f", "f32le", str(decoded_pcm),
    ])
    raw = decoded_pcm.read_bytes()
    samples = array("f")
    samples.frombytes(raw[: len(raw) - (len(raw) % 4)])
    assert len(samples) >= 87_000, f"decoded audio duration too short: {len(samples) / 44100:.3f}s"
    left_rms = _rms(samples, int(0.15 * 44100), int(0.85 * 44100))
    right_rms = _rms(samples, int(1.15 * 44100), int(1.85 * 44100))
    assert left_rms > 0.01 and right_rms > 0.01, (left_rms, right_rms)
    assert abs(left_rms - right_rms) / max(left_rms, right_rms) < 0.30, (left_rms, right_rms)

    _run_checked([ffmpeg, "-v", "error", "-i", str(final_mp4), "-f", "null", "-"])
