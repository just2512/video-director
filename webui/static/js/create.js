// §37/§38/§62 Create Flow（方案《FRONTEND_DEEP_UNIFICATION》）：
// 快速创作与完整创作不再是两套 UX——统一创建面板：
//   「从哪里开始？」：文案 / 素材 / 参考视频 / 软件项目（Create Source）
//   Essentials：文案 · 平台 · 时长 · 风格 · 素材
//   More Settings（折叠）：配音 · 字幕 · BGM · SFX · 水印 · 人物条
// 状态机：idle → editing → submitting → planning → awaiting_confirm → failed
// 创建成功 → openProject → workspace（§62 不再让 Create 自带结果编辑器）。

window.CF = {
  state: 'idle',          // idle|editing|submitting|planning|awaiting_confirm|failed
  source: 'script',       // script|assets|reference|repo
};

/** 打开创建面板（顶栏「+ 新建项目」唯一入口 §3.1）。 */
function openCreate(){
  closeCreate();
  const mask = document.createElement('div');
  mask.id = 'cf-mask';
  mask.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:900;display:flex;align-items:center;justify-content:center';
  const panel = document.createElement('div');
  panel.id = 'cf-panel';
  panel.className = 'card';
  panel.style.cssText = 'max-width:760px;width:94%;max-height:88vh;overflow:auto;padding:22px';
  panel.innerHTML = _createPanelHtml();
  mask.appendChild(panel);
  mask.addEventListener('click', ev => { if (ev.target === mask) closeCreate(); });
  document.body.appendChild(mask);
  CF.state = 'editing';
  createLoadDefaults();
  const ta = document.getElementById('cf-script');
  if (ta) ta.focus();
}
function closeCreate(){
  const m = document.getElementById('cf-mask');
  if (m) m.remove();
  CF.state = 'idle';
}

