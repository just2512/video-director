"""Adversarial tests for cross-process editor write serialization."""
from __future__ import annotations

import json
import multiprocessing
import os

from tests.fixtures.fixture_data import RECIPE_PROJECT


def _run_competing_edit(storage_root: str, barrier, duration: float, queue) -> None:
    # Spawned Windows and Linux workers receive their own environment/module state.
    os.environ["VIDEO_DIRECTOR_STORAGE_ROOT"] = storage_root
    from video_director import editor

    barrier.wait(timeout=20)
    result = editor.execute_command("fixture-concurrency", {
        "command": "set_duration",
        "targetId": "S01",
        "expectedVersion": 1,
        "payload": {"duration": duration},
    })
    queue.put((result.status, [item.get("code") for item in result.errors]))


def test_same_base_version_from_two_processes_cannot_overwrite_timeline(tmp_path, monkeypatch):
    """One writer commits; the other sees CONFLICT instead of last-write-wins."""
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    project_id = "fixture-concurrency"
    plans = tmp_path / project_id / "plans"
    plans.mkdir(parents=True)
    (plans / "composition.json").write_text(
        json.dumps(RECIPE_PROJECT["composition"], ensure_ascii=False), encoding="utf-8"
    )
    (plans / "revisions.jsonl").write_text("", encoding="utf-8")

    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    processes = [
        ctx.Process(target=_run_competing_edit, args=(str(tmp_path), barrier, duration, queue))
        for duration in (2.5, 3.0)
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=30)
        assert all(not process.is_alive() for process in processes), "editor command workers deadlocked"
        assert [process.exitcode for process in processes] == [0, 0]
        results = [queue.get(timeout=5) for _ in processes]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        queue.close()

    applied = [status for status, _errors in results].count("APPLIED")
    rejected = [(status, errors) for status, errors in results if status == "REJECTED"]
    assert applied == 1, f"same expectedVersion must commit exactly one edit, got {results}"
    assert len(rejected) == 1 and "CONFLICT" in rejected[0][1], results

    revisions = [line for line in (plans / "revisions.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(revisions) == 1, "conflicting writer must not append a revision"
    persisted = json.loads((plans / "composition.json").read_text(encoding="utf-8"))
    assert persisted["scenes"][0]["duration"] in (2.5, 3.0)
