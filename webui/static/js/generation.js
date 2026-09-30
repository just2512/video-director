function populateGenModels(){
  // §38 Model 选择器：从 model_config 状态（角色数组）取 image/video 角色模型
  try {
    fetch('/api/model_config').then(r => r.json()).then(list => {
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
    });
  } catch(e) {}
}
async function editorGenerate(){
  if (!ED.pid) { alert('先打开项目'); return; }
  const prompt = document.getElementById('gen-prompt').value.trim();
  const type = document.getElementById('gen-type').value;
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
  if (!r.ok) { status.textContent = ''; box.innerHTML = `<div class="err-box">❌ ${esc(r.error||'生成失败')}</div>`; return; }
  status.textContent = `✅ 已生成 ${r.results.length} 个变体（已保存到素材库）`;
  box.innerHTML = r.results.map(res =>
    `<div class="proj-row"><span><b>变体 ${esc(res.variant)}</b> <code>${esc(res.asset_id)}</code><br>`+
    `<span class="hint">${layerTypeCn(res.type)} · 提供方 ${esc(res.provider_id)} · 指纹 ${esc(res.input_fingerprint)}</span></span>`+
    `<span class="btn-row">`+
    `<button class="btn-sm" onclick="addGenToTimeline('${esc(res.asset_id)}')">加入时间线</button>`+
    `<button class="btn-secondary btn-sm" onclick="switchTab('assets')">已存素材库</button>`+
    `</span></div>`).join('');
}
async function addGenToTimeline(assetId){
  // 加入时间线需目标场景：无选中场景则用第一场景
  const sceneId = (ED.sel && ED.sel.type === 'scene') ? ED.sel.id :
                  (ED.comp && ED.comp.scenes[0] ? ED.comp.scenes[0].id : '');
  if (!sceneId) { alert('项目无场景'); return; }
  const r = await apiPost('/api/editor/generate/add', {project_id: ED.pid, scene_id: sceneId, asset_id: assetId});
  if (r.ok) {
    ED.version = r.composition_version;
    const comp = await (await fetch('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid))).json();
    ED.comp = comp; ED.version = comp.version;
    editorRenderTimeline();
    document.getElementById('gen-status').textContent = `✅ ${assetId} 已加入场景 ${sceneId}（revision ${r.revision_id}）`;
  } else {
    document.getElementById('gen-status').textContent = '❌ ' + (r.error || '加入失败');
  }
}

// ---- 版本历史 + Compare（§三十 Versions：每次编辑一版，选中两版对比）----

// §8 拆分导出：inline onclick/window 引用兼容（零行为改动）
Object.assign(window, {
  populateGenModels,
  editorGenerate,
  addGenToTimeline,
});
