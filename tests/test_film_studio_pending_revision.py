"""Studio must reconcile external revisions after busy work or stale drags finish."""
from pathlib import Path


def test_studio_queues_external_revisions_until_safe_to_reload():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "pendingExternalRevision:''" in source
    assert "function flushPendingExternalRevision()" in source
    assert "if(!S.pendingExternalRevision||!S.open||S.broken||S.busy||S.drag||S.envelopeDrag)return false;" in source
    assert "S.pendingExternalRevision=data.revision;" in source
    assert "flushPendingExternalRevision();" in source[source.index("async function filmStudioCommit"):source.index("async function filmStudioHistory")]


def test_studio_discards_timeline_and_envelope_drag_when_external_revision_arrives():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "本次旧画面拖动未提交，先载入外部版本" in source
    assert "本次拖动未提交，先载入外部版本" in source
    react_finish = source[source.index("function finishReactClipGesture(e, preparedOperations)"):source.index("async function onUp(e)")]
    assert "if(S.pendingExternalRevision)" in react_finish
    assert "本次旧画面拖动未提交" in react_finish
    assert react_finish.index("if(S.pendingExternalRevision)") < react_finish.index("await filmStudioCommit(preparedOperations,{requireExactRevision:true});")
    assert "flushPendingExternalRevision();" in react_finish
    pointer_cancel = source[source.index("document.addEventListener('pointercancel'"):source.index("window.filmStudioSetSideMode")]
    assert "cancelPointerGesture();" in pointer_cancel
    cancel_helper = source[source.index("function cancelPointerGesture()"):source.index("window.filmStudioCancelClipGesture=function")]
    assert "flushPendingExternalRevision();" in cancel_helper


def test_studio_does_not_apply_load_response_mid_drag_or_busy_edit():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    load = source[source.index("async function filmStudioLoad("):source.index("async function filmStudioInitialize")]
    load_guard = "if((!changed&&S.busy)||S.drag||S.envelopeDrag||hasUncommittedInspectorEdits()){"
    assert load.index("try{r=await apiGet(") < load.index(load_guard)
    assert load.index("if(!r){") < load.index("try{r=await apiGet(")
    assert "var r=knownFilmDocument||null;" in load
    assert "S.pendingExternalRevision=(r&&r.rev&&r.rev!==S.rev)?r.rev:'__reload__';" in load
    assert load.index(load_guard) < load.index("S.data=r;S.rev=r.rev;")
    assert "pending!==S.rev" in source[source.index("function flushPendingExternalRevision"):source.index("async function filmStudioCommit")]


def test_studio_discards_out_of_order_load_responses():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    load = source[source.index("async function filmStudioLoad("):source.index("async function filmStudioInitialize")]
    assert "loadGeneration:0" in source
    assert "var requestGeneration=++S.loadGeneration;" in load
    assert "if(requestProjectId!==S.projectId||requestGeneration!==S.loadGeneration)return false;" in load
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
    assert "window.filmStudioReactInspector.saveSelected()" in save
    assert "var committed=await filmStudioCommit([operation],{allowInspectorDraft:options&&options.allowInspectorDraft?'canvas':''});" in source
    assert "if(!committed||!S.data||!S.data.exists||S.rev===previousRevision)return;" in source
    assert "flushPendingExternalRevision();" in save


def test_dirty_inspector_blocks_export_history_and_project_switch():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    history = source[source.index("async function filmStudioHistory(action)"):source.index("function onMove(e)")]
    assert "if(hasUncommittedInspectorEdits())" in history
    assert "再撤销/重做" in history
    render = source[source.index("window.filmStudioRender=async function()"):source.index("function renderProposalPanel")]
    assert "if(hasUncommittedInspectorEdits())" in render
    assert "避免输出旧 revision" in render
    load = source[source.index("async function filmStudioLoad("):source.index("async function filmStudioInitialize")]
    assert "if(changed&&hasUncommittedInspectorEdits())" in load
    assert "selector.value=S.projectId" in load


def test_timeline_remove_preserves_selection_when_dirty_commit_is_rejected():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    remove = source[source.index("window.filmStudioRemove=async function"):source.index("window.filmStudioJoinThroughEdit")]
    assert "var committed=await filmStudioCommit(" in remove
    assert "if(!committed)return;" in remove


def test_all_document_mutations_respect_unsaved_inspector_drafts():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "if(changed&&hasUncommittedInspectorEdits())" in source
    assert "async function filmStudioCommit(ops, options)" in source
    assert "options.allowInspectorDraft||''" in source
    assert "hasUncommittedClipInspectorEdits()" in source
    assert "hasUncommittedCanvasInspectorEdits()" in source
    assert "再撤销/重做" in source
    assert "再添加素材" in source
    assert "再应用 AI 提案" in source
    assert "再请求 AI 修改" in source
    assert "避免输出旧 revision" in source
    assert "退出 Studio 将放弃这些修改" in source
    remove = source[source.index("window.filmStudioRemove=async function"):source.index("window.filmStudioJoinThroughEdit")]
    assert "if(!committed)return;" in remove
    poll = source[source.index("S.poll=setInterval(async function"):source.index("},1600);",source.index("S.poll=setInterval(async function"))]
    assert "S.pendingExternalRevision=r.rev;" in poll
    assert "S.data=r;S.rev=r.rev;S.broken=false" not in poll


