"""产品广告片主入口（借 shotcraft 视觉语言从产品主色生长思想，§2）。

    文案 → TTS → 产品图主色提取 → 镜头卡分配 → 逐场景 FFmpeg 渲染
    → concat → 字幕烧录 → QC → 状态机落库 → run manifest

与 shotcraft 的边界：
- shotcraft 用 Remotion/TSX + 浏览器截图 + 2.5D 相机；本系统用 FFmpeg 原生
  zoompan/crop/drawtext + 产品图直接渲染
- shotcraft 的"视觉语言从产品 tokens 生长"（§2）= 本系统的 derive_visual_spec
  从产品图主色推导 {bg_color, accent_color, text_color}
- shotcraft 的"每镜头只讲一个动效"（§4）= 本系统的 ShotCard.motion 一一对应
  一个 FFmpeg 动效，镜头间动效差异由 assign_shots 保证
- 不 import image_editor.py（并行 Agent 开发中，避免依赖冲突）
- 不破坏现有 pipeline.compose_auto / script_only.compose_script_only 签名
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

from video_director import (
    config,
    narration as narration_mod,
    pipeline,
    prober,
    qc as qc_mod,
    renderer,
    shot_cards,
    store,
    style as style_mod,
    subtitle as subtitle_mod,
)
from video_director.shot_cards import ShotCard


class ProductPromoError(Exception):
    """产品广告片结构性失败。"""


# ---------------------------------------------------------------------------
# 产品主色提取 → 视觉 spec（shotcraft §2：视觉语言从产品主色生长）
# ---------------------------------------------------------------------------


def derive_visual_spec(image_paths: List[str]) -> Dict[str, str]:
    """从产品图提取主色 → {bg_color, accent_color, text_color, style_note}。

    策略（PIL 优先，无 PIL 则 ffmpeg 缩图 + rawvideo 直方图）：
        1. 缩图到 64x64（降采样，避免边缘像素干扰）
        2. 计算各通道直方图，取众数附近作为主色
        3. 主色深 → 背景浅色系、文字深色
           主色浅 → 背景深色系（主色同色系暗化）、文字浅色
        4. accent = 主色互补色或主色本身（视深浅而定）

    返回字段：
        bg_color      背景色（0xRRGGBB，供 lavfi color 源）
        accent_color  强调色（0xRRGGBB，用于描边/装饰）
        text_color    文字色（white/black 或主色互补）
        style_note    人类可读风格说明（供 run manifest 追溯）
    """
    if not image_paths:
        # 无图兜底：深蓝灰背景 + 白字 + 琥珀 accent（与 script_only.BG_COLOR 同色系）
        return {
            "bg_color": "0x1a1a2e",
            "accent_color": "0xffa500",
            "text_color": "white",
            "style_note": "无产品图，默认深蓝灰背景+琥珀 accent 兜底",
        }

    primary_rgb = _extract_primary_color(image_paths[0])
    if primary_rgb is None:
        return {
            "bg_color": "0x1a1a2e",
            "accent_color": "0xffa500",
            "text_color": "white",
            "style_note": "主色提取失败，默认深蓝灰背景+琥珀 accent 兜底",
        }

    r, g, b = primary_rgb
    # 亮度估算（Rec.709 luma）
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    is_dark = luma < 128

    if is_dark:
        # 主色深 → 背景用主色浅化版（主色 + 80 各通道），文字用深色
        bg_r = min(255, r + 80)
        bg_g = min(255, g + 80)
        bg_b = min(255, b + 80)
        bg_color = f"0x{bg_r:02x}{bg_g:02x}{bg_b:02x}"
        accent_color = f"0x{r:02x}{g:02x}{b:02x}"  # accent = 主色本身
        text_color = "0x222222"
        style_note = (
            f"产品主色偏深(RGB {r},{g},{b})，背景浅化+主色 accent+深文字，"
            f"暗产品在浅背景上凸显体积"
        )
    else:
        # 主色浅 → 背景用主色暗化版（主色同色系深），文字浅色
        bg_r = max(0, int(r * 0.25))
        bg_g = max(0, int(g * 0.25))
        bg_b = max(0, int(b * 0.25))
        bg_color = f"0x{bg_r:02x}{bg_g:02x}{bg_b:02x}"
        # accent = 主色互补色（色环 180°）
        comp_r = 255 - r
        comp_g = 255 - g
        comp_b = 255 - b
        accent_color = f"0x{comp_r:02x}{comp_g:02x}{comp_b:02x}"
        text_color = "white"
        style_note = (
            f"产品主色偏浅(RGB {r},{g},{b})，背景同色系暗化+互补色 accent+白文字，"
            f"浅产品在深背景上凸显轮廓"
        )

    return {
        "bg_color": bg_color,
        "accent_color": accent_color,
        "text_color": text_color,
        "style_note": style_note,
    }


def _extract_primary_color(image_path: str) -> Optional[Tuple[int, int, int]]:
    """从单张图提取主色 RGB。

    PIL 优先（venv 有）；无 PIL 用 ffmpeg 缩图 + rawvideo + Python 端直方图。
    """
    # 优先尝试 PIL
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            img = img.convert("RGB").resize((64, 64))
            pixels = list(img.getdata())
        return _dominant_rgb(pixels)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning(f"PIL 主色提取失败 {image_path}: {exc}; 降级 ffmpeg")

    # 降级：ffmpeg 缩图 + rawvideo + Python 直方图
    return _ffmpeg_dominant_color(image_path)


def _dominant_rgb(pixels: List[Tuple[int, int, int]]) -> Tuple[int, int, int]:
    """从像素列表取主色（量化到 32 级后众数，避免边缘色干扰）。"""
    # 量化到 8 级（0,32,64,...,224）减少噪声
    buckets: Dict[Tuple[int, int, int], int] = {}
    for r, g, b in pixels:
        key = (r // 32 * 32, g // 32 * 32, b // 32 * 32)
        buckets[key] = buckets.get(key, 0) + 1
    # 众数
    best = max(buckets, key=lambda k: buckets[k])
    return best


def _ffmpeg_dominant_color(image_path: str) -> Optional[Tuple[int, int, int]]:
    """ffmpeg 缩图到 64x64 rawvideo → Python 端量化众数。"""
    ff = prober._resolve_ffmpeg()
    try:
        completed = subprocess.run(
            [ff, "-y", "-loglevel", "error",
             "-i", image_path,
             "-vf", "scale=64:64:force_original_aspect_ratio=decrease,"
                    "pad=64:64:color=black,format=rgb24",
             "-frames:v", "1",
             "-f", "rawvideo", "-"],
            capture_output=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning(f"ffmpeg 主色提取失败 {image_path}: {exc}")
        return None

    data = completed.stdout
    if len(data) < 64 * 64 * 3:
        logger.warning(f"ffmpeg rawvideo 数据不足 {len(data)}/{64*64*3}")
        return None

    pixels: List[Tuple[int, int, int]] = []
    for i in range(0, len(data), 3):
        if i + 2 < len(data):
            pixels.append((data[i], data[i + 1], data[i + 2]))
    return _dominant_rgb(pixels)


# ---------------------------------------------------------------------------
# 中文折行（复用 script_only 的零依赖折行，避免 import 循环）
# ---------------------------------------------------------------------------

import re


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9]+(?:['\-][A-Za-z0-9]+)*|.", text)


def _display_units(token: str) -> float:
    if token and token[0].isascii() and token[0].isalnum():
        return max(1.0, len(token) * 0.6)
    return 1.0


def _wrap_text(text: str, width: int, font_size: int, margin_x: int = 80) -> List[str]:
    """按显示宽度逐字折行（零依赖，不依赖 PIL）。"""
    max_units = max(4.0, (width - margin_x * 2) / font_size)
    lines: List[str] = []
    for para in re.split(r"\n+", text.strip()):
        if not para:
            continue
        cur, cur_u = "", 0.0
        for token in _tokenize(para):
            u = _display_units(token)
            if cur and cur_u + u > max_units:
                lines.append(cur)
                cur, cur_u = "", 0.0
            while u > max_units and len(token) > 1:
                cut = max(1, int(len(token) * max_units / u))
                lines.append(token[:cut])
                token = token[cut:]
                u = _display_units(token)
            cur += token
            cur_u += u
        if cur:
            lines.append(cur)
    return lines


# ---------------------------------------------------------------------------
# 字体探测 + 路径转义（复用 script_only 模式）
# ---------------------------------------------------------------------------

_CJK_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
)

TEXT_MARGIN_X = 80
LINE_SPACING_RATIO = 0.42


def find_cjk_font() -> Optional[str]:
    """按候选顺序探测中文字体。"""
    for cand in _CJK_FONT_CANDIDATES:
        if Path(cand).is_file():
            return cand
    return None


def _esc_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:")


def _path_arg(path: str) -> str:
    return f"'{_esc_path(path)}'"


def _emit_line_files(lines: List[str], out_dir: Path, prefix: str) -> List[str]:
    """每行写一个 utf-8 txt（drawtext textfile 方式）。"""
    paths: List[str] = []
    for i, line in enumerate(lines):
        p = out_dir / f"{prefix}.line{i}.txt"
        p.write_text(line, encoding="utf-8")
        paths.append(str(p))
    return paths


# ---------------------------------------------------------------------------
# 单场景渲染：产品图 + ShotCard 动效（FFmpeg 原生）
# ---------------------------------------------------------------------------


def render_product_scene(
    scene_text: str,
    image_path: Optional[str],
    card: ShotCard,
    visual_spec: Dict[str, str],
    duration: float,
    out_dir: Path,
    seq: int,
    width: int,
    height: int,
    fps: int,
    font_path: Optional[str],
    font_size: int = 56,
) -> str:
    """渲染单个场景为标准化 segment（无音轨）。

    根据卡.motion 选择 FFmpeg 动效：
        zoom_in            产品图 zoompan 推近 + 可选文字
        zoom_out_fade      产品图 zoompan 拉远 + 淡出
        pan                产品图水平平移（zoompan x 偏移）
        crop_focus         产品图 crop 聚焦细节
        spotlight          暗场 + 产品图亮度渐显
        color_reveal       主色背景淡入 + 产品图从中心放大
        list_reveal        主色背景 + 三条卖点文字逐条淡入
        callout_pulse      产品图 + 关键词描边脉冲
        wipe               主色色块从左到右覆盖
        logo_shrink        产品图居中缩小到 logo 位 + 品牌名淡入
        cta_fullscreen     主色背景全屏 + CTA 文字放大
    """
    if duration <= 0:
        raise ProductPromoError(f"render_product_scene: duration 非法 {duration}")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(out_dir / f"{seq:04d}.mp4")
    frames = max(1, round(duration * fps))
    ff = renderer._ffmpeg()

    bg_color = visual_spec.get("bg_color", "0x1a1a2e")
    accent_color = visual_spec.get("accent_color", "0xffa500")
    text_color = visual_spec.get("text_color", "white")

    vf_parts: List[str] = []
    inputs: List[str] = []
    is_lavfi_bg = False  # 是否用 lavfi color 作背景

    motion = card.motion

    # ---- 确定视觉源 ----
    if image_path and os.path.isfile(image_path):
        # 产品图：cover 适配 + 动效
        probe = prober.probe_image(image_path)
        src_w = probe.width or width
        src_h = probe.height or height
        cover_vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )

        if motion == "zoom_in":
            # zoompan 从 1.0 推到 1.15（借 renderer.py slow_zoom_in 参数模式）
            zmax = 1.15
            vf = (
                f"scale={width * 2}:{height * 2},"
                f"zoompan=z='1+({zmax}-1)*on/{max(frames - 1, 1)}'"
                f":x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2'"
                f":d={frames}:s={width}x{height}:fps={fps}"
            )
            inputs = ["-loop", "1", "-i", image_path]
            vf_parts = [vf]
        elif motion == "zoom_out_fade":
            # zoompan 从 1.15 拉到 1.0 + 末尾 0.5s 淡出
            vf = (
                f"scale={width * 2}:{height * 2},"
                f"zoompan=z='1.15-0.15*on/{max(frames - 1, 1)}'"
                f":x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2'"
                f":d={frames}:s={width}x{height}:fps={fps},"
                f"fade=t=out:st={max(0, duration - 0.5):.3f}:d=0.5"
            )
            inputs = ["-loop", "1", "-i", image_path]
            vf_parts = [vf]
        elif motion == "pan":
            # zoompan 固定 zoom=1.1，x 从左到右平移
            vf = (
                f"scale={width * 2}:{height * 2},"
                f"zoompan=z=1.1:x='iw/2-(iw/zoom)*on/{max(frames - 1, 1)}'"
                f":y='ih/2-(ih/zoom)/2'"
                f":d={frames}:s={width}x{height}:fps={fps}"
            )
            inputs = ["-loop", "1", "-i", image_path]
            vf_parts = [vf]
        elif motion == "crop_focus":
            # crop 聚焦中心 60% 区域 + 轻微 zoom
            crop_w = int(width * 0.6)
            crop_h = int(height * 0.6)
            crop_x = (width - crop_w) // 2
            crop_y = (height - crop_h) // 2
            vf = (
                f"{cover_vf},"
                f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                f"scale={width}:{height},"
                f"zoompan=z='1+0.08*on/{max(frames - 1, 1)}'"
                f":x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2'"
                f":d={frames}:s={width}x{height}:fps={fps}"
            )
            inputs = ["-loop", "1", "-i", image_path]
            vf_parts = [vf]
        elif motion == "spotlight":
            # 暗场背景 + 产品图亮度渐显（借 vignette + fade in）
            vf = (
                f"{cover_vf},"
                f"fade=t=in:st=0:d=1.0,"
                f"eq=brightness=-0.15:saturation=1.2,"
                f"vignette=PI/4"
            )
            inputs = ["-loop", "1", "-i", image_path]
            vf_parts = [vf]
        elif motion == "color_reveal":
            # 主色背景 + 产品图从中心放大 + 淡入
            # 两输入：lavfi bg + 产品图 overlay
            inputs = [
                "-f", "lavfi", "-i",
                f"color=c={bg_color}:s={width}x{height}:r={fps}:d={duration + 1.0:.3f}",
                "-loop", "1", "-i", image_path,
            ]
            # 产品图：从 0.5x 放大到 1.0x + 淡入
            reveal_vf = (
                f"[1:v]scale={width * 2}:{height * 2},"
                f"zoompan=z='0.5+0.5*on/{max(frames - 1, 1)}'"
                f":x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2'"
                f":d={frames}:s={width}x{height}:fps={fps},"
                f"fade=t=in:st=0:d=0.8[fg];"
                f"[0:v][fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2[v]"
            )
            vf_parts = [reveal_vf]
        elif motion == "callout_pulse":
            # 产品图 cover + 上方关键词描边脉冲
            vf = f"{cover_vf},fade=t=in:st=0:d=0.5"
            inputs = ["-loop", "1", "-i", image_path]
            vf_parts = [vf]
            # 文字层（callout）加到 vf 后
            if font_path and card.requires_text and scene_text.strip():
                callout_lines = _wrap_text(scene_text, width, font_size + 8)[:1]
                txt_paths = _emit_line_files(callout_lines, out_dir, f"{seq:04d}.callout")
                # 上方位置 + 描边脉冲（borderw 模拟）
                pulse_expr = f"1+0.15*sin(2*PI*t/1.5)"
                for i, tp in enumerate(txt_paths):
                    vf_parts.append(
                        f"drawtext=fontfile={_path_arg(font_path)}"
                        f":textfile={_path_arg(tp)}"
                        f":fontcolor={text_color}:fontsize={font_size + 16}"
                        f":bordercolor={accent_color}"
                        f":borderw=4"
                        f":x=(w-text_w)/2:y={height * 0.12 + i * (font_size + 24):.1f}"
                    )
        elif motion == "logo_shrink":
            # 产品图 cover + 从 1.0 缩到 0.6 居中（logo 位）
            vf = (
                f"scale={width * 2}:{height * 2},"
                f"zoompan=z='1-0.4*on/{max(frames - 1, 1)}'"
                f":x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2'"
                f":d={frames}:s={width}x{height}:fps={fps}"
            )
            inputs = ["-loop", "1", "-i", image_path]
            vf_parts = [vf]
        elif motion == "list_reveal":
            # 主色背景 + 产品图左侧固定 + 三条卖点逐条淡入
            inputs = [
                "-f", "lavfi", "-i",
                f"color=c={bg_color}:s={width}x{height}:r={fps}:d={duration + 1.0:.3f}",
                "-loop", "1", "-i", image_path,
            ]
            # 产品图左侧 40% 宽度固定
            side_w = int(width * 0.4)
            side_vf = (
                f"[1:v]scale={side_w}:{height}:force_original_aspect_ratio=increase,"
                f"crop={side_w}:{height}[side];"
                f"[0:v][side]overlay=0:0[v]"
            )
            vf_parts = [side_vf]
            # 三条卖点（按逗号/分号切分）
            points = [p.strip() for p in re.split(r"[，；;,]", scene_text) if p.strip()][:3]
            if not points:
                points = [scene_text[:20]]
            for i, pt in enumerate(points):
                tp_lines = _wrap_text(pt, int(width * 0.55), font_size)
                tps = _emit_line_files(tp_lines, out_dir, f"{seq:04d}.pt{i}")
                # 右侧 45% 区域，每条间隔 0.8s 淡入
                start_t = i * 0.8
                y_pos = int(height * 0.25 + i * (font_size * 2.5))
                for j, tp in enumerate(tps):
                    vf_parts.append(
                        f"drawtext=fontfile={_path_arg(font_path)}"
                        f":textfile={_path_arg(tp)}"
                        f":fontcolor={text_color}:fontsize={font_size}"
                        f":x={int(width * 0.45)}"
                        f":y={y_pos + j * (font_size + 16)}"
                        f":alpha='if(lt(t,{start_t}),0,if(lt(t,{start_t + 0.4}),"
                        f"(t-{start_t})/0.4,1))'"
                    )
        elif motion == "wipe":
            # 主色色块从左到右覆盖产品图
            inputs = [
                "-loop", "1", "-i", image_path,
                "-f", "lavfi", "-i",
                f"color=c={accent_color}:s={width}x{height}:r={fps}:d={duration + 1.0:.3f}",
            ]
            wipe_vf = (
                f"[0:v]{cover_vf}[base];"
                f"[base][1:v]overlay=x='min(0,t/{max(duration * 0.6, 0.1):.2f})*w'[v]"
            )
            vf_parts = [wipe_vf]
        elif motion == "cta_fullscreen":
            # 主色背景全屏 + CTA 文字放大居中
            inputs = ["-f", "lavfi", "-i",
                      f"color=c={bg_color}:s={width}x{height}:r={fps}:d={duration + 1.0:.3f}"]
            is_lavfi_bg = True
            if font_path and card.requires_text and scene_text.strip():
                cta_lines = _wrap_text(scene_text, width, font_size + 12)
                tps = _emit_line_files(cta_lines, out_dir, f"{seq:04d}.cta")
                for i, tp in enumerate(tps):
                    vf_parts.append(
                        f"drawtext=fontfile={_path_arg(font_path)}"
                        f":textfile={_path_arg(tp)}"
                        f":fontcolor={text_color}:fontsize={font_size + 16}"
                        f":bordercolor={accent_color}:borderw=3"
                        f":x=(w-text_w)/2:y={height * 0.4 + i * (font_size + 24):.1f}"
                        f":alpha='if(lt(t,0.3),0,if(lt(t,0.8),(t-0.3)/0.5,1))'"
                    )
        else:
            # 未知 motion：cover 适配 + 轻微 zoom（兜底，不静默忽略）
            logger.warning(f"未知 motion={motion}，降级 cover+slow_zoom_in")
            vf = (
                f"scale={width * 2}:{height * 2},"
                f"zoompan=z='1+0.06*on/{max(frames - 1, 1)}'"
                f":x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2'"
                f":d={frames}:s={width}x{height}:fps={fps}"
            )
            inputs = ["-loop", "1", "-i", image_path]
            vf_parts = [vf]
    else:
        # 无产品图：纯色背景 + 文字（类似 script_only 模式）
        inputs = ["-f", "lavfi", "-i",
                  f"color=c={bg_color}:s={width}x{height}:r={fps}:d={duration + 1.0:.3f}"]
        is_lavfi_bg = True
        if font_path and card.requires_text and scene_text.strip():
            lines = _wrap_text(scene_text, width, font_size)
            tps = _emit_line_files(lines, out_dir, f"{seq:04d}.txt")
            for i, tp in enumerate(tps):
                y0 = max(0.0, (height - len(lines) * (font_size + 16)) / 2)
                vf_parts.append(
                    f"drawtext=fontfile={_path_arg(font_path)}"
                    f":textfile={_path_arg(tp)}"
                    f":fontcolor={text_color}:fontsize={font_size}"
                    f":x=(w-text_w)/2:y={y0 + i * (font_size + 16):.1f}"
                )

    # ---- 构建命令 ----
    # 对于有 overlay filter 的场景，用 -filter_complex；其余用 -vf
    has_filter_complex = any("[v]" in p for p in vf_parts)

    cmd = [ff, "-y", "-loglevel", "error"]
    cmd.extend(inputs)

    if has_filter_complex:
        # filter_complex 需要最后一个 [v] 标签
        fc = ";".join(vf_parts)
        cmd += ["-filter_complex", fc, "-map", "[v]"]
    elif vf_parts:
        cmd += ["-vf", ",".join(vf_parts)]

    cmd += [
        "-an",
        "-frames:v", str(frames),
        "-r", str(fps),
        "-c:v", config.DEFAULT_VIDEO_CODEC,
        "-preset", "veryfast",
        "-crf", str(config.DEFAULT_CRF),
        "-pix_fmt", config.DEFAULT_PIXEL_FORMAT,
        out_path,
    ]

    try:
        renderer._run(cmd)
    except renderer.RenderError as exc:
        raise ProductPromoError(f"product segment 渲染失败 (motion={motion}): {exc}") from exc

    expect = frames / fps
    probe = prober.probe_media(out_path)
    if probe.duration is None or abs(probe.duration - expect) > 0.15:
        raise ProductPromoError(
            f"segment 时长 {probe.duration} 与预期 {expect:.3f}s 偏差超 0.15s: {out_path}"
        )
    logger.info(
        f"product segment {seq:04d} [{card.name}/{motion}] {probe.duration:.3f}s"
    )
    return out_path


# ---------------------------------------------------------------------------
# 主入口：文案 → 产品图 → final.mp4
# ---------------------------------------------------------------------------


@dataclass
class ProductPromoResult:
    """compose_product_promo 结果。"""

    project_dir: str
    final_path: str
    narration_duration: float
    qc_report: dict
    warnings: List[str] = field(default_factory=list)
    visual_spec: Dict[str, str] = field(default_factory=dict)
    shots_used: List[str] = field(default_factory=list)


def compose_product_promo(
    script: str,
    product_image_paths: List[str],
    project_id: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
    style_intent: Optional[str] = None,
    shot_names: Optional[List[str]] = None,
    width: int = config.DEFAULT_RESOLUTION[0],
    height: int = config.DEFAULT_RESOLUTION[1],
    fps: int = config.DEFAULT_FPS,
) -> ProductPromoResult:
    """产品广告片全链路：文案 + 产品图 → final.mp4。

    步骤：
        1. 文案分句（pipeline.split_script_scenes）
        2. TTS（narration.synth_narration）
        3. derive_visual_spec 提取产品主色 → 视觉 spec
        4. assign_shots 分配镜头卡（默认弧线；shot_names 显式指定则用）
        5. 逐场景渲染（render_product_scene，每场景一张图+一个动效）
        6. concat → 字幕烧录 → QC → 状态机落库 → run manifest
        7. 返回 ProductPromoResult

    不破坏 pipeline.compose_auto 签名；独立入口，不 import image_editor。
    """
    from video_director import doctor, runlog

    pid = project_id or "product-promo"
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
        # Style Contract 选择（V2 §17/§18）
        style_contract, style_warnings = style_mod.select_style(script, style_intent)
        warnings.extend(style_warnings)

        try:
            store.create_project(pid, script, {
                "voice": voice, "mode": "product_promo",
                "width": width, "height": height, "fps": fps,
                "style_id": style_contract.style_id,
                "image_count": len(product_image_paths),
            })
        except Exception as exc:
            logger.warning(f"store.create_project 失败（不阻塞主流程）: {exc}")

        # Capability Gate：必需能力缺失在生成前失败
        doctor_results = doctor.run_doctor()
        if doctor.has_blocking_failures(doctor_results):
            fail_detail = "; ".join(
                f"{r.name}: {r.detail}" for r in doctor_results if r.status == "FAIL"
            )
            _fail(f"CAPABILITY_MISSING: {fail_detail}")
            raise ProductPromoError(
                f"CAPABILITY_MISSING（环境自检未过，禁止盲目继续）: {fail_detail}"
            )

        # 1. 文案分句 + TTS
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

        # 2. 产品主色 → 视觉 spec（shotcraft §2）
        visual_spec = derive_visual_spec(product_image_paths)
        (dirs["plans"] / "visual_spec.json").write_text(
            json.dumps(visual_spec, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"visual spec: {visual_spec}")

        # 3. 镜头卡分配
        store.set_stage(pid, store.Stage.MATCHING)
        resolved_shot_names = shot_cards.assign_shots(
            len(narration_scenes), shot_names
        )
        cards_used = [shot_cards.get_card(n) for n in resolved_shot_names]
        # 验证动效有轮换（至少 2 种不同 motion，避免每张图一样）
        motions_used = {c.motion for c in cards_used}
        if len(motions_used) == 1 and len(cards_used) > 1:
            warnings.append(
                f"全部场景使用同一动效 {motions_used}（建议检查 shot_names 配置）"
            )
        with open(dirs["plans"] / "shot_assignments.json", "w", encoding="utf-8") as f:
            json.dump([{
                "scene": s["id"], "shot": n, "motion": c.motion,
                "card": c.name, "category": c.category,
            } for s, n, c in zip(narration_scenes, resolved_shot_names, cards_used)],
                f, ensure_ascii=False, indent=2)
        store.set_stage(pid, store.Stage.PLANNING)

        # 4. 逐场景渲染
        store.set_stage(pid, store.Stage.RENDERING)
        narration_duration = narration_scenes[-1]["start"] + narration_scenes[-1]["duration"]
        seg_dir = dirs["render"] / "segments"
        segments: List[str] = []
        font_path = find_cjk_font()
        if font_path is None:
            warnings.append("未找到中文字体（msyh/msyhbd/simhei 均缺失），文字层降级")

        for i, scene in enumerate(narration_scenes):
            card = cards_used[i]
            # 轮换使用产品图（场景多于图时循环）
            img_path = (
                product_image_paths[i % len(product_image_paths)]
                if product_image_paths else None
            )
            try:
                seg = render_product_scene(
                    scene_text=scene["text"],
                    image_path=img_path,
                    card=card,
                    visual_spec=visual_spec,
                    duration=scene["duration"],
                    out_dir=seg_dir,
                    seq=i + 1,
                    width=width,
                    height=height,
                    fps=fps,
                    font_path=font_path,
                    font_size=style_contract.subtitle_font_size,
                )
                segments.append(seg)
            except (renderer.RenderError, ProductPromoError) as exc:
                _fail(f"RENDER_FAILED scene {scene['id']}: {exc}")
                raise ProductPromoError(f"RENDER_FAILED scene {scene['id']}: {exc}") from exc

        # 5. concat
        try:
            visual_path = renderer.concat_segments(
                segments, str(dirs["render"] / "visual.mp4"), width, height, fps
            )
        except renderer.RenderError as exc:
            _fail(f"CONCAT_FAILED: {exc}")
            raise ProductPromoError(f"CONCAT_FAILED: {exc}") from exc

        # 6. 混音（narration 主音轨）+ 字幕烧录
        # 构造 IR 供 mix_audio 使用（narration track + 与 narration_scenes 对齐的场景层，
        # 避免 CompositionIR scenes=[] 校验失败）
        from video_director.models import (
            AudioTrack, AudioTrackType, CompositionIR, Layer, LayerType,
            ProjectSpec, SceneIR,
        )
        ir_scenes = [
            SceneIR(
                id=s["id"],
                start=s["start"],
                duration=s["duration"],
                text=s["text"],
                layers=[Layer(
                    type=LayerType.image,
                    start=0.0,
                    duration=s["duration"],
                    motion=cards_used[i].motion,
                )],
            )
            for i, s in enumerate(narration_scenes)
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
        # 保存完整 IR（供追溯）
        with open(dirs["plans"] / "composition.json", "w", encoding="utf-8") as f:
            json.dump(json.loads(ir.model_dump_json()), f, ensure_ascii=False, indent=2)
        try:
            master_path = renderer.mix_audio(
                visual_path, ir, {}, narration_audio, str(dirs["render"] / "master.mp4")
            )
        except renderer.RenderError as exc:
            _fail(f"AUDIO_MIX_FAILED: {exc}")
            raise ProductPromoError(f"AUDIO_MIX_FAILED: {exc}") from exc

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
            raise ProductPromoError(f"SUBTITLE_FAILED: {exc}") from exc

        # 7. QC + 交付
        store.set_stage(pid, store.Stage.QC)
        report = qc_mod.run_qc(
            ir, final_path, {}, narration_duration, expect_audio=True
        )
        report.save(str(dirs["qc"] / "deterministic.json"))
        if not report.ok:
            _fail(f"QC_FAILED: {json.dumps(report.to_dict(), ensure_ascii=False)}")
            raise ProductPromoError(f"QC 失败: {report.to_dict()}")

        final_out = dirs["output"] / "final.mp4"
        if Path(final_path) != final_out:
            import shutil
            shutil.copyfile(final_path, final_out)

        try:
            store.finish_project(pid, str(final_out), narration_duration, report.to_dict())
        except Exception as exc:
            logger.warning(f"store.finish_project 失败（不阻塞交付）: {exc}")

        # (dirs["plans"] / "style.json") 落盘
        (dirs["plans"] / "style.json").write_text(
            json.dumps({
                "style_id": style_contract.style_id,
                "reason": style_contract.reason,
                "contract": {
                    "subtitle_position": style_contract.subtitle_position,
                    "subtitle_font_size": style_contract.subtitle_font_size,
                    "image_motion_intensity": style_contract.image_motion_intensity,
                    "bgm_volume": style_contract.bgm_volume,
                },
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        result = ProductPromoResult(
            project_dir=str(base),
            final_path=str(final_out),
            narration_duration=narration_duration,
            qc_report=report.to_dict(),
            warnings=warnings,
            visual_spec=visual_spec,
            shots_used=resolved_shot_names,
        )

        # Run Manifest
        try:
            manifest = runlog.build_run_manifest(
                project_id=pid,
                workflow="product-promo-compose",
                script_path=str(dirs["input"] / "script.txt"),
                asset_paths=product_image_paths,
                providers={
                    "tts": "edge-tts",
                    "alignment": "edge-tts-word-boundaries",
                    "vision": "product-color-extract",
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

        store.set_stage(pid, store.Stage.DONE)
        return result

    except ProductPromoError:
        raise
    except Exception as exc:
        _fail(f"PRODUCT_PROMO_FAILED: {exc}")
        logger.exception(f"compose_product_promo 未预期失败: {exc}")
        raise ProductPromoError(f"PRODUCT_PROMO_FAILED: {exc}") from exc


__all__ = [
    "ProductPromoError",
    "ProductPromoResult",
    "ShotCard",
    "derive_visual_spec",
    "render_product_scene",
    "compose_product_promo",
    "find_cjk_font",
]
