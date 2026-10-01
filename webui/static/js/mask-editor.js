// §18/FE-008 Mask Path Editor：画布顶点编辑器。
// 交互指纹（方案 §18）：
//   Inspector「编辑路径」→ Preview Overlay 显示顶点手柄
//   拖动点 / 新增点 / 删除点 / 闭合 / 羽化 / 反转 / 撤销 / 重做
// 数据流（§18 关键要求）：
//   Pointer Gesture → CoordinateMapper（Composition Space 统一坐标，无第二套 Mask Space）
//   → 拖动过程 local preview（不请求服务器）
//   → pointerup 才 mask.apply Command（apply_mask 带新 geometry）→ Revision
// 坐标统一：全部归一 0-1（Composition Space），画布只做 px↔归一换算。

const MASK_ED = {
  open: false,          // 编辑态
  sceneId: null, layerIdx: null,
  geometry: [],          // [{x, y}] 归一顶点（ Composition Space）
  closed: true,
  dragIdx: -1,
  undoStack: [], redoStack: [],
  feather: 0, inverted: false,
};

/** 打开/关闭路径编辑（Inspector 蒙板块按钮「编辑路径」调用）。 */
function maskEditorOpen(sceneId, idx){
  MASK_ED.open = !MASK_ED.open;
  MASK_ED.sceneId = sceneId; MASK_ED.layerIdx = idx;
  MASK_ED.undoStack = []; MASK_ED.redoStack = [];
  const lay = _maskEdLayer();
  const existing = lay && lay.mask && (lay.mask.shape === 'polygon' || lay.mask.shape === 'path');
  if (MASK_ED.open) {
    // 已有 path/polygon 蒙版 → 载入其顶点；否则给默认四角形让用户直接拖
    if (existing && Array.isArray(lay.mask.geometry) && lay.mask.geometry.length >= 3) {
      MASK_ED.geometry = lay.mask.geometry.map(g => ({x: g[0], y: g[1]}));
    } else {
      MASK_ED.geometry = [{x: 0.25, y: 0.25}, {x: 0.75, y: 0.25},
                          {x: 0.75, y: 0.75}, {x: 0.25, y: 0.75}];
    }
    MASK_ED.feather = (lay && lay.mask && lay.mask.feather) || 0;
    MASK_ED.inverted = !!(lay && lay.mask && lay.mask.inverted);
    if (typeof editorToast === 'function') editorToast('路径编辑已打开：拖动顶点；双击边加新点；右键点删点；空白处单击加点；完成后「保存路径」');
  }
  maskEditorRender();
}
function _maskEdLayer(){
  if (!ED.comp || MASK_ED.sceneId == null) return null;
  const sc = ED.comp.scenes.find(s => s.id === MASK_ED.sceneId);
  return sc && sc.layers[MASK_ED.layerIdx];
}

/** 画布渲染：蒙版多边形 + 顶点手柄（local preview，不发请求）。 */
function maskEditorRender(){
  const stage = document.getElementById('ed-preview-stage');
  if (!stage) return;
  if (!MASK_ED.open) {
    // 关闭态：移除 overlay 保留预览原状
    const ov = document.getElementById('mask-editor-overlay');
    if (ov) ov.remove();
    return;
  }
  let ov = document.getElementById('mask-editor-overlay');
  if (!ov) {
    ov = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    ov.id = 'mask-editor-overlay';
    ov.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;z-index:5;touch-action:none;cursor:crosshair';
    ov.setAttribute('preserveAspectRatio', 'none');
    stage.style.position = stage.style.position || 'relative';
    stage.appendChild(ov);
    ov.addEventListener('pointerdown', _maskEdPointerDown);
    ov.addEventListener('pointermove', _maskEdPointerMove);
    ov.addEventListener('pointerup', _maskEdPointerUp);
    ov.addEventListener('contextmenu', _maskEdContextMenu);
    ov.addEventListener('dblclick', _maskEdDblClick);
  }
  const W = stage.clientWidth || 400, H = stage.clientHeight || 400;
  ov.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const pts = MASK_ED.geometry.map(p => `${(p.x * W).toFixed(1)},${(p.y * H).toFixed(1)}`).join(' ');
  const area = MASK_ED.geometry.map(p =>
    `<circle cx="${(p.x * W).toFixed(1)}" cy="${(p.y * H).toFixed(1)}" r="7"
      fill="${MASK_ED.dragIdx >= 0 ? '#ffd166' : '#4a90d9'}" stroke="#fff" stroke-width="1.5"
      style="cursor:grab" data-vertex="${MASK_ED.geometry.indexOf(p)}"/>`).join('');
  ov.innerHTML =
    `<polygon points="${pts}" fill="rgba(74,144,217,0.18)" stroke="#4a90d9"
       stroke-width="2" stroke-dasharray="${MASK_ED.closed ? '' : '6 4'}"/>` + area +
    `<text x="8" y="16" style="fill:#9aa4ad;font-size:11px">蒙版路径编辑（${MASK_ED.geometry.length} 点${MASK_ED.closed ? ' · 已闭合' : ''}）——拖点/双击边加点/右键删点</text>`;
}

