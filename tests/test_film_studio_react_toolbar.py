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
    assert "const StudioReactOwner: React.FC" in component
    assert component.count("createRoot(") == 1
    assert "createPortal" in component
    assert "<TimelineSurface key={model.renderKey}" not in component
    assert "key={track.id}" in component
    assert "key={clip.id}" in component
    assert "S.timelineRenderSequence=" not in studio
    roll_test = (root / "tests" / "test_film_studio_roll_browser.py").read_text(encoding="utf-8")
    insert_test = (root / "tests" / "test_film_studio_insert_browser.py").read_text(encoding="utf-8")
    assert '[data-film-action="split"]:visible' in roll_test
    assert '[data-film-action="slide-mode"]:visible' in roll_test
    assert '[data-film-action="insert-mode"]:visible' in insert_test
    assert 'data-film-action="slip-mode"' in component
    assert 'data-film-action="copy-clips"' in component
    assert 'data-film-action="paste-clips"' in component
    assert "window.filmStudioCopyClips" in studio
    assert "window.filmStudioPasteClips" in studio
    assert "window.filmStudioReactAssets.render({" in studio
    assert 'data-react-asset-list="true"' in component
    assert 'id="fs-assets-list"' in html
    assert 'id="fs-react-scrub-row"' in html
    assert 'id="fs-static-scrub-row"' in html
    assert "const StudioScrubRow: React.FC" in component
    assert "renderScrub(): void" in component
    assert "bridge.renderScrub();" in studio


def test_react_timeline_keeps_stable_dom_identity_for_tracks_and_clips():
    root = Path(__file__).resolve().parents[1]
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    assert "const StudioReactOwner: React.FC" in component
    assert component.count("createRoot(") == 1
    assert "createPortal" in component
    assert 'studioPortal("fs-timeline", <TimelineSurface model={state.timeline} />)' in component
    assert "window.filmStudioReactTimeline = {" in component
    assert "<TimelineSurface key=" not in component
    assert "key={track.id}" in component
    assert "key={clip.id}" in component


def test_stable_react_timeline_nodes_do_not_accumulate_native_event_handlers():
    from pathlib import Path

    studio = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "if(el.__filmStudioClipBound)return;" in studio
    assert "if(el.__filmStudioTrackLockBound)return;" in studio
    assert "if(el.__filmStudioTrackFlagBound)return;" in studio
    assert "if(el.__filmStudioRulerBound)return;" in studio
    assert "if(canvas.__filmStudioEnvelopeBound)return;" in studio
    assert "var clip=findClip(canvas.getAttribute('data-envelope-clip'));" in studio


def test_react_owns_clip_gesture_move_and_up_dispatch_and_projects_enter_film_studio():
    root = Path(__file__).resolve().parents[1]
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    html = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "window.filmStudioOpenIfCurrentProjectHasFilm = async function(expectedProjectId,knownFilmDocument)" in studio
    assert "async function filmStudioLoad(force,knownFilmDocument)" in studio
    assert "return await filmStudioLoad(true,result);" in studio
    assert "return await window.filmStudioToggle(result);" in studio
    assert "var loaded=await filmStudioLoad(true,knownFilmDocument);" in studio
    assert "if(requestProjectId!==S.projectId||requestGeneration!==S.loadGeneration)return false;" in studio
    assert "window.filmStudioToggleTrackLock?.(track.id)" in component
    assert "window.filmStudioToggleTrackFlag?.(track.id, \"sync-lock\")" in component
    assert "window.filmStudioToggleTrackLock = function(trackId)" in studio
    assert "window.filmStudioToggleTrackFlag = function(trackId,field)" in studio
    workspace = (root / "webui" / "static" / "js" / "workspace.js").read_text(encoding="utf-8")
    assert "const filmDocument = await apiGet('/api/editor/film?project_id=' + encodeURIComponent(pid));" in workspace
    assert "if (openedStudio) return {ok: true, mode: 'film'};" in workspace
    assert "Legacy projects without film.html keep the CompositionIR workspace/migration path." in workspace
    # Project selection was deliberately consolidated in workspaceOpen(); the film
    # adapter should not bind a second selector change listener.
    assert "const selEl = document.getElementById('editor-project');" in workspace
    assert "selEl.value = pid;" in workspace
    assert "selector.addEventListener('change'" not in studio
    assert "统一影片工作台" in html
    assert "activePointerId = useRef<number | null>(null)" in component
    assert "window.filmStudioOwnsClipGesturePointer = ownsPointer" in component
    assert "window.filmStudioOwnsClipGesturePointer(e.pointerId)" in studio
