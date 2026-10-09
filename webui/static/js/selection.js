// §63 Selection Service（方案《FRONTEND_DEEP_UNIFICATION》§18/§63）：
// 全系统唯一 Selection 事实源。Timeline/Preview/Inspector/Library/CommandBar/Review
// 共同订阅 'selection:changed'，任何一个入口改变选择，其余区域立即同步。
//
// 契约：
//   Selection = {type: 'project'|'scene'|'layer'|'scene_pair', id: string}
//   layer id = "sceneId#idx"；scene_pair id = "fromSceneId->toSceneId"
// 不允许任何模块自维护第二套 selection（§60 禁止 3）。

// 唯一状态（挂 window 防整体重赋值脱钩）
window.SEL = {selection: null, selectionSet: new Set(), lastSceneIdx: -1};

function _selKey(type, id) { return type + ':' + id; }

/** 选择变更事件（§63）：所有区域订阅此事件同步。 */
function _emitSelectionChanged(origin){
  document.dispatchEvent(new CustomEvent('selection:changed',
    {detail: {selection: SEL.selection, selectionSet: SEL.selectionSet, origin: origin || ''}}));
}

/** 主选择 API：select(type, id, ev, origin)。ev 支持 Ctrl 多选 / Shift 范围（场景）。
 * ev.boxIds：框选批量事务（Editor Session §框选）——一次性提交整个命中集合，
 * 仍是本函数单一写入，且只 emit 一次 selection:changed（拖框过程中不逐元素提交）。 */
function select(type, id, ev, origin){
  const boxIds = ev && Array.isArray(ev.boxIds) ? ev.boxIds.filter(Boolean) : null;
  if (boxIds) {
    const additive = !!(ev && (ev.ctrlKey || ev.metaKey || ev.shiftKey));
    const next = additive ? new Set(SEL.selectionSet) : new Set();
    boxIds.forEach(x => next.add(_selKey(type, x)));
    SEL.selectionSet = next;
    SEL.selection = {type, id};
    if (type === 'scene' && ED.comp) {
      const idx = ED.comp.scenes.findIndex(s => s.id === id);
      if (idx >= 0) SEL.lastSceneIdx = idx;
    }
    _emitSelectionChanged(origin);
    return;
  }
  const wantMulti = !!(ev && (ev.ctrlKey || ev.metaKey));
  const wantRange = !!(ev && ev.shiftKey && type === 'scene');
  if (!wantMulti && !wantRange) SEL.selectionSet = new Set();
  if (wantRange && ED.comp) {
    const ids = ED.comp.scenes.map(s => s.id);
    const from = SEL.lastSceneIdx >= 0 ? SEL.lastSceneIdx : ids.indexOf(id);
    const to = ids.indexOf(id);
    const [lo, hi] = from <= to ? [from, to] : [to, from];
    for (let i = lo; i <= hi; i++) SEL.selectionSet.add(_selKey('scene', ids[i]));
    SEL.selection = {type: 'scene', id};
  } else if (wantMulti) {
    const k = _selKey(type, id);
    if (SEL.selectionSet.has(k)) SEL.selectionSet.delete(k); else SEL.selectionSet.add(k);
    SEL.selection = {type, id};
  } else {
    SEL.selectionSet = new Set([_selKey(type, id)]);
    SEL.selection = {type, id};
  }
  if (type === 'scene' && ED.comp) {
    SEL.lastSceneIdx = ED.comp.scenes.findIndex(s => s.id === id);
  }
  _emitSelectionChanged(origin);
}

