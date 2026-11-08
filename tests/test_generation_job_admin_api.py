"""HTTP integration tests for operator reconciliation of ambiguous video jobs."""
from __future__ import annotations

import http.client
import importlib
import json
import threading
from http.server import ThreadingHTTPServer

import pytest


def _server(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    from video_director import config, generation as gen
    config.init_project_dirs(config.project_dir("generation-admin-api"))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, config, gen, "generation-admin-api"


def _request(server, method, path, payload=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    data = json.loads(response.read().decode("utf-8"))
    connection.close()
    return response.status, data


def test_generation_job_list_api_redacts_local_paths_and_exposes_reconciliation_state(
    tmp_path, monkeypatch
):
    server, thread, config, gen, pid = _server(tmp_path, monkeypatch)
    try:
        gen._update_job(pid, "job-ambiguous", {
            "gen_type": "video", "variant": "A", "provider_id": "provider-x",
            "provider_task_id": "", "input_fingerprint": "f" * 64,
            "status": "submission_unknown", "output_file": "/private/storage/video.mp4",
            "provider_response": {"secret": "must-not-leak"},
        })
        status, body = _request(server, "GET", f"/api/editor/generation/jobs?project_id={pid}")
        assert status == 200
        assert body["ok"] is True
        assert body["jobs"] == [{
            "job_id": "job-ambiguous", "status": "submission_unknown",
            "gen_type": "video", "variant": "A", "provider_id": "provider-x",
            "provider_task_id": "", "input_fingerprint": "f" * 64,
            "updated_at": body["jobs"][0]["updated_at"],
        }]
        serialized = json.dumps(body, ensure_ascii=False)
        assert "/private/storage/video.mp4" not in serialized
        assert "must-not-leak" not in serialized
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    ("resolution", "expected_status", "expected_decision"),
    [
        ({"provider_task_id": "provider-task-1"}, "running", "provider_task_found"),
        ({"confirm_no_provider_task": True}, "submission_rejected",
         "provider_task_not_found_confirmed"),
    ],
)
def test_generation_job_resolve_api_requires_one_operator_outcome_and_persists_it(
    tmp_path, monkeypatch, resolution, expected_status, expected_decision
):
    server, thread, config, gen, pid = _server(tmp_path, monkeypatch)
    try:
        gen._update_job(pid, "job-ambiguous", {
            "gen_type": "video", "variant": "A", "status": "submission_unknown",
        })
        status, body = _request(server, "POST", "/api/editor/generation/jobs/resolve", {
            "project_id": pid, "job_id": "job-ambiguous", **resolution,
        })
        assert status == 200
        assert body["ok"] is True
        assert body["job"]["status"] == expected_status
        persisted = gen._load_jobs(pid)["job-ambiguous"]
        assert persisted["status"] == expected_status
        assert persisted["submission_resolution"]["decision"] == expected_decision
        assert persisted["submission_resolution"]["source"] == "operator_cli"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_generation_job_resolve_api_rejects_ambiguous_or_conflicting_outcomes(
    tmp_path, monkeypatch
):
    server, thread, config, gen, pid = _server(tmp_path, monkeypatch)
    try:
        gen._update_job(pid, "job-ambiguous", {
            "gen_type": "video", "variant": "A", "status": "submission_unknown",
        })
        status, body = _request(server, "POST", "/api/editor/generation/jobs/resolve", {
            "project_id": pid, "job_id": "job-ambiguous",
        })
        assert status == 400
        assert body["code"] == "INVALID_SUBMISSION_RESOLUTION"

        status, body = _request(server, "POST", "/api/editor/generation/jobs/resolve", {
            "project_id": pid, "job_id": "job-ambiguous",
            "provider_task_id": "provider-task-1", "confirm_no_provider_task": True,
        })
        assert status == 400
        assert body["code"] == "INVALID_SUBMISSION_RESOLUTION"
        assert gen._load_jobs(pid)["job-ambiguous"]["status"] == "submission_unknown"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
