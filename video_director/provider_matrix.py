"""Provider Capability Matrix。

把「角色配置」与「实际可执行能力」分开：
- configured != supported
- supported != implemented
- implemented != available

矩阵输出只使用硬状态：
READY / MISSING / UNSUPPORTED / UNKNOWN。
所有执行前 gate 都从这里读取，避免各 Provider 在不同模块各自猜测。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.parse import urlparse

from video_director import model_config


READY = "READY"
MISSING = "MISSING"
UNSUPPORTED = "UNSUPPORTED"
UNKNOWN = "UNKNOWN"

OPERATIONS = (
    "text.generate",
    "text.rewrite",
    "vision.frame_analyze",
    "image.generate",
    "video.generate",
    "video.image_to_video",
    "tts.speak",
    "tts.word_boundary",
    "tts.preview",
    "stock.search_video",
    "stock.download_video",
    "motion.render",
)


@dataclass(frozen=True)
class ProviderDefinition:
    provider_id: str
    role: str
    label: str
    operations: tuple[str, ...]
    requires_key: bool = True
    requires_base_url: bool = False
    requires_model: bool = False
    implemented: bool = True
    note: str = ""


@dataclass(frozen=True)
class CapabilityState:
    provider_id: str
    role: str
    operation: str
    status: str
    configured: bool
    reason: str = ""


# 这里声明的是「本项目当前真正接通的协议/执行器」。
# 没有实现的 Provider 即使用户填了 key，也不会被标记 READY。
DEFINITIONS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition(
        "openai", "llm", "OpenAI",
        ("text.generate", "text.rewrite"), requires_model=True,
        note="OpenAI-compatible chat/completions",
    ),
    ProviderDefinition(
        "openai-compatible", "llm", "OpenAI 兼容文本服务",
        ("text.generate", "text.rewrite"), requires_model=True,
        note="按 OpenAI chat/completions 协议接入；具体厂商能力由端点负责",
    ),
    ProviderDefinition(
        "openai", "image", "OpenAI Images",
        ("image.generate",), requires_model=True, requires_base_url=True,
        note="OpenAI Images data[].b64_json/url 同步协议",
    ),
    ProviderDefinition(
        "openai-compatible", "image", "OpenAI 兼容生图",
        ("image.generate",), requires_model=True, requires_base_url=True,
        note="本项目只接受标准 Images 同步响应；异步 task_id 不算已接通",
    ),
    ProviderDefinition(
        "video-openai-compatible", "video", "OpenAI 兼容视频",
        ("video.generate",), requires_model=True, requires_base_url=True,
        note="当前执行器固定调用 /videos/generations；首末帧/I2V 尚未接通",
    ),
    ProviderDefinition(
        "frame-vision-openai-compatible", "vision", "OpenAI 兼容视觉",
        ("vision.frame_analyze",), requires_model=True, requires_base_url=True,
        note="抽帧后调用 chat/completions 多模态消息",
    ),
    ProviderDefinition(
        "edge-tts", "tts", "Edge TTS",
        ("tts.speak", "tts.word_boundary", "tts.preview"),
        requires_key=False, implemented=True,
        note="当前系统唯一真正接通的 TTS provider；公开端点",
    ),
    ProviderDefinition(
        "azure", "tts", "Azure Speech", ("tts.speak", "tts.word_boundary", "tts.preview"),
        requires_key=True, implemented=False,
        note="已登记，具体 SDK/voice 路由未接通",
    ),
    ProviderDefinition(
        "gemini", "tts", "Google Gemini TTS", ("tts.speak",),
        requires_key=True, implemented=False,
        note="已登记，具体 voice 路由未接通",
    ),
    ProviderDefinition(
        "elevenlabs", "tts", "ElevenLabs", ("tts.speak",),
        requires_key=True, implemented=False,
        note="已登记，具体 voice 路由未接通",
    ),
    ProviderDefinition(
        "siliconflow", "tts", "SiliconFlow TTS", ("tts.speak",),
        requires_key=True, implemented=False,
        note="已登记，具体 voice 路由未接通",
    ),
    ProviderDefinition(
        "kokoro", "tts", "Kokoro 本地", ("tts.speak",),
        requires_key=False, implemented=False,
        note="已登记，当前未接本地推理执行器",
    ),
    ProviderDefinition(
        "pexels", "stock", "Pexels",
        ("stock.search_video", "stock.download_video"),
        requires_key=True, implemented=True,
        note="官方视频搜索/下载",
    ),
    ProviderDefinition(
        "pixabay", "stock", "Pixabay",
        ("stock.search_video", "stock.download_video"),
        requires_key=True, implemented=True,
        note="官方视频搜索/下载；standalone 商用另受许可证策略约束",
    ),
    ProviderDefinition(
        "coverr", "stock", "Coverr", ("stock.search_video", "stock.download_video"),
        requires_key=True, implemented=False,
        note="自动搜索未开放；商业条款状态保留 REVIEW/UNKNOWN",
    ),
    ProviderDefinition(
        "manual-vision", "vision", "Manual Manifest",
        ("vision.frame_analyze",), requires_key=False, implemented=True,
        note="只有存在手工标注 manifest 时才 READY",
    ),
    ProviderDefinition(
        "huashu-motion", "motion", "Huashu Procedural Motion",
        ("motion.render",), requires_key=False, implemented=True,
        note="本地确定性动效，不依赖外部 API key",
    ),
    ProviderDefinition(
        "ffmpeg", "renderer", "FFmpeg",
        (), requires_key=False, implemented=True,
        note="通用渲染器；Provider Matrix 仅描述外部能力，FFmpeg 由 capabilities/doctor 检测",
    ),
    ProviderDefinition(
        "hyperframes", "renderer", "HyperFrames",
        (), requires_key=True, implemented=False,
        note="配置角色已存在，但当前成片链未实际调用",
    ),
)


def _host(base_url: str) -> str:
    try:
        return (urlparse(base_url or "").hostname or "").lower()
    except Exception:
        return ""


def infer_provider(role: str, cfg: Optional[Mapping[str, Any]] = None) -> str:
    cfg = _effective_config(role, cfg)
    if role.startswith("stock_"):
        return role[len("stock_"):]
    if role == "tts":
        return "edge-tts"
    if role == "motion":
        return "huashu-motion"
    if role == "renderer":
        return "ffmpeg"
    if role == "hyperframes":
        return "hyperframes"

    base = str(cfg.get("base_url") or "")
    host = _host(base)
    if role == "video":
        return "video-openai-compatible"
    if "api.openai.com" in host:
        return "openai"
    if "siliconflow" in host:
        return "openai-compatible"
    if "modelscope" in host:
        return "openai-compatible"
    if role in {"llm", "image", "vision"} and base:
        return "openai-compatible"
    return "unconfigured"


def _effective_config(role: str, cfg: Optional[Mapping[str, Any]] = None) -> dict:
    raw = dict(model_config.get_role_config(role) if cfg is None else cfg)
    definition = model_config.ROLE_DEFS.get(role, {})
    if not raw.get("base_url"):
        raw["base_url"] = definition.get("default_base_url", "")
    if not raw.get("model"):
        raw["model"] = definition.get("default_model", "")
    return raw


def _configured(defn: ProviderDefinition, cfg: Mapping[str, Any], *, manual_manifest: str = "") -> tuple[bool, str]:
    if defn.provider_id == "manual-vision":
        if manual_manifest and os.path.isfile(manual_manifest):
            return True, ""
        return False, "未提供存在的手工标注 manifest"

    if defn.provider_id == "huashu-motion":
        return True, ""

    if defn.provider_id == "ffmpeg":
        return True, ""

    if defn.requires_key and not str(cfg.get("api_key") or "").strip():
        return False, "未配置 API key"

    if defn.requires_base_url and not str(cfg.get("base_url") or "").strip():
        return False, "未配置服务地址"

    if defn.requires_model and not str(cfg.get("model") or "").strip():
        return False, "未配置模型名"

    return True, ""


def state_for_provider(provider_id: str, role: str, operation: str,
                         cfg: Optional[Mapping[str, Any]] = None,
                         *, manual_manifest: str = "") -> CapabilityState:
    matrix_role = "stock" if role.startswith("stock_") else ("renderer" if role == "hyperframes" else role)
    cfg = _effective_config(role, cfg)
    definition = next(
        (d for d in DEFINITIONS if d.provider_id == provider_id and d.role == matrix_role),
        None,
    )
    if definition is None:
        return CapabilityState(provider_id, role, operation, UNKNOWN,
                               bool(cfg.get("api_key")), "Provider 未登记")
    if operation not in definition.operations or not definition.implemented:
        return CapabilityState(
            provider_id, role, operation, UNSUPPORTED, bool(cfg.get("api_key")),
            definition.note or "当前 Provider/执行器尚未接通该能力",
        )
    ok, reason = _configured(definition, cfg, manual_manifest=manual_manifest)
    if not ok:
        return CapabilityState(provider_id, role, operation, MISSING, False, reason)
    return CapabilityState(provider_id, role, operation, READY, True, definition.note)


def state(role: str, operation: str, cfg: Optional[Mapping[str, Any]] = None,
          *, manual_manifest: str = "") -> CapabilityState:
    cfg = _effective_config(role, cfg)
    provider_id = infer_provider(role, cfg)
    return state_for_provider(
        provider_id, role, operation, cfg, manual_manifest=manual_manifest
    )


def relevant_operations(role: str, cfg: Optional[Mapping[str, Any]] = None) -> tuple[str, ...]:
    """该角色当前 provider 声明的能力操作（供设置页逐项展示状态）。"""
    cfg = _effective_config(role, cfg)
    provider_id = infer_provider(role, cfg)
    matrix_role = "stock" if role.startswith("stock_") else role
    definition = next(
        (d for d in DEFINITIONS if d.provider_id == provider_id and d.role == matrix_role),
        None,
    )
    if definition is None:
        return ()
    return definition.operations


def require(role: str, operation: str, cfg: Optional[Mapping[str, Any]] = None,
            *, manual_manifest: str = "") -> CapabilityState:
    result = state(role, operation, cfg, manual_manifest=manual_manifest)
    if result.status != READY:
        raise ProviderCapabilityError(result)
    return result


class ProviderCapabilityError(RuntimeError):
    def __init__(self, state: CapabilityState):
        self.state = state
        if state.status == UNSUPPORTED:
            code = "CAPABILITY_UNSUPPORTED"
        elif state.status == UNKNOWN:
            code = "PROVIDER_UNKNOWN"
        else:
            code = "PROVIDER_MISSING"
        self.code = code
        super().__init__(
            f"[{code}] {state.role}/{state.operation}: "
            f"provider={state.provider_id}; {state.reason}"
        )


def matrix_for_frontend() -> list[dict]:
    """不返回 key，只返回当前配置状态与已声明操作。"""
    out = []
    roles = ("llm", "image", "video", "vision")
    for role in roles:
        cfg = _effective_config(role)
        provider_id = infer_provider(role, cfg)
        operations = [op for op in OPERATIONS if any(
            d.role == role and d.provider_id == provider_id and op in d.operations
            for d in DEFINITIONS
        )]
        states = {
            op: state(role, op, cfg).status
            for op in operations
        }
        out.append({
            "role": role,
            "provider": provider_id,
            "configured": bool(cfg.get("api_key")),
            "model": cfg.get("model") or "",
            "base_url": cfg.get("base_url") or "",
            "operations": states,
        })

    # TTS 与 Stock 使用自己的 provider registry，不要求 API key 全局角色。
    for role, provider_ids in (
        ("tts", ("edge-tts", "azure", "gemini", "elevenlabs", "siliconflow", "kokoro")),
        ("stock", ("pexels", "pixabay", "coverr")),
    ):
        for provider_id in provider_ids:
            fake_cfg = {}
            if role == "tts" and provider_id == "edge-tts":
                fake_cfg = {"api_key": "not-required"}
            if role == "stock":
                fake_cfg = model_config.get_role_config(f"stock_{provider_id}")
            defs = [d for d in DEFINITIONS if d.role == role and d.provider_id == provider_id]
            ops = defs[0].operations if defs else ()
            matrix_role = "stock" if role == "stock" else "tts"
            display_configured = (
                bool(fake_cfg.get("api_key"))
                if role == "stock"
                else provider_id == "edge-tts"
            )
            out.append({
                "role": role,
                "provider": provider_id,
                "configured": display_configured,
                "operations": {
                    op: state_for_provider(
                        provider_id,
                        matrix_role,
                        op,
                        fake_cfg,
                        manual_manifest="",
                    ).status
                    for op in ops
                },
            })
    return out


def describe(role: str, cfg: Optional[Mapping[str, Any]] = None,
             *, manual_manifest: str = "") -> dict:
    cfg = _effective_config(role, cfg)
    pid = infer_provider(role, cfg)
    states = {}
    for op in OPERATIONS:
        st = state(role, op, cfg, manual_manifest=manual_manifest)
        if st.status != MISSING or op in ("video.image_to_video", "image.generate", "video.generate"):
            states[op] = {"status": st.status, "reason": st.reason}
    return {
        "role": role,
        "provider": pid,
        "configured": bool(cfg.get("api_key")),
        "model": cfg.get("model") or "",
        "base_url": cfg.get("base_url") or "",
        "capabilities": states,
    }


__all__ = [
    "READY", "MISSING", "UNSUPPORTED", "UNKNOWN", "ProviderCapabilityError",
    "CapabilityState", "infer_provider", "state_for_provider", "state", "require",
    "matrix_for_frontend", "describe",
]
