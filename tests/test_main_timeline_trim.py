"""Regressions for main-track edge trimming: pointer direction, timeline bounds, and gap persistence."""
from __future__ import annotations

import base64
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

from video_director import editor, renderer
from video_director.models import AssetRef, CompositionIR, Layer, LayerType, ProjectSpec, SceneIR


_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
    "lYkAAAAASUVORK5CYII="
)


def _project(tmp_path: Path, monkeypatch, *, with_gap: bool = False, real_video: bool = False):
    root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(root))
    project_id = "main-trim-gap" if with_gap else "main-trim-no-gap"
    plans = root / project_id / "plans"
    plans.mkdir(parents=True)
    source = tmp_path / "source.mp4"
    if real_video:
        run = subprocess.run(
            [
                renderer._ffmpeg(), "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=red:s=160x90:r=10:d=8",
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "8",
                str(source),
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert run.returncode == 0, run.stderr
        assert source.is_file() and source.stat().st_size > 100
    else:
        source.write_bytes(b"source fixture; command tests do not decode media")
    still = tmp_path / "still.png"
    still.write_bytes(base64.b64decode(_PNG))

    if with_gap:
        layers = [
            Layer(type=LayerType.gap, start=0.0, duration=1.0),
            Layer(
                type=LayerType.video, asset_id="source-video", start=1.0,
                duration=3.0, source_in=3.0, source_out=6.0,
            ),
            Layer(type=LayerType.image, asset_id="still", start=4.0, duration=6.0),
        ]
    else:
        layers = [
            Layer(
                type=LayerType.video, asset_id="source-video", start=0.0,
                duration=4.0, source_in=2.0, source_out=6.0,
            ),
            Layer(type=LayerType.image, asset_id="still", start=4.0, duration=6.0),
        ]
    ir = CompositionIR(
        project=ProjectSpec(width=160, height=90, fps=10, duration=10.0),
        scenes=[SceneIR(id="S01", start=0.0, duration=10.0, text="trim contract", layers=layers)],
        assets=[
            AssetRef(id="source-video", type="video", file=str(source), duration=8.0,
                     width=160, height=90, fps=10),
            AssetRef(id="still", type="image", file=str(still), width=1, height=1),
        ],
    )
    editor.save_composition(project_id, ir)
    (plans / "revisions.jsonl").write_text("", encoding="utf-8")
    return project_id, plans


def _trim(project_id: str, index: int, source_in: float, source_out: float):
    return editor.execute_command(project_id, {
        "command": "trim",
        "targetId": f"S01#{index}",
        "payload": {"sourceIn": source_in, "sourceOut": source_out, "edge": "left"},
        "expectedVersion": 1,
    })


def test_left_edge_trim_moves_clip_start_and_keeps_clip_end_and_following_edits_fixed(tmp_path, monkeypatch):
    project_id, plans = _project(tmp_path, monkeypatch)
    result = _trim(project_id, 0, 3.0, 6.0)

    assert result.status == "APPLIED", result.errors
    ir = editor.load_composition(project_id)
    scene = ir.scenes[0]
    assert scene.duration == 10.0
    assert len(scene.layers) == 3
    gap, video, still = scene.layers
    assert gap.type == LayerType.gap
    assert (gap.start, gap.duration) == (0.0, 1.0)
    assert video.type == LayerType.video
    assert (video.start, video.duration, video.source_in, video.source_out) == (1.0, 3.0, 3.0, 6.0)
    assert (still.start, still.duration) == (4.0, 6.0)
    assert (plans / "revisions.jsonl").read_text(encoding="utf-8").count("\n") == 1


def test_left_edge_extension_consumes_only_existing_gap(tmp_path, monkeypatch):
    project_id, _plans = _project(tmp_path, monkeypatch, with_gap=True)
    result = _trim(project_id, 1, 2.0, 6.0)

    assert result.status == "APPLIED", result.errors
    scene = editor.load_composition(project_id).scenes[0]
    assert len(scene.layers) == 2
    video, still = scene.layers
    assert video.type == LayerType.video
    assert (video.start, video.duration, video.source_in, video.source_out) == (0.0, 4.0, 2.0, 6.0)
    assert (still.start, still.duration) == (4.0, 6.0)
    assert scene.duration == 10.0


def test_left_edge_extension_without_gap_is_rejected_without_mutation(tmp_path, monkeypatch):
    project_id, plans = _project(tmp_path, monkeypatch)
    before = editor.load_composition(project_id).model_dump(mode="json")
    result = _trim(project_id, 0, 1.0, 6.0)

    assert result.status == "REJECTED"
    assert result.errors[0]["code"] == "TRIM_BOUNDARY"
    assert editor.load_composition(project_id).model_dump(mode="json") == before
    assert (plans / "revisions.jsonl").read_text(encoding="utf-8") == ""


def test_trim_rejects_source_out_beyond_known_media_duration(tmp_path, monkeypatch):
    project_id, plans = _project(tmp_path, monkeypatch)
    before = editor.load_composition(project_id).model_dump(mode="json")
    result = editor.execute_command(project_id, {
        "command": "trim",
        "targetId": "S01#0",
        "payload": {"sourceIn": 2.0, "sourceOut": 8.2, "edge": "right"},
        "expectedVersion": 1,
    })

    assert result.status == "REJECTED"
    assert result.errors[0]["code"] == "SOURCE_RANGE_OUT_OF_BOUNDS"
    assert editor.load_composition(project_id).model_dump(mode="json") == before
    assert (plans / "revisions.jsonl").read_text(encoding="utf-8") == ""


def test_chromium_main_track_edge_drag_matches_pointer_direction_and_source_limit():
    root = Path(__file__).resolve().parents[1]
    script = root / "webui" / "static" / "js" / "timeline.js"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for edge, down_x, up_x, source_duration, expected_source_in, expected_source_out in (
                ("left", 104, 144, 8.0, 2.4, 6.0),
                ("right", 496, 536, 8.0, 2.0, 6.4),
                ("right", 496, 596, 6.2, 2.0, 6.2),
            ):
                page = browser.new_page(viewport={"width": 1200, "height": 240})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.set_content("""<!doctype html><html><head><style>
                    html,body { margin:0; padding:0; }
                    #editor-timeline { width:1000px; height:160px; }
                    .ed-tl-scroll { width:1000px; height:160px; position:relative; overflow:visible; }
                    .ed-track-body { width:1000px; height:40px; position:relative; }
                    .ed-scene { position:absolute; top:0; left:0; width:1000px; height:80px; }
                    .ed-scene-body { width:1000px; height:40px; position:relative; }
                    .ed-layer { position:absolute; top:0; left:100px; width:400px; height:24px; background:#888; }
                </style></head><body><div id="editor-timeline"><div class="ed-tl-scroll">
                    <div class="ed-track-body" id="axis"></div>
                    <div class="ed-scene" data-scene="S01"><div class="ed-scene-body">
                        <div class="ed-layer" data-scene="S01" data-idx="0"></div>
                    </div></div>
                </div></div></body></html>""")
                page.evaluate("""({sourceDuration}) => {
                    window.ED = {comp: {
                        duration: 10,
                        assets: [{id: "source-video", type:"video", duration:sourceDuration}],
                        scenes: [{id:"S01", start:0, duration:10, layers:[
                            {type:"video", asset_id:"source-video", start:0, duration:4,
                             source_in:2, source_out:6, speed:1}
                        ]}]
                    }};
                    window.__commands = [];
                    window.executeCommand = async command => { window.__commands.push(command); return {ok:true}; };
                    window.editorToast = () => {};
                    window.refreshWorkspace = async () => {};
                    window.editorRenderTimeline = () => {};
                }""", {"sourceDuration": source_duration})
                page.add_script_tag(path=str(script))
                page.evaluate("_tlTimelineAxisWidth = () => 1000; _tlBindLayerDrag();")
                page.mouse.move(down_x, 12)
                page.mouse.down()
                page.mouse.move(up_x, 12)
                page.mouse.up()
                page.wait_for_function("window.__commands.length === 1")
                command = page.evaluate("window.__commands[0]")
                assert command["command"] == "trim", command
                assert command["payload"]["edge"] == edge, command
                assert command["payload"]["sourceIn"] == expected_source_in, command
                assert command["payload"]["sourceOut"] == expected_source_out, command
                assert not errors, errors
                page.close()
        finally:
            browser.close()

def _decoded_pixel_at(path: str, seconds: float) -> tuple[int, int, int]:
    run = subprocess.run(
        [
            renderer._ffmpeg(), "-v", "error", "-ss", str(seconds), "-i", path,
            "-vf", "scale=1:1:flags=area,format=rgb24", "-frames:v", "1",
            "-f", "rawvideo", "-",
        ],
        capture_output=True, timeout=30,
    )
    assert run.returncode == 0, run.stderr.decode("utf-8", errors="replace")
    assert len(run.stdout) == 3, f"Expected one decoded RGB pixel, got {len(run.stdout)} bytes"
    return tuple(run.stdout)


def test_left_edge_gap_is_present_in_a_real_decodable_mp4(tmp_path, monkeypatch):
    project_id, _plans = _project(tmp_path, monkeypatch, real_video=True)
    result = _trim(project_id, 0, 3.0, 6.0)
    assert result.status == "APPLIED", result.errors

    ir = editor.load_composition(project_id)
    assets = {asset.id: asset for asset in ir.assets}
    rendered = renderer.render_composition(ir, assets, tmp_path / "render")
    final_path = Path(rendered.final_path)
    assert final_path.is_file() and final_path.stat().st_size > 1000

    # A real full-file decode catches broken concat/segment handling, not just MP4 existence.
    decode = subprocess.run(
        [renderer._ffmpeg(), "-v", "error", "-i", str(final_path), "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    assert decode.returncode == 0, decode.stderr

    gap_rgb = _decoded_pixel_at(str(final_path), 0.5)
    video_rgb = _decoded_pixel_at(str(final_path), 2.0)
    assert max(gap_rgb) < 20, f"left-trim gap should render black, got RGB={gap_rgb}"
    assert video_rgb[0] > 140 and video_rgb[1] < 90 and video_rgb[2] < 90, (
        f"trimmed source should begin after the explicit gap, got RGB={video_rgb}"
    )

