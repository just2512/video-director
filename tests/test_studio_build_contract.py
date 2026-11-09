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


def test_studio_build_uses_isolated_hosted_runner_and_lockfile():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "studio-ui-build.yml").read_text(encoding="utf-8")
    contract = (root / "CONTRACT.md").read_text(encoding="utf-8")
    ai_workflow = (root / ".github" / "workflows" / "ai-director.yml").read_text(encoding="utf-8")
    proxy_bootstrap = (root / "tools" / "bootstrap_python_socks.sh").read_text(encoding="utf-8")
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    package_path = root / "studio_ui" / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    remotion_package = json.loads((root / "remotion_runtime" / "package.json").read_text(encoding="utf-8"))

    assert "runs-on: ubuntu-24.04" in workflow
    assert "runs-on: [self-hosted" not in workflow
    assert "group: studio-ui-hosted-" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "npm ci --no-audit --no-fund" in workflow
    assert "working-directory: studio_ui" in workflow
    assert "bash tools/bootstrap_python_socks.sh" not in workflow
    assert "bash tools/bootstrap_python_socks.sh" in ai_workflow
    # The real-provider credential must exist only in the isolated live-test child
    # process; broad regressions must not inherit the runner's paid model config.
    assert "VD_PRESERVED_MODEL_CONFIG_FILE" in ai_workflow
    assert 'current_mode="$(stat -c \'%a\' "$ORIGINAL_MODEL_CONFIG_FILE")"' in ai_workflow
    assert '"$current_mode" == "$ORIGINAL_MODEL_CONFIG_MODE"' in ai_workflow
    assert 'restored_mode="$(stat -c \'%a\' "$ORIGINAL_MODEL_CONFIG_FILE")"' in ai_workflow
    assert 'LOCAL_MODEL_CONFIG_PRESERVATION=FAIL restored file mode mismatch.' in ai_workflow
    assert "VD_LLM_LIVE_CONFIG_FILE" in ai_workflow
    live_config_step = ai_workflow.split(
        "      - name: Configure opt-in live model token-stream smoke", 1
    )[1].split("      - name: Run isolated real-model token-stream acceptance test", 1)[0]
    assert "resolve_live_api_key" in live_config_step
    assert "resolve_live_provider_override" in live_config_step
    assert "resolve_live_model_override" in live_config_step
    assert 'nvidia_api_key_secret=os.environ.get("NVIDIA_API_KEY_SECRET", "")' in live_config_step
    assert 'nvidia_api_key=os.environ.get("NVIDIA_API_KEY", "")' in live_config_step
    assert 'generic_api_key=os.environ.get("VD_LLM_API_KEY_SECRET", "")' in live_config_step
    assert 'saved_base_url=saved.get("base_url", "")' in live_config_step
    assert '"https://integrate.api.nvidia.com/v1"' in live_config_step
    # Provider-aware selection is implemented and unit-tested in the shared helper:
    # NVIDIA overrides may reuse a saved key only when its saved endpoint is NVIDIA;
    # otherwise a named NVIDIA key is required and generic credentials fail closed.
    helper = (root / "video_director" / "live_stream_acceptance.py").read_text(encoding="utf-8")
    nvidia_branch = helper.split('if provider == "nvidia":', 1)[1].split('    for source, value in (', 1)[0]
    assert 'saved_host == "integrate.api.nvidia.com"' in nvidia_branch
    assert '"NVIDIA_API_KEY_SECRET"' in helper and '"NVIDIA_API_KEY"' in helper
    assert "refusing to send an" in helper
    assert live_config_step.index("saved.get(\"base_url\")") < live_config_step.index('os.environ.get("VD_LLM_BASE_URL_CONFIG")')
    assert live_config_step.index("saved.get(\"model\")") < live_config_step.index('os.environ.get("VD_LLM_MODEL_CONFIG")')
    assert "value is redacted" in live_config_step
    assert "id: live_stream_smoke" in ai_workflow
    live_smoke = ai_workflow.split(
        "      - name: Run isolated real-model token-stream acceptance test", 1
    )[1].split("      - name: Install Remotion rendering runtime dependencies", 1)[0]
    assert "steps.live_stream_config.outcome == 'success'" in live_smoke
    assert "steps.live_provider_probe.outcome == 'success'" in live_smoke
    assert "VD_LLM_LIVE_STREAM_TEST" in live_smoke
    assert "run the documented opt-in test locally" not in live_smoke
    assert "steps.live_stream_acceptance.conclusion == 'success' || steps.live_stream_acceptance.conclusion == 'skipped'" in ai_workflow
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
    assert "Install Remotion runtime dependencies" in workflow
    assert "Install Remotion rendering runtime dependencies" in ai_workflow
    assert "working-directory: studio_ui" in ai_workflow
    assert "studio_ui/package-lock.json" in ai_workflow
    assert 'if "HTTP 404" not in str(exc):' in ai_workflow
    assert "-m playwright install --with-deps chromium" in workflow
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
    assert "runs-on: ubuntu-24.04" in studio_workflow
    assert "workflow_dispatch" in studio_workflow
    assert "Publish verified React Studio bundle" not in studio_workflow
    assert "git push" not in studio_workflow

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
    # MISSING is allowed when the live request was not opted in; an explicit
    # live request that rejects or times out must block release publication.
    require_live_or_not_requested = (
        "steps.live_stream_config.conclusion == 'success' || "
        "steps.live_stream_config.conclusion == 'skipped' || "
        "steps.live_stream_config.conclusion == 'failure'"
    )
    publish_step = ai_workflow.split(
        "      - name: Publish verified React Studio bundle and Tailwind CSS to main", 1
    )[1]
    assert require_live_or_not_requested in publish_step
    live_config_step = ai_workflow.split(
        "      - name: Configure opt-in live model token-stream smoke", 1
    )[1].split("      - name: Run isolated real-model token-stream acceptance test", 1)[0]
    assert "continue-on-error: true" in live_config_step
    assert "raise SystemExit(1)" in live_config_step
    assert "id: live_provider_probe" in ai_workflow
    provider_probe = ai_workflow.split(
        "      - name: Probe NVIDIA streaming endpoint with a minimal prompt", 1
    )[1].split("      - name: Run isolated real-model token-stream acceptance test", 1)[0]
    assert 'timeout --signal=TERM --kill-after=5s 150s' in provider_probe
    assert 'httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)' in provider_probe
    assert 'max_tokens=512' in provider_probe
    assert 'endpoint_host == "integrate.api.nvidia.com" and config["model"].strip().lower() == "z-ai/glm-5.3-flash"' in provider_probe
    assert '"reasoning_effort": effort' in provider_probe
    assert '"chat_template_kwargs": {"clear_thinking": True}' in provider_probe
    assert "type(exc).__name__" in provider_probe
    provider_source = (root / "video_director" / "llm_provider.py").read_text(encoding="utf-8")
    nvidia_request_options = provider_source.split("def _model_request_options", 1)[1].split(
        "def generate(", 1
    )[0]
    assert '"max_tokens": 8192' in nvidia_request_options
    live_smoke_step = ai_workflow.split(
        "      - name: Run isolated real-model token-stream acceptance test", 1
    )[1].split("      - name: Install Remotion rendering runtime dependencies", 1)[0]
    # Explicitly opted-in real-provider acceptance runs only after config and
    # upstream visible-token probe pass; ordinary CI remains uncharged.
    assert "steps.live_stream_config.outcome == 'success'" in live_smoke_step
    assert "steps.live_provider_probe.outcome == 'success'" in live_smoke_step
    assert "VD_LLM_LIVE_STREAM_TEST" in live_smoke_step
    assert "live-stream-junit.xml" in live_smoke_step
    assert "id: live_stream_smoke" in ai_workflow
    assert "steps.live_stream_config.outcome" in ai_workflow
    assert "steps.live_stream_acceptance.conclusion == 'success' || steps.live_stream_acceptance.conclusion == 'skipped'" in ai_workflow
    assert "steps.live_stream_acceptance.conclusion == 'success'" in ai_workflow
    live_report_step = ai_workflow.split(
        "      - name: Report real-model token-stream acceptance gate", 1
    )[1].split("      - name: Check JavaScript syntax", 1)[0]
    assert "continue-on-error: true" not in live_report_step
    assert "LIVE_STREAM_CONFIG_OUTCOME" in live_report_step and "steps.live_stream_config.outcome" in live_report_step
    assert "LIVE_STREAM_TEST_OUTCOME" in live_report_step and "steps.live_stream_smoke.outcome" in live_report_step
    # Diagnostics are written to the built-in Actions log and job summary.
    # Do not restore upload-artifact or its proxy bridge: action downloads happen
    # before shell steps and have repeatedly failed on this SOCKS-backed runner.
    assert "actions/upload-artifact@v4" not in ai_workflow
    assert "Prepare local HTTP bridge for SOCKS-backed artifact upload" not in ai_workflow
    assert "Stop local HTTP bridge for SOCKS-backed artifact upload" not in ai_workflow
    diagnostic_summary = ai_workflow.split(
        "      - name: Summarize retained regression diagnostics", 1
    )[1].split("      - name: Publish verified React Studio bundle", 1)[0]
    assert "GITHUB_STEP_SUMMARY" in diagnostic_summary
    assert "junit.xml" in diagnostic_summary
    assert "timeline-junit.xml" in diagnostic_summary
    assert "Detailed pytest output and failure traces remain in the corresponding Actions step logs." in diagnostic_summary
    assert "steps.timeline_regression_gate.outcome == 'success'" in ai_workflow
    # Artifact publication itself must not trigger an endless build/publish commit loop.
    assert '"message": "build: publish verified React 19 Studio bundle [skip ci]"' in ai_workflow
    assert '"BLOCKED_PROVIDER":' in live_acceptance
    assert '"BLOCKED_MODEL":' in live_acceptance
    assert '"insufficient balance" in evidence' in live_acceptance
    assert '"has reached its end of life"' in live_acceptance
    assert 'LIVE_STREAM_ACCEPTANCE={status}' in live_acceptance
    assert 'if config_outcome != "skipped" and status != "PASS":' in live_acceptance
    assert 'python3.11" -m video_director.live_stream_acceptance --junit live-stream-junit.xml' in ai_workflow
    manual = (root / "docs" / "FILM_AGENT_MANUAL.md").read_text(encoding="utf-8")
    assert "当前真实模型验收只在本地执行" in manual
    assert "Real-model token-stream acceptance (local opt-in)" in manual



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

