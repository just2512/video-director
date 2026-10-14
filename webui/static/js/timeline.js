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
function editorSelectLayer(sceneId, idx, ev){
  if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation();
  selectLayer(sceneId, idx, ev, 'timeline');
}
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
  if (typeof editorSourceMonitorRefreshTracks === 'function') editorSourceMonitorRefreshTracks();
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
  previewLoadDraft().then(() => { previewRenderComposition(); previewShowSelectionOverlay(); });
  if (!ED_LIB.domain) edLibDomain('content', 'scenes'); else edLibLoad();
  edVoiceLoadOptions();
  if (typeof directorRefreshContext === 'function') directorRefreshContext();
  return {ok: true};
}

// ---------------------------------------------------------------------------
// §46/§5.2/§6/§36 轨道对象点击 → Inspector 上下文（不再跳「声音与字幕」面板）
// ---------------------------------------------------------------------------

/** §46 Narration Clip：点旁白段 → 旁白设置上下文（音色/语速/试听/重生成）。 */
function selectNarration(sceneIdx){
  select('scene', (ED.comp.scenes[sceneIdx] || {}).id, null, 'timeline-narration');
  if (typeof editorVoiceOpen === 'function') editorVoiceOpen();
  editorToast('已选中旁白段（场景 ' + (sceneIdx + 1) + '）——配音设置在右侧「配音」分区');
}

/** §6 水印轨 → 项目叠加层分区。 */
function projectOverlayOpen(){
  selectProject();
  const sec = document.querySelector('#editor-inspector [data-section="overlays"]');
  if (sec) sec.scrollIntoView({behavior: 'smooth', block: 'center'});
  else if (typeof edShowInspector === 'function') edShowInspector();
}

/** §5.2 原声轨 → 原声 Inspector（来源/音量/闪避/淡入淡出）。 */
function originalTrackOpen(){
  selectProject();
  if (typeof editorAudioBusOpen === 'function') editorAudioBusOpen();
  const orig = (ED.comp.audio || []).find(a => a.type === 'original');
  editorToast(orig ? '原声轨：音量/静音在「整体调音」面板' : '原声未启用（选中视频层后「保留原声」）');
}

/** §36 BGM 轨 → 音乐上下文。 */
function bgmTrackOpen(){
  selectProject();
  if (typeof editorAudioBusOpen === 'function') editorAudioBusOpen();
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
function _tlAxisBody(el){
  return (el && el.querySelector && el.querySelector('.ed-track-body')) ||
    document.querySelector('#editor-timeline .ed-tl-canvas .ed-track-body');
}
function _tlTimelineAxisWidth(){
  const body = _tlAxisBody(document.querySelector('#editor-timeline .ed-tl-scroll'));
  return Math.max(1, body ? body.getBoundingClientRect().width : 1);
}
function _tlPlayheadLeft(at, total){
  const canvas = document.querySelector('#editor-timeline .ed-tl-canvas');
  const body = _tlAxisBody(canvas);
  if (!canvas || !body || !total) return 0;
  const canvasRect = canvas.getBoundingClientRect();
  const bodyRect = body.getBoundingClientRect();
  return Math.max(0, bodyRect.left - canvasRect.left + (at / total) * bodyRect.width);
}
function playheadSet(pct, ev){
  const total = ED.comp ? ED.comp.duration : 1;
  let at = Math.max(0, Math.min(1, pct)) * total;
  if (ev && window.editorSessionSnapTime) {
    at = window.editorSessionSnapTime(at, {shiftKey: !!ev.shiftKey});
  } else if (window.editorSessionSnapTime) {
    window.editorSessionSnapTime(at, {disable: true});
  }
  if (ev && ev.type === 'pointerdown' && typeof previewPause === 'function') previewPause();
  PLAYHEAD.at = at;
  const ph = document.getElementById('tl-playhead');
  if (ph) ph.style.left = _tlPlayheadLeft(at, total) + 'px';
  const label = document.getElementById('tl-playhead-label');
  if (label) label.textContent = PLAYHEAD.at.toFixed(2) + 's';
  // Scrubbing updates the central preview continuously, not only on pointer release.
  if (typeof previewSeek === 'function') previewSeek(PLAYHEAD.at);
}
function editorProjectFrameTime(value){
  const comp = (window.ED && window.ED.comp) || {};
  const project = comp.project || {};
  const rawFps = project.fps != null ? project.fps : (comp.fps != null ? comp.fps : 30);
  const fps = Number(rawFps);
  const frameRate = Number.isFinite(fps) && fps > 0 ? Math.max(1, Math.trunc(fps)) : 30;
  if (window.TimelineEditMath && typeof window.TimelineEditMath.projectFrameTime === 'function') {
    return window.TimelineEditMath.projectFrameTime(value, frameRate);
  }
  const time = Number(value);
  if (!Number.isFinite(time) || time < 0) return time;
  const frames = Math.floor(time * frameRate + 0.5);
  return Math.round((frames / frameRate) * 1000000) / 1000000;
}

function editorSnapPlayheadToFrame(value){
  const at = editorProjectFrameTime(value);
  if (!Number.isFinite(at) || at < 0) return at;
  PLAYHEAD.at = at;
  const comp = (window.ED && window.ED.comp) || {};
  const projectDuration = Number(comp.duration != null ? comp.duration : (comp.project && comp.project.duration));
  const total = Number.isFinite(projectDuration) && projectDuration > 0 ? projectDuration : 1;
  const handle = document.getElementById('tl-playhead');
  if (handle) handle.style.left = _tlPlayheadLeft(at, total) + 'px';
  const label = document.getElementById('tl-playhead-label');
  if (label) label.textContent = at.toFixed(2) + 's';
  if (typeof previewSeek === 'function') previewSeek(at);
  return at;
}

function _tlPct(ev, el){
  const body = _tlAxisBody(el);
  const r = (body || el).getBoundingClientRect();
  if (!Number.isFinite(r.width) || r.width <= 0) return 0;
  return Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
}

// ---------------------------------------------------------------------------
// §14 物理时间线渲染（四组结构）
// ---------------------------------------------------------------------------
function editorRenderTimeline(){
  const box = document.getElementById('editor-timeline');
  const view = ED.timelineView || 'time';
  const hasPhysicalTimeline = view !== 'semantic';
  if (view === 'semantic') {
    box.innerHTML = renderSemanticTimeline();
  } else if (view === 'dual') {
    box.innerHTML = renderSemanticTimeline() +
      '<div class="hint" style="margin:6px 0">物理时间线</div>' + renderPhysicalTimeline();
  } else {
    box.innerHTML = renderPhysicalTimeline();
  }

  // Dual view still contains the editable physical timeline. Keep all drag,
  // trim, watermark and session/snap bindings active in both physical views.
  // Returning early after rendering the semantic half left the physical half
  // looking editable while every pointer interaction was inert.
  if (hasPhysicalTimeline) {
    _tlBindPlayheadDrag();
    _tlBindLayerDrag();
    _tlBindAudioClipDrag();
    _tlBindWatermarkDrag();
    _tlBindVisualOverlayDrag();
    _tlBindSubtitleCueDrag();
    _tlBindSceneDrag();
    _tlBindWheelZoom();
    if (window.editorSessionBindTimeline) window.editorSessionBindTimeline();
  }
  document.dispatchEvent(new CustomEvent('editor:timeline-rendered'));
  if (!window.__tlViewportResizeBound) {
    window.__tlViewportResizeBound = true;
    let resizeFrame = 0;
    window.addEventListener('resize', () => {
      if (resizeFrame) cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = 0;
        const current = document.querySelector('#editor-timeline .ed-tl-scroll');
        if (!current || !ED.comp) return;
        const left = current.scrollLeft;
        editorRenderTimeline();
        const next = document.querySelector('#editor-timeline .ed-tl-scroll');
        if (next) next.scrollLeft = left;
      });
    });
  }
}

function _timelineAudioTracks(comp){
  const roleLanes = [
    {id:'audio-narration', name:'旁白音频', order:0, role:'narration'},
    {id:'audio-dialogue', name:'对白', order:1, role:'dialogue'},
    {id:'audio-original', name:'原声', order:2, role:'original'},
    {id:'audio-bgm', name:'背景音乐', order:3, role:'bgm'},
    {id:'audio-sfx', name:'音效', order:4, role:'sfx'},
    {id:'audio-ambience', name:'环境音', order:5, role:'ambience'},
  ];
  const roleByType = Object.fromEntries(roleLanes.map(lane => [lane.role, lane.id]));
  roleByType.ambient = roleByType.ambience;
  const lanes = Array.isArray(comp.audio_tracks) && comp.audio_tracks.length
    ? comp.audio_tracks.map(lane => ({...lane}))
    : roleLanes.map(lane => ({id:lane.id, name:lane.name, order:lane.order}));
  const ensureLane = (id, role) => {
    if (lanes.some(lane => lane.id === id)) return;
    const fallback = roleLanes.find(lane => lane.id === id || lane.role === role);
    lanes.push({id, name: fallback && id === fallback.id ? fallback.name : ('音轨 ' + (lanes.length + 1)),
      order: Math.max(-1, ...lanes.map(lane => Number(lane.order) || 0)) + 1});
  };
  (comp.audio || []).forEach(clip => {
    const role = String(clip.type || 'sfx').toLowerCase();
    const laneId = String(clip.track_id || clip.trackId || roleByType[role] || ('audio-' + role));
    ensureLane(laneId, role);
    if (!clip.track_id) clip.track_id = laneId; // legacy UI state only; the server migrates on save/load.
  });
  return lanes.sort((a,b) => Number(a.order || 0) - Number(b.order || 0));
}

