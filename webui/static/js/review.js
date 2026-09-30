async function editorLoadVersions(){
  if (!ED.pid) { alert('先打开项目'); return; }
  const revs = await (await fetch('/api/editor/revisions?project_id=' + encodeURIComponent(ED.pid))).json();
  const box = document.getElementById('editor-versions');
  if (!Array.isArray(revs) || !revs.length) {
    box.innerHTML = '<div class="hint">尚无编辑版本（去动效库换动效、或时间线拆分/删场景，即生成版本）。</div>';
    return;
  }
  box.innerHTML = revs.map((rv, i) => {
    const when = new Date((rv.created_at || 0) * 1000).toLocaleString();
    const target = rv.target_id || '';
    return `<div class="proj-row"><input type="checkbox" class="ver-chk" data-i="${i}" style="width:auto">`+
      `<b>v${rv.base_version + 1}</b><span>${esc(rv.command)} → ${esc(target)}</span>`+
      `<span class="hint">${when}</span>`+
      `<span class="hint">changed:${(rv.changed_artifacts||[]).length} reused:${(rv.reused_artifacts||[]).length}</span></div>`;
  }).join('');
}
async function editorCompareSelected(){
  const chk = Array.from(document.querySelectorAll('.ver-chk:checked'));
  if (chk.length < 2) { alert('勾选两个版本再对比'); return; }
  const revs = await (await fetch('/api/editor/revisions?project_id=' + encodeURIComponent(ED.pid))).json();
  const a = revs[parseInt(chk[0].dataset.i)], b = revs[parseInt(chk[1].dataset.i)];
  const changed = [a, b].map(x => (x.changed_artifacts || []).join('、') || '无');
  const reusedA = (a.reused_artifacts || []), reusedB = (b.reused_artifacts || []);
  const com = reusedA.filter(x => reusedB.includes(x));
  document.getElementById('editor-compare').innerHTML =
    `<div class="card"><b>对比 v${a.base_version+1} vs v${b.base_version+1}</b>`+
    `<div class="proj-row"><b>有改动</b><span>左侧：${changed[0]}</span></div>`+
    `<div class="proj-row"><b>有改动</b><span>右侧：${changed[1]}</span></div>`+
    `<div class="proj-row"><b>复用未动（两版相同）</b><span>${com.length} 项：${esc(com.slice(0,5).join('、'))}${com.length>5?'…':''}</span></div></div>`;
}
async function editorPreflight(){
  const box = document.getElementById('editor-preflight-box');
  const r = await (await fetch('/api/preflight?project_id=' + encodeURIComponent(ED.pid))).json();
  if (r.status === 'PASS') {
    box.innerHTML = `<div class="card ok-box">🛡 商用预检通过（${r.issues.length} 项提示，无阻断）`+
      (r.issues.length ? `<div>${r.issues.map(i=>`<div class="hint">⚠ ${esc(i.code)}: ${esc(i.message)}</div>`).join('')}</div>` : '') + `</div>`;
  } else {
    box.innerHTML = `<div class="card err-box">⛔ 商用预检未通过（${r.issues.filter(i=>!i.recoverable).length} 项阻断）：`+
      r.issues.map(i=>`<div class="hint">${i.recoverable?'⚠':'⛔'} [${esc(i.code)}] ${esc(i.message)}（素材：${esc(i.targetId)}）</div>`).join('')+
      `</div>`;
  }
  return r;
}
async function editorRenderFinal(){
  const pf = await editorPreflight();
  if (pf.status !== 'PASS') { alert('商用预检未通过，渲染中止（先处理阻断项）'); return; }
  editorToast('预检通过，渲染已提交到队列……');
  // 渲染走既有管线：confirm 接口渲染 AWAITING_CONFIRM 项目
  const r = await apiPost('/api/confirm', {project_id: ED.pid});
  if (r.ok) {
    document.getElementById('editor-preflight-box').innerHTML =
      `<div class="card ok-box">🎬 成片完成：<a class="dl" href="/file?path=${encodeURIComponent(r.final_path)}" download="final.mp4">下载 final.mp4</a> QC: ${esc((r.qc||{}).verdict||'')}</div>`;
  } else {
    document.getElementById('editor-preflight-box').innerHTML =
      `<div class="err-box">渲染失败：${esc(r.error||'')}</div>`;
  }
}

