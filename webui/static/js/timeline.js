function _selKey(type, id){ return type + ':' + id; }
function edSelect(id, type, ev){
  // 单选 / Ctrl 多选切换 / Shift 范围选择（scene）
  const wantMulti = !!(ev && (ev.ctrlKey || ev.metaKey));
  const wantRange = !!(ev && ev.shiftKey && type === 'scene');
  if (!wantMulti && !wantRange) { ED.selSet = new Set(); }
  if (wantRange) {
    // 范围：上次已选场景索引 → 当前索引之间全部纳入
    const ids = ED.comp.scenes.map(s => s.id);
    const from = ED._lastSceneIdx !== undefined ? ED._lastSceneIdx : ids.indexOf(id);
    const to = ids.indexOf(id);
    const [lo, hi] = from <= to ? [from, to] : [to, from];
    for (let i = lo; i <= hi; i++) ED.selSet.add(_selKey('scene', ids[i]));
    ED.sel = {type:'scene', id};
  } else if (wantMulti) {
    const k = _selKey(type, id);
    if (ED.selSet.has(k)) ED.selSet.delete(k); else ED.selSet.add(k);
    ED.sel = {type, id};
  } else {
    ED.selSet = new Set([_selKey(type, id)]);
    ED.sel = {type, id};
  }
  if (type === 'scene') ED._lastSceneIdx = ED.comp.scenes.findIndex(s => s.id === id);
  editorRenderTimeline();
  edShowInspector();
  const cnt = document.getElementById('editor-selection-count');
  if (cnt) cnt.textContent = ED.selSet.size ? `已选 ${ED.selSet.size} 项` : '未选中';
}
function edIsSelected(type, id){ return ED.selSet.has(_selKey(type, id)); }
function edClearSelection(){ ED.sel = null; ED.selSet = new Set(); editorRenderTimeline(); }
function edSelectedCount(){ return ED.selSet.size; }

async function editorInit(){
  // 项目下拉：全部有 composition.json 的项目（compose 过或模板实例）
  let projs;
  try { projs = await apiGet('/api/projects'); } catch(e) { projs = null; }
  const editable = [];
  if (Array.isArray(projs)) {
    for (const p of projs) {
      try {
        const c = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(p.project_id));
        if (c && c.scenes) editable.push([p.project_id, p.project_id]);
      } catch(e) {}
    }
  }
  _rebuildSelect('editor-project', editable.length ? editable : [['','（先去创作页或模板库生成一个项目）']]);
}

async function editorLoad(){
  const pid = document.getElementById('editor-project').value;
  if (!pid) { editorToast('请先选择项目', true); return; }
  const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(pid));
  if (!comp.scenes) { editorToast('项目无可编辑时间线：' + (comp.error || '未知')); return; }
  ED.pid = pid; ED.comp = comp; ED.version = comp.version; ED.sel = null;
  document.getElementById('editor-body').style.display = '';
  document.getElementById('editor-meta').textContent =
    ` v${comp.version} · ${comp.scenes.length} 场景 · ${comp.duration.toFixed(1)}s` +
    (comp.origin_template_id ? ` · 模板:${comp.origin_template_id}` : '');
  editorRenderTimeline();
  editorLoadVersions();
  populateGenModels();
  refreshGenerationAvailability();  // §31.1 生图/生视频按钮禁用态（先于点击，不 503）
  directorLoadCapCount();
  renderWatermarkList();
  // §46 顶栏 + Preview + §48 Library 默认入口
  editorTopbarRefresh();
  const pmeta = document.getElementById('ed-preview-meta');
  if (pmeta) pmeta.textContent = `v${comp.version} · ${comp.scenes.length} 场景 · ${comp.duration.toFixed(1)}s（点顶栏 ▶ Preview 加载草稿）`;
  if (!ED_LIB.nav) edLibNav('scenes'); else edLibLoad();
  edVoiceLoadOptions();
}

function timelineView(view){
  ED.timelineView = view;
  ['time','semantic','dual'].forEach(v => {
    const b = document.getElementById('tl-view-'+v);
    if (b) b.classList.toggle('tl-switch-on', v === view);
  });
  editorRenderTimeline();
}

// §37 Semantic Timeline：recipe id → 语义角色（Hook/Problem/Evidence/... 七段弧）
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
  // 位置弧兜底（§37 七段）
  const arc = ['Hook','Context','Problem','Evidence','Turn','Resolution','CTA'];
  return arc[0]; // 无 recipe 时归 Hook
}

