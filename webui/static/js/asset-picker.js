// §61/§九 Asset Picker（方案《前端交互、AI 控制与自动化工作流》最终版）：
// 整个系统唯一的资源选择入口（本地/项目/图库/AI 生成/最近/收藏 六 tab）。
// 从 library.js 拆出（§61「library 逐步退化为 Picker/Registry 的数据层」）。

// Picker 会话状态（intent: replace | addbroll | autoFind；recent/favorites 持久收藏）
// 挂 window：inspector.js（editorOpenAssetPicker）等跨文件消费（let 顶层不进 window）。
window.AP = {lastTab: 'project', intent: 'replace', recent: [], favorites: []};

function _apPassFilters(a){
  const ft = (document.getElementById('ap-f-type') || {}).value || '';
  const fc = (document.getElementById('ap-f-clear') || {}).checked;
  if (ft && a.type !== ft) return false;
  if (fc && a.commercial_status && a.commercial_status !== 'CLEAR') return false;
  return true;
}
function _apApplyAsset(sceneId, idx, aid, type){
  if (AP.intent === 'addbroll') {
    const payload = {assetId: aid, assetType: type === 'video' ? 'video' : 'image'};
    if (type === 'video') payload.sourceOut = 5;
    editorCmd('add_broll', payload, sceneId, true);
  } else {
    editorReplaceSource(sceneId, idx, aid);
  }
}
async function assetPickerTab(tab, idx, sceneId){
  const body = document.getElementById('asset-picker-body');
  if (!body) return;
  AP.lastTab = tab;
  if (tab === 'recent' || tab === 'favorites') {
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
    const items = (Array.isArray(lib) ? lib : []).filter(a => _apPassFilters(a));
    body.innerHTML = (items.length ? items.slice(0, 60).map(a => {
      const sid = sceneId; const iidx = idx; const aid = a.asset_id;
      const row = document.createElement('div');
      row.className = 'proj-row';
      row.innerHTML = '<span><span class="hint"></span></span>';
      row.querySelector('span').firstChild.textContent = aid + ' ';
      row.querySelector('.hint').textContent = (a.type || '') + (a.file ? ' · ' + String(a.file).split(/[\\/]/).pop() : '');
      const btn = document.createElement('button');
      btn.className = 'btn-secondary btn-sm';
      btn.textContent = AP.intent === 'addbroll' ? '加入' : '替换';
      btn.onclick = () => _apApplyAsset(sid, iidx, aid, a.type || '');
      row.appendChild(btn);
      return row.outerHTML;
    }).join('') : '<div class="hint">项目素材库为空</div>');
  } else if (tab === 'local') {
    body.innerHTML = '<div class="btn-row"><input type="file" id="ap-local-file" multiple accept=".mp4,.mov,.mkv,.avi,.webm,.m4v,.jpg,.jpeg,.png,.webp">'+
      '<button class="btn-secondary btn-sm" id="ap-local-btn">导入到项目</button></div>'+
      '<div id="ap-local-status" class="hint">选择视频/图片文件导入</div>';
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
      const r = await apiUpload('/api/assets_import', fd);
      st.textContent = r.ok ? `已导入 ${r.imported||0} 个素材` : (r.error || '导入失败');
      if (r.ok) await assetPickerTab('project', idx, sceneId);
    };
  } else if (tab === 'stock') {
    body.innerHTML = '<div class="btn-row"><input type="text" id="stock-q" class="rev-input" style="flex:1" placeholder="搜索关键词（如 coffee）">'+
      '<button class="btn-secondary btn-sm" id="stock-search-btn">搜索</button></div>'+
      '<div id="stock-results" class="hint">配置 Pexels / Pixabay / Coverr 后可搜索（设置页）</div>';
    const inp = document.getElementById('stock-q');
    if (inp) inp.onkeydown = (e) => { if (e.key === 'Enter') assetPickerStockSearch(); };
    const btn = document.getElementById('stock-search-btn');
    if (btn) btn.onclick = () => assetPickerStockSearch();
  } else {
    let lib;
    try { lib = await apiGet('/api/assets_library'); } catch(e) { lib = []; }
    const gen = (Array.isArray(lib) ? lib : []).filter(a => (a.origin === 'generated' || a.source === 'generated'));
    body.innerHTML = (gen.length ? gen.slice(0, 60).map(a => {
      const sid = sceneId; const iidx = idx; const aid = a.asset_id;
      const row = document.createElement('div');
      row.className = 'proj-row';
      row.innerHTML = '<span><span class="hint"></span></span>';
      row.querySelector('span').firstChild.textContent = aid + ' ';
      row.querySelector('.hint').textContent = (a.type || '') + ' · 生成';
      const btn = document.createElement('button');
      btn.className = 'btn-secondary btn-sm';
      btn.textContent = AP.intent === 'addbroll' ? '加入' : '替换';
      btn.onclick = () => _apApplyAsset(sid, iidx, aid, a.type || '');
      row.appendChild(btn);
      return row.outerHTML;
    }).join('') : '<div class="hint">尚无生成素材（时间线下方「生成画面」面板生成）</div>');
  }
}
async function assetPickerStockSearch(){
  const q = (document.getElementById('stock-q')||{}).value || '';
  const box = document.getElementById('stock-results');
  if (!box) return;
  if (!q) { box.innerHTML = '<div class="hint">请输入关键词</div>'; return; }
  box.textContent = '搜索中……';
  // §10 ratio 由当前项目画幅决定（不硬编码 9:16：16:9/1:1/3:4 项目不再搜错方向）
  const r = await apiPost('/api/stock/search',
    {query: q, project_id: ED.pid || '', ratio: ''});
  if (!r.ok) { box.innerHTML = `<div class="err-box">搜索失败：${esc(r.error||'')}${DEV.mode && r.code ? ` <code>${esc(r.code)}</code>` : ''}</div>`; return; }
  const cands = r.candidates || [];
  const stCn = {SUPPORTED:'可用', MISSING_REQUIREMENT:'素材不足', INCOMPATIBLE:'画幅不符', UNKNOWN:'待确认'};
  box.innerHTML = cands.length ? cands.map(c =>
    `<div class="proj-row"><span><b>${esc(c.provider)}</b> ${esc(c.visualDescription||c.sourceId)} <span class="hint">${c.width}x${c.height} · ${stCn[c.status]||c.status} · ${COMMERCIAL_CN[c.commercialStatus]||esc(c.commercialStatus)}</span></span>`+
    `<button class="btn-sm" onclick="assetPickerStockDownload(${esc(JSON.stringify(c))})">下载采用</button></div>`).join('')
    : '<div class="hint">无匹配素材</div>';
}
async function assetPickerStockDownload(asset){
  if (!ED.pid) { editorToast('先打开一个项目', true); return; }
  const r = await apiPost('/api/stock/download', {project_id: ED.pid, asset: asset});
  if (!r.ok) { editorToast((r.error||'下载失败'), true); return; }
  editorToast(`已下载素材到项目库（${r.width}x${r.height}）`);
  if (SEL.selection && SEL.selection.type === 'layer') {
    await editorReplaceSource(SEL.selection.id.split('#')[0], parseInt(SEL.selection.id.split('#')[1]), r.asset_id);
  }
}
function createGoStock(){
  editorToast('联网素材：打开项目后，左侧「内容 → 图库」搜索并下载采用');
  switchTab('editor');
}

async function editorReplaceSource(sceneId, idx, assetId){
  AP.recent = [assetId, ...AP.recent.filter(x => x !== assetId)].slice(0, 50);
  editorCmd('replace_source', {assetId}, `${sceneId}#${idx}`);
}
function apToggleFav(assetId, btnEl){
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

// §8 导出（inline onclick/window 引用兼容）
Object.assign(window, {
  _apPassFilters, assetPickerTab, assetPickerStockSearch, assetPickerStockDownload,
  createGoStock, editorReplaceSource, apToggleFav, _apApplyAsset,
});
