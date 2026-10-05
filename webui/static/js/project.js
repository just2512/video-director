// §61 Project Manager 前端（方案《FRONTEND_DEEP_UNIFICATION》§5/§7/§8/§58/§61）：
// 项目 = 有状态/有生命周期/有操作集合的一级业务对象，不是一串只能点击打开的链接。
//
// 全部动作走 POST /api/project_action，成功后重新拉 Project Index（§61：
// 不在前端本地修改 project list）。
//
// Project Index（§52）由服务端解释状态：status/editable/legacy/archived/
// scene_count/duration/has_draft/has_final/actions——前端不猜。

window.PM = {index: [], filter: 'all', query: '', shown: 50, busy: {}, selected: new Set()};

const PM_PAGE = 50;  // §8 大量项目：列表分页渲染（默认 50，「显示更多」追加）

const PM_STATUS_CN = {
  CREATED: '待处理', TTS: '生成中', ALIGNMENT: '生成中', MATCHING: '生成中',
  PLANNING: '生成中', AWAITING_CONFIRM: '待确认', RENDERING: '生成中', QC: '生成中',
  DONE: '已完成', FAILED: '失败', UNKNOWN: '旧版',
};

function pmStatusCn(p){
  if (p.archived) return '已归档';
  if (p.interrupted) return '已中断';
  if (p.legacy) return '旧版';
  const s = p.status;
  if (p.active_job && s !== 'AWAITING_CONFIRM')
    return `生成中${p.progress ? ' · ' + p.progress + '%' : ''}`;  // §58 进行中带进度
  return PM_STATUS_CN[s] || '未知';
}
function pmStatusCls(p){
  if (p.archived) return 'pm-st-muted';
  if (p.legacy) return 'pm-st-legacy';
  const s = p.status;
  if (s === 'DONE') return 'pm-st-ok';
  if (s === 'FAILED') return 'pm-st-err';
  if (s === 'AWAITING_CONFIRM') return 'pm-st-warn';
  if (['TTS','ALIGNMENT','MATCHING','PLANNING','RENDERING','QC'].includes(s)) return 'pm-st-run';
  return 'pm-st-muted';
}

/** 项目页主加载：Project Index + 筛选 + 搜索。§8 列表模式（名称|状态|时长|修改|操作）。 */
async function loadProjects(){
  const box = document.getElementById('pm-list');
  if (!box) return;
  box.innerHTML = '<div class="pm-empty">加载项目……</div>';
  try {
    const q = PM.query ? ('&q=' + encodeURIComponent(PM.query)) : '';
    const r = await apiGet('/api/projects?filter=' + PM.filter + q);
    if (r.error) throw new Error(r.error);
    PM.index = r.projects || [];
    PM.shown = PM_PAGE;
    renderProjectList();
  } catch (e) {
    box.innerHTML = `<div class="err-box">项目列表加载失败：${esc(e.message)}<br>可点「刷新」重试</div>`;
  }
}