function renderPhysicalTimeline(){
  const c = ED.comp;
  const total = c.duration || 1;
  const zoom = ED.zoom || 1.0;
  const timelineBox = document.getElementById('editor-timeline');
  const viewportWidth = Math.max(320, (timelineBox && timelineBox.clientWidth) || 900);
  const axisOffset = 98; // 90px label column + 8px track gap; labels are not time.
  const canvasWidth = Math.max(axisOffset + 1, Math.round(axisOffset + (viewportWidth - axisOffset) * zoom));
  const audioOf = ty => (c.audio || []).filter(a => a.type === ty || (ty === 'ambience' && a.type === 'ambient'));

  // §16 轨道归类：画面（主视觉/B-roll/图形/水印）/ 语言（旁白/对白/字幕）/
  // 声音（原声/BGM/SFX）/ 时间关系（关键帧）
  const tracks = {
    picture: [], language: [], sound: [], temporal: [],
  };
  // 轨 1 旁白（§46 Narration Clip = 可操作对象：点击 → 旁白 Inspector）
  const narration = (c.scenes || []).map(sc =>
    ({pct: sc.duration / total * 100, title: sc.text, text: (sc.text || '').slice(0, 10)}));
  tracks.language.push({id: 'narration', label: '旁白', readOnly: false,
    clips: narration.map((n, i) => ({pct: n.pct, text: n.text, title: n.title,
      onclick: `selectNarration(${i})`}))});
  // Render each subtitle cue as a timed clip on the shared CompositionIR clock.
  const subtitleTrack = c.subtitle_track || null;
  const subtitleCues = subtitleTrack && Array.isArray(subtitleTrack.cues) ? subtitleTrack.cues : [];
  tracks.language.push({id: 'subtitle', label: '字幕', readOnly: false,
    clips: subtitleCues.map((cue, index) => {
      const start = Number(cue && cue.start);
      const end = Number(cue && cue.end);
      const valid = Number.isFinite(start) && Number.isFinite(end)
        && end > start && end > 0 && start < total;
      const visibleStart = valid ? Math.max(0, start) : total * 0.992;
      const visibleEnd = valid ? Math.min(total, end) : total;
      const leftPct = Math.max(0, Math.min(100, visibleStart / total * 100));
      const pct = valid
        ? Math.max(0, Math.min(100 - leftPct, (visibleEnd - visibleStart) / total * 100))
        : 0.8;
      const text = String((cue && cue.text) || '（空字幕）');
      const kind = subtitleTrack.mode === 'word' ? '词级' : '句级';
      return {
        positioned: true, leftPct, pct, outOfRange: !valid, subtitleCueIndex: index,
        text: text.slice(0, 12),
        title: `${kind}字幕 · ${text} · ${Number.isFinite(start) ? start.toFixed(2) : '?'}–${Number.isFinite(end) ? end.toFixed(2) : '?'}s · 拖动移动、两端修剪`,
        onclick: `editorSubtitleCueSelect(${index}, event)`
      };
    })
  });
  // 画面组：主视觉（场景块 §14 hitbox 分离）
  tracks.picture.push({id: 'main', label: '主视觉', isSceneTrack: true});
  const brollLayers = [], graphicLayers = [];
  c.scenes.forEach(sc => (sc.layers || []).forEach(lay => {
    if (lay.type === 'broll' || (lay.role === 'broll')) brollLayers.push([sc, lay]);
    else if (lay.type === 'graphic') graphicLayers.push([sc, lay]);
  }));
  tracks.picture.push({id: 'broll', label: 'B-roll',
    clips: brollLayers.map(([sc, lay]) => ({
      positioned: true, leftPct: Math.max(0, Math.min(100, (Number(sc.start || 0) + Number(lay.start || 0)) / total * 100)), pct: Math.max(0, Math.min(100 - Math.max(0, Math.min(100, (Number(sc.start || 0) + Number(lay.start || 0)) / total * 100)), Number(lay.duration || 0) / total * 100)), text: 'B-roll', title: 'B-roll 层',
      onclick: `editorSelectLayer('${sc.id}',${sc.layers.indexOf(lay)}, event)`}))});
  tracks.picture.push({id: 'graphic', label: '图形',
    clips: graphicLayers.map(([sc, lay]) => ({
      positioned: true, leftPct: Math.max(0, Math.min(100, (Number(sc.start || 0) + Number(lay.start || 0)) / total * 100)), pct: Math.max(0, Math.min(100 - Math.max(0, Math.min(100, (Number(sc.start || 0) + Number(lay.start || 0)) / total * 100)), Number(lay.duration || 0) / total * 100)), text: '图形', title: '图形层',
      onclick: `editorSelectLayer('${sc.id}',${sc.layers.indexOf(lay)}, event)`}))});
  const watermarks = c.watermarks || [];
  tracks.picture.push({id: 'watermark', label: '贴纸 / 覆盖层',
    clips: watermarks.map((wm, index) => {
      const start = Math.max(0, Number(wm.start) || 0);
      const end = wm.duration == null ? total : Math.min(total, start + Math.max(0, Number(wm.duration) || 0));
      const visibleStart = Math.min(total, start);
      const duration = Math.max(0, end - visibleStart);
      const leftPct = Math.min(100, visibleStart / total * 100);
      const pct = Math.max(0, Math.min(100 - leftPct, duration / total * 100));
      const label = wm.text || wm.asset_id || wm.kind || '覆盖层';
      return {
        positioned: true, leftPct, pct, watermarkIndex: index,
        text: String(label).slice(0, 12),
        title: `覆盖层 · ${label} · ${start.toFixed(2)}–${(start + duration).toFixed(2)}s · 拖动移动、边缘修剪、点击编辑`,
        onclick: `editorWatermarkClipOpen(${index}, event)`
      };
    })});
  const visualOverlays = c.visual_overlays || [];
  const visualTracks = (Array.isArray(c.visual_tracks) && c.visual_tracks.length
    ? c.visual_tracks : [{id:'overlay-main', name:'画面叠加', order:0}])
    .slice().sort((left, right) => Number(left.order || 0) - Number(right.order || 0));
  const totalVisualDuration = Number(c.duration) || 1;
  visualTracks.forEach((track) => {
    const trackId = String(track.id || 'overlay-main');
    const clips = visualOverlays.map((clip, index) => {
      const start = Math.max(0, Number(clip.start) || 0);
      const duration = Math.max(0, Number(clip.duration) || 0);
      const leftPct = Math.min(100, start / totalVisualDuration * 100);
      const pct = Math.max(0, Math.min(100 - leftPct, duration / totalVisualDuration * 100));
      const sourceAssets = c.assets || {};
      const asset = Array.isArray(sourceAssets) ? sourceAssets.find(item => item.id === clip.asset_id) : sourceAssets[clip.asset_id];
      const assetName = asset && (asset.name || (asset.file && String(asset.file).split(/[\\/]/).pop())) || clip.asset_id || '素材';
      const thumbnailUrl = asset ? (asset.thumbnail_url || ('/api/asset_thumbnail?asset_id=' + encodeURIComponent(clip.asset_id))) : '';
      return { positioned:true, leftPct, pct, visualOverlayIndex:index, visualOverlayId:String(clip.id || ''),
        visualTrackId:trackId, thumbnailUrl,
        selected: typeof edIsSelected === 'function' && edIsSelected('visual_overlay', String(clip.id || '')),
        text:String(assetName).slice(0, 12),
        title:'画面轨道 · ' + (track.name || '画面叠加') + ' · ' + assetName + ' · ' + start.toFixed(2) + '–' + (start + duration).toFixed(2) + 's · 点击选中，拖动移动/修剪；选中后可切分或编辑',
        onclick:'editorSelectVisualOverlay(' + index + ', event)' };
    }).filter(item => String((visualOverlays[item.visualOverlayIndex] || {}).track_id || 'overlay-main') === trackId);
    tracks.picture.push({ id:'visual-overlay-' + trackId, label:track.name || '画面叠加',
      visualTrackId:trackId, trackOrder:Number(track.order) || 0, locked:!!track.locked, addAction:'editorVisualOverlayPick', newTrackAction:(Number(track.order) === 0 ? 'editorVisualTrackCreate' : null), clips });
  });
  // 声音组：每个音频 clip 依据 Composition 中的 start/duration 定位.
  // 之前每类声音只显示第一条记录并绘制为 100% 全长，和实际渲染时间语义不一致。
  const audioClip = (a, label, onclick) => {
    const start = Math.max(0, Number(a.start) || 0);
    const duration = Math.max(0, Number(a.duration) || 0);
    const inRange = start < total && duration > 0;
    const visibleDuration = inRange ? Math.min(duration, total - start) : 0;
    const leftPct = inRange ? Math.min(100, start / total * 100) : 99.2;
    const pct = inRange ? Math.max(0, Math.min(100 - leftPct, visibleDuration / total * 100)) : 0.8;
    const end = start + duration;
    const gain = a.muted ? '已静音' : ('音量 ' + (Number(a.volume ?? 1) * 100).toFixed(0) + '%');
    const asset = a.asset_id && c.assets && c.assets[a.asset_id];
    const assetName = asset && (asset.name || (asset.file && asset.file.split(/[\\/]/).pop())) || a.asset_id || '未绑定素材';
    const edgeNote = !inRange ? ' · 超出项目片长，当前不会出现在成片中' : (end > total ? ' · 末尾超出片长，渲染时截断' : '');
    const linkedOverlay = a.linked_visual_overlay_id
      ? (c.visual_overlays || []).find(item => item.id === a.linked_visual_overlay_id)
      : null;
    const linkNote = a.linked_visual_overlay_id
      ? (linkedOverlay
          ? ` · 已关联画面片段 ${linkedOverlay.id}（移动/修剪/切分/删除会同步声音）`
          : ' · 画面关联丢失，需检查项目数据')
      : '';
    // 音量包络线数据（剪映 SegmentKeyFrameAudioLine 吸收的最小可见版）：
    // clip.keyframes[property=volume] 点表 → 前端只做映射（at→leftPct、
    // volume→top%），渲染层不插值——插值归渲染器。
    const volKfs = (a.keyframes || [])
      .filter(kt => kt && kt.property === 'volume' && Array.isArray(kt.keyframes))
      .flatMap(kt => kt.keyframes)
      .filter(kf => kf && Number.isFinite(Number(kf.at)))
      .map(kf => ({at: Math.max(0, Number(kf.at)), volume: Math.max(0, Math.min(2, Number(kf.value ?? 1) || 0))}))
      .sort((x, y) => x.at - y.at);
    return {
      positioned: true, leftPct, pct, outOfRange: !inRange,
      text: label,
      title: `${label} · ${assetName} · ${start.toFixed(2)}–${end.toFixed(2)}s · ${gain}${edgeNote}${linkNote} · 点击打开该类型音轨设置`,
      audioIndex: (c.audio || []).indexOf(a),
      volumeEnvelope: volKfs.length ? volKfs : null,
      clipDuration: end - start,
      onclick
    };
  };
  const roleLabels = {narration:'旁白音频', dialogue:'对白', original:'原声', bgm:'BGM', sfx:'SFX', ambience:'环境音', ambient:'环境音'};
  _timelineAudioTracks(c).forEach(lane => {
    const indexed = (c.audio || []).map((clip, index) => ({clip, index})).filter(item => String(item.clip.track_id || '') === String(lane.id));
    const counts = {}, seen = {};
    indexed.forEach(item => { const role = String(item.clip.type || 'sfx').toLowerCase(); counts[role] = (counts[role] || 0) + 1; });
    const clips = indexed.map(item => {
      const role = String(item.clip.type || 'sfx').toLowerCase();
      seen[role] = (seen[role] || 0) + 1;
      const label = roleLabels[role] || role;
      return audioClip(item.clip, counts[role] > 1 ? label + ' ' + seen[role] : label, null);
    });
    tracks.sound.push({id:'audio-track-' + String(lane.id), label:lane.name || '音轨', audioTrackId:String(lane.id), locked:!!lane.locked, clips});
  });

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
  // Clips sharing a time interval must not overlap their own DOM hit targets.
  // Split only busy tracks; preserve command indices on every copied clip.
  if (window.TimelineEditMath && typeof window.TimelineEditMath.splitOverlappingTrack === 'function') {
    for (const group of GROUP_ORDER) {
      tracks[group] = (tracks[group] || []).flatMap(track =>
        window.TimelineEditMath.splitOverlappingTrack(track));
    }
  }
  const hasConvertibleMainClips = (c.scenes || []).some(scene =>
    (scene.layers || []).some(layer => ["video", "image"].includes(String(layer.type || "").toLowerCase())));
  const expandToolbar = hasConvertibleMainClips
    ? `<div class="ed-timeline-command-bar">
        <span class="hint">将兼容的视频/图片一次性移入自由画面轨，保留原时长并支持撤销。</span>
        <button type="button" id="ed-expand-main-timeline" class="btn-secondary btn-sm"
          onclick="editorExpandMainTimelineToGlobalTrack(event)">展开为自由时间线</button>
      </div>`
    : "";
  let html = `${expandToolbar}<div class="ed-tl-ruler"><span id="tl-playhead-label">${PLAYHEAD.at.toFixed(1)}s</span>
    <span class="ed-tl-zoom" role="toolbar" aria-label="时间线缩放">
      <button type="button" id="tl-zoom-out" class="btn-secondary btn-sm" title="缩小（Ctrl+滚轮也可缩放）" aria-label="缩小时间线" onclick="editorZoomTimeline(-1)">−</button>
      <span id="tl-zoom-value" aria-live="polite">${Math.round((ED.zoom || 1) * 100)}%</span>
      <button type="button" id="tl-zoom-in" class="btn-secondary btn-sm" title="放大（Ctrl+滚轮也可缩放）" aria-label="放大时间线" onclick="editorZoomTimeline(1)">＋</button>
    </span></div>
    <div class="ed-tl-scroll"><div class="ed-tl-canvas" style="width:${canvasWidth}px">`;
  const trackRow = (t) => {
    const bodyInner = t.isSceneTrack
      ? _sceneTrackHtml(c, total)
      : (t.clips || []).map(cl => {
          const style = cl.positioned
            ? `position:absolute;top:0;height:100%;left:${cl.leftPct}%;width:${cl.pct}%;`
            : `width:${cl.pct}%`;
          const outOfRangeClass = cl.outOfRange ? ' tl-out-of-range' : '';
          const hasAudioIndex = Number.isInteger(cl.audioIndex) && cl.audioIndex >= 0;
          const hasWatermarkIndex = Number.isInteger(cl.watermarkIndex) && cl.watermarkIndex >= 0;
          const hasVisualOverlayIndex = Number.isInteger(cl.visualOverlayIndex) && cl.visualOverlayIndex >= 0;
          const hasSubtitleCueIndex = Number.isInteger(cl.subtitleCueIndex) && cl.subtitleCueIndex >= 0;
          const audioAttr = hasAudioIndex
            ? ` data-audio-index="${cl.audioIndex}" aria-label="${esc(cl.text)}：拖动移动，边缘裁剪；点击打开片段设置"` : '';
          const watermarkAttr = hasWatermarkIndex
            ? ` data-watermark-index="${cl.watermarkIndex}" aria-label="${esc(cl.text)}：拖动移动、边缘修剪；点击编辑覆盖层"` : '';
          const visualOverlayAttr = hasVisualOverlayIndex
            ? ` data-visual-overlay-index="${cl.visualOverlayIndex}" aria-label="${esc(cl.text)}：拖动移动、边缘修剪；点击编辑画面叠加"` : '';
          const subtitleCueAttr = hasSubtitleCueIndex
            ? ` data-subtitle-cue-index="${cl.subtitleCueIndex}" aria-label="${esc(cl.text)}：拖动移动，两端修剪时间锚点"` : '';
          const clipClass = hasAudioIndex ? ' ed-audio-clip' :
            (hasWatermarkIndex ? ' ed-watermark-clip' :
            (hasVisualOverlayIndex ? ' ed-visual-overlay-clip' : (hasSubtitleCueIndex ? ' ed-subtitle-cue' : '')));
          const selectedClass = cl.selected ? ' ed-tl-clip-selected' : '';
          const thumbnailHtml = hasVisualOverlayIndex && cl.thumbnailUrl
            ? `<img class="ed-clip-thumb" src="${esc(cl.thumbnailUrl)}" alt="" loading="lazy" decoding="async"><span class="ed-track-clip-label">${esc(cl.text)}</span>`
            : esc(cl.text);
          // 音量包络线（A5）：clip 内 SVG 折线，0–2 音量映射到 clip 高度。
          // 关键帧用菱形点，播放语义见 title；不可拖动（编辑走 inspector）。
          let envelopeHtml = '';
          if (hasAudioIndex && Array.isArray(cl.volumeEnvelope) && cl.volumeEnvelope.length) {
            const dur = Math.max(0.01, Number(cl.clipDuration) || 1);
            const pts = cl.volumeEnvelope.map(k => {
              const x = Math.max(0, Math.min(100, k.at / dur * 100));
              const y = Math.max(0, Math.min(100, (1 - k.volume / 2) * 100));
              return `${x},${y.toFixed(1)}`;
            }).join(' ');
            const dots = cl.volumeEnvelope.map(k => {
              const x = Math.max(0, Math.min(100, k.at / dur * 100));
              const y = Math.max(0, Math.min(100, (1 - k.volume / 2) * 100));
              return `<circle cx="${x}%" cy="${y.toFixed(1)}%" r="3" class="ed-volume-kf-dot"></circle>`;
            }).join('');
            envelopeHtml = `<svg class="ed-volume-envelope" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}" class="ed-volume-envelope-line"></polyline>${dots}</svg>`;
          }
          const clickAttr = hasAudioIndex
            ? ` onclick="editorAudioClipOpen(${cl.audioIndex}, event)"`
            : (hasWatermarkIndex
                ? ` onclick="editorWatermarkClipOpen(${cl.watermarkIndex}, event)"`
                : (hasVisualOverlayIndex
                    ? ` onclick="editorSelectVisualOverlay(${cl.visualOverlayIndex}, event)"`
                    : (hasSubtitleCueIndex
                        ? ` onclick="editorSubtitleCueSelect(${cl.subtitleCueIndex}, event)"`
                        : (cl.onclick ? ` onclick="${cl.onclick}"` : ''))));
          return `<div class="ed-track-clip${t.readOnly ? ' tl-readonly' : ''}${outOfRangeClass}${clipClass}${selectedClass}" style="${style}"${audioAttr}${watermarkAttr}${visualOverlayAttr}${subtitleCueAttr}${cl.title ? ` title="${esc(cl.title)}"` : ''}${clickAttr}>${thumbnailHtml}${envelopeHtml}</div>`;
        }).join('');
    const kfHtml = (t.kfPoints || []).map(k =>
      `<span class="ed-kf-dot" style="left:${k.leftPct}%" title="${esc(k.title)}" onclick="${k.onclick}">◆</span>`).join('');
    const empty = (!t.clips || !t.clips.length) && !t.isSceneTrack && !(t.kfPoints || []).length;
    const labelHtml = t.audioTrackId
      ? '<div class="ed-track-label ed-audio-track-label"><span title="' + esc(t.label) + '">' + esc(t.label) + '</span><button type="button" class="btn-secondary btn-sm ed-track-lock" title="' + (t.locked ? '解锁音轨' : '锁定音轨') + '" aria-label="' + (t.locked ? '解锁音轨' : '锁定音轨') + '" aria-pressed="' + (t.locked ? 'true' : 'false') + '" onclick="editorAudioTrackToggleLock(event, &quot;' + esc(t.audioTrackId) + '&quot;)"></button><button type="button" class="btn-secondary btn-sm ed-audio-track-menu" title="音轨名称与删除" aria-label="编辑音轨" onclick="editorAudioTrackMenu(event, &quot;' + esc(t.audioTrackId) + '&quot;)"></button></div>'
      : t.visualTrackId && t.showTrackControls !== false
      ? `<div class="ed-track-label ed-visual-track-label"><span class="ed-visual-track-name" title="${esc(t.label)}">${esc(t.label)}</span><div class="ed-visual-track-actions"><button type="button" class="btn-secondary btn-sm ed-track-add" title="${t.locked ? '轨道已锁定' : '从项目素材库向此轨道添加片段'}" ${t.locked ? 'disabled' : ''} onclick="editorVisualOverlayPick(event, '${esc(t.visualTrackId)}')">＋</button><button type="button" class="btn-secondary btn-sm ed-track-lock" title="${t.locked ? '解锁画面轨道' : '锁定画面轨道'}" aria-label="${t.locked ? '解锁画面轨道' : '锁定画面轨道'}" aria-pressed="${t.locked ? 'true' : 'false'}" onclick="editorVisualTrackToggleLock(event, '${esc(t.visualTrackId)}')"></button><button type="button" class="btn-secondary btn-sm" title="轨道名称、顺序与删除" onclick="editorVisualTrackMenu(event, '${esc(t.visualTrackId)}')">⋯</button>${t.newTrackAction ? `<button type="button" class="btn-secondary btn-sm" title="新增画面轨道" onclick="editorVisualTrackCreate(event)">＋轨</button>` : ''}</div></div>`
      : t.visualTrackId
        ? `<div class="ed-track-label"><span title="${esc(t.label)}">${esc(t.label)}</span></div>`
        : t.addAction
        ? `<div class="ed-track-label">${esc(t.label)} <button type="button" class="btn-secondary btn-sm ed-track-add" title="添加片段" onclick="${t.addAction}(event)">＋</button></div>`
        : `<div class="ed-track-label">${esc(t.label)}</div>`;
    return '<div class="ed-track' + (t.locked ? ' ed-track-locked' : '') + '"' + (t.visualTrackId ? ' data-visual-track-id="' + esc(t.visualTrackId) + '"' : '') + (t.audioTrackId ? ' data-audio-track-id="' + esc(t.audioTrackId) + '"' : '') + '>' + labelHtml +
      `<div class="ed-track-body">${bodyInner}${kfHtml}${empty ? '<span class="hint tl-empty-hint">无</span>' : ''}</div></div>`;
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
        <span class="ed-group-caret">▾</span> ${G.label}` + (g === 'sound' ? '<button type="button" class="btn-secondary btn-sm ed-audio-track-add" title="新增音轨" onclick="event.stopPropagation(); editorAudioTrackAdd(event)">＋音轨</button>' : '') + '</div>';
      for (const t of tracks[g] || []) html += trackRow(t);
    }
  }
  // 播放头（§17 可拖）
  html += `<div class="tl-playhead" id="tl-playhead" style="left:${axisOffset + (PLAYHEAD.at / total) * (canvasWidth - axisOffset)}px" title="拖动定位预览"></div>`;
  html += '</div></div>';
  const cnt = document.getElementById('editor-selection-count');
  if (cnt) cnt.textContent = SEL.selectionSet.size ? `已选 ${SEL.selectionSet.size} 项` : '未选中';
  return html;
}

/** §14 场景轨：Scene 头部条（可点=选场景）+ 层窄带（点=选层）。空白区域点击=Scene。 */
function _tlAssetThumbHtml(lay){
  const aid = lay && lay.asset_id;
  const a = aid && ED.comp && ED.comp.assets && ED.comp.assets[aid];
  if (!aid || !a) return '<span class="tl-thumb-empty">无画面</span>';
  return '<img class="ed-scene-thumb" src="/api/asset_thumbnail?asset_id=' +
    encodeURIComponent(aid) + '" alt="" loading="lazy" decoding="async">';
}
function _sceneTrackHtml(c, total){
  let mInner = '';
  c.scenes.forEach((sc, si) => {
    const wPct = (sc.duration / total * 100).toFixed(2);
    const selCls = edIsSelected('scene', sc.id) ? ' ed-sel' : '';
    mInner += `<div class="ed-scene${selCls}" data-scene="${sc.id}" style="width:${wPct}%">`+
      `<div class="ed-scene-header" onclick="editorSelectScene('${sc.id}', event)">`+
      `<span class="ed-scene-thumb-wrap">${_tlAssetThumbHtml((sc.layers || [])[0])}</span><span class="ed-scene-num">${si + 1}</span>`+
      `<span class="ed-scene-title">${esc((sc.text || '').slice(0, 12))}</span>`+
      `<span class="ed-scene-dur">${sc.duration.toFixed(1)}s</span></div>`+
      `<div class="ed-scene-body" onclick="editorSelectScene('${sc.id}', event)">`;
    sc.layers.forEach((lay, li) => {
      if (lay.type === 'broll' || lay.role === 'broll' || lay.type === 'graphic') return; // 分轨展示
      const lsel = edIsSelected('layer', `${sc.id}#${li}`) ? ' ed-sel' : '';
      const lock = lay.locked ? '<span title="已锁定">锁</span>' : '';
      const gapCls = lay.type === 'gap' ? ' ed-layer-gap' : '';
      const gapTitle = lay.type === 'gap' ? ' title="该位置为空隙；已提升的视频片段可在独立轨道移动、修剪或切分"' : '';
      const mtag = lay.motion_recipe_id ? `<span class="ed-motion-tag">${esc(motionCnName(lay.motion_recipe_id))}</span>` : '';
      // §14 层窄带：data 属性驱动拖动（不用 stopPropagation——hitbox 物理分离）
      mInner += `<div class="ed-layer${lsel}${gapCls}" data-scene="${sc.id}" data-idx="${li}" data-dur="${lay.duration}" data-type="${esc(lay.type)}"${gapTitle}
        onclick="editorSelectLayer('${sc.id}',${li}, event)">${lock}${lay.type === 'gap' ? '空隙' : layerTypeCn(lay.type)}${mtag}</div>`;
    });
    mInner += '</div></div>';
  });
  return mInner;
}

