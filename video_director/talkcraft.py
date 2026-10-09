"""TalkCraft-inspired, auditable speech-to-shot planning for Video Director.

This module borrows workflow concepts, not third-party implementation code.
The source of truth remains CompositionIR + the existing Editor Command/Revision
pipeline. Character timings derived from word timestamps are explicitly marked
as interpolated; they are never presented as acoustic forced alignment.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from video_director import config, editor, store
from video_director.models import CompositionIR

SEMANTIC_VOCAB = (
    "钩子", "自我介绍", "介绍他人", "数据", "引用", "对比", "定义", "步骤",
    "列举", "时间地点", "机制", "选择", "过程演示", "空间叙事", "设问",
    "金句", "章节", "论点", "转折", "例证", "号召",
)
VISUAL_NEEDS = ("证据", "身份", "量化", "对比", "结构", "强调", "无")
WEIGHTS = ("main", "sub")
ROLES = ("hook", "context", "problem", "evidence", "turn", "resolution", "cta", "body")
SAFE_PROJECT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
URL_RE = re.compile(r"https?://[^\s）)]+", re.I)
NUMBER_RE = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?\s*(?:%|％|元|万|亿|天|年|人|次|倍|分钟|小时|美元|\$)?")
CN_NUMBER_RE = re.compile(r"[一二三四五六七八九十百千万亿零两]+(?:个|次|天|年|人|元|倍|步|种|条|件)")
_WORD_CHARS = re.compile(r"[一-鿿㐀-䶿A-Za-z0-9]")


class TalkCraftError(Exception):
    """A project-aware planning or validation error."""


def _safe_root(project_id: str) -> Path:
    if not isinstance(project_id, str) or not SAFE_PROJECT_ID.fullmatch(project_id):
        raise TalkCraftError("project_id 格式非法")
    if not store.get_project(project_id):
        raise TalkCraftError(f"项目不存在或尚未进入项目库：{project_id}")
    return config.project_dir(project_id)


def _read_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback
    except (OSError, json.JSONDecodeError) as exc:
        raise TalkCraftError(f"项目文件无法读取：{path.name}（{exc}）") from exc


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
        Path(tmp_name).replace(path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_file(path: Path | None) -> str | None:
    if not path or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm_char(ch: str) -> str:
    return ch.lower() if _WORD_CHARS.fullmatch(ch) else ""


def _semantic_suggestions(text: str) -> tuple[list[str], list[str], list[str]]:
    """Conservative lexical hints, never a substitute for human semantic review."""
    sem: list[str] = []
    need: list[str] = []
    reasons: list[str] = []
    s = text.strip()
    if not s:
        return ["论点"], ["无"], ["空文本，需人工补充"]
    if re.search(r"我(是|叫|们是)", s):
        sem.append("自我介绍")
        need.append("身份")
    if re.search(r"(点赞|关注|订阅|转发|评论|三连)", s):
        sem.append("号召")
        need.append("强调")
    if NUMBER_RE.search(s) or CN_NUMBER_RE.search(s):
        sem.append("数据")
        need.append("量化")
    if re.search(r"(相比|对比|而不是|不如|区别|不同|VS|vs\.)", s):
        sem.append("对比")
        need.append("对比")
    if re.search(r"[“\"「『].+[”\"」』]|(有人说|数据显示|研究表明|根据|来源于)", s):
        sem.append("引用")
        need.append("证据")
    if re.search(r"(什么是|所谓|指的是|定义为)", s):
        sem.append("定义")
        need.append("结构")
    if re.search(r"(首先|第一步|第二步|接下来|然后|最后一步|步骤)", s):
        sem.append("步骤")
        need.append("结构")
    if re.search(r"^(为什么|怎么|如何|难道|是不是)|[？?]$", s):
        sem.append("设问" if ("？" in s or "?" in s) else "钩子")
        need.append("强调")
    if re.search(r"(其实|但是|然而|真正的问题|关键在于)", s):
        sem.append("转折")
        need.append("强调")
    if not sem:
        sem.append("论点")
        reasons.append("仅提供保守默认语义，需人工确认")
    if not need:
        need.append("结构")
        reasons.append("画面需求由默认规则补出，需人工确认")
    return list(dict.fromkeys(sem)), list(dict.fromkeys(need)), reasons


def _entities(text: str) -> list[str]:
    out = []
    out.extend(URL_RE.findall(text))
    out.extend(NUMBER_RE.findall(text))
    out.extend(CN_NUMBER_RE.findall(text))
    return list(dict.fromkeys(x.strip() for x in out if x.strip()))


def _word_chars(words: list[dict], start: float, end: float) -> list[dict]:
    out: list[dict] = []
    for word in words:
        try:
            ws, we = float(word["start"]), float(word["end"])
            raw = str(word.get("text", ""))
        except (KeyError, TypeError, ValueError):
            continue
        if we <= start or ws >= end or we <= ws:
            continue
        # Clip to the scene boundary. Native word timing remains the only measured input.
        ws, we = max(ws, start), min(we, end)
        chars = [_norm_char(c) for c in raw]
        chars = [c for c in chars if c]
        if not chars:
            continue
        span = (we - ws) / len(chars)
        for i, ch in enumerate(chars):
            out.append({
                "text": ch,
                "start": round(ws + span * i, 4),
                "end": round(ws + span * (i + 1), 4),
                "timing_source": "interpolated_from_word",
            })
    return out


def _align_scene_chars(text: str, scene_start: float, scene_end: float,
                       word_chars: list[dict]) -> tuple[list[dict], float]:
    script_chars = [(i, _norm_char(ch)) for i, ch in enumerate(text) if _norm_char(ch)]
    if not script_chars:
        return [], 0.0
    observed = [x["text"] for x in word_chars]
    target = [ch for _, ch in script_chars]
    matcher = difflib.SequenceMatcher(a=target, b=observed, autojunk=False)
    mapped: list[dict | None] = [None] * len(target)
    for block in matcher.get_matching_blocks():
        for j in range(block.size):
            src = word_chars[block.b + j]
            mapped[block.a + j] = {
                "text": target[block.a + j],
                "start": src["start"],
                "end": src["end"],
                "timing_source": src["timing_source"],
            }
    direct = sum(1 for x in mapped if x is not None)
    anchors = [i for i, x in enumerate(mapped) if x is not None]
    if not anchors:
        # There is no evidence-backed anchor. Mark every character UNKNOWN rather than
        # fabricating a precise alignment from sentence duration.
        width = max(0.0, scene_end - scene_start)
        mapped = []
        for i, ch in enumerate(target):
            a = scene_start + width * i / len(target)
            b = scene_start + width * (i + 1) / len(target)
            mapped.append({"text": ch, "start": round(a, 4), "end": round(b, 4),
                           "timing_source": "estimated_from_scene", "review_required": True})
        return mapped, 0.0
    # Fill unmatched characters between real/derived word anchors, clearly marked.
    for i, item in enumerate(mapped):
        if item is not None:
            continue
        left = max((a for a in anchors if a < i), default=None)
        right = min((a for a in anchors if a > i), default=None)
        if left is None:
            end_t = mapped[right]["start"] if right is not None else scene_end
            start_t = max(scene_start, end_t - min(0.08 * (right - i), end_t - scene_start))
        elif right is None:
            start_t = mapped[left]["end"]
            end_t = min(scene_end, start_t + min(0.08 * (i - left), scene_end - start_t))
        else:
            start_t = mapped[left]["end"]
            end_t = mapped[right]["start"]
            if end_t < start_t:
                end_t = start_t
            step = (end_t - start_t) / (right - left)
            start_t = start_t + step * (i - left - 1)
            end_t = start_t + step
        mapped[i] = {"text": target[i], "start": round(start_t, 4), "end": round(end_t, 4),
                     "timing_source": "interpolated_between_anchors", "review_required": True}
    coverage = direct / max(1, len(target))
    return [x for x in mapped if x is not None], round(coverage, 4)


def _composition_signature(ir: CompositionIR, word_hash: str | None) -> str:
    material = {
        "scenes": [{"id": s.id, "start": s.start, "duration": s.duration, "text": s.text}
                   for s in ir.scenes],
        "word_hash": word_hash,
        "fps": ir.project.fps,
    }
    return _hash_text(json.dumps(material, ensure_ascii=False, sort_keys=True))


def _load_inputs(project_id: str):
    root = _safe_root(project_id)
    ir = editor.load_composition(project_id)
    words_path = root / "alignment" / "words.json"
    raw_words = _read_json(words_path, [])
    if isinstance(raw_words, dict):
        raw_words = raw_words.get("words") or raw_words.get("word_timeline") or []
    if not isinstance(raw_words, list):
        raise TalkCraftError("alignment/words.json 格式不正确；需要 [{text,start,end}]")
    word_hash = _hash_file(words_path)
    audio_candidates = [root / "audio" / "narration.mp3", root / "audio" / "narration.wav",
                        root / "audio" / "full.wav"]
    audio_path = next((p for p in audio_candidates if p.is_file()), None)
    input_signature = _composition_signature(ir, word_hash)
    return root, ir, raw_words, word_hash, _hash_file(audio_path), input_signature


def _build_artifacts(project_id: str, preserve_edits: bool = True) -> dict:
    root, ir, words, word_hash, audio_hash, signature = _load_inputs(project_id)
    timing_path = root / "alignment" / "character_timing.json"
    beats_path = root / "plans" / "semantic_beats.json"
    shots_path = root / "plans" / "shot_plan.json"
    old_beats = _read_json(beats_path, {}) or {}
    old_items = {b.get("id"): b for b in old_beats.get("beats", []) if isinstance(b, dict)}
    old_shots = _read_json(shots_path, {}) or {}
    old_shot_map = {s.get("scene_id"): s for s in old_shots.get("shots", []) if isinstance(s, dict)}
    same_source = old_beats.get("input_signature") == signature

    timing_sentences = []
    beats = []
    shots = []
    notes = []
    all_direct = 0
    all_chars = 0
    for index, scene in enumerate(ir.scenes):
        start, end = float(scene.start), float(scene.start + scene.duration)
        text = scene.text or ""
        scene_words = _word_chars(words, start, end)
        chars, coverage = _align_scene_chars(text, start, end, scene_words)
        all_direct += sum(1 for c in chars if c.get("timing_source") == "interpolated_from_word")
        all_chars += len(chars)
        timing_status = "ALIGNED_FROM_WORDS" if coverage >= 0.75 else ("PARTIAL" if coverage > 0 else "UNKNOWN")
        if timing_status != "ALIGNED_FROM_WORDS":
            notes.append(f"{scene.id}：逐字时间覆盖需要复核（{timing_status}）")
        timing_sentences.append({
            "scene_id": scene.id, "text": text, "start": round(start, 4), "end": round(end, 4),
            "coverage": coverage, "status": timing_status, "characters": chars,
        })
        sem, need, reasons = _semantic_suggestions(text)
        beat_id = f"beat-{scene.id}"
        previous = old_items.get(beat_id) if preserve_edits and same_source else None
        sem = previous.get("sem", sem) if previous else sem
        need = previous.get("need", need) if previous else need
        weight = previous.get("weight", "main") if previous else "main"
        intent = previous.get("intent", scene.intent or "") if previous else (scene.intent or "")
        visual_intent = previous.get("visual_intent", scene.visual_intent or "") if previous else (scene.visual_intent or "")
        review_status = previous.get("review_status", "NEEDS_REVIEW") if previous else "NEEDS_REVIEW"
        entities = previous.get("entities", _entities(text)) if previous else _entities(text)
        beats.append({
            "id": beat_id, "scene_id": scene.id, "index": index, "text": text,
            "start": round(start, 4), "end": round(end, 4), "sem": sem, "entities": entities,
            "need": need, "weight": weight, "intent": intent, "visual_intent": visual_intent,
            "timing_status": timing_status, "timing_coverage": coverage,
            "review_status": review_status, "review_reasons": reasons,
        })
        old_shot = old_shot_map.get(scene.id, {}) if same_source else {}
        layer_types = [getattr(l.type, "value", str(l.type)) for l in scene.layers]
        shots.append({
            "scene_id": scene.id, "start": round(start, 4), "end": round(end, 4),
            "text": text, "beat_ids": [beat_id],
            "intent": old_shot.get("intent", intent),
            "visual_intent": old_shot.get("visual_intent", visual_intent),
            "recipe_id": old_shot.get("recipe_id") or scene.origin_recipe_id,
            "layer_types": layer_types,
            "layer_count": len(scene.layers),
            "transition_out": getattr(scene.transition_out, "type", "cut"),
            "review_status": review_status,
        })

    timing = {
        "version": 1, "project_id": project_id, "input_signature": signature,
        "word_timing_hash": word_hash, "audio_hash": audio_hash,
        "fps": ir.project.fps, "generated_at": time.time(),
        "timing_contract": "Character intervals are derived from existing word timestamps; interpolated characters are explicitly labelled.",
        "sentences": timing_sentences,
    }
    semantic_doc = {
        "version": 1, "project_id": project_id, "input_signature": signature,
        "vocabulary": list(SEMANTIC_VOCAB), "visual_needs": list(VISUAL_NEEDS),
        "generated_at": time.time(), "revision": int(old_beats.get("revision", 1)) if same_source else 1, "beats": beats,
    }
    shot_doc = {
        "version": 1, "project_id": project_id, "input_signature": signature,
        "fps": ir.project.fps, "generated_at": time.time(),
        "revision": int(old_shots.get("revision", 1)) if same_source else 1,
        "source_of_truth": "CompositionIR scene ranges; this file is the editable shot-planning overlay.",
        "shots": shots,
    }
    _atomic_json(timing_path, timing)
    _atomic_json(beats_path, semantic_doc)
    _atomic_json(shots_path, shot_doc)
    return _snapshot(project_id, root, ir, timing, semantic_doc, shot_doc, notes, all_direct, all_chars)


def _list_recipes() -> list[dict]:
    from video_director.registries.recipes import MotionRegistry
    result = []
    try:
        recipes = MotionRegistry().all()
    except Exception as exc:
        return [{"id": "", "name": "动效库不可用", "enabled": False, "reason": str(exc)}]
    for item in recipes:
        duration = {
            "min": float(getattr(item, "duration_min", 0.3)),
            "recommended": float(getattr(item, "duration_recommended", 0.5)),
            "max": float(getattr(item, "duration_max", 1.0)),
        }
        channels = []
        for ch in (getattr(item, "channels", ()) or ()):
            channels.append({
                "property": getattr(ch, "property", ""),
                "interpolation": getattr(ch, "interpolation", "linear"),
                "keyframes": [
                    {"at": float(k.at), "value": k.value, "easing": k.easing}
                    for k in (getattr(ch, "keyframes", ()) or ())
                ],
            })
        raw_item = getattr(item, "item", None)
        result.append({
            "id": item.id, "name": item.name,
            "description": getattr(raw_item, "description", "") if raw_item else "",
            "semantic_intents": list(getattr(item, "semantic_intents", ()) or ()),
            "compatible_layers": list(getattr(item, "compatible_layers", ()) or ()),
            "commercial_status": getattr(item, "commercial_status", "UNKNOWN"),
            "enabled": True,
            "provenance_ref": getattr(item, "provenance_ref", None),
            "duration": duration, "channels": channels,
        })
    return result


def _snapshot(project_id: str, root: Path | None = None, ir: CompositionIR | None = None,
              timing: dict | None = None, semantic_doc: dict | None = None,
              shot_doc: dict | None = None, notes: list[str] | None = None,
              direct_chars: int = 0, all_chars: int = 0) -> dict:
    if root is None or ir is None:
        root, ir, _words, _word_hash, _audio_hash, signature = _load_inputs(project_id)
        timing = _read_json(root / "alignment" / "character_timing.json")
        semantic_doc = _read_json(root / "plans" / "semantic_beats.json")
        shot_doc = _read_json(root / "plans" / "shot_plan.json")
        current_signature = _composition_signature(ir, _hash_file(root / "alignment" / "words.json"))
    else:
        current_signature = _composition_signature(ir, _hash_file(root / "alignment" / "words.json"))
    prepared = bool(semantic_doc and shot_doc and timing)
    stale = prepared and semantic_doc.get("input_signature") != current_signature
    beats = semantic_doc.get("beats", []) if prepared else []
    shots = shot_doc.get("shots", []) if prepared else []
    return {
        "ok": True, "project_id": project_id,
        "status": "NOT_PREPARED" if not prepared else ("STALE" if stale else "READY"),
        "stale_reason": "CompositionIR 或 word timestamps 已变化，请重建蓝图；未自动覆盖用户标注。" if stale else "",
        "revision": int(semantic_doc.get("revision", 1)) if prepared else 0,
        "shot_revision": int(shot_doc.get("revision", 1)) if prepared else 0,
        "scene_count": len(ir.scenes),
        "beat_count": len(beats), "shot_count": len(shots),
        "timing_status": "MISSING" if not timing else (
            "PARTIAL" if any(s.get("status") != "ALIGNED_FROM_WORDS" for s in timing.get("sentences", [])) else "READY"
        ),
        "notes": notes or [],
        "beats": beats, "shots": shots, "timing": timing or {},
        "recipes": _list_recipes(),
    }


def get_plan(project_id: str) -> dict:
    return _snapshot(project_id)


def prepare_plan(project_id: str) -> dict:
    """Create/update derived artifacts. Existing user edits survive only when input hashes match."""
    root = _safe_root(project_id)
    existing = _read_json(root / "plans" / "semantic_beats.json", {}) or {}
    _root, ir, _words, word_hash, _audio_hash, signature = _load_inputs(project_id)
    same_source = existing.get("input_signature") == signature
    result = _build_artifacts(project_id, preserve_edits=same_source)
    result["message"] = "已建立口播创作蓝图。" if not same_source else "输入未变化，已保留原有标注并刷新派生时间表。"
    return result


def update_beat(project_id: str, beat_id: str, payload: dict) -> dict:
    root, ir, _words, _word_hash, _audio_hash, signature = _load_inputs(project_id)
    beats_path = root / "plans" / "semantic_beats.json"
    shots_path = root / "plans" / "shot_plan.json"
    semantic_doc = _read_json(beats_path)
    shot_doc = _read_json(shots_path)
    if not semantic_doc or not shot_doc or semantic_doc.get("input_signature") != signature:
        raise TalkCraftError("蓝图尚未建立或已过期；请先刷新蓝图。")
    expected = payload.get("expected_revision")
    current_revision = int(semantic_doc.get("revision", 1))
    if expected is not None and int(expected) != current_revision:
        raise TalkCraftError(f"REVISION_CONFLICT：页面版本 {expected}，当前版本 {current_revision}。请刷新再保存。")
    beat = next((b for b in semantic_doc.get("beats", []) if b.get("id") == beat_id), None)
    if not beat:
        raise TalkCraftError(f"找不到语义拍：{beat_id}")
    sem = payload.get("sem", beat.get("sem", []))
    need = payload.get("need", beat.get("need", []))
    if isinstance(sem, str):
        sem = [x.strip() for x in re.split(r"[、,，;；]", sem) if x.strip()]
    if isinstance(need, str):
        need = [x.strip() for x in re.split(r"[、,，;；]", need) if x.strip()]
    if not isinstance(sem, list) or not sem or any(x not in SEMANTIC_VOCAB for x in sem):
        raise TalkCraftError("语义标签必须从系统词表中选择至少一项。")
    if not isinstance(need, list) or not need or any(x not in VISUAL_NEEDS for x in need):
        raise TalkCraftError("画面需求必须从系统词表中选择至少一项。")
    weight = payload.get("weight", beat.get("weight", "main"))
    if weight not in WEIGHTS:
        raise TalkCraftError("weight 只能是 main 或 sub。")
    intent = str(payload.get("intent", beat.get("intent", ""))).strip()[:500]
    visual_intent = str(payload.get("visual_intent", beat.get("visual_intent", ""))).strip()[:500]
    entities = payload.get("entities", beat.get("entities", []))
    if isinstance(entities, str):
        entities = [x.strip() for x in re.split(r"[、,，;；]", entities) if x.strip()]
    if not isinstance(entities, list) or any(not isinstance(x, str) or len(x) > 200 for x in entities):
        raise TalkCraftError("entities 格式不正确。")
    review_status = "REVIEWED" if payload.get("mark_reviewed") else "NEEDS_REVIEW"
    scene_id = beat["scene_id"]
    # Persist the creative intent through the official Command -> Revision path.
    result = editor.execute_command(project_id, {
        "command": "set_scene_intent",
        "targetId": scene_id,
        "payload": {
            "intent": intent, "visualIntent": visual_intent,
            "semanticRole": payload.get("semantic_role", ""),
        },
        "expectedVersion": payload.get("composition_version"),
        "author": "talkcraft-workbench",
    })
    if result.status != "APPLIED":
        detail = "; ".join(str(e.get("message", e)) for e in (result.errors or []))
        raise TalkCraftError(f"场景意图未保存到时间线：{detail or result.status}")
    beat.update({
        "sem": list(dict.fromkeys(sem)), "need": list(dict.fromkeys(need)),
        "weight": weight, "intent": intent, "visual_intent": visual_intent,
        "entities": list(dict.fromkeys(entities)), "review_status": review_status,
        "last_edited_at": time.time(),
    })
    semantic_doc["revision"] = current_revision + 1
    semantic_doc["generated_at"] = time.time()
    _atomic_json(beats_path, semantic_doc)
    shot = next((s for s in shot_doc.get("shots", []) if s.get("scene_id") == scene_id), None)
    if shot:
        shot.update({"intent": intent, "visual_intent": visual_intent, "review_status": review_status})
        shot_doc["generated_at"] = time.time()
        _atomic_json(shots_path, shot_doc)
    return {
        "ok": True, "revision": semantic_doc["revision"],
        "composition_version": result.composition_version,
        "beat": beat, "shot": shot, "composition": result.composition,
        "message": "标注与场景意图已保存；已通过编辑器 Revision 记录。",
    }


def save_shot_recipe(project_id: str, scene_id: str, recipe_id: str,
                     expected_revision: int | None = None) -> dict:
    """Save planned recipe choice. Applying a recipe to a real layer is a separate explicit Action."""
    root, ir, _words, _word_hash, _audio_hash, signature = _load_inputs(project_id)
    path = root / "plans" / "shot_plan.json"
    shot_doc = _read_json(path)
    if not shot_doc or shot_doc.get("input_signature") != signature:
        raise TalkCraftError("蓝图尚未建立或已过期；请先刷新蓝图。")
    current_rev = int(shot_doc.get("revision", 1))
    if expected_revision is not None and int(expected_revision) != current_rev:
        raise TalkCraftError(f"REVISION_CONFLICT：页面版本 {expected_revision}，当前版本 {current_rev}。")
    shots = shot_doc.get("shots", [])
    shot = next((s for s in shots if s.get("scene_id") == scene_id), None)
    if not shot:
        raise TalkCraftError(f"找不到镜头：{scene_id}")
    valid = {x["id"]: x for x in _list_recipes() if x.get("id")}
    recipe = valid.get(recipe_id)
    if not recipe or not recipe.get("enabled") or recipe.get("commercial_status") == "BLOCKED":
        raise TalkCraftError("所选动效配方不存在、被禁用或禁止使用。")
    scene = next((s for s in ir.scenes if s.id == scene_id), None)
    if not scene:
        raise TalkCraftError("CompositionIR 中找不到对应场景。")
    applicable = [x for x in recipe.get("compatible_layers", []) if x in {
        getattr(l.type, "value", str(l.type)) for l in scene.layers
    }]
    if not applicable:
        raise TalkCraftError("该配方声明的输入类型与当前镜头图层不匹配；请选择兼容配方。")
    shot["recipe_id"] = recipe_id
    shot["recipe_name"] = recipe.get("name", recipe_id)
    shot["recipe_status"] = "PLANNED_NOT_APPLIED"
    shot_doc["revision"] = current_rev + 1
    shot_doc["generated_at"] = time.time()
    _atomic_json(path, shot_doc)
    return {"ok": True, "revision": shot_doc["revision"], "shot": shot,
            "message": "已保存镜头配方选择。此操作只更新镜头计划；点击“应用到当前图层”才会修改实际时间线。"}


def apply_shot_recipe(project_id: str, scene_id: str, layer_index: int,
                      recipe_id: str, expected_version: int | None) -> dict:
    """Apply a planned recipe using the existing editor command path and version gate."""
    root, ir, _words, _word_hash, _audio_hash, signature = _load_inputs(project_id)
    plan = _read_json(root / "plans" / "shot_plan.json")
    if not plan or plan.get("input_signature") != signature:
        raise TalkCraftError("蓝图尚未建立或已过期。")
    scene = next((s for s in ir.scenes if s.id == scene_id), None)
    if not scene:
        raise TalkCraftError("找不到场景。")
    if layer_index < 0 or layer_index >= len(scene.layers):
        raise TalkCraftError("图层索引越界。")
    layer = scene.layers[layer_index]
    layer_type = getattr(layer.type, "value", str(layer.type))
    recipe = next((x for x in _list_recipes() if x.get("id") == recipe_id), None)
    if not recipe or not recipe.get("enabled"):
        raise TalkCraftError("所选配方不可用。")
    if layer_type not in recipe.get("compatible_layers", []):
        raise TalkCraftError(f"配方 {recipe.get('name', recipe_id)} 不支持 {layer_type} 图层。")
    # Current FFmpeg renderer only permits opacity-only motion channels on video layers.
    channels = recipe.get("channels", [])
    props = {c.get("property") for c in channels if isinstance(c, dict)}
    if layer_type == "video" and props - {"opacity"}:
        raise TalkCraftError("该配方包含当前视频层渲染器不支持的运动通道；已阻止应用，避免渲染时才失败。")
    result = editor.execute_command(project_id, {
        "command": "replace_motion", "targetId": f"{scene_id}#{layer_index}",
        "payload": {"motionRecipeId": recipe_id},
        "expectedVersion": expected_version, "author": "talkcraft-workbench",
    })
    if result.status != "APPLIED":
        detail = "; ".join(str(e.get("message", e)) for e in (result.errors or []))
        raise TalkCraftError(f"动效未应用：{detail or result.status}")
    plan_shot = next((s for s in plan.get("shots", []) if s.get("scene_id") == scene_id), None)
    if plan_shot:
        plan_shot["recipe_status"] = "APPLIED_TO_LAYER"
        plan_shot["applied_layer"] = f"{scene_id}#{layer_index}"
        plan_shot["composition_version"] = result.composition_version
        _atomic_json(root / "plans" / "shot_plan.json", plan)
    return {"ok": True, "composition_version": result.composition_version,
            "changed_artifacts": result.changed_artifacts, "stale_artifacts": result.stale_artifacts,
            "message": "动效已通过 Editor Command → Revision 应用到图层。"}
