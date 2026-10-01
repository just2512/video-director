// §14/§15/§16/§17 Timeline（方案《FRONTEND_DEEP_UNIFICATION》）：
//   §14 Scene/Layer 独立 hitbox：Scene 头部条 + 层窄带，点击空白=Scene，
//      点层=Layer——不再靠 stopPropagation 修补。
//   §15 旁白轨 = 只读信息轨（cursor:default + 弱化 + 标头「只读」）。
//   §16 四轨道组（画面/语言/声音/时间关系），默认画面+语言展开、声音+时间收起。
//   §17 直接操作：拖播放头 / 拖动层 / 边缘 Trim / 键盘（Space/Delete/Undo/Redo/Esc）。
//      拖动中 Local Preview，释放才 Command → Composition → Revision。

// 选择经 Selection Service（唯一事实源），兼容旧函数名（inline onclick 用）
function edSelect(id, type, ev){ select(type, id, ev, 'timeline'); }
function editorSelectScene(id, ev){ selectScene(id, ev, 'timeline'); }
function editorSelectLayer(sceneId, idx, ev){ selectLayer(sceneId, idx, ev, 'timeline'); }
function edIsSelected(type, id){ return SEL.selectionSet.has(_selKey(type, id)); }
function edClearSelection(){ clearSelection('timeline'); }
function edSelectedCount(){ return SEL.selectionSet.size; }

// ---------------------------------------------------------------------------
// 项目装载（editorInit/editorLoad——工作台入口）
// ---------------------------------------------------------------------------
async function editorInit(){
  // 项目下拉：只显示 editable=true（§36 legacy 不进编辑器——Index 过滤）
  const sel = document.getElementById('editor-project');
  if (!sel) return;
  const r = await apiGet('/api/projects?filter=all&limit=500');
  const projs = (r && !r.error && r.projects) || [];
  const editable = projs.filter(p => p.editable)
    .map(p => [p.project_id, p.name || p.project_id]);
  const prev = sel.value;
  sel.innerHTML = editable.length
    ? editable.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('')
    : '<option value="">（还没有可编辑项目——去「项目」页新建）</option>';
  // 保持当前选择（重进 tab 不丢已打开项目）
  if (prev) {
    for (const opt of sel.options) if (opt.value === prev) { sel.value = prev; break; }
  }
  const empty = document.getElementById('editor-empty');
  const body = document.getElementById('editor-body');
  if (empty && body) {
    empty.style.display = (!ED.pid && !editable.length) ? '' : 'none';
  }
}

async function editorLoad(){
  const pid = document.getElementById('editor-project').value;
  if (!pid) { editorToast('请先选择项目', true); return {ok: false}; }
  const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(pid));
  if (!comp.scenes) {
    editorToast('项目无可编辑时间线：' + (comp.error || '未知'), true);
    return {ok: false, error: comp.error};
  }
  ED.pid = pid; ED.comp = comp; ED.version = comp.version;
  clearSelection('load');
  document.getElementById('editor-body').style.display = '';
  const empty = document.getElementById('editor-empty');
  if (empty) empty.style.display = 'none';
  const metaEl = document.getElementById('ed-preview-meta');
  if (metaEl) metaEl.textContent =
    `v${comp.version} · ${comp.scenes.length} 场景 · ${comp.duration.toFixed(1)}s`;
  editorRenderTimeline();
  editorLoadVersions();
  populateGenModels();
  refreshGenerationAvailability();
  directorLoadCapCount();
  renderWatermarkList();
  editorTopbarRefresh();
  previewLoadDraft().then(() => previewShowSelectionOverlay());
  if (!ED_LIB.domain) edLibDomain('content', 'scenes'); else edLibLoad();
  edVoiceLoadOptions();
  if (typeof directorRefreshContext === 'function') directorRefreshContext();
  return {ok: true};
}

// ---------------------------------------------------------------------------
// §16 轨道组状态（画面/语言展开，声音/时间关系收起）
// ---------------------------------------------------------------------------
window.TL_GROUPS = {
  picture:   {label: '画面', open: true,  tracks: []},
  language:  {label: '语言', open: true,  tracks: []},
  sound:     {label: '声音', open: false, tracks: []},
  temporal:  {label: '时间关系', open: false, tracks: []},
};
function tlToggleGroup(g){
  TL_GROUPS[g].open = !TL_GROUPS[g].open;
  editorRenderTimeline();
}

