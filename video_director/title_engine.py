"""标题引擎（方案 §16 Title Engine：MainTitle/Subtitle 入场动效片头卡）。

第一版能力：
- compose_auto(title=...) 在成片头部插入标题卡（主标题+可选副标题）
- 动效：黑底 → 标题淡入 → 副标题随后淡入（ffmpeg drawtext alpha 表达式）
- 与主时间轴关系：标题卡为**前置段**（不压缩正文旁白），时长 title_duration
- 诚实边界：只做片头标题层（TitleLayer entrance=fade-in/hold）；
  段中 SectionTitle/Quote/CTA 属 §16 后续扩展（数据结构就绪）

CJK 字体：find_cjk_font() 沿用产品级方案（script_only 同源）。
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

from video_director import config, prober

#: 默认标题卡时长（秒）：标题淡入 0.6s + 停留 1.4s
TITLE_CARD_DEFAULT_DURATION = 2.0

_FONT_CACHE: Optional[str] = None


def find_cjk_font() -> Optional[str]:
    """找可用中文字体（黑体优先），找不到返回 None（drawtext 缺字体时降级）。"""
    global _FONT_CACHE
    if _FONT_CACHE is not None:
        return _FONT_CACHE or None
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",     # 微软雅黑
        r"C:\Windows\Fonts\msyhbd.ttc",   # 雅黑粗体
        r"C:\Windows\Fonts\simhei.ttf",   # 黑体
        r"C:\Windows\Fonts\simsun.ttc",   # 宋体
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for f in candidates:
        if Path(f).is_file():
            _FONT_CACHE = f
            return f
    _FONT_CACHE = ""
    return None


def _esc_font_path(font: str) -> str:
    """drawtext fontfile 路径转义：Windows C:\\ → C\\:/（filter 解析器要求）。"""
    return font.replace("\\", "/").replace(":", "\\:")


def _esc_drawtext(text: str) -> str:
    """drawtext 文本转义（: ' \\ % 关键字符）。"""
    out = (text.replace("\\", "\\\\").replace(":", "\\:")
           .replace("'", "\\'").replace("%", "\\%"))
    return out


def render_title_card(
    title: str,
    out_path: str,
    subtitle: Optional[str] = None,
    duration: float = TITLE_CARD_DEFAULT_DURATION,
    width: int = None,
    height: int = None,
    fps: int = None,
    style_hint: str = "",
) -> str:
    """渲染标题卡段（黑底白字，主标题淡入 → 副标题随后）。

    Args:
        title: 主标题（必填）
        subtitle: 副标题（可选，第二行、较小字号）
        duration: 卡片总时长
        style_hint: 风格提示（cinematic-story=冷色调；预留扩展）
    """
    w = width or config.DEFAULT_RESOLUTION[0]
    h = height or config.DEFAULT_RESOLUTION[1]
    f = fps or config.DEFAULT_FPS
    if not title or not title.strip():
        raise ValueError("标题不能为空")
    font = find_cjk_font()
    if font is None:
        raise RuntimeError("未找到中文字体（Windows Fonts / 系统字体目录）")

    main_size = max(48, int(w * 0.07))
    sub_size = max(28, int(w * 0.036))

    # alpha 动效：主标题 0.2s 后 0.6s 淡入；副标题 0.8s 后 0.6s 淡入
    fade_main = f"if(lt(t,0.2),0,if(lt(t,0.8),(t-0.2)/0.6,1))"
    esc_font = _esc_font_path(font)
    fades = [
        f"drawtext=fontfile='{esc_font}':text='{_esc_drawtext(title)}'"
        f":fontsize={main_size}:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2"
        f":alpha='{fade_main}'",
    ]
    if subtitle and subtitle.strip():
        fade_sub = f"if(lt(t,0.8),0,if(lt(t,1.4),(t-0.8)/0.6,1))"
        fades.append(
            f"drawtext=fontfile='{esc_font}':text='{_esc_drawtext(subtitle)}'"
            f":fontsize={sub_size}:fontcolor=0xD0D0D0:x=(w-text_w)/2"
            f":y=(h-text_h)/2+{int(main_size*1.6)}:alpha='{fade_sub}'"
        )

    # 结尾 0.3s 微淡出（与正文首个镜头衔接更自然）
    end_fade = max(0.0, duration - 0.3)
    vf = ",".join(fades) + f",fade=t=out:st={end_fade:.2f}:d=0.3"

    ff = prober._resolve_ffmpeg()
    if ff is None:
        raise RuntimeError("ffmpeg 不可用")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ff, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=black:s={w}x{h}:d={duration}:r={f}",
        "-vf", vf,
        "-c:v", config.DEFAULT_VIDEO_CODEC,
        "-pix_fmt", "yuv420p",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"标题卡渲染失败: {r.stderr[-300:]}")
    logger.info(f"title card: 「{title[:16]}」{duration:.1f}s → {out.name}")
    return str(out)


