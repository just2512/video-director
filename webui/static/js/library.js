let AP = {lastTab: 'project', recent: [], favorites: []};
function _apPassFilters(a){
  // §50 筛选：类型 / 比例 / Commercial Clear（客户端过滤，含本地/项目素材）
  const ft = (document.getElementById('ap-f-type') || {}).value || '';
  const fr = (document.getElementById('ap-f-ratio') || {}).value || '';
  const fc = (document.getElementById('ap-f-clear') || {}).checked;
  if (ft && a.type !== ft) return false;
  if (fr && a.width && a.height) {
    const ratios = {'16:9': 16/9, '9:16': 9/16, '1:1': 1};
    const r = (a.width / a.height).toFixed(2);
    if (Math.abs(a.width / a.height - ratios[fr]) > 0.05) return false;
  }
  if (fc && a.commercial_status && a.commercial_status !== 'CLEAR') return false;
  return true;
}
/** 素材应用意图分流：replace=换层素材 / addbroll=作为新层加入场景（§20）。 */
function _apApplyAsset(sceneId, idx, aid, type){
  if (AP.intent === 'addbroll') {
    const payload = {assetId: aid, assetType: type === 'video' ? 'video' : 'image'};
    if (type === 'video') { payload.sourceOut = 5; }
    editorCmd('add_broll', payload, sceneId);
  } else {
    editorReplaceSource(sceneId, idx, aid);
  }
}