function renderProjectList(){
  const box = document.getElementById('pm-list');
  const countEl = document.getElementById('pm-count');
  if (!box) return;
  if (countEl) countEl.textContent = PM.index.length ? `${PM.index.length} 个项目` : '';
  if (!PM.index.length) {
    box.innerHTML = '<div class="pm-empty">没有符合条件的项目<br><span class="hint">换个筛选条件，或点上方「新建项目」开始创作。</span></div>';
    pmRefreshBulkBar();
    return;
  }
  const shown = PM.index.slice(0, PM.shown);
  box.innerHTML = shown.map(p => {
    const actions = p.actions || {};
    // 主行动作（§43 第一按钮：状态决定；其余进「···」菜单）
    let primary = '';
    if (p.legacy) primary = `<button class="btn-sm" onclick="convertLegacyProject('${esc(p.project_id)}')">转换为可编辑项目</button>`;
    else if (actions.continue_) primary = `<button class="btn-sm" onclick="continueProject('${esc(p.project_id)}')">继续确认</button>`;
    else if (p.status === 'FAILED') primary = `<button class="btn-secondary btn-sm" onclick="workspaceOpen('${esc(p.project_id)}')">查看问题</button>`;
    else if (actions.open) primary = `<button class="btn-secondary btn-sm" onclick="workspaceOpen('${esc(p.project_id)}')">继续编辑</button>`;
    if (p.status === 'FAILED') primary += ` <button class="btn-secondary btn-sm" onclick="pmReopenFailed('${esc(p.project_id)}')">重新执行</button>`;
    // 「···」菜单由 pmRowMenu 动态展开；动作存在性由 Index actions 决定。
    const dur = p.duration ? p.duration.toFixed(1) + 's' : '—';
    const when = p.updated_at ? new Date(p.updated_at * 1000).toLocaleDateString() : '—';
    const name = p.name || p.project_id;
    const busyAct = PM.busy[p.project_id];
    const busyCn = {rename: '重命名中……', clone: '复制中……', archive: '归档中……',
                    restore: '恢复中……', delete: '删除中……', convert: '转换中……'};
    return `<div class="pm-row${busyAct ? ' pm-row-busy' : ''}" data-pid="${esc(p.project_id)}">
      <span class="pm-name" onclick="pmOpenDetail('${esc(p.project_id)}')"><b>${esc(name)}</b>
        <span class="hint">${p.scene_count ? p.scene_count + ' 场景' : ''}${p.legacy ? ' · 旧版渲染项目' : (p.archived ? ' · 已归档' : '')}</span></span>
      <span class="pm-st ${pmStatusCls(p)}">${busyAct ? (busyCn[busyAct] || '处理中……') : pmStatusCn(p)}</span>
      <span class="pm-dur">${dur}</span>
      <span class="pm-when">${when}</span>
      <span class="pm-ops">${busyAct ? '<span class="hint">处理中……</span>'
        : `${primary} <button class="btn-secondary btn-sm pm-more-btn" onclick="pmRowMenu(event,'${esc(p.project_id)}')" title="更多操作">···</button>`}</span>
    </div>`;
  }).join('');
  if (PM.index.length > PM.shown) {
    box.innerHTML += `<div class="pm-empty"><button class="btn-secondary btn-sm" onclick="pmShowMore()">显示更多（还有 ${PM.index.length - PM.shown} 个）</button></div>`;
  }
}
// ---- 批量项目管理（同一后端 /api/bulk_action） ----
function pmClearSelection(){ PM.selected.clear(); renderProjectList(); }
function pmToggleSelected(pid, checked){
  if (checked) PM.selected.add(pid); else PM.selected.delete(pid);
  pmRefreshBulkBar();
}
function pmToggleAll(checked){
  const shown = PM.index.slice(0, PM.shown);
  for (const p of shown) {
    if (p.active_job) continue;
    if (checked) PM.selected.add(p.project_id); else PM.selected.delete(p.project_id);
  }
  renderProjectList();
}
function pmRefreshBulkBar(){
  const bar = document.getElementById('pm-bulk-bar');
  const count = document.getElementById('pm-selected-count');
  const del = document.getElementById('pm-bulk-delete');
  const arch = document.getElementById('pm-bulk-archive');
  const selAll = document.getElementById('pm-select-all');
  const rows = [...PM.selected].map(pid => PM.index.find(p => p.project_id === pid)).filter(Boolean);
  if (count) count.textContent = rows.length ? '已选择 ' + rows.length + ' 个项目' : '';
  if (bar) bar.style.display = rows.length ? 'flex' : 'none';
  if (del) del.disabled = !rows.length;
  const canArchive = rows.length > 0 && rows.every(p => !p.archived && !p.active_job);
  if (arch) arch.style.display = canArchive ? '' : 'none';
  if (selAll) {
    const selectable = PM.index.slice(0, PM.shown).filter(p => !p.active_job);
    const selected = selectable.filter(p => PM.selected.has(p.project_id));
    selAll.checked = selectable.length > 0 && selected.length === selectable.length;
    selAll.indeterminate = selected.length > 0 && selected.length < selectable.length;
  }
}
function pmAskBulkDelete(){
  if (!PM.selected.size) return;
  pmShowModal(`<h3>删除所选项目？</h3>
    <div class="pm-modal-name">共 ${PM.selected.size} 个项目</div>
    <div class="pm-modal-body">将删除配置、时间线、草稿、成片、任务记录和项目目录，此操作不可撤销。</div>
    <div class="pm-modal-actions">
      <button class="btn-secondary btn-sm" onclick="pmCloseModal()">取消</button>
      <button class="btn-sm pm-act-danger" onclick="pmBulkAction('delete')">删除所选</button>
    </div>`);
}
async function pmBulkAction(action){
  const ids = [...PM.selected];
  if (!ids.length) return;
  pmCloseModal();
  const r = await apiPost('/api/bulk_action', {resource:'projects', action, ids});
  if (!r.ok) { editorToast('批量操作未完成：' + (r.error || ''), true); return; }
  PM.selected.clear();
  await loadProjects();
  editorToast(action === 'delete' ? '已删除所选项目' : '已归档所选项目');
}

