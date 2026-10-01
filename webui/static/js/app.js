
let BGMLIST = [];
function _rebuildSelect(id, opts){
  // 重建下拉但保持当前选择（重建后原 value 仍存在则不丢用户选择）
  const sel = document.getElementById(id);
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML = opts.map(([v,l])=>`<option value="${v}">${l}</option>`).join('');
  if (prev !== undefined && prev !== null) {
    for (let i=0;i<sel.options.length;i++) {
      if (sel.options[i].value === prev) { sel.selectedIndex = i; return; }
    }
  }
}

// ===========================================================================
// App Shell（§3/§49）：四区路由（项目/工作台/审阅/设置）。
// 旧假一级页面别名（create/projects/series/capture/memory/templates/motions/
// assets/reference/publish/automation）全部删除——它们已是四区内的上下文。
// ===========================================================================
function switchTab(tab){
  window.UI.route = tab;
  for (const p of ['home','editor','review','settings']) {
    const el = document.getElementById('pane-' + p);
    if (el) el.style.display = (tab === p) ? '' : 'none';
    const tb = document.getElementById('tab-' + p);
    if (tb) tb.classList.toggle('tab-active', tab === p);
  }
  if (tab === 'home') { loadProjects(); loadSeries(); loadInbox(); }
  if (tab === 'editor') { editorInit(); }
  if (tab === 'review') { loadReview(); loadPublish(); }
  if (tab === 'settings') loadSettings();
}

// §27 收集箱抽屉（顶栏「收集」→ 浮层，不占页面主体）
function captureDrawerToggle(){
  const d = document.getElementById('capture-drawer');
  if (d) d.style.display = d.style.display === 'none' ? '' : 'none';
  if (d && d.style.display !== 'none') loadInbox();
}

function esc(s){ return String(s).replace(/&/g,'&').replace(/</g,'<').replace(/>/g,'>'); }

// ===========================================================================
// 设置页（§28）：模型 / 配音来源 / 环境自检 / 开发者模式
// ===========================================================================
let MODEL_ROLES = [];
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
    window.MODEL_ROLES = await apiGet('/api/model_config');
    if (Array.isArray(MODEL_ROLES) && document.getElementById('llm-panel').style.display !== 'none') {
      renderModelRoles();
    }
  } catch (e) { /* 面板未开时静默 */ }
}

function renderModelRoles(){
  const box = document.getElementById('model-roles');
  if (!box || !Array.isArray(MODEL_ROLES)) return;
  box.innerHTML = MODEL_ROLES.map(r => `
    <div class="role-block">
      <div class="role-head">
        <b>${esc(r.label)}</b>
        <span class="${r.enabled ? 'cap-ok' : 'cap-warn'}">${r.enabled ? '已配置' : '未配置（走兜底）'}</span>
      </div>
      <div class="hint">${esc(r.hint)}</div>
      <label>API 密钥</label>
      <input type="password" id="mc-${r.role}-key" placeholder="sk-…（留空=该角色走内置兜底）" value="${r.enabled && r.source === 'file' ? '（已保存）' : ''}">
      <label>API 地址</label>
      <input type="text" id="mc-${r.role}-url" value="${esc(r.base_url || '')}" placeholder="${esc(r.default_base_url)}">
      <label>模型名</label>
      <input type="text" id="mc-${r.role}-model" value="${esc(r.model || '')}" placeholder="${esc(r.default_model)}">
    </div>`).join('');
}

async function saveModelConfig(){
  const status = document.getElementById('llm-save-status');
  status.textContent = '保存中……';
  const configs = {};
  for (const r of MODEL_ROLES) {
    const keyEl = document.getElementById(`mc-${r.role}-key`);
    const urlEl = document.getElementById(`mc-${r.role}-url`);
    const modelEl = document.getElementById(`mc-${r.role}-model`);
    if (!keyEl) continue;
    const keyVal = keyEl.value;
    // 「（已保存）」占位 = 保持原有 key 不变；空值 = 清除该角色
    configs[r.role] = {
      api_key: keyVal === '（已保存）' ? '__KEEP__' : keyVal,
      base_url: urlEl ? urlEl.value : '',
      model: modelEl ? modelEl.value : '',
    };
  }
  const r2 = await apiPost('/api/model_config', {configs});
  if (!r2.ok) { status.textContent = '保存失败：' + (r2.error || '未知'); return; }
  window.MODEL_ROLES = r2.roles || MODEL_ROLES;
  renderModelRoles();
  status.textContent = '已保存并立即生效';
  refreshLLMBadge();
}

function toggleLLMPanel(){
  const p = document.getElementById('llm-panel');
  if (p.style.display === 'none') renderModelRoles();
  p.style.display = p.style.display === 'none' ? 'block' : 'none';
}

// §46 配音来源：voice providers 真实可用性（设置页消费 /api/voice/providers）
async function loadVoiceProviders(){
  const el = document.getElementById('voice-providers-line');
  if (!el) return;
  try {
    const r = await apiGet('/api/voice/providers');
    if (r.error) { el.textContent = '检测失败：' + r.error; return; }
    const provs = (r.providers || []).map(p =>
      `${p.name || p.id}${p.available ? '' : '（不可用）'}`).join('、');
    el.textContent = provs
      ? `当前可用：${provs} · 共 ${r.voices ? r.voices.length : 0} 个音色`
      : '无可用配音服务';
  } catch (e) {
    el.textContent = '检测失败：' + String(e);
  }
}

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
refreshLLMBadge();
loadVoiceProviders();
if (typeof devInit === 'function') devInit();

Object.assign(window, {
  _rebuildSelect,
  refreshLLMBadge,
  renderModelRoles,
  saveModelConfig,
  toggleLLMPanel,
  loadVoiceProviders,
  loadSettings,
  toggleCapPanel,
  switchTab,
  captureDrawerToggle,
  esc,
  BGMLIST,
  MODEL_ROLES,
});