// 播放头拖动（§17）：只在手柄（::after 头部）上按住才拖；线体 pointer-events:none
// 不再拦截场景/层的点击 hitbox（§14 修复的伴随缺陷）。
// ev 必须透传给 playheadSet——§2 snap：拖动中 editorSessionSnapTime(at) 真实吸附
// （此前恒传 null → playheadSet 走 disable 分支，播放头拖动从不吸附，Guide 也不显示）。
function _tlBindPlayheadDrag(){
  const scroll = document.querySelector('#editor-timeline .ed-tl-scroll');
  if (!scroll) return;
  const handle = document.getElementById('tl-playhead');
  if (handle) {
    handle.addEventListener('pointerdown', ev => {
      PLAYHEAD.dragging = true;
      ev.preventDefault();  // 手柄按下不触发下层场景选择
      playheadSet(_tlPct(ev, scroll), ev);
    });
  }
  scroll.addEventListener('pointerdown', ev => {
    // 轨道空白区按下（非层/非场景头）→ 直接定位播放头（§17 拖播放头）
    if (ev.target.closest('.ed-layer') || ev.target.closest('.ed-scene-header')
        || ev.target.closest('.ed-track-clip') || ev.target.closest('.ed-kf-dot')) return;
    if (ev.target.closest('.ed-scene') && !ev.target.closest('.ed-scene-body')) return;
    PLAYHEAD.dragging = true;
    playheadSet(_tlPct(ev, scroll), ev);
  });
  scroll.addEventListener('pointermove', ev => {
    if (PLAYHEAD.dragging) playheadSet(_tlPct(ev, scroll), ev);
  });
  // 释放 handler 挂 document（鼠标可移出 scroll 释放）。每次重渲都会重建
  // scroll 并再挂一个——旧 handler 闭包捕获脱离 DOM 的旧 scroll：
  // getBoundingClientRect()=0 宽 → (x-0)/0=Infinity → clamp=1 → 释放瞬间
  // 播放头跳到结尾并置 dragging=false 短路正确 handler（B9，实测必现）。
  // 修复：模块级单 handler，动态解析当前连接中的 scroll。
  if (!window.__tlPlayheadUp) {
    window.__tlPlayheadUp = ev => {
      const live = document.querySelector('#editor-timeline .ed-tl-scroll');
      if (!live || !live.isConnected) return;
      if (PLAYHEAD.dragging) {
        PLAYHEAD.dragging = false;
        playheadSet(_tlPct(ev, live), {type: 'pointerup', shiftKey: !!ev.shiftKey});
        // 释放后清 Snap Guide（拖动辅助线不留屏）
        if (window.editorSessionClearSnapGuide) window.editorSessionClearSnapGuide();
      }
    };
    document.addEventListener('pointerup', window.__tlPlayheadUp);
  }
}

// ---------------------------------------------------------------------------
// §18 时间线缩放 + 拖拽边缘自动滚动（剪映 v2 报告 §4 吸收：每种拖拽一个
// 自动滚动器；Ctrl+滚轮缩放以指针为锚；触控板二分 angleDelta 模式判别）
// ---------------------------------------------------------------------------
const TL_ZOOM_MIN = 0.25, TL_ZOOM_MAX = 8, TL_ZOOM_STEP = 1.25;
const TL_AXIS_OFFSET = 98; // 与 renderPhysicalTimeline 的 90px 标签列 + 8px 轨距一致
function editorZoomTimeline(direction, anchorClientX){
  const prev = Number(ED.zoom) || 1;
  const next = Math.max(TL_ZOOM_MIN, Math.min(TL_ZOOM_MAX, prev * (direction > 0 ? TL_ZOOM_STEP : 1 / TL_ZOOM_STEP)));
  if (Math.abs(next - prev) < 1e-9) return;
  const scroll = document.querySelector('#editor-timeline .ed-tl-scroll');
  const total = Number(ED.comp && ED.comp.duration) || 1;
  // 指针处的时间（精确映射：像素 = 轴偏移 + t/total × (视口宽-轴偏移)×zoom）
  let anchorTime = total / 2;
  if (scroll) {
    const rect = scroll.getBoundingClientRect();
    const inner = Math.max(1, (rect.width - TL_AXIS_OFFSET) * prev);
    const px = scroll.scrollLeft + (Number.isFinite(anchorClientX) && anchorClientX >= rect.left
      ? anchorClientX - rect.left : rect.width / 2);
    anchorTime = Math.max(0, Math.min(total, (px - TL_AXIS_OFFSET) / inner * total));
  }
  ED.zoom = next;
  const label = document.getElementById('tl-zoom-value');
  if (label) label.textContent = Math.round(next * 100) + '%';
  editorRenderTimeline();
  // 新 scale 下把锚点时间放回指针位置
  const live = document.querySelector('#editor-timeline .ed-tl-scroll');
  if (live) {
    const rect2 = live.getBoundingClientRect();
    const inner2 = Math.max(1, (rect2.width - TL_AXIS_OFFSET) * next);
    const anchorX = Number.isFinite(anchorClientX) && anchorClientX >= rect2.left
      ? anchorClientX - rect2.left : rect2.width / 2;
    live.scrollLeft = Math.max(0, TL_AXIS_OFFSET + anchorTime / total * inner2 - anchorX);
  }
}
window.editorZoomTimeline = editorZoomTimeline;

function _tlBindWheelZoom(){
  const scroll = document.querySelector('#editor-timeline .ed-tl-scroll');
  if (!scroll) return;
  scroll.addEventListener('wheel', ev => {
    // 触控板二分（剪映源码实证）：无修饰键且 angleDelta 非 120 倍数 = 触控板
    // 双指平移 → 交给浏览器原生横向滚动；Ctrl+滚轮 = 缩放（以指针为锚）。
    const deltaX = Math.abs(ev.deltaX || 0), deltaY = Math.abs(ev.deltaY || 0);
    const trackpadPan = !ev.ctrlKey && !ev.metaKey && deltaY !== 0 && deltaY % 100 !== 0;
    if (ev.ctrlKey || ev.metaKey) {
      ev.preventDefault();
      const dir = ev.deltaY < 0 ? 1 : -1;
      editorZoomTimeline(dir, ev.clientX);
      return;
    }
    if (trackpadPan && deltaX === 0) {
      // 触控板双指上下平移在横向时间线上转成水平滚动
      ev.preventDefault();
      scroll.scrollLeft += ev.deltaY;
    }
  }, {passive: false});
}

/** 边缘自动滚动器（剪映 ScrollTimer 模式）：拖拽中指针进入可视边缘
 *  40px 带内 → rAF 循环滚动并持续触发回调重算（拖拽增量随视口移动刷新）。
 *  用法：drag 开始时 startEdgeAutoScroll(onScroll)；pointermove 里 feed
 *  当前 clientX；pointerup/cancel 时 stopEdgeAutoScroll()。 */
