"""Protocol compatibility helpers for the unified Video Director MCP servers.

The server supports the 2026-07-28 stateless request envelope and the
2025-06-18 initialize handshake used by older MCP clients.
"""
from __future__ import annotations

from typing import Any

LEGACY_PROTOCOL_VERSION = "2025-06-18"
MODERN_PROTOCOL_VERSION = "2026-07-28"
SUPPORTED_PROTOCOL_VERSIONS = (MODERN_PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSION)

PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
UNSUPPORTED_PROTOCOL_VERSION_CODE = -32022


def request_protocol_version(params: Any) -> tuple[bool, Any]:
    """Return whether request metadata declares a protocol version and its value."""
    if not isinstance(params, dict):
        return False, None
    meta = params.get("_meta")
    if not isinstance(meta, dict) or PROTOCOL_VERSION_META_KEY not in meta:
        return False, None
    return True, meta.get(PROTOCOL_VERSION_META_KEY)


def validate_modern_request_metadata(params: Any) -> str | None:
    """Validate fields required by the 2026-07-28 RequestMetaObject."""
    if not isinstance(params, dict):
        return "modern requests require params to be an object"
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return "modern requests require params._meta to be an object"
    if meta.get(PROTOCOL_VERSION_META_KEY) != MODERN_PROTOCOL_VERSION:
        return "params._meta must declare the modern protocol version"
    if not isinstance(meta.get(CLIENT_CAPABILITIES_META_KEY), dict):
        return f"params._meta.{CLIENT_CAPABILITIES_META_KEY} must be an object"
    client_info = meta.get(CLIENT_INFO_META_KEY)
    if client_info is not None:
        if (not isinstance(client_info, dict)
                or not isinstance(client_info.get("name"), str)
                or not client_info.get("name", "").strip()
                or not isinstance(client_info.get("version"), str)
                or not client_info.get("version", "").strip()):
            return f"params._meta.{CLIENT_INFO_META_KEY} must contain non-empty name and version strings"
    return None


def unsupported_protocol_version_response(request_id: Any, requested: Any) -> dict[str, Any]:
    """Build the protocol-defined version-negotiation error response."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": UNSUPPORTED_PROTOCOL_VERSION_CODE,
            "message": "Unsupported protocol version",
            "data": {
                "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
                "requested": requested,
            },
        },
    }


def modern_result(value: Any, *, cacheable: bool = False) -> dict[str, Any]:
    """Add required modern-era result envelope fields without changing legacy results."""
    result = dict(value) if isinstance(value, dict) else {"value": value}
    result.setdefault("resultType", "complete")
    if cacheable:
        # Conservative defaults: no claim of freshness or cross-user cache sharing.
        result.setdefault("ttlMs", 0)
        result.setdefault("cacheScope", "private")
    return result


def discover_result(
    server_info: dict[str, str],
    capabilities: dict[str, Any],
    *,
    instructions: str | None = None,
) -> dict[str, Any]:
    """Build the mandatory 2026-07-28 server/discover result."""
    result: dict[str, Any] = {
        "resultType": "complete",
        "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "capabilities": dict(capabilities),
        "_meta": {SERVER_INFO_META_KEY: dict(server_info)},
        "ttlMs": 0,
        "cacheScope": "private",
    }
    if instructions:
        result["instructions"] = instructions
    return result
