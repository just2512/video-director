"""React 19 clip inspector ownership contract."""
from pathlib import Path


def test_react_clip_inspector_is_wired_through_film_studio_bridge():
    root = Path(__file__).resolve().parents[1]
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    assert "renderClip(clip)" in studio
    assert "window.filmStudioReactInspector.renderClip(clip)" in studio
    assert "const ClipInspector: React.FC" in component
    assert 'data-react-clip-inspector="true"' in component
    assert 'id="fs-field-at"' in component
    assert 'id="fs-field-speed"' in component
    assert 'id="fs-field-volume"' in component
    assert 'id="fs-field-fade-in"' in component
    assert 'id="fs-field-title"' in component
    assert "filmStudioSaveInspector?: () => Promise<void> | void" in component
    assert 'id="fs-apply-clip-inspector"' in component
    assert "window.filmStudioSaveInspector?.()" in component
