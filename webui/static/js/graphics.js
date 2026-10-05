// §22/§62 GFX = Graphic Proposal（方案《FRONTEND_DEEP_UNIFICATION》）：
// GFX 只做「方案生成入口」：文案 → 方案预览 → 确认 → Composition → Workspace。
// 进入工作台后 GFX 不再拥有独立编辑语义（不出现第二条编辑器/渲染链 §59）。
// compile html / source html / frame seek 只在开发者模式。

let GFX = {plan:null, blocks:[], previewHtml:null, content_type:'knowledge'};

async function gfxEnsureBlocks(){
  if (!GFX.blocks.length) {
    try {
      GFX.blocks = await apiGet('/api/catalog/blocks');
    } catch(e) { GFX.blocks = []; }
  }
}

function gfxSetType(t, btn){
  GFX.content_type = t;
  document.querySelectorAll('.gfx-type-btn').forEach(b=>b.classList.remove('on'));
  if (btn) btn.classList.add('on');
}

async function gfxMakePlan(){
  const script = document.getElementById('gfx-script')
    ? document.getElementById('gfx-script').value.trim()
    : (document.getElementById('cf-script') ? document.getElementById('cf-script').value.trim() : '');
  const status = document.getElementById('gfx-status');
  if (!script) { status.textContent = '请先输入文案'; return; }
  await gfxMakePlanFrom(script);
}

/** 方案生成（从新建面板或旧入口共用；script 显式传入）。 */
async function gfxMakePlanFrom(script){
  const status = document.getElementById('gfx-status');
  status.textContent = '生成方案中……';
  await gfxEnsureBlocks();
  const aspectEl = document.getElementById('gfx-aspect');
  const [w, h] = (aspectEl ? aspectEl.value : '1080x1920').split('x');
  const r = await apiPost('/api/graphics/plan', {
    script,
    content_type: GFX.content_type,
    style_id: (document.getElementById('gfx-style') || {}).value || 'editorial',
    target_duration: parseFloat((document.getElementById('gfx-duration') || {}).value || 30),
    width: parseInt(w), height: parseInt(h),
  });
  if (!r.ok) { status.textContent = (r.error || '生成失败'); return; }
  GFX.plan = r.plan;
  GFX.previewHtml = null;
  status.textContent = `方案已生成：${GFX.plan.scene_count} 个场景 · ${GFX.plan.total_duration}s（逐场景审阅后「确认进入工作台」）`;
  gfxRenderPlan();
}

