"""Integration tests for the agent-facing authoritative Film MCP server."""
from __future__ import annotations

import io
import json

from video_director import config, film_document as fd
from video_director import film_mcp_server as mcp

FILM = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden}</style></head>
<body><section id="video-main" data-label="主画面">
<video id="footage" src="assets/footage.mp4#t=0,5" at="0"></video>
</section><section id="audio-main" data-label="旁白">
<audio id="narration" src="assets/narration.mp3#t=0,5" at="0"></audio>
</section></body></html>'''


def _payload(result: dict) -> dict:
    assert result["content"], result
    return json.loads(result["content"][0]["text"])


def test_mcp_initialize_and_tool_catalog_are_protocol_valid():
    initialized = mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert initialized["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert initialized["result"]["capabilities"]["tools"] == {}
    assert initialized["result"]["serverInfo"]["name"] == "video-director-film"

    listed = mcp.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert names == {"film.status", "film.initialize", "film.validate", "film.edit", "film.diff", "film.look", "film.render"}
    assert mcp.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_standalone_mcp_supports_modern_discovery_and_versioned_tool_results(monkeypatch):
    modern_meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "contract-test", "version": "1.0.0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    discovery = mcp.handle_message({
        "jsonrpc": "2.0", "id": 1, "method": "server/discover",
        "params": {"_meta": modern_meta},
    })
    result = discovery["result"]
    assert result["resultType"] == "complete"
    assert "2026-07-28" in result["supportedVersions"]
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "video-director-film"
    assert result["capabilities"] == {"tools": {}}
    assert result["ttlMs"] == 0 and result["cacheScope"] == "private"

    listed = mcp.handle_message({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list",
        "params": {"_meta": modern_meta},
    })
    assert listed["result"]["resultType"] == "complete"
    assert listed["result"]["ttlMs"] == 0
    assert listed["result"]["cacheScope"] == "private"
    assert any(tool["name"] == "film.status" for tool in listed["result"]["tools"])

    monkeypatch.setattr(mcp, "call_tool", lambda name, args: {
        "content": [{"type": "text", "text": json.dumps({"name": name, "args": args})}],
        "isError": False,
    })
    called = mcp.handle_message({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"_meta": modern_meta, "name": "film.status", "arguments": {"project_id": "demo"}},
    })
    assert called["result"]["resultType"] == "complete"
    assert called["result"]["isError"] is False

    unsupported = mcp.handle_message({
        "jsonrpc": "2.0", "id": 4, "method": "tools/list",
        "params": {"_meta": {
            "io.modelcontextprotocol/protocolVersion": "2099-01-01",
            "io.modelcontextprotocol/clientCapabilities": {},
        }},
    })
    assert unsupported["error"]["code"] == -32022
    assert unsupported["error"]["data"]["requested"] == "2099-01-01"

    bad_meta = mcp.handle_message({
        "jsonrpc": "2.0", "id": 5, "method": "tools/list",
        "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}},
    })
    assert bad_meta["error"]["code"] == -32602


def test_standalone_mcp_rejects_invalid_request_envelopes_and_tool_params():
    invalid = [
        None,
        [],
        {"jsonrpc": "1.0", "id": 3, "method": "ping"},
        {"jsonrpc": "2.0", "id": 4, "method": []},
        {"jsonrpc": "2.0", "id": True, "method": "ping"},
    ]
    for message in invalid:
        response = mcp.handle_message(message)
        assert response is not None
        assert response["error"]["code"] == -32600, response
    assert mcp.handle_message({"jsonrpc": "2.0", "id": 8, "method": "ping"})["result"] == {}

    schema_invalid = mcp.call_tool("film.render", {
        "project_id": "some-project", "expected_revision": "rev-1", "fps": "fast"
    })
    assert schema_invalid["isError"] is True, schema_invalid
    assert "fps" in schema_invalid["content"][0]["text"], schema_invalid

    invalid_params = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "film.status", "arguments": []}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": 7}},
    ]
    for message in invalid_params:
        response = mcp.handle_message(message)
        assert response is not None
        assert response["error"]["code"] == -32602, response


    assert mcp.handle_message({"jsonrpc": "2.0", "method": "ping"}) is None
    assert mcp.handle_message({"jsonrpc": "2.0", "method": "tools/call",
                               "params": {"name": "film.status"}}) is None
    invalid_notification = mcp.handle_message({
        "jsonrpc": "2.0", "id": 12, "method": "notifications/initialized"
    })
    assert invalid_notification is not None
    assert invalid_notification["error"]["code"] == -32600, invalid_notification


def test_standalone_mcp_parse_error_does_not_break_stdio_loop(monkeypatch):
    source = io.StringIO(
        "not-json\n"
        + json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"})
        + "\n"
    )
    sink = io.StringIO()
    monkeypatch.setattr(mcp.sys, "stdin", source)
    monkeypatch.setattr(mcp.sys, "stdout", sink)

    mcp.main()

    responses = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert len(responses) == 2, responses
    assert responses[0]["error"]["code"] == -32700, responses[0]
    assert responses[1]["id"] == 9 and responses[1]["result"] == {}, responses[1]


def test_mcp_look_and_render_require_revision_and_render_forwards_exact_pin(monkeypatch):
    calls = []

    def fake_run_cli(args, timeout):
        calls.append((list(args), timeout))
        return mcp._json_result({
            "ok": True,
            "rendered_revision": "revision-exact",
            "fps": 60,
        })

    monkeypatch.setattr(mcp, "_run_cli", fake_run_cli)

    missing_render_revision = mcp.call_tool("film.render", {"project_id": "mcp-render-pin"})
    assert missing_render_revision["isError"] is True
    assert "expected_revision" in missing_render_revision["content"][0]["text"]

    missing_look_revision = mcp.call_tool("film.look", {"project_id": "mcp-look-pin"})
    assert missing_look_revision["isError"] is True
    assert "revision" in missing_look_revision["content"][0]["text"]
    assert calls == []

    invalid_fps = mcp.call_tool("film.render", {
        "project_id": "mcp-render-pin",
        "expected_revision": "revision-exact",
        "fps": 61,
    })
    assert invalid_fps["isError"] is True
    assert "60" in invalid_fps["content"][0]["text"]
    assert calls == []

    rendered = mcp.call_tool("film.render", {
        "project_id": "mcp-render-pin",
        "expected_revision": "revision-exact",
        "fps": 60,
    })
    assert rendered["isError"] is False
    args, timeout = calls.pop()
    assert args == [
        "film", "render", "--project-id", "mcp-render-pin",
        "--expected-revision", "revision-exact", "--fps", "60",
    ]
    assert timeout == 3600

    looked = mcp.call_tool("film.look", {
        "project_id": "mcp-look-pin",
        "revision": "revision-exact",
        "times": [0, 1.5],
    })
    assert looked["isError"] is False
    args, timeout = calls.pop()
    assert args == [
        "film", "look", "--project-id", "mcp-look-pin",
        "--revision", "revision-exact", "--times", "0,1.5",
    ]
    assert timeout == 1800
    assert calls == []


def test_film_edit_mcp_uses_the_same_revision_guard_and_undo_history(tmp_path, monkeypatch):
    storage_root = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    project_id = "film-mcp-contract"
    project = config.project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
    (project / fd.FILM_NAME).write_text(FILM, encoding="utf-8")

    status = _payload(mcp.call_tool("film.status", {"project_id": project_id}))
    assert status["ok"] is True and status["exists"] is True
    original_revision = status["revision"]

    edited = _payload(mcp.call_tool("film.edit", {
        "project_id": project_id,
        "base_revision": original_revision,
        "operations": [{"op": "set", "clip": "footage", "field": "at", "value": 0.5}],
    }))
    assert edited["ok"] is True
    assert edited["base_revision"] == original_revision
    assert edited["revision"] != original_revision
    assert edited["operation_count"] == 1

    stale = mcp.call_tool("film.edit", {
        "project_id": project_id,
        "base_revision": original_revision,
        "operations": [{"op": "set", "clip": "footage", "field": "at", "value": 1.25}],
    })
    assert stale["isError"] is True
    assert "REVISION_CONFLICT" in stale["content"][0]["text"]

    current = fd.read_project_film(project_id)
    assert current["rev"] == edited["revision"]
    assert float(current["doc"]["tracks"][0]["clips"][0]["attrs"]["at"]) == 0.5
    history = fd._history_read(project_id)
    assert len(history["past"]) == 1


def test_mcp_rejects_invalid_or_oversized_edit_before_invoking_cli(monkeypatch):
    def forbidden_run(*args, **kwargs):
        raise AssertionError("CLI must not run for invalid input")

    monkeypatch.setattr(mcp.subprocess, "run", forbidden_run)
    invalid = mcp.call_tool("film.edit", {
        "project_id": "some-project",
        "base_revision": "rev-1",
        "operations": [{"op": "set", "clip": "x", "field": "at", "value": float("nan")}],
    })
    assert invalid["isError"] is True

    oversized = mcp.call_tool("film.edit", {
        "project_id": "some-project",
        "base_revision": "rev-1",
        "operations": [{"op": "remove", "clip": "x"}] * (mcp.MAX_OPERATIONS + 1),
    })
    assert oversized["isError"] is True
    assert "1–200" in oversized["content"][0]["text"]

    unknown = mcp.call_tool("film.destroy_all", {"project_id": "some-project"})
    assert unknown["isError"] is True


def test_standalone_mcp_rejects_undeclared_arguments_before_side_effects(monkeypatch):
    def forbidden_run(*args, **kwargs):
        raise AssertionError("CLI must not run for undeclared arguments")

    monkeypatch.setattr(mcp.subprocess, "run", forbidden_run)
    result = mcp.call_tool("film.validate", {
        "project_id": "some-project",
        "unexpected": True,
    })
    assert result["isError"] is True, result
    assert "unexpected" in result["content"][0]["text"], result


def test_mcp_look_rejects_nonfinite_and_boolean_times(monkeypatch):
    def forbidden_run(*args, **kwargs):
        raise AssertionError("CLI must not run for invalid input")

    monkeypatch.setattr(mcp.subprocess, "run", forbidden_run)
    for times in ([float("inf")], [True], [-0.1], []):
        result = mcp.call_tool("film.look", {
            "project_id": "some-project", "revision": "rev-1", "times": times
        })
        assert result["isError"] is True
