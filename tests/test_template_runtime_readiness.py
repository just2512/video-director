"""Runtime-readiness and real template-to-MP4 tests.

The test intentionally renders a built-in template, not just a hand-written IR:
this catches required slots that disappear during template compilation.
"""
from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from video_director import prober, renderer, title_engine
from video_director.models import AssetRef
from video_director.registries.compiler import instantiate_template
from video_director.registries.runtime import (
    style_runtime_report, template_input_issues, template_runtime_report,
)
from video_director.registries.templates import TemplateRegistry


def _generated_image(tmp_path: Path) -> AssetRef:
    ffmpeg = renderer._ffmpeg()
    image = tmp_path / "generated-template-input.png"
    run = subprocess.run(
        [
            ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
            "color=c=0x284b63:s=96x96:d=1", "-frames:v", "1", str(image),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert run.returncode == 0, run.stderr
    assert image.is_file() and image.stat().st_size > 100
    return AssetRef(
        id="hero-asset", type="image", file=str(image),
        width=96, height=96, source="generated",
    )


def test_template_required_slots_are_bound_into_visible_timed_overlays(tmp_path):
    asset = _generated_image(tmp_path)
    assets = {asset.id: asset}
    ir = instantiate_template(
        "tpl.minimal-editorial", "1.0.0", "16:9",
        {
            "title": "Template smoke title",
            "body_text": "Template smoke body",
            "hero_image": asset.id,
        },
        assets,
        "template-smoke",
    )

    title = next(w for w in ir.watermarks if w.text == "Template smoke title")
    body = next(w for w in ir.watermarks if w.text == "Template smoke body")
    assert (title.start, title.duration) == (0.0, 4.0)
    assert (body.start, body.duration) == (4.0, 10.0)
    # The body image keeps its own image-motion reference. The text's motion
    # metadata must not overwrite a visual layer's motion by accident.
    body_scene = next(s for s in ir.scenes if s.id.endswith("scene-body"))
    image_layer = next(layer for layer in body_scene.layers if layer.asset_id == asset.id)
    assert image_layer.motion_recipe_id != "motion.fade-slide"


def test_builtin_template_renders_real_decodable_mp4_with_timed_text(tmp_path):
    font = title_engine.find_cjk_font()
    assert font, (
        "真实模板样片需要可用字体；CI 应安装 fonts-wqy-microhei，"
        "用户机器需安装中文字体后才能通过模板依赖门禁。"
    )
    asset = _generated_image(tmp_path)
    ir = instantiate_template(
        "tpl.minimal-editorial", "1.0.0", "16:9",
        {
            "title": "Template MP4 smoke",
            "body_text": "This text must be rendered",
            "hero_image": asset.id,
        },
        {asset.id: asset},
        "template-mp4-smoke",
    )
    # Keep the smoke render fast, but preserve the compiled template timeline and
    # execute the same real FFmpeg renderer, timed overlay filters, and mux path.
    ir.project = ir.project.model_copy(update={"width": 320, "height": 180, "fps": 10})
    result = renderer.render_composition(ir, {asset.id: asset}, tmp_path / "render")
    output = Path(result.final_path)
    assert output.is_file() and output.stat().st_size > 1024

    probe = prober.probe_media(str(output))
    assert probe.has_video_stream
    assert probe.duration is not None and probe.duration >= 13.5
    assert (probe.width, probe.height) == (320, 180)

    decoded = subprocess.run(
        [renderer._ffmpeg(), "-v", "error", "-i", str(output), "-f", "null", "-"],
        capture_output=True, text=True, timeout=90,
    )
    assert decoded.returncode == 0, decoded.stderr


def test_template_readiness_distinguishes_partial_from_executable(monkeypatch):
    monkeypatch.setattr(prober, "_resolve_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(prober, "_resolve_ffprobe", lambda: "/usr/bin/ffprobe")
    monkeypatch.setattr(title_engine, "find_cjk_font", lambda: "/usr/share/fonts/cjk.ttf")

    template = TemplateRegistry().get("tpl.minimal-editorial", "1.0.0")
    report = template_runtime_report(template)

    assert report["can_render"] is True
    assert report["status"] == "PARTIAL"
    assert report["sample_render"] == "NOT_RUN"
    assert any(item["code"] == "TEXT_MOTION_NOT_IMPLEMENTED" for item in report["warnings"])
    assert any(
        "motion.fade-slide" in item.get("references", []) for item in report["warnings"]
    )


def test_template_runtime_blocks_missing_engine_dependency(monkeypatch):
    monkeypatch.setattr(prober, "_resolve_ffmpeg", lambda: None)
    monkeypatch.setattr(prober, "_resolve_ffprobe", lambda: None)
    monkeypatch.setattr(title_engine, "find_cjk_font", lambda: None)

    template = TemplateRegistry().get("tpl.minimal-editorial", "1.0.0")
    report = template_runtime_report(template)

    codes = {item["code"] for item in report["blockers"]}
    assert report["status"] == "BLOCKED"
    assert report["can_instantiate"] is False
    assert {"ENGINE_NOT_INSTALLED", "MEDIA_PROBE_MISSING", "TEXT_FONT_MISSING"} <= codes


def test_template_runtime_blocks_a_required_slot_that_is_not_used():
    template = TemplateRegistry().get("tpl.minimal-editorial", "1.0.0")
    orphan = replace(
        template.slots[0], id="unreferenced_required_copy",
        label="未绑定必填文案", required=True,
    )
    broken = replace(template, slots=template.slots + (orphan,))

    report = template_runtime_report(broken)

    assert report["status"] == "BLOCKED"
    assert any(
        item["code"] == "REQUIRED_SLOT_UNUSED"
        and item["slot_id"] == "unreferenced_required_copy"
        for item in report["blockers"]
    )


def test_required_text_and_file_slots_are_checked_before_instantiation():
    template = TemplateRegistry().get("tpl.minimal-editorial", "1.0.0")
    issues = template_input_issues(
        template,
        {"title": "A title", "body_text": "A body"},
        uploaded_slot_ids=set(),
    )
    assert {item["slot_id"] for item in issues} == {"hero_image"}

    issues = template_input_issues(
        template,
        {"title": "  ", "body_text": ""},
        uploaded_slot_ids={"hero_image"},
    )
    assert {item["slot_id"] for item in issues} == {"title", "body_text"}


def test_required_unsupported_slot_type_is_blocked():
    template = TemplateRegistry().get("tpl.minimal-editorial", "1.0.0")
    slot = replace(template.slots[0], id="audio", label="背景音", type="audio", required=True)
    broken = replace(template, slots=template.slots + (slot,))
    report = template_runtime_report(broken)
    assert any(
        item["code"] == "UNSUPPORTED_REQUIRED_SLOT_TYPE" and item["slot_id"] == "audio"
        for item in report["blockers"]
    )


def test_style_registry_metadata_is_not_mislabeled_as_executable_pack():
    report = style_runtime_report(SimpleNamespace(id="style.cinematic", version="1.0.0"))
    assert report["status"] == "METADATA_ONLY"
    assert report["can_render"] is False
    assert report["sample_render"] == "NOT_RUN"
