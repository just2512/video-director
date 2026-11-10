"""Hard gates for the real React Studio build and GitHub-hosted runner contract."""
import json
from pathlib import Path


def test_react_build_uses_film_authority_safe_director_plan_handoff():
    root = Path(__file__).resolve().parents[1]
    studio = (root / "webui/static/js/film-studio.js").read_text(encoding="utf-8")
    shotbook = (root / "webui/static/js/shotbook.js").read_text(encoding="utf-8")
    creative = (root / "webui/static/js/creative-plan.js").read_text(encoding="utf-8")
    index = (root / "webui/static/index.html").read_text(encoding="utf-8")

    assert "planningIds.push('shotbook-details','creative-plan-panel')" in studio
    assert "function installFilmAuthorityPlanningWriteGate()" in studio
    assert "currentFilmAuthorityProjectId()" in studio
    assert "window.__filmStudioEditorAuthorityWrapperInstalled=true" in studio
    assert studio.index("window.__filmStudioEditorAuthorityWrapperInstalled=true") < studio.index("restoreDock();")
    assert 'onchange="if(this.value)window.editorLoad()"' in index
    assert 'onclick="window.editorLoad()"' in index
    assert "window.filmStudioProposeFromDirectorPlan=async function()" in studio
    assert "'director-propose-plan': () => window.filmStudioProposeFromDirectorPlan()" in studio
    assert "window.shotbookExportForFilmDirector=function()" in shotbook
    assert "window.creativePlanExportForFilmDirector = function ()" in creative
    assert "按现有分镜 / 语义计划生成提案" in index
    assert "shotbook-film-authority-note" in index
    assert "creative-plan-film-authority-note" in index
    assert "当前项目使用 film.html" in studio



def test_studio_build_uses_isolated_hosted_runner_and_lockfile():
    root = Path(__file__).resolve().parents[1]
    studio_workflow = (root / ".github" / "workflows" / "studio-ui-build.yml").read_text(encoding="utf-8")
    full_workflow = (root / ".github" / "workflows" / "full-hosted-regression.yml").read_text(encoding="utf-8")
    parallel_workflow = (root / ".github" / "workflows" / "parallel-workstream-ci.yml").read_text(encoding="utf-8")
    contract = (root / "CONTRACT.md").read_text(encoding="utf-8")
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    package = json.loads((root / "studio_ui" / "package.json").read_text(encoding="utf-8"))
    remotion_package = json.loads((root / "remotion_runtime" / "package.json").read_text(encoding="utf-8"))

    assert "runs-on: ubuntu-24.04" in studio_workflow
    assert "runs-on: [self-hosted" not in studio_workflow
    assert "group: studio-ui-hosted-" in studio_workflow
    assert "ref: ${{ github.sha }}" in studio_workflow
    assert "npm ci --no-audit --no-fund" in studio_workflow
    assert "working-directory: studio_ui" in studio_workflow
    assert "workflow_dispatch:" in studio_workflow
    assert "push:" not in studio_workflow.split("permissions:", 1)[0]

    active_workflows = studio_workflow + full_workflow + parallel_workflow
    assert "self-hosted" not in active_workflows
    assert "tools/bootstrap_python_socks.sh" not in active_workflows
    assert "runs-on: ubuntu-24.04" in full_workflow
    assert "runs-on: ubuntu-24.04" in parallel_workflow
    assert "python -m compileall -q video_director webui" in full_workflow
    assert "python -m pytest" in full_workflow
    assert "tests/test_bulk_management.py" in parallel_workflow
    assert "env -u VD_LLM_API_KEY" in full_workflow

    full_job = full_workflow.split("  full_regression:", 1)[1].split("  studio_release_gate:", 1)[0]
    studio_gate = full_workflow.split("  studio_release_gate:", 1)[1].split("  nvidia_live_stream:", 1)[0]
    live_job = full_workflow.split("  nvidia_live_stream:", 1)[1]
    assert "NVIDIA_API_KEY" not in full_job
    assert "NVIDIA_API_KEY" not in studio_gate
    assert "NVIDIA_API_KEY: ${{ secrets.NVIDIA_API_KEY }}" in live_job
    assert "python -m pip install -r requirements.txt httpx" in live_job

    assert "pytest" in requirements
    assert not any(name.startswith("@remotion/") for name in package["dependencies"])
    assert "@remotion/renderer" in remotion_package["dependencies"]
    assert "React 19 + Tailwind 4" in contract
    assert "第二份权威影片状态" in contract
    assert "GitHub-hosted Actions runner" in contract
    assert "禁止使用 `self-hosted`" in contract
    assert "windows-latest" in contract
    assert "0.2 CI Runner 契约统一" in contract

