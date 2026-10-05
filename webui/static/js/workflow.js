// §63 workflow.js：WorkflowRun UI State（任务中心的事实源渲染层）。
// 业务执行仍在后端（§63：Step Rendering/Resume/Retry/Cancel/Open Project——
// 全部调用后端 WorkflowRun 状态与动作端点，本文件只做 UI 状态渲染）。
//
// §46 任务中心 = 项目页顶部的「任务」抽屉（不新增第四个顶层 Tab）：
//   正在处理（RUNNING + 当前步骤人话 + 进度）/ 已完成 / 失败（查看原因 + 重试）。

window.WFUI = {drawerOpen: false, runs: [], refreshing: null, selected: new Set()};

/** §46 任务抽屉开关（项目页顶部）。 */
function taskDrawerToggle(){
  const d = document.getElementById('task-drawer');
  if (!d) return;
  WFUI.drawerOpen = !WFUI.drawerOpen;
  d.style.display = WFUI.drawerOpen ? '' : 'none';
  const btn = document.getElementById('pm-task-btn');
  if (btn) btn.classList.toggle('tl-switch-on', WFUI.drawerOpen);
  if (WFUI.drawerOpen) taskRefresh();
}

/** §47 拉取全部 WorkflowRun（GET /api/workflows）→ 三区渲染。 */
async function taskRefresh(){
  const box = document.getElementById('task-drawer-list');
  if (!box) return;
  box.innerHTML = '<div class="hint">加载任务……</div>';
  try {
    const r = await apiGet('/api/workflows?limit=50');
    WFUI.runs = (r && r.runs) || [];
  } catch (e) {
    box.innerHTML = '<div class="err-box">任务读取失败：' + esc(String(e).slice(0, 60)) + '</div>';
    return;
  }
  renderTaskDrawer();
  // RUNNING 存在 → 5s 轮询真实状态（§47 不能纯动画）
  if (WFUI.runs.some(x => x.status === 'RUNNING') && !WFUI.refreshing) {
    WFUI.refreshing = setInterval(() => {
      if (!WFUI.drawerOpen) { clearInterval(WFUI.refreshing); WFUI.refreshing = null; return; }
      taskRefresh();
    }, 5000);
  }
}

/** §47/§52 单 run 详情（步骤真实状态）。 */
async function taskShowDetail(runId){
  const box = document.getElementById('task-drawer-list');
  if (!box) return;
  box.innerHTML = '<div class="hint">读取任务步骤……</div>';
  let run = null;
  try {
    run = await apiGet('/api/workflows/' + encodeURIComponent(runId));
  } catch (e) { /* fallthrough */ }
  if (!run || run.error) { box.innerHTML = '<div class="err-box">任务不存在或已清理</div>'; return; }
  const stepRows = (run.steps || []).map(s => {
    const st = {DONE: ['✓', 'cap-ok'], RUNNING: ['···', 'cap-warn'],
                PENDING: ['—', ''], FAILED: ['✗', 'cap-off'], SKIPPED: ['⊘', '']}[s.status] || ['?', ''];
    const label = (run.labels || {})[s.id] || s.id;
    return `<div class="hint"><b class="${st[1]}">${st[0]}</b> ${esc(label)} <span class="hint">(${esc(s.status)})</span></div>`;
  }).join('');
  const result = run.result || {};
  box.innerHTML = `
    <div class="btn-row" style="align-items:center;margin-top:0">
      <b>${esc(run.kind)}</b> <span class="hint">${esc(run.status)}</span>
      <span class="tb-sep"></span>
      <button class="btn-secondary btn-sm" onclick="taskRefresh()">返回列表</button>
      ${result.project_id ? `<button class="btn-sm" onclick="taskOpenProject('${esc(result.project_id)}')">打开项目</button>` : ''}
    </div>
    ${run.error ? `<div class="err-box">失败原因：${esc(run.error.slice(0, 160))}</div>` : ''}
    <div style="margin-top:6px">${stepRows || '<div class="hint">无步骤记录</div>'}</div>`;
}

/** §46 失败任务「查看」→ 详情（含失败步骤定位）。 */
function taskOpenProject(pid){
  if (typeof workspaceOpen === 'function') workspaceOpen(pid);
  taskDrawerToggle();
}

