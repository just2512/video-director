"""最终方案 §23 VisualSearchIntent：Scene → 结构化视觉搜索意图。

方案原文：不能「一个脚本 → 5 个关键词」，而应形成
    Scene → VisualSearchIntent
{scene_id, script, subject, visual_concepts, actions, environment,
 shot, required_duration, ratio, asset_types}
搜索不再是「赚钱」，而是「忙碌上班族 / 深夜办公 / 电脑前工作 / 财务压力」。

生成链（§22 MPT 吸收 + 诚实降级）：
    LLM 可用 → 结构化 intent（subject/concepts/actions/environment/shot）
    LLM 不可用 → 规则生成（场景文本分词 + 场景类型知识 + 常用动作/环境词表），
                  诚实标注 source="rule"（不假装 LLM 理解）

§24 零评分：intent 只产检索词组（多组），不做 0.91 式数值匹配。
"""

from __future__ import annotations

from typing import List, Optional

from loguru import logger


class VisualSearchIntentError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


#: 场景类型 → 视觉概念候选（规则兜底词表；LLM 生成时不用）
_SCENE_TYPE_CONCEPTS = {
    "problem": ["忙碌工作", "压力场景", "困境画面"],
    "statistic": ["数据可视化", "图表背景", "数字强调"],
    "solution": ["轻松场景", "解决状态", "明亮环境"],
    "evidence": ["实证画面", "细节特写", "真实场景"],
    "cta": ["行动场景", "积极画面", "引导视觉"],
    "default": ["相关场景画面", "叙事补充画面"],
}

#: 通用动作/环境词（英文——规则兜底也必须产可检索词，中文不进检索词组）
_ACTION_WORDS = ["typing", "thinking", "walking", "talking"]
_ENV_WORDS = ["office", "indoors", "city", "home"]


#: 中文叙事核心词 → 英文检索词（规则兜底词表；LLM 可用时优先 LLM 生成）
_CN_EN_CORE = {
    "忙": "busy person", "加班": "working late", "上班": "office worker",
    "工作": "working", "深夜": "late night", "办公": "office desk",
    "赚": "money", "钱": "finance", "财务": "financial stress",
    "压力": "stress", "焦虑": "anxiety", "失眠": "insomnia",
    "城市": "city street", "家": "home", "咖啡": "coffee shop",
    "地铁": "subway commute", "开会": "business meeting", "手机": "phone screen",
    "电脑": "laptop work", "读书": "reading book", "学习": "studying",
    "运动": "exercise", "跑步": "running", "旅行": "travel",
    "孩子": "children", "家庭": "family", "医院": "hospital",
    "医生": "doctor", "产品": "product", "市场": "market",
    "创业": "startup", "团队": "team work", "成长": "growth",
    "时间": "clock time", "目标": "goal", "成功": "success",
    "失败": "failure", "选择": "choice", "未来": "future",
}


def _cn_to_en_candidates(script: str) -> List[str]:
    """中文文本 → 英文检索词候选（命中核心词按出现顺序，最多 3 个）。"""
    out = []
    for cn, en in _CN_EN_CORE.items():
        if cn in script and en not in out:
            out.append(en)
    return out[:3]


def _rule_intent(scene_id: str, script: str, required_duration: float,
                 ratio: str, scene_type: str = "") -> dict:
    """规则兜底：中文核心词→英文检索词 + 场景类型概念组合。

    候选不足 2 个时补场景类型默认概念（保证有可检索词组，不空转）。
    """
    en_hits = _cn_to_en_candidates(script)
    concepts = en_hits if len(en_hits) >= 2 else (
        en_hits + _SCENE_TYPE_CONCEPTS.get(
            scene_type or "default", _SCENE_TYPE_CONCEPTS["default"])[: 2 - len(en_hits)])
    if not concepts:
        concepts = _SCENE_TYPE_CONCEPTS["default"][:]
    subject = en_hits[0] if en_hits else (script[:8] or scene_id)
    return {
        "scene_id": scene_id,
        "script": script,
        "subject": subject,
        "visual_concepts": concepts,
        "actions": _ACTION_WORDS[:2],
        "environment": _ENV_WORDS[:2],
        "shot": "medium",
        "required_duration": required_duration,
        "ratio": ratio,
        "asset_types": ["video", "image"],
        "source": "rule",
    }