const _tlEdgeAutoScroll = {raf: 0, clientX: 0, onScroll: null};
function _tlStartEdgeAutoScroll(onScroll){
  _tlEdgeAutoScroll.onScroll = onScroll;
  if (_tlEdgeAutoScroll.raf) cancelAnimationFrame(_tlEdgeAutoScroll.raf);
  const step = () => {
    const scroll = document.querySelector('#editor-timeline .ed-tl-scroll');
    if (!scroll || !_tlEdgeAutoScroll.onScroll) { _tlEdgeAutoScroll.raf = 0; return; }
    const rect = scroll.getBoundingClientRect();
    const EDGE = 40;
    let dx = 0;
    if (_tlEdgeAutoScroll.clientX < rect.left + EDGE) dx = -Math.min(24, rect.left + EDGE - _tlEdgeAutoScroll.clientX);
    else if (_tlEdgeAutoScroll.clientX > rect.right - EDGE) dx = Math.min(24, _tlEdgeAutoScroll.clientX - (rect.right - EDGE));
    if (dx !== 0) {
      scroll.scrollLeft += dx;
      _tlEdgeAutoScroll.onScroll(dx);
    }
    _tlEdgeAutoScroll.raf = requestAnimationFrame(step);
  };
  _tlEdgeAutoScroll.raf = requestAnimationFrame(step);
}
function _tlFeedEdgeAutoScroll(clientX){ _tlEdgeAutoScroll.clientX = clientX; }
function _tlStopEdgeAutoScroll(){
  _tlEdgeAutoScroll.onScroll = null;
  if (_tlEdgeAutoScroll.raf) { cancelAnimationFrame(_tlEdgeAutoScroll.raf); _tlEdgeAutoScroll.raf = 0; }
}

/** §17 层直接操作：拖动重排（跨场景）+ 边缘 Trim（层内裁剪）。
 *  拖动全程 Local（DOM 类标记），释放才 Command → Revision。 */
let _layerDrag = null;
function _tlBindSceneDrag(){
  document.querySelectorAll('#editor-timeline .ed-scene-header').forEach(el => {
    el.addEventListener('pointerdown', ev => {
      // pointerdown 阶段必须 preventDefault：浏览器在 mousedown 后移动会启动
      // 文本选区拖拽（.ed-scene-title 是文本）/ <img> 原生拖拽（缩略图），
      // dragstart 一触发 pointerup 就被 pointercancel 顶掉——场景拖动排序
      // 在缩略图/标题落点全部失效（2026-10-09 Playwright 实测事件流：
      // pointerdown → dragstart → pointercancel → drag×N → dragend，
      // editorCmd('move') 永不触发）。move 阶段 preventDefault 已来不及。
      if (ev.button !== 0) return;
      ev.preventDefault();
      // setPointerCapture：鼠标移出 header 后 pointermove/up 仍派发给本元素
      // ——否则拖到目标场景位置时 pointerup 打在别的 header 上，本元素的
      // 监听器收不到，move 永不提交（同日实测：状态机 moved=true 但零请求）。
      try { el.setPointerCapture(ev.pointerId); } catch(e) { /* 老浏览器降级 */ }
      el._tlSceneDrag = { sceneId: el.closest('.ed-scene') && el.closest('.ed-scene').dataset.scene, startX: ev.clientX, moved: false };
    });
    el.addEventListener('pointermove', ev => {
      const d = el._tlSceneDrag;
      if (!d || Math.abs(ev.clientX - d.startX) <= 8) return;
      d.moved = true; ev.preventDefault();
      const scene = el.closest('.ed-scene'); if (scene) scene.classList.add('tl-dragging');
    });
    el.addEventListener('pointerup', ev => {
      const d = el._tlSceneDrag; el._tlSceneDrag = null;
      const src = el.closest('.ed-scene');
      if (!d || !d.moved || !src) return;
      ev.preventDefault(); src.classList.remove('tl-dragging');
      const target = [...document.querySelectorAll('#editor-timeline .ed-scene')].find(scEl => {
        const r = scEl.getBoundingClientRect(); return ev.clientX >= r.left && ev.clientX <= r.right;
      });
      if (!target || target.dataset.scene === d.sceneId) return;
      const toIdx = ED.comp.scenes.findIndex(sc => sc.id === target.dataset.scene);
      if (toIdx >= 0) editorCmd('move', {toIndex: toIdx}, d.sceneId, true);
    });
    el.addEventListener('pointercancel', () => {
      // 拖拽被系统手势打断时清状态，避免残留 moved 导致后续误提交
      el._tlSceneDrag = null;
      const scene = el.closest('.ed-scene');
      if (scene) scene.classList.remove('tl-dragging');
    });
  });
}
function _tlBindLayerDrag(){
  document.querySelectorAll('#editor-timeline .ed-layer').forEach(el => {
    el.addEventListener('pointerdown', ev => {
      if (ev.button !== 0) return;
      const scene = ED.comp && ED.comp.scenes.find(item => item.id === el.dataset.scene);
      const layer = scene && scene.layers[parseInt(el.dataset.idx, 10)];
      if (!layer || layer.locked) return;
      // Layer bars can contain thumbnails/text. Prevent Chromium's native
      // image/text drag from issuing pointercancel before our cross-track drop,
      // and capture the pointer so pointerup still reaches the editor after the
      // pointer leaves the source layer.
      ev.preventDefault();
      try { el.setPointerCapture(ev.pointerId); } catch (_) {}
      _layerDrag = {el, sceneId: el.dataset.scene, idx: parseInt(el.dataset.idx),
                    startX: ev.clientX, startY: ev.clientY, moved: false, mode: null};
      // §17 边缘 Trim：按下点在层带左/右 8px → trim 手势；中间 → move 手势
      const r = el.getBoundingClientRect();
      // Fixed 8px edge zones consumed the entire hit area of short clips at low
      // zoom, so a center drag was misclassified as a trim and never reordered.
      // Keep edge handles proportional to the clip width; sub-8px clips remain
      // movable by pointer and can be trimmed precisely in the clip inspector.
      const edge = Math.min(8, r.width * 0.25);
      if (r.width >= 8 && ev.clientX - r.left <= edge) _layerDrag.mode = 'trim-left';
      else if (r.width >= 8 && r.right - ev.clientX <= edge) _layerDrag.mode = 'trim-right';
      else _layerDrag.mode = 'move';
    });
  });
  // The timeline DOM is rebuilt after commands and preview refreshes. Bind these
  // document-level handlers once; per-render listeners otherwise pile up.
  if (window.__tlLayerDragHandlers) return;
  window.__tlLayerDragHandlers = true;
  document.addEventListener('pointermove', ev => {
    if (!_layerDrag || !_layerDrag.el.isConnected) { _layerDrag = null; return; }
    const dx = ev.clientX - _layerDrag.startX;
    const dy = ev.clientY - (_layerDrag.startY ?? ev.clientY);
    // A vertical drop onto a global visual track is stronger intent than the
    // horizontal edge-trim affordance. Small clips can be narrower than the
    // 8px trim hit-zone, so relying on the initial pointerdown mode makes them
    // impossible to lift by dragging. Promote the gesture to "move" as soon as
    // the pointer enters a track after meaningful vertical movement.
    const hover = document.elementFromPoint(ev.clientX, ev.clientY);
    const targetTrack = hover && hover.closest
      ? hover.closest('#editor-timeline .ed-track[data-visual-track-id]') : null;
    if (targetTrack && Math.abs(dy) > 8) _layerDrag.mode = 'move';
    if (Math.abs(dx) > 8 || (_layerDrag.mode === 'move' && Math.abs(dy) > 8)) {
      _layerDrag.moved = true;
      _layerDrag.el.classList.add('tl-dragging');
      if (_layerDrag.mode === 'move') {
        document.querySelectorAll('#editor-timeline .ed-track[data-visual-track-id]').forEach(row => row.classList.remove('tl-track-drop-target'));
        if (targetTrack) targetTrack.classList.add('tl-track-drop-target');
      }
      if (_layerDrag.mode.startsWith('trim')) {
        // Trim Local Preview：层带宽度实时跟随（改 width%）；指示方向
        const lay = (() => { const sc = ED.comp.scenes.find(s => s.id === _layerDrag.sceneId);
                             return sc && sc.layers[_layerDrag.idx]; })();
        if (lay) {
          const total = ED.comp.duration || 1;
          const scStart = ED.comp.scenes.find(s => s.id === _layerDrag.sceneId).start;
          const absDelta = (ev.clientX - _layerDrag.startX) / _tlTimelineAxisWidth() * total;
          if (_layerDrag.mode === 'trim-left') {
            const newIn = Math.max(0, (lay.start || 0) + absDelta);
            _layerDrag.el.title = `裁剪起点 → 场景内 ${newIn.toFixed(1)}s`;
          } else {
            const newOut = Math.max(0.5, lay.duration - absDelta);
            _layerDrag.el.title = `层时长 → ${newOut.toFixed(1)}s`;
          }
        }
      }
    }
  });
  document.addEventListener('pointercancel', () => {
    if (_layerDrag && _layerDrag.el) _layerDrag.el.classList.remove('tl-dragging');
    _layerDrag = null;
    document.querySelectorAll('#editor-timeline .ed-track[data-visual-track-id]')
      .forEach(row => row.classList.remove('tl-track-drop-target'));
  });
  document.addEventListener('pointerup', ev => {
    if (!_layerDrag) {
      window.__lastLayerDragDebug = {stage: 'pointerup-no-active-drag', x: ev.clientX, y: ev.clientY};
      return;
    }
    const d = _layerDrag;
    _layerDrag = null;
    const dragDebug = window.__lastLayerDragDebug = {
      stage: 'pointerup-start', mode: d.mode, moved: !!d.moved,
      sceneId: d.sceneId, idx: d.idx, startX: d.startX, startY: d.startY,
      dropX: ev.clientX, dropY: ev.clientY, elementConnected: !!(d.el && d.el.isConnected)
    };
    const hit = document.elementFromPoint(ev.clientX, ev.clientY);
    const targetVisualTrack = hit && hit.closest
      ? hit.closest('#editor-timeline .ed-track[data-visual-track-id]') : null;
    // Finalize the gesture from the actual drop target too. This protects against
    // pointermove coalescing and ensures a track drop wins over edge-trim mode.
    if (targetVisualTrack && Math.abs(ev.clientY - (d.startY ?? ev.clientY)) > 8) {
      d.mode = 'move';
      d.moved = true;
    }
    if (!d.moved) { dragDebug.stage = 'pointerup-not-moved'; return; }  // 纯点击走 onclick
    d.el.classList.remove('tl-dragging');
    document.querySelectorAll('#editor-timeline .ed-track[data-visual-track-id]').forEach(row => row.classList.remove('tl-track-drop-target'));
    const lay = (() => { const sc = ED.comp.scenes.find(s => s.id === d.sceneId);
                         return sc && sc.layers[d.idx]; })();
    if (!lay) { dragDebug.stage = 'missing-layer'; return; }
    const scrollW = _tlTimelineAxisWidth();
    const total = ED.comp.duration || 1;
    const absDelta = (ev.clientX - d.startX) / scrollW * total;
    if (d.mode === 'trim-left' || d.mode === 'trim-right') {
      // §17 Trim 释放 → trim Command（源区间）→ Revision
      // 源区间：source_in 起；source_out 缺省时按层时长×速度推
      const speed = lay.speed || 1;
      const srcIn0 = lay.source_in || 0;
      const srcOut0 = lay.source_out || (srcIn0 + lay.duration * speed);
      let srcIn = srcIn0, srcOut = srcOut0;
      if (d.mode === 'trim-left') {
        srcIn = Math.max(0, Math.min(srcOut0 - 0.5, srcIn0 + absDelta * speed));
      } else {
        // Right-edge drag follows pointer direction: left shortens; right extends.
        // Clamp to known source duration so we never persist an unrenderable range.
        const assets = ED.comp.assets || [];
        const asset = Array.isArray(assets)
          ? assets.find(item => item && item.id === lay.asset_id)
          : assets[lay.asset_id];
        const assetDuration = Number(asset && asset.duration);
        const maxSourceOut = Number.isFinite(assetDuration) && assetDuration > 0
          ? assetDuration : Number.POSITIVE_INFINITY;
        srcOut = Math.min(maxSourceOut, Math.max(srcIn0 + 0.5, srcOut0 + absDelta * speed));
      }
      if (!(srcOut > srcIn)) { editorToast('裁剪区间无效，未修改', true); return; }
      editorToast(`正在应用裁剪 ${srcIn.toFixed(1)}s–${srcOut.toFixed(1)}s……`);
      executeCommand({command: 'trim', targetId: `${d.sceneId}#${d.idx}`,
        payload: {
          sourceIn: +srcIn.toFixed(2),
          sourceOut: +srcOut.toFixed(2),
          edge: d.mode === 'trim-left' ? 'left' : 'right'
        }})
        .then(r => {
          editorToast(r.ok ? `已裁剪为源区间 ${srcIn.toFixed(1)}s–${srcOut.toFixed(1)}s`
                           : '裁剪未完成：' + (r.error || ''), !r.ok);
          if (r.ok) refreshWorkspace();
        });
      return;
    }
    // Dragging a normal main-video layer directly into a global visual track is
    // an edit command: the server atomically creates the overlay clip and leaves a
    // renderable gap in the original scene. X determines global start; Y selects track.
    if (d.mode === 'move' && targetVisualTrack) {
      const trackBody = targetVisualTrack.querySelector('.ed-track-body');
      const rect = trackBody && trackBody.getBoundingClientRect();
      if (!rect || rect.width <= 0) { dragDebug.stage = 'invalid-visual-track-rect'; editorRenderTimeline(); return; }
      const start = Math.round(Math.max(0, Math.min(total - lay.duration,
        ((ev.clientX - rect.left) / rect.width) * total)) * 1000) / 1000;
      const trackId = String(targetVisualTrack.dataset.visualTrackId || '');
      editorToast('正在将主画面片段移入画面轨道……');
      executeCommand({
        command: 'promote_layer_to_visual_track',
        targetId: `${d.sceneId}#${d.idx}`,
        payload: {trackId, start}
      }).then(result => {
        editorToast(result && result.ok
          ? `片段已放入画面轨道，位置 ${start.toFixed(2)}s；原位置保留可编辑空隙`
          : '片段未移动：' + ((result && result.error) || '请检查片段效果与时间范围'), !(result && result.ok));
        if (result && result.ok) return refreshWorkspace();
        editorRenderTimeline();
      }).catch(error => {
        editorToast('片段未移动：' + (error.message || error), true);
        editorRenderTimeline();
      });
      return;
    }
    // A layer drag must never invoke the scene-reorder command.
    // Same-scene drop reorders visual segments; cross-scene drop without a track is rejected.
    const targetScene = hit && hit.closest ? hit.closest('#editor-timeline .ed-scene') : null;
    const targetLayer = hit && hit.closest ? hit.closest('#editor-timeline .ed-layer') : null;
    dragDebug.hitClass = hit ? String(hit.className || hit.tagName) : null;
    dragDebug.targetSceneId = targetScene ? targetScene.dataset.scene : null;
    dragDebug.targetLayerIdx = targetLayer ? targetLayer.dataset.idx : null;
    if (targetScene && targetScene.dataset.scene === d.sceneId) {
      const scene = ED.comp.scenes.find(s => s.id === d.sceneId);
      if (!scene || !Array.isArray(scene.layers) || !scene.layers.length) { dragDebug.stage = 'invalid-scene-layers'; return; }
      dragDebug.sceneDuration = Number(scene.duration || 0);
      dragDebug.layers = scene.layers.map((layer, index) => ({
        index, start: Number(layer.start || 0), duration: Number(layer.duration || 0), type: layer.type
      }));
      const dx = ev.clientX - d.startX;
      const dy = ev.clientY - (d.startY ?? ev.clientY);
      const sceneBody = targetScene.querySelector('.ed-scene-body');
      const bodyRect = sceneBody && sceneBody.getBoundingClientRect();
      // The main picture row is a time target, not only a set of existing clip
      // hitboxes. Users must be able to drop at the requested time even when the
      // pointer lands on padding/empty lane space rather than another clip element.
      // Vertical drops retain before/after semantics and therefore need a target clip.
      const horizontalTimeDrop = d.mode === 'move' && Math.abs(dx) > 8
        && Math.abs(dy) <= Math.max(12, d.el.getBoundingClientRect().height * 0.5)
        && bodyRect && bodyRect.width > 0;
      dragDebug.dx = dx;
      dragDebug.dy = dy;
      dragDebug.horizontalTimeDrop = !!horizontalTimeDrop;
      dragDebug.bodyRect = bodyRect ? {
        left: bodyRect.left, right: bodyRect.right, width: bodyRect.width
      } : null;
      dragDebug.stage = 'same-scene-target-resolved';
      let toIndex;
      if (horizontalTimeDrop) {
        const ratio = Math.max(0, Math.min(1, (ev.clientX - bodyRect.left) / bodyRect.width));
        const targetLocalTime = ratio * Number(scene.duration || 0);
        let timeTargetIndex = scene.layers.findIndex(layer =>
          targetLocalTime < Number(layer.start || 0) + Number(layer.duration || 0) / 2);
        if (timeTargetIndex < 0) timeTargetIndex = scene.layers.length - 1;
        const timeTarget = scene.layers[timeTargetIndex];
        const afterTarget = targetLocalTime >= Number(timeTarget.start || 0) + Number(timeTarget.duration || 0) / 2;
        toIndex = timeTargetIndex + (afterTarget ? 1 : 0);
        if (d.idx < toIndex) toIndex -= 1;
      } else {
        if (!targetLayer || targetLayer.dataset.scene !== d.sceneId) return;
        const rect = targetLayer.getBoundingClientRect();
        const targetIdx = Number.parseInt(targetLayer.dataset.idx, 10);
        toIndex = targetIdx + (ev.clientY > rect.top + rect.height / 2 ? 1 : 0);
        if (d.idx < toIndex) toIndex -= 1;
      }
      toIndex = Math.max(0, Math.min(toIndex, scene.layers.length - 1));
      dragDebug.toIndex = toIndex;
      dragDebug.stage = 'reorder-index-calculated';
      if (toIndex === d.idx) { dragDebug.stage = 'reorder-noop-same-index'; return; }
      dragDebug.stage = 'reorder-command-issued';
      editorToast(horizontalTimeDrop ? '正在按时间位置重排片段（后续片段随之移位）……' : '正在调整片段顺序……');
      editorCmd('reorder_layer', {toIndex}, `${d.sceneId}#${d.idx}`, true)
        .then(r => editorToast(r && r.ok ? '片段顺序已更新' : '片段顺序未更改：' + ((r && r.error) || '请检查片段'), !(r && r.ok)));
      return;
    }
    if (targetScene && targetScene.dataset.scene !== d.sceneId) {
      // Cross-scene drops are real edits too. Lift a compatible video/still to a
      // full-frame global visual track at the pointer's absolute time; the source
      // scene receives an explicit gap, so neither scene duration nor audio anchors
      // are silently rewritten. Unsupported effects are rejected by the backend.
      const sourceScene = ED.comp.scenes.find(scene => scene.id === d.sceneId);
      if (!sourceScene || d.mode !== 'move') {
        dragDebug.stage = 'cross-scene-drop-not-liftable';
        editorToast('未移动：请从片段中间拖动；边缘裁剪请在原分镜内完成。', true);
        return;
      }
      const desiredStart = Math.max(0, Math.min(
        total - Number(lay.duration || 0),
        Number(sourceScene.start || 0) + Number(lay.start || 0) + absDelta
      ));
      // 统一编辑切点契约：跨场景提升起点也落到项目帧钟（后端会再 snap 兜底）。
      const start = typeof editorProjectFrameTime === 'function'
        ? editorProjectFrameTime(Math.max(0, desiredStart))
        : Math.round(desiredStart * 1000) / 1000;
      dragDebug.stage = 'cross-scene-lift-issued';
      dragDebug.crossSceneTarget = targetScene.dataset.scene;
      dragDebug.targetStart = start;
      editorToast('正在将片段移入全局画面轨道……');
      executeCommand({
        command: 'promote_layer_to_visual_track',
        targetId: `${d.sceneId}#${d.idx}`,
        payload: {start}
      }).then(result => {
        if (result && result.ok) {
          editorToast(`片段已移入独立画面轨道，位置 ${start.toFixed(2)}s；原位置保留空隙`);
          return refreshWorkspace();
        }
        editorToast('片段未移动：' + ((result && result.error) || '请检查片段效果与时间范围'), true);
        editorRenderTimeline();
      }).catch(error => {
        editorToast('片段未移动：' + (error.message || error), true);
        editorRenderTimeline();
      });
    } else {
      dragDebug.stage = 'no-same-scene-reorder-branch';
    }
  }, true); // Claim pointerup in capture phase before higher-level timeline gestures can consume it.
}

