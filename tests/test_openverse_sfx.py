"""Openverse 音效自动搜索链：搜索 → 下载 → manifest 入库 → scan_library 可见。

实测契约（2026-10-16 对 api.openverse.org 亲测）：
- GET /v1/audio/?q=whoosh&source=freesound&license=cc0 → 240 条结果
- license=cce 是非法值（400 "License 'cce' does not exist"）——正确值 cc0
- duration 单位毫秒；url 是 freesound CDN 直链 mp3（免 Key 可直接下载）
- attribution 现成署名字符串必须随 manifest 落盘
"""
import json
from pathlib import Path

import pytest

from video_director import audio, openverse_sfx


def test_search_builds_real_url_and_parses_results(monkeypatch):
    """URL 含 source=freesound + license=cc0；duration 毫秒→秒。"""
    captured = {}

    class _Resp:
        def __init__(self, body):
            self._body = body
        def read(self):
            return json.dumps(self._body).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _Resp({"results": [{
            "id": "eab8a6e2-0ac8", "title": "Deep Whoosh #1",
            "url": "https://cdn.freesound.org/previews/351/x.mp3",
            "duration": 3155,  # 毫秒
            "attribution": '"Deep Whoosh #1" by Kinoton is marked with CC0 1.0.',
            "creator": "Kinoton", "license": "cc0", "license_version": "1.0",
            "tags": [{"name": "air"}, {"name": "sfx"}],
        }]})

    monkeypatch.setattr(openverse_sfx.urllib.request, "urlopen", fake_urlopen)
    out = openverse_sfx.search_sfx("whoosh")

    assert "source=freesound" in captured["url"]
    assert "license=cc0" in captured["url"]
    assert out[0]["duration_sec"] == pytest.approx(3.155)
    assert out[0]["attribution"].startswith('"Deep Whoosh #1"')


def test_download_registers_manifest_and_scan_library_sees_it(tmp_path, monkeypatch):
    """下载 → manifest（署名落盘）→ scan_library 立即可见（同契约融合）。"""
    monkeypatch.setattr(audio, "library_root", lambda: tmp_path)

    ov_dir = tmp_path / "sfx-openverse"
    ov_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(openverse_sfx, "_openverse_dir", lambda: ov_dir)

    class _Resp:
        def read(self):
            return b"ID3\x03" + b"\x00" * 64
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(openverse_sfx.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp())
    cand = {"id": "eab8a6e2", "title": "Deep Whoosh #1",
            "url": "https://cdn.freesound.org/x.mp3", "duration_sec": 3.2,
            "attribution": '"Deep Whoosh #1" by Kinoton CC0 1.0', "creator": "Kinoton"}

    aid = openverse_sfx.download_sfx(cand, "whoosh")
    assert aid == "sfx-openverse-whoosh-eab8a6e2"
    assert (ov_dir / f"{aid}.mp3").is_file()

    mf = tmp_path / "sfx-openverse" / "audio_manifest.json"
    entry = json.loads(mf.read_text(encoding="utf-8"))[0]
    assert entry["asset_id"] == aid
    assert entry["type"] == "sfx"
    assert entry["license"]["license"] == "cc0"
    assert "Kinoton" in entry["license"]["attribution"]  # 署名必须落盘

    # scan_library 立即可见（manifest 同契约）
    lib = audio.scan_library()
    assert aid in lib
    assert lib[aid].type == "sfx"


def test_download_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "library_root", lambda: tmp_path)
    ov_dir = tmp_path / "sfx-openverse"
    ov_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(openverse_sfx, "_openverse_dir", lambda: ov_dir)

    calls = {"n": 0}

    class _Resp:
        def read(self):
            return b"ID3x"
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(openverse_sfx.urllib.request, "urlopen", fake)
    cand = {"id": "abc", "url": "https://x/y.mp3", "attribution": "", "creator": ""}
    a1 = openverse_sfx.download_sfx(cand, "whoosh")
    a2 = openverse_sfx.download_sfx(cand, "whoosh")
    assert a1 == a2
    assert calls["n"] == 1  # 第二次直接复用，不重复下载


def test_rate_limit_throttles_after_19(monkeypatch):
    monkeypatch.setattr(openverse_sfx, "_last_request_ts", [])
    monkeypatch.setattr(openverse_sfx, "_daily_count", {"day": "", "count": 0})
    for _ in range(openverse_sfx.RATE_LIMIT_PER_MINUTE):
        openverse_sfx._record_request()
    with pytest.raises(openverse_sfx.OpenverseError, match="限频"):
        openverse_sfx._throttle()


def test_resolve_action_sfx_falls_back_to_openverse(tmp_path, monkeypatch):
    """本地库缺 whoosh → Openverse 搜配 → 重扫 → 命中（整链行为级）。"""
    monkeypatch.setattr(audio, "library_root", lambda: tmp_path)
    ov_dir = tmp_path / "sfx-openverse"
    ov_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(openverse_sfx, "_openverse_dir", lambda: ov_dir)

    def fake_ensure(category, base_url=""):
        assert category == "whoosh"
        monkeypatch.setattr(openverse_sfx.urllib.request, "urlopen",
                            lambda req, timeout=None: _BytesResp())
        openverse_sfx.download_sfx(
            {"id": "ov1", "url": "https://cdn/x.mp3",
             "attribution": '"X" by Y is marked with CC0 1.0.', "creator": "Y"},
            "whoosh")
        return True

    class _BytesResp:
        def read(self):
            return b"ID3fake-mp3-bytes"
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(openverse_sfx, "ensure_sfx_for_category", fake_ensure)

    # 本地库空的：resolve 必然 None → 走 Openverse 兜底 → 命中
    aid = audio.resolve_action_sfx(
        {"at": 3.0, "action_id": "transition"}, audio.scan_library(),
        auto_search_openverse=True)
    assert aid and aid.startswith("sfx-openverse-whoosh-")

    # 开关关闭（默认）：本地未命中就诚实 None，不碰网络
    assert audio.resolve_action_sfx(
        {"at": 3.0, "action_id": "transition"}, {}) is None


def test_workflow_run_normalizes_openverse_auto_choice():
    """'openverse-auto' 必须翻译成 auto + 开搜索，绝不能当 asset_id 铺底。"""
    src = (Path(__file__).resolve().parents[1] / "video_director"
           / "workflow_run.py").read_text(encoding="utf-8")
    assert 'sfx_choice == "openverse-auto"' in src
    assert "'openverse-auto'" in (Path(__file__).resolve().parents[1]
                                  / "webui" / "static" / "js" / "create.js").read_text(encoding="utf-8")
