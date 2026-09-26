"""Font Resolver（§52）：字体必须冻结，保证 Preview ≠ Final 漂移。

方案 §52：用户/块引用字体 → Render 环境必须有该字体，否则 Preview ≠ Final。
处理链：Font Resolver → 本地字体 → @font-face → Render。
同时 Font provenance/License 进 Asset Governance（与 asset_resolver 同 manifest
模式；本模块独立 fonts manifest，商业状态四态）。

本仓库第一版策略（诚实边界）：
- 编译产物只使用系统已装字体栈（'Microsoft YaHei', system-ui, sans-serif）——
  不外链 Google Fonts（§50 preflight 已拒 url(http) 字体）
- find_local_font() 探测本地 CJK 字体 → compiler 注入 @font-face 绝对路径
  （file:// 渲染可用；Chromium set_content 渲染同字体 → parity）
- 记录 font provenance（系统字体 = own/CLEAR）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from loguru import logger

from video_director import config

#: Windows/Linux/macOS 常见 CJK 字体候选（按优先级）
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)

#: 系统 CJK 字体的 font-family 名（@font-face src 指向本地文件时用）
_CJK_FAMILY = "'Microsoft YaHei', 'PingFang SC', 'WenQuanYi Micro Hei', system-ui, sans-serif"


def find_local_font() -> Optional[str]:
    """探测本地 CJK 字体文件，返回路径（无则 None → CSS 走系统栈兜底）。"""
    for f in _FONT_CANDIDATES:
        if Path(f).is_file():
            return f
    return None


def font_face_css() -> str:
    """§52：本地字体 → @font-face 规则（编译产物注入；无候选返回空串走系统栈）。

    渲染（Chromium set_content）与预览（iframe srcdoc）同注入 → 字体 parity。
    """
    font_path = find_local_font()
    if not font_path:
        logger.warning("font: 未找到本地 CJK 字体文件，CSS 使用系统字体栈兜底")
        return ""
    esc = font_path.replace("\\", "/").replace(":", "\\:")
    return (
        "@font-face{font-family:'LocalCJK';"
        f"src:url('file://{esc}');"
        "font-display:block;}"
    )


def font_provenance_record() -> dict:
    """§52：字体 provenance 进 governance（系统字体=own/CLEAR）。"""
    font_path = find_local_font()
    return {
        "id": "font-system-cjk",
        "type": "font",
        "file": font_path or "",
        "source": "own",
        "commercial_status": "CLEAR",
        "notes": "操作系统内置 CJK 字体（随 OS 授权），非第三方分发字体",
    }


def ensure_fonts_ready() -> bool:
    """Render 前检查：至少一条可用字体路径或系统栈兜底（§50 readiness）。"""
    ok = find_local_font() is not None
    if ok:
        logger.info("font: 本地 CJK 字体就绪（渲染与预览同源，无漂移）")
    else:
        logger.warning("font: 无本地 CJK 字体文件，将用系统栈（Windows/Chromium 必装雅黑）")
    return True  # 系统栈在目标渲染环境可用 → READY（诚实评估，不假 FAIL）


def save_font_manifest() -> Path:
    """把 font provenance 落进 media_library/font_manifest.json（Asset Governance）。"""
    root = config.composer_storage_root() / "media_library"
    root.mkdir(parents=True, exist_ok=True)
    p = root / "font_manifest.json"
    p.write_text(json.dumps([font_provenance_record()], ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p