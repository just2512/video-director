"""Tests for the shared WebUI/Agent Action Registry execution service."""
import pytest

from video_director.action_service import ActionServiceError, execute_action_request


def test_action_request_requires_project_and_action():
    with pytest.raises(ActionServiceError) as exc:
        execute_action_request({"project_id": "", "action": ""})
    assert exc.value.code == "MISSING_REQUIREMENT"
    assert exc.value.status_code == 400


def test_action_rejects_path_like_project_id():
    with pytest.raises(ActionServiceError) as exc:
        execute_action_request({
            "project_id": "../outside", "action": "asset.search", "params": {}
        })
    assert exc.value.code == "INVALID_PROJECT_ID"


def test_action_params_must_be_object():
    with pytest.raises(ActionServiceError) as exc:
        execute_action_request({
            "project_id": "project-1",
            "action": "asset.search",
            "params": ["not", "object"],
        })
    assert exc.value.code == "INVALID_INPUT"


def test_registered_business_action_reaches_tool_executor(monkeypatch):
    from video_director import action_registry, project_context, tool_executor

    monkeypatch.setattr(project_context, "inspect_project_context", lambda project_id, selection: {
        "selection": selection
    })
    monkeypatch.setattr(action_registry, "action_availability", lambda *args, **kwargs: {
        "available": True, "reason": "", "next_actions": []
    })
    monkeypatch.setattr(action_registry, "resolve_step", lambda *args, **kwargs: {
        "action": "asset.search",
        "capability": "asset.search",
        "command": "search_and_adopt",
        "targetId": "S01",
        "payload": {"query": "街道夜景"},
    })
    captured = {}
    def fake_execute(project_id, request):
        captured["project_id"] = project_id
        captured["request"] = request
        return {
            "ok": True, "status": "APPLIED", "revision_id": "rev-1",
            "composition_version": 2, "result": {"asset_id": "asset-1"},
            "verify": {},
        }
    monkeypatch.setattr(tool_executor, "execute_tool", fake_execute)

    result = execute_action_request({
        "project_id": "project-1", "action": "asset.search",
        "target": "current_selection",
        "selection": {"type": "scene", "id": "S01"},
        "params": {"query": "街道夜景"},
    })
    assert result["ok"] is True
    assert result["revision_id"] == "rev-1"
    assert captured["project_id"] == "project-1"
    assert captured["request"]["tool"] == "asset.search"
    assert captured["request"]["targetId"] == "S01"


def test_ui_only_action_is_rejected_for_headless_agent(monkeypatch):
    from video_director import action_registry, project_context

    monkeypatch.setattr(project_context, "inspect_project_context", lambda project_id, selection: {})
    monkeypatch.setattr(action_registry, "action_availability", lambda *args, **kwargs: {
        "available": True, "reason": "", "next_actions": []
    })
    monkeypatch.setattr(action_registry, "resolve_step", lambda *args, **kwargs: {
        "action": "ui.generate_scene_visual",
        "capability": "generation.image",
        "command": "noop",
        "targetId": "scene-1",
        "payload": {},
    })
    with pytest.raises(ActionServiceError) as exc:
        execute_action_request({
            "project_id": "project-1",
            "action": "ui.generate_scene_visual",
            "target": "current_selection",
            "selection": {"type": "scene", "id": "scene-1"},
            "params": {},
        })
    assert exc.value.code == "UI_ACTION_REQUIRES_FRONTEND"
    assert exc.value.status_code == 409
