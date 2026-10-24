"""Opt-in end-to-end smoke test for real configured Film AI token streaming.

Set VD_LLM_LIVE_STREAM_TEST=1 to run this against the OpenAI-compatible endpoint
configured by VD_LLM_API_KEY, VD_LLM_BASE_URL, and VD_LLM_MODEL. The default CI
path skips this test so normal regressions never spend money or depend on a live provider.
"""
from __future__ import annotations

import importlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from video_director import film_document as fd


pytestmark = pytest.mark.skipif(
    os.environ.get("VD_LLM_LIVE_STREAM_TEST") != "1",
    reason="set VD_LLM_LIVE_STREAM_TEST=1 to call the configured live model",
)

FILM_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>body{margin:0}</style></head>
<body>
<section id="video-main" data-label="主画面">
  <video id="clip-a" src="../assets/a.mp4#t=0,8" at="0"></video>
  <video id="clip-b" src="../assets/b.mp4#t=0,8" at="8"></video>
</section>
</body></html>"""


def test_configured_film_ai_stream_delivers_tokens_before_result_without_writing_film(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api_key = os.environ.get("VD_LLM_API_KEY", "").strip()
    assert api_key, (
        "VD_LLM_LIVE_STREAM_TEST=1 requires VD_LLM_API_KEY; "
        "the key is not printed or included in test diagnostics"
    )
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))

    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-live-stream-" + uuid.uuid4().hex[:16]
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(FILM_HTML, encoding="utf-8")
    before_bytes = film_path.read_bytes()
    before = fd.read_project_film(project_id)
    assert before["exists"] and before["rev"], "live-stream fixture must parse as FilmDocument"

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    events: list[dict] = []
    started_at = time.monotonic()
    request_id = "live-stream-" + uuid.uuid4().hex
    payload = {
        "project_id": project_id,
        "base": before["rev"],
        "message": (
            "请提出一个待确认的编辑方案：将现有片段 clip-b 的时间线入点移动到 8.5 秒。"
            "只针对该 ID 生成一个 move 操作，不要修改或删除其他片段，也不要直接写入正式文档。"
        ),
        "stream": True,
        "request_id": request_id,
    }
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/editor/film/command",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    try:
        try:
            response = urllib.request.urlopen(request, timeout=240)
        except urllib.error.HTTPError as error:
            detail = error.read(600).decode("utf-8", errors="replace")
            pytest.fail(
                f"live Film AI streaming endpoint returned HTTP {error.code}: {detail}; "
                "verify the configured provider and proxy without printing credentials"
            )

        with response:
            assert response.status == 200
            assert response.headers.get_content_type() == "text/event-stream", response.headers.get("Content-Type")
            current_event: str | None = None
            data_lines: list[str] = []

            def flush_event() -> None:
                nonlocal current_event, data_lines
                if current_event:
                    data = json.loads("\n".join(data_lines)) if data_lines else {}
                    events.append({
                        "event": current_event,
                        "data": data,
                        "received_at": time.monotonic(),
                    })
                current_event = None
                data_lines = []

            while True:
                line = response.readline()
                if not line:
                    break
                if line in (b"\n", b"\r\n"):
                    flush_event()
                    continue
                if line.startswith(b"event:"):
                    current_event = line[len(b"event:"):].decode("utf-8", errors="replace").strip()
                elif line.startswith(b"data:"):
                    data_lines.append(line[len(b"data:"):].decode("utf-8", errors="strict").strip())
            flush_event()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    errors = [item for item in events if item["event"] == "error"]
    assert not errors, {
        "error_code": errors[-1]["data"].get("code") if errors else None,
        "event_names": [item["event"] for item in events],
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
    }
    result_indexes = [index for index, item in enumerate(events) if item["event"] == "result"]
    assert result_indexes, {
        "event_names": [item["event"] for item in events],
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
    }
    result_index = result_indexes[-1]
    result_event = events[result_index]
    result = result_event["data"]
    assert result.get("ok") is True, {
        "result_keys": sorted(result.keys()),
        "event_names": [item["event"] for item in events],
    }

    token_events = [
        (index, item) for index, item in enumerate(events[:result_index])
        if item["event"] == "token"
        and isinstance(item["data"].get("fragment"), str)
        and item["data"]["fragment"].strip()
    ]
    assert len(token_events) >= 2, {
        "nonempty_token_event_count": len(token_events),
        "event_names": [item["event"] for item in events],
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
        "hint": "If a proposal is returned only at the end, inspect buffering in VD_LLM_BASE_URL's proxy.",
    }
    assert token_events[0][0] < result_index and token_events[-1][0] < result_index
    # A fully buffered response delivers all token fragments back-to-back only after
    # model completion. Require a visible interval between the first fragment and result.
    first_to_result_seconds = result_event["received_at"] - token_events[0][1]["received_at"]
    assert first_to_result_seconds >= 0.1, {
        "first_token_to_result_seconds": round(first_to_result_seconds, 4),
        "token_event_count": len(token_events),
        "hint": "The configured proxy may be buffering the upstream SSE stream.",
    }

    proposal = result.get("proposal")
    if isinstance(proposal, dict):
        assert proposal.get("base_revision") == before["rev"], "proposal must use the exact requested base revision"
    after = fd.read_project_film(project_id)
    assert after["rev"] == before["rev"], "AI proposal generation must not mutate FilmDocument revision"
    assert film_path.read_bytes() == before_bytes, "AI proposal generation must not rewrite film.html"