def render_lower_third_overlay(
    video_in: str,
    name: str,
    out_path: str,
    role: Optional[str] = None,
    show_duration: float = 4.0,
    fade_in: float = 0.4,
) -> str:
    """人物条（§17 Lower Third）叠加到视频左下角：名字 + 可选中粽子衔。

    诚实边界：第一版只做「口播标注」——左下角身份条，读出场处 0.4s 渐入，
    显示 show_duration 后 0.4s 渐出。personId/avatar/跟踪位置为后续扩展。

    Args:
        video_in: 输入视频
        name: 人物名（必填）
        role: 头衔/机构（可选，副行小字）
        show_duration: 显示时长（叠加入场后持续显示秒数）
        fade_in: 入场淡入秒数
    """
    if not name or not name.strip():
        raise ValueError("人物条名称不能为空")
    font = find_cjk_font()
    if font is None:
        raise RuntimeError("未找到中文字体")

    from video_director import prober
    probe = prober.probe_media(video_in)
    w, h = probe.width or config.DEFAULT_RESOLUTION[0], probe.height or config.DEFAULT_RESOLUTION[1]
    end = fade_in + show_duration

    esc_font = _esc_font_path(font)
    name_size = max(30, int(w * 0.045))
    role_size = max(22, int(w * 0.032))

    # 名字（左下）：fade_in 内淡入，end 起淡出
    alpha_expr = (
        f"if(lt(t,{fade_in}),t/{fade_in},"
        f"if(lt(t,{end}),1,if(lt(t,{end}+{fade_in}),1-(t-{end})/{fade_in},0)))"
    )
    draw = (
        f"drawtext=fontfile='{esc_font}':text='{_esc_drawtext(name)}'"
        f":fontsize={name_size}:fontcolor=white:x=60:y=h-{name_size*2}-60"
        f":box=1:boxcolor=0x80000000:boxborderw=12"
        f":alpha='{alpha_expr}'"
    )
    filters = [draw]
    if role and role.strip():
        filters.append(
            f"drawtext=fontfile='{esc_font}':text='{_esc_drawtext(role)}'"
            f":fontsize={role_size}:fontcolor=0xD0D0D0:x=60:y=h-{int(name_size*1.5)}-60"
            f":box=1:boxcolor=0x80000000:boxborderw=12"
            f":alpha='{alpha_expr}'"
        )
    vf = ",".join(filters)

    ff = prober._resolve_ffmpeg()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ff, "-y", "-loglevel", "error", "-i", video_in,
        "-vf", vf, "-c:v", config.DEFAULT_VIDEO_CODEC, "-c:a", "copy",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"人物条渲染失败: {r.stderr[-300:]}")
    logger.info(f"lower third: 「{name}」叠加 → {out.name}")
    return str(out)


