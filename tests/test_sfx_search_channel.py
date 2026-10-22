"""差距 2a：generation.sfx 走 Openverse 搜索链（诚实边界——不是 AI 合成）。"""
from video_director import openverse_sfx, tool_executor


def test_sfx_search_requires_prompt():
    r = tool_executor._execute_sfx_search("p1", {})
    assert r["ok"] is False
    assert r["status"] == "INVALID_PAYLOAD"


def test_sfx_search_known_category_uses_ensure_chain(monkeypatch):
    monkeypatch.setattr(openverse_sfx, "ensure_sfx_for_category",
                        lambda category: "sfx-openverse-whoosh-abc")
    r = tool_executor._execute_sfx_search("p1", {"prompt": "whoosh 转场"})
    assert r["ok"] is True
    assert r["result"]["asset_id"] == "sfx-openverse-whoosh-abc"
    assert r["result"]["searched"] is True
    assert "非 AI 合成" in r["result"]["note"]


def test_sfx_search_rate_limited_is_honest(monkeypatch):
    def boom(category):
        raise openverse_sfx.OpenverseError("限频")
    monkeypatch.setattr(openverse_sfx, "ensure_sfx_for_category", boom)
    r = tool_executor._execute_sfx_search("p1", {"prompt": "impact hit"})
    assert r["ok"] is False
    assert r["status"] == "SFX_SEARCH_UNAVAILABLE"
    assert "限频" in (r["error"] or "")


def test_sfx_search_no_result_is_honest(monkeypatch):
    monkeypatch.setattr(openverse_sfx, "ensure_sfx_for_category", lambda c: None)
    r = tool_executor._execute_sfx_search("p1", {"prompt": "whoosh whoosh"})
    assert r["ok"] is False
    assert r["status"] == "NO_RESULT"
