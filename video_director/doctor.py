"""vd doctor（环境自检，借 jianying-headless doctor 思想）。

启动/生成前回答"为什么今天突然不能渲染"。三态输出：
    PASS  可用
    SKIP  可选能力未配置（不阻塞）
    FAIL  必需能力缺失（阻塞，生成前就该失败而不是渲染一半报错）

检查清单（按依赖必需性）：
    必需：FFmpeg / EdgeTTS 网络可达 / 磁盘空间
    可选：ffprobe（有 stderr 兜底）/ VisionProvider（视觉 LLM 未配置→SKIP）
          / faster-whisper（对齐 fallback）/ 音频库
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List

from loguru import logger

from video_director import audio as audio_mod, config, prober

#: 可用磁盘下限（GB）——渲染 60s 1080p 成片约需 0.5GB，留 10 倍余量
MIN_FREE_GB = 5.0

#: EdgeTTS 真实合成探测文案（2026-10-12 事故教训：TCP 连通 PASS 但合成
#: 403 已死——连通性检查探测不到端点封禁，必须真合成一次拿音频字节）。
EDGE_TTS_PROBE_TEXT = "环境自检"


@dataclass
class CheckResult:
    name: str
    status: str  # PASS | SKIP | FAIL
    detail: str
    # 可选能力的 FAIL（如 vision 误配纯文本模型）只亮黄灯不阻塞主链：
    # pipeline/script_only/product_promo/image_editor 都不依赖视觉，
    # 误配一个视觉模型不该让整条流水线停摆（2026-10-17 对抗审查 D1）。
    optional: bool = False


def _resolve_ffmpeg_executable() -> str | None:
    """返回可执行 ffmpeg 的真实路径（含 PATH 兜底验证），找不到返回 None。"""
    exe = prober._resolve_ffmpeg()
    if exe and (Path(exe).is_file() or shutil.which(exe)):
        return exe
    return shutil.which("ffmpeg")


def _check_ffmpeg() -> CheckResult:
    exe = _resolve_ffmpeg_executable()
    if exe is None:
        return CheckResult("FFmpeg", "FAIL", "未找到可执行文件")
    import subprocess
    try:
        r = subprocess.run([exe, "-version"], capture_output=True, timeout=10)
        if r.returncode != 0:
            return CheckResult("FFmpeg", "FAIL", f"exit={r.returncode}")
        ver = (r.stdout or b"").decode(errors="replace").splitlines()[0][:60]
        return CheckResult("FFmpeg", "PASS", ver)
    except Exception as exc:
        return CheckResult("FFmpeg", "FAIL", str(exc)[:80])


def _check_ffprobe() -> CheckResult:
    exe = prober._resolve_ffprobe()
    if exe:
        return CheckResult("ffprobe", "PASS", Path(exe).name)
    return CheckResult("ffprobe", "SKIP", "未找到，将用 ffmpeg -i stderr 解析兜底")


def _check_edge_tts() -> CheckResult:
    """真实合成探测：调一次 edge-tts 拿到音频字节才算 PASS。

    2026-10-12 事故：旧实现只 TCP 连 443，微软封端点后连通仍 PASS，
    跑片才在 narration 节点炸。合成路径 = 唯一可信的检查面。
    """
    import tempfile
    import video_director  # 触发 __init__ 端点补丁

    try:
        with tempfile.TemporaryDirectory(prefix="vd-doctor-tts-") as td:
            out = Path(td) / "probe.mp3"
            import video_director.narration as vd_narration
            vd_narration.synth_scene_audio(
                EDGE_TTS_PROBE_TEXT,
                "zh-CN-XiaoxiaoNeural",
                str(out),
                "+0%",
            )
            size = out.stat().st_size if out.is_file() else 0
            if size > 0:
                return CheckResult("TTS: EdgeTTS", "PASS",
                                   f"真实合成 {size}B（端点补丁生效）")
            return CheckResult("TTS: EdgeTTS", "FAIL", "合成返回 0 字节音频")
    except Exception as exc:
        return CheckResult("TTS: EdgeTTS", "FAIL", f"合成失败: {str(exc)[:120]}")


def _check_llm() -> CheckResult:
    """LLM 真实探测：配了 key 就真实 generate 一次（2026-10-13 报障教训：
    key 配置存在 + openai 包缺失 → get_llm() 曾在 scene_plan 炸死整个 run。
    连通性/存在性检查探测不到这类故障，必须真实调用拿回文本）。

    - 未配 key → SKIP（规则路径是完整产品能力，不算故障）
    - key 配了但包缺失 → FAIL（带 pip install 指引）
    - key 配了但端点/密钥坏 → FAIL（带真实异常）
    """
    from video_director import llm_provider

    cfg = llm_provider.get_llm_config()
    if not cfg.get("enabled"):
        return CheckResult("LLM", "SKIP", "未配置（消费点走规则路径）")
    provider = llm_provider.get_llm()
    if provider is None:
        return CheckResult(
            "LLM", "FAIL",
            "key 已配置但 Provider 构造失败（多半是 openai 库未安装）："
            "pip install -r requirements.txt",
        )
    try:
        text = provider.generate("环境自检：请只回复 OK", system="只输出 OK 两个字母。")
        text = (text or "").strip()
        if text:
            return CheckResult("LLM", "PASS",
                               f"真实调用成功（model={provider.model}，回复 {len(text)} 字）")
        return CheckResult("LLM", "FAIL", "端点返回空文本")
    except Exception as exc:
        return CheckResult("LLM", "FAIL", f"真实调用失败: {str(exc)[:120]}")


def _check_vision() -> CheckResult:
    """Vision 是可选增强：不配 key 完全不影响使用，核心内容匹配靠
    文件名语义 + 概念桥接（零依赖零成本）。

    2026-10-17 生产事故修复：检测源必须与运行时同源——运行时
    FrameVisionProvider 读 model_config（env 只是兜底），本检查却只查 env，
    导致用户前端配好 vision、doctor 却报「未配置」的自相矛盾。改为同读
    model_config + provider_matrix，纯文本模型误配（MiniMax-M3→400 白烧）
    在这里直接亮出来。
    """
    try:
        from video_director import model_config, provider_matrix
        cfg = model_config.get_role_config("vision")
        if not str(cfg.get("api_key") or "").strip():
            # env 兜底（与 FrameVisionProvider.__init__ 同序：model_config 优先，env 次之）
            import os
            if not os.environ.get("COMPOSER_VISION_API_KEY"):
                return CheckResult("Vision 增强（可选）", "SKIP",
                                   "未配置——不影响使用。核心匹配已用文件名语义零依赖完成；"
                                   "在设置 → 模型里配视觉角色可升级为视觉级理解")
        st = provider_matrix.state("vision", "vision.frame_analyze", cfg)
        if st.status == provider_matrix.READY:
            return CheckResult("Vision 增强（可选）", "PASS", str(cfg.get("model") or ""))
        # NOT_READY：纯文本模型误配等，如实亮出原因
        return CheckResult("Vision 增强（可选）", "FAIL", st.reason or "视觉能力不可用", optional=True)
    except Exception as exc:
        return CheckResult("Vision 增强（可选）", "FAIL", f"检测异常: {str(exc)[:120]}", optional=True)


def _check_whisper() -> CheckResult:
    try:
        import faster_whisper  # noqa: F401
        return CheckResult("Alignment: faster-whisper", "PASS", "已安装（fallback 可用）")
    except ImportError:
        return CheckResult("Alignment: faster-whisper", "SKIP",
                           "未安装（EdgeTTS 词级时间戳是主路径，不影响）")


def _check_audio_library() -> CheckResult:
    lib = audio_mod.scan_library()
    bgm = [a for a in lib.values() if a.type == "bgm"]
    sfx = [a for a in lib.values() if a.type == "sfx"]
    if bgm or sfx:
        return CheckResult("音频库", "PASS", f"BGM×{len(bgm)} SFX×{len(sfx)}（含许可台账）")
    return CheckResult("音频库", "SKIP", "空库（BGM/SFX 不可用，成片仅旁白+字幕）")


def _check_disk() -> CheckResult:
    try:
        usage = shutil.disk_usage(str(config.composer_storage_root()))
        free_gb = usage.free / 1024 ** 3
        ok = free_gb >= MIN_FREE_GB
        return CheckResult("磁盘空间", "PASS" if ok else "FAIL",
                           f"剩余 {free_gb:.1f}GB（下限 {MIN_FREE_GB}GB）")
    except Exception as exc:
        return CheckResult("磁盘空间", "FAIL", str(exc)[:60])


def run_doctor() -> List[CheckResult]:
    """跑全部检查，返回结果列表。含 FAIL 项时调用方应停止生成（capability gate）。"""
    results = [
        _check_ffmpeg(),
        _check_ffprobe(),
        _check_edge_tts(),
        _check_llm(),
        _check_vision(),
        _check_whisper(),
        _check_audio_library(),
        _check_disk(),
    ]
    for r in results:
        logger.info(f"doctor [{r.status}] {r.name}: {r.detail}")
    return results


def format_doctor_report(results: List[CheckResult]) -> str:
    lines = ["AI Video Director 环境自检", "=" * 44]
    for r in results:
        lines.append(f"[{r.status:^4}] {r.name}: {r.detail}")
    fails = [r for r in results if r.status == "FAIL"]
    lines.append("=" * 44)
    if fails:
        lines.append(f"[X] {len(fails)} 项必需能力缺失，生成将被阻塞：")
        lines.extend(f"  - {r.name}: {r.detail}" for r in fails)
    else:
        lines.append("[OK] 必需能力全部就绪（SKIP 为可选能力，不阻塞）")
    return "\n".join(lines)


def has_blocking_failures(results: List[CheckResult]) -> bool:
    # optional=True 的 FAIL（vision 误配等）不阻塞：主链四条流水线均不依赖视觉。
    return any(r.status == "FAIL" and not r.optional for r in results)
