"""Style Contract（V2 §15-§18：Style 不允许只是一个名字）。

四风格骨架（第一阶段），结构化字段而非字符串（V2 §16）：
    subtitle: 位置/字号基准/行数
    motion: 图片动效强度（zoom 幅度）
    transition: 转场密度（major effects 预算）
    audio: BGM 音量基准 / SFX 密度

选择规则（V2 §17/§18）：
- AUTO：按文案特征确定性选风格（历史/悬疑词→cinematic-story；知识/教育词→
  knowledge-explainer；情绪/生活词→minimal-premium；默认 documentary-calm）。
  不输出评分（V2 §17 禁令）。
- 用户一句描述：关键词映射到风格；映射不到则 AUTO + 记 warning（不编造风格）。

风格落地：pipeline 把 Style Contract 传给 planner/renderer/subtitle 消费。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger


@dataclass
class StyleContract:
    """结构化风格契约（V2 §16：不是一个名字）。"""

    style_id: str
    # 字幕（SVF 默认样式参考：bottom、maxLines=2、字号递减不截断）
    subtitle_position: str = "bottom"       # bottom | center | top
    subtitle_font_size: int = 56            # 基准字号（像素，1080 宽度下）
    subtitle_max_lines: int = 2
    # 动效
    image_motion_intensity: float = 1.12    # zoompan 终止倍率（越大越强）
    # 转场
    transition_budget: int = 1              # 全片效果转场上限（cut 不计）
    # 音频
    bgm_volume: float = 0.16
    sfx_density: str = "sparse"             # sparse | medium | dense
    # 选择理由（V2 §18：展示给用户，不用评分）
    reason: List[str] = field(default_factory=list)


#: 四风格骨架（V2 §15）
_STYLES = {
    "documentary-calm": StyleContract(
        style_id="documentary-calm",
        subtitle_position="bottom", subtitle_font_size=52, subtitle_max_lines=2,
        image_motion_intensity=1.08, transition_budget=1,
        bgm_volume=0.14, sfx_density="sparse",
    ),
    "knowledge-explainer": StyleContract(
        style_id="knowledge-explainer",
        subtitle_position="bottom", subtitle_font_size=60, subtitle_max_lines=2,
        image_motion_intensity=1.12, transition_budget=2,
        bgm_volume=0.16, sfx_density="medium",
    ),
    "energetic-social": StyleContract(
        style_id="energetic-social",
        subtitle_position="center", subtitle_font_size=64, subtitle_max_lines=2,
        image_motion_intensity=1.18, transition_budget=3,
        bgm_volume=0.20, sfx_density="dense",
    ),
    "cinematic-story": StyleContract(
        style_id="cinematic-story",
        subtitle_position="bottom", subtitle_font_size=48, subtitle_max_lines=1,
        image_motion_intensity=1.06, transition_budget=1,
        bgm_volume=0.12, sfx_density="sparse",
    ),
}

#: 用户描述 → 风格 关键词映射（V2 §19：自然语言换风格）
_DESC_MAP = {
    "cinematic-story": ["电影", "大片", "悬念", "悬疑", "故事", "历史", "史诗", "紧张"],
    "energetic-social": ["快节奏", "冲击", "燃", "热血", "带货", "营销", "激情", "炸裂"],
    "knowledge-explainer": ["知识", "讲解", "科普", "教学", "干货", "解释", "课程"],
    "documentary-calm": ["纪录片", "克制", "专业", "沉稳", "冷静", "严肃", "真实"],
}


def available_styles() -> List[str]:
    return list(_STYLES)


def select_style(
    script: str,
    user_intent: Optional[str] = None,
) -> tuple:
    """风格选择（V2 §17/§18）。返回 (StyleContract, warnings)。

    - user_intent 命中映射 → 对应风格
    - 否则 AUTO：文案特征词确定性选择
    - 都不中 → documentary-calm（默认）+ warning
    """
    warnings: List[str] = []

    if user_intent and user_intent.strip():
        intent = user_intent.strip()
        for style_id, words in _DESC_MAP.items():
            if any(w in intent for w in words):
                contract = _clone_with_reason(_STYLES[style_id], [
                    f"用户描述「{intent[:20]}」命中风格 {style_id}",
                ])
                return contract, warnings
        warnings.append(f"风格描述「{intent[:20]}」未命中预设，回退 AUTO 选择")

    # AUTO：文案特征词（确定性规则，无评分）
    if any(w in script for w in ("历史", "朝", "皇帝", "将军", "悬疑", "谜", "传说")):
        return _clone_with_reason(_STYLES["cinematic-story"], [
            "内容含历史/悬疑叙事", "适合克制的电影感节奏",
        ]), warnings
    if any(w in script for w in ("方法", "步骤", "原理", "如何", "技巧", "学会", "知识")):
        return _clone_with_reason(_STYLES["knowledge-explainer"], [
            "内容以知识解释为主", "字幕略大便于阅读",
        ]), warnings
    if any(w in script for w in ("！", "热血", "冲击", "改变", "突破", "立刻", "马上")):
        return _clone_with_reason(_STYLES["energetic-social"], [
            "内容情绪强烈", "适合快节奏与强调动效",
        ]), warnings
    return _clone_with_reason(_STYLES["documentary-calm"], [
        "未识别强特征，默认沉稳纪录片风",
    ]), warnings


def get_style(style_id: str) -> Optional[StyleContract]:
    st = _STYLES.get(style_id)
    return _clone_with_reason(st, []) if st else None


def _clone_with_reason(src: StyleContract, reason: List[str]) -> StyleContract:
    """不可变复制（编码规范：不 mutate 原对象）。"""
    return StyleContract(
        style_id=src.style_id,
        subtitle_position=src.subtitle_position,
        subtitle_font_size=src.subtitle_font_size,
        subtitle_max_lines=src.subtitle_max_lines,
        image_motion_intensity=src.image_motion_intensity,
        transition_budget=src.transition_budget,
        bgm_volume=src.bgm_volume,
        sfx_density=src.sfx_density,
        reason=reason,
    )


def apply_to_ir(ir, contract: StyleContract, config_module) -> None:
    """把 Style Contract 应用到全局参数与 IR（消费点收口，单一入口）。

    - 转场预算：覆盖 config.TRANSITION_MAX_MAJOR_EFFECTS（validator 消费）
    - ir 非 None 时写入 style_id（产物可追溯）
    """
    config_module.TRANSITION_MAX_MAJOR_EFFECTS = contract.transition_budget
    if ir is not None:
        ir.style_id = contract.style_id
    logger.info(f"style applied: {contract.style_id} (budget={contract.transition_budget}, "
                f"motion={contract.image_motion_intensity}, reason={contract.reason})")
