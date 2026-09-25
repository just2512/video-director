"""Motion Compiler（REN-001）：MotionRecipe channels → FFmpeg filter 表达式。

Renderer Adapter 的编译半边（方案 §二十六）：
    MotionRecipe（Renderer 无关声明）
        ↓ 本模块（编译）
    FFmpeg filter 语法（本渲染器实现）
    同一 MotionRecipe 未来可另编译成 SVG/Canvas/CSS（Renderer Adapter 各自实现）

编译能力边界（诚实）：
    支持 property: opacity / x / y / scale（COMPILABLE_PROPERTIES）
    支持 easing: linear/sine/cubic/quart/quint/expo/back 的 in/out/inOut
    不支持的 property（crop/blur/rotation/...）→ resolver 层已判 INCOMPATIBLE，
    本模块防御性再拒（双保险，IG-MOTION Compatibility 契约）。

关键帧语义：at ∈ [0,1] 归一化进度 → 实际秒 = at × duration。
动画不改变 clip 时长（Narration Timeline 铁律）。
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from video_director.registries.recipes import (
    COMPILABLE_PROPERTIES,
    MotionRecipe,
    MotionRegistry,
)


class MotionCompileError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


# ---------------------------------------------------------------------------
# Easing → FFmpeg 表达式
# ---------------------------------------------------------------------------

#: 各滤镜支持的时间变量方言（实测 ffmpeg 7.1：geq 用 T=秒；zoompan 用 on=帧号；
#: overlay/rotate/fade 等通用滤镜表达式引擎用 t=秒）
TIME_VAR_DIALECT = {
    "geq": "T",
    "zoompan": "on",
    "generic": "t",
}


def _easing_expr(easing: Optional[dict], p_expr: str) -> str:
    """easing 定义 → 值表达式（p_expr 为归一化进度 0..1 的表达式）。

    无 easing → 线性（直接返回进度表达式）。
    输出仍是 0..1 的映射，调用方再 lerp 到起止值。
    """
    if not easing or not easing.get("type"):
        return p_expr
    etype = easing["type"]
    mode = easing.get("mode", "out")
    if etype == "linear":
        return p_expr
    base = p_expr

    if etype == "quad":
        fn = f"pow({base},2)"
    elif etype == "cubic":
        fn = f"pow({base},3)"
    elif etype == "quart":
        fn = f"pow({base},4)"
    elif etype == "quint":
        fn = f"pow({base},5)"
    elif etype == "sine":
        # out: sin(p·π/2)  in: 1-cos(p·π/2)  inOut: -(cos(π·p)-1)/2
        if mode == "in":
            fn = f"(1-cos(PI*{base}/2))"
        elif mode == "inOut":
            fn = f"(-(cos(PI*{base})-1)/2)"
        else:
            fn = f"sin(PI*{base}/2)"
    elif etype == "expo":
        if mode == "in":
            fn = f"(if(eq({base},0),0,pow(2,10*({base}-1))))"
        elif mode == "inOut":
            fn = f"(if(eq({base},0),0,if(eq({base},1),1,if(lt({base},0.5),pow(2,20*{base}-10)/2,(2-pow(2,-10*{base}+10))/2))))"
        else:
            fn = f"(if(eq({base},1),1,pow(2,-10*{base}+10)*(-1)+1))"
    elif etype == "back":
        params = easing.get("parameters") or {}
        overshoot = params.get("overshoot", 1.70158)
        if mode == "in":
            fn = f"({base}*{base}*(({overshoot}+1)*{base}-{overshoot}))"
        else:
            # out: 1 + c3*(p-1)^3 + c1*(p-1)^2（标准 back-out）
            fn = f"(1+{overshoot}*pow({base}-1,3)+({overshoot}+1)*pow({base}-1,2))"
    elif etype == "circ":
        if mode == "in":
            fn = f"(1-sqrt(1-{base}*{base}))"
        elif mode == "inOut":
            fn = f"(if(lt({base},0.5),(1-sqrt(1-pow(2*{base},2)))/2,(sqrt(1-pow(-2*{base}+2,2))+1)/2))"
        else:
            fn = f"sqrt(1-pow({base}-1,2))"
    else:
        # elastic 等复杂 easing：退化为 cubic-out（诚实降级，记录于此）
        fn = f"(1-pow(1-{base},3))"

    if mode == "in":
        return fn
    if mode == "inOut":
        return fn
    # out：p^k 族镜像（sine/expo/circ/back 已在分支内处理 out 形态）
    if etype in ("quad", "cubic", "quart", "quint"):
        return f"(1-{fn.replace(base, f'(1-{base})')})"
    return fn


def _channel_expr(channel, duration: float, time_var: str) -> str:
    """单 channel → FFmpeg 值表达式（时间变量由调用方按滤镜方言注入）。

    分段关键帧：每段 [kf_i.at, kf_{i+1}.at]（归一化）→ 实际秒区间，
    lerp(easing(progress), v_i, v_{i+1})；段外取边界值。
    """
    prop = channel.property
    kfs = sorted(channel.keyframes, key=lambda k: k.at)
    if any(isinstance(k.value, str) for k in kfs):
        raise MotionCompileError(
            "UNSUPPORTED_VALUE_TYPE", f"channel {prop}: 字符串关键帧值暂不支持"
        )
    parts: List[str] = []
    for i in range(len(kfs) - 1):
        k0, k1 = kfs[i], kfs[i + 1]
        t0 = k0.at * duration
        t1 = k1.at * duration
        v0, v1 = float(k0.value), float(k1.value)
        prog = f"(({time_var}-{t0:.3f})/max({t1 - t0:.3f},0.001))"
        eased = _easing_expr(k1.easing, prog)
        val_expr = f"({v0:.4f}+({v1 - v0:.4f})*({eased}))"
        # 段内取插值；段间按嵌套 if 链回退
        cond = f"if(lt({time_var},{t1:.3f}),{val_expr},"
        parts.append(cond)
    tail = f"{kfs[-1].value:.4f}"
    expr = "".join(parts) + tail + ")" * len(parts)
    # 段前（t < t0_first）钳到首值
    first_val = f"{kfs[0].value:.4f}"
    first_t = kfs[0].at * duration
    return f"if(lt({time_var},{first_t:.3f}),{first_val},{expr})"


# ---------------------------------------------------------------------------
# 主入口：MotionRecipe → FFmpeg filter 片段
# ---------------------------------------------------------------------------

def compile_motion_to_ffmpeg(
    motion_recipe_id: str,
    motion_version: Optional[str],
    duration: float,
    width: int,
    height: int,
    parameters: Optional[dict] = None,
    fps: Optional[float] = None,
) -> dict:
    """编译 MotionRecipe → {vf_filter, overlay_x_expr, overlay_y_expr, scale_expr}。

    返回 Renderer 消费的结构（Renderer-neutral Render IR 的 FFmpeg 方言）：
        scale_expr  : zoompan 方言（时间变量=on/fps，fps 必传否则默认 30）
        opacity_expr: geq 方言（时间变量=T 秒）
        overlay_x/y : overlay 方言（时间变量=t 秒）
        params      : 透传实例参数（staggerMs 等由 renderer 特化消费）

    方言在编译期直接嵌入（不做字符串后替换——`lt(` 等函数名会与 't' 碰撞）。
    编译原则：不改变 clip 时长；表达式全部确定性可重现。
    失败抛 MotionCompileError（UNSUPPORTED_PROPERTY/UNSUPPORTED_VALUE_TYPE）。
    """
    registry = MotionRegistry()
    try:
        recipe = registry.get(motion_recipe_id, motion_version)
    except Exception as exc:
        raise MotionCompileError("MOTION_NOT_FOUND", str(exc)) from exc

    unsupported = [c.property for c in recipe.channels if c.property not in COMPILABLE_PROPERTIES]
    if unsupported:
        raise MotionCompileError(
            "UNSUPPORTED_PROPERTY",
            f"{recipe.id}: 渲染器不支持属性 {unsupported}（resolver 应已拒绝）",
        )

    inst_duration = float(duration or recipe.duration_recommended)
    if not (recipe.duration_min <= inst_duration <= recipe.duration_max):
        raise MotionCompileError(
            "DURATION_OUT_OF_RANGE",
            f"{recipe.id}: {inst_duration}s 超出 {recipe.duration_min}-{recipe.duration_max}s",
        )

    zoompan_fps = float(fps or 30.0)
    out = {
        "motion_id": recipe.id,
        "motion_version": recipe.version,
        "duration": inst_duration,
        "scale_expr": "1.0",          # zoompan 方言（时间变量=on 帧号/帧率）
        "opacity_expr": "1.0",        # geq 方言（时间变量=T 秒）
        "overlay_x_expr": "0",        # overlay 方言（时间变量=t 秒）
        "overlay_y_expr": "0",
        "params": dict(parameters or {}),
    }
    on_var = f"(on/{zoompan_fps:.4f})"
    for ch in recipe.channels:
        if ch.property == "scale":
            out["scale_expr"] = _channel_expr(ch, inst_duration, on_var)
        elif ch.property == "opacity":
            out["opacity_expr"] = _channel_expr(ch, inst_duration, "T")
        elif ch.property == "x":
            out["overlay_x_expr"] = _channel_expr(ch, inst_duration, "t")
        elif ch.property == "y":
            out["overlay_y_expr"] = _channel_expr(ch, inst_duration, "t")
        elif ch.property in ("scaleX", "scaleY"):
            out["scale_expr"] = _channel_expr(ch, inst_duration, on_var)  # 非均匀折叠为均匀（诚实近似）
    return out


# ---------------------------------------------------------------------------
# 渲染器接线：Layer（带 motion_recipe_id）→ segment 渲染的 zoompan/overlay 参数
# ---------------------------------------------------------------------------

def compile_layer_motion(layer, width: int, height: int, fps: Optional[float] = None) -> Optional[dict]:
    """IR Layer → motion 渲染参数（renderer.render_layer_segment 消费）。

    Layer 无 motion_recipe_id → None（走原逻辑）。
    有 → 返回编译产物；renderer 按表达式构造 zoompan（scale）/overlay（x/y）/
    fade（opacity）。
    """
    if not layer.motion_recipe_id:
        return None
    inst = layer.motion_instance or {}
    return compile_motion_to_ffmpeg(
        motion_recipe_id=layer.motion_recipe_id,
        motion_version=layer.motion_version,
        duration=inst.get("duration"),
        width=width, height=height,
        parameters=inst.get("parameters"),
        fps=fps,
    )
