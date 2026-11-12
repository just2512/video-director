"""Browser-level contract for the source-audio detach action in the existing clip inspector."""
from pathlib import Path

from playwright.sync_api import sync_playwright


def test_source_audio_detach_button_dispatches_editor_command_and_refreshes_workspace():
    root = Path(__file__).resolve().parents[1]
    script = root / "webui" / "static" / "js" / "visual-overlay-editor.js"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content("<!doctype html><html><body></body></html>")
            page.evaluate("""() => {
                window.ED = {comp: {
                    assets: [{id: "source-video", type: "video", name: "Source video", duration: 2}],
                    visual_overlays: [{
                        id: "overlay-source", asset_id: "source-video", start: 0.5, duration: 1,
                        source_in: 0.25, source_audio_enabled: true, source_audio_volume: 0.7,
                        presentation_mode: "full_frame", x: 0, y: 0, width: 1, height: 1,
                        opacity: 1, z_index: 0, track_id: "overlay-main"
                    }]
                }};
                window.esc = value => String(value ?? "").replace(/[&<>"]/g, char =>
                    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[char]));
                window.editorToast = (message, isError) => { window.__lastToast = {message, isError}; };
                window.executeCommand = async command => { window.__command = command; return {ok: true}; };
                window.refreshWorkspace = async () => { window.__refreshCount = (window.__refreshCount || 0) + 1; };
                window.PLAYHEAD = {at: 0.5};
            }""")
            page.add_script_tag(path=str(script))
            page.evaluate("editorVisualOverlayOpen(0)")
            button = page.get_by_role("button", name="拆分为独立原声轨")
            assert button.is_visible()
            button.click()
            page.wait_for_function("window.__command && window.__refreshCount === 1")
            assert page.evaluate("window.__command") == {
                "command": "separate_visual_overlay_source_audio",
                "targetId": "overlay#0",
                "payload": {},
            }
            assert page.locator("#visual-overlay-inspector-mask").count() == 0
            assert "可独立移动、修剪和混音" in page.evaluate("window.__lastToast.message")
        finally:
            browser.close()


def test_speeded_video_overlay_drag_uses_source_time_mapping():
    root = Path(__file__).resolve().parents[1]
    math_script = root / "webui" / "static" / "js" / "timeline-edit-math.js"
    overlay_script = root / "webui" / "static" / "js" / "visual-overlay-editor.js"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 180})
            page.set_content("""<!doctype html><html><head><style>
                html, body { margin: 0; padding: 0; }
                #editor-timeline { width: 1000px; height: 80px; }
                .ed-track-body { position: relative; width: 1000px; height: 30px; }
                .ed-visual-overlay-clip { position: absolute; top: 0; height: 24px; }
            </style></head><body><div id="editor-timeline">
                <div class="ed-track" data-visual-track-id="overlay-main">
                    <div class="ed-track-body">
                        <div id="speeded-clip" class="ed-visual-overlay-clip"
                             data-visual-overlay-index="0" style="left:50%;width:5%"></div>
                    </div>
                </div>
            </div></body></html>""")
            page.evaluate("""() => {
                window.ED = {comp: {
                    duration: 10,
                    assets: [{id: "source-video", type: "video", duration: 2}],
                    visual_tracks: [{id: "overlay-main", locked: false}],
                    visual_overlays: [{
                        id: "speeded-overlay", asset_id: "source-video", start: 5, duration: 0.5,
                        source_in: 0.25, speed: 2, track_id: "overlay-main"
                    }]
                }};
                window.__commands = [];
                window.__refreshCount = 0;
                window.executeCommand = async command => {
                    window.__commands.push(command);
                    return {ok: true};
                };
                window.editorToast = () => {};
                window.refreshWorkspace = async () => { window.__refreshCount += 1; };
                window.editorRenderTimeline = () => {};
            }""")
            page.add_script_tag(path=str(math_script))
            page.add_script_tag(path=str(overlay_script))
            page.evaluate("_tlBindVisualOverlayDrag()")

            # Left trim by 0.2 timeline seconds must advance source time by 0.4s at 2x.
            page.mouse.move(502, 10)
            page.mouse.down()
            page.mouse.move(522, 10)
            page.mouse.up()
            page.wait_for_function("window.__commands.length === 1")
            first = page.evaluate("window.__commands[0]")
            assert first["command"] == "trim_visual_overlay", first
            assert first["payload"] == {"start": 5.2, "sourceIn": 0.65, "duration": 0.3}, first

            # Right trim must stop at the source-media boundary: (2.0 - 0.25) / 2x = 0.875s.
            page.evaluate("""() => {
                const clip = document.getElementById("speeded-clip");
                clip.style.left = "50%";
                clip.style.width = "5%";
            }""")
            page.mouse.move(548, 10)
            page.mouse.down()
            page.mouse.move(648, 10)
            page.mouse.up()
            page.wait_for_function("window.__commands.length === 2")
            second = page.evaluate("window.__commands[1]")
            assert second["command"] == "trim_visual_overlay", second
            assert second["payload"] == {"start": 5, "sourceIn": 0.25, "duration": 0.875}, second
        finally:
            browser.close()



