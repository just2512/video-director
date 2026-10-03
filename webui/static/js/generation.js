async function populateGenModels(){
  // §38 Model 选择器：从 model_config 状态（角色数组）取 image/video 角色模型
  try {
    const list = await apiGet('/api/model_config');
    const sel = document.getElementById('gen-model');
    if (!sel || !Array.isArray(list)) return;
    const models = [];
    for (const ro of list) {
      if ((ro.role === 'image' || ro.role === 'video') && ro.model) {
        models.push(`[${ro.role}] ${ro.model}`);
      }
    }
    if (models.length) {
      sel.innerHTML = '<option value="">自动（按角色配置）</option>' +
        models.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
    }
  } catch(e) {}
}
/** §12/§31.1 Capability Availability：拉取 generation 可用态并驱动按钮禁用。 */
async function refreshGenerationAvailability(){
  try {
    const caps = await apiGet('/api/capabilities');
    const gen = (Array.isArray(caps) ? caps : []).find(c => c.name === 'generation.availability');
    if (gen && gen.generation) {
      CAPS.imageAvailable = !!gen.generation.image.available;
      CAPS.imageReason = gen.generation.image.reason || '';
      CAPS.videoAvailable = !!gen.generation.video.available;
      CAPS.videoReason = gen.generation.video.reason || '';
    }
  } catch (e) { /* 拉取失败保持 null（不误禁用） */ }
  applyGenerationAvailability();
}
/** §31.1 Gate：unavailable → 按钮 disabled + 原因可见 + 不发请求；程序化视觉仍可用。 */
function applyGenerationAvailability(){
  const sel = document.getElementById('gen-type');
  const status = document.getElementById('gen-status');
  const btn = document.querySelector('#pane-editor .card .btn-row button[onclick="editorGenerate()"]');
  if (!sel) return;
  const type = sel.value;
  const unavailable = (type === 'image' && CAPS.imageAvailable === false)
                   || (type === 'video' && CAPS.videoAvailable === false);
  if (btn) {
    btn.disabled = unavailable;
    btn.title = unavailable
      ? ((type === 'image' ? CAPS.imageReason : CAPS.videoReason) || '该生成服务未配置')
      : '';
    btn.textContent = unavailable ? '生成（暂不可用）' : '生成 3 变体';
  }
  if (status && unavailable) {
    const reason = (type === 'image' ? CAPS.imageReason : CAPS.videoReason)
      || '未配置对应的生成服务';
    status.textContent = `${reason}。程序化视觉（类型选「图形」）与本地素材不受影响。`;
  } else if (status && status.textContent.startsWith('暂')) {
    status.textContent = '';
  }
}

async function editorGenerate(){
  if (!ED.pid) { editorToast('先打开项目'); return; }
  const prompt = document.getElementById('gen-prompt').value.trim();
  const type = document.getElementById('gen-type').value;
  // §31.1 禁止「先允许点击再 503」：unavailable 直接拦截，不发起生成请求
  if ((type === 'image' && CAPS.imageAvailable === false)
      || (type === 'video' && CAPS.videoAvailable === false)) {
    const reason = (type === 'image' ? CAPS.imageReason : CAPS.videoReason)
      || '未配置对应的生成服务';
    editorToast(`${reason}。可改用程序化视觉（类型选「图形」）或本地素材。`, true);
    return;
  }
  const modelSel = (document.getElementById('gen-model') || {}).value || '';
  let refSel = (document.getElementById('gen-reference') || {}).value || '';
  const status = document.getElementById('gen-status');
  if (!prompt) { status.textContent = '请填生成提示词'; return; }
  // §90 参考图/参考视频：asset:first-image|first-video 解析为项目首个对应类型素材 id
  if (refSel === 'asset:first-image' || refSel === 'asset:first-video') {
    const wantType = refSel.endsWith('image') ? 'image' : 'video';
    let found = '';
    for (const sc of (ED.comp.scenes||[])) for (const lay of (sc.layers||[])) {
      if (lay.type === wantType && lay.asset_id) { found = lay.asset_id; break; }
    }
    if (!found) { status.textContent = `项目无 ${wantType} 素材可作参考`; return; }
    refSel = 'asset:' + found;
  }
  status.textContent = '生成中……（A/B/C 变体）';
  const r = await apiPost('/api/editor/generate', {project_id: ED.pid, prompt, type,
    model: modelSel.replace(/^\[(image|video)\] /, ''), reference: refSel,
    width: ED.comp ? (ED.comp.width || 1080) : 1080,
    height: ED.comp ? (ED.comp.height || 1920) : 1920});
  const box = document.getElementById('gen-results');
  if (!r.ok) { status.textContent = ''; box.innerHTML = `<div class="err-box">生成失败：${esc(r.error||'未知原因')}</div>`; return; }
  status.textContent = `已生成 ${r.results.length} 个变体（已保存到项目素材库）`;
  box.innerHTML = r.results.map(res =>
    `<div class="proj-row"><span><b>变体 ${esc(res.variant)}</b> <code>${esc(res.asset_id)}</code><br>`+
    `<span class="hint">${layerTypeCn(res.type)} · 来源：${res.provider_id === 'image-llm' ? '图片生成' : esc(res.provider_id || '生成')}</span>`+
    (DEV.mode ? `<span class="hint" style="font-size:11px">指纹 ${esc(res.input_fingerprint || '')}</span>` : '')+`</span>`+
    `<span class="btn-row">`+
    `<button class="btn-sm" data-action="asset.add_broll" data-params='{"assetId":"${esc(res.asset_id)}"}'>加入时间线</button>`+
    `<button class="btn-secondary btn-sm" onclick="edLibDomain('content','assets')">已存项目素材</button>`+
    `</span></div>`).join('');
}
async function addGenToTimeline(assetId){
  // 加入时间线需目标场景：无选中场景则用第一场景
  const sceneId = (ED.sel && ED.sel.type === 'scene') ? ED.sel.id :
                  (ED.comp && ED.comp.scenes[0] ? ED.comp.scenes[0].id : '');
  if (!sceneId) { editorToast('项目无场景'); return; }
  const r = await apiPost('/api/editor/generate/add', {project_id: ED.pid, scene_id: sceneId, asset_id: assetId});
  if (r.ok) {
    ED.version = r.composition_version;
    const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid));
    ED.comp = comp; ED.version = comp.version;
    editorRenderTimeline();
    document.getElementById('gen-status').textContent = `${assetId} 已加入场景 ${sceneId}`;
  } else {
    document.getElementById('gen-status').textContent = '加入失败：' + (r.error || '未知原因');
  }
}

// ---- 版本历史 + Compare（§三十 Versions：每次编辑一版，选中两版对比）----

// §8 拆分导出：inline onclick/window 引用兼容（零行为改动）
Object.assign(window, {
  populateGenModels,
  refreshGenerationAvailability,
  applyGenerationAvailability,
  editorGenerate,
  addGenToTimeline,
});
