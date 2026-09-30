// ===========================================================================
let GFX = {plan:null, blocks:[], previewHtml:null, content_type:'knowledge'};

async function gfxEnsureBlocks(){
  if (!GFX.blocks.length) {
    try {
      // §64 正名端点 /api/catalog/blocks（含 motion 字段；旧 /api/graphics/blocks 仅兼容）
      GFX.blocks = await (await fetch('/api/catalog/blocks')).json();
    } catch(e) { GFX.blocks = []; }
  }
}

function gfxSetType(t, btn){
  GFX.content_type = t;
  document.querySelectorAll('.gfx-type-btn').forEach(b=>b.classList.remove('on'));
  btn.classList.add('on');
}

async function gfxMakePlan(){
  const script = document.getElementById('gfx-script').value.trim();
  const status = document.getElementById('gfx-status');
  if (!script) { status.textContent = '请先输入文案'; return; }
  status.textContent = '生成方案中……';
  await gfxEnsureBlocks();
  const [w, h] = document.getElementById('gfx-aspect').value.split('x');
  const r = await apiPost('/api/graphics/plan', {
    script,
    content_type: GFX.content_type,
    style_id: document.getElementById('gfx-style').value,
    target_duration: parseFloat(document.getElementById('gfx-duration').value),
    width: parseInt(w), height: parseInt(h),
  });
  if (!r.ok) { status.textContent = '❌ ' + (r.error || '生成失败'); return; }
  GFX.plan = r.plan;
  GFX.previewHtml = null;
  status.textContent = `✅ 方案已生成：${GFX.plan.scene_count} 个场景 · ${GFX.plan.total_duration}s（下方逐场景审阅，换视觉块后预览/渲染）`;
  gfxRenderPlan();
}

