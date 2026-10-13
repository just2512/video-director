/* User-facing independent visual-overlay track controls. */
function editorVisualOverlayClose() {
  const mask = document.getElementById('visual-overlay-inspector-mask');
  if (mask) mask.remove();
  window.VISUAL_OVERLAY_TARGET_TRACK_ID = null;
}
function _visualOverlayAssets() {
  const source = ED.comp && ED.comp.assets;
  const pairs = Array.isArray(source) ? source.map(a => [a.id, a]) : Object.entries(source || {});
  return pairs.filter(([id, a]) => id && a && ['image', 'video'].includes(String(a.type || '').toLowerCase()));
}
function editorVisualOverlayPick(ev, trackId) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  if (!ED.comp) return;
  editorVisualOverlayClose();
  window.VISUAL_OVERLAY_TARGET_TRACK_ID = trackId || 'overlay-main';
  const assets = _visualOverlayAssets();
  if (!assets.length) { editorToast('项目素材库中没有可用的视频或图片，请先导入素材', true); return; }
  const total = Number(ED.comp.duration) || 1;
  const start = Math.max(0, Math.min(total - 0.05, Number(PLAYHEAD.at) || 0));
  const mask = document.createElement('div');
  mask.id = 'visual-overlay-inspector-mask';
  mask.className = 'audio-clip-inspector-mask';
  mask.innerHTML = `
    <section class="audio-clip-inspector" role="dialog" aria-modal="true" aria-labelledby="visual-overlay-create-title">
      <header><div><b id="visual-overlay-create-title">添加画面叠加</b><div class="hint">独立视频/图片轨道，可与主画面重叠</div></div>
        <button type="button" class="btn-secondary btn-sm" onclick="editorVisualOverlayClose()">关闭</button></header>
      <label for="visual-overlay-asset">项目素材</label>
      <select id="visual-overlay-asset">${assets.map(([id, a]) => {
        const name = a.name || (a.file && String(a.file).split(/[\\/]/).pop()) || id;
        return `<option value="${esc(id)}">${esc(name)} · ${esc(a.type)}</option>`;
      }).join('')}</select>
      <div class="audio-clip-field-row">
        <div><label for="visual-overlay-start">起点（秒）</label><input id="visual-overlay-start" type="number" min="0" max="${total}" step="0.05" value="${start.toFixed(2)}"></div>
        <div><label for="visual-overlay-duration">时长（秒）</label><input id="visual-overlay-duration" type="number" min="0.05" max="${total-start}" step="0.05" value="${Math.min(3, total-start).toFixed(2)}"></div>
      </div>
      <label for="visual-overlay-presentation-mode">画面用途</label>
      <select id="visual-overlay-presentation-mode">
        <option value="full_frame">全画幅镜头（覆盖当前画面）</option>
        <option value="picture_in_picture" selected>画中画（小窗叠加）</option>
        <option value="sticker">贴纸 / 图层（小尺寸叠加）</option>
      </select>
      <div id="visual-overlay-create-audio-row" style="margin-top:8px">
        <label><input id="visual-overlay-source-audio" type="checkbox"> 保留此视频素材自身的声音</label>
        <div class="hint">只对有音轨的视频生效；启用后与旁白、BGM、音效进入同一混音流程。</div>
      </div>
      <p class="hint">选择用途后会设置合适的画面尺寸；添加后可在时间线拖动、换轨、修剪与切分。</p>
      <footer><button type="button" class="btn-secondary" onclick="editorVisualOverlayClose()">取消</button>
        <button type="button" class="btn-primary" onclick="editorVisualOverlayCreate()">添加到时间线</button></footer>
    </section>`;
  mask.addEventListener('click', e => { if (e.target === mask) editorVisualOverlayClose(); });
  document.body.appendChild(mask);
  const select = mask.querySelector('#visual-overlay-asset');
  const startInput = mask.querySelector('#visual-overlay-start');
  const durationInput = mask.querySelector('#visual-overlay-duration');
  const audioRow = mask.querySelector('#visual-overlay-create-audio-row');
  const adjustDuration = () => {
    const a = assets.find(([id]) => id === select.value)?.[1];
    let max = total - Number(startInput.value || 0);
    const isVideo = a && String(a.type).toLowerCase() === 'video';
    if (isVideo && Number(a.duration) > 0) max = Math.min(max, Number(a.duration));
    max = Math.max(0.05, max);
    durationInput.max = String(max);
    if (Number(durationInput.value) > max) durationInput.value = max.toFixed(2);
    if (audioRow) audioRow.style.display = isVideo ? '' : 'none';
    if (!isVideo) {
      const audioToggle = mask.querySelector('#visual-overlay-source-audio');
      if (audioToggle) audioToggle.checked = false;
    }
  };
  select.addEventListener('change', adjustDuration);
  startInput.addEventListener('change', adjustDuration);
  adjustDuration();
  select.focus();
}
async function editorVisualOverlayCreate() {
  const mask = document.getElementById('visual-overlay-inspector-mask');
  if (!mask || !ED.comp) return;
  const assetId = mask.querySelector('#visual-overlay-asset').value;
  const start = Number(mask.querySelector('#visual-overlay-start').value);
  const duration = Number(mask.querySelector('#visual-overlay-duration').value);
  const total = Number(ED.comp.duration) || 1;
  if (!assetId || !Number.isFinite(start) || !Number.isFinite(duration) || start < 0 || duration < 0.05 || start + duration > total + 1e-6) {
    editorToast('时间参数无效：片段必须处于项目时长内且不少于 0.05 秒', true); return;
  }
  try {
    const presentationMode = mask.querySelector('#visual-overlay-presentation-mode').value;
    const sourceAudioEnabled = !!mask.querySelector('#visual-overlay-source-audio')?.checked;
    const result = await executeCommand({command:'add_visual_overlay', targetId:'overlays',
      payload:{assetId, start, duration, trackId:window.VISUAL_OVERLAY_TARGET_TRACK_ID || 'overlay-main',
        presentationMode, sourceAudioEnabled}});
    if (!result || !result.ok) { editorToast('添加失败：' + ((result && result.error) || '未知错误'), true); return; }
    editorVisualOverlayClose();
    editorToast(presentationMode === 'full_frame' ? '全画幅镜头已加入时间线' : (presentationMode === 'sticker' ? '贴纸图层已加入时间线' : '画中画已加入时间线'));
    await refreshWorkspace();
  } catch (error) { editorToast('画面叠加添加失败：' + (error.message || error), true); }
}
function editorVisualOverlayApplyPreset() {
  const mask = document.getElementById('visual-overlay-inspector-mask');
  if (!mask) return;
  const mode = mask.querySelector('#visual-overlay-edit-presentation-mode')?.value;
  const preset = mode === 'full_frame' ? [0, 0, 1, 1]
    : mode === 'sticker' ? [0.4, 0.4, 0.2, 0.2] : [0.65, 0.1, 0.3, 0.3];
  ['x', 'y', 'width', 'height'].forEach((key, index) => {
    const input = mask.querySelector('#visual-overlay-' + key);
    if (input) input.value = String(preset[index]);
  });
}

