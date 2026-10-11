// §56/§57/§58/§82 Action Dispatcher：业务按钮只声明 data-action，前端不维护
// Action→Command 映射表（Registry/Resolver 全在后端）。
//
// 两条通路（§58 契约）：
//   业务动作（ui 字段为空）  → POST /api/action/execute → Registry →
//                             Capability → Command → Composition/Workflow → 回读刷新
//   UI 交互动作（ui 字段非空）→ 本地 UI 行为（打开 Picker/Modal、播放、撤销）——
//                             由 UI_BEHAVIORS 表执行（§58「保留下来的函数只能是
//                             UI renderer/UI state/Picker/Modal/Preview」）
//
// §77 机械规则：每个业务按钮必须有 data-action 或属纯 UI 白名单（Tab 切换/
// 抽屉开关/筛选 chips 等不进 dispatcher——它们没有业务语义）。

function _dispatchSelection(){
  if (typeof SEL === 'undefined' || !SEL.selection) return {type:'project'};
  const s = SEL.selection;
  if (s.type === 'project') return {type:'project'};
  return {type:s.type, id:s.id || '', layer_type:s.layer_type || ''};
}

function _dispatchParams(el){
  const raw = el.getAttribute('data-params');
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (e) {
    editorToast('按钮参数不是合法 JSON，操作未发送。', true);
    return null;
  }
}

// §58 UI 交互动作本地行为表（key = ActionSpec.ui 字段值；不是业务映射，
// 是纯 UI renderer 调度——业务效果仍由打开的 UI 内的 data-action 走后端链）
const UI_BEHAVIORS = {
  undo: () => (typeof editorUndo === 'function' && editorUndo()),
  split_layer: () => (typeof editorSplitSelectedLayer === 'function' && editorSplitSelectedLayer()),
  redo: () => (typeof editorRedo === 'function' && editorRedo()),
  save: () => (typeof editorSaveBar === 'function' && editorSaveBar()),
  preview_draft: () => (typeof editorPreviewBar === 'function' && editorPreviewBar()),
  render_draft: () => (typeof edxRenderDraft === 'function' && edxRenderDraft()),
  move_prompt: () => (typeof editorMovePrompt === 'function' && editorMovePrompt()),
  add_broll_prompt: () => (typeof editorAddBrollPrompt === 'function' && editorAddBrollPrompt()),
  editor_session_snap: () => (typeof editorSessionToggleSnap === 'function' && editorSessionToggleSnap()),
  editor_session_box: () => (typeof editorSessionToggleBox === 'function' && editorSessionToggleBox()),
  creative_plan_toggle: () => (typeof window.creativePlanToggle === 'function' && window.creativePlanToggle()),
  creative_plan_reload: () => (typeof window.creativePlanReload === 'function' && window.creativePlanReload()),
  creative_plan_initialize: () => (typeof window.creativePlanInitialize === 'function' && window.creativePlanInitialize()),
  creative_plan_save: () => (typeof window.creativePlanSave === 'function' && window.creativePlanSave()),
  creative_plan_validate: () => (typeof window.creativePlanValidate === 'function' && window.creativePlanValidate()),
  open_asset_picker: () => {
    const sel = (typeof SEL !== 'undefined' && SEL.selection) || null;
    const id = (sel && sel.id) || '';
    const [sceneId, idxS] = id.includes('#') ? id.split('#') : [id, '0'];
    if (typeof editorOpenAssetPicker === 'function')
      editorOpenAssetPicker(sceneId, parseInt(idxS) || 0);
  },
  open_shot_picker: () => {
    const sel = (typeof SEL !== 'undefined' && SEL.selection) || null;
    if (sel && sel.type === 'scene' && typeof editorOpenShotPicker === 'function')
      editorOpenShotPicker(sel.id);
    else if (typeof editorToast === 'function')
      editorToast('先选中一个场景，再换镜头', true);
  },
  open_motion_picker: () => {
    const sel = (typeof SEL !== 'undefined' && SEL.selection) || null;
    if (sel && sel.type === 'layer' && typeof editorOpenMotionPicker === 'function') {
      const [sceneId, idxS] = (sel.id || '').split('#');
      editorOpenMotionPicker(sceneId, parseInt(idxS) || 0);
    } else if (typeof editorToast === 'function') {
      editorToast('先选中一个图层，再换动效', true);
    }
  },
  open_voice_settings: () => (typeof editorVoiceOpen === 'function' && editorVoiceOpen()),
  open_audio_bus: () => (typeof editorAudioBusOpen === 'function' && editorAudioBusOpen()),
  open_subtitle_style: () => (typeof editorSubtitleStyleOpen === 'function' && editorSubtitleStyleOpen()),
  generate_scene_visual: (params = {}) => {
    const sel = (typeof SEL !== 'undefined' && SEL.selection) || null;
    const sceneId = (sel && (sel.type === 'scene' ? sel.id : (sel.id || '').split('#')[0])) || '';
    if (typeof editorSceneGenerate === 'function') editorSceneGenerate(sceneId);
    if (params.prompt) {
      setTimeout(() => {
        const prompt = document.getElementById('gen-prompt');
        const type = document.getElementById('gen-type');
        const box = document.getElementById('gen-inline-box');
        const details = box && box.querySelector('details');
        if (prompt) prompt.value = params.prompt;
        if (type && params.type && ['image','video','graphic','motion'].includes(params.type)) {
          type.value = params.type;
          if (typeof applyGenerationAvailability === 'function') applyGenerationAvailability();
        }
        if (details) details.open = true;
        if (prompt) prompt.focus();
      }, 0);
    }
  },
  edit_mask: () => {
    const sel = (typeof SEL !== 'undefined' && SEL.selection) || null;
    if (sel && sel.type === 'layer' && typeof maskEditorOpen === 'function') {
      const [sceneId, idxS] = (sel.id || '').split('#');
      maskEditorOpen(sceneId, parseInt(idxS) || 0);
    } else if (typeof editorToast === 'function') {
      editorToast('先选中一个图层，再编辑蒙版', true);
    }
  },
  open_local_import: () => (typeof editorOpenAssetPicker === 'function'
    && editorOpenAssetPicker((SEL.selection && SEL.selection.id || '').split('#')[0] || '', 0, 'import')),
  open_stock_search: (params = {}) => {
    if (typeof edLibDomain === 'function') edLibDomain('content', 'stock');
    if (params.query) {
      setTimeout(() => {
        const q = document.getElementById('ed-stock-q');
        if (q) {
          q.value = params.query;
          if (typeof edLibStockSearch === 'function') edLibStockSearch();
        }
      }, 0);
    }
  },
  play_full_preview: () => (typeof edVoiceFullPreview === 'function' && edVoiceFullPreview()),
  play_music_preview: () => (typeof edLibAudioTry === 'function' && edLibAudioTry('bgm-demo')),
  open_voice_upload: () => (typeof edVoiceReplaceUpload === 'function' && edVoiceReplaceUpload()),
};