function pmShowMore(){
  PM.shown += PM_PAGE;
  renderProjectList();
}

/** 筛选 chips（§8：全部/待处理/进行中/已完成/失败/已归档/旧版）。 */
function pmSetFilter(f){
  PM.filter = f;
  document.querySelectorAll('#pm-filters button').forEach(b =>
    b.classList.toggle('chip-on', b.dataset.f === f));
  loadProjects();
}
function pmSearch(input){
  PM.query = (input.value || '').trim();
  loadProjects();
}

/** 项目动作统一入口（§61）：POST /api/project_action → 重拉 Index。
 *  §54 操作中态：动作执行期间该行按钮全禁用 + 行显示「处理中」，防重复点击。 */
async function projectAction(pid, action, args){
  if (PM.busy[pid]) { editorToast('这个项目有操作正在进行中，请稍候', true); return {ok: false}; }
  PM.busy[pid] = action;
  const rowBtns = document.querySelectorAll(`.pm-row`);
  renderProjectList();  // busy 态重渲染（按钮禁用）
  try {
    const r = await apiPost('/api/project_action',
      Object.assign({project_id: pid, action}, args || {}));
    if (!r.ok) {
      // §55 操作反馈：发生了什么/数据有没有变化/接下来怎么办
      editorToast(`未完成：${r.error || '操作失败'}${r.code === 'ACTIVE_JOB' ? '（请先等待任务结束）' : ''}`, true);
      return r;
    }
    await loadProjects();  // §61 成功后重拉 Index，不在前端本地改列表
    return r;
  } finally {
    delete PM.busy[pid];
    renderProjectList();
  }
}

// ---- 项目行更多菜单 ----
function _pmMenuItems(p){
  const actions = p.actions || {};
  return [
    p.legacy
      ? `<button class="btn-secondary btn-sm" onclick="pmCloseRowMenu();pmPreviewLegacy('${esc(p.project_id)}')">预览旧成品</button>`
      : (actions.preview && p.has_final ? `<button class="btn-secondary btn-sm" onclick="pmCloseRowMenu();pmPreviewFinal('${esc(p.project_id)}')">预览</button>` : ''),
    actions.rename ? `<button class="btn-secondary btn-sm" onclick="pmCloseRowMenu();pmAskRename('${esc(p.project_id)}')">重命名</button>` : '',
    actions.duplicate ? `<button class="btn-secondary btn-sm" onclick="pmCloseRowMenu();pmClone('${esc(p.project_id)}')">复制项目</button>` : '',
    p.archived
      ? `<button class="btn-secondary btn-sm" onclick="pmCloseRowMenu();pmRestore('${esc(p.project_id)}')">恢复</button>`
      : (actions.archive ? `<button class="btn-secondary btn-sm" onclick="pmCloseRowMenu();pmArchive('${esc(p.project_id)}')">归档</button>` : ''),
    actions.delete
      ? `<button class="btn-secondary btn-sm pm-act-danger" onclick="pmCloseRowMenu();pmAskDelete('${esc(p.project_id)}')">删除</button>`
      : `<button class="btn-secondary btn-sm" disabled title="项目正在执行任务，当前不能删除">删除</button>`,
  ].filter(Boolean);
}

