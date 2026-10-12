from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from video_director import article_import
from webui import server as web_server


def _post(base_url: str, data: bytes):
    request = Request(
        base_url + "/api/source/article",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as response:
        return response.code, json.loads(response.read().decode("utf-8"))


def test_article_source_api_validates_request_and_returns_import_snapshot(monkeypatch):
    original_import = article_import.import_article
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_port}"
    try:
        # Unsafe schemes are rejected by the real importer before any outbound connection.
        status, payload = _post(base_url, json.dumps({"url": "file:///etc/passwd"}).encode())
        assert status == 422
        assert payload["ok"] is False
        assert payload["code"] == "ARTICLE_IMPORT_REJECTED"

        # Verify the route preserves the result of the importer as one source object.
        expected = {
            "source_url": "https://news.example/article",
            "title": "Fixture headline",
            "content": "An imported article body with enough text for a source snapshot.",
            "content_sha256": "a" * 64,
            "content_chars": 62,
            "content_type": "text/html",
            "fetched_at": "2026-10-11T12:00:00+00:00",
            "extraction_status": "EXTRACTED",
        }
        monkeypatch.setattr(article_import, "import_article", lambda url: {**expected, "requested_url": url})
        status, payload = _post(base_url, json.dumps({"url": "https://news.example/article"}).encode())
        assert status == 200
        assert payload["ok"] is True
        assert payload["article"]["title"] == "Fixture headline"
        assert payload["article"]["source_url"] == "https://news.example/article"
        assert payload["article"]["content"] == expected["content"]
        assert payload["article"]["requested_url"] == "https://news.example/article"

        status, payload = _post(base_url, json.dumps({"url": "https://news.example/article"}).encode() + b" " * 9000)
        assert status == 413
        assert payload["code"] == "INVALID_REQUEST_SIZE"
    finally:
        monkeypatch.setattr(article_import, "import_article", original_import)
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