def test_only_matching_inspector_can_submit_its_own_dirty_form():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "filmStudioCommit(ops,{allowInspectorDraft:'clip'})" in source
    assert "options&&options.allowInspectorDraft?'canvas':''" in source
    tsx = (Path(__file__).resolve().parents[1] / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    assert "result.operation, {allowInspectorDraft:true}" in tsx


def test_rejected_timeline_commit_rolls_back_dom_without_remounting_inspector():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    start = source.index("if(hasConflictingDraft){", source.index("async function filmStudioCommit(ops, options)"))
    end = source.index("if(S.busy){clearTimelineGesturePreview();", start)
    rejected = source[start:end]
    assert "renderTimeline();" in rejected
    assert "render();" not in rejected
    assert "clearTimelineGesturePreview();" in rejected
    assert "return false;" in rejected



def test_broken_external_document_preserves_current_view_and_defers_reload_until_recovery():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    helper_start = source.index("function flushPendingExternalRevision")
    helper = source[helper_start:source.index("window.filmStudioReload=function()", helper_start)]
    broken_start = source.index("data.phase==='document_broken'")
    recovered_start = source.index("data.phase==='revision_changed'&&data.source==='external_file'", broken_start)
    broken = source[broken_start:recovered_start]
    assert "||S.broken||" in helper
    assert "S.broken=true;" in broken
    assert "保留当前画面并暂停编辑" in broken
    assert "S.pendingExternalRevision=data.revision" not in broken
    assert "flushPendingExternalRevision();" not in broken
    recovered = source[recovered_start:]
    assert "S.broken=false;" in recovered
    assert "S.pendingExternalRevision=data.revision;" in recovered
    assert "pendingRevisionSource=data.source||'external_file';" in recovered
    assert "flushPendingExternalRevision();" in recovered
    assert recovered.index("S.pendingExternalRevision=data.revision;") < recovered.index("flushPendingExternalRevision();")

def test_studio_restores_project_selector_when_switch_is_blocked_by_busy_transaction():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    load = source[source.index("async function filmStudioLoad("):source.index("async function filmStudioInitialize")]
    busy_guard = load.split("if(S.busy&&(!changed||S.pendingTransaction)){", 1)[1]

    assert "if(selector&&S.projectId)selector.value=S.projectId;" in busy_guard
    assert busy_guard.index("selector.value=S.projectId") < busy_guard.index("return false;")
    assert "上一项影片操作仍在保存；本次项目切换未执行。" in busy_guard
    assert "var changed=nextId!==S.projectId;" in load
    assert "if(S.busy&&(!changed||S.pendingTransaction)){" in load
    assert "var changed=nextId!==S.projectId;" in load

    store = (Path(__file__).resolve().parents[1] / "remotion_runtime" / "src" / "film-store.ts").read_text(encoding="utf-8")
    bindings = store[store.index("const bindings: Array"):store.index("for (const [legacyKey")]
    assert '["busy", "busy"]' in bindings

def test_studio_close_blocks_async_work_and_cancels_uncommitted_timeline_gestures():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    close = source[source.index("window.filmStudioClose=function()"):source.index("window.filmStudioLoad=filmStudioLoad")]
    for guard in ("if(S.rendering)", "if(S.busy)"):
        assert guard in close
        assert close.index(guard) < close.index("S.open=false")
    assert close.index("if(S.drag||S.envelopeDrag)cancelPointerGesture();") < close.index("S.open=false")
    assert "AI 指令仍在生成；先停止或等待生成结束，再退出 Studio。" in close
    assert "影片操作仍在保存；保存结束后再退出 Studio。" in close
    cancel = source[source.index("function cancelPointerGesture()"):source.index("window.filmStudioCancelClipGesture=function")]
    assert "S.envelopeDrag=null" in cancel
    assert "S.drag=null" in cancel
    assert "clearTimelineGesturePreview();" in cancel

def test_creative_plan_export_is_bound_to_the_active_project():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "creative-plan.js").read_text(encoding="utf-8")
    export = source[source.index("window.creativePlanExportForFilmDirector"):source.index("window.creativePlanValidate = async function")]
    assert "if (!state.plan || !state.projectId || state.projectId !== projectId()) return null;" in export



def test_stale_export_rejection_reloads_authoritative_film_document():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    start = source.index("window.filmStudioRender=async function()")
    end = source.index("function findProposalClip", start)
    render = source[start:end]

    assert "/REVISION_CONFLICT|影片文档已变化/.test(renderError)" in render
    assert "await filmStudioLoad(true);" in render
    assert "旧版本没有发布" in render
    assert render.index("S.rendering=false;") < render.index("await filmStudioLoad(true);")
