// §12/§13 Action Dispatcher：业务按钮只声明 data-action，不再维护一份前端 Action 映射表。
//
// 唯一执行链：UI [data-action] → POST /api/action/execute
// → Action Registry → Capability → Command Bus → Composition/Workflow → 回读状态。
// UI-only 行为（打开抽屉、切换 Tab、播放）不应使用 data-action。

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

async function executeAction(actionId, params={}, target=null, selection=null){
  const pid = (typeof ED !== 'undefined' && ED.pid) || '';
  if (!pid) {
    editorToast('先打开项目，再执行该操作。', true);
    return {ok:false,status:'NO_PROJECT'};
  }
  const sel = selection || _dispatchSelection();
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
  await executeAction(actionId, params, target, _dispatchSelection());
  if (ev) ev.preventDefault();
  return false;
}

document.addEventListener('click', (ev) => {
  const el = ev.target.closest && ev.target.closest('[data-action]');
  if (!el) return;
  actionDispatch(el, ev);
});

Object.assign(window, {actionDispatch, executeAction});