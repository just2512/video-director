
let BGMLIST = [];
function _rebuildSelect(id, opts){
  // 重建下拉但保持当前选择（重建后原 value 仍存在则不丢用户选择）
  const sel = document.getElementById(id);
  if (!sel) return;
  const prev = sel.value;
  sel.replaceChildren();
  for (const [value, label] of (Array.isArray(opts) ? opts : [])) {
    const option = document.createElement('option');
    option.value = String(value ?? '');
    option.textContent = String(label ?? '');
    sel.appendChild(option);
  }
  if (prev !== undefined && prev !== null) {
    for (let i=0;i<sel.options.length;i++) {
      if (sel.options[i].value === prev) { sel.selectedIndex = i; return; }
    }
  }
}

// ===========================================================================
// 顶层三空间路由（方案§3）：项目 / 工作台 / 设置。
// 「审阅」不再是独立空间——它是工作台的问题模式（reviewModeToggle）。
// ===========================================================================
function switchTab(tab){
  window.UI.route = tab;
  for (const p of ['home','editor','settings']) {
    const active = tab === p;
    const el = document.getElementById('pane-' + p);
    if (el) {
      el.style.display = active ? '' : 'none';
      el.setAttribute('aria-hidden', String(!active));
    }
    const tb = document.getElementById('tab-' + p);
    if (tb) {
      tb.classList.toggle('tab-active', active);
      if (active) tb.setAttribute('aria-current', 'page');
      else tb.removeAttribute('aria-current');
    }
  }
  if (tab === 'home') { loadProjects(); loadSeries(); loadInbox(); }
  if (tab === 'editor') { editorInit(); devShowBuildId(); }
  if (tab === 'settings') { loadSettings(); devShowBuildId(); loadStockStatusLine(); refreshLLMBadge(); }
}

/** §42 build_id 显示（Developer Mode）：源码契约与运行态一致性。 */
async function devShowBuildId(){
  const el = document.getElementById('dev-build-id');
  if (!el) return;
  try {
    const r = await apiGet('/api/actions');
    if (r && r.build_id) el.textContent = '动作注册表：' + r.build_id;
  } catch (e) { el.textContent = '读取失败（/api/actions）'; }
}

// ===========================================================================
// §51/§52 设置页：素材状态行 + 配置导入导出（密钥不导出/导入不覆盖）
// ===========================================================================
async function loadStockStatusLine(){
  const el = document.getElementById('settings-stock-line');
  if (!el) return;
  try {
    const roles = await apiGet('/api/model_config');
    const stock = Array.isArray(roles)
      ? roles.filter(r => r.kind === 'stock' || String(r.role || '').startsWith('stock_'))
      : [];
    STOCK_ROLES = stock;
    window.STOCK_ROLES = STOCK_ROLES;
    const parts = stock.map(r => r.label + (r.enabled ? ' 已配置' : ' 未配置'));
    el.textContent = parts.length ? '图库：' + parts.join(' · ') : '图库：未配置';
    renderStockRoles();
  } catch (e) {
    el.textContent = '图库状态读取失败：' + (e.message || e);
  }
}

