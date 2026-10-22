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
    for action in ("insert-mode", "toggle-snap", "split", "join-cut", "ripple-delete", "envelope-mode", "slide-mode", "add-track", "zoom-in", "apply-inspector"):
        assert f'data-film-action="{action}"' in component
    assert "renderTimeline(): void" in component
    assert "findTimelineSnap" in studio
    assert "updateDragAutoScroll" in studio
    assert "setInterval(function()" in studio
    assert "var minStart=(d.members||[]).reduce" in studio
    assert "fs-snap-guide" in studio
    assert "contextmenu" in studio
    assert "window.filmStudioDuplicateClips" in studio
    assert "timelineKeyframeMarks" in studio
    assert "data-film-keyframe-marker" in studio
    assert "clip.keyframeMarks.map" in component
    assert "var bridge=window.filmStudioReactToolbar" in studio
    assert "bridge.renderTimeline();" in studio
    assert "onClick={event => window.filmStudioClickClip?.(clip.id,event.nativeEvent)}" in component
    assert "activePointerId = useRef<number | null>(null)" in component
    assert "if(!reactTimeline){" in studio
    assert "window.filmStudioBeginClipDrag = function(e,el)" in studio
    assert "window.filmStudioOwnsClipGesturePointer = ownsPointer" in component
    assert "window.filmStudioOwnsClipGesturePointer(e.pointerId)" in studio
    assert "onClick={() => window.filmStudioSeek?.(tick.at)}" in component
    assert "window.filmStudioJumpToKeyframe?.(clip.id, mark.at)" in component
    assert "window.filmStudioJumpToKeyframe=function(clipId,at)" in studio
    assert "window.filmStudioJumpToKeyframe" in studio and "renderSurface" in studio
    assert "if(!reactTimeline){" in studio
    assert "window.filmStudioMoveClipGesture = function(e)" in studio
    assert "window.filmStudioCancelClipGesture?.(event.nativeEvent)" in component
    assert "window.filmStudioCancelClipGesture=function(e)" in studio
    assert "var nativeEnvelope=target&&target.closest?target.closest('[data-envelope-clip]'):null;" in studio
    assert "if(reactTimeline&&reactClip&&!nativeEnvelope)return;" in studio
    assert "if(window.filmStudioOwnsClipGesturePointer&&window.filmStudioOwnsClipGesturePointer(e.pointerId))return;" in studio
    assert "if(S.reactGesturePointerId===e.pointerId)return;" not in studio
