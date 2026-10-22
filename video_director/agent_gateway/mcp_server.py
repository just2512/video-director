"""AG-001 MCP Gateway：stdio JSON-RPC 2.0（MCP 协议）server。

协议：MCP initialize / tools/list / tools/call（2025-06-18 spec 的
stdio 传输即「每行一个 JSON-RPC 消息」）。零第三方依赖（Python stdlib
实现协议层），所有业务动作来自 capability_gateway（同一内部能力层）。

用法（Claude/Codex 客户端配置）：
    {"mcpServers": {"creative-os": {
        "command": "python",
        "args": ["-m", "video_director.agent_gateway.mcp_server"]}}}

工具目录：creative.inspect/plan/execute/action/actions.list/preview/render/verify/watch、director.create/analyze_reference/status/retry/continue、system.capabilities.list
内部仍使用 granular capabilities（watermark.add/motion.apply/mask.apply/
camera.apply/pip.configure/visual.morph/...）——§19「内部仍然使用」。
"""

from __future__ import annotations

import json
import sys

from video_director.agent_gateway import GatewayError, dispatch

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "creative-os-mcp", "version": "1.0.0"}


_OBJECT = {"type": "object", "additionalProperties": True}
_DIRECTOR_INPUTS_SCHEMA = {
    "type": "object",
    "properties": {
        "brief": {"type": "string"},
        "script": {
            "oneOf": [
                {"type": "string"},
                {"type": "object", "properties": {
                    "text": {"type": "string"},
                    "source": {"type": "string"},
                    "authority": {"type": "string"},
                    "editable": {"type": "boolean"}
                }, "additionalProperties": True}
            ]
        },
        "assets": {"type": "object", "properties": {
            "asset_refs": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "policy": {"type": "string", "enum": ["user_only", "user_first", "allow_external"]},
            "required": {"type": "boolean"}
        }, "additionalProperties": True},
        "references": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "articles": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "software_project": {"type": "object", "additionalProperties": True},
        "constraints": {"type": "object", "additionalProperties": True},
        "platform": {"type": "string"},
        "duration": {"type": "string"},
        "style": {"type": "string"}
    },
    "additionalProperties": True
}
_TOOL_SCHEMAS = {
    "creative.inspect": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "项目 ID；省略时返回系统摘要"},
            "selection": {"type": "object", "description": "可选的当前选择对象", "additionalProperties": True}
        },
        "additionalProperties": False
    },
    "creative.plan": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "项目 ID"},
            "instruction": {"type": "string", "description": "自然语言创作或修改意图"}
        },
        "required": ["project_id", "instruction"], "additionalProperties": False
    },
    "creative.execute": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "项目 ID"},
            "tool": {"type": "string", "description": "能力 ID，例如 watermark.add"},
            "payload": {"type": "object", "description": "能力参数对象", "additionalProperties": True},
            "target_id": {"type": "string", "description": "可选目标场景或层 ID"}
        },
        "required": ["project_id", "tool"], "additionalProperties": False
    },
    "creative.preview": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "项目 ID"},
            "scale": {"type": "number", "description": "预览缩放比例，默认 0.5", "minimum": 0.05, "maximum": 1}
        },
        "required": ["project_id"], "additionalProperties": False
    },
    "creative.render": {
        "type": "object", "properties": {"project_id": {"type": "string"}},
        "required": ["project_id"], "additionalProperties": False
    },
    "creative.verify": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "aspect": {"type": "string", "description": "校验范围，默认 all"}
        },
        "required": ["project_id"], "additionalProperties": False
    },
    "creative.watch": {
        "type": "object",
        "properties": {"project_id": {"type": "string", "description": "可选项目 ID；省略返回全局快照"}},
        "additionalProperties": False
    },
    "creative.actions.list": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "项目 ID"},
            "selection": {"type": "object", "description": "可选上下文；提供后返回该场景/图层的可用动作、前置条件和后续动作", "additionalProperties": True}
        },
        "required": ["project_id"],
        "additionalProperties": False
    },
    "creative.action": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "项目 ID"},
            "action": {"type": "string", "description": "Action Registry 的 action ID，例如 creative.select_candidate、asset.search、generate.image"},
            "target": {"type": "string", "description": "目标范围，默认 current_selection"},
            "params": {"type": "object", "description": "动作参数对象", "additionalProperties": True},
            "selection": {"type": "object", "description": "可选目标上下文，例如 scene/layer 选择", "additionalProperties": True}
        },
        "required": ["project_id", "action"],
        "additionalProperties": False
    },
    "director.create": {
        "type": "object",
        "properties": {
            "payload": {
                "type": "object",
                "description": "与 WebUI POST /api/director/create 一致的创建请求；完整上下文放入 director_inputs",
                "properties": {
                    "project_id": {"type": "string"},
                    "brief": {"type": "string"},
                    "script": {"type": "string"},
                    "director_inputs": _DIRECTOR_INPUTS_SCHEMA,
                    "platform": {"type": "string"},
                    "duration": {"type": "string"},
                    "style": {"type": "string"},
                    "resolution": {"type": "string", "pattern": "^\\d{2,5}x\\d{2,5}$"},
                    "fps": {"type": "integer", "minimum": 1, "maximum": 120},
                    "confirm_mode": {"type": "string", "enum": ["preview", "direct"]},
                    "voice": {"type": "string"},
                    "voice_mode": {"type": "string"},
                    "voice_rate": {"type": "string"},
                    "visual_source": {"type": "string", "enum": ["auto", "mixed", "online", "local"]},
                    "allow_generation": {"type": "boolean"}
                },
                "anyOf": [
                    {"required": ["brief"]},
                    {"required": ["script"]},
                    {"required": ["director_inputs"]}
                ],
                "additionalProperties": True
            }
        },
        "required": ["payload"], "additionalProperties": False
    },
    "director.analyze_reference": {
        "type": "object",
        "properties": {
            "video_path": {"type": "string", "description": "本地参考视频文件路径；仅在本机 MCP 进程可访问的文件系统中读取"},
            "use_llm": {"type": "boolean", "default": True, "description": "是否尝试使用已配置的 Vision/LLM 解释镜头语言；不可用时由现有分析器降级"}
        },
        "required": ["video_path"],
        "additionalProperties": False
    },
    "director.status": {
        "type": "object", "properties": {"run_id": {"type": "string"}},
        "required": ["run_id"], "additionalProperties": False
    },
    "director.retry": {
        "type": "object", "properties": {"project_id": {"type": "string"}},
        "required": ["project_id"], "additionalProperties": False
    },
    "director.continue": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "target": {"type": "string", "enum": ["preview", "video"], "default": "preview"}
        },
        "required": ["project_id"], "additionalProperties": False
    },
    "system.capabilities.list": {
        "type": "object", "properties": {}, "additionalProperties": False
    }
}


def _tool_schema_for(name: str) -> dict:
    """Return the exact JSON Schema contract advertised to MCP clients."""
    import copy
    return copy.deepcopy(_TOOL_SCHEMAS.get(
        name, {"type": "object", "properties": {}, "additionalProperties": False}
    ))

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
            from video_director import film_mcp_server
            tools.extend(film_mcp_server.tool_definitions())
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}
        if method == "tools/call":
            params = req.get("params") or {}
            name = params.get("name", "")
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                return {"jsonrpc": "2.0", "id": req_id, "error": {
                    "code": -32602, "message": "Tool arguments must be an object"}}
            if name.startswith("film."):
                from video_director import film_mcp_server
                return {"jsonrpc": "2.0", "id": req_id, "result": film_mcp_server.call_tool(name, args)}
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
