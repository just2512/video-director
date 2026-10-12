from pathlib import Path

from playwright.sync_api import sync_playwright


def test_source_monitor_marks_source_range_and_places_it_on_selected_unlocked_track():
    """Source monitor → mark in/out → target track → playhead placement is one UI flow."""
    page_html = """
    <div class="ed-monitor-grid">
      <select id="ed-source-asset" onchange="editorSourceMonitorSelectAsset()"><option value="">Loading</option></select>
      <div class="ed-source-stage" id="ed-source-stage"></div>
      <span id="ed-source-meta"></span>
      <button id="ed-source-mark-in" onclick="editorSourceMonitorMarkIn()">Mark In</button>
      <input id="ed-source-in" type="number" value="0">
      <button id="ed-source-mark-out" onclick="editorSourceMonitorMarkOut()">Mark Out</button>
      <input id="ed-source-out" type="number" value="3">
      <select id="ed-source-target-track"></select>
      <select id="ed-source-presentation"><option value="full_frame">Full frame</option><option value="sticker">Sticker</option></select>
      <div id="ed-source-audio-row"><input id="ed-source-audio-enabled" type="checkbox"></div>
      <button id="ed-source-add" onclick="editorSourceMonitorAddToTimeline()">Place at playhead</button>
      <span id="ed-source-status"></span>
    </div>
    <script>
      window.ED = {pid:'test-project', version:1, comp:{
        duration:10, fps:24,
        visual_tracks:[
          {id:'visual-top',name:'Top',order:0,locked:false},
          {id:'visual-locked',name:'Locked',order:1,locked:true}
        ]
      }};
      window.PLAYHEAD = {at:4};
      window.PV = {playhead:4};
      window.apiGet = async () => [{
        asset_id:'source-video', name:'Source video', type:'video',
        file:'/storage/assets/source-video.mp4', duration:12, has_audio:true
      }];
      window.executeCommand = async spec => {
        window.__command = spec;
        return {ok:true,status:'APPLIED'};
      };
      window.refreshWorkspace = async () => {};
      window.editorToast = value => { window.__toast = value; };
    </script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(page_html)
        page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / "webui/static/js/source-monitor.js"))
        page.wait_for_function("document.querySelector('#ed-source-asset option[value=source-video]') !== null")
        page.locator("#ed-source-asset").select_option("source-video")
        page.wait_for_function("document.querySelector('#ed-source-video') !== null")

        # Stub only the native currentTime property. All UI event handlers and
        # timeline command construction run as real Chromium JavaScript.
        page.evaluate("Object.defineProperty(document.querySelector('#ed-source-video'), 'currentTime', {configurable:true, value:1.25})")
        page.locator("#ed-source-mark-in").click()
        assert page.locator("#ed-source-in").input_value() == "1.250"

        page.evaluate("Object.defineProperty(document.querySelector('#ed-source-video'), 'currentTime', {configurable:true, value:3.75})")
        page.locator("#ed-source-mark-out").click()
        assert page.locator("#ed-source-out").input_value() == "3.750"
        assert page.locator("#ed-source-target-track option[value=visual-locked]").is_disabled()

        page.locator("#ed-source-target-track").select_option("visual-top")
        page.locator("#ed-source-presentation").select_option("sticker")
        page.locator("#ed-source-audio-enabled").check()
        page.locator("#ed-source-add").click()
        page.wait_for_function("window.__command !== undefined")
        result = page.evaluate("window.__command")
        assert result["command"] == "add_visual_overlay"
        assert result["targetId"] == "overlays"
        assert result["payload"]["assetId"] == "source-video"
        assert result["payload"]["start"] == 4
        assert result["payload"]["sourceIn"] == 1.25
        assert result["payload"]["duration"] == 2.5
        assert result["payload"]["trackId"] == "visual-top"
        assert result["payload"]["presentationMode"] == "sticker"
        assert result["payload"]["sourceAudioEnabled"] is True

        # An invalid interval must not dispatch another edit command.
        page.locator("#ed-source-in").fill("4")
        page.locator("#ed-source-out").fill("4")
        page.locator("#ed-source-add").click()
        assert page.evaluate("window.__command.payload.duration") == 2.5
        browser.close()
