from __future__ import annotations

import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("bytes=0-0", (0, 0)),
        ("bytes=2-5", (2, 5)),
        ("bytes=7-", (7, 9)),
        ("bytes=-3", (7, 9)),
        ("bytes=8-99", (8, 9)),
    ],
)
def test_parse_byte_range_valid_single_ranges(header, expected):
    from video_director.http_byte_range import parse_byte_range

    assert parse_byte_range(header, 10) == expected


@pytest.mark.parametrize("header", [None, "", "items=0-2", "bytes=bad", "bytes=-", "bytes=0-1,4-5"])
def test_parse_byte_range_ignores_absent_unknown_malformed_and_multiple_ranges(header):
    from video_director.http_byte_range import parse_byte_range

    assert parse_byte_range(header, 10) is None


@pytest.mark.parametrize("header", ["bytes=10-", "bytes=9-4", "bytes=-0"])
def test_parse_byte_range_rejects_valid_but_unsatisfiable_ranges(header):
    from video_director.http_byte_range import UnsatisfiableByteRange, parse_byte_range

    with pytest.raises(UnsatisfiableByteRange):
        parse_byte_range(header, 10)


def test_empty_representation_has_zero_length_and_cannot_satisfy_a_range():
    from video_director.http_byte_range import UnsatisfiableByteRange, parse_byte_range

    assert parse_byte_range(None, 0) is None
    with pytest.raises(UnsatisfiableByteRange):
        parse_byte_range("bytes=0-", 0)


def test_render_media_server_returns_byte_exact_ranges_and_falls_back_to_full_body(tmp_path, monkeypatch):
    from video_director import config, film_render

    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "media-range-contract"
    project = config.project_dir(project_id)
    config.init_project_dirs(project)
    payload = b"0123456789"
    media = project / "assets" / "clip.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(payload)
    empty_media = project / "assets" / "empty.mp4"
    empty_media.write_bytes(b"")

    server = film_render._RenderHTTPServer(("127.0.0.1", 0), project_id)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/media?project_id={project_id}&src=assets%2Fclip.mp4"

    def request(headers=None):
        return urlopen(Request(base, headers=headers or {}), timeout=5)

    try:
        with request({"Range": "bytes=2-5"}) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 2-5/10"
            assert response.headers["Content-Length"] == "4"
            assert response.read() == b"2345"

        with request({"Range": "bytes=-3"}) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 7-9/10"
            assert response.read() == b"789"

        with request({"Range": "bytes=8-99"}) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 8-9/10"
            assert response.read() == b"89"

        empty_url = base.replace("clip.mp4", "empty.mp4")
        with urlopen(empty_url, timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Length"] == "0"
            assert response.read() == b""

        # The implementation intentionally does not emit multipart/byteranges.
        # Unsupported multi-ranges are ignored and returned as a complete body.
        for header in ("bytes=0-1,4-5", "bytes=bad"):
            with request({"Range": header}) as response:
                assert response.status == 200
                assert response.read() == payload

        # Without a validator, If-Range cannot be proven to match; send the full body.
        with request({"Range": "bytes=2-5", "If-Range": '"stale-validator"'}) as response:
            assert response.status == 200
            assert response.read() == payload

        try:
            request({"Range": "bytes=10-"})
        except HTTPError as error:
            assert error.code == 416
            assert error.headers["Content-Range"] == "bytes */10"
        else:
            raise AssertionError("an unsatisfiable single range must return 416")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
