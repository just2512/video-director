"""Browser regression for Studio -> Shotbook -> Timeline, retaining failure evidence."""
from __future__ import annotations

import base64
import importlib
import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from video_director.models import (
    AssetRef, AudioLane, AudioTrack, AudioTrackType, CompositionIR, Layer, LayerType,
    ProjectSpec, SceneIR, VisualOverlayClip, VisualTrack, WatermarkLayer,
)
from video_director import editor, store, shotbook


def _test_composition(media_file: Path, *, include_extended_tracks: bool = False) -> CompositionIR:
    """Build the shared render fixture; browser-only lane metadata is opt-in.

    The native-style MP4 tests reuse this helper. Keep their default CompositionIR
    self-contained (every referenced asset must resolve) instead of leaking the
    browser-only placeholder audio ids into unrelated real-render tests.
    """
    layer1 = Layer(type=LayerType.image, asset_id="e2e-still", start=0, duration=1.0)
    layer2 = Layer(type=LayerType.image, asset_id="e2e-still", start=0, duration=1.0)
    recipes = shotbook._recipes()
    recipe_id = recipes[0]["id"] if recipes else None
    scenes = [
        SceneIR(id="S001", start=0, duration=1.0, text="第一镜用于工作台定位",
                layers=[layer1], origin_recipe_id=recipe_id),
        SceneIR(id="S002", start=1.0, duration=1.0, text="第二镜测试逐镜预览",
                layers=[layer2], origin_recipe_id=recipe_id),
    ]
    asset = AssetRef(
        id="e2e-still", type="image", file=str(media_file), width=32, height=32,
        source="uploaded", rights_status="CLEAR",
        provenance={"source": "Playwright regression fixture"},
    )
    timeline_extensions = {}
    if include_extended_tracks:
        timeline_extensions = {
            "audio_tracks": [
                AudioLane(id="audio-custom-music", name="自定义配乐", order=0),
                AudioLane(id="audio-custom-foley", name="细节音效", order=1),
            ],
            "audio": [
                AudioTrack(
                    id="e2e-music-clip", type=AudioTrackType.bgm,
                    track_id="audio-custom-music", asset_id="e2e-music",
                    start=0.2, duration=1.2, source_in=0.1, source_out=1.3,
                    volume=0.65, fadeIn=0.15, fadeOut=0.25,
                ),
                AudioTrack(
                    id="e2e-foley-clip", type=AudioTrackType.sfx,
                    track_id="audio-custom-foley", asset_id="e2e-foley",
                    start=1.0, duration=0.25, source_in=0.0, source_out=0.25,
                    volume=0.45,
                ),
            ],
            "watermarks": [
                WatermarkLayer(
                    id="e2e-timed-watermark", kind="text", text="BRAND",
                    anchor="top_right", offset_x=12, offset_y=8, safe_area=16,
                    opacity=0.7, scale=0.9, start=0.25, duration=0.75,
                ),
            ],
            "visual_tracks": [
                VisualTrack(id="overlay-main", name="品牌叠加", order=0),
                VisualTrack(id="overlay-callouts", name="动态字卡", order=1),
            ],
            "visual_overlays": [
                VisualOverlayClip(
                    id="e2e-overlay", asset_id="e2e-still", start=0.25, duration=0.5,
                    source_in=0.0, track_id="overlay-callouts",
                    presentation_mode="sticker", source_audio_enabled=False,
                    source_audio_volume=0.42,
                    x=0.6, y=0.1, width=0.3, height=0.3,
                    opacity=0.8, z_index=1,
                ),
            ],
        }
    return CompositionIR(
        project=ProjectSpec(width=160, height=90, fps=10, duration=2.0),
        scenes=scenes, assets=[asset], **timeline_extensions,
    )