/** §58 UI 动作缓存：/api/actions 声明 ui 字段的 Action → 本地行为键。 */
window._UI_ACTION_CACHE = null;
async function _loadUiActions(){
  if (window._UI_ACTION_CACHE) return window._UI_ACTION_CACHE;
  try {
    const r = await fetch('/api/actions');
    const d = await r.json();
    const sys = (d && d.system && d.system.actions) || [];
    // system.actions 只是 id 列表；ui 声明要逐个取——直接拉 manifest groups
    const uiMap = {};
    for (const group of Object.values((d && d.groups) || {})) {
      for (const a of group) {
        if (a.ui) uiMap[a.id] = a.ui;
      }
    }
    window._UI_ACTION_CACHE = uiMap;
    return uiMap;
  } catch (e) {
    window._UI_ACTION_CACHE = {};
    return {};
  }
}

window._renderMonitors = window._renderMonitors || {};

async function _startRenderWorkflow(actionId, pid){
  const kind = actionId === 'render.final' ? 'render_final' : 'render_draft';
  try {
    const r = await apiPost('/api/workflows', {kind, project_id: pid});
    if (!r || !r.ok || !r.run_id) {
      const msg = (r && (r.error || r.reason)) || '渲染任务提交失败';
      editorToast(msg, true);
      return r || {ok:false,error:msg};
    }
    const runId = r.run_id;
    window._renderMonitors[runId] = true;
    const meta = document.getElementById('ed-preview-meta');
    if (meta) meta.textContent = (kind === 'render_draft' ? '草稿渲染中……' : '成片渲染中……');
    editorToast(kind === 'render_draft' ? '草稿渲染已开始，可继续编辑' : '成片渲染已开始');
    const poll = async () => {
      if (!window._renderMonitors[runId]) return;
      try {
        const d = await apiGet('/api/workflows/' + encodeURIComponent(runId));
        const st = d && d.status;
        if (st === 'COMPLETED') {
          delete window._renderMonitors[runId];
          const info = await previewLoadDraft();
          const path = kind === 'render_draft' ? info && info.draft_path : info && info.final_path;
          if (kind === 'render_draft') {
            if (path) previewShowVideo(path, '草稿 · 已完成');
            else previewRenderComposition();
          }
          const meta2 = document.getElementById('ed-preview-meta');
          if (meta2 && path) meta2.textContent = (kind === 'render_draft' ? '草稿已完成' : '成片已完成');
          if (typeof taskRefresh === 'function') taskRefresh();
          editorToast(kind === 'render_draft' ? '草稿渲染完成，已载入预览' : '成片渲染完成');
          return;
        }
        if (['FAILED','INTERRUPTED','CANCELLED'].includes(st)) {
          delete window._renderMonitors[runId];
          const reason = (d && d.error) || '渲染未完成';
          editorToast('渲染失败：' + reason, true);
          if (typeof taskRefresh === 'function') taskRefresh();
          return;
        }
        setTimeout(poll, 800);
      } catch (e) {
        setTimeout(poll, 1500);
      }
    };
    poll();
    return r;
  } catch (e) {
    editorToast('渲染任务提交失败：' + (e.message || e), true);
    return {ok:false,error:e.message || String(e)};
  }
}
async function executeAction(actionId, params={}, target=null, selection=null){
  const pid = (typeof ED !== 'undefined' && ED.pid) || '';
  if (!pid) {
    editorToast('先打开项目，再执行该操作。', true);
    return {ok:false,status:'NO_PROJECT'};
  }
  const sel = selection || _dispatchSelection();
  if (actionId === 'render.draft' || actionId === 'render.final') {
    return await _startRenderWorkflow(actionId, pid);
  }
  const targetValue = target || (
    sel.type === 'project' || !sel.id ? 'project' : 'current_selection'
  );
  try {
    const r = await apiPost('/api/action/execute', {
      project_id: pid,
      action: actionId,
      target: targetValue,
      params: params || {},
      selection: sel,
    });
    if (!r.ok) {
      const reason = r.reason || r.error || '操作未生效';
      editorToast(reason, true);
      return r;
    }
    if (typeof r.composition_version === 'number' && typeof ED !== 'undefined') ED.version = r.composition_version;
    if (r.result && r.result.composition && typeof ED !== 'undefined') ED.comp = r.result.composition;
    if (typeof refreshWorkspace === 'function') await refreshWorkspace();
    else if (typeof editorLoad === 'function') await editorLoad();
    editorToast(r.message || '已应用');
    return r;
  } catch (e) {
    const msg = e && e.message ? e.message : String(e);
    editorToast('操作失败：' + msg, true);
    return {ok:false,status:'NETWORK_ERROR',error:msg};
  }
}

