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
  join_through_edits: () => (typeof editorJoinThroughEdits === 'function' && editorJoinThroughEdits()),
  edit_visual_overlay: () => (typeof editorEditSelectedVisualOverlay === 'function' && editorEditSelectedVisualOverlay()),
  lift_layer_to_playhead: () => (typeof editorLiftSelectedLayerToPlayhead === 'function' && editorLiftSelectedLayerToPlayhead()),
  add_edit_all_tracks: () => (typeof editorAddEditAllTracks === 'function' && editorAddEditAllTracks()),
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

async function _startRenderWorkflow(actionId, pid, contextVersion){
  const kind = actionId === 'render.final' ? 'render_final' : 'render_draft';
  const isCurrent = () => typeof editorContextMatches === 'function'
    ? editorContextMatches(pid, contextVersion)
    : (!!ED && ED.pid === pid && Object.is(ED.version, contextVersion));
  try {
    const r = await apiPost('/api/workflows', {kind, project_id: pid});
    if (!r || !r.ok || !r.run_id) {
      const msg = (r && (r.error || r.reason)) || '渲染任务提交失败';
      if (isCurrent()) editorToast(msg, true);
      return isCurrent() ? (r || {ok:false,error:msg})
        : {ok:false,status:'PROJECT_CHANGED',project_id:pid,applied:false};
    }
    const runId = r.run_id;
    window._renderMonitors[runId] = true;
    if (isCurrent()) {
      const meta = document.getElementById('ed-preview-meta');
      if (meta) meta.textContent = kind === 'render_draft' ? '草稿渲染中……' : '成片渲染中……';
      editorToast(kind === 'render_draft' ? '草稿渲染已开始，可继续编辑' : '成片渲染已开始');
    }
    const poll = async () => {
      if (!window._renderMonitors[runId]) return;
      try {
        const d = await apiGet('/api/workflows/' + encodeURIComponent(runId));
        const st = d && d.status;
        if (st === 'COMPLETED') {
          delete window._renderMonitors[runId];
          if (!isCurrent()) return;
          const info = await previewLoadDraft(pid, contextVersion);
          if (!isCurrent() || !info || info.staleProjectResponse) return;
          const path = kind === 'render_draft' ? info.draft_path : info.final_path;
          if (kind === 'render_draft') {
            if (path) previewShowVideo(path, '草稿 · 已完成', pid, contextVersion);
            else previewRenderComposition();
          }
          if (!isCurrent()) return;
          const meta2 = document.getElementById('ed-preview-meta');
          if (meta2 && path) meta2.textContent = kind === 'render_draft' ? '草稿已完成' : '成片已完成';
          if (kind === 'render_final' && path) {
            previewShowVideo(path, '成片 · 已完成', pid, contextVersion);
            const box = document.getElementById('editor-preflight-box');
            if (box && isCurrent()) {
              box.innerHTML =
                `<div class="panel ok-box">成片渲染完成：<a class="dl" href="/file?path=${encodeURIComponent(path)}" download="final.mp4">下载 final.mp4</a>` +
                `<button class="btn-secondary btn-sm" style="margin-left:8px" onclick="editorExportFreeze('${encodeURIComponent(pid)}', 2)">定格+2s</button>` +
                `<button class="btn-secondary btn-sm" style="margin-left:8px" onclick="edGenerateMetadata('${encodeURIComponent(pid)}')">生成发布文案</button></div>`;
              void edMetadataMaybeShow(pid, contextVersion);
            }
          }
          if (isCurrent() && typeof taskRefresh === 'function') taskRefresh();
          if (isCurrent()) editorToast(kind === 'render_draft' ? '草稿渲染完成，已载入预览' : '成片渲染完成，已载入预览');
          return;
        }
        if (['FAILED','INTERRUPTED','CANCELLED'].includes(st)) {
          delete window._renderMonitors[runId];
          if (isCurrent()) {
            const reason = (d && d.error) || '渲染未完成';
            editorToast('渲染失败：' + reason, true);
            if (typeof taskRefresh === 'function') taskRefresh();
          }
          return;
        }
        setTimeout(poll, 800);
      } catch (e) {
        setTimeout(poll, 1500);
      }
    };
    poll();
    return isCurrent() ? r : {ok:false,status:'PROJECT_CHANGED',project_id:pid,applied:true,run_id:runId};
  } catch (e) {
    if (isCurrent()) editorToast('渲染任务提交失败：' + (e.message || e), true);
    return {ok:false,status:isCurrent()?'NETWORK_ERROR':'PROJECT_CHANGED',error:e.message || String(e),project_id:pid};
  }
}