function timelineView(view){
  ED.timelineView = view;
  ['time','semantic','dual'].forEach(v => {
    const b = document.getElementById('tl-view-'+v);
    if (b) b.classList.toggle('tl-switch-on', v === view);
  });
  editorRenderTimeline();
}

// §37 语义角色
function semanticRoleOf(sc){
  const rid = sc.origin_recipe_id || '';
  const map = {
    'shot.title-card-minimal': 'Hook', 'shot.spotlight-opening': 'Hook',
    'shot.cta-closing': 'CTA', 'shot.soft-transition-breathe': 'Transition',
    'shot.person-interview-tight': 'Evidence', 'shot.data-number-burst': 'Proof',
    'shot.product-hero-assemble': 'Problem', 'shot.screen-demo-flow': 'Process',
    'shot.editorial-photo-story': 'Context', 'shot.card-stack-reveal': 'Turn',
  };
  if (map[rid]) return map[rid];
  const arc = ['Hook','Context','Problem','Evidence','Turn','Resolution','CTA'];
  return arc[0];
}

function renderSemanticTimeline(){
  const c = ED.comp;
  let html = '<div class="ed-semantic-list">';
  c.scenes.forEach((sc) => {
    const role = semanticRoleOf(sc);
    const selCls = edIsSelected('scene', sc.id) ? ' ed-sel' : '';
    html += `<div class="ed-semantic-row${selCls}" onclick="editorSelectScene('${sc.id}', event)">
      <div class="ed-semantic-role" title="${esc(role)}">${esc(role)}</div>
      <div class="ed-semantic-body">
        <div class="ed-semantic-text">${esc(sc.text || sc.id)}</div>
        <div class="hint" style="font-size:11px">${sc.duration.toFixed(1)}s · ${sc.origin_recipe_id ? esc(shotCnName(sc.origin_recipe_id)) : '自由镜头'} · ${sc.layers.length} 层</div>
      </div>
    </div>`;
  });
  return html + '</div>';
}

// ---------------------------------------------------------------------------
// §17 播放头（可拖）
// ---------------------------------------------------------------------------
window.PLAYHEAD = {at: 0, dragging: false};
function playheadSet(pct, ev){
  const total = ED.comp ? ED.comp.duration : 1;
  PLAYHEAD.at = Math.max(0, Math.min(1, pct)) * total;
  const ph = document.getElementById('tl-playhead');
  if (ph) ph.style.left = (Math.max(0, Math.min(1, pct)) * 100) + '%';
  const label = document.getElementById('tl-playhead-label');
  if (label) label.textContent = PLAYHEAD.at.toFixed(1) + 's';
  if (ev && ev.type === 'pointerup') previewSeek(PLAYHEAD.at);
}
function _tlPct(ev, el){
  const r = el.getBoundingClientRect();
  return Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
}

// ---------------------------------------------------------------------------
// §14 物理时间线渲染（四组结构）
// ---------------------------------------------------------------------------
function editorRenderTimeline(){
  const box = document.getElementById('editor-timeline');
  const view = ED.timelineView || 'time';
  if (view === 'semantic') { box.innerHTML = renderSemanticTimeline(); return; }
  if (view === 'dual') {
    box.innerHTML = renderSemanticTimeline() + '<div class="hint" style="margin:6px 0">物理时间线</div>' + renderPhysicalTimeline();
    return;
  }
  box.innerHTML = renderPhysicalTimeline();
  _tlBindPlayheadDrag();
  _tlBindLayerDrag();
}

