"""MPT 语义融合层：把 MPT 的生产语义接入 Director，而不是复制 MPT pipeline。"""

from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Sequence\nimport json

PAUSE_TAG_RE = re.compile(
    r"[\[\(]\s*(?:pause|pausa|silence|silencio|silêncio|silenzio|stille|"
    r"пауза|тишина|停顿|暂停|静音|ポーズ|一時停止|無音|일시중지|정지)\b"
    r"(?:\s*[:：]?\s*([^\]\)]*?))?\s*[\]\)]",
    re.IGNORECASE,
)
PAUSE_MIN_SECONDS = 0.1
PAUSE_MAX_SECONDS = 10.0

@dataclass(frozen=True)
class PauseEvent:
    at_text_offset: int
    duration: float
    raw: str

@dataclass(frozen=True)
class SpeechSegment:
    text: str
    pause_after: float = 0.0

@dataclass(frozen=True)
class MaterialUsage:
    asset_id: str
    source_in: float
    source_out: float
    scene_id: str
    usage_index: int
    search_term: str = ""
    selection_source: str = "planner"

@dataclass(frozen=True)
class MaterialIntent:
    scene_id: str
    text: str
    search_terms: tuple[str, ...] = ()
    ordered: bool = True
    target_duration: float = 0.0
    max_clip_duration: Optional[float] = None
    source_preference: str = "user_first"
    reuse_policy: str = "reuse_when_needed"
    diversity_policy: str = "prefer_unique_source"
    selection_reason: str = ""
    selected_asset_id: str = ""

def _parse_pause_value(raw: Optional[str]) -> Optional[float]:
    if raw is None or not raw.strip():
        return 1.0
    m = re.fullmatch(
        r"[+-]?\d+(?:\.\d+)?\s*"
        r"(?:s|sec|secs|second|seconds|ms|msec|msecs|秒|毫秒)?",
        raw.strip(), re.IGNORECASE,
    )
    if not m:
        return None
    num = re.match(r"([+-]?\d+(?:\.\d+)?)", raw.strip())
    if not num:
        return None
    value = float(num.group(1))
    unit = raw.strip()[num.end():].strip().lower()
    if "ms" in unit or "毫秒" in unit:
        value /= 1000.0
    if value <= 0:
        return None
    return max(PAUSE_MIN_SECONDS, min(PAUSE_MAX_SECONDS, value))

def parse_pause_markup(text: str) -> tuple[List[SpeechSegment], List[PauseEvent]]:
    """复刻 MPT pause markup：无效删除、0.1s 下限、10s 上限、连续停顿合并。"""
    text = text or ""
    if not text:
        return [], []

    segments: List[SpeechSegment] = []
    events: List[PauseEvent] = []
    cursor = 0

    for match in PAUSE_TAG_RE.finditer(text):
        speech = text[cursor:match.start()].strip()
        if speech:
            segments.append(SpeechSegment(speech, 0.0))

        duration = _parse_pause_value(match.group(1))
        cursor = match.end()
        if duration is None:
            continue

        # MPT：连续 pause 标签合并；无语音前缀时保留 leading pause。
        if segments:
            prev = segments[-1]
            merged = min(PAUSE_MAX_SECONDS, prev.pause_after + duration)
            segments[-1] = SpeechSegment(prev.text, merged)
        else:
            segments.append(SpeechSegment("", min(PAUSE_MAX_SECONDS, duration)))

        if events and events[-1].at_text_offset >= match.start() - 1:
            events[-1] = PauseEvent(
                events[-1].at_text_offset,
                min(PAUSE_MAX_SECONDS, events[-1].duration + duration),
                events[-1].raw + match.group(0),
            )
        else:
            events.append(PauseEvent(match.start(), duration, match.group(0)))

    tail = text[cursor:].strip()
    if tail:
        segments.append(SpeechSegment(tail, 0.0))
    return segments, events\n\n\ndef remove_pause_markup(text: str) -> str:
    cleaned = PAUSE_TAG_RE.sub(" ", text or "")
    return re.sub(r"[ \t]+", " ", cleaned).strip()

