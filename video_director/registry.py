"""效果注册表（V2 §25/§26/§72：AI 只能 search → select → configure，不能 invent）。

注册表分两层：
- REGISTRY：schema 已知的效果名（validator 允许出现在 IR 中排队实现）
- IMPLEMENTED：当前 BasicRenderer 实际已实现的效果（超出即 REJECT，诚实降级）
"""

# ---------------------------------------------------------------------------
# 转场（V2 §25）
# ---------------------------------------------------------------------------
TRANSITION_REGISTRY = (
    "cut",
    "crossfade",
    "blur_crossfade",
    "push_left",
    "push_right",
    "zoom_through",
    "glitch",
)

#: 第一版 BasicRenderer 实际实现
TRANSITION_IMPLEMENTED = ("cut",)

# ---------------------------------------------------------------------------
# 图片 Motion（V2 §14：>2s 图片默认轻微 motion，强度由 Style 控制）
# ---------------------------------------------------------------------------
MOTION_REGISTRY = (
    "slow_zoom_in",
    "slow_zoom_out",
    "pan_left",
    "pan_right",
    "ken_burns",
)

#: 第一版 BasicRenderer 实际实现
MOTION_IMPLEMENTED = ("slow_zoom_in",)


def is_known_transition(name: str) -> bool:
    return name in TRANSITION_REGISTRY


def is_implemented_transition(name: str) -> bool:
    return name in TRANSITION_IMPLEMENTED


def is_known_motion(name: str) -> bool:
    return name in MOTION_REGISTRY


def is_implemented_motion(name: str) -> bool:
    return name is None or name in MOTION_IMPLEMENTED


def is_major_effect_transition(name: str) -> bool:
    """cut 不是效果转场；其余计数受 TRANSITION_MAX_MAJOR_EFFECTS 约束。"""
    return name != "cut"
