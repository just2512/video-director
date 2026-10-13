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
  // Another preview workflow may replace stage.innerHTML without changing the IR ids.
  // Rebuild the live media host when that happens; otherwise overlays/subtitles are
  // silently detached and subsequent playhead seeks cannot restore the preview.
  const liveMediaMissing = !stage.querySelector('.pv-live-media');
  const changed = PV.activeSceneId !== sc.id || PV.activeLayerId !== key || liveMediaMissing;
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
  const mediaCanvas = _previewFitMediaCanvas(stage);
  const mainMedia = mediaCanvas && mediaCanvas.querySelector('#pv-live-video, .pv-live-image');
  if (mainMedia && lay) _previewApplyLayerTransform(mainMedia, lay);
  // Live overlay clips use the same CompositionIR global clock as the timeline;
  // draft preview already contains these clips in the rendered MP4, so it does not add them again.
  _previewRenderVisualOverlays(sec);
  _previewRenderSubtitles(sec);
  if (meta) meta.textContent = '实时预览 · ' + sec.toFixed(1) + ' / ' + (ED.comp.duration || 0).toFixed(1) + 's';
}

/** Fit the live preview canvas to the project's real aspect ratio inside the preview pane. */
function _previewFitMediaCanvas(stage, bindObserver = true) {
  if (!stage || !ED.comp) return null;
  const media = stage.querySelector('.pv-live-media');
  if (!media) return null;
  const project = ED.comp.project || {};
  const projectWidth = Math.max(1, Number(project.width) || 1080);
  const projectHeight = Math.max(1, Number(project.height) || 1920);
  const ratio = projectWidth / projectHeight;
  const paneStyle = window.getComputedStyle ? getComputedStyle(stage) : null;
  const horizontalPadding = paneStyle
    ? (parseFloat(paneStyle.paddingLeft) || 0) + (parseFloat(paneStyle.paddingRight) || 0)
    : 0;
  const verticalPadding = paneStyle
    ? (parseFloat(paneStyle.paddingTop) || 0) + (parseFloat(paneStyle.paddingBottom) || 0)
    : 0;
  const availableWidth = Math.max(1, stage.clientWidth - horizontalPadding);
  const availableHeight = Math.max(1, stage.clientHeight - verticalPadding);
  let width = availableWidth;
  let height = width / ratio;
  if (height > availableHeight) {
    height = availableHeight;
    width = height * ratio;
  }
  media.style.position = 'relative';
  media.style.flex = '0 0 auto';
  media.style.width = Math.max(1, width).toFixed(2) + 'px';
  media.style.height = Math.max(1, height).toFixed(2) + 'px';
  media.style.maxWidth = '100%';
  media.style.maxHeight = '100%';
  media.style.aspectRatio = projectWidth + ' / ' + projectHeight;

  if (bindObserver && !stage._pvCanvasResizeObserver && typeof ResizeObserver === 'function') {
    const observer = new ResizeObserver(() => _previewFitMediaCanvas(stage, false));
    observer.observe(stage);
    stage._pvCanvasResizeObserver = observer;
  }
  return media;
}

/** Render Layer.transform using the same normalized position/scale/rotation/opacity as FFmpeg export. */
function _previewApplyLayerTransform(node, lay) {
  if (!node || !lay) return;
  const legacy = (((lay.motion_instance || {}).parameters || {}).transform) || {};
  const raw = lay.transform || legacy;
  const number = (value, fallback) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };
  const clamp = (value, min, max, fallback) => Math.max(min, Math.min(max, number(value, fallback)));
  const anchors = {
    top_left: [0, 0], top_center: [0.5, 0], top_right: [1, 0],
    center_left: [0, 0.5], center: [0.5, 0.5], center_right: [1, 0.5],
    bottom_left: [0, 1], bottom_center: [0.5, 1], bottom_right: [1, 1]
  };
  const anchor = anchors[String(raw.anchor || 'center')] || anchors.center;
  const x = clamp(raw.x, 0, 1, 0.5);
  const y = clamp(raw.y, 0, 1, 0.5);
  const scale = clamp(raw.scale, 0.1, 5, 1);
  const rotation = number(raw.rotation, 0) % 360;
  const opacity = clamp(raw.opacity, 0, 1, 1);
  const pct = value => Number((value * 100).toFixed(4)).toString() + '%';

  node.style.position = 'absolute';
  node.style.maxWidth = 'none';
  node.style.maxHeight = 'none';
  node.style.objectFit = (String(lay.type || '').toLowerCase() === 'video' && String(lay.fit_mode || 'cover') === 'contain')
    ? 'contain' : 'cover';
  node.style.width = pct(scale);
  node.style.height = pct(scale);
  node.style.left = 'calc(' + pct(x) + ' - ' + pct(anchor[0] * scale) + ')';
  node.style.top = 'calc(' + pct(y) + ' - ' + pct(anchor[1] * scale) + ')';
  node.style.transformOrigin = pct(anchor[0]) + ' ' + pct(anchor[1]);
  node.style.transform = Math.abs(rotation) < 1e-9 ? 'none' : 'rotate(' + rotation + 'deg)';
  node.style.opacity = String(opacity);
}