def normalize_search_terms(raw: Any, scene_text: str = "", amount: int = 8) -> List[str]:
    values: Iterable[Any]
    if isinstance(raw, str):
        values = re.split(r"[\n,，;；|]+", raw)
    elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        values = raw
    else:
        values = []
    out: List[str] = []
    seen = set()
    for item in values:
        term = str(item or "").strip().strip("*[]\\\"' ")
        term = re.sub(r"^[-•\d.)、]+\s*", "", term).strip()
        if not term:
            continue
        key = re.sub(r"\s+", " ", term).casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(re.sub(r"\s+", " ", term))
        if len(out) >= max(1, amount):
            break
    if not out and scene_text.strip():
        out = [remove_pause_markup(scene_text)]
    return out

def build_material_intent(
    scene: Mapping[str, Any],
    *,
    search_terms: Any = None,
    max_clip_duration: Optional[float] = None,
    source_preference: str = "user_first",
    selected_asset_id: str = "",
    selection_reason: str = "",
) -> MaterialIntent:
    scene_id = str(scene.get("id") or "")
    text = remove_pause_markup(str(scene.get("text") or ""))
    duration = float(scene.get("duration") or 0.0)
    terms = normalize_search_terms(search_terms, text, amount=8)
    return MaterialIntent(
        scene_id=scene_id,
        text=text,
        search_terms=tuple(terms),
        target_duration=round(max(0.0, duration), 3),
        max_clip_duration=max_clip_duration,
        source_preference=source_preference,
        selected_asset_id=selected_asset_id,
        selection_reason=selection_reason,
    )

def material_usage_from_ir(ir: Any) -> List[MaterialUsage]:
    counts = {}
    out: List[MaterialUsage] = []
    for scene in getattr(ir, "scenes", []) or []:
        scene_id = str(getattr(scene, "id", "") or "")
        for layer in getattr(scene, "layers", []) or []:
            layer_type = getattr(layer, "type", None)
            layer_type = getattr(layer_type, "value", layer_type)
            if layer_type != "video":
                continue
            asset_id = str(getattr(layer, "asset_id", "") or "")
            if not asset_id:
                continue
            source_in = float(getattr(layer, "source_in", 0.0) or 0.0)
            source_out = float(getattr(layer, "source_out", source_in) or source_in)
            index = counts.get(asset_id, 0) + 1
            counts[asset_id] = index
            out.append(MaterialUsage(
                asset_id=asset_id,
                source_in=round(source_in, 3),
                source_out=round(source_out, 3),
                scene_id=scene_id,
                usage_index=index,
            ))
    return out

def build_artifact_fingerprint(parts: Mapping[str, Any]) -> str:
    def stable(v: Any) -> Any:
        if isinstance(v, Mapping):
            return {str(k): stable(v[k]) for k in sorted(v)}
        if isinstance(v, (list, tuple)):
            return [stable(x) for x in v]
        return v
    canonical = json.dumps(
        stable(parts),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]

def material_intent_snapshot(
    scenes: Sequence[Mapping[str, Any]],
    *,
    search_terms_by_scene: Optional[Mapping[str, Any]] = None,
    max_clip_duration: Optional[float] = None,
    selected_assets: Optional[Mapping[str, str]] = None,
    selection_reasons: Optional[Mapping[str, str]] = None,
) -> dict:
    search_terms_by_scene = search_terms_by_scene or {}
    selected_assets = selected_assets or {}
    selection_reasons = selection_reasons or {}
    intents = []
    for scene in scenes:
        sid = str(scene.get("id") or "")
        i = build_material_intent(
            scene,
            search_terms=search_terms_by_scene.get(sid),
            max_clip_duration=max_clip_duration,
            selected_asset_id=selected_assets.get(sid, ""),
            selection_reason=selection_reasons.get(sid, ""),
        )
        intents.append({
            "scene_id": i.scene_id,
            "text": i.text,
            "search_terms": list(i.search_terms),
            "search_order": list(i.search_terms),
            "ordered": i.ordered,
            "target_duration": i.target_duration,
            "max_clip_duration": i.max_clip_duration,
            "source_preference": i.source_preference,
            "reuse_policy": i.reuse_policy,
            "diversity_policy": i.diversity_policy,
            "selected_asset_id": i.selected_asset_id,
            "selection_reason": i.selection_reason,
        })
    return {"version": 1, "scenes": intents}
