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
    assert 'useFilmStoreField("envelopeMode")' in component
    assert 'useFilmStoreField("slideMode")' in component
    assert 'useFilmStoreField("slipMode")' in component
    assert '(envelopeMode ? " fs-envelope-mode" : "")' in component
    assert '(slideMode ? " fs-slide-mode" : "")' in component
    assert '(slipMode ? " fs-slip-mode" : "")' in component
    assert studio.count("if(panel&&!reactToolbar){") >= 3
    assert 'const playing=useFilmStoreField("playing");' in component
    assert 'aria-pressed={playing} title={playing ? "暂停预览" : "播放预览"}' in component
    assert 'playing: boolean;' in store
    assert '["playing", "playing"]' in store
    assert "function setPlaybackState(playing)" in studio
    assert "function syncPlaybackButtonText()" in studio
    assert "typeof bridge.renderScrub==='function')return;" in studio
    assert "var button=$('fs-play-toggle');if(button)button.textContent" not in studio
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
    timeline_math = (root / "remotion_runtime" / "src" / "film-timeline-math.ts").read_text(encoding="utf-8")
    assert "frameRate?: number" in timeline_math
    assert 'kind:"帧"' in timeline_math
    assert "function timelineFrameRate()" in studio
    assert "math.resolveSnapCandidate(rawAt,candidates,S.pxPerSec,thresholdPx,timelineFrameRate())" in studio
    assert "math.resolveSlipGesture(rawDeltaSeconds,playbackSpeed,S.snapEnabled,timelineFrameRate())" in studio
    assert "resolveTimelineMove(minStart,deltaSeconds,chosenIds,8)" in studio
    assert "function timelineIsFrameAligned(value)" in studio
    assert "function timelineOperationNumber(value,preserveFramePrecision)" in studio
    assert "timelineOperationNumber(trimEndCommit.target" in studio
    assert "timelineOperationNumber(slideDelta" in studio
    assert "timelineOperationNumber(Math.max(0,member.originalAt+delta)" in studio
    assert "resolveMoveGesture(minimumStart: number, rawDeltaSeconds: number" in (root / "remotion_runtime" / "src" / "film-timeline-math.ts").read_text(encoding="utf-8")
    controller = (root / "remotion_runtime" / "src" / "film-timeline-gesture-controller.ts").read_text(encoding="utf-8")
    assert "resolveRollGesture" in controller and "resolveSlideGesture" in controller and "resolveSlipGesture" in controller
    assert "controller.resolveRollGesture" in studio
    assert "controller.resolveSlideGesture" in studio
    assert "controller.resolveSlipGesture" in studio
    assert "slideCommitResolution.operation||" in studio
    assert "rollCommitResolution.operation||" in studio
    assert "slipResolution.operation||" in studio
    # Trim source bounds must survive the shared FilmStore's strict gesture-context normalizer.
    assert "clipTag: string | undefined;" in store
    assert 'const clipTag = optionalString("clipTag");' in store
    assert 'const originalSourceIn = optionalNumber("originalSourceIn");' in store
    assert 'const originalSpeed = optionalNumber("originalSpeed");' in store
    assert "clipTag," in store and "originalSourceIn," in store and "originalSpeed," in store
    assert "type FilmTimelineGesturePreviewGeometry" in component
    assert "setGesturePreview(preview: FilmTimelineGesturePreview): void" in component
    assert "targetTrackId?: string;" in (root / "remotion_runtime" / "src" / "film-store.ts").read_text(encoding="utf-8")
    assert "previewEntry.targetTrackId=destinationTrackId" in studio
    assert "crossTrackGhostsByTrack" in component
    assert 'data-gesture-ghost-for={clip.id}' in component
    assert 'data-gesture-target-track={track.id}' in component
    assert "fs-clip-gesture-ghost" in (root / "webui" / "static" / "css" / "film-studio.css").read_text(encoding="utf-8")
    assert "timelineGesturePreview: FilmStudioTimelineGesturePreview;" in store
    assert "timelineSnapGuide: FilmStudioTimelineSnapGuide;" in store
    assert "timelineDropTarget: FilmStudioTimelineDropTarget;" in store
    assert "timelineActiveDragIds: FilmStudioTimelineActiveDragIds;" in store
    assert "timelineEnvelopePreview: FilmStudioTimelineEnvelopePreview;" in store
    assert "timelineEnvelopeGesture: FilmStudioTimelineEnvelopeGesture;" in store
    assert "const asTimelineEnvelopeGesture =" in store
    assert "normalized = asTimelineEnvelopeGesture(value);" in store
    assert '["envelopeDrag", "timelineEnvelopeGesture"]' in store
    assert "pointerId: number;" in store
    assert "rect: FilmStudioTimelineEnvelopeRect;" in store
    assert "canvas:canvas||null" not in studio
    assert "var envelopeDomCanvas = null;" in studio
    assert "updateTimelineEnvelopeGesture({" in studio
    assert "setEnvelopePreview(preview: FilmStudioTimelineEnvelopePreview): void;" in component
    assert 'useFilmStoreField("timelineEnvelopePreview")' in component
    assert "const drawVolumeEnvelopeCanvas =" in component
    assert "canvas.dataset.reactEnvelopePainted = \"true\";" in component
    assert "data-react-envelope-source={isPreview ? \"preview\" : \"document\"}" in component
    assert "setTimelineEnvelopePreview(clip.id,points)" in studio
    assert "updateTimelineEnvelopeGesture({" in studio
    assert "points:points,active:true,changed:true" in studio
    assert "setTimelineEnvelopePreview(ed.clipId,points)" in studio
    assert "S.envelopeDrag={clipId:clip.id,pointerId:e.pointerId,rect:rect,points:points,index:index" in studio
    assert "canvas:canvas||null" not in studio
    assert "math.resolveEnvelopePointerDown(points,duration,rect.width,rect.height" in studio
    assert "math.moveEnvelopeKeyframe(ed.points,ed.index,ed.duration,rect.width,rect.height" in studio
    assert "math.normalizeEnvelopePoints(envelopeEdit.points)" in studio
    assert "filmStudioMoveEnvelopeGesture=function(e,rect)" in studio
    assert "filmStudioBeginEnvelopeGesture?: (event: PointerEvent, context: {clipId: string; rect:" in component
    assert "if(reactTimeline)return;" in studio
    assert "type FilmStudioTimelineWaveformSource = {duration: number; peaks: Uint8Array};" in store
    assert "timelineWaveformSources: FilmStudioTimelineWaveformSources;" in store
    assert 'useFilmStoreField("timelineWaveformSources")' in component
    assert "const AudioWaveformCanvas: React.FC<AudioWaveformCanvasProps>" in component
    assert "canvas.dataset.reactWaveformPainted = \"true\";" in component
    assert "data-waveform-key={waveformKey}" in component
    assert "function readTimelineWaveformSources()" in studio
    assert "function writeTimelineWaveformSource(key,source)" in studio
    assert "S.waveformCache" not in studio
    assert "syncTimelineWaveformSources" not in studio
    assert "bridge.setField('timelineWaveformSources',next)" in studio
    assert "删除并保留空隙" in component
    assert "波纹删除（收拢时间线）" in component
    assert "删除并保留空隙" in studio
    assert "波纹删除（收拢时间线）" in studio
    assert "并保留时间线空隙" in studio
    assert "if(reactTimeline)return;" in studio

    assert "setField<K extends keyof FilmStudioStoreSnapshot>(key: K, value: FilmStudioStoreSnapshot[K]): void;" in store
    assert 'useFilmStoreField("timelineGesturePreview")' in component
    assert 'useFilmStoreField("timelineSnapGuide")' in component
    assert 'useFilmStoreField("timelineDropTarget")' in component
    assert 'useFilmStoreField("timelineActiveDragIds")' in component
    assert "let updateTimelineGesturePreview:" not in component
    assert "let updateTimelineSnapGuide:" not in component
    assert "let updateTimelineDropTarget:" not in component
    assert "let updateTimelineActiveDragIds:" not in component
    assert "data-react-gesture-preview={previewForClip(clip.id) ? \"true\" : \"false\"}" in component
    assert "previewForClip(clip.id)?.width ?? filmTimelineMath.clipWidth" in component
    assert "setTimelineGesturePreview(previewLeftByClipId)" in studio
    assert "type FilmStudioTimelineGestureContext =" in store
    assert "timelineGestureContext: FilmStudioTimelineGestureContext | null;" in store
    assert "primaryMember.originalAt !== originalAt" in store
    assert 'mode !== "move" && members.length !== 1' in store
    assert "(id !== leftClipId && id !== rightClipId)" in store
    assert "centerClipId !== id" in store
    assert "if (!memberId || memberIds.has(memberId)" in store
    assert "!Number.isInteger(pointerId) || pointerId < 0" in store
    assert "if (!memberIds.has(id)) return null;" in store

    assert '["drag", "timelineGestureContext"]' in store
    assert "function updateTimelineGestureContext(patch)" in studio
    assert "var gestureDomRefs = null;" in studio
    assert "d.element" not in studio
    assert "d.slideElements" not in studio
    assert "d.rollElements" not in studio
    assert "member.element" not in studio
    assert "d.targetRow" not in studio
    assert "resolveTrimStart(originalAt: number, originalEnd: number" in (root / "remotion_runtime" / "src" / "film-timeline-math.ts").read_text(encoding="utf-8")
    assert "resolveTrimEnd(originalAt: number, originalEnd: number" in (root / "remotion_runtime" / "src" / "film-timeline-math.ts").read_text(encoding="utf-8")
    assert "resolveTimelineTrim('start',d.originalAt,d.originalEnd,deltaSeconds,[d.id],8)" in studio
    assert "resolveTimelineTrim('end',d.originalAt,d.originalEnd,deltaSeconds,[d.id],8)" in studio
    assert "resolveRollGesture(rightAt: number, rawDeltaSeconds: number" in (root / "remotion_runtime" / "src" / "film-timeline-math.ts").read_text(encoding="utf-8")
    assert "resolveSlideGesture(centerAt: number, rawDeltaSeconds: number" in (root / "remotion_runtime" / "src" / "film-timeline-math.ts").read_text(encoding="utf-8")
    math_source = (root / "remotion_runtime" / "src" / "film-timeline-math.ts").read_text(encoding="utf-8")
    assert "resolveSlipGesture(rawDeltaSeconds: number, playbackSpeed: number, snapEnabled?: boolean, frameRate?: number): FilmTimelineSlipResolution;" in math_source
    assert math_source.count("filmTimelineMath.resolveSnapCandidate(rawTarget, candidates, pixelsPerSecond, thresholdPixels, frameRate)") >= 2
    assert math_source.count("filmTimelineMath.resolveSnapCandidate(anchor + rawDelta, candidates, pixelsPerSecond, thresholdPixels, frameRate)") >= 2
    assert "var slipResolution=resolveTimelineSlip(deltaSeconds,slipSpeed,d.id);" in studio
    assert "var sourceDelta=slipResolution.sourceDeltaSeconds;" in studio
    assert "var slipResolution=resolveTimelineSlip(deltaSeconds,num(slipClip.attrs.speed,1),d.id);" in studio

    assert "resolveTimelineRoll(d,deltaSeconds,8)" in studio
    assert "resolveTimelineSlide(d,deltaSeconds,8)" in studio
    controller_source = (root / "remotion_runtime" / "src" / "film-timeline-gesture-controller.ts").read_text(encoding="utf-8")
    assert "const resolveTrimGesture = (input: FilmTimelineTrimGestureInput)" in controller_source
    assert "const buildMovePreview = (input: FilmTimelineMoveControllerInput): FilmTimelineMovePreviewResult =>" in controller_source
    assert "operations: buildMoveOperationsForResolution(input, resolution)" in controller_source
    assert "moveControllerResult=moveController.buildMovePreview({" in studio
    assert "moveControllerResult&&moveControllerResult.operations" in studio
    assert "await filmStudioCommit(moveControllerOperations);return;" in studio
    assert "resolveRollGesture" in controller_source
    assert "resolveSlideGesture" in controller_source
    assert "resolveSlipGesture" in controller_source
    assert "controller.resolveRollGesture" in studio
    assert "controller.resolveSlideGesture" in studio
    assert "controller.resolveSlipGesture" in studio
    assert "rollCommitResolution.operation||" in studio
    assert "slideCommitResolution.operation||" in studio
    assert "slipResolution.operation||" in studio
    assert "FilmTimelineControllerPreview" in controller_source
    assert "preview: FilmTimelineControllerPreview;" in controller_source
    assert "rollResolution.preview||null" in studio
    assert "slideResolution.preview||null" in studio
    assert "slipResolution.preview||{}" in studio
    assert 'mode: "roll"' in controller_source and 'mode: "slide"' in controller_source and 'mode: "slip"' in controller_source
    gesture_start = studio[studio.index("function beginClipGesture"):studio.index("window.filmStudioBeginClipGesture")]
    assert "if(mode==='move'){S.playhead=clip.at;setPlayheadVisual();previewCurrent();}" in gesture_start
    assert "S.playhead=clip.at;setPlayheadVisual();previewCurrent();\n        var mode=" not in gesture_start
    assert "if(d.mode==='slide'||d.mode==='slip')" in studio
    assert "var clickedClip=findClip(d.id);" in studio
    gesture_preview = studio[studio.index("function onMove(e)"):studio.index("async function onUp(e)")]
    assert "showTimelineSnapGuide(slideSnapUsed?slideSnapUsed.at:null,slideSnapUsed&&slideSnapUsed.kind)" in gesture_preview
    assert "showTimelineSnapGuide(rollSnapUsed?rollSnapUsed.at:null,rollSnapUsed&&rollSnapUsed.kind)" in gesture_preview
    assert "slideSnap.at" not in gesture_preview
    assert "rollSnap.at" not in gesture_preview
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
    assert 'const rendering = useFilmStoreField("rendering");' in component
    assert 'disabled={rendering} aria-busy={rendering}' in component
    assert 'rendering: boolean;' in store
    assert '["rendering", "rendering"]' in store
    render_function = studio.split("window.filmStudioRender=async function(){", 1)[1].split("function findProposalClip(", 1)[0]
    assert "button.disabled" not in render_function

    assert "function reactOwnsTimelineToolbar()" in studio
    assert "if(reactOwnsTimelineToolbar())return;" in studio
    assert "if(!reactOwnsTimelineToolbar()){" in studio
    assert "reactToolbar?null:document.querySelector('[data-film-action=\"envelope-mode\"]')" in studio
    assert "reactToolbar?null:document.querySelector('[data-film-action=\"slide-mode\"]')" in studio
    assert "reactToolbar?null:document.querySelector('[data-film-action=\"slip-mode\"]')" in studio
    assert "onClick={event => window.filmStudioClickClip?.(clip.id,event.nativeEvent)}" in component
    assert "activePointerId = useRef<number | null>(null)" in component
    assert "if(!reactTimeline){" in studio
    assert "window.filmStudioBeginClipGesture?.(event.nativeEvent,{" in component
    assert "clipId:clip.id" in component
    assert "targetIsButton:!!elementTarget?.closest(\"button\")" in component
    assert "event.currentTarget.setPointerCapture(event.nativeEvent.pointerId)" in component
    assert "window.filmStudioBeginClipGesture=function(e,context)" in studio
    assert "window.filmStudioBeginClipDrag = function(e,el)" in studio
    assert "return beginClipGesture(e,context,null);" in studio
    assert "var clipId=String(context.clipId||(el&&el.getAttribute('data-clip'))||'');" in studio
    assert "var trimEdge=context.trimEdge==='start'||context.trimEdge==='end'?context.trimEdge:null" in studio
    assert "var edge=trimEdge;" in studio
    assert "var reactOwnsGestureSurface=!!(gestureBridge&&typeof gestureBridge.setGesturePreview==='function'&&typeof gestureBridge.setActiveDragIds==='function');" in studio
    assert "if(!reactOwnsGestureSurface)" in studio
    assert "if(bridge&&typeof bridge.renderSurface==='function')return;" in studio
    assert "Store.selectedIdsVersion" in studio
    assert "element=Array.from($('fs-timeline').querySelectorAll('[data-clip]'))" in studio
    assert "if(!reactOwnsGestureSurface&&(!leftElement||!rightElement))" in studio

    assert "var blockedMember=null;" in studio
    assert "function groupMoveTargetError(d,targetTrackId,rawDeltaSeconds)" in studio
    assert "resolveTimelineMove(minStart,Number(rawDeltaSeconds)||0,selectedIds,8)" in studio
    assert "多选移动后片段 '+member.id+' 与目标轨道 '+destinationId+' 上的片段 '+other.id+' 时间重叠" in studio
    assert "d.invalidTargetMessage" in studio
    assert "fs-track-drop-invalid" in studio
    assert "var blockedLabel=blockedMember.reason==='已锁定'?'锁定':'不存在的';" in studio
    assert "多选移动包含'+blockedLabel+blockedMember.kind" in studio
    assert "window.filmStudioOwnsClipGesturePointer = ownsPointer" in component
    assert "window.filmStudioOwnsClipGesturePointer(e.pointerId)" in studio
    assert "window.filmStudioSeek?.(pending.tickAt)" in component
    assert 'event.stopPropagation();\n          const step = event.shiftKey ? 0.1 : 1;' in component
    assert 'onClick={() => window.filmStudioSeek?.(tick.at)}' not in component
    assert 'className={"fs-react-timeline-surface" +' in component
    assert 'data-react-timeline-surface="true"' in component
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
    assert "function mountDirectorPlanning(allowLegacyCompositionCommands)" in studio
    assert 'id="fs-director-authority-note"' in html
    assert 'id="fs-director-legacy-note"' in html
    load_valid = studio.split("if(r&&r.ok&&r.exists){", 1)[1].split("if(r&&r.ok&&!r.exists)", 1)[0]
    load_migration = studio.split("if(r&&r.ok&&!r.exists){", 1)[1].split("if(r&&!r.ok&&r.exists===true)", 1)[0]
    load_invalid = studio.split("function renderBrokenFilmState(result){", 1)[1].split("var message=", 1)[0]
    initialize = studio.split("async function filmStudioInitialize(acceptWarnings)", 1)[1].split("function inspectorFieldDiffersNumber", 1)[0]
    assert "mountDirectorPlanning(false);" in load_valid
    assert "mountDirectorPlanning(true);" in load_migration
    assert "mountDirectorPlanning(false);" in load_invalid
    assert "mountDirectorPlanning(false);" in initialize
    planning_mode = studio.split("function mountDirectorPlanning(allowLegacyCompositionCommands)", 1)[1].split("function restoreDirectorPlanning(){", 1)[0]
    assert planning_mode.index("restoreDirectorPlanningNode('director-bar-card')") < planning_mode.index("restoreDirectorPlanningNode('director-control-grid')")
    assert "restoreDirectorPlanningNode('director-control-grid')" in studio
    assert "restoreDirectorPlanningNode('director-bar-card')" in studio
    assert "if(!reactTimeline){" in studio
    assert "window.filmStudioMoveClipGesture = function(e)" in studio
    assert "window.filmStudioCancelClipGesture?.(event.nativeEvent)" in component
    assert "window.filmStudioCancelClipGesture=function(e)" in studio
    assert "sourceLabels={" in studio
    assert "已载入影片新 revision。修改来源：" in studio
    assert "pendingRevisionSource=data.source||'external_file';" in studio
    assert "pendingRevisionSource='external_file';" in studio
    assert "var nativeEnvelope=target&&target.closest?target.closest('[data-envelope-clip]'):null;" in studio
    assert "if(reactTimeline&&reactClip&&!nativeEnvelope)return;" in studio
    assert "if(window.filmStudioOwnsClipGesturePointer&&window.filmStudioOwnsClipGesturePointer(e.pointerId))return;" in studio
    assert "window.filmStudioBeginEnvelopeGesture?.(event.nativeEvent, {" in component
    assert "clipId:clip.id" in component
    assert "rect:{left:bounds.left,top:bounds.top,width:bounds.width,height:bounds.height}" in component
    assert "window.filmStudioOwnsEnvelopeGesturePointer = ownsEnvelopePointer" in component
    assert "window.filmStudioBeginEnvelopeGesture=function(e,context)" in studio
    assert "S.envelopeDrag={clipId:clip.id,pointerId:e.pointerId,rect:rect,points:points,index:index" in studio
    assert "var ed=S.envelopeDrag;" in studio
    assert "if(!e||e.pointerId!==ed.pointerId)return;" in studio
    assert "var rect=envelopeDomCanvas?envelopeDomCanvas.getBoundingClientRect():ed.rect;" in studio
    assert "if(!setTimelineEnvelopePreview(ed.clipId,points)&&envelopeDomCanvas)drawVolumeEnvelope(envelopeDomCanvas,points);" in studio
    assert "var envelopeDomCanvas = null;" in studio
    assert "canvas:canvas||null" not in studio
    assert "window.filmStudioMoveEnvelopeGesture?.(event.nativeEvent, {" in component
    assert "onClick={event => event.stopPropagation()}" in component
    assert "return beginVolumeEnvelopeGesture(e,context,null);" in studio
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
    assert "if(!reactOwnsActiveDrag)members.forEach(function(member){" in studio
    assert "var element=gestureDomRefs&&gestureDomRefs.memberElements[member.id];" in studio
    assert "if(element)element.classList.add('fs-dragging');" in studio
    assert 'id="fs-snap-guide"' in component
    assert "dropTarget?.trackId === track.id" in component
    assert "bridge.setSnapGuide==='function'" in studio
    assert "bridge.setTrackDropTarget==='function'" in studio
    assert "if(!setTimelineDropTarget(nextTrack,!!targetError))" in studio
    assert "var outsideTrackMessage='请将片段放置到有效轨道上，多选移动已取消。';" in studio
    assert "d=updateTimelineGestureContext({targetTrackId:d.trackId,invalidTargetMessage:outsideTrackMessage})||d;" in studio
    assert "clearDragAutoScroll();\n    showTimelineSnapGuide(null);" in studio
    assert "if(S.reactGesturePointerId===e.pointerId)return;" not in studio