function selectProject(pid){
  SEL.selection = {type: 'project', id: pid};
  SEL.selectionSet = new Set();
  _emitSelectionChanged('project');
}
function selectScene(sceneId, ev, origin){ select('scene', sceneId, ev, origin || 'timeline'); }
function selectLayer(sceneId, idx, ev, origin){ select('layer', `${sceneId}#${idx}`, ev, origin || 'timeline'); }
/** Scene Pair：相邻两镜关系（Morph/Continuity/Transition/Camera Path 的共同上下文 §47）。 */
function selectScenePair(fromSceneId, toSceneId, origin){
  SEL.selection = {type: 'scene_pair', id: `${fromSceneId}->${toSceneId}`};
  SEL.selectionSet = new Set([_selKey('scene', fromSceneId), _selKey('scene', toSceneId)]);
  _emitSelectionChanged(origin || 'review');
}
function clearSelection(origin){
  SEL.selection = null;
  SEL.selectionSet = new Set();
  _emitSelectionChanged(origin || 'clear');
}
/** 多选场景 → 取其中第一个场景（批量操作目标）。 */
function focusSelection(){
  const first = [...SEL.selectionSet][0];
  if (!first) return null;
  const [type, id] = first.split(':');
  return {type, id};
}
/** Selection 上下文（Command Bar / Director 请求携带 §23/§46）。 */
function getSelectionContext(){
  const sel = SEL.selection;
  if (!sel) return {type: 'none', id: ''};
  const ctx = {...sel};
  if (sel.type === 'layer') {
    const [sceneId, idxS] = sel.id.split('#');
    ctx.sceneId = sceneId;
    ctx.layerIndex = parseInt(idxS);
    const sc = ED.comp && ED.comp.scenes.find(s => s.id === sceneId);
    const lay = sc && sc.layers[ctx.layerIndex];
    if (lay) ctx.layerType = lay.type;
  } else if (sel.type === 'scene_pair') {
    const [fromId, toId] = sel.id.split('->');
    ctx.fromSceneId = fromId;
    ctx.toSceneId = toId;
  }
  return ctx;
}
/** 人话上下文标签（§23 Command Bar「正在修改：Scene 03 · 产品近景」）。 */
function selectionLabel(){
  const sel = SEL.selection;
  if (!sel) return '';
  if (sel.type === 'project') return '整个项目';
  if (sel.type === 'scene') {
    const sc = ED.comp && ED.comp.scenes.find(s => s.id === sel.id);
    return sc ? `场景 ${sel.id}（${(sc.text || '').slice(0, 12)}…）` : `场景 ${sel.id}`;
  }
  if (sel.type === 'layer') {
    const ctx = getSelectionContext();
    const lay = ctx.sceneId && ED.comp
      ? (() => { const sc = ED.comp.scenes.find(s => s.id === ctx.sceneId); return sc && sc.layers[ctx.layerIndex]; })()
      : null;
    return lay ? `${layerTypeCn(lay.type)}（${sel.id}）` : `图层 ${sel.id}`;
  }
  if (sel.type === 'scene_pair') {
    const [f, t] = sel.id.split('->');
    return `镜头关系 ${f} → ${t}`;
  }
  return '';
}

// ---- 兼容层：旧模块仍调用 ED.sel/ED.selSet。Selection Service 保持两者同步
//（过渡期单一事实源 = SEL；ED.sel 是镜像）。新代码一律用 select* API。
function _syncLegacyED(){
  if (typeof ED !== 'undefined') {
    ED.sel = SEL.selection;
    ED.selSet = SEL.selectionSet;
  }
}
document.addEventListener('selection:changed', _syncLegacyED);

// ---- §18 五区一致：Timeline / Preview / Inspector / CommandBar 在 Selection
// 变化时统一同步（Library 按 Domain 局部刷新，由 edLibLoad 场景列表自然反映）。
document.addEventListener('selection:changed', () => {
  const editorVisible = typeof document !== 'undefined'
    && document.getElementById('pane-editor')
    && document.getElementById('pane-editor').style.display !== 'none';
  if (!editorVisible) return;
  if (typeof editorRenderTimeline === 'function' && window.ED && ED.pid) editorRenderTimeline();
  if (typeof previewShowSelectionOverlay === 'function') previewShowSelectionOverlay();
  if (typeof directorRefreshContext === 'function') directorRefreshContext();
});

Object.assign(window, {
  select, selectProject, selectScene, selectLayer, selectScenePair,
  clearSelection, focusSelection, getSelectionContext, selectionLabel,
  _selKey,
});