/** §47 三区渲染：正在处理 / 已完成 / 失败。 */
async function taskBulkCleanup(){
  const ids=[...WFUI.selected];
  if(!ids.length)return;
  const r=await apiPost('/api/bulk_action',{resource:'workflow_runs',action:'delete',ids});
  if(!r.ok){editorToast('任务清理未完成：'+(r.error||''),true);return;}
  WFUI.selected.clear(); taskRefresh();
  editorToast('已清理 '+(r.deleted||ids).length+' 条已结束任务记录');
}
function taskToggleAllTerminal(checked){
  const terminal=WFUI.runs.filter(r=>['COMPLETED','FAILED','INTERRUPTED','CANCELLED'].includes(r.status));
  for(const r of terminal){if(checked)WFUI.selected.add(r.run_id);else WFUI.selected.delete(r.run_id);}
  renderTaskDrawer();
}
function taskToggleSelected(runId,checked){
  if(checked)WFUI.selected.add(runId);else WFUI.selected.delete(runId);
  renderTaskDrawer();
}

function renderTaskDrawer(){
  const box = document.getElementById('task-drawer-list');
  if (!box) return;
  const running = WFUI.runs.filter(r => r.status === 'RUNNING');
  const waiting = WFUI.runs.filter(r => r.status === 'WAITING_CONFIRMATION');
  const done = WFUI.runs.filter(r => r.status === 'COMPLETED');
  const failed = WFUI.runs.filter(r => r.status === 'FAILED');
  const row = (r, extra) => `
    <div class="proj-row"><span><b>${esc(r.kind)}${r.project_id ? ' · ' + esc(r.project_id) : ''}</b>
      <span class="hint">${extra}</span></span>
      <span class="btn-row">
        <button class="btn-secondary btn-sm" onclick="taskShowDetail('${esc(r.run_id)}')">查看</button>
      </span></div>`;
  const section = (title, items, emptyText) => `
    <div style="margin-top:8px"><b style="font-size:13px">${title}</b>
    ${items.length ? items.join('') : `<div class="hint">${emptyText}</div>`}</div>`;
  const terminal=[...WFUI.runs].filter(r=>['COMPLETED','FAILED','INTERRUPTED','CANCELLED'].includes(r.status));
  const terminalIds=terminal.map(r=>r.run_id);
  WFUI.selected.forEach(id=>{if(!terminalIds.includes(id))WFUI.selected.delete(id);});
  const allSelected=terminal.length>0&&terminal.every(r=>WFUI.selected.has(r.run_id));
  const controls=terminal.length?`
    <div class="btn-row" style="align-items:center;margin:6px 0">
      <label style="margin:0"><input type="checkbox" ${allSelected?'checked':''} onchange="taskToggleAllTerminal(this.checked)"> 全选已结束</label>
      <span class="hint">${WFUI.selected.size?'已选择 '+WFUI.selected.size+' 条':''}</span>
      ${WFUI.selected.size?'<button class="btn-secondary btn-sm" onclick="taskBulkCleanup()">清理所选</button>':''}
    </div>`:''; 
  box.innerHTML=controls+
    section('正在处理',running.map(r=>row(r,(r.current?'步骤：'+esc(r.current)+'……':'排队中'))),'没有进行中的任务')+
    section('等待确认',waiting.map(r=>row(r,'分镜/草稿等待你确认')),'无')+
    section('失败',failed.map(r=>'<div class="proj-row"><span style="width:28px"><input type="checkbox" '+(WFUI.selected.has(r.run_id)?'checked':'')+' onchange="taskToggleSelected(\''+esc(r.run_id)+'\',this.checked)" onclick="event.stopPropagation()"></span><span style="flex:1"><b>'+esc(r.kind)+(r.project_id?' · '+esc(r.project_id):'')+'</b><span class="hint">'+esc((r.error||'').slice(0,50)||'查看原因')+'</span></span><button class="btn-secondary btn-sm" onclick="taskShowDetail(\''+esc(r.run_id)+'\')">查看</button></div>'),'没有失败任务')+
    section('已完成',done.slice(0,10).map(r=>'<div class="proj-row"><span style="width:28px"><input type="checkbox" '+(WFUI.selected.has(r.run_id)?'checked':'')+' onchange="taskToggleSelected(\''+esc(r.run_id)+'\',this.checked)" onclick="event.stopPropagation()"></span><span style="flex:1"><b>'+esc(r.kind)+(r.project_id?' · '+esc(r.project_id):'')+'</b><span class="hint">'+new Date((r.updated_at||0)*1000).toLocaleString()+'</span></span><button class="btn-secondary btn-sm" onclick="taskShowDetail(\''+esc(r.run_id)+'\')">查看</button></div>'),'暂无');
}

Object.assign(window, {taskDrawerToggle, taskRefresh, taskShowDetail, taskOpenProject, renderTaskDrawer, taskBulkCleanup, taskToggleAllTerminal, taskToggleSelected, WFUI});
