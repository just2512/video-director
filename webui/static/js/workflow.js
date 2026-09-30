// §6 旧 Revision 链已收口：不再维护 currentProject.sb/edits 第二编辑态。
// compose 后 pending_confirm → 直接打开工作台（项目 composition 已就绪），
// 分镜审阅改稿统一走工作台 AI 指令栏/Inspector（proposal → executeCommand → composition）。
// 渲染出口与工作台 editorRenderFinal 统一（都走 /api/confirm，无 edits 重规划）。

// 平台 → 画幅联动（§十一 CreativeBrief：选平台自动定画幅）
function platformAspectSync(){
  const p = document.getElementById('target_platform').value;
  const map = { douyin: '1080x1920', bilibili: '1920x1080', xhs: '1080x1440', square: '1080x1080' };
  if (p && map[p]) document.getElementById('aspect').value = map[p];
}

async function compose(){
  const btn = document.getElementById('go');
  const out = document.getElementById('result');
  const fd = new FormData();
  // CreativeBrief 汇聚（§十一：平台/时长/受众并入创作简报，随 compose 下传）
  let brief = document.getElementById('creative_brief').value;
  const briefParts = [];
  const tp = document.getElementById('target_platform');
  if (tp.value) briefParts.push('目标平台：' + tp.options[tp.selectedIndex].text);
  const td = document.getElementById('target_duration');
  if (td.value) briefParts.push(`目标时长：约 ${td.value} 秒`);
  const au = document.getElementById('audience');
  if (au.value.trim()) briefParts.push(`受众：${au.value.trim()}`);
  if (briefParts.length) {
    brief = (brief ? brief + '\n' : '') + '【创作简报】' + briefParts.join('；');
  }
  fd.append('script', document.getElementById('script').value);
  fd.append('creative_brief', brief);
  fd.append('mode', document.getElementById('mode').value);
  fd.append('voice', document.getElementById('voice').value);
  fd.append('voice_rate', document.getElementById('voice_rate').value);
  fd.append('style_intent', document.getElementById('style_intent').value);
  fd.append('aspect', document.getElementById('aspect').value);
  fd.append('bgm', document.getElementById('bgm').value);
  fd.append('sfx', document.getElementById('sfx').value);
  fd.append('subtitle_mode', document.getElementById('subtitle_mode').value);
  fd.append('watermark', document.getElementById('watermark').value);
  fd.append('title', document.getElementById('title').value);
  fd.append('lower_third_name', document.getElementById('lower_third_name').value);
  fd.append('lower_third_role', document.getElementById('lower_third_role').value);
  fd.append('confirm_mode', document.getElementById('confirm_mode').value);
  for (const f of document.getElementById('videos').files) fd.append('videos', f, f.name);
  for (const f of document.getElementById('images').files) fd.append('images', f, f.name);

  btn.disabled = true;
  out.innerHTML = '<div class="card">正在生成……（配音+匹配+渲染，约 30-120 秒）</div>';
  const ctrl = new AbortController();
  const tm = setTimeout(() => ctrl.abort(), 300000);  // 5 分钟超时兜底
  try {
    const r = await fetch('/api/compose', { method:'POST', body: fd, signal: ctrl.signal });
    const j = await r.json();
    if (!j.ok && j.error) throw new Error(j.error);
    renderResult(j);
  } catch (e) {
    out.innerHTML = `<div class="err-box">生成失败：${esc(e.message)}</div>`;
  } finally {
    clearTimeout(tm);
    btn.disabled = false;
  }
}