// §34 Creative Plan Review / §35 Storyboard 卡片：
// Narration / Visual Intent / Visual Form / Block / Motion / Source / Duration
// 五操作：换视觉 / 换动画 / 换素材 / 换布局 / 生成素材（§34 逐 Scene 允许）。
function gfxRenderPlan(){
  const box = document.getElementById('gfx-plan-box');
  if (!GFX.plan) { box.innerHTML = ''; return; }
  // 视觉块下拉：catalog 域内全量（换视觉=换 Block）
  const opts = (sel) => GFX.blocks
    .filter(b => b.category === GFX.plan.category)
    .map(b => `<option value="${b.id}"${b.id===sel?' selected':''}>${esc(b.name)}（${esc(b.id)}）</option>`).join('');
  // 布局变体（§34 换布局）：同块的排版取向
  const layoutOpts = (sel) => ['center','left','split'].map(l =>
    `<option value="${l}"${(sel||'center')===l?' selected':''}>${{center:'居中',left:'左对齐',split:'分栏'}[l]}</option>`).join('');
  const cards = GFX.plan.scenes.map((s, i) => {
    const blk = GFX.blocks.find(b=>b.id===s.block_id) || {motion:'graphics.fade-up'};
    const motionSel = '<select style="flex:1.4" onchange="GFX.plan.scenes['+i+'].motion_id=this.value;GFX.previewHtml=null">'+
      ['fade-up','scale-punch','slide-left','word-relay','rise-soft']
        .map(m=>'<option value="graphics.'+m+'"'+((s.motion_id||blk.motion)==='graphics.'+m?' selected':'')+'>'+m+'</option>').join('')+'</select>';
    // §35 Visual Intent / Visual Form：由场景角色与所选块推导（可编辑）
    const intentMap = {problem_title:'吸引注意', wrong_belief:'纠正常见误解', cause:'解释机制',
      contrast:'制造对比', process:'展示步骤', concept:'建立概念', statistic:'数字冲击',
      definition:'给出定义', timeline:'历史脉络', formula:'公式推理', code:'技术演示',
      conclusion:'收束总结', problem:'指出痛点', product_hero:'建立第一印象',
      input:'说明输入', processing:'说明处理', output:'展示产出', benefit:'量化收益',
      feature:'突出卖点', proof:'建立信任', pricing:'价格对比', cta:'引导行动'};
    const formMap = {title:'Typography', concept_card:'Concept Card', comparison:'Duopanel',
      cause_effect:'Flow', process:'Flow', timeline:'Timeline', hierarchy:'Ordered List',
      matrix:'Grid', quote:'Quote', statistic:'Number', ranking:'Rank Rows',
      checklist:'List', before_after:'Duopanel', flowchart:'Flow',
      relationship_map:'Grid', formula:'Number', code:'Code', screenshot:'Frame',
      conclusion:'Typography', hero:'Typography', logo_reveal:'Number', problem:'Typography',
      feature:'Feature Card', feature_stack:'List', feature_comparison:'Duopanel',
      workflow:'Flow', screenshot_tour:'List', ui_demo:'Code', metric:'KPI Row',
      testimonial:'Quote', proof:'KPI Row', integration:'Flow', pricing:'Duopanel', cta:'Typography'};
    const blkKey = s.block_id.split('.')[1];
    const intent = s.visual_intent || intentMap[s.role] || '表达内容';
    const form = s.visual_form || formMap[blkKey] || 'Typography';
    const FORM_CN = {Typography:'标题排版', 'Concept Card':'概念卡', Duopanel:'双栏对比', Flow:'流程图',
      Timeline:'时间线', Grid:'网格', Quote:'引用', Number:'数字', 'Rank Rows':'排行列表', List:'列表',
      Frame:'框图', Code:'代码', 'KPI Row':'指标行', 'Feature Card':'特性卡'};
    const formCn = FORM_CN[form] || form;
    const motionForScene = (s.motion_id || blk.motion).replace('graphics.','');
    // §61 Continuity UI：段落边界显示「什么从上一镜延续过来 + 怎么过渡」
    const carryCn = {carry:'元素延续', zoom_through:'推镜穿越', morph:'形变过渡', pan_follow:'平移跟随',
      camera_dive:'镜头俯冲', materialize:'具象成形', gather:'聚拢收束', hard_cut:'结构性硬切'};
    const prev = i > 0 ? GFX.plan.scenes[i-1] : null;
    const carryHtml = prev
      ? `<div class="hint" style="margin:2px 0;border-left:2px solid #ffd166;padding-left:6px">`+
        `↳ 从上一镜延续：${esc((s.carry_from || prev.text||'').slice(0,16))} · 过渡方式 `+
        `<select style="width:96px;font-size:11px" onchange="GFX.plan.scenes[${i}].continuity=this.value">`+
        ['hard_cut','carry','zoom_through','morph','pan_follow','camera_dive','materialize','gather']
          .map(t=>`<option value="${t}"${(s.continuity||'hard_cut')===t?' selected':''}>${carryCn[t]}</option>`).join('')+
        `</select></div>`
      : '';
    return `<div class="scene-card">
      <div class="scene-head">
        <span class="scene-id">${esc(s.id)}</span>
        <span class="scene-role">${esc(s.role)}</span>
        <span class="scene-dur">${s.duration.toFixed(1)}s</span>
        <button class="scene-move" onclick="gfxRemoveScene(${i})">✕</button>
      </div>
      <div class="scene-text">旁白：${esc(s.text)}</div>
      <div class="scene-asset">视觉意图：${esc(intent)} · 视觉形态：${esc(formCn)}</div>
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
        <span class="hint">素材</span>
        <select style="flex:1.4" onchange="GFX.plan.scenes[${i}].source=this.value;GFX.previewHtml=null">
          <option value="Generated Graphic"${(s.source||'Generated Graphic')==='Generated Graphic'?' selected':''}>程序化图形</option>
          <option value="Library Asset"${s.source==='Library Asset'?' selected':''}>素材库资产</option>
          <option value="User Upload"${s.source==='User Upload'?' selected':''}>用户上传</option>
        </select>
      </div>
      <div class="scene-asset">视觉块：${esc(s.block_id)} · 动效：${esc(motionForScene)} · 来源：${s.source==='Library Asset'?'素材库资产':s.source==='User Upload'?'用户上传':'程序化图形'}</div>
      <div class="btn-row sb-actions" style="flex-wrap:wrap">
        <button class="btn-secondary btn-sm" onclick="gfxGenAsset(${i})">✨ 生成素材</button>
        <span class="hint">换视觉/动效/布局/素材：用上方下拉直接改（改后需重新预览）</span>
      </div>
    </div>`;
  }).join('');
  box.innerHTML = `<div class="card"><b>📋 你的创作方案</b> <span class="hint">（先审阅再渲染；每场景可换视觉/动画/素材/布局，或删场景。方案确认后可进入工作台继续精修——正式修改都会记入版本历史）</span>
    <div class="btn-row">
      <button class="btn-secondary" onclick="gfxApplyToWorkspace()">🎛 确认方案，进入工作台精修</button>
      <span class="hint" id="gfx-apply-status"></span>
    </div>
    <div class="scene-list" style="max-height:400px">${cards}</div></div>`;
}

