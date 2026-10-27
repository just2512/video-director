"""Deterministic HTTP/SSE contract tests for Film AI streaming without a live provider."""
from __future__ import annotations

import importlib
import json
import threading
import urllib.error
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
    """Exercise the production planner and HTTP/SSE route without a paid model API."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))

    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root
    # Patched below with a deterministic streaming fake; no provider is contacted.

    project_id = "film-mock-stream-" + uuid.uuid4().hex[:12]
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(FILM_HTML, encoding="utf-8")
    original_bytes = film_path.read_bytes()
    before = fd.read_project_film(project_id)
    assert before["exists"] and before["rev"]

    # Exercise the real plan_film_command() and HTTP/SSE route. Only the model
    # client is faked, so this verifies production token-delta forwarding and JSON
    # plan validation without credentials, network access, or model-call costs.
    output_fragments = [
        '{"summary":"',
        '将 clip-b ',
        '移动到 8.5 秒',
        '","operations":[',
        '{"op":"move","track_id":"video-main",',
        '"clip":"clip-b",',
        '"at":',
        '8.5',
        '}]}',
    ]
    expected_model_output = "".join(output_fragments)

    class DeterministicStreamingLLM:
        def generate_stream(self, _prompt, *, on_delta=None):
            assert callable(on_delta)
            for fragment in output_fragments:
                on_delta(fragment)
                # Make buffering regressions observable without a live model.
                time.sleep(0.06)
            return expected_model_output

    llm = DeterministicStreamingLLM()
    monkeypatch.setattr(web_server.vd_llm, "get_llm", lambda: llm)
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
                    # SSE servers may keep an HTTP/1.1 connection reusable after
                    # the terminal event. A client must not wait for EOF to observe it.
                    if events and events[-1]["event"] == "result":
                        break
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
    assert len(token_events) == len(output_fragments), {
        "events": [(e["event"], e["data"]) for e in events]
    }
    assert [item["data"].get("fragment") for _, item in token_events] == output_fragments, {
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
    assert operations[0].get("track_id") == "video-main"
    assert abs(float(operations[0].get("at", -1)) - 8.5) < 0.001

    # Streaming is feedback only; the candidate must remain unapplied until approval.
    after = fd.read_project_film(project_id)
    assert after["rev"] == before["rev"]
    assert film_path.read_bytes() == original_bytes


def test_stream_cancel_stops_cancellable_provider_without_creating_proposal(
    tmp_path: Path, monkeypatch
) -> None:
    """A cancel request must stop upstream generation before a proposal can be stored."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))

    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-cancel-stream-" + uuid.uuid4().hex[:12]
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(FILM_HTML, encoding="utf-8")
    original_bytes = film_path.read_bytes()
    before = fd.read_project_film(project_id)
    assert before["exists"] and before["rev"]

    valid_output = json.dumps({
        "summary": "移动 clip-b",
        "operations": [{"op": "move", "clip": "clip-b", "track_id": "video-main", "at": 8.5}],
    }, ensure_ascii=False)
    provider_entered = threading.Event()
    provider_closed = threading.Event()

    class ClosableProviderStream:
        def close(self):
            provider_closed.set()

    class CancellableStreamingLLM:
        supports_stream_cancellation = True

        def generate_stream(self, _prompt, *, on_delta=None, on_stream_open=None, cancel_event=None):
            assert callable(on_delta) and callable(on_stream_open) and cancel_event is not None
            upstream = ClosableProviderStream()
            on_stream_open(upstream)
            on_delta('{"summary":"')
            provider_entered.set()
            # Model generation must remain active until the real cancel endpoint signals it.
            assert cancel_event.wait(timeout=5), "server did not propagate cancellation upstream"
            return valid_output

    llm = CancellableStreamingLLM()
    monkeypatch.setattr(web_server.vd_llm, "get_llm", lambda: llm)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request_id = "cancel-stream-" + uuid.uuid4().hex
    events: list[tuple[str, dict]] = []

    def read_sse_event(response):
        event_name = None
        data_lines = []
        while True:
            line = response.readline()
            if not line:
                if event_name:
                    return event_name, json.loads("\n".join(data_lines)) if data_lines else {}
                return None
            if line in (b"\n", b"\r\n"):
                if event_name:
                    return event_name, json.loads("\n".join(data_lines)) if data_lines else {}
                event_name, data_lines = None, []
            elif line.startswith(b"event:"):
                event_name = line[len(b"event:"):].decode("utf-8").strip()
            elif line.startswith(b"data:"):
                data_lines.append(line[len(b"data:"):].decode("utf-8").strip())

    try:
        payload = {
            "project_id": project_id,
            "base": before["rev"],
            "message": "提出一个只移动 clip-b 的候选编辑方案。",
            "stream": True,
            "request_id": request_id,
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/editor/film/command",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
            assert response.headers.get_content_type() == "text/event-stream"
            first_event = read_sse_event(response)
            assert first_event and first_event[0] == "token", first_event
            events.append(first_event)
            assert provider_entered.wait(timeout=3), "provider never entered generation"

            cancel_request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/editor/film/command/cancel",
                data=json.dumps({
                    "project_id": project_id, "request_id": request_id,
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(cancel_request, timeout=5) as cancelled:
                cancel_result = json.loads(cancelled.read().decode("utf-8"))
            assert cancel_result == {
                "ok": True, "cancel_requested": True, "stream_found": True,
            }, cancel_result
            next_event = read_sse_event(response)
            while next_event:
                events.append(next_event)
                next_event = read_sse_event(response)

        assert provider_closed.wait(timeout=3), "server did not close the upstream stream"
        assert not any(name in {"result", "error"} for name, _ in events), events
        assert ("project_id", request_id) not in web_server._FILM_AI_ACTIVE_STREAMS
        with web_server._FILM_PROPOSALS._lock:
            assert not any(
                item["project_id"] == project_id
                for item in web_server._FILM_PROPOSALS._items.values()
            ), "a cancelled stream must not create a proposal"
        after = fd.read_project_film(project_id)
        assert after["rev"] == before["rev"]
        assert film_path.read_bytes() == original_bytes
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_stream_cancel_before_registration_blocks_provider_start(
    tmp_path: Path, monkeypatch
) -> None:
    """A cancel racing ahead of stream registration must be consumed before model work."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))

    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-cancel-race-" + uuid.uuid4().hex[:12]
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(FILM_HTML, encoding="utf-8")
    original_bytes = film_path.read_bytes()
    before = fd.read_project_film(project_id)
    request_id = "cancel-before-register-" + uuid.uuid4().hex

    class NeverStartedLLM:
        def generate_stream(self, *_args, **_kwargs):
            raise AssertionError("cancelled request must not start model generation")

        def generate(self, *_args, **_kwargs):
            raise AssertionError("cancelled request must not fall back to non-streaming generation")

    monkeypatch.setattr(web_server.vd_llm, "get_llm", lambda: NeverStartedLLM())
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cancel_request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/editor/film/command/cancel",
            data=json.dumps({
                "project_id": project_id, "request_id": request_id,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(cancel_request, timeout=5) as response:
            cancel_result = json.loads(response.read().decode("utf-8"))
        assert cancel_result == {
            "ok": True, "cancel_requested": True, "stream_found": False,
        }, cancel_result

        command_request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/editor/film/command",
            data=json.dumps({
                "project_id": project_id,
                "base": before["rev"],
                "message": "只移动 clip-b 到 8.5 秒。",
                "stream": True,
                "request_id": request_id,
            }, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        try:
            urllib.request.urlopen(command_request, timeout=5)
        except urllib.error.HTTPError as error:
            body = json.loads(error.read().decode("utf-8"))
            assert error.code == 409, (error.code, body)
            assert body.get("code") == "AI_REQUEST_CANCELLED", body
        else:
            raise AssertionError("a pre-cancelled AI request must return HTTP 409")

        key = (project_id, request_id)
        assert key not in web_server._FILM_AI_ACTIVE_STREAMS
        assert key not in web_server._FILM_AI_CANCELLED_REQUESTS
        after = fd.read_project_film(project_id)
        assert after["rev"] == before["rev"]
        assert film_path.read_bytes() == original_bytes
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