function gfxRenderPlan(){
  const box = document.getElementById('gfx-plan-box');
  if (!GFX.plan) { box.innerHTML = ''; return; }
  const opts = (sel) => GFX.blocks
    .filter(b => b.category === GFX.plan.category)
    .map(b => `<option value="${b.id}"${b.id===sel?' selected':''}>${esc(b.name)}</option>`).join('');
  const layoutOpts = (sel) => ['center','left','split'].map(l =>
    `<option value="${l}"${(sel||'center')===l?' selected':''}>${{center:'居中',left:'左对齐',split:'分栏'}[l]}</option>`).join('');
  const cards = GFX.plan.scenes.map((s, i) => {
    const blk = GFX.blocks.find(b=>b.id===s.block_id) || {motion:'graphics.fade-up'};
    const motionCn = {fade_up: '淡入上浮', scale_punch: '缩放强调', slide_left: '左滑入场',
                      word_relay: '逐词接力', rise_soft: '轻柔升起'};
    const motionSel = '<select style="flex:1.4" onchange="GFX.plan.scenes['+i+'].motion_id=this.value;GFX.previewHtml=null">'+
      ['fade-up','scale-punch','slide-left','word-relay','rise-soft']
        .map(m=>'<option value="graphics.'+m+'"'+((s.motion_id||blk.motion)==='graphics.'+m?' selected':'')+'>'+(motionCn[m.replace(/-/g,'_')] || m)+'</option>').join('')+'</select>';
    const intentMap = {problem_title:'吸引注意', wrong_belief:'纠正常见误解', cause:'解释机制',
      contrast:'制造对比', process:'展示步骤', concept:'建立概念', statistic:'数字冲击',
      definition:'给出定义', timeline:'历史脉络', formula:'公式推理', code:'技术演示',
      conclusion:'收束总结', problem:'指出痛点', product_hero:'建立第一印象',
      input:'说明输入', processing:'说明处理', output:'展示产出', benefit:'量化收益',
      feature:'突出卖点', proof:'建立信任', pricing:'价格对比', cta:'引导行动'};
    const intent = s.visual_intent || intentMap[s.role] || '表达内容';
    const carryCn = CONT_CN;
    const prev = i > 0 ? GFX.plan.scenes[i-1] : null;
    const carryHtml = prev
      ? `<div class="hint" style="margin:2px 0;padding-left:6px;border-left:2px solid var(--warning)">`+
        `从上一镜延续：${esc((s.carry_from || prev.text||'').slice(0,16))} · 过渡方式 `+
        `<select style="width:96px;font-size:11px" onchange="GFX.plan.scenes[${i}].continuity=this.value">`+
        Object.entries(carryCn).map(([t, l])=>`<option value="${t}"${(s.continuity||'hard_cut')===t?' selected':''}>${l}</option>`).join('')+
        `</select></div>`
      : '';
    return `<div class="scene-card">
      <div class="scene-head">
        <span class="scene-id">${esc(s.id)}</span>
        <span class="scene-role">${esc(s.role)}</span>
        <span class="scene-dur">${s.duration.toFixed(1)}s</span>
        <button class="scene-move" onclick="gfxRemoveScene(${i})">删除</button>
      </div>
      <div class="scene-text">旁白：${esc(s.text)}</div>
      <div class="scene-asset">视觉意图：${esc(intent)}</div>
      ${carryHtml}
      <div class="btn-row" style="flex-wrap:wrap">
        <span class="hint">视觉块</span>
        <select style="flex:2" onchange="GFX.plan.scenes[${i}].block_id=this.value;GFX.plan.scenes[${i}].motion_id=null;GFX.previewHtml=null">${opts(s.block_id)}</select>
        <span class="hint">动效</span>
        ${motionSel}
      </div>
      <div class="btn-row" style="flex-wrap:wrap">
        <span class="hint">布局</span>
        <select style="flex:1" onchange="GFX.plan.scenes[${i}].layout=this.value;GFX.previewHtml=null">${layoutOpts(s.layout)}</select>
        <button class="btn-secondary btn-sm" onclick="gfxGenAsset(${i})">生成素材</button>
      </div>
    </div>`;
  }).join('');
  box.innerHTML = `<div class="panel" style="padding:14px"><b>你的创作方案</b> <span class="hint">（每场景可换视觉/动效/布局；确认后进入工作台继续精修——修改都会记入版本历史）</span>
    <div class="btn-row">
      <button class="btn-sm" onclick="gfxApplyToWorkspace()">确认方案，进入工作台</button>
      <button class="btn-secondary btn-sm" onclick="gfxPreview()">预览动效</button>
      <span class="hint" id="gfx-apply-status"></span>
    </div>
    <div class="scene-list" style="max-height:400px">${cards}</div></div>`;
}

// §7/FE-003：proposal 确认 → 建项目 → workspaceOpen（§56 pollUntil，不再 sleep 400ms）
async function gfxApplyToWorkspace(){
  const status = document.getElementById('gfx-apply-status') || document.getElementById('gfx-status');
  if (!GFX.plan) { status.textContent = '先生成创作方案'; return; }
  status.textContent = '正在创建可编辑项目……';
  const [w, h] = document.getElementById('gfx-aspect').value.split('x');
  const script = (document.getElementById('gfx-script') || document.getElementById('cf-script') || {}).value || '';
  const r = await apiPost('/api/graphics/plan_to_project', {
    plan: GFX.plan,
    script: script,
    size: w + 'x' + h,
  });
  if (!r.ok) { status.textContent = (r.error || '创建失败'); return; }
  const wo = await workspaceOpen(r.project_id);
  if (wo && wo.ok) {
    editorToast(`方案已转为可编辑项目（${r.scene_count} 场景）。修改都会记入版本历史，可撤销。`);
  } else {
    status.textContent = '项目已创建，但打开工作台失败——去「项目」页打开它';
  }
}