// §7/FE-003 Creative Plan → Composition 归一：proposal 确认 → 建项目（合法 IR）
// → 打开工作台。此后换块/布局/连续性全部经 executeCommand（Command Bus）。
async function gfxApplyToWorkspace(){
  const status = document.getElementById('gfx-apply-status') || document.getElementById('gfx-status');
  if (!GFX.plan) { status.textContent = '先生成创作方案'; return; }
  status.textContent = '正在创建可编辑项目……';
  const [w, h] = document.getElementById('gfx-aspect').value.split('x');
  const r = await apiPost('/api/graphics/plan_to_project', {
    plan: GFX.plan,
    script: document.getElementById('gfx-script').value,
    size: w + 'x' + h,
  });
  if (!r.ok) { status.textContent = '❌ ' + (r.error || '创建失败'); return; }
  // 打开工作台并选中新项目（editorLoad 读项目列表）
  switchTab('editor');
  const sel = document.getElementById('editor-project');
  await new Promise(res => setTimeout(res, 400));  // editorInit 填充下拉
  if (sel) {
    for (const opt of sel.options) {
      if (opt.value === r.project_id) { sel.value = r.project_id; break; }
    }
  }
  if (typeof editorLoad === 'function') editorLoad();
  if (typeof editorToast === 'function') {
    editorToast(`✅ 方案已转为可编辑项目（${r.scene_count} 场景）。后续修改都会记入版本历史，可撤销。`);
  }
}

// §9.3/FE-007：gfxSwapBlock/Motion/Source/Layout 四个「输入编号」prompt 路径
// 已删除——方案卡内下拉（onchange）已覆盖同功能，正式 UI 不依赖 prompt()。
async function gfxGenAsset(i){
  // §34 生成素材：走生图角色（未配置 key 时诚实提示，不假成功）
  try {
    const r = await apiPost('/api/gfx/gen_asset', {scene_index: i, plan: GFX.plan});
    if (!r.ok) throw new Error(r.error || '生成失败');
    GFX.plan.scenes[i].source = 'Generated Image';
    GFX.previewHtml = null;
    gfxRenderPlan();
  } catch(e) {
    editorToast('生成素材不可用：' + e.message + '\n（需在「大模型配置中心」为 image 角色配置 API key；当前可先用程序化视觉块）', true);
  }
}

// §42 Block 开发体验：New Block 表单提交
async function gfxNewBlock(){
  const status = document.getElementById('nb-status');
  const id = document.getElementById('nb-id').value.trim();
  const name = document.getElementById('nb-name').value.trim();
  if (!id || !name) { status.textContent = '块 id 和块名都必填'; return; }
  const r = await apiPost('/api/catalog/blocks/create', {
    id, name,
    base_block: document.getElementById('nb-base').value,
    semantic_intents: (document.getElementById('nb-intents').value || 'custom').split(',').map(s=>s.trim()).filter(Boolean),
  });
  if (r.ok) {
    status.textContent = `✅ 块「${r.name}」（${r.id}）已进创作目录（换视觉下拉可见）`;
    GFX.blocks = [];  // 强制重拉 catalog
    await gfxEnsureBlocks();
    if (GFX.plan) gfxRenderPlan();
  } else {
    status.textContent = '❌ ' + (r.error || '保存失败');
  }
}
function gfxRemoveScene(i){
  GFX.plan.scenes.splice(i, 1);
  GFX.plan.scene_count = GFX.plan.scenes.length;
  GFX.plan.total_duration = Math.round(GFX.plan.scenes.reduce((a,s)=>a+s.duration,0)*100)/100;
  GFX.previewHtml = null;
  gfxRenderPlan();
}