def test_studio_published_assets_are_real_build_outputs():
    root = Path(__file__).resolve().parents[1]
    bundle = root / "webui" / "static" / "js" / "film-studio-react.js"
    css = root / "webui" / "static" / "css" / "film-studio-tailwind.css"
    lock = root / "studio_ui" / "package-lock.json"

    assert bundle.is_file(), "React Studio bundle must exist after build"
    assert css.is_file(), "Tailwind output must exist after build"
    assert lock.is_file(), "npm dependency lock must be committed with the verified build"
    assert bundle.stat().st_size > 1000, "React Studio bundle is still too small"
    assert css.stat().st_size > 1000, "Tailwind output is still too small"
    assert "Build output placeholder" not in bundle.read_text(encoding="utf-8")





def test_studio_release_gate_and_live_acceptance_are_separate_hosted_jobs():
    root = Path(__file__).resolve().parents[1]
    studio_workflow = (root / ".github" / "workflows" / "studio-ui-build.yml").read_text(encoding="utf-8")
    full_workflow = (root / ".github" / "workflows" / "full-hosted-regression.yml").read_text(encoding="utf-8")
    live_acceptance = (root / "video_director" / "live_stream_acceptance.py").read_text(encoding="utf-8")

    assert "runs-on: ubuntu-24.04" in studio_workflow
    assert "workflow_dispatch:" in studio_workflow
    assert "Publish verified React Studio bundle" not in studio_workflow
    assert "git push" not in studio_workflow
    assert "runs-on: ubuntu-24.04" in full_workflow

    gate = full_workflow.split("  studio_release_gate:", 1)[1].split("  nvidia_live_stream:", 1)[0]
    for required_real_gate in (
        "tests/test_film_document_contract.py",
        "tests/test_film_studio_external_watch_browser.py",
        "tests/test_film_studio_migration_preview_browser.py",
        "tests/test_film_studio_layout_persistence_browser.py",
        "tests/test_film_studio_ai_proposal_browser.py",
        "tests/test_film_studio_project_switch_four_way_browser.py",
        "tests/test_film_studio_generated_asset_adoption_browser.py",
        "tests/test_film_agent_cli.py",
        "tests/test_film_mcp_server.py",
        "tests/test_film_full_pipeline_e2e.py",
        "tests/test_film_render_scene_e2e.py",
    ):
        assert required_real_gate in gate, f"release gate lost {required_real_gate}"

    live_job = full_workflow.split("  nvidia_live_stream:", 1)[1]
    assert "needs: [full_regression, studio_release_gate]" in live_job
    assert "if: steps.live_provider_probe.outcome == 'success'" in live_job
    assert "python -m video_director.live_stream_acceptance --junit live-stream-junit.xml" in live_job
    assert "actions/upload-artifact@v7" in full_workflow
    assert "GITHUB_STEP_SUMMARY" in full_workflow
    assert "git push" not in full_workflow
    assert "LIVE_STREAM_ACCEPTANCE=MISSING" in live_job
    for status in ('"BLOCKED_PROVIDER": (', '"BLOCKED_MODEL": (', '"BLOCKED_ENDPOINT": (', '"MISSING": (', '"REJECT": ('):
        assert status in live_acceptance
    assert 'if config_outcome != "skipped" and status != "PASS":' in live_acceptance

