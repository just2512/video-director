"""React 19 owns Film Studio editing toolbars when the verified bundle is available."""
from pathlib import Path


def test_react_studio_timeline_toolbar_has_same_authoritative_actions_and_fallback():
    root = Path(__file__).resolve().parents[1]
    html = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    store = (root / "remotion_runtime" / "src" / "film-store.ts").read_text(encoding="utf-8")
    assert 'id="fs-react-timeline-toolbar"' in html
    assert 'id="fs-static-timeline-toolbar"' in html
    assert "const StudioTimelineToolbar: React.FC" in component
    for action in ("insert-mode", "toggle-snap", "split", "join-cut", "ripple-delete", "envelope-mode", "slide-mode", "add-track", "zoom-in", "apply-inspector"):
        assert f'data-film-action="{action}"' in component
    assert "renderTimeline(): void" in component
    assert "pixelsPerSecond: number;" in store
    assert '["pxPerSec", "pixelsPerSecond"]' in store
    assert 'scroll.addEventListener("wheel", onWheel, {passive:false})' in component
    assert 'data-react-scrub-ready="true"' in component
    assert 'role="slider"' in component
    assert 'aria-label="时间尺播放头位置；点击刻度跳转，拖动或使用方向键定位"' in component
    assert 'event.key !== "ArrowLeft" && event.key !== "ArrowRight"' in component
    assert "event.shiftKey ? 0.1 : 1" in component
    assert "activeScrubPointerId = useRef<number | null>(null)" in component
    assert "seekFromRulerPointer(event.clientX, event.currentTarget)" in component
    ruler_scrub_handlers = component.split('data-react-scrub-ready="true"', 1)[1]
    ruler_pointerdown = ruler_scrub_handlers.split("onPointerDown={event => {", 1)[1].split("onPointerMove", 1)[0]
    assert "event.currentTarget.setPointerCapture(event.pointerId)" in ruler_pointerdown
    assert "Math.hypot(dx, dy) < 4" in ruler_scrub_handlers
    assert 'target.closest(".fs-ruler-mark")' in component
    assert 'tickAt: number | null' in component
    assert 'window.filmStudioSeek?.(pending.tickAt)' in component
    assert 'onClick={() => window.filmStudioSeek?.(tick.at)}' not in component
    assert "onPointerCancel={event => {" in component
    assert "timelineRoot.addEventListener('pointerdown'" not in studio
    assert 'scroll.dataset.reactZoomReady = "true"' in component
    assert "bindFilmTimelineWheel" not in studio
    assert "findTimelineSnap" in studio
    assert "timelineDragDeltaSeconds(pointerDelta,scrollDelta,scale)" in studio
    assert "math.resolveSnapCandidate(rawAt,candidates,S.pxPerSec,thresholdPx)" in studio
    assert "resolveTimelineMove(minStart,deltaSeconds,chosenIds,8)" in studio
    assert "resolveMoveGesture(minimumStart: number, rawDeltaSeconds: number" in (root / "remotion_runtime" / "src" / "film-timeline-math.ts").read_text(encoding="utf-8")
    assert "type FilmTimelineGesturePreviewGeometry" in component
    assert "setGesturePreview(preview: FilmTimelineGesturePreview): void" in component
    assert "data-react-gesture-preview={previewForClip(clip.id) ? \"true\" : \"false\"}" in component
    assert "previewForClip(clip.id)?.width ?? filmTimelineMath.clipWidth" in component
    assert "setTimelineGesturePreview(previewLeftByClipId)" in studio
    assert "resolveTrimStart(originalAt: number, originalEnd: number" in (root / "remotion_runtime" / "src" / "film-timeline-math.ts").read_text(encoding="utf-8")
    assert "resolveTrimEnd(originalAt: number, originalEnd: number" in (root / "remotion_runtime" / "src" / "film-timeline-math.ts").read_text(encoding="utf-8")
    assert "resolveTimelineTrim('start',d.originalAt,d.originalEnd,deltaSeconds,[d.id],8)" in studio
    assert "resolveTimelineTrim('end',d.originalAt,d.originalEnd,deltaSeconds,[d.id],8)" in studio
    assert "resolveRollGesture(rightAt: number, rawDeltaSeconds: number" in (root / "remotion_runtime" / "src" / "film-timeline-math.ts").read_text(encoding="utf-8")
    assert "resolveSlideGesture(centerAt: number, rawDeltaSeconds: number" in (root / "remotion_runtime" / "src" / "film-timeline-math.ts").read_text(encoding="utf-8")
    assert "resolveTimelineRoll(d,deltaSeconds,8)" in studio
    assert "resolveTimelineSlide(d,deltaSeconds,8)" in studio
    assert "if(!setTimelineGesturePreview(trimStartPreview))" in studio
    assert "if(!setTimelineGesturePreview(trimEndPreview))" in studio
    assert "if(!setTimelineGesturePreview(rollPreview))" in studio
    assert "if(!setTimelineGesturePreview(slidePreview))" in studio
    assert "if(!setTimelineGesturePreview(slipPreview))" in studio
    assert "clearTimelineGesturePreview();" in studio
    assert "dragDeltaSeconds(pointerDeltaPixels: number, scrollDeltaPixels: number" in (root / "remotion_runtime" / "src" / "film-timeline-math.ts").read_text(encoding="utf-8")
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
    assert "var reactOwnsGestureSurface=!!(gestureBridge&&typeof gestureBridge.setGesturePreview==='function'&&typeof gestureBridge.setActiveDragIds==='function');" in studio
    assert "if(!reactOwnsGestureSurface)" in studio
    assert "if(bridge&&typeof bridge.renderSurface==='function')return;" in studio
    assert "Store.selectedIdsVersion" in studio
    assert "element=Array.from($('fs-timeline').querySelectorAll('[data-clip]'))" in studio
    assert "if(!reactOwnsGestureSurface&&(!leftElement||!rightElement))" in studio

    assert "var blockedMember=null;" in studio
    assert "function groupMoveTargetError(d,targetTrackId)" in studio
    assert "d.invalidTargetMessage" in studio
    assert "fs-track-drop-invalid" in studio
    assert "var blockedLabel=blockedMember.reason==='已锁定'?'锁定':'不存在的';" in studio
    assert "多选移动包含'+blockedLabel+blockedMember.kind" in studio
    assert "window.filmStudioOwnsClipGesturePointer = ownsPointer" in component
    assert "window.filmStudioOwnsClipGesturePointer(e.pointerId)" in studio
    assert "window.filmStudioSeek?.(pending.tickAt)" in component
    assert 'event.stopPropagation();\n          const step = event.shiftKey ? 0.1 : 1;' in component
    assert 'onClick={() => window.filmStudioSeek?.(tick.at)}' not in component
    assert 'className="fs-react-timeline-surface" data-react-timeline-surface="true"' in component
    assert 'style={{width:model.totalWidth,position:"relative"}}' in component
    assert "if(!reactTimeline)$('fs-timeline').style.width=" in studio
    assert 'const playhead=useFilmStoreField("playhead");' in component
    assert 'style={{left:132 + filmTimelineMath.clipLeft(playhead,model.pixelsPerSecond)}}' in component
    assert "if(line&&!reactTimeline)line.style.left=" in studio
    assert "window.filmStudioJumpToKeyframe?.(clip.id, mark.at)" in component
    assert "onPointerDown={(event) => {\n                    // A marker is a seek target" in component
    assert "timelineRoot.addEventListener('pointerdown'" not in studio
    assert "if(timelineRoot&&!reactOwnsTimeline&&!timelineRoot.__filmStudioKeyframeDelegationBound)" in studio
    assert "if(!marker)return;\n        event.preventDefault();event.stopPropagation();" in studio
    assert "window.filmStudioJumpToKeyframe=function(clipId,at)" in studio
    assert "window.filmStudioJumpToKeyframe" in studio and "renderSurface" in studio
    assert "clearSurface(): void;" in component
    assert "hasSurface(): boolean;" in component
    assert "typeof timelineBridge.clearSurface==='function')timelineBridge.clearSurface();" in studio
    assert "typeof timelineBridge.hasSurface==='function'&&!timelineBridge.hasSurface()" in studio
    assert "if(!reactTimeline){" in studio
    assert "window.filmStudioMoveClipGesture = function(e)" in studio
    assert "window.filmStudioCancelClipGesture?.(event.nativeEvent)" in component
    assert "window.filmStudioCancelClipGesture=function(e)" in studio
    assert "var nativeEnvelope=target&&target.closest?target.closest('[data-envelope-clip]'):null;" in studio
    assert "if(reactTimeline&&reactClip&&!nativeEnvelope)return;" in studio
    assert "if(window.filmStudioOwnsClipGesturePointer&&window.filmStudioOwnsClipGesturePointer(e.pointerId))return;" in studio
    assert "window.filmStudioBeginEnvelopeGesture?.(event.nativeEvent, event.currentTarget)" in component
    assert "window.filmStudioOwnsEnvelopeGesturePointer = ownsEnvelopePointer" in component
    assert "onClick={event => event.stopPropagation()}" in component
    assert "window.filmStudioBeginEnvelopeGesture=beginVolumeEnvelopeGesture" in studio
    assert "window.filmStudioOwnsEnvelopeGesturePointer(e.pointerId)" in studio
    assert "window.filmStudioOpenTimelineContextMenu?.(clip.id,event.clientX,event.clientY)" in component
    assert "window.filmStudioContextMenuAction?.(name, model.clipId)" in component
    assert "data-react-context-menu=\"true\"" in component
    assert "openContextMenu(model: FilmTimelineContextMenu): void;" in component
    assert "window.filmStudioOpenTimelineContextMenu=function(clipId,clientX,clientY)" in studio
    assert "window.filmStudioContextMenuAction=function(action,clipId)" in studio
    assert "bridge.isContextMenuOpen==='function'&&bridge.isContextMenuOpen()" in studio
    assert "setSnapGuide(guide: FilmTimelineSnapGuide): void;" in component
    assert "setTrackDropTarget(target: FilmTimelineDropTarget): void;" in component
    assert "setActiveDragIds(ids: FilmTimelineActiveDragIds): void;" in component
    assert "activeDragIds?.includes(clip.id) ? \" fs-dragging\" : \"\"" in component
    assert "bridge.setActiveDragIds!=='function'" in studio
    assert "if(!reactOwnsActiveDrag)members.forEach(function(member){member.element.classList.add('fs-dragging');});" in studio
    assert 'id="fs-snap-guide"' in component
    assert "dropTarget?.trackId === track.id" in component
    assert "bridge.setSnapGuide==='function'" in studio
    assert "bridge.setTrackDropTarget==='function'" in studio
    assert "if(!setTimelineDropTarget(nextTrack,!!targetError))" in studio
    assert "clearDragAutoScroll();\n    showTimelineSnapGuide(null);" in studio
    assert "if(S.reactGesturePointerId===e.pointerId)return;" not in studio
