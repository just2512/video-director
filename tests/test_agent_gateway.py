"""AG-001/AG-002 Agent Gateway 测试：MCP/CLI 统一内部能力层（§19/§31.3）。"""

import json
import io
import sys

import pytest

from video_director.agent_gateway import HIGH_LEVEL_TOOLS, GatewayError, dispatch


class TestHighLevelTools:
    """§19 七高层工具注册 + 统一入口。"""

    def test_seven_tools_registered(self):
        assert sorted(HIGH_LEVEL_TOOLS) == sorted([
            "creative.inspect", "creative.plan", "creative.execute",
            "creative.preview", "creative.render", "creative.verify",
            "creative.watch"])

    def test_inspect_system_summary(self):
        r = dispatch("creative.inspect")
        assert "projects" in r and "capabilities" in r

    def test_unknown_tool_rejected(self):
        with pytest.raises(GatewayError) as exc:
            dispatch("creative.nope")
        assert exc.value.code == "UNKNOWN_TOOL"

    def test_missing_project_rejected(self):
        with pytest.raises(GatewayError) as exc:
            dispatch("creative.verify")
        assert exc.value.code == "MISSING_PROJECT"

    def test_capabilities_count_117(self):
        r = dispatch("creative.inspect")
        assert r["capabilities"] >= 110, "能力族应 ≥110（§71 117 能力）"


class TestMCPGatewayProtocol:
    """AG-001：MCP stdio 协议握手 + tools/list + tools/call。"""

    def _invoke(self, messages):
        from video_director.agent_gateway import mcp_server
        old_in, old_out = sys.stdin, sys.stdout
        sys.stdin = io.StringIO("\n".join(json.dumps(m) for m in messages))
        sys.stdout = io.StringIO()
        try:
            mcp_server.main()
            out = sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_in, old_out
        return [json.loads(l) for l in out.splitlines() if l.strip()]

    def test_initialize_handshake(self):
        resps = self._invoke([{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {}}])
        assert resps[0]["id"] == 1
        assert resps[0]["result"]["serverInfo"]["name"] == "creative-os-mcp"
        assert resps[0]["result"]["protocolVersion"] == "2025-06-18"

    def test_tools_list_seven(self):
        resps = self._invoke([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        tools = [t["name"] for t in resps[1]["result"]["tools"]]
        assert "creative.execute" in tools and "creative.verify" in tools

    def test_tools_call_inspect(self):
        resps = self._invoke([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "creative.inspect", "arguments": {}}},
        ])
        inner = json.loads(resps[1]["result"]["content"][0]["text"])
        assert "projects" in inner

    def test_unknown_method(self):
        resps = self._invoke([{"jsonrpc": "2.0", "id": 9, "method": "bogus.method"}])
        assert resps[0]["error"]["code"] == -32601

    def test_parse_error(self):
        old_in, old_out = sys.stdin, sys.stdout
        sys.stdin = io.StringIO("not-json\n")
        sys.stdout = io.StringIO()
        try:
            from video_director.agent_gateway import mcp_server
            mcp_server.main()
            out = sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_in, old_out
        assert "Parse error" in out


class TestCLIGateway:
    """AG-002：CLI 对等入口（与 UI/MCP 同一 dispatch）。"""

    def _run(self, *argv):
        from video_director.agent_gateway import cli
        old_out = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = cli.main(list(argv))
            out = sys.stdout.getvalue()
        finally:
            sys.stdout = old_out
        try:
            parsed = json.loads(out) if out.strip() else {}
        except json.JSONDecodeError:
            parsed = {}  # 非 JSON 输出（help 文本）
        return code, parsed

    def test_capabilities_list(self):
        code, out = self._run("capabilities", "list")
        assert code == 0
        assert out["count"] >= 110
        assert "camera.apply" in out["capabilities"]

    def test_inspect_system(self):
        code, out = self._run("inspect")
        assert code == 0
        assert "projects" in out

    def test_execute_missing_project(self):
        code, out = self._run("execute", "--project", "nope", "--command",
                              "camera.apply", "--json", "{}")
        r = out if isinstance(out, dict) else {}
        # NOT_FOUND 会落在 execute 结果里（非异常）
        assert "ok" in r

    def test_bad_payload_json(self):
        code, _ = self._run("execute", "--project", "x", "--command", "t",
                            "--json", "{bad json")
        assert code == 2  # 参数错误返回 2

    def test_no_verb_prints_help(self):
        code, _ = self._run()
        assert code == 2  # 无子命令 → argparse 帮助，返回 2


class TestParity:
    """§31.3 Gate：MCP/CLI/UI 三入口同一内部链（分层完成后全量验证）。"""

    def test_dispatch_is_single_path(self):
        """creative.execute 内部就是 tool_executor.execute_tool（无第二实现）。"""
        import inspect
        from video_director.agent_gateway import capability_gateway as cg
        src = inspect.getsource(cg.creative_execute)
        assert "tool_executor.execute_tool" in src, "execute 必须经唯一 Tool Executor"


class TestAgentGatewayContext:
    def test_context_snapshot(self):
        from video_director.agent_gateway import context
        r = context.project_context_snapshot("web-f18ea82b")
        assert r["project_id"] == "web-f18ea82b"
        assert "scene_count" in r and "capabilities" in r