async function gfxPreview(){
  const status = document.getElementById('gfx-status');
  if (!GFX.plan) { status.textContent = '先「生成创作方案」'; return; }
  if (!GFX.previewHtml) {
    status.textContent = '编译确定性 HTML 中……';
    const r = await apiPost('/api/graphics/preview', {plan: GFX.plan});
    if (!r.ok) { status.textContent = '❌ ' + (r.error || '预览失败'); return; }
    GFX.previewHtml = r.html;
  }
  const [w, h] = document.getElementById('gfx-aspect').value.split('x');
  const scale = Math.min(1, 420 / parseInt(h));
  const box = document.getElementById('gfx-preview-box');
  // §40 Developer Mode：Visual / Source 两模式（Source=编译产物 HTML 只读展示）
  box.innerHTML = `<div class="card"><b>动效预览</b> <span class="hint">（沙箱 iframe 隔离运行，与正式渲染同一份 HTML——预览即所得）</span>
    <div class="btn-row"><button class="btn-secondary btn-sm" id="gfx-mode-visual" onclick="gfxMode('visual')">视觉视图</button>
    <button class="btn-secondary btn-sm" id="gfx-mode-source" onclick="gfxMode('source')">源码视图（开发者模式）</button></div>
    <div id="gfx-visual-view"><iframe id="gfx-preview-frame" sandbox="allow-scripts" style="width:${parseInt(w)*scale}px;height:${parseInt(h)*scale}px;transform-origin:top left;border:1px solid #2a343d;border-radius:8px;background:#101418;display:block"></iframe>
    <div class="btn-row"><button class="btn-secondary btn-sm" onclick="gfxPlay()">▶ 播放</button>
    <button class="btn-secondary btn-sm" onclick="gfxPause()">⏸ 暂停</button></div>
    <div class="btn-row"><span class="hint">Seek</span>
    <input type="range" id="gfx-seek-slider" min="0" max="100" value="0" style="flex:1" oninput="gfxSeekTo(this.value)">
    <span class="hint" id="gfx-seek-label">0%</span></div></div>
    <div id="gfx-source-view" style="display:none">
      <div class="hint">开发者模式：编译产物 HTML（只读；重编译须回方案换块——HTML 是执行格式，不是编辑入口）</div>
      <textarea id="gfx-source-ta" style="min-height:200px;font-family:Consolas,monospace;font-size:12px" readonly></textarea>
    </div></div>`;
  const frame = document.getElementById('gfx-preview-frame');
  frame.srcdoc = GFX.previewHtml;
  document.getElementById('gfx-source-ta').value = GFX.previewHtml;
  // §39 Direct Manipulation：启用点击选中（元素 → bounds 高亮 → 信号回 Inspector 面板）
  window.addEventListener('message', function(ev){
    if (ev.data && ev.data.type === 'gf-selected') {
      const el = document.getElementById('gfx-selected-info');
      if (el) el.textContent = `已选中：${ev.data.cls.split(' ')[0]}「${ev.data.text}…」——修改请回上方方案卡（手势→Command→重编译，不直接改 HTML）`;
    }
  });
  frame.addEventListener('load', function(){
    frame.contentWindow.postMessage({type:'gf-enable-select'}, '*');
  });
  const selInfo = document.createElement('div');
  selInfo.id = 'gfx-selected-info';
  selInfo.className = 'hint';
  document.getElementById('gfx-visual-view').appendChild(selInfo);
  status.textContent = '预览就绪（Visual/Source 可切换；播放查看动效，满意后渲染成片）';
  box.scrollIntoView({behavior:'smooth'});
}
function gfxMode(mode){
  const v = document.getElementById('gfx-visual-view');
  const s = document.getElementById('gfx-source-view');
  if (!v || !s) return;
  v.style.display = mode === 'visual' ? '' : 'none';
  s.style.display = mode === 'source' ? '' : 'none';
  document.getElementById('gfx-mode-visual').style.background = mode === 'visual' ? '#26405a' : '';
  document.getElementById('gfx-mode-source').style.background = mode === 'source' ? '#26405a' : '';
}

