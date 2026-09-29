"""MPT 吸收方案 §20-§27 素材系统：Stock Provider 三件套 + 商业安全矩阵 + AssetCandidate。

§21 Asset Resolver 返回的不只是"视频链接"，而是带来源/尺寸/语义/商业状态的
完整 AssetCandidate；§23 四态零评分（SUPPORTED/MISSING_REQUIREMENT/
INCOMPATIBLE/UNKNOWN，不存相似度分数）；§25 商业安全：
    - Pexels   → CLEAR（须存 source page/creator/provider + provider license valid）
    - Pixabay  → CLEAR（但禁止 standalone 素材导出）
    - Coverr   → 默认 REVIEW/UNKNOWN（API 文档对商业使用表述不一致，未确认前不放行）
缺 API key → 诚实 STOCK_UNAVAILABLE（禁伪造结果）。

HTTP 用标准库 urllib（不引入第三方依赖），key 从环境变量读取。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger


class StockError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


#: §24 统一 Asset Model：Stock 三种都进入同一 Asset Model（provider 字段区分）
#: §25 商业安全矩阵：{provider: {缺key状态, 有key状态, 备注}}
PROVIDER_POLICY = {
    "pexels": {
        "label": "Pexels",
        "default_status": "CLEAR",      # §25：照片视频免费商用，但存来源+展示标识
        "requires_attribution": True,
        "standalone_export_allowed": True,
        "key_env": "PEXELS_API_KEY",
    },
    "pixabay": {
        "label": "Pixabay",
        "default_status": "CLEAR",      # §25：可商用修改，但禁 standalone 分发
        "requires_attribution": False,
        "standalone_export_allowed": False,  # §25：禁止基本不变 standalone 出售/分发
        "key_env": "PIXABAY_API_KEY",
    },
    "coverr": {
        "label": "Coverr",
        "default_status": "REVIEW",     # §25：API 文档商业表述不一致，默认 REVIEW
        "requires_attribution": True,
        "standalone_export_allowed": False,
        "key_env": "COVERR_API_KEY",
    },
}

#: §23 四态（零评分）
SUPPORTED = "SUPPORTED"
MISSING_REQUIREMENT = "MISSING_REQUIREMENT"
INCOMPATIBLE = "INCOMPATIBLE"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StockAsset:
    """§21：Asset Resolver 返回的完整 Stock 资产（非"视频链接"）。

    字段名对齐方案 JSON：assetId / semanticTags / visualDescription /
    commercialStatus + sourceId / sourcePage / width / height / duration。
    """

    provider: str
    source_id: str
    source_page: str
    media_url: str
    width: int
    height: int
    duration: float
    creator: str = ""
    description: str = ""
    source: str = "stock"          # §10 AssetSource：来源大类（stock），provider 是具体方
    asset_id: str = ""             # §21 assetId（provider:source_id 稳定 id）
    semantic_tags: tuple = ()      # §21 semanticTags（检索词+描述词）
    commercial_status: str = UNKNOWN  # §25 由 provider policy 定

    def __post_init__(self):
        if not self.asset_id:
            # §21 assetId：provider+sourceId 构成的稳定资产 id
            object.__setattr__(self, "asset_id", f"{self.provider}:{self.source_id}")
        if not self.semantic_tags:
            # §21 semanticTags：从描述派生的语义标签（无 LLM 时的确定性降级）
            words = [w for w in str(self.description or "").split() if w]
            object.__setattr__(self, "semantic_tags", tuple(words[:8]))


@dataclass(frozen=True)
class AssetCandidate:
    """§23：带语义/兼容性/溯源信息的候选，四态零评分。

    字段对齐 §23：asset / semantic match / visual description /
    duration compatibility / aspect compatibility / continuity /
    source uniqueness / commercial status。
    """

    asset: StockAsset
    status: str                    # SUPPORTED | MISSING_REQUIREMENT | INCOMPATIBLE | UNKNOWN
    visual_description: str = ""
    duration_compatible: bool = True
    aspect_compatible: bool = True
    continuity: str = "unknown"    # 语义顺序衔接（beat id 或 query）
    source_unique: bool = True
    missing_requirements: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        a = self.asset
        return {
            "assetId": a.asset_id,          # §21
            "source": a.source,             # §10 来源大类（stock）
            "provider": a.provider,
            "sourceId": a.source_id,
            "sourcePage": a.source_page,
            "mediaUrl": a.media_url,
            "width": a.width,
            "height": a.height,
            "duration": a.duration,
            "creator": a.creator,
            "semanticTags": list(a.semantic_tags),   # §21
            "visualDescription": self.visual_description or a.description,  # §21
            "commercialStatus": a.commercial_status,
            "status": self.status,
            "durationCompatible": self.duration_compatible,
            "aspectCompatible": self.aspect_compatible,
            "sourceUnique": self.source_unique,
            "missingRequirements": self.missing_requirements,
        }


def _api_key(provider: str) -> Optional[str]:
    """§25：key 从环境变量读。无 key → None（调用方诚实报 STOCK_UNAVAILABLE）。"""
    env = PROVIDER_POLICY[provider]["key_env"]
    return os.environ.get(env) or None


# ---------------------------------------------------------------------------
# 三 Provider 搜索（真实 HTTP；有 key 才调，无 key 诚实报错）
# ---------------------------------------------------------------------------

def _http_json(url: str, headers: Optional[dict] = None, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise StockError("STOCK_HTTP_ERROR", f"HTTP {exc.code}: {url}") from exc
    except urllib.error.URLError as exc:
        raise StockError("STOCK_NETWORK_ERROR", f"网络错误: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise StockError("STOCK_PARSE_ERROR", "响应非 JSON") from exc


def _pexels_videos(query: str, orientation: str, per_page: int) -> List[StockAsset]:
    key = _api_key("pexels")
    if not key:
        raise StockError("STOCK_UNAVAILABLE", "Pexels 未配置 API key（PEXELS_API_KEY）")
    params = urllib.parse.urlencode({
        "query": query, "per_page": per_page,
        "orientation": orientation or "portrait",
    })
    data = _http_json(f"https://api.pexels.com/videos/search?{params}",
                      headers={"Authorization": key})
    out = []
    for v in data.get("videos", []):
        # 取最高清 rendition（§20：rendition 记录）
        files = v.get("video_files") or []
        best = sorted([f for f in files if f.get("link")],
                      key=lambda f: (f.get("width") or 0) * (f.get("height") or 0),
                      reverse=True)
        if not best:
            continue
        b = best[0]
        out.append(StockAsset(
            provider="pexels", source_id=str(v.get("id", "")),
            source_page=v.get("url", ""),
            media_url=b["link"],
            width=b.get("width") or 0, height=b.get("height") or 0,
            duration=float(v.get("duration") or 0),
            creator=(v.get("user") or {}).get("name", ""),
            description=(v.get("alt") or v.get("url", ""))[:200],
            commercial_status="CLEAR",  # §25
        ))
    return out


def _pixabay_videos(query: str, orientation: str, per_page: int) -> List[StockAsset]:
    key = _api_key("pixabay")
    if not key:
        raise StockError("STOCK_UNAVAILABLE", "Pixabay 未配置 API key（PIXABAY_API_KEY）")
    params = urllib.parse.urlencode({
        "key": key, "q": query, "per_page": per_page,
        "video_type": "film" if orientation != "portrait" else "all",
    })
    data = _http_json(f"https://pixabay.com/api/videos/?{params}")
    out = []
    for v in data.get("hits", []):
        vids = v.get("videos") or {}
        best = sorted((vids.get(k) or {} for k in ("large", "medium", "small", "tiny")
                       if vids.get(k)),
                      key=lambda f: (f.get("width") or 0) * (f.get("height") or 0),
                      reverse=True)
        if not best:
            continue
        b = best[0]
        out.append(StockAsset(
            provider="pixabay", source_id=str(v.get("id", "")),
            source_page=v.get("pageURL", ""),
            media_url=b.get("url", ""),
            width=b.get("width") or 0, height=b.get("height") or 0,
            duration=float(v.get("duration") or 0),
            creator=v.get("user", ""),
            description=(v.get("tags") or "")[:200],
            commercial_status="CLEAR",  # §25（但禁 standalone 导出）
        ))
    return out


def _coverr_videos(query: str, orientation: str, per_page: int) -> List[StockAsset]:
    # §25：Coverr API 文档对商业使用与 attribution 表述不一致 → 默认 REVIEW，不自动进入商用
    # 无稳定公开 API 契约可依赖 → 诚实 UNKNOWN 而非伪造 CLEAR
    key = _api_key("coverr")
    if not key:
        raise StockError("STOCK_UNAVAILABLE", "Coverr 未配置 API key（COVERR_API_KEY）")
    raise StockError("STOCK_UNAVAILABLE",
                     "Coverr 商业条款未确认（§25），暂不开放自动搜索；素材标记 REVIEW/UNKNOWN")


# ---------------------------------------------------------------------------
# 统一搜索入口（§24：用户只看到"素材"，不关心是哪个 provider）
# ---------------------------------------------------------------------------

def search_stock(query: str, provider: Optional[str] = None,
                 orientation: Optional[str] = None, per_page: int = 10) -> List[StockAsset]:
    """§24 统一 Stock 搜索。provider=None → 遍历有 key 的 provider。

    全部缺 key → StockError(STOCK_UNAVAILABLE)。
    """
    providers = [provider] if provider else list(PROVIDER_POLICY.keys())
    results: List[StockAsset] = []
    last_error = None
    for p in providers:
        try:
            if p == "pexels":
                results.extend(_pexels_videos(query, orientation, per_page))
            elif p == "pixabay":
                results.extend(_pixabay_videos(query, orientation, per_page))
            elif p == "coverr":
                results.extend(_coverr_videos(query, orientation, per_page))
        except StockError as exc:
            last_error = exc
            logger.info(f"stock.{p}: {exc.code}")
            if provider:  # 显式指定单 provider 失败 → 抛出
                raise
            continue
    if not results and last_error:
        raise StockError(last_error.code, "所有 Stock provider 均未配置 key 或搜索失败")
    return results


# ---------------------------------------------------------------------------
# §22/§23：Asset Resolver（语义匹配 → Candidate，四态零评分）
# ---------------------------------------------------------------------------

def resolve_candidates(assets: List[StockAsset], target_ratio: Optional[str] = "9:16",
                       target_duration: Optional[float] = None) -> List[AssetCandidate]:
    """§22/§23：StockAsset → AssetCandidate（语义/时长/画幅兼容性判定）。

    四态判定（零评分，不存 similarity）：
        - 画幅不匹配 → INCOMPATIBLE
        - 时长不足 → MISSING_REQUIREMENT
        - 商业状态 REVIEW/UNKNOWN → 候选保留但 status=UNKNOWN（§26 不自动进商用）
        - 其余 → SUPPORTED
    """
    cands: List[AssetCandidate] = []
    for a in assets:
        status = SUPPORTED
        missing: List[str] = []
        # 画幅兼容（§22 aspect compatibility）
        r = f"{a.width}:{a.height}" if a.height else ""
        ratio_norm = _norm_ratio(target_ratio or "")
        source_norm = _norm_ratio_of(a.width, a.height)
        aspect_ok = (not ratio_norm) or (source_norm == ratio_norm) or (source_norm == "unknown")
        if not aspect_ok:
            status = INCOMPATIBLE
            missing.append(f"aspect {source_norm} != {ratio_norm}")
        # 时长兼容（§22 duration compatibility）
        dur_ok = True
        if target_duration and a.duration and a.duration < target_duration:
            dur_ok = False
            status = MISSING_REQUIREMENT if status == SUPPORTED else status
            missing.append(f"duration {a.duration}s < {target_duration}s")
        # 商业状态（§26：REVIEW/UNKNOWN 不自动进商用发布）
        final_status = status
        if a.commercial_status in ("REVIEW", "UNKNOWN"):
            final_status = UNKNOWN
        cands.append(AssetCandidate(
            asset=a, status=final_status,
            visual_description=a.description,
            duration_compatible=dur_ok, aspect_compatible=aspect_ok,
            source_unique=True, missing_requirements=missing,
        ))
    return cands


def _norm_ratio(ratio: str) -> str:
    """16:9 / 9:16 / 1:1 归一。"""
    if ratio in ("16:9", "9:16", "1:1"):
        return ratio
    if ratio in ("1920x1080", "1080x1920", "1080x1080"):
        w, h = ratio.split("x")
        return _norm_ratio_of(int(w), int(h))
    return ""


def _norm_ratio_of(w: int, h: int) -> str:
    if not w or not h:
        return "unknown"
    r = w / h
    if abs(r - 16 / 9) < 0.1:
        return "16:9"
    if abs(r - 9 / 16) < 0.1:
        return "9:16"
    if abs(r - 1.0) < 0.1:
        return "1:1"
    return "other"


def _guess_ext(url: str) -> str:
    """从 media_url 猜扩展名（图片/视频），缺省按视频兜底。"""
    path = urllib.parse.urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        return ext
    if ext in (".mp4", ".mov", ".webm", ".mkv"):
        return ext
    return ".mp4" if "video" in url.lower() else ".jpg"


def download_asset(asset: StockAsset, project_base) -> dict:
    """§24 联网素材下载到项目输入目录（打通「搜索 → 下载采用 → 进时间线」）。

    返回 {asset_id, file, type, width, height, duration, provider, source_page,
    creator, commercial_status}；失败抛 StockError(DOWNLOAD_FAILED)。
    """
    ext = _guess_ext(asset.media_url)
    kind = "image" if ext in (".jpg", ".jpeg", ".png", ".webp") else "video"
    out_dir = project_base / "input" / ("images" if kind == "image" else "videos")
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-]", "_", str(asset.source_id or "")) or "asset"
    dst = out_dir / f"stock-{asset.provider}-{safe}{ext}"
    try:
        req = urllib.request.Request(asset.media_url, headers={"User-Agent": "Mozilla/5.0"})
        dst.write_bytes(urllib.request.urlopen(req, timeout=60).read())
        if dst.stat().st_size == 0:
            dst.unlink(missing_ok=True)
            raise StockError("DOWNLOAD_FAILED", "下载内容为空")
    except StockError:
        raise
    except Exception as exc:
        dst.unlink(missing_ok=True)
        raise StockError("DOWNLOAD_FAILED", f"下载失败: {exc}") from exc
    try:
        from video_director import prober
        probe = prober.probe_media(str(dst))
        width, height, duration = probe.width, probe.height, probe.duration
    except Exception:
        width, height, duration = None, None, None
    return {
        "asset_id": asset.asset_id, "file": str(dst), "type": kind,
        "width": width or asset.width, "height": height or asset.height,
        "duration": duration or asset.duration,
        "provider": asset.provider, "source_page": asset.source_page,
        "creator": asset.creator, "commercial_status": asset.commercial_status,
    }