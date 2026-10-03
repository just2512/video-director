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
  const r = await executeCommand({command: 'set_audio_volume',
    targetId: kind === 'bgm' ? 'bgm' : 'sfx', payload: {assetId, volume: 0.3}});
  editorToast(r.ok ? '已应用（音量 0.3，可在项目「整体调音」调整）' : '应用失败：' + (r.error || ''), !r.ok);
}

Object.assign(window, {
  edVoiceTry, edLibAudioTry, edLibAudioApply,
});
