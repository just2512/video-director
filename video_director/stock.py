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
from pathlib import Path

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


def _role_cfg_for_provider(provider: str) -> dict:
    try:
        from video_director import model_config as _mc
        return _mc.get_role_config(f"stock_{provider}")
    except Exception:
        return {}


def _api_key(provider: str) -> Optional[str]:
    """§25：设置中心优先，环境变量向后兼容。"""
    try:
        from video_director import model_config as _mc
        cfg = _mc.get_role_config(f"stock_{provider}")
        if cfg.get("api_key"):
            return cfg["api_key"]
    except Exception:
        pass
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

# ---------------------------------------------------------------------------
# §三十/§三十一 API 限流契约：normalize query → 24h 响应缓存
# Pixabay 官方要求 API 响应缓存 24 小时；Pexels 200/hour——重复查询走缓存，
# 不能「LLM 无限循环搜索」（方案§三十 原文 normalize query → cache → search）。
# ---------------------------------------------------------------------------
STOCK_CACHE_TTL_S = 24 * 3600  # §三十 官方要求的 24 小时


def _normalize_query(query: str) -> str:
    """normalize query：小写 + 压空白（「Coffee  Shop 」与「coffee shop」同缓存键）。"""
    return " ".join((query or "").lower().split())


def _stock_cache_path(provider: str, norm_query: str, orientation: Optional[str]) -> "Path":
    """缓存文件：storage/stock_cache/<provider>-<hash>.json（键=归一化查询+方向）。"""
    import hashlib as _hl
    from video_director import config as _config
    d = _config.composer_storage_root() / "stock_cache"
    d.mkdir(parents=True, exist_ok=True)
    key = _hl.sha1(f"{provider}:{norm_query}:{orientation or 'any'}".encode("utf-8")).hexdigest()[:16]
    return d / f"{provider}-{key}.json"


def _cache_get(path: "Path", ttl_s: int = STOCK_CACHE_TTL_S):
    """命中（存在且未过期）→ 返回数据；否则 None。损坏 JSON 视为未命中。"""
    import time as _t
    if not path.is_file():
        return None
    try:
        if _t.time() - path.stat().st_mtime > ttl_s:
            return None  # 过期（Pexels/Pixabay 政策 TTL）
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else None
    except (OSError, ValueError):
        return None


def _cache_put(path: "Path", results: List["StockAsset"]) -> None:
    """结果写缓存（StockAsset dataclass 序列化；失败不阻断搜索主链）。"""
    try:
        from dataclasses import asdict
        path.write_text(json.dumps([asdict(a) for a in results], ensure_ascii=False),
                        encoding="utf-8")
    except (OSError, ValueError, TypeError):
        pass


def search_stock(query: str, provider: Optional[str] = None,
                 orientation: Optional[str] = None, per_page: int = 10) -> List[StockAsset]:
    """§24 统一 Stock 搜索。provider=None → 遍历有 key 的 provider。

    §三十 限流契约：查询先归一化再查 24h 缓存，命中不进 provider（省配额）。
    全部缺 key → StockError(STOCK_UNAVAILABLE)。
    """
    norm = _normalize_query(query)
    providers = [provider] if provider else list(PROVIDER_POLICY.keys())
    results: List[StockAsset] = []
    last_error: Optional[StockError] = None

    def _record_provider_error(exc: "ProviderCapabilityError") -> None:  # noqa: F821
        nonlocal last_error
        status = ("STOCK_UNAVAILABLE"
                  if exc.state.status == provider_matrix.MISSING
                  else "CAPABILITY_UNSUPPORTED")
        # MISSING（有实现、缺 key）比 UNSUPPORTED（未接通）是更可行动的信号，
        # 汇总错误优先报 MISSING——用户配置 key 即可解，而不是被"能力不支持"误导。
        if last_error is None or (status == "STOCK_UNAVAILABLE"
                                  and last_error.code != "STOCK_UNAVAILABLE"):
            last_error = StockError(status, str(exc))
        logger.info(f"stock.{p}: {status}")

    for p in providers:
        # §三十 24h 缓存：命中直接用（零 API 消耗）。缓存命中不要求 provider
        # 就绪——命中路径不进 provider，矩阵门只拦真实外呼。
        cache_file = _stock_cache_path(p, norm, orientation)
        cached = _cache_get(cache_file)
        if cached is not None:
            results.extend(StockAsset(**c) for c in cached)
            logger.info(f"stock.{p}: 缓存命中（24h 内同查询不再消耗 API 配额）")
            continue
        try:
            from video_director import provider_matrix
            cfg = _role_cfg_for_provider(p)
            provider_matrix.require(
                f"stock_{p}", "stock.search_video", cfg
            )
        except provider_matrix.ProviderCapabilityError as exc:
            _record_provider_error(exc)
            if provider:
                raise last_error
            continue
        try:
            fresh: List[StockAsset] = []
            if p == "pexels":
                fresh = _pexels_videos(query, orientation, per_page)
            elif p == "pixabay":
                fresh = _pixabay_videos(query, orientation, per_page)
            elif p == "coverr":
                fresh = _coverr_videos(query, orientation, per_page)
            results.extend(fresh)
            _cache_put(cache_file, fresh)  # §三十 响应缓存 24h
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
# ---------------------------------------------------------------------------
# §MPT：文案 → 网络素材自动检索/下载
# ---------------------------------------------------------------------------