async function assetPickerTab(tab, idx, sceneId){
  const body = document.getElementById('asset-picker-body');
  if (!body) return;
  AP.lastTab = tab;
  if (tab === 'recent' || tab === 'favorites') {
    // §24 最近使用 / 收藏（会话级状态 + localStorage 持久化）
    try { AP.favorites = JSON.parse(localStorage.getItem('ap_favorites') || '[]'); } catch(e) { AP.favorites = []; }
    const ids = tab === 'recent' ? AP.recent : AP.favorites;
    let lib;
    try { lib = await apiGet('/api/assets_library'); } catch(e) { lib = []; }
    const items = (Array.isArray(lib) ? lib : []).filter(a => ids.includes(a.asset_id) && _apPassFilters(a));
    body.innerHTML = (items.length ? items.map(a => {
      const btn = `<button class="btn-secondary btn-sm" onclick="_apApplyAsset('${esc(sceneId)}',${idx},'${esc(a.asset_id)}','${esc(a.type || '')}')">${AP.intent === 'addbroll' ? '加入' : '替换'}</button>`+
        (tab === 'favorites'
          ? ` <button class="btn-sm" onclick="apToggleFav('${esc(a.asset_id)}',this)" title="取消收藏">★</button>`
          : ` <button class="btn-sm" onclick="apToggleFav('${esc(a.asset_id)}',this)" title="收藏">☆</button>`);
      return `<div class="proj-row"><span><b>${esc(a.asset_id)}</b> <span class="hint">${esc(a.type || '')}</span></span><span>${btn}</span></div>`;
    }).join('') : `<div class="hint">${tab === 'recent' ? '暂无最近使用（替换素材后记录）' : '暂无收藏（☆ 收藏素材）'}</div>`);
    return;
  }
  if (tab === 'project') {
    let lib;
    try { lib = await apiGet('/api/assets_library'); } catch(e) { lib = []; }
    const items = (Array.isArray(lib) ? lib : []).filter(a => a.type === 'image' || a.type === 'video' && _apPassFilters(a));
    body.innerHTML = (items.length ? items.slice(0, 60).map(a => {
      const sid = sceneId; const iidx = idx; const aid = a.asset_id;
      const row = document.createElement('div');
      row.className = 'proj-row';
      const span = document.createElement('span');
      span.innerHTML = '<span></span>';
      row.innerHTML = '<span><span class="hint"></span></span>';
      row.querySelector('span').firstChild.textContent = aid + ' ';
      row.querySelector('.hint').textContent = (a.type || '') + (a.file ? ' · ' + String(a.file).split(/[\\/]/).pop() : '');
      const btn = document.createElement('button');
      btn.className = 'btn-secondary btn-sm';
      btn.textContent = AP.intent === 'addbroll' ? '加入' : '替换';
      btn.onclick = () => _apApplyAsset(sid, iidx, aid, a.type || '');
      row.appendChild(btn);
      return row.outerHTML;
    }).join('')
      : '<div class="hint">项目素材库为空</div>');
  } else if (tab === 'local') {
    // §50 本地导入 tab：文件直接进项目素材库（video/image），再替换到层
    body.innerHTML = '<div class="btn-row"><input type="file" id="ap-local-file" multiple accept=".mp4,.mov,.mkv,.avi,.webm,.m4v,.jpg,.jpeg,.png,.webp">'+
      '<button class="btn-secondary btn-sm" id="ap-local-btn">⬆ 导入到项目</button></div>'+
      '<div id="ap-local-status" class="hint">选择视频/图片文件导入（导入后在本 tab 与项目素材可见）</div>';
    const btn = document.getElementById('ap-local-btn');
    if (btn) btn.onclick = async () => {
      const fi = document.getElementById('ap-local-file');
      const st = document.getElementById('ap-local-status');
      if (!fi.files || !fi.files.length) { st.textContent = '请先选择文件'; return; }
      if (!ED.pid) { st.textContent = '需先打开项目'; return; }
      st.textContent = '导入中……';
      const fd = new FormData();
      for (const f of fi.files) fd.append('files', f);
      fd.append('project_id', ED.pid);
      try {
        const resp = await fetch('/api/assets_import', {method:'POST', body: fd});
        const r = await resp.json();
        st.textContent = r.ok ? `✅ 已导入 ${r.imported||0} 个素材（asset ids: ${(r.asset_ids||[]).slice(0,3).join('、')}）`
                              : '❌ ' + (r.error || '导入失败');
        if (r.ok) await assetPickerTab('project', idx, sceneId);
      } catch(e) { st.textContent = '❌ ' + e; }
    };
  } else if (tab === 'stock') {
    body.innerHTML = '<div class="btn-row"><input type="text" id="stock-q" class="rev-input" style="flex:1" placeholder="搜索关键词（如 coffee）">'+
      '<button class="btn-secondary btn-sm" id="stock-search-btn">搜索</button></div>'+
      '<div id="stock-results" class="hint">配置 Pexels / Pixabay / Coverr 后可按图库搜索（缺 key 会诚实提示）</div>';
    const inp = document.getElementById('stock-q');
    if (inp) inp.onkeydown = (e) => { if (e.key === 'Enter') assetPickerStockSearch(); };
    const btn = document.getElementById('stock-search-btn');
    if (btn) btn.onclick = () => assetPickerStockSearch();
  } else { // generated
    let lib;
    try { lib = await apiGet('/api/assets_library'); } catch(e) { lib = []; }
    const gen = (Array.isArray(lib) ? lib : []).filter(a => (a.origin === 'generated' || a.source === 'generated'));
    body.innerHTML = (gen.length ? gen.slice(0, 60).map(a => {
      const sid = sceneId; const iidx = idx; const aid = a.asset_id;
      const row = document.createElement('div');
      row.className = 'proj-row';
      row.innerHTML = '<span><span class="hint"></span></span>';
      row.querySelector('span').firstChild.textContent = aid + ' ';
      row.querySelector('.hint').textContent = (a.type || '') + ' · generated';
      const btn = document.createElement('button');
      btn.className = 'btn-secondary btn-sm';
      btn.textContent = AP.intent === 'addbroll' ? '加入' : '替换';
      btn.onclick = () => _apApplyAsset(sid, iidx, aid, a.type || '');
      row.appendChild(btn);
      return row.outerHTML;
    }).join('')
      : '<div class="hint">尚无生成素材（去「生成素材」面板生成）</div>');
  }
}
async function assetPickerStockSearch(){
  const q = (document.getElementById('stock-q')||{}).value || '';
  const box = document.getElementById('stock-results');
  if (!box) return;
  if (!q) { box.innerHTML = '<div class="hint">请输入关键词</div>'; return; }
  box.innerHTML = '搜索中……';
  const r = await apiPost('/api/stock/search', {query: q, ratio: '9:16'});
  if (!r.ok) { box.innerHTML = `<div class="err-box">❌ ${esc(r.error||'搜索失败')}${r.code ? ` <code>${esc(r.code)}</code>` : ''}</div>`; return; }
  const cands = r.candidates || [];
  const statusCn = {SUPPORTED:'✓', MISSING_REQUIREMENT:'⚠ 素材不足', INCOMPATIBLE:'× 画幅不符', UNKNOWN:'? 待确认'};
  box.innerHTML = cands.length ? cands.map(c =>
    `<div class="proj-row"><span><b>${esc(c.provider)}</b> ${esc(c.visualDescription||c.sourceId)} <span class="hint">${c.width}x${c.height} · ${c.duration}s · ${statusCn[c.status]||c.status}</span></span>`+
    `<span class="btn-row" style="gap:4px"><span class="hint">${COMMERCIAL_CN[c.commercialStatus]||c.commercialStatus}</span>`+
    `<button class="btn-sm" onclick="assetPickerStockDownload(${esc(JSON.stringify(c))})">⬇ 下载采用</button></span></div>`).join('')
    : '<div class="hint">无匹配素材</div>';
}
async function assetPickerStockDownload(asset){
  if (!ED.pid) { editorToast('先在编辑器打开一个项目'); return; }
  const r = await apiPost('/api/stock/download', {project_id: ED.pid, asset: asset});
  if (!r.ok) { editorToast('❌ ' + (r.error||'下载失败'), true); return; }
  editorToast(`✅ 已下载 ${r.type} 素材到项目库（${r.width}x${r.height}）`);
  if (ED.sel && ED.sel.type === 'layer') {
    await editorReplaceSource(ED.sel.id.split('#')[0], parseInt(ED.sel.id.split('#')[1]), r.asset_id);
  }
}
function createGoStock(){
  // 创作页「联网搜图库」入口 → 引导到编辑器图库（下载采用需项目上下文）
  switchTab('editor');
  editorToast('联网素材：打开项目后，左侧「资源库→图库」搜索 → 点「下载采用」');
}
async function editorReplaceSource(sceneId, idx, assetId){
  // §24 最近使用：替换动作记入 AP.recent（去重置顶，最多 50）
  AP.recent = [assetId, ...AP.recent.filter(x => x !== assetId)].slice(0, 50);
  editorCmd('replace_source', {assetId}, `${sceneId}#${idx}`);
}
function apToggleFav(assetId, btnEl){
  // §24 收藏：localStorage 持久化
  try { AP.favorites = JSON.parse(localStorage.getItem('ap_favorites') || '[]'); } catch(e) { AP.favorites = []; }
  if (AP.favorites.includes(assetId)) {
    AP.favorites = AP.favorites.filter(x => x !== assetId);
    if (btnEl) btnEl.textContent = '☆';
  } else {
    AP.favorites.push(assetId);
    if (btnEl) btnEl.textContent = '★';
  }
  try { localStorage.setItem('ap_favorites', JSON.stringify(AP.favorites)); } catch(e) {}
}