function editorVisualOverlayOpen(index, ev) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  const clip = ED.comp && (ED.comp.visual_overlays || [])[index];
  if (!clip) { editorToast('画面叠加片段已变化，请重新选择', true); return; }
  editorVisualOverlayClose();
  const fmt = n => Number(n || 0).toFixed(2);
  const sourceAsset = _visualOverlayAssets().find(([id]) => id === clip.asset_id)?.[1];
  const isVideoAsset = sourceAsset && String(sourceAsset.type).toLowerCase() === 'video';
  const sourceAudioSettings = isVideoAsset ? `
      <div class="audio-clip-field-row">
        <label><input id="visual-overlay-edit-source-audio" type="checkbox" ${clip.source_audio_enabled ? 'checked' : ''}> 保留视频源音轨</label>
        <div><label for="visual-overlay-edit-source-audio-volume">源音量（0–2）</label>
          <input id="visual-overlay-edit-source-audio-volume" type="number" min="0" max="2" step="0.05" value="${Number(clip.source_audio_volume ?? 1)}"></div>
      </div>
      <p class="hint">启用后会将该视频原声与旁白、BGM、SFX 一起混音；没有音轨的源视频会在渲染时明确报错。</p>`
    : '<p class="hint">该素材为图片，没有可保留的源音轨。</p>';
  const sourceAudioDetachAction = clip.source_audio_enabled ? `
      <div class="audio-clip-field-row">
        <button type="button" class="btn-secondary btn-sm" onclick="editorVisualOverlayDetachAudio(${index})">拆分为独立原声轨</button>
      </div>
      <p class="hint">拆分后，原声会出现在下方时间线的“原声”轨，可独立移动、修剪、静音和混音；视频片段不再重复播放这份声音。可用“撤销”恢复。</p>` : '';
  const presentationControls = clip.transform
    ? '<p class="hint">此片段从主画面提升而来，已保留原来的位置/缩放/旋转/透明度。它作为全画幅素材叠加在时间线上，不能切换为画中画/贴纸；可以继续调整下面的变换参数。</p>'
    : `<label for="visual-overlay-edit-presentation-mode">画面用途</label>
      <select id="visual-overlay-edit-presentation-mode" onchange="editorVisualOverlayApplyPreset()">
        <option value="full_frame" ${clip.presentation_mode === 'full_frame' ? 'selected' : ''}>全画幅镜头</option>
        <option value="picture_in_picture" ${!clip.presentation_mode || clip.presentation_mode === 'picture_in_picture' ? 'selected' : ''}>画中画</option>
        <option value="sticker" ${clip.presentation_mode === 'sticker' ? 'selected' : ''}>贴纸 / 图层</option>
      </select>`;
  const placementControls = clip.transform
    ? `<div class="audio-clip-field-row">
        <div><label for="visual-overlay-transform-x">锚点 X（0–1）</label><input id="visual-overlay-transform-x" type="number" min="0" max="1" step="0.01" value="${Number(clip.transform.x ?? 0.5)}"></div>
        <div><label for="visual-overlay-transform-y">锚点 Y（0–1）</label><input id="visual-overlay-transform-y" type="number" min="0" max="1" step="0.01" value="${Number(clip.transform.y ?? 0.5)}"></div>
      </div>
      <div class="audio-clip-field-row">
        <div><label for="visual-overlay-transform-scale">缩放（0.1–5）</label><input id="visual-overlay-transform-scale" type="number" min="0.1" max="5" step="0.05" value="${Number(clip.transform.scale ?? 1)}"></div>
        <div><label for="visual-overlay-transform-rotation">旋转角度（-360–360°）</label><input id="visual-overlay-transform-rotation" type="number" min="-360" max="360" step="1" value="${Number(clip.transform.rotation ?? 0)}"></div>
      </div>
      <label for="visual-overlay-transform-opacity">不透明度（0–1）</label><input id="visual-overlay-transform-opacity" type="number" min="0" max="1" step="0.05" value="${Number(clip.transform.opacity ?? 1)}">
      <p class="hint">锚点：${esc(String(clip.transform.anchor || 'center'))}。调整 X/Y 改变锚点位置；缩放、旋转围绕该锚点执行。</p>`
    : `<div class="audio-clip-field-row">
        <div><label for="visual-overlay-x">X 位置（0–1）</label><input id="visual-overlay-x" type="number" min="0" max="1" step="0.01" value="${Number(clip.x ?? 0.65)}"></div>
        <div><label for="visual-overlay-y">Y 位置（0–1）</label><input id="visual-overlay-y" type="number" min="0" max="1" step="0.01" value="${Number(clip.y ?? 0.1)}"></div>
      </div>
      <div class="audio-clip-field-row">
        <div><label for="visual-overlay-width">宽度（0.05–1）</label><input id="visual-overlay-width" type="number" min="0.05" max="1" step="0.01" value="${Number(clip.width ?? 0.3)}"></div>
        <div><label for="visual-overlay-height">高度（0.05–1）</label><input id="visual-overlay-height" type="number" min="0.05" max="1" step="0.01" value="${Number(clip.height ?? 0.3)}"></div>
      </div>
      <label for="visual-overlay-opacity">不透明度（0–1）</label><input id="visual-overlay-opacity" type="number" min="0" max="1" step="0.05" value="${Number(clip.opacity ?? 1)}">`;
  const mask = document.createElement('div');
  mask.id = 'visual-overlay-inspector-mask';
  mask.className = 'audio-clip-inspector-mask';
  mask.innerHTML = `
    <section class="audio-clip-inspector" role="dialog" aria-modal="true" aria-labelledby="visual-overlay-edit-title">
      <header><div><b id="visual-overlay-edit-title">画面叠加 · 设置</b><div class="hint">${esc(clip.asset_id)} · ${fmt(clip.start)}–${fmt(Number(clip.start)+Number(clip.duration))}s</div></div>
        <button type="button" class="btn-secondary btn-sm" onclick="editorVisualOverlayClose()">关闭</button></header>
      <p class="hint">时间位置与时长可直接在时间线上拖动/修剪；全画幅、画中画和贴纸共用同一可编辑片段模型。</p>
      ${presentationControls}
      ${sourceAudioSettings}
      ${sourceAudioDetachAction}
      ${placementControls}
  <label for="visual-overlay-z-index">叠加层级（数值越大越靠前）</label><input id="visual-overlay-z-index" type="number" min="-100" max="100" step="1" value="${Number(clip.z_index ?? 0)}">
      <footer><button type="button" class="btn-danger" onclick="editorVisualOverlayRemove(${index})">删除叠加</button>
        <button type="button" class="btn-secondary" onclick="editorVisualOverlaySplit(${index})">在播放头切分</button>
        <button type="button" class="btn-secondary" onclick="editorVisualOverlayClose()">取消</button>
        <button type="button" class="btn-primary" onclick="editorVisualOverlayApply(${index})">保存设置</button></footer>
    </section>`;
  mask.addEventListener('click', e => { if (e.target === mask) editorVisualOverlayClose(); });
  document.body.appendChild(mask);
  const initialFocus = mask.querySelector('#visual-overlay-transform-x') || mask.querySelector('#visual-overlay-x');
  if (initialFocus) initialFocus.focus();
}
async function editorVisualOverlaySplit(index) {
  const clip = ED.comp && (ED.comp.visual_overlays || [])[index];
  if (!clip) {
    editorToast('画面叠加片段已变化，请重新选择', true);
    return {ok:false, error:'VISUAL_OVERLAY_NOT_FOUND'};
  }
  const at = Number(window.PLAYHEAD && PLAYHEAD.at);
  const start = Number(clip.start) || 0;
  const end = start + (Number(clip.duration) || 0);
  if (!Number.isFinite(at) || at <= start + 0.05 || at >= end - 0.05) {
    const message = '请先把播放头放到片段内部，且切分两侧至少保留 0.05 秒';
    editorToast(message, true);
    return {ok:false, error:'SPLIT_OUTSIDE_CLIP', message};
  }
  try {
    const result = await executeCommand({
      command: 'split_visual_overlay',
      targetId: 'overlay#' + index,
      payload: {at}
    });
    if (!result || !result.ok) {
      editorToast('切分未完成：' + ((result && result.error) || '未知错误'), true);
      return result || {ok:false, error:'EMPTY_RESULT'};
    }
    editorVisualOverlayClose();
    editorToast('画面叠加已在播放头切分；源素材未改写');
    await refreshWorkspace();
    return result;
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    editorToast('画面叠加切分失败：' + message, true);
    return {ok:false, error:'VISUAL_OVERLAY_SPLIT_FAILED', message};
  }
}


