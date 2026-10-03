// 方案§47/§3：审阅 = 工作台的「问题模式」（不是独立页面）。
// 对当前 Composition 做 Verify → 定位到具体 Scene/Layer/Scene Pair → 修复。
// Global Selection 唯一（§47），Review 与工作台共享同一 Selection Service。
//
// 兼容层：loadReview()/loadPublish() 保留（旧入口调用安全），但不再渲染
// 独立页面——问题模式面板（review-mode-panel）是唯一宿主。

window.RV = {issues: [], selected: null, projects: [], mode: false};

/** §3 问题模式开关（工作台顶栏「问题」按钮）。 */
function reviewModeToggle(){
  const panel = document.getElementById('review-mode-panel');
  if (!panel) { editorToast('先打开一个项目（问题模式检查当前项目）', true); return; }
  RV.mode = !RV.mode;
  panel.style.display = RV.mode ? '' : 'none';
  const btn = document.getElementById('tb-review-mode');
  if (btn) btn.classList.toggle('tl-switch-on', RV.mode);
  if (RV.mode) { reviewModeScan(); loadReview(); loadPublish(); }
}

/** §47 扫描当前项目：连续性 + 商用预检 → 问题列表（真实验证，非汇总页）。 */
async function reviewModeScan(){
  const box = document.getElementById('review-mode-list');
  if (!box) return;
  if (!ED.pid) { box.textContent = '先打开一个项目。'; return; }
  box.innerHTML = '<div class="hint">检查中……</div>';
  const issues = [];
  try {
    const c = await apiGet('/api/continuity?project_id=' + encodeURIComponent(ED.pid));
    if (c && !c.error) {
      for (const i of (c.issues || [])) issues.push({
        code: i.issue_code, message: i.message, kind: '连续性',
        scene_id: i.scene_id || i.to_scene_id || '',
        from_scene_id: i.from_scene_id || '', to_scene_id: i.to_scene_id || i.scene_id || '',
        target: i.from_scene_id && i.to_scene_id
          ? {type: 'scene_pair', id: `${i.from_scene_id}->${i.to_scene_id}`}
          : (i.scene_id ? {type: 'scene', id: i.scene_id} : {type: 'project', id: ED.pid}),
        recoverable: i.recoverable !== false,
      });
    }
  } catch (e) { /* 单源失败不阻塞 */ }
  try {
    const pf = await apiGet('/api/preflight?project_id=' + encodeURIComponent(ED.pid));
    if (pf && !pf.error) {
      for (const i of (pf.issues || [])) issues.push({
        code: i.code, message: i.message, kind: '商用预检',
        scene_id: (i.targetId && i.targetId.includes('#')) ? i.targetId.split('#')[0] : '',
        from_scene_id: '', to_scene_id: '',
        target: (i.targetId && i.targetId.includes('#'))
          ? {type: 'layer', id: i.targetId}
          : (i.targetId ? {type: 'scene', id: i.targetId} : {type: 'project', id: ED.pid}),
        recoverable: i.recoverable !== false,
      });
    }
  } catch (e) { /* 同上 */ }
  RV.issues = issues;
  const summary = document.getElementById('review-mode-summary');
  if (summary) summary.textContent = issues.length
    ? `发现 ${issues.length} 个问题（连续性 + 商用预检）——点问题定位修复`
    : '当前项目检查通过：没有发现问题';
  if (!issues.length) {
    box.innerHTML = '<div class="hint">全片检查完成：没有发现问题。</div>';
    const fix = document.getElementById('review-mode-fix');
    if (fix) fix.textContent = '';
    return;
  }
  const counts = {};
  for (const i of issues) counts[i.kind] = (counts[i.kind] || 0) + 1;
  box.innerHTML = `<div class="hint" style="margin:2px 0 6px">${Object.entries(counts).map(([k, n]) => `${esc(k)} ${n}`).join(' · ')}</div>`+
    issues.map((i, n) => `
      <div class="proj-row" onclick="reviewModeSelect(${n})" style="cursor:pointer">
        <span><b class="${i.recoverable ? 'cap-warn' : 'cap-off'}">${esc(ISSUE_CODE_CN[i.code] || i.code)}</b>
        <span class="hint">${i.from_scene_id ? esc(i.from_scene_id) + ' → ' + esc(i.to_scene_id) : (i.scene_id ? esc(i.scene_id) : '整个项目')}</span>
        <span class="hint">${esc((i.message || '').slice(0, 60))}</span></span>
      </div>`).join('');
}

