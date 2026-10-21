"""Studio must reconcile external revisions after busy work or stale drags finish."""
from pathlib import Path


def test_studio_queues_external_revisions_until_safe_to_reload():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "pendingExternalRevision:''" in source
    assert "function flushPendingExternalRevision()" in source
    assert "if(!S.pendingExternalRevision||!S.open||S.busy||S.drag||S.envelopeDrag)return false;" in source
    assert "S.pendingExternalRevision=data.revision;" in source
    assert "flushPendingExternalRevision();" in source[source.index("async function filmStudioCommit"):source.index("async function filmStudioHistory")]


def test_studio_discards_timeline_and_envelope_drag_when_external_revision_arrives():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "本次旧画面拖动未提交，先载入外部版本" in source
    assert "本次拖动未提交，先载入外部版本" in source
    assert "S.pendingExternalRevision" in source[source.index("async function onUp"):source.index("document.addEventListener('pointermove',onUp)") if "document.addEventListener('pointermove',onUp)" in source else source.index("document.addEventListener('pointermove',onMove)")]
    pointer_cancel = source[source.index("document.addEventListener('pointercancel'"):source.index("window.filmStudioSetSideMode")]
    assert "flushPendingExternalRevision();" in pointer_cancel


def test_studio_does_not_apply_load_response_mid_drag_or_busy_edit():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    load = source[source.index("async function filmStudioLoad(force)"):source.index("async function filmStudioInitialize")]
    assert load.index("var r=await apiGet(") < load.index("if(S.busy||S.drag||S.envelopeDrag){")
    assert "S.pendingExternalRevision=(r&&r.rev&&r.rev!==S.rev)?r.rev:'__reload__';" in load
    assert load.index("if(S.busy||S.drag||S.envelopeDrag){") < load.index("S.data=r;S.rev=r.rev;")
    assert "pending!==S.rev" in source[source.index("function flushPendingExternalRevision"):source.index("async function filmStudioCommit")]


def test_studio_discards_out_of_order_load_responses():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    load = source[source.index("async function filmStudioLoad(force)"):source.index("async function filmStudioInitialize")]
    assert "loadGeneration:0" in source
    assert "var requestGeneration=++S.loadGeneration;" in load
    assert "if(requestProjectId!==S.projectId||requestGeneration!==S.loadGeneration)return;" in load
    assert load.index("requestGeneration!==S.loadGeneration") < load.index("S.data=r;S.rev=r.rev;")


def test_studio_preserves_unsubmitted_inspector_and_canvas_form_drafts_on_external_revision():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "function hasUncommittedInspectorEdits()" in source
    for field in ("fs-field-at", "fs-field-speed", "fs-field-volume", "fs-field-muted", "fs-field-fade-in", "fs-field-fade-out", "fs-field-title"):
        assert field in source
    for field in ("fs-element-text", "fs-element-left", "fs-element-top", "fs-element-width", "fs-element-height", "fs-element-color", "fs-element-font-size", "fs-element-rotate"):
        assert field in source
    flush = source[source.index("function flushPendingExternalRevision"):source.index("async function filmStudioCommit")]
    assert "if(hasUncommittedInspectorEdits())" in flush
    assert "未提交的属性草稿" in flush


def test_explicit_reload_confirms_discard_and_canvas_save_reports_failure_honestly():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "window.filmStudioReload=function()" in source
    reload = source[source.index("window.filmStudioReload=function()"):source.index("async function filmStudioCommit", source.index("window.filmStudioReload=function()"))]
    assert "if(S.busy||S.drag||S.envelopeDrag)" in reload
    assert reload.index("if(S.busy||S.drag||S.envelopeDrag)") < reload.index("var dirty=hasUncommittedInspectorEdits()")
    assert "重新载入将放弃这些修改" in source
    assert "reload: () => window.filmStudioReload()" in source
    save_start = source.index("window.filmStudioSaveSelectedElement=async function()")
    save_end = source.index("window.addEventListener('message',function(event)", save_start)
    save = source[save_start:save_end]
    assert "var committed=await filmStudioCommit([elementOp]);" in save
    assert "if(!committed||S.rev===previousRevision)return;" in save
    assert "flushPendingExternalRevision();" in save
