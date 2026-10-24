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


#: Chromium 内置封禁端口（net/base/port_util.cc kRestrictedPorts）——绑到这些端口
#: 的本地服务会被浏览器以 net::ERR_UNSAFE_PORT 拒绝，与代码无关。
_CHROMIUM_RESTRICTED_PORTS = frozenset({
    1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 69, 77, 79,
    87, 95, 101, 102, 103, 104, 109, 110, 111, 113, 115, 117, 119, 123, 135, 137,
    139, 143, 161, 179, 389, 427, 465, 512, 513, 514, 515, 526, 530, 531, 532,
    540, 548, 554, 556, 563, 587, 601, 636, 989, 990, 993, 995, 1719, 1720, 1723,
    2049, 3659, 4045, 5060, 5061, 6000, 6566, 6665, 6666, 6667, 6668, 6669, 6697,
    10080,
})


def _free_port() -> int:
    """挑一个 Chromium 一定不会拒绝的端口。

    本机动态端口区间是 1024-14999 且 Windows 顺序发放（netsh 实测），因此
    必然走过封禁表里的 6000/6566/6665-6669/6697/10080 → 偶发
    net::ERR_UNSAFE_PORT（2026-10-15 全量跑实测一次）。这里显式排除封禁表，
    不假设动态区间在哪一段。
    """
    for _ in range(200):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port > 1023 and port not in _CHROMIUM_RESTRICTED_PORTS:
            return port
    raise RuntimeError("找不到 Chromium 可用端口（封禁表外）")