async function gfxGenAsset(i){
  try {
    const r = await apiPost('/api/gfx/gen_asset', {scene_index: i, plan: GFX.plan});
    if (!r.ok) throw new Error(r.error || '生成失败');
    GFX.plan.scenes[i].source = 'Generated Image';
    GFX.previewHtml = null;
    gfxRenderPlan();
  } catch(e) {
    editorToast('生成素材不可用：' + e.message + '（需在「设置 → 模型」为生图角色配置密钥；可先用程序化视觉块）', true);
  }
}

async function gfxPreview(){
  const status = document.getElementById('gfx-status');
  if (!GFX.plan) { status.textContent = '先生成创作方案'; return; }
  if (!GFX.previewHtml) {
    status.textContent = '编译预览中……';
    const r = await apiPost('/api/graphics/preview', {plan: GFX.plan});
    if (!r.ok) { status.textContent = (r.error || '预览失败'); return; }
    GFX.previewHtml = r.html;
  }
  const [w, h] = document.getElementById('gfx-aspect').value.split('x');
  const scale = Math.min(1, 420 / parseInt(h));
  const box = document.getElementById('gfx-preview-box');
  // §22 普通用户只见视觉预览；源码视图开发者模式限定
  box.innerHTML = `<div class="panel" style="padding:14px"><b>动效预览</b> <span class="hint">（与正式渲染同一份画面——预览即所得）</span>
    <div class="btn-row"><button class="btn-secondary btn-sm" onclick="gfxPlay()">播放</button>
    <button class="btn-secondary btn-sm" onclick="gfxPause()">暂停</button></div>
    <div class="btn-row"><span class="hint">进度</span>
    <input type="range" id="gfx-seek-slider" min="0" max="100" value="0" style="flex:1" oninput="gfxSeekTo(this.value)">
    <span class="hint" id="gfx-seek-label">0%</span></div>
    <iframe id="gfx-preview-frame" sandbox="allow-scripts" style="width:${parseInt(w)*scale}px;height:${parseInt(h)*scale}px;transform-origin:top left;border:1px solid var(--border);border-radius:8px;background:var(--bg);display:block"></iframe>
    ${DEV.mode ? `<details class="dev-only"><summary>编译产物 HTML（开发者模式）</summary>
      <textarea style="min-height:160px;font-family:Consolas,monospace;font-size:12px" readonly>${esc(GFX.previewHtml.slice(0, 5000))}</textarea></details>` : ''}
    </div>`;
  const frame = document.getElementById('gfx-preview-frame');
  frame.srcdoc = GFX.previewHtml;
  frame.addEventListener('load', function(){
    frame.contentWindow.postMessage({type:'gf-enable-select'}, '*');
  });
  status.textContent = '预览就绪（满意后「确认方案，进入工作台」）';
  box.scrollIntoView({behavior:'smooth'});
}
function gfxPlay(){
  const frame = document.getElementById('gfx-preview-frame');
  if (frame) frame.contentWindow.postMessage({type:'gf-play'}, '*');
}
function gfxPause(){
  const frame = document.getElementById('gfx-preview-frame');
  if (frame) frame.contentWindow.postMessage({type:'gf-pause'}, '*');
}
function gfxSeekTo(pct){
  const frame = document.getElementById('gfx-preview-frame');
  if (!frame) return;
  const fps = 30;
  const totalFrames = Math.max(1, Math.round((GFX.plan ? GFX.plan.total_duration : 0) * fps));
  const f = Math.round(totalFrames * (parseFloat(pct) / 100));
  frame.contentWindow.postMessage({type:'gf-seek', frame: f}, '*');
  const label = document.getElementById('gfx-seek-label');
  if (label) label.textContent = pct + '%';
}

async function gfxRender(){
  // §22 收口：GFX 无独立成片——统一「确认 → 工作台 → 渲染」
  await gfxApplyToWorkspace();
}

