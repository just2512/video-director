// §10/§11/§26/§67 Library（方案《FRONTEND_DEEP_UNIFICATION》）：
// 左栏四领域：内容（素材/图库/生成/场景/文案）、设计（镜头/模板/动效/转场/风格/
// 标题/人物条/字幕样式/图形）、声音（旁白/BGM/SFX）、参考（参考视频/软件项目/证据）。
//
// §67 统一 Picker 契约：search / preview / compatibility(SUPPORTED→可选，
// MISSING_REQUIREMENT→显示原因，INCOMPATIBLE→禁用+原因) / apply / cancel。
// 应用一律 Command → Composition → Revision（§11 不再点即改）。


// §10 领域 → 子入口表（domain → [{id, label, kind}]）
const LIB_DOMAINS = {
  content: {label: '内容', subs: [
    {id: 'scenes', label: '场景'}, {id: 'script', label: '文案'},
    {id: 'assets', label: '项目素材'}, {id: 'stock', label: '图库'},
    {id: 'generate', label: '生成'},
  ]},
  design: {label: '设计', subs: [
    {id: 'shots', label: '镜头'}, {id: 'templates', label: '模板'},
    {id: 'motions', label: '动效'}, {id: 'transitions', label: '转场'},
    {id: 'titles', label: '标题样式'}, {id: 'lower_thirds', label: '人物条'},
    {id: 'subtitle_styles', label: '字幕样式'}, {id: 'graphics', label: '图形'},
  ]},
  sound: {label: '声音', subs: [
    {id: 'voice', label: '配音'}, {id: 'bgm', label: '背景音乐'}, {id: 'sfx', label: '音效'},
  ]},
  reference: {label: '参考', subs: [
    {id: 'references', label: '参考视频'}, {id: 'repo', label: '软件项目'},
    {id: 'evidence', label: '素材证据'},
  ]},
};
window.ED_LIB = {domain: 'content', sub: 'scenes', blocks: [], loaded: false};

/** 领域切换（§10）。sub 可选：指定子入口。 */
function edLibDomain(domain, sub){
  ED_LIB.domain = domain;
  document.querySelectorAll('#ed-lib-nav button').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-lib') === domain);
  });
  const subnav = document.getElementById('ed-lib-subnav');
  if (subnav) {
    subnav.innerHTML = (LIB_DOMAINS[domain].subs).map(s =>
      `<button data-sub="${s.id}" class="${(sub || ED_LIB.sub) === s.id ? 'active' : ''}" onclick="edLibSub('${s.id}')">${esc(s.label)}</button>`).join('');
  }
  ED_LIB.sub = sub && LIB_DOMAINS[domain].subs.some(s => s.id === sub) ? sub : LIB_DOMAINS[domain].subs[0].id;
  edLibLoad();
}
function edLibSub(sub){
  ED_LIB.sub = sub;
  document.querySelectorAll('#ed-lib-subnav button').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-sub') === sub);
  });
  edLibLoad();
}
// 旧入口兼容（edLibNav('scenes') → 内容/场景）
function edLibNav(kind){
  for (const [d, cfg] of Object.entries(LIB_DOMAINS)) {
    if (cfg.subs.some(s => s.id === kind)) { edLibDomain(d, kind); return; }
  }
  edLibDomain('content');
}

/** §67 Picker 状态徽标（SUPPORTED/MISSING_REQUIREMENT/INCOMPATIBLE/UNKNOWN）。 */
function _pickerStatusCn(c){
  if (!c || c.compatibility === 'SUPPORTED' || c.status === 'SUPPORTED') return {t: '', cls: 'cap-ok'};
  if (c.compatibility === 'INCOMPATIBLE' || c.status === 'INCOMPATIBLE')
    return {t: '不适用', cls: 'cap-off', reason: (c.missing_requirements || c.reasons || []).join('；') || '与当前对象不匹配'};
  return {t: '缺条件', cls: 'cap-warn', reason: (c.missing_requirements || c.reasons || []).join('；')};
}

// ---------------------------------------------------------------------------
// §67 统一 Picker：搜索 + 兼容态 + §11 预览→应用两步（不再点即改）
// ---------------------------------------------------------------------------
let _pickerStaged = null;  // §11 预览暂存：{id, label, note}——应用/取消前持续可见
window._pickerStaged = null;  // 可观察（验收/调试用；setter 同步见下）
function _setPickerStaged(v){
  _pickerStaged = v;
  window._pickerStaged = v;
}

async function pickerOpen({title, items, onApply, describe}) {
  // items: [{id, name, note, compatibility, reason}]；onApply(id)；describe 可选
  const q = ((document.getElementById('ed-lib-search')||{}).value || '').toLowerCase();
  const list = items.filter(it => !q || (it.name + ' ' + it.id).toLowerCase().includes(q));
  const box = document.getElementById('ed-lib-list');
  if (!list.length) { box.innerHTML = '<div class="hint">无匹配项</div>'; return; }
  box.innerHTML = `<div class="hint" style="margin:2px 0 6px">${esc(title)}——点「预览」选中看说明，再「应用」写入项目。</div>` +
    (_pickerStaged ? `<div class="ok-box" style="padding:8px;margin:4px 0">已预览：<b>${esc(_pickerStaged.label)}</b>
      <div class="hint">${esc(_pickerStaged.note)}</div>
      <div class="btn-row" style="margin-top:6px">
        <button class="btn-sm" onclick="pickerApply('${esc(_pickerStaged.id)}')">应用</button>
        <button class="btn-secondary btn-sm" onclick="pickerCancel()">取消（不修改）</button>
      </div></div>` : '') +
    list.map(it => {
      const st = _pickerStatusCn(it);
      const dis = st.cls === 'cap-off';
      return `<div class="proj-row"><span><b>${esc(it.name)}</b>`+
        `<span class="hint">${esc(it.note || '')}${st.t ? ` · <span class="${st.cls}"${st.reason ? ` title="${esc(st.reason)}"` : ''}>${st.t}</span>` : ''}</span>`+
        (DEV.mode && it.id ? `<span class="dev-only hint"><code>${esc(it.id)}</code></span>` : '')+
        `</span><span class="btn-row">`+
        `<button class="btn-secondary btn-sm" onclick='pickerPreviewStage(${esc(JSON.stringify({id: it.id, label: it.name, note: describe ? describe(it) : (it.note || it.name)}))})'>预览</button>`+
        `<button class="btn-sm"${dis ? ' disabled title="' + esc(st.reason || '不适用') + '"' : ` onclick="pickerApply('${esc(it.id)}')"`}>直接应用</button>`+
        `</span></div>`;
    }).join('');
  window._pickerApply = onApply;
}
/** §11 预览某候选：暂存 + 说明呈现（中央 Preview 区同步提示），应用前不入项目。 */
function pickerPreviewStage(item){
  _setPickerStaged(item);
  editorToast(`已预览「${item.label}」：${item.note}——确认后「应用」，或「取消」不修改`);
  const stage = document.getElementById('ed-preview-stage');
  if (stage && !stage.querySelector('video')) {
    stage.innerHTML = `<div class="hint">预览中：${esc(item.label)}<br>${esc(item.note)}<br>（应用后体现在时间线）</div>`;
  }
  edLibLoad();  // 重渲染出 [应用][取消] 两步区
}
/** §11 取消：清暂存，零修改离开。 */
function pickerCancel(){
  const label = _pickerStaged ? _pickerStaged.label : '';
  _setPickerStaged(null);
  edLibLoad();
  editorToast(label ? `已取消：未应用「${label}」，项目无变化` : '已取消');
}
async function pickerApply(id){
  const fn = window._pickerApply;
  const staged = _pickerStaged;
  _setPickerStaged(null);
  if (typeof fn === 'function') await fn(id, staged);
}

