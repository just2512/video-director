"""Semantic Rerank + Visual QA。

职责：
- Marengo：Scene 文本 ↔ 视频素材做多模态 embedding，生成候选顺序。
- Pegasus：对候选素材做事实型视觉 QA，输出离散 verdict + evidence。
- 不向产品持久化 similarity / probability / score。
- Provider 不可用、素材超出当前同步接口硬限制时返回 UNKNOWN/MISSING_REQUIREMENT，
  不伪造“视觉理解成功”。

Twelve Labs 当前公开 API 采用：
- Marengo 3.5: POST /v1.3/embed-v2，multi_input，可组合文本和视频媒体。
- Pegasus 1.5: POST /v1.3/analyze，同步分析，可接 base64/url/asset_id。
本实现使用本地文件 base64，避免要求用户先把素材公开到互联网。
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from loguru import logger

from video_director import config
from video_director import model_config
from video_director import provider_matrix


VERDICTS = (
    "DIRECT_MATCH",
    "SUPPORTING",
    "CONFLICT",
    "UNKNOWN",
)
RERANK_READY = "READY"
RERANK_UNKNOWN = "UNKNOWN"


class VisualAnalysisError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True)
class VisualEvidence:
    scene_id: str
    asset_id: str
    verdict: str
    provider: str
    evidence: str = ""
    subjects: Tuple[str, ...] = ()
    actions: Tuple[str, ...] = ()
    on_screen_text: Tuple[str, ...] = ()
    time_ranges: Tuple[Tuple[float, float], ...] = ()
    status: str = RERANK_READY
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "asset_id": self.asset_id,
            "verdict": self.verdict,
            "provider": self.provider,
            "evidence": self.evidence,
            "subjects": list(self.subjects),
            "actions": list(self.actions),
            "on_screen_text": list(self.on_screen_text),
            "time_ranges": [
                [float(a), float(b)] for a, b in self.time_ranges
            ],
            "status": self.status,
            "reason": self.reason,
        }


def _role(role: str) -> dict:
    return model_config.get_role_config(role)


def _post_json(url: str, api_key: str, payload: dict,
               timeout: int = 120) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as exc:
        raise VisualAnalysisError("PROVIDER_REQUEST_FAILED", str(exc)) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VisualAnalysisError(
            "PROVIDER_BAD_JSON", f"Provider 返回非 JSON: {raw[:300]}"
        ) from exc


def _api(role: str) -> tuple[str, str, str]:
    cfg = _role(role)
    try:
        provider_matrix.require(
            role,
            "visual.semantic_retrieve" if role == "semantic_rerank" else "visual.qa",
            cfg,
        )
    except provider_matrix.ProviderCapabilityError as exc:
        code = (
            "PROVIDER_MISSING"
            if exc.state.status == provider_matrix.MISSING
            else "CAPABILITY_UNSUPPORTED"
            if exc.state.status == provider_matrix.UNSUPPORTED
            else "PROVIDER_UNKNOWN"
        )
        raise VisualAnalysisError(code, str(exc)) from exc
    return (
        str(cfg.get("base_url") or "").rstrip("/"),
        str(cfg.get("api_key") or ""),
        str(cfg.get("model") or ""),
    )


def _read_inline_video(path: str, max_bytes: int) -> str:
    p = Path(path)
    if not p.is_file():
        raise VisualAnalysisError("ASSET_FILE_MISSING", f"素材文件不存在：{path}")
    size = p.stat().st_size
    if size > max_bytes:
        raise VisualAnalysisError(
            "MISSING_REQUIREMENT",
            f"本地视频 {size} bytes 超过同步接口 base64 上限 {max_bytes} bytes",
        )
    return base64.b64encode(p.read_bytes()).decode("ascii")


def _extract_embedding(data: dict) -> List[float]:
    rows = data.get("data")
    if not isinstance(rows, list) or not rows:
        raise VisualAnalysisError("PROVIDER_EMPTY", "Marengo 未返回 embedding")
    emb = rows[0].get("embedding")
    if not isinstance(emb, list) or not emb:
        raise VisualAnalysisError("PROVIDER_EMPTY", "Marengo embedding 为空")
    return [float(x) for x in emb]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n <= 0:
        raise VisualAnalysisError("EMBEDDING_INVALID", "embedding 长度为空")
    aa = a[:n]
    bb = b[:n]
    na = math.sqrt(sum(x * x for x in aa))
    nb = math.sqrt(sum(x * x for x in bb))
    if na == 0 or nb == 0:
        raise VisualAnalysisError("EMBEDDING_INVALID", "embedding 范数为 0")
    return sum(x * y for x, y in zip(aa, bb)) / (na * nb)


def _cache_path() -> Path:
    p = config.composer_storage_root() / "assets" / "semantic_cache.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_cache() -> dict:
    p = _cache_path()
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    _cache_path().write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _asset_fingerprint(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()[:24]


def marengo_embed_text(text: str) -> List[float]:
    """Scene 文本 embedding。"""
    base_url, api_key, model = _api("semantic_rerank")
    result = _post_json(
        f"{base_url}/v1.3/embed-v2",
        api_key,
        {
            "input_type": "multi_input",
            "model_name": model or "marengo3.5",
            "multi_input": {"input_text": str(text or "").strip()},
        },
    )
    return _extract_embedding(result)


def marengo_embed_video(path: str, *, asset_cache_key: str = "") -> List[float]:
    """视频 embedding。

    当前按 Marengo 3.5 同步接口硬限制：
    - 单视频 ≤ 30 秒
    - base64 媒体 ≤ 32 MB
    超限不偷偷截断，直接 MISSING_REQUIREMENT。
    """
    base_url, api_key, model = _api("semantic_rerank")
    try:
        from video_director import prober
        probe = prober.probe_media(path)
        duration = float(probe.duration or 0.0)
    except Exception as exc:
        raise VisualAnalysisError(
            "ASSET_PROBE_FAILED", f"无法读取素材时长：{exc}"
        ) from exc

    if duration > 30.0:
        raise VisualAnalysisError(
            "MISSING_REQUIREMENT",
            f"素材时长 {duration:.2f}s > Marengo 3.5 同步多媒体上限 30s；"
            "当前不自动截断，以免丢失场景语义",
        )

    key = asset_cache_key or _asset_fingerprint(path)
    cache = _load_cache()
    cache_key = f"marengo3.5:video:{key}"
    cached = cache.get(cache_key)
    if isinstance(cached, list) and cached:
        return [float(x) for x in cached]

    b64 = _read_inline_video(path, 32 * 1024 * 1024)
    result = _post_json(
        f"{base_url}/v1.3/embed-v2",
        api_key,
        {
            "input_type": "multi_input",
            "model_name": model or "marengo3.5",
            "multi_input": {
                "media_sources": [{
                    "name": "clip",
                    "media_type": "video",
                    "base_64_string": b64,
                }],
            },
        },
    )
    emb = _extract_embedding(result)
    cache[cache_key] = emb
    _save_cache(cache)
    return emb


def semantic_rerank(
    scene_text: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    limit: int = 5,
) -> tuple[List[dict], dict]:
    """Marengo 语义重排。

    对外不返回 similarity/score。返回按语义相关度排列的候选顺序，
    并对无法验证的素材保留 UNKNOWN，而不是伪装成可比较结果。
    """
    candidates = list(candidates or [])
    if not candidates:
        return [], {
            "status": RERANK_UNKNOWN,
            "provider": "twelvelabs-marengo",
            "reason": "无候选素材",
        }

    try:
        query = marengo_embed_text(scene_text)
    except VisualAnalysisError as exc:
        return [dict(c) for c in candidates], {
            "status": RERANK_UNKNOWN,
            "provider": "twelvelabs-marengo",
            "reason": str(exc),
        }

    ranked: List[tuple[float, int, dict]] = []
    unresolved: List[dict] = []
    for idx, candidate in enumerate(candidates):
        item = dict(candidate)
        path = item.get("file") or ""
        try:
            emb = marengo_embed_video(path, asset_cache_key=item.get("content_hash", ""))
            # similarity 只用于内部排序，不写入返回对象/项目台账。
            sim = _cosine(query, emb)
            ranked.append((sim, -idx, item))
        except VisualAnalysisError as exc:
            item["semantic_status"] = "UNKNOWN"
            item["semantic_reason"] = str(exc)
            unresolved.append(item)

    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    ordered = [item for _, _, item in ranked]
    ordered.extend(unresolved)
    return ordered[: max(1, int(limit))], {
        "status": RERANK_READY if ranked else RERANK_UNKNOWN,
        "provider": "twelvelabs-marengo",
        "reason": "" if ranked else "候选均无法完成语义 embedding",
    }


def _parse_json_text(raw: str) -> dict:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise VisualAnalysisError("PROVIDER_BAD_JSON", raw[:300])
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError as exc:
        raise VisualAnalysisError(
            "PROVIDER_BAD_JSON", f"Pegasus 返回 JSON 解析失败：{raw[:300]}"
        ) from exc


def _normalize_verdict(value: Any) -> str:
    verdict = str(value or "").strip().upper()
    return verdict if verdict in VERDICTS else "UNKNOWN"


QA_PROMPT = """你是短视频视觉 QA。请判断这段视频素材是否适合表达下面这个文案场景。

