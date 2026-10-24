"""React 19 clip inspector ownership contract."""
from pathlib import Path


def test_react_clip_inspector_is_wired_through_film_studio_bridge():
    root = Path(__file__).resolve().parents[1]
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    contract = (root / "remotion_runtime" / "src" / "film-timeline-contract.ts").read_text(encoding="utf-8")
    assert "window.filmStudioReactInspector.renderClip(Object.assign({},clip" in studio
    assert "const ClipInspector: React.FC" in component
    assert 'data-react-clip-inspector="true"' in component
    assert 'id="fs-field-at"' in component
    assert 'id="fs-field-speed"' in component
    assert 'id="fs-field-volume"' in component
    assert 'id="fs-field-fade-in"' in component
    assert 'id="fs-field-title"' in component

    source = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    render_end = source[source.index('  render(element) {'):source.index('  sync(element) {')]
    assert "if (element) this.sync(element);" in render_end
    assert "export const buildCanvasElementOperation =" in component
    assert "changed:Object.keys(style).length>0||textChanged" in component
    assert "const style:Record<string,string>={};" in component
    assert "if(!result.changed)return;" in component
    assert "editableCssLength(styles.lineHeight)" in component
    assert "editableCssLength(styles.letterSpacing)" in component
    assert "validEditableElementCssLength(lineHeightInput.value,'lineHeight')" in studio
    assert "if(!Object.keys(elementStyle).length&&!elementTextChanged)return;" in studio
    for field_id in (
        "fs-element-background-enabled", "fs-element-background-color",
        "fs-element-opacity", "fs-element-font-weight", "fs-element-text-align",
        "fs-element-line-height", "fs-element-letter-spacing",
    ):
        assert f'id="{field_id}"' in component
    assert "backgroundColor" in component and "normalizedFontWeight" in component
    assert "window.filmStudioReactInspector.saveSelected()" in studio
    assert "window.filmStudioCommitCanvasElement=async function(operation,options)" in studio
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
    assert "const clipFormSignature=JSON.stringify(clipFormDefaults)" in component
    assert "setForm({...clipFormDefaults})" in component
    assert "[clip?.id,clipFormSignature,keyframesAttribute]" in component
    assert "export const buildClipInspectorOperations" in component
    assert "buildClipInspectorOperations(clip, {...form})" in component
    assert "filmStudioCommitInspectorOperations=async function(ops)" in studio
    assert "await filmStudioCommit(ops)" in studio
    assert "var committed=await filmStudioCommit(ops)" in studio
    assert "if(!committed)" in studio
    assert "片段属性没有保存，请检查影片状态后重试。" in studio
    assert "return false;" in studio[studio.index("async function filmStudioCommit(ops, options)"):studio.index("async function filmStudioHistory(action)")]
    runtime = (root / "webui" / "static" / "js" / "film-runtime.js").read_text(encoding="utf-8")
    assert "localRect:message.localRect||rect" in runtime
    assert '| "src" | "title" | "fade" |' in contract
    assert '{ op: "set"; clip: string; field: FilmStringField; value: string | null }' in contract
    assert 'field: "muted"; value: boolean' in contract
    assert "c.trackLocked = Object.prototype.hasOwnProperty.call(t.attrs || {}, 'locked')" in studio
    assert "trackLocked?: boolean" in component
    assert "if (clip.trackLocked)" in component
    assert "const trackLocked = Boolean(clip.trackLocked)" in component
    assert "disabled={readOnly}" in component
    assert "所在轨道已锁定；解锁轨道后才能编辑该片段。" in component
    assert 'id="fs-toggle-clip-lock" className="fs-btn" disabled={trackLocked}' in component
    for field_id in ("fs-keyframe-property", "fs-keyframe-add", "fs-keyframe-clear"):
        assert f'id="{field_id}"' in component
    assert "keyframePath" in component
    assert "type FilmKeyframeBezier" in component
    assert "parseBezierEasing" in component
    assert "const bezierValue=" in component
    assert 'option value="bezier">自定义贝塞尔</option>' in component
    assert 'data-bezier-handle="p1"' in component and 'data-bezier-handle="p2"' in component
    assert "updateBezierControl" in component
    assert "fs-keyframe-bezier-graph" in component
    assert '"data-film-keyframes"' in contract
    assert 'field: FilmStringField; value: string | null' in contract
    assert "keyframes" in component


def test_react_film_operation_contract_covers_the_supported_editor_operations():
    from pathlib import Path

    contract_path = Path("remotion_runtime/src/film-timeline-contract.ts")
    contract = contract_path.read_text(encoding="utf-8")
    for operation in (
        "set", "move", "trim", "split", "slip", "roll", "slide", "join",
        "remove", "ripple_delete", "insert", "insert_ripple", "track_add",
        "track_remove", "track", "element_set", "clip_lock",
    ):
        assert f'op: "{operation}"' in contract, f"TypeScript FilmOperation is missing {operation}"
    for field in ("src", "volume_envelope", "overrides", "sync-lock"):
        assert f'"{field}"' in contract, f"TypeScript FilmOperation is missing field {field}"
