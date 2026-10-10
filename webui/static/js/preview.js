// §64 Preview Service（方案《FRONTEND_DEEP_UNIFICATION》§19/§64）：
// 中央 Preview Stage 是唯一视觉事实。所有选择/修改都反馈到这里：
//   选择场景 → 高亮；选 Layer → 边界框；画中画 → PiP 框；蒙版编辑 → 顶点手柄；
//   Scene Pair → A→B 关系提示。禁止各模块自建第二个 <video>/iframe。
//
// 职责：loadDraft / showSelectionOverlay / showMaskOverlay / showScenePair /
//       play / pause / seek

window.PV = {draftPath: '', finalPath: '', playhead: 0, pairMode: null, mode: 'live', playing: false, timer: null, activeSceneId: '', activeLayerId: ''};

/** One playhead clock shared by the timeline, live preview, and rendered-draft preview. */
function previewSyncTimelinePlayhead(sec){
  if (!ED.comp) return;
  const total = Math.max(0.001, Number(ED.comp.duration) || 1);
  const at = Math.max(0, Math.min(total, Number(sec) || 0));
  PV.playhead = at;
  if (typeof PLAYHEAD !== 'undefined') PLAYHEAD.at = at;
  const ph = document.getElementById('tl-playhead');
  if (ph) ph.style.left = (at / total * 100) + '%';
  const label = document.getElementById('tl-playhead-label');
  if (label) label.textContent = at.toFixed(2) + 's';
}

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
  // 草稿成片预览：切 draft 模式（播放/暂停/seek 走 <video> 原生通道；
  // 不设 mode 时渲染完成载入草稿后点 ▶ 会被 live 分支顶掉——方案 §五
  // Preview 与 Draft Render 分离的破坏）。
  PV.mode = 'draft';
  if (PV.timer) { clearInterval(PV.timer); PV.timer = null; }
  PV.playing = false;
  stage.innerHTML = `<video controls src="/file?path=${encodeURIComponent(path)}"></video>`;
  const draftVideo = stage.querySelector('video');
  if (draftVideo) draftVideo.addEventListener('timeupdate', () => {
    previewSyncTimelinePlayhead(draftVideo.currentTime);
    if (meta) meta.textContent = '草稿预览 · ' + draftVideo.currentTime.toFixed(2) + ' / ' +
      (ED.comp && ED.comp.duration || draftVideo.duration || 0).toFixed(2) + 's';
  });
  if (meta) meta.textContent = label || ('草稿 · ' + path.split(/[\\/]/).pop());
  _previewApplyOverlay();
}

/** 播放/暂停/seek（§64）。 */
function previewPlay(){
  const v = document.querySelector('#ed-preview-stage video');
  if (PV.mode === 'draft' && v) { v.play().catch(()=>{}); return; }
  if (!ED.comp) return;
  PV.mode = 'live';
  PV.playing = true;
  if (PV.timer) clearInterval(PV.timer);
  let last = performance.now();
  PV.timer = setInterval(() => {
    if (!PV.playing) return;
    const now = performance.now();
    const dt = Math.min(0.2, Math.max(0, (now - last) / 1000));
    last = now;
    const total = ED.comp.duration || 0;
    PV.playhead += dt;
    if (PV.playhead >= total) {
      PV.playhead = total;
      previewPause();
    }
    _previewRenderAt(PV.playhead, true);
  }, 80);
  _previewRenderAt(PV.playhead, true);
}
function previewPause(){
  const v = document.querySelector('#ed-preview-stage video');
  if (v && PV.mode === 'draft') v.pause();
  PV.playing = false;
  if (PV.timer) { clearInterval(PV.timer); PV.timer = null; }
}
function previewSeek(sec){
  if (!ED.comp || sec < 0) return;
  previewSyncTimelinePlayhead(Math.min(sec, ED.comp.duration || sec));
  if (PV.mode === 'draft') {
    const v = document.querySelector('#ed-preview-stage video');
    if (v) v.currentTime = PV.playhead;
    return;
  }
  _previewRenderAt(PV.playhead, false);
}

/** Step one output frame in the same preview/playhead clock used by the timeline. */
function previewStepFrame(direction){
  if (!ED.comp) return;
  const draftVideo = PV.mode === 'draft' ? document.querySelector('#ed-preview-stage video') : null;
  const baseTime = draftVideo ? Number(draftVideo.currentTime) || 0 : Number(PV.playhead) || 0;
  previewPause();
  const fps = Math.max(1, Number((ED.comp.project || {}).fps) || 30);
  const lastFrame = Math.max(0, Math.round((Number(ED.comp.duration) || 0) * fps));
  const currentFrame = Math.round(baseTime * fps);
  const frame = Math.max(0, Math.min(lastFrame, currentFrame + (Number(direction) < 0 ? -1 : 1)));
  previewSyncTimelinePlayhead(frame / fps);
  if (PV.mode === 'draft') {
    const v = document.querySelector('#ed-preview-stage video');
    if (v) v.currentTime = PV.playhead;
    const meta = document.getElementById('ed-preview-meta');
    if (meta) meta.textContent = '草稿预览 · ' + PV.playhead.toFixed(2) + ' / ' + (ED.comp.duration || 0).toFixed(2) + 's';
    return;
  }
  PV.mode = 'live';
  _previewRenderAt(PV.playhead, false);
}

