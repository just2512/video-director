"""VisionProvider：素材视觉理解（按《视频理解》方案 2026-09-18）。

Asset Understanding 六级优先链（§23）：
    P1 已有语义 metadata（AssetRef.prompt/description 非空）
    P2 generation prompt / scene metadata
    P3 NativeVideoProvider（Qwen/豆包/混元原生视频 API，预留）
    P4 FrameVisionProvider（抽帧 + 任意视觉 LLM，主路径）
    P5 ManualProvider（手工标注 manifest fallback）
    → MISSING / NEEDS_REVIEW（诚实降级，禁止编造描述）

VisionProvider 只给 Asset Director 提供"眼睛"（§14），不做匹配决策。
manifest 叙事语义字段（§12）：narrative_concepts / usable_for。
"""

from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from video_director import prober
from video_director.models import AssetRef


class VisionError(Exception):
    pass


# ---------------------------------------------------------------------------
# 抽帧（§八：默认 4 帧/8s ≈ 0.5fps；动作理解升 8 帧）
# ---------------------------------------------------------------------------

def extract_frames(
    video_file: str,
    out_dir: Path,
    frames: int = 4,
) -> List[str]:
    """均匀抽 N 帧存 jpg（宽 480，控制 token/带宽）。返回帧路径列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    probe = prober.probe_media(video_file)
    dur = probe.duration or 0
    if dur <= 0:
        raise VisionError(f"无法抽帧（无时长）: {video_file}")
    stem = Path(video_file).stem
    paths: List[str] = []
    ff = prober._resolve_ffmpeg()
    for i in range(frames):
        # 采样点落在 5%~95% 区间避免首尾黑帧/fade
        t = dur * 0.05 + (dur * 0.9) * (i + 0.5) / frames
        out = str(out_dir / f"{stem}_f{i + 1}.jpg")
        r = subprocess.run(
            [ff, "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", video_file,
             "-frames:v", "1", "-vf", "scale=480:-2", out],
            capture_output=True, text=True, errors="replace", timeout=60,
        )
        if r.returncode != 0:
            logger.warning(f"抽帧失败 {video_file}@{t:.1f}s: {r.stderr[-200:]}")
            continue
        if Path(out).is_file():
            paths.append(out)
    if not paths:
        raise VisionError(f"全部抽帧失败: {video_file}")
    return paths


# ---------------------------------------------------------------------------
# Provider 统一接口
# ---------------------------------------------------------------------------

class VisionProvider(ABC):
    """analyze(asset) → 语义清单条目。失败抛 VisionError（调用方逐级降级）。"""

    @abstractmethod
    def available(self) -> bool:
        ...

    @abstractmethod
    def analyze(self, asset: AssetRef, frame_dir: Path) -> dict:
        """返回 {description, tags, narrative_concepts, usable_for}（§12 字段）。"""
        ...


ANALYZE_PROMPT = """你是视频素材标注员。以下是一个视频素材的{frame_count}个关键帧（均匀采样）。

