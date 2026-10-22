"""Real-browser regression for the OpenFilm-inspired Film Studio roll-trim gesture."""
from __future__ import annotations

import importlib
import os
import subprocess
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

from video_director import film_document as fd


SAMPLE = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"><style>body{margin:0}</style></head>
<body>
<section id="video-main" data-label="主画面">
<video id="clip-a" src="../assets/a.mp4#t=2,12" at="0" speed="2" volume="1" data-film-keyframes='[{"property":"opacity","keyframes":[{"at":0,"value":0,"easing":"linear"},{"at":1,"value":1,"easing":"ease_out"}]}]'></video>
<video id="clip-b" src="../assets/b.mp4#t=0,5" at="5"></video>
</section>
<section id="audio-bed" data-label="BGM">
<audio id="audio-1" src="../assets/music.wav#t=0,20" at="0" volume="0.2"></audio>
</section>
</body></html>'''


def test_film_studio_ctrl_drag_roll_trim_updates_persisted_timeline(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    # Use a real source file so the server-side Slip duration guard is exercised.
    asset_root = storage_root / "assets"
    asset_root.mkdir(parents=True, exist_ok=True)
    from video_director import renderer
    generated = subprocess.run(
        [renderer._ffmpeg(), "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=20",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", "-y",
         str(asset_root / "a.mp4")],
        capture_output=True, text=True, timeout=60,
    )
    assert generated.returncode == 0, generated.stderr

    project_id = "film-studio-roll-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    assert fd.read_project_film(project_id)["exists"]

    results_dir = Path(os.environ.get("VIDEO_DIRECTOR_TEST_RESULTS", str(tmp_path / "test-results")))
    results_dir.mkdir(parents=True, exist_ok=True)
    trace_path = results_dir / "film-studio-roll-trim-trace.zip"

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            tracing = False
            try:
                context.tracing.start(screenshots=True, snapshots=True, sources=True)
                tracing = True
                page = context.new_page()
                page_errors: list[str] = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded"
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.filmStudioToggle === 'function'")
                # The workspace Studio is mounted inside the editor tab; opening it
                # while the home tab is display:none correctly leaves it hidden.
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                # editorInit() repopulates the select asynchronously; wait before
                # inserting the test-only fixture project to avoid a lost selection.
                page.wait_for_function(
                    "() => { const select = document.querySelector('#editor-project'); return !!select && select.options.length > 0; }"
                )
                page.evaluate("""(pid) => {
                  const select = document.querySelector('#editor-project');
                  if (!select) throw new Error('project selector is missing');
                  let option = [...select.options].find(item => item.value === pid);
                  if (!option) { option = new Option(pid, pid); select.add(option); }
                  select.value = pid;
                }""", project_id)
                page.evaluate("window.filmStudioToggle()")
                page.locator('#film-studio-panel.fs-studio-expanded').wait_for(state="visible")
                # Attribute inspection and AI direction must be visible at the same
                # time; resizing their divider must persist the user-selected split.
                inspector = page.locator("#fs-side-inspector")
                ai_panel = page.locator("#fs-side-ai")
                side_divider = page.locator("#fs-side-divider")
                inspector.wait_for(state="visible")
                ai_panel.wait_for(state="visible")
                before_inspector_height = inspector.bounding_box()["height"]
                side_box = side_divider.bounding_box()
                assert side_box and before_inspector_height > 0
                page.mouse.move(side_box["x"] + side_box["width"] / 2,
                                side_box["y"] + side_box["height"] / 2)
                page.mouse.down()
                page.mouse.move(side_box["x"] + side_box["width"] / 2,
                                side_box["y"] + side_box["height"] / 2 + 28,
                                steps=4)
                page.mouse.up()
                after_inspector_height = inspector.bounding_box()["height"]
                assert after_inspector_height >= before_inspector_height + 12, "Inspector/AI seam should resize the inspector"
                saved_share = page.evaluate("localStorage.getItem('film-studio-inspector-share')")
                assert saved_share and float(saved_share) > 54, "Inspector split should persist the changed share"
                assert inspector.is_visible() and ai_panel.is_visible(), "AI and Inspector must not hide each other"

                # Exercise the actual horizontal dock seam, not only its DOM presence.
                timeline_pane = page.locator('.fs-timeline-pane')
                divider = page.locator('#fs-timeline-divider')
                divider.wait_for(state="visible")
                before_height = timeline_pane.bounding_box()["height"]
                divider_box = divider.bounding_box()
                assert divider_box and before_height > 0
                page.mouse.move(divider_box["x"] + divider_box["width"] / 2,
                                divider_box["y"] + divider_box["height"] / 2)
                page.mouse.down()
                page.mouse.move(divider_box["x"] + divider_box["width"] / 2,
                                divider_box["y"] + divider_box["height"] / 2 - 64,
                                steps=4)
                page.mouse.up()
                after_height = timeline_pane.bounding_box()["height"]
                assert after_height >= before_height + 30, "timeline seam should resize the timeline panel"
                saved_height = page.evaluate("localStorage.getItem('film-studio-timeline-height')")
                assert saved_height and abs(float(saved_height) - after_height) < 2
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').click()
                page.evaluate("window.filmStudioSeek(2)")
                page.locator('[data-film-action="split"]:visible').click()
                page.wait_for_function(
                    "() => [...document.querySelectorAll('#fs-timeline .fs-clip')].some(el => el.dataset.clip.startsWith('clip-a-split-'))",
                    timeout=10000,
                )
                right_id = page.evaluate("""() => {
                  const item = [...document.querySelectorAll('#fs-timeline .fs-clip')]
                    .find(el => el.dataset.clip.startsWith('clip-a-split-'));
                  return item && item.dataset.clip;
                }""")
                assert right_id, "split should create the right-hand clip on the actual timeline"

                handle = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"] [data-trim="end"]')
                handle.scroll_into_view_if_needed()
                bounds = handle.bounding_box()
                assert bounds and bounds["width"] > 0 and bounds["height"] > 0
                x = bounds["x"] + bounds["width"] / 2
                y = bounds["y"] + bounds["height"] / 2
                page.keyboard.down("Control")
                try:
                    page.mouse.move(x, y)
                    page.mouse.down()
                    page.mouse.move(x + 18, y, steps=5)  # +0.5 s at 36 px/s
                    page.mouse.up()
                finally:
                    page.keyboard.up("Control")

                page.wait_for_function("""async (args) => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(args.pid));
                  if (!response.ok) return false;
                  const doc = await response.json();
                  const clips = (doc.tracks || []).flatMap(track => track.clips || []);
                  const left = clips.find(clip => clip.id === 'clip-a');
                  const right = clips.find(clip => clip.id === args.rightId);
                  return !!left && !!right
                    && String(left.attrs.src).endsWith('#t=2,7')
                    && String(right.attrs.src).endsWith('#t=7,12')
                    && Math.abs(Number(right.attrs.at) - 2.5) < 0.001;
                }""", arg={"pid": project_id, "rightId": right_id}, timeout=10000)

                persisted = fd.read_project_film(project_id)
                clips = {clip["id"]: clip for track in persisted["doc"]["tracks"] for clip in track["clips"]}
                assert clips["clip-a"]["attrs"]["src"].endswith("#t=2,7")
                assert clips[right_id]["attrs"]["src"].endswith("#t=7,12")
                assert float(clips[right_id]["attrs"]["at"]) == 2.5

                # Re-split the rolled right segment, then exercise Slide trim through
                # the actual clip-body modifier gesture (Alt+Shift drag).
                page.locator('#fs-timeline .fs-clip[data-clip="' + right_id + '"]').click()
                page.evaluate("window.filmStudioSeek(4)")
                page.keyboard.press("s")
                page.wait_for_function(
                    "(id) => [...document.querySelectorAll('#fs-timeline .fs-clip')].some(el => el.dataset.clip.startsWith(id + '-split-'))",
                    arg=right_id,
                    timeout=10000,
                )
                slide_right_id = page.evaluate("""(id) => {
                  const item = [...document.querySelectorAll('#fs-timeline .fs-clip')]
                    .find(el => el.dataset.clip.startsWith(id + '-split-'));
                  return item && item.dataset.clip;
                }""", right_id)
                assert slide_right_id, "second split should create a third source-contiguous segment"
                middle = page.locator('#fs-timeline .fs-clip[data-clip="' + right_id + '"]')
                box = middle.bounding_box()
                assert box and box["width"] >= 36 and box["height"] > 0
                x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                page.locator('[data-film-action="slide-mode"]:visible').click()
                assert page.locator('[data-film-action="slide-mode"]:visible').get_attribute("aria-pressed") == "true"
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + 9, y, steps=3)  # +0.25 s at 36 px/s
                page.mouse.up()

                page.wait_for_function("""async (args) => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(args.pid));
                  if (!response.ok) return false;
                  const doc = await response.json();
                  const clips = (doc.tracks || []).flatMap(track => track.clips || []);
                  const left = clips.find(clip => clip.id === 'clip-a');
                  const middle = clips.find(clip => clip.id === args.middleId);
                  const right = clips.find(clip => clip.id === args.rightId);
                  return !!left && !!middle && !!right
                    && String(left.attrs.src).endsWith('#t=2,7.5')
                    && String(middle.attrs.src).endsWith('#t=7,10')
                    && Math.abs(Number(middle.attrs.at) - 2.75) < 0.001
                    && String(right.attrs.src).endsWith('#t=10.5,12')
                    && Math.abs(Number(right.attrs.at) - 4.25) < 0.001;
                }""", arg={"pid": project_id, "middleId": right_id, "rightId": slide_right_id}, timeout=10000)

                page.locator('[data-film-action="slide-mode"]:visible').click()
                assert page.locator('[data-film-action="slide-mode"]:visible').get_attribute("aria-pressed") == "false"
                persisted = fd.read_project_film(project_id)
                clips = {clip["id"]: clip for track in persisted["doc"]["tracks"] for clip in track["clips"]}
                assert clips["clip-a"]["attrs"]["src"].endswith("#t=2,7.5")
                assert clips[right_id]["attrs"]["src"].endswith("#t=7,10")
                assert float(clips[right_id]["attrs"]["at"]) == 2.75
                assert clips[slide_right_id]["attrs"]["src"].endswith("#t=10.5,12")
                assert float(clips[slide_right_id]["attrs"]["at"]) == 4.25
                assert fd.clip_end(clips[slide_right_id]) == 5
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-panel')?.getAttribute('data-busy') !== 'true'",
                    timeout=15000,
                )

                # Slip the left clip without moving it or changing its timeline
                # duration. Use the explicit mode for this integration assertion;
                # Alt-drag remains a supported shortcut but is not required for discoverability.
                slip_mode = page.locator('[data-film-action="slip-mode"]:visible')
                slip_mode.click()
                assert slip_mode.get_attribute("aria-pressed") == "true"
                left_element = page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]')
                left_box = left_element.bounding_box()
                assert left_box and left_box["width"] > 40
                x, y = left_box["x"] + left_box["width"] / 2, left_box["y"] + left_box["height"] / 2
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + 9, y, steps=3)  # +0.25 timeline s × 2× speed = +0.5 source s
                page.mouse.up()

                page.wait_for_function("""async (pid) => {
                  const response = await fetch('/api/editor/film?project_id=' + encodeURIComponent(pid));
                  if (!response.ok) return false;
                  const doc = await response.json();
                  const clips = (doc.tracks || []).flatMap(track => track.clips || []);
                  const left = clips.find(clip => clip.id === 'clip-a');
                  return !!left && String(left.attrs.src).endsWith('#t=2.5,8')
                    && Math.abs(Number(left.attrs.at)) < 0.001
                    && Math.abs((Number(left.attrs.src.match(/#t=[^,]+,([^#]+)/)?.[1]) - Number(left.attrs.src.match(/#t=([^,]+)/)?.[1])) / Number(left.attrs.speed) - 2.75) < 0.001;
                }""", arg=project_id, timeout=10000)
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-panel')?.getAttribute('data-busy') === 'false'",
                    timeout=15000,
                )

                persisted = fd.read_project_film(project_id)
                clips = {clip["id"]: clip for track in persisted["doc"]["tracks"] for clip in track["clips"]}
                assert clips["clip-a"]["attrs"]["src"].endswith("#t=2.5,8")
                assert float(clips["clip-a"]["attrs"]["at"]) == 0
                assert fd.clip_duration(clips["clip-a"]) == 2.75

                # Same-project keyboard copy/paste must preserve the source range
                # and speed, allocate a new stable ID, and avoid track overlap.
                page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').click()
                keyframe_marker = page.locator('#fs-timeline [data-film-keyframe-marker="clip-a"][data-keyframe-at="1"]')
                keyframe_marker.wait_for(state="visible", timeout=5000)
                keyframe_marker.click()
                assert abs(float(page.locator("#fs-playhead").input_value()) - 1.0) < 0.001
                before_paste = fd.read_project_film(project_id)
                before_count = sum(len(track["clips"]) for track in before_paste["doc"]["tracks"])
                page.keyboard.press("Control+C")
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('已复制 1 个片段')",
                    timeout=5000,
                )
                page.keyboard.press("Control+V")
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('已粘贴 1 个片段')",
                    timeout=15000,
                )
                pasted = fd.read_project_film(project_id)
                assert sum(len(track["clips"]) for track in pasted["doc"]["tracks"]) == before_count + 1
                video_track = next(track for track in pasted["doc"]["tracks"] if track["id"] == "video-main")
                copies = [clip for clip in video_track["clips"]
                          if clip["id"] != "clip-a" and clip["attrs"].get("src", "").endswith("#t=2.5,8")]
                assert len(copies) == 1
                duplicate = copies[0]
                assert duplicate["attrs"]["speed"] == "2"
                assert fd.clip_duration(duplicate) == 2.75
                for existing in video_track["clips"]:
                    if existing["id"] == duplicate["id"]:
                        continue
                    assert not (float(duplicate["attrs"]["at"]) < fd.clip_end(existing) - 0.001
                                and fd.clip_end(duplicate) > float(existing["attrs"]["at"]) + 0.001)

                # The clip context menu must invoke the same undoable duplicate transaction.
                page.locator('#fs-timeline .fs-clip[data-clip="' + duplicate["id"] + '"]').click(button="right")
                page.locator('.fs-context-menu [data-context-action="duplicate"]').wait_for(state="visible", timeout=5000)
                page.locator('.fs-context-menu [data-context-action="duplicate"]').click()
                page.wait_for_function(
                    "() => document.querySelector('#film-studio-alert')?.textContent.includes('已粘贴 1 个片段')",
                    timeout=15000,
                )
                duplicated_again = fd.read_project_film(project_id)
                assert sum(len(track["clips"]) for track in duplicated_again["doc"]["tracks"]) == before_count + 2
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            except Exception:
                if tracing:
                    context.tracing.stop(path=str(trace_path))
                    tracing = False
                raise
            finally:
                if tracing:
                    context.tracing.stop()
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
