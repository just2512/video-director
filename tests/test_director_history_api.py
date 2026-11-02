from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from video_director import config, director_history
from webui import server as web_server


def _request(base_url: str, path: str, *, method: str = "GET", payload: dict | None = None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(
        base_url + path,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method=method,
    )
    try:
        with urlopen(request, timeout=5) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as response:
        raw = response.read().decode("utf-8")
        return response.code, json.loads(raw) if raw else {}




def test_director_conversation_http_cursor_pagination_and_client_errors(tmp_path, monkeypatch):
    """The real HTTP route preserves cursor ordering and rejects malformed client cursors."""
    root = tmp_path / "composer"
    monkeypatch.setattr(config, "composer_storage_root", lambda: root)
    monkeypatch.setattr(director_history.time, "time", lambda: 1700000000.0)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_port}"
    try:
        for index in range(5):
            status, created = _request(
                base_url,
                "/api/director/conversations",
                method="POST",
                payload={
                    "conversation_id": f"conv-http-page-{index:02d}",
                    "project_id": "page-project" if index < 4 else "other-project",
                    "title": f"分页记录 {index}",
                    "snapshot": {},
                },
            )
            assert status == 200 and created["ok"] is True

        status, first = _request(base_url, "/api/director/conversations?project_id=page-project&limit=2")
        assert status == 200 and first["ok"] is True
        assert [row["conversation_id"] for row in first["conversations"]] == [
            "conv-http-page-03",
            "conv-http-page-02",
        ]
        assert first["has_more"] is True
        assert first["next_cursor"] == {
            "updated_at": 1700000000.0,
            "conversation_id": "conv-http-page-02",
        }

        cursor = first["next_cursor"]
        status, second = _request(
            base_url,
            "/api/director/conversations?project_id=page-project&limit=2"
            f"&before_updated_at={cursor['updated_at']}"
            f"&before_conversation_id={cursor['conversation_id']}",
        )
        assert status == 200 and second["ok"] is True
        assert [row["conversation_id"] for row in second["conversations"]] == [
            "conv-http-page-01",
            "conv-http-page-00",
        ]
        assert second["has_more"] is False
        assert second["next_cursor"] is None

        all_ids = [
            row["conversation_id"]
            for row in first["conversations"] + second["conversations"]
        ]
        assert len(all_ids) == len(set(all_ids)) == 4

        status, partial_cursor = _request(
            base_url,
            "/api/director/conversations?before_updated_at=1700000000",
        )
        assert status == 400
        assert partial_cursor["ok"] is False
        assert "同时包含" in partial_cursor["error"]

        status, invalid_timestamp = _request(
            base_url,
            "/api/director/conversations?before_updated_at=nan&before_conversation_id=conv-http-page-02",
        )
        assert status == 400
        assert invalid_timestamp["ok"] is False
        assert "无效" in invalid_timestamp["error"]

        status, invalid_limit = _request(
            base_url,
            "/api/director/conversations?limit=not-a-number",
        )
        assert status == 400
        assert invalid_limit["ok"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_director_draft_and_conversation_http_lifecycle(tmp_path, monkeypatch):
    """Exercise the actual Handler routes, not only the SQLite module."""
    root = tmp_path / "composer"
    monkeypatch.setattr(config, "composer_storage_root", lambda: root)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_port}"
    try:
        status, saved = _request(
            base_url,
            "/api/director/drafts/active",
            method="POST",
            payload={
                "data": {
                    "version": 1,
                    "fields": {"cf-director-brief": "制作一条软件介绍视频"},
                    "context": {"asset_refs": [{"asset_id": "asset-http-01"}]},
                }
            },
        )
        assert status == 200 and saved["ok"] is True
        assert saved["revision"] == 1

        status, draft = _request(base_url, "/api/director/drafts/active")
        assert status == 200 and draft["ok"] is True
        assert draft["draft"]["data"]["fields"]["cf-director-brief"] == "制作一条软件介绍视频"
        assert draft["draft"]["data"]["context"]["asset_refs"][0]["asset_id"] == "asset-http-01"

        status, created = _request(
            base_url,
            "/api/director/conversations",
            method="POST",
            payload={
                "conversation_id": "conv-http-01",
                "title": "软件介绍视频",
                "initial_message": "制作一条软件介绍视频",
                "snapshot": {"form": {"fields": {"cf-style": "documentary-calm"}}},
            },
        )
        assert status == 200 and created["ok"] is True
        assert created["conversation_id"] == "conv-http-01"

        status, listing = _request(base_url, "/api/director/conversations?limit=50")
        assert status == 200 and listing["ok"] is True
        assert [x["conversation_id"] for x in listing["conversations"]] == ["conv-http-01"]

        status, linked = _request(
            base_url,
            "/api/director/conversations/conv-http-01/link",
            method="POST",
            payload={"project_id": "project-http-01", "workflow_run_id": "run-http-01"},
        )
        assert status == 200 and linked["ok"] is True

        status, appended = _request(
            base_url,
            "/api/director/conversations/conv-http-01/messages",
            method="POST",
            payload={"role": "assistant", "content": "文案已经生成。", "metadata": {"source": "test"}},
        )
        assert status == 200 and appended["ok"] is True

        status, detail = _request(base_url, "/api/director/conversations/conv-http-01")
        assert status == 200 and detail["ok"] is True
        conversation = detail["conversation"]
        assert conversation["project_id"] == "project-http-01"
        assert conversation["workflow_run_id"] == "run-http-01"
        assert conversation["snapshot"]["form"]["fields"]["cf-style"] == "documentary-calm"
        assert [m["role"] for m in conversation["messages"]] == ["user", "assistant"]
        assert conversation["messages"][-1]["content"] == "文案已经生成。"

        status, cleared = _request(
            base_url,
            "/api/director/drafts/active/clear",
            method="POST",
            payload={},
        )
        assert status == 200 and cleared["ok"] is True
        status, missing = _request(base_url, "/api/director/drafts/active")
        assert status == 200 and missing["draft"] is None
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
