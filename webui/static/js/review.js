// §24/§25/§45/§68 Review（方案《FRONTEND_DEEP_UNIFICATION》）：
// 问题工作台三栏：问题列表 | 当前预览 | 修复。
//   - ContinuityVerifier = Issue 一等来源（§25）
//   - 每个 Issue：发生了什么 / 影响对象 / 为什么被拦截 / 定位 / 修复 / 重新检查
//   - Locate → workspaceOpen(pid, target) 带定位跳转（§45 自动选对象+开 Inspector 分区）
//   - §68 Issue 统一结构：code/message/project_id/scene_id/from/to/target/recoverable

window.RV = {issues: [], selected: null, projects: []};

const ISSUE_CODE_CN = {
  // 连续性（§25）
  CARRY_MISSING_SOURCE: '元素延续缺来源层', CARRY_MISSING_TARGET: '元素延续缺目标层',
  CAMERA_PATH_MISSING: '镜头穿越缺路径', MORPH_PATH_MISSING: '形变缺路径',
  STATIC_REPLACEMENT: '静态画面连续替换', ANCHOR_JUMP: '延续元素锚点跳变',
  NO_CONTINUITY_DECLARED: '相邻镜头未声明关系',
  // 商用预检（§68）
  ASSET_LICENSE_UNKNOWN: '素材授权未知', ASSET_LICENSE_BLOCKED: '素材禁止商用',
  COMPOSITION_INVALID: '时间线不完整', MEDIA_MISSING: '素材文件缺失',
};

/** 审阅页装载：项目三态待办 + 全项目 Issue 聚合。 */
async function loadReview(){
  const box = document.getElementById('review-list');
  const issueBox = document.getElementById('review-issue-list');
  if (box) box.innerHTML = '加载中……';
  const r = await apiGet('/api/projects?filter=all&limit=100');
  const projs = (r && !r.error && r.projects) || [];
  RV.projects = projs;
  // 三态待办（§24 底部确认区）
  const pending = projs.filter(p => p.status === 'AWAITING_CONFIRM' && !p.archived);
  const failed = projs.filter(p => p.status === 'FAILED' && !p.archived);
  if (box) {
    const row = (p, label, cls) => `<div class="proj-row"><span><b>${esc(p.name || p.project_id)}</b>`+
      `<span class="hint">${p.scene_count ? p.scene_count + ' 场景 · ' : ''}${p.duration ? p.duration.toFixed(1) + 's · ' : ''}${label}</span></span>`+
      `<span class="btn-row">`+
      (p.editable ? `<button class="btn-sm" onclick="workspaceOpen('${esc(p.project_id)}')">继续确认</button>` : '')+
      (p.has_final ? `<button class="btn-secondary btn-sm" onclick="pmPreviewFinal('${esc(p.project_id)}')">预览</button>` : '')+
      `</span></div>`;
    box.innerHTML = (pending.map(p => row(p, '等待确认分镜/草稿', 'cap-warn')).join('')
      + failed.map(p => row(p, '生成失败（打开可看错误）', 'cap-off')).join(''))
      || '<div class="hint">没有待确认项目（全部已完成或在生成中）。</div>';
  }
  // Issue 聚合（§25 Continuity + §68 预检）
  if (issueBox) {
    issueBox.innerHTML = '<div class="hint">检查中……</div>';
    RV.issues = [];
    const checkable = projs.filter(p => p.editable && !p.archived).slice(0, 30);
    for (const p of checkable) {
      // 连续性 Issue（§25 一等来源）
      try {
        const c = await apiGet('/api/continuity?project_id=' + encodeURIComponent(p.project_id));
        if (c && !c.error) {
          for (const i of (c.issues || [])) {
            RV.issues.push({
              code: i.issue_code, message: i.message,
              project_id: p.project_id, project_name: p.name || p.project_id,
              scene_id: i.scene_id || i.to_scene_id || '',
              from_scene_id: i.from_scene_id || '', to_scene_id: i.to_scene_id || i.scene_id || '',
              target: i.from_scene_id && i.to_scene_id ? {type: 'scene_pair', id: `${i.from_scene_id}->${i.to_scene_id}`}
                    : (i.scene_id ? {type: 'scene', id: i.scene_id} : {type: 'project', id: p.project_id}),
              kind: '连续性', recoverable: i.recoverable !== false,
            });
          }
        }
      } catch (e) { /* 单项目失败不阻塞聚合 */ }
      // 商用预检 Issue（§68）
      try {
        const pf = await apiGet('/api/preflight?project_id=' + encodeURIComponent(p.project_id));
        if (pf && !pf.error) {
          for (const i of (pf.issues || [])) {
            RV.issues.push({
              code: i.code, message: i.message,
              project_id: p.project_id, project_name: p.name || p.project_id,
              scene_id: (i.targetId && i.targetId.includes('#')) ? i.targetId.split('#')[0] : '',
              from_scene_id: '', to_scene_id: '',
              target: (i.targetId && i.targetId.includes('#'))
                ? {type: 'layer', id: i.targetId}
                : (i.targetId ? {type: 'scene', id: i.targetId} : {type: 'project', id: p.project_id}),
              kind: '商用预检', recoverable: i.recoverable !== false,
            });
          }
        }
      } catch (e) { /* 同上 */ }
    }
    renderIssueList();
  }
  loadPublish();
}