async function editorVisualOverlayDetachAudio(index) {
  const mask = document.getElementById('visual-overlay-inspector-mask');
  const clip = ED.comp && (ED.comp.visual_overlays || [])[index];
  if (!mask || !clip || !clip.source_audio_enabled) {
    editorToast('该片段当前没有可拆分的内嵌源音频', true);
    return;
  }
  const checkbox = mask.querySelector('#visual-overlay-edit-source-audio');
  if (checkbox && !checkbox.checked) {
    editorToast('你已关闭源音频但尚未保存；请先保存当前设置，再重新打开片段执行拆分。', true);
    return;
  }
  try {
    const result = await executeCommand({
      command: 'separate_visual_overlay_source_audio',
      targetId: 'overlay#' + index,
      payload: {}
    });
    if (!result || !result.ok) {
      editorToast('源音频拆分未完成：' + ((result && result.error) || '未知错误'), true);
      return;
    }
    editorVisualOverlayClose();
    editorToast('原声已拆分到“原声”轨，可独立移动、修剪和混音；可用撤销还原。');
    await refreshWorkspace();
  } catch (error) {
    editorToast('源音频拆分失败：' + (error.message || error), true);
  }
}

async function editorVisualOverlayApply(index) {
  const mask = document.getElementById('visual-overlay-inspector-mask');
  const clip = ED.comp && (ED.comp.visual_overlays || [])[index];
  if (!mask || !clip) return;
  const payload = {
    z_index:Number(mask.querySelector('#visual-overlay-z-index').value),
    presentation_mode:mask.querySelector('#visual-overlay-edit-presentation-mode')?.value || clip.presentation_mode || 'full_frame',
    source_audio_enabled:!!mask.querySelector('#visual-overlay-edit-source-audio')?.checked,
    source_audio_volume:Number(mask.querySelector('#visual-overlay-edit-source-audio-volume')?.value ?? clip.source_audio_volume ?? 1)
  };
  if (clip.transform) {
    payload.transform = {
      x:Number(mask.querySelector('#visual-overlay-transform-x').value),
      y:Number(mask.querySelector('#visual-overlay-transform-y').value),
      scale:Number(mask.querySelector('#visual-overlay-transform-scale').value),
      rotation:Number(mask.querySelector('#visual-overlay-transform-rotation').value),
      opacity:Number(mask.querySelector('#visual-overlay-transform-opacity').value),
      anchor:String(clip.transform.anchor || 'center')
    };
  } else {
    Object.assign(payload, {
      x:Number(mask.querySelector('#visual-overlay-x').value),
      y:Number(mask.querySelector('#visual-overlay-y').value),
      width:Number(mask.querySelector('#visual-overlay-width').value),
      height:Number(mask.querySelector('#visual-overlay-height').value),
      opacity:Number(mask.querySelector('#visual-overlay-opacity').value)
    });
  }
  const values = clip.transform
    ? [payload.transform.x, payload.transform.y, payload.transform.scale, payload.transform.rotation,
       payload.transform.opacity, payload.z_index, payload.source_audio_volume]
    : [payload.x, payload.y, payload.width, payload.height, payload.opacity, payload.z_index, payload.source_audio_volume];
  const invalidTransform = !!clip.transform && (
    payload.transform.x < 0 || payload.transform.x > 1 ||
    payload.transform.y < 0 || payload.transform.y > 1 ||
    payload.transform.scale < 0.1 || payload.transform.scale > 5 ||
    payload.transform.rotation < -360 || payload.transform.rotation > 360 ||
    payload.transform.opacity < 0 || payload.transform.opacity > 1 ||
    !['top_left','top_center','top_right','center_left','center','center_right','bottom_left','bottom_center','bottom_right'].includes(payload.transform.anchor) ||
    payload.presentation_mode !== 'full_frame'
  );
  const invalidRectangle = !clip.transform && (
    payload.x < 0 || payload.y < 0 ||
    payload.width < 0.05 || payload.height < 0.05 || payload.width > 1 || payload.height > 1 ||
    payload.opacity < 0 || payload.opacity > 1 ||
    !['full_frame', 'picture_in_picture', 'sticker'].includes(payload.presentation_mode) ||
    payload.x + payload.width > 1 || payload.y + payload.height > 1
  );
  if (!values.every(Number.isFinite) || !Number.isInteger(payload.z_index) || payload.z_index < -100 || payload.z_index > 100 ||
      payload.source_audio_volume < 0 || payload.source_audio_volume > 2 || invalidTransform || invalidRectangle) {
    editorToast('位置/尺寸或变换参数越界；透明度为 0–1，源音量为 0–2', true); return;
  }
  try {
    const result = await executeCommand({command:'set_visual_overlay', targetId:'overlay#' + index, payload});
    if (!result || !result.ok) { editorToast('设置未保存：' + ((result && result.error) || '未知错误'), true); return; }
    editorVisualOverlayClose();
    editorToast('画面叠加设置已保存');
    await refreshWorkspace();
  } catch (error) { editorToast('设置未保存：' + (error.message || error), true); }
}
async function editorVisualOverlayRemove(index) {
  if (!ED.comp || !(ED.comp.visual_overlays || [])[index]) { editorVisualOverlayClose(); return; }
  try {
    const result = await executeCommand({command:'remove_visual_overlay', targetId:'overlay#' + index, payload:{}});
    if (!result || !result.ok) { editorToast('删除未完成：' + ((result && result.error) || '未知错误'), true); return; }
    editorVisualOverlayClose();
    editorToast('画面叠加已删除');
    await refreshWorkspace();
  } catch (error) { editorToast('删除未完成：' + (error.message || error), true); }
}