function gfxRemoveScene(i){
  // 方案内删场景（proposal 本地态，未入 Composition——确认时才整体转项目）
  GFX.plan.scenes.splice(i, 1);
  GFX.plan.scene_count = GFX.plan.scenes.length;
  GFX.plan.total_duration = Math.round(GFX.plan.scenes.reduce((a,s)=>a+s.duration,0)*100)/100;
  GFX.previewHtml = null;
  gfxRenderPlan();
}

// ---------------------------------------------------------------------------
// 首页「最近项目」（§4：封面卡片 + Project Index 同源状态）
// ---------------------------------------------------------------------------
async function loadHome(){
  const box = document.getElementById('home-projects');
  if (!box) return;
  box.innerHTML = '加载中……';
  const r = await apiGet('/api/projects?filter=all&limit=12');
  const projects = (r && !r.error && r.projects) || [];
  const recent = projects.filter(p => !p.archived).slice(0, 6).map(p => {
    const state = pmStatusCn(p);
    const cls = pmStatusCls(p);
    // 封面视频懒加载：preload=none + 鼠标移入才取帧（121 项列表不拖首屏）
    return `<div class="proj-row home-card" onclick="workspaceOpen('${esc(p.project_id)}')">
      ${p.has_final
        ? `<video class="home-poster" data-final="${esc(p.final_path)}" src="" muted preload="none"
             onmouseover="if(!this.src)this.src='/file?path='+encodeURIComponent(this.dataset.final)+'#t=1'"
             onerror="this.style.display='none'"></video>`
        : `<div class="home-poster home-poster-empty">${p.project_id.startsWith('tpl-') ? '模板' : '视频'}</div>`}
      <b>${esc(p.name || p.project_id)}</b>
      <span class="hint">${p.updated_at ? new Date(p.updated_at * 1000).toLocaleString() : ''}</span>
      <span class="${cls}">${state}${p.duration ? ' · ' + p.duration.toFixed(1) + 's' : ''}</span>
    </div>`;
  }).join('');
  box.innerHTML = recent || '<div class="hint">还没有项目，点右上角「新建项目」开始。</div>';
}

// ---- 系列 ----
window.SERIES_UI = window.SERIES_UI || {selected: new Set()};
window.INBOX_UI = window.INBOX_UI || {selected: new Set()};

function seriesToggle(id, checked){ if (checked) SERIES_UI.selected.add(id); else SERIES_UI.selected.delete(id); loadSeries(); }
function seriesToggleAll(checked, ids){ for (const id of ids || []) { if (checked) SERIES_UI.selected.add(id); else SERIES_UI.selected.delete(id); } loadSeries(); }
async function seriesBulkDelete(){
  const ids=[...SERIES_UI.selected]; if(!ids.length) return;
  const r=await apiPost('/api/bulk_action',{resource:'series',action:'delete',ids});
  if(!r.ok){ editorToast('系列批量删除未完成：'+(r.error||''),true); return; }
  SERIES_UI.selected.clear(); loadSeries(); editorToast('已删除所选系列配置（不会删除已生成项目）');
}

async function loadSeries(){
  const box = document.getElementById('series-list');
  if (!box) return;
  try {
    const list = await apiGet('/api/series');
    if (!Array.isArray(list)) throw new Error(list.error || '读取失败');
    const ids=list.map(s=>s.series_id);
    SERIES_UI.selected.forEach(id=>{if(!ids.includes(id)) SERIES_UI.selected.delete(id);});
    box.innerHTML=`
      <div class="btn-row" style="align-items:center;margin-bottom:6px">
        <label style="margin:0"><input type="checkbox" onchange='seriesToggleAll(this.checked, ${JSON.stringify(ids)})'> 全选</label>
        <span class="hint">${SERIES_UI.selected.size ? '已选择 '+SERIES_UI.selected.size+' 个' : ''}</span>
        ${SERIES_UI.selected.size ? '<button class="btn-secondary btn-sm" onclick="seriesBulkDelete()">删除所选系列</button>' : ''}
      </div>`+(list.map(s=>`
      <div class="proj-row">
        <span style="width:28px"><input type="checkbox" ${SERIES_UI.selected.has(s.series_id)?'checked':''} onchange="seriesToggle('${esc(s.series_id)}',this.checked)" onclick="event.stopPropagation()"></span>
        <span style="flex:1"><b>${esc(s.name)}</b><span class="hint">${esc(s.series_id)} · 配音 ${esc(s.spec.voice||'默认')}</span></span>
      </div>`).join('')||'<div class="hint">还没有系列。</div>');
  } catch (e) {
    box.innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
  }
}