def render_watermark_overlay(
    video_in: str,
    text: str,
    out_path: str,
) -> str:
    """右下角水印（全程显示，半透明白字，小字号）。"""
    if not text or not text.strip():
        return video_in
    font = find_cjk_font()
    if font is None:
        return video_in
    from video_director import prober
    probe = prober.probe_media(video_in)
    w = probe.width or config.DEFAULT_RESOLUTION[0]
    h = probe.height or config.DEFAULT_RESOLUTION[1]
    size = max(16, int(w * 0.026))
    esc_font = _esc_font_path(font)
    draw = (
        f"drawtext=fontfile='{esc_font}':text='{_esc_drawtext(text.strip())}'"
        f":fontsize={size}:fontcolor=white@0.75"
        f":x=w-text_w-{int(w * 0.03)}:y=h-text_h-{int(h * 0.02)}"
    )
    ff = prober._resolve_ffmpeg()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ff, "-y", "-loglevel", "error", "-i", video_in,
        "-vf", draw, "-c:v", config.DEFAULT_VIDEO_CODEC,
        "-preset", "veryfast", "-crf", str(config.DEFAULT_CRF),
        "-c:a", "copy", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"水印渲染失败: {r.stderr[-300:]}")
    logger.info(f"watermark: 「{text.strip()[:16]}」→ 右下角")
    return str(out)


# ---------------------------------------------------------------------------
# MPT 吸收方案 §19：WatermarkLayer 完整渲染（anchor 九宫格 / opacity /
# scale / image-logo overlay）。供 render_composition 读 ir.watermarks 调用。
# ---------------------------------------------------------------------------

#: anchor → drawtext x/y 表达式（相对 w/h，九宫格 + margin 因子）
def _anchor_xy(anchor: str, w: int, h: int, m: float) -> tuple:
    """返回 (x_expr, y_expr) 字符串。m 为边距占宽高比。"""
    xm, ym = int(w * m), int(h * m)
    if anchor == "top_left":
        return f"{xm}", f"{ym}"
    if anchor == "top_right":
        return f"w-text_w-{xm}", f"{ym}"
    if anchor == "bottom_left":
        return f"{xm}", f"h-text_h-{ym}"
    if anchor == "center":
        return "(w-text_w)/2", "(h-text_h)/2"
    # 默认 bottom_right（原 render_watermark_overlay 语义）
    return f"w-text_w-{xm}", f"h-text_h-{ym}"


