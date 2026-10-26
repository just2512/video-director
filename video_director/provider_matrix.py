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
    # §十八 付费 Provider 确认：视频/图片生成调用真实产生费用，前端须显式
    # 告知「本镜头需付费视频生成」，执行器前置 confirm 门禁。TTS/LLM 文本
    # 默认不标付费（Edge TTS 免费；LLM 文本成本远低于视频生成）。
    is_paid: bool = False
    # §四十#6 ProviderCapability 深层属性（文档1 归一化契约）：
    # sync="sync"|"async"|"unknown"；max_duration_seconds=None=不限/未知；
    # supports_reference=None=不支持；retry_safe=None=未知（transport error
    # ≠ 可安全重试——远端可能已完成并收费，MPT §16）。
    sync_mode: str = "sync"
    max_duration_seconds: Optional[float] = None
    supports_reference: Optional[bool] = None
    supports_first_frame: Optional[bool] = None
    supports_last_frame: Optional[bool] = None
    retry_safe: Optional[bool] = None
    # 已知纯文本模型黑名单（2026-10-17 生产事故：vision 配 MiniMax-M3 → 全部素材 400 白烧）。
    # 命中即判 NOT_CONFIGURED 并写明原因；未收录模型放行，由真实调用失败诚实降级。
    text_only_model_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityState:
    provider_id: str
    role: str
    operation: str
    status: str
    configured: bool
    reason: str = ""
    is_paid: bool = False            # §十八 该操作是否产生付费
    requires_confirmation: bool = False  # §十八 READY+付费 → 前端须确认
    # §四十#6 深层属性透传（None=未知，不冒充知道）
    sync_mode: str = "sync"
    max_duration_seconds: Optional[float] = None
    supports_reference: Optional[bool] = None
    supports_first_frame: Optional[bool] = None
    supports_last_frame: Optional[bool] = None
    retry_safe: Optional[bool] = None


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
        is_paid=True,
    ),
    ProviderDefinition(
        "openai-compatible", "image", "OpenAI 兼容生图",
        ("image.generate",), requires_model=True, requires_base_url=True,
        note="本项目只接受标准 Images 同步响应；异步 task_id 不算已接通",
        is_paid=True,
    ),
    ProviderDefinition(
        "video-openai-compatible", "video", "OpenAI 兼容视频",
        ("video.generate",), requires_model=True, requires_base_url=True,
        note="当前执行器固定调用 /videos/generations；首末帧/I2V 尚未接通",
        is_paid=True,
        # 真实协议：异步 task_id 轮询（generation_jobs.json 恢复链）；参考图走
        # reference 通道；首末帧参数存在但未接通 → 显式 False（不冒充支持）。
        # retry_safe=None：transport error ≠ 可安全重试（远端可能已计费）。
        sync_mode="async",
        supports_reference=True,
        supports_first_frame=False,
        supports_last_frame=False,
        retry_safe=None,
    ),
    ProviderDefinition(
        "frame-vision-openai-compatible", "vision", "OpenAI 兼容视觉",
        ("vision.frame_analyze",), requires_model=True, requires_base_url=True,
        note="抽帧后调用 chat/completions 多模态消息",
        text_only_model_patterns=(
            # 已知纯文本模型（误配进 vision 角色时提前拦截，不发 image_url 才发现的 400）
            "MiniMax-M3*", "MiniMax-Text-*",
            "deepseek-v3*", "deepseek-chat*", "deepseek-r1*",
            "gpt-3.5*", "gpt-4-mini", "gpt-4-turbo-2024-*", "gpt-4-turbo-1106*",
            "qwen-turbo*", "qwen-plus*", "qwen-max*",
            "glm-4", "glm-4-*", "glm-4.5", "glm-4.5-*",
            "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k",
            "*-text-*",
        ),
    ),
    ProviderDefinition(
        "twelvelabs-marengo", "semantic_rerank", "Twelve Labs Marengo",
        ("visual.semantic_retrieve",), requires_model=True, requires_base_url=True,
        note="Marengo 3.5 多模态 embedding；用于语义检索候选生成",
        is_paid=True,
    ),
    ProviderDefinition(
        "twelvelabs-pegasus", "visual_qa", "Twelve Labs Pegasus",
        ("visual.qa",), requires_model=True, requires_base_url=True,
        note="Pegasus 1.5 视频理解；用于 Scene 对素材的语义/画面验证",
        is_paid=True,
    ),
    ProviderDefinition(
        "edge-tts", "tts", "Edge TTS",
        ("tts.speak", "tts.word_boundary", "tts.preview"),
        requires_key=False, implemented=True,
        note="当前系统唯一真正接通的 TTS provider；公开端点",
    ),
    ProviderDefinition(
        "azure", "tts", "Azure Speech", ("tts.speak", "tts.preview"),
        requires_key=True, implemented=True,
        note="SSML+REST 真实执行器（voice.synth_preview）；region 默认 eastasia（tts_azure_region 或 AZURE_REGION）",
    ),
    ProviderDefinition(
        "elevenlabs", "tts", "ElevenLabs", ("tts.speak", "tts.preview"),
        requires_key=True, implemented=True,
        note="REST /v1/text-to-speech 真实执行器（voice.synth_preview）",
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

    # vision 角色只登记了 frame-vision-openai-compatible 这一个可执行 provider。
    # 若继续走下面的 host 推断，会返回 openai / openai-compatible，而这两个 id
    # 没有 vision 定义 → state_for_provider 判定「Provider 未登记」返回 UNKNOWN
    # → FrameVisionProvider.available() 恒为 False：视觉理解链路整体不可达，
    # 用户配任何 key/base_url/模型都无效，素材永远「全部 VisionProvider 不可用」。
    if role == "vision":
        return "frame-vision-openai-compatible"

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
    if "twelvelabs" in host:
        if role == "semantic_rerank":
            return "twelvelabs-marengo"
        if role == "visual_qa":
            return "twelvelabs-pegasus"
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

    # 已知纯文本模型配进多模态角色 → 提前判不就绪（不发 image_url 才发现的 400）。
    # 同时匹配全名与「/」末段（ModelScope 风格 Org/Model 前缀：MiniMax/MiniMax-M3）。
    model_id = str(cfg.get("model") or "").strip()
    if defn.text_only_model_patterns and model_id:
        import fnmatch
        candidates = {model_id.lower(), model_id.rsplit("/", 1)[-1].lower()}
        for pat in defn.text_only_model_patterns:
            pat = pat.lower()
            if any(fnmatch.fnmatch(m, pat) for m in candidates):
                return False, f"模型 {model_id} 是纯文本模型，不支持视觉输入；请在设置里换成多模态模型"

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
    # §四十#6 深层属性从 definition 统一透传（全部状态分支一致）
    deep = dict(
        sync_mode=definition.sync_mode,
        max_duration_seconds=definition.max_duration_seconds,
        supports_reference=definition.supports_reference,
        supports_first_frame=definition.supports_first_frame,
        supports_last_frame=definition.supports_last_frame,
        retry_safe=definition.retry_safe,
    )
    if operation not in definition.operations or not definition.implemented:
        return CapabilityState(
            provider_id, role, operation, UNSUPPORTED, bool(cfg.get("api_key")),
            definition.note or "当前 Provider/执行器尚未接通该能力",
            is_paid=definition.is_paid, **deep,
        )
    ok, reason = _configured(definition, cfg, manual_manifest=manual_manifest)
    if not ok:
        return CapabilityState(provider_id, role, operation, MISSING, False, reason,
                               is_paid=definition.is_paid, **deep)
    # §十八：READY + 付费 → 前端须显式确认「本镜头需付费视频生成」
    return CapabilityState(provider_id, role, operation, READY, True, definition.note,
                           is_paid=definition.is_paid,
                           requires_confirmation=definition.is_paid, **deep)


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
    roles = ("llm", "image", "video", "vision", "semantic_rerank", "visual_qa")
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
        # §十八 付费标记：取该 provider 声明的 is_paid（image/video/semantic_rerank/visual_qa=True）
        defn = next((d for d in DEFINITIONS
                     if d.role == role and d.provider_id == provider_id), None)
        is_paid = bool(defn.is_paid) if defn else False
        row = {
            "role": role,
            "provider": provider_id,
            "configured": bool(cfg.get("api_key")),
            "model": cfg.get("model") or "",
            "base_url": cfg.get("base_url") or "",
            "operations": states,
            "is_paid": is_paid,
            "requires_confirmation": is_paid and any(
                st == READY for st in states.values()),
        }
        # §四十#6 深层属性进前端矩阵（None 序列化为 null）
        if defn:
            row.update({
                "sync_mode": defn.sync_mode,
                "max_duration_seconds": defn.max_duration_seconds,
                "supports_reference": defn.supports_reference,
                "supports_first_frame": defn.supports_first_frame,
                "supports_last_frame": defn.supports_last_frame,
                "retry_safe": defn.retry_safe,
            })
        out.append(row)

    # TTS 与 Stock 使用自己的 provider registry，不要求 API key 全局角色。
    for role, provider_ids in (
        ("tts", ("edge-tts", "azure", "elevenlabs")),
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
