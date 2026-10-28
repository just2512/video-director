"""Hard gates for the real React Studio build and its self-hosted runner contract."""
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
    assert "window.filmStudioProposeFromDirectorPlan=async function()" in studio
    assert "'director-propose-plan': () => window.filmStudioProposeFromDirectorPlan()" in studio
    assert "window.shotbookExportForFilmDirector=function()" in shotbook
    assert "window.creativePlanExportForFilmDirector = function ()" in creative
    assert "按现有分镜 / 语义计划生成提案" in index
    assert "shotbook-film-authority-note" in index
    assert "creative-plan-film-authority-note" in index
    assert "当前项目使用 film.html" in studio


def test_studio_build_uses_existing_self_hosted_runner_and_lockfile():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "studio-ui-build.yml").read_text(encoding="utf-8")
    contract = (root / "CONTRACT.md").read_text(encoding="utf-8")
    ai_workflow = (root / ".github" / "workflows" / "ai-director.yml").read_text(encoding="utf-8")
    proxy_bootstrap = (root / "tools" / "bootstrap_python_socks.sh").read_text(encoding="utf-8")
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    package_path = root / "studio_ui" / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    remotion_package = json.loads((root / "remotion_runtime" / "package.json").read_text(encoding="utf-8"))

    assert "runs-on: [self-hosted, linux, x64]" in workflow
    assert "runs-on: ubuntu-latest" not in workflow
    assert "PY311_BIN: /home/actions/miniforge3/envs/py311/bin" in workflow
    assert "npm ci --no-audit --no-fund --prefer-offline --fetch-retries=2" in workflow
    assert "working-directory: studio_ui" in workflow
    assert "bash tools/bootstrap_python_socks.sh" in workflow
    assert "bash tools/bootstrap_python_socks.sh" in ai_workflow
    # The real-provider credential must exist only in the isolated live-test child
    # process; broad regressions must not inherit the runner's paid model config.
    assert "VD_PRESERVED_MODEL_CONFIG_FILE" in ai_workflow
    assert "VD_LLM_LIVE_CONFIG_FILE" in ai_workflow
    assert "id: live_stream_smoke" in ai_workflow
    assert "live-stream-junit.xml" in ai_workflow
    assert "env -u VD_LLM_API_KEY" in ai_workflow
    assert "VIDEO_DIRECTOR_STORAGE_ROOT=%s" not in ai_workflow
    assert not any(
        "VD_LLM_API_KEY=%s" in line and "GITHUB_ENV" in line
        for line in ai_workflow.splitlines()
    )
    assert 'download_and_install_wheel "PySocks" "1.7.1"' in proxy_bootstrap
    assert 'download_and_install_wheel "socksio" "1.0.0"' in proxy_bootstrap
    assert 'if ! "$python_bin" -c "import socksio"' in proxy_bootstrap
    assert "import socks" in proxy_bootstrap and "import socksio" in proxy_bootstrap
    assert "socksio==1.0.0" in requirements
    assert not any(line.startswith(("import json", "import sys", "from pathlib")) for line in workflow.splitlines())
    assert "Install Remotion render runtime dependencies" in workflow
    assert "Install Remotion rendering runtime dependencies" in ai_workflow
    assert "working-directory: studio_ui" in ai_workflow
    assert "studio_ui/package-lock.json" in ai_workflow
    assert 'if "HTTP 404" not in str(exc):' in ai_workflow
    assert "-m playwright install chromium" in workflow
    assert "--with-deps chromium" not in workflow
    assert "build:studio" in package["scripts"]
    assert "build:studio:css" in package["scripts"]
    assert package["scripts"]["build:studio:css"].startswith("tailwindcss -i film-studio-tailwind.css")
    tailwind_entry = root / "studio_ui" / "film-studio-tailwind.css"
    assert tailwind_entry.is_file()
    assert '@source "../remotion_runtime/src/StudioCanvasInspector.tsx";' in tailwind_entry.read_text(encoding="utf-8")
    assert not any(name.startswith("@remotion/") for name in package["dependencies"])
    assert "@remotion/renderer" in remotion_package["dependencies"]
    assert "React 19 + Tailwind 4" in contract
    assert "第二份权威影片状态" in contract


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




