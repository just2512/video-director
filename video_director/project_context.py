"""MPT 吸收方案 §6 ProjectContextService：Director 的"当前状态"快照。

方案 §6 的 JSON 示例字段（project/selection/timeline/assets/
activeFeatures/providers）逐一对齐。Director 每次收到用户要求，第一步
不是回答，而是读这份上下文 + Capability Manifest（§4）。

provider 可用性来自 video_director.model_config（roles 配置）与
video_director.capabilities（环境级检测）——Director 据此知道
"现在能调用哪些 TTS/Stock/生成 Provider"，而不是背 API。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from video_director.editor import load_composition


def _ratio_of(w: int, h: int) -> str:
    """宽高比映射（对 mp 方案 §43 的 16:9/9:16/1:1 三档）。"""
    if w <= 0 or h <= 0:
        return "unknown"
    r = w / h
    if abs(r - 16 / 9) < 0.1:
        return "16:9"
    if abs(r - 9 / 16) < 0.1:
        return "9:16"
    if abs(r - 1.0) < 0.1:
        return "1:1"
    return f"{w}x{h}"


def _provider_availability() -> Dict[str, List[str]]:
    """按 §6 providers 结构汇总各 Provider 可用性（诚实，缺 key 不进列表）。"""
    import os
    from video_director import model_config as mc
    providers: Dict[str, List[str]] = {"tts": [], "stock": [], "video_generation": [], "image": []}
    # TTS：edge-tts 免费无 key 默认可用（系统现有 /api/voice_preview 即基于此）
    try:
        import edge_tts  # noqa: F401
        providers["tts"].append("edge-tts")
    except Exception:
        pass
    # Stock：pexels/pixabay/coverr 需 API key（§25 商业安全：缺 key 不枚举）
    for name, env in (("pexels", "PEXELS_API_KEY"), ("pixabay", "PIXABAY_API_KEY"),
                      ("coverr", "COVERR_API_KEY")):
        if os.environ.get(env):
            providers["stock"].append(name)
    # 生成：video/image 角色是否配置（§28 Generation Provider 统一）
    for key, slot in (("video", "video_generation"), ("image", "image")):
        if mc.is_role_enabled(key):
            cfg = mc.get_role_config(key)
            providers[slot].append(cfg.get("model") or key)
    return providers


def inspect_project_context(project_id: str, selection: Optional[Dict[str, Any]] = None) -> dict:
    """生成 §6 结构的项目上下文快照。读失败时返回 {}（诚实：不编造状态）。"""
    try:
        ir = load_composition(project_id)
    except Exception:
        return {}

    video = sum(1 for sc in ir.scenes for l in sc.layers if l.type.value == "video")
    image = sum(1 for sc in ir.scenes for l in sc.layers if l.type.value == "image")
    audio_n = len(ir.audio)

    # timeline 轨（§6：Narration/OriginalAudio/MainVisual/Broll/Subtitle/BGM）
    track_types: List[str] = []
    for t in ir.audio:
        name = {"narration": "Narration", "original": "OriginalAudio", "bgm": "BGM",
                "sfx": "SFX", "ambient": "Ambient", "ambience": "Ambience",
                "dialogue": "Dialogue", "original": "OriginalAudio"}.get(t.type.value, t.type.value)
        if name not in track_types:
            track_types.append(name)
    if any(l.type.value in ("video", "image") for sc in ir.scenes for l in sc.layers):
        track_types.extend(["MainVisual"] if not track_types else [])
    track_types.append("Subtitle")  # 字幕轨默认存在（§31 Track 化）

    # MPT 融合上下文：检索意图、source-window 台账、构建指纹都属于项目事实，
    # 供 Director 与 Inspector 共用；文件缺失时返回空结构，不猜状态。
    mpt = {
        "material_intents": [],
        "material_usage": [],
        "build_fingerprint": "",
    }
    try:
        from pathlib import Path
        from video_director import config as vd_config
        base = vd_config.project_dir(project_id)
        intent_path = base / "plans" / "material_intents.json"
        usage_path = base / "plans" / "material_usage.json"
        fingerprint_path = base / "plans" / "mpt_build_fingerprint.json"
        if intent_path.is_file():
            import json
            data = json.loads(intent_path.read_text(encoding="utf-8"))
            mpt["material_intents"] = data.get("scenes") or []
        if usage_path.is_file():
            import json
            data = json.loads(usage_path.read_text(encoding="utf-8"))
            mpt["material_usage"] = data if isinstance(data, list) else []
        if fingerprint_path.is_file():
            import json
            data = json.loads(fingerprint_path.read_text(encoding="utf-8"))
            mpt["build_fingerprint"] = str(data.get("fingerprint") or "")
    except Exception:
        pass

    # activeFeatures（§6：subtitle/original_audio/bgm 是否启用）
    active: List[str] = []
    if any(t.type.value == "original" for t in ir.audio):
        active.append("original_audio")
    if any(t.type.value == "bgm" for t in ir.audio):
        active.append("bgm")
    if any(sc.selections for sc in ir.scenes):
        active.append("subtitle")  # 有语义锚点即具备字幕轨基础

    return {
        "project": {
            "id": project_id,
            "duration": round(ir.project.duration, 2),
            "ratio": _ratio_of(ir.project.width, ir.project.height),
            "width": ir.project.width,
            "height": ir.project.height,
        },
        "selection": selection or {"type": None, "ids": []},
        "timeline": {
            "tracks": track_types,
            "scene_count": len(ir.scenes),
        },
        "assets": {"video": video, "image": image, "audio": audio_n},
        "activeFeatures": active,
        "providers": _provider_availability(),
        "mpt": mpt,
    }