function editorToast(msg, isErr){
  const meta = document.getElementById('editor-meta');
  meta.textContent = msg;
  meta.style.color = isErr ? '#c0392b' : '#27ae60';
  setTimeout(()=>{ meta.style.color=''; }, 6000);
}

// ===========================================================================
// §48 Library 八入口（Scenes/Script/Assets/Stock/Generate/Motions/Templates/References）
// 只保留浏览入口；点击应用回 Timeline/Inspector。Blocks 并入 Assets 入口展示。
// ===========================================================================
let ED_LIB = {blocks: [], motions: [], loaded: false, nav: 'scenes'};

function edLibNav(kind){
  // §48 左栏入口切换：高亮当前入口并加载对应列表
  ED_LIB.nav = kind;
  document.querySelectorAll('#ed-lib-nav button').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-lib') === kind);
  });
  edLibLoad();
}

async function edLibLoad(){
  const q = (document.getElementById('ed-lib-search').value || '').toLowerCase();
  const box = document.getElementById('ed-lib-list');
  box.innerHTML = '加载中……';
  try {
    if (ED_LIB.nav === 'blocks') {
      if (!ED_LIB.blocks.length) ED_LIB.blocks = await (await fetch('/api/catalog/blocks')).json();
      const list = ED_LIB.blocks.filter(b => !q || (b.id + b.name).toLowerCase().includes(q));
      box.innerHTML = list.map(b => `<div class="proj-row"><span><b>${esc(b.name)}</b> <code>${esc(b.id)}</code>`+
        `<span class="hint">${esc(b.category)} · ${b.slots.join('/')}</span></span>`+
        `<button class="btn-secondary btn-sm" onclick="edLibApplyBlock('${esc(b.id)}')">应用</button></div>`).join('')
        || '<div class="hint">无匹配</div>';
    } else if (ED_LIB.nav === 'motions') {
      const ms = await (await fetch('/api/registry/motions')).json();
      window.MOTIONS = ms;
      const zoneCard = (label, ids) => `<div style="margin:6px 0"><b style="color:#8ab4d8">${label}</b><div class="btn-row" style="flex-wrap:wrap">`+
        ids.map(id => { const m = ms.find(x=>x.id===id); if(!m) return ''; return `<span class="motion-card" style="width:110px">`+
          `<span class="motion-preview ${m.preview_only?'motion-preview-dim':''}" style="${motionCssPreview(m)}">${esc(m.name)}</span>`+
          `<button class="btn-secondary btn-sm" onclick="edLibApplyMotion('${esc(m.id)}')">应用</button></span>`; }).join('')+'</div></div>';
      const shown = [...new Set([...MOTION_ZONES.Entrance, ...MOTION_ZONES.Emphasis, ...MOTION_ZONES.Exit])];
      const rest = ms.filter(m => !shown.includes(m.id) && (!q || (m.id + m.name).toLowerCase().includes(q)));
      box.innerHTML = zoneCard('入场', MOTION_ZONES.Entrance) + zoneCard('强调', MOTION_ZONES.Emphasis) + zoneCard('出场', MOTION_ZONES.Exit) +
        (rest.length ? `<div style="margin:6px 0"><b style="color:#8ab4d8">其他</b><div class="btn-row" style="flex-wrap:wrap">`+
          rest.map(m => `<span class="motion-card" style="width:110px"><span class="motion-preview ${m.preview_only?'motion-preview-dim':''}" style="${motionCssPreview(m)}">${esc(m.name)}</span>`+
          `<button class="btn-secondary btn-sm" onclick="edLibApplyMotion('${esc(m.id)}')">应用</button></span>`).join('')+'</div></div>' : '');
    } else if (ED_LIB.nav === 'templates') {
      const tpls = await (await fetch('/api/registry/templates')).json();
      window.TPLS = tpls;
      const list = tpls.filter(t => !q || (t.id + t.name).toLowerCase().includes(q));
      box.innerHTML = list.map(t => `<div class="proj-row"><span><b>${esc(t.name)}</b> <code>${esc(t.id)}</code>`+
        `<span class="hint">画幅 ${t.supported_ratios.join('/')} · ${t.scene_count} 场景</span></span>`+
        `<button class="btn-sm" onclick="edLibOpenTemplate('${t.id}')">试用</button></div>`).join('') || '<div class="hint">无匹配</div>';
    } else if (ED_LIB.nav === 'assets') {
      const assets = await (await fetch('/api/assets_library')).json();
      const list = assets.filter(a => !q || (a.asset_id + (a.description||'')).toLowerCase().includes(q));
      box.innerHTML = list.slice(0, 50).map(a => `<div class="proj-row"><span><b>${esc(a.asset_id)}</b>`+
        `<span class="hint">${esc(a.type)} · 用于 ${a.used_in.length} 项目</span></span>`+
        `<button class="btn-secondary btn-sm" onclick="edLibApplyAsset('${esc(a.asset_id)}')">换到此层</button></div>`).join('')
        || '<div class="hint">无素材</div>';
    } else if (ED_LIB.nav === 'scenes') {
      if (!ED.comp || !ED.comp.scenes) { box.innerHTML = '<div class="hint">先打开项目</div>'; return; }
      const list = ED.comp.scenes.filter(s => !q || (s.id + s.text).toLowerCase().includes(q));
      box.innerHTML = list.map(s => `<div class="proj-row" onclick="editorSelectScene('${esc(s.id)}', event)"><span><b>${esc(s.id)}</b>`+
        `<span class="hint">${s.duration.toFixed(1)}s · ${esc((s.text||'').slice(0,20))}</span></span></div>`).join('');
    } else if (ED_LIB.nav === 'script') {
      // §48 Script 入口：项目分镜文案浏览（只读，编辑回 Inspector/时间线）
      if (!ED.comp || !ED.comp.scenes) { box.innerHTML = '<div class="hint">先打开项目</div>'; return; }
      const list = ED.comp.scenes.filter(s => !q || (s.text||'').toLowerCase().includes(q));
      box.innerHTML = list.map((s, i) => `<div class="proj-row"><span><span class="hint">#${i+1} ${esc(s.id)} · ${s.duration.toFixed(1)}s</span><br>${esc(s.text||'')}</span></div>`).join('')
        || '<div class="hint">无文案场景</div>';
    } else if (ED_LIB.nav === 'stock') {
      // §48 Stock 入口：三 Provider 统一搜索（缺 key 诚实提示 §25）
      box.innerHTML = `<div class="btn-row"><input type="text" id="ed-stock-q" class="rev-input" style="flex:1" placeholder="搜索 Stock 素材（coffee）" onkeydown="if(event.key==='Enter')edLibStockSearch()">
        <button class="btn-secondary btn-sm" onclick="edLibStockSearch()">搜索</button></div><div id="ed-stock-results" class="hint" style="margin-top:6px">Pexels / Pixabay / Coverr 统一入口</div>`;
    } else if (ED_LIB.nav === 'generate') {
      // §48 Generate 入口：跳到下方生成面板（入口非独立产品）
      box.innerHTML = '<div class="hint">生成素材面板在时间线下方。<br>选中场景后填提示词 → 生成 A/B/C 变体 → 加入时间线。</div>';
    } else if (ED_LIB.nav === 'references') {
      // §48 References 入口：参考视频/素材引用列表（§68/§90 asset: 引用）
      if (!ED.comp) { box.innerHTML = '<div class="hint">先打开项目</div>'; return; }
      const refs = [];
      for (const sc of ED.comp.scenes) for (const lay of (sc.layers||[])) {
        if (lay.asset_id && !refs.includes(lay.asset_id)) refs.push(lay.asset_id);
      }
      box.innerHTML = refs.length ? refs.map(r => `<div class="proj-row"><span><b>${esc(r)}</b><span class="hint">可作生成引用</span></span></div>`).join('')
        : '<div class="hint">项目暂无素材引用</div>';
    }
  } catch (e) {
    box.innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
  }
}
async function edLibStockSearch(){
  const inp = document.getElementById('ed-stock-q');
  const box = document.getElementById('ed-stock-results');
  if (!inp || !box) return;
  const q = inp.value.trim();
  if (!q) { box.textContent = '请输入关键词'; return; }
  box.textContent = '搜索中……';
  const r = await apiPost('/api/stock/search', {query: q, ratio: 'any'});
  if (!r.ok) {
    box.innerHTML = `<div class="err-box">❌ ${esc(r.error || '搜索失败')}${r.code ? ` <code>${esc(r.code)}</code>` : ''}</div>`;
    return;
  }
  const cands = r.candidates || [];
  const statusCn = {SUPPORTED:'✓', MISSING_REQUIREMENT:'⚠ 素材不足', INCOMPATIBLE:'× 画幅不符', UNKNOWN:'? 待确认'};
  box.innerHTML = cands.length ? cands.map(c =>
    `<div class="proj-row"><span><b>${esc(c.provider)}</b> ${esc(c.visualDescription || c.sourceId)}`+
    `<span class="hint">${c.width}x${c.height} · ${c.duration}s · ${statusCn[c.status] || c.status} · ${COMMERCIAL_CN[c.commercialStatus] || esc(c.commercialStatus)}</span></span></div>`).join('')
    : '<div class="hint">无匹配素材</div>';
}