function renderPhysicalTimeline(){
  const c = ED.comp;
  const total = c.duration || 1;
  const zoom = ED.zoom || 1.0;
  const audioOf = ty => (c.audio || []).filter(a => a.type === ty || (ty === 'ambience' && a.type === 'ambient'));

  // §16 轨道归类：画面（主视觉/B-roll/图形/水印）/ 语言（旁白/对白/字幕）/
  // 声音（原声/BGM/SFX）/ 时间关系（关键帧）
  const tracks = {
    picture: [], language: [], sound: [], temporal: [],
  };
  // 轨 1 旁白（§15 只读信息轨）
  const narration = (c.scenes || []).map(sc =>
    ({pct: sc.duration / total * 100, title: sc.text, text: (sc.text || '').slice(0, 10)}));
  tracks.language.push({id: 'narration', label: '旁白 · 只读', readOnly: true,
    clips: narration.map(n => ({pct: n.pct, text: n.text, title: n.title}))});
  const dialogue = audioOf('dialogue');
  tracks.language.push({id: 'dialogue', label: '对白', readOnly: true,
    clips: dialogue.length ? [{pct: 100, text: '对白', title: '对白轨'}] : []});
  const hasSub = !!(c.subtitle_track && c.subtitle_track.cues && c.subtitle_track.cues.length);
  tracks.language.push({id: 'subtitle', label: '字幕', readOnly: false,
    clips: hasSub ? [{pct: 100,
      text: `${c.subtitle_track.mode === 'word' ? '逐词' : '句子'} ${c.subtitle_track.cues.length} 条`,
      title: '字幕轨（样式在右侧「声音与字幕」面板）',
      onclick: "document.getElementById('editor-audio-card').scrollIntoView({behavior:'smooth'})"}] : []});
  // 画面组：主视觉（场景块 §14 hitbox 分离）
  tracks.picture.push({id: 'main', label: '主视觉', isSceneTrack: true});
  const brollLayers = [], graphicLayers = [];
  c.scenes.forEach(sc => (sc.layers || []).forEach(lay => {
    if (lay.type === 'broll' || (lay.role === 'broll')) brollLayers.push([sc, lay]);
    else if (lay.type === 'graphic') graphicLayers.push([sc, lay]);
  }));
  tracks.picture.push({id: 'broll', label: 'B-roll',
    clips: brollLayers.map(([sc, lay]) => ({
      pct: lay.duration / total * 100, text: 'B-roll', title: 'B-roll 层',
      onclick: `editorSelectLayer('${sc.id}',${sc.layers.indexOf(lay)}, event)`}))});
  tracks.picture.push({id: 'graphic', label: '图形',
    clips: graphicLayers.map(([sc, lay]) => ({
      pct: lay.duration / total * 100, text: '图形', title: '图形层',
      onclick: `editorSelectLayer('${sc.id}',${sc.layers.indexOf(lay)}, event)`}))});
  const wmCount = (c.watermarks || []).length;
  tracks.picture.push({id: 'watermark', label: '水印',
    clips: wmCount ? [{pct: 100, text: '水印 ×' + wmCount, title: '项目级水印',
      onclick: "document.getElementById('editor-audio-card').scrollIntoView({behavior:'smooth'})"}] : []});
  // 声音组
  const original = audioOf('original');
  tracks.sound.push({id: 'original', label: '原声',
    clips: original.length ? [{pct: 100,
      text: `原声${original[0].volume !== undefined ? ' · ' + original[0].volume.toFixed(2) : ''}`,
      title: '素材原声轨', onclick: "editorAudioVolume('original')"}] : []});
  const bgms = audioOf('bgm');
  tracks.sound.push({id: 'bgm', label: '背景音乐',
    clips: bgms.length ? [{pct: 100, text: '背景音乐', title: '背景音乐轨',
      onclick: "editorBgmVolume()"}] : []});
  const sfxs = audioOf('sfx');
  tracks.sound.push({id: 'sfx', label: '音效',
    clips: sfxs.length ? [{pct: 100, text: '音效', title: '音效轨',
      onclick: "editorAudioVolume('sfx')"}] : []});
  // 时间关系组：关键帧
  const kfPoints = [];
  c.scenes.forEach(sc => (sc.layers || []).forEach((lay, li) => {
    (lay.keyframes || []).forEach(t => t.keyframes.forEach(k => {
      const absAt = sc.start + (lay.start || 0) + k.at;
      kfPoints.push({leftPct: Math.min(99.5, Math.max(0, absAt / total * 100)),
        title: `${sc.id}#${li} ${t.property} @${k.at}s`,
        onclick: `editorSelectLayer('${sc.id}',${li}, event)`});
    }));
  }));
  tracks.temporal.push({id: 'keyframe', label: '关键帧',
    clips: [], kfPoints});

  // ---- 渲染（组标题可折叠 §16）----
  const GROUP_ORDER = ['picture', 'language', 'sound', 'temporal'];
  let html = `<div class="ed-tl-ruler"><span id="tl-playhead-label">${PLAYHEAD.at.toFixed(1)}s</span></div>
    <div class="ed-tl-scroll">`;
  const trackRow = (t) => {
    const bodyInner = t.isSceneTrack
      ? _sceneTrackHtml(c, total)
      : (t.clips || []).map(cl =>
          `<div class="ed-track-clip${t.readOnly ? ' tl-readonly' : ''}" style="width:${cl.pct}%"${cl.title ? ` title="${esc(cl.title)}"` : ''}${cl.onclick ? ` onclick="${cl.onclick}"` : ''}>${esc(cl.text)}</div>`).join('');
    const kfHtml = (t.kfPoints || []).map(k =>
      `<span class="ed-kf-dot" style="left:${k.leftPct}%" title="${esc(k.title)}" onclick="${k.onclick}">◆</span>`).join('');
    const empty = (!t.clips || !t.clips.length) && !t.isSceneTrack && !(t.kfPoints || []).length;
    return `<div class="ed-track"><div class="ed-track-label">${esc(t.label)}</div>`+
      `<div class="ed-track-body" style="transform:scaleX(${zoom});transform-origin:left">${bodyInner}${kfHtml}${empty ? '<span class="hint tl-empty-hint">无</span>' : ''}</div></div>`;
  };
  for (const g of GROUP_ORDER) {
    const G = TL_GROUPS[g];
    const tCount = G.tracks.length;
    if (!G.open) {
      // 收起态：只画组头 + 一条摘要轨道
      const summary = g === 'picture' ? '主视觉 / B-roll / 图形 / 水印'
        : g === 'language' ? '旁白 / 对白 / 字幕'
        : g === 'sound' ? '原声 / 背景音乐 / 音效' : '关键帧';
      html += `<div class="ed-track-group ed-group-closed" onclick="tlToggleGroup('${g}')">
        <span class="ed-group-caret">▸</span> ${G.label} <span class="hint">${summary}</span></div>`;
    } else {
      html += `<div class="ed-track-group" onclick="tlToggleGroup('${g}')">
        <span class="ed-group-caret">▾</span> ${G.label}</div>`;
      for (const t of tracks[g] || []) html += trackRow(t);
    }
  }
  // 播放头（§17 可拖）
  html += `<div class="tl-playhead" id="tl-playhead" style="left:${PLAYHEAD.at / total * 100}%" title="拖动定位预览"></div>`;
  html += '</div>';
  const cnt = document.getElementById('editor-selection-count');
  if (cnt) cnt.textContent = SEL.selectionSet.size ? `已选 ${SEL.selectionSet.size} 项` : '未选中';
  return html;
}