def test_studio_artifact_publish_is_single_and_requires_expanded_release_gate():
    """Only the full browser/render/CLI/MCP gate may publish the built Studio bundle."""
    root = Path(__file__).resolve().parents[1]
    studio_workflow = (root / ".github" / "workflows" / "studio-ui-build.yml").read_text(encoding="utf-8")
    ai_workflow = (root / ".github" / "workflows" / "ai-director.yml").read_text(encoding="utf-8")
    live_acceptance = (root / "video_director" / "live_stream_acceptance.py").read_text(encoding="utf-8")

    assert "Publish built Studio assets without overwriting newer main" not in studio_workflow
    assert "Asset publication is centralized in ai-director.yml" in studio_workflow

    gate = ai_workflow.split(
        "      - name: Gate Studio asset publication on timeline browser regressions", 1
    )[1].split("      - name: Publish verified React Studio bundle and Tailwind CSS to main", 1)[0]
    for required_real_gate in (
        "tests/test_film_studio_timeline_history_browser.py",
        "tests/test_film_studio_external_watch_browser.py",
        "tests/test_film_studio_ai_proposal_browser.py",
        "tests/test_film_agent_cli.py",
        "tests/test_film_mcp_server.py",
        "tests/test_film_full_pipeline_e2e.py",
        "tests/test_film_render_scene_e2e.py",
    ):
        assert required_real_gate in gate, f"release gate lost {required_real_gate}"

    assert "id: live_stream_config" in ai_workflow
    assert "id: full_regression" in ai_workflow
    assert "steps.full_regression.outcome == 'success'" in ai_workflow
    # Live-model diagnostics are intentionally non-blocking: the report must
    # record REJECT/MISSING, while unrelated regression and release gates continue.
    require_live_or_not_requested = (
        "steps.live_stream_config.conclusion == 'success' || "
        "steps.live_stream_config.conclusion == 'skipped' || "
        "steps.live_stream_config.conclusion == 'failure'"
    )
    assert ai_workflow.count(require_live_or_not_requested) >= 2
    live_config_step = ai_workflow.split(
        "      - name: Configure opt-in live model token-stream smoke", 1
    )[1].split("      - name: Run isolated real-model token-stream acceptance test", 1)[0]
    assert "continue-on-error: true" in live_config_step
    assert "raise SystemExit(1)" in live_config_step
    assert "id: live_stream_smoke" in ai_workflow
    assert "steps.live_stream_config.outcome" in ai_workflow
    assert "steps.live_stream_acceptance.conclusion == 'success'" in ai_workflow
    assert "steps.timeline_regression_gate.outcome == 'success'" in ai_workflow
    assert '"BLOCKED_PROVIDER":' in live_acceptance
    assert '"BLOCKED_MODEL":' in live_acceptance
    assert '"insufficient balance" in evidence' in live_acceptance
    assert '"has reached its end of life"' in live_acceptance
    assert 'LIVE_STREAM_ACCEPTANCE={status}' in live_acceptance
    assert 'if config_outcome != "skipped" and status != "PASS":' in live_acceptance
    assert 'python3.11" -m video_director.live_stream_acceptance --junit live-stream-junit.xml' in ai_workflow



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
    assert "return await filmStudioCommit(ops)" in helper
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
    assert "filmStudioCommit(ops)" in helper
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

def test_explicit_live_model_stream_gate_fails_closed_when_configuration_is_missing():
    """An opt-in live request must not silently pass when the provider is unconfigured."""
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "ai-director.yml").read_text(encoding="utf-8")
    config_step = workflow.split("      - name: Configure opt-in live model token-stream smoke", 1)[1].split(
        "      - name: Run isolated real-model token-stream acceptance test", 1
    )[0]
    live_step = workflow.split("      - name: Run isolated real-model token-stream acceptance test", 1)[1].split(
        "      - name: Install Remotion rendering runtime dependencies", 1
    )[0]

    assert "contains(github.event.head_commit.message, '[live-stream-smoke]')" in workflow
    assert "VD_LLM_API_KEY_SECRET" in config_step
    assert "VD_LLM_BASE_URL_CONFIG" in config_step
    assert "VD_LLM_MODEL_CONFIG" in config_step
    assert "Opt-in live-model smoke is missing a complete key/base URL/model configuration." in config_step
    assert "raise SystemExit(1)" in config_step
    assert "VD_LLM_LIVE_STREAM_TEST=1" not in config_step
    assert "VD_LLM_LIVE_STREAM_TEST" in live_step
    assert "live-stream-junit.xml" in live_step
    assert "raise SystemExit(result.returncode" in live_step

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
    finish_start = legacy.index("async function finishReactClipGesture(e, preparedOperations)")
    finish_end = legacy.index("async function onUp(e)", finish_start)
    finish_body = legacy[finish_start:finish_end]
    assert "S.drag=null;\n    if(d.active)suppressNextClipClick();" in finish_body
    cancel_start = legacy.index("function cancelReactTimelineGestureAfterContractLoss(e)")
    cancel_end = legacy.index("function updateTimelineGestureContext", cancel_start)
    cancel_body = legacy[cancel_start:cancel_end]
    assert "parentNode.removeChild" not in cancel_body
    assert "querySelectorAll('#fs-timeline .fs-track-drop-target" not in cancel_body
    assert "function cancelReactTimelineGestureAfterContractLoss(e)" in legacy
    assert "var context=snapshot&&snapshot.timelineGestureContext;" in legacy
    assert "if(!context||!e||context.pointerId!==e.pointerId)return false;" in legacy
    assert "cancelReactTimelineGestureAfterContractLoss(e);" in legacy


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
    assert "await filmStudioCommit(preparedOperations);" in legacy
    assert "if(!Array.isArray(preparedOperations))return onUp(e);" in legacy
    assert "async function onUp(e)" in legacy

