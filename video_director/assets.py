"""Asset Manifest 构建（V1 §25：第一版只做 ffprobe，不做 AI 看视频）。

Mode A/B：文件名排序即场景对应顺序（scene-001 ↔ S01），
图片排在视频之后作为补时候选（§16 优先级）。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from loguru import logger

from video_director.models import AssetRef
from video_director import prober

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


class AssetError(Exception):
    pass


def classify(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    raise AssetError(f"不支持的素材类型 {ext}: {file_path}")


def _unique_id(stem: str, used: set) -> str:
    if stem not in used:
        used.add(stem)
        return stem
    n = 2
    while f"{stem}-{n}" in used:
        n += 1
    used.add(f"{stem}-{n}")
    return f"{stem}-{n}"


def _library_semantics(path: str) -> dict:
    """从 Asset Library 取回已写入的语义（description/tags）。

    上传素材入库时只有 Library 台账记着它的语义；素材清单若不在这里回落，
    这些素材到 analyze_assets 时永远是「无描述」→ LLM 匹配拿到空名册 →
    顺序兜底。任何失败都静默返回空（语义回落不能炸主流程）。
    """
    try:
        from video_director import asset_catalog
        entry = asset_catalog.find_by_path(path)
    except Exception as exc:
        logger.debug(f"library 语义回落失败 {path}: {exc}")
        return {}
    if not entry:
        return {}
    return {
        "description": entry.get("description") or "",
        "keywords": list(entry.get("tags") or []),
    }


def _asset_meta(path: str, metadata: Dict[str, dict]) -> dict:
    """合并素材元数据：Library 已登记语义 < 调用方显式 metadata（后者优先）。

    调用方显式给的 source/license_note 是可信上下文的覆盖，必须赢；
    Library 只补语义字段。
    """
    meta: dict = dict(_library_semantics(path))
    meta.update(metadata.get(path) or {})
    return meta


def build_manifest(
    video_files: List[str],
    image_files: List[str],
    license_note: str = "user-provided",
    asset_metadata: Optional[Dict[str, dict]] = None,
) -> Tuple[Dict[str, AssetRef], List[str]]:
    """探测全部素材，返回 (assets dict, 使用顺序 list)。

    顺序 = 视频按文件名排序 + 图片按文件名排序（Mode A 文件名对应 / Mode B 上传顺序
    由调用方传入已排序的列表即可）。
    """
    assets: Dict[str, AssetRef] = {}
    order: List[str] = []
    used_ids: set = set()
    metadata = asset_metadata or {}

    for path in list(video_files) + list(image_files):
        if not os.path.isfile(path):
            raise AssetError(f"素材不存在: {path}")
        kind = classify(path)
        stem = os.path.splitext(os.path.basename(path))[0]
        asset_id = _unique_id(stem, used_ids)
        try:
            if kind == "video":
                probe = prober.probe_media(path)
                if not probe.has_video_stream:
                    raise AssetError(f"视频文件无视频流: {path}")
                if probe.duration is None or probe.duration <= 0:
                    raise AssetError(f"视频时长无效: {path}")
                meta = _asset_meta(path, metadata)
                assets[asset_id] = AssetRef(
                    id=asset_id, type="video", file=path,
                    duration=probe.duration, width=probe.width,
                    height=probe.height, fps=probe.fps,
                    prompt=meta.get("prompt") or None,
                    description=meta.get("description") or None,
                    keywords=list(meta.get("keywords") or []),
                    source=meta.get("source", "user"),
                    license_note=meta.get("license_note", license_note),
                    provenance=meta.get("provenance"),
                    rights_status=meta.get("rights_status"),
                )
            else:
                probe = prober.probe_image(path)
                meta = _asset_meta(path, metadata)
                assets[asset_id] = AssetRef(
                    id=asset_id, type="image", file=path,
                    width=probe.width, height=probe.height,
                    prompt=meta.get("prompt") or None,
                    description=meta.get("description") or None,
                    keywords=list(meta.get("keywords") or []),
                    source=meta.get("source", "user"),
                    license_note=meta.get("license_note", license_note),
                    provenance=meta.get("provenance"),
                    rights_status=meta.get("rights_status"),
                )
        except prober.ProbeError as exc:
            logger.error(f"素材探测失败 {path}: {exc}")
            raise AssetError(f"素材探测失败: {path}") from exc
        order.append(asset_id)
        logger.info(f"asset {asset_id} [{kind}] <- {os.path.basename(path)}")

    return assets, order