async function actionDispatch(el, ev){
  const actionId = el.getAttribute('data-action');
  if (!actionId) return true;
  if (el.disabled || el.getAttribute('aria-disabled') === 'true') {
    if (ev) ev.preventDefault();
    return false;
  }
  const params = _dispatchParams(el);
  if (params === null) {
    if (ev) ev.preventDefault();
    return false;
  }
  const target = el.getAttribute('data-target') || null;
  // §58 分流：ui 型动作（Registry 声明 ui 字段）本地执行，不进后端
  const uiMap = await _loadUiActions();
  const uiKey = uiMap[actionId];
  if (uiKey && typeof UI_BEHAVIORS[uiKey] === 'function') {
    UI_BEHAVIORS[uiKey](params);
    if (ev) ev.preventDefault();
    return false;
  }
  if (uiKey) {
    editorToast(`界面动作 ${actionId} 未接线（已诚实报告）`, true);
    if (ev) ev.preventDefault();
    return false;
  }
  await executeAction(actionId, params, target, _dispatchSelection());
  if (ev) ev.preventDefault();
  return false;
}

document.addEventListener('click', (ev) => {
  const el = ev.target.closest && ev.target.closest('[data-action]');
  if (!el) return;
  actionDispatch(el, ev);
});

Object.assign(window, {actionDispatch, executeAction, UI_BEHAVIORS});