async function edLibApplyBlock(blockId){
  // §36：Block 应用 = 给选中场景/层挂 graphic 层（layer.add 链，Block id 记 lineage）
  if (!ED.pid || !ED.sel) { alert('先在时间线选中场景或图层'); return; }
  const sceneId = ED.sel.type === 'layer' ? ED.sel.id.split('#')[0] : ED.sel.id;
  const r = await apiPost('/api/editor/command', {project_id: ED.pid, command: 'add_broll',
    targetId: sceneId, payload: {kind: 'graphic', blockId: blockId}, expectedVersion: ED.version});
  if (r.ok) {
    ED.version = r.composition_version;
    const comp = await (await fetch('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid))).json();
    ED.comp = comp; ED.version = comp.version;
    editorRenderTimeline();
    editorToast(`✅ 图形块「${blockId}」已加入场景 ${sceneId}`);
  } else {
    editorToast('❌ ' + ((r.errors||[{}])[0].message || r.error || '应用失败'), true);
  }
}
async function edLibApplyMotion(motionId){
  if (!ED.pid || !ED.sel || ED.sel.type !== 'layer') { alert('先在时间线选中一个图层'); return; }
  const r = await apiPost('/api/editor/command', {project_id: ED.pid, command: 'replace_motion',
    targetId: ED.sel.id, payload: {motionRecipeId: motionId}, expectedVersion: ED.version});
  if (r.ok) {
    ED.version = r.composition_version;
    const comp = await (await fetch('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid))).json();
    ED.comp = comp; ED.version = comp.version;
    editorRenderTimeline();
    editorToast(`✅ ${motionId} 已应用（Creative Library）`);
  } else { editorToast('❌ ' + ((r.errors||[{}])[0].message || r.error), true); }
}
async function edLibApplyAsset(assetId){
  if (!ED.pid || !ED.sel || ED.sel.type !== 'layer') { alert('先在时间线选中一个图层'); return; }
  const r = await apiPost('/api/editor/command', {project_id: ED.pid, command: 'replace_source',
    targetId: ED.sel.id, payload: {assetId}, expectedVersion: ED.version});
  if (r.ok) {
    ED.version = r.composition_version;
    const comp = await (await fetch('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid))).json();
    ED.comp = comp; ED.version = comp.version;
    editorRenderTimeline();
    editorToast(`✅ 素材已换为 ${assetId}`);
  } else { editorToast('❌ ' + ((r.errors||[{}])[0].message || r.error), true); }
}

function edLibOpenTemplate(tid){
  const t = TPLS.find(x=>x.id===tid);
  if (!t) return;
  const box = document.getElementById('ed-lib-list');
  const ratioOpts = t.supported_ratios.map(r=>`<option value="${r}">${r}</option>`).join('');
  const slotInputs = t.slots.map(s=>
    `<label>${esc(s.label)}${s.required?'（必填）':''}</label>`+
    (s.type === 'image' || s.type === 'video' || s.type === 'product' || s.type === 'person'
      ? `<input type="file" id="tpl-slot-${s.id}" accept=".mp4,.jpg,.jpeg,.png,.webp,.mov">`
      : `<input type="text" id="tpl-slot-${s.id}" placeholder="填入${esc(s.label)}">`)
  ).join('');
  box.innerHTML = `<div><b>试用模板：${esc(t.name)}</b>`+
    `<label>画幅</label><select id="tpl-ratio">${ratioOpts}</select>`+slotInputs+
    `<div class="btn-row"><button class="btn" onclick="instantiateTemplate('${t.id}')">🧩 生成可编辑项目</button>`+
    `<button class="btn-secondary btn-sm" onclick="edLibNav('templates')">← 返回模板列表</button></div>`+
    `<div class="hint" id="tpl-status"></div></div>`;
}

// ===========================================================================
// 模板库（Template Gallery）
// ===========================================================================
let TPLS = [];
async function loadTemplates(){
  window.TPLS = await (await fetch('/api/registry/templates')).json();
  renderTemplateGallery();
}
function renderTemplateGallery(){
  const q = ((document.getElementById('tpl-search')||{}).value || '').toLowerCase();
  const cat = (document.getElementById('tpl-category')||{}).value || '';
  const list = TPLS.filter(t =>
    (!cat || t.category === cat) &&
    (!q || (t.name + t.id).toLowerCase().includes(q))
  );
  document.getElementById('templates-list').innerHTML = list.map(t=>
    `<div class="proj-row"><span><b>${esc(t.name)}</b> <code>${esc(t.id)}</code><br>`+
    `<span class="hint">画幅 ${t.supported_ratios.join('/')} · 建议 ${t.duration_recommended}s · ${t.scene_count} 场景 · 授权 ${esc(t.commercial_status)}</span></span>`+
    `<button class="btn-sm" onclick="openTemplateForm('${t.id}')">试用</button></div>`).join('')
    || '<div class="hint">无匹配模板</div>';
}
function openTemplateForm(tid){
  const t = TPLS.find(x=>x.id===tid);
  const box = document.getElementById('template-form-box');
  const ratioOpts = t.supported_ratios.map(r=>`<option value="${r}">${r}</option>`).join('');
  const slotInputs = t.slots.map(s=>
    `<label>${esc(s.label)}${s.required?'（必填）':''} [${s.type}]</label>`+
    (s.type === 'image' || s.type === 'video' || s.type === 'product' || s.type === 'person'
      ? `<input type="file" id="tpl-slot-${s.id}" accept=".mp4,.jpg,.jpeg,.png,.webp,.mov">`
      : `<input type="text" id="tpl-slot-${s.id}" placeholder="填入${esc(s.label)}">`)
  ).join('');
  box.innerHTML = `<div class="card"><b>试用模板：${esc(t.name)}</b>`+
    `<label>画幅</label><select id="tpl-ratio">${ratioOpts}</select>`+
    slotInputs +
    `<div class="btn-row"><button onclick="instantiateTemplate('${t.id}')">🧩 生成可编辑项目</button></div>`+
    `<div class="hint" id="tpl-status"></div></div>`;
  box.scrollIntoView({behavior:'smooth'});
}
async function instantiateTemplate(tid){
  const t = TPLS.find(x=>x.id===tid);
  const slotValues = {};
  for (const s of t.slots) {
    const el = document.getElementById('tpl-slot-' + s.id);
    if (!el) continue;
    if (el.type === 'file') continue;  // 文件槽只走 FormData 文件域，不进 JSON
    if (el.value.trim()) { slotValues[s.id] = el.value.trim(); }
  }
  const fd = new FormData();
  fd.append('template_id', tid);
  fd.append('ratio', document.getElementById('tpl-ratio').value);
  fd.append('slot_values_json', JSON.stringify(slotValues));
  for (const s of t.slots) {
    const el = document.getElementById('tpl-slot-' + s.id);
    if (el && el.type === 'file' && el.files.length) fd.append(s.id + '_file', el.files[0], el.files[0].name);
  }
  document.getElementById('tpl-status').textContent = '生成中……';
  const resp = await fetch('/api/templates/instantiate', {method:'POST', body: fd});
  const r = await resp.json();
  if (r.ok) {
    document.getElementById('tpl-status').textContent =
      `✅ 项目已建立：${r.project_id}（${r.scene_count} 场景 / ${r.duration}s）→ 正在打开编辑器…`;
    setTimeout(async ()=>{
      switchTab('editor');
      await editorInit();  // 重新拉项目下拉（新项目刚建，旧列表没有）
      document.getElementById('editor-project').value = r.project_id;
      editorLoad();
    }, 800);
  } else {
    document.getElementById('tpl-status').textContent = '❌ ' + (r.error || '生成失败');
  }
}

// ===========================================================================
// 动效库（Motion Gallery：搜索/筛选/CSS 循环预览/应用到项目/收藏/意图筛选）
// ===========================================================================
let MOTIONS = [];
let MOTION_BEHAVIOR = {};  // §三十二 Style DNA：{id: {pinned: n, used: n, ...}}
async function loadMotions(){
  window.MOTIONS = await (await fetch('/api/registry/motions')).json();
  // 意图筛选项动态填充（§25：Semantic Intent 过滤）
  const intentSel = document.getElementById('motion-intent-filter');
  if (intentSel) {
    const intents = [...new Set(MOTIONS.flatMap(m => m.semantic_intents || []))].sort();
    intentSel.innerHTML = '<option value="">全部意图</option>' +
      intents.map(i=>`<option value="${esc(i)}">${esc(i)}</option>`).join('');
  }
  try {
    const r = await (await fetch('/api/style_dna/behaviors')).json();
    window.MOTION_BEHAVIOR = r.summary || {};
  } catch(e) { MOTION_BEHAVIOR = {}; }
  renderMotionGallery();
  renderMotionZones();
}
// §37 Motion 三区：Entrance（入场：fade/slide/reveal/scale）/ Emphasis（强调：
// pulse/highlight/underline 类）/ Exit（出场：fade/slide）按用途分组展示。
const MOTION_ZONES = {
  Entrance: ['motion.fade-in', 'motion.fade-slide', 'motion.rise-fade', 'motion.zoom-in-soft', 'motion.slide-left', 'motion.slide-right', 'motion.wipe-reveal', 'motion.ink-bleed', 'motion.card-assemble'],
  Emphasis: ['motion.scale-punch', 'motion.pulse-emphasis', 'motion.shake-emphasis', 'motion.spotlight-focus', 'motion.word-relay', 'motion.ken-burns-soft'],
  Exit: ['motion.fade-out', 'motion.scale-down-exit', 'motion.slide-down-exit', 'motion.blur-slide'],
};
function renderMotionZones(){
  const box = document.getElementById('motion-zones');
  if (!box) return;
  const zoneHtml = (label, ids) => `<div style="margin:1.2vh 0"><b style="color:#8ab4d8">${label}</b><div>`+
    ids.map(id => {
      const m = MOTIONS.find(x => x.id === id);
      if (!m) return '';
      const kf = motionCssPreview(m);
      return `<div class="motion-card" style="width:120px">`+
        `<div class="motion-preview ${m.preview_only ? 'motion-preview-dim' : ''}" style="${kf}">${esc(m.name)}</div>`+
        `<button class="btn-secondary btn-sm" style="margin-top:6px;width:100%" onclick="motionApplyToProject('${esc(m.id)}')">应用</button></div>`;
    }).join('') + '</div></div>';
  box.innerHTML = zoneHtml('入场', MOTION_ZONES.Entrance) +
                   zoneHtml('强调', MOTION_ZONES.Emphasis) +
                   zoneHtml('出场', MOTION_ZONES.Exit);
}
function renderMotionGallery(){
  const q = (document.getElementById('motion-search').value || '').toLowerCase();
  const f = document.getElementById('motion-filter').value;
  const intentSel = document.getElementById('motion-intent-filter');
  const intent = intentSel ? intentSel.value : '';
  const favChk = document.getElementById('motion-fav-only');
  const favOnly = favChk ? favChk.checked : false;
  const list = MOTIONS.filter(m =>
    (!f || m.motion_type === f) &&
    (!intent || (m.semantic_intents||[]).includes(intent)) &&
    (!favOnly || (MOTION_BEHAVIOR[m.id]||{}).pinned) &&
    (!q || (m.name + m.id).toLowerCase().includes(q))
  );
  document.getElementById('motions-list').innerHTML = list.map(m=>{
    const kf = motionCssPreview(m);
    const pinned = (MOTION_BEHAVIOR[m.id]||{}).pinned;
    const usedN = (MOTION_BEHAVIOR[m.id]||{}).used || 0;
    return `<div class="motion-card" data-mid="${esc(m.id)}">`+
      `<div class="motion-preview ${m.preview_only ? 'motion-preview-dim' : ''}" style="${kf}">预览</div>`+
      `<div class="motion-title">${esc(m.name)} <button class="fav-btn ${pinned?'pinned':''}" title="收藏（风格记忆）" onclick="toggleMotionPin('${esc(m.id)}', this)">${pinned?'★':'☆'}</button></div>`+
      `<div class="hint">${esc(m.id)} · ${MOTION_TYPE_CN[m.motion_type]||m.motion_type} · ${m.duration_recommended}s${m.preview_only ? ' · 仅预览' : ''}${usedN ? ' · 用过'+usedN+'次' : ''}</div>`+
      `<button class="btn-secondary btn-sm" onclick="motionApplyToProject('${esc(m.id)}')">应用到项目</button>`+
      `</div>`;
  }).join('') || '<div class="hint">无匹配动效</div>';
}
async function toggleMotionPin(mid, btn){
  const has = (MOTION_BEHAVIOR[mid]||{}).pinned;
  const r = await apiPost('/api/style_dna/record', {item_id: mid, behavior: has ? 'avoided' : 'pinned', item_kind: 'motion'});
  if (r.ok) {
    MOTION_BEHAVIOR[mid] = MOTION_BEHAVIOR[mid]||{};
    if (has) { delete MOTION_BEHAVIOR[mid].pinned; btn.classList.remove('pinned'); btn.textContent='☆'; }
    else { MOTION_BEHAVIOR[mid].pinned = 1; btn.classList.add('pinned'); btn.textContent='★'; }
  }
}
function motionCssPreview(m){
  // channels → CSS keyframes 循环（opacity/scale/x/y → transform+opacity）
  const parts = [];
  let from = {}, to = {};
  for (const ch of m.channels) {
    const v0 = ch.keyframes[0].value, v1 = ch.keyframes[ch.keyframes.length-1].value;
    if (ch.property === 'opacity') { from.opacity = v0; to.opacity = v1; }
    if (ch.property === 'scale') { from.scale = v0; to.scale = v1; }
    if (ch.property === 'x') { from.tx = v0; to.tx = v1; }
    if (ch.property === 'y') { from.ty = v0; to.ty = v1; }
  }
  const tf = (o) => `scale(${o.scale !== undefined ? o.scale : 1}) translate(${o.tx||0}px, ${o.ty||0}px)`;
  const dur = Math.max(0.4, Math.min(2.5, m.duration_recommended));
  const kfName = 'mp_' + m.id.replace(/[^a-z0-9_]/gi, '');
  // 注入 keyframes（幂等：同 id 覆盖）
  let styleEl = document.getElementById('kf_' + kfName);
  if (!styleEl) { styleEl = document.createElement('style'); styleEl.id = 'kf_' + kfName; document.head.appendChild(styleEl); }
  styleEl.textContent = `@keyframes ${kfName}{0%{opacity:${from.opacity!==undefined?from.opacity:1};transform:${tf(from)}}100%{opacity:${to.opacity!==undefined?to.opacity:1};transform:${tf(to)}}}`;
  return `animation:${kfName} ${dur}s ease-in-out infinite alternate`;
}
async function motionApplyToProject(mid){
  if (!ED.pid || !ED.sel || ED.sel.type !== 'layer') {
    alert('先在「编辑器」打开项目并选中一个图层，再回来应用动效'); switchTab('editor'); return;
  }
  const r = await apiPost('/api/editor/command', {project_id: ED.pid, command: 'replace_motion',
    targetId: ED.sel.id, payload: {motionRecipeId: mid}, expectedVersion: ED.version});
  if (r.ok) {
    ED.version = r.composition_version;
    const comp = await (await fetch('/api/editor/composition?project_id=' + encodeURIComponent(ED.pid))).json();
    ED.comp = comp; ED.version = comp.version;
    editorToast(`✅ ${mid} 已应用到 ${ED.sel.id}`);
    switchTab('editor'); editorRenderTimeline();
  } else {
    const err = (r.errors && r.errors[0]) || {};
    alert('❌ ' + (err.message || r.error));
  }
}

// ---- 通用 POST 助手（Editor/Proposal/Resolve 全走这里）----

// §8 拆分导出：inline onclick/window 引用兼容（零行为改动）
Object.assign(window, {
  editorLoadVersions,
  editorCompareSelected,
  editorPreflight,
  editorRenderFinal,
  editorToast,
  edLibNav,
  edLibLoad,
  edLibStockSearch,
  edLibApplyBlock,
  edLibApplyMotion,
  edLibApplyAsset,
  edLibOpenTemplate,
  loadTemplates,
  renderTemplateGallery,
  openTemplateForm,
  instantiateTemplate,
  loadMotions,
  renderMotionZones,
  renderMotionGallery,
  toggleMotionPin,
  motionCssPreview,
  motionApplyToProject,
  ED_LIB,
  TPLS,
  MOTIONS,
  MOTION_BEHAVIOR,
});