async function createSeriesAndBatch(){
  const status = document.getElementById('series-status');
  const name = document.getElementById('series-name').value.trim();
  const scripts = document.getElementById('series-scripts').value.trim();
  if (!name || !scripts) { status.textContent = '请填系列名和多集文案'; return; }
  status.textContent = '创建系列并入队……';
  const r1 = await apiPost('/api/series_create', {name, spec: {
    voice: document.getElementById('series-voice').value || 'zh-CN-XiaoxiaoNeural',
    subtitle_mode: document.getElementById('series-subtitle').value || 'plain',
    watermark: document.getElementById('series-watermark') ? document.getElementById('series-watermark').value : null,
  }});
  if (!r1.ok) { status.textContent = '失败：' + (r1.error || ''); return; }
  const fd = new FormData();
  fd.append('series_id', r1.series_id);
  fd.append('scripts', scripts);
  const vf = document.getElementById('series-videos');
  if (vf && vf.files) for (const f of vf.files) fd.append('videos', f, f.name);
  const j2 = await apiUpload('/api/series_batch', fd);
  if (!j2.ok) { status.textContent = '失败：' + (j2.error || ''); return; }
  status.textContent = `已入队 ${j2.episodes} 集（审阅页可见进度）`;
}

// ---- 收集箱（§27 抽屉）----
async function inboxAdd(kind){
  const content = document.getElementById('inbox-content').value.trim();
  const status = document.getElementById('inbox-status');
  if (!content) { status.textContent = '请输入内容'; return; }
  const r = await apiPost('/api/inbox_add', {kind, content});
  if (!r.ok) { status.textContent = '失败：' + (r.error || ''); return; }
  document.getElementById('inbox-content').value = '';
  status.textContent = '已存入收集箱';
  loadInbox();
}
async function loadInbox(){
  const box = document.getElementById('inbox-list');
  if (!box) return;
  try {
    const list = await apiGet('/api/inbox');
    if (!Array.isArray(list)) throw new Error(list.error || '读取失败');
    const ids=list.map(it=>it.item_id);
    INBOX_UI.selected.forEach(id=>{if(!ids.includes(id)) INBOX_UI.selected.delete(id);});
    const allSelected=ids.length>0&&ids.every(id=>INBOX_UI.selected.has(id));
    box.innerHTML=`
      <div class="btn-row" style="align-items:center;margin-bottom:6px">
        <label style="margin:0"><input type="checkbox" ${allSelected?'checked':''} onchange='inboxToggleAll(this.checked, ${JSON.stringify(ids)})'> 全选</label>
        <span class="hint">${INBOX_UI.selected.size?'已选择 '+INBOX_UI.selected.size+' 条':''}</span>
        ${INBOX_UI.selected.size?'<button class="btn-secondary btn-sm" onclick="inboxBulkDeleteSelected()">删除所选</button><button class="btn-secondary btn-sm" onclick="inboxBulkDismissSelected()">丢弃所选</button>':''}
      </div>`+(list.map(it=>`
      <div class="proj-row">
        <span style="width:28px"><input type="checkbox" ${INBOX_UI.selected.has(it.item_id)?'checked':''} onchange="inboxToggle('${esc(it.item_id)}',this.checked)" onclick="event.stopPropagation()"></span>
        <span style="flex:1">${esc(it.content.slice(0,120))}</span>
        <span class="hint">${it.created_at ? new Date(it.created_at*1000).toLocaleDateString() : ''}</span>
        <button class="btn-secondary btn-sm" onclick="inboxToCreate('${esc(it.item_id)}')">转创作</button>
        <button class="btn-secondary btn-sm" onclick="inboxAction('${esc(it.item_id)}','delete')">删</button>
      </div>`).join('')||'<div class="hint">收集箱为空。</div>');
  } catch (e) {
    box.innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
  }
}
function inboxToggle(id, checked){ if(checked) INBOX_UI.selected.add(id); else INBOX_UI.selected.delete(id); loadInbox(); }
function inboxToggleAll(checked, ids){ for(const id of ids||[]){ if(checked) INBOX_UI.selected.add(id); else INBOX_UI.selected.delete(id); } loadInbox(); }
function inboxBulkDeleteSelected(){ return inboxBulkAction('delete'); }
function inboxBulkDismissSelected(){ return inboxBulkAction('dismissed'); }

