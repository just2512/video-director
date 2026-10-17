/* Physical timeline clip clipboard. All mutations go through Editor Command,
 * so clipboard edits receive the same validation, revision history and undo path.
 * Cut deletes the source only after a paste was accepted by the server.
 */
(function installTimelineClipClipboard() {
  if (window.__editorClipClipboardInstalled) return;
  window.__editorClipClipboardInstalled = true;

  const state = window.EDITOR_CLIPBOARD_STATE = {
    selection: null,
    clipboard: null,
    context: null,
    menu: null
  };

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function audioFingerprint(clip) {
    return JSON.stringify([
      String(clip.type || ''),
      String(clip.asset_id || ''),
      Number(clip.start || 0),
      Number(clip.duration || 0),
      Number(clip.source_in || 0),
      clip.source_out == null ? null : Number(clip.source_out),
      String(clip.track_id || ''),
      Number(clip.volume == null ? 1 : clip.volume)
    ]);
  }

  function mainLayerEntry(sceneId, layerIndex) {
    if (!window.ED || !ED.comp) return null;
    const scene = (ED.comp.scenes || []).find(item => String(item.id) === String(sceneId));
    const layer = scene && (scene.layers || [])[Number(layerIndex)];
    if (!scene || !layer || !layer.asset_id || !['video', 'image'].includes(String(layer.type || '').toLowerCase())) return null;
    const tracks = (ED.comp.visual_tracks || []).slice().sort((a,b) => Number(a.order || 0) - Number(b.order || 0));
    const lane = tracks.find(track => !track.locked) || tracks[0];
    if (!lane) return null;
    const sourceIn = Number(layer.source_in || 0);
    const speed = Number(layer.speed || 1);
    const duration = Number(layer.duration || 0);
    const globalStart = Number(scene.start || 0) + Number(layer.start || 0);
    const sourceOut = layer.source_out == null ? sourceIn + duration * speed : Number(layer.source_out);
    // A main-scene video may have a separately represented original-audio window.
    // Copy that sound with the picture atomically instead of pasting a silent duplicate.
    const linkedAudio = String(layer.type || '').toLowerCase() === 'video'
      ? (ED.comp.audio || []).find(audio =>
          String(audio.type || '') === 'original' &&
          String(audio.asset_id || '') === String(layer.asset_id) &&
          audio.linked_visual_overlay_id == null &&
          audio.follows_scene_video !== false &&
          Math.abs(Number(audio.start || 0) - globalStart) <= 0.05 &&
          Math.abs(Number(audio.duration || 0) - duration) <= 0.05 &&
          Math.abs(Number(audio.source_in || 0) - sourceIn) <= 0.05 &&
          (audio.source_out == null || Math.abs(Number(audio.source_out) - sourceOut) <= 0.05) &&
          Math.abs(Number(audio.speed || 1) - speed) <= 0.000001
        )
      : null;
    const clip = {
      id: 'copied-main-layer-' + String(scene.id) + '-' + String(layerIndex),
      asset_id: String(layer.asset_id),
      start: globalStart,
      duration: duration,
      source_in: sourceIn,
      speed: speed,
      track_id: String(lane.id),
      presentation_mode: 'full_frame',
      fit_mode: String(layer.fit_mode || 'cover'),
      source_audio_enabled: false,
      source_audio_volume: 1,
      x: 0, y: 0, width: 1, height: 1, opacity: 1, z_index: 0,
      transform: layer.transform ? clone(layer.transform) : null
    };
    return {
      kind: 'visual', clip: clip,
      origin: {kind:'layer', sceneId:String(scene.id), layerIndex:Number(layerIndex),
        assetId:String(layer.asset_id), sourceIn:sourceIn, sourceOut:sourceOut, speed:speed,
        start:globalStart, duration:duration},
      trackId:String(lane.id),
      sourceIsMainLayer:true,
      linkedAudio: linkedAudio ? clone(linkedAudio) : null,
      linkedAudioTrackId: linkedAudio ? String(linkedAudio.track_id || '') : ''
    };
  }
  function getEntry(element) {
    if (!element || !window.ED || !ED.comp) return null;
    if (element.matches('.ed-layer[data-scene][data-idx]')) {
      return mainLayerEntry(element.dataset.scene, Number.parseInt(element.dataset.idx, 10));
    }
    if (element.matches('.ed-audio-clip[data-audio-index]')) {
      const index = Number.parseInt(element.dataset.audioIndex, 10);
      const clip = (ED.comp.audio || [])[index];
      if (!clip) return null;
      const row = element.closest('.ed-track[data-audio-track-id]');
      return {
        kind: 'audio',
        clip: clone(clip),
        origin: {
          kind: 'audio',
          index: index,
          id: String(clip.id || ''),
          fingerprint: audioFingerprint(clip),
          assetId: String(clip.asset_id || ''),
          type: String(clip.type || '')
        },
        trackId: String(clip.track_id || (row && row.dataset.audioTrackId) || '')
      };
    }
    if (element.matches('.ed-visual-overlay-clip[data-visual-overlay-index]')) {
      const index = Number.parseInt(element.dataset.visualOverlayIndex, 10);
      const clip = (ED.comp.visual_overlays || [])[index];
      if (!clip || !clip.id) return null;
      return {
        kind: 'visual',
        clip: clone(clip),
        origin: {kind: 'visual', id: String(clip.id), assetId: String(clip.asset_id || '')},
        trackId: String(clip.track_id || 'overlay-main')
      };
    }
    return null;
  }

  function resolveEntry(entry) {
    if (!entry || !window.ED || !ED.comp) return null;
    if (entry.kind === 'visual') {
      if (entry.origin && entry.origin.kind === 'layer') {
        const current = mainLayerEntry(entry.origin.sceneId, entry.origin.layerIndex);
        if (!current || current.origin.assetId !== String(entry.origin.assetId || '') ||
            Math.abs(current.origin.sourceIn - Number(entry.origin.sourceIn || 0)) > 1e-6 ||
            Math.abs(current.origin.sourceOut - Number(entry.origin.sourceOut || 0)) > 1e-6 ||
            Math.abs(current.origin.speed - Number(entry.origin.speed || 1)) > 1e-6 ||
            Math.abs(current.origin.start - Number(entry.origin.start || 0)) > 1e-6 ||
            Math.abs(current.origin.duration - Number(entry.origin.duration || 0)) > 1e-6) return null;
        return current;
      }
      const list = ED.comp.visual_overlays || [];
      const index = list.findIndex(item => String(item.id) === String(entry.origin.id));
      if (index < 0) return null;
      const clip = list[index];
      return {
        kind: 'visual', clip: clone(clip),
        origin: {kind: 'visual', id: String(clip.id), assetId: String(clip.asset_id || '')},
        trackId: String(clip.track_id || 'overlay-main'), index: index
      };
    }
    if (entry.kind === 'audio') {
      const list = ED.comp.audio || [];
      const origin = entry.origin || {};
      let index = origin.id
        ? list.findIndex(clip => String(clip.id || '') === String(origin.id))
        : -1;
      if (index < 0 && Number.isInteger(origin.index) &&
          origin.index >= 0 && origin.index < list.length) {
        const candidate = list[origin.index];
        // AudioTrack.id is optional in legacy projects. Keep the selection anchored
        // through ordinary move/trim operations when the indexed media is unchanged.
        if (String(candidate.type || '') === String(origin.type || '') &&
            String(candidate.asset_id || '') === String(origin.assetId || '')) index = origin.index;
      }
      if (index < 0) {
        index = list.findIndex(clip =>
          String(clip.type || '') === String(origin.type || '') &&
          String(clip.asset_id || '') === String(origin.assetId || '') &&
          audioFingerprint(clip) === String(origin.fingerprint || '')
        );
      }
      if (index < 0) return null;
      const clip = list[index];
      return {
        kind: 'audio', clip: clone(clip),
        origin: {
          kind: 'audio', index: index, id: String(clip.id || ''), fingerprint: audioFingerprint(clip),
          assetId: String(clip.asset_id || ''), type: String(clip.type || '')
        },
        trackId: String(clip.track_id || ''), index: index
      };
    }
    return null;
  }

  function trackContext(row) {
    if (!row) return null;
    if (row.dataset.audioTrackId) return {kind:'audio', trackId:String(row.dataset.audioTrackId)};
    if (row.dataset.visualTrackId) return {kind:'visual', trackId:String(row.dataset.visualTrackId)};
    return null;
  }

  function toast(message, isError) {
    if (typeof window.editorToast === 'function') window.editorToast(message, !!isError);
  }

  function paintSelection() {
    const timeline = document.getElementById('editor-timeline');
    if (!timeline) return;
    timeline.querySelectorAll('.ed-clip-clipboard-selected, .ed-clip-cut-pending').forEach(el => {
      el.classList.remove('ed-clip-clipboard-selected', 'ed-clip-cut-pending');
    });
    const selected = resolveEntry(state.selection);
    if (selected) {
      state.selection = selected;
      timeline.querySelectorAll('.ed-audio-clip[data-audio-index], .ed-visual-overlay-clip[data-visual-overlay-index]').forEach(el => {
        const current = getEntry(el);
        if (!current) return;
        const match = current.kind === 'visual'
          ? selected.kind === 'visual' && current.origin.id === selected.origin.id
          : selected.kind === 'audio' && current.index === selected.index;
        if (match) el.classList.add('ed-clip-clipboard-selected');
      });
    }
    if (state.clipboard && state.clipboard.cut && state.clipboard.origin) {
      timeline.querySelectorAll('.ed-audio-clip[data-audio-index], .ed-visual-overlay-clip[data-visual-overlay-index]').forEach(el => {
        const current = getEntry(el);
        if (!current || current.kind !== state.clipboard.kind) return;
        const match = current.kind === 'visual'
          ? current.origin.id === state.clipboard.origin.id
          : current.index === state.clipboard.origin.index &&
            current.origin.fingerprint === state.clipboard.origin.fingerprint;
        if (match) el.classList.add('ed-clip-cut-pending');
      });
    }
  }

  function chooseSelection(element) {
    const entry = getEntry(element);
    if (!entry) return null;
    state.selection = entry;
    state.context = trackContext(element.closest('.ed-track'));
    paintSelection();
    return entry;
  }

  function isProtectedTtsNarration(entry) {
    return !!entry && entry.kind === 'audio' && String((entry.clip || {}).type || '') === 'narration';
  }
  function isMainLayerSource(entry) {
    return !!entry && entry.kind === 'visual' && !!entry.origin && entry.origin.kind === 'layer';
  }
  function copySelection(cut) {
    const selected = resolveEntry(state.selection);
    if (!selected) {
      toast('先选择一条独立画面或音频片段。', true);
      return false;
    }
    if (isProtectedTtsNarration(selected)) {
      toast('系统主旁白由 TTS 渲染链管理，不能作为普通片段复制/剪切；额外旁白请先作为独立音频素材导入。', true);
      return false;
    }
    if (cut && isMainLayerSource(selected)) {
      toast('主画面镜头可以复制；若要剪切，请先“展开为自由时间线”再操作，避免破坏场景时长约束。', true);
      return false;
    }
    state.selection = selected;
    const clipboard = {
      kind: selected.kind,
      clip: clone(selected.clip),
      origin: clone(selected.origin),
      trackId: selected.trackId,
      cut: !!cut
    };
    if (selected.kind === 'visual') {
      const linkedAudio = selected.linkedAudio ||
        (ED.comp.audio || []).find(clip => String(clip.linked_visual_overlay_id || '') === String(selected.clip.id));
      if (linkedAudio) {
        clipboard.linkedAudio = clone(linkedAudio);
        clipboard.linkedAudioTrackId = String(selected.linkedAudioTrackId || linkedAudio.track_id || '');
      }
    }
    state.clipboard = clipboard;
    paintSelection();
    toast(cut
      ? '已标记剪切；粘贴成功后才会删除原片段。'
      : (isMainLayerSource(selected) ? '主画面镜头已复制；粘贴时会作为全画幅独立片段放入自由画面轨。' : '片段已复制，可以在播放头位置粘贴。'));
    return true;
  }

  function findAudioOrigin(origin) {
    const list = (ED.comp && ED.comp.audio) || [];
    if (origin.id) {
      const stableIndex = list.findIndex(clip => String(clip.id || '') === String(origin.id));
      if (stableIndex >= 0) return stableIndex;
    }
    if (Number.isInteger(origin.index) && origin.index >= 0 && origin.index < list.length) {
      const clip = list[origin.index];
      if (String(clip.type || '') === String(origin.type || '') &&
          String(clip.asset_id || '') === String(origin.assetId || '') &&
          audioFingerprint(clip) === String(origin.fingerprint || '')) return origin.index;
    }
    return list.findIndex(clip =>
      String(clip.type || '') === String(origin.type || '') &&
      String(clip.asset_id || '') === String(origin.assetId || '') &&
      audioFingerprint(clip) === String(origin.fingerprint || '')
    );
  }

  async function pasteClipboard() {
    const item = state.clipboard;
    if (!item || !item.clip || !window.ED || !ED.pid || !ED.comp) {
      toast('剪贴板为空，请先选择片段并复制。', true);
      return {ok:false};
    }
    const total = Math.max(0, Number(ED.comp.duration) || 0);
    const duration = Math.max(0, Number(item.clip.duration) || 0);
    if (!total || !duration || duration > total) {
      toast('片段时长超出当前工程范围，未粘贴。', true);
      return {ok:false};
    }
    const rawPlayhead = Number(window.PLAYHEAD && PLAYHEAD.at) || 0;
    const start = Math.max(0, Math.min(rawPlayhead, total - duration));
    const preferred = state.context && state.context.kind === item.kind ? state.context : null;
    let trackId = String((preferred && preferred.trackId) || item.trackId || '');
    if (item.kind === 'audio') {
      const lanes = ED.comp.audio_tracks || [];
      if (!lanes.some(lane => String(lane.id) === trackId)) trackId = String((lanes[0] || {}).id || '');
      if (!trackId) { toast('工程没有可用的音频轨道。', true); return {ok:false}; }
    } else {
      const lanes = (ED.comp.visual_tracks || []).slice().sort((a,b) => Number(a.order || 0) - Number(b.order || 0));
      if (!lanes.some(lane => String(lane.id) === trackId)) trackId = String((lanes[0] || {}).id || '');
      if (!trackId) { toast('工程没有可用的画面轨道。', true); return {ok:false}; }
    }

    let linkedAudioTrackId = '';
    if (item.kind === 'visual' && item.linkedAudio) {
      const audioLanes = (ED.comp.audio_tracks || []).slice().sort((a,b) => Number(a.order || 0) - Number(b.order || 0));
      const preferredAudioLane = audioLanes.find(lane => String(lane.id) === String(item.linkedAudioTrackId) && !lane.locked);
      const fallbackAudioLane = audioLanes.find(lane => !lane.locked);
      const chosenAudioLane = preferredAudioLane || fallbackAudioLane;
      if (!chosenAudioLane) { toast('该视频带有独立原声，但项目没有可用的未锁定音轨，未粘贴。', true); return {ok:false}; }
      linkedAudioTrackId = String(chosenAudioLane.id);
    }
    const command = item.kind === 'audio' ? 'paste_audio_clip' : 'paste_visual_overlay';
    const targetId = item.kind === 'audio' ? 'audio-tracks' : 'overlays';
    let result;
    try {
      result = await window.executeCommand({
        command: command,
        targetId: targetId,
        payload: {
          clip:clone(item.clip), start:start, trackId:trackId,
          ...(item.kind === 'visual' && item.linkedAudio ? {linkedAudio:clone(item.linkedAudio), audioTrackId:linkedAudioTrackId} : {})
        }
      });
    } catch (error) {
      toast('粘贴失败：' + (error.message || error), true);
      return {ok:false};
    }
    if (!result || !result.ok) {
      toast('粘贴失败：' + ((result && result.error) || '工程没有变化'), true);
      return {ok:false, error:(result && result.error) || 'PASTE_REJECTED'};
    }

    // Capture the newly written stable clip ID before a cut removes the source.
    let pastedId = '';
    if (item.kind === 'visual') {
      const clips = ED.comp.visual_overlays || [];
      const last = clips.length ? clips[clips.length - 1] : null;
      const startTolerance = Math.max(0.001, 1 / Math.max(1, Number(ED.comp.project && ED.comp.project.fps) || 30) + 1e-6);
      if (last && String(last.asset_id || '') === String(item.clip.asset_id || '') && Math.abs(Number(last.start || 0) - Number(start)) <= startTolerance) pastedId = String(last.id || '');
    } else {
      const clips = ED.comp.audio || [];
      const last = clips.length ? clips[clips.length - 1] : null;
      const startTolerance = Math.max(0.001, 1 / Math.max(1, Number(ED.comp.project && ED.comp.project.fps) || 30) + 1e-6);
      if (last && String(last.asset_id || '') === String(item.clip.asset_id || '') && String(last.track_id || '') === String(trackId) && Math.abs(Number(last.start || 0) - Number(start)) <= startTolerance) pastedId = String(last.id || '');
    }

    if (item.cut && item.origin) {
      let removed;
      try {
        if (item.kind === 'visual') {
          removed = await window.executeCommand({
            command:'remove_visual_overlay', targetId:String(item.origin.id), payload:{}
          });
        } else {
          const index = findAudioOrigin(item.origin);
          removed = index >= 0
            ? await window.executeCommand({command:'remove_audio_clip', targetId:'audio#' + index, payload:{}})
            : {ok:false, error:'原片段已变化，未自动删除'};
        }
      } catch (error) {
        removed = {ok:false, error:error.message || String(error)};
      }
      item.cut = false;
      if (!removed || !removed.ok) {
        toast('片段已粘贴，但原片段未能删除：' + ((removed && removed.error) || '请检查时间线。'), true);
      }
    }

    try { await window.refreshWorkspace(); }
    catch (error) { toast('片段已写入工程，但时间线刷新失败：' + (error.message || error), true); }
    if (pastedId && ED.comp) {
      if (item.kind === 'visual') {
        const index = (ED.comp.visual_overlays || []).findIndex(clip => String(clip.id || '') === pastedId);
        const element = index >= 0 ? document.querySelector('#editor-timeline .ed-visual-overlay-clip[data-visual-overlay-index="' + index + '"]') : null;
        if (element) state.selection = getEntry(element);
      } else {
        const index = (ED.comp.audio || []).findIndex(clip => String(clip.id || '') === pastedId);
        const element = index >= 0 ? document.querySelector('#editor-timeline .ed-audio-clip[data-audio-index="' + index + '"]') : null;
        if (element) state.selection = getEntry(element);
      }
    }
    if (!item.cut) toast('片段已粘贴到时间线。');
    paintSelection();
    return {ok:true};
  }

  async function deleteSelection() {
    const selected = resolveEntry(state.selection);
    if (!selected) {
      toast('先选择需要删除的独立画面或音频片段。', true);
      return {ok:false};
    }
    if (isProtectedTtsNarration(selected)) {
      toast('系统主旁白不能作为普通片段删除；请使用音量/静音控制。', true);
      return {ok:false, error:'TTS_NARRATION_PIPELINE_CLIP'};
    }
    if (isMainLayerSource(selected)) {
      toast('主画面镜头不能作为自由轨片段直接删除；若要删除/剪切，请先“展开为自由时间线”。', true);
      return {ok:false, error:'MAIN_SCENE_LAYER_REQUIRES_FREE_TIMELINE'};
    }
    const command = selected.kind === 'audio' ? 'remove_audio_clip' : 'remove_visual_overlay';
    const targetId = selected.kind === 'audio' ? 'audio#' + selected.index : selected.origin.id;
    let result;
    try {
      result = await window.executeCommand({command:command, targetId:targetId, payload:{}});
    } catch (error) {
      toast('删除失败：' + (error.message || error), true);
      return {ok:false};
    }
    if (!result || !result.ok) {
      toast('删除未执行：' + ((result && result.error) || '未知错误'), true);
      return {ok:false};
    }
    state.selection = null;
    await window.refreshWorkspace();
    toast('片段已删除，可使用 Ctrl+Z 撤销。');
    return {ok:true};
  }

  function closeMenu() {
    if (state.menu) state.menu.remove();
    state.menu = null;
  }

  function showMenu(event, context, hasClip) {
    closeMenu();
    state.context = context || null;
    const canPaste = !!(state.clipboard && context && state.clipboard.kind === context.kind);
    const actions = [];
    if (hasClip) {
      const selected = resolveEntry(state.selection);
      if (isProtectedTtsNarration(selected)) {
        actions.push(['protected','系统主旁白由 TTS 渲染链管理']);
      } else if (isMainLayerSource(selected)) {
        actions.push(['copy','复制镜头　Ctrl+C']);
        actions.push(['main-layer-help','复制后将在自由画面轨粘贴']);
      } else {
        actions.push(['copy','复制　Ctrl+C']);
        actions.push(['cut','剪切　Ctrl+X']);
        actions.push(['delete','删除　Delete']);
      }
    }
    if (canPaste) actions.push(['paste','粘贴到播放头　Ctrl+V']);
    actions.push(['close','取消']);
    const menu = document.createElement('div');
    menu.className = 'ed-clip-context-menu';
    menu.setAttribute('role','menu');
    actions.forEach(action => {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.clipAction = action[0];
      button.textContent = action[1];
      menu.appendChild(button);
    });
    menu.addEventListener('pointerdown', event => event.stopPropagation());
    menu.addEventListener('click', async event => {
      const button = event.target.closest('button[data-clip-action]');
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      const action = button.dataset.clipAction;
      closeMenu();
      if (action === 'protected') toast('系统主旁白不能作为普通片段编辑；额外旁白请添加为独立音频片段。', true);
      else if (action === 'main-layer-help') toast('此镜头会粘贴成全画幅自由轨片段。若要在主序列内剪切/重排，请使用“展开为自由时间线”。');
      else if (action === 'copy') copySelection(false);
      else if (action === 'cut') copySelection(true);
      else if (action === 'delete') await deleteSelection();
      else if (action === 'paste') await pasteClipboard();
    });
    document.body.appendChild(menu);
    const width = 205, height = actions.length * 36 + 8;
    menu.style.left = Math.max(8, Math.min(event.clientX, window.innerWidth - width - 8)) + 'px';
    menu.style.top = Math.max(8, Math.min(event.clientY, window.innerHeight - height - 8)) + 'px';
    state.menu = menu;
  }

  const style = document.createElement('style');
  style.id = 'editor-clip-clipboard-style';
  style.textContent =
    '.ed-clip-clipboard-selected{outline:2px solid var(--accent,#63a7ff)!important;outline-offset:-2px;z-index:3}' +
    '.ed-clip-cut-pending{opacity:.42!important;filter:grayscale(.7)}' +
    '.ed-clip-context-menu{position:fixed;z-index:3000;min-width:205px;padding:4px;background:var(--panel-bg,#202126);color:var(--text,#f1f1f1);border:1px solid var(--border,#555);border-radius:8px;box-shadow:0 8px 28px rgba(0,0,0,.35)}' +
    '.ed-clip-context-menu button{display:block;width:100%;padding:8px 10px;border:0;border-radius:5px;color:inherit;background:transparent;text-align:left;cursor:pointer}' +
    '.ed-clip-context-menu button:hover{background:var(--hover-bg,rgba(255,255,255,.1))}';
  document.head.appendChild(style);

  document.addEventListener('pointerdown', event => {
    if (!event.target.closest('#editor-timeline')) return;
    const row = event.target.closest('.ed-track[data-audio-track-id], .ed-track[data-visual-track-id]');
    const layer = event.target.closest('.ed-layer[data-scene][data-idx]');
    state.context = trackContext(row) || (layer ? (() => {
      const entry = mainLayerEntry(layer.dataset.scene, Number.parseInt(layer.dataset.idx, 10));
      return entry ? {kind:'visual', trackId:entry.trackId} : null;
    })() : null);
    const clip = event.target.closest('.ed-audio-clip[data-audio-index], .ed-visual-overlay-clip[data-visual-overlay-index], .ed-layer[data-scene][data-idx]');
    if (clip) chooseSelection(clip);
  }, true);

  document.addEventListener('contextmenu', event => {
    if (!event.target.closest('#editor-timeline') || !window.ED || !ED.comp) return;
    const clip = event.target.closest('.ed-audio-clip[data-audio-index], .ed-visual-overlay-clip[data-visual-overlay-index], .ed-layer[data-scene][data-idx]');
    const row = (clip || event.target).closest('.ed-track[data-audio-track-id], .ed-track[data-visual-track-id]');
    const layer = clip && clip.matches('.ed-layer[data-scene][data-idx]') ? clip : null;
    const layerEntry = layer ? mainLayerEntry(layer.dataset.scene, Number.parseInt(layer.dataset.idx, 10)) : null;
    const context = trackContext(row) || (layerEntry ? {kind:'visual', trackId:layerEntry.trackId} : null);
    if (!context) return;
    if (clip) chooseSelection(clip);
    event.preventDefault();
    event.stopPropagation();
    showMenu(event, context, !!clip);
  }, true);

  document.addEventListener('keydown', async event => {
    const target = event.target;
    if (target && target.closest && target.closest('input, textarea, select, [contenteditable="true"]')) return;
    if (!window.ED || !ED.pid || !ED.comp) return;
    const key = String(event.key || '').toLowerCase();
    const modifier = event.ctrlKey || event.metaKey;
    if (modifier && key === 'c' && resolveEntry(state.selection)) {
      event.preventDefault(); copySelection(false); return;
    }
    if (modifier && key === 'x' && resolveEntry(state.selection)) {
      event.preventDefault(); copySelection(true); return;
    }
    if (modifier && key === 'v' && state.clipboard) {
      event.preventDefault(); await pasteClipboard(); return;
    }
    if (key === 'delete' && resolveEntry(state.selection)) {
      event.preventDefault(); await deleteSelection(); return;
    }
    if (key === 'escape') closeMenu();
  }, true);

  document.addEventListener('editor:timeline-rendered', paintSelection);
  window.editorClipCopy = copySelection;
  window.editorClipPaste = pasteClipboard;
  window.editorClipDeleteSelected = deleteSelection;
})();