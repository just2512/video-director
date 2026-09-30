// 当前待确认项目的状态（自然语言改稿累积 edits）
let currentProject = { pid: '', sb: null, edits: {} };

function renderStoryboard(sb){
  if (!sb || !sb.scenes || !sb.scenes.length) return '<div class="hint">（无场景）</div>';
  const cards = sb.scenes.map((sc, i) => {
    // 展示该场景全部素材（多镜头全列出——用户能看到 8 个素材都进片）
    const aids = (sc.assets_used || []).map(a => a.asset_id).filter(Boolean);
    const aidText = aids.length ? aids.join('、') : '未配素材';
    const n = sb.scenes.length;
    // 语义角色（§十二：HOOK/intro/body/outro 按位置推断）
    const role = i === 0 ? 'HOOK' : (i === n - 1 ? 'OUTRO' : 'BODY');
    const moveBtns = (n > 1) ? `
        <button class="scene-move" onclick="moveScene(${i}, -1)" ${i===0?'disabled':''}>↑</button>
        <button class="scene-move" onclick="moveScene(${i}, 1)" ${i===n-1?'disabled':''}>↓</button>` : '';
    return `<div class="scene-card">
      <div class="scene-head">
        <span class="scene-id">${esc(sc.scene_id||'')}</span>
        <span class="scene-role">${role}</span>
        <span class="scene-dur">${(sc.duration||0).toFixed(1)}s</span>
        ${moveBtns}
        <button class="scene-del" onclick="removeSceneDirect('${esc(sc.scene_id||'')}')">删除</button>
      </div>
      <div class="scene-text">${esc(sc.text||'')}</div>
      <div class="scene-asset">素材（${aids.length}个）：${esc(aidText)}</div>
      <div class="btn-row sb-actions">
        <button class="btn-secondary btn-sm" onclick="sbChangeShot(${i})">🎬 换一个镜头</button>
        <button class="btn-secondary btn-sm" onclick="sbChangeVisual(${i})">🖼 换视觉</button>
        <button class="btn-secondary btn-sm" onclick="sbGenAsset(${i})">✨ 生成素材</button>
        <button class="btn-secondary btn-sm" onclick="sbAdjust(${i})">⚙ 调整</button>
      </div>
      <div id="sb-card-${i}-inline"></div>
    </div>`;
  }).join('');
  return `<div class="scene-list">${cards}</div>`;
}
// 分镜卡四按钮（§十二）：换镜头=resolver 候选；换视觉=素材重绑；生成素材=生图引导；调整=改稿
// §9.3/FE-007：全部 prompt/alert → 卡内联 UI / Toast（分镜卡尾部原位展开）
function _sbInlineRow(i, inner, onApply, applyLabel){
  const host = document.getElementById(`sb-card-${i}-inline`);
  if (!host) { if (typeof editorToast === 'function') editorToast('界面元素缺失，请刷新页面'); return; }
  host.innerHTML = '';
  const row = document.createElement('div');
  row.className = 'btn-row';
  row.style.cssText = 'margin-top:6px;flex-wrap:wrap;align-items:center;gap:6px';
  row.innerHTML = inner + `<button class="btn-sm">${esc(applyLabel || '应用')}</button>`;
  host.appendChild(row);
  row.querySelector('button').onclick = () => {
    const v = row.querySelector('select, input');
    host.innerHTML = '';
    onApply(v ? v.value : '');
  };
}
async function sbChangeShot(i){
  const r = await apiPost('/api/registry/shots/resolve', {semantic_intents: [], content_type: 'product_video', duration: 4, available_asset_types: ['image','product','video'], scene_position: i===0?'first':'middle'});
  const cands = (r.candidates||[]).filter(c=>c.compatibility!=='INCOMPATIBLE');
  if (!cands.length) { editorToast('暂无匹配的镜头方案', true); return; }
  const top5 = cands.slice(0, 5);
  const opts = top5.map((c, ci) => `<option value="${ci}">${ci + 1}. ${esc(c.name)}（${esc(c.recipe_id)}）</option>`).join('');
  _sbInlineRow(i, `<select style="flex:2">${opts}</select>`, (v) => {
    const chosen = top5[parseInt(v)];
    if (!chosen) { editorToast('编号无效', true); return; }
    document.getElementById('creative_brief').value += `\n[分镜${i+1} 使用镜头方案：${chosen.name}（${chosen.recipe_id}）]`;
    editorToast(`✅ 镜头方案已记入创作要求（渲染时生效）：${chosen.name}`);
  }, '记入要求');
}
function sbChangeVisual(i){
  _sbInlineRow(i,
    `<input type="text" placeholder="素材文件名（如 3.mp4），或"自动"让 AI 重新匹配" style="flex:2">`,
    (t) => {
      if (!t) return;
      const nums = ['一','二','三','四','五','六','七','八','九','十'];
      document.getElementById('rev-input').value = `第${nums[i]||(i+1)}段换成 ${t}`;
      applyRevision();
    });
}
async function sbGenAsset(i){
  // 分镜卡生成素材：与编辑器「生成」同一链（/api/editor/generate，诚实依赖生图 Provider）
  if (!currentProject.pid) { editorToast('先创建/打开项目', true); return; }
  _sbInlineRow(i,
    `<input type="text" placeholder="画面描述（例：极简产品特写，暖光）" style="flex:2">`,
    async (t) => {
      if (!t) return;
      try {
        const r = await apiPost('/api/editor/generate', {project_id: currentProject.pid, prompt: t,
          type: 'image', model: '', reference: ''});
        if (!r.ok) { editorToast('生成暂不可用：' + (r.error || '未配置图片生成服务。可到「大模型配置中心」为生图角色配置服务，或先本地上传素材文件。'), true); return; }
        editorToast(`✅ 已生成 ${(r.results||[]).length} 个变体并保存到素材库，可到编辑器「资源库→生成」查看采用`);
      } catch (e) { editorToast('生成失败：' + String(e), true); }
    }, '生成配图');
}
function sbAdjust(i){
  _sbInlineRow(i,
    `<input type="text" placeholder="描述你要的改变（例：这段时长改为 6 秒）" style="flex:2">`,
    (t) => {
      if (!t) return;
      document.getElementById('rev-input').value = t;
      applyRevision();
    });
}