// ---------------------------------------------------------------------------
// §17 音频片段直接操作：拖动移动、左右边缘裁剪。
// 所有持久化修改仍经 Editor Command / Revision；拖动期间只改当前 DOM 预览。
let _audioClipDrag = null;
let _audioClipSuppressClick = null;

const _AUDIO_CLIP_CN = {
  narration: '旁白', dialogue: '对白', original: '原声',
  bgm: '背景音乐', ambience: '环境音', ambient: '环境音', sfx: '音效'
};

function editorAudioClipClose(){
  const mask = document.getElementById('audio-clip-inspector-mask');
  if (mask) mask.remove();
}

function editorAudioClipOpen(index, ev){
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  const clip = ED.comp && (ED.comp.audio || [])[index];
  if (!clip) { editorToast('音频片段已变化，请重新选择', true); return; }
  editorAudioClipClose();
  const type = String(clip.type || '');
  const asset = clip.asset_id && ED.comp.assets && ED.comp.assets[clip.asset_id];
  const assetName = asset && (asset.name || (asset.file && asset.file.split(/[\\/]/).pop()))
    || clip.asset_id || '未绑定素材';
  const duration = Math.max(0.05, Number(clip.duration) || 0.05);
  const fmt = value => Number(value || 0).toFixed(2);
  const splitAt = Number(PLAYHEAD && PLAYHEAD.at) || 0;
  const splitStart = Number(clip.start) || 0;
  const splitEnd = splitStart + duration;
  const loopedBackground = (type === 'bgm' || type === 'ambience' || type === 'ambient')
    && clip.source_out == null;
  const narrationUnsupported = type === 'narration';
  const automatedClip = Array.isArray(clip.keyframes) && clip.keyframes.length > 0;
  const canSplitAtPlayhead = splitAt > splitStart + 0.05 && splitAt < splitEnd - 0.05
    && !loopedBackground && !narrationUnsupported && !automatedClip;
  const splitHint = narrationUnsupported
    ? '旁白由独立渲染链路处理，当前版本暂不能切分'
    : (loopedBackground ? '循环背景音需先裁出明确源区间'
      : (automatedClip ? '带关键帧的音频片段暂不能切分' :
        (canSplitAtPlayhead ? '左右两段各保留至少 0.05 秒' : '请先将播放头放在片段内部，再打开此设置')));
  const mask = document.createElement('div');
  mask.id = 'audio-clip-inspector-mask';
  mask.className = 'audio-clip-inspector-mask';
  mask.innerHTML = `
    <section class="audio-clip-inspector" role="dialog" aria-modal="true" aria-labelledby="audio-clip-inspector-title">
      <header>
        <div>
          <b id="audio-clip-inspector-title">${esc(_AUDIO_CLIP_CN[type] || type)} · 片段设置</b>
          <div class="hint">${esc(assetName)} · ${fmt(clip.start)}–${fmt(Number(clip.start || 0) + duration)}s</div>
        </div>
        <button type="button" class="btn-secondary btn-sm" onclick="editorAudioClipClose()" aria-label="关闭音频片段设置">关闭</button>
      </header>
      <div class="audio-clip-split-row">
        <button type="button" id="audio-clip-split-at-playhead" class="btn-secondary btn-sm"
          onclick="editorAudioClipSplit(${index})" ${canSplitAtPlayhead ? '' : 'disabled'}>
          在播放头处分割 · ${fmt(splitAt)}s
        </button>
        <span class="hint">${esc(splitHint)}</span>
      </div>
      <label for="audio-clip-volume">音量（0–200%）</label>
      <div class="audio-clip-field-row">
        <input id="audio-clip-volume" type="number" min="0" max="2" step="0.05" value="${Number(clip.volume ?? 1)}">
        <span class="hint">当前 ${Math.round(Number(clip.volume ?? 1) * 100)}%</span>
      </div>
      <label for="audio-clip-fade-in">淡入（秒）</label>
      <input id="audio-clip-fade-in" type="number" min="0" max="${duration}" step="0.05" value="${Number(clip.fadeIn ?? clip.fade_in ?? 0)}">
      <label for="audio-clip-fade-out">淡出（秒）</label>
      <input id="audio-clip-fade-out" type="number" min="0" max="${duration}" step="0.05" value="${Number(clip.fadeOut ?? clip.fade_out ?? 0)}">
      <label class="audio-clip-check"><input id="audio-clip-muted" type="checkbox" ${clip.muted ? 'checked' : ''}> 静音此片段</label>
      ${type === 'original' ? `<label class="audio-clip-check"><input id="audio-clip-ducking" type="checkbox" ${clip.ducking ? 'checked' : ''}> 旁白播放时压低原声</label>` : ''}
      <footer>
        <button type="button" class="btn-secondary" onclick="editorAudioClipClose()">取消</button>
        <button type="button" class="btn-primary" onclick="editorAudioClipApply(${index})">保存片段设置</button>
      </footer>
    </section>`;
  mask.addEventListener('click', event => {
    if (event.target === mask) editorAudioClipClose();
  });
  document.body.appendChild(mask);
  const first = mask.querySelector('#audio-clip-volume');
  if (first) first.focus();
}

async function editorAudioClipSplit(index){
  const clip = ED.comp && (ED.comp.audio || [])[index];
  if (!clip) { editorAudioClipClose(); editorToast('音频片段已变化，请重新打开', true); return; }
  const requestedAt = Number(PLAYHEAD && PLAYHEAD.at);
  const at = Number.isFinite(requestedAt) ? editorSnapPlayheadToFrame(requestedAt) : requestedAt;
  const start = Number(clip.start) || 0;
  const end = start + (Number(clip.duration) || 0);
  if (!Number.isFinite(at) || at <= start + 0.05 || at >= end - 0.05) {
    editorToast('请先把播放头放在片段内部（两侧至少保留 0.05 秒）再切分', true);
    return;
  }
  const type = String(clip.type || '');
  if (type === 'narration') {
    editorToast('旁白由独立渲染链路处理，当前版本暂不能切分', true);
    return;
  }
  if ((type === 'bgm' || type === 'ambience' || type === 'ambient') && clip.source_out == null) {
    editorToast('循环背景音需先裁出明确源区间，再进行切分', true);
    return;
  }
  if (Array.isArray(clip.keyframes) && clip.keyframes.length) {
    editorToast('该音频片段带有关键帧自动化，暂不能切分以免丢失曲线', true);
    return;
  }
  try {
    const result = await executeCommand({
      command: 'split_audio_clip',
      targetId: 'audio#' + index,
      payload: {at},
    });
    if (!result || !result.ok) {
      editorToast('音频片段未切分：' + ((result && result.error) || '未知错误'), true);
      return;
    }
    editorAudioClipClose();
    editorToast('音频片段已在 ' + at.toFixed(2) + 's 切分');
    await refreshWorkspace();
  } catch (error) {
    editorToast('音频片段未切分：' + (error.message || error), true);
  }
}