function gfxPlay(){
  const frame = document.getElementById('gfx-preview-frame');
  if (frame) frame.contentWindow.postMessage({type:'gf-play'}, '*');
}
function gfxPause(){
  const frame = document.getElementById('gfx-preview-frame');
  if (frame) frame.contentWindow.postMessage({type:'gf-pause'}, '*');
}
// §53 Timeline Interaction：drag 滑块 → 统一 runtime.seek（不各管各的）
function gfxSeekTo(pct){
  const frame = document.getElementById('gfx-preview-frame');
  if (!frame) return;
  // 总帧数从 srcdoc 编译时已知：用 GFX.plan.total_duration × fps
  const fps = 30;
  const totalFrames = Math.max(1, Math.round((GFX.plan ? GFX.plan.total_duration : 0) * fps));
  const f = Math.round(totalFrames * (parseFloat(pct) / 100));
  frame.contentWindow.postMessage({type:'gf-seek', frame: f}, '*');
  const label = document.getElementById('gfx-seek-label');
  if (label) label.textContent = pct + '% · frame ' + f;
}

async function gfxRender(){
  const status = document.getElementById('gfx-status');
  const btn = document.getElementById('gfx-render-btn');
  if (!GFX.plan) { status.textContent = '先「生成创作方案」'; return; }
  btn.disabled = true; btn.textContent = '渲染中……';
  status.textContent = '逐帧渲染中（约 10-60 秒）……';
  try {
    const r = await apiPost('/api/graphics/render', {
      plan: GFX.plan,
      quality: 'draft',
      script: document.getElementById('gfx-script').value,
    });
    if (!r.ok) throw new Error(r.error || '渲染失败');
    document.getElementById('gfx-result').innerHTML = `
      <div class="ok-box"><b>图形视频已生成</b> · ${r.duration.toFixed(1)}s · ${r.frames} 帧 · 项目 ${esc(r.project_id)}
      <video controls src="/file?path=${encodeURIComponent(r.final_path)}"></video><br>
      <a class="dl" href="/file?path=${encodeURIComponent(r.final_path)}" download="graphic.mp4">⬇ 下载 MP4</a></div>`;
    status.textContent = '✅ 渲染完成（项目已登记，首页/项目页可见）';
  } catch(e) {
    status.textContent = '❌ 渲染失败：' + e.message;
  } finally {
    btn.disabled = false; btn.textContent = '🎬 渲染成片';
  }
}

// ---- 首页：我现在该做什么 ----
// 9 入口路由：按起点预填创作页（PDF/录屏暂走素材上传引导，能力就绪前诚实降级）
function newEntry(kind){
  switchTab('create');
  const modeSel = document.getElementById('mode');
  const hint = {
    script: '📝 直接把脚本/口播文案粘进下方文案框。',
    article: '📰 把整篇文章粘进文案框，系统按句号自动分场景。',
    video: '🎥 上传已有视频当素材（可多条），配新文案重新成片。',
    assets: '🖼 先上传素材，文案可留白让系统按素材描述生成。',
    product: '📦 切换到「产品广告片」模式，上传产品图。',
    pdf: '📄 PDF 暂不支持直接解析——把文字内容复制进文案框（PDF 解析在能力面板就绪后开放）。',
    screen: '🖥 上传录屏文件（mp4），配讲解文案成片。',
    blank: '⬜ 从零开始：先填文案，或先去收集箱攒灵感。',
  }[kind] || '';
  if (kind === 'product') modeSel.value = 'product';
  else if (kind === 'screen' || kind === 'video' || kind === 'assets') modeSel.value = 'auto';
  else modeSel.value = 'auto';
  document.getElementById('script').focus();
  if (hint) { document.getElementById('script').placeholder = hint; }
}

