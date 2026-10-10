"""Regression tests for timeline overlay windows and render-time asset references."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from video_director import editor, renderer, title_engine
from video_director.models import CompositionIR, WatermarkLayer


def _composition():
    return CompositionIR(
        project={"width": 640, "height": 360, "fps": 30, "duration": 10.0},
        scenes=[{
            "id": "S01", "start": 0.0, "duration": 10.0, "text": "overlay test",
            "layers": [{"type": "image", "asset_id": "img", "start": 0.0, "duration": 10.0}],
        }],
    )


def _fake_render_deps(monkeypatch, calls):
    monkeypatch.setattr(title_engine.prober, "probe_media",
                        lambda _path: SimpleNamespace(width=640, height=360))
    monkeypatch.setattr(title_engine.prober, "_resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(title_engine, "find_cjk_font", lambda: "/fake/fonts/test.ttf")
    monkeypatch.setattr(title_engine, "_esc_font_path", lambda value: value)

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(title_engine.subprocess, "run", fake_run)


def test_text_watermark_is_visible_only_during_its_timeline_window(monkeypatch, tmp_path):
    calls = []
    _fake_render_deps(monkeypatch, calls)
    watermark = WatermarkLayer(
        id="wm-text-1", kind="text", text="仅在区间出现",
        start=2.0, duration=3.0, anchor="top_right",
    )

    title_engine.render_watermark_layer("input.mp4", watermark, str(tmp_path / "out.mp4"))

    vf = calls[0][calls[0].index("-vf") + 1]
    assert "fontcolor=white@0.750" in vf
    assert "enable='between(t,2.000,5.000)'" in vf


def test_image_watermark_uses_the_same_timeline_window(monkeypatch, tmp_path):
    calls = []
    _fake_render_deps(monkeypatch, calls)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"test-placeholder")
    watermark = WatermarkLayer(
        id="wm-logo-1", kind="image", asset_id=str(logo),
        start=1.0, duration=1.5, anchor="bottom_right",
    )

    title_engine.render_watermark_layer("input.mp4", watermark, str(tmp_path / "out.mp4"))

    graph = calls[0][calls[0].index("-filter_complex") + 1]
    assert "overlay=" in graph
    assert "enable='between(t,1.000,2.500)'" in graph


def test_legacy_full_length_watermark_starts_at_its_start_time(monkeypatch, tmp_path):
    calls = []
    _fake_render_deps(monkeypatch, calls)
    watermark = WatermarkLayer(
        id="wm-text-legacy", kind="text", text="延续旧格式",
        start=1.25, duration=None,
    )

    title_engine.render_watermark_layer("input.mp4", watermark, str(tmp_path / "out.mp4"))

    vf = calls[0][calls[0].index("-vf") + 1]
    assert "enable='gte(t,1.250)'" in vf


def test_overlay_asset_id_is_resolved_from_project_asset_map(tmp_path):
    logo = tmp_path / "brand.png"
    logo.write_bytes(b"test-placeholder")
    watermark = WatermarkLayer(id="wm-logo", kind="image", asset_id="asset-logo")
    asset = SimpleNamespace(file=str(logo))

    resolved = renderer._resolve_watermark_asset(watermark, {"asset-logo": asset})

    assert resolved.asset_id == str(logo.resolve())
    assert watermark.asset_id == "asset-logo"  # resolution must not mutate the persisted IR


def test_missing_overlay_asset_fails_loudly_instead_of_silently_disappearing():
    watermark = WatermarkLayer(id="wm-missing", kind="image", asset_id="missing-logo")
    with pytest.raises(renderer.RenderError, match="素材无法解析"):
        renderer._resolve_watermark_asset(watermark, {})


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan")])
def test_overlay_duration_must_be_finite_and_positive(duration):
    with pytest.raises(ValueError, match="duration"):
        WatermarkLayer(id="wm-invalid", kind="text", text="invalid", duration=duration)


def test_overlay_drag_moves_and_trims_exact_watermark_without_changing_other_layers():
    ir = _composition()
    ir.watermarks = [
        WatermarkLayer(id="wm-a", kind="text", text="A", start=1.0, duration=2.0),
        WatermarkLayer(id="wm-b", kind="text", text="B", start=5.0, duration=2.0),
    ]

    changed, stale = editor._cmd_move_watermark(
        ir, "watermark#1", {"start": 6.0, "duration": 2.0}
    )
    assert changed == ["watermark:wm-b"]
    assert stale == ["project:render"]
    assert (ir.watermarks[1].start, ir.watermarks[1].duration) == (6.0, 2.0)
    assert ir.watermarks[0].start == 1.0

    editor._cmd_trim_watermark(ir, "watermark#1", {"start": 6.5, "duration": 1.0})
    assert (ir.watermarks[1].start, ir.watermarks[1].duration) == (6.5, 1.0)


def test_overlay_move_and_trim_reject_ranges_outside_the_finished_video():
    ir = _composition()
    ir.watermarks = [WatermarkLayer(id="wm-a", kind="text", text="A", start=1.0, duration=2.0)]

    with pytest.raises(editor.EditorError, match="覆盖层移动后"):
        editor._cmd_move_watermark(ir, "watermark#0", {"start": 9.0, "duration": 2.0})
    with pytest.raises(editor.EditorError, match="覆盖层裁剪后"):
        editor._cmd_trim_watermark(ir, "watermark#0", {"start": 8.0, "duration": 3.0})
    with pytest.raises(editor.EditorError, match="索引"):
        editor._cmd_trim_watermark(ir, "watermark#2", {"start": 0.0, "duration": 1.0})


def test_new_same_kind_overlays_get_unique_ids_and_editing_preserves_timing():
    ir = _composition()
    ir.watermarks = [WatermarkLayer(
        id="wm-text", kind="text", text="Existing", start=0.0, duration=10.0
    )]

    editor._cmd_set_watermark(
        ir, "", {"kind": "text", "text": "Second", "createNew": True, "start": 2.0, "duration": 3.0}
    )
    assert len(ir.watermarks) == 2
    assert len({w.id for w in ir.watermarks}) == 2

    editor._cmd_set_watermark(
        ir, "watermark#1", {"kind": "text", "text": "Edited", "anchor": "top_left"}
    )
    assert ir.watermarks[0].text == "Existing"
    assert ir.watermarks[1].text == "Edited"
    assert (ir.watermarks[1].start, ir.watermarks[1].duration) == (2.0, 3.0)

    editor._cmd_remove_watermark(ir, "watermark#0", {})
    assert [w.id for w in ir.watermarks] == [ir.watermarks[0].id]
    assert ir.watermarks[0].text == "Edited"