async function editorBatchCmd(subcommand){
  // §47 multi-select：多选集合批量命令（单 revision）
  if (!ED.pid) { editorToast('先打开项目'); return; }
  let targets = [];
  if (subcommand === 'lock') {
    targets = [...ED.selSet].filter(k => k.startsWith('layer:')).map(k => k.slice(6));
    if (!targets.length) { editorToast('批量锁定需选中图层'); return; }
  } else {
    // delete/duplicate 作用于选中场景
    targets = ED.comp.scenes.filter(s => edIsSelected('scene', s.id)).map(s => s.id);
    if (!targets.length) { editorToast('批量' + (subcommand==='delete'?'删除':'复制') + '需选中场景'); return; }
  }
  const r = await apiPost('/api/editor/command', {
    project_id: ED.pid, command: 'batch',
    targetId: targets[0],
    payload: {subcommand, targets, locked: true},
    expectedVersion: ED.version,
  });
  if (r.ok) {
    ED.version = r.composition_version;
    const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid));
    ED.comp = comp; ED.version = comp.version;
    ED.sel = null; ED.selSet = new Set();
    editorRenderTimeline();
    const cnt = document.getElementById('editor-selection-count');
    if (cnt) cnt.textContent = '未选中';
    editorToast(`批量${subcommand} 完成` + (DEV.mode ? `（版本 ${r.revision_id}；变更 ${(r.changed_artifacts||[]).length} 项）` : ''));
  } else {
    const err = (r.errors && r.errors[0]) || {};
    editorToast(`❌ 批量${subcommand}：${err.message || r.error || ''}`, true);
  }
}

