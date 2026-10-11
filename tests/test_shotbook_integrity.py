"""Regression tests for Shotbook integrity, provenance and concurrency boundaries.

适配 main 的实现形态（PR#21-31 演进版）：
- _read_plan(path) 单参：损坏→.bak 恢复（恢复写回主文件），双损坏→CORRUPT_PLAN
- 对齐指纹在 pipeline._persist_narration_timing（SHA-256 + audio_file 锚点）
- 并发序列化走共享 project_lock（editor/shotbook 同锁）
保留 #20 的测试意图：损坏不静默、指纹跟字节、锁真互斥、URL 不当证据。
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import pytest

from video_director import shotbook
from video_director.pipeline import _persist_narration_timing


def _valid_plan(project_id: str, version: int = 1) -> dict:
    return {"schema_version": 1, "project_id": project_id, "version": version,
            "updated_at": int(time.time()), "shots": []}


def test_corrupt_primary_recovers_only_from_a_valid_backup(tmp_path):
    path = tmp_path / "shotbook.json"
    path.write_text("{broken", encoding="utf-8")
    backup = path.with_suffix(".json.bak")
    backup.write_text(json.dumps(_valid_plan("test-project", 3)), encoding="utf-8")

    plan = shotbook._read_plan(path)

    assert plan is not None and plan["version"] == 3
    # main 语义：恢复会把备份写回主文件（下次读取走主文件）
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 3


def test_corrupt_primary_without_backup_is_not_silently_treated_as_empty(tmp_path):
    path = tmp_path / "shotbook.json"
    path.write_text('{"project_id":"another-project","version":1,"shots":[]}', encoding="utf-8")
    # 主文件结构有效但属于其他项目 → main 的 _read_plan 校验 shots 结构而非归属；
    # 真实归属校验在 get_project_shotbook 层（CORRUPT_PLAN 只在双损坏时抛）。
    # 此处钉住同意图：损坏且无备份必须显式抛错，绝不静默当作空白。
    path.write_text("{broken-too", encoding="utf-8")

    with pytest.raises(shotbook.ShotbookError) as error:
        shotbook._read_plan(path)

    assert error.value.code == "CORRUPT_PLAN"


def test_alignment_fingerprint_changes_when_audio_bytes_change(tmp_path):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    audio = audio_dir / "voice.wav"
    audio.write_bytes(b"voice-a")
    plans = tmp_path / "plans"
    timing = {"source": "tts", "duration": 1.0,
              "word_timeline": [{"text": "hello", "start": 0.0, "end": 1.0}]}

    _persist_narration_timing({"plans": plans, "audio": audio_dir}, "tts",
                              timing["word_timeline"], 1.0,
                              audio_path=audio)
    first = json.loads((plans / "narration_timing.json").read_text(encoding="utf-8"))
    audio.write_bytes(b"voice-b")
    _persist_narration_timing({"plans": plans, "audio": audio_dir}, "tts",
                              timing["word_timeline"], 1.0,
                              audio_path=audio)
    second = json.loads((plans / "narration_timing.json").read_text(encoding="utf-8"))

    assert first["audio_fingerprint"]["sha256"] != second["audio_fingerprint"]["sha256"]


def test_url_or_unresolved_reference_does_not_pass_evidence_gate():
    shot = {
        "shot_type": "screenshot", "evidence_ref": "https://example.invalid/evidence.png",
        "reviewed": True,
        "semantic_role": "evidence", "visual_intent": "prove",
        "shot_recipe_id": "", "has_visual_layer": True,
    }

    gates = {row["id"]: row for row in shotbook._gates([shot], [], "native_character")}

    assert gates["evidence"]["status"] == "REVIEW"


def test_project_lock_serializes_mutations_across_threads(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    from video_director.project_lock import project_lock
    active = 0
    maximum = 0
    guard = Lock()

    def mutate():
        nonlocal active, maximum
        with project_lock("lock-test"):
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.015)
            with guard:
                active -= 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: mutate(), range(16)))

    assert maximum == 1