// ---------------------------------------------------------------------------
// 各子入口装载
// ---------------------------------------------------------------------------
async function edLibLoad(){
  const box = document.getElementById('ed-lib-list');
  if (!box) return;
  if (!ED.comp || !ED.pid) {
    box.innerHTML = '<div class="hint">先打开一个项目（上方选择项目）。</div>';
    return;
  }
  const sub = ED_LIB.sub;
  box.innerHTML = '<div class="hint">加载中……</div>';
  try {
    // ---- 内容域 ----
    if (sub === 'scenes') {
      const list = ED.comp.scenes;
      box.innerHTML = list.map((s, i) =>
        `<div class="proj-row" onclick="selectScene('${esc(s.id)}', null, 'library')"><span><b>场景 ${i + 1}</b>`+
        `<span class="hint">${s.duration.toFixed(1)}s · ${esc((s.text||'').slice(0, 24))}</span></span></div>`).join('');
    } else if (sub === 'script') {
      box.innerHTML = ED.comp.scenes.map((s, i) =>
        `<div class="proj-row"><span><span class="hint">#${i + 1} · ${s.duration.toFixed(1)}s</span><br>${esc(s.text || '')}</span></div>`).join('');
    } else if (sub === 'assets') {
      const assets = await apiGet('/api/assets_library');
      const list = (Array.isArray(assets) ? assets : []).slice(0, 200);
      const selectedAssets = ED_LIB.selectedAssets || new Set();
      ED_LIB.selectedAssets = selectedAssets;
      const unused = list.filter(a => !(a.used_in || []).length);
      const allUnusedSelected = unused.length > 0 && unused.every(a => selectedAssets.has(a.asset_id));
      const controls = unused.length ? `
        <div class="btn-row" style="align-items:center;margin-bottom:6px">
          <label style="margin:0"><input type="checkbox" ${allUnusedSelected?'checked':''} onchange="edLibToggleUnusedAll(this.checked)"> 全选未使用素材</label>
          <span class="hint">${selectedAssets.size ? '已选择 '+selectedAssets.size+' 个' : ''}</span>
          ${selectedAssets.size ? '<button class="btn-secondary btn-sm pm-act-danger" onclick="edLibBulkDeleteAssets()">删除所选未使用素材</button>' : ''}
        </div>` : '';
      box.innerHTML = controls + (list.length ? list.map(a => `
        <div class="proj-row">
          <span style="width:28px"><input type="checkbox" ${selectedAssets.has(a.asset_id)?'checked':''}
            ${(a.used_in||[]).length?'disabled title="素材仍被项目使用，不能删除"':''}
            onchange="edLibToggleAsset('${esc(a.asset_id)}',this.checked)" onclick="event.stopPropagation()"></span>
          <span style="flex:1"><b>${esc(a.asset_id)}</b>
            <span class="hint">${esc(a.type)} · 用于 ${(a.used_in||[]).length} 项目${(a.used_in||[]).length?' · 已使用，不能删除':' · 未使用，可删除'}</span>
          </span>
          <button class="btn-secondary btn-sm" onclick="edLibApplyAsset('${esc(a.asset_id)}')">应用到选中层</button>
        </div>`).join('') : '<div class="hint">项目素材库为空</div>');
    } else if (sub === 'stock') {
      box.innerHTML = `<div class="btn-row"><input type="text" id="ed-stock-q" class="rev-input" style="flex:1" placeholder="搜索图库素材（如 coffee）" onkeydown="if(event.key==='Enter')edLibStockSearch()">
        <button class="btn-secondary btn-sm" onclick="edLibStockSearch()">搜索</button></div>
        <div id="ed-stock-results" class="hint" style="margin-top:6px">Pexels / Pixabay / Coverr 统一入口（需在「设置」配置图库密钥）</div>`;
    } else if (sub === 'generate') {
      box.innerHTML = '<div class="hint">生成画面面板在时间线下方——选中场景后填描述，生成 A/B/C 变体后「加入当前场景」。</div>';
    }
    // ---- 设计域 ----
    else if (sub === 'shots') {
      // §11 换镜头绑定当前场景
      if (!SEL.selection || SEL.selection.type !== 'scene') {
        box.innerHTML = '<div class="hint">先在时间线选中一个场景，再换镜头。</div>'; return;
      }
      const sc = ED.comp.scenes.find(s => s.id === SEL.selection.id);
      const r = await apiPost('/api/registry/shots/resolve', {
        semantic_intents: [], content_type: 'product_video', duration: sc.duration,
        available_asset_types: ['image','product','video'],
        scene_position: ED.comp.scenes[0].id === sc.id ? 'first' : 'middle',
      });
      const cands = (r.candidates || []);
      devCacheShots(cands);
      await pickerOpen({
        title: '镜头方案（换镜头不换素材）',
        items: cands.map(c => ({id: c.recipe_id, name: c.name,
          note: (c.reasons || []).join('；'), compatibility: c.compatibility,
          missing_requirements: c.missing_requirements})),
        onApply: (rid) => editorApplyShot(SEL.selection.id, rid),
      });
    } else if (sub === 'templates') {
      const tpls = await apiGet('/api/registry/templates');
      window.TPLS = tpls;
      const q = ((document.getElementById('ed-lib-search')||{}).value || '').toLowerCase();
      const list = tpls.filter(t => !q || (t.name + t.id).toLowerCase().includes(q));
      box.innerHTML = list.map(t => {
        const rt = t.runtime || {};
        const status = rt.status || 'UNKNOWN';
        const label = status === 'BLOCKED' ? '不可执行' :
          (status === 'PARTIAL' ? '可生成样片 · 部分效果未实现' :
          (status === 'READY' ? '运行前检查通过' : '状态未知'));
        const notes = [...(rt.blockers || []), ...(rt.warnings || [])]
          .map(x => x.message).filter(Boolean).slice(0, 2).join('；');
        const statusClass = status === 'BLOCKED' ? 'cap-off' :
          (status === 'PARTIAL' ? 'cap-warn' : 'cap-ok');
        return `<div class="proj-row"><span><b>${esc(t.name)}</b>`+
          `<span class="hint">画幅 ${t.supported_ratios.join('/')} · ${t.scene_count} 场景</span>`+
          `<span class="${statusClass}">${esc(label)}</span>`+
          (notes ? `<span class="hint">${esc(notes)}</span>` : '')+`</span>`+
          `<button class="btn-secondary btn-sm" onclick="edLibOpenTemplate('${esc(t.id)}')">${status === 'BLOCKED' ? '查看原因' : '试用'}</button></div>`;
      }).join('') || '<div class="hint">无匹配模板</div>';
    } else if (sub === 'motions') {
      if (!SEL.selection || SEL.selection.type !== 'layer') {
        box.innerHTML = '<div class="hint">先在时间线选中一个图层，再换动效。</div>'; return;
      }
      const r = await apiPost('/api/registry/motions/resolve', {layer_type: 'image', semantic_intent: 'enter', duration: 0.5});
      const cands = r.candidates || [];
      await pickerOpen({
        title: '动效（应用到选中图层）',
        items: cands.map(c => ({id: c.recipe_id, name: c.name,
          note: c.duration ? `建议 ${c.duration}s` : '', compatibility: c.compatibility,
          missing_requirements: c.missing_requirements})),
        onApply: (mid) => edLibApplyMotion(mid),
      });
    } else if (sub === 'transitions') {
      // §26 Transition Registry → Scene Pair Picker
      if (!SEL.selection || SEL.selection.type !== 'scene_pair') {
        box.innerHTML = '<div class="hint">镜头间转场作用于「镜头关系」——先选中相邻两镜（时间线上 Shift 范围选，或在 Inspector 打开镜头关系）。</div>'; return;
      }
      const trs = await apiGet('/api/registry/transitions');
      const [f, t] = SEL.selection.id.split('->');
      const items = (Array.isArray(trs) ? trs : []).map(x => ({
        id: x.id, name: x.name,
        note: (x.tags || []).slice(0, 3).join('·'),
        compatibility: x.commercial_status === 'BLOCKED' ? 'INCOMPATIBLE' : 'SUPPORTED',
      }));
      await pickerOpen({
        title: `转场（${f} → ${t}）`,
        items,
        onApply: (tid) => applyTransitionToPair(f, t, tid),
      });
    } else if (sub === 'titles' || sub === 'lower_thirds' || sub === 'subtitle_styles' || sub === 'graphics') {
      // §43/§71 四层证据：无真实 Command 的类型普通用户不显示（NOT_IMPLEMENTED
      // 只在 Developer Mode 可见——不允许「看起来能点，点了没效果」）。
      // subtitle_styles 有 set_subtitle_style 执行链 → 正常显示。
      const IMPLEMENTED = new Set(['subtitle_styles']);
      if (!IMPLEMENTED.has(sub) && !(window.DEV && DEV.mode)) {
        box.innerHTML = '<div class="hint">该样式类型暂未开放（无完整执行链）。</div>';
        return;
      }
      // §26/§46 Title/LowerThird/Subtitle/Graphic Registry → 统一 Picker
      const apiMap = {titles: '/api/registry/titles', lower_thirds: '/api/registry/lower_thirds',
                      subtitle_styles: '/api/registry/subtitles', graphics: '/api/registry/graphics'};
      const items = await apiGet(apiMap[sub]);
      const list = Array.isArray(items) ? items : [];
      const what = {titles: '标题样式', lower_thirds: '人物条样式',
                    subtitle_styles: '字幕样式', graphics: '图形'}[sub];
      await pickerOpen({
        title: `${what}（${IMPLEMENTED.has(sub) ? '应用到当前项目' : 'Developer 预览：未接线'}）`,
        items: list.map(x => ({id: x.id, name: x.name,
          note: (x.description || (x.tags || []).slice(0, 2).join('·') || ''),
          compatibility: x.commercial_status === 'BLOCKED' ? 'INCOMPATIBLE'
            : (IMPLEMENTED.has(sub) ? 'SUPPORTED' : 'INCOMPATIBLE')})),
        onApply: (id) => applyRegistryStyle(sub, id),
      });
    }
    // ---- 声音域 ----
    else if (sub === 'voice') {
      const r = await apiGet('/api/voice/providers');
      const provs = (r && r.providers) || [];
      const voices = (r && r.voices) || [];
      box.innerHTML = `<div class="hint">配音来源：${provs.map(p => esc(p.name || p.id) + (p.available ? '' : '（不可用）')).join('、') || '无'}</div>`+
        `<div class="hint">配音在项目 Inspector「声音策略 → 配音设置」；此处试听。</div>`+
        voices.slice(0, 12).map(v =>
          `<div class="proj-row"><span><b>${esc(v.name || v.voice_id || v)}</b></span>`+
          `<button class="btn-secondary btn-sm" onclick="edVoiceTry('${esc(v.voice_id || v.id || v)}')">试听</button></div>`).join('');
    } else if (sub === 'bgm' || sub === 'sfx') {
      const kind = sub;
      const lib = await apiGet(kind === 'bgm' ? '/api/bgm' : '/api/sfx');
      const list = Array.isArray(lib) ? lib : [];
      box.innerHTML = list.length ? list.map(a =>
        `<div class="proj-row"><span><b>${esc(a.label || a.id)}</b><span class="hint">${esc(a.id)}</span></span>`+
        `<span class="btn-row">`+
        `<button class="btn-secondary btn-sm" onclick="edLibAudioTry('${esc(a.id)}')">试听</button>`+
        `<button class="btn-sm" onclick="edLibAudioApply('${kind}','${esc(a.id)}')">应用</button></span></div>`).join('')
        : '<div class="hint">音频库为空（可在设置上传本地音乐）</div>';
    }
    // ---- 参考域 ----
    else if (sub === 'references') {
      box.innerHTML = `<div class="hint">上传优秀视频分析镜头结构/节奏/运动语言（在「新建项目 → 从参考视频开始」使用分析结果）。</div>
        <div class="btn-row"><button class="btn-secondary btn-sm" onclick="switchTab('home');openCreate()">去新建项目（参考视频起点）</button></div>`;
    } else if (sub === 'repo') {
      box.innerHTML = `<div class="hint">从软件项目发现可验证的卖点做宣传片（证据链追溯，AI 不编造功能）。</div>
        <div class="btn-row"><button class="btn-secondary btn-sm" onclick="switchTab('home');openCreate()">去新建项目（软件项目起点）</button></div>`;
    } else if (sub === 'evidence') {
      // 素材引用/来源（§48 References：asset 引用证据）
      const refs = [];
      for (const sc of ED.comp.scenes) for (const lay of (sc.layers||[])) {
        if (lay.asset_id && !refs.includes(lay.asset_id)) refs.push(lay.asset_id);
      }
      box.innerHTML = refs.length ? refs.map(r =>
        `<div class="proj-row"><span><b>${esc(r)}</b><span class="hint">可作生成引用</span></span></div>`).join('')
        : '<div class="hint">项目暂无素材引用</div>';
    }
  } catch (e) {
    box.innerHTML = `<div class="err-box">加载失败：${esc(e.message || e)}</div>`;
  }
}

