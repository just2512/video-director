"""Asset Matcher：场景 ↔ 素材内容匹配（用户 2026-09-18 点名的核心剪辑价值）。

双路匹配（§十/十一）：
- LLM 语义匹配（主路）：场景 text/role × 素材 description/narrative_concepts，
  LLM 判断"哪个素材最适合表达这句话"，不是词面匹配
- 确定性标签匹配（fallback）：字符级 tag 重合度 + 未用优先，无 LLM 也可跑

匹配输出：每个场景的素材候选优先序，供 ContentMatchPlanner 消费。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from video_director.models import AssetRef
from video_director.source_usage_policy import enforce_assignments


class MatcherError(Exception):
    pass


MATCH_PROMPT = """你是视频剪辑的素材匹配师。给定若干视频素材的语义描述，为每个文案场景选出最匹配的素材。

素材清单（id: 描述 / 叙事概念）：
{assets_desc}

文案场景（id: 文本）：
{scenes_desc}

规则：
1. 画面要能"表达"这句话的意思（语义匹配，不是词面匹配）
2. 每个素材最多用一次（用过的不再选）
3. 素材少于场景时，选最合适的重复使用，但优先覆盖未用素材
4. 只能输出 JSON，格式：
{{"assignments": [{{"scene_id": "S01", "asset_id": "v1"}}, ...]}}"""


# ---------------------------------------------------------------------------
# 概念桥接（§11：词面匹配的根本盲区——"付之一炬"≠"焚烧"字面重合）
# ---------------------------------------------------------------------------
# 场景词 → 视频标注常用概念词。标注端词汇由 bootstrap manifest 约定，
# 此表只做"文案用语 → 标注概念"的单向扩展，出现命中即给足权重。
CONCEPT_BRIDGES: Dict[str, List[str]] = {
    # 焚烧类
    "炬": ["焚烧", "火焰", "烧毁"], "焚": ["焚烧", "火焰"], "烧": ["焚烧", "火焰"],
    "付之一炬": ["焚烧"], "销毁": ["焚烧"],
    # 丧葬类
    "陪葬": ["送葬", "棺椁", "墓室", "古墓"], "入土": ["古墓", "坟冢", "埋葬"],
    "下葬": ["古墓", "坟冢", "送葬"], "埋": ["坟冢", "封土"], "葬": ["送葬", "坟冢"],
    "墓": ["古墓", "墓室", "坟冢"], "坟": ["坟冢"], "棺": ["棺椁", "抬棺"],
    "送葬": ["送葬队伍", "仪仗"], "抬棺": ["抬棺力士"], "力士": ["抬棺力士"],
    "坟冢": ["坟冢"], "封土": ["封土"],
    # 战争/人物类
    "战神": ["将军", "出征", "战场"], "横扫": ["出征", "军队", "战场"],
    "征战": ["出征", "战场", "军队"], "大明": ["宫廷", "龙袍"], "皇帝": ["宫廷", "龙袍"],
    "朱元璋": ["宫廷", "圣旨"], "圣旨": ["圣旨", "文书"], "赐": ["宫廷", "圣旨"],
    # 空间/悬念类
    "空荡": ["空荡", "墓室"], "空荡荡": ["空荡"], "没位置": ["墓室", "空荡"],
    "战利品": ["战利品", "谜团"], "掌心": ["谜团"], "到底": ["谜团", "悬疑"],
    "谜": ["谜团", "悬疑"], "去向": ["谜团"], "谁": ["谜团"],
    # 时间/氛围类
    "夜": ["夜色", "月夜"], "月": ["月夜", "满月"], "当年": ["历史"],
}


def _expand_scene_text(scene_text: str) -> set:
    """场景文本 → 概念扩展词集（原文 chars + 桥接词）。"""
    expanded = set(scene_text)
    for word, concepts in CONCEPT_BRIDGES.items():
        if word in scene_text:
            expanded.update(concepts)
    return expanded


def _filename_keywords(asset: AssetRef) -> List[str]:
    """零依赖素材语义来源：从文件名 stem 提取中文关键词候选。

    用户素材常带语义文件名（"战场出征.mp4"、"送葬队伍_2.mp4"、"factory-tour.mp4"）。
    提取规则：连续中文片段按 2 字滑动切词 + 完整片段，英文按分隔符切词。
    这是无 API key、无手工标注、无本地模型时的核心匹配信号。
    """
    stem = Path(asset.file).stem
    kw = set()
    # 连续中文片段（≥2 字）
    for seg in re.findall(r"[一-鿿]{2,}", stem):
        kw.add(seg)                    # 完整片段
        for i in range(len(seg) - 1):  # 2 字滑动
            kw.add(seg[i:i + 2])
    # 英文/数字片段（用 - _ 空格 分隔）
    for seg in re.split(r"[-_\s]+", stem):
        s = seg.strip().lower()
        if re.match(r"^[a-z]{3,}$", s):
            kw.add(s)
    return list(kw)


def deterministic_match(
    scene_texts: List[str],
    scene_ids: List[str],
    assets: Dict[str, AssetRef],
    used: set,
) -> Optional[str]:
    """确定性主路（零依赖）：概念桥接 + 标签/描述/文件名语义 全文重合。

    素材语义来源优先级（第一版零依赖）：
        P0 文件名语义（本函数，零成本，无需任何配置）
        P2 手工标注 manifest（用户主动提供）
        P5 Vision API（增强，配 key 才有，不配不阻塞）
    """
    expanded = _expand_scene_text(scene_texts)

    def overlap(asset: AssetRef) -> float:
        score = 0.0
        sources = list(asset.keywords)
        sources += [k for k in _filename_keywords(asset) if k not in sources]
        # 1) tags/文件名关键词 精确词命中（权重 2）
        for tag in sources:
            chars = [c for c in tag if not c.isspace()]
            if not chars:
                continue
            hits = sum(1 for c in chars if c in expanded)
            if hits == len(chars):
                score += 2.0
            elif hits > 0:
                score += hits / len(chars)
        # 2) 概念桥接命中（整词在 tags/description/文件名 中出现，权重 3）
        blob = " ".join(sources) + " " + (asset.description or "")
        for w in expanded - set(scene_texts):
            if len(w) >= 2 and w in blob:
                score += 3.0
        # 3) description 字符重合（弱权重，补充氛围关联）
        if asset.description:
            dhits = sum(1 for c in set(asset.description) if c in expanded)
            score += min(dhits * 0.05, 0.6)
        return score

    scored = []
    for aid, asset in assets.items():
        if asset.type != "video":
            continue
        has_signal = asset.keywords or asset.description or _filename_keywords(asset)
        if not has_signal:
            continue
        bonus = 0.5 if aid not in used else 0.0
        scored.append((overlap(asset) + bonus, aid))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1] if scored[0][0] > 0.5 else None


def llm_match(
    scenes: List[dict],
    assets: Dict[str, AssetRef],
    llm_call,
) -> Dict[str, str]:
    """LLM 语义匹配（主路）。llm_call(prompt) -> str 由调用方注入。

    返回 {scene_id: asset_id}。失败抛 MatcherError（调用方降级 deterministic）。
    """
    assets_desc = "\n".join(
        f"- {aid}: {a.description or '（无描述）'} / 概念: {', '.join(a.keywords) or '无'}"
        for aid, a in assets.items()
        if a.type == "video"
    )
    scenes_desc = "\n".join(f"- {s['id']}: {s['text']}" for s in scenes)
    prompt = MATCH_PROMPT.format(assets_desc=assets_desc, scenes_desc=scenes_desc)
    raw = llm_call(prompt)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0:
        raise MatcherError(f"LLM 匹配输出无 JSON: {raw[:200]}")
    parsed = json.loads(raw[start : end + 1])
    assignments = {}
    for a in parsed.get("assignments", []):
        sid, aid = a.get("scene_id"), a.get("asset_id")
        if sid in scenes and aid in [k for k, v in assets.items() if v.type == "video"]:
            assignments[sid] = aid
    if not assignments:
        raise MatcherError("LLM 匹配结果为空")
    return assignments


def match_assets(
    scenes: List[dict],
    assets: Dict[str, AssetRef],
    llm_call=None,
    *,
    policy_by_scene: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, str]:
    """统一入口：LLM 主路 → 确定性 fallback → 顺序兜底（保证总有结果）。

    返回 {scene_id: asset_id}（每个场景至少有一个候选）。
    """
    scene_ids = [s["id"] for s in scenes]
    assignments: Dict[str, str] = {}
    used: set = set()

    # 主路：LLM
    if llm_call is not None:
        try:
            assignments = llm_match(scenes, assets, llm_call)
            used = set(assignments.values())
            logger.info(f"asset match: LLM 主路完成 {len(assignments)}/{len(scenes)} 场景")
        except Exception as exc:
            # 任意 LLM 失败（429 余额不足/限流/超时）都降级，不炸主流程
            logger.warning(f"LLM 匹配失败，降级确定性: {exc}")
            assignments = {}

    # fallback：确定性重合度
    for s in scenes:
        if s["id"] in assignments:
            continue
        aid = deterministic_match(s["text"], s["id"], assets, used)
        if aid:
            assignments[s["id"]] = aid
            used.add(aid)
    if assignments:
        logger.info(f"asset match: 确定性补齐至 {len(assignments)}/{len(scenes)}")

    # 兜底：顺序分配（保证闭环，但记 warning）
    video_ids = [aid for aid in assets if assets[aid].type == "video"]
    for i, s in enumerate(scenes):
        if s["id"] not in assignments and video_ids:
            assignments[s["id"]] = video_ids[i % len(video_ids)]
            logger.warning(f"asset match: 场景 {s['id']} 无语义匹配，顺序兜底 {assignments[s['id']]}")

    # Step ⑤：语义匹配完成后再执行来源/复用政策。
    # 政策不能改变“是否匹配”的语义模型，只负责处理连续重复、来源切换与复用边界。
    if assignments and assets:
        assignments, decisions = enforce_assignments(
            scenes, assets, assignments, video_ids,
            policy_by_scene=policy_by_scene,
        )
        deferred = [d for d in decisions if d.get("reason", "").startswith("存在其他")]
        if deferred:
            logger.info(
                "asset usage policy: 调整 %d 个场景的来源/复用顺序",
                len(deferred),
            )
    return assignments
