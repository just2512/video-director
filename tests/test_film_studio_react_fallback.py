"""A clean source checkout must retain an editable canvas inspector before bundling React."""
from pathlib import Path


def test_canvas_inspector_has_functional_unbundled_fallback():
    root = Path(__file__).resolve().parents[1]
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "function renderElementInspector(element)" in studio
    assert "data-canvas-inspector-fallback" in studio
    assert "id=\"fs-element-text\"" in studio
    assert "id=\"fs-element-width\"" in studio
    assert "id=\"fs-apply-element\"" in studio
    assert "window.filmStudioSaveSelectedElement()" in studio
    assert "renderElementInspector(element);" in studio
    assert "renderElementInspector(null);" in studio