async function editorCmd(cmd, extra, targetOverride, useRawTarget){
  if (!ED.pid) { editorToast('先打开项目'); return; }
  let target = targetOverride || (ED.sel ? ED.sel.id : '');
  if (!target && cmd !== 'noop' && !useRawTarget) { editorToast('先在时间线选中场景或图层'); return; }
  // split 只作用于场景：图层选中时归约到其所属场景
  if (cmd === 'split' && target.includes('#')) target = target.split('#')[0];
  const payload = extra || {};
  // §82 统一 Command Protocol：commandId/projectId/variantId/targetId/payload/expectedVersion
  const r = await apiPost('/api/editor/command', {project_id: ED.pid, variantId: ED.variantId || ED.pid, command: cmd, targetId: target, payload, expectedVersion: ED.version});
  if (r.ok) {
    ED.version = r.composition_version;
    const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid));
    ED.comp = comp; ED.version = comp.version;
    editorRenderTimeline();
    edShowInspector();
    renderWatermarkList();
    editorToast(`${cmd} 完成` + (DEV.mode ? `（版本 ${r.revision_id}；已更新：${(r.stale_artifacts||[]).join('，') || '无'}）` : ''));
  } else {
    const err = (r.errors && r.errors[0]) || {};
    editorToast(`❌ ${err.code || '错误'}：${err.message || r.error || ''}`, true);
  }
}
function editorApplyDuration(sceneId){
  const v = parseFloat(document.getElementById('ed-dur').value);
  if (!(v > 0.3)) { editorToast('时长需不小于 0.3 秒', true); return; }
  editorCmd('set_duration', {duration: v});
}
function editorMovePrompt(){
  // §9.3：prompt → 内联输入（目标序号）
  const hostId = 'ed-move-host';
  _ensureInlineHost(hostId);
  uiInlineInput(hostId, {type: 'number', def: 1, min: 1, max: 99, step: 1, unit: '第几位'},
    (v) => { if (v >= 1) editorCmd('move', {toIndex: Math.round(v) - 1}); });
}
function editorAddBrollPrompt(){
  // 加 B-roll：打开统一素材选择器，选素材「加入」为场景新层（§20 add_broll；
  // §9.3：prompt/confirm → 素材选择器弹层）
  if (!ED.sel || ED.sel.type !== 'scene') { editorToast('先在时间线选中一个场景', true); return; }
  editorOpenAssetPicker(ED.sel.id, 0, 'addbroll');
}

// ---- Motion Picker（动效库数据源 + resolver 三态）----
async function editorOpenMotionPicker(sceneId, idx){
  const box = document.getElementById('editor-picker-box');
  box.innerHTML = '加载动效候选……';
  const r = await apiPost('/api/registry/motions/resolve', {layer_type: 'image', semantic_intent: 'enter', duration: 0.5});
  const cands = (r.candidates || []).filter(c=>c.compatibility !== 'INCOMPATIBLE');
  box.innerHTML = '<label>选择动效（优先推荐可用动效；缺依赖的也可应用）</label>' +
    cands.map(c=>`<div class="proj-row"><span><b>${esc(c.name)}</b>`+
      `<span class="dev-only hint"><code>${esc(c.recipe_id)}</code></span>`+
      `<span class="hint">${c.compatibility === 'SUPPORTED' ? '✅ 可用' : '⚠ ' + esc((c.missing_requirements||[]).join('；'))}</span></span>`+
      `<button class="btn-secondary btn-sm" onclick="editorApplyMotion('${sceneId}',${idx},'${c.recipe_id}')">应用</button></div>`).join('');
}
async function editorApplyMotion(sceneId, idx, motionId){
  const r = await apiPost('/api/editor/command', {project_id: ED.pid, command: 'replace_motion',
    targetId: `${sceneId}#${idx}`, payload: {motionRecipeId: motionId}, expectedVersion: ED.version});
  if (r.ok) {
    ED.version = r.composition_version;
    const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid));
    ED.comp = comp; ED.version = comp.version;
    edSelect(`${sceneId}#${idx}`, 'layer', null);
    editorRenderTimeline(); edShowInspector();
    editorToast(`✅ 动效已应用：${motionCnName(motionId)}` + (DEV.mode ? `（${motionId}）` : ''));
  } else {
    const err = (r.errors && r.errors[0]) || {};
    editorToast(`❌ ${err.message || r.error}`, true);
  }
}

