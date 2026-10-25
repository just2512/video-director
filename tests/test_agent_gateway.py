"""AG-001/AG-002 Agent Gateway 测试：MCP/CLI 统一内部能力层（§19/§31.3）。"""

import json
import io
import sys

import pytest

from video_director.agent_gateway import HIGH_LEVEL_TOOLS, GatewayError, dispatch


class TestHighLevelTools:
    """高层 Agent 工具注册 + 统一入口。"""

    def test_gateway_tools_registered(self):
        assert sorted(HIGH_LEVEL_TOOLS) == sorted([
            "creative.inspect", "creative.plan", "creative.execute",
            "creative.preview", "creative.render", "creative.verify",
            "creative.watch", "creative.action", "creative.actions.list",
            "director.create", "director.analyze_reference", "director.status",
            "director.retry", "director.continue", "system.capabilities.list"])

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

    def test_path_like_project_id_is_rejected_before_dispatch(self):
        with pytest.raises(GatewayError) as exc:
            dispatch("creative.actions.list", project_id="../outside")
        assert exc.value.code == "INVALID_PROJECT_ID"

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

    def test_tools_list_exposes_agent_actions(self):
        resps = self._invoke([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        tools = [t["name"] for t in resps[1]["result"]["tools"]]
        assert "creative.execute" in tools and "creative.verify" in tools
        assert "director.create" in tools and "director.status" in tools
        assert "creative.action" in tools and "creative.actions.list" in tools
        assert "system.capabilities.list" in tools
        action = next(t for t in resps[1]["result"]["tools"] if t["name"] == "creative.action")
        assert action["inputSchema"]["properties"]["params"]["type"] == "object"
        action_list = next(t for t in resps[1]["result"]["tools"] if t["name"] == "creative.actions.list")
        assert action_list["inputSchema"]["required"] == ["project_id"]
        reference_tool = next(t for t in resps[1]["result"]["tools"] if t["name"] == "director.analyze_reference")
        assert reference_tool["inputSchema"]["required"] == ["video_path"]
        create = next(t for t in resps[1]["result"]["tools"] if t["name"] == "director.create")
        assert create["inputSchema"]["properties"]["payload"]["type"] == "object"

    def test_every_registered_tool_has_an_explicit_schema(self):
        from video_director.agent_gateway.mcp_server import _tool_schema_for
        for name in HIGH_LEVEL_TOOLS:
            schema = _tool_schema_for(name)
            assert schema.get("type") == "object", name
            assert isinstance(schema.get("properties"), dict), name

    def test_mcp_schemas_use_native_parameter_types(self):
        from video_director.agent_gateway.mcp_server import _tool_schema_for
        execute = _tool_schema_for("creative.execute")
        preview = _tool_schema_for("creative.preview")
        assert execute["properties"]["payload"]["type"] == "object"
        assert preview["properties"]["scale"]["type"] == "number"
        assert "project_id" in execute["required"]
        assert _tool_schema_for("director.status")["required"] == ["run_id"]
        assert _tool_schema_for("system.capabilities.list")["additionalProperties"] is False

    def test_tools_call_analyze_reference(self, monkeypatch):
        import video_director.director_service as service
        seen = {}
        def fake_analyze(video_path, use_llm=True):
            seen["video_path"] = video_path
            seen["use_llm"] = use_llm
            return {"ok": True, "reference": {"id": "ref-test"}}
        monkeypatch.setattr(service, "analyze_reference_video", fake_analyze)
        resps = self._invoke([{
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "director.analyze_reference", "arguments": {
                "video_path": "/tmp/reference.mp4", "use_llm": False
            }},
        }])
        inner = json.loads(resps[0]["result"]["content"][0]["text"])
        assert inner["ok"] is True
        assert seen == {"video_path": "/tmp/reference.mp4", "use_llm": False}
        assert inner["reference"]["id"] == "ref-test"

    def test_tools_call_director_status_validation(self):
        resps = self._invoke([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "director.status", "arguments": {"run_id": ""}}},
        ])
        inner = json.loads(resps[0]["result"]["content"][0]["text"])
        assert inner["ok"] is False
        assert inner["code"] == "MISSING_RUN_ID"

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

    def test_invalid_request_envelopes_do_not_terminate_stdio_loop(self):
        resps = self._invoke([
            None,
            [],
            {"jsonrpc": "1.0", "id": 3, "method": "ping"},
            {"jsonrpc": "2.0", "id": 4, "method": 17},
            {"jsonrpc": "2.0", "id": True, "method": "ping"},
            {"jsonrpc": "2.0", "id": 5, "method": "ping"},
        ])
        assert len(resps) == 6, resps
        assert [response["id"] for response in resps] == [None, None, 3, 4, None, 5], resps
        assert [response["error"]["code"] for response in resps[:5]] == [-32600] * 5, resps
        assert resps[5]["result"] == {}, resps[5]

    def test_tools_call_rejects_non_object_params_and_arguments(self):
        resps = self._invoke([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "creative.inspect", "arguments": []}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": 7}},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        ])
        assert len(resps) == 4, resps
        assert [response["error"]["code"] for response in resps[:3]] == [-32602] * 3, resps
        assert [response["id"] for response in resps] == [1, 2, 3, 4], resps
        assert resps[3]["result"] == {}, resps[3]

    def test_notifications_never_return_results_or_dispatch_tools(self):
        from video_director.agent_gateway import mcp_server

        tool_calls = []
        original = mcp_server.dispatch

        def guarded_dispatch(name, **args):
            tool_calls.append((name, args))
            return original(name, **args)

        from unittest.mock import patch
        messages = [
            {"jsonrpc": "2.0", "method": "ping"},
            {"jsonrpc": "2.0", "method": "tools/list"},
            {"jsonrpc": "2.0", "method": "tools/call",
             "params": {"name": "creative.inspect", "arguments": {}}},
            {"jsonrpc": "2.0", "method": "tools/call", "params": []},
            {"jsonrpc": "2.0", "id": 9, "method": "ping"},
            {"jsonrpc": "2.0", "id": 10, "method": "notifications/initialized"},
        ]
        with patch.object(mcp_server, "dispatch", side_effect=guarded_dispatch):
            resps = self._invoke(messages)
        assert len(resps) == 2, resps
        assert resps[0]["id"] == 9 and resps[0]["result"] == {}, resps
        assert resps[1]["id"] == 10 and resps[1]["error"]["code"] == -32600, resps
        assert tool_calls == [], tool_calls

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

    def test_actions_cli_lists_current_project_actions(self, monkeypatch):
        import video_director.agent_gateway.cli as cli
        seen = {}
        def fake_dispatch(name, **kwargs):
            seen["name"] = name
            seen.update(kwargs)
            return {"ok": True, "groups": {"scene": []}}
        monkeypatch.setattr(cli, "dispatch", fake_dispatch)
        code, out = self._run("actions", "--project", "project-1",
                              "--selection", '{"type":"scene","id":"S01"}')
        assert code == 0
        assert seen["name"] == "creative.actions.list"
        assert seen["project_id"] == "project-1"
        assert seen["selection"]["id"] == "S01"
        assert out["ok"] is True

    def test_action_cli_rejects_non_object_parameters(self):
        code, out = self._run("action", "--project", "p", "--action", "asset.search",
                              "--json", '["not","object"]')
        assert code == 2
        assert out["code"] == "INVALID_INPUT"

    def test_director_status_reports_missing_run_id(self):
        code, out = self._run("director-status", "--run-id", "")
        assert code == 1
        assert out["ok"] is False
        assert out["code"] == "MISSING_RUN_ID"

    def test_director_reference_cli_calls_shared_service(self, tmp_path, monkeypatch):
        import video_director.agent_gateway.cli as cli
        import video_director.director_service as service
        video = tmp_path / "reference.mp4"
        video.write_bytes(b"video-placeholder")
        seen = {}
        def fake_analyze(path, use_llm=True):
            seen["path"] = path
            seen["use_llm"] = use_llm
            return {"ok": True, "reference": {"id": "ref-test"}}
        monkeypatch.setattr(service, "analyze_reference_video", fake_analyze)
        code, out = self._run("director-reference", "--video", str(video), "--no-llm")
        assert code == 0
        assert seen["path"] == str(video)
        assert seen["use_llm"] is False
        assert out["reference"]["id"] == "ref-test"

    def test_director_create_reads_json_payload_file(self, tmp_path, monkeypatch):
        import video_director.director_service as service
        payload_file = tmp_path / "director-request.json"
        payload_file.write_text('{"brief":"制作视频"}', encoding="utf-8")
        seen = {}
        def fake_create(payload):
            seen["payload"] = payload
            return {"ok": True, "project_id": "p-1", "run_id": "r-1"}
        monkeypatch.setattr(service, "create_director_run", fake_create)
        code, out = self._run("director-create", "--input", str(payload_file))
        assert code == 0
        assert seen["payload"]["brief"] == "制作视频"
        assert out["run_id"] == "r-1"

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