"""Procedural graphics provider contract.

Procedural visuals are first-class GraphicScene engines. Providers compile a
structured program into a self-contained HTML fragment; the Director never
supplies arbitrary executable code.

The registry keeps Composition/Workflow independent of any visual engine.
"""

from __future__ import annotations

from typing import Dict, Protocol

from video_director.graphics.program import GraphicProgram, GraphicScene


class ProceduralProviderError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


class ProceduralProvider(Protocol):
    provider_id: str

    def compile_scene(self, program: GraphicProgram, scene: GraphicScene) -> str:
        """Return a self-contained HTML fragment for one GraphicScene."""
        ...


_PROVIDERS: Dict[str, ProceduralProvider] = {}


def register_provider(provider: ProceduralProvider) -> None:
    provider_id = str(provider.provider_id).strip()
    if not provider_id:
        raise ValueError("Procedural Provider 必须有 provider_id")
    if provider_id in _PROVIDERS:
        raise ValueError(f"重复注册 Procedural Provider: {provider_id}")
    _PROVIDERS[provider_id] = provider


def get_provider(provider_id: str) -> ProceduralProvider:
    try:
        return _PROVIDERS[provider_id]
    except KeyError as exc:
        raise ProceduralProviderError(
            "PROCEDURAL_PROVIDER_NOT_FOUND",
            f"未注册 Procedural Provider: {provider_id!r}",
        ) from exc


def list_providers() -> list[str]:
    return sorted(_PROVIDERS)
