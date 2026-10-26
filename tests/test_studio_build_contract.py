"""Hard gates for the real React Studio build and its self-hosted runner contract."""
import json
from pathlib import Path


def test_studio_build_uses_existing_self_hosted_runner_and_lockfile():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "studio-ui-build.yml").read_text(encoding="utf-8")
    contract = (root / "CONTRACT.md").read_text(encoding="utf-8")
    ai_workflow = (root / ".github" / "workflows" / "ai-director.yml").read_text(encoding="utf-8")
    proxy_bootstrap = (root / "tools" / "bootstrap_python_socks.sh").read_text(encoding="utf-8")
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
    assert "pysocks-1.7.1.json" in proxy_bootstrap
    assert 'import json,sys; print(next(item["url"]' in proxy_bootstrap
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
    step = workflow.split("      - name: Configure opt-in live model token-stream smoke", 1)[1].split(
        "      - name: Install Remotion rendering runtime dependencies", 1
    )[0]

    assert "contains(github.event.head_commit.message, '[live-stream-smoke]')" in workflow
    assert "VD_LLM_API_KEY_SECRET" in step
    assert "VD_LLM_BASE_URL_CONFIG" in step
    assert "VD_LLM_MODEL_CONFIG" in step
    assert 'echo "::error::Real-model token-stream smoke was explicitly requested but configuration is incomplete.' in step
    assert "exit 1" in step
    assert "Real-model token-stream smoke was not run" not in step
    assert "VD_LLM_LIVE_STREAM_TEST=1" in step

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

def test_react_owner_does_not_subscribe_to_playhead_for_clip_inspector():
    """Playhead ticks should update only the inspector and focused timeline leaves, not the React owner."""
    root = Path(__file__).resolve().parents[1]
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    inspector_panel = component.split("const StudioClipInspectorPanel: React.FC", 1)[1].split("const StudioReactOwner: React.FC", 1)[0]
    owner = component.split("const StudioReactOwner: React.FC = () => {", 1)[1].split("function renderStudioReactOwner", 1)[0]

    assert 'useFilmStoreField("playhead")' in inspector_panel
    assert 'useFilmStoreField("selectedId")' in inspector_panel
    assert "buildClipInspectorModel(timelineFilm,clipInspectorSelectedId,clipInspectorPlayhead)" in inspector_panel
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
    assert "if(snapshot&&snapshot.timelineGestureContext&&" in legacy


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
    assert "typeof timeline.prepareClipGestureCommit==='function'" in legacy
    assert "return onUp(e,prepared);" in legacy
    assert "async function onUp(e, reactPreparedOperations)" in legacy
    assert "await filmStudioCommit(reactPreparedOperations);return;" in legacy