function renderIssueList(){
  const box = document.getElementById('review-issue-list');
  if (!box) return;
  if (!RV.issues.length) {
    box.innerHTML = '<div class="hint">全片检查完成：没有发现问题。</div>';
    const prev = document.getElementById('review-preview');
    const fix = document.getElementById('review-fix-panel');
    if (prev) prev.textContent = '选中一个问题后在这里定位';
    if (fix) fix.textContent = '选中问题后给出修复选项';
    return;
  }
  const counts = {};
  for (const i of RV.issues) counts[i.kind] = (counts[i.kind] || 0) + 1;
  box.innerHTML =
    `<div class="hint" style="margin:2px 0 6px">${Object.entries(counts).map(([k, n]) => `${esc(k)} ${n}`).join(' · ')}</div>`+
    RV.issues.map((i, n) => {
      const selCls = RV.selected === n ? ' rv-sel' : '';
      return `<div class="proj-row${selCls}" onclick="reviewSelectIssue(${n})">
        <span><b class="${i.recoverable ? 'cap-warn' : 'cap-off'}">${esc(ISSUE_CODE_CN[i.code] || i.code)}</b>
        <span class="hint">${esc(i.project_name)}${i.from_scene_id ? ' · ' + esc(i.from_scene_id) + ' → ' + esc(i.to_scene_id) : (i.scene_id ? ' · ' + esc(i.scene_id) : '')}</span>
        <span class="hint">${esc((i.message || '').slice(0, 60))}</span></span>
      </div>`;
    }).join('');
}

/** 选中 Issue → 预览列（发生了什么/影响对象/为什么被拦截）+ 修复列。 */
function reviewSelectIssue(n){
  RV.selected = n;
  const i = RV.issues[n];
  if (!i) return;
  renderIssueList();
  const prev = document.getElementById('review-preview');
  prev.innerHTML = `
    <b>${esc(ISSUE_CODE_CN[i.code] || i.code)}</b>
    <div class="hint">项目：${esc(i.project_name)}${i.scene_id || i.from_scene_id ? ' · 对象：' + esc(i.from_scene_id ? i.from_scene_id + ' → ' + i.to_scene_id : i.scene_id) : ' · 整个项目'}</div>
    <div class="hint">发生了什么：${esc(i.message || '—')}</div>
    <div class="hint">为什么被拦截：${esc(_issueWhy(i))}</div>
    <div class="btn-row"><button class="btn-sm" onclick="reviewLocate(${n})">定位到工作台</button></div>`;
  const fix = document.getElementById('review-fix-panel');
  fix.innerHTML = `
    <b>修复</b>
    <div class="hint">${esc(i.project_name)}${i.from_scene_id ? ' · ' + esc(i.from_scene_id) + ' → ' + esc(i.to_scene_id) : ''}</div>
    ${_fixOptions(n)}
    <div class="btn-row" style="margin-top:8px">
      <button class="btn-secondary btn-sm" onclick="reviewRecheck('${esc(i.project_id)}')">重新检查</button>
      <button class="btn-secondary btn-sm" onclick="reviewAskAI('${esc(i.project_id)}','${esc((i.message || '').slice(0,40))}')">让 AI 修复</button>
    </div>`;
}

function _issueWhy(i){
  const why = {
    CARRY_MISSING_SOURCE: '元素延续声明了来源，但来源层不存在——渲染时延续元素会凭空出现',
    CARRY_MISSING_TARGET: '元素延续声明了目标，但目标层不存在——延续没有落点',
    CAMERA_PATH_MISSING: '镜头穿越/俯冲需要明确的镜头路径，缺失会导致运动不成立',
    MORPH_PATH_MISSING: '形变过渡需要形变路径（自有图形块间），缺失则形变无法执行',
    STATIC_REPLACEMENT: '相邻画面连续静态替换（幻灯片式），观感断裂',
    ANCHOR_JUMP: '延续元素的锚点位置在两镜间跳变，视觉上会突然位移',
    NO_CONTINUITY_DECLARED: '相邻镜头未声明任何关系，系统无法保证画面衔接',
    ASSET_LICENSE_UNKNOWN: '素材来源不明，商业发布存在授权风险',
    ASSET_LICENSE_BLOCKED: '素材授权被标记为禁止商用',
    COMPOSITION_INVALID: '时间线存在不完整字段，渲染会失败',
    MEDIA_MISSING: '引用的素材文件已不存在',
  };
  return why[i.code] || '该问题会在渲染或发布时造成阻断/观感问题';
}