def _llm_intent(scene_id: str, script: str, required_duration: float,
                ratio: str, scene_type: str = "") -> Optional[dict]:
    """LLM 生成结构化 intent（§23 JSON 契约）。失败返回 None（走规则）。"""
    from video_director.llm_provider import get_llm
    provider = get_llm()
    if provider is None:
        return None
    system = (
        "你是视频素材搜索意图生成器。把一句视频文案转成结构化搜索意图 JSON：\n"
        '{"subject": "<英文主体短语>", "visual_concepts": ["<英文视觉概念>", ...], '
        '"actions": ["<英文动作>", ...], "environment": ["<英文环境>", ...], '
        '"shot": "wide|medium|closeup"}\n'
        "要求：visual_concepts 2-4 个、英文短语（1-3 词，供 Pexels/Pixabay 检索）；"
        "贴合文案语义，不要泛化成 single word。只输出 JSON。"
    )
    try:
        import json as _json
        import re as _re
        raw = provider.generate(f"文案：{script}", system=system)
        text = (raw or "").strip()
        if text.startswith("```"):
            text = _re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = _re.sub(r"\s*```$", "", text)
        s, e = text.find("{"), text.rfind("}")
        if s < 0 or e <= s:
            return None
        d = _json.loads(text[s:e + 1])
        if not isinstance(d, dict) or not d.get("visual_concepts"):
            return None
        return {
            "scene_id": scene_id,
            "script": script,
            "subject": d.get("subject", ""),
            "visual_concepts": [str(c) for c in d["visual_concepts"][:4]],
            "actions": [str(a) for a in (d.get("actions") or [])[:3]],
            "environment": [str(x) for x in (d.get("environment") or [])[:3]],
            "shot": d.get("shot", "medium"),
            "required_duration": required_duration,
            "ratio": ratio,
            "asset_types": ["video", "image"],
            "source": "llm",
        }
    except Exception as exc:  # noqa: BLE001 LLM 故障走规则，不编造
        logger.info(f"VisualSearchIntent LLM 失败（规则兜底）: {exc}")
        return None


def build_intent(scene_id: str, script: str, required_duration: float = 4.0,
                 ratio: str = "9:16", scene_type: str = "",
                 use_llm: bool = True) -> dict:
    """§23 主入口：Scene → VisualSearchIntent。

    LLM 优先（结构化英文检索词），规则兜底（诚实 source 标注）。
    """
    if not script or not str(script).strip():
        # 空文案：给出最小可检索意图（不抛——缺画面回退链仍需要 intent 形态）
        return _rule_intent(scene_id, "", required_duration, ratio, scene_type)
    if use_llm:
        llm = _llm_intent(scene_id, script, required_duration, ratio, scene_type)
        if llm:
            return llm
    return _rule_intent(scene_id, script, required_duration, ratio, scene_type)


def search_queries(intent: dict, limit: int = 4) -> List[str]:
    """§23 intent → 检索词组列表（按优先级：concept > subject+action > subject+env）。

    「忙碌上班族 / 深夜办公 / 电脑前工作」而非「赚钱」——多组词让
    §四十回退链（Local→Pexels→Pixabay→Coverr→AI）逐组尝试。
    """
    queries: List[str] = []
    for c in intent.get("visual_concepts", [])[:limit]:
        queries.append(c)
    subj = intent.get("subject", "")
    for a in intent.get("actions", [])[:2]:
        if subj:
            queries.append(f"{subj} {a}")
    for e in intent.get("environment", [])[:2]:
        if subj:
            queries.append(f"{subj} {e}")
    return queries[:limit]
