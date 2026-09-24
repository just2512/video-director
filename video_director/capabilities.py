"""Capabilities Registry（OpenMontage Tool Registry / Capability Catalog 思想）.

能力声明 + Selector：
    registry()              → 当前系统能力表（声明式，非检查式）
    capability_report()     → 人类可读报告
    is_capable(name)        → 该能力是否可用（status != MISSING）
    degradation_explanation → 降级原因（如 vision 无 key 时靠文件名语义）
    preflight(required_caps)→ 任务级 gate：某类任务能不能做

能力级 vs 环境级（分工写清，避免职责混淆）：
    capabilities.preflight  能力级判断——某类任务能不能做
                            （talking-head 需要 matting → blocked；
                             script-media 需要 tts/renderer → ok）
    doctor.run_doctor      环境级检查——ffmpeg 在不在、磁盘够不够、
                            EdgeTTS 域名通不通

pipeline 启动顺序：先 preflight（任务级 gate，阻塞则不进入生成），
再 doctor（环境级 gate，FAIL 则不渲染）。两者互补，不互相替代。

本模块独立：
    - 不 import doctor（避免循环；renderer 可用性通过 prober._resolve_ffmpeg 判断，
      与 doctor 共享底层 prober 但无直接依赖）
    - 不 import pipeline（避免循环）
    - 零外部依赖（only stdlib + loguru + 同包 prober/audio）

状态四态（与 doctor 的 PASS/SKIP/FAIL 对应但语义不同）：
    PASS      主路径 + 全部 fallback 就绪（当前未使用，预留最优态）
    AVAILABLE 主路径就绪，可执行该能力核心功能
    DEGRADED  主路径缺失，fallback 可用（如 vision 无 key → 文件名语义）
    MISSING   无任何可用 provider（该类任务 BLOCKED）
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from loguru import logger

#: 能力状态枚举（字符串常量，便于日志/报告直接展示）
STATUS_PASS = "PASS"
STATUS_AVAILABLE = "AVAILABLE"
STATUS_DEGRADED = "DEGRADED"
STATUS_MISSING = "MISSING"

#: 所有合法状态（用于校验输入/输出）
_ALL_STATUSES = (STATUS_PASS, STATUS_AVAILABLE, STATUS_DEGRADED, STATUS_MISSING)

#: Vision API key 环境变量名（与 vision.FrameVisionProvider 一致）
VISION_API_KEY_ENV = "COMPOSER_VISION_API_KEY"


# ---------------------------------------------------------------------------
# 数据类（frozen dataclass，不可变，符合 coding-style.md immutability 原则）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Provider:
    """能力提供者声明（命名元组语义，dataclass 便于类型注解与默认值）。

    字段：
        name              provider 唯一名（如 "Edge", "FFmpeg", "FrameVision"）
        available         当前环境是否可用（运行时探测结果）
        requires_api_key  是否需要 API key（True=外部服务，False=本地/零依赖）
        runtime           运行时类别："cloud" | "cloud_llm" | "binary" |
                          "local_model" | "tts_native" | "local" | "service"
        note              人类可读说明（一句）
    """

    name: str
    available: bool
    requires_api_key: bool
    runtime: str
    note: str = ""


@dataclass(frozen=True)
class Capability:
    """能力声明（一个能力 = 一组 provider + 状态 + 支持特性 + 降级链）。

    字段：
        name           能力唯一 key（如 "tts", "vision", "matting"）
        providers      provider 列表（按优先级排序，主路径在前）
        status         整体状态（PASS/AVAILABLE/DEGRADED/MISSING）
        supports       该能力支持的具体特性（如 ["word_boundary", "multi_voice"]）
        fallback_tools 可用作 fallback 的工具名（仅列当前 available 的）
        agent_skills   该能力解锁的 agent 技能（OpenMontage 语义标签）
    """

    name: str
    providers: Tuple[Provider, ...]
    status: str
    supports: Tuple[str, ...]
    fallback_tools: Tuple[str, ...]
    agent_skills: Tuple[str, ...]


# ---------------------------------------------------------------------------
# 内部探测函数（惰性 import，避免模块加载时的副作用）
# ---------------------------------------------------------------------------


def _check_faster_whisper() -> bool:
    """faster-whisper 是否已安装（alignment fallback）。"""
    try:
        import faster_whisper  # noqa: F401

        return True
    except ImportError:
        return False


def _check_ffmpeg_available() -> bool:
    """FFmpeg 可执行文件是否可解析（与 doctor 共享 prober 底层逻辑）。

    不直接 import doctor（保持模块独立），但通过 prober._resolve_ffmpeg
    拿到与 doctor 一致的判断结果。
    """
    try:
        from video_director import prober

        exe = prober._resolve_ffmpeg()
        if not exe:
            return False
        # _resolve_ffmpeg 兜底返回 "ffmpeg" 字符串，需验证是否真实存在
        if exe == "ffmpeg":
            return shutil.which("ffmpeg") is not None
        return os.path.isfile(exe)
    except Exception as exc:  # pragma: no cover - 防御性，prober 不应抛
        logger.warning(f"capabilities: prober._resolve_ffmpeg 异常: {exc}")
        return False


def _check_audio_library_nonempty() -> bool:
    """本地音频库是否有可用 BGM/SFX 条目（不阻塞，仅影响 fallback 丰富度）。"""
    try:
        from video_director import audio as audio_mod

        lib = audio_mod.scan_library()
        return any(a.type in ("bgm", "sfx") for a in lib.values())
    except Exception as exc:  # pragma: no cover
        logger.warning(f"capabilities: 音频库扫描异常: {exc}")
        return False


def _has_vision_api_key() -> bool:
    """Vision API key 是否配置（FrameVisionProvider 主路径前提）。"""
    return bool(os.environ.get(VISION_API_KEY_ENV))


# ---------------------------------------------------------------------------
# 能力构造器（每个返回独立 Capability 实例，状态由当前环境决定）
# ---------------------------------------------------------------------------


def _build_tts() -> Capability:
    """tts：旁白合成。主路径 EdgeTTS（零 key，WordBoundary 词级时间戳）。"""
    edge = Provider(
        name="Edge",
        available=True,
        requires_api_key=False,
        runtime="cloud",
        note="edge-tts WordBoundary，零 key 零模型，词级时间戳主路径",
    )
    # EdgeTTS 在当前系统始终视为可用（doctor 单独测网络连通性，capabilities 只声明能力存在性）
    return Capability(
        name="tts",
        providers=(edge,),
        status=STATUS_AVAILABLE,
        supports=("word_boundary", "multi_voice", "rate_control"),
        fallback_tools=(),
        agent_skills=("narration",),
    )


def _build_vision() -> Capability:
    """vision：素材视觉理解。

    主路径 FrameVisionProvider（抽帧 + 视觉 LLM，需 key）；
    fallback ManualProvider（文件名语义 + 手工标注 manifest，零依赖）。

    无 key 时 status=DEGRADED（不是 MISSING）——核心内容匹配靠文件名语义
    零依赖完成，Vision 只是可选增强（项目理念：零 API key 可跑核心链路）。
    """
    has_key = _has_vision_api_key()
    frame_vision = Provider(
        name="FrameVision",
        available=has_key,
        requires_api_key=True,
        runtime="cloud_llm",
        note="抽帧 + OpenAI 兼容视觉 LLM（gpt-4o/Qwen-VL，需 COMPOSER_VISION_API_KEY）",
    )
    manual = Provider(
        name="Manual",
        available=True,  # 文件名语义零依赖，始终可用
        requires_api_key=False,
        runtime="local",
        note="文件名语义 + 手工标注 manifest（零依赖零成本 fallback）",
    )
    providers = (frame_vision, manual)
    if has_key:
        status = STATUS_AVAILABLE
        supports = ("frame_analysis", "narrative_concepts", "scene_matching")
    else:
        status = STATUS_DEGRADED
        supports = ("filename_semantic", "manifest_metadata")
    return Capability(
        name="vision",
        providers=providers,
        status=status,
        supports=supports,
        fallback_tools=("Manual",),  # Manual 始终可用作 fallback
        agent_skills=("asset_understanding", "scene_matching"),
    )


def _build_alignment() -> Capability:
    """alignment：词级对齐。

    主路径 EdgeWordBoundary（TTS 原生时间戳，零额外模型）；
    fallback FasterWhisper（旁白音频转写，需 pip install faster-whisper）。
    """
    fw_available = _check_faster_whisper()
    edge_boundary = Provider(
        name="EdgeWordBoundary",
        available=True,
        requires_api_key=False,
        runtime="tts_native",
        note="edge-tts WordBoundary 原生时间戳（主路径，零额外模型）",
    )
    faster_whisper = Provider(
        name="FasterWhisper",
        available=fw_available,
        requires_api_key=False,
        runtime="local_model",
        note="faster-whisper 词级转写（fallback，惰性加载模型）",
    )
    return Capability(
        name="alignment",
        providers=(edge_boundary, faster_whisper),
        status=STATUS_AVAILABLE,  # EdgeWordBoundary 始终可用
        supports=("word_level_timestamps", "scene_segmentation"),
        fallback_tools=("FasterWhisper",) if fw_available else (),
        agent_skills=("subtitle_sync", "narration_timeline"),
    )


def _build_renderer() -> Capability:
    """renderer：视频渲染合成。唯一主路径 FFmpeg（随包分发，任何电脑可用）。

    分发口径（诚实）：
    - FFmpeg：随安装包内置（lib 包），用户电脑零依赖，恒可用
    - MPT/HyperFrames：开发参考实现，仅存在于开发机，不打包进产品；
      打包分发后这两项不会出现在他人电脑上（也不需要——FFmpeg 覆盖全部
      渲染能力）。此处检测只为开发者提供参考资产位置。
    """
    ffmpeg_ok = _check_ffmpeg_available()
    ffmpeg = Provider(
        name="FFmpeg",
        available=ffmpeg_ok,
        requires_api_key=False,
        runtime="binary",
        note="随包内置视频引擎（任何电脑可用，覆盖全部渲染）",
    )
    mpt_present = False
    try:
        mpt_present = (Path(r"D:\autocut") / "MoneyPrinterTurbo").is_dir()
    except Exception:
        mpt_present = False
    mpt = Provider(
        name="MPT（本机参考）",
        available=mpt_present,
        requires_api_key=False,
        runtime="service",
        note="开发参考实现（仅本机，不打包进产品）",
    )
    hyper = Provider(
        name="HyperFrames（云端参考）",
        available=False,
        requires_api_key=False,
        runtime="cloud",
        note="云端动效渲染服务（付费 API，可选；不配置不影响任何功能）",
    )
    status = STATUS_AVAILABLE if ffmpeg_ok else STATUS_MISSING
    return Capability(
        name="renderer",
        providers=(ffmpeg, mpt, hyper),
        status=status,
        supports=("video_compose", "cut", "slow_zoom_in", "crossfade_pending"),
        fallback_tools=(),
        agent_skills=("render",),
    )


def _build_audio() -> Capability:
    """audio（music/sfx）：BGM 与 SFX 资产。

    LocalLibrary：扫描本地 audio_manifest.json（有则丰富，无则空）；
    SelfGenerated：ffmpeg 合成自产音频（license=self-generated，始终可用）。

    因 SelfGenerated 始终可用，status 恒为 AVAILABLE（不阻塞）。
    """
    has_lib = _check_audio_library_nonempty()
    local_lib = Provider(
        name="LocalLibrary",
        available=has_lib,
        requires_api_key=False,
        runtime="local",
        note="audio_manifest.json 扫描（BGM/SFX 商业可用素材）",
    )
    self_gen = Provider(
        name="SelfGenerated",
        available=True,
        requires_api_key=False,
        runtime="ffmpeg_synth",
        note="ffmpeg 合成自产测试音频（license=self-generated，无版权风险）",
    )
    return Capability(
        name="audio",
        providers=(local_lib, self_gen),
        status=STATUS_AVAILABLE,  # SelfGenerated 始终可用
        supports=("bgm", "sfx"),
        fallback_tools=("SelfGenerated",),
        agent_skills=("bgm_score", "sfx_action"),
    )


def _build_matting() -> Capability:
    """matting：抠图（talking-head 前置能力）。

    ChromaKey：ffmpeg colorkey 绿/蓝幕抠像，零模型依赖，恒可用（限绿幕素材）。
    Rembg/Modnet：人像级模型抠像，未安装（装包后此 provider 自动出现）。
    """
    chroma_available = False
    try:
        from video_director import matting as matting_mod
        chroma_available = matting_mod.get_matting_provider() is not None
    except Exception:
        chroma_available = False
    rembg_available = False
    try:
        import importlib.util as _u
        rembg_available = _u.find_spec("rembg") is not None
    except Exception:
        rembg_available = False

    providers = [
        Provider(
            name="ChromaKey",
            available=chroma_available,
            requires_api_key=False,
            runtime="ffmpeg-colorkey",
            note="绿/蓝幕抠像（零依赖）",
        ),
    ]
    if rembg_available:
        providers.append(Provider(
            name="Rembg",
            available=True,
            requires_api_key=False,
            runtime="rembg-model",
            note="人像级抠像（已安装）",
        ))
    return Capability(
        name="matting",
        providers=tuple(providers),
        status=STATUS_AVAILABLE if chroma_available else STATUS_MISSING,
        supports=("green_screen", "blue_screen") + (("portrait",) if rembg_available else ()),
        fallback_tools=("ChromaKey",),
        agent_skills=("portrait_segmentation", "talking_head"),
    )


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

#: 已注册能力名（用于校验 preflight 输入）
REGISTERED_CAPS = ("tts", "vision", "alignment", "renderer", "audio", "matting")


def registry() -> Dict[str, Capability]:
    """返回完整能力表。每次调用重新探测环境（保证 freshness）。

    成本：内部惰性 import + 轻量探测（shutil.which / os.environ / try import），
    无网络/磁盘重操作。可在 pipeline 启动前任意调用。

    Returns:
        dict[str, Capability]——key 为能力名，value 为 Capability 声明
    """
    return {
        "tts": _build_tts(),
        "vision": _build_vision(),
        "alignment": _build_alignment(),
        "renderer": _build_renderer(),
        "audio": _build_audio(),
        "matting": _build_matting(),
    }


def is_capable(cap_name: str) -> bool:
    """该能力是否可用（status != MISSING）。

    DEGRADED 视为"可用"——主路径缺失但 fallback 可工作，任务可执行（质量降级）。
    """
    caps = registry()
    cap = caps.get(cap_name)
    if cap is None:
        logger.warning(f"is_capable: 未知能力名 '{cap_name}'")
        return False
    return cap.status != STATUS_MISSING


def degradation_explanation(cap_name: str) -> str:
    """返回该能力的降级/缺失原因（人类可读）。

    AVAILABLE：说明主路径可用，无需降级。
    DEGRADED：说明主路径为何缺失 + fallback 如何替代。
    MISSING：说明为何阻塞 + 哪类任务受影响。
    """
    caps = registry()
    cap = caps.get(cap_name)
    if cap is None:
        return f"未知能力: {cap_name}（合法名: {', '.join(REGISTERED_CAPS)}）"

    if cap.status == STATUS_PASS:
        return f"{cap_name}: 主路径与全部 fallback 就绪（PASS）"

    if cap.status == STATUS_AVAILABLE:
        available_names = [p.name for p in cap.providers if p.available]
        return f"{cap_name}: 主路径就绪（{', '.join(available_names)}），无需降级"

    if cap.status == STATUS_DEGRADED:
        if cap_name == "vision":
            return (
                "vision: 视觉 LLM key 未配置（COMPOSER_VISION_API_KEY），主路径 "
                "FrameVision 不可用；降级为文件名语义匹配 + metadata manifest "
                "（零依赖零成本，核心内容匹配仍可完成，仅丢失画面级理解精度）"
            )
        # 通用降级说明
        unavailable = [p.name for p in cap.providers if not p.available]
        fallback = list(cap.fallback_tools)
        return (
            f"{cap_name}: 主路径 {', '.join(unavailable)} 不可用；"
            f"降级到 fallback: {', '.join(fallback) if fallback else '无'}"
        )

    if cap.status == STATUS_MISSING:
        if cap.providers:
            names = [p.name for p in cap.providers]
            return (
                f"{cap_name}: 全部 provider 不可用（{', '.join(names)}），"
                f"任务 BLOCKED——影响 agent_skills: {', '.join(cap.agent_skills)}"
            )
        return (
            f"{cap_name}: 无已注册 provider，任务 BLOCKED"
            f"——影响 agent_skills: {', '.join(cap.agent_skills) if cap.agent_skills else '无'}"
        )

    return f"{cap_name}: 状态 {cap.status}（无解释模板）"


def preflight(
    required_caps: List[str],
) -> Tuple[bool, List[str], List[str]]:
    """任务级能力 gate：检查每条 pipeline 所需能力，返回阻塞项与降级项。

    用法：
        # script-media pipeline 需要 tts + renderer
        ok, blocked, degraded = preflight(["tts", "renderer"])
        if not ok:
            raise RuntimeError(f"能力缺失，任务阻塞: {blocked}")

        # talking-head pipeline 需要 matting（当前系统无）
        ok, blocked, degraded = preflight(["tts", "renderer", "matting"])
        # → ok=False, blocked=["matting"]

        # vision 增强任务（无 key 时降级，不阻塞）
        ok, blocked, degraded = preflight(["tts", "renderer", "vision"])
        # → ok=True, degraded=["vision"]

    Args:
        required_caps: 该任务所需的能力名列表（须在 REGISTERED_CAPS 中）

    Returns:
        (all_ok, blocked, degraded):
        - all_ok:  True 当且仅当 blocked 为空
        - blocked: status=MISSING 的能力（任务不可执行）
        - degraded: status=DEGRADED 的能力（任务可执行但质量降级）
    """
    caps = registry()
    blocked: List[str] = []
    degraded: List[str] = []

    for cap_name in required_caps:
        cap = caps.get(cap_name)
        if cap is None:
            blocked.append(cap_name)
            logger.warning(
                f"preflight: 未知能力名 '{cap_name}'"
                f"（合法名: {', '.join(REGISTERED_CAPS)}）"
            )
            continue
        if cap.status == STATUS_MISSING:
            blocked.append(cap_name)
            logger.info(
                f"preflight [BLOCKED] {cap_name}: {degradation_explanation(cap_name)}"
            )
        elif cap.status == STATUS_DEGRADED:
            degraded.append(cap_name)
            logger.info(
                f"preflight [DEGRADED] {cap_name}: {degradation_explanation(cap_name)}"
            )
        # PASS / AVAILABLE 不记入 blocked/degraded

    all_ok = len(blocked) == 0
    if all_ok:
        logger.info(
            f"preflight PASS: required={required_caps}"
            f" degraded={degraded or 'none'}"
        )
    else:
        logger.warning(
            f"preflight FAIL: required={required_caps}"
            f" blocked={blocked} degraded={degraded}"
        )
    return all_ok, blocked, degraded


def capability_report() -> str:
    """生成人类可读的多行能力报告。

    格式：每行 [STATUS] cap_name: providers — supports — fallback — note
    末尾汇总 blocked/degraded 数。
    """
    caps = registry()
    lines: List[str] = [
        "AI Video Director 能力注册表（Capability Catalog）",
        "=" * 60,
    ]

    blocked_count = 0
    degraded_count = 0

    for cap_name in REGISTERED_CAPS:
        cap = caps[cap_name]
        # provider 摘要
        if cap.providers:
            prov_parts = []
            for p in cap.providers:
                flag = "可" if p.available else "不可"
                key_tag = "需key" if p.requires_api_key else "无key"
                prov_parts.append(f"{p.name}({flag},{key_tag})")
            prov_str = ", ".join(prov_parts)
        else:
            prov_str = "无 provider"

        supports_str = ", ".join(cap.supports) if cap.supports else "—"
        fallback_str = ", ".join(cap.fallback_tools) if cap.fallback_tools else "—"

        lines.append(
            f"[{cap.status:<8}] {cap_name}: {prov_str}"
            f" | supports: {supports_str}"
            f" | fallback: {fallback_str}"
        )

        if cap.status == STATUS_MISSING:
            blocked_count += 1
        elif cap.status == STATUS_DEGRADED:
            degraded_count += 1

    lines.append("=" * 60)
    summary_parts = []
    if blocked_count:
        summary_parts.append(f"{blocked_count} 项 MISSING（阻塞）")
    if degraded_count:
        summary_parts.append(f"{degraded_count} 项 DEGRADED（降级可用）")
    available_count = len(REGISTERED_CAPS) - blocked_count - degraded_count
    summary_parts.append(f"{available_count} 项 AVAILABLE/PASS（就绪）")
    lines.append(" | ".join(summary_parts))

    if blocked_count:
        blocked_names = [
            n for n in REGISTERED_CAPS if caps[n].status == STATUS_MISSING
        ]
        lines.append(f"BLOCKED 任务: 需要 {blocked_names} 的任务不可执行")
    if degraded_count:
        degraded_names = [
            n for n in REGISTERED_CAPS if caps[n].status == STATUS_DEGRADED
        ]
        for n in degraded_names:
            lines.append(f"DEGRADED {n}: {degradation_explanation(n)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 模块自检（python -m video_director.capabilities 时打印报告）
# ---------------------------------------------------------------------------


def _main() -> None:  # pragma: no cover
    print(capability_report())


if __name__ == "__main__":  # pragma: no cover
    _main()