请输出 JSON（不要 markdown 代码块，不要解释）：
{{
  "description": "一句话中文描述画面主体/场景/氛围",
  "tags": ["5-10个中文关键词，用于与中文文案匹配"],
  "narrative_concepts": ["叙事语义概念：如 职场压力/悬疑/历史感/转折/收尾"],
  "usable_for": ["适用叙事位置：hook/problem/development/turning_point/conclusion/transition 中选0-3个"]
}}"""


class FrameVisionProvider(VisionProvider):
    """抽帧 + OpenAI 兼容视觉 LLM（§20 第一选择：不新增本地依赖，模型解耦）。

    配置（多模型配置中心 model_config 角色 "vision"，环境变量兜底）：
        前端「大模型设置」视觉角色   优先
        COMPOSER_VISION_API_KEY      环境变量回退
        COMPOSER_VISION_BASE_URL     默认 https://api.openai.com/v1（兼容 Claude/Qwen 中转）
        COMPOSER_VISION_MODEL        默认 gpt-4o（任何多模态模型 id）
    """

    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        import os

        # 多模型配置中心（前端可配）优先，环境变量兜底
        mc_cfg = {"api_key": "", "base_url": "", "model": ""}
        try:
            from video_director import model_config
            mc = model_config.get_role_config("vision")
            mc_cfg = {"api_key": mc.get("api_key", "") or "",
                      "base_url": mc.get("base_url", "") or "",
                      "model": mc.get("model", "") or ""}
        except Exception:
            pass
        self._api_key = (
            api_key or mc_cfg["api_key"]
            or os.environ.get("COMPOSER_VISION_API_KEY", "")
        )
        self._base_url = (
            base_url or mc_cfg["base_url"]
            or os.environ.get("COMPOSER_VISION_BASE_URL", "https://api.openai.com/v1")
        ).rstrip("/")
        self._model = (
            model or mc_cfg["model"]
            or os.environ.get("COMPOSER_VISION_MODEL", "gpt-4o")
        )

    def available(self) -> bool:
        try:
            from video_director import provider_matrix
            cfg = {"api_key": self._api_key, "base_url": self._base_url, "model": self._model}
            return provider_matrix.state("vision", "vision.frame_analyze", cfg).status == provider_matrix.READY
        except Exception:
            return bool(self._api_key)

    def analyze(self, asset: AssetRef, frame_dir: Path) -> dict:
        import base64
        import urllib.request

        try:
            from video_director import provider_matrix
            provider_matrix.require("vision", "vision.frame_analyze", {
                "api_key": self._api_key, "base_url": self._base_url, "model": self._model
            })
        except provider_matrix.ProviderCapabilityError as exc:
            raise VisionError(str(exc)) from exc
        frames = extract_frames(asset.file, frame_dir / asset.id, frames=4)
        content = [{"type": "text", "text": ANALYZE_PROMPT.format(frame_count=len(frames))}]
        for fp in frames:
            b64 = base64.b64encode(Path(fp).read_bytes()).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
            })
        payload = json.dumps({
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 500,
        }).encode()
        req = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        # 2026-10-20 用户裁决统一策略：LLM 调用失败 10 次重试、单次超时
        # ≥3 分钟（此前 2 次/120s 生产显示经常边缘超时）。
        data = None
        last_exc: Optional[Exception] = None
        max_attempts = 10
        for attempt in range(1, max_attempts + 1):
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = json.loads(resp.read().decode())
                break
            except Exception as exc:
                last_exc = exc
                if attempt < max_attempts:
                    import time as _time
                    _wait = min(10.0 * attempt, 60.0)
                    logger.warning(
                        f"视觉 LLM 调用第 {attempt}/{max_attempts} 次失败"
                        f"（{_wait:.0f}s 后重试）: {exc}")
                    _time.sleep(_wait)
        if data is None:
            raise VisionError(f"视觉 LLM 调用失败（重试 {max_attempts} 次后）: {last_exc}") from last_exc
        # 防三连：choices 空（网关错误体）→ IndexError；content 为 None（安全
        # 过滤/截断）→ NoneType.find；花括号切片非 JSON → 裸 json.loads 崩。
        # 全部换成带原始响应摘要的诚实报错，调用方仍按 provider 失败降级。
        choices = data.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else None
        if content is None or not str(content).strip():
            err = data.get("error")
            detail = f"：{str(err)[:160]}" if err else f"（响应键：{sorted(data.keys())[:6]}）"
            raise VisionError(f"视觉 LLM 返回空内容{detail}")
        raw = str(content).strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise VisionError(f"视觉 LLM 输出无 JSON: {raw[:200]}")
        try:
            parsed = json.loads(raw[start : end + 1])
        except (json.JSONDecodeError, ValueError) as exc:
            raise VisionError(f"视觉 LLM 输出 JSON 解析失败: {raw[:120]}") from exc
        return {
            "description": parsed.get("description", ""),
            "tags": parsed.get("tags", []),
            "narrative_concepts": parsed.get("narrative_concepts", []),
            "usable_for": parsed.get("usable_for", []),
        }


class NativeVideoProvider(VisionProvider):
    """原生视频理解 API（Qwen/豆包/混元，§B 类）。预留接口，第二阶段实现。"""

    def available(self) -> bool:
        return False  # 未实现（P3 预留位）

    def analyze(self, asset: AssetRef, frame_dir: Path) -> dict:
        raise VisionError("NativeVideoProvider 未实现（第二阶段）")


class ManualProvider(VisionProvider):
    """手工标注 fallback（§P5）：读 assets_semantic.json 形式的标注文件。"""

    def __init__(self, manifest_path: str):
        self._path = Path(manifest_path)

    def available(self) -> bool:
        return self._path.is_file()

    def analyze(self, asset: AssetRef, frame_dir: Path) -> dict:
        data = json.loads(self._path.read_text(encoding="utf-8"))
        # 支持两种结构：{assets:[{id,...}]} 或 {v1:{...}}
        entry = None
        for a in data.get("assets", []):
            if a.get("id") == asset.id or a.get("file") == Path(asset.file).name:
                entry = a
                break
        if entry is None and asset.id in data:
            entry = data[asset.id]
        if entry is None:
            raise VisionError(f"手工标注中无 {asset.id}")
        return {
            "description": entry.get("description", ""),
            "tags": entry.get("tags", []),
            "narrative_concepts": entry.get("narrative_concepts", []),
            "usable_for": entry.get("usable_for", []),
        }


# ---------------------------------------------------------------------------
# 主管道：六级优先链（§23）
# ---------------------------------------------------------------------------

def analyze_assets(
    assets: Dict[str, AssetRef],
    work_dir: Path,
    manual_manifest: Optional[str] = None,
    vision_provider: Optional[VisionProvider] = None,
) -> Dict[str, AssetRef]:
    """按优先链为每个资产补语义。返回增强后的资产表（原表不变，返回新表）。

    P1/P2: AssetRef 已有 prompt/description 直接用（不重复调用视觉模型，V1 §7）
    P3/P4: vision_provider（调用方注入 FrameVisionProvider 或 Native）
    P5:    manual_manifest 文件
    全失败: description 留空 → matcher 走顺序 fallback 并记 warning（不编造）
    """
    enhanced = {aid: a.model_copy(deep=True) for aid, a in assets.items()}
    frame_dir = work_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)

    providers: List[VisionProvider] = []
    if vision_provider is not None and vision_provider.available():
        providers.append(vision_provider)
    if manual_manifest:
        mp = ManualProvider(manual_manifest)
        if mp.available():
            providers.append(mp)

    for aid, asset in enhanced.items():
        # P1/P2 已有语义
        if asset.prompt or asset.description:
            logger.info(f"asset {aid}: 已有语义（P1/P2），跳过视觉分析")
            continue
        if asset.type != "video":
            continue  # 图片标签由 matcher 独立处理（第一版图片按顺序兜底）
        analyzed = False
        for provider in providers:
            try:
                sem = provider.analyze(asset, frame_dir)
                asset.description = sem["description"]
                asset.keywords = list(dict.fromkeys(asset.keywords + sem["tags"]))
                # 叙事语义写入 AssetRef 扩展字段（license_note 同构，塞 JSON 不污染核心模型）
                asset.license_note = asset.license_note or ""
                asset.prompt = asset.prompt or sem.get("description")
                logger.info(f"asset {aid}: {type(provider).__name__} → {sem['description'][:40]}")
                analyzed = True
                break
            except Exception as exc:
                # 任意失败（429 余额不足/限流/网络/解析）都降级，不炸主流程
                logger.warning(f"asset {aid}: {type(provider).__name__} 失败: {exc}")
        if not analyzed:
            logger.warning(f"asset {aid}: 全部 VisionProvider 不可用，语义缺失（顺序兜底）")
    return enhanced