async function inboxBulkAction(action){
  const ids=[...INBOX_UI.selected]; if(!ids.length)return;
  const r=await apiPost('/api/bulk_action',{resource:'inbox',action,ids});
  if(!r.ok){editorToast('收集箱批量操作未完成：'+(r.error||''),true);return;}
  INBOX_UI.selected.clear();loadInbox();editorToast(action==='delete'?'已删除所选收集内容':'已丢弃所选收集内容');
}
function inboxToCreate(itemId){
  const row = [...document.querySelectorAll('#inbox-list .proj-row')]
    .find(r => r.innerHTML.includes(itemId));
  const content = row ? row.querySelector('span').textContent : '';
  openCreate();
  const ta = document.getElementById('cf-script');
  if (ta && content) ta.value = content;
  apiPost('/api/inbox_action', {item_id: itemId, action: 'converted'}).catch(()=>{});
}
async function inboxAction(itemId, act){
  const r = await apiPost('/api/inbox_action', {item_id: itemId, action: act});
  if (!r.ok && r.error) editorToast('操作失败：' + r.error, true);
  loadInbox();
}

// ---- 队列（§42 自动化；审阅页消费）----
async function loadQueue(){
  const box = document.getElementById('queue-list');
  if (!box) return;
  try {
    const jobs = await apiGet('/api/jobs');
    if (!Array.isArray(jobs)) throw new Error(jobs.error || '读取失败');
    const st = {waiting:'排队中', running:'生成中', done:'完成', failed:'失败'};
    box.innerHTML = jobs.map(j => `
      <div class="proj-row">
        <b>${esc(j.job_id)}</b>
        <span class="hint">${j.kind === 'series_episode' ? '系列单集 #'+j.spec.episode : '平台变体'}</span>
        <span class="${j.status==='done'?'cap-ok':(j.status==='failed'?'cap-off':(j.status==='running'?'cap-warn':'hint'))}">${st[j.status]||j.status}</span>
        ${j.error ? `<span class="hint" style="color:var(--danger)">${esc(j.error.slice(0,80))}</span>` : ''}
      </div>`).join('') || '<div class="hint">队列为空。</div>';
  } catch (e) {
    box.innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
  }
}

// ---- 多平台发布 ----
async function publishVariants(pid){
  const r = await apiPost('/api/variants_batch', {
    project_id: pid, platforms: ['douyin','bilibili','xhs','square'],
  });
  if (!r.ok) { editorToast('发布失败：' + (r.error || ''), true); return; }
  editorToast(`已入队 ${r.job_ids.length} 个平台变体（审阅页下方可见进度）`);
}

Object.assign(window, {
  gfxEnsureBlocks, gfxSetType, gfxMakePlan, gfxRenderPlan, gfxApplyToWorkspace,
  gfxGenAsset, gfxPreview, gfxPlay, gfxPause, gfxSeekTo, gfxRender, gfxRemoveScene,
  loadHome, loadSeries, createSeriesAndBatch,
  inboxAdd, loadInbox, inboxToCreate, inboxAction, inboxToggle, inboxToggleAll, inboxBulkAction,
  loadQueue, publishVariants, GFX, SERIES_UI, INBOX_UI, seriesToggle, seriesToggleAll, seriesBulkDelete,
});