// ---- Shot Picker（换镜头）----
async function editorOpenShotPicker(sceneId){
  const box = document.getElementById('editor-picker-box');
  box.innerHTML = '加载镜头候选……';
  const sc = ED.comp.scenes.find(s=>s.id===sceneId);
  const r = await apiPost('/api/registry/shots/resolve', {
    semantic_intents: [], content_type: 'product_video', duration: sc.duration,
    available_asset_types: ['image','product','video'],
    scene_position: sceneId.startsWith('scene-hook') || ED.comp.scenes[0].id===sceneId ? 'first' : 'middle',
  });
  const cands = (r.candidates || []).filter(c=>c.compatibility !== 'INCOMPATIBLE');
  devCacheShots(r.candidates);  // §9.2 中文名缓存（Inspector 显示用）
  box.innerHTML = '<label>选择镜头方案（换镜头不换素材）</label>' +
    cands.map(c=>`<div class="proj-row"><span><b>${esc(c.name)}</b>`+
      `<span class="dev-only hint"><code>${esc(c.recipe_id)}</code></span>`+
      `<span class="hint">${esc((c.reasons||[]).join('；'))}</span></span>`+
      `<button class="btn-secondary btn-sm" onclick="editorApplyShot('${sceneId}','${c.recipe_id}')">使用</button></div>`).join('');
}
async function editorApplyShot(sceneId, recipeId){
  const r = await apiPost('/api/editor/command', {project_id: ED.pid, command: 'replace_recipe',
    targetId: sceneId, payload: {recipeId}, expectedVersion: ED.version});
  if (r.ok) {
    ED.version = r.composition_version;
    const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid));
    ED.comp = comp; ED.version = comp.version;
    editorRenderTimeline();
    editorToast(`✅ 镜头已更换：${recipeId}`);
  } else {
    const err = (r.errors && r.errors[0]) || {};
    editorToast(`❌ ${err.message || r.error}`, true);
  }
}

// ---- AI 提案（propose → preview → apply/reject）----
async function editorAiPropose(){
  const inst = document.getElementById('editor-ai-input').value.trim();
  if (!inst) { editorToast('请输入修改描述'); return; }
  const box = document.getElementById('editor-ai-proposal');
  box.innerHTML = 'AI 解析中……';
  const r = await apiPost('/api/proposal/propose', {project_id: ED.pid, instruction: inst});
  if (!r.ok) { box.innerHTML = `<div class="err-box">❌ ${esc(r.error || '解析失败')}</div>`; return; }
  ED.pendingProposal = r.proposal_id;
  box.innerHTML = `<b>提案 ${r.proposal_id}</b>（来源:${r.source}）<div class="hint">${esc(r.explanation||'')}</div>` +
    r.patches.map(p=>`<div class="proj-row"><span>${esc(p.command)} → ${esc(p.targetId||'')}`+
      `<span class="hint">${esc(p.reason||'')}</span></span></div>`).join('') +
    `<div class="btn-row"><button class="btn-secondary btn-sm" onclick="editorAiPreview()">👁 预览效果</button>`+
    `<button class="btn-sm" onclick="editorAiApply()">✅ 应用</button>`+
    `<button class="btn-secondary btn-sm" onclick="editorAiReject()">✗ 拒绝</button></div>`;
}
async function editorAiPreview(){
  const r = await apiPost('/api/proposal/preview', {project_id: ED.pid, proposal_id: ED.pendingProposal});
  const box = document.getElementById('editor-ai-proposal');
  if (!r.ok) { box.innerHTML += `<div class="err-box">预览失败：${esc(r.error)}</div>`; return; }
  box.innerHTML += `<div class="hint">预览：${r.results.map(x=>`${x.status==='PREVIEW_OK'?'✅':'❌'} ${esc(x.patch.command)}(${esc(x.patch.targetId)})`).join('；')}</div>`;
}
async function editorAiApply(){
  const r = await apiPost('/api/proposal/apply', {project_id: ED.pid, proposal_id: ED.pendingProposal});
  const box = document.getElementById('editor-ai-proposal');
  if (r.ok && r.status === 'APPLIED') {
    const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid));
    ED.comp = comp; ED.version = comp.version;
    editorRenderTimeline();
    box.innerHTML += `<div class="hint">✅ 已应用 ${r.applied.length} 项修改（revision 已记录）</div>`;
  } else {
    box.innerHTML += `<div class="err-box">部分失败：${JSON.stringify(r.errors||[]).slice(0,200)}</div>`;
  }
}
async function editorAiReject(){
  const r = await apiPost('/api/proposal/reject', {project_id: ED.pid, proposal_id: ED.pendingProposal});
  document.getElementById('editor-ai-proposal').innerHTML +=
    `<div class="hint">已拒绝（项目零变更${r.changed === false ? '，已验证' : ''}）</div>`;
}

