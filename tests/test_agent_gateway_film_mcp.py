"""Agent-facing Film MCP integration through the existing unified MCP gateway."""
from __future__ import annotations

import json

from video_director import config, editor, film_document as fd, film_mcp_server
from video_director.agent_gateway import mcp_server
from video_director.models import CompositionIR


def _payload(result: dict) -> dict:
    assert result.get("content"), result
    return json.loads(result["content"][0]["text"])


def _gateway_call(name: str, arguments: dict, request_id: int = 100) -> tuple[dict, bool]:
    response = mcp_server._handle({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert "result" in response, response
    result = response["result"]
    return _payload(result), bool(result.get("isError"))


def test_existing_gateway_lists_and_routes_film_tools(monkeypatch):
    listed = mcp_server._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {item["name"] for item in listed["result"]["tools"]}
    assert {"film.status", "film.initialize", "film.validate", "film.edit", "film.diff", "film.look", "film.render"} <= names
    gateway_film_tools = {
        item["name"]: item for item in listed["result"]["tools"] if item["name"].startswith("film.")
    }
    standalone_film_tools = {item["name"]: item for item in film_mcp_server.tool_definitions()}
    assert gateway_film_tools == standalone_film_tools

    monkeypatch.setattr(film_mcp_server, "call_tool", lambda name, args: {
        "content": [{"type": "text", "text": json.dumps({"ok": True, "name": name, "args": args})}],
        "isError": False,
    })
    called = mcp_server._handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "film.status", "arguments": {"project_id": "demo-project"}},
    })
    assert called["result"]["isError"] is False
    body = _payload(called["result"])
    assert body["name"] == "film.status"
    assert body["args"]["project_id"] == "demo-project"


def test_gateway_supports_modern_discovery_metadata_and_cacheable_results(monkeypatch):
    modern_meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "contract-test", "version": "1.0.0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    discover = mcp_server._handle({
        "jsonrpc": "2.0", "id": "discover", "method": "server/discover",
        "params": {"_meta": modern_meta},
    })
    assert "result" in discover, discover
    discovery = discover["result"]
    assert discovery["resultType"] == "complete"
    assert "2026-07-28" in discovery["supportedVersions"]
    assert mcp_server.SERVER_INFO == discovery["_meta"]["io.modelcontextprotocol/serverInfo"]
    assert discovery["capabilities"] == {"tools": {}}
    assert discovery["ttlMs"] == 0 and discovery["cacheScope"] == "private"

    listed = mcp_server._handle({
        "jsonrpc": "2.0", "id": "list", "method": "tools/list",
        "params": {"_meta": modern_meta},
    })
    assert listed["result"]["resultType"] == "complete"
    assert listed["result"]["ttlMs"] == 0
    assert listed["result"]["cacheScope"] == "private"
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert "creative.inspect" in names and "film.status" in names

    monkeypatch.setattr(film_mcp_server, "call_tool", lambda name, args: {
        "content": [{"type": "text", "text": json.dumps({"name": name, "args": args})}],
        "isError": False,
    })
    called = mcp_server._handle({
        "jsonrpc": "2.0", "id": "call", "method": "tools/call",
        "params": {
            "_meta": modern_meta,
            "name": "film.status",
            "arguments": {"project_id": "demo-project"},
        },
    })
    assert called["result"]["resultType"] == "complete"
    assert called["result"]["isError"] is False

    unsupported = mcp_server._handle({
        "jsonrpc": "2.0", "id": "old-client", "method": "tools/list",
        "params": {"_meta": {
            "io.modelcontextprotocol/protocolVersion": "2099-01-01",
            "io.modelcontextprotocol/clientCapabilities": {},
        }},
    })
    assert unsupported["error"]["code"] == -32022
    assert unsupported["error"]["data"]["requested"] == "2099-01-01"
    assert "2026-07-28" in unsupported["error"]["data"]["supported"]

    missing_capabilities = mcp_server._handle({
        "jsonrpc": "2.0", "id": "bad-meta", "method": "tools/list",
        "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}},
    })
    assert missing_capabilities["error"]["code"] == -32602


def test_film_initialize_requires_the_exact_composition_revision_and_warning_acceptance(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "agent-film-initialize"
    project_root = config.project_dir(project_id)
    assets = project_root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "logo.png").write_bytes(b"test image bytes")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-logo", "file": "assets/logo.png", "type": "image"},
    ]), encoding="utf-8")

    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 1},
        "style_id": "brand-editorial",
        "style_contract": {
            "style_id": "brand-editorial",
            "palette": {"accent": "#d02030", "background": "#f5f0e7"},
            "typography": {"font": "Brand Sans", "weight": "700"},
            "motion_temper": "calm",
            "safe_zone": 0.9,
        },
        "origin_template_id": "product-launch",
        "origin_template_version": "3.2.1",
        "references": {"references": [{
            "id": "ref-logo", "ref_type": "visual_reference", "target_asset_id": "asset-logo",
            "role": "brand identity", "note": "brand mark reference",
        }]},
        "assets": [{
            "id": "asset-logo", "type": "image", "file": "assets/logo.png",
            "prompt": "Minimal red brand mark on warm paper",
            "description": "Official launch brand logo",
            "keywords": ["brand", "logo"],
            "source": "stock",
            "license_note": "Use permitted in campaign media",
            "provenance": {"provider": "test-library", "source_url": "https://example.invalid/logo"},
            "rights_status": "CLEAR",
        }],
        "scenes": [{
            "id": "scene-logo", "start": 0, "duration": 1, "text": "launch",
            "layers": [{
                "id": "logo-layer", "type": "image", "asset_id": "asset-logo",
                "start": 0, "duration": 1,
            }],
        }],
    })
    editor.save_composition(project_id, ir)
    status, status_error = _gateway_call("film.status", {"project_id": project_id}, request_id=20)
    assert status_error is False
    assert status["ok"] is True and status["exists"] is False
    assert status["source_composition_available"] is True
    source_version = status["source_composition_version"]
    assert source_version == editor.composition_version(project_id)
    assert status["migration_preview"]["source_composition_version"] == source_version

    stale, stale_error = _gateway_call("film.initialize", {
        "project_id": project_id,
        "expected_composition_version": source_version + 1,
        "accept_warnings": True,
    }, request_id=21)
    assert stale_error is True
    assert stale["ok"] is False and stale["code"] == "COMPOSITION_CHANGED"
    assert not (project_root / fd.FILM_NAME).exists()

    requested = {
        "project_id": project_id,
        "expected_composition_version": source_version,
    }
    initialized, initialized_error = _gateway_call("film.initialize", requested, request_id=22)
    if not initialized.get("ok") and initialized.get("code") == "MIGRATION_WARNINGS_REQUIRE_CONFIRMATION":
        assert initialized_error is True and initialized["warnings"]
        initialized, initialized_error = _gateway_call(
            "film.initialize", {**requested, "accept_warnings": True}, request_id=23
        )
    assert initialized_error is False
    assert initialized["ok"] is True and initialized["initialized"] is True
    assert (project_root / fd.FILM_NAME).is_file()
    current = fd.read_project_film(project_id)
    assert current["exists"] is True
    assert current["rev"] == initialized["rev"]