def test_crossfade_action_has_strict_overlap_and_single_transaction_contract():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    document = (root / "video_director" / "film_document.py").read_text(encoding="utf-8")
    assert 'data-context-action="crossfade-transition"' in runtime
    assert 'data-context-action="crossfade-transition"' in component
    assert 'data-transition-duration="true"' in runtime
    assert 'data-transition-duration="true"' in component
    assert "function transitionDurationOverride(value)" in runtime
    assert "window.filmStudioApplyCrossfadeTransition=async function(clipId,requestedDuration)" in runtime
    helper = runtime.split(
        "window.filmStudioApplyCrossfadeTransition=async function(clipId,requestedDuration)", 1
    )[1].split("window.filmStudioContextMenuAction=function", 1)[0]
    assert "track_id:track.id,at:newAt" in helper
    assert "return await filmStudioCommitExactRevision(ops)" in helper
    assert "previousFade.out>0.001" in helper
    assert "current.attrs.volume_envelope" in helper
    assert "field:'volume_envelope',value:outgoingAudioEnvelope" in helper
    assert "已有自定义音量包络" in helper
    assert "active_clips" in document
    assert "def _clip_overlap_is_valid(previous: dict, current: dict, overlap: float) -> bool:" in document
    assert "_clip_overlap_is_valid(previous, clip, overlap)" in document
    assert "crossfade-transition" in component

def test_fade_transition_action_is_explicitly_same_track_adjacent_and_one_transaction():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    assert 'data-context-action="fade-transition"' in runtime
    assert 'data-context-action="fade-transition"' in component
    assert "window.filmStudioApplyFadeTransition=async function(clipId,requestedDuration)" in runtime
    helper = runtime.split(
        "window.filmStudioApplyFadeTransition=async function(clipId,requestedDuration)", 1
    )[1].split("window.filmStudioContextMenuAction=function", 1)[0]
    assert "Math.abs(previousEnd-nextAt)>0.001" in helper
    assert "filmStudioCommitExactRevision(ops)" in helper
    assert "action==='fade-transition'" in runtime
    assert "requestedDuration" in runtime
    assert "transitionDurationTouched?transitionDuration:undefined" in component
    assert "正在为两个相邻片段提交淡出/淡入衔接" in runtime
    assert "片段位置不变" in runtime
    assert "淡出/淡入接续（无重叠）" in component
    assert "不重叠、不改变位置" in component


def test_studio_components_share_one_react_root_and_keep_portal_slots():
    root = Path(__file__).resolve().parents[1]
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    html = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")

    assert component.count("createRoot(") == 1
    assert "createPortal" in component
    assert "const StudioReactOwner: React.FC" in component
    assert 'id="fs-react-root"' in html
    assert 'id="fs-side-workbench"' in html
    assert 'id="fs-side-divider"' in html
    assert 'id="fs-side-ai" role="region" aria-label="AI 导演"' in html
    assert 'id="fs-side-inspector" role="region" aria-label="属性检查器"' in html
    assert 'id="fs-side-ai" aria-label="AI 导演" hidden' not in html
    runtime = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "film-studio-inspector-share" in runtime
    assert "if(inspector)inspector.hidden" not in runtime
    assert "if(ai)ai.hidden" not in runtime
    assert "side-split" in runtime
    assert "applySideShare((e.clientY-d.rect.top)/sideHeight*100,true)" in runtime
    for surface in (
        "fs-assets-list", "fs-timeline", "fs-react-toolbar",
        "fs-react-timeline-toolbar", "fs-react-scrub-row",
        "fs-react-ai-director", "fs-inspector", "fs-react-canvas-inspector",
    ):
        assert f'"{surface}"' in component


