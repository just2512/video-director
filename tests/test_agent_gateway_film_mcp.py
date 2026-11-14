"""Agent-facing Film MCP integration through the existing unified MCP gateway."""
from __future__ import annotations

import json
import pytest

from video_director import config, editor, film_document as fd, film_mcp_server, tool_executor
from video_director.agent_gateway import capability_gateway
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


def test_film_authority_blocks_legacy_agent_paths_but_preserves_asset_preparation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    project_id = "agent-film-authority-hard-gate"
    project_root = config.project_dir(project_id)
    project_root.mkdir(parents=True, exist_ok=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text("""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=640,height=360"></head>
<body><section id="v1" data-label="主画面"><video id="clip-a" src="assets/a.mp4#t=0,10" at="0"></video></section></body></html>
""", encoding="utf-8")
    original = film_path.read_bytes()
    assets_dir = project_root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "a.mp4").write_bytes(b"test media placeholder")

    for tool, payload, target_id in (
        ("timeline.move", {"clipId": "clip-a", "trackId": "video-main", "at": 2}, None),
        ("render.final", {}, None),
        ("generation.image", {"prompt": "替换镜头", "confirmed": True}, "clip-a"),
        ("music.match_video", {}, None),
    ):
        result = tool_executor.execute_tool(project_id, {
            "tool": tool, "payload": payload, "targetId": target_id,
        })
        assert result["ok"] is False, (tool, result)
        assert result["status"] == "REJECTED", (tool, result)
        assert result["code"] == "FILM_AUTHORITY_ACTIVE", (tool, result)
        assert result["result"]["authority"] == "film.html"
        assert result["result"]["revision"] == fd.read_project_film(project_id)["rev"]

    # MCP and CLI both dispatch through the same guarded gateway.
    mcp_result, mcp_error = _gateway_call("creative.execute", {
        "project_id": project_id, "tool": "timeline.move",
        "payload": {"clipId": "clip-a", "trackId": "video-main", "at": 3},
    }, request_id=901)
    assert mcp_error is True and mcp_result["code"] == "FILM_AUTHORITY_ACTIVE", mcp_result
    cli_result = capability_gateway.dispatch(
        "creative.execute", project_id=project_id, tool="render.final", payload={}
    )
    assert cli_result["status"] == "REJECTED"
    assert cli_result["code"] == "FILM_AUTHORITY_ACTIVE"

    # High-level direct paths must not fall through to legacy Director/render/verify.
    monkeypatch.setattr("video_director.director.direct", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy director called")))
    monkeypatch.setattr("video_director.confirm.render_draft", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy render called")))
    for result in (
        capability_gateway.creative_plan(project_id, "调整节奏"),
        capability_gateway.creative_preview(project_id),
        capability_gateway.creative_verify(project_id),
    ):
        assert result["status"] == "REJECTED", result
        assert result["code"] == "FILM_AUTHORITY_ACTIVE", result

    for result in (
        capability_gateway.director_create({"project_id": project_id}),
        capability_gateway.director_retry(project_id),
        capability_gateway.director_continue(project_id),
    ):
        assert result["status"] == "REJECTED", result
        assert result["code"] == "FILM_AUTHORITY_ACTIVE", result

    # The legacy Python CLI must not bypass the same authority boundary.
    from video_director.__main__ import main as cli_main
    script = tmp_path / "legacy-script.txt"
    script.write_bytes(("不应覆盖已初始化影片的旧 CLI").encode("utf-8"))
    with pytest.raises(SystemExit) as cli_exit:
        cli_main(["text", "--script", str(script), "--project-id", project_id])
    assert cli_exit.value.code == 2
    cli_output = capsys.readouterr().out
    assert '"code": "FILM_AUTHORITY_ACTIVE"' in cli_output

    context_result = capability_gateway.creative_execute(
        project_id, "system.context.inspect", {"selection": {"type": "project"}}
    )
    assert context_result["ok"] is True, context_result
    assert context_result["result"]["context"]["authority"] == "film.html"

    # Asset generation without a timeline target remains available; supplying a
    # target is rejected before the legacy Composition adoption path can run.
    monkeypatch.setattr(
        tool_executor, "_execute_generation",
        lambda pid, tool, payload, target: {
            "tool": tool, "ok": True, "status": "ASSET_PREPARED",
            "result": {"project_id": pid, "target_id": target},
        },
    )
    prepared = tool_executor.execute_tool(project_id, {
        "tool": "generation.image", "payload": {"prompt": "准备素材"},
    })
    assert prepared["ok"] is True and prepared["status"] == "ASSET_PREPARED", prepared
    assert film_path.read_bytes() == original
    assert fd.read_project_film(project_id)["rev"] == fd.revision_of(original.decode("utf-8"))

    # The new authority remains editable through the revision-bound MCP surface.
    status = _payload(film_mcp_server.call_tool("film.status", {"project_id": project_id}))
    assert status["ok"] is True and status["revision"] == fd.revision_of(original.decode("utf-8"))
    validation = _payload(film_mcp_server.call_tool("film.validate", {"project_id": project_id}))
    assert validation["ok"] is True, validation
    edited = _payload(film_mcp_server.call_tool("film.edit", {
        "project_id": project_id,
        "base_revision": status["revision"],
        "operations": [{"op": "move", "clip": "clip-a", "track_id": "v1", "at": 1.0}],
    }))
    assert edited["ok"] is True, edited
    assert edited["revision"] != status["revision"]