function renderResult(j){
  const out = document.getElementById('result');
  if (j.pending_confirm) {
    // §6/§13.3 收口：项目已创建（composition 已就绪），直接打开工作台。
    // 分镜审阅/改稿/渲染统一在工作台（选中场景→Inspector，或 AI 指令栏一句话）。
    out.innerHTML = `
      <div class="ok-box" data-pid="${esc(j.project_id || '')}">
        <b>方案已生成，正在打开工作台……</b>
        <div class="hint">分镜审阅、改稿、渲染都在工作台完成——选中场景改属性，或用 AI 指令栏一句话修改。</div>
      </div>`;
    reviewGoTimeline(j.project_id);
    return;
  }
  const QC_CN = {PASS:'通过', FAIL:'未通过', SKIP:'跳过'};
  const qcRows = (j.qc?.checks||[]).map(c =>
    `<div class="qc-line ${c.result==='PASS'?'qc-pass':'qc-fail'}">${QC_CN[c.result]||esc(c.result)} · ${esc(c.check)} — ${esc(c.detail)}</div>`).join('');
  const videoUrl = '/file?path=' + encodeURIComponent(j.final_path);
  const fbRow = j.case_id ? `
      <div class="feedback-row" id="fb-row">
        <button class="btn fb-btn fb-btn-liked" onclick="sendFeedback('${esc(j.case_id)}', true)">👍 喜欢</button>
        <button class="btn fb-btn fb-btn-disliked" onclick="sendFeedback('${esc(j.case_id)}', false)">👎 不喜欢</button>
        <span class="fb-note"><input type="text" id="fb-note" placeholder="一句话说明（可选），如：节奏再快一点"></span>
      </div>` : '';
  out.innerHTML = `
    <div class="ok-box">
      <b>生成成功</b> · 耗时 ${j.elapsed_s}s · 旁白 ${j.narration_duration.toFixed(1)}s · 质检结果：${j.qc?.verdict === 'PASS' ? '✓ 通过' : (j.qc?.verdict === 'SKIP' ? '（本模式不质检）' : esc(String(j.qc?.verdict || '—')))}
      <video controls src="${videoUrl}"></video><br>
      <a class="dl" href="${videoUrl}" download="final.mp4">⬇ 下载 MP4</a>
      <div style="margin-top:12px">${qcRows}</div>
      ${j.warnings?.length ? `<div class="hint">提示：${esc(j.warnings.join('；'))}</div>` : ''}
      ${fbRow}
    </div>`;
}

// ===========================================================================
// §三十三 四页：Review / Publish / Automation / Settings
// ===========================================================================
async function loadReview(){
  const box = document.getElementById('review-list');
  box.innerHTML = '加载中……';
  let projs;
  try { projs = await apiGet('/api/projects'); } catch(e) { projs = null; }
  if (!Array.isArray(projs)) { box.innerHTML = '<div class="hint">项目列表加载失败，点刷新重试</div>'; return; }
  const rows = [];
  for (const p of projs) {
    const stage = p.stage || '';
    const done = p.has_final;
    const label = stage === 'AWAITING_CONFIRM' ? '🟡 待确认分镜' : stage === 'AWAITING_DRAFT' ? '🟠 待确认草稿' : (done ? '✅ 已完成' : '🔵 生成中/待处理');
    let issuesHtml = '';
    try {
      const pf = await apiGet('/api/preflight?project_id=' + encodeURIComponent(p.project_id));
      const issues = (pf && pf.issues) || [];
      if (issues.length) {
        issuesHtml = '<div class="hint" style="margin:2px 0 2px 12px">' +
          issues.map(i => {
            const block = i.recoverable === false;
            return `<div class="hint" style="margin:2px 0">${block ? '⛔' : '⚠'} ${esc(i.code || '')} — ${esc(i.message || i.detail || '')}
              <button class="btn-secondary btn-sm" onclick="reviewGoTimeline('${esc(p.project_id)}')">去时间线</button>
              <button class="btn-secondary btn-sm" onclick="reviewAskAI('${esc(p.project_id)}','${esc((i.message || i.detail || '').slice(0,40))}')">让 AI 修复</button></div>`;
          }).join('') + '</div>';
      }
    } catch(e) {}
    rows.push(`<div class="proj-row"><span><b>${esc(p.project_id)}</b> ${label}</span>`+
      `<button class="btn-sm" onclick="reviewGoTimeline('${esc(p.project_id)}')">打开确认</button></div>` + issuesHtml);
  }
  box.innerHTML = rows.join('') || '<div class="hint">没有待审核项目（去创作页开始一个）</div>';
}

function reviewGoTimeline(pid){
  switchTab('editor');
  setTimeout(async () => {
    await editorInit();
    const sel = document.getElementById('editor-project');
    if (sel) {
      let found = false;
      for (let i = 0; i < sel.options.length; i++) {
        if (sel.options[i].value === pid) { sel.selectedIndex = i; found = true; break; }
      }
      if (found) editorLoad();
    }
  }, 500);
}

