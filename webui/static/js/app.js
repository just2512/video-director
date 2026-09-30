
let BGMLIST = [];
function _rebuildSelect(id, opts){
  // 重建下拉但保持当前选择（重建后原 value 仍存在则不丢用户选择）
  const sel = document.getElementById(id);
  const prev = sel ? sel.value : '';
  sel.innerHTML = opts.map(([v,l])=>`<option value="${v}">${l}</option>`).join('');
  if (prev !== undefined && prev !== null) {
    for (let i=0;i<sel.options.length;i++) {
      if (sel.options[i].value === prev) { sel.selectedIndex = i; return; }
    }
  }
}
async function loadOptions(){
  const voices = await apiGet('/api/voices');
  _rebuildSelect('voice', voices.map(v=>[v.id, v.label]));
  const aspects = await apiGet('/api/aspects');
  _rebuildSelect('aspect', aspects.map(a=>[a.value, a.label]));
  window.BGMLIST = await apiGet('/api/bgm');
  _rebuildSelect('bgm', [['auto','🤖 AI 自动匹配（按视频风格选曲）'],['','（不配乐）'],
                         ...BGMLIST.map(b=>[b.id, b.label])]);
  const sfx = await apiGet('/api/sfx');
  _rebuildSelect('sfx', [['auto','自动（按镜头动作配音效，推荐）'],['','关闭（不加音效）'],
                         ...sfx.map(s=>[s.id, s.label])]);
  // §8 Registry 产品化：风格选择器（style registry → 预设 chips，点击填入）
  try {
    const styles = await apiGet('/api/registry/styles');
    if (Array.isArray(styles) && styles.length) {
      document.getElementById('style-presets').innerHTML =
        `<span>风格：</span>` + styles.map(s =>
          `<button class="btn-secondary btn-sm" onclick="stylePresetPick('${esc(s.name)}','${esc((s.description||'').slice(0,20))}')" title="${esc(s.description||'')}">${esc(s.name)}</button>`).join('');
    }
  } catch (e) { document.getElementById('style-presets').innerHTML = ''; }
  await refreshLLMBadge();
  // 能力徽章摘要（不展开面板，只数状态）
  try {
    const caps = await apiGet('/api/capabilities');
    if (Array.isArray(caps)) {
      const ok = caps.filter(c=>c.status==='PASS'||c.status==='AVAILABLE').length;
      const warn = caps.filter(c=>c.status==='DEGRADED').length;
      const el = document.getElementById('cap-badge');
      el.textContent = `🔌 功能就绪：${ok} 项${warn?` · ${warn} 项可用但受限`:''}`;
    }
  } catch (e) { /* 能力徽章失败不阻塞主界面 */ }
}