async function loadHome(){
  try {
    const projects = await (await fetch('/api/projects')).json();
    if (!Array.isArray(projects)) throw new Error(projects.error || '读取失败');
    // 封面卡片式最近项目（方案 §十：封面+项目名，不用纯文本行）
    const recent = projects.slice(0, 6).map(p => {
      const state = p.pending_confirm ? '⚠ 待确认' : (p.has_final ? '✅ 已完成' : '· 进行中');
      const qc = p.qc === 'PASS' ? ' · QC 通过' : '';
      return `<div class="proj-row home-card" onclick="openProject('${esc(p.project_id)}')">
        ${p.has_final
          ? `<video class="home-poster" src="/file?path=${encodeURIComponent(p.final_path)}#t=1" muted preload="metadata" onerror="this.style.display='none'"></video><div class="home-poster home-poster-empty" style="display:none"></div>`
          : `<div class="home-poster home-poster-empty">${p.project_id.startsWith('tpl-') ? '🧩' : '🎬'}</div>`}
        <b>${esc(p.project_id)}</b>
        <span class="hint">${new Date(p.mtime*1000).toLocaleString()}</span>
        <span class="${p.pending_confirm ? 'cap-warn' : 'cap-ok'}">${state}${qc}</span>
      </div>`;
    }).join('');
    document.getElementById('home-projects').innerHTML =
      recent || '<div class="hint">还没有项目，点击上方任一入口开始。</div>';
    // 待处理三态分区（方案 §十：需确认分镜 / 需确认草稿 / 生成完成）
    const pendingSb = projects.filter(p => p.pending_confirm && !p.draft_exists);
    const pendingDraft = projects.filter(p => p.pending_confirm && p.draft_exists);
    const doneRecent = projects.filter(p => p.has_final && Date.now()/1000 - p.mtime < 3600 * 24);
    const fmt = (list, label, color) => list.length
      ? list.map(p => `<div class="proj-row" onclick="openProject('${esc(p.project_id)}')">
          <b>${esc(p.project_id)}</b><span class="${color}">${label}</span></div>`).join('')
      : '';
    document.getElementById('home-pending-storyboard').innerHTML =
      fmt(pendingSb, '● 需要确认分镜', 'cap-warn') || '<div class="hint">— 无待确认分镜</div>';
    document.getElementById('home-pending-draft').innerHTML =
      fmt(pendingDraft, '● 需要确认草稿', 'cap-warn') || '<div class="hint">— 无待确认草稿</div>';
    document.getElementById('home-done-recent').innerHTML =
      fmt(doneRecent, '● 生成任务完成', 'cap-ok') || '<div class="hint">— 24 小时内无新完成项目</div>';
    const n = pendingSb.length + pendingDraft.length;
    document.getElementById('home-todo-hint').textContent = n
      ? `你有 ${n} 个项目在等你确认（下方），或直接开始新创作。`
      : '没有待办。挑一个入口开始新创作，或打开最近项目继续。';
  } catch (e) {
    document.getElementById('home-projects').innerHTML =
      `<div class="err-box">加载失败：${esc(e.message)}</div>`;
  }
}

// ---- 项目页 ----
async function loadProjects(){
  try {
    const projects = await (await fetch('/api/projects')).json();
    if (!Array.isArray(projects)) throw new Error(projects.error || '读取失败');
    document.getElementById('projects-list').innerHTML = projects.map(p => {
      const state = p.pending_confirm ? '⚠ 待确认' : (p.has_final ? '✅ 已完成' : '· 进行中');
      return `<div class="proj-row" onclick="openProject('${esc(p.project_id)}')">
        <b>${esc(p.project_id)}</b>
        <span class="hint">${new Date(p.mtime*1000).toLocaleString()}</span>
        <span class="${p.pending_confirm ? 'cap-warn' : 'cap-ok'}">${state}</span>
      </div>`;
    }).join('') || '<div class="hint">还没有项目。</div>';
  } catch (e) {
    document.getElementById('projects-list').innerHTML =
      `<div class="err-box">加载失败：${esc(e.message)}</div>`;
  }
}