function editorVisualTrackCreate(ev) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  if (!ED.comp) return;
  editorVisualOverlayClose();
  const mask = document.createElement('div');
  mask.id = 'visual-track-create-mask';
  mask.className = 'audio-clip-inspector-mask';
  const count = (ED.comp.visual_tracks || []).length;
  mask.innerHTML = `
    <section class="audio-clip-inspector" role="dialog" aria-modal="true" aria-labelledby="visual-track-create-title">
      <header><div><b id="visual-track-create-title">新增画面轨道</b><div class="hint">轨道顺序决定画面叠加的前后层级</div></div>
        <button type="button" class="btn-secondary btn-sm" onclick="editorVisualTrackCreateClose()">关闭</button></header>
      <label for="visual-track-create-name">轨道名称</label>
      <input id="visual-track-create-name" maxlength="60" value="画面轨 ${count + 1}" autocomplete="off">
      <footer><button type="button" class="btn-secondary" onclick="editorVisualTrackCreateClose()">取消</button>
        <button type="button" class="btn-primary" onclick="editorVisualTrackConfirm()">创建轨道</button></footer>
    </section>`;
  mask.addEventListener('click', event => { if (event.target === mask) editorVisualTrackCreateClose(); });
  document.body.appendChild(mask);
  const input = mask.querySelector('#visual-track-create-name');
  input.focus(); input.select();
}
function editorVisualTrackCreateClose() {
  const mask = document.getElementById('visual-track-create-mask');
  if (mask) mask.remove();
}
async function editorVisualTrackConfirm() {
  const mask = document.getElementById('visual-track-create-mask');
  const name = String(mask && mask.querySelector('#visual-track-create-name').value || '').trim();
  if (!name || name.length > 60) { editorToast('轨道名称必须为 1–60 个字符', true); return; }
  try {
    const result = await executeCommand({command:'add_visual_track', targetId:'visual-tracks', payload:{name}});
    if (!result || !result.ok) { editorToast('创建轨道失败：' + ((result && result.error) || '未知错误'), true); return; }
    editorVisualTrackCreateClose();
    editorToast('画面轨道已创建');
    await refreshWorkspace();
  } catch (error) { editorToast('创建轨道失败：' + (error.message || error), true); }
}
function editorVisualTrackMenu(ev, trackId) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  if (!ED.comp) return;
  editorVisualTrackMenuClose();
  const tracks = (ED.comp.visual_tracks || [{id:'overlay-main', name:'画面叠加', order:0}])
    .slice().sort((a,b) => Number(a.order || 0) - Number(b.order || 0));
  const index = tracks.findIndex(track => track.id === trackId);
  const track = tracks[index];
  if (!track) { editorToast('画面轨道已变化，请刷新后重试', true); return; }
  const hasClips = (ED.comp.visual_overlays || []).some(clip => (clip.track_id || 'overlay-main') === trackId);
  const mask = document.createElement('div');
  mask.id = 'visual-track-menu-mask';
  mask.className = 'audio-clip-inspector-mask';
  const id = esc(trackId);
  mask.innerHTML = `
    <section class="audio-clip-inspector" role="dialog" aria-modal="true" aria-labelledby="visual-track-menu-title">
      <header><div><b id="visual-track-menu-title">${esc(track.name)}</b><div class="hint">画面轨道 · 顺序 ${index + 1}</div></div>
        <button type="button" class="btn-secondary btn-sm" onclick="editorVisualTrackMenuClose()">关闭</button></header>
      <div class="ed-visual-track-menu-actions">
        <button type="button" class="btn-secondary" ${index === tracks.length - 1 ? 'disabled' : ''} onclick="editorVisualTrackMove(event, '${id}', ${index + 1})">上移一层</button>
        <button type="button" class="btn-secondary" ${index === 0 ? 'disabled' : ''} onclick="editorVisualTrackMove(event, '${id}', ${index - 1})">下移一层</button>
        <button type="button" class="btn-secondary" onclick="editorVisualTrackRename(event, '${id}')">重命名</button>
        <button type="button" class="btn-danger" ${tracks.length <= 1 || hasClips ? 'disabled' : ''} onclick="editorVisualTrackRemove(event, '${id}')">删除空轨道</button>
      </div>
      <p class="hint">${hasClips ? '先将此轨道中的片段拖到其他画面轨，或删除片段，再删除此轨道。' : '轨道顺序会直接影响最终成片里的叠加层级。'}</p>
    </section>`;
  mask.addEventListener('click', event => { if (event.target === mask) editorVisualTrackMenuClose(); });
  document.body.appendChild(mask);
}
function editorVisualTrackMenuClose() {
  const mask = document.getElementById('visual-track-menu-mask');
  if (mask) mask.remove();
}
async function editorVisualTrackToggleLock(ev, trackId) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  const track = (ED.comp && ED.comp.visual_tracks || []).find(item => String(item.id) === String(trackId));
  if (!track) { editorToast('画面轨道已变化，请刷新后重试', true); return; }
  const locked = !Boolean(track.locked);
  editorVisualTrackMenuClose();
  try {
    const result = await executeCommand({command:'set_track_locked', targetId:trackId, payload:{trackType:'visual', locked}});
    if (!result || !result.ok) { editorToast('画面轨道锁定状态未修改：' + ((result && result.error) || '未知错误'), true); return; }
    await refreshWorkspace();
    editorToast(locked ? '画面轨道已锁定：全轨切分、拖动和修剪会跳过该轨' : '画面轨道已解锁：可继续编辑');
  } catch (error) { editorToast('画面轨道锁定状态未修改：' + (error.message || error), true); }
}

