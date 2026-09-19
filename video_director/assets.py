"""Asset Manifest 构建（V1 §25：第一版只做 ffprobe，不做 AI 看视频）。

Mode A/B：文件名排序即场景对应顺序（scene-001 ↔ S01），
图片排在视频之后作为补时候选（§16 优先级）。
"""

from __future__ import annotations

import os
from typing import Dict, List, Tuple

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


def build_manifest(
    video_files: List[str],
    image_files: List[str],
    license_note: str = "user-provided",
) -> Tuple[Dict[str, AssetRef], List[str]]:
    """探测全部素材，返回 (assets dict, 使用顺序 list)。

    顺序 = 视频按文件名排序 + 图片按文件名排序（Mode A 文件名对应 / Mode B 上传顺序
    由调用方传入已排序的列表即可）。
    """
    assets: Dict[str, AssetRef] = {}
    order: List[str] = []
    used_ids: set = set()

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
                assets[asset_id] = AssetRef(
                    id=asset_id, type="video", file=path,
                    duration=probe.duration, width=probe.width,
                    height=probe.height, fps=probe.fps,
                    source="user", license_note=license_note,
                )
            else:
                probe = prober.probe_image(path)
                assets[asset_id] = AssetRef(
                    id=asset_id, type="image", file=path,
                    width=probe.width, height=probe.height,
                    source="user", license_note=license_note,
                )
        except prober.ProbeError as exc:
            logger.error(f"素材探测失败 {path}: {exc}")
            raise AssetError(f"素材探测失败: {path}") from exc
        order.append(asset_id)
        logger.info(f"asset {asset_id} [{kind}] <- {os.path.basename(path)}")

    return assets, order