/** §25 修复选项：按 Issue 类型给「镜头关系」语义的修复建议（人话）。 */
function _fixOptions(n){
  const i = RV.issues[n];
  if (i.kind === '连续性') {
    return `<div class="hint">建议的修复方向：</div>
      <div class="btn-row" style="flex-wrap:wrap">
        ${i.from_scene_id && i.to_scene_id ? `<button class="btn-secondary btn-sm" onclick="reviewFixRelation('${esc(i.project_id)}','${esc(i.from_scene_id)}','${esc(i.to_scene_id)}','carry')">改元素延续</button>
        <button class="btn-secondary btn-sm" onclick="reviewFixRelation('${esc(i.project_id)}','${esc(i.from_scene_id)}','${esc(i.to_scene_id)}','morph')">改形变过渡</button>
        <button class="btn-secondary btn-sm" onclick="reviewFixRelation('${esc(i.project_id)}','${esc(i.from_scene_id)}','${esc(i.to_scene_id)}','hard_cut')">改为硬切</button>` : ''}
      </div>
      <div class="hint">点任一方向会打开工作台、选中这一对镜头并显示「镜头关系」面板，确认后应用。</div>`;
  }
  return `<div class="hint">点「定位到工作台」查看问题对象；素材授权类问题需换用可商用素材（选中层 → 换素材）。</div>`;
}

/** §45 Locate：带定位跳转（open project + select target + 打开 Inspector 分区）。 */
async function reviewLocate(n){
  const i = RV.issues[n];
  if (!i) return;
  const r = await workspaceOpen(i.project_id, {
    type: i.target.type, id: i.target.id,
    openSection: i.kind === '连续性' ? 'relation' : 'source',
  });
  if (r && r.ok) editorToast(`已定位：${i.from_scene_id ? i.from_scene_id + ' → ' + i.to_scene_id : (i.scene_id || i.project_id)}`);
}

/** §25 修复方向：定位 + 预填镜头关系类型（应用仍在工作台确认）。 */
async function reviewFixRelation(pid, fromId, toId, type){
  const r = await workspaceOpen(pid, {type: 'scene_pair', id: `${fromId}->${toId}`, openSection: 'relation'});
  if (!r || !r.ok) return;
  // 预填关系类型（Inspector 里的下拉同步），用户点「应用」才落 Command
  const sel = document.getElementById('ed-cont-type');
  if (sel) { sel.value = type; edContTypeChanged(); }
  editorToast(`已选中 ${fromId} → ${toId}：确认「${CONT_CN[type] || type}」后点「应用」`);
}

/** 重新检查（§24：修复后回到这里验证）。 */
async function reviewRecheck(pid){
  const fixBox = document.getElementById('review-fix-panel');
  const st = document.createElement('div');
  st.className = 'hint';
  st.textContent = '重新检查中……';
  fixBox.appendChild(st);
  const c = await apiGet('/api/continuity?project_id=' + encodeURIComponent(pid));
  const pf = await apiGet('/api/preflight?project_id=' + encodeURIComponent(pid));
  const nCont = (c.issues || []).length;
  const nPf = (pf.issues || []).length;
  st.textContent = nCont + nPf === 0
    ? '检查通过：连续性与商用预检均无问题'
    : `仍有 ${nCont} 条连续性 + ${nPf} 条预检问题（刷新上方列表查看最新）`;
}

/** §23 Command Bar 承载自然语言修复。 */
async function reviewAskAI(pid, issue){
  const r = await workspaceOpen(pid);
  if (!r || !r.ok) return;
  const inp = document.getElementById('director-input');
  if (inp) { inp.value = '修复以下问题：' + issue; inp.focus(); }
  editorToast('已带问题描述进入工作台，按回车让 AI 执行');
}