function _createPanelHtml(){
  return `
  <div style="display:flex;align-items:center;gap:10px">
    <h3 style="font-size:17px;margin:0">新建项目</h3>
    <span style="flex:1"></span>
    <button class="btn-secondary btn-sm" onclick="closeCreate()">关闭</button>
  </div>
  <label>从哪里开始？</label>
  <div class="btn-row cf-sources">
    <button class="btn-secondary btn-sm cf-src${CF.source === 'script' ? ' chip-on' : ''}" data-src="script">文案</button>
    <button class="btn-secondary btn-sm cf-src${CF.source === 'assets' ? ' chip-on' : ''}" data-src="assets">素材</button>
    <button class="btn-secondary btn-sm cf-src${CF.source === 'reference' ? ' chip-on' : ''}" data-src="reference">参考视频</button>
    <button class="btn-secondary btn-sm cf-src${CF.source === 'repo' ? ' chip-on' : ''}" data-src="repo">软件项目</button>
  </div>
  <div id="cf-source-area"></div>
  <div id="cf-essentials">
    <label>文案</label>
    <textarea id="cf-script" style="min-height:110px" placeholder="粘贴你的文案或创意……（素材起步可留空，AI 按素材生成）"></textarea>
    <div class="btn-row" style="align-items:center;flex-wrap:wrap">
      <button class="btn-secondary btn-sm" onclick="createEnhance()">AI 润色文案</button>
      <span class="hint" id="cf-enhance-status"></span>
      <span class="tb-sep" style="flex:1"></span>
      <span class="hint">或</span>
      <button class="btn-secondary btn-sm" onclick="cfMakeGraphicPlan()">生成图形方案预览</button>
      <span class="hint">（程序化动效画面，无需素材；预览满意后一键进入工作台）</span>
    </div>
    <div id="cf-enhance-box" style="display:none" class="enhance-box">
      <div class="hint">润色结果（采用后替换原文）：</div>
      <textarea id="cf-enhanced" class="enhanced-ta" style="min-height:90px"></textarea>
      <div class="btn-row">
        <button class="btn-sm" onclick="createAcceptEnhanced()">采用</button>
        <button class="btn-secondary btn-sm" onclick="document.getElementById('cf-enhance-box').style.display='none'">放弃</button>
      </div>
    </div>
    <div class="btn-row" style="gap:10px;margin-top:12px">
      <div style="flex:1"><label>目标平台</label>
        <select id="cf-platform" onchange="createPlatformSync()">
          <option value="">不指定</option>
          <option value="douyin">抖音/视频号 9:16</option>
          <option value="bilibili">B站 16:9</option>
          <option value="xhs">小红书 3:4</option>
          <option value="square">朋友圈 1:1</option>
        </select></div>
      <div style="flex:1"><label>时长（秒，可选）</label>
        <input type="number" id="cf-duration" placeholder="例：30" min="3" step="1"></div>
      <div style="flex:1"><label>风格</label>
        <input type="text" id="cf-style" placeholder="例：纪录片沉稳"></div>
    </div>
    <div id="cf-style-presets" class="btn-row" style="flex-wrap:wrap;gap:6px;font-size:12px;color:var(--muted)"></div>
    <label>素材（可多选：视频/图片；没有素材可跳过）</label>
    <input type="file" id="cf-videos" multiple accept=".mp4,.mov,.mkv,.webm">
    <input type="file" id="cf-images" multiple accept=".jpg,.jpeg,.png,.webp" style="margin-top:6px">
  </div>
  <!-- §22 GFX = 方案生成入口：图形方案预览区（proposal——确认后进工作台，无第二编辑器） -->
  <div id="cf-gfx-area" style="display:none">
    <div class="btn-row" style="align-items:center;flex-wrap:wrap">
      <span class="hint">图形方案设置：</span>
      <select id="gfx-duration" style="width:90px"><option value="15">15s</option><option value="30" selected>30s</option><option value="60">60s</option></select>
      <select id="gfx-style" style="width:110px"><option value="editorial">编辑风</option><option value="minimal">极简风</option><option value="warm">暖调</option></select>
      <select id="gfx-aspect" style="width:100px"><option value="1080x1920">9:16</option><option value="1920x1080">16:9</option><option value="1080x1080">1:1</option></select>
      <span class="hint" id="gfx-status"></span>
    </div>
    <div id="gfx-plan-box"></div>
    <div id="gfx-preview-box"></div>
  </div>
  <details style="margin-top:14px">
    <summary style="cursor:pointer;color:var(--muted);font-size:13px">更多设置（配音 / 字幕 / 音乐 / 水印 / 人物条）</summary>
    <div id="cf-more-settings">
      <div class="btn-row" style="gap:10px;margin-top:10px">
        <div style="flex:1"><label>配音</label>
          <select id="cf-voice"></select></div>
        <div style="flex:1"><label>语速</label>
          <select id="cf-voice-rate">
            <option value="0.9">0.9x 放慢</option>
            <option value="1.0" selected>1.0x 正常</option>
            <option value="1.3">1.3x 加快</option>
          </select></div>
        <div style="flex:1"><label>字幕</label>
          <select id="cf-subtitle">
            <option value="plain">普通字幕</option>
            <option value="karaoke">卡拉OK（逐词高亮）</option>
          </select></div>
      </div>
      <div class="btn-row" style="gap:10px;margin-top:8px">
        <div style="flex:1"><label>背景音乐</label>
          <select id="cf-bgm"></select></div>
        <div style="flex:1"><label>音效</label>
          <select id="cf-sfx"></select></div>
      </div>
      <div class="btn-row" style="gap:10px;margin-top:8px">
        <div style="flex:1"><label>水印（留空不加）</label>
          <input type="text" id="cf-watermark" placeholder="例：品牌名"></div>
        <div style="flex:1"><label>人物条·姓名（可选）</label>
          <input type="text" id="cf-lower-name" placeholder="留空=不显示"></div>
        <div style="flex:1"><label>人物条·头衔（可选）</label>
          <input type="text" id="cf-lower-role" placeholder="例：创始人"></div>
      </div>
      <div class="btn-row" style="margin-top:8px">
        <div style="flex:1"><label>成片前人工确认</label>
          <select id="cf-confirm">
            <option value="preview">先预览分镜再渲染（推荐）</option>
            <option value="direct">直接一键到底</option>
          </select></div>
      </div>
    </div>
  </details>
  <div class="btn-row" style="margin-top:18px">
    <button id="cf-go" onclick="createProject()">开始创作</button>
    <span class="hint" id="cf-status"></span>
  </div>`;
}

