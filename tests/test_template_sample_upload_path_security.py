"""Adversarial path traversal tests for the template sample-render upload route."""
from __future__ import annotations

import base64
import importlib
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
    "lYkAAAAASUVORK5CYII="
)


def _multipart(boundary: str, fields: dict[str, str], files: list[tuple[str, str, bytes]]) -> bytes:
    out: list[bytes] = []
    for name, value in fields.items():
        out.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode("utf-8"),
            b"\r\n",
        ])
    for name, filename, payload in files:
        out.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: image/png\r\n\r\n",
            payload,
            b"\r\n",
        ])
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out)


def test_template_sample_upload_rejects_path_traversal_file_field(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))

    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    boundary = "VdSampleSecurityBoundary"
    payload = _multipart(
        boundary,
        {
            "template_id": "tpl.minimal-editorial",
            "template_version": "1.0.0",
            "slot_values_json": json.dumps({"title": "Path test", "body_text": "Should not render"}),
        },
        [
            ("hero_image_file", "hero.png", _PNG),
            # This untrusted multipart field name previously became part of Path(...).
            # With the vulnerable path join it resolves to template-samples/outside-evil.png.
            ("../../outside_file", "evil.png", _PNG),
        ],
    )
    outside = storage / "template-samples" / "outside-evil.png"
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/api/templates/sample_render",
            data=payload,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=15) as response:
                status, raw = response.status, response.read()
        except HTTPError as exc:
            status, raw = exc.code, exc.read()

        body = json.loads(raw)
        assert status == 400, body
        assert body.get("code") == "INVALID_UPLOAD_SLOT", body
        assert not outside.exists(), f"untrusted multipart field wrote outside sample inputs: {outside}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