/** P1 定格出口：POST /api/export_freeze → 成片尾帧定格 N 秒 → 换下载链接。 */
async function editorExportFreeze(encodedPid, freezeSeconds){
  const pid = decodeURIComponent(encodedPid || '');
  const contextVersion = ED && ED.version;
  const isCurrent = () => !!pid && (typeof editorContextMatches === 'function'
    ? editorContextMatches(pid, contextVersion) : (!!ED && ED.pid===pid && Object.is(ED.version,contextVersion)));
  if (!pid) { editorToast('缺少项目 ID', true); return {ok:false,status:'NO_PROJECT'}; }
  if (!isCurrent()) return {ok:false,status:'PROJECT_CHANGED',project_id:pid};
  const btn = document.querySelector('#editor-preflight-box button[onclick*="editorExportFreeze"]');
  if (btn) { btn.disabled = true; btn.textContent = '定格中…'; }
  try {
    const r = await apiPost('/api/export_freeze', {project_id:pid,freeze_seconds:freezeSeconds});
    if (!isCurrent()) return {ok:false,status:'PROJECT_CHANGED',project_id:pid,staleProjectResponse:true,applied:!!(r&&r.ok)};
    if (!r || !r.ok) throw new Error((r && r.error) || '定格失败');
    const box = document.getElementById('editor-preflight-box');
    const link = box ? box.querySelector('a.dl') : null;
    if (link) {
      link.href = '/file?path=' + encodeURIComponent(r.final_path);
      link.setAttribute('download', 'final_freeze.mp4');
      link.textContent = '下载 final_freeze.mp4（定格+' + freezeSeconds + 's）';
    }
    editorToast('已生成定格版本（+' + freezeSeconds + 's）');
    if (typeof previewLoadDraft === 'function') await previewLoadDraft(pid,contextVersion);
    return r;
  } catch (e) {
    if (isCurrent()) {
      editorToast('定格失败：' + (e.message || e), true);
      if (btn && btn.isConnected) { btn.disabled = false; btn.textContent = '定格+' + freezeSeconds + 's'; }
    }
    return {ok:false,status:isCurrent()?'ERROR':'PROJECT_CHANGED',error:e.message||String(e),project_id:pid};
  } finally {
    if (isCurrent() && btn && btn.isConnected) { btn.disabled = false; btn.textContent = '定格+' + freezeSeconds + 's'; }
  }
}
async function executeAction(actionId, params={}, target=null, selection=null){
  const pid = (typeof ED !== 'undefined' && ED.pid) || '';
  if (!pid) {
    editorToast('先打开项目，再执行该操作。', true);
    return {ok:false,status:'NO_PROJECT'};
  }
  const contextVersion = ED.version;
  if (typeof editorContextMatches === 'function' && !editorContextMatches(pid, contextVersion)) {
    return {ok:false,status:'PROJECT_CHANGED',project_id:pid,staleProjectResponse:true,applied:false,
      error:'项目选择已变化；当前动作未发送，请重新打开所选项目。'};
  }
  const sel = selection || _dispatchSelection();
  if (actionId === 'render.draft' || actionId === 'render.final') {
    return await _startRenderWorkflow(actionId, pid, contextVersion);
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
    const contextCurrent = typeof editorContextMatches === 'function'
      ? editorContextMatches(pid, contextVersion)
      : (typeof ED !== 'undefined' && ED.pid === pid && Object.is(ED.version, contextVersion));
    if (!contextCurrent) {
      return Object.assign({}, r || {}, {
        ok:false,status:'PROJECT_CHANGED',project_id:pid,staleProjectResponse:true,
        applied:!!(r && r.ok),
        error:r && r.ok
          ? '动作已在原项目提交；当前工作区已切换，未更新当前项目状态。'
          : '原项目动作已返回；当前工作区已切换，未触碰当前项目状态。',
      });
    }
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

/**
 * §66 交付元数据：生成发布文案（标题/简介/话题标签）。
 * 走 Action 通路 delivery.generate_metadata → tool_executor LLM 生成 →
 * params_json.delivery_metadata 落盘；失败诚实降级显示原因。
 */
async function edGenerateMetadata(encodedPid){
  const pid = decodeURIComponent(encodedPid || '');
  const contextVersion = ED && ED.version;
  const isCurrent = () => !!pid && (typeof editorContextMatches === 'function'
    ? editorContextMatches(pid,contextVersion) : (!!ED && ED.pid===pid && Object.is(ED.version,contextVersion)));
  if (!pid) { editorToast('缺少项目 ID', true); return; }
  if (!isCurrent()) return {ok:false,status:'PROJECT_CHANGED',project_id:pid};
  const btn = document.querySelector('#editor-preflight-box button[onclick*="edGenerateMetadata"]');
  if (btn) { btn.disabled = true; btn.textContent = '生成中…'; }
  try {
    const r = await executeAction('delivery.generate_metadata', {project_id:pid}, 'project');
    if (!isCurrent() || (r && r.staleProjectResponse)) return {ok:false,status:'PROJECT_CHANGED',project_id:pid,applied:!!(r&&r.applied)};
    if (!r || !r.ok) throw new Error((r && r.error) || '生成失败');
    const meta = (r.result && r.result.metadata) || null;
    if (!meta) throw new Error('未返回文案内容');
    edMetadataRender(meta,pid,contextVersion);
    editorToast('发布文案已生成并保存到项目参数');
    return r;
  } catch (e) {
    if (isCurrent()) editorToast('发布文案生成失败：' + (e.message || e), true);
    return {ok:false,status:isCurrent()?'ERROR':'PROJECT_CHANGED',error:e.message||String(e),project_id:pid};
  } finally {
    if (isCurrent() && btn && btn.isConnected) { btn.disabled = false; btn.textContent = '生成发布文案'; }
  }
}

/** 把发布文案渲染到成片完成框下方（可复制文本块）。 */
function edMetadataRender(meta, expectedProjectId, expectedVersion){
  if (expectedProjectId && typeof editorContextMatches === 'function'
      && !editorContextMatches(expectedProjectId,expectedVersion)) return;
  const box = document.getElementById('editor-preflight-box');
  if (!box) return;
  let panel = document.getElementById('ed-metadata-panel');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'ed-metadata-panel';
    panel.className = 'panel';
    panel.style.marginTop = '6px';
    box.appendChild(panel);
  }
  const tags = (meta.tags || []).map(t => '#' + String(t).replace(/^#/, '')).join(' ');
  panel.innerHTML =
    `<div style="font-weight:600;margin-bottom:4px">发布文案（已存入项目参数）</div>` +
    `<div style="margin:4px 0"><b>标题：</b>${esc(String(meta.title || ''))}</div>` +
    `<div style="margin:4px 0"><b>简介：</b>${esc(String(meta.description || ''))}</div>` +
    `<div style="margin:4px 0"><b>标签：</b>${esc(tags)}</div>`;
}

/** 项目已有历史发布文案（params_json.delivery_metadata）时直接展示。 */
async function edMetadataMaybeShow(pid, expectedVersion){
  const version = arguments.length >= 2 ? expectedVersion : (ED && ED.version);
  const isCurrent = () => !!pid && (typeof editorContextMatches === 'function'
    ? editorContextMatches(pid,version) : (!!ED && ED.pid===pid && Object.is(ED.version,version)));
  if (!isCurrent()) return {ok:false,status:'PROJECT_CHANGED',project_id:pid};
  try {
    const d = await apiGet('/api/project_detail?project_id=' + encodeURIComponent(pid));
    if (!isCurrent()) return {ok:false,status:'PROJECT_CHANGED',project_id:pid,staleProjectResponse:true};
    const meta = d && d.delivery_metadata;
    if (meta && meta.title) edMetadataRender(meta,pid,version);
    return {ok:true,project_id:pid,hasMetadata:!!(meta&&meta.title)};
  } catch (e) { return {ok:false,status:isCurrent()?'LOAD_FAILED':'PROJECT_CHANGED',project_id:pid,error:e.message||String(e)}; }
}

Object.assign(window, {actionDispatch, executeAction, UI_BEHAVIORS,
                       editorExportFreeze, edGenerateMetadata, edMetadataMaybeShow});
