"""MPT 吸收方案 §5 Capability Registry 的 loader：清单加载与缓存。

方案 §5 目录结构（manifest/loader/resolver/validator/schemas）里的
loader 层：负责把 manifest 加载进只读单例、按 id/category 定位、返回
Level-1 摘要与 Level-2 详情。与 manifest.py 的区别——manifest 管"数据
定义"，loader 管"加载生命周期 + 查询接口"。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from video_director.capability_registry.manifest import manifest as _manifest_fn
from video_director.capability_registry.schemas import Capability

#: loader 缓存（进程内，清单是只读事实源，无需刷新机制）
_cache: Optional[Dict[str, Capability]] = None


def load_manifest() -> Dict[str, Capability]:
    """惰性加载完整清单（id → Capability）。"""
    global _cache
    if _cache is None:
        _cache = _manifest_fn()
    return _cache


def get_capability(cap_id: str) -> Optional[Capability]:
    """单条能力（None = 不存在，调用方诚实报 UNKNOWN_CAPABILITY）。"""
    return load_manifest().get(cap_id)


def list_ids() -> List[str]:
    """§63 Level-1 摘要：id 列表（AI 上下文避免塞满 schema）。"""
    return sorted(load_manifest().keys())


def list_by_category(category: str) -> List[Capability]:
    """按能力组（ai/edit/asset/audio/subtitle/music/generation/render/revision/qc）。"""
    return [c for c in load_manifest().values() if c.category == category]


def describe(cap_id: str) -> Optional[dict]:
    """§63 Level-2：单条完整 Contract（序列化为 §5 示例同构 JSON）。"""
    c = get_capability(cap_id)
    return c.to_dict() if c else None


def summary_by_category() -> Dict[str, int]:
    """各能力组计数（前端能力面板 / context 用）。"""
    out: Dict[str, int] = {}
    for c in load_manifest().values():
        out[c.category] = out.get(c.category, 0) + 1
    return out