"""Shared action execution service for WebUI and external Agents.

All actions resolve through Action Registry -> Capability -> Tool Executor.
Browser-only UI actions are deliberately not executed in a headless Agent.
"""
from __future__ import annotations

from typing import Any


class ActionServiceError(ValueError):
    """Action rejection with the same body/status contract used by the WebUI."""

    def __init__(self, body: dict, status_code: int):
        self.body = body
        self.status_code = status_code
        self.code = body.get("code", "ACTION_ERROR")
        super().__init__(str(body.get("error") or body.get("reason") or self.code))


def execute_action_request(payload: dict) -> dict:
    """Execute one registered user-level action against the selected project."""
    if not isinstance(payload, dict):
        raise ActionServiceError({
            "ok": False, "status": "REJECTED", "code": "INVALID_INPUT",
            "error": "动作请求必须是 JSON 对象",
        }, 400)

    pid = payload.get("project_id") or ""
    action_id = payload.get("action") or ""
    target = payload.get("target") or "current_selection"
    params = payload.get("params") or {}
    selection = payload.get("selection") or {"type": "project"}
    if not isinstance(pid, str) or not isinstance(action_id, str):
        raise ActionServiceError({
            "ok": False, "status": "REJECTED", "code": "INVALID_INPUT",
            "error": "project_id 和 action 必须是字符串",
        }, 400)
    pid, action_id = pid.strip(), action_id.strip()
    import re
    if pid and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", pid):
        raise ActionServiceError({
            "ok": False, "status": "REJECTED", "code": "INVALID_PROJECT_ID",
            "error": "project_id 只能包含字母、数字、点、下划线和连字符，且必须以字母或数字开头",
        }, 400)
    if not pid or not action_id:
        raise ActionServiceError({
            "ok": False, "status": "REJECTED", "code": "MISSING_REQUIREMENT",
            "error": "缺少 project_id 或 action",
        }, 400)
    if not isinstance(params, dict) or not isinstance(selection, dict):
        raise ActionServiceError({
            "ok": False, "status": "REJECTED", "code": "INVALID_INPUT",
            "error": "params 和 selection 必须是对象",
        }, 400)
    if not isinstance(target, str):
        raise ActionServiceError({
            "ok": False, "status": "REJECTED", "code": "INVALID_INPUT",
            "error": "target 必须是字符串",
        }, 400)

    try:
        from video_director import action_registry as actions
        from video_director import project_context
        from video_director import tool_executor

        rejection = tool_executor.film_authority_rejection(pid, "creative.action")
        if rejection is not None:
            raise ActionServiceError(rejection, 409)

        context = project_context.inspect_project_context(pid, selection)
        availability = actions.action_availability(action_id, pid, context=context)
        if not availability.get("available"):
            raise ActionServiceError({
                "ok": False, "status": "REJECTED", "code": "ACTION_UNAVAILABLE",
                "action": action_id,
                "reason": availability.get("reason") or "当前对象不可执行该操作",
                "next_actions": availability.get("next_actions") or [],
            }, 409)

        resolved = actions.resolve_step(
            {"action": action_id, "target": target, "params": params},
            selection=selection,
        )
        if resolved.get("error"):
            raise ActionServiceError({
                "ok": False, "status": "REJECTED", "code": "ACTION_RESOLVE_FAILED",
                "action": action_id, "error": resolved["error"],
            }, 400)

        spec = actions.get_action(resolved["action"])
        if spec and spec.ui:
            raise ActionServiceError({
                "ok": False, "status": "REJECTED", "code": "UI_ACTION_REQUIRES_FRONTEND",
                "action": resolved["action"],
                "error": "这是需要浏览器交互的界面动作；Agent 应调用对应的业务动作或生成能力",
                "ui_action": spec.ui,
            }, 409)

        selection_type = selection.get("type") or "project"
        target_type = (
            "project" if target == "project" or not resolved.get("targetId")
            else selection_type
        )
        if spec and spec.targets and target_type not in spec.targets:
            raise ActionServiceError({
                "ok": False, "status": "REJECTED", "code": "TARGET_NOT_ALLOWED",
                "action": resolved["action"],
                "error": f"动作 {resolved['action']} 不允许目标 {target_type}",
            }, 409)

        if resolved["action"] == "render.final":
            from video_director import preflight
            publish_check = preflight.run_preflight(pid, for_publish=True)
            if publish_check.status != "PASS":
                raise ActionServiceError({
                    "ok": False, "status": "REJECTED", "code": "PREFLIGHT_REJECT",
                    "action": resolved["action"], "reason": "商用预检未通过",
                    "preflight": publish_check.to_dict(),
                }, 409)

        result = tool_executor.execute_tool(pid, {
            "tool": resolved["capability"],
            "targetId": resolved.get("targetId") or "",
            "payload": resolved.get("payload") or {},
        })
        ok = bool(result.get("ok"))
        return {
            "ok": ok,
            "status": result.get("status") or ("APPLIED" if ok else "REJECTED"),
            "action": resolved["action"],
            "capability": resolved["capability"],
            "command": resolved["command"],
            "revision_id": result.get("revision_id"),
            "composition_version": result.get("composition_version"),
            "result": result.get("result") or {},
            "verify": result.get("verify") or {},
            "next_actions": [] if ok else (availability.get("next_actions") or []),
            "error": result.get("error"),
            "message": spec.label if ok and spec else "",
        }
    except ActionServiceError:
        raise
    except Exception as exc:
        raise ActionServiceError({
            "ok": False, "status": "ERROR", "code": "ACTION_EXECUTE_FAILED",
            "action": action_id, "error": str(exc),
        }, 500) from exc