function refreshStoryboardUI(){
  const box = document.getElementById('storyboard-box');
  if (!box || !currentProject.sb) return;  // 结果区无分镜 DOM（成片直出路径）→ 跳过
  box.innerHTML = renderStoryboard(currentProject.sb);
  const meta = document.getElementById('sb-meta');
  if (meta) meta.textContent =
    `总时长 ${(currentProject.sb?.total_duration||0).toFixed(1)}s · ${currentProject.sb?.scene_count||0} 场景`;
}

async function doRevision(payload){
  const out = document.getElementById('rev-status');
  out.innerHTML = '<span class="rev-hint">应用修改中……</span>';
  try {
    const r = await fetch('/api/revision', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(Object.assign({project_id: currentProject.pid, edits: currentProject.edits}, payload))
    });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || '修改失败');
    currentProject.edits = j.edits || {};
    currentProject.sb = j.storyboard || currentProject.sb;
    refreshStoryboardUI();
    document.getElementById('rev-input').value = '';
    out.innerHTML = '<span class="rev-done">✅ 已应用（正式渲染时将生效）</span>';
  } catch (e) {
    out.innerHTML = `<span class="rev-err">${esc(e.message)}</span>`;
  }
}

function applyRevision(){
  const instr = (document.getElementById('rev-input').value || '').trim();
  if (!instr) { document.getElementById('rev-status').innerHTML = '<span class="rev-err">请输入修改内容</span>'; return; }
  doRevision({instruction: instr});
}

function removeSceneDirect(sid){
  doRevision({direct_edits: {remove: [sid]}});
}

function moveScene(index, delta){
  // 前端本地重排（reorder 语义在 edits 里表达，确认渲染时生效）
  const sc = currentProject.sb;
  if (!sc || !sc.scenes) return;
  const j = index + delta;
  if (j < 0 || j >= sc.scenes.length) return;
  const ids = sc.scenes.map(s => s.scene_id);
  [ids[index], ids[j]] = [ids[j], ids[index]];
  doRevision({direct_edits: {reorder: ids}});
}

async function renderDraftPreview(){
  const box = document.getElementById('draft-box');
  const vid = document.getElementById('draft-video');
  box.style.display = 'block';
  vid.removeAttribute('src');
  vid.insertAdjacentHTML('beforebegin', '');
  const statusEl = box.querySelector('.hint');
  statusEl.textContent = '草稿渲染中……（低分辨率快速，约 10-30 秒）';
  try {
    const r = await fetch('/api/draft', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({project_id: currentProject.pid})
    });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || '草稿渲染失败');
    vid.src = '/file?path=' + encodeURIComponent(j.draft_path);
    vid.play().catch(()=>{});
    statusEl.textContent = '动态草稿（低分辨率快速预览，不烧字幕）：';
  } catch (e) {
    statusEl.textContent = '草稿渲染失败：' + e.message;
  }
}

