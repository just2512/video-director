"""Frontend Unification 渲染消费：Camera / Mask / PiP / Morph → ffmpeg filtergraph。

上一轮（Command→Composition→Revision）命令链已闭环，但 renderer 从不读
``camera_tracks`` / ``Layer.mask`` / ``parameters.pip`` / ``continuity_links``，
点渲染无视觉变化——这就是「命令能落盘但看不到效果」的尾巴。

本模块把四类视觉语义映射为 ffmpeg filter 片段（纯函数，可单测），
render_composition 真实接线后，命令 → IR → 渲染视觉变化 全程闭环：

    camera  scene 级 → zoompan 动态推拉/横移/俯仰（position [x,y,z] 投影）
    mask    层级      → crop 几何裁剪（矩形/椭圆外接框，feather 用 boxblur 近似）
    pip     层级      → scale 缩放到 transform 位置 + pad 到画幅（小窗呈现）
    morph   场景级    → xfade dissolve（自有 block 形变→确定性交叉溶解过渡）

诚实边界：全部是 2D 确定性投影（perspective 3D / 任意 DOM 形变不做，
与 §14/§16「不 fake 3D / 只做结构化形变」一致）。feather/圆角用
boxblur 近似边缘软化（真实有视觉变化，非像素级完美）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from video_director.visual_semantics import CameraTrack, Mask


def camera_zoompan_expr(camera: CameraTrack, duration: float,
                        width: int, height: int, fps: int) -> Optional[str]:
    """scene 级 camera → 动态 zoompan 表达式（z/x/y 线性插值）。

    position [x, y, z]：x/y 是画面中心归一 [0,1]；z 是深度（1.0 中性，
    <1 拉近=放大，>1 拉远=缩小）。zoom = 1/z；pan 由 x/y 偏离中心产生。

    关键帧数 <2（无运动）→ None（不注入滤镜）。单帧→常值。
    """
    if camera is None or len(camera.keyframes) < 2:
        return None
    k0, k1 = camera.keyframes[0], camera.keyframes[-1]
    frames = max(1, int(round(duration * fps)))
    z0, z1 = 1.0 / max(k0.position[2], 1e-3), 1.0 / max(k1.position[2], 1e-3)
    x0, x1 = k0.position[0], k1.position[0]
    y0, y1 = k0.position[1], k1.position[1]
    if abs(z1 - z0) < 1e-4 and abs(x1 - x0) < 1e-4 and abs(y1 - y0) < 1e-4:
        return None  # 无有效运动

    def lerp(a, b):
        return f"{a:.4f}+({(b - a):.4f})*on/{max(frames - 1, 1)}"

    # zoompan 的 x/y = 画布左上角；中心偏移 = (0.5 - pos) * 放大后尺寸
    zoom = lerp(z0, z1)
    cx = lerp(0.5 - x0, 0.5 - x1)  # 中心偏移系数（负=左移画面）
    cy = lerp(0.5 - y0, 0.5 - y1)
    x_expr = f"({cx})*(iw/zoom)"
    y_expr = f"({cy})*(ih/zoom)"
    return (
        f"scale={width * 2}:{height * 2},"
        f"zoompan=z='({zoom})':x='{x_expr}':y='{y_expr}'"
        f":d=1:s={width}x{height}:fps={fps}"
    )


def apply_camera_filter(camera: CameraTrack, width: int, height: int,
                        fps: int, dur: float) -> Optional[str]:
    """scene 级 camera 的独立滤镜表达式（对已合成 scene clip 二次编码）。"""
    if camera is None:
        return None
    # 只有单关键帧或零关键帧 → 无运动
    return camera_zoompan_expr(camera, dur, width, height, fps)


def mask_crop_filter(mask: Mask, width: int, height: int) -> Optional[str]:
    """层级 mask → 几何裁剪（矩形用 crop；椭圆用外接框 crop 近似）。

    归一 x/y（中心）+ width/height → 像素区域。feather → boxblur 边缘软化。
    inverted 语义：蒙版外可见——第一版诚实标注为「裁剪反转未实现」不 fake，
    实现为「保留区域内」清晰裁剪（inverted=True 时用 negate+crop 反转不好，
    故 inverted 仅记日志不伪造结果）。
    """
    if mask is None:
        return None
    mw = max(0.0, min(1.0, mask.width))
    mh = max(0.0, min(1.0, mask.height))
    if mw <= 0.01 or mh <= 0.01:
        return None
    cw = int(width * mw)
    ch = int(height * mh)
    cx = int(width * mask.x) - cw // 2
    cy = int(height * mask.y) - ch // 2
    # clamp 到画幅内
    cx = max(0, min(width - 1, cx))
    cy = max(0, min(height - 1, cy))
    cw = min(cw, width - cx)
    ch = min(ch, height - cy)
    if cw <= 8 or ch <= 8:
        return None
    parts = [f"crop={cw}:{ch}:{cx}:{cy}", f"scale={width}:{height}"]
    if mask.feather and mask.feather > 0:
        # 羽化近似：边缘 boxblur（像素值换算，四分位取整）
        blur = max(1, int(mask.feather / 4))
        parts.append(f"boxblur={blur}:1")
    return ",".join(parts)


def pip_transform_params(pip: Dict, width: int, height: int) -> Optional[Dict]:
    """层级 pip → 缩放 + 定位参数（transform.x/y 归一 → 像素左上角 + 缩放）。

    返回 {w, h, x, y, radius} 供 overlay/scale-pad 使用；无 pip 子对象 → None。
    """
    if not pip:
        return None
    scale = float(pip.get("transform", {}).get("scale", 0.35) if isinstance(pip.get("transform"), dict) else 0.35)
    tx = float((pip.get("transform", {}) or {}).get("x", 0.78))
    ty = float((pip.get("transform", {}) or {}).get("y", 0.22))
    scale = max(0.1, min(1.0, scale))
    w = max(8, int(width * scale))
    h = max(8, int(height * scale))
    x = int(width * tx) - w // 2
    y = int(height * ty) - h // 2
    x = max(0, min(width - w, x))
    y = max(0, min(height - h, y))
    return {
        "w": w, "h": h, "x": x, "y": y,
        "radius": float(pip.get("radius", 0) if isinstance(pip.get("radius"), (int, float)) else 0),
    }


def pip_filter_expr(pip: Dict, width: int, height: int) -> Optional[str]:
    """pip → 缩小到角落小窗的 filter（scale 到 pip 尺寸 → pad 回画幅黑底）。

    黑底小窗（非透明叠加主画面）是诚实的第一版：pip 作为独立层呈现为
    屏幕角落的小窗画面。真正 alpha overlay 叠加主层需 filter_complex 双输入，
    属于 render_composition 的 overlay 合成（下一步），此处先让 pip 层
    「缩为小窗」可见。
    """
    p = pip_transform_params(pip, width, height)
    if p is None:
        return None
    return (
        f"scale={p['w']}:{p['h']}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:{p['x']}:{p['y']}:color=black"
    )


def morph_transition_type(morph_path: Optional[str]) -> Optional[str]:
    """morph continuity → 场景间过渡类型（deterministic cross-dissolve）。

    morph_path 存 JSON（from_block/to_block/duration/easing），渲染成
    'morph' 过渡 → splice 层映射为 xfade dissolve（形变的确定性近似）。
    无 morph → None。
    """
    if not morph_path:
        return None
    return "morph"