function renderSemanticTimeline(){
  // §37 语义视图：场景按语义角色 + 旁白 + 视觉意图纵向列出
  const c = ED.comp;
  const arc = ['Hook','Context','Problem','Evidence','Turn','Resolution','CTA'];
  const n = c.scenes.length;
  let html = '<div class="ed-semantic-list">';
  c.scenes.forEach((sc, si) => {
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
  html += '</div>';
  return html;
}

function editorRenderTimeline(){
  const box = document.getElementById('editor-timeline');
  const view = ED.timelineView || 'time';
  if (view === 'semantic') {
    box.innerHTML = renderSemanticTimeline();
    return;
  }
  if (view === 'dual') {
    box.innerHTML = renderSemanticTimeline() + '<div class="hint" style="margin-top:6px">⏱ 物理时间线</div>' + renderPhysicalTimeline();
    return;
  }
  box.innerHTML = renderPhysicalTimeline();
}

// ---- §46 顶栏：Undo / Redo / Save / Preview ----
async function editorUndo(){
  if (!ED.pid) { editorToast('先打开项目'); return; }
  const r = await apiPost('/api/editor/command', {project_id: ED.pid, command: 'undo'});
  if (r.ok) {
    const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid));
    ED.comp = comp; ED.version = comp.version;
    editorRenderTimeline(); edShowInspector(); renderWatermarkList(); editorTopbarRefresh();
    editorToast('↩ 已撤销上一步' + (DEV.mode && r.revision_id ? `（${r.revision_id}）` : ''));
  } else {
    const err = (r.errors && r.errors[0]) || {};
    editorToast(`❌ Undo：${err.message || r.error || '不可用'}`, true);
  }
}
async function editorRedo(){
  if (!ED.pid) { editorToast('先打开项目'); return; }
  const r = await apiPost('/api/editor/command', {project_id: ED.pid, command: 'redo'});
  if (r.ok) {
    const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid));
    ED.comp = comp; ED.version = comp.version;
    editorRenderTimeline(); edShowInspector(); renderWatermarkList(); editorTopbarRefresh();
    editorToast('↪ 已恢复' + (DEV.mode && r.revision_id ? `（${r.revision_id}）` : ''));
  } else {
    const err = (r.errors && r.errors[0]) || {};
    editorToast(`❌ Redo：${err.message || r.error || '不可用'}`, true);
  }
}
async function editorSaveBar(){
  if (!ED.pid) { editorToast('先打开项目'); return; }
  // project.save：命令落盘已原子，此处确认 + 版本号上报（§46 Save 语义）
  const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid));
  if (comp && comp.version) {
    ED.comp = comp; ED.version = comp.version; editorTopbarRefresh();
    editorToast(`💾 已保存（composition v${comp.version}）`);
  } else editorToast('❌ 保存确认失败', true);
}
async function editorPreviewBar(){
  // §46 Preview：加载项目草稿到中央预览区（有 draft 播放，无 draft 诚实提示）
  if (!ED.pid) { editorToast('先打开项目'); return; }
  const stage = document.getElementById('ed-preview-stage');
  const meta = document.getElementById('ed-preview-meta');
  if (!stage) return;
  stage.innerHTML = '加载草稿信息……';
  try {
    const info = await apiGet('/api/projects/' + encodeURIComponent(ED.pid) + '/draft_info');
    if (info && info.draft_path) {
      stage.innerHTML = `<video controls src="/file?path=${encodeURIComponent(info.draft_path)}"></video>`;
      if (meta) meta.textContent = `草稿 · ${info.draft_path.split(/[\\/]/).pop()}`;
    } else {
      stage.innerHTML = '<div>尚无草稿（时间线「渲染草稿」或 AI 命令「生成草稿」后可预览）</div>';
      if (meta) meta.textContent = '无 draft.mp4';
    }
  } catch (e) {
    stage.innerHTML = '<div>草稿信息读取失败：' + esc(String(e)) + '</div>';
  }
}
function editorTopbarRefresh(){
  const tp = document.getElementById('tb-project');
  const tv = document.getElementById('tb-version');
  if (tp && ED.pid) tp.textContent = ED.pid;
  if (tv && ED.comp && ED.comp.version) tv.textContent = 'V' + ED.comp.version;
}