// ---------------------------------------------------------------------------
// 发布预检（审阅 → 预检 → 发布同一交付链 §28）
// ---------------------------------------------------------------------------
async function loadPublish(){
  const box = document.getElementById('publish-list');
  if (!box) return;
  box.innerHTML = '加载中……';
  const projs = RV.projects.length ? RV.projects : (async () => {
    const r = await apiGet('/api/projects?filter=all&limit=100');
    return (r && r.projects) || [];
  })();
  const list = await projs;
  const finals = list.filter(p => p.has_final && !p.archived).slice(0, 20);
  box.innerHTML = finals.length ? finals.map(p =>
    `<div class="proj-row"><span><b>${esc(p.name || p.project_id)}</b>
    <span class="hint">有成品 · ${p.duration ? p.duration.toFixed(1) + 's' : ''}</span></span>`+
    `<span class="btn-row"><button class="btn-secondary btn-sm" onclick="publishDetail('${esc(p.project_id)}')">查看预检</button>`+
    `<button class="btn-sm" onclick="publishVariants('${esc(p.project_id)}')">发布多平台</button></span></div>`).join('')
    : '<div class="hint">暂无已完成项目。</div>';
}
async function publishDetail(pid){
  const box = document.getElementById('publish-detail-box');
  box.innerHTML = '预检中……';
  const pf = await apiGet('/api/preflight?project_id=' + encodeURIComponent(pid));
  const row = (i) => {
    const block = i.recoverable === false && i.code && !i.code.startsWith('ASSET_');
    return `<div class="${block ? 'err-box' : 'hint'}" style="margin:4px 0">${block ? '阻断' : '提示'} ${esc(ISSUE_CODE_CN[i.code] || i.code || '')} — ${esc(i.message||i.detail||'')}</div>`;
  };
  box.innerHTML = `<div class="panel" style="margin-top:8px;padding:12px"><b>发布预检：${esc(pid)}</b>
    <div class="hint">状态：<b>${pf.status}</b>${pf.status==='PASS' ? '（可发布）' : '（有阻断，不能发布）'}</div>
    ${(pf.issues||[]).map(row).join('') || '<div class="hint">无问题。</div>'}</div>`;
}

// ---------------------------------------------------------------------------
// Preflight / Render / Toast（工作台消费）
// ---------------------------------------------------------------------------
async function editorPreflight(){
  const box = document.getElementById('editor-preflight-box');
  const r = await apiGet('/api/preflight?project_id=' + encodeURIComponent(ED.pid));
  if (r.status === 'PASS') {
    box.innerHTML = `<div class="panel ok-box">商用预检通过（${r.issues.length} 项提示，无阻断）`+
      (r.issues.length ? `<div>${r.issues.map(i=>`<div class="hint">提示 ${esc(ISSUE_CODE_CN[i.code] || i.code)}：${esc(i.message)}</div>`).join('')}</div>` : '') + `</div>`;
  } else {
    box.innerHTML = `<div class="panel err-box">商用预检未通过（${r.issues.filter(i=>!i.recoverable).length} 项阻断）：
      ${r.issues.map(i=>`<div class="hint">${i.recoverable?'提示':'阻断'} ${esc(ISSUE_CODE_CN[i.code] || i.code)}：${esc(i.message)}${i.targetId ? '（对象 ' + esc(i.targetId) + '）' : ''}</div>`).join('')}</div>`;
  }
  return r;
}
async function editorRenderFinal(){
  const pf = await editorPreflight();
  if (pf.status !== 'PASS') { editorToast('商用预检未通过，渲染中止（先处理阻断项）', true); return; }
  editorToast('预检通过，渲染已提交……');
  const r = await apiPost('/api/confirm', {project_id: ED.pid});
  if (r.ok) {
    document.getElementById('editor-preflight-box').innerHTML =
      `<div class="panel ok-box">成片完成：<a class="dl" href="/file?path=${encodeURIComponent(r.final_path)}" download="final.mp4">下载 final.mp4</a> 质检：${esc((r.qc||{}).verdict||'')}</div>`;
  } else {
    document.getElementById('editor-preflight-box').innerHTML =
      `<div class="err-box">渲染失败：${esc(r.error||'')}</div>`;
  }
}

function editorToast(msg, isErr){
  const host = document.getElementById('global-toast-host');
  const h = host || (() => {
    const el = document.createElement('div');
    el.id = 'global-toast-host';
    el.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:9999;max-width:420px';
    document.body.appendChild(el);
    return el;
  })();
  const t = document.createElement('div');
  t.textContent = msg;
  t.style.cssText = 'background:' + (isErr ? 'var(--surface-3)' : 'var(--surface-2)') + ';' +
    'border:1px solid ' + (isErr ? 'var(--danger)' : 'var(--success)') + ';color:' +
    (isErr ? 'var(--danger)' : 'var(--success)') + ';padding:10px 14px;border-radius:8px;' +
    'font-size:13px;margin-top:6px;box-shadow:0 4px 16px rgba(0,0,0,.4)';
  h.appendChild(t);
  setTimeout(() => { t.remove(); }, 5000);
}

Object.assign(window, {
  loadReview, renderIssueList, reviewSelectIssue, reviewLocate,
  reviewFixRelation, reviewRecheck, reviewAskAI,
  loadPublish, publishDetail,
  editorPreflight, editorRenderFinal, editorToast,
  ISSUE_CODE_CN, RV,
});