def test_live_model_stream_smoke_is_explicit_ci_opt_in_and_fails_closed():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "full-hosted-regression.yml").read_text(encoding="utf-8")
    live_job = workflow.split("  nvidia_live_stream:", 1)[1]
    helper = (root / "video_director" / "live_stream_acceptance.py").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "run_live_nvidia:" in workflow
    assert "default: false" in workflow
    assert "github.event_name == 'workflow_dispatch' &&" in live_job
    assert "inputs.run_live_nvidia" in live_job
    assert "github.event_name == 'push'" not in live_job
    assert "[live-stream-smoke]" not in live_job
    assert "[live-provider=nvidia]" not in live_job
    assert "[live-model=z-ai/glm-5.3-flash]" not in live_job
    assert "needs: [full_regression, studio_release_gate]" in live_job
    assert "NVIDIA_API_KEY: ${{ secrets.NVIDIA_API_KEY }}" in live_job
    assert "https://integrate.api.nvidia.com/v1" in live_job
    assert "z-ai/glm-5.3-flash" in live_job
    assert "python -m pip install -r requirements.txt httpx" in live_job
    assert "httpx.Timeout(connect=15.0, read=210.0, write=30.0, pool=15.0)" in live_job
    assert "max_retries=0" in live_job
    assert "if: steps.live_provider_probe.outcome == 'success'" in live_job
    assert "LIVE_STREAM_ACCEPTANCE=MISSING" in live_job
    assert "LIVE_PROVIDER_PROBE_ERROR_TYPE" in live_job
    assert "python -m video_director.live_stream_acceptance --junit live-stream-junit.xml" in live_job

    assert "NVIDIA live override requires NVIDIA_API_KEY" in helper
    assert "BLOCKED_ENDPOINT" in helper
    assert "ModuleNotFoundError" in helper
    assert 'if config_outcome != "skipped" and status != "PASS":' in helper

def test_react_timeline_owns_pointer_anchored_zoom_and_legacy_toolbar_delegates():
    """The built React timeline owns zoom scale/anchor; legacy toolbar is compatibility only."""
    root = Path(__file__).resolve().parents[1]
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    legacy = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")

    assert "zoom(direction: number, anchorClientX?: number): void;" in component
    assert "filmTimelineMath.zoomAnchorTime(" in component
    assert "filmTimelineMath.zoomScrollLeft(" in component
    assert 'filmStore.setField("pixelsPerSecond",nextScale)' in component
    assert "timeline.zoom(direction,event.clientX)" in component
    legacy_zoom = legacy.split("window.filmStudioZoom=function(direction,anchorClientX){", 1)[1].split("};", 1)[0]
    assert "timeline.zoom(direction,anchorClientX)" in legacy_zoom

def test_react_timeline_owns_move_target_hit_testing_and_legacy_fallback_is_gated():
    """React resolves drop rows and validates the drop; legacy hit-testing is fallback-only."""
    root = Path(__file__).resolve().parents[1]
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    legacy = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")

    assert "const updateReactGestureTarget = useCallback((event:PointerEvent)" in component
    assert "document.elementFromPoint(event.clientX,event.clientY)" in component
    assert "filmTimelineGestureController.validateMoveTarget(input)" in component
    assert "filmStore.setField(\"timelineGestureContext\"" in component
    assert "updateReactGestureTarget(pointerEvent)" in component
    assert "if(d.mode==='move'&&gestureDomRefs){" in legacy

    # FilmDocument is a hard authority boundary: missing React must not rebuild
    # the native DOM timeline or leave stale native clips interactive.
    renderer = legacy.split("function renderTimeline() {", 1)[1].split("var fallbackWaveformSources", 1)[0]
    assert "if(!reactTimeline){" in renderer
    no_react_gate = renderer.split("if(!reactTimeline){", 1)[1].split("if(reactTimeline){", 1)[0]
    assert "fs-timeline-react-required" in no_react_gate
    assert "timelineHost.replaceChildren()" in no_react_gate
    assert "setAttribute('role','alert')" in no_react_gate
    assert "return;" in no_react_gate
    gate_index = renderer.index("if(!reactTimeline){")
    assert gate_index < renderer.index("return;", gate_index) < renderer.index("var laneWidth=")
    gesture_entry = legacy.split("function beginClipGesture(e,context,el,prepareOnly)", 1)[1].split("window.filmStudioPrepareClipGestureStart=function", 1)[0]
    assert "S.data&&S.data.exists&&(!reactGestureBridge||typeof reactGestureBridge.renderSurface!=='function')" in gesture_entry
    assert "没有回退到旧版时间线" in gesture_entry

