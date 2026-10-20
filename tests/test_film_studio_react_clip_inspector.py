"""React 19 clip inspector ownership contract."""
from pathlib import Path


def test_react_clip_inspector_is_wired_through_film_studio_bridge():
    root = Path(__file__).resolve().parents[1]
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    assert "renderClip(clip)" in studio
    assert "window.filmStudioReactInspector.renderClip(clip)" in studio
    assert "const ClipInspector: React.FC" in component
    assert 'data-react-clip-inspector="true"' in component
    assert 'id="fs-field-at"' in component
    assert 'id="fs-field-speed"' in component
    assert 'id="fs-field-volume"' in component
    assert 'id="fs-field-fade-in"' in component
    assert 'id="fs-field-title"' in component
    assert "filmStudioSaveInspector?: (values?: FilmClipInspectorValues) => Promise<void> | void" in component
    assert "const [form, setForm] = useState<FilmClipInspectorValues>" in component
    assert "const update = <K extends keyof FilmClipInspectorValues>" in component
    assert 'value={form.at}' in component
    assert 'onChange={(event) => update("at", event.currentTarget.value)}' in component
    assert 'id="fs-apply-clip-inspector"' in component
    assert "window.filmStudioSaveInspector?.({...form})" in component
    assert "window.filmStudioSaveInspector=async function(form)" in studio
    assert "function read(key,id)" in studio
    assert "await filmStudioCommit(ops)" in studio
