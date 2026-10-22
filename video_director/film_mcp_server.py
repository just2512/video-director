"""MCP tools for the authoritative film.html workflow.
This adapter delegates to the existing revision-checked Film CLI. It does not create
a parallel editor, write film.html directly, or accept shell commands from an agent.
Run with: python -m video_director.film_mcp_server
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "video-director-film", "version": "1.0.0"}
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MAX_OPERATIONS = 200

_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "film.status",
        "description": "Read the authoritative film.html revision, stage, duration, tracks, and stable clip IDs.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string", "minLength": 1}},
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "film.validate",
        "description": "Strictly parse the film document and verify local media references and migration source snapshot integrity. This does not prove frame(t) determinism.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string", "minLength": 1}},
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "film.edit",
        "description": "Apply 1–200 typed operations against an exact base revision. A stale revision is rejected; operations are validated and enter the same undo history as Studio edits.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "minLength": 1},
                "base_revision": {"type": "string", "minLength": 1},
                "operations": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_OPERATIONS,
                    "items": {"type": "object"},
                },
            },
            "required": ["project_id", "base_revision", "operations"],
            "additionalProperties": False,
        },
    },
    {
        "name": "film.diff",
        "description": "Show a unified text diff from a retained film revision to the current document. History is bounded.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "minLength": 1},
                "from_revision": {"type": "string", "minLength": 1},
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "film.look",
        "description": "Render real-browser contact sheets and repeat selected timestamps to check supported frame(t) determinism. Inspect the returned image path and warnings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "minLength": 1},
                "revision": {"type": "string", "minLength": 1},
                "times": {
                    "type": "array",
                    "items": {"type": "number", "minimum": 0},
                    "minItems": 1,
                },
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "film.render",
        "description": "Export the current authoritative film revision with the real browser/FFmpeg pipeline. Returns the exported revision and QC evidence; technical PASS does not replace human visual/audio review.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "minLength": 1},
                "fps": {"type": "integer", "minimum": 1, "maximum": 120},
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
]


def _text_result(text: str, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _validate_project_id_arg(arguments: dict[str, Any]) -> str:
    value = arguments.get("project_id")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("project_id 必须是非空字符串。")
    return value.strip()


def _run_cli(args: list[str], timeout: int) -> dict[str, Any]:
    """Run only the fixed Video Director CLI command family; never use a shell."""
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "video_director", *args],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _text_result(
            f"FILM_COMMAND_TIMEOUT：影片命令超过 {timeout} 秒。检查后台进程和当前项目状态后再重试。",
            True,
        )
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode == 0:
        return _text_result(stdout or "命令已完成，但 CLI 没有返回文本。")
    detail = stdout or stderr or f"CLI exit code {completed.returncode}"
    if stdout and stderr:
        detail += "\n\nstderr:\n" + stderr
    return _text_result(detail, True)


def call_tool(name: str, arguments: Any) -> dict[str, Any]:
    """Execute one declared tool and return the MCP CallToolResult."""
    if not isinstance(arguments, dict):
        return _text_result("MCP 工具参数必须是 JSON 对象。", True)
    known = {tool["name"] for tool in _TOOL_DEFINITIONS}
    if name not in known:
        return _text_result(f"UNKNOWN_TOOL：不支持的影片工具 {name!r}。", True)

    try:
        project_id = _validate_project_id_arg(arguments)
        args = ["film", name.split(".", 1)[1], "--project-id", project_id]
        timeout = 60

        if name == "film.edit":
            base_revision = arguments.get("base_revision")
            operations = arguments.get("operations")
            if not isinstance(base_revision, str) or not base_revision.strip():
                raise ValueError("base_revision 必须是从 film.status 获取的精确 revision。")
            if (not isinstance(operations, list) or not operations
                    or len(operations) > MAX_OPERATIONS
                    or any(not isinstance(operation, dict) for operation in operations)):
                raise ValueError(f"operations 必须是 1–{MAX_OPERATIONS} 个 JSON 对象组成的数组。")
            with tempfile.TemporaryDirectory(prefix="video-director-film-mcp-") as temp_dir:
                ops_path = Path(temp_dir) / "film-ops.json"
                ops_path.write_text(
                    json.dumps(operations, ensure_ascii=False, allow_nan=False),
                    encoding="utf-8",
                )
                return _run_cli(
                    args + ["--base-revision", base_revision.strip(), "--ops-file", str(ops_path)],
                    timeout=60,
                )

        if name == "film.diff":
            from_revision = arguments.get("from_revision")
            if from_revision is not None:
                if not isinstance(from_revision, str) or not from_revision.strip():
                    raise ValueError("from_revision 必须是非空 revision 字符串。")
                args += ["--from-revision", from_revision.strip()]

        elif name == "film.look":
            revision = arguments.get("revision")
            if revision is not None:
                if not isinstance(revision, str) or not revision.strip():
                    raise ValueError("revision 必须是非空 revision 字符串。")
                args += ["--revision", revision.strip()]
            times = arguments.get("times")
            if times is not None:
                if not isinstance(times, list) or not times:
                    raise ValueError("times 必须是非空数字数组。")
                checked: list[str] = []
                for value in times:
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                        raise ValueError("times 中每个时间必须是非负有限数字。")
                    checked.append(format(float(value), ".9g"))
                args += ["--times", ",".join(checked)]
            timeout = 1800

        elif name == "film.render":
            fps = arguments.get("fps")
            if fps is not None:
                if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 120:
                    raise ValueError("fps 必须是 1–120 之间的整数。")
                args += ["--fps", str(fps)]
            timeout = 3600

        return _run_cli(args, timeout=timeout)
    except (ValueError, TypeError, OverflowError) as exc:
        return _text_result(str(exc), True)


def handle_message(message: Any) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Notifications intentionally produce no response."""
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    method = message.get("method")
    request_id = message.get("id")
    is_notification = "id" not in message

    if method == "notifications/initialized" and is_notification:
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": _TOOL_DEFINITIONS}}
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "tools/call 缺少有效的 name。"}}
        result = call_tool(params["name"], params.get("arguments", {}))
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    if is_notification:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    """Serve newline-delimited JSON-RPC on stdio; stdout is reserved for protocol."""
    for line in sys.stdin:
        if not line.strip():
            continue
        message: Any = None
        try:
            message = json.loads(line)
            response = handle_message(message)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        except Exception as exc:
            request_id = message.get("id") if isinstance(message, dict) else None
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": f"Internal error: {type(exc).__name__}: {exc}"},
            }
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