def test_visual_overlay_create_exposes_overlay_overwrite_and_insert_in_chromium():
    root = Path(__file__).resolve().parents[1]
    script = root / "webui" / "static" / "js" / "visual-overlay-editor.js"
    cases = (
        ("overlay", 2.0, 2.0),
        ("overwrite", 2.0, 2.0),
        # Insert is allowed to cross the old project end; the server extends it.
        ("insert", 8.0, 4.0),
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for placement_mode, start, duration in cases:
                page = browser.new_page()
                try:
                    page.set_content("<!doctype html><html><body></body></html>")
                    page.evaluate("""() => {
                        window.ED = {comp: {
                            duration: 10,
                            assets: [{id: "source-video", type: "video", name: "Source video", duration: 6}],
                            visual_overlays: []
                        }};
                        window.PLAYHEAD = {at: 2};
                        window.__commands = [];
                        window.esc = value => String(value ?? "").replace(/[&<>"]/g, char =>
                            ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[char]));
                        window.editorToast = (message, isError) => { window.__lastToast = {message, isError}; };
                        window.executeCommand = async command => {
                            window.__commands.push(command);
                            return {ok: true};
                        };
                        window.refreshWorkspace = async () => {};
                    }""")
                    page.add_script_tag(path=str(script))
                    page.evaluate("editorVisualOverlayPick(null, 'overlay-main')")

                    placement = page.locator("#visual-overlay-placement-mode")
                    assert placement.locator("option").count() == 3
                    placement.select_option(placement_mode)
                    page.locator("#visual-overlay-start").fill(str(start))
                    page.locator("#visual-overlay-start").dispatch_event("change")
                    page.locator("#visual-overlay-duration").fill(str(duration))
                    page.get_by_role("button", name="添加到时间线").click()
                    page.wait_for_function("window.__commands.length === 1")

                    command = page.evaluate("window.__commands[0]")
                    assert command["command"] == "add_visual_overlay", command
                    payload = command["payload"]
                    assert payload["placementMode"] == placement_mode, payload
                    assert payload["start"] == start, payload
                    assert payload["duration"] == duration, payload
                    assert payload["trackId"] == "overlay-main", payload
                    assert page.locator("#visual-overlay-inspector-mask").count() == 0
                    if placement_mode == "insert":
                        assert "时间线已延长" in page.evaluate("window.__lastToast.message")
                finally:
                    page.close()
        finally:
            browser.close()

def test_locked_visual_overlay_track_rejects_pointer_drag_without_dispatching_command():
    root = Path(__file__).resolve().parents[1]
    math_script = root / "webui" / "static" / "js" / "timeline-edit-math.js"
    overlay_script = root / "webui" / "static" / "js" / "visual-overlay-editor.js"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 180})
            page.set_content("""<!doctype html><html><head><style>
                html, body { margin: 0; padding: 0; }
                #editor-timeline { width: 1000px; height: 80px; }
                .ed-track-body { position: relative; width: 1000px; height: 30px; }
                .ed-visual-overlay-clip { position: absolute; top: 0; height: 24px; }
            </style></head><body><div id="editor-timeline">
                <div class="ed-track" data-visual-track-id="overlay-locked">
                    <div class="ed-track-body">
                        <div id="locked-clip" class="ed-visual-overlay-clip"
                             data-visual-overlay-index="0" style="left:20%;width:20%"></div>
                    </div>
                </div>
            </div></body></html>""")
            page.evaluate("""() => {
                window.ED = {comp: {
                    duration: 10,
                    assets: [{id: "still", type: "image", duration: 10}],
                    visual_tracks: [{id: "overlay-locked", locked: true}],
                    visual_overlays: [{
                        id: "locked-overlay", asset_id: "still", start: 2, duration: 2,
                        source_in: 0, track_id: "overlay-locked"
                    }]
                }};
                window.__commands = [];
                window.executeCommand = async command => {
                    window.__commands.push(command);
                    return {ok: true};
                };
                window.editorToast = (message, isError) => { window.__toast = {message, isError}; };
                window.refreshWorkspace = async () => {};
                window.editorRenderTimeline = () => {};
            }""")
            page.add_script_tag(path=str(math_script))
            page.add_script_tag(path=str(overlay_script))
            page.evaluate("_tlBindVisualOverlayDrag()")
            page.mouse.move(300, 10)
            page.mouse.down()
            page.mouse.move(420, 10)
            page.mouse.up()
            assert page.evaluate("window.__commands") == []
            assert page.evaluate("window.__toast") == {
                "message": "画面轨道已锁定，先解锁后才能移动或修剪片段。",
                "isError": True,
            }
        finally:
            browser.close()