/** §47 选中问题 → 定位（工作台内：select target + 打开 Inspector 分区）。 */
function reviewModeSelect(n){
  const i = RV.issues[n];
  if (!i) return;
  if (i.target.type === 'scene') {
    selectScene(i.target.id, null, 'review-mode');
    timelineScrollToScene(i.target.id);
  } else if (i.target.type === 'layer') {
    const [sceneId, idxS] = i.target.id.split('#');
    selectLayer(sceneId, parseInt(idxS), null, 'review-mode');
    timelineScrollToScene(sceneId);
  } else if (i.target.type === 'scene_pair') {
    const [f, t] = i.target.id.split('->');
    selectScenePair(f, t, 'review-mode');
    timelineScrollToScene(t);
  }
  if (typeof edShowInspector === 'function') edShowInspector();
  const section = i.kind === '连续性' ? 'relation' : 'source';
  if (typeof inspectorOpenSection === 'function') inspectorOpenSection(section);
  const fix = document.getElementById('review-mode-fix');
  if (fix) fix.innerHTML = `
    <b>${esc(ISSUE_CODE_CN[i.code] || i.code)}</b> · ${esc((i.message || '').slice(0, 80))}
    <div class="hint">${esc(_issueWhy(i))}</div>
    ${i.kind === '连续性' && i.from_scene_id && i.to_scene_id ? `
    <div class="btn-row" style="margin-top:4px;flex-wrap:wrap">
      <button class="btn-secondary btn-sm" data-action="relation.set" data-params='{"type":"carry"}'>改元素延续</button>
      <button class="btn-secondary btn-sm" data-action="relation.set" data-params='{"type":"morph"}'>改形变过渡</button>
      <button class="btn-secondary btn-sm" data-action="relation.set" data-params='{"type":"hard_cut"}'>改为硬切</button>
    </div>` : ''}
    <div class="btn-row" style="margin-top:4px;flex-wrap:wrap">
      <button class="btn-secondary btn-sm" onclick="reviewModeScan()">重新检查</button>
      <button class="btn-secondary btn-sm" onclick="reviewAskAI('${esc(ED.pid)}','${esc((i.message || '').slice(0,40))}')">让 AI 修复</button>
    </div>`;
  editorToast('已定位问题对象（右侧 Inspector 已打开对应分区）');
}

/** 旧入口兼容（不再有独立审阅页；问题模式面板内呈现待确认）。 */
async function loadReview(){
  const box = document.getElementById('review-list');
  if (!box) return;
  const r = await apiGet('/api/projects?filter=all&limit=100');
  const projs = (r && !r.error && r.projects) || [];
  RV.projects = projs;
  const pending = projs.filter(p => p.status === 'AWAITING_CONFIRM' && !p.archived);
  box.innerHTML = pending.length ? pending.map(p =>
    `<div class="proj-row"><span><b>${esc(p.name || p.project_id)}</b>
      <span class="hint">${p.scene_count || 0} 场景 · 等待确认分镜</span></span>
      <span class="btn-row">
      ${p.editable ? `<button class="btn-sm" onclick="workspaceOpen('${esc(p.project_id)}')">继续确认</button>` : ''}
      ${p.has_final ? `<button class="btn-secondary btn-sm" onclick="pmPreviewFinal('${esc(p.project_id)}')">预览</button>` : ''}
      </span></div>`).join('')
    : '<div class="hint">没有待确认项目。</div>';
}

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

/** §25 修复方向：定位 + 预填镜头关系类型（应用仍在工作台确认）。 */
async function reviewFixRelation(pid, fromId, toId, type){
  // 问题模式内已是当前项目：直接选中这一对镜头；跨项目才 workspaceOpen
  if (pid && ED.pid === pid) {
    selectScenePair(fromId, toId, 'review-mode');
    const sel = document.getElementById('ed-cont-type');
    if (sel) { sel.value = type; edContTypeChanged(); }
    if (typeof edShowInspector === 'function') edShowInspector();
    if (typeof inspectorOpenSection === 'function') inspectorOpenSection('relation');
    editorToast(`已选中 ${fromId} → ${toId}：确认「${CONT_CN[type] || type}」后点「应用」`);
    return;
  }
  const r = await workspaceOpen(pid, {type: 'scene_pair', id: `${fromId}->${toId}`, openSection: 'relation'});
  if (!r || !r.ok) return;
  const sel = document.getElementById('ed-cont-type');
  if (sel) { sel.value = type; edContTypeChanged(); }
  editorToast(`已选中 ${fromId} → ${toId}：确认「${CONT_CN[type] || type}」后点「应用」`);
}

/** 重新检查（问题模式：修复后回到面板验证）。 */
async function reviewRecheck(pid){
  reviewModeScan();
}

/** §23 Command Bar 承载自然语言修复。 */
async function reviewAskAI(pid, issue){
  const inp = document.getElementById('director-input');
  if (inp) { inp.value = '修复以下问题：' + issue; inp.focus(); }
  editorToast('已带问题描述，按回车让 AI 执行');
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

/** §40 Draft 级：低分辨率快速草稿（先看效果，满意再渲成片）。 */
async function editorDraftRender(){
  if (!ED.pid) { editorToast('先打开项目', true); return; }
  editorToast('正在快速渲染草稿（低分辨率，速度优先）……');
  const r = await apiPost('/api/draft', {project_id: ED.pid});
  if (!r.ok) {
    document.getElementById('editor-preflight-box').innerHTML =
      `<div class="err-box">草稿渲染失败：${esc(r.error || '未知')}（项目内容没有变化，可重试）</div>`;
    return;
  }
  // 草稿直接进中央 Preview（§19 唯一预览宿主）
  previewShowVideo(r.draft_path, '草稿（低清快速版）');
  document.getElementById('editor-preflight-box').innerHTML =
    `<div class="panel ok-box">草稿已生成（预览区播放中）。满意后点「渲染成片」出正式版；
      不满意继续在时间线改，改完重新渲染草稿。</div>`;
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
  reviewModeToggle, reviewModeScan, reviewModeSelect,
  loadReview, loadPublish, publishDetail,
  reviewFixRelation, reviewRecheck, reviewAskAI,
  editorPreflight, editorRenderFinal, editorDraftRender, editorToast,
  ISSUE_CODE_CN, RV,
});
