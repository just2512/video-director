/* Insert a registered audio-library asset as an independent timeline clip. */
(function installAudioTrackAssetPicker() {
  if (window.__audioTrackAssetPickerInstalled) return;
  window.__audioTrackAssetPickerInstalled = true;
  let activeTrackId = '';

  function toast(message, isError) {
    if (typeof window.editorToast === 'function') window.editorToast(message, !!isError);
  }
  function close() {
    const mask = document.getElementById('audio-asset-picker-mask');
    if (mask) mask.remove();
  }
  function roleLabel(role) {
    return ({
      dialogue: '人声 / 旁白录音（独立片段）',
      bgm: '背景音乐（支持循环延长）',
      sfx: '音效',
      ambience: '环境音（支持循环延长）'
    })[role] || role;
  }
  function selectedChoice() {
    const select = document.getElementById('audio-asset-picker-asset');
    const state = window.AUDIO_ASSET_PICKER_STATE;
    if (!select || !state) return null;
    return state.choices.find(item => String(item.id) === String(select.value)) || null;
  }
  function setDefaultsFromChoice(resetRole) {
    const choice = selectedChoice();
    const roleSelect = document.getElementById('audio-asset-picker-role');
    const durationInput = document.getElementById('audio-asset-picker-duration');
    const total = Math.max(0, Number(window.ED && ED.comp && ED.comp.duration) || 0);
    const start = Math.max(0, Number((window.PLAYHEAD || {}).at) || 0);
    const remaining = Math.max(0.05, total - start);
    if (!choice || !roleSelect || !durationInput) return;
    const suggestedRole = ['bgm', 'sfx', 'ambience'].includes(String(choice.role || '').toLowerCase())
      ? String(choice.role).toLowerCase()
      : 'dialogue';
    if (resetRole !== false) roleSelect.value = suggestedRole;
    const role = roleSelect.value;
    if (role === 'bgm' || role === 'ambience') {
      durationInput.value = Math.min(15, remaining).toFixed(2);
    } else if (Number.isFinite(Number(choice.duration)) && Number(choice.duration) > 0) {
      durationInput.value = Math.min(Number(choice.duration), remaining).toFixed(2);
    } else {
      // Leave empty to use the probed source duration on the write path.
      durationInput.value = '';
    }
    const volume = document.getElementById('audio-asset-picker-volume');
    if (volume) volume.value = role === 'dialogue' ? '1.0' : '0.6';
    const hint = document.getElementById('audio-asset-picker-hint');
    if (hint) hint.textContent = roleLabel(role) + '；素材时长会在写入时核验，BGM/环境音从源起点开始时可循环。';
  }

  async function open(event, trackId) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    if (!window.ED || !ED.pid || !ED.comp) { toast('先打开项目再添加音频。', true); return; }
    const lane = (ED.comp.audio_tracks || []).find(item => String(item.id) === String(trackId));
    if (!lane) { toast('音轨已变化，请刷新后重试。', true); return; }
    if (lane.locked) { toast('该音轨已锁定，请先解锁。', true); return; }
    close();
    let choices;
    try {
      const response = await apiGet('/api/audio_library');
      if (!Array.isArray(response)) throw new Error((response && response.error) || '音频素材库响应格式错误');
      choices = response;
    } catch (error) {
      toast('读取音频素材库失败：' + (error.message || error), true);
      return;
    }
    choices = (Array.isArray(choices) ? choices : [])
      .filter(item => item && item.id && item.available !== false &&
        ['dialogue', 'bgm', 'sfx', 'ambience'].includes(String(item.role || '').toLowerCase()));
    if (!choices.length) {
      toast('音频素材库没有可用素材；请先在音频库登记 BGM、SFX 或环境音。', true);
      return;
    }
    activeTrackId = String(trackId);
    const mask = document.createElement('div');
    mask.id = 'audio-asset-picker-mask';
    mask.className = 'audio-clip-inspector-mask';
    mask.innerHTML =
      '<section class="audio-clip-inspector" role="dialog" aria-modal="true" aria-labelledby="audio-asset-picker-title">' +
      '<header><div><b id="audio-asset-picker-title">向「' + escapeText(lane.name || '音轨') + '」添加音频</b>' +
      '<div class="hint">新增独立片段；移动、裁剪、复制和粘贴不修改源文件。</div></div>' +
      '<button type="button" class="btn-secondary btn-sm" id="audio-asset-picker-close">关闭</button></header>' +
      '<label for="audio-asset-picker-asset">音频素材</label><select id="audio-asset-picker-asset"></select>' +
      '<label for="audio-asset-picker-role">播放角色</label><select id="audio-asset-picker-role">' +
      '<option value="dialogue">人声 / 旁白录音（独立片段）</option>' +
      '<option value="bgm">背景音乐（支持循环延长）</option>' +
      '<option value="sfx">音效</option><option value="ambience">环境音（支持循环延长）</option></select>' +
      '<div class="audio-clip-field-row">' +
      '<div><label for="audio-asset-picker-start">时间线起点（秒）</label><input id="audio-asset-picker-start" type="number" min="0" step="0.05"></div>' +
      '<div><label for="audio-asset-picker-duration">片段时长（秒，可留空）</label><input id="audio-asset-picker-duration" type="number" min="0.05" step="0.05"></div></div>' +
      '<div class="audio-clip-field-row"><div><label for="audio-asset-picker-volume">音量（0–2）</label><input id="audio-asset-picker-volume" type="number" min="0" max="2" step="0.05" value="1"></div>' +
      '<div><label for="audio-asset-picker-fade">淡入（秒）</label><input id="audio-asset-picker-fade" type="number" min="0" step="0.05" value="0"></div></div>' +
      '<p id="audio-asset-picker-hint" class="hint"></p>' +
      '<footer><button type="button" class="btn-secondary" id="audio-asset-picker-cancel">取消</button>' +
      '<button type="button" class="btn-primary" id="audio-asset-picker-add">添加到时间线</button></footer></section>';
    const state = window.AUDIO_ASSET_PICKER_STATE = {choices:choices, trackId:activeTrackId};
    const assetSelect = mask.querySelector('#audio-asset-picker-asset');
    choices.forEach(choice => {
      const option = document.createElement('option');
      option.value = String(choice.id);
      option.textContent = String(choice.label || choice.id) + ' · ' + roleLabel(choice.role);
      assetSelect.appendChild(option);
    });
    const startInput = mask.querySelector('#audio-asset-picker-start');
    const total = Math.max(0, Number(ED.comp.duration || (ED.comp.project && ED.comp.project.duration)) || 0);
    const start = Math.max(0, Math.min(Math.max(0, total - 0.05), Number((window.PLAYHEAD || {}).at) || 0));
    startInput.max = String(total);
    startInput.value = start.toFixed(2);
    mask.querySelector('#audio-asset-picker-close').onclick = close;
    mask.querySelector('#audio-asset-picker-cancel').onclick = close;
    mask.querySelector('#audio-asset-picker-asset').addEventListener('change', () => setDefaultsFromChoice(true));
    mask.querySelector('#audio-asset-picker-role').addEventListener('change', () => setDefaultsFromChoice(false));
    mask.querySelector('#audio-asset-picker-add').onclick = confirmAdd;
    mask.addEventListener('click', e => { if (e.target === mask) close(); });
    document.body.appendChild(mask);
    setDefaultsFromChoice(true);
  }

  function escapeText(value) {
    const node = document.createElement('span');
    node.textContent = String(value == null ? '' : value);
    return node.innerHTML;
  }

  async function confirmAdd() {
    const state = window.AUDIO_ASSET_PICKER_STATE;
    const choice = selectedChoice();
    const mask = document.getElementById('audio-asset-picker-mask');
    if (!state || !choice || !mask) return;
    const role = String(mask.querySelector('#audio-asset-picker-role').value || 'dialogue');
    const start = Number(mask.querySelector('#audio-asset-picker-start').value);
    const durationRaw = String(mask.querySelector('#audio-asset-picker-duration').value || '').trim();
    const volume = Number(mask.querySelector('#audio-asset-picker-volume').value);
    const fadeIn = Number(mask.querySelector('#audio-asset-picker-fade').value);
    const projectDuration = Math.max(0, Number(ED.comp.duration || (ED.comp.project && ED.comp.project.duration)) || 0);
    const remaining = Math.max(0, projectDuration - start);
    if (!Number.isFinite(start) || start < 0 || start >= projectDuration) {
      toast('时间线起点必须位于工程时长内。', true); return;
    }
    if (durationRaw && (!Number.isFinite(Number(durationRaw)) || Number(durationRaw) <= 0 ||
        Number(durationRaw) > remaining + 1e-6)) {
      toast('片段时长必须大于 0，且不能超过工程剩余时长。', true); return;
    }
    if (!Number.isFinite(volume) || volume < 0 || volume > 2) {
      toast('音量必须在 0–2 之间。', true); return;
    }
    if (!Number.isFinite(fadeIn) || fadeIn < 0) {
      toast('淡入时长必须是非负数字。', true); return;
    }
    const payload = {
      assetId: String(choice.id),
      trackId: String(state.trackId),
      role: role,
      start: start,
      volume: volume,
      fadeIn: fadeIn
    };
    if (durationRaw) payload.duration = Number(durationRaw);
    const button = mask.querySelector('#audio-asset-picker-add');
    button.disabled = true;
    button.textContent = '正在添加…';
    try {
      const result = await window.executeCommand({
        command:'add_audio_asset_clip',
        targetId:'audio-tracks',
        payload:payload
      });
      if (!result || !result.ok) {
        toast('音频片段未添加：' + ((result && result.error) || '未知错误'), true);
        button.disabled = false;
        button.textContent = '添加到时间线';
        return;
      }
      close();
      await window.refreshWorkspace();
      toast('音频片段已添加，可拖动移动或修剪边缘。');
    } catch (error) {
      button.disabled = false;
      button.textContent = '添加到时间线';
      toast('音频片段未添加：' + (error.message || error), true);
    }
  }

  window.editorAudioAssetPick = open;
  window.editorAudioAssetPickConfirm = confirmAdd;
})();