def test_clip_inspector_does_not_rerender_on_playhead_updates():
    """Playback updates the keyframe playhead marker without rebuilding the full inspector."""
    root = Path(__file__).resolve().parents[1]
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    inspector_panel = component.split("const StudioClipInspectorPanel: React.FC", 1)[1].split("const StudioReactOwner: React.FC", 1)[0]
    keyframe_playhead = component.split("const FilmKeyframePlayheadLine: React.FC", 1)[1].split("const ClipInspector: React.FC", 1)[0]
    owner = component.split("const StudioReactOwner: React.FC = () => {", 1)[1].split("function renderStudioReactOwner", 1)[0]

    assert 'useFilmStoreField("playhead")' in keyframe_playhead
    assert 'useFilmStoreField("playhead")' not in inspector_panel
    assert 'useFilmStoreField("selectedId")' in inspector_panel
    assert "buildClipInspectorModel(timelineFilm,clipInspectorSelectedId)" in inspector_panel
    assert 'filmStore.getField("playhead")' in component
    assert '<FilmKeyframePlayheadLine clipAt={clip.at} clipDuration={clip.duration} />' in component
    assert 'useFilmStoreField("playhead")' not in owner
    assert 'useFilmStoreField("selectedId")' not in owner
    assert '<StudioClipInspectorPanel />' in owner


def test_react_playhead_updates_are_isolated_from_track_tree_rendering():
    """High-frequency scrub/playback updates only rerender the ruler and playhead leaf nodes."""
    root = Path(__file__).resolve().parents[1]
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    timeline_surface = component.split("const TimelineSurface: React.FC", 1)[1].split("const StudioAssets", 1)[0]
    scrub_ruler = component.split("const TimelineScrubRuler: React.FC", 1)[1].split("const TimelineSurface: React.FC", 1)[0]
    playhead_line = component.split("const TimelinePlayheadLine: React.FC", 1)[1].split("const TimelineScrubRuler: React.FC", 1)[0]

    assert 'useFilmStoreField("playhead")' not in timeline_surface
    assert 'const TimelineSurface: React.FC<{model: FilmTimelineSurfaceModel}> = React.memo(({model}) => {' in component
    assert 'useFilmStoreField("duration")' not in timeline_surface
    assert 'useFilmStoreField("playhead")' in scrub_ruler
    assert 'useFilmStoreField("duration")' in scrub_ruler
    assert 'useFilmStoreField("playhead")' in playhead_line
    assert '<TimelineScrubRuler model={model} />' in timeline_surface
    assert '<TimelinePlayheadLine pixelsPerSecond={model.pixelsPerSecond} />' in timeline_surface
    assert 'data-react-scrub-ready="true"' in scrub_ruler
    assert 'Math.hypot(dx, dy) < 4' in scrub_ruler
    assert 'onPointerCancel={event => {' in scrub_ruler


def test_react_move_target_and_preview_share_one_pointer_event_snapshot():
    """Hit testing and preview use one immutable read of tracks/snap points per pointer event."""
    root = Path(__file__).resolve().parents[1]
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")

    assert "const reactMoveGestureSnapshots = new WeakMap<object, ReactMoveGestureSnapshot>();" in component
    assert "const reactMoveGestureSnapshot = (" in component
    assert component.count("reactMoveGestureSnapshot(event,state,gesture)") == 2
    assert "cached.film === state.film" in component
    assert "cached.signature === signature" in component
    assert "filmTimelineGestureController.validateMoveTarget(input)" in component