// ---- Picker apply 实现（全部 Command → Revision）----
function edLibToggleAsset(assetId, checked){
  ED_LIB.selectedAssets=ED_LIB.selectedAssets||new Set();
  if(checked)ED_LIB.selectedAssets.add(assetId);else ED_LIB.selectedAssets.delete(assetId);
  edLibLoad();
}
function edLibToggleUnusedAll(checked){
  apiGet('/api/assets_library').then(list=>{
    const unused=(Array.isArray(list)?list:[]).filter(a=>!(a.used_in||[]).length);
    ED_LIB.selectedAssets=ED_LIB.selectedAssets||new Set();
    for(const a of unused){if(checked)ED_LIB.selectedAssets.add(a.asset_id);else ED_LIB.selectedAssets.delete(a.asset_id);}
    edLibLoad();
  });
}
async function edLibBulkDeleteAssets(){
  const ids=[...(ED_LIB.selectedAssets||[])];
  if(!ids.length)return;
  const r=await apiPost('/api/bulk_action',{resource:'assets',action:'delete',ids});
  if(!r.ok){editorToast('素材批量删除未完成：'+(r.error||''),true);return;}
  ED_LIB.selectedAssets.clear();edLibLoad();
  editorToast('已删除 '+(r.deleted||ids).length+' 个未使用素材');
}

