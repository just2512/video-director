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
