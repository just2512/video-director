"""Windows-native spawned-process generation submission lock regression."""
from __future__ import annotations

import multiprocessing
import time

import pytest


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _SharedSubmitClient:
    def __init__(self, calls, calls_lock):
        self.calls = calls
        self.calls_lock = calls_lock

    def post(self, _path, json=None):
        with self.calls_lock:
            self.calls.value += 1
        # Hold the first worker inside the durable submit boundary so the
        # second spawned process must contend on the same FileLock.
        time.sleep(0.2)
        return _FakeResponse({"id": "windows-provider-task-single-submit", "status": "queued"})

    def get(self, _path):
        raise AssertionError("zero-timeout reservation test must not poll the provider")


def _spawn_generation_submit_same_fingerprint(project_id, prompt, barrier, result_queue, calls, calls_lock):
    from video_director import generation as gen, model_config

    # Spawn starts a fresh interpreter, so configure the deterministic provider
    # identity in the child rather than relying on a parent monkeypatch.
    model_config.get_role_config = lambda role: (
        {"api_key": "test-key", "base_url": "https://provider.invalid/v1", "model": "fake-model"}
        if role == "video" else {"api_key": "", "base_url": "", "model": ""}
    )
    original_submit = gen._create_poll_and_persist_video

    def submit_without_polling(client, *args, **kwargs):
        kwargs["timeout"] = 0
        return original_submit(client, *args, **kwargs)

    gen._create_poll_and_persist_video = submit_without_polling
    gen._video_client = lambda _cfg: _SharedSubmitClient(calls, calls_lock)
    try:
        barrier.wait(timeout=20)
        gen.generate(
            project_id,
            prompt,
            gen_type="video",
            width=320,
            height=180,
            variant="A",
            model="fake-model",
        )
        result_queue.put("unexpected-success")
    except gen.GenerationError as exc:
        result_queue.put(exc.code)
    except Exception as exc:
        result_queue.put(f"{type(exc).__name__}: {exc}")


def test_windows_spawned_processes_submit_same_video_fingerprint_only_once(tmp_path, monkeypatch):
    """FileLock must serialize Windows spawn workers across lookup/reserve/POST."""
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))

    from video_director import config, generation as gen

    project_id = "windows-generation-submit-lock"
    prompt = "one provider task for one identical video request"
    config.init_project_dirs(config.project_dir(project_id))

    context = multiprocessing.get_context("spawn")
    calls = context.Value("i", 0)
    calls_lock = context.Lock()
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    workers = [
        context.Process(
            target=_spawn_generation_submit_same_fingerprint,
            args=(project_id, prompt, barrier, result_queue, calls, calls_lock),
        )
        for _ in range(2)
    ]

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    try:
        assert all(not worker.is_alive() for worker in workers)
        assert [worker.exitcode for worker in workers] == [0, 0]
        results = [result_queue.get(timeout=5) for _ in workers]
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        result_queue.close()

    assert results == ["VIDEO_GENERATION_TIMEOUT", "VIDEO_GENERATION_TIMEOUT"], results
    assert calls.value == 1, f"same fingerprint created {calls.value} provider tasks"

    jobs = gen._load_jobs(project_id)
    assert len(jobs) == 1, jobs
    job = next(iter(jobs.values()))
    assert job["status"] == "running", job
    assert job["provider_task_id"] == "windows-provider-task-single-submit", job
    assert job["input_fingerprint"], job
    assert job["provider_identity"]["fingerprint"], job