/** §37 切换 Create Source：仅替换输入区，不改变后面的 Composition 流程。 */
function setCreateSource(src){
  CF.source = src;
  document.querySelectorAll('.cf-src').forEach(b =>
    b.classList.toggle('chip-on', b.dataset.src === src));
  const area = document.getElementById('cf-source-area');
  if (!area) return;
  if (src === 'reference') {
    area.innerHTML = `
      <label>参考视频（从优秀视频提取风格/节奏/镜头结构，指导本次创作）</label>
      <input type="file" id="cf-reference" accept=".mp4,.mov,.mkv,.webm">
      <div class="hint">上传后先分析，镜头语法会并入创作简报；不复制参考内容。</div>`;
  } else if (src === 'repo') {
    area.innerHTML = `
      <label>软件项目根目录（从代码库发现可验证的卖点，AI 不编造功能）</label>
      <input type="text" id="cf-repo-root" class="rev-input" placeholder="例：D:\\Projects\\my-app">
      <div class="hint">会扫描 README/CHANGELOG 等证据文件，只采用可追溯的功能声明。</div>`;
  } else if (src === 'assets') {
    area.innerHTML = `
      <div class="hint">素材起步：先在下方选素材，文案可留空让 AI 按素材描述生成。</div>`;
  } else {
    area.innerHTML = '';
  }
}

/** 下拉/风格预设填充（单一数据源：与设置页同 API）。 */
async function createLoadDefaults(){
  try {
    const voices = await apiGet('/api/voices');
    _rebuildSelect('cf-voice', voices.map(v => [v.id, v.label]));
    const bgms = await apiGet('/api/bgm');
    _rebuildSelect('cf-bgm', [['auto', 'AI 自动匹配'], ['', '（不配乐）'],
                              ...bgms.map(b => [b.id, b.label])]);
    const sfxs = await apiGet('/api/sfx');
    _rebuildSelect('cf-sfx', [['auto', '自动（按镜头配音效，推荐）'], ['', '关闭'],
                              ...sfxs.map(s => [s.id, s.label])]);
  } catch (e) { /* 下拉加载失败不阻塞创建 */ }
  try {
    const styles = await apiGet('/api/registry/styles');
    if (Array.isArray(styles) && styles.length) {
      const box = document.getElementById('cf-style-presets');
      box.innerHTML = '<span>风格：</span>' + styles.slice(0, 5).map(s =>
        `<button class="btn-secondary btn-sm" onclick="document.getElementById('cf-style').value='${esc(s.name)}'" title="${esc(s.description || '')}">${esc(s.name)}</button>`).join('');
    }
  } catch (e) { /* 预设失败不阻塞 */ }
  // 来源按钮绑定
  document.querySelectorAll('.cf-src').forEach(b => {
    b.onclick = () => setCreateSource(b.dataset.src);
  });
}

function createPlatformSync(){
  const map = {douyin: '1080x1920', bilibili: '1920x1080', xhs: '1080x1440', square: '1080x1080'};
  const p = (document.getElementById('cf-platform') || {}).value;
  if (p && map[p]) window._cfAspect = map[p];
}

