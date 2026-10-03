"""最终方案 §三十/§三十一 Stock API 限流契约测试。

normalize query → 24h 响应缓存（Pixabay 官方要求；Pexels 200/h 配额保护）。
"""

from pathlib import Path

import pytest

from video_director import config


@pytest.fixture()
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path / "composer")
    yield


class TestStockCacheContract:
    def test_normalize_query(self, _iso):
        from video_director import stock
        assert stock._normalize_query("  Coffee   SHOP ") == "coffee shop"
        assert stock._normalize_query("") == ""
        assert stock._normalize_query("单字") == "单字"

    def test_cache_roundtrip_and_ttl(self, _iso):
        """写 → 命中 → 24h 过期未命中（官方 TTL 政策）。"""
        import os
        import time
        from video_director import stock
        from video_director.stock import StockAsset
        cf = stock._stock_cache_path("pixabay", "office work", "portrait")
        assert stock._cache_get(cf) is None, "未写缓存前不命中"
        a = StockAsset(provider="pixabay", source_id="9", source_page="", media_url="http://x",
                       width=1080, height=1920, duration=4.0, creator="", description="")
        stock._cache_put(cf, [a])
        got = stock._cache_get(cf)
        assert got and got[0]["provider"] == "pixabay"
        old = time.time() - stock.STOCK_CACHE_TTL_S - 10
        os.utime(cf, (old, old))
        assert stock._cache_get(cf) is None, "超过 24h 过期不再命中"

    def test_search_hits_cache_zero_api(self, _iso):
        """§三十：同（归一化）查询 24h 内命中缓存 → 不打 provider API。"""
        from video_director import stock
        from video_director.stock import StockAsset
        cf = stock._stock_cache_path("pexels", "coffee shop", "portrait")
        a = StockAsset(provider="pexels", source_id="1", source_page="", media_url="http://x",
                       width=1080, height=1920, duration=5.0, creator="", description="")
        stock._cache_put(cf, [a])
        # 大小写/多空白变体 → 同一缓存键 → 命中（无 PEXELS_API_KEY 也不抛错）
        hits = stock.search_stock("Coffee  SHOP", provider="pexels", orientation="portrait")
        assert hits and hits[0].provider == "pexels"

    def test_corrupt_cache_treated_as_miss(self, _iso):
        """损坏缓存视为未命中（不崩、走 provider 正常链）。"""
        from video_director import stock
        cf = stock._stock_cache_path("coverr", "bad", None)
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text("{not json", encoding="utf-8")
        assert stock._cache_get(cf) is None
