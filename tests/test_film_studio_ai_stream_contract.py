"""Deterministic HTTP/SSE contract tests for Film AI streaming without a live provider."""
from __future__ import annotations

import importlib
import json
import threading
import time
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

from video_director import film_document as fd


FILM_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>body{margin:0}</style></head>
<body>
<section id="video-main" data-label="主画面">
  <video id="clip-a" src="../assets/a.mp4#t=0,8" at="0"></video>
  <video id="clip-b" src="../assets/b.mp4#t=0,8" at="8"></video>
</section>
</body></html>"""


def test_streaming_http_route_flushes_model_tokens_before_read_only_proposal(
    tmp_path: Path, monkeypatch
) -> None:
    """Exercise the production command route with a deterministic planner, never a paid API."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))

    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root
    monkeypatch.setattr(web_server.vd_llm, "get_llm", lambda: object())

    project_id = "film-mock-stream-" + uuid.uuid4().hex[:12]
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(FILM_HTML, encoding="utf-8")
    original_bytes = film_path.read_bytes()
    before = fd.read_project_film(project_id)
    assert before["exists"] and before["rev"]

    fragments = ["将", " clip-b", " 移动到 8.5 秒"]
    def deterministic_planner(
        _llm, state, _message, _selected_clip_id, *,
        summary_out=None, selected_element=None, on_token=None,
        on_stream_open=None, cancel_event=None,
    ):
        assert selected_element is None
        for fragment in fragments:
            if cancel_event is not None and cancel_event.is_set():
                raise ConnectionAbortedError("cancelled by test")
            assert on_token is not None
            on_token(fragment)
            # Make buffering regressions observable without a live model or credentials.
            time.sleep(0.12)
        if summary_out is not None:
            summary_out["summary"] = "将 clip-b 移动到 8.5 秒"
        return [{"op": "move", "clip": "clip-b", "at": 8.5}]

    monkeypatch.setattr(fd, "plan_film_command", deterministic_planner)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    events: list[dict] = []
    try:
        payload = {
            "project_id": project_id,
            "base": before["rev"],
            "message": "提出一个只移动 clip-b 的候选编辑方案。",
            "stream": True,
            "request_id": "mock-stream-" + uuid.uuid4().hex,
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/editor/film/command",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
            assert response.headers.get_content_type() == "text/event-stream"
            current_event: str | None = None
            data_lines: list[str] = []

            def flush_event() -> None:
                nonlocal current_event, data_lines
                if current_event:
                    events.append({
                        "event": current_event,
                        "data": json.loads("\n".join(data_lines)) if data_lines else {},
                        "received_at": time.monotonic(),
                    })
                current_event, data_lines = None, []

            while True:
                line = response.readline()
                if not line:
                    break
                if line in (b"\n", b"\r\n"):
                    flush_event()
                elif line.startswith(b"event:"):
                    current_event = line[len(b"event:"):].decode("utf-8").strip()
                elif line.startswith(b"data:"):
                    data_lines.append(line[len(b"data:"):].decode("utf-8").strip())
            flush_event()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    result_indexes = [i for i, item in enumerate(events) if item["event"] == "result"]
    token_events = [
        (i, item) for i, item in enumerate(events[:result_indexes[-1] if result_indexes else len(events)])
        if item["event"] == "token" and item["data"].get("fragment", "").strip()
    ]
    assert result_indexes, {"events": [(e["event"], e["data"]) for e in events]}
    assert len(token_events) == len(fragments), {
        "events": [(e["event"], e["data"]) for e in events]
    }
    result_index = result_indexes[-1]
    assert all(index < result_index for index, _ in token_events)
    assert token_events[0][1]["received_at"] < token_events[-1][1]["received_at"] < events[result_index]["received_at"]
    assert events[result_index]["received_at"] - token_events[0][1]["received_at"] >= 0.2

    result = events[result_index]["data"]
    proposal = result.get("proposal")
    assert result.get("ok") is True and isinstance(proposal, dict), result
    assert proposal.get("base_revision") == before["rev"]
    operations = proposal.get("operations") or []
    assert len(operations) == 1
    assert operations[0].get("op") == "move"
    assert operations[0].get("clip") == "clip-b"
    assert abs(float(operations[0].get("at", -1)) - 8.5) < 0.001

    # Streaming is feedback only; the candidate must remain unapplied until approval.
    after = fd.read_project_film(project_id)
    assert after["rev"] == before["rev"]
    assert film_path.read_bytes() == original_bytes
