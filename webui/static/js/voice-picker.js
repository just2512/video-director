// §61/§35/§36 Voice/Music Picker：音色试听 + BGM/SFX 应用
// （试听走 /api/voice_preview / /api/audio_preview；应用一律 Command → Revision）。
function edVoiceTry(voiceId){
  try { new Audio('/api/voice_preview?voice=' + encodeURIComponent(voiceId)).play(); }
  catch (e) { editorToast('试听失败：' + e, true); }
}
function edLibAudioTry(assetId){
  try { new Audio('/api/audio_preview?asset_id=' + encodeURIComponent(assetId)).play(); }
  catch (e) { editorToast('试听失败：' + e, true); }
}
async function edLibAudioApply(kind, assetId){
  // §82：音频应用 → executeAction（music.add 建 BGM 轨 / sfx.add 建音效轨）
  const action = kind === 'bgm' ? 'music.add' : 'sfx.add';
  const r = await executeAction(action, {assetId, volume: 0.3}, 'project');
  if (r && r.ok) editorToast('已应用（音量 0.3，可在「整体调音」调整）');
}

Object.assign(window, {
  edVoiceTry, edLibAudioTry, edLibAudioApply,
});