async function reviewAskAI(pid, issue){
  switchTab('editor');
  setTimeout(async () => {
    await editorInit();
    const sel = document.getElementById('editor-project');
    if (sel) {
      for (let i = 0; i < sel.options.length; i++) {
        if (sel.options[i].value === pid) { sel.selectedIndex = i; break; }
      }
      editorLoad();
    }
    // AI 指令栏（Director）承载自然语言修复（§11 Command Bar）
    const inp = document.getElementById('director-input');
    if (inp) inp.value = '修复以下问题：' + issue;
  }, 500);
}

async function loadPublish(){
  const box = document.getElementById('publish-list');
  box.innerHTML = '加载中……';
  let projs;
  try { projs = await apiGet('/api/projects'); } catch(e) { projs = null; }
  if (!Array.isArray(projs)) { box.innerHTML = '<div class="hint">项目列表加载失败，点刷新重试</div>'; return; }
  const rows = [];
  for (const p of projs) {
    let pf;
    try { pf = await apiGet('/api/preflight?project_id=' + encodeURIComponent(p.project_id)); } catch(e) {}
    const status = pf && pf.status ? pf.status : '—';
    const blocked = (pf && pf.issues || []).filter(i=>!i.recoverable).length;
    rows.push(`<div class="proj-row"><span><b>${esc(p.project_id)}</b>
      <span class="hint">${p.has_final ? '✓ 有成品' : '未成片'} · 预检 <b>${status}</b>${blocked ? ' · '+blocked+' 阻断' : ''}</span></span>`+
      `<button class="btn-sm" onclick="publishDetail('${esc(p.project_id)}')">查看</button></div>`);
  }
  box.innerHTML = rows.join('') || '<div class="hint">暂无项目</div>';
}

async function publishDetail(pid){
  const box = document.getElementById('publish-detail-box');
  box.innerHTML = '预检中……';
  const pf = await apiGet('/api/preflight?project_id=' + encodeURIComponent(pid));
  const row = (i) => {
    const block = i.recoverable === false && i.code && !i.code.startsWith('ASSET_');
    return `<div class="${block ? 'err-box' : 'hint'}" style="margin:4px 0">
      ${block ? '⛔' : '⚠'} ${esc(i.code||'')} — ${esc(i.message||i.detail||'')}`+
      (i.code && i.code.startsWith('ASSET_') ? ` <button class="btn-sm" onclick="locateAsset('${esc(i.targetId||'')}')">定位素材</button>` : '') +
      `</div>`;
  };
  box.innerHTML = `<div class="card"><b>发布预检：${esc(pid)}</b>
    <div class="hint">状态：<b>${pf.status}</b>${pf.status==='PASS' ? '（可发布）' : '（有阻断，不能发布）'}</div>
    ${(pf.issues||[]).map(row).join('')}</div>`;
}

function locateAsset(assetId){
  // E2E-5 商业授权拦截 → 定位素材（跳素材库并高亮该素材行）
  switchTab('assets');
  setTimeout(()=>{
    const items = document.querySelectorAll('#assets-list .proj-row');
    for (const el of items) {
      if (el.textContent.includes(assetId)) {
        el.scrollIntoView({behavior:'smooth', block:'center'});
        el.style.outline = '2px solid #e0533d';
        el.style.outlineOffset = '2px';
        break;
      }
    }
  }, 600);
}

async function loadAutomation(){
  // §四十二 Automation：队列已并入本页（#queue-list），直接加载队列
  await loadQueue();
}

async function loadSettings(){
  const box = document.getElementById('settings-env');
  box.innerHTML = '自检中……';
  try {
    const r = await apiGet('/api/doctor');
    const cn = {PASS:'✅', FAIL:'⛔', SKIP:'🔶'};
    box.innerHTML = (Array.isArray(r) ? r : []).map(c =>
      `<div class="hint">${cn[c.status]||'·'} ${esc(c.name)}：<b>${esc(c.status)}</b> — ${esc(c.detail)}</div>`).join('')
      || '<div class="hint">无自检结果</div>';
  } catch(e) { box.innerHTML = '<div class="err-box">自检失败：' + esc(String(e)) + '</div>'; }
}

// §8 拆分导出：inline onclick/window 引用兼容
Object.assign(window, {
  platformAspectSync,
  compose,
  renderResult,
  loadReview,
  reviewGoTimeline,
  reviewAskAI,
  loadPublish,
  publishDetail,
  locateAsset,
  loadAutomation,
  loadSettings,
});