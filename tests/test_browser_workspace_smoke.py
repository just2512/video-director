"""Real Chromium smoke test for the single-screen editor workspace.

On failure, retain a Playwright trace under test-results/ for CI artifact upload.
"""
from __future__ import annotations

import hashlib
import json
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


def test_chromium_loads_unified_editor_workspace(tmp_path: Path):
    port = _free_port()
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    # Browser-driven uploads and template samples must never touch a user's/default storage.
    env["VIDEO_DIRECTOR_STORAGE_ROOT"] = str(tmp_path / "storage")
    still = tmp_path / "template-sample-input.png"
    generated = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
            "color=c=0x284b63:s=96x96:d=1", "-frames:v", "1", str(still),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert generated.returncode == 0, generated.stderr
    assert still.is_file() and still.stat().st_size > 100
    results = ROOT / "test-results"
    results.mkdir(exist_ok=True)
    trace_path = results / "workspace-smoke-trace.zip"
    server_log = results / "workspace-smoke-server.log"
    # stdout 落盘而不是 PIPE：服务端每个请求都打日志，不读取的 PIPE 填满
    # 64KB 缓冲后写阻塞 → HTTP 处理线程挂死 → 前端 API 永久超时（画廊卡
    # .tg-template-card 实测复现）。落盘保留完整日志供失败诊断。
    log_handle = open(server_log, "wb")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "webui" / "server.py"), str(port)],
        cwd=ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
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
            log_handle.close()
            if not server_log.stat().st_size:
                server_log.write_text(
                    "Server did not become ready within 30 seconds (empty log).",
                    encoding="utf-8")
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

                # The dual view displays a physical timeline, so its ruler must remain
                # interactive. This was the unique regression test from stale PR #33;
                # the implementation had reached main, but this real-browser test had not.
                dual_scrub = page.evaluate("""() => {
                    const pane = document.getElementById('pane-editor');
                    const body = document.getElementById('editor-body');
                    const timeline = document.getElementById('editor-timeline');
                    const old = {
                        paneDisplay: pane.style.display, bodyDisplay: body.style.display,
                        html: timeline.innerHTML, comp: ED.comp, view: ED.timelineView,
                        at: PLAYHEAD.at, dragging: PLAYHEAD.dragging,
                        seek: window.previewSeek, pause: window.previewPause
                    };
                    try {
                        pane.style.display = '';
                        body.style.display = '';
                        ED.comp = {
                            version: 1, duration: 10, assets: {}, audio: [], watermarks: [],
                            scenes: [{
                                id: 'dual-smoke-scene', start: 0, duration: 10,
                                text: 'dual timeline interaction check', layers: [{
                                    type: 'image', asset_id: 'dual-smoke-image',
                                    start: 0, duration: 10
                                }]
                            }]
                        };
                        ED.timelineView = 'dual';
                        PLAYHEAD.at = 0;
                        PLAYHEAD.dragging = false;
                        window.previewSeek = () => {};
                        window.previewPause = () => {};
                        editorRenderTimeline();
                        const scroll = timeline.querySelector('.ed-tl-scroll');
                        if (!scroll) throw new Error('dual view did not render its physical timeline');
                        const rect = scroll.getBoundingClientRect();
                        if (rect.width <= 0) throw new Error('dual timeline has no interactive width');
                        scroll.dispatchEvent(new PointerEvent('pointerdown', {
                            bubbles: true, cancelable: true, pointerId: 41,
                            pointerType: 'mouse', button: 0,
                            clientX: rect.left + rect.width * 0.6,
                            clientY: rect.top + 3
                        }));
                        const at = PLAYHEAD.at;
                        document.dispatchEvent(new PointerEvent('pointerup', {
                            bubbles: true, cancelable: true, pointerId: 41,
                            pointerType: 'mouse', button: 0,
                            clientX: rect.left + rect.width * 0.6,
                            clientY: rect.top + 3
                        }));
                        return {
                            at, width: rect.width,
                            semanticRows: timeline.querySelectorAll('.ed-semantic-row').length,
                            physicalRuler: !!timeline.querySelector('.ed-tl-scroll')
                        };
                    } finally {
                        ED.comp = old.comp;
                        ED.timelineView = old.view;
                        PLAYHEAD.at = old.at;
                        PLAYHEAD.dragging = old.dragging;
                        window.previewSeek = old.seek;
                        window.previewPause = old.pause;
                        timeline.innerHTML = old.html;
                        pane.style.display = old.paneDisplay;
                        body.style.display = old.bodyDisplay;
                    }
                }""")
                assert 5.0 <= dual_scrub["at"] <= 7.0, dual_scrub
                assert dual_scrub["semanticRows"] == 1, dual_scrub
                assert dual_scrub["physicalRuler"], dual_scrub

                # Article URL import is a first-class Director input, but the UI test uses
                # a deterministic intercepted response rather than depending on an external site.
                page.route(
                    "**/api/source/article",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps({
                            "ok": True,
                            "article": {
                                "source_url": "https://news.example/article",
                                "requested_url": "https://news.example/article",
                                "title": "Imported article fixture",
                                "content": "An imported article contains an explained fact. " * 5,
                                "content_sha256": hashlib.sha256(("An imported article contains an explained fact. " * 5).encode()).hexdigest(),
                                "content_chars": len("An imported article contains an explained fact. " * 5),
                                "content_type": "text/html",
                                "fetched_at": "2026-10-11T12:00:00+00:00",
                                "extraction_status": "EXTRACTED",
                            },
                        }),
                    ),
                )
                page.evaluate("openCreate()")
                page.locator("#cf-article-url").fill("https://news.example/article")
                page.locator("#cf-import-article-btn").click()
                page.get_by_text("Imported article fixture").wait_for(timeout=10000)
                assert page.evaluate("window._cfArticle.title") == "Imported article fixture"
                assert page.evaluate("window._cfArticle.content_sha256.length") == 64
                assert "文章已导入" in page.locator("#cf-article-status").inner_text()
                page.evaluate("closeCreate()")
                page.evaluate("openCreate()")
                page.locator("#cf-article-url").wait_for(timeout=10000)
                assert page.locator("#cf-article-url").input_value() == "https://news.example/article"
                assert page.get_by_text("Imported article fixture").count() > 0
                assert "已恢复上一轮导入" in page.locator("#cf-article-status").inner_text()
                page.evaluate("cfClearArticleSource()")
                assert page.evaluate("window._cfArticle") is None
                assert page.locator("#cf-article-url").input_value() == ""
                page.unroute("**/api/source/article")

                # User-facing template gallery must use the real registry/runtime API,
                # expose its readiness caveats, and refuse actions until assets exist.
                page.locator("#cf-template-gallery-btn").wait_for(timeout=10000)
                page.locator("#cf-template-gallery-btn").click()
                page.locator("#tg-mask").wait_for(timeout=10000)
                page.locator(".tg-template-card").first.wait_for(timeout=15000)
                assert page.locator(".tg-template-card").count() > 0
                assert page.locator("#tg-detail").inner_text().find("执行前检查") >= 0
                assert page.locator("#tg-detail").inner_text().find("输出画幅") >= 0
                assert page.locator("#tg-render").is_disabled()
                assert page.locator("#tg-instantiate").is_disabled()
                assert page.locator(".tg-status-chip").count() >= 2

                # True browser-to-renderer loop: upload a real image, fill required slots,
                # render through the actual multipart endpoint, and inspect the decoded MP4.
                minimal_index = page.evaluate(
                    "() => window.TG.visible.findIndex(item => item.id === 'tpl.minimal-editorial')"
                )
                assert minimal_index >= 0, "built-in minimal editorial template is not in the real registry"
                page.locator(".tg-template-card").nth(minimal_index).click()
                page.locator("#tg-ratio-select").select_option("16:9")
                page.locator("[data-tg-text-slot='title']").fill("Browser smoke title")
                page.locator("[data-tg-text-slot='body_text']").fill("Real template render verification")
                page.locator("[data-tg-file-slot='hero_image']").set_input_files(str(still))
                page.wait_for_function("() => !document.getElementById('tg-render').disabled")
                page.locator("#tg-render").click()
                page.wait_for_function(
                    "() => { const s = document.getElementById('tg-detail-status')?.textContent || '';"
                    "return s.includes('真实渲染和 MP4 解码通过') || s.includes('样片未通过验证'); }",
                    timeout=300000,
                )
                render_status = page.locator("#tg-detail-status").inner_text()
                assert "真实渲染和 MP4 解码通过" in render_status, render_status
                sample_evidence = page.locator("#tg-sample-result .hint").inner_text()
                assert '"decode":"PASS"' in sample_evidence, sample_evidence
                assert '"duration_seconds":' in sample_evidence, sample_evidence
                page.wait_for_function(
                    "() => { const v = document.querySelector('#tg-sample-result video');"
                    "return !!v && v.readyState >= 1 && Number.isFinite(v.duration) && v.duration > 0; }",
                    timeout=30000,
                )
                preview_duration = page.locator("#tg-sample-result video").evaluate("(v) => v.duration")
                assert preview_duration > 0, f"sample MP4 did not load in the gallery video preview: {preview_duration}"
                page.evaluate("templateGalleryClose()")
                page.locator("#cf-style-gallery-btn").wait_for(timeout=10000)
                page.locator("#cf-style-gallery-btn").click()
                page.locator("#sg-list .sg-card").first.wait_for(timeout=15000)
                assert page.locator("#sg-list .sg-card").count() >= 4
                assert "METADATA_ONLY" in page.locator("#sg-list").inner_text()
                assert "图片运动倍率" in page.locator("#sg-list").inner_text()
                page.locator('[data-style-id="style.documentary-calm"]').click()
                assert page.locator("#cf-style").input_value() == "纪录片、沉稳、专业"
                page.evaluate("closeCreate()")

                assert page.locator("#editor-workspace").count() == 1
                assert page.locator("#editor-timeline").count() == 1
                assert page.locator("#ed-preview-stage").count() == 1
                assert page.locator("#shotbook-list").count() == 1
                assert page.locator("#shotbook-storyboard").count() == 1
                assert page.evaluate("typeof window.shotbookPreviewScene") == "function"
                board_result = page.evaluate("""async () => {
                    const board = document.getElementById('shotbook-storyboard');
                    const list = document.getElementById('shotbook-list');
                    const status = document.getElementById('shotbook-status');
                    const meta = document.getElementById('shotbook-meta');
                    const note = document.getElementById('shotbook-timing-note');
                    const gates = document.getElementById('shotbook-gates');
                    const old = {
                        book: SHOTBOOK, comp: ED.comp, board: board.innerHTML, list: list.innerHTML,
                        status: status.textContent, meta: meta.textContent, note: note.textContent,
                        gates: gates.innerHTML, mode: PV.mode, playhead: PV.playhead, playing: PV.playing,
                        pause: window.previewPause, seek: window.previewSeek, play: window.previewPlay,
                        locate: window.shotbookLocate
                    };
                    const base = {
                        semantic_role: 'hook', visual_intent: 'establish', shot_type: 'still',
                        shot_recipe_id: '', motion_recipe_id: '', motion_layer_target: '',
                        overlay_text: '', evidence_ref: '', sfx_cue: '', notes: '',
                        reviewed: false, characters: [], applied_motion_recipe_id: ''
                    };
                    SHOTBOOK = {
                        project_id: 'browser-smoke', version: 4, saved: true, timing_note: 'smoke',
                        gates: [], recipes: [], motions: [], shots: [
                            Object.assign({}, base, {scene_id: 'smoke-shot-1', start: 0, end: 2,
                                text: 'First visual storyboard frame',
                                editable_layers: [{target_id:'smoke-shot-1#0',type:'image',asset_id:'test-image',locked:false}],
                                has_bound_asset: true}),
                            Object.assign({}, base, {scene_id: 'smoke-shot-2', start: 2, end: 5,
                                text: 'Second visual storyboard frame', editable_layers: [], has_bound_asset: false})
                        ]
                    };
                    ED.comp = {assets: { 'test-image': {type:'image', file_url:'/file?path=storybook-smoke.png'} }};
                    shotbookRender();
                    const result = {
                        cards: board.querySelectorAll('.sb-board-card').length,
                        thumbnails: board.querySelectorAll('.sb-board-thumb img').length,
                        previewButtons: board.querySelectorAll('[data-shotbook-action="preview"]').length,
                        sfxButtons: list.querySelectorAll('[data-shotbook-action="open-sfx"]').length,
                        firstText: board.innerText.includes('First visual storyboard frame')
                    };
                    const calls = [];
                    window.previewPause = () => { calls.push('pause'); PV.playing = false; };
                    window.previewSeek = at => { calls.push(['seek', at]); PV.mode = 'live'; PV.playhead = at; };
                    window.previewPlay = () => { calls.push('play'); PV.playhead = 2; PV.playing = true; };
                    window.shotbookLocate = id => calls.push(['locate', id]);
                    PV.mode = 'live'; PV.playhead = 0; PV.playing = false;
                    shotbookPreviewScene('smoke-shot-1');
                    await new Promise(resolve => setTimeout(resolve, 70));
                    result.previewCalls = calls;
                    result.stoppedAtShotEnd = window.SB_PREVIEW_TIMER === null;
                    if (window.SB_PREVIEW_TIMER) clearInterval(window.SB_PREVIEW_TIMER);
                    window.SB_PREVIEW_TIMER = null;
                    SHOTBOOK = old.book; ED.comp = old.comp;
                    board.innerHTML = old.board; list.innerHTML = old.list;
                    status.textContent = old.status; meta.textContent = old.meta;
                    note.textContent = old.note; gates.innerHTML = old.gates;
                    PV.mode = old.mode; PV.playhead = old.playhead; PV.playing = old.playing;
                    window.previewPause = old.pause; window.previewSeek = old.seek;
                    window.previewPlay = old.play; window.shotbookLocate = old.locate;
                    return result;
                }""")
                assert board_result["cards"] == 2
                assert board_result["thumbnails"] == 1
                assert board_result["previewButtons"] == 2
                assert board_result["sfxButtons"] == 2
                assert board_result["firstText"] is True
                sfx_picker = page.evaluate("""async () => {
                    const oldBook = SHOTBOOK, oldComp = ED.comp;
                    SHOTBOOK = {project_id:'browser-smoke', shots:[
                        {scene_id:'sfx-scene', start:1, end:3, sfx_cue:'轻微落点'}
                    ]};
                    ED.comp = {duration:10, assets:{}};
                    try {
                        const assets = await apiGet('/api/sfx');
                        await shotbookOpenSfx('sfx-scene');
                        const select = document.getElementById('shotbook-sfx-asset');
                        const player = document.getElementById('shotbook-sfx-preview');
                        const result = {
                            apiAssets: Array.isArray(assets) ? assets.length : -1,
                            modal: !!document.getElementById('shotbook-sfx-modal'),
                            hasSelector: !!select,
                            count: select ? select.options.length : 0,
                            previewRoute: player ? player.getAttribute('src') : '',
                            status: (document.getElementById('shotbook-sfx-status') || {}).textContent || ''
                        };
                        shotbookSfxModalClose();
                        return result;
                    } finally {
                        SHOTBOOK = oldBook;
                        ED.comp = oldComp;
                    }
                }""")
                assert sfx_picker["apiAssets"] > 0
                assert sfx_picker["modal"] is True
                assert sfx_picker["hasSelector"] is True
                assert sfx_picker["count"] > 0
                assert sfx_picker["previewRoute"].startswith("/api/audio_preview?asset_id=")
                assert ["seek", 0] in board_result["previewCalls"]
                assert "play" in board_result["previewCalls"]
                assert ["locate", "smoke-shot-1"] in board_result["previewCalls"]
                assert board_result["stoppedAtShotEnd"] is True
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
                            watermarks: [{id: 'wm-geometry', kind: 'text', text: 'Overlay', start: 2, duration: 3, anchor: 'top_right', opacity: 0.75}],
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
                        return {bgm: clip('背景音乐'), sfx: clip('音效'), ambience: clip('环境音'), broll: clip('B-roll'), overlay: clip('贴纸 / 覆盖层')};
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
                assert geometry["overlay"]["left"] == "20%" and geometry["overlay"]["width"] == "30%"

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
                        watermarks: [{id: 'wm-browser', kind: 'text', text: 'Overlay', start: 2, duration: 3, anchor: 'top_right', opacity: 0.75}], subtitle_track: {mode: 'word', cues: []}
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
                # The timeline lives below the fold in this full-workspace browser test.
                # Playwright's mouse API sends page coordinates; never click a valid but
                # off-viewport bounding box (which can be > viewport height).
                audio_clip.scroll_into_view_if_needed()
                move_box = audio_clip.bounding_box()
                assert move_box and move_box["width"] > 16 and move_box["height"] > 0
                viewport_height = page.viewport_size["height"]
                assert 0 <= move_box["y"] and move_box["y"] + move_box["height"] <= viewport_height, (
                    f"audio move target must be inside viewport: box={move_box}, viewport={page.viewport_size}"
                )
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
                trim_clip.scroll_into_view_if_needed()
                trim_box = trim_clip.bounding_box()
                assert trim_box and trim_box["width"] > 16
                assert 0 <= trim_box["y"] and trim_box["y"] + trim_box["height"] <= viewport_height, (
                    f"audio trim target must be inside viewport: box={trim_box}, viewport={page.viewport_size}"
                )
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
# The text/logo overlay is an actual positioned clip, not a full-width summary.
                captured_commands.clear()
                page.evaluate("editorRenderTimeline()")
                overlay_clip = page.locator('#editor-timeline .ed-watermark-clip[data-watermark-index="0"]')
                assert overlay_clip.count() == 1
                overlay_clip.scroll_into_view_if_needed()
                overlay_box = overlay_clip.bounding_box()
                assert overlay_box and overlay_box["width"] > 16 and overlay_box["height"] > 0
                assert 0 <= overlay_box["y"] and overlay_box["y"] + overlay_box["height"] <= viewport_height, (
                    f"overlay move target must be inside viewport: box={overlay_box}, viewport={page.viewport_size}"
                )
                page.mouse.move(overlay_box["x"] + overlay_box["width"] / 2,
                                overlay_box["y"] + overlay_box["height"] / 2)
                page.mouse.down()
                page.mouse.move(overlay_box["x"] + overlay_box["width"] / 2 + 40,
                                overlay_box["y"] + overlay_box["height"] / 2, steps=4)
                page.mouse.up()
                page.wait_for_timeout(100)
                moved_overlay = next((c for c in captured_commands if c.get("command") == "move_watermark"), None)
                assert moved_overlay is not None, "overlay drag did not POST move_watermark"
                assert moved_overlay["targetId"] == "watermark#0"
                assert moved_overlay["payload"]["start"] > 2

                captured_commands.clear()
                page.evaluate("editorRenderTimeline()")
                overlay_clip = page.locator('#editor-timeline .ed-watermark-clip[data-watermark-index="0"]')
                overlay_clip.scroll_into_view_if_needed()
                overlay_box = overlay_clip.bounding_box()
                assert overlay_box and overlay_box["width"] > 16
                assert 0 <= overlay_box["y"] and overlay_box["y"] + overlay_box["height"] <= viewport_height, (
                    f"overlay trim target must be inside viewport: box={overlay_box}, viewport={page.viewport_size}"
                )
                page.mouse.move(overlay_box["x"] + overlay_box["width"] - 1,
                                overlay_box["y"] + overlay_box["height"] / 2)
                page.mouse.down()
                page.mouse.move(overlay_box["x"] + overlay_box["width"] - 31,
                                overlay_box["y"] + overlay_box["height"] / 2, steps=4)
                page.mouse.up()
                page.wait_for_timeout(100)
                trimmed_overlay = next((c for c in captured_commands if c.get("command") == "trim_watermark"), None)
                assert trimmed_overlay is not None, "overlay edge trim did not POST trim_watermark"
                assert trimmed_overlay["targetId"] == "watermark#0"
                assert trimmed_overlay["payload"]["duration"] < 3

                # Overlay clips must be visually identifiable and the Inspector's
                # cut-at-playhead control must reach the versioned Editor Command API.
                overlay_ui = page.evaluate("""() => {
                    ED.comp.visual_overlays = [{
                        id: 'overlay-ui-smoke', asset_id: 'img', start: 2, duration: 4,
                        source_in: 0, x: 0.65, y: 0.1, width: 0.3, height: 0.3,
                        opacity: 1, z_index: 0
                    }];
                    ED.comp.assets.img.type = 'image';
                    ED.comp.assets.img.thumbnail_url = '/overlay-smoke-thumb.svg';
                    PLAYHEAD.at = 4;
                    editorRenderTimeline();
                    const clip = document.querySelector('#editor-timeline .ed-visual-overlay-clip');
                    return {
                        count: document.querySelectorAll('#editor-timeline .ed-visual-overlay-clip').length,
                        thumbnail: clip && clip.querySelector('img.ed-clip-thumb')?.getAttribute('src'),
                        label: clip && clip.querySelector('.ed-track-clip-label')?.textContent
                    };
                }""")
                assert overlay_ui["count"] == 1
                assert overlay_ui["thumbnail"] == "/overlay-smoke-thumb.svg"
                assert overlay_ui["label"] == "Still"

                page.route(
                    "**/overlay-smoke-thumb.svg",
                    lambda route: route.fulfill(
                        status=200, content_type="image/svg+xml",
                        body='<svg xmlns="http://www.w3.org/2000/svg" width="40" height="24"><rect width="40" height="24" fill="#284b63"/></svg>',
                    ),
                )
                overlay_clip = page.locator("#editor-timeline .ed-visual-overlay-clip")
                overlay_clip.scroll_into_view_if_needed()
                overlay_clip.click()
                inspector = page.locator("#visual-overlay-inspector-mask")
                inspector.wait_for(timeout=5000)
                assert inspector.get_by_role("button", name="在播放头切分").is_visible()
                captured_commands.clear()
                inspector.get_by_role("button", name="在播放头切分").click()
                page.wait_for_timeout(100)
                split_cmd = next((c for c in captured_commands if c.get("command") == "split_visual_overlay"), None)
                assert split_cmd is not None, "cut-at-playhead button did not POST split_visual_overlay"
                assert split_cmd["targetId"] == "overlay#0"
                assert split_cmd["payload"]["at"] == pytest.approx(4.0)
                # The intercepted endpoint deliberately rejects the command; the UI
                # must keep the Inspector open and must not claim the split succeeded.
                assert page.locator("#visual-overlay-inspector-mask").count() == 1
                page.evaluate("editorVisualOverlayClose()")
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
        log_handle.close()  # 日志已全程落盘，句柄收尾

