"""AG-001 MCP Gateway：stdio JSON-RPC 2.0（MCP 协议）server。

协议：MCP initialize / tools/list / tools/call（2025-06-18 spec 的
stdio 传输即「每行一个 JSON-RPC 消息」）。零第三方依赖（Python stdlib
实现协议层），所有工具实现来自 capability_gateway（§19 同一内部能力层）。

用法（Claude/Codex 客户端配置）：
    {"mcpServers": {"creative-os": {
        "command": "python",
        "args": ["-m", "video_director.agent_gateway.mcp_server"]}}}

高层工具（§51 指纹）：creative.inspect/plan/execute/preview/render/verify/watch
内部仍使用 granular capabilities（watermark.add/motion.apply/mask.apply/
camera.apply/pip.configure/visual.morph/...）——§19「内部仍然使用」。
"""

from __future__ import annotations

import json
import sys

from video_director.agent_gateway import GatewayError, dispatch
from video_director.capability_registry import list_capabilities

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "creative-os-mcp", "version": "1.0.0"}


def _tool_schema_for(name: str) -> dict:
    """高层工具的输入 schema（MCP tools/list 用；参数宽松——说明性 schema）。"""
    schemas = {
        "creative.inspect": {"project_id": "string?（空=系统摘要）", "selection": "object?"},
        "creative.plan": {"project_id": "string", "instruction": "string（自然语言创作/修改意图）"},
        "creative.execute": {"project_id": "string", "tool": "string（capability id，如 watermark.add）",
                             "payload": "object?", "target_id": "string?"},
        "creative.preview": {"project_id": "string", "scale": "number?（默认 0.5）"},
        "creative.render": {"project_id": "string"},
        "creative.verify": {"project_id": "string", "aspect": "string?（默认 all）"},
        "creative.watch": {"project_id": "string?（空=全局）"},
    }
    props = schemas.get(name, {})
    return {"type": "object",
            "properties": {k: {"type": "string", "description": v}
                           for k, v in props.items()},
            "required": [k for k, v in props.items() if "?" not in v] or []}


def _handle(req: dict) -> dict:
    method = req.get("method", "")
    req_id = req.get("id")
    try:
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO}}
        if method == "notifications/initialized" or method.startswith("notifications/"):
            return {}  # 通知无响应
        if method == "tools/list":
            tools = []
            for name, fn in dispatch.__globals__["HIGH_LEVEL_TOOLS"].items():
                tools.append({"name": name, "description": (fn.__doc__ or "").strip().split("\n")[0],
                              "inputSchema": _tool_schema_for(name)})
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}
        if method == "tools/call":
            params = req.get("params") or {}
            name = params.get("name", "")
            args = params.get("arguments") or {}
            if name == "system.capabilities.list":
                # 附加低层能力发现（agent.mcp capability 的 tool_discovery 语义）
                caps = list_capabilities()
                return {"jsonrpc": "2.0", "id": req_id, "result": {
                    "content": [{"type": "text",
                                 "text": json.dumps({"capabilities": list(caps.keys())},
                                                    ensure_ascii=False)}]}}
            result = dispatch(name, **args)
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": bool(result.get("ok") is False)}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        if req_id is not None:
            return {"jsonrpc": "2.0", "id": req_id, "error": {
                "code": -32601, "message": f"Method not found: {method}"}}
        return {}
    except GatewayError as exc:
        return {"jsonrpc": "2.0", "id": req_id, "error": {
            "code": -32000, "message": str(exc)}}
    except Exception as exc:  # noqa: BLE001 协议层兜底：单请求失败不杀 server
        return {"jsonrpc": "2.0", "id": req_id, "error": {
            "code": -32603, "message": f"Internal error: {exc}"}}


def main() -> None:
    """stdio 主循环：每行一个 JSON-RPC 消息（MCP stdio 传输）。"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {
                "code": -32700, "message": "Parse error"}}
        else:
            resp = _handle(req)
        if resp:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