def test_react_timeline_owns_clip_gesture_previews_and_legacy_dispatch_delegates():
    """React calculates move/trim/roll/slide/slip previews; old JS only remains fallback."""
    root = Path(__file__).resolve().parents[1]
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    legacy = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")

    assert "previewClipGesture(event:FilmStudioGesturePointerInput): void;" in component
    assert "const resolveReactClipGesturePreview = (" in component
    assert "forCommit = false" in component
    for call in (
        "filmTimelineGestureController.buildMovePreview(input)",
        "filmTimelineGestureController.resolveTrimGesture({",
        "filmTimelineGestureController.resolveRollGesture({",
        "filmTimelineGestureController.resolveSlideGesture({",
        "filmTimelineGestureController.resolveSlipGesture({",
    ):
        assert call in component
    assert "reactTimeline.previewClipGesture(e)" in legacy
    assert "if(reactTimeline&&document.querySelector('.fs-react-timeline-surface')){" in legacy
    assert "React 时间线手势上下文丢失；本次操作已取消，未回退到旧版编辑路径。" in legacy
    # Native click follows pointerup in a later browser task on Chromium. Keep
    # the one-shot suppression alive through that event; a zero-delay reset
    # can race Ctrl/Shift selection and undo the pointerdown selection.
    assert "function suppressNextClipClick()" in legacy
    assert "if(skipClipClickGeneration===generation)S.skipNextClipClick=false;" in legacy
    assert "setTimeout(function(){S.skipNextClipClick=false;},0);" not in legacy
    assert "if(reactOwnsTimeline&&e&&Number(e.detail)>0){" in legacy
    click_route_start = legacy.index("if(reactOwnsTimeline&&e&&Number(e.detail)>0){")
    click_route_end = legacy.index("if(selectClipWithModifiers(id,e)){", click_route_start)
    click_route = legacy[click_route_start:click_route_end]
    assert "selectClipWithModifiers(id,e)" not in click_route
    assert "S.playhead=pointerClickedClip.at;" in click_route
    assert "setPlayheadVisual();previewCurrent();" in click_route
    pointer_down_start = component.index("onPointerDown={event => {", component.index("data-react-timeline-clip={clip.id}"))
    pointer_down_end = component.index("onPointerMove={event => {", pointer_down_start)
    pointer_down = component[pointer_down_start:pointer_down_end]
    assert "timelineBridge.beginClipGesture(event.nativeEvent,gestureContext)" in pointer_down
    assert "window.filmStudioBeginClipGesture" not in pointer_down
    assert "React 时间线片段起手接口不可用" in pointer_down
    pointer_move_marker = component.index("dispatchReactClipGesturePreview(pointerEvent);")
    pointer_up_start = component.index("onPointerUp={event => {", pointer_move_marker)
    pointer_up_end = component.index("onPointerCancel={event => {", pointer_up_start)
    pointer_up_end = component.index("onPointerCancel={event => {", pointer_up_start)
    pointer_up = component[pointer_up_start:pointer_up_end]
    dispatch_start = component.index("const dispatchReactClipGesturePreview =")
    dispatch_end = component.index("let reactClipClickSuppressionGeneration",dispatch_start)
    dispatch_body = component[dispatch_start:dispatch_end]
    assert "window.filmStudioMoveClipGesture" not in dispatch_body
    assert "cancelReactClipGesture(event," in dispatch_body
    assert "timelineBridge.endClipGesture(event.nativeEvent)" in pointer_up
    assert "window.filmStudioEndClipGesture?.(event.nativeEvent)" not in pointer_up
    assert "endClipGesture(event:PointerEvent): Promise<void> | void;" in component
    assert "typeof bridge.endClipGesture==='function'&&" in legacy
    assert "window.filmStudioCommitPreparedClipOperations = function(operations)" in legacy
    assert "return filmStudioCommitExactRevision(operations,{requireExactRevision:true});" in legacy

    finish_start = legacy.index("async function finishReactClipGesture(e, preparedOperations)")
    finish_end = legacy.index("async function onUp(e)", finish_start)
    finish_body = legacy[finish_start:finish_end]
    assert "S.drag=null;\n    if(d.active)suppressNextClipClick();" in finish_body
    cancel_start = legacy.index("function recordReactTimelineGestureCancellation(e,reason)")
    cancel_end = legacy.index("function updateTimelineGestureContext", cancel_start)
    cancel_body = legacy[cancel_start:cancel_end]
    assert "window.__filmStudioTimelineGestureCancellation=diagnostic;" in cancel_body
    assert "REACT_TIMELINE_GESTURE_CANCEL=" in cancel_body
    assert "store.setField" not in cancel_body
    assert "flushPendingExternalRevision" not in cancel_body
    assert "window.filmStudioRecordTimelineGestureCancellation = function(e,reason)" in legacy
    assert "function delegateReactTimelineGestureCancellation(e,reason)" in legacy
    assert "return cancel(e,reason);" in legacy
    assert "window.filmStudioCancelReactClipGesture = cancelReactClipGesture;" in component
    assert "cancelReactTimelineGestureAfterContractLoss" not in legacy


