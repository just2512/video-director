// §64 Preview Service（方案《FRONTEND_DEEP_UNIFICATION》§19/§64）：
// 中央 Preview Stage 是唯一视觉事实。所有选择/修改都反馈到这里：
//   选择场景 → 高亮；选 Layer → 边界框；画中画 → PiP 框；蒙版编辑 → 顶点手柄；
//   Scene Pair → A→B 关系提示。禁止各模块自建第二个 <video>/iframe。
//
// 职责：loadDraft / showSelectionOverlay / showMaskOverlay / showScenePair /
//       play / pause / seek

window.PV = {draftPath: '', finalPath: '', playhead: 0, pairMode: null};

/** 加载项目草稿/成片信息（顶栏「预览」与打开项目时调用）。 */
async function previewLoadDraft(){
  if (!ED.pid) return {ok: false, error: '无项目'};
  const info = await apiGet('/api/projects/' + encodeURIComponent(ED.pid) + '/draft_info');
  if (info && !info.error) {
    PV.draftPath = info.draft_path || '';
    PV.finalPath = info.final_path || '';
  }
  return info;
}

/** 把视频渲染进中央 Stage（唯一 Preview 宿主 §19）。 */
function previewShowVideo(path, label){
  const stage = document.getElementById('ed-preview-stage');
  const meta = document.getElementById('ed-preview-meta');
  if (!stage) return;
  if (!path) {
    stage.innerHTML = '<div class="hint">尚无草稿（时间线「渲染草稿」后可预览）</div>';
    if (meta) meta.textContent = '无草稿';
    return;
  }
  stage.innerHTML = `<video controls src="/file?path=${encodeURIComponent(path)}"></video>`;
  if (meta) meta.textContent = label || ('草稿 · ' + path.split(/[\\/]/).pop());
  _previewApplyOverlay();
}

/** 播放/暂停/seek（§64）。 */
function previewPlay(){
  const v = document.querySelector('#ed-preview-stage video');
  if (v) v.play().catch(()=>{});
}
function previewPause(){
  const v = document.querySelector('#ed-preview-stage video');
  if (v) v.pause();
}
function previewSeek(sec){
  const v = document.querySelector('#ed-preview-stage video');
  if (v && sec >= 0) { v.currentTime = sec; PV.playhead = sec; }
}

/** 选择高亮 Overlay：Selection → Preview 边界框（§19）。
 *  无视频时给出占位画布（场景序号 + 时长条），让 Preview 永不空转。 */
function previewShowSelectionOverlay(){
  const stage = document.getElementById('ed-preview-stage');
  if (!stage) return;
  const hasVideo = !!stage.querySelector('video');
  if (!hasVideo) previewRenderPlaceholder();
  _previewApplyOverlay();
}

/** 无视频时的占位 Preview：全片场景结构条 + 当前选中高亮（创作中可见反馈）。 */
function previewRenderPlaceholder(){
  const stage = document.getElementById('ed-preview-stage');
  if (!stage || !ED.comp) return;
  const c = ED.comp;
  const total = c.duration || 1;
  const selSceneId = SEL.selection && (SEL.selection.type === 'scene'
    ? SEL.selection.id
    : (SEL.selection.type === 'layer' ? SEL.selection.id.split('#')[0]
       : (SEL.selection.type === 'scene_pair' ? SEL.selection.id.split('->')[1] : '')));
  const bars = c.scenes.map((sc, i) => {
    const sel = sc.id === selSceneId;
    return `<div class="pv-ph-bar${sel ? ' pv-ph-sel' : ''}" style="flex:${sc.duration}"
      title="${esc(sc.id)} · ${sc.duration.toFixed(1)}s${sel ? '（当前选中）' : ''}">${i + 1}</div>`;
  }).join('');
  const pairHint = SEL.selection && SEL.selection.type === 'scene_pair'
    ? `<div class="pv-ph-pair">镜头关系：${esc(SEL.selection.id.split('->')[0])} → ${esc(SEL.selection.id.split('->')[1])}</div>` : '';
  stage.innerHTML = `<div class="pv-placeholder">${bars}${pairHint}</div>`;
}

/** 叠加层：PiP 框 / 蒙版手柄（mask-editor 已有 overlay 逻辑则不重复挂）。 */
function _previewApplyOverlay(){
  // mask-editor 的 overlay 自管理（MASK_ED.open 时接管 Preview），此处不覆盖
  if (typeof MASK_ED !== 'undefined' && MASK_ED.open) return;
  const old = document.getElementById('pv-overlay');
  if (old) old.remove();
  const stage = document.getElementById('ed-preview-stage');
  if (!stage || !SEL.selection) return;
  if (SEL.selection.type !== 'layer') return;
  const ov = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  ov.id = 'pv-overlay';
  ov.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;z-index:4;pointer-events:none';
  ov.setAttribute('preserveAspectRatio', 'none');
  const ctx = getSelectionContext();
  const lay = ED.comp && ctx.sceneId
    ? (() => { const sc = ED.comp.scenes.find(s => s.id === ctx.sceneId); return sc && sc.layers[ctx.layerIndex]; })()
    : null;
  if (!lay) return;
  const pip = ((lay.motion_instance || {}).parameters || {}).pip;
  const tf = lay.transform || {};
  if (pip && tf.x !== undefined) {
    // PiP 边界框（§19）：按 transform 归一坐标画框
    const cx = (tf.x || 0.5) * 100, cy = (tf.y || 0.5) * 100;
    const w = ((tf.scale || 0.35) * 100) / 2;
    ov.innerHTML = `<rect x="${cx - w}%" y="${cy - w * 1.4}%" width="${w * 2}%" height="${w * 2.8}%"
      fill="none" stroke="var(--accent)" stroke-width="2" stroke-dasharray="6 3"/>
      <text x="${cx - w}%" y="${cy - w * 1.4 - 2}%" style="fill:var(--accent);font-size:11px">画中画</text>`;
  } else if (lay.mask) {
    ov.innerHTML = `<rect x="4%" y="4%" width="92%" height="92%"
      fill="none" stroke="var(--accent)" stroke-width="2" stroke-dasharray="4 4"/>
      <text x="4%" y="9%" style="fill:var(--accent);font-size:11px">蒙版：${maskShapeCn(lay.mask.shape)}（点检视器「编辑路径」精调）</text>`;
  } else {
    return;  // 普通层不画框（避免视觉噪音）
  }
  stage.style.position = stage.style.position || 'relative';
  stage.appendChild(ov);
}

/** Scene Pair 模式提示（§19：A → B 关系提示）。 */
function previewShowScenePair(fromId, toId){
  PV.pairMode = {from: fromId, to: toId};
  const stage = document.getElementById('ed-preview-stage');
  if (stage && !stage.querySelector('video')) previewRenderPlaceholder();
  const meta = document.getElementById('ed-preview-meta');
  if (meta) meta.textContent = `镜头关系：${fromId} → ${toId}`;
}

Object.assign(window, {
  previewLoadDraft, previewShowVideo, previewPlay, previewPause, previewSeek,
  previewShowSelectionOverlay, previewRenderPlaceholder, previewShowScenePair,
  PV,
});