/** 选择高亮 Overlay：Selection → Preview 边界框（§19）。
 *  无视频时给出占位画布（场景序号 + 时长条），让 Preview 永不空转。 */
function previewShowSelectionOverlay(){
  const stage = document.getElementById('ed-preview-stage');
  if (!stage) return;
  if (!stage.querySelector('video, img, .pv-live-media')) previewRenderComposition();
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


/** 实时 Composition 预览：编辑态直接播放 IR，不要求先生成 draft.mp4。 */
function previewRenderComposition(){
  if (!ED.comp) return;
  PV.mode = 'live';
  _previewRenderAt(PV.playhead || 0, false);
}
function _previewSceneAt(sec){
  const scenes = ED.comp && ED.comp.scenes || [];
  if (!scenes.length) return null;
  return scenes.find((sc, i) =>
    (sec >= sc.start && sec < sc.start + sc.duration) ||
    (i === scenes.length - 1 && sec >= sc.start)
  ) || scenes[0];
}
function _previewLayerAt(sc, local){
  const layers = (sc.layers || []).slice().sort((a,b)=>(a.start||0)-(b.start||0));
  return layers.find(l => local >= (l.start||0) && local < (l.start||0) + (l.duration||0))
      || layers.find(l => l.asset_id)
      || layers[0] || null;
}
function _previewAsset(aid){
  return (ED.comp && ED.comp.assets && ED.comp.assets[aid]) || null;
}
function _previewRenderAt(sec, keepPlaying){
  if (!ED.comp) return;
  previewSyncTimelinePlayhead(sec);
  const stage = document.getElementById('ed-preview-stage');
  const meta = document.getElementById('ed-preview-meta');
  if (!stage) return;
  const sc = _previewSceneAt(sec);
  if (!sc) return;
  const local = Math.max(0, Math.min(sc.duration, sec - sc.start));
  const lay = _previewLayerAt(sc, local);
  const asset = lay && _previewAsset(lay.asset_id);
  const key = (lay && lay.asset_id) || '';
  const changed = PV.activeSceneId !== sc.id || PV.activeLayerId !== key;
  if (changed) {
    PV.activeSceneId = sc.id;
    PV.activeLayerId = key;
    let media = '<div class="pv-live-empty">本镜暂无可预览素材</div>';
    if (asset && asset.file_url) {
      if (asset.type === 'video') {
        media = '<video id="pv-live-video" muted playsinline preload="metadata" src="' + asset.file_url + '"></video>';
      } else {
        media = '<img class="pv-live-image" src="' + asset.file_url + '" alt="">';
      }
    }
    const sourceBadge = asset && asset.source === 'user' ? ' · 用户素材' : '';
    const transition = sc.transition_out && sc.transition_out.type && sc.transition_out.type !== 'cut'
      ? '<span class="pv-live-transition">下一镜：' + esc(sc.transition_out.type) + '</span>' : '';
    stage.innerHTML =
      '<div class="pv-live-media">' + media + '</div>' +
      '<div class="pv-live-caption"><b>' + esc(sc.text || sc.id) + '</b>' +
      '<span class="hint">场景 ' + esc(String((ED.comp.scenes || []).indexOf(sc) + 1)) +
      ' · ' + sc.duration.toFixed(1) + 's</span>' + sourceBadge + transition + '</div>';
    const v = document.getElementById('pv-live-video');
    if (v) {
      v.addEventListener('loadedmetadata', () => _previewSyncVideo(v, sc, lay, local));
      if (PV.playing && keepPlaying) v.play().catch(()=>{});
    }
  }
  const v = document.getElementById('pv-live-video');
  if (v && lay && lay.type === 'video') _previewSyncVideo(v, sc, lay, local);
  if (meta) meta.textContent = '实时预览 · ' + sec.toFixed(1) + ' / ' + (ED.comp.duration || 0).toFixed(1) + 's';
}
function _previewSyncVideo(v, sc, lay, local){
  if (!v || !lay) return;
  const sourceIn = Number(lay.source_in || 0);
  const speed = Number(lay.speed || 1);
  const within = Math.max(0, local - Number(lay.start || 0));
  const t = sourceIn + within * speed;
  const fps = Math.max(1, Number((ED.comp && ED.comp.project || {}).fps) || 30);
  // Half-frame tolerance keeps normal playback stable while allowing one-frame stepping.
  if (Number.isFinite(t) && Math.abs((v.currentTime || 0) - t) > 0.5 / fps) v.currentTime = t;
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

Object.assign(window, { previewRenderComposition, previewPlay, previewPause, previewSeek, 
  previewLoadDraft, previewShowVideo, previewPlay, previewPause, previewSeek,
  previewSyncTimelinePlayhead, previewShowSelectionOverlay, previewRenderPlaceholder, previewShowScenePair,
  previewStepFrame, PV,
});