/** §14 场景轨：Scene 头部条（可点=选场景）+ 层窄带（点=选层）。空白区域点击=Scene。 */
function _sceneTrackHtml(c, total){
  let mInner = '';
  c.scenes.forEach((sc, si) => {
    const wPct = (sc.duration / total * 100).toFixed(2);
    const selCls = edIsSelected('scene', sc.id) ? ' ed-sel' : '';
    mInner += `<div class="ed-scene${selCls}" data-scene="${sc.id}" style="width:${wPct}%">`+
      `<div class="ed-scene-header" onclick="editorSelectScene('${sc.id}', event)">`+
      `<span class="ed-scene-num">${si + 1}</span>`+
      `<span class="ed-scene-title">${esc((sc.text || '').slice(0, 12))}</span>`+
      `<span class="ed-scene-dur">${sc.duration.toFixed(1)}s</span></div>`+
      `<div class="ed-scene-body" onclick="editorSelectScene('${sc.id}', event)">`;
    sc.layers.forEach((lay, li) => {
      if (lay.type === 'broll' || lay.role === 'broll' || lay.type === 'graphic') return; // 分轨展示
      const lsel = edIsSelected('layer', `${sc.id}#${li}`) ? ' ed-sel' : '';
      const lock = lay.locked ? '<span title="已锁定">锁</span>' : '';
      const mtag = lay.motion_recipe_id ? `<span class="ed-motion-tag">${esc(motionCnName(lay.motion_recipe_id))}</span>` : '';
      // §14 层窄带：data 属性驱动拖动（不用 stopPropagation——hitbox 物理分离）
      mInner += `<div class="ed-layer${lsel}" data-scene="${sc.id}" data-idx="${li}" data-dur="${lay.duration}"
        onclick="editorSelectLayer('${sc.id}',${li}, event)">${lock}${layerTypeCn(lay.type)}${mtag}</div>`;
    });
    mInner += '</div></div>';
  });
  return mInner;
}