async function openProject(pid){
  switchTab('projects');
  const box = document.getElementById('project-detail-box');
  box.innerHTML = '加载项目详情……';
  try {
    const j = await (await fetch('/api/project_detail?project_id=' + encodeURIComponent(pid))).json();
    if (j.error) throw new Error(j.error);
    const sb = j.storyboard || {};
    const sceneRows = (sb.scenes || []).map(s =>
      `<div class="scene-card">
        <div class="scene-head"><span class="scene-id">${esc(s.scene_id||'')}</span>
          <span class="scene-dur">${(s.duration||0).toFixed(1)}s</span></div>
        <div class="scene-text">${esc(s.text||'')}</div>
        <div class="scene-asset">素材（${(s.assets||[]).length}个）：${esc((s.assets||[]).join('、')||'—')}</div>
      </div>`).join('');
    const trs = (j.transitions || []).filter(t => t && t !== 'cut').length;
    box.innerHTML = `
      <div class="ok-box">
        <b>${esc(pid)}</b> · ${sb.scene_count||'?'} 场景 · ${sb.total_duration||'?'}s · 效果转场 ${trs} 处
        ${j.final_exists ? '<span class="cap-ok">✅ 已有成品</span>' : '<span class="cap-warn">未渲染完成</span>'}
        ${j.draft_exists ? ' · <span class="cap-ok">有草稿</span>' : ''}
        <div class="scene-list" style="max-height:280px">${sceneRows || '<div class="hint">无分镜数据</div>'}</div>
        ${j.final_exists ? `
          <div class="btn-row">
            <button>▶ 播放成品</button>
            <button class="btn-secondary" onclick="publishVariants('${esc(pid)}')">📤 发布多平台（9:16/16:9/3:4/1:1）</button>
          </div>
          <video controls id="proj-video" style="width:100%;max-width:420px;display:none;margin-top:10px;border-radius:8px;background:#000"></video>` : ''}
        <div class="hint">完整文案：${esc((j.script||'').slice(0,200))}${(j.script||'').length>200?'…':''}</div>
      </div>`;
    if (j.final_exists) {
      const btn = box.querySelector('button');
      btn.onclick = async () => {
        // 服务端返回的绝对路径（环境无关；不再硬编码检出区目录）
        const v = document.getElementById('proj-video');
        v.style.display = 'block';
        v.src = '/file?path=' + encodeURIComponent(j.final_path ||
          (j.project_id ? j.project_id : ''));
        v.play().catch(()=>{});
      };
    }
  } catch (e) {
    box.innerHTML = `<div class="err-box">项目详情加载失败：${esc(e.message)}</div>`;
  }
}

// ---- 素材库（Asset Graph）----
// ---- 系列（§5 模板 + §42 批量）----
async function loadSeries(){
  try {
    const list = await (await fetch('/api/series')).json();
    if (!Array.isArray(list)) throw new Error(list.error || '读取失败');
    document.getElementById('series-list').innerHTML = list.map(s => `
      <div class="proj-row">
        <b>${esc(s.name)}</b>
        <span class="hint">${esc(s.series_id)} · 配音 ${esc(s.spec.voice||'默认')} · ${esc(s.spec.subtitle_mode||'plain')}</span>
      </div>`).join('') || '<div class="hint">还没有系列。</div>';
  } catch (e) {
    document.getElementById('series-list').innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
  }
  // 系列表单下拉复用创作页已加载的 voices/aspects（单一数据源，避免重复拉取）
  const voices = document.getElementById('voice');
  const sv = document.getElementById('series-voice');
  if (voices && sv && !sv.options.length) sv.innerHTML = voices.innerHTML;
  const aspects = document.getElementById('aspect');
  const sa = document.getElementById('series-aspect');
  if (aspects && sa && !sa.options.length) sa.innerHTML = aspects.innerHTML;
}

async function createSeriesAndBatch(){
  const status = document.getElementById('series-status');
  const name = document.getElementById('series-name').value.trim();
  const scripts = document.getElementById('series-scripts').value.trim();
  if (!name || !scripts) { status.textContent = '请填系列名和多集文案'; return; }
  status.textContent = '创建系列并入队……';
  try {
    const spec = {
      voice: document.getElementById('series-voice').value || 'zh-CN-XiaoxiaoNeural',
      subtitle_mode: document.getElementById('series-subtitle').value || 'plain',
      watermark: document.getElementById('series-watermark').value || null,
    };
    const asp = document.getElementById('series-aspect').value;
    if (asp && asp.includes('x')) {
      const [w, h] = asp.split('x');
      spec.width = parseInt(w); spec.height = parseInt(h);
    }
    const r1 = await fetch('/api/series_create', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name, spec})
    });
    const j1 = await r1.json();
    if (!j1.ok) throw new Error(j1.error);
    // 批量（multipart：scripts + 可选共享素材）
    const fd = new FormData();
    fd.append('series_id', j1.series_id);
    fd.append('scripts', scripts);
    for (const f of document.getElementById('series-videos').files) fd.append('videos', f, f.name);
    const r2 = await fetch('/api/series_batch', {method:'POST', body: fd});
    const j2 = await r2.json();
    if (!j2.ok) throw new Error(j2.error);
    status.textContent = `✅ 已入队 ${j2.episodes} 集，去「自动化」页看进度`;
    switchTab('automation');
  } catch (e) {
    status.textContent = '失败：' + e.message;
  }
}

