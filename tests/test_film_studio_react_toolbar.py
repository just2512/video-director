"""React 19 owns the primary Studio header toolbar when its verified bundle is present."""
from pathlib import Path


def test_react_studio_toolbar_has_a_compatibility_fallback():
    root = Path(__file__).resolve().parents[1]
    html = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    assert 'id="fs-react-toolbar"' in html
    assert 'id="fs-static-toolbar"' in html
    assert "const StudioToolbar: React.FC" in component
    assert "filmStudioReactToolbar?: FilmStudioReactToolbarBridge" in component
    assert "window.filmStudioReactToolbar = {" in component
    assert "mountReactToolbar();" in studio
    assert "if(legacy)legacy.style.display='none';" in studio
    assert "if(legacy)legacy.style.display='';" in studio
    assert "const StudioAssets: React.FC" in component
    assert "window.filmStudioReactAssets = {" in component
    assert "const TimelineSurface: React.FC" in component
    assert "window.filmStudioReactTimeline = {" in component
    assert "renderSurface(model)" in component
    assert "window.filmStudioReactTimeline.renderSurface(renderModel);" in studio
    assert 'data-react-timeline-clip={clip.id}' in component
    assert "window.filmStudioReactAssets.render({" in studio
    assert 'data-react-asset-list="true"' in component
    assert 'id="fs-assets-list"' in html
    assert 'id="fs-react-scrub-row"' in html
    assert 'id="fs-static-scrub-row"' in html
    assert "const StudioScrubRow: React.FC" in component
    assert "renderScrub(): void" in component
    assert "bridge.renderScrub();" in studio