function renderPhysicalTimeline(){
  // §46 十轨时间线（方案 ASCII 图逐轨）：Narration / Dialogue / Original Audio /
  // Main Visual / B-roll / Graphic / Watermark / Subtitle / BGM / SFX。
  const c = ED.comp;
  const total = c.duration || 1;
  const zoom = ED.zoom || 1.0;
  const audioOf = ty => (c.audio || []).filter(a => a.type === ty || (ty === 'ambience' && a.type === 'ambient'));
  const narration = audioOf('narration');
  const dialogue = audioOf('dialogue');
  const original = audioOf('original');
  const bgms = audioOf('bgm');
  const sfxs = audioOf('sfx');
  const brollLayers = []; const graphicLayers = []; const mainLayers = [];
  c.scenes.forEach(sc => (sc.layers || []).forEach(lay => {
    if (lay.type === 'broll' || (lay.role === 'broll')) brollLayers.push([sc, lay]);
    else if (lay.type === 'graphic') graphicLayers.push([sc, lay]);
    else mainLayers.push([sc, lay]);
  }));
  const wmCount = (c.watermarks || []).length;
  const hasSub = !!(c.subtitle_track && c.subtitle_track.cues && c.subtitle_track.cues.length);
  let html = '<div class="ed-track-row">';
  const trackRow = (label, inner, cls) =>
    `<div class="ed-track${cls || ''}"><div class="ed-track-label">${label}</div>`+
    `<div class="ed-track-body" style="transform:scaleX(${zoom});transform-origin:left">${inner}</div></div>`;
  const clip = (text, title, pct, onclick) =>
    `<div class="ed-track-clip" style="width:${pct}%"${title ? ` title="${esc(title)}"` : ''}${onclick ? ` onclick="${onclick}"` : ''}>${esc(text)}</div>`;

  // 轨 1 Narration：场景旁白文案（narration 音轨存在时叠加显示）
  let nInner = '';
  c.scenes.forEach(sc => { nInner += clip('💬' + (sc.text || '').slice(0, 8), sc.text, (sc.duration / total * 100).toFixed(2)); });
  html += trackRow('旁白', nInner || '<div class="hint">无</div>');
  // 轨 2 Dialogue（六类型：对白轨独立车道）
  html += trackRow('对白', dialogue.length ? dialogue.map(t => clip('🗣 对白', '对白轨', '100')).join('') : '<div class="hint">无</div>');
  // 轨 3 原声（原声一等公民：保留/静音状态可视化）
  const oInner = original.length
    ? original.map(t => clip(`🎤 原声${t.volume !== undefined ? ' · ' + t.volume.toFixed(2) : ''}${t.ducking ? ' · 压低' : ''}`,
        '素材原声轨', '100', `editorAudioVolume('original')`)).join('')
    : '<div class="hint">无（点击「保留原声」加入）</div>';
  html += trackRow('原声', oInner);
  // 轨 4 Main Visual（场景 + 主视觉层，可点选）
  let mInner = '';
  c.scenes.forEach((sc, si) => {
    const wPct = (sc.duration / total * 100).toFixed(2);
    const selCls = edIsSelected('scene', sc.id) ? ' ed-sel' : '';
    mInner += `<div class="ed-scene${selCls}" data-scene="${sc.id}" style="width:${wPct}%" onclick="editorSelectScene('${sc.id}', event)">`+
      `<div class="ed-scene-label">${si+1}. ${esc((sc.text||'').slice(0,12))} · ${sc.duration.toFixed(1)}s</div>`;
    sc.layers.forEach((lay, li) => {
      if (lay.type === 'broll' || lay.role === 'broll' || lay.type === 'graphic') return; // 分轨展示
      const lwPct = (lay.duration / sc.duration * 100).toFixed(2);
      const lsel = edIsSelected('layer', `${sc.id}#${li}`) ? ' ed-sel' : '';
      const lock = lay.locked ? '<span title="已锁定">🔒</span>' : '';
      const mtag = lay.motion_recipe_id ? `<span class="ed-motion-tag">${esc(motionCnName(lay.motion_recipe_id))}</span>` : '';
      mInner += `<div class="ed-layer${lsel}" onclick="event.stopPropagation();editorSelectLayer('${sc.id}',${li}, event)" style="width:${lwPct}%">`+
        `${lock}${layerTypeCn(lay.type)}${mtag}</div>`;
    });
    mInner += '</div>';
  });
  html += trackRow('主视觉', mInner);
  // 轨 5 B-roll
  html += trackRow('B-roll', brollLayers.length
    ? brollLayers.map(([sc, lay]) => clip('🎞 B-roll', 'B-roll 层', (lay.duration / total * 100).toFixed(2), `editorSelectLayer('${sc.id}',${sc.layers.indexOf(lay)}, event)`)).join('')
    : '<div class="hint">无</div>');
  // 轨 6 图形
  html += trackRow('图形', graphicLayers.length
    ? graphicLayers.map(([sc, lay]) => clip('🎨 图形', '图形层', (lay.duration / total * 100).toFixed(2), `editorSelectLayer('${sc.id}',${sc.layers.indexOf(lay)}, event)`)).join('')
    : '<div class="hint">无</div>');
  // 轨 7 水印（项目级；点击滚动到水印区）
  html += trackRow('水印', wmCount
    ? (c.watermarks || []).map(w => clip('🔷 ' + (w.text || w.kind), `水印 ${w.kind}@${w.anchor} 不透明度=${w.opacity}`, '100', "document.getElementById('editor-audio-card').scrollIntoView({behavior:'smooth'})")).join('')
    : '<div class="hint">无</div>');
  // 轨 8 字幕
  html += trackRow('字幕', hasSub
    ? clip(`💬 ${c.subtitle_track.mode === 'word' ? '逐词' : '句子'} ${c.subtitle_track.cues.length} 条`, '字幕轨', '100', "document.getElementById('editor-audio-card').scrollIntoView({behavior:'smooth'})")
    : '<div class="hint">无</div>');
  // 轨 9 背景音乐
  html += trackRow('背景音乐', bgms.length
    ? bgms.map(t => clip(`🎵 BGM${t.volume !== undefined && t.volume !== 1 ? ' · ' + t.volume.toFixed(2) : ''}${t.fadeIn ? ' · 淡入' + t.fadeIn + 's' : ''}${t.fadeOut ? ' · 淡出' + t.fadeOut + 's' : ''}`, '背景音乐轨', '100', "editorAudioVolume('bgm')")).join('')
    : '<div class="hint">无</div>');
  // 轨 10 音效
  html += trackRow('音效', sfxs.length
    ? sfxs.map(t => clip('🔔 音效' + (t.volume !== undefined && t.volume !== 1 ? ' · ' + t.volume.toFixed(2) : ''), '音效轨', '100', "editorAudioVolume('sfx')")).join('')
    : '<div class="hint">无</div>');
  // 轨 11 关键帧（§24：层的 KeyframeTrack 帧点按时间位置可视化）
  let kInner = '';
  c.scenes.forEach(sc => (sc.layers || []).forEach((lay, li) => {
    (lay.keyframes || []).forEach(t => t.keyframes.forEach(k => {
      // 帧点绝对时间 = 场景起点 + 层起点 + 帧 at；按全片总长定位
      const absAt = sc.start + (lay.start || 0) + k.at;
      const leftPct = Math.min(99.5, Math.max(0, absAt / total * 100));
      kInner += `<span class="ed-kf-dot" style="left:${leftPct}%" title="${sc.id}#${li} ${t.property} @${k.at}s=${k.value}" `+
        `onclick="editorSelectLayer('${sc.id}',${li}, event)">◆</span>`;
    }));
  }));
  html += trackRow('关键帧', kInner || '<div class="hint">无（检视器「打关键帧」添加）</div>');
  html += '</div>';
  return html;
}

function editorSelectScene(id, ev){
  edSelect(id, 'scene', ev);
}
function editorSelectLayer(sceneId, idx, ev){
  edSelect(`${sceneId}#${idx}`, 'layer', ev);
}

// §8 拆分导出：inline onclick/window 引用兼容（零行为改动）
Object.assign(window, {
  _selKey,
  edSelect,
  edIsSelected,
  edClearSelection,
  edSelectedCount,
  editorInit,
  editorLoad,
  timelineView,
  semanticRoleOf,
  renderSemanticTimeline,
  editorRenderTimeline,
  editorUndo,
  editorRedo,
  editorSaveBar,
  editorPreviewBar,
  editorTopbarRefresh,
  renderPhysicalTimeline,
  editorSelectScene,
  editorSelectLayer,
});