// 播放头拖动（§17）：只在手柄（::after 头部）上按住才拖；线体 pointer-events:none
// 不再拦截场景/层的点击 hitbox（§14 修复的伴随缺陷）。
function _tlBindPlayheadDrag(){
  const scroll = document.querySelector('#editor-timeline .ed-tl-scroll');
  if (!scroll) return;
  const handle = document.getElementById('tl-playhead');
  if (handle) {
    handle.addEventListener('pointerdown', ev => {
      PLAYHEAD.dragging = true;
      ev.preventDefault();  // 手柄按下不触发下层场景选择
      playheadSet(_tlPct(ev, scroll), null);
    });
  }
  scroll.addEventListener('pointerdown', ev => {
    // 轨道空白区按下（非层/非场景头）→ 直接定位播放头（§17 拖播放头）
    if (ev.target.closest('.ed-layer') || ev.target.closest('.ed-scene-header')
        || ev.target.closest('.ed-track-clip') || ev.target.closest('.ed-kf-dot')) return;
    if (ev.target.closest('.ed-scene') && !ev.target.closest('.ed-scene-body')) return;
    PLAYHEAD.dragging = true;
    playheadSet(_tlPct(ev, scroll), null);
  });
  scroll.addEventListener('pointermove', ev => {
    if (PLAYHEAD.dragging) playheadSet(_tlPct(ev, scroll), null);
  });
  const up = ev => {
    if (PLAYHEAD.dragging) {
      PLAYHEAD.dragging = false;
      playheadSet(_tlPct(ev, scroll), {type: 'pointerup'});
    }
  };
  document.addEventListener('pointerup', up);
}

/** §17 层拖动重排（Local 预览序号提示，释放 → move Command → Revision）。 */
let _layerDrag = null;
function _tlBindLayerDrag(){
  document.querySelectorAll('#editor-timeline .ed-layer').forEach(el => {
    el.addEventListener('pointerdown', ev => {
      // 层内按下只记录拖动候选；移动超过阈值才算拖（避免误伤点击选择）
      _layerDrag = {el, sceneId: el.dataset.scene, idx: parseInt(el.dataset.idx),
                    startX: ev.clientX, moved: false};
    });
  });
  document.addEventListener('pointermove', ev => {
    if (!_layerDrag || !_layerDrag.el.isConnected) { _layerDrag = null; return; }
    if (Math.abs(ev.clientX - _layerDrag.startX) > 8) {
      _layerDrag.moved = true;
      _layerDrag.el.classList.add('tl-dragging');
    }
  });
  document.addEventListener('pointerup', ev => {
    if (!_layerDrag) return;
    const d = _layerDrag;
    _layerDrag = null;
    if (!d.moved) return;  // 纯点击走 onclick
    d.el.classList.remove('tl-dragging');
    // 找到松手位置落点的场景序号（横向命中）
    const scenes = [...document.querySelectorAll('#editor-timeline .ed-scene')];
    const target = scenes.find(scEl => {
      const r = scEl.getBoundingClientRect();
      return ev.clientX >= r.left && ev.clientX <= r.right;
    });
    if (!target || target.dataset.scene === d.sceneId) return;
    const toIdx = ED.comp.scenes.findIndex(s => s.id === target.dataset.scene);
    if (toIdx < 0) return;
    const fromIdx = ED.comp.scenes.findIndex(s => s.id === d.sceneId);
    editorToast(`正在把场景 ${d.sceneId} 移到第 ${toIdx + 1} 位……`);
    editorCmd('move', {toIndex: toIdx}, d.sceneId, true);
  });
}

