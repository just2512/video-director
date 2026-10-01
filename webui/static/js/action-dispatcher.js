// 方案§12/§13/§59 Action Dispatcher：按钮 = data-action 声明，前端只负责触发。
// <button data-action="audio.preserve" data-target="selection">保留原声</button>
// dispatcher 统一解析：action id → /api/actions 契约（§62 同源）→ Command。
//
// 兼容层：既有 onclick 函数仍工作（渐进迁移），但新 UI 用 data-action 声明。
// dispatcher 优先调 action_registry 前端镜像（/api/actions 的 groups），
// 找不到时回落到 window[action.label 对应的旧函数]（诚实降级，不静默失败）。

window.ACTION_DISPATCH = {
  // action id → 执行器（参数来自 data-params JSON 或无参）
  'audio.preserve': () => editorCmd('set_original_audio', {mode: 'preserve', volume: 0.8, duckAgainst: 'narration'}, '', true),
  'audio.mute_original': () => editorCmd('set_original_audio', {mode: 'mute', volume: 0.0, duckAgainst: 'narration'}, '', true),
  'audio.original_volume': () => (typeof editorLayerOriginalVolume === 'function'
    ? editorLayerOriginalVolume('') : editorOriginalAudioVolume()),
  'music.volume': (p) => editorCmd('set_audio_volume', {volume: (p && p.volume) ?? 0.3}, 'bgm', true),
  'music.remove': () => editorCmd('remove_audio_track', {type: 'bgm'}, '', true),
  'subtitle.generate': () => editorSubtitleGenerate(),
  'subtitle.style': () => editorSubtitleStyleOpen(),
  'watermark.add': () => editorWatermarkAdd(),
  'watermark.remove': () => editorCmd('remove_watermark', {}, '', true),
  'asset.search': () => (SEL.selection && SEL.selection.type !== 'project'
    ? editorAutoFindAsset((SEL.selection.id || '').split('#')[0])
    : editorToast('先选中场景或图层，再自动找素材', true)),
  'asset.replace': () => {
    if (!SEL.selection) { editorToast('先选中场景或图层', true); return; }
    const [sceneId, idxS] = (SEL.selection.id || '').split('#');
    editorOpenAssetPicker(sceneId || SEL.selection.id, parseInt(idxS) || 0);
  },
  'generate.image': () => editorSceneGenerate((SEL.selection || {}).id || ''),
  'render.draft': () => editorDraftRender(),
  'render.final': () => editorRenderFinal(),
  'verify.run': () => editorPreflight(),
};

/** §12 统一分发：[data-action] 按钮点击 → ACTION_DISPATCH[action]。 */
function actionDispatch(el, ev){
  const actionId = el.getAttribute('data-action');
  if (!actionId) return true;  // 无声明走默认 onclick
  const fn = window.ACTION_DISPATCH[actionId];
  if (typeof fn !== 'function') {
    editorToast(`动作 ${actionId} 尚未接线（已诚实报告，不会静默失败）`, true);
    return ev ? ev.preventDefault() : false;
  }
  let params = {};
  const raw = el.getAttribute('data-params');
  if (raw) { try { params = JSON.parse(raw); } catch (e) { /* params 忽略 */ } }
  const r = fn(params);
  if (ev) ev.preventDefault();
  return r;
}

// 全局事件委托：任何 [data-action] 按钮点击统一进 dispatcher（§12 不再散装 onclick）
document.addEventListener('click', (ev) => {
  const el = ev.target.closest && ev.target.closest('[data-action]');
  if (!el) return;
  if (el.disabled) return;
  actionDispatch(el, ev);
});

Object.assign(window, {actionDispatch, ACTION_DISPATCH});