// ---- 各动作 ----
/** 继续确认（AWAITING_CONFIRM：打开工作台走确认流）。 */
async function continueProject(pid){
  const r = await workspaceOpen(pid);
  if (r && r.ok) editorToast('项目已打开——选中场景查看分镜，或用下方指令栏直接修改后渲染');
}
async function renameProject(pid){
  const input = document.getElementById('pm-rename-input');
  const name = (input ? input.value : '').trim();
  if (!name) { editorToast('项目名不能为空', true); return; }
  const r = await projectAction(pid, 'rename', {new_name: name});
  if (r.ok) {
    pmCloseModal();
    editorToast('✓ 项目已重命名');
  }
}
async function pmClone(pid){
  editorToast('复制项目中……');
  const r = await projectAction(pid, 'clone');
  if (r.ok) editorToast(`✓ 已创建副本（${r.project_id}），原项目不变`);
}
async function pmArchive(pid){
  const r = await projectAction(pid, 'archive');
  if (r.ok) editorToast('✓ 项目已归档（可在「已归档」筛选中找到并恢复）');
}
async function pmRestore(pid){
  const r = await projectAction(pid, 'restore');
  if (r.ok) editorToast('✓ 项目已恢复');
}
async function convertLegacyProject(pid){
  editorToast('正在转换为可编辑项目……');
  const r = await projectAction(pid, 'convert');
  if (r.ok) {
    editorToast(`✓ 已生成可编辑项目（${r.project_id}），旧项目原样保留`);
    workspaceOpen(r.project_id);
  }
}
/** FAILED 项目「重新执行」：进工作台看错误 + 一键重渲染。 */
function pmReopenFailed(pid){
  workspaceOpen(pid);
  if (typeof editorPreflight === 'function') setTimeout(editorPreflight, 600);
}
/** 预览成品（中央 preview 语义：项目页就地播放，不跳页）。 */
async function pmPreviewFinal(pid){
  const info = await apiGet('/api/projects/' + encodeURIComponent(pid) + '/draft_info');
  if (info && info.final_path) {
    const box = document.getElementById('pm-preview-box');
    if (box) {
      box.style.display = '';
      box.innerHTML = `<b>成品预览：${esc(pid)}</b>
        <video controls src="/file?path=${encodeURIComponent(info.final_path)}"></video>`;
      box.scrollIntoView({behavior: 'smooth'});
    }
  } else {
    editorToast('该项目还没有可预览的成品', true);
  }
}
/** 旧版项目「预览旧成品」（legacy 也可能有 final.mp4）。 */
async function pmPreviewLegacy(pid){
  const info = await apiGet('/api/projects/' + encodeURIComponent(pid) + '/draft_info');
  if (info && info.final_path) {
    pmPreviewFinal(pid);
  } else {
    editorToast('该旧版项目没有留下成品文件（可转换后再生成）', true);
  }
}

// ---- 删除（§6.4 项目级 Modal，不用 confirm()）----
function pmAskDelete(pid){
  const p = PM.index.find(x => x.project_id === pid);
  const name = p ? (p.name || pid) : pid;
  pmShowModal(`
    <h3>删除项目？</h3>
    <div class="pm-modal-name">「${esc(name)}」</div>
    <div class="pm-modal-body">此操作会删除：
      <ul>
        <li>Composition（时间线与所有编辑版本）</li>
        <li>素材引用</li>
        <li>生成结果（草稿与成片）</li>
        <li>日志与任务记录</li>
      </ul>
      <div class="hint">删除不可撤销。如只是想收起来，用「归档」。</div>
    </div>
    <div class="pm-modal-actions">
      <button class="btn-secondary btn-sm" onclick="pmCloseModal()">取消</button>
      <button class="btn-sm pm-act-danger" onclick="deleteProject('${esc(pid)}')">删除项目</button>
    </div>`);
}
async function deleteProject(pid){
  const r = await projectAction(pid, 'delete');
  if (r.ok) {
    pmCloseModal();
    editorToast('✓ 项目已删除（配置/时间线/草稿/成片已一并清理）');
  }
}

// ---- 重命名（内联 Modal 输入）----
function pmAskRename(pid){
  const p = PM.index.find(x => x.project_id === pid);
  const name = p ? (p.name || '') : '';
  pmShowModal(`
    <h3>重命名项目</h3>
    <div class="pm-modal-body">
      <input type="text" id="pm-rename-input" value="${esc(name)}" placeholder="项目名"
        onkeydown="if(event.key==='Enter')renameProject('${esc(pid)}');if(event.key==='Escape')pmCloseModal()">
    </div>
    <div class="pm-modal-actions">
      <button class="btn-secondary btn-sm" onclick="pmCloseModal()">取消</button>
      <button class="btn-sm" onclick="renameProject('${esc(pid)}')">保存</button>
    </div>`);
  const input = document.getElementById('pm-rename-input');
  if (input) { input.focus(); input.select(); }
}