async function settingsExport(){
  const st = document.getElementById('settings-config-status');
  try {
    const r = await apiGet('/api/settings/export');
    if (!r.ok) throw new Error(r.error || '导出失败');
    const blob = new Blob([JSON.stringify(r, null, 2)], {type: 'application/json'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'video-director-settings.json';
    a.click();
    URL.revokeObjectURL(a.href);
    if (st) st.textContent = '已导出（密钥不含在内；导入后需重新填 key）';
  } catch (e) {
    if (st) st.textContent = '导出失败：' + (e.message || e);
  }
}

async function settingsImport(input){
  const st = document.getElementById('settings-config-status');
  const file = input.files && input.files[0];
  if (!file) { if (st) st.textContent = '先选择导出文件'; return; }
  try {
    const data = JSON.parse(await file.text());
    const r = await apiPost('/api/settings/import', data);
    if (r.ok) {
      if (st) st.textContent = `已导入 ${r.imported_roles.length} 个角色（密钥保留本机原值）`;
      refreshLLMBadge();
    } else {
      if (st) st.textContent = '导入失败：' + (r.error || '文件格式不对');
    }
  } catch (e) {
    if (st) st.textContent = '导入失败：' + (e.message || e);
  }
  input.value = '';
}

// §27 收集箱抽屉（顶栏「收集」→ 浮层，不占页面主体）
function captureDrawerToggle(){
  const d = document.getElementById('capture-drawer');
  if (d) d.style.display = d.style.display === 'none' ? '' : 'none';
  if (d && d.style.display !== 'none') loadInbox();
}

function esc(s){
  const entities={'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
  return String(s).replace(/[&<>"']/g,character=>entities[character]);
}

// ===========================================================================
// 设置页（§28）：模型 / 配音来源 / 环境自检 / 开发者模式
// ===========================================================================
let MODEL_ROLES = [];
let STOCK_ROLES = [];
let TTS_ROLES = [];
let RENDER_ROLES = [];
async function refreshLLMBadge(){
  // 徽章内联进设置页（§28：不再漂浮在主工作区）
  const cfg = await apiGet('/api/llm_status');
  const el = document.getElementById('llm-badge-inline');
  if (el) {
    el.textContent = cfg.enabled
      ? `已启用（${cfg.model} · ${cfg.source === 'runtime' ? '页面设置' : '环境变量'}）`
      : '未配置（未配置的角色走内置兜底，功能不缺失）';
  }
  try {
    MODEL_ROLES = await apiGet('/api/model_config');
    if (!Array.isArray(MODEL_ROLES)) MODEL_ROLES = [];
    STOCK_ROLES = MODEL_ROLES.filter(r => r.kind === 'stock' || String(r.role || '').startsWith('stock_'));
    TTS_ROLES = MODEL_ROLES.filter(r => r.kind === 'tts' || String(r.role || '').startsWith('tts_'));
    RENDER_ROLES = MODEL_ROLES.filter(r => r.role === 'hyperframes');
    MODEL_ROLES = MODEL_ROLES.filter(r => r.kind !== 'stock' && !String(r.role || '').startsWith('stock_')
                                    && r.kind !== 'tts' && !String(r.role || '').startsWith('tts_')
                                    && r.role !== 'hyperframes');
    window.MODEL_ROLES = MODEL_ROLES;
    window.STOCK_ROLES = STOCK_ROLES;
    window.TTS_ROLES = TTS_ROLES;
    window.RENDER_ROLES = RENDER_ROLES;
    if (document.getElementById('llm-panel')?.style.display !== 'none') renderModelRoles();
    if (document.getElementById('stock-roles')) renderStockRoles();
    if (document.getElementById('tts-roles')) renderTtsRoles();
    if (document.getElementById('render-roles')) renderRenderRoles();
  } catch (e) { /* 面板未开时静默 */ }
}

// 2026-10-26 用户裁决：TTS 供应商自主配置（设置页配音面板）
function renderTtsRoles(){
  const box = document.getElementById('tts-roles');
  if (!box || !Array.isArray(TTS_ROLES)) return;
  box.innerHTML = TTS_ROLES.map(r => `
    <div class="role-block">
      <div class="role-head">
        <b>${esc(r.label)}</b>
        <span class="${r.enabled ? 'cap-ok' : 'cap-warn'}">${r.enabled ? '已配置' : '未配置（不可用）'}</span>
      </div>
      <div class="hint">${esc(r.hint)}</div>
      <label>API Key</label>
      <input type="password" id="mc-${r.role}-key" placeholder="留空=未配置" value="${r.enabled && r.source === 'file' ? '（已保存）' : ''}">
      <label>${r.role === 'tts_azure' ? 'Region（如 eastasia）' : '模型'}</label>
      <input type="text" id="mc-${r.role}-model" value="${esc(r.model || '')}" placeholder="${esc(r.default_model)}">
    </div>`).join('');
}

function renderModelRoles(){
  const box = document.getElementById('model-roles');
  if (!box || !Array.isArray(MODEL_ROLES)) return;
  const roleCard = r => `
    <div class="role-block">
      <div class="role-head">
        <b>${esc(r.label)}</b>
        <span class="${r.enabled ? 'cap-ok' : 'cap-warn'}">${r.enabled ? '已配置' : '未配置（走兜底）'}</span>
      </div>
      <div class="hint">${esc(r.hint)}</div>
      <div class="hint">Provider：<b>${esc(r.provider || '未识别')}</b>${
        r.capability_states
          ? ' · ' + Object.entries(r.capability_states).map(([k,v]) => `${esc(k)}=${esc(v)}`).join(' · ')
          : ''
      }</div>
      <label>密钥（服务方提供的一串字符）</label>
      <input type="password" id="mc-${r.role}-key" placeholder="sk-…（留空=该角色走内置兜底）" value="${r.enabled && r.source === 'file' ? '（已保存）' : ''}">
      <label>服务地址（不填用默认）</label>
      <input type="text" id="mc-${r.role}-url" value="${esc(r.base_url || '')}" placeholder="${esc(r.default_base_url)}">
      <label>模型名</label>
      <input type="text" id="mc-${r.role}-model" value="${esc(r.model || '')}" placeholder="${esc(r.default_model)}">
    </div>`;
  const groups = [
    {title:'内容创作', roles:MODEL_ROLES.filter(r => r.role === 'llm')},
    {title:'画面生成', roles:MODEL_ROLES.filter(r => ['image','video'].includes(r.role))},
    {title:'画面理解', roles:MODEL_ROLES.filter(r => ['vision','semantic_rerank','visual_qa'].includes(r.role))},
    {title:'其他模型角色', roles:MODEL_ROLES.filter(r => !['llm','image','video','vision','semantic_rerank','visual_qa'].includes(r.role))}
  ];
  box.innerHTML = groups.filter(group => group.roles.length).map(group => `
    <section class="settings-role-group" style="margin-top:14px">
      <h3 style="font-size:15px;margin:0 0 8px">${group.title}</h3>
      ${group.roles.map(roleCard).join('')}
    </section>`).join('');
}

function renderRenderRoles(){
  const box = document.getElementById('render-roles');
  if (!box || !Array.isArray(RENDER_ROLES)) return;
  box.innerHTML = RENDER_ROLES.map(r => `
    <div class="role-block">
      <div class="role-head">
        <b>${esc(r.label)}</b>
        <span class="${r.enabled ? 'cap-ok' : 'cap-warn'}">${r.enabled ? '已配置' : '未配置（使用 FFmpeg 兜底）'}</span>
      </div>
      <div class="hint">${esc(r.hint)}</div>
      <label>API Key</label>
      <input type="password" id="mc-${r.role}-key" placeholder="留空=使用内置 FFmpeg"
        value="${r.enabled && r.source === 'file' ? '（已保存）' : ''}">
      <label>服务地址</label>
      <input type="text" id="mc-${r.role}-url" value="${esc(r.base_url || '')}" placeholder="${esc(r.default_base_url)}">
      <label>模型名</label>
      <input type="text" id="mc-${r.role}-model" value="${esc(r.model || '')}" placeholder="${esc(r.default_model)}">
    </div>`).join('');
}

async function saveModelConfig(scope = 'all'){
  const roleSets = { models: MODEL_ROLES, voice: TTS_ROLES, stock: STOCK_ROLES, render: RENDER_ROLES };
  const targetRoles = scope === 'all' ? [...MODEL_ROLES, ...RENDER_ROLES, ...STOCK_ROLES, ...TTS_ROLES] : (roleSets[scope] || []);
  const statusIds = { all: 'llm-save-status', models: 'llm-save-status', voice: 'tts-save-status', stock: 'stock-save-status', render: 'render-save-status' };
  const status = document.getElementById(statusIds[scope] || statusIds.all);
  if (status) status.textContent = '保存中……';
  const configs = {};
  for (const r of targetRoles) {
    const keyEl = document.getElementById(`mc-${r.role}-key`);
    const urlEl = document.getElementById(`mc-${r.role}-url`);
    const modelEl = document.getElementById(`mc-${r.role}-model`);
    if (!keyEl) continue;
    const keyVal = keyEl.value;
    configs[r.role] = {
      api_key: keyVal === '（已保存）' ? '__KEEP__' : keyVal,
      ...(urlEl ? {base_url:urlEl.value} : {}),
      ...(modelEl ? {model:modelEl.value} : {})
    };
  }
  if (!Object.keys(configs).length) { if (status) status.textContent = '没有可保存的配置项'; return; }
  const response = await apiPost('/api/model_config', {configs});
  if (!response.ok) { if (status) status.textContent = '保存失败：' + (response.error || '未知'); return; }
  const allRoles = Array.isArray(response.roles) ? response.roles : [...MODEL_ROLES, ...RENDER_ROLES, ...STOCK_ROLES, ...TTS_ROLES];
  STOCK_ROLES = allRoles.filter(r => r.kind === 'stock' || String(r.role || '').startsWith('stock_'));
  TTS_ROLES = allRoles.filter(r => r.kind === 'tts' || String(r.role || '').startsWith('tts_'));
  RENDER_ROLES = allRoles.filter(r => r.role === 'hyperframes');
  MODEL_ROLES = allRoles.filter(r => r.kind !== 'stock' && !String(r.role || '').startsWith('stock_') && r.kind !== 'tts' && !String(r.role || '').startsWith('tts_') && r.role !== 'hyperframes');
  window.MODEL_ROLES = MODEL_ROLES; window.STOCK_ROLES = STOCK_ROLES; window.TTS_ROLES = TTS_ROLES; window.RENDER_ROLES = RENDER_ROLES;
  renderModelRoles(); renderRenderRoles(); renderStockRoles(); renderTtsRoles();
  if (status) status.textContent = '已保存并立即生效';
  refreshLLMBadge();
}

function renderStockRoles(){
  const box = document.getElementById('stock-roles');
  if (!box) return;
  box.innerHTML = STOCK_ROLES.map(r => `
    <div class="role-block">
      <div class="role-head">
        <b>${esc(r.label)}</b>
        <span class="${r.enabled ? 'cap-ok' : 'cap-warn'}">${r.enabled ? '已配置' : '未配置'}</span>
      </div>
      <div class="hint">${esc(r.hint)}</div>
      <div class="hint">Provider：<b>${esc(r.provider || r.role || '未识别')}</b>${
        r.capability_states
          ? ' · ' + Object.entries(r.capability_states).map(([k,v]) => `${esc(k)}=${esc(v)}`).join(' · ')
          : ''
      }</div>
      <label>API Key</label>
      <input type="password" id="mc-${r.role}-key" placeholder="留空=未配置"
        value="${r.enabled && r.source === 'file' ? '（已保存）' : ''}">
      <label>服务地址（通常无需修改）</label>
      <input type="text" id="mc-${r.role}-url" value="${esc(r.base_url || '')}" placeholder="${esc(r.default_base_url)}">
    </div>`).join('');
}
  
function toggleLLMPanel(){
  const p = document.getElementById('llm-panel');
  if (p.style.display === 'none') renderModelRoles();
  p.style.display = p.style.display === 'none' ? 'block' : 'none';
}

// §46 配音：设置页不再做孤立文字披露（引擎只有 Edge TTS，配置无意义），
// 入口改「去音色库试听」（edLibDomain('sound','voice')，那里有真实试听交互）。

// §28 环境自检（人话 + 需要时怎么办）
async function loadSettings(){
  const box = document.getElementById('settings-env');
  box.innerHTML = '自检中……';
  const r = await apiGet('/api/doctor');
  if (r.error) { box.innerHTML = `<div class="err-box">自检失败：${esc(r.error)}</div>`; return; }
  const cn = {PASS:'正常', FAIL:'缺失', SKIP:'跳过'};
  const cls = {PASS:'cap-ok', FAIL:'cap-off', SKIP:'cap-warn'};
  box.innerHTML = (Array.isArray(r) ? r : []).map(c =>
    `<div class="hint"><b class="${cls[c.status] || ''}">${cn[c.status] || c.status}</b> ${esc(c.name)} — ${esc(c.detail)}</div>`).join('')
    || '<div class="hint">无自检结果</div>';
}

function toggleCapPanel(){
  const el = document.getElementById('cap-table');
  el.innerHTML = '检测中……';
  apiGet('/api/capabilities').then(caps => {
    if (!Array.isArray(caps)) throw new Error(caps.error || '读取失败');
    const NAME_CN = {tts:'配音', vision:'视觉理解', alignment:'词级对齐',
                    renderer:'渲染器', audio:'音频库', matting:'抠像'};
    const cls = {PASS:'cap-ok', AVAILABLE:'cap-ok', DEGRADED:'cap-warn', MISSING:'cap-off'};
    const label = {PASS:'正常', AVAILABLE:'可用', DEGRADED:'受限', MISSING:'缺失'};
    el.innerHTML = `<table class="cap-table"><tr><td>能力</td><td>状态</td><td>提供者</td></tr>`+
      caps.map(c=>`<tr><td>${NAME_CN[c.name]||c.name}</td>`+
        `<td class="${cls[c.status]||''}">${label[c.status]||esc(c.status)}</td>`+
        `<td>${(c.providers||[]).filter(p=>p.available).length} 项可用</td></tr>`).join('')+
      `</table><div class="hint">以上为当前电脑实际可用能力；受限项不影响其他功能。</div>`;
  }).catch(e => {
    el.innerHTML = `<div class="err-box">读取失败：${esc(e.message)}</div>`;
  });
}

// ===========================================================================
// 启动：首屏路由（项目页）。Developer Mode 恢复 + 名称映射预热。
// ===========================================================================
switchTab('home');
// Model roles are loaded when the settings pane is opened. A second startup
// fetch races that route fetch and can re-render the form over unsaved edits.
if (typeof devInit === 'function') devInit();

Object.assign(window, {
  _rebuildSelect,
  refreshLLMBadge,
  renderModelRoles,
  saveModelConfig,
  toggleLLMPanel,
  loadSettings,
  renderTtsRoles,
  toggleCapPanel,
  devShowBuildId, loadStockStatusLine, settingsExport, settingsImport,
  switchTab,
  captureDrawerToggle,
  esc,
  BGMLIST,
  MODEL_ROLES,
  STOCK_ROLES,
});