function edLibApplyAsset(assetId){
  // §82 唯一通路：Asset Picker「应用到选中层」→ executeAction(asset.replace)
  if (!SEL.selection || SEL.selection.type !== 'layer') {
    editorToast('先在时间线选中一个图层，再应用素材', true); return;
  }
  executeAction('asset.replace', {assetId}, 'current_selection');
}
async function edLibApplyMotion(motionId){
  if (!SEL.selection || SEL.selection.type !== 'layer') { editorToast('先选中一个图层', true); return; }
  const r = await executeAction('visual.motion', {motionRecipeId: motionId}, 'current_selection');
  if (r && r.ok) editorToast(`动效已应用：${motionCnName(motionId)}`);
}
/** §26 转场 → Scene Pair continuity（transition 类型并入镜头关系语义）。 */
async function applyTransitionToPair(fromId, toId, transitionId){
  // §82：转场（Scene Pair 语义）→ executeAction(relation.set)
  const r = await executeAction('relation.set',
    {type: 'carry', sourceLayerId: fromId + '#0', targetLayerId: toId + '#0',
     transition: transitionId}, 'current_selection');
  if (r && r.ok) editorToast(`转场已应用到 ${fromId} → ${toId}`);
}
/** §26 样式 Registry 应用（title/lower_third/subtitle/graphic → 对应命令）。 */
async function applyRegistryStyle(sub, id){
  let cmd = null, payload = {styleId: id};
  if (sub === 'subtitle_styles') cmd = 'set_subtitle_style';
  if (cmd) {
    const r = await executeCommand({command: cmd, targetId: '', payload});
    if (r.ok) { editorToast('样式已应用'); refreshWorkspace(); }
    else editorToast('样式应用失败：' + (r.error || '') + '（该样式类型暂不支持一键应用，可参考描述手动设置）', true);
    return;
  }
  // Registry 中存在 ≠ 当前 Composition 有可执行 Command。没有命令就明确显示 MISSING，
  // 不再把“记录选择”伪装成已经生效。
  editorToast('MISSING：' + sub + ' 的样式目前没有对应的可执行 Action/Command，项目未改变。', true);
}
async function edLibStockSearch(){
  const inp = document.getElementById('ed-stock-q');
  const box = document.getElementById('ed-stock-results');
  if (!inp || !box) return;
  const q = inp.value.trim();
  if (!q) { box.textContent = '请输入关键词'; return; }
  box.textContent = '搜索中……';
  const r = await apiPost('/api/stock/search', {query: q, project_id: (typeof ED !== 'undefined' ? ED.pid : ''), ratio: ''});
  if (!r.ok) { box.innerHTML = `<div class="err-box">搜索失败：${esc(r.error || '')}${DEV.mode && r.code ? ' <code>' + esc(r.code) + '</code>' : ''}</div>`; return; }
  const cands = r.candidates || [];
  const stCn = {SUPPORTED:'可用', MISSING_REQUIREMENT:'素材不足', INCOMPATIBLE:'画幅不符', UNKNOWN:'待确认'};
  box.innerHTML = cands.length ? cands.map(c =>
    `<div class="proj-row"><span><b>${esc(c.provider)}</b> ${esc(c.visualDescription || c.sourceId)}`+
    `<span class="hint">${c.width}x${c.height} · ${stCn[c.status] || esc(c.status)}</span></span>`+
    `<button class="btn-secondary btn-sm" onclick="assetPickerStockDownload(${esc(JSON.stringify(c))})">下载采用</button></div>`).join('')
    : '<div class="hint">无匹配素材</div>';
}

