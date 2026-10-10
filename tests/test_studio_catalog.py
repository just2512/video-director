"""Studio capability catalog must reflect executable contracts, not metadata presence."""
from __future__ import annotations

from video_director import style
from video_director import studio_catalog


def test_selected_native_style_id_reaches_the_style_contract():
    selected, warnings = style.select_style("一段普通旁白", "knowledge-explainer")

    assert selected.style_id == "knowledge-explainer"
    assert not warnings


def test_studio_catalog_only_marks_executable_whole_video_engine_and_styles(monkeypatch):
    monkeypatch.setattr(studio_catalog, "_ffmpeg_status",
                        lambda: ("PASS", "/usr/bin/ffmpeg", "ffmpeg test fixture"))
    monkeypatch.setattr(studio_catalog, "_browser_status",
                        lambda: ("PASS", "Chromium test fixture"))
    monkeypatch.setattr(studio_catalog, "list_providers",
                        lambda: [{"id": "browser", "can_render": "AVAILABLE"},
                                 {"id": "hyperframes", "can_render": "UNAVAILABLE"}])

    catalog = studio_catalog.build_studio_catalog()

    final_engines = [item for item in catalog["engines"] if item["scope"] == "full_video"]
    assert [item["id"] for item in final_engines if item["selectable"]] == ["ffmpeg"]
    assert all(item["selectable"] for item in catalog["styles"])
    # Template metadata is not executable until a real TemplateDefinition -> CompositionIR
    # instantiator exists; never present it as something the user can apply.
    assert all(not item["selectable"] and item["status"] == "MISSING"
               for item in catalog["templates"])
    assert any("实例化执行适配器" in item["detail"] for item in catalog["templates"])
    hyperframes = next(item for item in catalog["engines"] if item["id"] == "hyperframes")
    assert hyperframes["selectable"] is False