def test_react_timeline_prepares_all_clip_gesture_operations_before_dispatch():
    """React uses the shared gesture controller for final ops; the legacy shell only submits the transaction."""
    root = Path(__file__).resolve().parents[1]
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    legacy = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")

    assert "prepareClipGestureCommit(event:FilmStudioGesturePointerInput): FilmOperation[] | undefined;" in component
    assert "return resolveReactClipGesturePreview(event,true);" in component
    assert "if (forCommit) return result.operations ?? [];" in component
    assert "return result ? [result.operation] : [];" in component
    assert "return result && Math.abs(result.resolution.deltaSeconds) >= 0.001" in component
    assert "return result && Math.abs(result.resolution.sourceDeltaSeconds) >= 0.001" in component
    assert "function hasCompleteReactTimelineGestureContract(bridge,controller)" in legacy
    assert "typeof bridge.prepareClipGestureCommit==='function'" in legacy
    assert "var prepared=timeline.prepareClipGestureCommit(e);" in legacy
    assert "if(Array.isArray(prepared))return finishReactClipGesture(e,prepared);" in legacy
    assert "function finishReactClipGesture(e, preparedOperations)" in legacy
    assert "await filmStudioCommitExactRevision(preparedOperations,{requireExactRevision:true});" in legacy
    assert "if(!Array.isArray(preparedOperations))return onUp(e);" in legacy
    assert "async function onUp(e)" in legacy
    assert "endClipGesture(event) {" in component
    end_start = component.index("endClipGesture(event) {")
    end_end = component.index("zoom(direction,anchorClientX)", end_start)
    end_body = component[end_start:end_end]
    assert "bridge.prepareClipGestureCommit(event)" in end_body
    assert "const commit = window.filmStudioCommitPreparedClipOperations;" in end_body
    assert "filmStore.setField(\"timelineGesturePreview\",null);" in end_body
    assert "filmStore.setField(\"timelineGestureContext\",null);" in end_body
    assert "suppressReactClipClick()" in end_body
    assert "return Promise.resolve(commit!(prepared)).then(" in end_body
    assert "cancel(\"missing commit runtime bridge/controller contract\")" in end_body
    assert "cancelReactClipGesture(event,reason);" in end_body


def test_all_active_regression_workflows_use_hosted_runners():
    root = Path(__file__).resolve().parents[1]
    workflow_paths = (
        ".github/workflows/full-hosted-regression.yml",
        ".github/workflows/parallel-workstream-ci.yml",
        ".github/workflows/product-ia-regression.yml",
        ".github/workflows/studio-ui-build.yml",
        ".github/workflows/validate-workflows.yml",
    )
    active = {
        path: (root / path).read_text(encoding="utf-8")
        for path in workflow_paths
    }
    windows = (root / ".github" / "workflows" / "windows-native-publication.yml").read_text(encoding="utf-8")

    for path, workflow in active.items():
        assert "self-hosted" not in workflow, f"{path} must not require a self-hosted runner"
        assert "runs-on: ubuntu-24.04" in workflow, f"{path} must use the pinned hosted Linux runner"
    assert "runs-on: windows-latest" in windows
    assert "self-hosted" not in windows
    studio = active[".github/workflows/studio-ui-build.yml"]
    assert "workflow_dispatch:" in studio
    assert "push:" not in studio.split("permissions:", 1)[0]
    assert "group: studio-ui-hosted-" in studio


def test_full_hosted_workflow_retains_regression_diagnostics():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "full-hosted-regression.yml").read_text(encoding="utf-8")
    assert "Summarize full pytest JUnit results" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
    assert "timeline-junit.xml" in workflow
    assert "pytest-output.log" in workflow
    assert "junit.xml" in workflow
    assert "actions/upload-artifact@v7" in workflow

def test_film_ai_progress_events_are_distinct_from_model_tokens():
    root = Path(__file__).resolve().parent.parent
    server = (root / "webui" / "server.py").read_text(encoding="utf-8")
    runtime = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")

    assert "FILM_AI_STREAM_PROGRESS_INTERVAL_SECONDS = 10.0" in server
    assert 'emit("progress", progress_payload)' in server
    assert 'emit("token", {"fragment": fragment})' in server
    assert "eventName==='progress'" in runtime
    progress_branch = runtime.split("if(eventName==='progress')", 1)[1].split("else if(eventName==='token')", 1)[0]
    assert "if(!S.aiStreamText)" in progress_branch
    assert "cancel_upstream_on_disconnect" in server
    assert "cancel_event.set()" in server
    assert "AI 指令正在处理，模型尚未返回可预览文本。" in server
    assert "AI 指令正在处理，模型尚未返回可预览文本。" in runtime




