"""IMAGE_EDITOR 图片智能编辑成动画（V2 第三阶段：零视频理解模型的图片动画化）。

图片 → AI 分析（主色/亮度/显著性区域）→ 关键元素裁剪 → 多动效动画渲染 → 成片

替代方案说明（重要）：
    本模块不依赖任何视频理解模型（cv2/torch/transformers）。图片分析使用
    PIL（若 venv 有）+ ffmpeg rawvideo 兜底；显著性区域用亮度对比 + 中心
    偏置 + 边缘密度启发式估计，退化为黄金分割九宫格中心。这些是"替代方案"，
    视频理解模型（如 SAM/U2Net）是未来的增强方向，当前为零依赖兜底。

与 script_only.py 的关系：
    script_only 是「纯色背景 + drawtext 文字卡」零素材模式；
    本模块是「图片素材 + AI 分析 + 关键元素裁剪 + 多动效动画」图片成片模式。
    两者都绕开 assets/vision/matcher 素材匹配链，自带渲染能力。

零外部 AI/视觉库：仅 stdlib + PIL（若可用）+ ffmpeg + 现有包内模块。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

from video_director import (
    config,
    narration as narration_mod,
    prober,
    qc as qc_mod,
    renderer,
    store,
    style as style_mod,
    subtitle as subtitle_mod,
)
from video_director.script_only import (
    find_cjk_font,
    wrap_text,
    _emit_line_files,
    _path_arg,
    _esc_path,
    LINE_SPACING_RATIO,
)


class ImageEditorError(Exception):
    """IMAGE_EDITOR 模式结构性失败（能力缺失/渲染失败/QC 拒绝）。"""


# ---------------------------------------------------------------------------
# 动效枚举（字符串常量，便于日志/IR 直接展示）
# ---------------------------------------------------------------------------

MOTION_KENBURNS_ZOOM_IN = "kenburns_zoom_in"
MOTION_KENBURNS_ZOOM_OUT = "kenburns_zoom_out"
MOTION_PAN_LEFT = "pan_left"
MOTION_PAN_RIGHT = "pan_right"
MOTION_FOCUS_ZOOM = "focus_zoom"

#: 全部合法动效（场景轮换池，让成片有变化）
_ALL_MOTIONS = (
    MOTION_KENBURNS_ZOOM_IN,
    MOTION_KENBURNS_ZOOM_OUT,
    MOTION_PAN_LEFT,
    MOTION_PAN_RIGHT,
    MOTION_FOCUS_ZOOM,
)

#: 文字覆盖位置（drawtext y 坐标策略）
TEXT_POSITION_BOTTOM = "bottom"
TEXT_POSITION_CENTER = "center"
TEXT_POSITION_TOP = "top"


# ---------------------------------------------------------------------------
# PIL 可用性探测（一次性，模块级缓存）
# ---------------------------------------------------------------------------

_PIL_AVAILABLE: Optional[bool] = None


def _pil_available() -> bool:
    """PIL 是否可用（venv 有则用，无则纯 ffmpeg rawvideo 兜底）。"""
    global _PIL_AVAILABLE
    if _PIL_AVAILABLE is None:
        try:
            import PIL  # noqa: F401
            _PIL_AVAILABLE = True
        except ImportError:
            _PIL_AVAILABLE = False
            logger.info("PIL 不可用，图片分析走 ffmpeg rawvideo 兜底路径")
    return _PIL_AVAILABLE


# ---------------------------------------------------------------------------
# 图片分析（替代方案：PIL 优先，ffmpeg rawvideo 兜底）
# ---------------------------------------------------------------------------

def analyze_image(img_path: str) -> dict:
    """分析图片，返回 §30 完整字段（focal_point/negative_space 已补齐）。

    替代方案说明（§30 ImageAnalyzer）：
        - 主色提取：PIL 缩到 32x32 算直方图（或 ffmpeg rawvideo 兜底）
        - 显著性区域：亮度对比 + 中心偏置 + 边缘密度启发式估计，
          退化为黄金分割九宫格中心（无视频理解模型，这是零依赖兜底）
        - focal_point：从 saliency_region 中心派生 (x+w/2, y+h/2)，
          若调用方已有 VisionProvider 的对象 bbox，可覆盖此字段
        - negative_space：图片四周相对 saliency 区域的"留白"分布
          （top/bottom/left/right 比例，用于文字放置决策）
        - 视频理解模型（SAM/U2Net）是未来增强方向，当前为替代方案

    Args:
        img_path: 图片文件路径

    Returns:
        dict with keys:
            width: int
            height: int
            mean_color: [R, G, B]（0-255）
            brightness: float（0-255，灰度均值）
            dominant_colors: [[R,G,B], ...] 前 3 主色（颜色量化）
            saliency_region: {x, y, w, h} 显著性区域（像素坐标）
            focal_point: {x, y} 焦点坐标（saliency_region 中心，像素坐标）
            negative_space: {top, bottom, left, right} 留白比例（0-1，
                           saliency_region 边到图边的占比，越大越空）
    """
    if not os.path.isfile(img_path):
        raise ImageEditorError(f"图片不存在: {img_path}")

    if _pil_available():
        result = _analyze_with_pil(img_path)
    else:
        result = _analyze_with_ffmpeg(img_path)
    # 补 §30 focal_point / negative_space（从 saliency 派生，零额外计算）
    result["focal_point"] = _derive_focal_point(result["saliency_region"],
                                                result["width"], result["height"])
    result["negative_space"] = _derive_negative_space(result["saliency_region"],
                                                       result["width"], result["height"])
    return result


def _derive_focal_point(saliency: dict, w: int, h: int) -> Dict[str, int]:
    """从 saliency_region 中心派生 focal_point（§30）。

    saliency_region 形如 {x, y, w, h}，focal_point = (x + w/2, y + h/2)。
    若 saliency 字段缺失，退化到图中心。
    """
    if not isinstance(saliency, dict):
        return {"x": w // 2, "y": h // 2}
    sx = int(saliency.get("x", 0))
    sy = int(saliency.get("y", 0))
    sw = int(saliency.get("w", 0))
    sh = int(saliency.get("h", 0))
    if sw <= 0 or sh <= 0:
        return {"x": w // 2, "y": h // 2}
    return {"x": sx + sw // 2, "y": sy + sh // 2}


def _derive_negative_space(saliency: dict, w: int, h: int) -> Dict[str, float]:
    """从 saliency_region 派生 negative_space 四向比例（§30）。

    返回 {top, bottom, left, right}，值 0-1，表示该方向上 saliency 边到图边
    的像素数占该方向总长的比例。值越大 → 该方向留白越多 → 越适合放文字。
    """
    if not isinstance(saliency, dict) or w <= 0 or h <= 0:
        return {"top": 0.5, "bottom": 0.5, "left": 0.5, "right": 0.5}
    sx = max(0, int(saliency.get("x", 0)))
    sy = max(0, int(saliency.get("y", 0)))
    sw = max(0, int(saliency.get("w", 0)))
    sh = max(0, int(saliency.get("h", 0)))
    right_edge = min(w, sx + sw)
    bottom_edge = min(h, sy + sh)
    return {
        "top": round(sy / h, 3) if h > 0 else 0.5,
        "bottom": round((h - bottom_edge) / h, 3) if h > 0 else 0.5,
        "left": round(sx / w, 3) if w > 0 else 0.5,
        "right": round((w - right_edge) / w, 3) if w > 0 else 0.5,
    }


def _analyze_with_pil(img_path: str) -> dict:
    """PIL 路径：缩略图 + 直方图 + 启发式显著性。"""
    from PIL import Image

    img = Image.open(img_path)
    img = img.convert("RGB")
    w, h = img.size

    # 缩略图用于颜色分析（降低计算量）
    thumb = img.copy()
    thumb.thumbnail((64, 64))
    pixels = list(thumb.getdata())

    # 均值颜色 + 亮度
    total_r = sum(p[0] for p in pixels)
    total_g = sum(p[1] for p in pixels)
    total_b = sum(p[2] for p in pixels)
    n = len(pixels)
    mean_color = [total_r // n, total_g // n, total_b // n]
    # 亮度 = 0.299R + 0.587G + 0.114B（BT.601）
    brightness = sum(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in pixels) / n

    # 主色提取：简单颜色量化（每通道分 4 档：0-63/64-127/128-191/192-255）
    quantized = {}
    for r, g, b in pixels:
        qr = r // 64
        qg = g // 64
        qb = b // 64
        key = (qr, qg, qb)
        if key not in quantized:
            quantized[key] = {"count": 0, "r_sum": 0, "g_sum": 0, "b_sum": 0}
        quantized[key]["count"] += 1
        quantized[key]["r_sum"] += r
        quantized[key]["g_sum"] += g
        quantized[key]["b_sum"] += b

    # 取前 3 主色（按像素数排序）
    sorted_colors = sorted(quantized.values(), key=lambda x: x["count"], reverse=True)
    dominant_colors = []
    for c in sorted_colors[:3]:
        dominant_colors.append([
            c["r_sum"] // c["count"],
            c["g_sum"] // c["count"],
            c["b_sum"] // c["count"],
        ])

    # 显著性区域：亮度对比 + 中心偏置启发式
    saliency = _estimate_saliency_pil(img, w, h)

    return {
        "width": w,
        "height": h,
        "mean_color": mean_color,
        "brightness": round(brightness, 2),
        "dominant_colors": dominant_colors,
        "saliency_region": saliency,
    }


def _estimate_saliency_pil(img, w: int, h: int) -> dict:
    """启发式显著性估计：亮度对比 + 中心偏置 + 边缘密度。

    替代方案（无视频理解模型）：
        1. 把图缩到 64x64 灰度
        2. 计算每个 block 与全局均值的对比度
        3. 叠加中心偏置（九宫格中心权重高）
        4. 取最高显著性的连通区域为 saliency_region
        退化：无有效对比时返回九宫格中心区域
    """
    from PIL import Image

    thumb = img.copy().convert("L")
    thumb.thumbnail((64, 64))
    tw, th = thumb.size
    gray = list(thumb.getdata())

    # 全局亮度均值
    global_mean = sum(gray) / len(gray) if gray else 128.0

    # 分 block（8x8 网格）计算对比度 + 中心偏置
    blocks_x = min(8, tw)
    blocks_y = min(8, th)
    block_w = tw / blocks_x
    block_h = th / blocks_y

    # 九宫格中心偏置权重（中心格权重 1.0，边缘递减）
    center_weights = _compute_center_weights(blocks_x, blocks_y)

    block_scores = []
    for by in range(blocks_y):
        for bx in range(blocks_x):
            x0 = int(bx * block_w)
            y0 = int(by * block_h)
            x1 = int((bx + 1) * block_w)
            y1 = int((by + 1) * block_h)
            pixels = []
            for y in range(y0, y1):
                for x in range(x0, x1):
                    if y * tw + x < len(gray):
                        pixels.append(gray[y * tw + x])
            if not pixels:
                continue
            block_mean = sum(pixels) / len(pixels)
            contrast = abs(block_mean - global_mean)
            center_weight = center_weights[by][bx]
            score = contrast * (0.6 + 0.4 * center_weight)
            block_scores.append((bx, by, score, x0, y0, x1, y1))

    if not block_scores:
        # 退化：九宫格中心
        return _fallback_saliency(w, h)

    # 取最高显著性的 block，映射回原图坐标
    best = max(block_scores, key=lambda s: s[2])
    bx, by, score, x0, y0, x1, y1 = best
    # 映射：thumb 坐标 → 原图坐标
    scale_x = w / tw
    scale_y = h / th
    rx = int(x0 * scale_x)
    ry = int(y0 * scale_y)
    rw = int((x1 - x0) * scale_x)
    rh = int((y1 - y0) * scale_y)
    # 确保区域在图内
    rw = min(rw, w - rx)
    rh = min(rh, h - ry)
    if rw <= 0 or rh <= 0:
        return _fallback_saliency(w, h)

    return {"x": rx, "y": ry, "w": rw, "h": rh}


def _compute_center_weights(cols: int, rows: int) -> List[List[float]]:
    """九宫格中心偏置权重（中心 1.0，向边缘递减到 0.3）。"""
    weights = []
    cx = (cols - 1) / 2
    cy = (rows - 1) / 2
    max_dist = (cx ** 2 + cy ** 2) ** 0.5 or 1.0
    for y in range(rows):
        row = []
        for x in range(cols):
            dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            weight = 1.0 - 0.7 * (dist / max_dist)
            row.append(max(0.3, weight))
        weights.append(row)
    return weights


def _fallback_saliency(w: int, h: int) -> dict:
    """退化：黄金分割九宫格中心区域（图中心的 1/3 宽 × 1/3 高）。"""
    cw = max(1, w // 3)
    ch = max(1, h // 3)
    cx = (w - cw) // 2
    cy = (h - ch) // 2
    return {"x": cx, "y": cy, "w": cw, "h": ch}


def _analyze_with_ffmpeg(img_path: str) -> dict:
    """ffmpeg rawvideo 兜底路径（无 PIL 时）。

    缩到 32x32 raw RGB，Python 端读字节算直方图。
    """
    ff = prober._resolve_ffmpeg()
    probe = prober.probe_image(img_path)
    w, h = probe.width or 0, probe.height or 0
    if w == 0 or h == 0:
        raise ImageEditorError(f"无法探测图片分辨率: {img_path}")

    # 缩到 32x32 rawvideo RGB
    sw, sh = 32, 32
    cmd = [
        ff, "-y", "-loglevel", "error",
        "-i", img_path,
        "-vf", f"scale={sw}:{sh}",
        "-pix_fmt", "rgb24",
        "-f", "rawvideo", "-",
    ]
    completed = subprocess.run(cmd, capture_output=True, timeout=60)
    data = completed.stdout
    expected = sw * sh * 3
    if len(data) < expected:
        raise ImageEditorError(
            f"ffmpeg rawvideo 读取不足: {len(data)}/{expected} bytes"
        )

    # 均值颜色 + 亮度
    total_r = total_g = total_b = 0
    pixels = []
    for i in range(0, expected, 3):
        r, g, b = data[i], data[i + 1], data[i + 2]
        total_r += r
        total_g += g
        total_b += b
        pixels.append((r, g, b))
    n = sw * sh
    mean_color = [total_r // n, total_g // n, total_b // n]
    brightness = sum(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in pixels) / n

    # 主色提取（同 PIL 路径的量化逻辑）
    quantized = {}
    for r, g, b in pixels:
        key = (r // 64, g // 64, b // 64)
        if key not in quantized:
            quantized[key] = {"count": 0, "r_sum": 0, "g_sum": 0, "b_sum": 0}
        quantized[key]["count"] += 1
        quantized[key]["r_sum"] += r
        quantized[key]["g_sum"] += g
        quantized[key]["b_sum"] += b
    sorted_colors = sorted(quantized.values(), key=lambda x: x["count"], reverse=True)
    dominant_colors = [
        [c["r_sum"] // c["count"], c["g_sum"] // c["count"], c["b_sum"] // c["count"]]
        for c in sorted_colors[:3]
    ]

    # 显著性：ffmpeg rawvideo 灰度分 block 算对比度
    saliency = _estimate_saliency_ffmpeg(img_path, w, h)

    return {
        "width": w,
        "height": h,
        "mean_color": mean_color,
        "brightness": round(brightness, 2),
        "dominant_colors": dominant_colors,
        "saliency_region": saliency,
    }


def _estimate_saliency_ffmpeg(img_path: str, w: int, h: int) -> dict:
    """ffmpeg 灰度 rawvideo 兜底显著性估计。"""
    ff = prober._resolve_ffmpeg()
    sw, sh = 32, 32
    cmd = [
        ff, "-y", "-loglevel", "error",
        "-i", img_path,
        "-vf", f"scale={sw}:{sh},format=gray",
        "-f", "rawvideo", "-",
    ]
    completed = subprocess.run(cmd, capture_output=True, timeout=60)
    data = completed.stdout
    if len(data) < sw * sh:
        return _fallback_saliency(w, h)
    gray = list(data[: sw * sh])
    global_mean = sum(gray) / len(gray) if gray else 128.0

    blocks_x = blocks_y = 8
    block_w = sw / blocks_x
    block_h = sh / blocks_y
    center_weights = _compute_center_weights(blocks_x, blocks_y)

    block_scores = []
    for by in range(blocks_y):
        for bx in range(blocks_x):
            x0 = int(bx * block_w)
            y0 = int(by * block_h)
            x1 = int((bx + 1) * block_w)
            y1 = int((by + 1) * block_h)
            pixels = []
            for y in range(y0, y1):
                for x in range(x0, x1):
                    if y * sw + x < len(gray):
                        pixels.append(gray[y * sw + x])
            if not pixels:
                continue
            block_mean = sum(pixels) / len(pixels)
            contrast = abs(block_mean - global_mean)
            center_weight = center_weights[by][bx]
            score = contrast * (0.6 + 0.4 * center_weight)
            block_scores.append((bx, by, score, x0, y0, x1, y1))

    if not block_scores:
        return _fallback_saliency(w, h)

    best = max(block_scores, key=lambda s: s[2])
    bx, by, score, x0, y0, x1, y1 = best
    scale_x = w / sw
    scale_y = h / sh
    rx = int(x0 * scale_x)
    ry = int(y0 * scale_y)
    rw = int((x1 - x0) * scale_x)
    rh = int((y1 - y0) * scale_y)
    rw = min(rw, w - rx)
    rh = min(rh, h - ry)
    if rw <= 0 or rh <= 0:
        return _fallback_saliency(w, h)
    return {"x": rx, "y": ry, "w": rw, "h": rh}


# ---------------------------------------------------------------------------
# 关键元素裁剪
# ---------------------------------------------------------------------------

def crop_region(img_path: str, region: dict, out_path: str) -> str:
    """基于 saliency_region 裁剪关键元素，输出裁剪图。

    使用 PIL（若可用）或 ffmpeg crop 滤镜。

    Args:
        img_path: 源图路径
        region: {x, y, w, h} 裁剪区域（像素坐标）
        out_path: 输出路径

    Returns:
        out_path
    """
    if not os.path.isfile(img_path):
        raise ImageEditorError(f"裁剪源图不存在: {img_path}")

    # probe_media 依赖 store 缓存表，独立调用时幂等初始化
    try:
        store.init_db()
    except Exception as exc:
        logger.warning(f"store.init_db 失败（继续渲染）: {exc}")

    x = max(0, int(region.get("x", 0)))
    y = max(0, int(region.get("y", 0)))
    w = max(1, int(region.get("w", 0)))
    h = max(1, int(region.get("h", 0)))

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    if _pil_available():
        return _crop_with_pil(img_path, x, y, w, h, out_path)
    return _crop_with_ffmpeg(img_path, x, y, w, h, out_path)


def _crop_with_pil(img_path: str, x: int, y: int, w: int, h: int, out_path: str) -> str:
    from PIL import Image

    img = Image.open(img_path)
    iw, ih = img.size
    # 确保裁剪区域在图内
    x = min(x, iw - 1)
    y = min(y, ih - 1)
    w = min(w, iw - x)
    h = min(h, ih - y)
    if w <= 0 or h <= 0:
        raise ImageEditorError(f"裁剪区域非法: x={x},y={y},w={w},h={h} (图 {iw}x{ih})")
    cropped = img.crop((x, y, x + w, y + h))
    cropped.save(out_path)
    logger.info(f"crop(PIL): {img_path} -> {out_path} [{x},{y},{w},{h}]")
    return out_path


def _crop_with_ffmpeg(img_path: str, x: int, y: int, w: int, h: int, out_path: str) -> str:
    ff = prober._resolve_ffmpeg()
    cmd = [
        ff, "-y", "-loglevel", "error",
        "-i", img_path,
        "-vf", f"crop={w}:{h}:{x}:{y}",
        "-frames:v", "1",
        out_path,
    ]
    try:
        renderer._run(cmd)
    except renderer.RenderError as exc:
        raise ImageEditorError(f"ffmpeg crop 失败: {exc}") from exc
    logger.info(f"crop(ffmpeg): {img_path} -> {out_path} [{x},{y},{w},{h}]")
    return out_path


# ---------------------------------------------------------------------------
# 图片动画渲染（zoompan / crop 随时间变化 + drawtext 文字）
# ---------------------------------------------------------------------------

def render_image_animation(
    img: str,
    text: str,
    duration: float,
    out_path: str,
    w: int = config.DEFAULT_RESOLUTION[0],
    h: int = config.DEFAULT_RESOLUTION[1],
    fps: int = config.DEFAULT_FPS,
    motion: str = MOTION_KENBURNS_ZOOM_IN,
    text_position: str = TEXT_POSITION_BOTTOM,
    font_size: int = 56,
) -> str:
    """渲染单张图片为动画 segment（无音轨）。

    动效实现（FFmpeg zoompan / crop 表达式）：
        - kenburns_zoom_in: z 从 1.0 线性增到 zmax（缓推近）
        - kenburns_zoom_out: z 从 zmax 线性减到 1.0（缓拉远）
        - pan_left: crop 的 x 从右到左随时间变化（画面左移）
        - pan_right: crop 的 x 从左到右随时间变化（画面右移）
        - focus_zoom: 先推近到 saliency 区域，再轻微拉回（呼吸感）

    文字叠加：drawtext textfile（参照 script_only.py 的字体探测 + 折行），
    每行一个 filter，y 按位置策略定位。

    Args:
        img: 源图路径
        text: 场景文案（叠加到画面）
        duration: 段时长（秒）
        out_path: 输出 segment 路径
        w, h: 输出分辨率
        fps: 帧率
        motion: 动效类型（见 _ALL_MOTIONS）
        text_position: 文字位置 bottom/center/top
        font_size: 字体大小

    Returns:
        out_path
    """
    if duration <= 0:
        raise ImageEditorError(f"render_image_animation: duration 非法 {duration}")
    if motion not in _ALL_MOTIONS:
        raise ImageEditorError(
            f"未知动效 {motion}（合法: {', '.join(_ALL_MOTIONS)}）"
        )

    d = Path(out_path).parent
    d.mkdir(parents=True, exist_ok=True)

    # probe_media 依赖 store 缓存表，独立调用时幂等初始化
    try:
        store.init_db()
    except Exception as exc:
        logger.warning(f"store.init_db 失败（继续渲染）: {exc}")

    # 图片分析（用于 focus_zoom 的 saliency 定位）
    analysis = analyze_image(img)
    src_w = analysis["width"]
    src_h = analysis["height"]

    frames = max(1, round(duration * fps))
    ff = renderer._ffmpeg()

    # 构造 zoompan / crop 滤镜
    vf_motion = _build_motion_filter(motion, src_w, src_h, w, h, frames, analysis, fps)

    # 文字叠加（drawtext textfile 方式）
    vf_parts = [vf_motion]
    font_path = find_cjk_font()
    if font_path and text.strip():
        lines = wrap_text(text, w, font_size)
        # 用场景索引作为 seq 避免文件名冲突
        seq = abs(hash(out_path)) & 0xFFFF
        txt_paths = _emit_line_files(lines, d, seq)
        drawtext_vf = _build_drawtext_with_position(
            lines, txt_paths, font_path, font_size, w, h, text_position
        )
        vf_parts.append(drawtext_vf)
    elif font_path is None:
        logger.warning(
            "未找到中文字体，该图片动画段降级为无文字叠加"
        )

    vf = ",".join(vf_parts)

    cmd = [
        ff, "-y", "-loglevel", "error",
        "-loop", "1", "-i", img,
        "-vf", vf,
        "-frames:v", str(frames),
        "-an",
        "-r", str(fps),
        "-c:v", config.DEFAULT_VIDEO_CODEC,
        "-preset", "veryfast",
        "-crf", str(config.DEFAULT_CRF),
        "-pix_fmt", config.DEFAULT_PIXEL_FORMAT,
        "-t", f"{duration:.3f}",
        out_path,
    ]
    try:
        renderer._run(cmd)
    except renderer.RenderError as exc:
        raise ImageEditorError(f"图片动画渲染失败: {exc}") from exc

    # 校验产物时长
    probe = prober.probe_media(out_path)
    if probe.duration is None or abs(probe.duration - duration) > 0.15:
        raise ImageEditorError(
            f"segment 时长 {probe.duration} 与预期 {duration:.3f}s 偏差超 0.15s: {out_path}"
        )
    logger.info(
        f"image_animation segment: {motion} {probe.duration:.3f}s, "
        f"{src_w}x{src_h} -> {w}x{h}"
    )
    return out_path


def _build_motion_filter(
    motion: str,
    src_w: int,
    src_h: int,
    dst_w: int,
    dst_h: int,
    frames: int,
    analysis: dict,
    fps: int,
) -> str:
    """构造 zoompan / crop 动效滤镜表达式。

    zoompan 参数：
        z: 缩放倍率表达式（1.0=原尺寸，>1 放大）
        x, y: 输出画面在缩放后图中的起始坐标（居中 = (iw-iw/z)/2）
        d: 持续帧数
        s: 输出分辨率
        fps: 输出帧率

    策略：
        1. 先 scale 到 2 倍目标分辨率（保证 zoompan 有像素余量）
        2. zoompan 的 z/x/y 表达式按 motion 类型构造
    """
    # 先放大到 2 倍目标尺寸，给 zoompan 足够的像素空间
    scale_w = dst_w * 2
    scale_h = dst_h * 2
    scale_filter = f"scale={scale_w}:{scale_h}"

    zmax = 1.15  # 缩放上界（与 style.image_motion_intensity 量级一致）
    d = max(frames, 1)

    if motion == MOTION_KENBURNS_ZOOM_IN:
        # z: 1.0 → zmax 线性推近
        z_expr = f"1+({zmax}-1)*on/{d - 1 if d > 1 else 1}"
        x_expr = "'iw/2-(iw/zoom)/2'"
        y_expr = "'ih/2-(ih/zoom)/2'"
    elif motion == MOTION_KENBURNS_ZOOM_OUT:
        # z: zmax → 1.0 线性拉远
        z_expr = f"{zmax}-({zmax}-1)*on/{d - 1 if d > 1 else 1}"
        x_expr = "'iw/2-(iw/zoom)/2'"
        y_expr = "'ih/2-(ih/zoom)/2'"
    elif motion == MOTION_PAN_LEFT:
        # 固定 z=1.0，x 从右到左变化
        # 输入已 scale 到 scale_w×scale_h，pan 范围 = scale_w - dst_w
        # y 垂直居中：z=1.0 时 ih=scale_h，居中偏移 = (scale_h - dst_h) / 2
        pan_range = scale_w - dst_w
        y_center = (scale_h - dst_h) / 2
        z_expr = "1.0"
        x_expr = f"'{pan_range}-({pan_range})*on/{d - 1 if d > 1 else 1}'"
        y_expr = f"'{y_center:.0f}'"
    elif motion == MOTION_PAN_RIGHT:
        # 固定 z=1.0，x 从左到右变化
        pan_range = scale_w - dst_w
        y_center = (scale_h - dst_h) / 2
        z_expr = "1.0"
        x_expr = f"'0+({pan_range})*on/{d - 1 if d > 1 else 1}'"
        y_expr = f"'{y_center:.0f}'"
    elif motion == MOTION_FOCUS_ZOOM:
        # 呼吸感：先推近到 zmax 的 80%，再回到 1.0（正弦曲线）
        half = d / 2.0
        z_expr = f"1+({zmax}-1)*0.8*sin(on/{d}*PI)"
        # 聚焦到 saliency 区域（映射到 scale 后的坐标）
        sal = analysis.get("saliency_region", {})
        sal_x = sal.get("x", src_w // 3) / src_w * scale_w if src_w > 0 else scale_w / 2
        sal_y = sal.get("y", src_h // 3) / src_h * scale_h if src_h > 0 else scale_h / 2
        x_expr = f"'{sal_x:.0f}-(iw/zoom-{dst_w})/2'"
        y_expr = f"'{sal_y:.0f}-(ih/zoom-{dst_h})/2'"
    else:
        # 退化：静态（不应到达，motion 已校验）
        z_expr = "1.0"
        x_expr = "'iw/2-(iw/zoom)/2'"
        y_expr = "'ih/2-(ih/zoom)/2'"

    zoompan = (
        f"zoompan=z='{z_expr}'"
        f":x={x_expr}:y={y_expr}"
        f":d={d}:s={dst_w}x{dst_h}:fps={fps}"
    )
    return f"{scale_filter},{zoompan}"


def _build_drawtext_with_position(
    lines: List[str],
    txt_paths: List[str],
    font_path: str,
    font_size: int,
    w: int,
    h: int,
    position: str,
) -> str:
    """构造 drawtext 滤镜链，文字按 position 定位。

    与 script_only._drawtext_vf 的差异：
        - 支持 bottom/center/top 三种位置
        - bottom 时加半透明背景条（提升可读性）
    """
    spacing = max(8, round(font_size * LINE_SPACING_RATIO))
    line_h = font_size + spacing
    block_h = len(lines) * line_h - spacing

    if position == TEXT_POSITION_BOTTOM:
        # 底部：y 从底部安全边距往上排
        margin_bottom = max(120, h // 8)
        y0 = h - margin_bottom - block_h
    elif position == TEXT_POSITION_TOP:
        margin_top = max(80, h // 12)
        y0 = margin_top
    else:  # center
        y0 = max(0.0, (h - block_h) / 2)

    parts = []
    for i, txt in enumerate(txt_paths):
        parts.append(
            f"drawtext=fontfile={_path_arg(font_path)}"
            f":textfile={_path_arg(txt)}"
            f":fontcolor=white:fontsize={font_size}"
            f":borderw=3:bordercolor=black@0.7"
            f":x=(w-text_w)/2:y={y0 + i * line_h:.1f}"
        )
    return ",".join(parts)


# ---------------------------------------------------------------------------
# 图片-文案匹配（替代方案：主色 tag + 文案关键词简单匹配）
# ---------------------------------------------------------------------------

#: 主色 → 语义 tag 映射（颜色启发式，替代方案）
_COLOR_TAGS = [
    (lambda r, g, b: r > 150 and g > 150 and b > 150, "bright_light"),
    (lambda r, g, b: r < 60 and g < 60 and b < 60, "dark_night"),
    (lambda r, g, b: r > g + 30 and r > b + 30, "warm_red"),
    (lambda r, g, b: g > r + 20 and g > b + 20, "nature_green"),
    (lambda r, g, b: b > r + 20 and b > g + 20, "cool_blue"),
    (lambda r, g, b: r > 180 and g > 140 and b < 100, "sunset_gold"),
]


def _color_to_tag(mean_color: List[int]) -> str:
    """主色 → 语义 tag（启发式，替代方案）。"""
    r, g, b = mean_color
    for matcher, tag in _COLOR_TAGS:
        if matcher(r, g, b):
            return tag
    return "neutral"


#: 文案关键词 → 期望图片 tag（简单映射，替代方案）
_TEXT_TAG_MAP = {
    "清晨": "bright_light", "早上": "bright_light", "阳光": "bright_light",
    "夜晚": "dark_night", "晚上": "dark_night", "黑暗": "dark_night",
    "花": "nature_green", "树": "nature_green", "草": "nature_green",
    "海": "cool_blue", "天": "cool_blue", "蓝": "cool_blue",
    "夕阳": "sunset_gold", "黄昏": "sunset_gold",
    "红": "warm_red", "火": "warm_red",
}


def _text_to_tags(text: str) -> List[str]:
    """文案 → 期望图片 tag 列表（关键词匹配，替代方案）。"""
    tags = []
    for keyword, tag in _TEXT_TAG_MAP.items():
        if keyword in text:
            tags.append(tag)
    return tags


def _match_image_to_scene(
    scene_text: str,
    image_analyses: List[dict],
    image_paths: List[str],
    scene_idx: int,
) -> int:
    """为场景选一张图（替代方案：主色 tag + 文案关键词匹配）。

    策略：
        1. 文案关键词 → 期望 tag 列表
        2. 图片主色 → tag
        3. tag 匹配的图片优先；无匹配则按场景索引轮换（i % N）
    """
    expected_tags = _text_to_tags(scene_text)
    if expected_tags:
        for i, analysis in enumerate(image_analyses):
            img_tag = _color_to_tag(analysis["mean_color"])
            if img_tag in expected_tags:
                return i
    # 轮换：场景 i 对应图 i % N
    return scene_idx % len(image_paths)


# ---------------------------------------------------------------------------
# 单图多镜头：§29 一张图产多镜头（hero/detail/macro 三连拍）
# ---------------------------------------------------------------------------


#: 三连拍默认镜头规格（§29 + §30）：
#:   hero    全景推近（Ken Burns zoom_in），文字落底，展示整体
#:   detail  局部 crop 放大（saliency 区域），文字居中，讲细节
#:   macro   微距 zoom（focus_zoom 呼吸感），文字居中，强化质感
_DEFAULT_SHOTS_SPEC: List[dict] = [
    {
        "name": "hero",
        "motion": MOTION_KENBURNS_ZOOM_IN,
        "text_position": TEXT_POSITION_BOTTOM,
        "duration_hint_s": 4.0,
        "crop_to_saliency": False,
        "label": "全景推近",
    },
    {
        "name": "detail",
        "motion": MOTION_FOCUS_ZOOM,
        "text_position": TEXT_POSITION_CENTER,
        "duration_hint_s": 3.0,
        "crop_to_saliency": True,
        "label": "局部放大",
    },
    {
        "name": "macro",
        "motion": MOTION_FOCUS_ZOOM,
        "text_position": TEXT_POSITION_CENTER,
        "duration_hint_s": 2.5,
        "crop_to_saliency": True,
        "scale_factor": 0.5,  # macro 再裁到 saliency 中心 50% 区域
        "label": "微距特写",
    },
]


@dataclass
class ImageShotResult:
    """compose_image_shots 单镜头结果。"""

    name: str
    segment_path: str
    duration: float
    motion: str
    analysis: dict


@dataclass
class ComposeImageShotsResult:
    """compose_image_shots 整体结果（§29 一图多镜头 concat 成片）。"""

    project_dir: str
    final_path: str
    total_duration: float
    shots: List[ImageShotResult] = field(default_factory=list)
    analysis: dict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def compose_image_shots(
    image_path: str,
    shots_spec: Optional[List[dict]] = None,
    project_id: str = "img-shots",
    texts: Optional[List[str]] = None,
    width: int = config.DEFAULT_RESOLUTION[0],
    height: int = config.DEFAULT_RESOLUTION[1],
    fps: int = config.DEFAULT_FPS,
) -> ComposeImageShotsResult:
    """一张图产多镜头（§29 hero/detail/macro 三连拍）→ concat 成片。

    非 slideshow（§28 硬约束）：
        - 同一张图不是简单排序展示，而是"全景 → 局部 crop → 微距 zoom"
          的镜头语言递进
        - detail/macro 镜头先 crop 到 saliency_region，再用 focus_zoom
          产生呼吸感动效，让"局部放大"有镜头运动
        - 每镜头 motion 不同（zoom_in / focus_zoom / focus_zoom+crop），
          视觉节奏有变化

    与 SegmentationProvider 的关系（§31）：
        - 本函数**不**调用 segmentation——只用 crop+zoom 即可达成
          "局部放大"效果，符合 §31 "普通局部放大不需 SAM" 的约束
        - 若调用方需要"主体脱离背景"的 parallax，那是 SegmentationProvider
          的职责，不是本函数

    Args:
        image_path: 单张原图路径
        shots_spec: 镜头规格列表，每项 {name, motion, text_position,
                     duration_hint_s, crop_to_saliency, scale_factor, label}。
                     None 用 _DEFAULT_SHOTS_SPEC（hero/detail/macro 三连拍）
        project_id: 项目 ID
        texts: 每镜头文案列表，长度应与 shots_spec 一致；
               None 则用镜头 name 作为占位文案
        width, height, fps: 输出参数

    Returns:
        ComposeImageShotsResult
    """
    if not os.path.isfile(image_path):
        raise ImageEditorError(f"图片不存在: {image_path}")

    spec_list = shots_spec if shots_spec is not None else list(_DEFAULT_SHOTS_SPEC)
    if not spec_list:
        raise ImageEditorError("shots_spec 为空，至少需要 1 个镜头")
    if texts is not None and len(texts) != len(spec_list):
        raise ImageEditorError(
            f"texts 长度 {len(texts)} != shots_spec 长度 {len(spec_list)}"
        )

    pid = project_id or "img-shots"
    base = config.project_dir(pid)
    dirs = config.init_project_dirs(base)
    warnings: List[str] = []
    seg_dir = dirs["render"] / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)

    try:
        store.init_db()
    except Exception as exc:
        logger.warning(f"store.init_db 失败（继续渲染）: {exc}")

    # 1. 分析原图（§30 focal_point/negative_space 已自动补齐）
    analysis = analyze_image(image_path)
    logger.info(
        f"compose_image_shots: {Path(image_path).name} "
        f"{analysis['width']}x{analysis['height']}, "
        f"focal={analysis['focal_point']}, "
        f"negative_space={analysis['negative_space']}"
    )

    saliency = analysis.get("saliency_region", {})
    src_w = analysis["width"]
    src_h = analysis["height"]

    # 2. 为每镜头准备源图（hero 用原图，detail/macro 先 crop 到 saliency）
    shot_segments: List[ImageShotResult] = []
    for i, spec in enumerate(spec_list):
        name = spec.get("name", f"shot_{i}")
        motion = spec.get("motion", MOTION_KENBURNS_ZOOM_IN)
        text_position = spec.get("text_position", TEXT_POSITION_BOTTOM)
        duration = float(spec.get("duration_hint_s", 3.0))
        text = (texts[i] if texts else name)

        # 决定源图：是否需要先 crop 到 saliency
        if spec.get("crop_to_saliency", False) and saliency:
            # crop 到 saliency_region，可选再缩小到 scale_factor 中心区域
            crop_src = str(seg_dir / f"{i:02d}_{name}_src.png")
            try:
                crop_region(image_path, saliency, crop_src)
            except ImageEditorError as exc:
                warnings.append(f"shot {name} crop 失败，降级用原图: {exc}")
                crop_src = image_path
            # macro: 再从 saliency 中心裁 scale_factor
            sf = spec.get("scale_factor")
            if sf and sf < 1.0 and crop_src != image_path:
                sub_analysis = analyze_image(crop_src)
                sub_w = sub_analysis["width"]
                sub_h = sub_analysis["height"]
                cx = sub_analysis["focal_point"]["x"]
                cy = sub_analysis["focal_point"]["y"]
                cw = max(1, int(sub_w * sf))
                ch = max(1, int(sub_h * sf))
                rx = max(0, min(cx - cw // 2, sub_w - cw))
                ry = max(0, min(cy - ch // 2, sub_h - ch))
                macro_src = str(seg_dir / f"{i:02d}_{name}_macro.png")
                try:
                    crop_region(crop_src, {"x": rx, "y": ry, "w": cw, "h": ch}, macro_src)
                    crop_src = macro_src
                except ImageEditorError as exc:
                    warnings.append(f"shot {name} macro crop 失败，用 detail: {exc}")
        else:
            crop_src = image_path

        # 3. 渲染单镜头
        seg_path = str(seg_dir / f"{i:02d}_{name}.mp4")
        try:
            render_image_animation(
                img=crop_src,
                text=text,
                duration=duration,
                out_path=seg_path,
                w=width, h=height, fps=fps,
                motion=motion,
                text_position=text_position,
            )
        except ImageEditorError as exc:
            raise ImageEditorError(f"shot {name} 渲染失败: {exc}") from exc

        shot_segments.append(ImageShotResult(
            name=name,
            segment_path=seg_path,
            duration=duration,
            motion=motion,
            analysis=analysis if i == 0 else {},
        ))
        logger.info(f"shot {name} rendered: {seg_path} ({duration}s, {motion})")

    # 4. concat 成片
    seg_paths = [s.segment_path for s in shot_segments]
    total_duration = sum(s.duration for s in shot_segments)
    visual_path = str(dirs["render"] / "visual.mp4")
    try:
        final_path = renderer.concat_segments(
            seg_paths, visual_path, width, height, fps
        )
    except renderer.RenderError as exc:
        raise ImageEditorError(f"concat 成片失败: {exc}") from exc

    # probe 验证
    probe = prober.probe_media(final_path)
    if probe.duration is None or probe.duration <= 0:
        raise ImageEditorError(f"concat 产物时长无效: {probe.duration}")
    if abs(probe.duration - total_duration) > 0.5:
        warnings.append(
            f"concat 时长 {probe.duration:.3f}s 与预期 {total_duration:.3f}s "
            f"偏差 > 0.5s"
        )

    # 复制到 output
    final_out = dirs["output"] / "final.mp4"
    final_out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(final_path, final_out)

    logger.info(
        f"compose_image_shots 完成: {len(shot_segments)} shots, "
        f"{probe.duration:.3f}s, {final_out}"
    )

    return ComposeImageShotsResult(
        project_dir=str(base),
        final_path=str(final_out),
        total_duration=probe.duration or total_duration,
        shots=shot_segments,
        analysis=analysis,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# 主入口：图片 + 文案 → 动画成片
# ---------------------------------------------------------------------------

@dataclass
class ImageEditorResult:
    """compose_images_video 结果（字段与 pipeline.PipelineResult 对齐）。"""

    project_dir: str
    final_path: str
    narration_duration: float
    qc_report: dict
    analysis: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def compose_images_video(
    script: str,
    image_paths: List[str],
    project_id: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
    style_intent: Optional[str] = None,
    width: int = config.DEFAULT_RESOLUTION[0],
    height: int = config.DEFAULT_RESOLUTION[1],
    fps: int = config.DEFAULT_FPS,
) -> ImageEditorResult:
    """图片智能编辑成动画全链路：

    文案分句 → TTS → 图片分析 → 关键元素裁剪 → 多动效动画 → concat → 字幕 → QC

    复用 narration/style/subtitle/renderer.concat_segments/renderer.mix_audio/
    renderer.subtitle_burn_in/qc.run_qc/store/doctor/runlog，零外部 AI 库。

    每个场景的 motion 轮换使用（让成片有变化，不是每张图同样的静态画面）。

    Args:
        script: 文案文本
        image_paths: 图片路径列表
        project_id: 项目 ID
        voice: TTS 语音
        style_intent: 风格描述（None 则 AUTO）
        width, height, fps: 输出参数

    Returns:
        ImageEditorResult
    """
    from video_director import doctor, pipeline, runlog

    if not image_paths:
        raise ImageEditorError("image_paths 为空，无法生成图片动画")

    pid = project_id or "image-editor"
    base = config.project_dir(pid)
    dirs = config.init_project_dirs(base)
    warnings: List[str] = []

    def _fail(stage_err: str):
        try:
            store.set_stage(pid, store.Stage.FAILED, error=stage_err[:2000])
        except Exception:
            pass

    try:
        store.init_db()
        style_contract, style_warnings = style_mod.select_style(script, style_intent)
        warnings.extend(style_warnings)
        try:
            store.create_project(pid, script, {
                "voice": voice, "mode": "image_editor",
                "width": width, "height": height, "fps": fps,
                "style_id": style_contract.style_id,
                "image_count": len(image_paths),
            })
        except Exception as exc:
            logger.warning(f"store.create_project 失败（不阻塞主流程）: {exc}")

        # Capability Gate
        from video_director import capabilities as caps_mod
        ok, blocked, _degraded = caps_mod.preflight(["tts", "renderer"])
        if not ok:
            _fail(f"CAPABILITY_MISSING: {', '.join(blocked)}")
            raise ImageEditorError(
                f"CAPABILITY_MISSING（能力缺失，禁止盲目继续）: {', '.join(blocked)}"
            )
        doctor_results = doctor.run_doctor()
        if doctor.has_blocking_failures(doctor_results):
            fail_detail = "; ".join(
                f"{r.name}: {r.detail}" for r in doctor_results if r.status == "FAIL"
            )
            _fail(f"CAPABILITY_MISSING: {fail_detail}")
            raise ImageEditorError(
                f"CAPABILITY_MISSING（环境自检未过）: {fail_detail}"
            )

        # 1. 图片分析（所有图片一次性分析）
        store.set_stage(pid, store.Stage.MATCHING)
        image_analyses = []
        for i, img_path in enumerate(image_paths):
            if not os.path.isfile(img_path):
                raise ImageEditorError(f"图片不存在: {img_path}")
            analysis = analyze_image(img_path)
            analysis["path"] = img_path
            analysis["index"] = i
            image_analyses.append(analysis)
            logger.info(
                f"image analysis [{i}] {Path(img_path).name}: "
                f"{analysis['width']}x{analysis['height']}, "
                f"mean={analysis['mean_color']}, "
                f"brightness={analysis['brightness']}, "
                f"saliency={analysis['saliency_region']}"
            )
        # 落盘分析结果
        with open(dirs["assets"] / "image_analysis.json", "w", encoding="utf-8") as f:
            json.dump(image_analyses, f, ensure_ascii=False, indent=2)

        # 2. 文案分句 + TTS
        scene_texts = pipeline.split_script_scenes(script)
        store.set_stage(pid, store.Stage.TTS)
        narration_audio, word_timeline, narration_scenes = narration_mod.synth_narration(
            scene_texts, voice, dirs["audio"]
        )
        (dirs["input"] / "script.txt").write_text(script, encoding="utf-8")
        with open(dirs["plans"] / "scenes.json", "w", encoding="utf-8") as f:
            json.dump(narration_scenes, f, ensure_ascii=False, indent=2)
        with open(dirs["alignment"] / "words.json", "w", encoding="utf-8") as f:
            json.dump(word_timeline, f, ensure_ascii=False, indent=2)
        store.set_stage(pid, store.Stage.ALIGNMENT)

        # 3. 每场景：选图 → 分析 → 裁剪 → 动画（motion 轮换）
        store.set_stage(pid, store.Stage.RENDERING)
        narration_duration = narration_scenes[-1]["start"] + narration_scenes[-1]["duration"]
        seg_dir = dirs["render"] / "segments"
        segments: List[str] = []

        # 文字位置策略：跟随 style 的 subtitle_position
        text_position = style_contract.subtitle_position
        font_size = style_contract.subtitle_font_size

        # 为每个场景选图 + motion 轮换
        scene_assignments = []
        for i, scene in enumerate(narration_scenes):
            img_idx = _match_image_to_scene(
                scene["text"], image_analyses, image_paths, i
            )
            motion = _ALL_MOTIONS[i % len(_ALL_MOTIONS)]
            scene_assignments.append({
                "scene_id": scene["id"],
                "image_index": img_idx,
                "image_path": image_paths[img_idx],
                "motion": motion,
                "duration": scene["duration"],
            })
        with open(dirs["plans"] / "image_assignments.json", "w", encoding="utf-8") as f:
            json.dump(scene_assignments, f, ensure_ascii=False, indent=2)

        try:
            for i, assign in enumerate(scene_assignments):
                seg_path = str(seg_dir / f"{i + 1:04d}.mp4")
                segments.append(render_image_animation(
                    img=assign["image_path"],
                    text=narration_scenes[i]["text"],
                    duration=assign["duration"],
                    out_path=seg_path,
                    w=width, h=height, fps=fps,
                    motion=assign["motion"],
                    text_position=text_position,
                    font_size=font_size,
                ))
            visual_path = renderer.concat_segments(
                segments, str(dirs["render"] / "visual.mp4"), width, height, fps
            )
        except (renderer.RenderError, ImageEditorError) as exc:
            _fail(f"RENDER_FAILED: {exc}")
            raise ImageEditorError(f"RENDER_FAILED: {exc}") from exc

        # 4. 混音（narration 主音轨）
        # 构建 IR 仅为满足 mix_audio/qc 的结构校验（scenes 必须非空且无缝铺满）。
        # 实际视觉段已由 render_image_animation 渲染并 concat 完成；
        # 这里用 image 层重建一个结构合规的 IR（不参与渲染，只供下游消费）。
        from video_director.models import (
            AudioTrack, AudioTrackType, CompositionIR, Layer, LayerType,
            ProjectSpec, SceneIR,
        )
        ir_scenes = [
            SceneIR(
                id=ns["id"],
                start=ns["start"],
                duration=ns["duration"],
                text=ns["text"],
                layers=[Layer(
                    type=LayerType.image,
                    start=0.0,
                    duration=ns["duration"],
                    motion=scene_assignments[i]["motion"],
                )],
            )
            for i, ns in enumerate(narration_scenes)
        ]
        ir = CompositionIR(
            project=ProjectSpec(width=width, height=height, fps=fps, duration=narration_duration),
            style_id=style_contract.style_id,
            scenes=ir_scenes,
            audio=[AudioTrack(
                type=AudioTrackType.narration,
                start=0.0,
                duration=narration_duration,
            )],
        )
        with open(dirs["plans"] / "composition.json", "w", encoding="utf-8") as f:
            json.dump(json.loads(ir.model_dump_json()), f, ensure_ascii=False, indent=2)
        try:
            master_path = renderer.mix_audio(
                visual_path, ir, {}, narration_audio, str(dirs["render"] / "master.mp4")
            )
        except renderer.RenderError as exc:
            _fail(f"AUDIO_MIX_FAILED: {exc}")
            raise ImageEditorError(f"AUDIO_MIX_FAILED: {exc}") from exc

        # 5. 字幕烧录
        srt_path = dirs["render"] / "master.srt"
        srt_path.write_text(
            subtitle_mod.build_srt_from_scenes(narration_scenes), encoding="utf-8"
        )
        try:
            final_path = renderer.subtitle_burn_in(
                master_path, str(srt_path), str(dirs["render"] / "final.mp4")
            )
        except renderer.RenderError as exc:
            _fail(f"SUBTITLE_FAILED: {exc}")
            raise ImageEditorError(f"SUBTITLE_FAILED: {exc}") from exc

        # 6. QC + 交付
        store.set_stage(pid, store.Stage.QC)
        report = qc_mod.run_qc(
            ir, final_path, {}, narration_duration, expect_audio=True
        )
        report.save(str(dirs["qc"] / "deterministic.json"))
        if not report.ok:
            _fail(f"QC_FAILED: {json.dumps(report.to_dict(), ensure_ascii=False)}")
            raise ImageEditorError(f"QC 失败: {report.to_dict()}")
        final_out = dirs["output"] / "final.mp4"
        if Path(final_path) != final_out:
            shutil.copyfile(final_path, final_out)
        try:
            store.finish_project(pid, str(final_out), narration_duration, report.to_dict())
        except Exception as exc:
            logger.warning(f"store.finish_project 失败（不阻塞交付）: {exc}")

        result = ImageEditorResult(
            project_dir=str(base),
            final_path=str(final_out),
            narration_duration=narration_duration,
            qc_report=report.to_dict(),
            analysis=image_analyses,
            warnings=warnings,
        )
        # Run Manifest
        try:
            manifest = runlog.build_run_manifest(
                project_id=pid,
                workflow="image-editor-compose",
                script_path=str(dirs["input"] / "script.txt"),
                asset_paths=image_paths,
                providers={
                    "tts": "edge-tts",
                    "alignment": "edge-tts-word-boundaries",
                    "vision": "image-analysis-pil-or-ffmpeg",
                    "llm": "none",
                },
                verification={
                    "doctor": "PASS",
                    "deterministic_qc": result.qc_report.get("verdict", "?"),
                },
                artifact_path=result.final_path,
                warnings=result.warnings,
            )
            runlog.save_run_manifest(base, manifest)
        except Exception as exc:
            logger.warning(f"run manifest 写入失败（不阻塞交付）: {exc}")
        return result
    except ImageEditorError:
        raise
    except Exception as exc:
        _fail(f"IMAGE_EDITOR_FAILED: {exc}")
        logger.exception(f"compose_images_video 未预期失败: {exc}")
        raise ImageEditorError(f"IMAGE_EDITOR_FAILED: {exc}") from exc