// ===========================================================================
// AI Director Command Bar（§47/§92）：一句话剪辑意图 → Director → 执行 → verify
// ===========================================================================
async function directorLoadCapCount(){
  try {
    const d = await apiGet('/api/director/capabilities');
    const n = (d.capabilities || []).length;
    const el = document.getElementById('director-cap-count');
    if (el) el.textContent = n;
  } catch(e) {}
}
async function directorExecute(){
  if (!ED.pid) { editorToast('先打开项目（上方「打开项目」）'); return; }
  const inp = document.getElementById('director-input');
  const inst = (inp.value || '').trim();
  if (!inst) { editorToast('请输入剪辑意图（例：保留原声，加右上角水印）'); return; }
  const box = document.getElementById('director-result');
  box.innerHTML = '<div class="hint">AI 导演规划中……</div>';
  // §48 SSE：实时进度推送（多页面共享通道；无连接时静默跳过）
  try {
    if (!window._directorSSE) {
      window._directorSSE = new EventSource('/api/events');
      window._directorSSE.onmessage = (ev) => {
        try {
          const m = JSON.parse(ev.data);
          if (m.event === 'director') {
            const bar = document.getElementById('director-progress');
            if (!bar) return;
            const d = m.data || {};
            if (d.phase === 'planning') bar.textContent = '⏳ 规划中……';
            else if (d.phase === 'tool') bar.textContent = `${d.ok ? '✅' : '❌'} ${d.tool || ''}（${d.status || ''}）`;
            else if (d.phase === 'done') bar.textContent = d.verify_ok ? '✅ 全部完成（已验证）' : '⚠ 部分未通过验证';
            else if (d.phase === 'error') bar.textContent = '❌ ' + (d.code || '失败');
          }
        } catch(e) {}
      };
    }
  } catch(e) {}
  const bar = document.getElementById('director-progress');
  if (bar) bar.textContent = '⏳ 规划中……';
  const r = await apiPost('/api/director/execute', {project_id: ED.pid, instruction: inst});
  if (!r.ok) {
    box.innerHTML = `<div class="err-box">❌ ${esc(r.error || '执行失败')}${r.code ? ` <code>${esc(r.code)}</code>` : ''}</div>`;
    return;
  }
  const d = r.director || {};
  const ctx = d.context_summary || {};
  box.innerHTML =
    `<div class="hint">项目 ${esc(ctx.duration??'')}s · 轨 ${esc((ctx.tracks||[]).join('/'))} · 来源 ${esc(d.source)}</div>`+
    (d.plan||[]).map(p=>`<div class="proj-row"><span><code>${esc(p.tool)}</code>`+
      `<span class="hint">${esc(p.reason||'')}</span></span></div>`).join('') +
    `<div class="hint" style="margin-top:4px">执行结果：</div>`+
    (d.executed||[]).map(e=>`<div class="proj-row"><span>${e.ok?'✅':'❌'} <code>${esc(e.tool)}</code>`+
      `<span class="hint">${esc(e.status)}${e.error?' · '+esc(e.error):''}`+
      (e.verify && e.verify.checks ? ' · 验证 ' + Object.entries(e.verify.checks).map(([k,v])=>`${esc(k)}=${esc(v)}`).join(' ') : '')+
      `</span></span></div>`).join('') +
    `<div class="hint" style="margin-top:2px">${d.verify_ok ? '✅ 全部验证通过' : '⚠ 部分步骤未通过验证（见上方 ❌）'}</div>`;
  // 刷新时间线 + Inspector，展示 AI 落地效果
  const comp = await apiGet('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid));
  if (comp && comp.version !== undefined) { ED.comp = comp; ED.version = comp.version; }
  editorRenderTimeline();
  renderWatermarkList();
  editorToast(`AI 导演完成：${(d.executed||[]).filter(e=>e.ok).length}/${(d.executed||[]).length} 步成功`);
}

// ---- EDT-008 Timeline-native Generation（生成素材：变体 → 加入时间线）----

// §8 拆分导出：inline onclick/window 引用兼容（零行为改动）
Object.assign(window, {
  _apPassFilters,
  assetPickerTab,
  assetPickerStockSearch,
  assetPickerStockDownload,
  createGoStock,
  editorReplaceSource,
  apToggleFav,
  _apApplyAsset,
  editorBatchCmd,
  editorCmd,
  editorApplyDuration,
  editorMovePrompt,
  editorAddBrollPrompt,
  editorOpenMotionPicker,
  editorApplyMotion,
  editorOpenShotPicker,
  editorApplyShot,
  editorAiPropose,
  editorAiPreview,
  editorAiApply,
  editorAiReject,
  directorLoadCapCount,
  directorExecute,
  AP,
});