def test_live_model_stream_smoke_is_explicit_ci_opt_in_and_default_ci_reports_missing():
    """The live provider request is intentionally local; default CI must not fake a PASS."""
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "ai-director.yml").read_text(encoding="utf-8")
    config_step = workflow.split("      - name: Configure opt-in live model token-stream smoke", 1)[1].split(
        "      - name: Probe NVIDIA streaming endpoint with a minimal prompt", 1
    )[0]
    live_step = workflow.split("      - name: Run isolated real-model token-stream acceptance test", 1)[1].split(
        "      - name: Install Remotion rendering runtime dependencies", 1
    )[0]
    report_step = workflow.split(
        "      - name: Report real-model token-stream acceptance gate", 1
    )[1].split("      - name: Check JavaScript syntax", 1)[0]

    # The config/probe can only be reached through explicit opt-in configuration;
    # the expensive real-model acceptance itself is never run by normal CI.
    assert "contains(github.event.head_commit.message, '[live-stream-smoke]')" in workflow
    assert "VD_LLM_API_KEY_SECRET" in config_step
    assert "VD_LLM_BASE_URL_CONFIG" in config_step
    assert "VD_LLM_MODEL_CONFIG" in config_step
    assert "NVIDIA_API_KEY_SECRET" in config_step
    assert "NVIDIA_API_KEY" in config_step
    assert "resolve_live_api_key" in config_step
    assert 'saved_base_url=saved.get("base_url", "")' in config_step
    assert 'generic_api_key=os.environ.get("VD_LLM_API_KEY_SECRET", "")' in config_step
    helper = (root / "video_director" / "live_stream_acceptance.py").read_text(encoding="utf-8")
    assert 'saved_host == "integrate.api.nvidia.com"' in helper
    assert "NVIDIA live override requires NVIDIA_API_KEY" in helper
    assert "unrelated provider key." in helper
    assert "Opt-in live-model smoke is missing a complete key/base URL/model configuration." in config_step
    assert "raise SystemExit(1)" in config_step
    assert "steps.live_stream_config.outcome == 'success'" in live_step
    assert "steps.live_provider_probe.outcome == 'success'" in live_step
    assert "VD_LLM_LIVE_STREAM_TEST" in live_step
    assert "live-stream-junit.xml" in live_step
    assert "LIVE_STREAM_CONFIG_OUTCOME" in report_step
    assert "LIVE_STREAM_TEST_OUTCOME" in report_step
    assert "steps.live_stream_acceptance.conclusion == 'success' || steps.live_stream_acceptance.conclusion == 'skipped'" in workflow
    assert 'python3.11" -m video_director.live_stream_acceptance --junit live-stream-junit.xml' in workflow

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

