"""素材语义链：Library 写入 → 路径回落 → 素材清单注入。

背景（2026-10-16 实测）：上传素材语义只登记在 Library（catalog.json），但
(1) 没有任何写入函数/端点，(2) build_manifest 不读它。结果 8 条上传素材
description/tags 全空 → LLM 匹配拿到空名册返回空 → 顺序兜底，又被写成
「语义匹配」。这里验证写入与回落两条链均已闭合。
"""
from pathlib import Path

from video_director import asset_catalog
from video_director import assets as assets_mod


def _make_catalog(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    asset_catalog.library_root().mkdir(parents=True, exist_ok=True)
    return storage


def _write_entry(file_path):
    return {
        "asset_id": "local-abc", "type": "video", "source": "user",
        "file": str(file_path), "tags": [], "description": "",
        "commercial_status": "USER_SUPPLIED", "license_note": "",
        "created_at": 1, "content_hash": "abc",
    }


def _seed(tmp_path, monkeypatch):
    _make_catalog(tmp_path, monkeypatch)
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"\x00" * 16)
    catalog = asset_catalog._load_catalog()
    catalog["local-abc"] = _write_entry(media)
    asset_catalog._save_catalog(catalog)
    return media


def test_find_by_path_and_update_semantics_roundtrip(tmp_path, monkeypatch):
    media = _seed(tmp_path, monkeypatch)

    assert asset_catalog.find_by_path(str(media))["asset_id"] == "local-abc"
    assert asset_catalog.find_by_path(str(tmp_path / "nope.mp4")) is None

    entry = asset_catalog.update_asset_semantics(
        "local-abc", description=" 咖啡特写 ", tags=[" 咖啡 ", "", " 手冲 "])
    assert entry["description"] == "咖啡特写"
    assert entry["tags"] == ["咖啡", "手冲"]
    # 只改语义，不动溯源字段
    assert entry["content_hash"] == "abc"
    assert entry["source"] == "user"
    assert asset_catalog.get_asset("local-abc")["description"] == "咖啡特写"


def test_update_semantics_rejects_missing_asset(tmp_path, monkeypatch):
    _make_catalog(tmp_path, monkeypatch)
    try:
        asset_catalog.update_asset_semantics("nope", description="x")
    except asset_catalog.AssetLibraryError as exc:
        assert exc.code == "ASSET_NOT_FOUND"
    else:
        raise AssertionError("应拒绝不存在的素材")


def test_library_semantics_feeds_asset_meta(tmp_path, monkeypatch):
    media = _seed(tmp_path, monkeypatch)
    catalog = asset_catalog._load_catalog()
    catalog["local-abc"]["description"] = "咖啡特写"
    catalog["local-abc"]["tags"] = ["咖啡"]
    asset_catalog._save_catalog(catalog)

    sem = assets_mod._library_semantics(str(media))
    assert sem["description"] == "咖啡特写"
    assert sem["keywords"] == ["咖啡"]

    meta = assets_mod._asset_meta(str(media), {})
    assert meta["description"] == "咖啡特写"
    assert meta["keywords"] == ["咖啡"]
    # 调用方显式 metadata 必须覆盖 Library（source 是可信上下文）
    meta2 = assets_mod._asset_meta(
        str(media), {str(media): {"source": "user", "description": "覆盖"}})
    assert meta2["source"] == "user"
    assert meta2["description"] == "覆盖"


def test_server_has_semantics_endpoint():
    src = (Path(__file__).resolve().parents[1] / "webui" / "server.py").read_text(encoding="utf-8")
    assert 'parsed.path == "/api/asset_semantics"' in src
    assert "update_asset_semantics" in src