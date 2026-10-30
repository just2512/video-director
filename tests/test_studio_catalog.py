"""Studio capability catalog reflects the canonical template runtime contract."""
from __future__ import annotations

from dataclasses import replace

from video_director import style, studio_catalog, prober, title_engine
from video_director.registries.templates import TemplateRegistry


def _stable_runtime_environment(monkeypatch):
    monkeypatch.setattr(studio_catalog, "_ffmpeg_status",
                        lambda: ("PASS", "/usr/bin/ffmpeg", "ffmpeg test fixture"))
    monkeypatch.setattr(studio_catalog, "_browser_status",
                        lambda: ("PASS", "Chromium test fixture"))
    monkeypatch.setattr(studio_catalog, "list_providers",
                        lambda: [{"id": "browser", "can_render": "AVAILABLE"},
                                 {"id": "hyperframes", "can_render": "UNAVAILABLE"}])
    # template_runtime_report calls these resolvers directly as well as the catalog
    # probe; pin them so this contract test does not depend on the host machine.
    monkeypatch.setattr(prober, "_resolve_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(prober, "_resolve_ffprobe", lambda: "/usr/bin/ffprobe")
    monkeypatch.setattr(title_engine, "find_cjk_font", lambda: "/usr/share/fonts/cjk.ttf")


def test_selected_native_style_id_reaches_the_style_contract():
    selected, warnings = style.select_style("一段普通旁白", "knowledge-explainer")

    assert selected.style_id == "knowledge-explainer"
    assert not warnings


def test_studio_catalog_only_marks_executable_whole_video_engine_and_styles(monkeypatch):
    _stable_runtime_environment(monkeypatch)
    catalog = studio_catalog.build_studio_catalog()

    final_engines = [item for item in catalog["engines"] if item["scope"] == "full_video"]
    assert [item["id"] for item in final_engines if item["selectable"]] == ["ffmpeg"]
    assert all(item["selectable"] for item in catalog["styles"])
    hyperframes = next(item for item in catalog["engines"] if item["id"] == "hyperframes")
    assert hyperframes["selectable"] is False


def test_studio_catalog_uses_real_template_compiler_and_reports_unverified_sample(monkeypatch):
    _stable_runtime_environment(monkeypatch)
    template = TemplateRegistry().get("tpl.minimal-editorial", "1.0.0")
    monkeypatch.setattr(studio_catalog.TemplateRegistry, "all", lambda self: [template])

    catalog = studio_catalog.build_studio_catalog()
    item = catalog["templates"][0]

    # This template has a real TemplateDefinition -> CompositionIR compiler path.
    # It is PARTIAL because requested text motion is not implemented, not because
    # the previously-missing compiler adapter is actually absent.
    assert item["selectable"] is True
    assert item["can_instantiate"] is True
    assert item["can_render"] is True
    assert item["status"] == "PARTIAL"
    assert item["sample_render"] == "NOT_RUN"
    assert any("文字" in warning["message"] for warning in item["warnings"])
    assert "没有从 TemplateDefinition 到 CompositionIR" not in item["detail"]


def test_studio_catalog_blocks_templates_with_an_unregistered_renderer(monkeypatch):
    _stable_runtime_environment(monkeypatch)
    template = TemplateRegistry().get("tpl.minimal-editorial", "1.0.0")
    blocked = replace(template, runtime={"renderer": "remotion", "deterministic": True})
    monkeypatch.setattr(studio_catalog.TemplateRegistry, "all", lambda self: [blocked])

    catalog = studio_catalog.build_studio_catalog()
    item = catalog["templates"][0]

    assert item["selectable"] is False
    assert item["can_instantiate"] is False
    assert item["can_render"] is False
    assert item["status"] == "BLOCKED"
    assert any(issue["code"] == "ENGINE_NOT_REGISTERED" for issue in item["blockers"])


def test_full_video_engine_status_reuses_ffmpeg_catalog_probe(monkeypatch):
    monkeypatch.setattr(
        studio_catalog, "_ffmpeg_status",
        lambda: ("PASS", "/usr/bin/ffmpeg", "ffmpeg fixture"),
    )
    result = studio_catalog.full_video_engine_status("ffmpeg")
    assert result["selectable"] is True
    assert result["status"] == "PASS"
    assert result["path"] == "/usr/bin/ffmpeg"

    unsupported = studio_catalog.full_video_engine_status("browser")
    assert unsupported["selectable"] is False
    assert unsupported["scope"] == "full_video"
