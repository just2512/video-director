"""Creative Candidate Set：把 MPT 的“先产生候选、再选择”语义融入 Director。

这里的候选是“创作方案”，不是另一个素材库：
- 每个 Scene 保留一个当前采用方案；
- 同时保留最多 3 个有明显差异的视觉方案；
- 方案只描述可执行意图、检索词和生成路径；
- 真正的 Asset / Composition 修改仍通过现有 Asset/Editor Command 链。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence


APPROACH_LABELS = {
    "direct": "直叙表达",
    "broll": "细节补画面",
    "concept": "概念化表达",
}

SOURCE_PREFERENCES = {"user_asset", "stock", "generated", "reference_style", "software_evidence"}


def _terms(value: Any, fallback: str = "") -> List[str]:
    if isinstance(value, str):
        raw: Iterable[Any] = value.replace("；", ",").replace("，", ",").split(",")
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        raw = value
    else:
        raw = []

    out: List[str] = []
    seen = set()
    for item in raw:
        term = " ".join(str(item or "").strip().split())
        if not term:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
        if len(out) >= 8:
            break
    if not out and fallback:
        out = [" ".join(fallback.split())]
    return out


def _clean_candidate(scene_id: str, index: int, raw: Mapping[str, Any], scene_text: str) -> dict:
    approach = str(raw.get("approach") or raw.get("style") or "").strip().lower()
    if approach not in APPROACH_LABELS:
        approach = ("direct", "broll", "concept")[min(index, 2)]

    title = str(raw.get("title") or APPROACH_LABELS[approach]).strip()
    visual_intent = str(raw.get("visual_intent") or raw.get("visualIntent") or scene_text).strip()
    source = str(raw.get("source_preference") or "").strip().lower()
    if source not in SOURCE_PREFERENCES:
        source = {"direct": "user_asset", "broll": "stock", "concept": "generated"}[approach]

    generation_type = str(raw.get("generation_type") or "").strip().lower()
    if generation_type not in {"", "image", "video"}:
        generation_type = ""
    if not generation_type and source == "generated":
        generation_type = "image"

    terms = _terms(raw.get("search_terms") or raw.get("searchTerms"), visual_intent)
    prompt = str(raw.get("generation_prompt") or raw.get("generationPrompt") or visual_intent).strip()

    return {
        "id": str(raw.get("id") or f"{scene_id}-C{index + 1}"),
        "title": title[:40],
        "approach": approach,
        "visual_intent": visual_intent[:500],
        "search_terms": terms,
        "source_preference": source,
        "generation_type": generation_type,
        "generation_prompt": prompt[:800],
        "status": str(raw.get("status") or "INTENT_ONLY"),
        "asset_id": str(raw.get("asset_id") or raw.get("assetId") or ""),
    }


def default_candidates(scene_id: str, scene_text: str, base_visual_intent: str = "") -> List[dict]:
    visual = base_visual_intent.strip() or scene_text.strip()
    return [
        _clean_candidate(scene_id, 0, {
            "title": "直叙表达",
            "approach": "direct",
            "visual_intent": visual,
            "source_preference": "user_asset",
            "search_terms": [visual, scene_text],
        }, scene_text),
        _clean_candidate(scene_id, 1, {
            "title": "细节补画面",
            "approach": "broll",
            "visual_intent": f"用具体动作、局部细节或环境 B-roll 解释：{visual}",
            "source_preference": "stock",
            "search_terms": [visual, f"{scene_text} b-roll", f"{visual} close up"],
        }, scene_text),
        _clean_candidate(scene_id, 2, {
            "title": "概念化表达",
            "approach": "concept",
            "visual_intent": f"用更有视觉隐喻和氛围的动态画面表达：{visual}",
            "source_preference": "generated",
            "generation_type": "image",
            "generation_prompt": f"竖屏短视频概念化画面：{visual}。真实、克制、可用于信息表达。",
            "search_terms": [f"{visual} concept", f"{visual} cinematic"],
        }, scene_text),
    ]


def normalize_scene_candidates(
    scene_id: str,
    scene_text: str,
    raw_candidates: Any = None,
    base_visual_intent: str = "",
) -> List[dict]:
    if not isinstance(raw_candidates, list):
        return default_candidates(scene_id, scene_text, base_visual_intent)

    out: List[dict] = []
    seen = set()
    for index, raw in enumerate(raw_candidates[:3]):
        if not isinstance(raw, Mapping):
            continue
        item = _clean_candidate(scene_id, index, raw, scene_text)
        key = item["approach"]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)

    if len(out) < 3:
        for item in default_candidates(scene_id, scene_text, base_visual_intent):
            if item["approach"] in seen:
                continue
            seen.add(item["approach"])
            out.append(item)
            if len(out) >= 3:
                break
    return out[:3]


def build_candidate_snapshot(
    scenes: Sequence[Mapping[str, Any]],
    *,
    selected_by_scene: Mapping[str, str] | None = None,
) -> dict:
    selected_by_scene = selected_by_scene or {}
    items = []
    for scene in scenes:
        sid = str(scene.get("id") or "")
        text = str(scene.get("text") or "")
        candidates = normalize_scene_candidates(
            sid,
            text,
            scene.get("candidates"),
            str(scene.get("visual_intent") or ""),
        )
        selected = str(selected_by_scene.get(sid) or candidates[0]["id"])
        if selected not in {x["id"] for x in candidates}:
            selected = candidates[0]["id"]
        for c in candidates:
            c["selected"] = c["id"] == selected
        items.append({
            "scene_id": sid,
            "selected_candidate_id": selected,
            "candidates": candidates,
        })
    return {"version": 1, "scenes": items}


def find_candidate(snapshot: Mapping[str, Any], scene_id: str, candidate_id: str) -> dict | None:
    for scene in snapshot.get("scenes") or []:
        if scene.get("scene_id") != scene_id:
            continue
        for candidate in scene.get("candidates") or []:
            if candidate.get("id") == candidate_id:
                return dict(candidate)
    return None