def test_studio_hosted_workflow_isolated_from_ai_director_self_hosted_runner():
    root = Path(__file__).resolve().parents[1]
    ai_workflow = (root / ".github" / "workflows" / "ai-director.yml").read_text(encoding="utf-8")
    studio_workflow = (root / ".github" / "workflows" / "studio-ui-build.yml").read_text(encoding="utf-8")
    expected_group = "group: video-director-self-hosted-${{ github.repository }}"
    assert expected_group in ai_workflow
    assert expected_group not in studio_workflow
    assert "runs-on: ubuntu-24.04" in studio_workflow
    assert "group: studio-ui-hosted-" in studio_workflow
    assert "runs-on: [self-hosted, linux, x64]" not in studio_workflow
    assert "cancel-in-progress: false" in studio_workflow
    trigger_block = studio_workflow.split("permissions:", 1)[0]
    assert "\n  workflow_dispatch:\n" in trigger_block
    assert "\n  push:\n" not in trigger_block
    assert "contains(github.event.head_commit.message, '[unblock-runner]')" in ai_workflow


def test_ai_director_workflow_retains_regression_diagnostics():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "ai-director.yml").read_text(encoding="utf-8")
    assert "actions/upload-artifact@v4" not in workflow
    assert "Summarize retained regression diagnostics" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
    assert "timeline-junit.xml" in workflow
    assert "timeline-regression-output.log" in workflow
    assert "pytest-output.log" in workflow


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