def test_chromium_loads_unified_editor_workspace(tmp_path: Path):
    port = _free_port()
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    # Browser-driven uploads and template samples must never touch a user's/default storage.
    env["VIDEO_DIRECTOR_STORAGE_ROOT"] = str(tmp_path / "storage")
    still = tmp_path / "template-sample-input.png"
    # 走产品自身的 ffmpeg 解析链（release/bin 随包二进制优先，PATH 兜底），
    # 与运行时行为一致——裸 "ffmpeg" 在无 PATH 机器上必挂（2026-10-13 实锤）。
    from video_director import prober as _vd_prober
    _ffmpeg = _vd_prober._resolve_ffmpeg() or shutil.which("ffmpeg")
    assert _ffmpeg, "ffmpeg 不可用：release/bin 缺失且 PATH 无 ffmpeg"
    generated = subprocess.run(
        [
            _ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
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
                            const row = [...host.querySelectorAll('.ed-track')].find(item => {
                                // Track controls must not be part of the semantic label lookup.
                                const name = item.querySelector(
                                    '.ed-audio-track-label > span, .ed-visual-track-name, .ed-track-label > span'
                                )?.textContent?.trim();
                                const firstText = [...(item.querySelector('.ed-track-label')?.childNodes || [])]
                                    .find(node => node.nodeType === Node.TEXT_NODE)?.textContent?.trim();
                                return (name || firstText || '') === label;
                            });
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
                        audio_tracks: [
                            {id: 'audio-bgm', name: '背景音乐', order: 0},
                            {id: 'audio-sfx', name: '音效', order: 1}
                        ],
                        audio: [
                            {type: 'bgm', asset_id: 'bgm-1', track_id: 'audio-bgm', start: 3, duration: 2, volume: 0.5},
                            {type: 'sfx', asset_id: 'sfx-1', track_id: 'audio-sfx', start: 7, duration: 1, volume: 0.4}
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

                # Browser-level contract for lifting a main video clip into the same
                # independently editable global visual track shown in the timeline.
                promotion_setup = page.evaluate("""() => {
                    ED.comp.visual_tracks = [{id: 'overlay-main', name: '画面叠加', order: 0}];
                    ED.comp.scenes[0].layers[0] = {
                        type: 'video', asset_id: 'video-source', start: 0, duration: 10,
                        source_in: 0, source_out: 10, speed: 1, fit_mode: 'cover'
                    };
                    ED.comp.assets['video-source'] = {name: 'Source video', file: 'video.mp4'};
                    SEL.selection = {type: 'layer', id: 'S01#0'};
                    SEL.selectionSet = new Set([_selKey('layer', 'S01#0')]);
                    edShowInspector();
                    return {
                        buttonCount: [...document.querySelectorAll('#editor-inspector button')]
                            .filter(button => button.textContent.includes('提升到独立画面轨道')).length,
                        disabled: [...document.querySelectorAll('#editor-inspector button')]
                            .find(button => button.textContent.includes('提升到独立画面轨道'))?.disabled
                    };
                }""")
                assert promotion_setup["buttonCount"] == 1 and promotion_setup["disabled"] is False
                promote_button = page.get_by_role("button", name="提升到独立画面轨道")
                promote_button.scroll_into_view_if_needed()
                promote_button.click()
                page.wait_for_timeout(100)
                promoted_command = next(
                    (command for command in captured_commands
                     if command.get("command") == "promote_layer_to_visual_track"),
                    None,
                )
                assert promoted_command is not None, "browser click did not dispatch promote_layer_to_visual_track"
                assert promoted_command["targetId"] == "S01#0"
                assert not promoted_command["payload"].get("trackId"), "button should ask the server to create a dedicated track atomically"
                captured_commands.clear()

                locked_state = page.evaluate("""() => {
                    ED.comp.scenes[0].layers[0] = {
                        type: 'video', asset_id: 'video-source', start: 0, duration: 10,
                        source_in: 0, source_out: 10, speed: 1, fit_mode: 'cover', locked: true
                    };
                    SEL.selection = {type: 'layer', id: 'S01#0'};
                    SEL.selectionSet = new Set([_selKey('layer', 'S01#0')]);
                    edShowInspector();
                    const button = [...document.querySelectorAll('#editor-inspector button')]
                        .find(item => item.textContent.includes('提升到独立画面轨道'));
                    return {exists: !!button, disabled: button && button.disabled, title: button && button.title};
                }""")
                assert locked_state["exists"] and locked_state["disabled"]
                assert "锁定" in locked_state["title"]

                # Real pointer gesture: drag a normal main-track video vertically
                # onto an existing global visual track. The payload must carry both
                # the selected destination track and the time under the drop pointer.
                drag_boxes = page.evaluate("""async () => {
                    ED.comp.duration = 20;
                    if (ED.comp.project) ED.comp.project.duration = 20;
                    ED.comp.visual_tracks = [{id: 'overlay-main', name: '画面叠加', order: 0}];
                    ED.comp.visual_overlays = [];
                    ED.comp.scenes[0].start = 0;
                    ED.comp.scenes[0].duration = 10;
                    ED.comp.scenes[0].layers[0] = {
                        type: 'video', asset_id: 'video-source', start: 0, duration: 10,
                        source_in: 0, source_out: 10, speed: 1, fit_mode: 'cover'
                    };
                    editorRenderTimeline();
                    const source = document.querySelector('#editor-timeline .ed-layer[data-scene="S01"][data-idx="0"]');
                    const target = document.querySelector('#editor-timeline .ed-track[data-visual-track-id="overlay-main"] .ed-track-body');
                    if (!source || !target) return null;
                    // scrollIntoView can be animated by the app's global smooth-scroll
                    // CSS. Read geometry only after disabling smooth scroll on the
                    // relevant ancestor chain and allowing layout to settle.
                    for (const item of [source, target]) {
                        for (let node = item; node; node = node.parentElement) {
                            node.style.scrollBehavior = 'auto';
                        }
                        item.scrollIntoView({block: item === source ? 'center' : 'nearest', behavior: 'instant'});
                    }
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    const s = source.getBoundingClientRect(), t = target.getBoundingClientRect();
                    const startHit = document.elementFromPoint(s.left + s.width / 2, s.top + s.height / 2);
                    const endHit = document.elementFromPoint(t.left + t.width * 0.30, t.top + t.height / 2);
                    return {
                        source: {x:s.x, y:s.y, w:s.width, h:s.height},
                        target: {x:t.x, y:t.y, w:t.width, h:t.height},
                        viewport: {w:window.innerWidth, h:window.innerHeight},
                        startHit: startHit ? (startHit.tagName + '.' + String(startHit.className || '')) : null,
                        startHitIsSource: !!startHit && (startHit === source || source.contains(startHit)),
                        endHit: endHit ? (endHit.tagName + '.' + String(endHit.className || '')) : null
                    };
                }""")
                assert drag_boxes is not None, "timeline source clip or visual track drop target is missing"
                for key in ("source", "target"):
                    box = drag_boxes[key]
                    assert 0 <= box["y"] and box["y"] + box["h"] <= drag_boxes["viewport"]["h"], (
                        f"main clip and destination track must be simultaneously visible for drag: {drag_boxes}"
                    )
                # Preserve pointer lifecycle evidence on failure. This runs in Chromium
                # against the actual editor listeners; it is intentionally diagnostic
                # rather than a mocked unit test so event retargeting/cancellation is visible.
                page.evaluate("""() => {
                    window.__mainClipDragEvents = [];
                    const describe = (phase, ev) => {
                        const target = ev.target instanceof Element ? ev.target : null;
                        const under = Number.isFinite(ev.clientX) && Number.isFinite(ev.clientY)
                            ? document.elementFromPoint(ev.clientX, ev.clientY) : null;
                        let state = null;
                        try {
                            const d = typeof _layerDrag !== 'undefined' ? _layerDrag : null;
                            state = d ? {
                                sceneId: d.sceneId, idx: d.idx, mode: d.mode, moved: d.moved,
                                connected: !!(d.el && d.el.isConnected),
                                hasCapture: !!(d.el && d.el.hasPointerCapture && d.el.hasPointerCapture(ev.pointerId))
                            } : null;
                        } catch (error) { state = {readError: String(error)}; }
                        window.__mainClipDragEvents.push({
                            phase, type: ev.type, pointerId: ev.pointerId,
                            x: ev.clientX, y: ev.clientY, defaultPrevented: ev.defaultPrevented,
                            target: target ? target.tagName + '.' + String(target.className || '') : String(ev.target),
                            source: target && target.closest('.ed-layer')
                                ? target.closest('.ed-layer').dataset.scene + '#' + target.closest('.ed-layer').dataset.idx : null,
                            underTrack: under && under.closest('.ed-track[data-visual-track-id]')
                                ? under.closest('.ed-track[data-visual-track-id]').dataset.visualTrackId : null,
                            state
                        });
                    };
                    for (const type of ['pointerdown','pointermove','pointerup','pointercancel','gotpointercapture','lostpointercapture','dragstart','dragend']) {
                        document.addEventListener(type, ev => describe('capture', ev), true);
                        document.addEventListener(type, ev => describe('bubble', ev), false);
                    }
                }""")
                drag_start_x = drag_boxes["source"]["x"] + drag_boxes["source"]["w"] / 2
                drag_start_y = drag_boxes["source"]["y"] + drag_boxes["source"]["h"] / 2
                drag_target_x = drag_boxes["target"]["x"] + drag_boxes["target"]["w"] * 0.30
                drag_target_y = drag_boxes["target"]["y"] + drag_boxes["target"]["h"] / 2
                page.mouse.move(drag_start_x, drag_start_y)
                start_hit = page.evaluate("""coords => {
                    const source = document.querySelector('#editor-timeline .ed-layer[data-scene="S01"][data-idx="0"]');
                    const hit = document.elementFromPoint(coords.x, coords.y);
                    return {sourceConnected: !!source && source.isConnected,
                            hit: hit ? hit.tagName + '.' + String(hit.className || '') : null,
                            hitIsSource: !!source && !!hit && (hit === source || source.contains(hit))};
                }""", {"x": drag_start_x, "y": drag_start_y})
                assert drag_boxes["startHitIsSource"] and start_hit["hitIsSource"], (
                    f"drag must start on the source clip; geometry={drag_boxes}, hit={start_hit}"
                )
                page.mouse.down()
                page.mouse.move(drag_target_x, drag_target_y, steps=8)
                page.mouse.up()
                page.wait_for_timeout(100)
                drag_command = next(
                    (command for command in captured_commands
                     if command.get("command") == "promote_layer_to_visual_track"),
                    None,
                )
                drag_debug = page.evaluate("window.__mainClipDragEvents")
                assert drag_command is not None, (
                    "dropping a main-video clip onto a track did not dispatch promotion; "
                    f"pointer trace={json.dumps(drag_debug, ensure_ascii=False)}"
                )
                assert drag_command["targetId"] == "S01#0"
                assert drag_command["payload"]["trackId"] == "overlay-main"
                assert abs(float(drag_command["payload"]["start"]) - 6.0) < 0.2, drag_command["payload"]
                captured_commands.clear()

                page.evaluate("""() => {
                    ED.comp.duration = 10;
                    if (ED.comp.project) ED.comp.project.duration = 10;
                    ED.comp.scenes[0].layers[0] = {
                        type: 'image', asset_id: 'img', start: 0, duration: 10
                    };
                    SEL.selection = {type: 'project'};
                    SEL.selectionSet = new Set();
                    editorRenderTimeline();
                }""")

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

                # Real pointer drag into another named audio lane must dispatch a versioned
                # command while preserving the semantic mix role and source interval.
                captured_commands.clear()
                source_clip = page.locator('#editor-timeline .ed-audio-clip[data-audio-index="0"]')
                target_row = page.locator('#editor-timeline .ed-track[data-audio-track-id="audio-sfx"]')
                source_clip.scroll_into_view_if_needed()
                target_row.scroll_into_view_if_needed()
                source_box = source_clip.bounding_box()
                target_box = target_row.locator('.ed-track-body').bounding_box()
                assert source_box and target_box
                assert 0 <= source_box["y"] and source_box["y"] + source_box["height"] <= viewport_height
                assert 0 <= target_box["y"] and target_box["y"] + target_box["height"] <= viewport_height
                source_x = source_box["x"] + source_box["width"] / 2
                source_y = source_box["y"] + source_box["height"] / 2
                target_y = target_box["y"] + target_box["height"] / 2
                page.mouse.move(source_x, source_y)
                page.mouse.down()
                page.mouse.move(source_x + 12, target_y, steps=5)
                page.mouse.up()
                page.wait_for_timeout(100)
                lane_move = next((command for command in captured_commands if command.get("command") == "move_audio_clip"), None)
                assert lane_move is not None, "dropping audio on another lane must dispatch move_audio_clip"
                assert lane_move["targetId"] == "audio#0"
                assert lane_move["payload"].get("trackId") == "audio-sfx", lane_move

                captured_commands.clear()
                page.locator('#editor-timeline .ed-audio-track-add').first.click()
                page.wait_for_timeout(100)
                add_lane = next((command for command in captured_commands if command.get("command") == "add_audio_track"), None)
                assert add_lane is not None, "＋音轨 must dispatch add_audio_track"
                assert add_lane["targetId"] == "audio-tracks"
                assert add_lane["payload"]["name"].startswith("音轨 ")

                # Real browser click: audio inspector's cut-at-playhead control must
                # dispatch the same versioned command as the backend unit contract.
                split_ui = page.evaluate("""() => {
                    PLAYHEAD.at = 7.5;
                    editorAudioClipOpen(1);
                    const button = document.getElementById('audio-clip-split-at-playhead');
                    return {
                        dialog: !!document.getElementById('audio-clip-inspector-mask'),
                        exists: !!button,
                        disabled: !!button && button.disabled,
                        label: button ? button.textContent.trim() : ''
                    };
                }""")
                assert split_ui["dialog"] and split_ui["exists"] and not split_ui["disabled"], split_ui
                captured_commands.clear()
                page.locator("#audio-clip-split-at-playhead").click()
                page.wait_for_timeout(100)
                split_command = next(
                    (command for command in captured_commands if command.get("command") == "split_audio_clip"),
                    None,
                )
                assert split_command is not None, "audio Inspector split button did not dispatch split_audio_clip"
                assert split_command["targetId"] == "audio#1"
                assert abs(float(split_command["payload"]["at"]) - 7.5) < 0.001
                page.evaluate("editorAudioClipClose()")
                captured_commands.clear()

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
                    ED.comp.visual_tracks = [
                        {id:'overlay-main', name:'画面叠加', order:0},
                        {id:'vtrack-ui-smoke', name:'证据画面', order:1}
                    ];
                    ED.comp.visual_overlays = [{
                        id: 'overlay-ui-smoke', asset_id: 'img', track_id:'overlay-main',
                        start: 2, duration: 4, source_in: 0, x: 0.65, y: 0.1,
                        width: 0.3, height: 0.3, opacity: 1, z_index: 0
                    }];
                    ED.comp.assets.img.type = 'image';
                    ED.comp.assets.img.thumbnail_url = '/overlay-smoke-thumb.svg';
                    PLAYHEAD.at = 4;
                    editorRenderTimeline();
                    const clip = document.querySelector('#editor-timeline .ed-visual-overlay-clip');
                    return {
                        count: document.querySelectorAll('#editor-timeline .ed-visual-overlay-clip').length,
                        trackRows: document.querySelectorAll('#editor-timeline .ed-track[data-visual-track-id]').length,
                        defaultTrack: clip && clip.closest('.ed-track').dataset.visualTrackId,
                        thumbnail: clip && clip.querySelector('img.ed-clip-thumb')?.getAttribute('src'),
                        label: clip && clip.querySelector('.ed-track-clip-label')?.textContent
                    };
                }""")
                assert overlay_ui["count"] == 1
                assert overlay_ui["thumbnail"] == "/overlay-smoke-thumb.svg"
                assert overlay_ui["label"] == "Still"
                assert overlay_ui["trackRows"] == 2
                assert overlay_ui["defaultTrack"] == "overlay-main"

                # Live preview and FFmpeg export must agree on visual-track stacking.
                # A lower track with a high local z_index must still sit below a higher track.
                preview_order = page.evaluate("""() => {
                    const stage = document.getElementById('ed-preview-stage');
                    if (!stage) return {available:false};
                    let media = stage.querySelector('.pv-live-media');
                    let createdMedia = false;
                    if (!media) {
                        media = document.createElement('div');
                        media.className = 'pv-live-media';
                        media.style.width = '400px';
                        media.style.height = '220px';
                        stage.appendChild(media);
                        createdMedia = true;
                    }
                    const saved = {
                        tracks: ED.comp.visual_tracks, overlays: ED.comp.visual_overlays,
                        mode: PV.mode, fileUrl: ED.comp.assets.img.file_url
                    };
                    try {
                        ED.comp.assets.img.type = 'image';
                        ED.comp.assets.img.file_url = '/overlay-smoke-thumb.svg';
                        PV.mode = 'live';
                        ED.comp.visual_tracks = [
                            {id:'overlay-main', name:'底层', order:0},
                            {id:'vtrack-ui-smoke', name:'上层', order:1}
                        ];
                        ED.comp.visual_overlays = [
                            {id:'bottom-high-z',asset_id:'img',track_id:'overlay-main',start:2,duration:4,
                             source_in:0,x:0.1,y:0.1,width:0.3,height:0.3,opacity:1,z_index:100},
                            {id:'top-low-z',asset_id:'img',track_id:'vtrack-ui-smoke',start:2,duration:4,
                             source_in:0,x:0.1,y:0.1,width:0.3,height:0.3,opacity:1,z_index:-100}
                        ];
                        _previewRenderVisualOverlays(3);
                        const read = () => [...media.querySelectorAll('[data-overlay-index]')].map(node => ({
                            index:Number(node.dataset.overlayIndex), z:Number(node.style.zIndex)
                        }));
                        const initial = read();
                        ED.comp.visual_tracks = [
                            {id:'overlay-main', name:'底层', order:1},
                            {id:'vtrack-ui-smoke', name:'上层', order:0}
                        ];
                        _previewRenderVisualOverlays(3);
                        const reordered = read();
                        return {available:true,initial,reordered};
                    } finally {
                        ED.comp.visual_tracks = saved.tracks;
                        ED.comp.visual_overlays = saved.overlays;
                        ED.comp.assets.img.file_url = saved.fileUrl;
                        PV.mode = saved.mode;
                        const host = media.querySelector('.pv-live-overlays');
                        if (host) host.remove();
                        if (createdMedia) media.remove();
                    }
                }""")
                assert preview_order["available"] is True
                assert [c["index"] for c in preview_order["initial"]] == [0, 1]
                assert preview_order["initial"][1]["z"] > preview_order["initial"][0]["z"]
                assert [c["index"] for c in preview_order["reordered"]] == [1, 0]
                assert preview_order["reordered"][1]["z"] > preview_order["reordered"][0]["z"]

                page.route(
                    "**/overlay-smoke-thumb.svg",
                    lambda route: route.fulfill(
                        status=200, content_type="image/svg+xml",
                        body='<svg xmlns="http://www.w3.org/2000/svg" width="40" height="24"><rect width="40" height="24" fill="#284b63"/></svg>',
                    ),
                )
                create_track_button = page.locator('#editor-timeline .ed-track[data-visual-track-id="overlay-main"] button[title="新增画面轨道"]')
                assert create_track_button.count() == 1
                captured_commands.clear()
                create_track_button.click()
                create_mask = page.locator("#visual-track-create-mask")
                create_mask.wait_for(timeout=3000)
                create_mask.get_by_role("button", name="创建轨道").click()
                page.wait_for_timeout(100)
                add_track_cmd = next((c for c in captured_commands if c.get("command") == "add_visual_track"), None)
                assert add_track_cmd is not None, "create track UI did not POST add_visual_track"
                assert add_track_cmd["targetId"] == "visual-tracks"
                assert add_track_cmd["payload"]["name"] == "画面轨 3"
                assert create_mask.count() == 1  # rejected response must not pretend that the track was created
                create_mask.get_by_role("button", name="关闭").click()

                main_track_row = page.locator('#editor-timeline .ed-track[data-visual-track-id="overlay-main"]')
                main_track_row.locator('button[title="轨道名称、顺序与删除"]').click()
                track_menu = page.locator("#visual-track-menu-mask")
                track_menu.wait_for(timeout=3000)
                captured_commands.clear()
                track_menu.get_by_role("button", name="上移一层").click()
                page.wait_for_timeout(100)
                reorder_cmd = next((c for c in captured_commands if c.get("command") == "reorder_visual_track"), None)
                assert reorder_cmd is not None, "track menu did not POST reorder_visual_track"
                assert reorder_cmd["targetId"] == "overlay-main"
                assert reorder_cmd["payload"]["toIndex"] == 1

                source_clip = page.locator('#editor-timeline .ed-track[data-visual-track-id="overlay-main"] .ed-visual-overlay-clip')
                target_track = page.locator('#editor-timeline .ed-track[data-visual-track-id="vtrack-ui-smoke"] .ed-track-body')
                source_box = source_clip.bounding_box()
                target_box = target_track.bounding_box()
                assert source_box and target_box
                # Previous watermark gestures are a separate interaction; establish an
                # explicit starting playhead so this test detects only clip-drag bubbling.
                page.evaluate("PLAYHEAD.at = 4.0")
                captured_commands.clear()
                page.mouse.move(source_box["x"] + source_box["width"] * 0.55, source_box["y"] + source_box["height"] / 2)
                page.mouse.down()
                page.mouse.move(source_box["x"] + source_box["width"] * 0.55, target_box["y"] + target_box["height"] / 2, steps=5)
                page.mouse.up()
                page.wait_for_timeout(100)
                cross_track_cmd = next((c for c in captured_commands if c.get("command") == "move_visual_overlay"), None)
                assert cross_track_cmd is not None, "dragging a visual clip to another track did not POST move_visual_overlay"
                assert cross_track_cmd["payload"]["trackId"] == "vtrack-ui-smoke"
                # Dragging a clip must not bubble into the timeline scrub handler.
                assert page.evaluate("PLAYHEAD.at") == pytest.approx(4.0)
                # The test endpoint rejects edits, so the original track remains the source of truth.
                assert page.locator('#editor-timeline .ed-track[data-visual-track-id="overlay-main"] .ed-visual-overlay-clip').count() == 1

                overlay_clip = page.locator("#editor-timeline .ed-track[data-visual-track-id='overlay-main'] .ed-visual-overlay-clip")
                overlay_clip.scroll_into_view_if_needed()
                overlay_clip.click()
                page.wait_for_function("() => SEL.selection && SEL.selection.type === 'visual_overlay' && SEL.selection.id === 'overlay-ui-smoke'")
                inspector = page.locator("#visual-overlay-inspector-mask")
                assert inspector.count() == 0, "Selecting a timeline clip should not open a blocking inspector mask"
                page.locator('button[data-action="ui.edit_visual_overlay"]').click()
                inspector.wait_for(state="visible", timeout=5000)
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

                # Authority routing must fail closed when film.html exists or cannot be checked;
                # only a positive exists:false response may enter the legacy CompositionIR editor.
                authority_routing = page.evaluate("""async () => {
                    const selector = document.getElementById('editor-project');
                    const pid = 'workspace-authority-route-fixture';
                    const previousValue = selector.value;
                    let option = [...selector.options].find(item => item.value === pid);
                    const addedOption = !option;
                    if (!option) { option = new Option(pid, pid); selector.add(option); }
                    const original = {
                        apiGet: window.apiGet,
                        editorInit: window.editorInit,
                        editorLoad: window.editorLoad,
                        directorLoadLatestRun: window.directorLoadLatestRun,
                        filmOpen: window.filmStudioOpenIfCurrentProjectHasFilm,
                        prepareLegacy: window.filmStudioPrepareLegacyProjectOpen,
                        editorToast: window.editorToast,
                    };
                    const counters = {legacyLoad: 0, filmOpen: 0, prepareLegacy: 0, messages: []};
                    let filmReply = {ok:true, exists:true, rev:'fixture-rev'};
                    window.apiGet = async url => {
                        if (String(url).startsWith('/api/editor/film?')) return filmReply;
                        if (String(url) === '/api/actions') return {ok:true, build_id:'workspace-route-smoke'};
                        return {ok:true};
                    };
                    window.editorInit = async () => ({ok:true});
                    window.editorLoad = async () => { counters.legacyLoad += 1; return {ok:true}; };
                    window.directorLoadLatestRun = async () => ({ok:true});
                    window.filmStudioOpenIfCurrentProjectHasFilm = async () => { counters.filmOpen += 1; return false; };
                    window.filmStudioPrepareLegacyProjectOpen = () => { counters.prepareLegacy += 1; return true; };
                    window.editorToast = message => counters.messages.push(String(message));
                    const snapshot = async () => {
                        const result = await window.workspaceOpen(pid);
                        return {result,legacyLoad:counters.legacyLoad,filmOpen:counters.filmOpen,
                            prepareLegacy:counters.prepareLegacy,message:counters.messages.at(-1)||''};
                    };
                    try {
                        selector.value = pid;
                        const existingFilm = await snapshot();
                        filmReply = {ok:false,error:'temporary preflight failure'};
                        const unknownAuthority = await snapshot();
                        filmReply = {ok:true,exists:false};
                        const confirmedLegacy = await snapshot();
                        return {existingFilm,unknownAuthority,confirmedLegacy};
                    } finally {
                        window.apiGet = original.apiGet;
                        window.editorInit = original.editorInit;
                        window.editorLoad = original.editorLoad;
                        window.directorLoadLatestRun = original.directorLoadLatestRun;
                        window.filmStudioOpenIfCurrentProjectHasFilm = original.filmOpen;
                        window.filmStudioPrepareLegacyProjectOpen = original.prepareLegacy;
                        window.editorToast = original.editorToast;
                        selector.value = previousValue;
                        if (addedOption) option.remove();
                    }
                }""")
                assert authority_routing["existingFilm"]["result"]["ok"] is False, authority_routing
                assert authority_routing["existingFilm"]["result"]["mode"] == "film", authority_routing
                assert authority_routing["existingFilm"]["legacyLoad"] == 0, authority_routing
                assert "未回退" in authority_routing["existingFilm"]["message"], authority_routing
                assert authority_routing["unknownAuthority"]["result"]["ok"] is False, authority_routing
                assert authority_routing["unknownAuthority"]["result"]["mode"] == "unknown", authority_routing
                assert authority_routing["unknownAuthority"]["legacyLoad"] == 0, authority_routing
                assert authority_routing["confirmedLegacy"]["result"]["ok"] is True, authority_routing
                assert authority_routing["confirmedLegacy"]["legacyLoad"] == 1, authority_routing

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