def render_watermark_layer(
    video_in: str,
    wm,  # WatermarkLayer（models）
    out_path: str,
) -> str:
    """§19：按 WatermarkLayer 渲染水印（text 用 drawtext，image/logo 用 overlay）。

    anchor 九宫格定位；opacity/scale 生效；start/duration 区间未铺满时
    用 enable 表达式控制显示窗口（第一版：全程显示 text 水印是主流，
    image/logo 支持 overlay）。
    """
    from video_director import prober as _prober
    try:
        from video_director.models import WatermarkLayer  # noqa: F401
    except ImportError:
        pass
    probe = _prober.probe_media(video_in)
    w = probe.width or config.DEFAULT_RESOLUTION[0]
    h = probe.height or config.DEFAULT_RESOLUTION[1]

    ff = _prober._resolve_ffmpeg()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    raw_opacity = getattr(wm, "opacity", 0.75)
    opacity = 0.75 if raw_opacity is None else float(raw_opacity)
    if not math.isfinite(opacity) or not 0.0 <= opacity <= 1.0:
        raise ValueError(f"覆盖层 opacity 必须是有限的 0–1 数值，收到 {raw_opacity!r}")
    scale = float(getattr(wm, "scale", 1.0) or 1.0)
    anchor = getattr(wm, "anchor", "bottom_right") or "bottom_right"
    kind = getattr(wm, "kind", "text") or "text"

    if kind == "text":
        text = (getattr(wm, "text", "") or "").strip()
        if not text:
            return video_in
        font = find_cjk_font()
        if font is None:
            raise RuntimeError("无法渲染文字覆盖层：未找到可用字体；请安装中文字体后重试")
        esc_font = _esc_font_path(font)
        size = max(16, int(w * 0.026 * scale))
        # §19 safeArea：像素安全距离 → 边距比例（默认 32px，clamp [0,200]）
        safe = float(getattr(wm, "safe_area", 32) or 0)
        margin = max(0.0, min(safe / max(w, 1), 0.2))
        x_expr, y_expr = _anchor_xy(anchor, w, h, margin)
        offset_x = int(getattr(wm, "offset_x", 0) or 0)
        offset_y = int(getattr(wm, "offset_y", 0) or 0)
        if offset_x:
            x_expr = f"({x_expr}){offset_x:+d}"
        if offset_y:
            y_expr = f"({y_expr}){offset_y:+d}"
        # FFmpeg drawtext color alpha uses a decimal 0–1 value (white@0.750),
        # not the 8-bit hexadecimal alpha used by some color formats.
        alpha_value = f"{opacity:.3f}"
        # The overlay must follow the timeline interval stored in WatermarkLayer.
        # Without enable, a timed overlay is silently rendered for the entire video.
        start = max(0.0, float(getattr(wm, "start", 0.0) or 0.0))
        duration = getattr(wm, "duration", None)
        if duration is not None:
            end = max(start, start + float(duration))
            enable = f":enable='between(t,{start:.3f},{end:.3f})'"
        else:
            enable = f":enable='gte(t,{start:.3f})'" if start > 0 else ""
        draw = (
            f"drawtext=fontfile='{esc_font}':text='{_esc_drawtext(text)}'"
            f":fontsize={size}:fontcolor=white@{alpha_value}"
            f":x={x_expr}:y={y_expr}{enable}"
        )
    else:
        # image/logo 水印：overlay 一张项目素材图
        asset_id = getattr(wm, "asset_id", None)
        if not asset_id:
            return video_in
        asset_path = Path(str(asset_id))
        if not asset_path.is_file():
            # asset_id 可能是 manifest id 而非路径（渲染入口已经过 assets 表解析）
            logger.warning(f"watermark image/logo asset 非文件路径，跳过: {asset_id}")
            return video_in
        ow = max(1, int(w * 0.18 * scale))
        offset_x = int(getattr(wm, "offset_x", 0) or 0)
        offset_y = int(getattr(wm, "offset_y", 0) or 0)
        # §19 safeArea 同语义用于 image/logo overlay
        safe = int(max(0, min(float(getattr(wm, "safe_area", 32) or 0), 200)))
        xm, ym = safe, safe
        if anchor == "top_left":
            ox, oy = xm, ym
        elif anchor == "top_right":
            ox, oy = w - ow - xm, ym
        elif anchor == "bottom_left":
            ox, oy = xm, h - (ow * 9 // 16) - ym
        elif anchor == "center":
            ox, oy = (w - ow) // 2, (h - ow) // 2
        else:
            ox, oy = w - ow - xm, h - (ow * 9 // 16) - ym
        ox += offset_x
        oy += offset_y
        # The video overlay is a time-ranged timeline clip too. Apply the same
        # visibility contract as text, rather than burning logos in for the full clip.
        start = max(0.0, float(getattr(wm, "start", 0.0) or 0.0))
        duration = getattr(wm, "duration", None)
        if duration is not None:
            end = max(start, start + float(duration))
            enable = f":enable='between(t,{start:.3f},{end:.3f})'"
        else:
            enable = f":enable='gte(t,{start:.3f})'" if start > 0 else ""
        draw = (
            f"[1:v]scale={ow}:-1,format=rgba[wm];"
            f"[0:v][wm]overlay={ox}:{oy}:alpha={opacity:.2f}{enable}"
        )
        cmd = [
            ff, "-y", "-loglevel", "error",
            "-i", video_in, "-i", str(asset_path),
            "-filter_complex", draw,
            "-c:v", config.DEFAULT_VIDEO_CODEC,
            "-preset", "veryfast", "-crf", str(config.DEFAULT_CRF),
            "-c:a", "copy", str(out),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"image 水印渲染失败: {r.stderr[-300:]}")
        logger.info(f"watermark: {anchor} image/logo (scale={scale})")
        return str(out)

    cmd = [
        ff, "-y", "-loglevel", "error", "-i", video_in,
        "-vf", draw, "-c:v", config.DEFAULT_VIDEO_CODEC,
        "-preset", "veryfast", "-crf", str(config.DEFAULT_CRF),
        "-c:a", "copy", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"text 水印渲染失败: {r.stderr[-300:]}")
    logger.info(f"watermark: 「{text[:16]}」→ {anchor} (opacity={opacity}, scale={scale})")
    return str(out)
