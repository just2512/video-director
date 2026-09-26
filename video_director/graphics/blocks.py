"""Creative Block Catalog（GFX-009）：自撰视觉原语块库。

方案 §15-19/§27/§28：Block = 视觉结构 + 可编辑槽位 + motion 绑定 + 风格契约。
这里每个 Block 是一个纯 Python 渲染函数，产出**确定性 HTML 片段**（内联 CSS，
无外链字体/图片/脚本，动画用 @keyframes + 负 animation-delay 逐帧 scrub）。

商业边界：全部自撰 HTML/CSS（origin=own/CLEAR），不引第三方字体/图标库/动画库。
动画能力用纯 CSS transform+opacity，与 compiler 注入的 FrameAdapter 无关，
scrub 由 render/preview 层的负 delay 驱动。

命名约定：
    kb.*   knowledge（知识动效：概念/定义/对比/流程/数字/结论）
    pd.*   product（产品介绍：hero/feature/cta）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Tuple

# ---------------------------------------------------------------------------
# Block 定义
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Block:
    id: str
    name: str
    category: str  # knowledge | product
    semantic_intents: Tuple[str, ...]
    slots: Tuple[str, ...]
    duration_min: float
    duration_max: float
    render: Callable[["Block", dict, float, str], str]  # (slot_values, duration, style_id) -> html


def _escape(s) -> str:
    """HTML 转义（slot 值是用户/AI 内容，进入 innerHTML 前必须转义）。"""
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def motion_keyframe_name(motion: str) -> str:
    """motion id → @keyframes 名（--anim 变量用）。"""
    return _MOTION_KEYFRAMES.get(motion, _MOTION_KEYFRAMES["graphics.fade-up"])[0]


def _scene_shell(block_id: str, inner: str, duration: float, style_id: str, motion: str) -> str:
    """clip 外壳：data-block 标识 + --dur/--anim 变量。

    动效 keyframes 由 compiler 顶层注入；--anim 让每个 scene 精确绑定自己的
    motion（避免多个 motion 的 animation-name 规则互相覆盖）。
    --t/--dur 由 FrameAdapter 运行时设置（负 delay 逐帧 scrub）。
    """
    accent = "#4a90d9" if style_id != "warm" else "#d98a4a"
    anim_name = motion_keyframe_name(motion)
    return (
        f'<section class="gfx-scene" data-block="{block_id}" '
        f'style="--dur:{duration:.3f}s;--anim:{anim_name};--accent:{accent}">'
        f'{inner}</section>'
    )


# motion → 确定性 @keyframes（只用 transform/opacity，GPU 合成器友好）
# 值：@keyframes 名 → 对应 CSS（含 scrub 规则里的 animation-name 引用）
_MOTION_KEYFRAMES = {
    "graphics.fade-up": ("gfxfadeup",
                         "@keyframes gfxfadeup{from{opacity:0;transform:translateY(6%)}to{opacity:1;transform:none}}"),
    "graphics.scale-punch": ("gfxpunch",
                             "@keyframes gfxpunch{from{opacity:0;transform:scale(.82)}to{opacity:1;transform:scale(1)}}"),
    "graphics.slide-left": ("gfxslide",
                            "@keyframes gfxslide{from{opacity:0;transform:translateX(-6%)}to{opacity:1;transform:none}}"),
    "graphics.word-relay": ("gfxword",
                            "@keyframes gfxword{from{opacity:0;transform:translateY(10%)}to{opacity:1;transform:none}}"),
    "graphics.rise-soft": ("gfxrise",
                           "@keyframes gfxrise{from{opacity:0;transform:translateY(12%)}to{opacity:1;transform:none}}"),
}


def _motion_style(motion: str, duration: float) -> str:
    # 返回 CSS 规则：keyframes + .gfx-anim（负 delay + paused 逐帧 scrub）。
    # animation-name 用 var(--anim)（scene 级变量），避免多 motion 规则互相覆盖；
    # 动画 duration 用 var(--dur)，负 delay 用 var(--t,0s)（FrameAdapter 设置）。
    kf_name, kf_css = _MOTION_KEYFRAMES.get(
        motion, _MOTION_KEYFRAMES["graphics.fade-up"])
    return (
        f"{kf_css}\n"
        f".gfx-scene .gfx-anim{{animation-name:var(--anim);"
        f"animation-duration:var(--dur);animation-timing-function:cubic-bezier(.16,1,.3,1);"
        f"animation-delay:calc(-1 * var(--t, 0s));animation-fill-mode:both;"
        f"animation-play-state:paused;}}"
    )


# ---------------------------------------------------------------------------
# 通用排版片段（用来组合各 block，保持素描级视觉诚实）
# ---------------------------------------------------------------------------

def _typography(title: str, body: str, duration: float) -> str:
    t = _escape(title)
    b = _escape(body)
    return (
        f'<div class="gfx-anim gfx-type">'
        f'<div class="gfx-kicker">GRAPHIC</div>'
        f'<h2 class="gfx-title">{t}</h2>'
        f'<p class="gfx-body">{b}</p>'
        f"</div>"
    )


def _base_css(style_id: str) -> str:
    accent = "#4a90d9" if style_id != "warm" else "#d98a4a"
    return (
        f".gfx-scene{{position:absolute;inset:0;display:flex;align-items:center;"
        f"justify-content:center;background:linear-gradient(160deg,#101418,#1a2530);"
        f"color:#e8e8e8;font-family:'Microsoft YaHei',system-ui,sans-serif;padding:0 8%;}}"
        f".gfx-type{{text-align:center;max-width:82%;}}"
        f".gfx-kicker{{color:{accent};font-size:2.2vw;letter-spacing:.35em;"
        f"font-weight:700;margin-bottom:2vh;text-transform:uppercase;}}"
        f".gfx-title{{font-size:6vw;line-height:1.18;margin:0 0 3vh;color:#fff;font-weight:800;}}"
        f".gfx-body{{font-size:2.6vw;line-height:1.7;color:#b8c2ca;margin:0;}}"
        f".gfx-num{{font-size:11vw;font-weight:900;color:{accent};line-height:1;}}"
        f".gfx-cell{{background:#0d1116;border:1px solid #2a343d;border-radius:2vh;"
        f"padding:2.5vh 3vh;margin:1.4vh 0;}}"
        f".gfx-cell .gfx-cell-head{{color:#8ab4d8;font-size:2vw;font-weight:700;}}"
    )


# ---------------------------------------------------------------------------
# Knowledge blocks（§27 知识动效视觉原语）
# ---------------------------------------------------------------------------

def _render_kb_title(b: Block, sv: dict, duration: float, style_id: str) -> str:
    return _render_kb_generic(b, sv, duration, style_id)


def _render_kb_generic(b: Block, sv: dict, duration: float, style_id: str) -> str:
    title = sv.get("title", "标题")
    body = sv.get("body", "要点")
    inner = f"{_typography(title, body, duration)}"
    return _scene_shell(b.id, inner, duration, style_id, "graphics.fade-up")


def _render_kb_statistic(b: Block, sv: dict, duration: float, style_id: str) -> str:
    num = _escape(sv.get("value", "80%"))
    title = _escape(sv.get("title", "观点"))
    body = _escape(sv.get("body", ""))
    inner = (
        f'<div class="gfx-anim gfx-type">'
        f'<div class="gfx-num">{num}</div>'
        f'<h2 class="gfx-title" style="font-size:3.4vw">{title}</h2>'
        f'<p class="gfx-body">{body}</p>'
        f"</div>"
    )
    return _scene_shell(b.id, inner, duration, style_id, "graphics.scale-punch")


def _render_kb_comparison(b: Block, sv: dict, duration: float, style_id: str) -> str:
    left = _escape(sv.get("left", "A"))
    right = _escape(sv.get("right", "B"))
    title = _escape(sv.get("title", "对比"))
    inner = (
        f'<div class="gfx-anim gfx-type">'
        f'<div class="gfx-kicker">{title}</div>'
        f'<div style="display:flex;gap:4vw;width:100%;text-align:left">'
        f'<div class="gfx-cell" style="flex:1"><div class="gfx-cell-head">是</div>'
        f'<div style="font-size:2.4vw;line-height:1.6">{left}</div></div>'
        f'<div class="gfx-cell" style="flex:1"><div class="gfx-cell-head">不是</div>'
        f'<div style="font-size:2.4vw;line-height:1.6">{right}</div></div>'
        f"</div></div>"
    )
    return _scene_shell(b.id, inner, duration, style_id, "graphics.rise-soft")


def _render_kb_conclusion(b: Block, sv: dict, duration: float, style_id: str) -> str:
    title = _escape(sv.get("title", "一句话总结"))
    body = _escape(sv.get("body", ""))
    inner = (
        f'<div class="gfx-anim gfx-type" style="border-top:3px solid var(--accent);padding-top:5vh">'
        f'<div class="gfx-kicker">CONCLUSION</div>'
        f'<h2 class="gfx-title">{title}</h2>'
        f'<p class="gfx-body">{body}</p>'
        f"</div>"
    )
    return _scene_shell(b.id, inner, duration, style_id, "graphics.fade-up")


# ---------------------------------------------------------------------------
# Product blocks（§28 产品介绍视觉原语）
# ---------------------------------------------------------------------------

def _render_pd_hero(b: Block, sv: dict, duration: float, style_id: str) -> str:
    title = _escape(sv.get("title", "产品名"))
    body = _escape(sv.get("body", "一句话价值"))
    inner = (
        f'<div class="gfx-anim gfx-type">'
        f'<div class="gfx-kicker">PRODUCT</div>'
        f'<h2 class="gfx-title">{title}</h2>'
        f'<p class="gfx-body">{body}</p>'
        f"</div>"
    )
    return _scene_shell(b.id, inner, duration, style_id, "graphics.scale-punch")


def _render_pd_feature(b: Block, sv: dict, duration: float, style_id: str) -> str:
    title = _escape(sv.get("title", "功能点"))
    body = _escape(sv.get("body", "说明"))
    inner = (
        f'<div class="gfx-anim gfx-type">'
        f'<div class="gfx-kicker">FEATURE</div>'
        f'<h2 class="gfx-title" style="font-size:3.8vw">{title}</h2>'
        f'<div class="gfx-cell"><div style="font-size:2.4vw;line-height:1.7">{body}</div></div>'
        f"</div>"
    )
    return _scene_shell(b.id, inner, duration, style_id, "graphics.fade-up")


def _render_pd_cta(b: Block, sv: dict, duration: float, style_id: str) -> str:
    title = _escape(sv.get("title", "立即体验"))
    body = _escape(sv.get("body", "CTA 一句话"))
    inner = (
        f'<div class="gfx-anim gfx-type">'
        f'<h2 class="gfx-title">{title}</h2>'
        f'<p class="gfx-body">{body}</p>'
        f'<div style="margin-top:4vh;font-size:2.4vw;color:var(--accent);font-weight:700">→ NOW</div>'
        f"</div>"
    )
    return _scene_shell(b.id, inner, duration, style_id, "graphics.fade-up")


# ---------------------------------------------------------------------------
# Catalog 注册表
# ---------------------------------------------------------------------------

_CATALOG: Tuple[Block, ...] = (
    Block("kb.title", "概念标题", "knowledge",
          ("knowledge", "concept", "title", "opening"),
          ("title", "body"), 2.0, 6.0, _render_kb_title),
    Block("kb.definition", "定义卡", "knowledge",
          ("knowledge", "definition", "concept"),
          ("title", "body"), 2.5, 6.0, _render_kb_generic),
    Block("kb.statistic", "数字冲击", "knowledge",
          ("knowledge", "statistic", "data", "number"),
          ("value", "title", "body"), 2.0, 6.0, _render_kb_statistic),
    Block("kb.comparison", "对比卡", "knowledge",
          ("knowledge", "comparison", "contrast"),
          ("title", "left", "right"), 3.0, 6.0, _render_kb_comparison),
    Block("kb.conclusion", "结论卡", "knowledge",
          ("knowledge", "conclusion", "summary", "closing"),
          ("title", "body"), 2.0, 6.0, _render_kb_conclusion),
    Block("pd.hero", "产品 Hero", "product",
          ("product", "hero", "introduction"),
          ("title", "body"), 2.5, 6.0, _render_pd_hero),
    Block("pd.feature", "卖点卡", "product",
          ("product", "feature", "benefit"),
          ("title", "body"), 2.5, 6.0, _render_pd_feature),
    Block("pd.cta", "行动号召", "product",
          ("product", "cta", "closing"),
          ("title", "body"), 2.0, 5.0, _render_pd_cta),
)


def get_block(block_id: str) -> Block:
    for b in _CATALOG:
        if b.id == block_id:
            return b
    raise KeyError(f"block {block_id} 不在 Creative Catalog")


def all_blocks() -> List[Block]:
    return list(_CATALOG)


def render_block(block: Block, slot_values: dict, duration: float, style_id: str) -> str:
    """block → 确定性 HTML 片段（含 clip shell + 动效 keyframes）。"""
    if not (block.duration_min - 0.01 <= duration <= block.duration_max + 0.01):
        raise ValueError(
            f"block {block.id} 时长 {duration}s 超出 {block.duration_min}-{block.duration_max}s"
        )
    return block.render(block, slot_values, duration, style_id)


def block_motion_style(motion: str, duration: float) -> str:
    """给定 motion 产出确定性 @keyframes + .gfx-anim scrub 规则。

    负 delay + paused：由 render/preview 层设置 --t（frame/fps），
    浏览器停在该帧的精确视觉态——frame 是唯一时间事实。
    """
    return _motion_style(motion, duration)


def graphics_base_css(style_id: str) -> str:
    return _base_css(style_id)