场景：
{scene_text}

只输出 JSON：
{{
  "verdict": "DIRECT_MATCH|SUPPORTING|CONFLICT|UNKNOWN",
  "evidence": "只描述视频中实际看见/听见/读到的证据，不要推测",
  "subjects": ["实际出现的主体"],
  "actions": ["实际发生的动作"],
  "on_screen_text": ["实际看到的屏幕文字"],
  "time_ranges": [[start_sec, end_sec]]
}}

判定：
- DIRECT_MATCH：画面主体/动作/环境直接表达场景核心意思
- SUPPORTING：不能直接表达核心意思，但能明确作为辅助画面
- CONFLICT：画面与场景核心意思明显冲突
- UNKNOWN：证据不足或无法可靠判断

不要因为文件名、用户提示词或常识替视频补充不存在的事实。
"""

def pegasus_visual_qa(scene_id: str, scene_text: str, asset_id: str,
                      asset_file: str) -> VisualEvidence:
    base_url, api_key, model = _api("visual_qa")
    b64 = _read_inline_video(asset_file, 30 * 1024 * 1024)
    result = _post_json(
        f"{base_url}/v1.3/analyze",
        api_key,
        {
            "model_name": model or "pegasus1.5",
            "video": {
                "type": "base64_string",
                "base_64_string": b64,
            },
            "prompt": QA_PROMPT.format(scene_text=scene_text),
            "stream": False,
            "max_tokens": 700,
        },
    )
    raw = str(result.get("data") or result.get("text") or "")
    if not raw:
        raise VisualAnalysisError("PROVIDER_EMPTY", "Pegasus 未返回分析文本")
    parsed = _parse_json_text(raw)

    ranges = []
    for row in parsed.get("time_ranges") or []:
        try:
            if isinstance(row, (list, tuple)) and len(row) == 2:
                a, b = float(row[0]), float(row[1])
                if a >= 0 and b >= a:
                    ranges.append((a, b))
        except (TypeError, ValueError):
            continue

    return VisualEvidence(
        scene_id=scene_id,
        asset_id=asset_id,
        verdict=_normalize_verdict(parsed.get("verdict")),
        provider="twelvelabs-pegasus",
        evidence=str(parsed.get("evidence") or "").strip(),
        subjects=tuple(str(x).strip() for x in parsed.get("subjects") or [] if str(x).strip())[:12],
        actions=tuple(str(x).strip() for x in parsed.get("actions") or [] if str(x).strip())[:12],
        on_screen_text=tuple(str(x).strip() for x in parsed.get("on_screen_text") or [] if str(x).strip())[:12],
        time_ranges=tuple(ranges[:12]),
    )


def verify_scene_candidates(
    scene_id: str,
    scene_text: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    max_qa: int = 3,
) -> dict:
    """先 Marengo 重排，再对前若干候选做 Pegasus 硬 verdict。

    最终顺序使用离散 verdict：
    DIRECT_MATCH → SUPPORTING → UNKNOWN → CONFLICT。
    不输出任何数值 score。
    """
    ranked, rerank_meta = semantic_rerank(
        scene_text, candidates, limit=max(max_qa, len(candidates))
    )
    qa_results: List[VisualEvidence] = []
    for candidate in ranked[:max_qa]:
        asset_id = str(candidate.get("asset_id") or candidate.get("assetId") or "")
        asset_file = str(candidate.get("file") or "")
        if not asset_id or not asset_file:
            continue
        try:
            qa_results.append(
                pegasus_visual_qa(scene_id, scene_text, asset_id, asset_file)
            )
        except VisualAnalysisError as exc:
            qa_results.append(VisualEvidence(
                scene_id=scene_id,
                asset_id=asset_id,
                verdict="UNKNOWN",
                provider="twelvelabs-pegasus",
                status=RERANK_UNKNOWN,
                reason=str(exc),
            ))

    order = {
        "DIRECT_MATCH": 0,
        "SUPPORTING": 1,
        "UNKNOWN": 2,
        "CONFLICT": 3,
    }
    qa_results.sort(key=lambda x: order.get(x.verdict, 2))
    selected = qa_results[0].asset_id if qa_results and qa_results[0].verdict == "DIRECT_MATCH" else None
    return {
        "scene_id": scene_id,
        "status": RERANK_READY if qa_results else RERANK_UNKNOWN,
        "selected_asset_id": selected,
        "rerank": rerank_meta,
        "verdicts": [q.to_dict() for q in qa_results],
    }


def save_scene_evidence(project_id: str, evidence: VisualEvidence) -> Path:
    """持久化单条 QA 证据，按 scene_id + asset_id 幂等覆盖。"""
    path = config.project_dir(project_id) / "plans" / "visual_qa.json"
    reports = []
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            reports = current.get("reports", []) if isinstance(current, dict) else []
        except (OSError, json.JSONDecodeError):
            reports = []
    row = evidence.to_dict()
    reports = [
        x for x in reports
        if not (
            isinstance(x, dict)
            and x.get("scene_id") == evidence.scene_id
            and x.get("asset_id") == evidence.asset_id
        )
    ]
    reports.append(row)
    return save_scene_report(project_id, reports)


def save_scene_report(project_id: str, scene_reports: Sequence[Mapping[str, Any]]) -> Path:
    path = config.project_dir(project_id) / "plans" / "visual_qa.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "reports": list(scene_reports),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