def test_studio_to_timeline_browser_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    results_dir = Path(os.environ.get("VIDEO_DIRECTOR_TEST_RESULTS", str(tmp_path / "test-results")))
    results_dir.mkdir(parents=True, exist_ok=True)
    trace_path = results_dir / "studio-workspace-failure-trace.zip"

    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = Path(os.environ["VIDEO_DIRECTOR_STORAGE_ROOT"])

    pid = "web-e2e-studio-flow"
    still = tmp_path / "e2e-still.png"
    still.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
        "lYkAAAAASUVORK5CYII="
    ))
    store.create_project(pid, "Playwright 工作台回归", {"name": "Playwright 工作台回归"})
    editor.save_composition(pid, _test_composition(still, include_extended_tracks=True))

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = None
            tracing = False
            try:
                context.tracing.start(screenshots=False, snapshots=True, sources=True)
                tracing = True
                page = context.new_page()
                page_errors: list[str] = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))

                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle"
                )
                assert response and response.status == 200
                page.locator('button[onclick="openCreate()"]').click()
                page.locator("#cf-render-engine").wait_for(state="visible")
                page.wait_for_function(
                    "() => window.CF_STUDIO_CATALOG && document.querySelectorAll('[data-studio-style]').length > 0",
                    timeout=10000,
                )
                assert page.locator("#cf-panel").is_visible()
                assert page.locator("#cf-render-engine").is_enabled()
                assert page.locator("#cf-render-engine option").count() >= 1
                first_style = page.locator("[data-studio-style]").first
                selected_style = first_style.get_attribute("data-studio-style")
                assert selected_style
                first_style.click()
                assert page.locator("#cf-style").input_value() == selected_style
                assert page.locator("#cf-studio-template-list").inner_text().startswith("模板目录")
                usable_templates = page.locator("[data-studio-template]")
                assert usable_templates.count() > 0, "catalog must expose at least one genuinely instantiable built-in template"
                selected_template = usable_templates.first.evaluate(
                    "(el) => ({id: el.dataset.studioTemplate, version: el.dataset.templateVersion, text: el.innerText})"
                )
                usable_templates.first.click()
                page.locator("#tg-mask").wait_for(state="visible")
                page.wait_for_function(
                    "(key) => window.TG && window.TG.selectedKey === key",
                    arg=selected_template["id"] + "@" + selected_template["version"],
                )
                assert selected_template["text"] or selected_template["id"]
                assert page.locator("#tg-detail").inner_text().find("使用自己的素材") >= 0
                page.evaluate("templateGalleryClose()")

                page.locator('#cf-panel button:has-text("关闭")').first.click()
                page.wait_for_function(
                    "() => typeof workspaceOpen === 'function' && typeof shotbookLoad === 'function'"
                )
                opened = page.evaluate("(projectId) => workspaceOpen(projectId)", pid)
                assert opened.get("ok"), f"workspace did not open: {opened}"
                # The actual composition endpoint must round-trip persisted overlay
                # clips and expose the referenced source, not just an in-memory UI mock.
                api_composition = page.evaluate("""async (projectId) => {
                  const response = await fetch('/api/editor/composition?project_id=' + encodeURIComponent(projectId));
                  return {status: response.status, body: await response.json()};
                }""", pid)
                assert api_composition["status"] == 200, api_composition
                api_overlays = api_composition["body"]["visual_overlays"]
                assert len(api_overlays) == 1, api_composition
                api_overlay = api_overlays[0]
                assert {key: api_overlay.get(key) for key in (
                    "id", "asset_id", "start", "duration", "source_in", "track_id",
                    "presentation_mode", "source_audio_enabled", "source_audio_volume",
                    "x", "y", "width", "height", "opacity", "z_index",
                )} == {
                    "id": "e2e-overlay", "asset_id": "e2e-still", "start": 0.25,
                    "duration": 0.5, "source_in": 0.0, "track_id": "overlay-callouts",
                    "presentation_mode": "sticker", "source_audio_enabled": False,
                    "source_audio_volume": 0.42,
                    "x": 0.6, "y": 0.1, "width": 0.3, "height": 0.3,
                    "opacity": 0.8, "z_index": 1,
                }, api_composition
                assert api_composition["body"]["audio_tracks"] == [
                    {"id": "audio-custom-music", "name": "自定义配乐", "order": 0, "locked": False},
                    {"id": "audio-custom-foley", "name": "细节音效", "order": 1, "locked": False},
                ], api_composition
                assert [
                    {key: lane.get(key) for key in ("id", "type", "track_id", "asset_id", "start",
                                                     "duration", "source_in", "source_out", "volume",
                                                     "muted", "fadeIn", "fadeOut")}
                    for lane in api_composition["body"]["audio"]
                ] == [
                    {"id": "e2e-music-clip", "type": "bgm", "track_id": "audio-custom-music",
                     "asset_id": "e2e-music", "start": 0.2, "duration": 1.2,
                     "source_in": 0.1, "source_out": 1.3, "volume": 0.65,
                     "muted": False, "fadeIn": 0.15, "fadeOut": 0.25},
                    {"id": "e2e-foley-clip", "type": "sfx", "track_id": "audio-custom-foley",
                     "asset_id": "e2e-foley", "start": 1.0, "duration": 0.25,
                     "source_in": 0.0, "source_out": 0.25, "volume": 0.45,
                     "muted": False, "fadeIn": 0.0, "fadeOut": 0.0},
                ], api_composition
                assert api_composition["body"]["visual_tracks"] == [
                    {"id": "overlay-main", "name": "品牌叠加", "order": 0, "locked": False},
                    {"id": "overlay-callouts", "name": "动态字卡", "order": 1, "locked": False},
                ], api_composition
                assert api_composition["body"]["watermarks"] == [{
                    "id": "e2e-timed-watermark", "kind": "text", "text": "BRAND",
                    "asset_id": None, "anchor": "top_right", "offset_x": 12, "offset_y": 8,
                    "safe_area": 16, "opacity": 0.7, "scale": 0.9, "start": 0.25, "duration": 0.75,
                }], api_composition
                loaded = page.evaluate("() => shotbookLoad()")
                assert loaded, "Shotbook did not load through the real HTTP handler"
                page.locator("#shotbook-details").evaluate("(element) => { element.open = true; }")
                assert page.locator("#shotbook-list .sb-shot").count() == 2
                assert page.locator("#editor-timeline").count() == 1
                assert page.locator("#ed-preview-stage").count() == 1
                restored_tracks = page.evaluate("""() => {
                  const previousSoundOpen = TL_GROUPS.sound.open;
                  TL_GROUPS.sound.open = true;
                  editorRenderTimeline();
                  const laneInfo = selector => Array.from(document.querySelectorAll(selector)).map(row => ({
                    id: row.dataset.audioTrackId || row.dataset.visualTrackId,
                    name: (row.querySelector('.ed-track-label span') || row.querySelector('.ed-track-label'))?.textContent?.trim() || ''
                  }));
                  const result = {
                    audioLanes: laneInfo('#editor-timeline .ed-track[data-audio-track-id]'),
                    visualLanes: laneInfo('#editor-timeline .ed-track[data-visual-track-id]'),
                    musicClip: !!document.querySelector('.ed-track[data-audio-track-id="audio-custom-music"] .ed-audio-clip[data-audio-index="0"]'),
                    foleyClip: !!document.querySelector('.ed-track[data-audio-track-id="audio-custom-foley"] .ed-audio-clip[data-audio-index="1"]'),
                    overlayClip: !!document.querySelector('.ed-track[data-visual-track-id="overlay-callouts"] .ed-visual-overlay-clip[data-visual-overlay-index="0"]')
                  };
                  TL_GROUPS.sound.open = previousSoundOpen;
                  editorRenderTimeline();
                  return result;
                }""")
                assert [(lane["id"], lane["name"]) for lane in restored_tracks["audioLanes"]] == [
                    ("audio-custom-music", "自定义配乐"), ("audio-custom-foley", "细节音效"),
                ], restored_tracks
                assert ("overlay-callouts", "动态字卡") in [
                    (lane["id"], lane["name"]) for lane in restored_tracks["visualLanes"]
                ], restored_tracks
                assert restored_tracks["musicClip"] and restored_tracks["foleyClip"] and restored_tracks["overlayClip"], restored_tracks


                # Real Chromium regression: dragging a 2s audio clip past the
                # 10s project end must submit start=8s, keeping the full clip in
                # the project instead of relying on the renderer to truncate it.
                audio_drag_boundary = page.evaluate("""async () => {
                  const previousComp = ED.comp;
                  const previousView = ED.timelineView;
                  const previousSoundOpen = TL_GROUPS.sound.open;
                  const previousExecute = window.executeCommand;
                  const commands = [];
                  try {
                    const comp = JSON.parse(JSON.stringify(previousComp));
                    comp.duration = 10;
                    comp.audio_tracks = [{id:'test-audio-lane', name:'测试音轨', order:0}];
                    comp.audio = [{
                      id:'boundary-audio', type:'sfx', asset_id:'synthetic-audio',
                      track_id:'test-audio-lane', start:5, duration:2,
                      source_in:0, source_out:2, volume:0.5, muted:false,
                      fadeIn:0, fadeOut:0, ducking:false
                    }];
                    ED.comp = comp;
                    ED.timelineView = 'dual';
                    // The editor intentionally collapses the sound group by default.
                    // Expand it so this regression exercises an actual visible clip.
                    TL_GROUPS.sound.open = true;
                    window.executeCommand = async command => {
                      commands.push(command);
                      return {ok:false, error:'command captured by browser regression'};
                    };
                    editorRenderTimeline();
                    const clip = document.querySelector('.ed-audio-clip[data-audio-index="0"]');
                    const body = clip && clip.closest('.ed-track-body');
                    if (!clip || !body) return {error:'audio clip or track body missing', commands};
                    const clipRect = clip.getBoundingClientRect();
                    const bodyRect = body.getBoundingClientRect();
                    const x = clipRect.left + clipRect.width / 2;
                    const y = clipRect.top + clipRect.height / 2;
                    const farX = x + bodyRect.width * 2;
                    clip.dispatchEvent(new PointerEvent('pointerdown', {
                      bubbles:true, cancelable:true, pointerId:42, button:0, clientX:x, clientY:y
                    }));
                    document.dispatchEvent(new PointerEvent('pointermove', {
                      bubbles:true, cancelable:true, pointerId:42, button:0, clientX:farX, clientY:y
                    }));
                    document.dispatchEvent(new PointerEvent('pointerup', {
                      bubbles:true, cancelable:true, pointerId:42, button:0, clientX:farX, clientY:y
                    }));
                    await new Promise(resolve => setTimeout(resolve, 0));
                    return {commands, expectedStart:8, duration:2, projectDuration:10};
                  } finally {
                    window.executeCommand = previousExecute;
                    ED.comp = previousComp;
                    ED.timelineView = previousView;
                    TL_GROUPS.sound.open = previousSoundOpen;
                    editorRenderTimeline();
                  }
                }""")
                assert "error" not in audio_drag_boundary, audio_drag_boundary
                assert audio_drag_boundary["commands"], audio_drag_boundary
                assert audio_drag_boundary["commands"][0]["command"] == "move_audio_clip", audio_drag_boundary
                assert audio_drag_boundary["commands"][0]["payload"]["start"] == 8, audio_drag_boundary
                # Dual view must not merely render the physical timeline: the
                # playhead must react to pointer input on the real timeline element.
                dual_scrub = page.evaluate("""() => {
                  const oldView = ED.timelineView;
                  ED.timelineView = 'dual';
                  editorRenderTimeline();
                  const scroll = document.querySelector('#editor-timeline .ed-tl-scroll');
                  if (!scroll) return {hasPhysicalTimeline: false, before: 0, after: 0};
                  // The scroller includes the track-label column; scrub against the
                  // actual time axis so added lock/menu controls cannot skew pointer time.
                  const axis = scroll.querySelector('.ed-track-body');
                  if (!axis) return {hasPhysicalTimeline: false, before: 0, after: 0};
                  const axisRect = axis.getBoundingClientRect();
                  const x = axisRect.left + axisRect.width * 0.75;
                  const y = axisRect.top + Math.min(12, axisRect.height / 2);
                  const before = window.PLAYHEAD.at;
                  scroll.dispatchEvent(new PointerEvent('pointerdown', {
                    bubbles: true, cancelable: true, pointerId: 7, button: 0,
                    clientX: x, clientY: y
                  }));
                  scroll.dispatchEvent(new PointerEvent('pointermove', {
                    bubbles: true, cancelable: true, pointerId: 7, buttons: 1,
                    clientX: x, clientY: y
                  }));
                  document.dispatchEvent(new PointerEvent('pointerup', {
                    bubbles: true, cancelable: true, pointerId: 7, button: 0,
                    clientX: x, clientY: y
                  }));
                  const after = window.PLAYHEAD.at;
                  const hasPhysicalTimeline = !!scroll.isConnected;
                  ED.timelineView = oldView;
                  editorRenderTimeline();
                  return {hasPhysicalTimeline, before, after};
                }""")
                assert dual_scrub["hasPhysicalTimeline"], dual_scrub
                assert 1.2 < dual_scrub["after"] < 1.8, dual_scrub

                # Zoom must change the actual shared time-axis width; clip geometry,
                # pointer-to-time mapping and playhead coordinates must stay aligned.
                zoom_geometry = page.evaluate("""() => {
                  const previousZoom = ED.zoom;
                  const previousView = ED.timelineView;
                  const result = [];
                  ED.timelineView = 'dual';
                  for (const zoom of [0.5, 1, 2]) {
                    ED.zoom = zoom;
                    editorRenderTimeline();
                    const scroll = document.querySelector('#editor-timeline .ed-tl-scroll');
                    const canvas = scroll && scroll.querySelector('.ed-tl-canvas');
                    const body = canvas && canvas.querySelector('.ed-track-body');
                    const playhead = document.getElementById('tl-playhead');
                    if (!scroll || !canvas || !body || !playhead) {
                      result.push({zoom, missing: true});
                      continue;
                    }
                    const cr = canvas.getBoundingClientRect();
                    const br = body.getBoundingClientRect();
                    const expectedLeft = br.left - cr.left +
                      (window.PLAYHEAD.at / (ED.comp.duration || 1)) * br.width;
                    const mapped = _tlPct({clientX: br.left + br.width * 0.75}, scroll);
                    result.push({
                      zoom,
                      axisWidth: br.width,
                      canvasWidth: cr.width,
                      viewportWidth: scroll.clientWidth,
                      playheadLeft: parseFloat(playhead.style.left),
                      expectedLeft,
                      mapped,
                      scrollable: scroll.scrollWidth > scroll.clientWidth
                    });
                  }
                  ED.zoom = previousZoom;
                  ED.timelineView = previousView;
                  editorRenderTimeline();
                  return result;
                }""")
                assert len(zoom_geometry) == 3, zoom_geometry
                assert all("missing" not in item for item in zoom_geometry), zoom_geometry
                base_axis = zoom_geometry[1]["axisWidth"]
                assert 0.45 < zoom_geometry[0]["axisWidth"] / base_axis < 0.55, zoom_geometry
                assert 0.98 < zoom_geometry[1]["axisWidth"] / base_axis < 1.02, zoom_geometry
                assert 1.9 < zoom_geometry[2]["axisWidth"] / base_axis < 2.1, zoom_geometry
                assert all(abs(item["playheadLeft"] - item["expectedLeft"]) < 2 for item in zoom_geometry), zoom_geometry
                assert all(abs(item["mapped"] - 0.75) < 0.01 for item in zoom_geometry), zoom_geometry
                assert zoom_geometry[2]["scrollable"], zoom_geometry



                # Real Chromium interaction: a horizontal drag inside the main picture
                # lane uses the drop time, not the pointer's vertical half of the target bar.
                horizontal_drag_setup = page.evaluate("""async () => {
                  window.__horizontalDragOriginal = {
                    comp: ED.comp, view: ED.timelineView, zoom: ED.zoom,
                    executeCommand: window.executeCommand,
                    refreshWorkspace: window.refreshWorkspace,
                    editorToast: window.editorToast
                  };
                  const original = window.__horizontalDragOriginal;
                  window.__horizontalDragEvents = [];
                  window.__horizontalDragListeners = [];
                  for (const type of ["pointerdown","pointermove","pointerup","pointercancel","mousedown","mousemove","mouseup"]) {
                    const listener = event => {
                      if (window.__horizontalDragEvents.length < 80) {
                        const target = event.target;
                        const under = document.elementFromPoint(event.clientX || 0, event.clientY || 0);
                        const source = window.__horizontalDragSelector
                          ? document.querySelector(window.__horizontalDragSelector) : null;
                        const sr = source && source.getBoundingClientRect();
                        const scroll = source && source.closest('.ed-tl-scroll');
                        const css = source && getComputedStyle(source);
                        window.__horizontalDragEvents.push({
                          type, x:event.clientX, y:event.clientY, button:event.button,
                          pointerId:event.pointerId,
                          target:target ? {tag:target.tagName,cls:String(target.className),scene:target.dataset && target.dataset.scene,idx:target.dataset && target.dataset.idx} : null,
                          under:under ? {tag:under.tagName,cls:String(under.className),scene:under.dataset && under.dataset.scene,idx:under.dataset && under.dataset.idx} : null,
                          sourceState:source ? {
                            connected:source.isConnected,
                            rect:{left:sr.left,top:sr.top,right:sr.right,bottom:sr.bottom,width:sr.width,height:sr.height},
                            pointerEvents:css.pointerEvents,display:css.display,visibility:css.visibility,
                            parentRect:(() => {const r=source.parentElement.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom};})(),
                            scroll:scroll ? {left:scroll.scrollLeft,top:scroll.scrollTop,rect:(() => {const r=scroll.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom};})()} : null,
                            hitContains:!!(under && (under===source || source.contains(under)))
                          } : null,
                          windowScroll:{x:window.scrollX,y:window.scrollY}
                        });
                      }
                    };
                    document.addEventListener(type, listener, true);
                    window.__horizontalDragListeners.push({type, listener});
                  }
                  const comp = JSON.parse(JSON.stringify(original.comp));
                  comp.duration = 10;
                  comp.assets = {
                    "drag-a": {id:"drag-a",type:"image",name:"Clip A",file:"/a.png",duration:2},
                    "drag-b": {id:"drag-b",type:"image",name:"Clip B",file:"/b.png",duration:1},
                    "drag-c": {id:"drag-c",type:"image",name:"Clip C",file:"/c.png",duration:3}
                  };
                  comp.scenes[0].start = 0;
                  comp.scenes[0].duration = 6;
                  comp.scenes[0].layers = [
                    {type:"image",asset_id:"drag-a",start:0,duration:2},
                    {type:"image",asset_id:"drag-b",start:2,duration:1},
                    {type:"image",asset_id:"drag-c",start:3,duration:3}
                  ];
                  if (comp.scenes[1]) {
                    comp.scenes[1].start = 6;
                    comp.scenes[1].duration = 4;
                  }
                  ED.comp = comp;
                  ED.timelineView = "dual";
                  ED.zoom = 0.5;
                  window.refreshWorkspace = async () => {};
                  window.editorToast = () => {};
                  editorRenderTimeline();

                  // Scope the source clip to the physical timeline canvas. Semantic
                  // storyboard rows are not draggable timeline clips.
                  const canvas = document.querySelector("#editor-timeline .ed-tl-canvas");
                  const selector = '.ed-layer[data-scene="' + comp.scenes[0].id + '"][data-idx="0"]';
                  const source = canvas && canvas.querySelector(selector);
                  const scene = source && source.closest(".ed-scene");
                  const body = scene && scene.querySelector(".ed-scene-body");
                  if (!source || !body) return {error:"physical main clip or scene body missing"};
                  window.__horizontalDragSelector = selector;
                  // Make scroll deterministic: smooth/intermediate scrolling can
                  // invalidate getBoundingClientRect coordinates before real input.
                  source.scrollIntoView({block:"center", inline:"nearest", behavior:"instant"});
                  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                  const sr = source.getBoundingClientRect(), br = body.getBoundingClientRect();
                  const x = sr.left + sr.width / 2;
                  const y = sr.top + sr.height / 2;
                  const dropX = br.left + br.width * 0.92;
                  const dropTarget = document.elementFromPoint(dropX, y);
                  const sourceHit = document.elementFromPoint(x, y);
                  if (x < 0 || x >= innerWidth || y < 0 || y >= innerHeight ||
                      dropX < 0 || dropX >= innerWidth || y < 0 || y >= innerHeight) {
                    return {error:"real mouse drag coordinates are outside viewport",
                      x,y,dropX,viewport:{width:innerWidth,height:innerHeight}};
                  }
                  return {
                    x,y,dropX,viewport:{width:innerWidth,height:innerHeight},
                    sourceRect:{left:sr.left,top:sr.top,width:sr.width,height:sr.height},
                    bodyRect:{left:br.left,top:br.top,width:br.width,height:br.height},
                    sceneRect:(() => { const r=scene.getBoundingClientRect(); return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}; })(),
                    sourceHit:sourceHit ? {tag:sourceHit.tagName,className:String(sourceHit.className),scene:sourceHit.dataset && sourceHit.dataset.scene,idx:sourceHit.dataset && sourceHit.dataset.idx} : null,
                    dropTarget:dropTarget ? {tag:dropTarget.tagName,className:String(dropTarget.className),scene:dropTarget.dataset && dropTarget.dataset.scene,idx:dropTarget.dataset && dropTarget.dataset.idx} : null,
                    handlersBound:!!window.__tlLayerDragHandlers
                  };
                }""")
                captured_editor_commands: list[dict] = []
                def capture_horizontal_drag_command(route):
                    try:
                        body = json.loads(route.request.post_data or "{}")
                    except (TypeError, json.JSONDecodeError):
                        body = {}
                    if not isinstance(body, dict) or body.get("command") != "reorder_layer":
                        route.continue_()
                        return
                    captured_editor_commands.append(body)
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps({
                            "ok": False,
                            "status": "CAPTURED_FOR_TEST",
                            "error": "captured by browser regression",
                        }),
                    )

                # Intercept at the real HTTP command boundary. Overriding window.executeCommand
                # is not reliable for the global editorCmd reference and previously allowed
                # this regression to POST into the test server without capturing the request.
                page.route("**/api/editor/command", capture_horizontal_drag_command)
                try:
                    assert "error" not in horizontal_drag_setup, horizontal_drag_setup
                    # Use Playwright's real Chromium mouse input rather than dispatching
                    # synthetic PointerEvents on document. This exercises pointer capture,
                    # hit testing, and the same event path used by a person dragging a clip.
                    page.mouse.move(horizontal_drag_setup["x"], horizontal_drag_setup["y"])
                    page.mouse.down()
                    page.mouse.move(horizontal_drag_setup["dropX"], horizontal_drag_setup["y"], steps=12)
                    page.mouse.up()
                    page.wait_for_timeout(50)
                    captured_commands = list(captured_editor_commands)
                    captured_events = page.evaluate("window.__horizontalDragEvents || []")
                    horizontal_layer_drag = {
                        "commands": captured_commands,
                        "events": captured_events,
                        "expectedToIndex": 2,
                        "geometry": horizontal_drag_setup,
                    }
                finally:
                    page.unroute("**/api/editor/command", capture_horizontal_drag_command)
                    page.evaluate("""() => {
                      const original = window.__horizontalDragOriginal;
                      if (original) {
                        ED.comp = original.comp;
                        ED.timelineView = original.view;
                        ED.zoom = original.zoom;
                        window.executeCommand = original.executeCommand;
                        window.refreshWorkspace = original.refreshWorkspace;
                        window.editorToast = original.editorToast;
                      }
                      // Remove the capture listeners before deleting their event buffer.
                      // They otherwise survive this instrumentation block and throw on
                      // later unrelated pointer events when __horizontalDragEvents is gone.
                      for (const entry of window.__horizontalDragListeners || []) {
                        document.removeEventListener(entry.type, entry.listener, true);
                      }
                      delete window.__horizontalDragListeners;
                      delete window.__horizontalDragOriginal;
                      delete window.__horizontalDragCommands;
                      delete window.__horizontalDragEvents;
                      delete window.__horizontalDragSelector;
                      editorRenderTimeline();
                    }""")
                assert horizontal_layer_drag["commands"], json.dumps({
                    "commands": captured_commands,
                    "expectedToIndex": 2,
                    "geometry": horizontal_drag_setup,
                    "dragDebug": page.evaluate("window.__lastLayerDragDebug || null"),
                    # Emit complete, compact real-input evidence. A dict repr is
                    # pytest-truncated and hid the one event that determines why
                    # a human-style drag did not reach the command path.
                    "pointerEvents": [
                        {
                            "type": item.get("type"), "x": item.get("x"), "y": item.get("y"),
                            "button": item.get("button"), "pointerId": item.get("pointerId"),
                            "target": item.get("target"), "under": item.get("under"),
                            "sourceState": item.get("sourceState"), "windowScroll": item.get("windowScroll"),
                        }
                        for item in captured_events
                        if str(item.get("type", "")).startswith("pointer")
                        or item.get("type") in ("mousedown", "mouseup")
                    ],
                }, ensure_ascii=False, indent=2)
                assert horizontal_layer_drag["commands"][0]["command"] == "reorder_layer", horizontal_layer_drag
                assert horizontal_layer_drag["commands"][0]["payload"]["toIndex"] == 2, horizontal_layer_drag

                # Real user-facing overlay lane: render, edit inspector, add from project
                # assets, delete, and drag a clip. Commands are captured at the same
                # frontend command boundary used by the real API so no disk data changes.
                overlay_ui = page.evaluate("""async () => {
                  const original = {
                    comp: ED.comp, view: ED.timelineView,
                    executeCommand: window.executeCommand,
                    refreshWorkspace: window.refreshWorkspace,
                    editorToast: window.editorToast
                  };
                  const commands = [];
                  try {
                    ED.comp = Object.assign({}, original.comp, {
                      duration: 5,
                      assets: {
                        "overlay-test-image": {id:"overlay-test-image", type:"image", name:"Test overlay image", file:"/fake.png", duration:1},
                        "overlay-test-video": {id:"overlay-test-video", type:"video", name:"Test overlay video", file:"/fake.mp4", duration:3}
                      },
                      visual_overlays: [{
                        id:"overlay-test", asset_id:"overlay-test-video", start:1, duration:2, source_in:0,
                        presentation_mode:"picture_in_picture", source_audio_enabled:false, source_audio_volume:1,
                        x:0.65, y:0.1, width:0.3, height:0.3, opacity:1, z_index:0
                      }]
                    });
                    ED.timelineView = "dual";
                    window.executeCommand = async command => { commands.push(command); return {ok:true}; };
                    window.refreshWorkspace = async () => {};
                    window.editorToast = () => {};
                    editorRenderTimeline();
                    const trackExists = !!document.querySelector('.ed-visual-overlay-clip[data-visual-overlay-index="0"]');
                    const addButtonExists = !!document.querySelector(".ed-track-add");
                    editorVisualOverlayOpen(0);
                    document.getElementById("visual-overlay-z-index").value = "3";
                    const editMode = document.getElementById("visual-overlay-edit-presentation-mode");
                    editMode.value = "full_frame";
                    editMode.dispatchEvent(new Event("change", {bubbles:true}));
                    const fullFramePreset = ["x", "y", "width", "height"].map(key => Number(document.getElementById("visual-overlay-" + key).value));
                    document.getElementById("visual-overlay-edit-source-audio").checked = true;
                    document.getElementById("visual-overlay-edit-source-audio-volume").value = "0.8";
                    const editControlsUnique = new Set(Array.from(document.querySelectorAll("#visual-overlay-inspector-mask [id]")).map(el => el.id)).size === document.querySelectorAll("#visual-overlay-inspector-mask [id]").length;
                    await editorVisualOverlayApply(0);
                    const editPayload = commands.find(command => command.command === "set_visual_overlay").payload;
                    editorVisualOverlayPick();
                    const overlayAssetSelect = document.getElementById("visual-overlay-asset");
                    overlayAssetSelect.value = "overlay-test-video";
                    overlayAssetSelect.dispatchEvent(new Event("change", {bubbles:true}));
                    const createAudioControlVisible = getComputedStyle(document.getElementById("visual-overlay-create-audio-row")).display !== "none";
                    document.getElementById("visual-overlay-presentation-mode").value = "full_frame";
                    document.getElementById("visual-overlay-source-audio").checked = true;
                    document.getElementById("visual-overlay-start").value = "0.5";
                    document.getElementById("visual-overlay-duration").value = "1";
                    await editorVisualOverlayCreate();
                    const createPayload = commands.find(command => command.command === "add_visual_overlay").payload;
                    await editorVisualOverlayRemove(0);
                    editorRenderTimeline();
                    const clip = document.querySelector('.ed-visual-overlay-clip[data-visual-overlay-index="0"]');
                    const rect = clip.getBoundingClientRect();
                    const body = clip.closest(".ed-track-body").getBoundingClientRect();
                    const x = rect.left + rect.width / 2;
                    const y = rect.top + rect.height / 2;
                    clip.dispatchEvent(new PointerEvent("pointerdown", {bubbles:true, cancelable:true, pointerId:27, button:0, clientX:x, clientY:y}));
                    document.dispatchEvent(new PointerEvent("pointermove", {bubbles:true, cancelable:true, pointerId:27, buttons:1, clientX:x + body.width * 0.1, clientY:y}));
                    document.dispatchEvent(new PointerEvent("pointerup", {bubbles:true, cancelable:true, pointerId:27, button:0, clientX:x + body.width * 0.1, clientY:y}));
                    await Promise.resolve();
                    await new Promise(resolve => setTimeout(resolve, 0));
                    return {
                      trackExists, addButtonExists, commands,
                      editControlsUnique, editPayload, createPayload, createAudioControlVisible, fullFramePreset,
                      dialogRemoved:!document.getElementById("visual-overlay-inspector-mask")
                    };
                  } finally {
                    ED.comp = original.comp;
                    ED.timelineView = original.view;
                    window.executeCommand = original.executeCommand;
                    window.refreshWorkspace = original.refreshWorkspace;
                    window.editorToast = original.editorToast;
                    editorRenderTimeline();
                  }
                }""")
                assert overlay_ui["trackExists"], overlay_ui
                assert overlay_ui["addButtonExists"], overlay_ui
                overlay_commands = [command.get("command") for command in overlay_ui["commands"]]
                assert overlay_commands == [
                    "set_visual_overlay", "add_visual_overlay", "remove_visual_overlay", "move_visual_overlay"
                ], overlay_ui
                assert overlay_ui["commands"][0]["payload"]["z_index"] == 3, overlay_ui
                assert overlay_ui["commands"][0]["payload"]["presentation_mode"] == "full_frame", overlay_ui
                assert overlay_ui["fullFramePreset"] == [0, 0, 1, 1], overlay_ui
                assert [overlay_ui["editPayload"][key] for key in ("x", "y", "width", "height")] == [0, 0, 1, 1], overlay_ui
                assert overlay_ui["editPayload"]["source_audio_enabled"] is True, overlay_ui
                assert overlay_ui["editPayload"]["source_audio_volume"] == 0.8, overlay_ui
                assert overlay_ui["createPayload"]["presentationMode"] == "full_frame", overlay_ui
                assert overlay_ui["createPayload"]["sourceAudioEnabled"] is True, overlay_ui
                assert overlay_ui["createAudioControlVisible"], overlay_ui
                assert overlay_ui["commands"][1]["payload"]["start"] == 0.5, overlay_ui
                assert overlay_ui["editControlsUnique"], overlay_ui
                assert overlay_ui["dialogRemoved"], overlay_ui

                # Live preview must paint the same global overlay clip only inside its
                # output interval, and apply the timeline's geometry/opacity to the frame.
                live_overlay_preview = page.evaluate("""() => {
                  const originalComp = ED.comp;
                  const originalPV = {
                    mode: PV.mode, playing: PV.playing, playhead: PV.playhead,
                    activeSceneId: PV.activeSceneId, activeLayerId: PV.activeLayerId
                  };
                  try {
                    ED.comp = Object.assign({}, originalComp, {
                      duration: 5,
                      project: Object.assign({}, originalComp.project || {}, {fps: 30, width: 320, height: 180}),
                      scenes: [{
                        id: 'PV-preview-scene', start: 0, duration: 5, text: 'Preview fixture',
                        layers: [{id: 'PV-base-layer', type: 'image', asset_id: 'PV-base-asset', start: 0, duration: 5}]
                      }],
                      assets: {
                        'PV-base-asset': {id: 'PV-base-asset', type: 'image', file_url: '/preview-base.png'},
                        'PV-overlay-asset': {id: 'PV-overlay-asset', type: 'image', file_url: '/preview-overlay.png'}
                      },
                      visual_overlays: [{
                        id: 'PV-overlay', asset_id: 'PV-overlay-asset', start: 1, duration: 2,
                        source_in: 0, x: 0.65, y: 0.1, width: 0.3, height: 0.3, opacity: 0.8, z_index: 4
                      }]
                    });
                    PV.mode = 'live';
                    PV.playing = false;
                    PV.playhead = 1.5;
                    PV.activeSceneId = '';
                    PV.activeLayerId = '';
                    previewRenderComposition();
                    const previewState = (label) => {
                      const stage = document.querySelector('#ed-preview-stage');
                      const media = stage && stage.querySelector('.pv-live-media');
                      const host = stage && stage.querySelector('.pv-live-overlays');
                      return {
                        label, mode: PV.mode, playhead: PV.playhead,
                        stageExists: !!stage, mediaExists: !!media, hostExists: !!host,
                        activeSceneId: PV.activeSceneId, activeLayerId: PV.activeLayerId,
                        stageMarkup: stage ? stage.innerHTML.slice(0, 260) : ''
                      };
                    };
                    const stateAfterRender = previewState('after-render');
                    const host = document.querySelector('#ed-preview-stage .pv-live-overlays');
                    const active = host && host.querySelector('[data-overlay-index="0"]');
                    const activeStyle = active ? {
                      tag: active.tagName, left: active.style.left, top: active.style.top,
                      width: active.style.width, height: active.style.height,
                      opacity: active.style.opacity, zIndex: active.style.zIndex,
                      hostPosition: getComputedStyle(host).position
                    } : null;
                    previewSeek(0.5);
                    const stateAfterOutsideStart = previewState('outside-start');
                    const outsideHost = document.querySelector('#ed-preview-stage .pv-live-overlays');
                    const outsideStartCount = outsideHost ? outsideHost.children.length : -1;
                    previewSeek(2.5);
                    const stateInside = previewState('inside');
                    const insideHost = document.querySelector('#ed-preview-stage .pv-live-overlays');
                    const insideCount = insideHost ? insideHost.children.length : -1;
                    previewSeek(3.0);
                    const stateAtEnd = previewState('at-end');
                    const endHost = document.querySelector('#ed-preview-stage .pv-live-overlays');
                    const atEndCount = endHost ? endHost.children.length : -1;
                    return {activeStyle, outsideStartCount, insideCount, atEndCount,
                      diagnostics: [stateAfterRender, stateAfterOutsideStart, stateInside, stateAtEnd]};
                  } finally {
                    ED.comp = originalComp;
                    PV.mode = originalPV.mode;
                    PV.playing = originalPV.playing;
                    PV.playhead = originalPV.playhead;
                    PV.activeSceneId = '';
                    PV.activeLayerId = '';
                    previewRenderComposition();
                    PV.activeSceneId = originalPV.activeSceneId;
                    PV.activeLayerId = originalPV.activeLayerId;
                  }
                }""")
                assert live_overlay_preview["activeStyle"] == {
                    "tag": "IMG", "left": "65%", "top": "10%", "width": "30%",
                    "height": "30%", "opacity": "0.8", "zIndex": "105", "hostPosition": "absolute"
                }, live_overlay_preview
                assert live_overlay_preview["outsideStartCount"] == 0, json.dumps(live_overlay_preview.get("diagnostics", []), ensure_ascii=False)
                assert live_overlay_preview["insideCount"] == 1, live_overlay_preview
                assert live_overlay_preview["atEndCount"] == 0, live_overlay_preview

                # The word-level subtitle track is time-aligned too: preserve the
                # phrase around the current word, highlight that word, and apply style.
                live_subtitle_preview = page.evaluate("""() => {
                  const originalComp = ED.comp;
                  const originalPV = {
                    mode: PV.mode, playing: PV.playing, playhead: PV.playhead,
                    activeSceneId: PV.activeSceneId, activeLayerId: PV.activeLayerId
                  };
                  try {
                    ED.comp = Object.assign({}, originalComp, {
                      duration: 5,
                      project: Object.assign({}, originalComp.project || {}, {fps: 30, width: 320, height: 180}),
                      scenes: [{
                        id: 'PV-subtitle-scene', start: 0, duration: 5, text: 'Subtitle fixture',
                        layers: [{id: 'PV-subtitle-base', type: 'image', asset_id: 'PV-subtitle-base-asset', start: 0, duration: 5}]
                      }],
                      assets: {'PV-subtitle-base-asset': {id: 'PV-subtitle-base-asset', type: 'image', file_url: '/preview-subtitle-base.png'}},
                      subtitle_track: {
                        mode: 'word', source: 'asr', cues: [
                          {start: 1.0, end: 1.25, text: 'I'},
                          {start: 1.25, end: 1.5, text: 'love'},
                          {start: 1.5, end: 1.8, text: 'editing.'}
                        ]
                      },
                      subtitle_style: {
                        font: 'Arial', size: 1.25, color: '#FFFFFF', stroke_color: '#000000',
                        stroke_width: 2, background: '#000000@0.5', background_opacity: 0.5,
                        position: 'bottom', alignment: 'center', max_width: 0.9, animation: 'none', mode: 'word'
                      }
                    });
                    PV.mode = 'live'; PV.playing = false; PV.playhead = 1.35;
                    PV.activeSceneId = ''; PV.activeLayerId = '';
                    previewRenderComposition();
                    const host = document.querySelector('#ed-preview-stage .pv-live-subtitle');
                    const active = host && host.querySelector('.pv-live-subtitle-word.is-active');
                    const initial = {
                      text: host ? host.textContent : '',
                      activeWord: active ? active.textContent : '',
                      fontSize: host ? host.style.fontSize : '',
                      maxWidth: host ? host.style.maxWidth : '',
                      background: host ? host.style.backgroundColor : '',
                      bottom: host ? host.style.bottom : ''
                    };
                    previewSeek(0.5);
                    const outsideCount = host ? host.childElementCount : -1;
                    previewSeek(1.6);
                    const activeWordLater = host && host.querySelector('.pv-live-subtitle-word.is-active');
                    const later = activeWordLater ? activeWordLater.textContent : '';
                    previewSeek(2.0);
                    const atEndCount = host ? host.childElementCount : -1;
                    return {initial, outsideCount, later, atEndCount};
                  } finally {
                    ED.comp = originalComp;
                    PV.mode = originalPV.mode; PV.playing = originalPV.playing; PV.playhead = originalPV.playhead;
                    PV.activeSceneId = ''; PV.activeLayerId = '';
                    previewRenderComposition();
                    PV.activeSceneId = originalPV.activeSceneId; PV.activeLayerId = originalPV.activeLayerId;
                  }
                }""")
                assert live_subtitle_preview["initial"] == {
                    "text": "I love editing.", "activeWord": "love",
                    "fontSize": "22.5px", "maxWidth": "90%", "background": "rgba(0, 0, 0, 0.5)",
                    "bottom": "8%"
                }, live_subtitle_preview
                assert live_subtitle_preview["outsideCount"] == 0, live_subtitle_preview
                assert live_subtitle_preview["later"] == "editing.", live_subtitle_preview
                assert live_subtitle_preview["atEndCount"] == 0, live_subtitle_preview

                # Re-rendering the physical timeline must not attach another pair
                # of document-level layer-drag handlers. Count real registrations.
                layer_handler_count = page.evaluate("""() => {
                  const originalAdd = document.addEventListener;
                  const registrations = [];
                  document.addEventListener = function(type, listener, options) {
                    if (type === 'pointermove' || type === 'pointerup') registrations.push(type);
                    return originalAdd.call(this, type, listener, options);
                  };
                  const view = ED.timelineView;
                  ED.timelineView = 'dual';
                  editorRenderTimeline();
                  editorRenderTimeline();
                  document.addEventListener = originalAdd;
                  ED.timelineView = view;
                  editorRenderTimeline();
                  return {
                    guardBound: window.__tlLayerDragHandlers === true,
                    pointermove: registrations.filter(type => type === 'pointermove').length,
                    pointerup: registrations.filter(type => type === 'pointerup').length
                  };
                }""")
                assert layer_handler_count == {"guardBound": True, "pointermove": 0, "pointerup": 0}, layer_handler_count
                # 布局契约：Shotbook 与编辑器共享同一工作区（main 布局：#editor-body
                # 与 #editor-workspace 同属 pane-editor；不要求在 .ws-col 三栏列内）。
                assert page.locator("#shotbook-details").evaluate(
                    "(element) => !!element.closest('#editor-body') && !!document.getElementById('editor-workspace')"
                ), "Shotbook must live inside the shared editor workspace"

                page.locator(
                    '#shotbook-list [data-shotbook-action="locate"][data-scene-key="S002"]'
                ).click()
                page.wait_for_function(
                    "() => Math.abs((window.PV && window.PV.playhead || 0) - 1) < 0.02"
                )
                # 信任边界：生成分镜草案落盘（未逐镜确认）→ render_final 被服务端
                # 409 拦截。门语义=「保存过 Shotbook 的项目须确认后成片」——
                # 仅打开过工作台（纯 GET load）不落盘、不设门，legacy 项目兼容。
                gen = page.evaluate("""async (projectId) => {
                  const r = await fetch('/api/editor/shotbook', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({project_id: projectId, action: 'generate',
                                          base_version: 0})
                  });
                  return {status: r.status, body: await r.json()};
                }""", pid)
                assert gen["body"].get("ok"), gen
                response = page.evaluate("""async (projectId) => {
                  const result = await fetch('/api/workflows', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({project_id: projectId, kind: 'render_final'})
                  });
                  return {status: result.status, body: await result.json()};
                }""", pid)
                assert response["status"] == 409, response
                assert response["body"].get("code") == "SHOTBOOK_NOT_CONFIRMED", response
                # Real Chromium lock/unlock round trip: click the actual track-header
                # controls, wait for the versioned Editor Command to refresh state, and
                # confirm the persisted API state. Unlock after the assertion so later
                # independent drag tests keep their original preconditions.
                page.evaluate("""() => {
                  TL_GROUPS.sound.open = true;
                  TL_GROUPS.picture.open = true;
                  ED.timelineView = 'dual';
                  editorRenderTimeline();
                }""")
                audio_lock_button = page.locator(
                    '#editor-timeline .ed-track[data-audio-track-id="audio-custom-music"] .ed-track-lock'
                )
                visual_lock_button = page.locator(
                    '#editor-timeline .ed-track[data-visual-track-id="overlay-callouts"] .ed-track-lock'
                )
                assert audio_lock_button.count() == 1
                assert visual_lock_button.count() == 1
                audio_lock_button.click()
                page.wait_for_function(
                    "() => ED.comp.audio_tracks.find(t => t.id === 'audio-custom-music')?.locked === true"
                )
                visual_lock_button.click()
                page.wait_for_function(
                    "() => ED.comp.visual_tracks.find(t => t.id === 'overlay-callouts')?.locked === true"
                )
                locked_api = page.evaluate("""async (projectId) => {
                  const response = await fetch('/api/editor/composition?project_id=' + encodeURIComponent(projectId));
                  const body = await response.json();
                  return {
                    status: response.status,
                    audio: body.audio_tracks.find(t => t.id === 'audio-custom-music')?.locked,
                    visual: body.visual_tracks.find(t => t.id === 'overlay-callouts')?.locked
                  };
                }""", pid)
                assert locked_api == {"status": 200, "audio": True, "visual": True}, locked_api
                audio_lock_button = page.locator(
                    '#editor-timeline .ed-track[data-audio-track-id="audio-custom-music"] .ed-track-lock'
                )
                visual_lock_button = page.locator(
                    '#editor-timeline .ed-track[data-visual-track-id="overlay-callouts"] .ed-track-lock'
                )
                audio_lock_button.click()
                page.wait_for_function(
                    "() => ED.comp.audio_tracks.find(t => t.id === 'audio-custom-music')?.locked === false"
                )
                visual_lock_button.click()
                page.wait_for_function(
                    "() => ED.comp.visual_tracks.find(t => t.id === 'overlay-callouts')?.locked === false"
                )

                assert not page_errors, "Browser runtime errors: " + " | ".join(page_errors)
            except BaseException:
                # Preserve the DOM/trace failure evidence without writing screenshots;
                # don't let cleanup errors replace the actual failing assertion/timeout.
                if tracing:
                    try:
                        context.tracing.stop(path=str(trace_path))
                        tracing = False
                    except Exception:
                        pass
                raise
            else:
                if tracing:
                    context.tracing.stop()
                    tracing = False
            finally:
                try:
                    context.close()
                finally:
                    browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_cross_scene_drag_lifts_still_into_real_global_track(tmp_path, monkeypatch):
    """A real pointer drag across scene boundaries must persist as a global clip."""
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    results_dir = Path(os.environ.get("VIDEO_DIRECTOR_TEST_RESULTS", str(tmp_path / "test-results")))
    results_dir.mkdir(parents=True, exist_ok=True)
    trace_path = results_dir / "cross-scene-lift-failure-trace.zip"

    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = Path(os.environ["VIDEO_DIRECTOR_STORAGE_ROOT"])

    pid = "web-e2e-cross-scene-lift"
    still = tmp_path / "e2e-still.png"
    still.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
        "lYkAAAAASUVORK5CYII="
    ))
    store.create_project(pid, "Cross-scene lift regression", {"name": "Cross-scene lift regression"})
    editor.save_composition(pid, _test_composition(still))

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        tracing = False
        try:
            context.tracing.start(screenshots=False, snapshots=True, sources=True)
            tracing = True
            response = page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
            assert response and response.status == 200
            opened = page.evaluate("(projectId) => workspaceOpen(projectId)", pid)
            assert opened.get("ok"), f"workspace did not open: {opened}"
            page.evaluate("() => { ED.timelineView = 'time'; editorRenderTimeline(); }")
            source = page.locator('#editor-timeline .ed-layer[data-scene="S001"][data-idx="0"]')
            destination = page.locator('#editor-timeline .ed-layer[data-scene="S002"][data-idx="0"]')
            source.wait_for(state="visible")
            destination.wait_for(state="visible")
            # "visible" does not imply viewport-visible: the full editor is vertically
            # below the fold on CI's 1000px viewport. Playwright mouse events outside
            # the viewport do not hit the timeline, so scroll the real clips into view
            # before collecting coordinates and dragging.
            source.scroll_into_view_if_needed()
            destination.scroll_into_view_if_needed()
            source_rect = source.bounding_box()
            destination_rect = destination.bounding_box()
            assert source_rect and destination_rect, {"source": source_rect, "destination": destination_rect}
            viewport_height = page.evaluate("() => window.innerHeight")
            assert 0 <= source_rect["y"] < viewport_height and source_rect["y"] + source_rect["height"] <= viewport_height, {
                "source": source_rect, "viewport_height": viewport_height
            }
            assert 0 <= destination_rect["y"] < viewport_height and destination_rect["y"] + destination_rect["height"] <= viewport_height, {
                "destination": destination_rect, "viewport_height": viewport_height
            }
            sx = source_rect["x"] + source_rect["width"] / 2
            sy = source_rect["y"] + source_rect["height"] / 2
            dx = destination_rect["x"] + destination_rect["width"] / 2
            dy = destination_rect["y"] + destination_rect["height"] / 2
            assert dx > sx + 20, {"source": source_rect, "destination": destination_rect}

            with page.expect_response(
                lambda item: item.request.method == "POST" and item.url.endswith("/api/editor/command"),
                timeout=10000,
            ) as response_info:
                page.mouse.move(sx, sy)
                page.mouse.down()
                page.mouse.move(dx, dy, steps=12)
                page.mouse.up()
            command_response = response_info.value
            assert command_response.status == 200
            command_body = command_response.json()
            assert command_body.get("status") == "APPLIED", command_body

            persisted = page.evaluate("""async (projectId) => {
              const response = await fetch('/api/editor/composition?project_id=' + encodeURIComponent(projectId));
              return {status: response.status, body: await response.json()};
            }""", pid)
            assert persisted["status"] == 200, persisted
            comp = persisted["body"]
            assert comp["scenes"][0]["layers"][0]["type"] == "gap", comp["scenes"][0]
            assert len(comp["visual_overlays"]) == 1, comp.get("visual_overlays")
            lifted = comp["visual_overlays"][0]
            assert lifted["asset_id"] == "e2e-still", lifted
            assert lifted["presentation_mode"] == "full_frame", lifted
            assert lifted["source_audio_enabled"] is False, lifted
            assert 0.5 < float(lifted["start"]) < 1.1, lifted
            assert abs(float(lifted["duration"]) - 1.0) < 0.01, lifted
            assert any(track["id"] == lifted["track_id"] and "独立图片" in track["name"]
                       for track in comp["visual_tracks"]), comp["visual_tracks"]
            # /api/editor/composition returns the normalized editor summary:
            # project duration is top-level, not nested under a "project" object.
            assert abs(float(comp["duration"]) - 2.0) < 0.01
            assert not page_errors, "Browser runtime errors: " + " | ".join(page_errors)
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
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

