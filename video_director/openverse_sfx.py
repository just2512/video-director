"""Openverse 音效自动搜索（免 API Key）。

接口：https://api.openverse.org/v1/audio/（无 Key 每分钟 20 次、每天 200 次）
策略：音效按来源 freesound 搜（59 万条音效），license=cc0 只挑 CC0——
每条结果自带 attribution 现成署名字符串，随 manifest 落盘，不靠用户补。

接入点：本地 SFX 库未命中时（resolve_action_sfx 返回 None），按语义类别
自动搜索并下载入库（audio_library/sfx-openverse/），沿用 audio_manifest.json
契约 → scan_library() 立即可见，下游 resolve/build_sfx_track 无需改动。
网络失败/限频/无结果 → 诚实降级返回 None，不装成功。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

#: 免 Key 端点（用户提供的默认地址，可用环境变量覆盖）
OPENVERSE_API_BASE = "https://api.openverse.org"

#: 音效按来源 freesound 搜（59 万条音效；Openverse 总库 521 万+，音乐类多在 Jamendo）
DEFAULT_SOURCE = "freesound"

#: 免 Key 限频：每分钟 20 次、每天 200 次。内置节流按分钟窗口保守执行（19 次留余量）。
RATE_LIMIT_PER_MINUTE = 19
RATE_LIMIT_PER_DAY = 200

#: 语义类别 → Openverse 搜索词（英文检索；whoosh/impact/click 是 ACTION_SFX_MAP 的全部值）
CATEGORY_QUERY = {
    "whoosh": "whoosh transition",
    "impact": "impact hit",
    "click": "click ui",
}

#: SFX 动作类别 → 本地库 tags（入库后走同一 resolve 链，必须对齐 audio.py 的类别词）
CATEGORY_TAGS = {
    "whoosh": ["transition", "whoosh", "soft"],
    "impact": ["emphasis", "impact", "hit"],
    "click": ["text", "click", "ui"],
}

_last_request_ts: List[float] = []  # 滑动窗口限频（进程内）
_daily_count = {"day": "", "count": 0}


class OpenverseError(RuntimeError):
    pass


def _throttle() -> None:
    """滑动窗口限频（免 Key 配额是共享资源，超了会 429 影响后续所有搜索）。"""
    now = time.time()
    while _last_request_ts and now - _last_request_ts[0] > 60.0:
        _last_request_ts.pop(0)
    if len(_last_request_ts) >= RATE_LIMIT_PER_MINUTE:
        raise OpenverseError("Openverse 免 Key 限频（每分钟 20 次），本次跳过")
    today = time.strftime("%Y-%m-%d")
    if _daily_count["day"] != today:
        _daily_count.update(day=today, count=0)
    if _daily_count["count"] >= RATE_LIMIT_PER_DAY:
        raise OpenverseError("Openverse 免 Key 日配额（200 次）已用尽，明天再搜")


def _record_request() -> None:
    _last_request_ts.append(time.time())
    today = time.strftime("%Y-%m-%d")
    if _daily_count["day"] != today:
        _daily_count.update(day=today, count=0)
    _daily_count["count"] += 1


def search_sfx(query: str, *, source: str = DEFAULT_SOURCE,
                license_type: str = "cc0", page_size: int = 8,
                base_url: str = "") -> List[dict]:
    """搜索 CC0 音效，返回按相关度排序的候选（不下载）。

    每条：{id, title, url, duration_sec, attribution, creator, license, tags}
    license_type 固定 cc0（图省事只挑 CC0，免署名负担）；传空串则不过滤授权。
    """
    _throttle()
    base = (base_url or OPENVERSE_API_BASE).rstrip("/")
    params = {
        "q": query,
        "source": source,
        "page_size": str(page_size),
    }
    if license_type:
        params["license"] = license_type
    url = f"{base}/v1/audio/?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "video-director/1.0"})
    try:
        _record_request()
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise OpenverseError(f"Openverse 搜索失败 HTTP {exc.code}: {exc.read().decode()[:160]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OpenverseError(f"Openverse 网络不可达: {exc}") from exc

    out: List[dict] = []
    for r in data.get("results") or []:
        # duration 是毫秒；SFX 太长（>30s）通常是环境录音不是音效
        dur_ms = r.get("duration")
        try:
            dur_sec = float(dur_ms) / 1000.0 if dur_ms is not None else None
        except (TypeError, ValueError):
            dur_sec = None
        dl = r.get("url") or ""
        if not dl:
            continue
        out.append({
            "id": str(r.get("id") or ""),
            "title": str(r.get("title") or ""),
            "url": dl,
            "duration_sec": dur_sec,
            "attribution": str(r.get("attribution") or ""),
            "creator": str(r.get("creator") or ""),
            "license": str(r.get("license") or "cc0"),
            "license_version": str(r.get("license_version") or ""),
            "tags": [str(t.get("name")) for t in (r.get("tags") or []) if isinstance(t, dict) and t.get("name")],
        })
    return out


def _openverse_dir() -> Path:
    """Openverse 音效分区（与 bgm/ sfx/ 平级，manifest 同契约）。"""
    from video_director.audio import library_root
    d = library_root() / "sfx-openverse"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_entries() -> List[dict]:
    mf = _openverse_dir() / "audio_manifest.json"
    if not mf.is_file():
        return []
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


_manifest_lock = None  # 延迟创建（import 期不可用 threading 时兜底）


def _get_manifest_lock():
    """manifest 读-改-写进程级互斥（对抗审查 MEDIUM 5：ThreadingHTTPServer
    + workflow 线程并发入库时，全文覆盖会丢另一线程的条目）。"""
    global _manifest_lock
    import threading
    if _manifest_lock is None:
        _manifest_lock = threading.Lock()
    return _manifest_lock


def _append_manifest(entry: dict) -> None:
    with _get_manifest_lock():
        entries = _manifest_entries()
        if any(e.get("asset_id") == entry["asset_id"] for e in entries):
            return
        entries.append(entry)
        # 临时文件 + rename：崩溃/并发不会留下半个 JSON
        mf = _openverse_dir() / "audio_manifest.json"
        tmp = mf.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(mf)


def ensure_sfx_for_category(category: str, base_url: str = "") -> Optional[str]:
    """确保本地库有某类别的 Openverse 音效：搜 → 下载 → 入 manifest。

    返回新入库的 asset_id；不可用/无结果/限频 → None（调用方诚实降级）。
    category ∈ {whoosh, impact, click}（ACTION_SFX_MAP 的语义类别）。
    """
    query = CATEGORY_QUERY.get(category)
    if not query:
        return None
    try:
        candidates = search_sfx(query, base_url=base_url)
    except OpenverseError as exc:
        logger.warning(f"openverse: 类别 {category} 搜索跳过: {exc}")
        return None
    # 音效宜短：优先 0.2–15s 的候选；再按列表序（相关度）
    usable = [c for c in candidates
              if c["duration_sec"] is None or 0.2 <= c["duration_sec"] <= 15.0]
    for cand in (usable or candidates)[:3]:
        asset_id = download_sfx(cand, category, base_url=base_url)
        if asset_id:
            return asset_id
    if not candidates:
        logger.warning(f"openverse: 类别 {category} 无 CC0 结果（query={query!r}）")
    return None


def download_sfx(candidate: dict, category: str, base_url: str = "") -> Optional[str]:
    """下载单条音效入 audio_library/sfx-openverse/ 并登记 manifest。"""
    dl = candidate.get("url") or ""
    if not dl:
        return None
    ov_id = (candidate.get("id") or "").replace("-", "")[:12] or "ov"
    asset_id = f"sfx-openverse-{category}-{ov_id}"
    dest_dir = _openverse_dir()
    ext = ".mp3"
    dest = dest_dir / f"{asset_id}{ext}"
    if dest.is_file():
        return asset_id  # 幂等：已下载过直接复用
    try:
        req = urllib.request.Request(dl, headers={"User-Agent": "video-director/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # 修 cand→candidate NameError：下载失败分支本就会崩，异常路径不可用
        logger.warning(f"openverse: 音效下载失败 {candidate.get('title')!r}: {exc}")
        return None
    if not payload:
        return None
    dest.write_bytes(payload)
    attribution = (candidate.get("attribution") or "").strip()
    # manifest 的 file 相对 manifest 所在目录（scan_library: mf.parent / e["file"]）
    _append_manifest({
        "asset_id": asset_id,
        "file": f"{asset_id}{ext}",
        "type": "sfx",
        "tags": CATEGORY_TAGS.get(category, [category]),
        "license": {
            "source": "openverse:freesound",
            "commercial_use": True,
            "attribution_required": False,
            "license": "cc0",
            "attribution": attribution,
            "creator": candidate.get("creator") or "",
            "openverse_id": candidate.get("id") or "",
        },
    })
    logger.info(f"openverse: 音效入库 {asset_id}（{candidate.get('title')!r}，cc0）")
    return asset_id


def categories_missing_in_library(library: Dict) -> List[str]:
    """本地库缺哪些 SFX 语义类别（whoosh/impact/click）。"""
    have = {str(k).lower() for a in library.values()
            if getattr(a, "type", "") == "sfx" for k in (a.keywords or [])}
    return [c for c, tags in CATEGORY_TAGS.items() if not any(t in have for t in tags)]
