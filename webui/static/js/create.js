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
  <div class="panel" style="margin-top:12px;padding:14px">
    <div style="display:flex;align-items:center;gap:8px">
      <b style="font-size:15px">AI 导演</b>
      <span class="hint">你只需要描述想做什么，其他步骤由 AI 自动编排</span>
    </div>
    <textarea id="cf-director-brief" style="min-height:82px;margin-top:8px"
      placeholder="例如：做一条 45 秒的视频，讲清楚为什么很多人越忙越没钱，口吻克制，画面以真实生活为主，适合抖音。"></textarea>
    <div class="btn-row" style="margin-top:8px;align-items:center">
      <button id="cf-ai-go" class="btn-sm" onclick="createProject()">AI 开始创作</button>
      <span class="hint">可以不写文案；AI 会自己完成文案、分镜、配音、找画面、字幕、音乐、预览与预检。</span>
    </div>
    <div id="cf-director-plan-preview" class="hint" style="margin-top:8px"></div>
  </div>
  <details id="cf-manual-options" style="margin-top:12px">
    <summary style="cursor:pointer;color:var(--muted);font-size:13px">手动输入与高级设置（AI 会自动决策，只有需要精确控制时才展开）</summary>
  <div id="cf-essentials">
    <label>主题（可选，交给 AI 写文案）</label>
    <div class="btn-row" style="align-items:center">
      <input type="text" id="cf-topic" class="rev-input" style="flex:1" placeholder="例：为什么很多人越忙越没有钱">
      <button class="btn-secondary btn-sm" onclick="cfGenerateScript()">AI 生成文案</button>
    </div>
    <div class="hint" id="cf-script-gen-status"></div>
    <label>文案</label>
    <textarea id="cf-script" style="min-height:110px" placeholder="粘贴你的文案；也可以先填主题，再让 AI 生成。"></textarea>
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
    <label>本地素材（视频 / 图片，可多选）</label>
    <input type="file" id="cf-assets" multiple accept=".mp4,.mov,.mkv,.webm,.m4v,.jpg,.jpeg,.png,.webp,.bmp">
    <div class="hint" id="cf-assets-summary">未添加本地素材</div>
    <div class="btn-row" style="align-items:center;gap:10px;margin-top:8px">
      <div style="flex:1"><label>画面来源</label>
        <select id="cf-visual-source" onchange="cfVisualSourceSync()">
          <option value="auto" selected>自动匹配（本地优先，缺画面再联网）</option>
          <option value="mixed">混合素材（本地 + 网络）</option>
          <option value="online">网络素材（按文案自动搜索）</option>
          <option value="local">仅使用我的素材</option>
        </select>
      </div>
      <span class="hint" id="cf-visual-source-hint">本地素材与网络素材共同参与匹配；需要时自动搜索 Pexels / Pixabay 补足画面。</span>
    </div>
    <div class="panel" style="margin-top:10px;padding:10px">
      <b style="font-size:13px">画面与时间线</b>
      <div class="btn-row" style="gap:10px;flex-wrap:wrap;margin-top:8px">
        <div><label>画面适配</label>
          <select id="cf-fit-mode"><option value="cover">铺满裁切</option><option value="contain">完整保留</option></select></div>
        <div><label>单段画面时长</label>
          <select id="cf-clip-duration"><option value="">自动</option><option value="3">3 秒</option><option value="5" selected>5 秒</option><option value="8">8 秒</option></select></div>
        <div><label>视频播放速度</label>
          <select id="cf-clip-speed"><option value="">自动</option><option value="0.9">0.9x</option><option value="1">1.0x</option><option value="1.2">1.2x</option><option value="1.5">1.5x</option></select></div>
        <div><label>素材顺序</label>
          <select id="cf-concat-mode"><option value="sequential">按素材顺序</option><option value="random">确定性随机</option></select></div>
        <div><label>场景转场</label>
          <select id="cf-transition-mode"><option value="cut">硬切</option><option value="crossfade">溶解</option></select></div>
        <label style="display:flex;align-items:flex-end;gap:6px;padding-bottom:5px"><input type="checkbox" id="cf-match-materials" checked> 按文案匹配画面</label>
      </div>
    </div>
  </div>
  </details>
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
        <div style="flex:1"><label>配音方式（§34 三模式）</label>
          <select id="cf-voice-mode" onchange="cfVoiceModeSync()">
            <option value="auto">自动配音（选音色）</option>
            <option value="upload">上传配音（mp3/wav/m4a）</option>
            <option value="silent">无配音（纯画面 + 字幕 + 音乐）</option>
          </select></div>
        <div style="flex:1" id="cf-voice-wrap"><label>音色（自动配音时）</label>
          <select id="cf-voice"></select></div>
      <div class="btn-row" style="gap:10px;margin-top:8px">
        <div style="flex:1;display:none" id="cf-voice-upload-wrap">
          <label>上传配音文件</label>
          <input type="file" id="cf-voice-file" accept=".mp3,.wav,.m4a,.aac">
        </div>
        <div style="flex:1"><label>语速</label>
          <select id="cf-voice-rate">
            <option value="0.9">0.9x 放慢</option>
            <option value="1.0" selected>1.0x 正常</option>
            <option value="1.3">1.3x 加快</option>
          </select></div>
        <div style="flex:1"><label>旁白音量</label>
          <input type="number" id="cf-voice-volume" value="1.0" min="0" max="2" step="0.05">
        </div>
        <div style="flex:1"><label>字幕</label>
          <select id="cf-subtitle">
            <option value="plain">普通字幕</option>
            <option value="karaoke">卡拉OK（逐词高亮）</option>
          </select></div>
      </div>
      <div class="btn-row" style="gap:10px;margin-top:8px">
        <div style="flex:1"><label>背景音乐</label>
          <select id="cf-bgm"></select></div>
        <div style="width:120px"><label>BGM 音量</label>
          <input type="number" id="cf-bgm-volume" value="0.3" min="0" max="2" step="0.05"></div>
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
      <div class="btn-row" style="gap:10px;margin-top:8px;flex-wrap:wrap">
        <div><label>输出帧率</label>
          <select id="cf-fps"><option value="24">24 fps</option><option value="30" selected>30 fps</option><option value="60">60 fps</option></select></div>
        <div style="flex:1"><label>输出分辨率</label>
          <select id="cf-resolution">
            <option value="1080x1920" selected>1080×1920</option>
            <option value="2160x3840">4K 竖屏 2160×3840</option>
            <option value="1920x1080">1920×1080</option>
            <option value="3840x2160">4K 横屏 3840×2160</option>
            <option value="1080x1080">1080×1080</option>
          </select></div>
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
    <button id="cf-go" onclick="createProject()">AI 开始创作</button>
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
      <div class="btn-row"><button class="btn-secondary btn-sm" onclick="cfAnalyzeReference()">分析参考视频</button>
      <span class="hint" id="cf-reference-status">分析报告（镜头结构/节奏/运动语言）会并入创作简报，指导成片风格；不复制参考内容。</span></div>
      <div id="cf-reference-result"></div>`;
  } else if (src === 'repo') {
    area.innerHTML = `
      <label>软件项目根目录（从代码库发现可验证的卖点，AI 不编造功能）</label>
      <input type="text" id="cf-repo-root" class="rev-input" placeholder="例：D:\\Projects\\my-app">
      <div class="btn-row"><button class="btn-secondary btn-sm" onclick="cfScanRepo()">扫描软件项目</button>
      <span class="hint" id="cf-repo-status">会扫描 README/CHANGELOG 等证据文件，可验证的卖点会并入创作简报。</span></div>
      <div id="cf-repo-result"></div>`;
  } else if (src === 'assets') {
    area.innerHTML = `
      <div class="hint">素材起步：先添加素材；文案请至少写一句创作说明，系统才能按场景匹配画面。</div>`;
  } else {
    area.innerHTML = '';
  }
}

/** 下拉/风格预设填充（单一数据源：与设置页同 API）。 */
async function createLoadDefaults(){
  const assetInput = document.getElementById('cf-assets');
  if (assetInput) {
    assetInput.onchange = cfRefreshAssetSummary;
    cfRefreshAssetSummary();
  }
  cfVisualSourceSync();
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

function cfRefreshAssetSummary(){
  const el = document.getElementById('cf-assets-summary');
  const input = document.getElementById('cf-assets');
  if (!el || !input) return;
  const files = Array.from(input.files || []);
  if (!files.length) { el.textContent = '未添加本地素材'; return; }
  const videos = files.filter(f => /\.(mp4|mov|mkv|webm|m4v)$/i.test(f.name)).length;
  const images = files.filter(f => /\.(jpg|jpeg|png|webp|bmp)$/i.test(f.name)).length;
  el.textContent = `已添加 ${files.length} 个：视频 ${videos} · 图片 ${images}`;
}

function cfVisualSourceSync(){
  const source = document.getElementById('cf-visual-source');
  const hint = document.getElementById('cf-visual-source-hint');
  const input = document.getElementById('cf-assets');
  const v = source ? source.value : 'auto';
  if (v === 'online') {
    if (hint) hint.textContent = '仅按文案联网搜索画面；本地素材不会进入本次自动匹配。';
  } else if (v === 'mixed') {
    if (hint) hint.textContent = '本地素材与网络素材一起参与匹配；网络素材用于补足场景。';
  } else if (v === 'local') {
    if (hint) hint.textContent = '只使用本地素材；缺少素材的场景会直接保留缺口。';
  } else {
    if (hint) hint.textContent = '有本地素材就优先使用；不足的场景自动搜索 Pexels / Pixabay。';
  }
  if (input) input.style.opacity = v === 'online' ? '0.55' : '1';
}

function createPlatformSync(){
  const map = {douyin: '1080x1920', bilibili: '1920x1080', xhs: '1080x1440', square: '1080x1080'};
  const p = (document.getElementById('cf-platform') || {}).value;
  if (p && map[p]) {
    window._cfAspect = map[p];
    const res = document.getElementById('cf-resolution');
    if (res && [...res.options].some(o => o.value === map[p])) res.value = map[p];
  }
}

/** §34 配音三模式联动：auto 显示音色下拉；upload 显示上传；silent 隐藏两者。 */
function cfVoiceModeSync(){
  const mode = (document.getElementById('cf-voice-mode') || {}).value || 'auto';
  const voiceWrap = document.getElementById('cf-voice-wrap');
  const uploadWrap = document.getElementById('cf-voice-upload-wrap');
  const rateWrap = document.getElementById('cf-voice-rate')?.parentElement;
  if (voiceWrap) voiceWrap.style.display = mode === 'auto' ? '' : 'none';
  if (uploadWrap) uploadWrap.style.display = mode === 'upload' ? '' : 'none';
  if (rateWrap) rateWrap.style.display = mode === 'auto' ? '' : 'none';
  if (mode === 'silent') {
    const st = document.getElementById('cf-status');
    if (st) st.textContent = '无配音模式：成片为纯画面 + 字幕 + 音乐（不会合成假旁白）';
  }
}

/** §37 参考视频 Source：分析 → 结果入简报（真实接线，非跳转提示）。 */
async function cfAnalyzeReference(){
  const f = document.getElementById('cf-reference');
  const st = document.getElementById('cf-reference-status');
  const out = document.getElementById('cf-reference-result');
  if (!f || !f.files || !f.files.length) { if (st) st.textContent = '请先选择参考视频'; return; }
  if (st) st.textContent = '分析中……（镜头检测 + 语法提取，约 10-40 秒）';
  const fd = new FormData();
  fd.append('reference', f.files[0], f.files[0].name);
  const r = await apiUpload('/api/reference', fd);
  if (!r.ok) { if (st) st.textContent = '分析失败：' + (r.error || '未知'); return; }
  const rep = r.report || {};
  const vl = rep.visual_language || {};
  const sg = (rep.shot_grammar || []).slice(0, 8);
  window._cfReferenceBrief = `参考视频风格：节奏 ${vl.pace || '—'} · 镜头密度 ${vl.density || '—'} · 运动语言 ${vl.motion || '—'} · 转场 ${vl.transition || '—'}${sg.length ? '；镜头结构参考 ' + sg.map(s => `${s.duration || 0}s`).join('/') : ''}`;
  out.innerHTML = `<div class="ok-box">分析完成 · ${sg.length} 个镜头 · 节奏 ${esc(String(vl.pace||'—'))} · 运动语言 ${esc(String(vl.motion||'—'))}
    <div class="hint">已并入创作简报——开始创作时会自动带上这段风格指导。</div></div>`;
  if (st) st.textContent = '分析完成，风格已并入创作简报。';
}
/** §37 软件项目 Source：扫描 → 证据卖点入简报。 */
async function cfScanRepo(){
  const root = (document.getElementById('cf-repo-root') || {}).value || '';
  const st = document.getElementById('cf-repo-status');
  const out = document.getElementById('cf-repo-result');
  if (!root.trim()) { if (st) st.textContent = '请填开发项目根目录'; return; }
  if (st) st.textContent = '扫描中……';
  const r = await apiPost('/api/promo/features', {root: root.trim()});
  if (!r.ok) { if (st) st.textContent = '扫描失败：' + (r.error || '目录无效'); return; }
  const claims = ((r.evidence_graph || {}).claims || []).filter(c => c.status === 'VERIFIED');
  if (!claims.length) {
    if (st) st.textContent = '未发现可验证的卖点（可补 README 后再扫，或切回文案起步）';
    window._cfRepoBrief = '';
    return;
  }
  window._cfRepoBrief = '产品卖点（已验证）：' + claims.slice(0, 6).map(c => c.text).join('；');
  out.innerHTML = `<div class="ok-box">发现 ${claims.length} 条可验证卖点（前 6 条已并入创作简报）：
    ${claims.slice(0, 6).map(c => `<div class="hint">· ${esc(c.text)}</div>`).join('')}</div>`;
  if (st) st.textContent = '卖点已并入创作简报。';
}

/** §38 状态机：提交 → WorkflowRun（§52 run_id + §53 真实步骤事件轮询）→ 工作台。 */
async function createProject(){
  const btn = document.getElementById('cf-go');
  const status = document.getElementById('cf-status');
  const script = (document.getElementById('cf-script') || {}).value || '';
  const brief = (document.getElementById('cf-director-brief') || {}).value || '';
  const assets = document.getElementById('cf-assets');
  const hasFiles = !!(assets && assets.files.length);
  const visualSource = (document.getElementById('cf-visual-source') || {}).value || 'auto';
  if (!brief.trim() && !script.trim() && !hasFiles) {
    status.textContent = '请告诉 AI 想做什么，或填写文案 / 添加素材';
    return;
  }
  if (!brief.trim() && !script.trim() && hasFiles) {
    status.textContent = '已添加素材，但还缺少创作意图：请在 AI 导演里用一句话说明怎么剪';
    return;
  }
  CF.state = 'submitting';
  btn.disabled = true;
  status.textContent = '正在提交创作任务……';
  // 创作简报：平台/时长/风格并入
  const briefParts = [];
  const tp = document.getElementById('cf-platform');
  if (tp && tp.value) briefParts.push('目标平台：' + tp.options[tp.selectedIndex].text);
  const td = document.getElementById('cf-duration');
  if (td && td.value) briefParts.push(`目标时长：约 ${td.value} 秒`);
  const st = document.getElementById('cf-style');
  if (st && st.value.trim()) briefParts.push('风格：' + st.value.trim());
  // 参考视频/软件项目 Source 分析结果并入简报
  const briefExtra = [];
  if (window._cfReferenceBrief) briefExtra.push(window._cfReferenceBrief);
  if (window._cfRepoBrief) briefExtra.push(window._cfRepoBrief);
  if (briefExtra.length) briefParts.push(briefExtra.join('；'));

  // 素材先落盘（WorkflowRun spec 走 JSON，文件经既有 /api/compose 的 multipart
  // 路径暂存后传路径——这里直接上传到项目暂存目录再引用）
  let videoNames = [], imageNames = [];
  try {
    if (hasFiles) {
      const fd = new FormData();
      for (const f of assets.files) fd.append('files', f, f.name);
      const imported = await apiUpload('/api/assets/upload', fd);
      if (imported && imported.ok) {
        videoNames = (imported.imported || []).filter(a => a.type === 'video' && a.file).map(a => a.file);
        imageNames = (imported.imported || []).filter(a => a.type === 'image' && a.file).map(a => a.file);
        if (imported.errors && imported.errors.length) {
          const warn = document.getElementById('cf-status');
          if (warn) warn.textContent = '部分素材未导入：' + imported.errors.join('；');
        }
      } else {
        CF.state = 'failed';
        status.textContent = '本地素材入库失败：' + ((imported && imported.error) || '无法写入素材库');
        btn.disabled = false;
        return;
      }
    }
  } catch (e) {
    CF.state = 'failed';
    status.textContent = '本地素材上传失败：' + (e.message || e);
    btn.disabled = false;
    return;
  }

  // §52 POST /api/workflows → run_id（不阻塞 HTTP；服务端后台执行）
  const resolution = (document.getElementById('cf-resolution') || {}).value || (window._cfAspect || '1080x1920');
  const aspect = resolution.split('x');
  // §34 三模式：auto=选音色；upload=上传配音文件；silent=无配音
  const voiceMode = (document.getElementById('cf-voice-mode') || {}).value || 'auto';
  const voiceFileEl = document.getElementById('cf-voice-file');
  if (voiceMode === 'upload' && (!voiceFileEl || !voiceFileEl.files.length)) {
    CF.state = 'failed';
    status.textContent = '上传配音模式请先选择配音文件（mp3/wav/m4a）';
    btn.disabled = false;
    return;
  }
  let uploadedNarration = '';
  if (voiceMode === 'upload' && voiceFileEl && voiceFileEl.files.length) {
    // 上传配音先进入统一 stage；WorkflowRun 会在项目初始化后执行 Probe→ASR/估算→Word Timing。
    const fd2 = new FormData();
    fd2.append('files', voiceFileEl.files[0], voiceFileEl.files[0].name);
    try {
      const st2 = await apiUpload('/api/assets/stage', fd2);
      if (st2 && st2.ok && (st2.audio || []).length) uploadedNarration = st2.audio[0];
    } catch (e) { /* 状态保留为失败，不静默改成自动配音 */ }
    if (!uploadedNarration) {
      CF.state = 'failed';
      status.textContent = '配音文件上传失败，可重试或切换自动配音';
      btn.disabled = false;
      return;
    }
  }
  const directorBrief = brief.trim();
  const requestOptions = {
    brief: directorBrief,
    script: script.trim(),
    creative_brief: briefParts.length ? '【创作简报】' + briefParts.join('；') : '',
    mode: 'auto',
    voice: voiceMode === 'auto' ? ((document.getElementById('cf-voice') || {}).value || 'zh-CN-XiaoxiaoNeural') : '',
    voice_mode: voiceMode,
    narration_upload: uploadedNarration,
    voice_rate: (document.getElementById('cf-voice-rate') || {}).value || '1.0',
    narration_volume: parseFloat((document.getElementById('cf-voice-volume') || {}).value || '1.0'),
    style_intent: (st || {}).value || '',
    width: parseInt(aspect[0]), height: parseInt(aspect[1]),
    resolution: (document.getElementById('cf-resolution') || {}).value || `${aspect[0]}x${aspect[1]}`,
    bgm: (document.getElementById('cf-bgm') || {}).value || 'auto',
    bgm_volume: parseFloat((document.getElementById('cf-bgm-volume') || {}).value || '0.3'),
    sfx_plan: null,
    subtitle_mode: (document.getElementById('cf-subtitle') || {}).value || 'plain',
    fit_mode: (document.getElementById('cf-fit-mode') || {}).value || 'cover',
    clip_duration: (() => { const v = (document.getElementById('cf-clip-duration') || {}).value; return v ? parseFloat(v) : null; })(),
    clip_speed: (() => { const v = (document.getElementById('cf-clip-speed') || {}).value; return v ? parseFloat(v) : null; })(),
    concat_mode: (document.getElementById('cf-concat-mode') || {}).value || 'sequential',
    transition_mode: (document.getElementById('cf-transition-mode') || {}).value || 'cut',
    match_materials_to_script: !!(document.getElementById('cf-match-materials') || {}).checked,
    fps: parseInt((document.getElementById('cf-fps') || {}).value || '30'),
    watermark: (document.getElementById('cf-watermark') || {}).value || '',
    lower_third_name: (document.getElementById('cf-lower-name') || {}).value || '',
    lower_third_role: (document.getElementById('cf-lower-role') || {}).value || '',
    confirm_mode: (document.getElementById('cf-confirm') || {}).value || 'preview',
    visual_source: visualSource,
    auto_confirm: ((document.getElementById('cf-confirm') || {}).value || 'preview') === 'direct',
    video_paths: videoNames, image_paths: imageNames,
  };
  const wfResp = directorBrief
    ? await apiPost('/api/director/create', {
        ...requestOptions,
        brief: directorBrief,
        platform: (document.getElementById('cf-platform') || {}).value || '',
        duration: (document.getElementById('cf-duration') || {}).value || '',
        style: (document.getElementById('cf-style') || {}).value || '',
      })
    : await apiPost('/api/workflows', {kind: 'compose', spec: requestOptions});
  if (!wfResp.ok || !wfResp.run_id) {
    CF.state = 'failed';
    status.textContent = '任务提交失败：' + (wfResp.error || '未知') + '（内容未保存，可重试）';
    btn.disabled = false;
    return;
  }
  // §53 真实事件流：轮询 WorkflowRun 步骤（持久化状态，不是动画短语）
  const runId = wfResp.run_id;
  const pollTimer = setInterval(async () => {
    try {
      const run = await apiGet('/api/workflows/' + encodeURIComponent(runId));
      if (!run || run.error) return;
      const labels = run.labels || {};
      const cur = run.current ? (labels[run.current] || run.current) : '';
      const doneSteps = (run.steps || []).filter(s => s.status === 'DONE').length;
      const totalSteps = (run.steps || []).length;
      status.textContent = run.status === 'RUNNING' || run.status === 'WAITING_CONFIRMATION'
        ? `${cur ? cur + '……' : '处理中……'}（${doneSteps}/${totalSteps} 步）`
        : status.textContent;
      if (run.status === 'COMPLETED' || run.status === 'WAITING_CONFIRMATION') {
        clearInterval(pollTimer);
        const result = run.result || {};
        CF.state = run.status === 'WAITING_CONFIRMATION' ? 'awaiting_confirm' : 'editing';
        closeCreate();
        if (run.status === 'WAITING_CONFIRMATION' && result.project_id) {
          editorToast('方案已生成，正在打开工作台……');
          await workspaceOpen(result.project_id);
          await directorLoadRun(runId);
        } else if (result.project_id) {
          await workspaceOpen(result.project_id);
          await directorLoadRun(runId);
          editorToast('创作完成，已打开工作台');
        }
        loadProjects();
      } else if (run.status === 'FAILED') {
        clearInterval(pollTimer);
        CF.state = 'failed';
        status.textContent = '生成失败：' + (run.error || '未知') + '（内容未保存，可调整后重试）';
        btn.disabled = false;
      }
    } catch (e) { /* 轮询失败下次再试 */ }
  }, 1500);
}

/** 润色（走原 enhance API，面板内联呈现）。 */
async function cfGenerateScript(){
  const topic = (document.getElementById('cf-topic') || {}).value || '';
  const status = document.getElementById('cf-script-gen-status');
  if (!topic.trim()) { if (status) status.textContent = '请先填写主题'; return; }
  const btn = document.querySelector('#cf-topic + button');
  if (btn) btn.disabled = true;
  if (status) status.textContent = '正在生成文案……';
  try {
    const platform = (document.getElementById('cf-platform') || {}).value || '';
    const duration = (document.getElementById('cf-duration') || {}).value || '';
    const style = (document.getElementById('cf-style') || {}).value || '';
    const r = await apiPost('/api/script/generate', {topic: topic.trim(), platform, duration, style});
    if (!r.ok) throw new Error(r.error || '生成失败');
    const ta = document.getElementById('cf-script');
    if (ta) ta.value = r.script || '';
    if (status) status.textContent = '文案已生成，可直接修改后创作。';
  } catch (e) {
    if (status) status.textContent = '生成失败：' + (e.message || e);
  } finally {
    if (btn) btn.disabled = false;
  }
}

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
  cfMakeGraphicPlan, cfAnalyzeReference, cfScanRepo, cfVoiceModeSync, CF,
});
