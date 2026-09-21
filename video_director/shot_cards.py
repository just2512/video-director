"""镜头配方卡注册表（借 video-shotcraft 镜头卡分类学思想，自写 10 张核心卡）。

shotcraft 思想吸收（SKILL.md §1-§5）：
1. 镜头配方卡 = {name, 一句话, 适用, 时长, 能量, 标签, 动作key} 的结构化契约
   —— 但本系统的 motion 字段是给产品渲染器用的枚举，不是 Remotion TSX 路径
2. 视觉语言从产品主色/设计 tokens 生长，不另造皮肤（§2）
3. 每镜头只讲一个动效；关键信息落定后 hold ≥1s（§4）
4. SFX 语义分类（camera/impact/light/riser/transition...）由 audio 模块消费

与 video-shotcraft 的边界：
- shotcraft 卡 = Remotion/TSX demo 源码 + 精调参数（157 张）
- 本系统卡 = FFmpeg 原生动效枚举（zoom_in/pan/crop/callout/spotlight/...）
  适用于「产品图 + FFmpeg」场景，零 TSX 依赖
- 卡的 one_liner/energy/duration_hint 语义对齐 shotcraft，便于未来映射

不破坏现有 models.Layer.motion 字段（那里是 slow_zoom_in/out/pan_left/right）；
本模块卡的 motion key 服务于 product_promo 渲染器，两者通过 ProductPromoRenderer
内部映射关联，不互相 import。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from video_director import config


# ---------------------------------------------------------------------------
# 数据契约
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShotCard:
    """镜头配方卡（frozen：不可变，符合编码规范）。

    字段语义对齐 shotcraft frontmatter：
        name         卡名（snake_case，唯一）
        category     分类（opening/product/feature/transition/closing）
        one_liner    一句话意图（shotcraft "一句话"）
        energy       能量等级 low/mid/high（shotcraft "能量"）
        duration_hint_s  建议时长（秒，shotcraft "时长"近似）
        motion       渲染动作 key（product_promo.ProductPromoRenderer 消费）
        requires_text 是否需要文字层（callout 类卡为 True）
    """

    name: str
    category: str
    one_liner: str
    energy: str
    duration_hint_s: float
    motion: str
    requires_text: bool


# ---------------------------------------------------------------------------
# 10 张核心卡（分类学受 shotcraft 启发，文案与 motion key 自写）
# ---------------------------------------------------------------------------

_SHOT_CARDS: Tuple[ShotCard, ...] = (
    # ---- opening（开场，2 张）----
    ShotCard(
        name="spotlight_focus",
        category="opening",
        one_liner="暗场聚光灯收拢锁定主体，开场只讲一件事：这个产品是主角",
        energy="mid",
        duration_hint_s=4.0,
        motion="spotlight",
        requires_text=False,
    ),
    ShotCard(
        name="hero_zoom_in",
        category="opening",
        one_liner="全幅产品图从远到近缓推，品牌名随 zoom 同步浮现",
        energy="mid",
        duration_hint_s=3.5,
        motion="zoom_in",
        requires_text=True,
    ),
    # ---- product（产品展示，3 张）----
    ShotCard(
        name="three_sixty_rotate_sim",
        category="product",
        one_liner="产品图水平方向缓慢平移模拟 360 环绕，展示多角度",
        energy="low",
        duration_hint_s=4.5,
        motion="pan",
        requires_text=False,
    ),
    ShotCard(
        name="detail_macro",
        category="product",
        one_liner="裁剪聚焦产品细节区域，放大特写展示工艺",
        energy="low",
        duration_hint_s=3.0,
        motion="crop_focus",
        requires_text=False,
    ),
    ShotCard(
        name="color_pop",
        category="product",
        one_liner="主色背景淡入 + 产品图从中心放大浮现，主色与产品共生",
        energy="mid",
        duration_hint_s=3.5,
        motion="color_reveal",
        requires_text=False,
    ),
    # ---- feature（卖点，2 张）----
    ShotCard(
        name="three_point_list",
        category="feature",
        one_liner="主色背景上三个卖点逐条淡入（每条间隔 0.8s），产品图左侧固定",
        energy="mid",
        duration_hint_s=5.0,
        motion="list_reveal",
        requires_text=True,
    ),
    ShotCard(
        name="text_callout",
        category="feature",
        one_liner="产品图上方放大卖点关键词，主色作为 accent 描边脉冲一次",
        energy="high",
        duration_hint_s=3.0,
        motion="callout_pulse",
        requires_text=True,
    ),
    # ---- transition（转场，2 张）----
    ShotCard(
        name="soft_zoom_fade",
        category="transition",
        one_liner="当前产品图缓慢 zoom_out 同时 opacity 淡出，下一张从黑场淡入",
        energy="low",
        duration_hint_s=2.0,
        motion="zoom_out_fade",
        requires_text=False,
    ),
    ShotCard(
        name="color_wipe",
        category="transition",
        one_liner="主色作为色块从左到右 wipe 覆盖画面，下一场景从色块中浮现",
        energy="mid",
        duration_hint_s=2.0,
        motion="wipe",
        requires_text=False,
    ),
    # ---- closing（收尾，1 张）----
    ShotCard(
        name="logo_lockup",
        category="closing",
        one_liner="产品图居中缩小到 logo 位，品牌名+标语 lockup 落定 hold ≥1s",
        energy="mid",
        duration_hint_s=4.0,
        motion="logo_shrink",
        requires_text=True,
    ),
    ShotCard(
        name="cta_call",
        category="closing",
        one_liner="主色背景全屏 + 行动号召文案居中放大，箭头/按钮装饰",
        energy="high",
        duration_hint_s=3.5,
        motion="cta_fullscreen",
        requires_text=True,
    ),
)


# ---------------------------------------------------------------------------
# 默认镜头编排（opening → product → feature → transition → closing 循环）
# ---------------------------------------------------------------------------


def default_shot_sequence() -> List[str]:
    """默认场景到镜头的编排顺序（shot_names 未显式指定时用此循环）。

    按 opening → product → feature → transition → closing 的叙事弧编排，
    场景多于卡时按 category 循环（避免每张图相同动效）。
    """
    return [
        "spotlight_focus",        # 开场
        "hero_zoom_in",           # 开场/产品
        "three_sixty_rotate_sim", # 产品
        "color_pop",              # 产品
        "detail_macro",           # 产品/卖点
        "three_point_list",       # 卖点
        "text_callout",           # 卖点
        "soft_zoom_fade",         # 转场
        "color_wipe",             # 转场
        "logo_lockup",            # 收尾
        "cta_call",               # 收尾
    ]


#: 按 category 分组的默认循环池（场景数 >11 时按 category 轮换）
_CATEGORY_CYCLE: Dict[str, List[str]] = {
    "opening": ["spotlight_focus", "hero_zoom_in"],
    "product": ["three_sixty_rotate_sim", "detail_macro", "color_pop"],
    "feature": ["three_point_list", "text_callout"],
    "transition": ["soft_zoom_fade", "color_wipe"],
    "closing": ["logo_lockup", "cta_call"],
}


#: 场景序号 → 期望 category 的弧线映射（11+ 场景时按此分配 category）
_CATEGORY_ARC = [
    "opening", "opening", "product", "product", "product",
    "feature", "feature", "transition", "transition",
    "closing", "closing",
]


# ---------------------------------------------------------------------------
# 查询接口
# ---------------------------------------------------------------------------


def get_shot_cards() -> List[ShotCard]:
    """返回全部镜头配方卡（顺序固定，便于测试断言）。"""
    return list(_SHOT_CARDS)


def get_card(name: str) -> ShotCard:
    """按名取卡；不存在抛 KeyError（诚实暴露，禁止静默返回默认）。

    shotcraft 原则：用卡必须先校验名存在，凭名新写 = 放弃调校积累。
    """
    for card in _SHOT_CARDS:
        if card.name == name:
            return card
    raise KeyError(f"ShotCard 不存在: {name}（可用: {list(c.name for c in _SHOT_CARDS)}）")


def cards_by_category() -> Dict[str, List[ShotCard]]:
    """按 category 分组返回。"""
    result: Dict[str, List[ShotCard]] = {}
    for card in _SHOT_CARDS:
        result.setdefault(card.category, []).append(card)
    return result


def assign_shots(
    scene_count: int,
    shot_names: List[str] = None,
) -> List[str]:
    """为 N 个场景分配镜头卡名。

    - shot_names 显式指定：长度必须 == scene_count（调用方责任）
    - 否则按 default_shot_sequence 分配；场景多于 11 时按 _CATEGORY_ARC 循环
      再从对应 category 池里轮换取卡（保证相邻场景动效不同）
    """
    if shot_names is not None:
        if len(shot_names) != scene_count:
            raise ValueError(
                f"shot_names 长度 {len(shot_names)} != scene_count {scene_count}"
            )
        # 校验每张卡存在（早失败，不进渲染器才报）
        for name in shot_names:
            get_card(name)
        return shot_names

    if scene_count <= 0:
        return []

    default_seq = default_shot_sequence()
    if scene_count <= len(default_seq):
        return default_seq[:scene_count]

    # 场景多于默认序列：按 category 弧线分配，每 category 内轮换
    result: List[str] = []
    category_cursors: Dict[str, int] = {}
    used_names = set()

    for i in range(scene_count):
        category = _CATEGORY_ARC[i % len(_CATEGORY_ARC)]
        pool = _CATEGORY_CYCLE[category]
        # 在该 category 池里找一张未用过的；用完则允许重复但跳过上一张
        cursor = category_cursors.get(category, 0)
        chosen = None
        for offset in range(len(pool)):
            candidate = pool[(cursor + offset) % len(pool)]
            if candidate not in used_names:
                chosen = candidate
                category_cursors[category] = (cursor + offset + 1) % len(pool)
                break
        if chosen is None:
            # 池全用过：取下一张（与上一张不同即可）
            chosen = pool[cursor % len(pool)]
            category_cursors[category] = (cursor + 1) % len(pool)
        result.append(chosen)
        used_names.add(chosen)
        # 保留最近 3 张已用，避免紧邻重复（滑动窗口）
        if len(used_names) > max(len(pool) for pool in _CATEGORY_CYCLE.values()):
            # 池已轮完一轮，重置窗口允许 category 内自然循环
            used_names.clear()
            used_names.add(chosen)

    return result


__all__ = [
    "ShotCard",
    "get_shot_cards",
    "get_card",
    "cards_by_category",
    "assign_shots",
    "default_shot_sequence",
]