async function editorVisualTrackMove(ev, trackId, toIndex) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  editorVisualTrackMenuClose();
  try {
    const result = await executeCommand({command:'reorder_visual_track', targetId:trackId, payload:{toIndex}});
    if (!result || !result.ok) { editorToast('轨道顺序未修改：' + ((result && result.error) || '未知错误'), true); return; }
    editorToast('画面轨道层级已更新');
    await refreshWorkspace();
  } catch (error) { editorToast('轨道顺序未修改：' + (error.message || error), true); }
}
async function editorVisualTrackRename(ev, trackId) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  const track = (ED.comp && ED.comp.visual_tracks || []).find(item => item.id === trackId);
  if (!track) { editorToast('画面轨道已变化，请刷新后重试', true); return; }
  editorVisualTrackMenuClose();
  const name = window.prompt('修改画面轨道名称', track.name);
  if (name == null) return;
  try {
    const result = await executeCommand({command:'rename_visual_track', targetId:trackId, payload:{name:name.trim()}});
    if (!result || !result.ok) { editorToast('轨道名称未修改：' + ((result && result.error) || '未知错误'), true); return; }
    editorToast('画面轨道名称已更新');
    await refreshWorkspace();
  } catch (error) { editorToast('轨道名称未修改：' + (error.message || error), true); }
}
async function editorVisualTrackRemove(ev, trackId) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  editorVisualTrackMenuClose();
  try {
    const result = await executeCommand({command:'remove_visual_track', targetId:trackId, payload:{}});
    if (!result || !result.ok) { editorToast('轨道未删除：' + ((result && result.error) || '未知错误'), true); return; }
    editorToast('空画面轨道已删除');
    await refreshWorkspace();
  } catch (error) { editorToast('轨道未删除：' + (error.message || error), true); }
}