async function editorAudioClipApply(index){
  const mask = document.getElementById('audio-clip-inspector-mask');
  const clip = ED.comp && (ED.comp.audio || [])[index];
  if (!mask || !clip) { editorAudioClipClose(); editorToast('音频片段已变化，请重新打开', true); return; }
  const number = id => {
    const node = mask.querySelector('#' + id);
    return node ? Number(node.value) : NaN;
  };
  const volume = number('audio-clip-volume');
  const fadeIn = number('audio-clip-fade-in');
  const fadeOut = number('audio-clip-fade-out');
  if (![volume, fadeIn, fadeOut].every(Number.isFinite)
      || volume < 0 || volume > 2 || fadeIn < 0 || fadeOut < 0
      || fadeIn > clip.duration || fadeOut > clip.duration) {
    editorToast('参数无效：音量须为 0–200%，淡入/淡出不能超过片段时长', true);
    return;
  }
  const payload = {
    volume, fadeIn, fadeOut,
    muted: !!mask.querySelector('#audio-clip-muted').checked,
  };
  const duck = mask.querySelector('#audio-clip-ducking');
  if (duck) payload.ducking = !!duck.checked;
  try {
    editorToast('正在保存音频片段设置……');
    const result = await executeCommand({
      command: 'set_audio_clip_properties',
      targetId: 'audio#' + index,
      payload,
    });
    if (!result || !result.ok) {
      editorToast('音频片段设置未保存：' + ((result && result.error) || '未知错误'), true);
      return;
    }
    editorAudioClipClose();
    editorToast('音频片段设置已保存');
    await refreshWorkspace();
  } catch (error) {
    editorToast('音频片段设置未保存：' + (error.message || error), true);
  }
}

function editorAudioTrackMenuClose(ev){
  if (ev) ev.stopPropagation();
  const mask = document.getElementById('audio-track-inspector-mask');
  if (mask) mask.remove();
}

async function editorAudioTrackToggleLock(ev, trackId) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  const lane = (ED.comp && ED.comp.audio_tracks || []).find(item => String(item.id) === String(trackId));
  if (!lane) { editorToast('音轨已变化，请刷新后重试', true); return; }
  const locked = !Boolean(lane.locked);
  try {
    const result = await executeCommand({command:'set_track_locked', targetId:trackId, payload:{trackType:'audio', locked}});
    if (!result || !result.ok) { editorToast('音轨锁定状态未修改：' + ((result && result.error) || '未知错误'), true); return; }
    await refreshWorkspace();
    editorToast(locked ? '音轨已锁定：全轨切分、拖动和修剪会跳过该轨' : '音轨已解锁：可继续编辑');
  } catch (error) { editorToast('音轨锁定状态未修改：' + (error.message || error), true); }
}

async function editorAudioTrackAdd(ev){
  if (ev) ev.stopPropagation();
  if (!ED.pid || !ED.comp) { editorToast('先打开项目'); return; }
  const lanes = _timelineAudioTracks(ED.comp);
  const used = new Set(lanes.map(lane => String(lane.name || '').trim().toLocaleLowerCase()));
  let number = 1;
  while (used.has(('音轨 ' + number).toLocaleLowerCase())) number += 1;
  try {
    const result = await executeCommand({command:'add_audio_track', targetId:'audio-tracks', payload:{name:'音轨 ' + number}});
    if (!result || !result.ok) { editorToast('新增音轨失败：' + ((result && result.error) || '未知错误'), true); return; }
    await refreshWorkspace();
    editorToast('已新增音轨 ' + number);
  } catch (error) { editorToast('新增音轨失败：' + (error.message || error), true); }
}

function editorAudioTrackMenu(ev, trackId){
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  if (!ED.comp) return;
  editorAudioTrackMenuClose();
  const lane = _timelineAudioTracks(ED.comp).find(item => item.id === trackId);
  if (!lane) { editorToast('音轨已变化，请刷新后重试', true); return; }
  const clipCount = (ED.comp.audio || []).filter(clip => clip.track_id === trackId).length;
  const mask = document.createElement('div');
  mask.id = 'audio-track-inspector-mask';
  mask.className = 'audio-track-inspector-mask';
  mask.innerHTML = '<section class="audio-track-inspector" role="dialog" aria-modal="true" aria-label="音轨设置">' +
    '<header><b>音轨设置</b><button type="button" class="btn-secondary btn-sm" onclick="editorAudioTrackMenuClose(event)">关闭</button></header>' +
    '<label for="audio-track-name">名称</label>' +
    '<input id="audio-track-name" type="text" maxlength="60" value="' + esc(lane.name || '') + '" autocomplete="off">' +
    '<p class="hint">此轨道有 ' + clipCount + ' 个片段。移动片段只改变轨道归属，不改变 BGM、SFX、原声等混音角色。</p>' +
    '<footer><button type="button" class="btn-secondary btn-sm" ' + (clipCount ? 'disabled title="请先移动或删除片段"' : '') + ' onclick="editorAudioTrackRemove(\'' + esc(trackId) + '\', event)">删除空轨道</button>' +
    '<button type="button" class="btn-primary btn-sm" onclick="editorAudioTrackApplyName(\'' + esc(trackId) + '\', event)">保存名称</button></footer></section>';
  mask.addEventListener('click', event => { if (event.target === mask) editorAudioTrackMenuClose(); });
  document.body.appendChild(mask);
  const input = mask.querySelector('#audio-track-name');
  if (input) { input.focus(); input.select(); }
}

async function editorAudioTrackApplyName(trackId, ev){
  if (ev) ev.stopPropagation();
  const input = document.querySelector('#audio-track-name');
  const name = input ? input.value.trim() : '';
  if (!name || name.length > 60) { editorToast('音轨名称必须为 1–60 个字符', true); return; }
  try {
    const result = await executeCommand({command:'rename_audio_track', targetId:trackId, payload:{name}});
    if (!result || !result.ok) { editorToast('音轨名称未保存：' + ((result && result.error) || '未知错误'), true); return; }
    editorAudioTrackMenuClose();
    await refreshWorkspace();
    editorToast('音轨名称已保存');
  } catch (error) { editorToast('音轨名称未保存：' + (error.message || error), true); }
}

async function editorAudioTrackRemove(trackId, ev){
  if (ev) ev.stopPropagation();
  const clips = (ED.comp && ED.comp.audio || []).filter(clip => clip.track_id === trackId);
  if (clips.length) { editorToast('该音轨仍有片段，请先移动或删除片段', true); return; }
  try {
    const result = await executeCommand({command:'remove_audio_track', targetId:trackId, payload:{}});
    if (!result || !result.ok) { editorToast('删除音轨失败：' + ((result && result.error) || '未知错误'), true); return; }
    editorAudioTrackMenuClose();
    await refreshWorkspace();
    editorToast('空音轨已删除');
  } catch (error) { editorToast('删除音轨失败：' + (error.message || error), true); }
}

function _tlBindAudioClipDrag(){
  document.querySelectorAll('#editor-timeline .ed-audio-clip[data-audio-index]').forEach(el => {
    el.addEventListener('pointerdown', ev => {
      if (ev.button !== 0 || !ED.comp) return;
      const index = Number.parseInt(el.dataset.audioIndex, 10);
      const track = (ED.comp.audio || [])[index];
      if (!track) return;
      const row = el.closest('.ed-track[data-audio-track-id]');
      const laneId = String(track.track_id || (row && row.dataset.audioTrackId) || '');
      const lane = (ED.comp.audio_tracks || []).find(item => String(item.id) === laneId);
      if (lane && lane.locked) { ev.stopPropagation(); editorToast('音轨已锁定，先解锁后才能移动或修剪片段。', true); return; }
      const rect = el.getBoundingClientRect();
      const edge = Math.min(8, rect.width / 4);
      const mode = ev.clientX - rect.left <= edge ? 'trim-left'
        : rect.right - ev.clientX <= edge ? 'trim-right' : 'move';
      _audioClipDrag = {
        el, index, mode, startX: ev.clientX, moved: false,
        start: Number(track.start) || 0,
        duration: Number(track.duration) || 0,
        sourceIn: Number(track.source_in) || 0,
        // A missing source_out means a loop-to-duration track (typically BGM).
        // First trim materializes its current window as an explicit source range.
        sourceOut: track.source_out == null
          ? (Number(track.source_in) || 0) + (Number(track.duration) || 0)
          : Number(track.source_out),
        startTrackId: String(track.track_id || (((el.closest('.ed-track[data-audio-track-id]') || {}).dataset || {}).audioTrackId) || ''),
        baseLeft: Number.parseFloat(el.style.left) || 0,
        baseWidth: Number.parseFloat(el.style.width) || 0,
      };
      try { el.setPointerCapture(ev.pointerId); } catch (_) {}
    });
  });
  if (window.__tlAudioDragHandlers) return;
  window.__tlAudioDragHandlers = true;

  // Suppress the click handler only for the clip just dragged; a plain click still
  // opens that track's existing inspector. A new pointerdown clears stale suppression.
  document.addEventListener('pointerdown', () => {
    if (!_audioClipDrag) _audioClipSuppressClick = null;
  }, true);
  document.addEventListener('click', ev => {
    if (!_audioClipSuppressClick) return;
    const hit = ev.target && ev.target.closest ? ev.target.closest('.ed-audio-clip') : null;
    if (hit === _audioClipSuppressClick) {
      ev.preventDefault();
      ev.stopImmediatePropagation();
      _audioClipSuppressClick = null;
    }
  }, true);

  document.addEventListener('pointermove', ev => {
    const d = _audioClipDrag;
    if (!d || !d.el.isConnected) { _audioClipDrag = null; return; }
    _tlFeedEdgeAutoScroll(ev.clientX);
    const dx = ev.clientX - d.startX;
    if (Math.abs(dx) <= 8 && !d.moved) return;
    d.moved = true;
    if (!_tlEdgeAutoScroll.onScroll) {
      // 边缘自动滚动（剪映 ScrollTimer）：指针停在边缘带内时滚动继续推进拖拽。
      d._virtualX = ev.clientX;
      _tlStartEdgeAutoScroll((dxStep) => {
        const dd = _audioClipDrag;
        if (!dd) return;
        dd._virtualX = (dd._virtualX || dd.startX) + dxStep;
        const body = dd.el.closest('.ed-track-body');
        const width = body ? body.getBoundingClientRect().width : 0;
        if (width <= 0) return;
        const deltaPct = (dd._virtualX - dd.startX) / width * 100;
        if (dd.mode === 'move') dd.el.style.left = (dd.baseLeft + deltaPct) + '%';
        else if (dd.mode === 'trim-left') {
          dd.el.style.left = (dd.baseLeft + deltaPct) + '%';
          dd.el.style.width = Math.max(0.1, dd.baseWidth - deltaPct) + '%';
        } else {
          dd.el.style.width = Math.max(0.1, dd.baseWidth + deltaPct) + '%';
        }
      });
    }
    d.el.classList.add('tl-dragging');
    const body = d.el.closest('.ed-track-body');
    const width = body ? body.getBoundingClientRect().width : 0;
    if (width <= 0) return;
    const deltaPct = dx / width * 100;
    const minPct = Math.max(0.1, 0.05 / Math.max(0.01, ED.comp.duration || 1) * 100);
    if (d.mode === 'move') {
      d.el.style.left = (d.baseLeft + deltaPct) + '%';
    } else if (d.mode === 'trim-left') {
      d.el.style.left = (d.baseLeft + deltaPct) + '%';
      d.el.style.width = Math.max(minPct, d.baseWidth - deltaPct) + '%';
    } else {
      d.el.style.width = Math.max(minPct, d.baseWidth + deltaPct) + '%';
    }
    d.el.title = d.mode === 'move' ? '松开以移动音频片段'
      : (d.mode === 'trim-left' ? '松开以裁剪音频源起点' : '松开以裁剪音频源终点');
  }, true); // capture before EditorSession's box-select handlers can consume the pointer stream

  document.addEventListener('pointerup', ev => {
    const d = _audioClipDrag;
    if (!d) return;
    _audioClipDrag = null;
    _tlStopEdgeAutoScroll();
    d.el.classList.remove('tl-dragging');
    if (!d.moved || !d.el.isConnected || !ED.comp) return;
    _audioClipSuppressClick = d.el;
    const body = d.el.closest('.ed-track-body');
    const width = body ? body.getBoundingClientRect().width : 0;
    if (width <= 0) { editorRenderTimeline(); return; }
    const total = Number(ED.comp.duration) || 1;
    // 边缘自动滚动期间指针没动但视口在动：释放增量按虚拟坐标算，否则
    // 自动滚动推进的部分会被丢弃。
    const releaseX = Number.isFinite(d._virtualX) ? d._virtualX : ev.clientX;
    const delta = (releaseX - d.startX) / width * total;
    // 统一编辑切点契约：音频拖拽/裁剪落到项目帧钟；素材源区间保持毫秒精度，
    // 由后端按帧钟对齐输出时长后反推 source_out。
    const round = value => (typeof editorProjectFrameTime === 'function')
      ? editorProjectFrameTime(Math.max(0, value))
      : Math.round(value * 1000) / 1000;
    const roundMs = value => Math.round(value * 1000) / 1000;
    let command, payload;
    if (d.mode === 'move') {
      command = 'move_audio_clip';
      const hit = Number.isFinite(ev.clientX) && Number.isFinite(ev.clientY)
        ? document.elementFromPoint(ev.clientX, ev.clientY) : null;
      const targetRow = hit && hit.closest ? hit.closest('.ed-track[data-audio-track-id]') : null;
      const targetTrackId = targetRow ? String(targetRow.dataset.audioTrackId || '') : d.startTrackId;
      // Keep the clip's full duration inside the project; the server enforces the same invariant.
      let nextStart = Math.max(0, Math.min(Math.max(0, total - d.duration), d.start + delta));
      // 磁贴吸附（A3）：拖拽落点对齐邻块边界/播放头（Shift 旁路）。
      if (window.editorSessionSnapTime && !ev.shiftKey) {
        const snapped = window.editorSessionSnapTime(nextStart, {shiftKey: !!ev.shiftKey});
        if (Number.isFinite(snapped)) {
          nextStart = Math.max(0, Math.min(Math.max(0, total - d.duration), snapped));
        }
      }
      payload = {start: round(nextStart)};
      if (targetTrackId && targetTrackId !== d.startTrackId) payload.trackId = targetTrackId;
    } else if (d.mode === 'trim-left') {
      const dt = Math.min(Math.max(delta, -Math.min(d.start, d.sourceIn)), d.duration - 0.05);
      const sourceIn = d.sourceIn + dt;
      const sourceOut = d.sourceOut;
      const start = d.start + dt;
      if (sourceOut - sourceIn < 0.05) {
        editorToast('音频裁剪后不足 0.05 秒，未修改', true);
        editorRenderTimeline();
        return;
      }
      command = 'trim_audio_clip';
      payload = {start: round(start), sourceIn: roundMs(sourceIn), sourceOut: roundMs(sourceOut)};
    } else {
      // A right trim may shorten freely, but must not extend beyond the project end.
      const dt = Math.max(-(d.duration - 0.05), Math.min(delta, total - d.start - d.duration));
      const sourceIn = d.sourceIn;
      const sourceOut = d.sourceOut + dt;
      if (sourceOut - sourceIn < 0.05) {
        editorToast('音频裁剪后不足 0.05 秒，未修改', true);
        editorRenderTimeline();
        return;
      }
      command = 'trim_audio_clip';
      payload = {start: round(d.start), sourceIn: roundMs(sourceIn), sourceOut: roundMs(sourceOut)};
    }
    editorToast(command === 'move_audio_clip' ? '正在移动音频片段……' : '正在裁剪音频源区间……');
    executeCommand({command, targetId: 'audio#' + d.index, payload})
      .then(result => {
        if (result && result.ok) {
          editorToast(command === 'move_audio_clip' ? '音频片段位置已更新' : '音频源区间已更新');
          return refreshWorkspace();
        }
        editorToast('音频片段未修改：' + ((result && result.error) || '未知错误'), true);
        editorRenderTimeline();
      })
      .catch(error => {
        editorToast('音频片段未修改：' + (error.message || error), true);
        editorRenderTimeline();
      });
  }, true);

  document.addEventListener('pointercancel', () => {
    const d = _audioClipDrag;
    if (!d) return;
    _audioClipDrag = null;
    _tlStopEdgeAutoScroll();
    if (d.el && d.el.isConnected) editorRenderTimeline();
  }, true);
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Subtitle cues share the same CompositionIR timing and revision-safe command path.
let _subtitleCueDrag = null;
let _subtitleCueSuppressClick = null;

function editorSubtitleCueSelect(index, ev) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  const track = ED.comp && ED.comp.subtitle_track;
  const cue = track && Array.isArray(track.cues) ? track.cues[index] : null;
  if (!cue) return;
  if (typeof previewPause === 'function') previewPause();
  const at = Math.max(0, Math.min(Number(ED.comp.duration) || 0, Number(cue.start) || 0));
  if (typeof previewSeek === 'function') previewSeek(at);
  else PLAYHEAD.at = at;
  if (typeof editorToast === 'function') editorToast(`已定位字幕锚点：${String(cue.text || '').slice(0, 20)} · ${at.toFixed(2)}s`);
}

