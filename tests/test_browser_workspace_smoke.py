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

                # Real DOM pointer interaction: moving and trimming audio clips must
                # dispatch versioned Editor Commands rather than only changing CSS.
                setup = page.evaluate("""() => {
                    const pane = document.getElementById('pane-editor');
                    const body = document.getElementById('editor-body');
                    pane.style.display = '';
                    body.style.display = '';
                    ED.pid = 'browser-audio-test';
                    ED.version = 1;
                    ED.zoom = 1;
                    ED.timelineView = 'time';
                    PLAYHEAD.at = 0;
                    Object.values(TL_GROUPS).forEach(group => { group.open = true; });
                    ED.comp = {
                        duration: 10, version: 1,
                        project: {fps: 30, duration: 10},
                        scenes: [{id: 'S01', start: 0, duration: 10, text: 'Audio interaction',
                                  layers: [{type: 'image', asset_id: 'img', start: 0, duration: 10}]}],
                        assets: {
                            img: {name: 'Still', file: 'still.jpg'},
                            'bgm-1': {name: 'Music', file: 'music.wav'},
                            'sfx-1': {name: 'Impact', file: 'impact.wav'}
                        },
                        audio: [
                            {type: 'bgm', asset_id: 'bgm-1', start: 3, duration: 2, volume: 0.5},
                            {type: 'sfx', asset_id: 'sfx-1', start: 7, duration: 1, volume: 0.4}
                        ],
                        watermarks: [], subtitle_track: {mode: 'word', cues: []}
                    };
                    editorRenderTimeline();
                    const clips = [...document.querySelectorAll('#editor-timeline .ed-audio-clip[data-audio-index]')];
                    return {count: clips.length, firstIndex: clips[0] && clips[0].dataset.audioIndex};
                }""")
                assert setup["count"] == 2 and setup["firstIndex"] == "0"

                captured_commands = []
                def capture_editor_command(route):
                    captured_commands.append(route.request.post_data_json)
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body='{"ok":false,"status":"REJECTED","error":"intentional browser test interception"}',
                    )
                page.route("**/api/editor/command", capture_editor_command)

                audio_clip = page.locator('#editor-timeline .ed-audio-clip[data-audio-index="0"]')
                move_box = audio_clip.bounding_box()
                assert move_box and move_box["width"] > 16 and move_box["height"] > 0
                page.mouse.move(move_box["x"] + move_box["width"] / 2, move_box["y"] + move_box["height"] / 2)
                page.mouse.down()
                page.mouse.move(move_box["x"] + move_box["width"] / 2 + 40,
                                move_box["y"] + move_box["height"] / 2, steps=4)
                page.mouse.up()
                page.wait_for_timeout(100)
                moved = next((c for c in captured_commands if c.get("command") == "move_audio_clip"), None)
                assert moved is not None, "pointer drag did not POST move_audio_clip to the real Editor Command endpoint"
                assert moved["targetId"] == "audio#0"
                assert moved["payload"]["start"] > 3

                captured_commands.clear()
                page.evaluate("editorRenderTimeline()")
                trim_clip = page.locator('#editor-timeline .ed-audio-clip[data-audio-index="0"]')
                trim_box = trim_clip.bounding_box()
                assert trim_box and trim_box["width"] > 16
                page.mouse.move(trim_box["x"] + trim_box["width"] - 1, trim_box["y"] + trim_box["height"] / 2)
                page.mouse.down()
                page.mouse.move(trim_box["x"] + trim_box["width"] - 31,
                                trim_box["y"] + trim_box["height"] / 2, steps=4)
                page.mouse.up()
                page.wait_for_timeout(100)
                trimmed = next((c for c in captured_commands if c.get("command") == "trim_audio_clip"), None)
                assert trimmed is not None, "pointer edge trim did not POST trim_audio_clip to the real Editor Command endpoint"
                assert trimmed["targetId"] == "audio#0"
                assert trimmed["payload"]["sourceOut"] < 2
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
