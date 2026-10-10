"""Real Chromium smoke test for the single-screen editor workspace.

On failure, retain a Playwright trace under test-results/ for CI artifact upload.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_chromium_loads_unified_editor_workspace():
    port = _free_port()
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "webui" / "server.py"), str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    results = ROOT / "test-results"
    results.mkdir(exist_ok=True)
    trace_path = results / "workspace-smoke-trace.zip"
    server_log = results / "workspace-smoke-server.log"
    deadline = time.monotonic() + 30
    ready = False
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                    if response.status == 200:
                        ready = True
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)
        if not ready:
            output = proc.stdout.read() if proc.stdout else ""
            server_log.write_text(output or "Server did not become ready within 30 seconds.", encoding="utf-8")
            pytest.fail("WebUI server did not become ready; see test-results/workspace-smoke-server.log")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            try:
                response = page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded", timeout=15000)
                assert response is not None and response.status == 200
                assert page.title()
                assert page.locator("#editor-workspace").count() == 1
                assert page.locator("#editor-timeline").count() == 1
                assert page.locator("#ed-preview-stage").count() == 1
                assert page.locator("#shotbook-list").count() == 1
                assert page.locator("#pane-editor").count() == 1
            except Exception:
                context.tracing.stop(path=str(trace_path))
                raise
            else:
                context.tracing.stop()
            finally:
                context.close()
                browser.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if proc.stdout:
            output = proc.stdout.read()
            if output:
                server_log.write_text(output, encoding="utf-8")
