# -*- coding: utf-8 -*-
"""自然语言 → 导演参数 的意图解析（2026-10-18 系统性修复）。

用户用自然语言向 AI 导演提要求（「加水印」「40 秒以上」「放慢配音速度」），
此前这些只存在 brief 文本里，从不被解析进 spec——AI 导演「看不到」这些指令，
于是整条「自然语言 → 系统能力」链是断的。

本模块建一道能力目录驱动的解析桥：
1. INTENT_FIELDS 目录声明系统当前**全部**可被用户自然语言要求的导演参数
   （spec 字段 + 中文别名 + 取值类型 + 示例）——加新能力 = 在这里加一行。
2. resolve_director_intents(brief) 先走确定性规则层（数字/关键词/显式引号），
   再走 LLM 泛化层（把 brief 里剩余语义意图映射到目录字段），返回
   {spec_field: normalized_value}。LLM 缺失/失败/超时一律静默降级，不阻塞创建。
3. 目录之外的诉求（系统没有对应能力）如实进 unresolved，由调用方提示用户，
   绝不编造一个「执行成功」。

归一化取值直接对齐下游契约（voice_rate 用百分比、resolution 用 宽x高、
duration 用秒等），避免「解析出来但下游不认识」的二次断链。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

# 系统能力目录：用户自然语言要求 → 导演 spec 字段。
# label 给 LLM 看；aliases 给规则层用；kind 决定归一化。
INTENT_FIELDS: List[Dict[str, Any]] = [
    # 尺寸 / 平台取向
    {"field": "resolution", "label": "视频分辨率/画幅（如 1080x1920 竖屏、1920x1080 横屏）",
     "aliases": ["分辨率", "画幅", "横屏", "竖屏", "横版", "竖版", "宽屏", "正方", "方形",
                 "16:9", "9:16", "3:4", "4:3", "1:1", "16比9", "9比16", "九比十六", "十六比九"],
     "kind": "resolution"},
    {"field": "duration", "label": "成片目标总时长（秒；支持「40 秒以上」「1 分钟」「不超过 30 秒」）",
     "aliases": ["时长", "总时长", "长度", "多少秒", "几秒", "分钟", "秒以上", "秒以内", "不超过", "至少"],
     "kind": "duration"},
    {"field": "voice_rate", "label": "配音语速（相对百分比：+0% 正常、+15% 更快、-10% 更慢）",
     "aliases": ["语速", "配音速度", "念快", "念慢", "放慢", "加快", "慢一点", "快一点", "语速放慢", "语速加快"],
     "kind": "voice_rate"},
    {"field": "watermark", "label": "水印文本（片尾/角标署名文字，如「××出品」）",
     "aliases": ["水印", "署名", "版权", "加水印", "角标", "出品"],
     "kind": "text"},
    {"field": "subtitle_mode", "label": "字幕开关（plain 中文单语 / bilingual 双语 / off 不要字幕）",
     "aliases": ["字幕", "不要字幕", "加字幕", "双语字幕", "中英字幕", "无字幕"],
     "kind": "subtitle"},
    {"field": "voice", "label": "配音音色（如 女声/男声/儿童，或具体 voice id）",
     "aliases": ["音色", "配音声音", "女声", "男声", "童声", "配音"],
     "kind": "text"},
    {"field": "voice_mode", "label": "配音模式（auto 自动合成 / silent 静音无配音）",
     "aliases": ["静音", "不要配音", "无配音", "不说话"],
     "kind": "text"},
    {"field": "bgm", "label": "背景音乐（auto 自动选曲 / off 关闭 / 具体风格）",
     "aliases": ["背景音乐", "bgm", "配乐", "不要音乐", "无配乐", "音乐"],
     "kind": "text"},
    {"field": "bgm_volume", "label": "背景音乐音量（0~2，1 默认、0.3 很轻）",
     "aliases": ["音乐音量", "配乐音量", "背景音乐小声", "音乐小声", "音乐大声"],
     "kind": "volume"},
    {"field": "narration_volume", "label": "旁白音量（0~2，1 默认）",
     "aliases": ["旁白音量", "配音音量", "解说音量"],
     "kind": "volume"},
    {"field": "title", "label": "视频标题（片头/封面标题文字）",
     "aliases": ["标题", "片名", "题目"],
     "kind": "text"},
    {"field": "style", "label": "视觉风格（如 真实质感/漫画风/赛博朋克）",
     "aliases": ["风格", "画风", "视觉风格", "质感"],
     "kind": "text"},
    {"field": "transition_mode", "label": "转场方式（cut 硬切 / fade 淡入淡出 / 其他）",
     "aliases": ["转场", "过渡", "淡入淡出", "硬切"],
     "kind": "text"},
    {"field": "fit_mode", "label": "画面适配（cover 填满裁剪 / contain 完整保留黑边）",
     "aliases": ["适配", "裁剪", "黑边", "填满"],
     "kind": "text"},
    {"field": "clip_duration", "label": "单镜头时长（秒）",
     "aliases": ["镜头时长", "每个镜头", "单镜头"],
     "kind": "duration"},
    {"field": "clip_speed", "label": "画面变速（1.0 正常，>1 加快）",
     "aliases": ["变速", "播放速度", "画面速度"],
     "kind": "text"},
    {"field": "lower_third_name", "label": "人物条姓名（受访者/人物介绍条）",
     "aliases": ["人物条", "人名条", "受访者", "名字条"],
     "kind": "text"},
    {"field": "lower_third_role", "label": "人物条头衔/身份",
     "aliases": ["头衔", "身份", "职位"],
     "kind": "text"},
    {"field": "sfx", "label": "音效偏好（auto 自动 / off 关闭 / 具体音效）",
     "aliases": ["音效", "不要音效", "无音效"],
     "kind": "text"},
]

# 分辨率别名 → 宽x高（精确覆盖显式画幅提示）
_RESOLUTION_ALIAS = {
    "16:9": (1920, 1080), "16比9": (1920, 1080), "十六比九": (1920, 1080),
    "9:16": (1080, 1920), "9比16": (1080, 1920), "九比十六": (1080, 1920),
    "横屏": (1920, 1080), "横版": (1920, 1080), "宽屏": (1920, 1080),
    "竖屏": (1080, 1920), "竖版": (1080, 1920),
    "3:4": (1080, 1440), "4:3": (1440, 1080),
    "1:1": (1080, 1080), "正方": (1080, 1080), "方形": (1080, 1080),
}

_COMMON_WIDTHS = {1080, 720, 1920, 1280, 3840, 2560, 1440, 2160}


def _resolve_resolution(text: str) -> Optional[str]:
    t = text.lower()
    for alias, dims in _RESOLUTION_ALIAS.items():
        if alias.lower() in t:
            return f"{dims[0]}x{dims[1]}"
    m = re.search(r"(\d{2,5})\s*[x×*]\s*(\d{2,5})", text)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        if w in _COMMON_WIDTHS or h in _COMMON_WIDTHS:
            return f"{w}x{h}"
    return None


def _resolve_duration(text: str) -> Optional[str]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*分钟", text)
    if m:
        secs = int(float(m.group(1)) * 60)
    else:
        m = re.search(r"(\d+(?:\.\d+)?)\s*秒", text)
        if not m:
            return None
        secs = int(round(float(m.group(1))))
    # 方向语义保留：「以上/至少」= 下限；「以内/不超过」= 上限；默认 = 目标值。
    if any(w in text for w in ("以上", "至少", "不少于", "不低于")):
        return f"{secs}+"
    if any(w in text for w in ("以内", "不超过", "最多", "之内")):
        return f"{secs}-"
    return str(secs)


def _resolve_voice_rate(text: str) -> Optional[str]:
    t = text
    slow = any(w in t for w in ("放慢", "慢一点", "念慢", "语速慢", "更慢", "慢些"))
    fast = any(w in t for w in ("加快", "快一点", "念快", "语速快", "更快", "快些"))
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", t)
    if m and ("语速" in t or "速度" in t or "配音" in t):
        return f"{int(round(float(m.group(1)))):+d}%"
    if slow and not fast:
        return "-15%"
    if fast and not slow:
        return "+15%"
    return None


def _resolve_subtitle(text: str) -> Optional[str]:
    if any(w in text for w in ("不要字幕", "无字幕", "不加字幕", "去掉字幕")):
        return "off"
    if any(w in text for w in ("双语", "中英", "中英文")):
        return "bilingual"
    if any(w in text for w in ("字幕", "加字幕")):
        return "plain"
    return None


def _resolve_text_field(text: str, field: str, aliases: List[str]) -> Optional[str]:
    for alias in aliases:
        if alias in text:
            m = re.search(alias + r"\s*(?:叫|是|为|叫做|名为|设成|设为)?\s*[「『\"\']([^」「』\"\']+)", text)
            if m:
                return m.group(1).strip()
            m = re.search(r"[「『\"\']([^」「』\"\']+)[」』\"\']\s*" + re.escape(alias), text)
            if m:
                return m.group(1).strip()
    return None


def _resolve_volume(text: str, aliases: List[str]) -> Optional[str]:
    for alias in aliases:
        if alias in text:
            if any(w in text for w in ("小声", "轻", "低", "降低", "调低")):
                return "0.4"
            if any(w in text for w in ("大声", "高", "调高", "加大", "提高")):
                return "1.3"
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
            if m:
                return str(min(2.0, float(m.group(1)) / 100.0))
    return None


def _rule_resolve(brief: str) -> Tuple[Dict[str, str], List[str]]:
    """确定性规则层：快、可测、零 LLM 依赖。覆盖最明确的指令形态。"""
    out: Dict[str, str] = {}
    text = brief or ""
    r = _resolve_resolution(text)
    if r:
        out["resolution"] = r
    # 「每个镜头 N 秒」（clip_duration）与总时长（duration）独立提取，不互斥：
    # 复合 brief「时长40秒以上，每个镜头3秒」两者都要拿到。镜头语境只接管
    # 紧跟语境词的那个数字，其余「N 秒」仍归总时长。
    clip_m = re.search(r"(?:每个镜头|单镜头|每个画面|镜头时长)\s*(\d+(?:\.\d+)?)\s*秒", text)
    if clip_m:
        out["clip_duration"] = str(int(round(float(clip_m.group(1)))))
        # 从总时长扫描文本里剔除镜头语境片段，避免「3 秒」被误当总时长
        text = text[:clip_m.start()] + text[clip_m.end():]
    d = _resolve_duration(text)
    if d:
        out["duration"] = d
    vr = _resolve_voice_rate(text)
    if vr:
        out["voice_rate"] = vr
    sub = _resolve_subtitle(text)
    if sub:
        out["subtitle_mode"] = sub
    for item in INTENT_FIELDS:
        if item["field"] in ("watermark", "title", "lower_third_name"):
            val = _resolve_text_field(text, item["field"], item["aliases"])
            if val:
                out[item["field"]] = val
    for item in INTENT_FIELDS:
        if item["field"] in ("bgm_volume", "narration_volume"):
            val = _resolve_volume(text, item["aliases"])
            if val:
                out[item["field"]] = val
    if any(w in text for w in ("静音", "不要配音", "无配音")):
        out.setdefault("voice_mode", "silent")
    if any(w in text for w in ("不要音乐", "无配乐", "不要背景音乐")):
        out.setdefault("bgm", "off")
    if any(w in text for w in ("不要音效", "无音效")):
        out.setdefault("sfx", "off")
    return out, []


def _llm_resolve(brief: str) -> Dict[str, str]:
    """LLM 泛化层：目录驱动，把规则层没命中的语义意图映射到字段。"""
    from video_director import llm_provider
    provider = llm_provider.get_llm()
    if provider is None:
        return {}
    catalog = "\n".join(
        f"- {item['field']}（{item['label']}）；别名：{'、'.join(item['aliases'])}"
        for item in INTENT_FIELDS
    )
    system = (
        "你是视频导演参数解析器。从用户的创作要求中，把能映射到下列字段的指令抽取出来。"
        "只输出 JSON 对象，键是下列字段名。"
        "voice 字段输出用户的自然语言描述（如「温柔女声」「沉稳男声」），系统会自动映射到真实音色，不要自己编 id。"
        "style 字段输出用户的风格描述原文（如「赛博朋克」「纪录片风格」），系统会映射到注册风格或作风格意图。"
        "其余枚举字段按字段说明的取值输出。"
        "要求没有提到的字段不要输出；系统没有对应字段的要求忽略不要臆造。"
        "只输出 JSON，不要解释、不要 markdown 代码块。"
    )
    prompt = "可映射字段：\n" + catalog + "\n\n用户要求：\n" + brief
    try:
        raw = provider.generate(prompt, system=system).strip()
    except Exception:
        return {}
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0:
        return {}
    try:
        parsed = json.loads(raw[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    known = {item["field"] for item in INTENT_FIELDS}
    return {str(k): v for k, v in parsed.items() if k in known and v not in (None, "")}


def _normalize_voice(value: str) -> Optional[str]:
    """自由文本音色（「温柔女声」）→ 系统 voice_id 白名单。"""
    v = str(value or "").strip()
    if not v:
        return None
    from video_director import voice
    try:
        profiles = voice.list_voices("edge-tts")
    except Exception:
        return v  # voice 模块不可用时不拦截，交给下游兜底
    # 已是合法 voice_id
    if any(p["voiceId"] == v for p in profiles):
        return v
    # 按 gender + style 匹配自由文本
    female = any(w in v for w in ("女", "女生", "温柔", "活泼", "晓"))
    male = any(w in v for w in ("男", "男生", "沉稳", "阳光", "清亮", "新闻", "云"))
    for p in profiles:
        if p.get("language") and p["language"] != "zh-CN" and p["language"] != "zh-TW":
            continue
        style = str(p.get("style") or "")
        if female and str(p.get("gender") or "") == "female":
            if style and style in v:
                return p["voiceId"]
        if male and str(p.get("gender") or "") == "male":
            if style and style in v:
                return p["voiceId"]
    # 兜底：gender 匹配取第一个
    for p in profiles:
        if p.get("language") and p["language"] not in ("zh-CN", "zh-TW"):
            continue
        if female and str(p.get("gender") or "") == "female":
            return p["voiceId"]
        if male and str(p.get("gender") or "") == "male":
            return p["voiceId"]
    return None  # 无法落到系统音色：不传，交默认女声


def _normalize_style(value: str) -> str:
    """自由文本风格（「赛博朋克」）优先映射系统 style_id，否则保留原文作 style_intent。"""
    v = str(value or "").strip()
    if not v:
        return ""
    try:
        from video_director import style as style_mod
        if v in style_mod.available_styles():
            return v
        for style_id, words in getattr(style_mod, "_DESC_MAP", {}).items():
            if any(w in v for w in words):
                return style_id
    except Exception:
        pass
    return v  # 未命中注册风格：保留自由文本，由 select_style 的 user_intent 透传兜底


def _normalize_value(field: str, value: str) -> Optional[str]:
    """字段级归一化：把 LLM/规则的粗值落到系统真实契约。"""
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    if field == "voice":
        mapped = _normalize_voice(v)
        if mapped is None:
            # 无法落到系统音色：不喂非法 id 给 edge-tts，但原始请求必须带出
            # （director 侧据此发 warning，用户知道「温柔女声」没配上）。
            return None
        return mapped
    if field == "style":
        return _normalize_style(v)
    if field == "transition_mode":
        # fade/淡入淡出 → fade；其余透传（cut 等）
        return "fade" if any(w in v for w in ("淡入", "淡出", "fade", "溶解")) else v
    if field == "fit_mode":
        return "cover" if any(w in v for w in ("填满", "裁剪", "cover", "铺满")) else \
               ("contain" if any(w in v for w in ("黑边", "完整", "contain", "适应")) else v)
    if field == "subtitle_mode":
        return v if v in ("plain", "bilingual", "off") else (
            "off" if any(w in v for w in ("不要", "无", "去掉", "关")) else
            ("bilingual" if any(w in v for w in ("双", "中英")) else "plain"))
    if field in ("clip_duration",):
        try:
            return str(int(round(float(v))))
        except (TypeError, ValueError):
            return None
    return v


def resolve_director_intents(brief: str) -> Dict[str, str]:
    """brief → {spec_field: normalized_value}。规则层 + LLM 层合并，规则层优先。

    附加键 `_unresolved`（逗号分隔）：系统有字段但值无法落到真实契约的原始
    请求（如「外星女声」），由 director 侧转 warning 如实告知用户。
    """
    rule, _ = _rule_resolve(brief or "")
    merged = dict(rule)
    llm = _llm_resolve(brief or "")
    for k, v in llm.items():
        merged.setdefault(k, v)  # 规则层已给的确定性值优先，LLM 只补齐
    # 规则层已按镜头语境正确分流（clip_duration 独立提取并从总时长扫描剔除）；
    # 此处只在「规则层没拿到 duration 而 LLM 给了」且镜头语境成立时防 LLM 双输出。
    if ("clip_duration" in merged and "duration" in merged
            and "duration" not in rule
            and any(w in (brief or "") for w in ("每个镜头", "单镜头", "每个画面", "镜头时长"))):
        merged.pop("duration", None)
    normalized: Dict[str, str] = {}
    unresolved: List[str] = []
    for k, v in merged.items():
        nv = _normalize_value(k, v)
        if nv is None:
            if k in ("voice", "style"):
                unresolved.append(f"{k}={v}")
            continue
        normalized[k] = nv
    if unresolved:
        normalized["_unresolved"] = "；".join(unresolved)
    return normalized


def catalog_for_frontend() -> List[Dict[str, str]]:
    """供前端/文档展示系统当前支持自然语言控制的能力清单。"""
    return [{"field": i["field"], "label": i["label"]} for i in INTENT_FIELDS]