// ---- 收集箱（§30）----
async function inboxAdd(kind){
  const content = document.getElementById('inbox-content').value.trim();
  const status = document.getElementById('inbox-status');
  if (!content) { status.textContent = '请输入内容'; return; }
  try {
    const r = await fetch('/api/inbox_add', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({kind, content})
    });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error);
    document.getElementById('inbox-content').value = '';
    status.textContent = '✅ 已存入收集箱';
    loadInbox();
  } catch (e) { status.textContent = '失败：' + e.message; }
}

async function loadInbox(){
  try {
    const list = await (await fetch('/api/inbox')).json();
    if (!Array.isArray(list)) throw new Error(list.error || '读取失败');
    document.getElementById('inbox-list').innerHTML = list.map(it => `
      <div class="proj-row">
        <span style="flex:1">${esc(it.content.slice(0,120))}</span>
        <span class="hint">${new Date(it.created_at*1000).toLocaleDateString()}</span>
        <button class="btn-secondary btn-sm" onclick="inboxToCreate('${esc(it.item_id)}')">🎬 转创作</button>
        <button class="btn-secondary btn-sm" onclick="inboxAction('${esc(it.item_id)}','delete')">🗑</button>
      </div>`).join('') || '<div class="hint">收集箱为空。</div>';
  } catch (e) {
    document.getElementById('inbox-list').innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
  }
}

function inboxToCreate(itemId){
  const row = [...document.querySelectorAll('#inbox-list .proj-row')]
    .find(r => r.innerHTML.includes(itemId));
  const content = row ? row.querySelector('span').textContent : '';
  switchTab('create');
  const ta = document.getElementById('script');
  if (ta && content) { ta.value = content; }
  fetch('/api/inbox_action', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({item_id: itemId, action: 'converted'})}).catch(()=>{});
}

async function inboxAction(itemId, act){
  try {
    await fetch('/api/inbox_action', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({item_id: itemId, action: act})});
    loadInbox();
  } catch (e) { editorToast('操作失败：' + e.message, true); }
}

// ---- 队列（§42）----
async function loadQueue(){
  try {
    const jobs = await (await fetch('/api/jobs')).json();
    if (!Array.isArray(jobs)) throw new Error(jobs.error || '读取失败');
    const st = {waiting:'· 排队中', running:'⚙ 生成中', done:'✅ 完成', failed:'✗ 失败'};
    document.getElementById('queue-list').innerHTML = jobs.map(j => `
      <div class="proj-row">
        <b>${esc(j.job_id)}</b>
        <span class="hint">${j.kind === 'series_episode' ? '系列单集 #'+j.spec.episode : '平台变体 '+esc(j.spec.platform||'')}</span>
        <span class="${j.status==='done'?'cap-ok':(j.status==='failed'?'cap-off':(j.status==='running'?'cap-warn':'hint'))}">${st[j.status]||j.status}</span>
        ${j.result && j.result.qc ? `<span class="hint">QC ${esc(j.result.qc)}</span>` : ''}
        ${j.error ? `<span class="hint" style="color:#e05e5e">${esc(j.error.slice(0,80))}</span>` : ''}
      </div>`).join('') || '<div class="hint">队列为空。</div>';
  } catch (e) {
    document.getElementById('queue-list').innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
  }
}

// ---- 多平台发布（§41：项目详情 → 批量变体）----
async function publishVariants(pid){
  try {
    const r = await fetch('/api/variants_batch', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({project_id: pid, platforms: ['douyin','bilibili','xhs','square']})
    });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error);
    editorToast(`已入队 ${j.job_ids.length} 个平台变体，去「自动化」页看进度`);
    switchTab('automation');
  } catch (e) { editorToast('发布失败：' + e.message, true); }
}

// §8 拆分导出：inline onclick/window 引用兼容（零行为改动）
Object.assign(window, {
  gfxEnsureBlocks,
  gfxSetType,
  gfxMakePlan,
  gfxRenderPlan,
  gfxApplyToWorkspace,
  gfxGenAsset,
  gfxNewBlock,
  gfxRemoveScene,
  gfxPreview,
  gfxMode,
  gfxPlay,
  gfxPause,
  gfxSeekTo,
  gfxRender,
  newEntry,
  loadHome,
  loadProjects,
  openProject,
  loadSeries,
  createSeriesAndBatch,
  inboxAdd,
  loadInbox,
  inboxToCreate,
  inboxAction,
  loadQueue,
  publishVariants,
  GFX,
});
