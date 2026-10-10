"""Regression tests for Shotbook integrity, provenance and concurrency boundaries."""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

import pytest

from video_director import shotbook


def _valid_plan(project_id: str, version: int = 1) -> dict:
    return {"schema_version": 1, "project_id": project_id, "version": version,
            "updated_at": int(time.time()), "shots": []}


def test_corrupt_primary_recovers_only_from_a_valid_backup(tmp_path):
    path = tmp_path / "shotbook.json"
    path.write_text("{broken", encoding="utf-8")
    backup = path.with_suffix(".json.bak")
    backup.write_text(json.dumps(_valid_plan("test-project", 3)), encoding="utf-8")

    plan, status = shotbook._read_plan(path, "test-project")

    assert status == "RECOVERED"
    assert plan["version"] == 3
    assert path.read_text(encoding="utf-8") == "{broken"  # recovery is read-only


def test_corrupt_primary_without_backup_is_not_silently_treated_as_empty(tmp_path):
    path = tmp_path / "shotbook.json"
    path.write_text('{"project_id":"another-project","version":1,"shots":[]}', encoding="utf-8")

    with pytest.raises(shotbook.ShotbookError) as error:
        shotbook._read_plan(path, "test-project")

    assert error.value.code == "CORRUPT_SHOTBOOK"


def test_alignment_fingerprint_changes_when_audio_bytes_change(tmp_path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"voice-a")
    timing = {"source": "tts", "duration": 1.0, "word_timeline": [
        {"text": "hello", "start": 0.0, "end": 1.0}], "audio_path": str(audio)}

    first = shotbook._alignment_fingerprint(timing, "test-project")
    audio.write_bytes(b"voice-b")
    os.utime(audio, None)
    second = shotbook._alignment_fingerprint(timing, "test-project")

    assert first != second


def test_source_fingerprint_includes_asset_bytes(tmp_path):
    media = tmp_path / "asset.bin"
    media.write_bytes(b"asset-one")
    ir = SimpleNamespace(project="test-project", scenes=[], audio=[],
                         assets=[SimpleNamespace(id="asset-1", type="image", file=str(media),
                                                 source="uploaded", rights_status="CLEAR",
                                                 provenance={"source_page": "local"})])

    first = shotbook._source_fingerprint(ir)
    media.write_bytes(b"asset-two")
    os.utime(media, None)
    second = shotbook._source_fingerprint(ir)

    assert first != second


def test_url_or_unresolved_reference_does_not_pass_evidence_gate():
    shot = {
        "shot_type": "screenshot", "evidence_ref": "https://example.invalid/evidence.png",
        "evidence_status": "REVIEW", "reviewed": True,
        "semantic_role": "evidence", "visual_intent": "prove",
        "shot_recipe_id": "", "has_visual_layer": True,
    }

    gates = {row["id"]: row for row in shotbook._gates([shot], [], "native_character")}

    assert gates["evidence"]["status"] == "REVIEW"


def test_project_lock_serializes_mutations_across_threads(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    active = 0
    maximum = 0
    guard = Lock()

    def mutate():
        nonlocal active, maximum
        with shotbook._project_lock("lock-test"):
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.015)
            with guard:
                active -= 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: mutate(), range(16)))

    assert maximum == 1