function _tlBindSubtitleCueDrag() {
  document.querySelectorAll('#editor-timeline .ed-subtitle-cue[data-subtitle-cue-index]').forEach(el => {
    el.addEventListener('pointerdown', ev => {
      if (ev.button !== 0 || !ED.comp || !ED.comp.subtitle_track) return;
      const index = Number.parseInt(el.dataset.subtitleCueIndex, 10);
      const track = ED.comp.subtitle_track;
      const cue = (track.cues || [])[index];
      if (!cue) return;
      const start = Number(cue.start), end = Number(cue.end);
      const total = Number(ED.comp.duration) || 1;
      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || end <= 0 || start >= total) return;
      const rect = el.getBoundingClientRect();
      const edge = Math.min(8, rect.width / 4);
      const mode = ev.clientX - rect.left <= edge ? 'trim-left'
        : rect.right - ev.clientX <= edge ? 'trim-right' : 'move';
      _subtitleCueDrag = {el, index, mode, startX: ev.clientX, moved: false, start, end, previewStart: start, previewEnd: end};
      try { el.setPointerCapture(ev.pointerId); } catch (_) {}
    }, true);
  });
  if (window.__tlSubtitleCueDragHandlers) return;
  window.__tlSubtitleCueDragHandlers = true;
  document.addEventListener('pointermove', ev => {
    const d = _subtitleCueDrag;
    if (!d || !d.el.isConnected) { _subtitleCueDrag = null; return; }
    const body = d.el.closest('.ed-track-body');
    const width = body ? body.getBoundingClientRect().width : 0;
    if (width <= 0) return;
    const dx = ev.clientX - d.startX;
    if (Math.abs(dx) <= 8 && !d.moved) return;
    d.moved = true;
    ev.preventDefault();
    d.el.classList.add('tl-dragging');
    const total = Number(ED.comp.duration) || 1;
    const delta = dx / width * total;
    let start = d.start, end = d.end;
    if (d.mode === 'move') {
      const duration = d.end - d.start;
      start = Math.max(0, Math.min(total - duration, d.start + delta));
      end = start + duration;
    } else if (d.mode === 'trim-left') {
      start = Math.max(0, Math.min(d.end - 0.05, d.start + delta));
    } else {
      end = Math.max(d.start + 0.05, Math.min(total, d.end + delta));
    }
    d.previewStart = start;
    d.previewEnd = end;
    d.el.style.left = (start / total * 100) + '%';
    d.el.style.width = Math.max(0, (end - start) / total * 100) + '%';
    d.el.title = d.mode === 'move' ? '松开以移动字幕时间锚点'
      : (d.mode === 'trim-left' ? '松开以修剪字幕起点' : '松开以修剪字幕终点');
  }, true);
  document.addEventListener('pointerup', () => {
    const d = _subtitleCueDrag;
    if (!d) return;
    _subtitleCueDrag = null;
    d.el.classList.remove('tl-dragging');
    if (!d.moved || !d.el.isConnected || !ED.comp || !ED.comp.subtitle_track) return;
    _subtitleCueSuppressClick = d.el;
    const track = ED.comp.subtitle_track;
    const round = value => Math.round(value * 1000) / 1000;
    const start = round(d.previewStart), end = round(d.previewEnd);
    if (end - start < 0.05) {
      editorToast('字幕时间段需至少 0.05 秒，未修改', true);
      editorRenderTimeline();
      return;
    }
    const cues = (track.cues || []).map((cue, index) => index === d.index ? {...cue, start, end} : {...cue});
    executeCommand({
      command: 'set_subtitle_track',
      targetId: 'subtitle',
      payload: {mode: track.mode || 'sentence', source: track.source || 'manual', cues},
    }).then(result => {
      if (result && result.ok) {
        editorToast('字幕时间锚点已保存');
        return refreshWorkspace();
      }
      editorToast('字幕时间锚点未保存：' + ((result && result.error) || '未知错误'), true);
      editorRenderTimeline();
    }).catch(error => {
      editorToast('字幕时间锚点未保存：' + (error.message || error), true);
      editorRenderTimeline();
    });
  }, true);
  document.addEventListener('pointercancel', () => {
    const d = _subtitleCueDrag;
    if (!d) return;
    _subtitleCueDrag = null;
    if (d.el && d.el.isConnected) editorRenderTimeline();
  }, true);
  document.addEventListener('click', ev => {
    if (!_subtitleCueSuppressClick) return;
    const hit = ev.target && ev.target.closest ? ev.target.closest('.ed-subtitle-cue') : null;
    if (hit === _subtitleCueSuppressClick) {
      ev.preventDefault();
      ev.stopImmediatePropagation();
      _subtitleCueSuppressClick = null;
    }
  }, true);
}

// §18 Timeline overlays: timed text/logo layers use the same global time ruler.
// Edits preview locally while dragging, then persist through Editor Command/Revision.
let _watermarkDrag = null;
let _watermarkClipSuppressClick = null;

function _tlBindWatermarkDrag(){
  document.querySelectorAll('#editor-timeline .ed-watermark-clip[data-watermark-index]').forEach(el => {
    el.addEventListener('pointerdown', ev => {
      if (ev.button !== 0 || !ED.comp) return;
      const index = Number.parseInt(el.dataset.watermarkIndex, 10);
      const wm = (ED.comp.watermarks || [])[index];
      const total = Number(ED.comp.duration) || 1;
      if (!wm) return;
      const start = Math.max(0, Number(wm.start) || 0);
      const end = wm.duration == null ? total : Math.min(total, start + Number(wm.duration || 0));
      const duration = end - start;
      if (duration < 0.05 || start >= total) return;
      const rect = el.getBoundingClientRect();
      const edge = Math.min(8, rect.width / 4);
      const mode = ev.clientX - rect.left <= edge ? 'trim-left'
        : rect.right - ev.clientX <= edge ? 'trim-right' : 'move';
      _watermarkClipSuppressClick = null;
      _watermarkDrag = {
        el, index, mode, startX: ev.clientX, moved: false,
        start, duration, end,
        previewStart: start, previewDuration: duration,
      };
      try { el.setPointerCapture(ev.pointerId); } catch (_) {}
    }, true);
  });
  if (window.__tlWatermarkDragHandlers) return;
  window.__tlWatermarkDragHandlers = true;
  document.addEventListener('pointerdown', () => {
    if (!_watermarkDrag) _watermarkClipSuppressClick = null;
  }, true);
  document.addEventListener('pointermove', ev => {
    const d = _watermarkDrag;
    if (!d || !d.el.isConnected) { _watermarkDrag = null; return; }
    const body = d.el.closest('.ed-track-body');
    const width = body ? body.getBoundingClientRect().width : 0;
    if (width <= 0) return;
    const dx = ev.clientX - d.startX;
    if (Math.abs(dx) <= 8 && !d.moved) return;
    d.moved = true;
    ev.preventDefault();
    d.el.classList.add('tl-dragging');
    const total = Number(ED.comp.duration) || 1;
    const delta = dx / width * total;
    let start = d.start, duration = d.duration;
    if (d.mode === 'move') {
      start = Math.max(0, Math.min(total - duration, d.start + delta));
    } else if (d.mode === 'trim-left') {
      start = Math.max(0, Math.min(d.end - 0.05, d.start + delta));
      duration = d.end - start;
    } else {
      duration = Math.max(0.05, Math.min(total - d.start, d.duration + delta));
    }
    d.previewStart = start;
    d.previewDuration = duration;
    d.el.style.left = (start / total * 100) + '%';
    d.el.style.width = (duration / total * 100) + '%';
    d.el.title = d.mode === 'move' ? '松开以移动覆盖层'
      : (d.mode === 'trim-left' ? '松开以修剪覆盖层起点' : '松开以修剪覆盖层终点');
  }, true);
  document.addEventListener('pointerup', ev => {
    const d = _watermarkDrag;
    if (!d) return;
    _watermarkDrag = null;
    d.el.classList.remove('tl-dragging');
    if (!d.moved || !d.el.isConnected || !ED.comp) return;
    _watermarkClipSuppressClick = d.el;
    const start = Math.round(d.previewStart * 1000) / 1000;
    const duration = Math.round(d.previewDuration * 1000) / 1000;
    const command = d.mode === 'move' ? 'move_watermark' : 'trim_watermark';
    editorToast(command === 'move_watermark' ? '正在移动覆盖层……' : '正在修剪覆盖层显示区间……');
    executeCommand({
      command, targetId: 'watermark#' + d.index,
      payload: {start, duration},
    }).then(result => {
      if (result && result.ok) {
        editorToast(command === 'move_watermark' ? '覆盖层位置已更新' : '覆盖层显示区间已更新');
        return refreshWorkspace();
      }
      editorToast('覆盖层未修改：' + ((result && result.error) || '未知错误'), true);
      editorRenderTimeline();
    }).catch(error => {
      editorToast('覆盖层未修改：' + (error.message || error), true);
      editorRenderTimeline();
    });
  }, true);
  document.addEventListener('pointercancel', () => {
    const d = _watermarkDrag;
    if (!d) return;
    _watermarkDrag = null;
    if (d.el && d.el.isConnected) editorRenderTimeline();
  }, true);
  document.addEventListener('click', ev => {
    if (!_watermarkClipSuppressClick) return;
    const hit = ev.target && ev.target.closest ? ev.target.closest('.ed-watermark-clip') : null;
    if (hit === _watermarkClipSuppressClick) {
      ev.preventDefault();
      ev.stopImmediatePropagation();
      _watermarkClipSuppressClick = null;
    }
  }, true);
}