def test_full_hosted_regression_gate_is_main_sha_pinned_and_uses_no_self_hosted_runner():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "full-hosted-regression.yml").read_text(encoding="utf-8")

    assert "branches: [main]" in workflow
    assert "runs-on: ubuntu-24.04" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "runs-on: [self-hosted" not in workflow
    assert "python -m pytest" in workflow
    assert "junit.xml" in workflow
    assert "timeline-junit.xml" in workflow
    assert "-p pytest_exit_diagnostics" in workflow
    assert "env -u VD_LLM_API_KEY" in workflow
    assert "tests/test_film_studio_timeline_history_browser.py" in workflow
    assert "tests/test_film_studio_ai_proposal_browser.py" in workflow
    assert "tests/test_film_studio_project_switch_four_way_browser.py" in workflow
    assert "tests/test_film_studio_generated_asset_adoption_browser.py" in workflow
    assert "tests/test_film_full_pipeline_e2e.py" in workflow
    assert "tests/test_film_render_scene_e2e.py" in workflow
    assert "needs: [full_regression, studio_release_gate]" in workflow
    assert "secrets.NVIDIA_API_KEY" in workflow
    assert "https://integrate.api.nvidia.com/v1" in workflow
    assert "z-ai/glm-5.3-flash" in workflow
    assert "LIVE_STREAM_ACCEPTANCE=MISSING" in workflow
    assert "VIDEO_DIRECTOR_STORAGE_ROOT" in workflow

    ignored = workflow.split("paths-ignore:", 1)[1].split("  workflow_dispatch:", 1)[0]
    assert "'docs/**'" in ignored
    assert "'**/*.md'" in ignored
    assert "'README*'" in ignored
    assert "tests/" not in ignored
    assert "video_director/" not in ignored
    assert ".github/workflows/" not in ignored

    assert "[live-stream-smoke]" not in workflow
    assert "[live-provider=nvidia]" not in workflow
    assert "[live-model=z-ai/glm-5.3-flash]" not in workflow
    live_job = workflow.split("  nvidia_live_stream:", 1)[1]
    assert "github.event_name == 'workflow_dispatch' &&" in live_job
    assert "inputs.run_live_nvidia" in live_job
    assert "needs: [full_regression, studio_release_gate]" in live_job
    assert "NVIDIA_API_KEY: ${{ secrets.NVIDIA_API_KEY }}" in live_job
    assert 'export VD_LLM_API_KEY="$NVIDIA_API_KEY"' in live_job
    assert 'export VD_LLM_BASE_URL="https://integrate.api.nvidia.com/v1"' in live_job
    assert 'export VD_LLM_MODEL="z-ai/glm-5.3-flash"' in live_job
    assert "OPENAI_API_KEY" not in live_job
    assert "ANTHROPIC_API_KEY" not in live_job
    assert "environment:" not in live_job
    assert "id: live_provider_probe" in live_job
    assert "python -m pip install -r requirements.txt httpx" in live_job
    assert "LIVE_PROVIDER_PROBE_OUTCOME" in live_job
    assert "httpx.Timeout(connect=15.0, read=210.0, write=30.0, pool=15.0)" in live_job
    assert 'VD_LLM_REQUEST_TIMEOUT_SECONDS: "210"' in live_job
    assert 'VD_LLM_MAX_RETRIES: "0"' in live_job
    assert "max_tokens=512" in live_job
    assert 'reasoning_effort="low"' in live_job
    assert '"chat_template_kwargs": {"clear_thinking": True}' in live_job
    assert "steps.live_provider_probe.outcome == 'success'" in live_job
    assert 'LIVE_STREAM_CONFIG_OUTCOME="$LIVE_CONFIG_OUTCOME"' in live_job
    assert 'LIVE_PROVIDER_PROBE_ERROR_TYPE="$LIVE_PROVIDER_PROBE_ERROR_TYPE"' in live_job
    assert "python -m video_director.live_stream_acceptance --junit live-stream-junit.xml" in live_job