/** §38 状态机：提交 → planning → awaiting_confirm → workspace。 */
async function createProject(){
  const btn = document.getElementById('cf-go');
  const status = document.getElementById('cf-status');
  const script = (document.getElementById('cf-script') || {}).value || '';
  const videos = document.getElementById('cf-videos');
  const images = document.getElementById('cf-images');
  const hasFiles = (videos && videos.files.length) || (images && images.files.length);
  if (!script.trim() && !hasFiles) { status.textContent = '请先填文案，或选择素材'; return; }
  CF.state = 'submitting';
  btn.disabled = true;
  status.textContent = '正在理解文案……';
  // 创作简报（§十一）：平台/时长/风格并入
  const briefParts = [];
  const tp = document.getElementById('cf-platform');
  if (tp && tp.value) briefParts.push('目标平台：' + tp.options[tp.selectedIndex].text);
  const td = document.getElementById('cf-duration');
  if (td && td.value) briefParts.push(`目标时长：约 ${td.value} 秒`);
  const st = document.getElementById('cf-style');
  if (st && st.value.trim()) briefParts.push('风格：' + st.value.trim());
  const fd = new FormData();
  fd.append('script', script);
  fd.append('creative_brief', briefParts.length ? '【创作简报】' + briefParts.join('；') : '');
  fd.append('mode', hasFiles ? 'auto' : 'script_only');
  fd.append('voice', (document.getElementById('cf-voice') || {}).value || '');
  fd.append('voice_rate', (document.getElementById('cf-voice-rate') || {}).value || '1.0');
  fd.append('style_intent', (st || {}).value || '');
  fd.append('aspect', window._cfAspect || '1080x1920');
  fd.append('bgm', (document.getElementById('cf-bgm') || {}).value || 'auto');
  fd.append('sfx', (document.getElementById('cf-sfx') || {}).value || 'auto');
  fd.append('subtitle_mode', (document.getElementById('cf-subtitle') || {}).value || 'plain');
  fd.append('watermark', (document.getElementById('cf-watermark') || {}).value || '');
  fd.append('lower_third_name', (document.getElementById('cf-lower-name') || {}).value || '');
  fd.append('lower_third_role', (document.getElementById('cf-lower-role') || {}).value || '');
  fd.append('confirm_mode', (document.getElementById('cf-confirm') || {}).value || 'preview');
  if (videos) for (const f of videos.files) fd.append('videos', f, f.name);
  if (images) for (const f of images.files) fd.append('images', f, f.name);
  // §39 创作状态人话（非技术步骤）
  const phrases = ['正在理解文案……', '正在设计镜头……', '正在匹配画面……', '正在生成草稿……'];
  let pi = 0;
  const phraseTimer = setInterval(() => {
    pi = (pi + 1) % phrases.length;
    if (CF.state === 'submitting' || CF.state === 'planning') status.textContent = phrases[pi];
  }, 3500);
  try {
    const ctrl = new AbortController();
    const tm = setTimeout(() => ctrl.abort(), 300000);
    const r = await apiUpload('/api/compose', fd, {signal: ctrl.signal});
    clearTimeout(tm);
    if (!r.ok && r.error) throw new Error(r.error);
    CF.state = r.pending_confirm ? 'awaiting_confirm' : 'editing';
    clearInterval(phraseTimer);
    closeCreate();
    if (r.pending_confirm) {
      editorToast('方案已生成，正在打开工作台……');
      workspaceOpen(r.project_id);
    } else {
      renderResult(r);
    }
    // 刷新项目页 Index（新项目立即可见）
    loadProjects();
  } catch (e) {
    CF.state = 'failed';
    clearInterval(phraseTimer);
    status.textContent = '生成失败：' + e.message + '（内容未保存，可调整后重试）';
    btn.disabled = false;
  }
}

/** 润色（走原 enhance API，面板内联呈现）。 */
async function createEnhance(){
  const script = (document.getElementById('cf-script') || {}).value;
  const status = document.getElementById('cf-enhance-status');
  if (!script.trim()) { status.textContent = '请先填文案'; return; }
  status.textContent = '润色中……';
  try {
    const r = await apiPost('/api/enhance', {script});
    if (!r.ok && r.error) throw new Error(r.error);
    document.getElementById('cf-enhance-box').style.display = '';
    document.getElementById('cf-enhanced').value = r.enhanced || script;
    status.textContent = r.changed ? 'AI 已润色（对比后采用）' : 'AI 认为原文已足够好，未改动';
  } catch (e) {
    status.textContent = '润色失败：' + e.message;
  }
}
function createAcceptEnhanced(){
  document.getElementById('cf-script').value = document.getElementById('cf-enhanced').value;
  document.getElementById('cf-enhance-box').style.display = 'none';
  document.getElementById('cf-enhance-status').textContent = '已采用润色结果';
}

/** §22 GFX 入口：文案 → 图形方案预览（在面板内联渲染 graphics.js 的方案区）。 */
function cfMakeGraphicPlan(){
  const script = (document.getElementById('cf-script') || {}).value || '';
  if (!script.trim()) { editorToast('先填文案，再生成图形方案', true); return; }
  const area = document.getElementById('cf-gfx-area');
  if (!area) return;
  area.style.display = '';
  // 图形方案链复用 graphics.js（同一方案生成/预览/确认逻辑，无第二套）
  const status = document.getElementById('gfx-status');
  if (status) status.textContent = '生成方案中……';
  GFX.content_type = 'knowledge';
  gfxMakePlanFrom(script);
  area.scrollIntoView({behavior: 'smooth'});
}

Object.assign(window, {
  openCreate, closeCreate, setCreateSource, createLoadDefaults,
  createPlatformSync, createProject, createEnhance, createAcceptEnhanced,
  cfMakeGraphicPlan, CF,
});
