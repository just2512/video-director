// §61/§35/§36 Voice/Music Picker：音色试听 + BGM/SFX 应用
// （试听走 /api/voice_preview / /api/audio_preview；应用一律 Command → Revision）。

// 2026-10-26 用户报障：试听必须互斥——点第二个时第一个立刻停，
// 不能多个声音同时响（谁分得清）。全局单例 Audio，播放前先停旧的。
let _previewAudio = null;
function _stopPreview(){
  if (_previewAudio) {
    try { _previewAudio.pause(); } catch (e) {}
    try { _previewAudio.src = ''; } catch (e) {}
    _previewAudio = null;
  }
}
function _playPreviewSync(url){
  _stopPreview();
  const a = new Audio(url);
  _previewAudio = a;
  // 加载失败（音色不可用/网络失败）要反馈，不能静默没反应
  a.addEventListener('error', () => {
    editorToast('试听失败：' + (url.includes('voice_preview') ? '音色合成失败或网络不可用' : '音频加载失败或网络不可用'), true);
  });
  const p = a.play();
  if (p && p.catch) p.catch((e) => editorToast('试听失败：' + e, true));
  return a;
}
// 目前 edge-tts 音色合成是同步端点（缓存命中则秒回），但首次合成可能 1-3 秒；
// 试听先切播放态提示，避免用户以为没反应。
function _markTrying(btnEl){
  if (!btnEl) return;
  const old = btnEl.textContent;
  btnEl.textContent = '试听中…';
  btnEl.disabled = true;
  setTimeout(() => { btnEl.textContent = old; btnEl.disabled = false; }, 1500);
}

function edVoiceTry(voiceId, provider, btnEl){
  const prov = provider || 'edge-tts';
  try { _playPreviewSync('/api/voice_preview?voice=' + encodeURIComponent(voiceId) + '&provider=' + encodeURIComponent(prov)); _markTrying(btnEl); }
  catch (e) { editorToast('试听失败：' + e, true); }
}
function edLibAudioTry(assetId, btnEl){
  try { _playPreviewSync('/api/audio_preview?asset_id=' + encodeURIComponent(assetId)); _markTrying(btnEl); }
  catch (e) { editorToast('试听失败：' + e, true); }
}
async function edLibAudioApply(kind, assetId){
  // §82：音频应用 → executeAction（music.add 建 BGM 轨 / sfx.add 建音效轨）
  const action = kind === 'bgm' ? 'music.add' : 'sfx.add';
  const r = await executeAction(action, {assetId, volume: 0.3}, 'project');
  if (r && r.ok) editorToast('已应用（音量 0.3，可在「整体调音」调整）');
  else editorToast('应用失败：' + ((r && r.error) || '未知'), true);
}

Object.assign(window, {
  edVoiceTry, edLibAudioTry, edLibAudioApply, _stopPreview,
});