"""End-to-end style sample rendering: actual MP4 decode, safe download, and fail-closed metadata."""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from video_director import editor, store
from tests.test_studio_browser_flow import _test_composition


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
    "lYkAAAAASUVORK5CYII="
)


def _request(url: str, payload: dict | None = None):
    if payload is None:
        request = Request(url, method="GET")
    else:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    try:
        with urlopen(request, timeout=90) as response:
            return response.status, response.headers, response.read()
    except HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def test_native_style_sample_renders_valid_mp4_without_mutating_project(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))

    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = Path(os.environ["VIDEO_DIRECTOR_STORAGE_ROOT"])

    from video_director import asset_catalog, config

    pid = "style-sample-e2e"
    asset_root = asset_catalog.library_root() / "local"
    asset_root.mkdir(parents=True, exist_ok=True)
    still = asset_root / "style-sample-e2e.png"
    still.write_bytes(_PNG)

    store.create_project(pid, "Native style sample", {"name": "Native style sample"})
    composition = _test_composition(still)
    editor.save_composition(pid, composition)
    project_dir = config.project_dir(pid)
    manifest = project_dir / "assets" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps([{
        "id": "e2e-still",
        "type": "image",
        "file": str(still.resolve()),
        "width": 1,
        "height": 1,
        "source": "uploaded",
    }]), encoding="utf-8")

    before = hashlib.sha256(editor.load_composition(pid).model_dump_json().encode("utf-8")).hexdigest()
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        status, _, raw = _request(base + "/api/styles/sample_render", {
            "project_id": pid,
            "style_id": "style.documentary-calm",
        })
        assert status == 200, raw.decode("utf-8", "replace")
        response = json.loads(raw)
        assert response["ok"] is True, response
        assert response["runtime"]["sample_render"] == "PASS", response
        assert response["runtime"]["mp4_decode"] == "PASS", response
        assert response["runtime"]["status"] == "PARTIAL", response
        assert "音效密度：不自动增删用户时间线中的 SFX" in response["runtime"]["not_applied_parameters"]
        sample_url = base + response["download_url"]
        dl_status, headers, mp4 = _request(sample_url)
        assert dl_status == 200, (dl_status, mp4[:500])
        assert headers.get_content_type() == "video/mp4"
        assert len(mp4) > 1024, len(mp4)
        after = hashlib.sha256(editor.load_composition(pid).model_dump_json().encode("utf-8")).hexdigest()
        assert after == before, "sample rendering mutated the saved project CompositionIR"

        # Exercise the user-visible path, not just the HTTP handler.
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(120000)
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            try:
                # /api/events 是 SSE 长连接，networkidle 永不满足；后续 UI 断言负责就绪
                page.goto(base, wait_until="domcontentloaded")
                page.wait_for_function("() => typeof workspaceOpen === 'function'")
                opened = page.evaluate("(projectId) => workspaceOpen(projectId, undefined, {legacyCompositionMode:true})", pid)
                assert opened and opened.get("ok"), opened
                page.locator("#tb-style-sample").click()
                button = page.locator('[data-style-sample="style.documentary-calm"]')
                button.wait_for(state="visible")
                button.click()
                page.wait_for_selector("#sg-sample-result video")
                page.wait_for_function(
                    "() => document.querySelector('#sg-status')?.textContent.includes('完整解码均通过')",
                    timeout=120000,
                )
                src = page.locator("#sg-sample-result video").get_attribute("src")
                assert src and src.startswith("/api/style_sample/download?id="), src
                assert "已实际应用：" in page.locator("#sg-sample-result").inner_text()
                assert "本版尚未应用" in page.locator("#sg-sample-result").inner_text()
                assert not page_errors, page_errors
            finally:
                page.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_external_metadata_style_is_not_executed_or_marked_renderable(tmp_path):
    from video_director.style_sample import StyleSampleError, render_style_sample

    root = tmp_path / "samples"
    try:
        render_style_sample("safe-project-id", "style.external-imported", root)
    except StyleSampleError as exc:
        assert exc.code == "METADATA_ONLY"
        assert "受控的内置执行器" in str(exc)
    else:
        raise AssertionError("external metadata-only style was incorrectly treated as executable")
    assert not list(root.glob("*.mp4")) if root.exists() else True


def test_style_sample_does_not_claim_subtitle_parameters_when_output_has_no_subtitles(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    from video_director import asset_catalog, config
    from video_director.style_sample import render_style_sample

    project_id = "style-no-subtitle"
    asset_root = asset_catalog.library_root() / "local"
    asset_root.mkdir(parents=True, exist_ok=True)
    still = asset_root / (project_id + ".png")
    still.write_bytes(_PNG)

    store.create_project(project_id, "No subtitles", {"name": "No subtitles"})
    composition = _test_composition(still)
    composition = composition.model_copy(update={
        "scenes": [scene.model_copy(update={"text": ""}) for scene in composition.scenes],
        "subtitle_track": None,
    })
    editor.save_composition(project_id, composition)
    project_dir = config.project_dir(project_id)
    manifest = project_dir / "assets" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps([{
        "id": "e2e-still",
        "type": "image",
        "file": str(still.resolve()),
        "width": 1,
        "height": 1,
        "source": "uploaded",
    }]), encoding="utf-8")

    result = render_style_sample(project_id, "style.documentary-calm", tmp_path / "samples")
    assert result["runtime"]["sample_render"] == "PASS", result
    assert result["runtime"]["mp4_decode"] == "PASS", result
    applied = result["runtime"]["applied_parameters"]
    not_applied = result["runtime"]["not_applied_parameters"]
    assert "字幕位置" not in applied, applied
    assert "字幕字号" not in applied, applied
    assert "字幕样式：当前项目没有可渲染的字幕文本/时间锚点" in not_applied, not_applied