// ---------------------------------------------------------------------------
// §17 键盘（Space 播放/暂停、Delete 删除、Ctrl+Z 撤销、Ctrl+Shift+Z 重做、Esc 取消）
// ---------------------------------------------------------------------------
document.addEventListener('keydown', ev => {
  const tag = (ev.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
  if (!ED.pid || document.getElementById('pane-editor').style.display === 'none') return;
  const mod = ev.ctrlKey || ev.metaKey;
  if (ev.code === 'Space') { ev.preventDefault(); previewPlay(); return; }
  if (ev.key === 'Escape') { clearSelection('keyboard'); return; }
  if (ev.key === 'Delete' || ev.key === 'Backspace') {
    ev.preventDefault();
    if (SEL.selection) editorCmd('delete');
    return;
  }
  if (mod && !ev.shiftKey && (ev.key === 'z' || ev.key === 'Z')) { ev.preventDefault(); editorUndo(); return; }
  if (mod && ev.shiftKey && (ev.key === 'z' || ev.key === 'Z')) { ev.preventDefault(); editorRedo(); return; }
  if (mod && (ev.key === 'k' || ev.key === 'K')) {
    ev.preventDefault();
    const inp = document.getElementById('director-input');
    if (inp) inp.focus();
  }
});

// ---------------------------------------------------------------------------
// §46 顶栏 Undo/Redo/Save/Preview + §43 版本抽屉 + §44 项目抽屉
// ---------------------------------------------------------------------------
async function editorUndo(){
  if (!ED.pid) { editorToast('先打开项目'); return; }
  const r = await apiPost('/api/editor/command', {project_id: ED.pid, command: 'undo'});
  if (r.ok) {
    await refreshComposition();
    editorToast('已撤销上一步' + (DEV.mode && r.revision_id ? `（${r.revision_id}）` : ''));
  } else {
    const err = (r.errors && r.errors[0]) || {};
    editorToast(`撤销失败：${err.message || r.error || '没有可撤销的编辑'}`, true);
  }
}
async function editorRedo(){
  if (!ED.pid) { editorToast('先打开项目'); return; }
  const r = await apiPost('/api/editor/command', {project_id: ED.pid, command: 'redo'});
  if (r.ok) {
    await refreshComposition();
    editorToast('已恢复' + (DEV.mode && r.revision_id ? `（${r.revision_id}）` : ''));
  } else {
    const err = (r.errors && r.errors[0]) || {};
    editorToast(`恢复失败：${err.message || r.error || '没有可恢复的编辑'}`, true);
  }
}
async function editorSaveBar(){
  if (!ED.pid) { editorToast('先打开项目'); return; }
  const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid));
  if (comp && comp.version) {
    ED.comp = comp; ED.version = comp.version; editorTopbarRefresh();
    editorToast('已保存（项目会实时保存，此处为确认）');
  } else editorToast('保存确认失败', true);
}
async function editorPreviewBar(){
  if (!ED.pid) { editorToast('先打开项目'); return; }
  const info = await previewLoadDraft();
  previewShowVideo(info && info.draft_path, info && info.draft_path
    ? '草稿 · ' + info.draft_path.split(/[\\/]/).pop() : '');
  if (!info || !info.draft_path) {
    const stage = document.getElementById('ed-preview-stage');
    if (stage) stage.innerHTML = '<div>尚无草稿（时间线「渲染」生成后可预览）</div>';
    editorToast('尚无草稿可预览');
  }
}
function editorTopbarRefresh(){
  const tp = document.getElementById('tb-project');
  const tv = document.getElementById('tb-version');
  // §44 顶栏显示项目人话名（Project Index 同源）
  if (tp && ED.pid) {
    const r = (window.PM && PM.index.find(p => p.project_id === ED.pid)) || null;
    const name = r ? (r.name || ED.pid) : ED.pid;
    tp.textContent = name + ' ▾';
    tp.title = '项目操作（重命名/复制/归档/删除/版本历史）';
  }
  if (tv && ED.comp && ED.comp.version) tv.textContent = 'V' + ED.comp.version;
}