/** Show CompositionIR subtitle cues on the same live-preview clock as the timeline. */
function _previewRenderSubtitles(sec) {
  if (!ED.comp || PV.mode !== 'live') return;
  const media = document.querySelector('#ed-preview-stage .pv-live-media');
  if (!media) return;
  let host = media.querySelector('.pv-live-subtitle');
  if (!host) {
    host = document.createElement('div');
    host.className = 'pv-live-subtitle';
    host.setAttribute('aria-live', 'off');
    media.appendChild(host);
  }

  const track = ED.comp.subtitle_track || null;
  const cues = track && Array.isArray(track.cues) ? track.cues : [];
  const ordered = cues.map((cue, index) => ({cue, index}))
    .filter(item => item.cue && Number.isFinite(Number(item.cue.start)) && Number.isFinite(Number(item.cue.end)) && Number(item.cue.end) > Number(item.cue.start))
    .sort((a, b) => Number(a.cue.start) - Number(b.cue.start) || a.index - b.index);
  const activeIndex = ordered.findIndex(({cue}) => sec >= Number(cue.start) && sec < Number(cue.end));
  if (activeIndex < 0) {
    host.innerHTML = '';
    host.dataset.renderKey = '';
    host.style.display = 'none';
    return;
  }

  const style = ED.comp.subtitle_style || {};
  const mode = String((track && track.mode) || style.mode || 'sentence');
  let first = activeIndex;
  let last = activeIndex;
  if (mode === 'word') {
    const boundary = /[.!?。！？；;:]$/u;
    while (first > 0) {
      const prev = ordered[first - 1].cue;
      const current = ordered[first].cue;
      const gap = Number(current.start) - Number(prev.end);
      if (gap > 0.65 || boundary.test(String(prev.text || '').trim())) break;
      first -= 1;
    }
    while (last < ordered.length - 1) {
      const current = ordered[last].cue;
      const next = ordered[last + 1].cue;
      const gap = Number(next.start) - Number(current.end);
      if (gap > 0.65 || boundary.test(String(current.text || '').trim())) break;
      last += 1;
    }
    // Keep word-by-word karaoke readable on narrow preview stages.
    first = Math.max(first, activeIndex - 8);
    last = Math.min(last, activeIndex + 8);
  }

  const visible = ordered.slice(first, last + 1);
  const hasCjk = visible.some(({cue}) => /[\u3400-\u9fff]/u.test(String(cue.text || '')));
  const separator = mode === 'word' && !hasCjk ? ' ' : '';
  const renderKey = [
    mode, activeIndex, first, last,
    visible.map(({index, cue}) => index + ':' + String(cue.text || '')).join('|')
  ].join('::');
  if (host.dataset.renderKey !== renderKey) {
    host.innerHTML = visible.map(({index, cue}, localIndex) => {
      const word = esc(String(cue.text || '').trim());
      const activeWord = mode === 'word' && first + localIndex === activeIndex;
      return `<span class="pv-live-subtitle-word${activeWord ? ' is-active' : ''}" data-cue-index="${index}">${word}</span>`;
    }).join(separator);
    host.dataset.renderKey = renderKey;
  }
  host.style.display = 'block';

  const safeColor = (value, fallback) => {
    const color = String(value || '').trim();
    return /^#[0-9a-f]{3,8}$/i.test(color) || /^(white|black|yellow|red|transparent)$/i.test(color)
      ? color : fallback;
  };
  const bg = String(style.background || '').trim();
  const bgMatch = bg.match(/^(#[0-9a-f]{6})(?:@([0-9]*\.?[0-9]+))?$/i);
  if (bgMatch) {
    const rgb = bgMatch[1].slice(1);
    const opacity = Math.max(0, Math.min(1, Number(bgMatch[2] || style.background_opacity || 0.5)));
    host.style.backgroundColor = `rgba(${parseInt(rgb.slice(0,2),16)},${parseInt(rgb.slice(2,4),16)},${parseInt(rgb.slice(4,6),16)},${opacity})`;
  } else {
    host.style.backgroundColor = 'transparent';
  }
  host.style.color = safeColor(style.color, '#FFFFFF');
  host.style.webkitTextStroke = Math.max(0, Math.min(12, Number(style.stroke_width) || 0)) +
    'px ' + safeColor(style.stroke_color, '#000000');
  const fontName = String(style.font || '').replace(/[;"'<>\\]/g, '').slice(0, 80).trim();
  host.style.fontFamily = fontName ? '"' + fontName + '"' : '';
  const size = Number(style.size);
  host.style.fontSize = (Math.max(0.5, Math.min(4, Number.isFinite(size) && size > 0 ? size : 1)) * 18) + 'px';
  host.style.maxWidth = (Math.max(0.3, Math.min(1, Number(style.max_width) || 0.9)) * 100) + '%';
  host.style.textAlign = ['left', 'center', 'right'].includes(style.alignment) ? style.alignment : 'center';
  host.style.left = '50%';
  const position = ['top', 'center', 'bottom'].includes(style.position) ? style.position : 'bottom';
  host.style.top = position === 'top' ? '8%' : position === 'center' ? '50%' : '';
  host.style.bottom = position === 'bottom' ? '8%' : '';
  host.style.transform = position === 'center' ? 'translate(-50%, -50%)' : 'translateX(-50%)';
  const animation = ['fade', 'pop_spring'].includes(style.animation) ? style.animation : 'none';
  host.classList.toggle('pv-live-subtitle-fade', animation === 'fade');
  host.classList.toggle('pv-live-subtitle-pop', animation === 'pop_spring');
  host.classList.toggle('pv-live-subtitle-word-mode', mode === 'word');
}

/** Render global visual-overlay clips over the live preview at the shared playhead time. */
function _previewRenderVisualOverlays(sec) {
  if (!ED.comp || PV.mode !== 'live') return;
  const media = document.querySelector('#ed-preview-stage .pv-live-media');
  if (!media) return;
  media.style.position = 'relative';
  let host = media.querySelector('.pv-live-overlays');
  if (!host) {
    host = document.createElement('div');
    host.className = 'pv-live-overlays';
    host.setAttribute('aria-label', '当前时间点的画面叠加预览');
    host.setAttribute('aria-hidden', 'true');
    media.appendChild(host);
  }

  const sourceAssets = ED.comp.assets || {};
  const getAsset = assetId => Array.isArray(sourceAssets)
    ? sourceAssets.find(asset => asset && asset.id === assetId) || null
    : sourceAssets[assetId] || null;
  const clamp = (value, min, max, fallback) => {
    const n = Number(value);
    return Number.isFinite(n) ? Math.max(min, Math.min(max, n)) : fallback;
  };
  const overlays = Array.isArray(ED.comp.visual_overlays) ? ED.comp.visual_overlays : [];
  const visualTracks = Array.isArray(ED.comp.visual_tracks) && ED.comp.visual_tracks.length
    ? ED.comp.visual_tracks : [{id:'overlay-main', order:0}];
  const stackingZ = clip => window.TimelineEditMath
    && typeof window.TimelineEditMath.visualOverlayStackingZIndex === 'function'
      ? window.TimelineEditMath.visualOverlayStackingZIndex(clip, visualTracks)
      : Math.round(clamp(clip && clip.z_index, -100, 100, 0));
  const active = overlays.map((clip, index) => ({
    clip, index, asset: getAsset(clip && clip.asset_id), stackingZ: stackingZ(clip)
  }))
    .filter(({clip}) => {
      if (!clip) return false;
      const start = Number(clip.start);
      const duration = Number(clip.duration);
      return Number.isFinite(start) && Number.isFinite(duration) && duration > 0
        && sec >= start && sec < start + duration - 1e-6;
    })
    .sort((a, b) => a.stackingZ - b.stackingZ || a.index - b.index);
  const key = active.map(({clip, index, asset, stackingZ}) =>
    [index, clip.id || '', clip.asset_id || '', clip.track_id || 'overlay-main',
      stackingZ, asset && asset.file_url || '', asset && asset.type || ''].join(':')
  ).join('|');

  if (host.dataset.activeKey !== key) {
    host.dataset.activeKey = key;
    host.innerHTML = active.map(({clip, index, asset}) => {
      const left = clamp(clip.x, 0, 1, 0.65) * 100;
      const top = clamp(clip.y, 0, 1, 0.1) * 100;
      const width = clamp(clip.width, 0.05, 1, 0.3) * 100;
      const height = clamp(clip.height, 0.05, 1, 0.3) * 100;
      const opacity = clamp(clip.opacity, 0, 1, 1);
      const zIndex = stackingZ(clip);
      const style = `left:${left}%;top:${top}%;width:${width}%;height:${height}%;opacity:${opacity};z-index:${zIndex}`;
      const type = String(asset && asset.type || '').toLowerCase();
      const src = asset && asset.file_url;
      if (!src || !['image', 'video'].includes(type)) {
        return `<div class="pv-live-overlay-missing" data-overlay-index="${index}" style="${style}" title="当前素材无法在实时预览中加载">叠加素材暂不可预览</div>`;
      }
      if (type === 'video') {
        return `<video class="pv-live-overlay-video" data-overlay-index="${index}" src="${esc(src)}" muted playsinline preload="metadata" style="${style}"></video>`;
      }
      return `<img class="pv-live-overlay-image" data-overlay-index="${index}" src="${esc(src)}" alt="" style="${style}">`;
    }).join('');
  }

  const fps = Math.max(1, Number((ED.comp.project || {}).fps) || 30);
  for (const {clip, index, asset, stackingZ: effectiveZ} of active) {
    const node = host.querySelector('[data-overlay-index="' + index + '"]');
    if (!node) continue;
    const left = clamp(clip.x, 0, 1, 0.65) * 100;
    const top = clamp(clip.y, 0, 1, 0.1) * 100;
    const width = clamp(clip.width, 0.05, 1, 0.3) * 100;
    const height = clamp(clip.height, 0.05, 1, 0.3) * 100;
    // Keep the live Preview's source fitting identical to the FFmpeg renderer.
    // PiP/stickers preserve aspect ratio inside their box; full-frame is cover by default.
    const fitMode = clip.presentation_mode === 'full_frame'
      ? (clip.fit_mode === 'contain' ? 'contain' : 'cover')
      : 'contain';
    if (clip.transform) {
      _previewApplyLayerTransform(node, {
        transform: clip.transform,
        type: asset && asset.type || '',
        fit_mode: fitMode
      });
    } else {
      node.style.left = left + '%';
      node.style.top = top + '%';
      node.style.width = width + '%';
      node.style.height = height + '%';
      node.style.objectFit = fitMode;
      node.style.opacity = String(clamp(clip.opacity, 0, 1, 1));
    }
    node.style.zIndex = String(effectiveZ);
    if (node.tagName !== 'VIDEO') continue;

    const sourceIn = Math.max(0, Number(clip.source_in) || 0);
    const sourceTime = sourceIn + Math.max(0, sec - Number(clip.start || 0));
    if (node.readyState > 0 && Math.abs((node.currentTime || 0) - sourceTime) > 0.5 / fps) {
      try { node.currentTime = sourceTime; } catch (_) {}
    }
    if (PV.playing) node.play().catch(() => {});
    else node.pause();
  }
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
