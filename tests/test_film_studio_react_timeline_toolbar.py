"""React 19 owns Film Studio editing toolbars when the verified bundle is available."""
from pathlib import Path


def test_react_studio_timeline_toolbar_has_same_authoritative_actions_and_fallback():
    root = Path(__file__).resolve().parents[1]
    html = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    assert 'id="fs-react-timeline-toolbar"' in html
    assert 'id="fs-static-timeline-toolbar"' in html
    assert "const StudioTimelineToolbar: React.FC" in component
    for action in ("insert-mode", "split", "join-cut", "ripple-delete", "envelope-mode", "slide-mode", "add-track", "zoom-in", "apply-inspector"):
        assert f'data-film-action="{action}"' in component
    assert "renderTimeline(): void" in component
    assert "window.filmStudioReactToolbar.renderTimeline();" in studio