/** §43 版本历史抽屉（不再平铺占据工作区）。 */
function versionDrawerToggle(){
  const box = document.getElementById('version-drawer');
  if (!box) return;
  if (box.style.display === '' && box.innerHTML) { box.style.display = 'none'; return; }
  box.style.display = '';
  editorLoadVersions();
}

/** §44 项目操作抽屉（顶栏项目名 → 重命名/复制/归档/删除/版本历史）。 */
async function projectDrawerToggle(){
  const box = document.getElementById('project-drawer');
  if (!box || !ED.pid) return;
  if (box.style.display === '' && box.innerHTML) { box.style.display = 'none'; return; }
  box.style.display = '';
  box.innerHTML = '<div class="hint">加载项目操作……</div>';
  try {
    const d = await apiGet('/api/project_detail?project_id=' + encodeURIComponent(ED.pid));
    const name = (d && d.name) || ED.pid;
    box.innerHTML = `
      <div class="drawer-inline">
        <b>${esc(name)}</b> <span class="hint">${d && d.status ? esc(pmStatusCn(d)) : ''}${d && d.scene_count ? ' · ' + d.scene_count + ' 场景' : ''}</span>
        <div class="btn-row" style="flex-wrap:wrap">
          <button class="btn-secondary btn-sm" onclick="pmAskRename('${esc(ED.pid)}')">重命名</button>
          <button class="btn-secondary btn-sm" onclick="pmClone('${esc(ED.pid)}')">复制</button>
          <button class="btn-secondary btn-sm" onclick="pmArchive('${esc(ED.pid)}')">归档</button>
          <button class="btn-secondary btn-sm pm-act-danger" onclick="pmAskDelete('${esc(ED.pid)}')">删除项目</button>
          <button class="btn-secondary btn-sm" onclick="versionDrawerToggle()">版本历史</button>
        </div>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="err-box">项目信息读取失败：${esc(e.message || e)}</div>`;
  }
}

// 版本历史渲染进 §43 抽屉
async function editorLoadVersions(){
  if (!ED.pid) { editorToast('先打开项目'); return; }
  const revs = await apiGet('/api/editor/revisions?project_id=' + encodeURIComponent(ED.pid));
  const box = document.getElementById('version-drawer');
  if (!box) return;
  if (!Array.isArray(revs) || !revs.length) {
    box.innerHTML = '<div class="hint">尚无编辑版本（每次修改自动记为一版，可回退）。</div>';
    return;
  }
  box.innerHTML = `<div class="drawer-inline"><b>版本历史</b>
    <div class="scene-list" style="max-height:240px">
    ${revs.slice(-20).reverse().map(rv => {
      const when = new Date((rv.created_at || 0) * 1000).toLocaleString();
      return `<div class="proj-row"><b>v${rv.base_version + 1}</b>`+
        `<span class="hint">${esc(rv.command)} → ${esc(rv.target_id || '')}</span>`+
        `<span class="hint">${when}</span></div>`;
    }).join('')}
    </div>
    <div class="btn-row"><button class="btn-secondary btn-sm" onclick="editorUndo()">撤销最新一版</button></div>
  </div>`;
}

// §8 拆分导出：inline onclick/window 引用兼容
Object.assign(window, {
  edSelect, editorSelectScene, editorSelectLayer, edIsSelected,
  edClearSelection, edSelectedCount,
  editorInit, editorLoad, timelineView, semanticRoleOf, renderSemanticTimeline,
  editorRenderTimeline, tlToggleGroup,
  editorUndo, editorRedo, editorSaveBar, editorPreviewBar, editorTopbarRefresh,
  versionDrawerToggle, projectDrawerToggle, editorLoadVersions,
  PLAYHEAD, TL_GROUPS,
});
