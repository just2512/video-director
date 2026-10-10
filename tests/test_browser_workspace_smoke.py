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
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
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
                assert page.locator('button[title="上一帧"]').count() == 1
                assert page.locator('button[title="下一帧"]').count() == 1
                assert page.evaluate("typeof window.previewStepFrame") == "function"
                stepped = page.evaluate("""() => {
                    const previous = ED.comp;
                    const previousMode = PV.mode;
                    const previousHead = PV.playhead;
                    ED.comp = {project: {fps: 24}, duration: 2, scenes: []};
                    PV.mode = 'live';
                    PV.playhead = 0;
                    previewStepFrame(1);
                    const result = PV.playhead;
                    ED.comp = previous;
                    PV.mode = previousMode;
                    PV.playhead = previousHead;
                    return result;
                }""")
                assert abs(stepped - 1 / 24) < 0.00001
                assert page.evaluate("typeof window.renderPhysicalTimeline") == "function"
                geometry = page.evaluate("""() => {
                    const previousComp = ED.comp;
                    const previousZoom = ED.zoom;
                    const previousPlayhead = PLAYHEAD.at;
                    const previousGroups = Object.fromEntries(
                        Object.entries(TL_GROUPS).map(([key, group]) => [key, group.open])
                    );
                    try {
                        ED.zoom = 1;
                        PLAYHEAD.at = 0;
                        Object.values(TL_GROUPS).forEach(group => { group.open = true; });
                        ED.comp = {
                            duration: 10,
                            project: {fps: 30, duration: 10},
                            scenes: [
                                {id: 'S01', start: 0, duration: 5, text: 'First',
                                 layers: [{type: 'image', asset_id: 'img', start: 0, duration: 5}]},
                                {id: 'S02', start: 5, duration: 5, text: 'Second',
                                 layers: [
                                   {type: 'image', asset_id: 'img', start: 0, duration: 3},
                                   {type: 'broll', role: 'broll', asset_id: 'clip', start: 3, duration: 2}
                                 ]}
                            ],
                            assets: {
                                img: {name: 'Still', file: 'still.jpg'},
                                clip: {name: 'B-roll source', file: 'clip.mp4'},
                                'bgm-1': {name: 'Music', file: 'music.wav'},
                                'sfx-1': {name: 'Impact', file: 'impact.wav'},
                                'amb-1': {name: 'Room tone', file: 'room.wav'}
                            },
                            audio: [
                                {type: 'bgm', asset_id: 'bgm-1', start: 3, duration: 2, volume: 0.5},
                                {type: 'sfx', asset_id: 'sfx-1', start: 7, duration: 1, volume: 0.4},
                                {type: 'ambience', asset_id: 'amb-1', start: 6, duration: 1, volume: 0.3}
                            ],
                            watermarks: [],
                            subtitle_track: {mode: 'word', cues: []}
                        };
                        const host = document.createElement('div');
                        host.innerHTML = renderPhysicalTimeline();
                        const clip = label => {
                            const row = [...host.querySelectorAll('.ed-track')].find(
                                item => item.querySelector('.ed-track-label')?.textContent === label
                            );
                            const node = row && row.querySelector('.ed-track-clip');
                            return node ? {left: node.style.left, width: node.style.width, title: node.title} : null;
                        };
                        return {bgm: clip('背景音乐'), sfx: clip('音效'), ambience: clip('环境音'), broll: clip('B-roll')};
                    } finally {
                        ED.comp = previousComp;
                        ED.zoom = previousZoom;
                        PLAYHEAD.at = previousPlayhead;
                        for (const [key, open] of Object.entries(previousGroups)) TL_GROUPS[key].open = open;
                    }
                }""")
                assert geometry["bgm"]["left"] == "30%" and geometry["bgm"]["width"] == "20%"
                assert geometry["sfx"]["left"] == "70%" and geometry["sfx"]["width"] == "10%"
                assert geometry["ambience"]["left"] == "60%" and geometry["ambience"]["width"] == "10%"
                assert geometry["broll"]["left"] == "80%" and geometry["broll"]["width"] == "20%"
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
