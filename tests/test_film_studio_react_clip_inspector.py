"""React 19 clip inspector ownership contract."""
from pathlib import Path


def test_react_clip_inspector_is_wired_through_film_studio_bridge():
    root = Path(__file__).resolve().parents[1]
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    contract = (root / "remotion_runtime" / "src" / "film-timeline-contract.ts").read_text(encoding="utf-8")
    assert "renderClip(clip)" in studio
    assert "window.filmStudioReactInspector.renderClip(clip)" in studio
    assert "const ClipInspector: React.FC" in component
    assert 'data-react-clip-inspector="true"' in component
    assert 'id="fs-field-at"' in component
    assert 'id="fs-field-speed"' in component
    assert 'id="fs-field-volume"' in component
    assert 'id="fs-field-fade-in"' in component
    assert 'id="fs-field-title"' in component
    assert "export const buildCanvasElementOperation =" in component
    assert "window.filmStudioReactInspector.saveSelected()" in studio
    assert "window.filmStudioCommitCanvasElement=async function(operation)" in studio
    assert 'id="fs-toggle-clip-lock"' in component
    assert 'window.filmStudioSetClipLock?.(clip.id, !clipLocked)' in component
    assert 'window.filmStudioSetClipLock=async function(clipId,locked)' in studio
    assert 'kind == "clip_lock"' in (root / "video_director" / "film_document.py").read_text(encoding="utf-8")
    assert "filmStudioCommitInspectorOperations?: (ops: FilmOperation[]) => Promise<void> | void" in component
    assert "const [form, setForm] = useState<FilmClipInspectorValues>" in component
    assert "const update = <K extends keyof FilmClipInspectorValues>" in component
    assert 'value={form.at}' in component
    assert 'onChange={(event) => update("at", event.currentTarget.value)}' in component
    assert 'id="fs-apply-clip-inspector"' in component
    assert "export const buildClipInspectorOperations" in component
    assert "buildClipInspectorOperations(clip, {...form})" in component
    assert "filmStudioCommitInspectorOperations=async function(ops)" in studio
    assert "await filmStudioCommit(ops)" in studio
    assert 'field: "title" | "fade"; value: string | null' in contract
    assert 'field: "muted"; value: boolean' in contract
    assert "c.trackLocked = Object.prototype.hasOwnProperty.call(t.attrs || {}, 'locked')" in studio
    assert "trackLocked?: boolean" in component
    assert "if (clip.trackLocked)" in component
    assert "const trackLocked = Boolean(clip.trackLocked)" in component
    assert "disabled={readOnly}" in component
    assert "所在轨道已锁定；解锁轨道后才能编辑该片段。" in component
    assert 'id="fs-toggle-clip-lock" className="fs-btn" disabled={trackLocked}' in component
