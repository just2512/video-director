"""Run Manifest（运行审计，借 jianying-headless 固定运行环境记录思想）。

每次成片保存 run.json：输入 hash / providers / 版本 / 验证结果。
回答"这一版和上一版为什么不一样 / 当时为什么能生成"。

失败二分（RETRYABLE / BLOCKED，jianying §12）：
    RETRYABLE：瞬态 ffmpeg/网络错误 → 修复循环可重试
    BLOCKED：素材不存在/能力缺失/计划非法 → 停止并报告，禁止盲目重试
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from loguru import logger

from video_director import config, prober
from video_director import COMPOSER_VERSION as VD_VERSION


class FailureKind(str, Enum):
    RETRYABLE = "RETRYABLE"   # 瞬态：可重试
    BLOCKED = "BLOCKED"        # 结构性：停止并报告


@dataclass
class Failure:
    kind: FailureKind
    stage: str
    message: str


def classify_failure(stage: str, exc: Exception) -> Failure:
    """失败二分。渲染器/网络的瞬态错误 RETRYABLE；素材/能力/校验类 BLOCKED。"""
    from video_director import models, pipeline, renderer, vision

    msg = str(exc)[:500]
    if isinstance(exc, (models.MissingAssetError, pipeline.PipelineError,
                        vision.VisionError)):
        # MissingAsset/Pipeline/Vision 属结构性；但 PipelineError 包渲染失败
        # 的情况在 renderer 层已分类，此处 PipelineError 默认 BLOCKED，
        # render 段的 RenderError 单独走下一条
        kind = FailureKind.BLOCKED
        if isinstance(exc, pipeline.PipelineError) and "QC 失败" in msg:
            kind = FailureKind.RETRYABLE  # QC 失败进修复循环（上限内）
        return Failure(kind, stage, msg)
    if isinstance(exc, renderer.RenderError):
        # ffmpeg 超时/瞬态 exit → RETRYABLE；文件级错误（素材坏/不存在）→ BLOCKED
        transient = "超时" in msg or "timed out" in msg.lower()
        return Failure(
            FailureKind.RETRYABLE if transient else FailureKind.BLOCKED, stage, msg
        )
    # 网络类（edge-tts 连接失败）→ RETRYABLE；其余未知默认 BLOCKED（不盲目重试）
    net_hint = any(k in msg.lower() for k in ("connection", "timeout", "network", "暂时"))
    return Failure(FailureKind.RETRYABLE if net_hint else FailureKind.BLOCKED, stage, msg)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()[:16]}"


def _ffmpeg_version() -> str:
    exe = prober._resolve_ffmpeg()
    if not exe or not Path(exe).is_file() and not subprocess_exifies(exe):
        return "unknown"
    try:
        r = subprocess.run([exe, "-version"], capture_output=True, timeout=10)
        return (r.stdout or b"").decode(errors="replace").splitlines()[0].split("version")[-1].strip()[:40]
    except Exception:
        return "unknown"


def subprocess_exifies(exe: Optional[str]) -> bool:
    import shutil
    return bool(exe) and shutil.which(exe) is not None


def build_run_manifest(
    project_id: str,
    workflow: str,
    script_path: str,
    asset_paths: list,
    providers: dict,
    verification: dict,
    artifact_path: str,
    warnings: list,
) -> dict:
    """构造 run manifest（V2 §21-21 + jianying-headless run 记录）。"""
    manifest = {
        "run_id": f"{time.strftime('%Y%m%d')}-{project_id}",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "workflow": workflow,
        "input": {
            "script": _sha256_file(script_path) if Path(script_path).is_file() else None,
            "assets": [_sha256_file(p) for p in asset_paths if Path(p).is_file()],
        },
        "providers": providers,
        "runtime": {
            "video_director": VD_VERSION,
            "ffmpeg": _ffmpeg_version(),
            "python": platform.python_version(),
            "platform": platform.platform()[:60],
        },
        "verification": verification,
        "artifact": artifact_path,
        "warnings": warnings,
    }
    return manifest


def save_run_manifest(base_dir: Path, manifest: dict) -> Path:
    """落盘 <项目>/plans/run.json（幂等覆盖，每次运行一条最新记录）。"""
    plans = base_dir / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    out = plans / "run.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"run manifest saved: {out}")
    return out
