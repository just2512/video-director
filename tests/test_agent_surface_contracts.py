"""Guardrails for user-facing and Agent entry points."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_webui_creation_uses_shared_director_service():
    source = (ROOT / "webui" / "server.py").read_text(encoding="utf-8")
    route = source[source.index('if parsed.path == "/api/director/create":'):
                    source.index('if parsed.path == "/api/director/continue":')]
    assert "create_director_run(payload)" in route
    assert "director_inputs.build_bundle" not in route


def test_webui_action_route_uses_shared_action_service():
    source = (ROOT / "webui" / "server.py").read_text(encoding="utf-8")
    start = source.index('if parsed.path == "/api/action/execute":')
    end = source.index('if parsed.path == "/api/director/execute":', start)
    route = source[start:end]
    assert "execute_action_request(payload)" in route
    assert "action_registry as vd_actions" not in route


def test_selected_reference_video_is_analyzed_before_submission():
    source = (ROOT / "webui" / "static" / "js" / "create.js").read_text(encoding="utf-8")
    assert "window._cfReferenceFileObject !== referenceFile" in source
    assert "window._cfReferenceFileObject = file" in source
    assert "else if (!selectedReference && window._cfReferenceFileObject)" in source
    assert "const analyzed = await cfAnalyzeReference();" in source
    assert "创作任务未提交" in source


def test_webui_director_continue_uses_shared_service():
    source = (ROOT / "webui" / "server.py").read_text(encoding="utf-8")
    start = source.index('if parsed.path == "/api/director/continue":')
    end = source.index('if parsed.path == "/api/director/retry":', start)
    route = source[start:end]
    assert "continue_director_project(" in route
    assert "run_director_continue_async(" not in route


def test_failed_director_tasks_expose_retry_from_task_drawer():
    source = (ROOT / "webui" / "static" / "js" / "workflow.js").read_text(encoding="utf-8")
    assert "async function taskRetryDirector(projectId)" in source
    assert "apiPost('/api/director/retry'" in source
    assert "taskRetryDirector(" in source
    assert "重新执行</button>" in source