// ---------------------------------------------------------------------------
// 素材选择器（AssetPicker：Inspector/加素材层共用，六 tab）
// ---------------------------------------------------------------------------
// 模板试用（§26：应用前表单填槽，生成可编辑项目 → workspaceOpen）
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
  const rt = t.runtime || {};
  const blocked = rt.status === 'BLOCKED';
  const rtLabel = blocked ? '当前环境不可执行' :
    (rt.status === 'PARTIAL' ? '可渲染静态样片；部分模板动效尚未实现' : '运行前检查通过');
  const rtNotes = [...(rt.blockers || []), ...(rt.warnings || [])]
    .map(x => x.message).filter(Boolean);
  const rtBoxClass = blocked ? 'err-box' : (rt.status === 'PARTIAL' ? 'hint' : 'cap-ok');
  box.innerHTML = `<div><b>试用模板：${esc(t.name)}</b>`+
    `<div class="${rtBoxClass}" id="tpl-runtime-status"><b>${esc(rtLabel)}</b>`+
    (rtNotes.length ? `<ul>${rtNotes.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '')+
    `<div class="hint">依赖状态/模板绑定不等于渲染证据。需运行样片并完成 MP4 全解码，才会显示 PASS。</div></div>`+
    `<label>画幅</label><select id="tpl-ratio">${ratioOpts}</select>`+slotInputs+
    `<div class="btn-row"><button class="btn" onclick="instantiateTemplate('${t.id}')"${blocked?' disabled':''}>生成可编辑项目</button>`+
    `<button class="btn-secondary" onclick="renderTemplateSample('${t.id}')"${blocked?' disabled':''}>运行样片并验证 MP4</button>`+
    `<button class="btn-secondary btn-sm" onclick="edLibSub('templates')">返回模板列表</button></div>`+
    `<div class="hint" id="tpl-status"></div><div id="tpl-sample-result"></div></div>`;
}
async function renderTemplateSample(tid){
  const t = TPLS.find(x=>x.id===tid);
  if (!t) { editorToast('模板不存在，请刷新模板清单', true); return; }
  const rt = t.runtime || {};
  if (rt.status === 'BLOCKED') { editorToast('模板运行前置检查未通过，不能伪造样片', true); return; }
  const status = document.getElementById('tpl-status');
  const resultBox = document.getElementById('tpl-sample-result');
  if (status) status.textContent = '正在运行真实 FFmpeg 样片并完整解码 MP4……';
  if (resultBox) resultBox.innerHTML = '';
  const slotValues = {};
  for (const s of t.slots) {
    const el = document.getElementById('tpl-slot-' + s.id);
    if (!el || el.type === 'file') continue;
    if (el.value.trim()) slotValues[s.id] = el.value.trim();
  }
  const fd = new FormData();
  fd.append('template_id', tid);
  fd.append('template_version', t.version);
  fd.append('ratio', document.getElementById('tpl-ratio').value);
  fd.append('slot_values_json', JSON.stringify(slotValues));
  for (const s of t.slots) {
    const el = document.getElementById('tpl-slot-' + s.id);
    if (el && el.type === 'file' && el.files.length) fd.append(s.id + '_file', el.files[0], el.files[0].name);
  }
  try {
    const r = await apiUpload('/api/templates/sample_render', fd);
    if (!r.ok) {
      if (status) status.textContent = (r.error || '样片验证失败');
      if (resultBox && r.runtime) {
        const details = [...(r.runtime.blockers || []), ...(r.runtime.warnings || [])]
          .map(x => x.message).filter(Boolean);
        resultBox.innerHTML = details.map(x => `<div class="hint">${esc(x)}</div>`).join('');
      }
      return;
    }
    const m = r.runtime && r.runtime.sample_mp4 || {};
    if (status) status.textContent =
      `MP4 验证 PASS · ${m.duration_seconds || '?'} 秒 · ${m.width || '?'}×${m.height || '?'} · ${m.bytes || '?'} 字节`;
    if (resultBox) resultBox.innerHTML =
      `<a href="${esc(r.download_url)}">下载已验证的样片 MP4</a>`+
      `<div class="hint">视频完整解码：PASS。模板运行状态：${esc(r.runtime.status)}；未实现的效果见上方提示。</div>`+
      [...(r.runtime.warnings || [])].map(x => `<div class="hint">${esc(x.message || '')}</div>`).join('');
  } catch (error) {
    if (status) status.textContent = '样片验证失败：' + (error.message || error);
  }
}

async function instantiateTemplate(tid){
  const t = TPLS.find(x=>x.id===tid);
  const slotValues = {};
  for (const s of t.slots) {
    const el = document.getElementById('tpl-slot-' + s.id);
    if (!el) continue;
    if (el.type === 'file') continue;
    if (el.value.trim()) slotValues[s.id] = el.value.trim();
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
  const r = await apiUpload('/api/templates/instantiate', fd);
  if (r.ok) {
    document.getElementById('tpl-status').textContent = `项目已建立（${r.scene_count} 场景），正在打开工作台……`;
    workspaceOpen(r.project_id);  // §56 pollUntil 确定性等待（不再 setTimeout 赌运气）
  } else {
    document.getElementById('tpl-status').textContent = (r.error || '生成失败');
  }
}

// ---------------------------------------------------------------------------
// §23/§46 AI Command Bar：当前对象的修改器（不是 Chat）。
// 选中对象 → 上下文行「正在修改：……」；Director 请求带 selection（/api/director/context）。
// 复杂动作 Proposal → Command → Composition；技术细节只入 Developer Mode。
// ---------------------------------------------------------------------------
async function directorRefreshContext(){
  const line = document.getElementById('director-ctx-line');
  if (!line) return;
  if (!ED.pid) { line.textContent = ''; return; }
  const label = selectionLabel();
  line.textContent = label ? `正在修改：${label}` : '未选中对象（指令会作用于整个项目）';
  try {
    // §46 Selection Context 上报 Director（失败不阻塞输入）
    const ctx = getSelectionContext();
    if (ctx.type !== 'none') {
      await apiPost('/api/director/context', {project_id: ED.pid, selection: ctx});
    }
  } catch (e) { /* 上下文上报失败静默 */ }
}

async function directorLoadCapCount(){
  try {
    const d = await apiGet('/api/director/capabilities');
    const n = (d.capabilities || []).length;
    const el = document.getElementById('director-cap-count');
    if (el) el.textContent = n;
  } catch(e) {}
}

function directorRenderNodeControls(n, box){
  const id=n.id, label=n.label||id;
  const escv=x=>esc(String(x==null?'':x));
  let html='<b>'+escv(label)+'</b><div class="hint" style="margin:4px 0 8px">这里直接操作，不展示内部命令、检查结果或 PASS。</div>';
  if(id==='understand'||id==='script') html+='<div class="hint">把修改要求直接写进下方 AI 导演输入框。</div><button class="btn-secondary btn-sm" data-dctrl="focus-ai">继续修改</button>';
  else if(id==='scene_plan'){
    html+='<div class="director-node-scenes">'+(ED.comp.scenes||[]).map((sc,i)=>'<div class="proj-row"><span style="flex:1"><b>场景 '+(i+1)+'</b> <span class="hint">'+escv((sc.text||'').slice(0,50))+' · '+sc.duration.toFixed(1)+'s</span></span><button class="btn-secondary btn-sm" data-dctrl="select-scene" data-scene-id="'+escv(sc.id)+'">选中</button></div>').join('')+'</div>';
    html+='<div class="hint" style="margin-top:6px">选中后可在时间线拖动排序、裁剪、改时长。</div>';
  } else if(id==='material_match'){
    const sc=(ED.comp.scenes||[]).find(s=>s.id===((typeof SEL!=='undefined'&&SEL.selection&&SEL.selection.type==='scene')?SEL.selection.id:''))||(ED.comp.scenes||[])[0];
    const assets=Object.values((ED.comp&&ED.comp.assets)||{}).filter(a=>a.type==='video'||a.type==='image');
    html+='<div class="hint">当前场景：'+escv(sc&&sc.text||'未选择')+'</div><div class="director-asset-grid">'+assets.slice(0,24).map(a=>'<div class="director-asset-card"><img src="/api/asset_thumbnail?asset_id='+encodeURIComponent(a.asset_id)+'" alt="" loading="lazy"><button class="btn-secondary btn-sm" data-dctrl="replace-asset" data-scene-id="'+escv(sc&&sc.id||'')+'" data-asset-id="'+escv(a.asset_id)+'">采用</button></div>').join('')+'</div>';
  } else if(id==='narration') html+='<button class="btn-secondary btn-sm" data-dctrl="voice">打开配音设置</button>';
  else if(id==='subtitle') html+='<button class="btn-secondary btn-sm" data-dctrl="subtitle">打开字幕设置</button>';
  else if(id==='music') html+='<button class="btn-secondary btn-sm" data-dctrl="audio">打开音乐/音量</button>';
  else if(id==='watermark') html+='<button class="btn-secondary btn-sm" data-dctrl="watermark">打开品牌设置</button>';
  else if(id==='composition') html+='<button class="btn-secondary btn-sm" data-dctrl="timeline">定位时间线</button>';
  else if(id==='preview') html+='<button class="btn-secondary btn-sm" data-dctrl="preview">实时预览</button>';
  else if(id==='verify') html+='<button class="btn-secondary btn-sm" data-dctrl="verify">执行预检</button>';
  else if(id==='render') html+='<button class="btn-secondary btn-sm" data-dctrl="render">渲染草稿</button>';
  else html+='<button class="btn-secondary btn-sm" data-dctrl="focus-ai">继续告诉 AI</button>';
  const props=(ED.comp&&ED.comp.transition_proposals)||[];
  if((id==='scene_plan'||id==='material_match')&&props.length){
    html+='<div class="role-block" style="margin-top:8px"><b>待确认转场</b>'+props.map(p=>{ const from=(ED.comp.scenes||[]).find(s=>s.id===p.from_scene_id); const applied=!!(from&&from.transition_out&&from.transition_out.type===p.type); return '<div class="proj-row"><span style="flex:1">'+escv(p.from_scene_id)+' → '+escv(p.to_scene_id)+' · '+escv(p.type)+' '+escv(p.duration)+'s</span>'+(applied?'<span class="hint">已应用</span>':'<button class="btn-secondary btn-sm" data-dctrl="transition" data-scene-id="'+escv(p.from_scene_id)+'" data-type="'+escv(p.type)+'" data-duration="'+escv(p.duration)+'">确认</button>')+'</div>'; }).join('')+'</div>';
  }
  box.innerHTML=html;
  box.querySelectorAll('[data-dctrl]').forEach(btn=>btn.addEventListener('click',async()=>{
    const a=btn.dataset.dctrl;
    if(a==='focus-ai'){document.getElementById('director-input')?.focus();return;}
    if(a==='select-scene'){editorSelectScene(btn.dataset.sceneId,null);return;}
    if(a==='replace-asset'){await directorReplaceSceneAsset(btn.dataset.sceneId,btn.dataset.assetId);return;}
    if(a==='voice'){editorVoiceOpen();return;} if(a==='subtitle'){editorSubtitleStyleOpen();return;}
    if(a==='audio'){editorAudioBusOpen();return;} if(a==='watermark'){projectOverlayOpen();return;}
    if(a==='timeline'){document.getElementById('editor-timeline')?.scrollIntoView({behavior:'smooth',block:'center'});return;}
    if(a==='preview'){previewRenderComposition();previewPlay();return;} if(a==='verify'){await executeAction('verify.run',{},'project');return;}
    if(a==='render'){await executeAction('render.draft',{},'project');return;}
    if(a==='transition'){await directorApplyTransition(btn.dataset.sceneId,btn.dataset.type,Number(btn.dataset.duration||0.5));}
  }));
}
async function directorReplaceSceneAsset(sceneId,assetId){
  const sc=(ED.comp.scenes||[]).find(s=>s.id===sceneId); if(!sc||!sc.layers||!sc.layers.length){editorToast('当前场景没有可替换画面',true);return;}
  const idx=sc.layers.findIndex(l=>l.asset_id||l.type==='video'||l.type==='image');
  const r=await executeCommand({command:'replace_source',targetId:sceneId+'#'+Math.max(0,idx),payload:{assetId:assetId}});
  if(r&&r.ok){await refreshWorkspace();editorToast('画面已替换');}
}
async function directorApplyTransition(sceneId,type,duration){
  const r=await executeCommand({command:'set_transition_out',targetId:sceneId,payload:{type:type,duration:duration}});
  if(r&&r.ok){await refreshWorkspace();editorToast('转场已确认并应用');}
}

function directorRenderPlan(run, detail){
  const card = document.getElementById('director-plan-card');
  const nodesBox = document.getElementById('director-plan-nodes');
  const goal = document.getElementById('director-plan-goal');
  const status = document.getElementById('director-plan-status');
  const detailBox = document.getElementById('director-plan-detail');
  if (!card || !nodesBox) return;
  const plan = (detail && detail.result && detail.result.director_plan) || (run && run.result && run.result.director_plan) || null;
  if (!plan || !Array.isArray(plan.nodes)) { card.style.display = 'none'; return; }
  card.style.display = '';
  if (goal) goal.textContent = plan.goal ? '· ' + String(plan.goal).slice(0, 90) : '';
  if (status) status.textContent = run.status === 'COMPLETED' ? '已完成' : run.status === 'WAITING_CONFIRMATION' ? '等待确认' : run.status === 'FAILED' ? '失败' : '进行中';
  const stepState = {};
  (detail && detail.steps || []).forEach(s => { stepState[s.id] = s.status; });
  const labelFor = {understand:'理解', script:'文案', scene_plan:'分镜', narration:'配音', material_match:'画面', subtitle:'字幕', music:'音乐', watermark:'品牌', composition:'时间线', preview:'预览', verify:'预检', render:'成片'};
  const stateText = s => s === 'DONE' ? '完成' : s === 'RUNNING' ? '执行中' : s === 'FAILED' ? '失败' : '待处理';
  nodesBox.innerHTML = plan.nodes.map(n => {
    let st = stepState[n.id] || 'PENDING';
    if (n.id === 'understand') st = detail ? 'DONE' : 'PENDING';
    if (n.id === 'render' && run.status === 'COMPLETED') st = 'DONE';
    return `<button class="btn-secondary btn-sm" data-director-node="${esc(n.id)}" style="min-width:78px;text-align:left;white-space:nowrap"><b>${esc(labelFor[n.id] || n.label || n.id)}</b><br><span class="hint">${stateText(st)}</span></button>`;
  }).join('<span style="align-self:center;color:var(--muted)">→</span>');
  nodesBox.querySelectorAll('[data-director-node]').forEach(btn => {
    btn.onclick = () => {
      const n = plan.nodes.find(x => x.id === btn.dataset.directorNode);
      if (!n) return;
      if (detailBox) {
        const inputs = Array.isArray(n.inputs) ? n.inputs : [];
        const outputs = Array.isArray(n.outputs) ? n.outputs : [];
        const verify = n.verify || {};
        const decision = n.decision || {};
        detailBox.innerHTML =
          `<b>${esc(n.label || n.id)}</b>：${esc(n.description || '')}` +
          `<div class="hint" style="margin-top:5px">输入：${inputs.length ? inputs.map(x => esc(String(x))).join('、') : '—'}</div>` +
          `<div class="hint">输出：${outputs.length ? outputs.map(x => esc(String(x))).join('、') : '—'}</div>` +
          `<div class="hint">验证：${esc(String(verify.status || (verify.ok === true ? 'PASS' : verify.ok === false ? 'FAIL' : 'PENDING')))}</div>` +
          (decision && Object.keys(decision).length
            ? `<div class="hint">决策：${esc(JSON.stringify(decision).slice(0, 360))}</div>`
            : '') +
          (Array.isArray(n.artifacts) && n.artifacts.length
            ? `<div class="hint">产物：${n.artifacts.map(x => esc(String(x))).join('、')}</div>`
            : '');
      }
      if (typeof directorRenderNodeControls === 'function') directorRenderNodeControls(n, detailBox);
      const input = document.getElementById('director-input');
      if (input && n.id !== 'understand') input.placeholder = `继续告诉 AI 如何处理“${n.label || n.id}”`;
    };
  });
  if (detailBox && !detailBox.innerHTML) {
    const current = (detail && detail.current) || '';
    detailBox.textContent = current ? `当前节点：${labelFor[current] || current}` : '点击任意节点查看该阶段';
  }
}

async function directorLoadRun(runId){
  if (!runId) return;
  try { const d = await apiGet('/api/workflows/' + encodeURIComponent(runId)); if (d && !d.error) directorRenderPlan(d, d); } catch (e) {}
}

async function directorLoadLatestRun(projectId){
  if (!projectId) return;
  try {
    const list = await apiGet('/api/workflows?project_id=' + encodeURIComponent(projectId));
    const runs = (list && list.runs) || [];
    for (const item of runs) {
      const d = await apiGet('/api/workflows/' + encodeURIComponent(item.run_id));
      if (d && d.result && d.result.director_plan) { directorRenderPlan(d, d); return d; }
    }
    const card = document.getElementById('director-plan-card'); if (card) card.style.display = 'none';
  } catch (e) {}
}
async function directorExecute(){
  if (!ED.pid) { editorToast('请先用“新建项目 → AI 导演”创建视频，或打开已有项目', true); return; }
  const inp = document.getElementById('director-input');
  const inst = (inp.value || '').trim();
  if (!inst) { editorToast('告诉 AI 想做什么，例如：第二段换真实生活画面，字幕小一点'); return; }
  const box = document.getElementById('director-result');
  box.innerHTML = '<div class="hint">AI 正在理解你的修改意图……</div>';
  const bar = document.getElementById('director-progress');
  // SSE 进度通道（已有则复用）
  try {
    if (!window._directorSSE) {
      window._directorSSE = new EventSource('/api/events');
      window._directorSSE.onmessage = (ev) => {
        try {
          const m = JSON.parse(ev.data);
          if (m.event === 'director') {
            const b = document.getElementById('director-progress');
            if (!b) return;
            const d = m.data || {};
            if (d.phase === 'planning') b.textContent = '规划中……';
            else if (d.phase === 'tool') b.textContent = `${d.ok ? '已执行' : '未成功'}：${d.tool || ''}`;
            else if (d.phase === 'done') b.textContent = d.verify_ok ? '全部完成（已验证）' : '部分未通过验证';
            else if (d.phase === 'error') b.textContent = '失败：' + (d.code || '');
          }
        } catch(e) {}
      };
    }
  } catch(e) {}
  if (bar) bar.textContent = '规划中……';
  const r = await apiPost('/api/director/execute', {project_id: ED.pid, instruction: inst});
  if (!r.ok) {
    box.innerHTML = `<div class="err-box">未完成：${esc(r.error || '执行失败')}（项目内容没有变化${r.code === 'CONFLICT' ? '，请重试' : ''}）</div>`;
    return;
  }
  const d = r.director || {};
  const failures = (d.executed||[]).filter(e => !e.ok);
  const reasons = (d.plan||[]).map(p => p.reason).filter(Boolean);
  // §11/§23 人话优先：改了什么/是否成功；tool id/verify 入 dev-only
  box.innerHTML =
    `<div class="hint"><b>${failures.length ? '部分修改未应用' : '已应用'}</b>`+
    ` · ${(d.executed||[]).length} 处修改 · 版本 ${ED.version || ''}</div>`+
    (reasons.length ? `<div class="hint">${reasons.map(x=>esc(x)).join('；')}</div>` : '')+
    (failures.length ? `<div class="hint" style="color:var(--danger)">未应用：${failures.map(e=>esc(e.error||e.tool)).join('；')}（可换个说法再试，或在检视器手动修改）</div>` : '')+
    (DEV.mode ? `<div class="hint dev-only" style="margin-top:4px">计划：`+
      (d.plan||[]).map(p=>`<code>${esc(p.action || p.tool || '')}</code>`).join(' · ') + `</div>`+
      (d.executed||[]).map(e=>`<div class="hint dev-only" style="font-size:11px">${e.ok?'OK':'FAIL'} <code>${esc(e.tool)}</code> ${esc(e.status||'')}</div>`).join('')
    : '')+
    `<div class="hint" style="margin-top:2px">${d.verify_ok ? '已验证生效' : (failures.length ? '请先处理未应用项' : '部分步骤待验证')}</div>`;
  await refreshComposition();
  await directorLoadLatestRun(ED.pid);
  editorToast(failures.length ? 'AI 修改部分完成，请检查未应用节点' : 'AI 修改完成');
}

// ---------------------------------------------------------------------------
// 统一命令入口（editorCmd / editorBatchCmd / editorApplyDuration / 提示式操作）
// ---------------------------------------------------------------------------
async function editorCmd(cmd, extra, targetOverride, useRawTarget){
  if (!ED.pid) { editorToast('先打开项目', true); return; }
  let target = targetOverride !== undefined ? targetOverride
    : (SEL.selection ? SEL.selection.id : '');
  if (target === '' && !useRawTarget && cmd !== 'noop') { editorToast('先在时间线选中场景或图层', true); return; }
  if (cmd === 'split' && target && target.includes('#')) target = target.split('#')[0];
  const payload = extra || {};
  const r = await apiPost('/api/editor/command', {project_id: ED.pid, variantId: ED.pid,
    command: cmd, targetId: target || '', payload, expectedVersion: ED.version});
  if (r.ok) {
    ED.version = r.composition_version;
    await refreshComposition();
    editorToast('已完成' + (DEV.mode ? `（版本 ${r.revision_id}）` : ''));
  } else {
    const err = (r.errors && r.errors[0]) || {};
    editorToast(`未完成：${err.message || r.error || ''}`, true);
  }
}
async function editorBatchCmd(subcommand){
  if (!ED.pid) { editorToast('先打开项目', true); return; }
  let targets = [];
  if (subcommand === 'lock') {
    targets = [...SEL.selectionSet].filter(k => k.startsWith('layer:')).map(k => k.slice(6));
    if (!targets.length) { editorToast('批量锁定需选中图层', true); return; }
  } else {
    targets = ED.comp.scenes.filter(s => edIsSelected('scene', s.id)).map(s => s.id);
    if (!targets.length) { editorToast('批量' + (subcommand==='delete'?'删除':'复制') + '需选中场景', true); return; }
  }
  const r = await apiPost('/api/editor/command', {
    project_id: ED.pid, command: 'batch',
    targetId: targets[0],
    payload: {subcommand, targets, locked: true},
    expectedVersion: ED.version,
  });
  if (r.ok) {
    clearSelection('batch');
    await refreshComposition();
    editorToast(`批量${subcommand} 完成`);
  } else {
    const err = (r.errors && r.errors[0]) || {};
    editorToast(`批量${subcommand} 未完成：${err.message || r.error || ''}`, true);
  }
}
function editorApplyDuration(sceneId){
  const v = parseFloat((document.getElementById('ed-dur')||{}).value);
  if (!(v > 0.3)) { editorToast('时长需不小于 0.3 秒', true); return; }
  editorCmd('set_duration', {duration: v}, sceneId);
}
function editorMovePrompt(){
  const hostId = 'ed-move-host';
  _ensureInlineHost(hostId);
  uiInlineInput(hostId, {type: 'number', def: 1, min: 1, max: 99, step: 1, unit: '第几位'},
    (v) => { const target = SEL.selection && SEL.selection.type === 'scene' ? SEL.selection.id : undefined;
             if (v >= 1) editorCmd('move', {toIndex: Math.round(v) - 1}, target); });
}
function editorAddBrollPrompt(){
  if (!SEL.selection || SEL.selection.type !== 'scene') { editorToast('先在时间线选中一个场景', true); return; }
  editorOpenAssetPicker(SEL.selection.id, 0, 'addbroll');
}

// ---- Motion/Shot Picker 快捷入口（Inspector 与 Library 共用）----
async function editorOpenMotionPicker(sceneId, idx){
  const box = document.getElementById('editor-picker-box');
  if (!box) return;
  box.innerHTML = '加载动效候选……';
  const r = await apiPost('/api/registry/motions/resolve', {layer_type: 'image', semantic_intent: 'enter', duration: 0.5});
  const cands = (r.candidates || []);
  await pickerOpen({
    title: '动效（应用到本层）',
    items: cands.map(c => ({id: c.recipe_id, name: c.name,
      note: c.duration ? `建议 ${c.duration}s` : '', compatibility: c.compatibility,
      missing_requirements: c.missing_requirements})),
    onApply: (mid) => edLibApplyMotionTo(`${sceneId}#${idx}`, mid),
  });
  // pickerOpen 渲染进 ed-lib-list；这里同步渲染到 Inspector picker box
  box.innerHTML = document.getElementById('ed-lib-list').innerHTML;
  window._pickerApply = (id) => edLibApplyMotionTo(`${sceneId}#${idx}`, id);
}
async function edLibApplyMotionTo(layerId, motionId){
  const r = await executeAction('visual.motion', {motionRecipeId: motionId}, 'current_selection');
  if (r && r.ok) editorToast(`动效已应用：${motionCnName(motionId)}`);
}
async function editorOpenShotPicker(sceneId){
  const box = document.getElementById('editor-picker-box');
  if (!box) return;
  const sc = ED.comp.scenes.find(s=>s.id===sceneId);
  const r = await apiPost('/api/registry/shots/resolve', {
    semantic_intents: [], content_type: 'product_video', duration: sc.duration,
    available_asset_types: ['image','product','video'],
    scene_position: ED.comp.scenes[0].id===sceneId ? 'first' : 'middle',
  });
  const cands = (r.candidates || []);
  devCacheShots(cands);
  await pickerOpen({
    title: '镜头方案（换镜头不换素材）',
    items: cands.map(c => ({id: c.recipe_id, name: c.name,
      note: (c.reasons || []).join('；'), compatibility: c.compatibility,
      missing_requirements: c.missing_requirements})),
    onApply: (rid) => editorApplyShot(sceneId, rid),
  });
  box.innerHTML = document.getElementById('ed-lib-list').innerHTML;
  window._pickerApply = (id) => editorApplyShot(sceneId, id);
}
async function editorApplyShot(sceneId, recipeId){
  const r = await executeAction('visual.camera', {recipeId}, 'current_selection');
  if (r && r.ok) editorToast(`镜头已更换：${shotCnName(recipeId)}`);
}

Object.assign(window, {
  LIB_DOMAINS, edLibDomain, edLibSub, edLibNav, edLibLoad,
  pickerOpen, pickerPreviewStage, pickerCancel, pickerApply,
  edLibApplyAsset, edLibApplyMotion, applyTransitionToPair, applyRegistryStyle,
  edLibStockSearch,
  edLibOpenTemplate, instantiateTemplate, AP,
  directorRefreshContext, directorLoadCapCount, directorRenderPlan, directorLoadRun, directorLoadLatestRun, directorExecute,
  editorCmd, editorBatchCmd, editorApplyDuration, editorMovePrompt, editorAddBrollPrompt,
  editorOpenMotionPicker, editorOpenShotPicker, editorApplyShot, edLibApplyMotionTo,
});