/** CoordinateMapper（§18 坐标统一）：px → 归一 Composition Space。 */
function _maskEdToNorm(ev){
  const stage = document.getElementById('ed-preview-stage');
  const r = stage.getBoundingClientRect();
  return {x: Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)),
          y: Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height))};
}
function _maskEdNearestVertex(p){
  let best = -1, bd = 0.03;  // 命中半径 3%（归一）
  MASK_ED.geometry.forEach((v, i) => {
    const d = Math.hypot(v.x - p.x, v.y - p.y);
    if (d < bd) { bd = d; best = i; }
  });
  return best;
}
function _maskEdNearestEdge(p){
  // 返回 {seg: i}（在第 i→i+1 边上最近），无近边 -1
  const n = MASK_ED.geometry.length;
  let best = -1, bd = 0.03;
  for (let i = 0; i < n; i++) {
    const a = MASK_ED.geometry[i], b = MASK_ED.geometry[(i + 1) % n];
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    const d = Math.hypot(mx - p.x, my - p.y);
    if (d < bd) { bd = d; best = i; }
  }
  return best;
}
function _maskEdPushUndo(){
  MASK_ED.undoStack.push(JSON.stringify(MASK_ED.geometry));
  if (MASK_ED.undoStack.length > 50) MASK_ED.undoStack.shift();
  MASK_ED.redoStack = [];
}

function _maskEdPointerDown(ev){
  if (ev.button !== 0) return;
  const p = _maskEdToNorm(ev);
  const vi = _maskEdNearestVertex(p);
  if (vi >= 0) {
    _maskEdPushUndo();
    MASK_ED.dragIdx = vi;
    maskEditorRender();
  } else if (MASK_ED.geometry.length < 16) {
    // 空白处单击：加点（末尾追加）
    _maskEdPushUndo();
    MASK_ED.geometry.push(p);
    maskEditorRender();
  }
}
function _maskEdPointerMove(ev){
  if (MASK_ED.dragIdx < 0) return;
  const p = _maskEdToNorm(ev);
  MASK_ED.geometry[MASK_ED.dragIdx] = p;
  maskEditorRender();  // local preview（不发请求 §18）
}
function _maskEdPointerUp(){
  if (MASK_ED.dragIdx < 0) return;
  MASK_ED.dragIdx = -1;
  maskEditorRender();
}
function _maskEdDblClick(ev){
  // 双击边中点：在该边插入新点
  const p = _maskEdToNorm(ev);
  const ei = _maskEdNearestEdge(p);
  if (ei >= 0 && MASK_ED.geometry.length < 16) {
    _maskEdPushUndo();
    MASK_ED.geometry.splice(ei + 1, 0, p);
    maskEditorRender();
  }
}
function _maskEdContextMenu(ev){
  // 右键顶点：删除（保持 ≥3 点）
  ev.preventDefault();
  const p = _maskEdToNorm(ev);
  const vi = _maskEdNearestVertex(p);
  if (vi >= 0 && MASK_ED.geometry.length > 3) {
    _maskEdPushUndo();
    MASK_ED.geometry.splice(vi, 1);
    maskEditorRender();
  }
}

function maskEditorUndo(){
  if (!MASK_ED.undoStack.length) { if (typeof editorToast === 'function') editorToast('没有可撤销的路径编辑'); return; }
  MASK_ED.redoStack.push(JSON.stringify(MASK_ED.geometry));
  MASK_ED.geometry = JSON.parse(MASK_ED.undoStack.pop());
  maskEditorRender();
}
function maskEditorRedo(){
  if (!MASK_ED.redoStack.length) { if (typeof editorToast === 'function') editorToast('没有可恢复的路径编辑'); return; }
  MASK_ED.undoStack.push(JSON.stringify(MASK_ED.geometry));
  MASK_ED.geometry = JSON.parse(MASK_ED.redoStack.pop());
  maskEditorRender();
}
function maskEditorToggleClose(){
  MASK_ED.closed = !MASK_ED.closed;
  maskEditorRender();
}

/** 保存路径：pointerup 语义的正式提交（§18：正式修改只在确认时发生）→ apply_mask Command。 */
async function maskEditorSave(){
  if (!MASK_ED.open || MASK_ED.geometry.length < 3) {
    if (typeof editorToast === 'function') editorToast('路径至少需要 3 个顶点');
    return;
  }
  // Mask.geometry 契约：[[x, y], ...]（归一）；closed 语义由顶点序表达（≥3 点即闭多边形）
  const geometry = MASK_ED.geometry.map(p => [+p.x.toFixed(4), +p.y.toFixed(4)]);
  const r = await executeCommand({
    command: 'apply_mask',
    targetId: `${MASK_ED.sceneId}#${MASK_ED.layerIdx}`,
    payload: {shape: 'path', geometry,
              feather: MASK_ED.feather || 0, inverted: MASK_ED.inverted},
  });
  if (r.ok) {
    MASK_ED.open = false;
    maskEditorRender();
    if (typeof editorToast === 'function') editorToast('蒙版路径已保存' + (DEV.mode ? `（版本 ${r.data.composition_version}）` : ''));
    if (typeof edShowInspector === 'function') edShowInspector();
  } else if (typeof editorToast === 'function') {
    editorToast('保存失败：' + (r.error || '未知错误'), true);
  }
}

Object.assign(window, {maskEditorOpen, maskEditorRender, maskEditorSave,
                       maskEditorUndo, maskEditorRedo, maskEditorToggleClose,
                       MASK_ED});
