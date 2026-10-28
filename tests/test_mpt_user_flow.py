# -*- coding: utf-8 -*-
"""《用户操作路径.md》用户路径断言测试（PR #1 MPT 收口链）。

指纹级对照方案：
- §五 script_only 不再绕过 stock —— compose_auto visual_source 四态语义
- §八 Stock API Key 从设置中心读取（环境变量回退保留）
- §A 主题→AI 文案：无 LLM 诚实报错，不静默
- §十 auto_download_for_scenes 只采用 SUPPORTED 候选
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from video_director import model_config as mc
from video_director import stock as vd_stock


# ---------------------------------------------------------------------------
# §八 设置中心图库 Key：_api_key 先读 model_config，环境变量兜底
# ---------------------------------------------------------------------------

def test_stock_api_key_prefers_model_config(monkeypatch, tmp_path):
    monkeypatch.setattr(mc, "_config_path", lambda: tmp_path / "mc.json")
    monkeypatch.setattr(mc, "_cache", {})
    monkeypatch.setenv("PEXELS_API_KEY", "env-key")
    assert vd_stock._api_key("pexels") == "env-key"
    mc.save_all({"stock_pexels": {"api_key": "settings-key", "base_url": "", "model": ""}})
    assert vd_stock._api_key("pexels") == "settings-key"


def test_stock_roles_exist_in_settings():
    """设置中心必须有 Pexels/Pixabay/Coverr 三角色（方案 §十六/§八）。"""
    for role in ("stock_pexels", "stock_pixabay", "stock_coverr"):
        assert role in mc.ROLE_DEFS
        assert mc.ROLE_DEFS[role].get("kind") == "stock"
    roles = mc.status_for_frontend()
    stock = [r for r in roles if r.get("kind") == "stock"]
    assert {r["role"] for r in stock} == {"stock_pexels", "stock_pixabay", "stock_coverr"}


def test_coverr_not_in_auto_chain(monkeypatch):
    """Coverr 只进配置体系；自动商用链 providers 默认 pexels/pixabay（版权 REVIEW 语义保持）。"""
    seen = []

    def fake_search(query, provider=None, orientation=None, per_page=None):
        raise vd_stock.StockError("STOCK_UNAVAILABLE", "no key")

    monkeypatch.setattr(vd_stock, "search_stock",
                        lambda q, **kw: (seen.append(kw.get("provider")),
                                         fake_search(q, **kw))[1] or [])
    vd_stock.auto_download_for_scenes(["城市夜景"], Path("."))
    assert "coverr" not in seen


# ---------------------------------------------------------------------------
# §五 auto_download_for_scenes：场景 → 检索词 → 只下载 SUPPORTED
# ---------------------------------------------------------------------------

def test_build_scene_search_query_falls_back_to_scene_text():
    q = vd_stock.build_scene_search_query("上海 深夜的便利店 灯光通明", llm_call=None)
    assert q == "上海 深夜的便利店 灯光通明"


def test_build_scene_search_query_uses_llm(monkeypatch):
    def fake_llm(prompt):
        return "`shanghai convenience store night`"
    q = vd_stock.build_scene_search_query("上海深夜便利店", llm_call=fake_llm)
    assert q == "shanghai convenience store night"


def test_auto_download_for_scenes_no_key_honest_warning(tmp_path, monkeypatch):
    """无 Key：search_stock 抛 STOCK_UNAVAILABLE → 收进 warnings，不静默。"""
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    monkeypatch.setattr(mc, "_config_path", lambda: tmp_path / "mc.json")
    monkeypatch.setattr(mc, "_cache", {})
    result = vd_stock.auto_download_for_scenes(["城市夜景航拍"], tmp_path)
    assert result["files"] == []
    assert any("图库搜索失败" in w for w in result["warnings"])


def test_auto_download_for_scenes_downloads_supported(tmp_path, monkeypatch):
    """只采用 SUPPORTED 候选；metadata 带 provenance/rights（§23 零评分）。"""

    class _Asset:
        provider = "pexels"
        source_id = "v1"
        source_page = "https://pexels.com/v1"
        creator = "a"
        duration = 5.0
        width = 1080
        height = 1920
        kind = "video"
        commercial_status = "allowed"

    def fake_search(query, provider=None, orientation=None, per_page=None):
        return [
            vd_stock.StockAsset(
                provider="pexels", source_id="v1", source_page="https://pexels.com/v1",
                creator="a", duration=5.0, width=1080, height=1920,
                media_url="https://example.com/v1.mp4", commercial_status="CLEAR"),
        ]

    def fake_download(asset, project_base):
        p = Path(project_base) / f"{asset.provider}_{asset.source_id}.mp4"
        p.write_bytes(b"fake")
        return {"file": str(p), "duration": asset.duration, "provider": asset.provider,
                "source_page": asset.source_page, "creator": asset.creator,
                "commercial_status": asset.commercial_status}

    monkeypatch.setattr(vd_stock, "search_stock", fake_search)
    monkeypatch.setattr(vd_stock, "download_asset", fake_download)
    result = vd_stock.auto_download_for_scenes(["夜景航拍"], tmp_path)
    assert len(result["files"]) == 1
    meta = result["metadata"][result["files"][0]]
    assert meta["provenance"]["provider"] == "pexels"
    assert meta["rights_status"] == "CLEAR"
    assert meta["scene_id"] == "S01"
    assert result["queries"][0]["scene_id"] == "S01"


# ---------------------------------------------------------------------------
# §A 主题→AI 文案：无 LLM 时明确报错（不静默成功）
# ---------------------------------------------------------------------------

def test_generate_script_requires_llm(monkeypatch, tmp_path):
    from video_director import llm_provider as lp
    monkeypatch.setattr(mc, "_config_path", lambda: tmp_path / "mc.json")
    monkeypatch.setattr(mc, "_cache", {})
    # 进程级状态必须一并清零，否则全量序列中前序测试的真实配置/运行时
    # 残留会越过本测试的"未配置"契约，直接对残留端点发起真实调用
    # （2026-10-28 CI 实证：set_llm_config 残留 → 越过断言 → 429）。
    monkeypatch.setattr(lp, "_provider_cache", None)
    monkeypatch.setattr(lp, "_runtime_config", None)
    for env in ("VD_LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    with pytest.raises(RuntimeError, match="未配置文本大模型"):
        lp.generate_script("为什么越忙越没钱")


def test_generate_script_empty_topic():
    from video_director import llm_provider as lp
    with pytest.raises(ValueError):
        lp.generate_script("  ")


# ---------------------------------------------------------------------------
# §46 任务抽屉列表层：失败原因必须在列表可见（Visible→Understandable）
# ---------------------------------------------------------------------------

def test_list_runs_includes_error(tmp_path, monkeypatch):
    """《用户操作路径.md》§18：列表层能看到失败原因，不能只显示「查看原因」占位。"""
    from video_director import workflow_run as wr
    monkeypatch.setattr(wr, "_db_path", lambda: __import__("pathlib").Path(tmp_path / "wf.db"))
    rid = wr.start_run("compose", spec={"script": "x"})
    wr.finish_run(rid, "FAILED", error="scene S01: 素材不足")
    runs = wr.list_runs(limit=5)
    hit = [r for r in runs if r["run_id"] == rid]
    assert hit and hit[0].get("error") == "scene S01: 素材不足"