let _visualOverlayDrag = null;
let _visualOverlaySuppressClick = null;
function _tlBindVisualOverlayDrag() {
  document.querySelectorAll('#editor-timeline .ed-visual-overlay-clip[data-visual-overlay-index]').forEach(el => {
    el.addEventListener('pointerdown', ev => {
      if (ev.button !== 0 || !ED.comp) return;
      // A clip gesture is an edit gesture, not a canvas scrub. Stop the event
      // before it bubbles to the timeline ruler/playhead pointer handler.
      ev.stopPropagation();
      const index = Number.parseInt(el.dataset.visualOverlayIndex, 10);
      const clip = (ED.comp.visual_overlays || [])[index];
      if (!clip) return;
      const lane = (ED.comp.visual_tracks || []).find(item => String(item.id) === String(clip.track_id || 'overlay-main'));
      if (lane && lane.locked) { editorToast('画面轨道已锁定，先解锁后才能移动或修剪片段。', true); return; }
      const rect = el.getBoundingClientRect();
      const edge = Math.min(8, rect.width / 4);
      const mode = ev.clientX - rect.left <= edge ? 'trim-left'
        : rect.right - ev.clientX <= edge ? 'trim-right' : 'move';
      const allAssets = ED.comp.assets || {};
      const asset = Array.isArray(allAssets) ? allAssets.find(a => a.id === clip.asset_id) : allAssets[clip.asset_id];
      _visualOverlaySuppressClick = null;
      _visualOverlayDrag = {
        el, index, mode, trackId:String(clip.track_id || 'overlay-main'),
        startX:ev.clientX, startY:ev.clientY, moved:false,
        start:Number(clip.start)||0, duration:Number(clip.duration)||0,
        sourceIn:Number(clip.source_in)||0, baseLeft:Number.parseFloat(el.style.left)||0,
        baseWidth:Number.parseFloat(el.style.width)||0,
        assetType:String((asset||{}).type||'').toLowerCase(), assetDuration:Number((asset||{}).duration)||0
      };
      try { el.setPointerCapture(ev.pointerId); } catch (_) {}
    }, true);
  });
  if (window.__tlVisualOverlayDragHandlers) return;
  window.__tlVisualOverlayDragHandlers = true;
  document.addEventListener('pointerdown', () => { if (!_visualOverlayDrag) _visualOverlaySuppressClick = null; }, true);
  document.addEventListener('click', ev => {
    if (!_visualOverlaySuppressClick) return;
    const hit = ev.target && ev.target.closest ? ev.target.closest('.ed-visual-overlay-clip') : null;
    if (hit === _visualOverlaySuppressClick) {
      ev.preventDefault(); ev.stopImmediatePropagation(); _visualOverlaySuppressClick = null;
    }
  }, true);
  document.addEventListener('pointermove', ev => {
    const d = _visualOverlayDrag;
    if (!d || !d.el.isConnected) { _visualOverlayDrag = null; return; }
    const dx = ev.clientX - d.startX;
    const dy = ev.clientY - d.startY;
    if (d.mode === 'move' ? (Math.abs(dx) <= 6 && Math.abs(dy) <= 6 && !d.moved) : (Math.abs(dx) <= 6 && !d.moved)) return;
    d.moved = true; d.el.classList.add('tl-dragging');
    document.querySelectorAll('#editor-timeline .ed-track[data-visual-track-id]').forEach(row => row.classList.remove('tl-track-drop-target'));
    if (d.mode === 'move') {
      const row = document.elementFromPoint(ev.clientX, ev.clientY);
      const target = row && row.closest ? row.closest('#editor-timeline .ed-track[data-visual-track-id]') : null;
      if (target) target.classList.add('tl-track-drop-target');
    }
    const body = d.el.closest('.ed-track-body');
    const width = body ? body.getBoundingClientRect().width : 0;
    if (width <= 0) return;
    const dp = dx / width * 100;
    const minPct = Math.max(0.1, 0.05 / Math.max(0.01, ED.comp.duration || 1) * 100);
    if (d.mode === 'move') d.el.style.left = (d.baseLeft + dp) + '%';
    else if (d.mode === 'trim-left') { d.el.style.left = (d.baseLeft + dp) + '%'; d.el.style.width = Math.max(minPct, d.baseWidth - dp) + '%'; }
    else d.el.style.width = Math.max(minPct, d.baseWidth + dp) + '%';
    d.el.title = d.mode === 'move' ? '松开以移动画面叠加' : (d.mode === 'trim-left' ? '松开以修剪起点' : '松开以修剪终点');
  }, true);
  document.addEventListener('pointerup', ev => {
    const d = _visualOverlayDrag;
    if (!d) return;
    _visualOverlayDrag = null; d.el.classList.remove('tl-dragging');
    const hit = document.elementFromPoint(ev.clientX, ev.clientY);
    const dropRow = hit && hit.closest ? hit.closest('#editor-timeline .ed-track[data-visual-track-id]') : null;
    const nextTrackId = d.mode === 'move' && dropRow ? String(dropRow.dataset.visualTrackId) : d.trackId;
    document.querySelectorAll('#editor-timeline .ed-track[data-visual-track-id]').forEach(row => row.classList.remove('tl-track-drop-target'));
    if (!d.moved || !d.el.isConnected || !ED.comp) return;
    _visualOverlaySuppressClick = d.el;
    const body = d.el.closest('.ed-track-body');
    const width = body ? body.getBoundingClientRect().width : 0;
    if (width <= 0) { editorRenderTimeline(); return; }
    const total = Number(ED.comp.duration)||1;
    const delta = (ev.clientX - d.startX) / width * total;
    const round = n => Math.round(n * 1000) / 1000;
    let command, payload;
    if (d.mode === 'move') {
      command = 'move_visual_overlay';
      if (Math.abs(delta) < 0.05 && nextTrackId === d.trackId) { editorRenderTimeline(); return; }
      payload = {start:round(Math.max(0, Math.min(total - d.duration, d.start + delta))), trackId:nextTrackId};
    } else if (d.mode === 'trim-left') {
      const minDelta = -(d.assetType === 'video' ? Math.min(d.start, d.sourceIn) : d.start);
      const dt = Math.min(Math.max(delta, minDelta), d.duration - 0.05);
      command = 'trim_visual_overlay';
      payload = {start:round(d.start + dt), sourceIn:round(d.sourceIn + (d.assetType === 'video' ? dt : 0)), duration:round(d.duration - dt)};
    } else {
      // Reuse the shared boundary guard: a trim cannot exceed the output
      // project end or the remaining source duration of a video asset.
      const dt = window.TimelineEditMath.trimEndDelta({
        delta, start:d.start, duration:d.duration, total,
        assetType:d.assetType, assetDuration:d.assetDuration, sourceIn:d.sourceIn,
        minDuration:0.05
      });
      command = 'trim_visual_overlay';
      payload = {start:round(d.start), sourceIn:round(d.sourceIn), duration:round(d.duration + dt)};
    }
    editorToast(command === 'move_visual_overlay' ? '正在移动画面叠加……' : '正在修剪画面叠加……');
    executeCommand({command, targetId:'overlay#' + d.index, payload}).then(result => {
      if (result && result.ok) {
        editorToast(command === 'move_visual_overlay' ? '画面叠加位置已更新' : '画面叠加区间已更新');
        return refreshWorkspace();
      }
      editorToast('画面叠加未修改：' + ((result && result.error) || '未知错误'), true);
      editorRenderTimeline();
    }).catch(error => {
      editorToast('画面叠加未修改：' + (error.message || error), true);
      editorRenderTimeline();
    });
  }, true);
  document.addEventListener('pointercancel', () => {
    const d = _visualOverlayDrag;
    if (!d) return;
    _visualOverlayDrag = null;
    document.querySelectorAll('#editor-timeline .ed-track[data-visual-track-id]').forEach(row => row.classList.remove('tl-track-drop-target'));
    if (d.el && d.el.isConnected) editorRenderTimeline();
  }, true);
}
