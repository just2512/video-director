"""A clean source checkout must retain an editable canvas inspector before bundling React."""
from pathlib import Path


def test_canvas_inspector_has_functional_unbundled_fallback():
    root = Path(__file__).resolve().parents[1]
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "function renderElementInspector(element)" in studio
    assert "data-canvas-inspector-fallback" in studio
    assert "id=\"fs-element-text\"" in studio
    assert "id=\"fs-element-width\"" in studio
    for field_id in (
        "fs-element-background-enabled", "fs-element-background-color",
        "fs-element-opacity", "fs-element-font-weight", "fs-element-text-align",
    ):
        assert f'id="{field_id}"' in studio
    assert "id=\"fs-apply-element\"" in studio
    assert "window.filmStudioSaveSelectedElement()" in studio
    assert 'id="fs-field-keyframes"' in studio
    assert "function renderFallbackKeyframeControls(clip)" in studio
    assert "function bindFallbackKeyframeEditor(clip)" in studio
    assert "data-fallback-kf-point" in studio
    assert "data-fallback-kf-add" in studio
    assert "data-fallback-kf-remove" in studio
    assert "fallbackKeyframeCurve" in studio
    assert "setPointerCapture" in studio
    assert "clip.trackLocked" in studio

    assert "window.filmStudioSaveInspector=async function()" in studio
    assert "data-film-keyframes" in studio
    assert "renderElementInspector(element);" in studio
    assert "renderElementInspector(null);" in studio
