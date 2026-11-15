"""Opt-in real-model test: stream a proposal, then verify one explicit approval and replay rejection.

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
    base_url = os.environ.get("VD_LLM_BASE_URL", "").strip() or None
    model = os.environ.get("VD_LLM_MODEL", "").strip() or None
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))

    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root
    # Pin this smoke test to the explicit env endpoint. Without this override,
    # model_config.json has higher priority and the test could silently hit another provider.
    # Restore both module singletons after this test so a live key/provider cannot leak
    # into later tests in the same pytest process.
    monkeypatch.setattr(web_server.vd_llm, "_runtime_config", web_server.vd_llm._runtime_config)
    monkeypatch.setattr(web_server.vd_llm, "_provider_cache", web_server.vd_llm._provider_cache)
    web_server.vd_llm.set_llm_config(api_key, base_url, model)

    project_id = "film-live-stream-" + uuid.uuid4().hex[:16]
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_bytes(FILM_HTML.encode("utf-8"))
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
            detail = detail.replace(api_key, "[redacted]")
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
        first_token_seconds = token_events[0][1]["received_at"] - started_at
        first_to_result_seconds = result_event["received_at"] - token_events[0][1]["received_at"]
        assert first_to_result_seconds >= 0.1, {
            "first_token_to_result_seconds": round(first_to_result_seconds, 4),
            "token_event_count": len(token_events),
            "hint": "The configured proxy may be buffering the upstream SSE stream.",
        }

        # Retain auditable metadata from the actual text/event-stream response.
        # Never print generated token contents or credentials.
        print(
            "LIVE_SSE_EVIDENCE=PASS "
            f"http_status={response.status} "
            f"content_type={response.headers.get_content_type()} "
            f"sse_event_count={len(events)} "
            f"token_chunk_count={len(token_events)} "
            f"first_token_seconds={first_token_seconds:.4f} "
            f"first_token_to_result_seconds={first_to_result_seconds:.4f} "
            f"event_sequence={','.join(item['event'] for item in events)}"
        )
        for chunk_index, (_, token_event) in enumerate(token_events, start=1):
            fragment = token_event["data"]["fragment"]
            elapsed = token_event["received_at"] - started_at
            print(
                f"LIVE_SSE_CHUNK index={chunk_index} "
                f"elapsed_seconds={elapsed:.4f} "
                f"fragment_chars={len(fragment)}"
            )

        proposal = result.get("proposal")
        assert isinstance(proposal, dict), {
            "result_keys": sorted(result.keys()),
            "message": result.get("message"),
            "hint": "The live test expects a validated proposal, not a no-op response.",
        }
        assert proposal.get("base_revision") == before["rev"], "proposal must use the exact requested base revision"
        operations = proposal.get("operations") or []
        assert len(operations) == 1, operations
        assert operations[0].get("op") == "move" and operations[0].get("clip") == "clip-b", operations
        assert abs(float(operations[0].get("at", -1)) - 8.5) < 0.001, operations
        after = fd.read_project_film(project_id)
        assert after["rev"] == before["rev"], "AI proposal generation must not mutate FilmDocument revision"
        assert film_path.read_bytes() == before_bytes, "AI proposal generation must not rewrite film.html"




        # The generated proposal is still read-only until the explicit approval endpoint.
        approval = {
            "project_id": project_id,
            "proposal_id": proposal["id"],
            "base_revision": before["rev"],
            # The browser can identify a proposal but cannot replace its registered operations.
            "ops": [{"op": "remove", "clip": "clip-a"}],
        }
        approval_request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/editor/film/apply-proposal",
            data=json.dumps(approval, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            approval_response = urllib.request.urlopen(approval_request, timeout=30)
        except urllib.error.HTTPError as error:
            detail = error.read(600).decode("utf-8", errors="replace").replace(api_key, "[redacted]")
            pytest.fail(
                f"explicit proposal approval returned HTTP {error.code}: {detail}; "
                "verify the proposal registry and exact base revision"
            )
        with approval_response:
            assert approval_response.status == 200
            applied_payload = json.loads(approval_response.read().decode("utf-8"))
        assert applied_payload.get("ok") is True, applied_payload
        assert applied_payload.get("base") == before["rev"], applied_payload
        assert applied_payload.get("source") == "ai_proposal", applied_payload
        assert applied_payload.get("applied_proposal") == proposal["id"], applied_payload
        assert applied_payload.get("rev") != before["rev"], applied_payload

        applied = fd.read_project_film(project_id)
        applied_bytes = film_path.read_bytes()
        assert applied["rev"] == applied_payload["rev"]
        applied_clips = {
            clip["id"]: clip
            for track in applied["doc"]["tracks"]
            for clip in track["clips"]
        }
        assert "clip-a" in applied_clips, "client-supplied ops must not replace the registered proposal"
        assert abs(float(applied_clips["clip-b"]["attrs"]["at"]) - 8.5) < 0.001

        try:
            urllib.request.urlopen(approval_request, timeout=30)
        except urllib.error.HTTPError as error:
            retry_status = error.code
            retry_body = json.loads(error.read().decode("utf-8"))
        else:
            pytest.fail("the same live-model proposal must be single-use")
        assert retry_status == 409, retry_body
        assert retry_body.get("code") == "PROPOSAL_NOT_FOUND_OR_STALE", retry_body
        after_retry = fd.read_project_film(project_id)
        assert after_retry["rev"] == applied["rev"]
        assert film_path.read_bytes() == applied_bytes, "repeated approval must not produce a second write"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
