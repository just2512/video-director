from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from video_director import config, workflow_run
from webui import server as web_server


def _post(base_url: str, payload: dict) -> tuple[int, dict]:
    request = Request(
        base_url + "/api/workflows/cancel",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except HTTPError as response:
        body = response.read().decode("utf-8")
        return response.code, json.loads(body) if body else {}


def _get(base_url: str, path: str) -> tuple[int, dict]:
    with urlopen(base_url + path, timeout=5) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}


def test_workflow_cancel_api_enforces_real_worker_capabilities(tmp_path, monkeypatch):
    root = tmp_path / "composer"
    root.mkdir()
    monkeypatch.setattr(config, "composer_storage_root", lambda: root)
    monkeypatch.setattr(web_server, "COMPOSER_ROOT", root)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_port}"
    run_ids: list[str] = []
    try:
        # Render workers do not currently install/check the compose cancellation
        # hook. The API must not claim success or leave a phantom cancel flag.
        render_id = workflow_run.start_run(
            "film_render_final", project_id="cancel-api-render", steps=["render", "verify"]
        )
        run_ids.append(render_id)
        render_detail = workflow_run.get_run(render_id)
        render_list = workflow_run.list_runs(project_id="cancel-api-render")
        assert render_detail["cancellable"] is False
        assert render_list[0]["cancellable"] is False
        status, rejected = _post(base_url, {"run_id": render_id})
        assert status == 409
        assert rejected["ok"] is False
        assert rejected["status"] == workflow_run.RUNNING
        assert "暂不支持中断" in rejected["note"]
        assert not workflow_run._cancel_requested(render_id)
        assert workflow_run.finish_run(render_id, workflow_run.COMPLETED)
        
        # Compose workers support cooperative cancellation through the same
        # WorkflowRun flag consumed by the LLM retry hook and node boundaries.
        compose_id = workflow_run.start_run(
            "compose", project_id="cancel-api-compose", steps=["script"]
        )
        run_ids.append(compose_id)
        compose_detail = workflow_run.get_run(compose_id)
        compose_list = workflow_run.list_runs(project_id="cancel-api-compose")
        assert compose_detail["cancellable"] is True
        assert compose_list[0]["cancellable"] is True
        status, accepted = _post(base_url, {"run_id": compose_id})
        assert status == 200
        assert accepted["ok"] is True
        assert "已请求停止" in accepted["note"]
        assert workflow_run._cancel_requested(compose_id)
        assert workflow_run.finish_run(
            compose_id, workflow_run.COMPLETED, result={"late_success": True}
        )
        assert workflow_run.get_run(compose_id)["status"] == workflow_run.CANCELLED
        assert workflow_run.get_run(compose_id)["result"] == {}

        # A repeated request after terminal closeout is an honest no-op.
        status, terminal = _post(base_url, {"run_id": render_id})
        assert status == 200
        assert terminal["ok"] is True
        assert terminal["status"] == workflow_run.COMPLETED
        assert not workflow_run._cancel_requested(render_id)

        # The task drawer asks for 50 entries. GET /api/workflows must forward
        # the bounded query parameter instead of silently using list_runs' 20 default.
        calls = {}
        original_list_runs = workflow_run.list_runs
        def capture_list_runs(project_id=None, limit=20):
            calls.update(project_id=project_id, limit=limit)
            return [{"run_id": "listed-run", "status": workflow_run.COMPLETED}]
        monkeypatch.setattr(workflow_run, "list_runs", capture_list_runs)
        list_status, listing = _get(
            base_url, "/api/workflows?project_id=limit-check&limit=50"
        )
        assert list_status == 200
        assert listing["runs"][0]["run_id"] == "listed-run"
        assert calls == {"project_id": "limit-check", "limit": 50}
        monkeypatch.setattr(workflow_run, "list_runs", original_list_runs)
    finally:
        for run_id in run_ids:
            run = workflow_run.get_run(run_id)
            if run and run["status"] == workflow_run.RUNNING:
                workflow_run.finish_run(run_id, workflow_run.CANCELLED, error="测试清理")
            workflow_run.delete_run(run_id, allow_waiting=True)
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