async function confirmRender(){
  const statusEl = document.getElementById('confirm-status');
  statusEl.textContent = '正在正式渲染……';
  try {
    const r = await fetch('/api/confirm', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({project_id: currentProject.pid, edits: currentProject.edits})
    });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || '确认渲染失败');
    // 复用 renderResult 展示 final（pending_confirm=false 走成片分支）
    renderResult({...j, elapsed_s: 0});
  } catch (e) {
    statusEl.textContent = '渲染失败：' + e.message;
  }
}

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
    window.currentProject = { pid: j.project_id || '', sb: j.storyboard || {}, edits: {} };
    const briefNow = (document.getElementById('creative_brief').value || '').trim();
    const briefLine = briefNow
      ? `<div class="hint">📋 创作要求：${esc(briefNow)}</div>` : '';
    out.innerHTML = `
      <div class="ok-box" data-pid="${esc(j.project_id || '')}">
        <b>已暂停在「等待确认」。</b>分镜如下，可逐段改稿：
        ${briefLine}
        <div class="hint" id="sb-meta"></div>
        <div id="storyboard-box"></div>
        <div class="rev-console">
          <b>自然语言改稿</b>
          <div class="btn-row">
            <input type="text" id="rev-input" class="rev-input" placeholder="输入修改，如：第二段换成 3.mp4 / 删掉第二段 / 第三段改成新文案">
            <button class="btn-secondary" onclick="applyRevision()">应用修改</button>
          </div>
          <div id="rev-status"></div>
          <div class="rev-hint">换素材（换镜头）正式渲染时生效；删段/改文案正式渲染时重合成旁白生效。</div>
        </div>
        <div class="btn-row">
          <button class="btn-secondary" onclick="renderDraftPreview()">🎬 先看草稿视频（快速低清）</button>
          <button onclick="confirmRender()">✅ 确认渲染</button>
          <button class="btn-secondary" onclick="location.reload()">↩ 返回修改</button>
        </div>
        <div id="draft-box" style="display:none" class="enhance-box">
          <div class="hint">动态草稿（低分辨率快速预览，不烧字幕；正式渲染看下方确认）：</div>
          <video controls id="draft-video" style="width:100%;max-width:420px;margin-top:8px;border-radius:8px;background:#000"></video>
        </div>
        <div class="hint" id="confirm-status"></div>
      </div>`;
    refreshStoryboardUI();
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
// §三十三 新增四页：Review / Publish / Automation / Settings
// ===========================================================================
async function loadReview(){
  const box = document.getElementById('review-list');
  box.innerHTML = '加载中……';
  let projs;
  try { projs = await (await fetch('/api/projects')).json(); } catch(e) { projs = null; }
  if (!Array.isArray(projs)) { box.innerHTML = '<div class="hint">项目列表加载失败，点刷新重试</div>'; return; }
  const rows = [];
  for (const p of projs) {
    const stage = p.stage || '';
    const done = p.has_final;
    const label = stage === 'AWAITING_CONFIRM' ? '🟡 待确认分镜' : stage === 'AWAITING_DRAFT' ? '🟠 待确认草稿' : (done ? '✅ 已完成' : '🔵 生成中/待处理');
    // §39：每个项目附带 Issues（preflight 阻断/警告），Issue 有 [去时间线][让 AI 修复]
    let issuesHtml = '';
    try {
      const pf = await (await fetch('/api/preflight?project_id=' + encodeURIComponent(p.project_id))).json();
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
    const inp = document.getElementById('editor-ai-input');
    if (inp) inp.value = '修复以下问题：' + issue;
  }, 500);
}

async function loadPublish(){
  const box = document.getElementById('publish-list');
  box.innerHTML = '加载中……';
  let projs;
  try { projs = await (await fetch('/api/projects')).json(); } catch(e) { projs = null; }
  if (!Array.isArray(projs)) { box.innerHTML = '<div class="hint">项目列表加载失败，点刷新重试</div>'; return; }
  const rows = [];
  for (const p of projs) {
    let pf;
    try { pf = await (await fetch('/api/preflight?project_id=' + encodeURIComponent(p.project_id))).json(); } catch(e) {}
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
  const pf = await (await fetch('/api/preflight?project_id=' + encodeURIComponent(pid))).json();
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
    const r = await (await fetch('/api/doctor')).json();
    const cn = {PASS:'✅', FAIL:'⛔', SKIP:'🔶'};
    box.innerHTML = (Array.isArray(r) ? r : []).map(c =>
      `<div class="hint">${cn[c.status]||'·'} ${esc(c.name)}：<b>${esc(c.status)}</b> — ${esc(c.detail)}</div>`).join('')
      || '<div class="hint">无自检结果</div>';
  } catch(e) { box.innerHTML = '<div class="err-box">自检失败：' + esc(String(e)) + '</div>'; }
}

// §8 拆分导出：inline onclick/window 引用兼容（零行为改动）
Object.assign(window, {
  renderStoryboard,
  sbChangeShot,
  sbChangeVisual,
  sbGenAsset,
  sbAdjust,
  refreshStoryboardUI,
  doRevision,
  applyRevision,
  removeSceneDirect,
  moveScene,
  renderDraftPreview,
  confirmRender,
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
  currentProject,
});