def _stock_orientation(target_ratio: str) -> str:
    ratio = _norm_ratio(target_ratio or "")
    if ratio == "16:9":
        return "landscape"
    if ratio == "1:1":
        return "square"
    return "portrait"

def build_scene_search_query(scene_text: str, llm_call=None) -> str:
    """把一个场景转成图库检索词；无 LLM 时直接回退场景文本。"""
    text = " ".join(str(scene_text or "").split()).strip()
    if not text:
        return ""
    if llm_call:
        prompt = (
            "把下面的视频旁白场景改写成图库检索词。只返回一条短语，"
            "优先描述可拍摄的主体、动作、环境，使用英文短语，"
            "不要解释、不要引号、不要编号、不要标点。\n"
            f"场景：{text[:500]}"
        )
        try:
            q = str(llm_call(prompt) or "").strip()
            q = q.strip("`\\\"\' .，。；;：:!?！？")
            if q:
                return " ".join(q.split())[:120]
        except Exception as exc:
            logger.info(f"stock query LLM 失败，回退场景文本：{exc}")
    return text[:120]

def auto_download_for_scenes(
    scene_texts: List[str],
    project_base: Path,
    target_ratio: str = "9:16",
    llm_call=None,
    per_scene: int = 1,
    providers: Optional[List[str]] = None,
    max_scenes: Optional[int] = None,
    scene_ids: Optional[List[str]] = None,
    search_terms_by_scene: Optional[List[List[str]]] = None,
) -> dict:
    """搜索并下载场景素材，支持 MPT 式按叙事顺序传入多个搜索词。"""
    providers = providers or ["pexels", "pixabay"]
    providers = [p for p in providers if p in PROVIDER_POLICY] or ["pexels", "pixabay"]
    orientation = _stock_orientation(target_ratio)
    files: List[str] = []
    metadata = {}
    warnings: List[str] = []
    queries = []
    used_sources = set()
    wanted = max(0, int(per_scene or 1))

    for idx, scene_text in enumerate(scene_texts or []):
        if max_scenes is not None and idx >= max_scenes:
            break
        scene_id = scene_ids[idx] if scene_ids and idx < len(scene_ids) else f"S{idx + 1:02d}"
        supplied = []
        if search_terms_by_scene and idx < len(search_terms_by_scene):
            supplied = _normalize_scene_terms(search_terms_by_scene[idx])
        scene_queries = supplied[:3] if supplied else [build_scene_search_query(scene_text, llm_call)]
        scene_queries = [q for q in scene_queries if q]
        if not scene_queries:
            warnings.append(f"{scene_id}：场景没有可用检索词")
            continue

        selected = 0
        for query in scene_queries:
            queries.append({"scene_id": scene_id, "query": query})
            hits: List[StockAsset] = []
            provider_errors = []
            for provider in providers:
                try:
                    hits.extend(search_stock(
                        query, provider=provider, orientation=orientation,
                        per_page=max(4, wanted * 4),
                    ))
                except StockError as exc:
                    provider_errors.append(f"{provider}:{exc.code}")

            if not hits and provider_errors:
                warnings.append(
                    f"{scene_id}：图库搜索失败（{'；'.join(provider_errors)}）"
                )
                continue

            for candidate in resolve_candidates(
                hits, target_ratio=target_ratio or "any", target_duration=None
            ):
                a = candidate.asset
                key = f"{a.provider}:{a.source_id}"
                if candidate.status != SUPPORTED or key in used_sources:
                    continue
                try:
                    out = download_asset(a, project_base)
                except StockError as exc:
                    warnings.append(f"{scene_id}：{a.provider} 下载失败（{exc.code}）")
                    continue
                used_sources.add(key)
                files.append(out["file"])
                metadata[out["file"]] = {
                    "source": "stock",
                    "license_note": f"{a.provider}·{a.source_page}",
                    "provenance": {
                        "provider": a.provider,
                        "sourceId": a.source_id,
                        "sourcePage": a.source_page,
                        "creator": a.creator,
                    },
                    "rights_status": a.commercial_status,
                    "scene_id": scene_id,
                    "query": query,
                    "query_order": len(queries),
                }
                selected += 1
                if selected >= wanted:
                    break
            if selected >= wanted:
                break

        if selected == 0:
            warnings.append(f"{scene_id}：没有满足画幅/授权条件的网络素材")

    return {
        "files": files,
        "metadata": metadata,
        "warnings": warnings,
        "queries": queries,
    }


def _normalize_scene_terms(raw: Any) -> List[str]:
    """Stock 层小型归一器，保持搜索词首次出现顺序并去重。"""
    if isinstance(raw, str):
        raw_values = re.split(r"[\n,，;；|]+", raw)
    elif isinstance(raw, (list, tuple)):
        raw_values = raw
    else:
        raw_values = []
    out = []
    seen = set()
    for value in raw_values:
        term = re.sub(r"^[-•\d.)、]+\s*", "", str(value or "").strip())
        term = re.sub(r"\s+", " ", term)
        key = term.casefold()
        if not term or key in seen:
            continue
        seen.add(key)
        out.append(term)
    return out