// §17 键盘（Space 播放/暂停、Delete 删除、Ctrl+Z 撤销、Ctrl+Shift+Z 重做、Esc 取消）
// ---------------------------------------------------------------------------
document.addEventListener('keydown', ev => {
  const tag = (ev.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
  if (!ED.pid || document.getElementById('pane-editor').style.display === 'none') return;
  const mod = ev.ctrlKey || ev.metaKey;
  if (ev.code === 'Space') { ev.preventDefault(); previewPlay(); return; }
  if (ev.key === 'Escape') {
    // §20 Esc 优先级：蒙版编辑中=取消路径编辑；否则清选择
    if (typeof MASK_ED !== 'undefined' && MASK_ED.open
        && typeof maskEditorCancel === 'function') {
      maskEditorCancel();
      return;
    }
    clearSelection('keyboard');
    return;
  }
  if (ev.key === 'Delete' || ev.key === 'Backspace') {
    ev.preventDefault();
    // §42 删除语义随对象：场景=删除场景 / 图层=移除图层（同一命令通道）
    if (SEL.selection) {
      editorToast(SEL.selection.type === 'layer' ? '移除图层……' : '删除场景……');
      editorCmd('delete');
    }
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
  PV.mode = 'live';
  PV.playhead = PLAYHEAD.at || 0;
  previewRenderComposition();
  _previewApplyOverlay();
  editorToast('实时预览已打开；渲染草稿用于生成可交付草稿文件');
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

// 版本历史渲染进 §43 抽屉（含 §43 对比）
let _verSelection = [];
async function editorLoadVersions(){
  if (!ED.pid) { editorToast('先打开项目'); return; }
  const revs = await apiGet('/api/editor/revisions?project_id=' + encodeURIComponent(ED.pid));
  const box = document.getElementById('version-drawer');
  if (!box) return;
  _verSelection = [];
  if (!Array.isArray(revs) || !revs.length) {
    box.innerHTML = '<div class="hint">尚无编辑版本（每次修改自动记为一版，可回退）。</div>';
    return;
  }
  const recent = revs.slice(-20).reverse();
  box.innerHTML = `<div class="drawer-inline"><b>版本历史</b>
    <div class="scene-list" style="max-height:240px">
    ${recent.map((rv, i) => {
      const when = new Date((rv.created_at || 0) * 1000).toLocaleString();
      return `<div class="proj-row"><input type="checkbox" class="ver-chk" data-i="${revs.indexOf(rv)}"
        style="width:auto" onchange="verToggleCheck(this, ${revs.indexOf(rv)})">
        <b>v${rv.base_version + 1}</b>`+
        `<span class="hint">${esc(rv.command)} → ${esc(rv.target_id || '')}</span>`+
        `<span class="hint">${when}</span></div>`;
    }).join('')}
    </div>
    <div class="btn-row">
      <button class="btn-secondary btn-sm" onclick="verCompareSelected()">对比勾选的两版</button>
      <button class="btn-secondary btn-sm" data-action="ui.undo">撤销最新一版</button>
    </div>
    <div id="version-compare"></div>
  </div>`;
}
function verToggleCheck(el, idx){
  if (el.checked) { if (!_verSelection.includes(idx)) _verSelection.push(idx); }
  else { _verSelection = _verSelection.filter(x => x !== idx); }
}
async function verCompareSelected(){
  const box = document.getElementById('version-compare');
  if (_verSelection.length !== 2) {
    box.innerHTML = '<div class="hint">勾选两个版本再对比（各显示改了什么、复用什么）。</div>';
    return;
  }
  const revs = await apiGet('/api/editor/revisions?project_id=' + encodeURIComponent(ED.pid));
  const a = revs[Math.min(..._verSelection)], b = revs[Math.max(..._verSelection)];
  const changed = [a, b].map(x => (x.changed_artifacts || []).join('、') || '无改动清单');
  const reusedA = (a.reused_artifacts || []), reusedB = (b.reused_artifacts || []);
  const common = reusedA.filter(x => reusedB.includes(x));
  box.innerHTML = `<div class="hint">
    <b>v${a.base_version + 1} → v${b.base_version + 1} 对比</b><br>
    v${a.base_version + 1} 改动：${esc(changed[0])}<br>
    v${b.base_version + 1} 改动：${esc(changed[1])}<br>
    两版相同未动：${common.length} 项${common.length ? '（' + esc(common.slice(0, 4).join('、')) + '）' : ''}</div>`;
}

// §8 拆分导出：inline onclick/window 引用兼容
/** Split the selected visual layer at the global playback-head time. */
/** Select a global visual clip without opening a modal so timeline tools remain reachable. */
function editorSelectVisualOverlay(index, ev){
  if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
  if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation();
  const clip = ED.comp && (ED.comp.visual_overlays || [])[index];
  if (!clip || !clip.id) {
    editorToast('画面片段已变化，请重新选择。', true);
    return {ok:false, error:'VISUAL_OVERLAY_NOT_FOUND'};
  }
  select('visual_overlay', String(clip.id), ev, 'timeline');
  if (typeof editorRenderTimeline === 'function') editorRenderTimeline();
  return {ok:true, id:String(clip.id)};
}

/** Open the visual inspector for the currently selected global visual clip. */
function editorEditSelectedVisualOverlay(){
  const selection = (typeof SEL !== 'undefined') ? SEL.selection : null;
  if (!selection || selection.type !== 'visual_overlay' || !selection.id) {
    editorToast('先在独立画面轨道中选中一个片段，再编辑它的属性。', true);
    return {ok:false, error:'NO_VISUAL_OVERLAY_SELECTION'};
  }
  const overlays = ED.comp && Array.isArray(ED.comp.visual_overlays) ? ED.comp.visual_overlays : [];
  const index = overlays.findIndex(clip => String(clip.id) === String(selection.id));
  if (index < 0) {
    editorToast('所选画面片段已变化，请重新选择。', true);
    return {ok:false, error:'VISUAL_OVERLAY_NOT_FOUND'};
  }
  editorVisualOverlayOpen(index);
  return {ok:true, id:selection.id};
}

/** Split selected main-picture layers or global visual clips at the playhead. */
async function editorSplitSelectedLayer(){
  const selection = (typeof SEL !== 'undefined') ? SEL.selection : null;
  if (!selection || !selection.id) {
    editorToast('先在时间线中选择一个片段，再把播放头放到片段内部切分。', true);
    return {ok:false, error:'NO_TIMELINE_SELECTION'};
  }
  if (selection.type === 'visual_overlay') {
    const overlays = ED.comp && Array.isArray(ED.comp.visual_overlays) ? ED.comp.visual_overlays : [];
    const index = overlays.findIndex(clip => String(clip.id) === String(selection.id));
    if (index < 0) {
      editorToast('所选画面片段已变化，请重新选择。', true);
      return {ok:false, error:'VISUAL_OVERLAY_NOT_FOUND'};
    }
    if (typeof editorVisualOverlaySplit !== 'function') {
      editorToast('画面片段切分入口尚未加载，项目未修改。', true);
      return {ok:false, error:'VISUAL_OVERLAY_SPLIT_UNAVAILABLE'};
    }
    return await editorVisualOverlaySplit(index);
  }
  if (selection.type !== 'layer') {
    editorToast('当前选择不是可切分的主画面或独立画面片段。', true);
    return {ok:false, error:'UNSUPPORTED_SELECTION'};
  }
  const at = editorSnapPlayheadToFrame(Number(PLAYHEAD && PLAYHEAD.at));
  const result = await editorCmd('split_layer', {at}, selection.id, true);
  if (result && result.ok) editorToast('片段已在播放头处切分');
  else editorToast('切分未完成：' + ((result && result.error) || '请把播放头放在片段内部'), true);
  return result || {ok:false, error:'EMPTY_RESULT'};
}

/** 愈合切口（A7 前端入口）：选中同源切开的相邻段之一，一键合回。 */
async function editorJoinThroughEdits(){
  const selection = (typeof SEL !== 'undefined') ? SEL.selection : null;
  if (!selection || !selection.id) {
    editorToast('先选中一个切开的片段，再愈合它与相邻段之间的切口。', true);
    return {ok:false, error:'NO_TIMELINE_SELECTION'};
  }
  let targetId = null;
  if (selection.type === 'visual_overlay') {
    targetId = 'visual-overlay:' + String(selection.id);
  } else if (selection.type === 'audio_clip') {
    targetId = 'audio#' + Number(selection.index);
  } else if (selection.type === 'layer') {
    targetId = String(selection.id);
  } else {
    editorToast('当前选择不支持愈合：请选中主画面片段、独立画面片段或音频片段。', true);
    return {ok:false, error:'UNSUPPORTED_SELECTION'};
  }
  if (!Number.isInteger(Number(selection.index)) && targetId.startsWith('audio#')) {
    editorToast('音频片段索引已变化，请重新选择。', true);
    return {ok:false, error:'STALE_AUDIO_INDEX'};
  }
  const result = await editorCmd('join_through_edits', {}, targetId, true);
  if (result && result.ok) editorToast('切口已愈合：两段合回一个片段');
  else editorToast('未愈合：' + ((result && result.error) || '相邻段不是同源连续切口'), true);
  return result || {ok:false, error:'EMPTY_RESULT'};
}


async function editorLiftSelectedLayerToPlayhead(){
  const selection = (typeof SEL !== 'undefined') ? SEL.selection : null;
  if (!selection || selection.type !== 'layer' || !selection.id) {
    editorToast('先选中主画面中的视频/图片片段，再把播放头移到目标起点。', true);
    return {ok:false, error:'NO_LAYER_SELECTION'};
  }
  const parts = String(selection.id).split('#');
  const sceneId = parts[0];
  const layerIndex = parts.length === 2 ? Number.parseInt(parts[1], 10) : NaN;
  const scene = (ED.comp && ED.comp.scenes || []).find(item => item.id === sceneId);
  const layer = scene && Number.isInteger(layerIndex) ? scene.layers[layerIndex] : null;
  if (!layer || !['video', 'image'].includes(String(layer.type || '').toLowerCase())) {
    editorToast('只有主画面中的视频或静态图片可以移到独立画面轨道；动态效果会由服务端检查。', true);
    return {ok:false, error:'UNSUPPORTED_LAYER'};
  }
  const requestedStart = Number(PLAYHEAD && PLAYHEAD.at);
  // 统一编辑切点契约：提升放置起点先落到项目帧钟，与切分命令一致。
  const start = Number.isFinite(requestedStart) && typeof editorSnapPlayheadToFrame === 'function'
    ? editorSnapPlayheadToFrame(requestedStart)
    : requestedStart;
  const duration = Number(layer.duration);
  const total = Number((ED.comp && ED.comp.duration) || (ED.comp && ED.comp.project && ED.comp.project.duration));
  if (!Number.isFinite(start) || start < 0 || !Number.isFinite(duration) || duration <= 0 ||
      !Number.isFinite(total) || total <= 0) {
    editorToast('播放头或片段时长无效，项目未修改。', true);
    return {ok:false, error:'INVALID_TIMELINE_RANGE'};
  }
  if (start + duration > total + 0.001) {
    editorToast('片段放到此处会超出项目片长；请将播放头移到 ' +
      Math.max(0, total - duration).toFixed(2) + 's 之前。项目未修改。', true);
    return {ok:false, error:'INVALID_TIMELINE_RANGE'};
  }
  let result;
  try {
    result = await executeCommand({
      command: 'promote_layer_to_visual_track',
      targetId: selection.id,
      payload: {start}
    });
  } catch (error) {
    editorToast('片段未移动：' + (error && error.message ? error.message : error), true);
    return {ok:false, error:String(error)};
  }
  if (result && result.ok) {
    editorToast('片段已放到 ' + start.toFixed(2) +
      's 的独立画面轨道；原位置保留空隙，现在可以独立移动、修剪和切分。');
    if (typeof refreshWorkspace === 'function') await refreshWorkspace();
  } else {
    editorToast('片段未移动：' + ((result && (result.error || result.message)) || '服务端拒绝此次转换'), true);
  }
  return result || {ok:false, error:'EMPTY_RESULT'};
}

async function editorExpandMainTimelineToGlobalTrack(ev){
  if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
  if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation();
  const scenes = ED.comp && Array.isArray(ED.comp.scenes) ? ED.comp.scenes : [];
  const clips = scenes.reduce((items, scene) => items.concat(
    (scene.layers || [])
      .filter(layer => ['video', 'image'].includes(String(layer.type || '').toLowerCase())
      )
      .map(layer => ({sceneId: scene.id, layer}))
  ), []);
  if (!clips.length) {
    editorToast('没有可展开的主画面视频/图片片段；项目未修改。', true);
    return {ok:false, error:'NO_MAIN_MEDIA_CLIPS'};
  }
  editorToast(`正在检查并展开 ${clips.length} 个主画面片段；任何一个不兼容都会整单取消……`);
  const result = await editorCmd('expand_main_timeline_to_visual_track', {}, 'timeline:main', true);
  if (result && result.ok) {
    editorToast(`已将 ${clips.length} 个片段展开为自由时间线；原时长、变换与可用原声已保留，可撤销。`);
    if (typeof refreshWorkspace === 'function') await refreshWorkspace();
  } else {
    editorToast('自由时间线展开已取消：' + ((result && (result.error || result.message)) || '片段不兼容；项目未修改。'), true);
  }
  return result || {ok:false, error:'EMPTY_RESULT'};
}

async function editorAddEditAllTracks(){
  const requestedAt = Number(PLAYHEAD && PLAYHEAD.at);
  if (!Number.isFinite(requestedAt) || requestedAt < 0) {
    editorToast('播放头时间无效；项目未修改。', true);
    return;
  }
  const at = editorSnapPlayheadToFrame(requestedAt);
  const result = await editorCmd('add_edit_all_tracks', {at}, 'timeline', true);
  if (result && result.ok) {
    const warnings = result.data && Array.isArray(result.data.warnings) ? result.data.warnings : [];
    if (warnings.length) {
      editorToast('已切分可编辑片段；未自动切分：' + warnings.join('；'), true);
    } else {
      editorToast('已在播放头处完成全轨添加编辑');
    }
  } else {
    editorToast('全轨添加编辑未完成：' + ((result && result.error) || '播放头位置没有可安全切分的片段；项目未修改。'), true);
  }
}

Object.assign(window, {
  edSelect, editorSelectScene, editorSelectLayer, edIsSelected,
  edClearSelection, edSelectedCount,
  editorInit, editorLoad, timelineView, semanticRoleOf, renderSemanticTimeline,

  editorRenderTimeline, tlToggleGroup, editorSelectVisualOverlay, editorEditSelectedVisualOverlay, editorSplitSelectedLayer, editorJoinThroughEdits, editorLiftSelectedLayerToPlayhead, editorExpandMainTimelineToGlobalTrack, editorAddEditAllTracks,
  editorProjectFrameTime, editorSnapPlayheadToFrame,
  editorAudioClipOpen, editorAudioClipClose, editorAudioClipApply,
  editorAudioTrackAdd, editorAudioTrackToggleLock, editorAudioTrackMenu, editorAudioTrackMenuClose,
  editorAudioTrackApplyName, editorAudioTrackRemove,
  selectNarration, projectOverlayOpen, originalTrackOpen, bgmTrackOpen,
  editorUndo, editorRedo, editorSaveBar, editorPreviewBar, editorTopbarRefresh,
  versionDrawerToggle, projectDrawerToggle, editorLoadVersions,
  verToggleCheck, verCompareSelected,
  PLAYHEAD, TL_GROUPS,
});