// ---- 项目行更多菜单浮层 ----
function pmCloseRowMenu(){
  const menu = document.getElementById('pm-row-menu');
  if (menu) menu.remove();
  document.removeEventListener('pointerdown', pmRowMenuOutside);
}
function pmRowMenuOutside(event){
  const menu = document.getElementById('pm-row-menu');
  if (menu && !menu.contains(event.target)) pmCloseRowMenu();
}
function pmRowMenu(event, pid){
  event.stopPropagation();
  pmCloseRowMenu();
  const p = PM.index.find(x => x.project_id === pid);
  if (!p) return;
  const btn = event.currentTarget;
  const menu = document.createElement('div');
  menu.id = 'pm-row-menu';
  menu.className = 'card';
  menu.style.cssText = 'position:fixed;z-index:1100;min-width:150px;padding:6px;display:flex;flex-direction:column;gap:4px;box-shadow:0 10px 28px rgba(0,0,0,.25)';
  menu.innerHTML = _pmMenuItems(p).join('');
  document.body.appendChild(menu);
  const rect = btn.getBoundingClientRect();
  const mw = menu.offsetWidth;
  const mh = menu.offsetHeight;
  let left = rect.right - mw;
  let top = rect.bottom + 6;
  if (left < 8) left = 8;
  if (left + mw > window.innerWidth - 8) left = window.innerWidth - mw - 8;
  if (top + mh > window.innerHeight - 8) top = rect.top - mh - 6;
  menu.style.left = left + 'px';
  menu.style.top = Math.max(8, top) + 'px';
  setTimeout(() => document.addEventListener('pointerdown', pmRowMenuOutside), 0);
}

// ---- 通用 Modal（§32 Modal 组件：全系统同一种弹窗）----
function pmShowModal(inner){
  pmCloseModal();
  const mask = document.createElement('div');
  mask.id = 'pm-modal-mask';
  mask.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:900;' +
    'display:flex;align-items:center;justify-content:center';
  const card = document.createElement('div');
  card.id = 'pm-modal';
  card.className = 'card';
  card.style.cssText = 'max-width:420px;width:92%;padding:20px';
  card.innerHTML = inner;
  mask.appendChild(card);
  mask.addEventListener('click', ev => { if (ev.target === mask) pmCloseModal(); });
  document.body.appendChild(mask);
}
function pmCloseModal(){
  const mask = document.getElementById('pm-modal-mask');
  if (mask) mask.remove();
}

/** 项目详情抽屉（行名称点击展开：§5 打开/预览/继续处理）。 */
async function pmOpenDetail(pid){
  const box = document.getElementById('pm-detail-box');
  if (!box) return;
  box.style.display = '';
  box.innerHTML = '<div class="hint">加载项目详情……</div>';
  try {
    const j = await apiGet('/api/project_detail?project_id=' + encodeURIComponent(pid));
    if (j.error) throw new Error(j.error);
    const sb = j.storyboard || {};
    const sceneRows = (sb.scenes || []).map(s =>
      `<div class="pm-detail-scene"><b>${esc(s.scene_id || '')}</b> · ${(s.duration || 0).toFixed(1)}s<div>${esc(s.text || '')}</div></div>`).join('');
    box.innerHTML = `
      <h3 style="font-size:15px">${esc(j.name || pid)}</h3>
      <div class="hint">${pmStatusCn(j)} · ${sb.scene_count || '?'} 场景 · ${sb.total_duration || '?'}s
        ${j.has_final ? ' · 有成品' : ''}${j.has_draft ? ' · 有草稿' : ''}</div>
      <div class="pm-detail-scenes">${sceneRows || '<div class="hint">无分镜数据</div></div>'}
      <div class="btn-row">
        ${j.editable ? `<button class="btn-sm" onclick="workspaceOpen('${esc(pid)}')">打开工作台</button>` : ''}
        ${j.has_final ? `<button class="btn-secondary btn-sm" onclick="pmPreviewFinal('${esc(pid)}')">预览成品</button>` : ''}
        <button class="btn-secondary btn-sm" onclick="loadProjects()">刷新列表</button>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="err-box">详情加载失败：${esc(e.message)}</div>`;
  }
}

Object.assign(window, {
  loadProjects, renderProjectList, pmSetFilter, pmSearch,
  projectAction, renameProject, continueProject, pmClone, pmArchive, pmRestore,
  convertLegacyProject, pmReopenFailed, pmPreviewFinal, pmPreviewLegacy,
  pmAskDelete, deleteProject, pmAskRename, pmShowModal, pmCloseModal,
  pmShowMore, pmOpenDetail, pmRowMenu, pmCloseRowMenu,
  pmToggleSelected, pmToggleAll, pmRefreshBulkBar, pmAskBulkDelete, pmBulkAction,
  PM, PM_STATUS_CN, PM_PAGE,
});
