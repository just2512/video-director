"""Creative Block Catalog（§15-17/§27/§28）：自撰视觉原语块库。

方案 §16 Block 定义：视觉结构 + 可编辑槽位 + motion 绑定 + 风格契约。
方案 §17：Block（独立场景）与 Component（嵌入片段）分离。
方案 §27 knowledge 20 类 + §28 product 16 类，全部自撰 HTML/CSS
（origin=own/CLEAR），无第三方字体/图标/动画库。

渲染分层（§14 HTML 是执行格式）：
    通用排版基件（typography/number/duo/list/flow/timeline/matrix/quote…）
    × Block 定义（slots + 语义 + motion + 风格约束）
    每块产出确定性 inner HTML；render_block 统一包 §8 data-* clip shell。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from video_director.graphics.contract import RationalFps  # noqa: F401  契约对齐声明


def _escape(s) -> str:
    """HTML 转义（slot 值是用户/AI 内容，进入 innerHTML 前必须转义）。"""
    # 实体用 chr+name 拼接，避免源码里的实体名被编辑/写文件管道解码成字面尖括号
    amp = "&" + "amp;"
    lt = "&" + "lt;"
    gt = "&" + "gt;"
    return (
        str(s).replace("&", amp).replace("<", lt).replace(">", gt)
    )


@dataclass(frozen=True)
class Block:
    id: str
    name: str
    category: str  # knowledge | product
    semantic_intents: Tuple[str, ...]
    slots: Tuple[str, ...]
    duration_min: float
    duration_max: float
    motion: str  # 入场动效 id（§16 motion binding）
    render: Callable[["Block", dict, float, str], str]


# ---------------------------------------------------------------------------
# motion → 确定性 @keyframes（只用 transform/opacity，GPU 合成器友好）
# ---------------------------------------------------------------------------

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


def motion_keyframe_name(motion: str) -> str:
    """motion id → @keyframes 名（--anim 变量用）。"""
    return _MOTION_KEYFRAMES.get(motion, _MOTION_KEYFRAMES["graphics.fade-up"])[0]


def all_motion_ids() -> List[str]:
    """Motion Search（§20）：全量 motion id。"""
    return list(_MOTION_KEYFRAMES)


def block_motion_style(motion: str, duration: float) -> str:
    """motion → @keyframes + .gfx-anim scrub 规则（负 delay + paused 逐帧 scrub）。"""
    kf_name, kf_css = _MOTION_KEYFRAMES.get(
        motion, _MOTION_KEYFRAMES["graphics.fade-up"])
    return (
        f"{kf_css}\n"
        f".gfx-scene .gfx-anim{{animation-name:var(--anim);"
        f"animation-duration:var(--dur);animation-timing-function:cubic-bezier(.16,1,.3,1);"
        f"animation-delay:calc(-1 * var(--t, 0s));animation-fill-mode:both;"
        f"animation-play-state:paused;}}"
    )


def _scene_shell(block_id: str, inner: str, duration: float, style_id: str,
                 motion: str, start: float = 0.0, track_index: int = 0,
                 layout: str = "center") -> str:
    """clip 外壳：§8 Graphics Composition Contract 的 data-* 时序契约。

    data-layout 承载 §34「换布局」（center/left/split），CSS 消费：
    left → .gfx-type 文本左对齐；split → 双栏基件已有结构。
    """
    accent = "#4a90d9" if style_id != "warm" else "#d98a4a"
    anim_name = motion_keyframe_name(motion)
    layout = layout if layout in ("center", "left", "split") else "center"
    return (
        f'<section class="gfx-scene" data-block="{block_id}" '
        f'data-start="{start:.3f}" data-duration="{duration:.3f}" '
        f'data-track-index="{track_index}" data-layout="{layout}" '
        f'style="--dur:{duration:.3f}s;--anim:{anim_name};--accent:{accent}">'
        f'{inner}</section>'
    )


# ---------------------------------------------------------------------------
# 通用排版基件（§14：视觉知识在块定义层，不在 HTML 字符串层）
# ---------------------------------------------------------------------------

def _t_title(kicker: str, title: str, body: str = "") -> str:
    parts = [f'<div class="gfx-kicker">{_escape(kicker)}</div>',
             f'<h2 class="gfx-title">{_escape(title)}</h2>']
    if body:
        parts.append(f'<p class="gfx-body">{_escape(body)}</p>')
    return f'<div class="gfx-anim gfx-type">{"".join(parts)}</div>'


def _t_number(value: str, title: str, body: str = "") -> str:
    parts = [f'<div class="gfx-num">{_escape(value)}</div>',
             f'<h2 class="gfx-title" style="font-size:3.4vw">{_escape(title)}</h2>']
    if body:
        parts.append(f'<p class="gfx-body">{_escape(body)}</p>')
    return f'<div class="gfx-anim gfx-type">{"".join(parts)}</div>'


def _t_duo(label_l: str, text_l: str, label_r: str, text_r: str, kicker: str = "") -> str:
    head = f'<div class="gfx-kicker">{_escape(kicker)}</div>' if kicker else ""
    return (
        f'<div class="gfx-anim gfx-type">{head}'
        f'<div style="display:flex;gap:4vw;width:100%;text-align:left">'
        f'<div class="gfx-cell" style="flex:1"><div class="gfx-cell-head">{_escape(label_l)}</div>'
        f'<div style="font-size:2.4vw;line-height:1.6">{_escape(text_l)}</div></div>'
        f'<div class="gfx-cell" style="flex:1"><div class="gfx-cell-head">{_escape(label_r)}</div>'
        f'<div style="font-size:2.4vw;line-height:1.6">{_escape(text_r)}</div></div>'
        f'</div></div>'
    )


def _t_list(items: List[str], kicker: str = "", ordered: bool = False) -> str:
    tag = "ol" if ordered else "ul"
    lis = "".join(f'<li style="font-size:2.6vw;line-height:1.8;text-align:left">{_escape(i)}</li>'
                  for i in items)
    head = f'<div class="gfx-kicker">{_escape(kicker)}</div>' if kicker else ""
    return f'<div class="gfx-anim gfx-type">{head}<{tag} style="padding-left:6vw;margin:0">{lis}</{tag}></div>'


def _t_flow(steps: List[str], kicker: str = "") -> str:
    cells = "".join(
        f'<div class="gfx-cell" style="flex:1;text-align:center">'
        f'<div style="font-size:3vw;font-weight:800;color:var(--accent)">{i + 1}</div>'
        f'<div style="font-size:2.2vw;line-height:1.5">{_escape(s)}</div></div>'
        f'<div style="align-self:center;color:var(--accent);font-size:3vw">→</div>'
        if i < len(steps) - 1 else
        f'<div class="gfx-cell" style="flex:1;text-align:center">'
        f'<div style="font-size:3vw;font-weight:800;color:var(--accent)">{i + 1}</div>'
        f'<div style="font-size:2.2vw;line-height:1.5">{_escape(s)}</div></div>'
        for i, s in enumerate(steps))
    head = f'<div class="gfx-kicker">{_escape(kicker)}</div>' if kicker else ""
    return f'<div class="gfx-anim gfx-type">{head}<div style="display:flex;gap:2vw;width:100%">{cells}</div></div>'


def _t_timeline(events: List[str], kicker: str = "") -> str:
    rows = "".join(
        f'<div style="display:flex;gap:3vw;width:100%;text-align:left;margin:1.2vh 0">'
        f'<div class="gfx-cell-head" style="min-width:8vw;color:var(--accent)">{i + 1}</div>'
        f'<div style="font-size:2.4vw;line-height:1.5">{_escape(e)}</div></div>'
        for i, e in enumerate(events))
    head = f'<div class="gfx-kicker">{_escape(kicker)}</div>' if kicker else ""
    return f'<div class="gfx-anim gfx-type">{head}{rows}</div>'


def _t_matrix(cells: List[str], kicker: str = "") -> str:
    grid = "".join(
        f'<div class="gfx-cell" style="text-align:center"><div style="font-size:2.4vw;line-height:1.5">{_escape(c)}</div></div>'
        for c in cells)
    head = f'<div class="gfx-kicker">{_escape(kicker)}</div>' if kicker else ""
    return (
        f'<div class="gfx-anim gfx-type">{head}'
        f'<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:2vh 2vw;width:100%">{grid}</div></div>'
    )


def _t_quote(text: str, source: str = "") -> str:
    src = f'<div class="hint" style="margin-top:2vh">—— {_escape(source)}</div>' if source else ""
    return (
        f'<div class="gfx-anim gfx-type" style="border-left:4px solid var(--accent);padding-left:4vw;text-align:left">'
        f'<div style="font-size:3.2vw;line-height:1.6;color:#fff">{_escape(text)}</div>{src}</div>'
    )


def _t_kpi(items: List[str], kicker: str = "") -> str:
    """['1,250,000|用户', …] 格式 → KPI 行。"""
    cells = ""
    for it in items:
        val, _, label = it.partition("|")
        cells += (
            f'<div class="gfx-cell" style="flex:1;text-align:center">'
            f'<div class="gfx-num" style="font-size:5vw">{_escape(val)}</div>'
            f'<div class="hint">{_escape(label)}</div></div>'
        )
    head = f'<div class="gfx-kicker">{_escape(kicker)}</div>' if kicker else ""
    return f'<div class="gfx-anim gfx-type">{head}<div style="display:flex;gap:3vw;width:100%">{cells}</div></div>'


def _t_code(code: str, kicker: str = "") -> str:
    head = f'<div class="gfx-kicker">{_escape(kicker)}</div>' if kicker else ""
    return (
        f'<div class="gfx-anim gfx-type">{head}'
        f'<div class="gfx-cell" style="text-align:left;font-family:Consolas,monospace;'
        f'font-size:2.2vw;line-height:1.6;white-space:pre-wrap">{_escape(code)}</div></div>'
    )


def _split_items(sv: dict, key: str = "items") -> List[str]:
    raw = sv.get(key) or ""
    return [s.strip() for s in str(raw).split("|") if s.strip()][:6] or ["要点一", "要点二"]


# ---------------------------------------------------------------------------
# 通用 render 帮手（slot 缺省值兜底，保证编译永远出有效视觉）
# ---------------------------------------------------------------------------

def _sv(sv, key, default):
    return sv.get(key) or default


# ---------------------------------------------------------------------------
# Knowledge blocks 渲染（§27 20 类）
# ---------------------------------------------------------------------------

def _r_title(b, sv, dur, st):    return _t_title("KNOWLEDGE", _sv(sv, "title", "概念标题"), _sv(sv, "body", ""))
def _r_definition(b, sv, dur, st): return _t_title("DEFINITION", _sv(sv, "term", _sv(sv, "title", "术语")), _sv(sv, "body", "定义正文"))
def _r_concept_card(b, sv, dur, st): return _t_title("CONCEPT", _sv(sv, "title", "概念卡"), _sv(sv, "body", ""))
def _r_comparison(b, sv, dur, st): return _t_duo("A", _sv(sv, "left", "一方面"), "B", _sv(sv, "right", "另一方面"), "COMPARISON")
def _r_cause_effect(b, sv, dur, st): return _t_flow([_sv(sv, "cause", "原因"), _sv(sv, "effect", "结果")], "CAUSE → EFFECT")
def _r_process(b, sv, dur, st): return _t_flow(_split_items(sv), "PROCESS")
def _r_timeline(b, sv, dur, st): return _t_timeline(_split_items(sv), "TIMELINE")
def _r_hierarchy(b, sv, dur, st): return _t_list(_split_items(sv), "HIERARCHY", ordered=True)
def _r_matrix(b, sv, dur, st): return _t_matrix(_split_items(sv), "MATRIX")
def _r_quote(b, sv, dur, st): return _t_quote(_sv(sv, "quote", _sv(sv, "title", "引用")), _sv(sv, "source", ""))
def _r_statistic(b, sv, dur, st): return _t_number(_sv(sv, "value", "80%"), _sv(sv, "title", "观点"), _sv(sv, "body", ""))
def _r_ranking(b, sv, dur, st):
    items = _split_items(sv)
    rows = "".join(f'<div style="display:flex;gap:3vw;width:100%;text-align:left;margin:1vh 0">'
                   f'<div class="gfx-cell-head" style="color:var(--accent)">#{i + 1}</div>'
                   f'<div style="font-size:2.4vw">{_escape(x)}</div></div>' for i, x in enumerate(items))
    return f'<div class="gfx-anim gfx-type"><div class="gfx-kicker">RANKING</div>{rows}</div>'
def _r_checklist(b, sv, dur, st): return _t_list(_split_items(sv), "CHECKLIST")
def _r_before_after(b, sv, dur, st): return _t_duo("BEFORE", _sv(sv, "left", "改变前"), "AFTER", _sv(sv, "right", "改变后"), "BEFORE / AFTER")
def _r_flowchart(b, sv, dur, st): return _t_flow(_split_items(sv), "FLOWCHART")
def _r_relationship(b, sv, dur, st):
    items = _split_items(sv)
    grid = "".join(f'<div class="gfx-cell" style="text-align:center"><div style="font-size:2.2vw">{_escape(x)}</div></div>' for x in items)
    return f'<div class="gfx-anim gfx-type"><div class="gfx-kicker">RELATIONSHIP</div><div style="display:grid;grid-template-columns:repeat(2,1fr);gap:2vh 2vw;width:100%">{grid}</div></div>'
def _r_formula(b, sv, dur, st): return _t_number(_sv(sv, "value", "E=mc²"), _sv(sv, "title", _sv(sv, "term", "公式")))
def _r_code(b, sv, dur, st): return _t_code(_sv(sv, "code", "print('hello')"), "CODE")
def _r_conclusion(b, sv, dur, st):
    t = _sv(sv, "title", "一句话总结")
    return (
        f'<div class="gfx-anim gfx-type" style="border-top:3px solid var(--accent);padding-top:5vh">'
        f'<div class="gfx-kicker">CONCLUSION</div>'
        f'<h2 class="gfx-title">{_escape(t)}</h2>'
        f'<p class="gfx-body">{_escape(_sv(sv, "body", ""))}</p></div>'
    )


# ---------------------------------------------------------------------------
# Product blocks 渲染（§28 16 类）
# ---------------------------------------------------------------------------

def _p_hero(b, sv, dur, st): return _t_title("PRODUCT", _sv(sv, "title", "产品名"), _sv(sv, "body", "一句话价值"))
def _p_logo_reveal(b, sv, dur, st): return _t_number(_sv(sv, "logo", "LOGO"), _sv(sv, "title", _sv(sv, "brand", "品牌名")))
def _p_problem(b, sv, dur, st): return _t_title("PROBLEM", _sv(sv, "title", "痛点"), _sv(sv, "body", ""))
def _p_feature(b, sv, dur, st): return _t_title("FEATURE", _sv(sv, "title", "功能点"), _sv(sv, "body", "说明"))
def _p_feature_stack(b, sv, dur, st): return _t_list(_split_items(sv), "FEATURE STACK")
def _p_feature_comparison(b, sv, dur, st): return _t_duo("US", _sv(sv, "left", "我们"), "THEM", _sv(sv, "right", "他们"), "WHY US")
def _p_workflow(b, sv, dur, st): return _t_flow(_split_items(sv), "HOW IT WORKS")
def _p_before_after(b, sv, dur, st): return _t_duo("BEFORE", _sv(sv, "left", "用前"), "AFTER", _sv(sv, "right", "用后"), "BEFORE / AFTER")
def _p_screenshot_tour(b, sv, dur, st): return _t_list(_split_items(sv), "TOUR")
def _p_ui_demo(b, sv, dur, st): return _t_code(_sv(sv, "code", "操作演示步骤"), "UI DEMO")
def _p_metric(b, sv, dur, st): return _t_kpi(_split_items(sv), "METRICS")
def _p_testimonial(b, sv, dur, st): return _t_quote(_sv(sv, "quote", _sv(sv, "title", "用户说")), _sv(sv, "source", "用户"))
def _p_proof(b, sv, dur, st): return _t_kpi(_split_items(sv), "PROOF")
def _p_integration(b, sv, dur, st): return _t_flow(_split_items(sv), "INTEGRATIONS")
def _p_pricing(b, sv, dur, st): return _t_duo("FREE", _sv(sv, "left", "免费版"), "PRO", _sv(sv, "right", "专业版"), "PRICING")
def _p_cta(b, sv, dur, st):
    t = _sv(sv, "title", "立即体验")
    return (
        f'<div class="gfx-anim gfx-type">'
        f'<h2 class="gfx-title">{_escape(t)}</h2>'
        f'<p class="gfx-body">{_escape(_sv(sv, "body", ""))}</p>'
        f'<div style="margin-top:4vh;font-size:2.4vw;color:var(--accent);font-weight:700">→ NOW</div></div>'
    )


# ---------------------------------------------------------------------------
# Catalog（§27 knowledge 20 + §28 product 16；slots 按各块真实可编辑字段）
# ---------------------------------------------------------------------------

_CATALOG: Tuple[Block, ...] = (
    # ---- §27 knowledge/ 20 ----
    Block("kb.title", "概念标题", "knowledge", ("knowledge", "concept", "title", "opening"),
          ("title", "body"), 2.0, 6.0, "graphics.fade-up", _r_title),
    Block("kb.definition", "定义卡", "knowledge", ("knowledge", "definition", "concept"),
          ("term", "body"), 2.5, 6.0, "graphics.fade-up", _r_definition),
    Block("kb.concept-card", "概念卡", "knowledge", ("knowledge", "concept"),
          ("title", "body"), 2.5, 6.0, "graphics.fade-up", _r_concept_card),
    Block("kb.comparison", "对比卡", "knowledge", ("knowledge", "comparison", "contrast"),
          ("title", "left", "right"), 3.0, 6.0, "graphics.rise-soft", _r_comparison),
    Block("kb.cause-effect", "因果图", "knowledge", ("knowledge", "cause", "effect", "mechanism"),
          ("cause", "effect"), 3.0, 6.0, "graphics.slide-left", _r_cause_effect),
    Block("kb.process", "过程动画", "knowledge", ("knowledge", "process", "steps"),
          ("items",), 3.0, 8.0, "graphics.fade-up", _r_process),
    Block("kb.timeline", "时间线", "knowledge", ("knowledge", "timeline", "history"),
          ("items",), 3.0, 8.0, "graphics.word-relay", _r_timeline),
    Block("kb.hierarchy", "层级图", "knowledge", ("knowledge", "hierarchy", "structure"),
          ("items",), 3.0, 8.0, "graphics.rise-soft", _r_hierarchy),
    Block("kb.matrix", "矩阵", "knowledge", ("knowledge", "matrix", "grid"),
          ("items",), 3.0, 8.0, "graphics.fade-up", _r_matrix),
    Block("kb.quote", "引用", "knowledge", ("knowledge", "quote", "authority"),
          ("quote", "source"), 2.5, 6.0, "graphics.fade-up", _r_quote),
    Block("kb.statistic", "数字冲击", "knowledge", ("knowledge", "statistic", "data", "number"),
          ("value", "title", "body"), 2.0, 6.0, "graphics.scale-punch", _r_statistic),
    Block("kb.ranking", "排行", "knowledge", ("knowledge", "ranking", "top"),
          ("items",), 3.0, 8.0, "graphics.word-relay", _r_ranking),
    Block("kb.checklist", "清单", "knowledge", ("knowledge", "checklist", "todo"),
          ("items",), 3.0, 8.0, "graphics.fade-up", _r_checklist),
    Block("kb.before-after", "前后对比", "knowledge", ("knowledge", "before-after", "change"),
          ("title", "left", "right"), 3.0, 6.0, "graphics.rise-soft", _r_before_after),
    Block("kb.flowchart", "流程图", "knowledge", ("knowledge", "flowchart", "diagram"),
          ("items",), 3.0, 8.0, "graphics.slide-left", _r_flowchart),
    Block("kb.relationship-map", "关系图", "knowledge", ("knowledge", "relationship", "map"),
          ("items",), 3.0, 8.0, "graphics.fade-up", _r_relationship),
    Block("kb.formula", "公式", "knowledge", ("knowledge", "formula", "equation"),
          ("value", "term"), 2.5, 6.0, "graphics.scale-punch", _r_formula),
    Block("kb.code", "代码", "knowledge", ("knowledge", "code", "technical"),
          ("code",), 2.5, 8.0, "graphics.fade-up", _r_code),
    Block("kb.screenshot", "截图说明", "knowledge", ("knowledge", "screenshot", "demo"),
          ("title", "body"), 2.5, 6.0, "graphics.fade-up", _r_concept_card),
    Block("kb.conclusion", "结论卡", "knowledge", ("knowledge", "conclusion", "summary", "closing"),
          ("title", "body"), 2.0, 6.0, "graphics.word-relay", _r_conclusion),
    # ---- §28 product/ 16 ----
    Block("pd.hero", "产品 Hero", "product", ("product", "hero", "introduction"),
          ("title", "body"), 2.5, 6.0, "graphics.scale-punch", _p_hero),
    Block("pd.logo-reveal", "Logo 揭示", "product", ("product", "logo", "brand"),
          ("logo", "brand"), 2.0, 5.0, "graphics.scale-punch", _p_logo_reveal),
    Block("pd.problem", "痛点", "product", ("product", "problem", "pain"),
          ("title", "body"), 2.5, 6.0, "graphics.fade-up", _p_problem),
    Block("pd.feature", "卖点卡", "product", ("product", "feature", "benefit"),
          ("title", "body"), 2.5, 6.0, "graphics.fade-up", _p_feature),
    Block("pd.feature-stack", "功能堆叠", "product", ("product", "features", "stack"),
          ("items",), 3.0, 8.0, "graphics.word-relay", _p_feature_stack),
    Block("pd.feature-comparison", "竞品对比", "product", ("product", "comparison", "versus"),
          ("title", "left", "right"), 3.0, 6.0, "graphics.rise-soft", _p_feature_comparison),
    Block("pd.workflow", "工作流", "product", ("product", "workflow", "how"),
          ("items",), 3.0, 8.0, "graphics.slide-left", _p_workflow),
    Block("pd.before-after", "效果对比", "product", ("product", "before-after", "result"),
          ("title", "left", "right"), 3.0, 6.0, "graphics.rise-soft", _p_before_after),
    Block("pd.screenshot-tour", "界面巡礼", "product", ("product", "screenshot", "tour"),
          ("items",), 3.0, 8.0, "graphics.word-relay", _p_screenshot_tour),
    Block("pd.ui-demo", "操作演示", "product", ("product", "ui", "demo"),
          ("code",), 3.0, 8.0, "graphics.fade-up", _p_ui_demo),
    Block("pd.metric", "指标卡", "product", ("product", "metric", "data", "kpi"),
          ("items",), 3.0, 6.0, "graphics.scale-punch", _p_metric),
    Block("pd.testimonial", "用户证言", "product", ("product", "testimonial", "quote"),
          ("quote", "source"), 2.5, 6.0, "graphics.fade-up", _p_testimonial),
    Block("pd.proof", "信任背书", "product", ("product", "proof", "social"),
          ("items",), 3.0, 6.0, "graphics.rise-soft", _p_proof),
    Block("pd.integration", "集成", "product", ("product", "integration", "connect"),
          ("items",), 3.0, 8.0, "graphics.slide-left", _p_integration),
    Block("pd.pricing", "价格", "product", ("product", "pricing", "plans"),
          ("title", "left", "right"), 3.0, 6.0, "graphics.fade-up", _p_pricing),
    Block("pd.cta", "行动号召", "product", ("product", "cta", "closing"),
          ("title", "body"), 2.0, 5.0, "graphics.fade-up", _p_cta),
)


def get_block(block_id: str) -> Block:
    for b in all_blocks():
        if b.id == block_id:
            return b
    raise KeyError(f"block {block_id} 不在 Creative Catalog")


# ---------------------------------------------------------------------------
# §42 Block 开发体验：用户自定义块（New Block → BlockDefinition → Catalog）
# 存储在 storage/composer/custom_blocks.json；服务启动时 load_custom_blocks()
# 并入运行时 catalog。渲染基件复用既有块（定义语义/槽位/意图，不改执行代码）。
# ---------------------------------------------------------------------------

_CUSTOM_BLOCKS: List[Block] = []


def register_custom_block(block_id: str, name: str, category: str,
                          semantic_intents: Tuple[str, ...], slots: Tuple[str, ...],
                          duration_min: float, duration_max: float,
                          motion: str, base_block_id: str) -> Block:
    """注册用户块（§42）。渲染复用 base_block 的基件。"""
    base = get_block(base_block_id)
    blk = Block(block_id, name, category, tuple(semantic_intents), tuple(slots),
                duration_min, duration_max, motion, base.render)
    _CUSTOM_BLOCKS[:] = [b for b in _CUSTOM_BLOCKS if b.id != block_id]
    _CUSTOM_BLOCKS.append(blk)
    return blk


def load_custom_blocks(storage_root) -> int:
    """启动时加载 <composer root>/custom_blocks.json 进 catalog。返回加载数。

    storage_root = config.composer_storage_root()（已含 env 隔离；
    不再内部拼 "/composer"——那是旧 storage/composer 布局的硬编码）。
    """
    import json
    from pathlib import Path
    cfile = Path(storage_root) / "custom_blocks.json"
    if not cfile.is_file():
        return 0
    n = 0
    try:
        defs = json.loads(cfile.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    for d in defs:
        try:
            register_custom_block(
                d["id"], d["name"], d.get("category", "knowledge"),
                tuple(d.get("semantic_intents") or ["custom"]),
                tuple(d.get("slots") or ("title", "body")),
                float(d.get("duration_min", 2.0)), float(d.get("duration_max", 6.0)),
                d.get("motion", "graphics.fade-up"), d.get("base_block", "kb.title"),
            )
            n += 1
        except Exception:
            continue  # 单条坏定义不阻塞其余（诚实跳过）
    return n


def all_blocks() -> List[Block]:
    return list(_CATALOG) + list(_CUSTOM_BLOCKS)


def render_block(block: Block, slot_values: dict, duration: float, style_id: str,
                 start: float = 0.0, track_index: int = 0) -> str:
    """block → 确定性 HTML 片段（含 clip shell + §8 data-* 时序契约）。"""
    if not (block.duration_min - 0.01 <= duration <= block.duration_max + 0.01):
        raise ValueError(
            f"block {block.id} 时长 {duration}s 超出 {block.duration_min}-{block.duration_max}s"
        )
    inner = block.render(block, slot_values, duration, style_id)
    return _scene_shell(block.id, inner, duration, style_id, block.motion,
                        start=start, track_index=track_index,
                        layout=str(slot_values.get("layout") or "center"))


# ---------------------------------------------------------------------------
# Block Search（§19）/ Motion Search（§20）：intent/contentType/style/duration/
# ratio/layerTypes/requiredInputs → 过滤。零评分（§18）。
# ---------------------------------------------------------------------------

def search_blocks(semantic_intents=(), content_type: str = "",
                  duration: Optional[float] = None) -> List[Block]:
    """§19 Block Search：查询条件过滤（纯过滤，不排序不打分）。"""
    out = []
    for b in all_blocks():
        if content_type in ("knowledge", "knowledge_video", "concept") and b.category != "knowledge":
            continue
        if content_type in ("product_video", "product", "advertisement") and b.category != "product":
            continue
        if semantic_intents and not any(si in b.semantic_intents for si in semantic_intents):
            continue
        if duration is not None and not (b.duration_min - 0.01 <= duration <= b.duration_max + 0.01):
            continue
        out.append(b)
    return out


def graphics_base_css(style_id: str) -> str:
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
        # §34 换布局：data-layout 消费（left → 文本块左对齐）
        f".gfx-scene[data-layout=left] .gfx-type{{text-align:left;margin-left:4%;}}"
        f".gfx-scene[data-layout=left] .gfx-title{{font-size:5vw;}}"
        # §39 Direct Manipulation：选中态 bounds 高亮
        f".gf-sel{{outline:3px dashed #4a90d9;outline-offset:4px;}}"
    )