let MODEL_ROLES = [];
async function refreshLLMBadge(){
  // 徽章：文本模型状态（主角色），面板打开时展示四角色
  const cfg = await apiGet('/api/llm_status');
  const el = document.getElementById('llm-badge');
  if (cfg.enabled) {
    el.textContent = `🤖 大模型已启用（${cfg.model} · ${cfg.source === 'runtime' ? '页面设置' : '环境变量'}）`;
    el.classList.add('on');
  } else {
    el.textContent = '🤖 大模型配置（点击配置各角色，未配置走规则兜底）';
    el.classList.remove('on');
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
  if (!Array.isArray(MODEL_ROLES)) return;
  box.innerHTML = MODEL_ROLES.map(r => `
    <div class="role-block">
      <div class="role-head">
        <b>${esc(r.label)}</b>
        <span class="${r.enabled ? 'cap-ok' : 'cap-warn'}">${r.enabled ? '✓ 已配置' : '未配置（走兜底）'}</span>
      </div>
      <div class="hint">${esc(r.hint)}</div>
      <label>API 密钥</label>
      <input type="password" id="mc-${r.role}-key" placeholder="sk-…（留空=该角色走规则/内置兜底）" value="${r.enabled && r.source === 'file' ? '（已保存）' : ''}">
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
  try {
    const r2 = await fetch('/api/model_config', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({configs})
    });
    const j = await r2.json();
    if (!j.ok) throw new Error(j.error || '保存失败');
    window.MODEL_ROLES = j.roles || MODEL_ROLES;
    renderModelRoles();
    status.textContent = '✅ 已保存并立即生效';
    await refreshLLMBadge();
  } catch (e) { status.textContent = '保存失败：' + e.message; }
}

function toggleLLMPanel(){
  const p = document.getElementById('llm-panel');
  if (p.style.display === 'none') renderModelRoles();
  p.style.display = p.style.display === 'none' ? 'block' : 'none';
}

function toggleCapPanel(){
  const p = document.getElementById('cap-panel');
  if (p.style.display === 'none') loadCapabilities();
  p.style.display = p.style.display === 'none' ? 'block' : 'none';
}

async function loadCapabilities(){
  const el = document.getElementById('cap-table');
  el.innerHTML = '检测中……';
  try {
    const caps = await apiGet('/api/capabilities');
    if (!Array.isArray(caps)) throw new Error(caps.error || '读取失败');
    const NAME_CN = {tts:'配音TTS', vision:'视觉理解', alignment:'词级对齐',
                     renderer:'渲染器', audio:'音频库', matting:'抠像'};
    const cls = {PASS:'cap-ok', AVAILABLE:'cap-ok', DEGRADED:'cap-warn', MISSING:'cap-off'};
    const label = {PASS:'✓ 正常', AVAILABLE:'✓ 可用', DEGRADED:'⚠ 降级', MISSING:'✗ 缺失'};
    const PROV_CN = {
      'FFmpeg':'视频引擎（随包内置）', 'Edge':'微软 Edge 语音', 'EdgeWordBoundary':'Edge 词级时间戳', 'FasterWhisper':'本地语音识别',
      'FrameVision':'视觉大模型', 'Manual':'人工标注', 'LocalLibrary':'本地音频库',
      'SelfGenerated':'自产音频合成', 'ChromaKey':'绿幕抠像', 'Rembg':'人像抠像模型'};
    const rows = caps.map(c=>{
      // 用户口径：只显示真实可用的提供者。未接线的参考实现/未安装可选增强
      // 对用户是噪音（不可用=不可用，显示出来只会引发"要不要装"的疑问）。
      const provs = (c.providers||[]).filter(p=>p.available)
        .map(p=>`<span class="cap-ok"${p.note ? ` title="${esc(p.note)}"` : ''}>${esc(PROV_CN[p.name] || p.name)}</span>`)
        .join('、');
      return `<tr>
      <td>${NAME_CN[c.name]||c.name}</td>
      <td class="${cls[c.status]||''}">${label[c.status]||esc(c.status)}</td>
      <td>${provs||'—'}</td>
      <td>${(c.fallback_tools||[]).join('、')||'—'}</td></tr>`;}).join('');
    el.innerHTML = `<table class="cap-table"><tr><td>能力</td><td>状态</td><td>提供者（悬停看说明）</td><td>降级链</td></tr>${rows}</table>
      <div class="hint" style="margin-top:8px">以上均为当前电脑实际可用的能力。视觉理解/大模型在「大模型配置」里配置后自动增强（不配全部可用，走内置兜底）。</div>`;
  } catch (e) {
    el.innerHTML = `<div class="err-box">读取失败：${esc(e.message)}</div>`;
  }
}

async function enhanceScript(){
  const btn = document.getElementById('enhance-btn');
  const box = document.getElementById('enhance-result');
  const script = document.getElementById('script').value;
  if (!script.trim()) { editorToast('请先粘贴文案', true); return; }
  btn.disabled = true; btn.textContent = '润色中……';
  try {
    const r = await fetch('/api/enhance', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({script})
    });
    const j = await r.json();
    if (!j.ok && j.error) throw new Error(j.error);
    box.style.display = 'block';
    document.getElementById('enhanced-text').value = j.enhanced || script;
    if (!j.changed) {
      box.querySelector('.hint').textContent = j.llm_enabled
        ? 'AI 认为原文已足够好，未改动（可直接采用或放弃）'
        : 'AI 大模型未启用：原文原样返回（配置后可润色）';
    } else {
      box.querySelector('.hint').textContent = 'AI 润色结果（对比后采用，原文保留）：';
    }
  } catch (e) {
    editorToast('润色失败：' + e.message, true);
  } finally {
    btn.disabled = false; btn.textContent = '✨ AI 润色文案';
  }
}

function acceptEnhanced(){
  document.getElementById('script').value = document.getElementById('enhanced-text').value;
  discardEnhanced();
}
function discardEnhanced(){
  document.getElementById('enhance-result').style.display = 'none';
}

function switchTab(tab){
  const TAB_ALIAS = {create:'home', projects:'home', series:'home', capture:'home', memory:'home', templates:'editor', motions:'editor', assets:'editor', reference:'editor', publish:'review', automation:'review'};
  tab = TAB_ALIAS[tab] || tab;
  for (const p of ['home','editor','review','settings']) {
    const el = document.getElementById('pane-' + p);
    if (el) el.style.display = (tab === p) ? '' : 'none';
    const tb = document.getElementById('tab-' + p);
    if (tb) tb.classList.toggle('tab-active', tab === p);
  }
  if (tab === 'home') { loadHome(); loadProjects(); loadSeries(); loadInbox(); loadMemoryView(); }
  if (tab === 'editor') { editorInit(); }
  if (tab === 'review') { loadReview(); loadPublish(); loadAutomation(); }
  if (tab === 'settings') loadSettings();
}

// ===========================================================================
// 编辑器（Editor：Timeline + Inspector + AI 提案）——Shotcraft EDT 集成
function esc(s){ return String(s).replace(/&/g,'&').replace(/</g,'<').replace(/>/g,'>'); }

// §8 Registry 产品化：风格预设点击 → 填入 style_intent（名称 + 描述语义）
function stylePresetPick(name, desc){
  const el = document.getElementById('style_intent');
  if (!el) return;
  el.value = name + (desc ? '（' + desc + '）' : '');
}

// §8 入口：模块加载完成即初始化（等价旧 <script> 底部的直接调用）
loadOptions();
loadHome();  // 首页默认 tab
if (typeof devInit === 'function') devInit();  // §10 Developer Mode 状态恢复 + 名称映射预热

// §8 拆分导出：inline onclick/window 引用兼容（零行为改动）
Object.assign(window, {
  _rebuildSelect,
  loadOptions,
  refreshLLMBadge,
  stylePresetPick,
  renderModelRoles,
  saveModelConfig,
  toggleLLMPanel,
  toggleCapPanel,
  loadCapabilities,
  enhanceScript,
  acceptEnhanced,
  discardEnhanced,
  switchTab,
  esc,
  BGMLIST,
  MODEL_ROLES,
});
