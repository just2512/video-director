// §37/§38/§62 Create Flow（方案《FRONTEND_DEEP_UNIFICATION》）：
// 快速创作与完整创作不再是两套 UX——统一创建面板：
//   「从哪里开始？」：文案 / 素材 / 参考视频 / 软件项目（Create Source）
//   Essentials：文案 · 平台 · 时长 · 风格 · 素材
//   More Settings（折叠）：配音 · 字幕 · BGM · SFX · 水印 · 人物条
// 状态机：idle → editing → submitting → planning → awaiting_confirm → failed
// 创建成功 → openProject → workspace（§62 不再让 Create 自带结果编辑器）。

window.CF = {
  state: 'idle',          // idle|editing|submitting|planning|awaiting_confirm|failed
  source: 'script',       // UI picker only; never changes the Director workflow
  explicitFields: new Set(),
};

// A control's markup value is a default, not necessarily a user decision. Track only
// genuine UI input so natural-language intent can override untouched defaults.
const CF_EXPLICIT_CONTROL_FIELDS = {
  'cf-platform': 'platform',
  'cf-duration': 'duration',
  'cf-style': 'style',
  'cf-resolution': 'resolution',
  'cf-voice': 'voice',
  'cf-voice-mode': 'voice_mode',
  'cf-voice-rate': 'voice_rate',
  'cf-voice-volume': 'narration_volume',
  'cf-bgm': 'bgm',
  'cf-bgm-volume': 'bgm_volume',
  'cf-sfx': 'sfx',
  'cf-subtitle': 'subtitle_mode',
  'cf-fit-mode': 'fit_mode',
  'cf-clip-duration': 'clip_duration',
  'cf-clip-speed': 'clip_speed',
  'cf-concat-mode': 'concat_mode',
  'cf-transition-mode': 'transition_mode',
  'cf-watermark': 'watermark',
  'cf-lower-name': 'lower_third_name',
  'cf-lower-role': 'lower_third_role'
};

function cfMarkExplicitControl(event) {
  const target = event.target;
  if (!target || !target.id || !CF_EXPLICIT_CONTROL_FIELDS[target.id]) return;
  if (CF.state !== 'editing') return;
  CF.explicitFields.add(CF_EXPLICIT_CONTROL_FIELDS[target.id]);
}
document.addEventListener('input', cfMarkExplicitControl, true);
document.addEventListener('change', cfMarkExplicitControl, true);
document.addEventListener('click', function(event) {
  // Style cards set the text field programmatically, so mark the choice explicitly too.
  const styleButton = event.target && event.target.closest
    ? event.target.closest('[data-studio-style]') : null;
  if (styleButton && CF.state === 'editing') {
    CF.explicitFields.add('style');
  }
});

/** 打开创建面板（顶栏「+ 新建项目」唯一入口 §3.1）。 */
function openCreate(projectId){
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
  document.body.appendChild(mask);
  CF.state = 'editing';
  CF.explicitFields = new Set();
  createLoadDefaults();
  cfRestoreArticleSourceUI();
  // 2026-10-25 用户裁决：随时可回到新建项目对话框——带 projectId 时回填
  // 该项目的创作要求/文案（素材在项目里保留，file input 无法回填但清单提示）。
  if (projectId) cfPrefillFromProject(projectId);
  const ta = document.getElementById('cf-script');
  if (ta) ta.focus();
}

/** 从已有项目回填表单（修改要求重新创作的入口）。 */
async function cfPrefillFromProject(pid){
  const status = document.getElementById('cf-status');
  try {
    const brief = await apiPost('/api/director/brief', {project_id: pid});
    const bundle = (brief && brief.ok && brief.bundle) || {};
    if (brief && brief.ok && brief.brief) {
      const el = document.getElementById('cf-director-brief');
      if (el) el.value = brief.brief;
    }
    // 文案：持久化 bundle.script.text 优先（director_inputs.json 是权威要求源）
    // 项目恢复必须沿用同一份素材池与结构化参考输入，不能只显示一个文件名摘要。
    const refs = (bundle.assets && bundle.assets.asset_refs) || [];
    window.CF_DRAFT_CONTEXT = window.CF_DRAFT_CONTEXT || {assetRefs:[]};
    window.CF_DRAFT_CONTEXT.assetRefs = refs;
    window.CF_DRAFT_CONTEXT.projectId = pid;
    window._cfReference = (bundle.references || [])[0] || null;
    window._cfArticle = (bundle.articles || [])[0] || null;
    window._cfRepo = bundle.software_project || null;
    window.CF_DRAFT_CONTEXT.reference = window._cfReference;
    window.CF_DRAFT_CONTEXT.article = window._cfArticle;
    window.CF_DRAFT_CONTEXT.repo = window._cfRepo;
    const spec = (brief && brief.ok && brief.spec_settings) || {};
    const settingFields = {
      'cf-duration': spec.duration,
      'cf-style': spec.style_intent,
      'cf-voice-mode': spec.voice_mode,
      'cf-voice': spec.voice,
      'cf-voice-rate': spec.voice_rate,
      'cf-voice-volume': spec.narration_volume,
      'cf-subtitle': spec.subtitle_mode,
      'cf-bgm': spec.bgm,
      'cf-bgm-volume': spec.bgm_volume,
      'cf-sfx': spec.sfx,
      'cf-fit-mode': spec.fit_mode,
      'cf-clip-duration': spec.clip_duration,
      'cf-clip-speed': spec.clip_speed,
      'cf-concat-mode': spec.concat_mode,
      'cf-transition-mode': spec.transition_mode,
      'cf-stop-at': spec.stop_at,
      'cf-fps': spec.fps,
      'cf-watermark': spec.watermark,
      'cf-lower-name': spec.lower_third_name,
      'cf-lower-role': spec.lower_third_role,
      'cf-confirm': spec.confirm_mode,
      'cf-visual-source': spec.visual_source
    };
    for (const [id,value] of Object.entries(settingFields)) {
      const el=document.getElementById(id);
      if(el && value !== undefined && value !== null) el.value=String(value);
    }
    if (spec.width && spec.height) {
      const resolution=document.getElementById('cf-resolution');
      if(resolution) resolution.value=String(spec.width)+'x'+String(spec.height);
      const aspect=String(spec.width)+'x'+String(spec.height);
      const platformByAspect={'1080x1920':'douyin','1920x1080':'bilibili','1080x1440':'xhs','1080x1080':'square'};
      const platform=document.getElementById('cf-platform');
      if(platform && platformByAspect[aspect]) platform.value=platformByAspect[aspect];
    }
    if (window.CF) {
      window.CF.explicitFields = new Set(Object.keys(CF_EXPLICIT_CONTROL_FIELDS).map(id => CF_EXPLICIT_CONTROL_FIELDS[id])
        .filter(field => settingFields[Object.keys(CF_EXPLICIT_CONTROL_FIELDS).find(id => CF_EXPLICIT_CONTROL_FIELDS[id] === field)] !== undefined));
    }
    const scriptText = (bundle.script && bundle.script.text) || '';
    const ta = document.getElementById('cf-script');
    if (scriptText && ta) ta.value = scriptText;
    // 素材：file input 无法回填，但列出已入库素材清单（重新创作沿用，无需重传）
    if (refs.length) {
      const names = refs.map(r => {
        const f = r && r.file ? String(r.file).split(/[\\/]/).pop() : '';
        return f || (r && r.asset_id) || '';
      }).filter(Boolean);
      const summary = document.getElementById('cf-assets-summary');
      if (summary) summary.textContent = '已入库素材 ' + refs.length + ' 个：' + names.join('、') + '（重新创作沿用，无需重新上传）';
      if (window.CF_DRAFT_CONTEXT) window.CF_DRAFT_CONTEXT.assetRefs = refs;
    }
    // bundle 缺失时用 project_detail 兜底补文案（兼容旧流程项目）
    if (!scriptText) {
      const detail = await apiGet('/api/project_detail?project_id=' + encodeURIComponent(pid));
      if (detail && !detail.error && detail.script) {
        const ta2 = document.getElementById('cf-script');
        if (ta2 && !ta2.value) ta2.value = detail.script;
      }
    }
    if (window._cfArticle && typeof cfRenderArticleSource === 'function')
      cfRenderArticleSource(window._cfArticle, '已恢复项目关联文章资料。');
    if (window._cfReference) {
      const node=document.getElementById('cf-reference-result');
      if(node)node.innerHTML='<div class="ok-box">已恢复项目关联参考视频与分析结果。</div>';
    }
    if (window._cfRepo) {
      const node=document.getElementById('cf-repo-result');
      if(node)node.innerHTML='<div class="ok-box">已恢复软件项目证据。</div>';
    }
    if (window.CF && window.CF.explicitFields) {
      for (const field of Object.values(CF_EXPLICIT_CONTROL_FIELDS)) {
        if (settingFields[Object.keys(CF_EXPLICIT_CONTROL_FIELDS).find(id => CF_EXPLICIT_CONTROL_FIELDS[id] === field)] !== undefined)
          window.CF.explicitFields.add(field);
      }
    }
    if (typeof cfVoiceModeSync === 'function') cfVoiceModeSync();
    if (typeof cfVisualSourceSync === 'function') cfVisualSourceSync();
    if (typeof window.CF_DRAFT_CONTEXT !== 'undefined') window.CF_DRAFT_CONTEXT.projectId = pid;
    if (typeof window.CF_DRAFT_CONTEXT !== 'undefined') window.CF_DRAFT_CONTEXT.assetRefs = refs;
    if (typeof window.CF_DRAFT_CONTEXT !== 'undefined') await apiPost('/api/director/drafts/active', {data:{
      version:1,fields:(function(){const out={};document.querySelectorAll('#cf-form input[id^="cf-"],#cf-form textarea[id^="cf-"],#cf-form select[id^="cf-"]').forEach(el=>{if(el.type==='file')return;if(el.type==='checkbox')out[el.id]=el.checked;else if(el.type==='radio'){if(el.checked)out[el.name||el.id]=el.value;}else out[el.id]=el.value;});return out;})(),
      context:{asset_refs:refs,reference:window._cfReference,article:window._cfArticle,repo:window._cfRepo,project_id:pid}
    }});
    if (status) status.textContent =
      '已载入项目「' + pid + '」的创作要求、文案、素材与原始制作参数。修改后将创建新版本。';
  } catch (e) {
    if (status) status.textContent = '项目信息载入部分失败（要求/文案仍可手动填写）。';
  }
}
function closeCreate(){
  if (_cfProgressTimer) { clearInterval(_cfProgressTimer); _cfProgressTimer = null; }
  const m = document.getElementById('cf-mask');
  if (m) m.remove();
  // 创建页是模板/风格画廊的宿主：创建页关闭时子画廊的 fixed 全屏遮罩必须一并
  // 移除，否则 sg-mask/tg-mask 会留在 body 上拦截整个工作台的指针事件。
  for (const id of ['sg-mask', 'tg-mask']) {
    const child = document.getElementById(id);
    if (child) child.remove();
  }
  CF.state = 'idle';
}

function _createPanelHtml(){
  return `
  <div id="cf-form">
  <div style="display:flex;align-items:center;gap:10px">
    <h3 style="font-size:17px;margin:0">新建项目</h3>
    <span style="flex:1"></span>
    <button class="btn-secondary btn-sm" onclick="closeCreate()">收起</button>
  </div>
  <div class="panel" style="margin-top:12px;padding:14px">
    <div style="display:flex;align-items:center;gap:8px">
      <b style="font-size:15px">AI 导演</b>
      <span class="hint">你只需要描述想做什么，其他步骤由 AI 自动编排</span>
    </div>
    <textarea id="cf-director-brief" style="min-height:82px;margin-top:8px"
      placeholder="例如：做一条 45 秒的视频，讲清楚为什么很多人越忙越没钱，口吻克制，画面以真实生活为主，适合抖音。"></textarea>
    <div class="hint" style="margin-top:8px">可以只写创作要求，也可以同时提供权威文案、素材与参考资料。AI 负责其余导演工作。</div>
    <div id="cf-director-plan-preview" class="hint" style="margin-top:8px"></div>
  </div>
  <div class="panel" style="margin-top:10px;padding:12px">
    <div class="btn-row" style="align-items:center">
      <b>输入上下文</b><span class="hint">文案、素材、参考视频、软件项目可以同时提供，也可以只提供其中一部分。</span>
    </div>
    <div class="panel" style="margin-top:8px;padding:10px">
      <b>文案</b><span class="hint"> 可选；填写后作为权威文案</span>
      <textarea id="cf-script" style="min-height:110px;margin-top:6px" placeholder="粘贴你已经写好的文案；没有文案也可以留空，让 AI 根据目标和其他输入生成。"></textarea>
      <div class="hint" style="margin-top:5px">支持 [pause: 2s] / [停顿: 2s] / [silence: 1s]；停顿会真正进入旁白、字幕和镜头时间轴（0.1–10 秒）。</div>
    </div>
    <div class="panel" style="margin-top:8px;padding:10px">
      <b>文章链接</b><span class="hint"> 可选；提取公开网页正文作为创作资料，不会自动覆盖你已写的文案</span>
      <div class="btn-row" style="margin-top:6px;align-items:center">
        <input type="url" id="cf-article-url" class="rev-input" placeholder="文章 / GitHub 仓库地址均可，如 https://github.com/owner/repo" style="flex:1;min-width:180px">
        <button type="button" id="cf-import-article-btn" class="btn-secondary btn-sm" onclick="cfImportArticle()">导入文章</button>
        <button type="button" id="cf-clear-article-btn" class="btn-secondary btn-sm" onclick="cfClearArticleSource()" disabled>移除</button>
      </div>
      <div id="cf-article-status" class="hint" role="status" aria-live="polite">仅抓取公网 HTTP/HTTPS 页面；不访问内网、登录态或本机服务。</div>
      <div id="cf-article-result"></div>
    </div>
    <div class="panel" style="margin-top:8px;padding:10px">
      <b>我的素材</b><span class="hint"> 可选；图片 / 视频可多选</span>
      <input type="file" id="cf-assets" multiple accept=".mp4,.mov,.mkv,.webm,.m4v,.jpg,.jpeg,.png,.webp,.bmp" style="margin-top:6px">
      <div class="hint" id="cf-assets-summary">未添加本地素材</div>
    </div>
    <div class="panel" style="margin-top:8px;padding:10px">
      <b>参考视频</b><span class="hint"> 可选；只用于分析节奏、镜头语言和风格</span>
      <input type="file" id="cf-reference" accept=".mp4,.mov,.mkv,.webm" style="margin-top:6px">
      <div class="btn-row" style="margin-top:6px">
        <button class="btn-secondary btn-sm" onclick="cfAnalyzeReference()">分析参考视频</button>
        <span class="hint" id="cf-reference-status">分析完成后加入导演上下文。</span>
      </div>
      <div id="cf-reference-result"></div>
    </div>
    <div class="panel" style="margin-top:8px;padding:10px">
      <b>软件项目</b><span class="hint"> 可选；AI 只使用已验证的功能事实</span>
      <input type="text" id="cf-repo-root" class="rev-input" placeholder="例：D:\Projects\my-app（本地项目路径）" style="margin-top:6px">
      <div class="btn-row" style="margin-top:6px">
        <button class="btn-secondary btn-sm" onclick="cfScanRepo()">扫描软件项目</button>
        <span class="hint" id="cf-repo-status">扫描 README / CHANGELOG 等证据文件。</span>
      </div>
      <div id="cf-repo-result"></div>
    </div>
  </div>
  <div class="panel" style="margin-top:10px;padding:12px">
    <div class="btn-row" style="align-items:center">
      <b>创作范围</b>
      <label style="margin:0"><input type="radio" name="cf-scope" value="single" checked onchange="cfSeriesSync()"> 单条视频</label>
      <label style="margin:0"><input type="radio" name="cf-scope" value="series" onchange="cfSeriesSync()"> 系列视频</label>
    </div>
    <div id="cf-series-options" style="display:none;margin-top:8px">
      <label>系列名称</label>
      <input type="text" id="cf-series-name" placeholder="例：AI 工具实测系列">
      <div class="hint" style="margin-top:4px">每集文案用一行 <code>------------</code> 分隔；上面的素材、参考视频和软件项目可作为共享上下文。</div>
    </div>
  </div>
  <div class="panel" style="margin-top:10px;padding:12px">
    <div class="btn-row" style="align-items:center;gap:10px"><button id="cf-template-gallery-btn" class="btn-secondary btn-sm" type="button" onclick="templateGalleryOpen()">浏览模板与真实样片</button><span class="hint">模板独立于风格；可先查看必填素材、未实现效果与真实样片状态。</span></div>
  </div>

    <div class="panel" style="margin-top:8px;padding:10px">
      <div class="btn-row" style="align-items:center">
        <b>Studio：风格与执行引擎</b>
        <span class="hint" id="cf-studio-catalog-status" role="status" aria-live="polite">正在核验本机可执行能力……</span>
      </div>
      <div class="btn-row" style="gap:10px;align-items:center;margin-top:7px">
        <div style="flex:1;min-width:180px">
          <label for="cf-render-engine">最终视频引擎</label>
          <select id="cf-render-engine"><option value="ffmpeg">FFmpeg · 当前生产渲染器</option></select>
        </div>
        <div id="cf-studio-graphics-status" class="hint" style="flex:2">动态图形引擎状态将由运行环境实际检查。</div>
      </div>
      <div id="cf-style-presets" class="btn-row" style="flex-wrap:wrap;gap:6px;font-size:12px;color:var(--muted);margin-top:7px"></div>
      <div id="cf-style-contract-detail" class="hint" style="margin-top:5px">只显示由现有 StyleContract 执行的风格。</div>
      <div id="cf-studio-template-list" class="hint" style="margin-top:7px"></div>
    </div>
  <details id="cf-manual-options" style="margin-top:12px">
    <summary style="cursor:pointer;color:var(--muted);font-size:13px">手动输入与高级设置（AI 会自动决策，只有需要精确控制时才展开）</summary>
  <div id="cf-essentials">
    <div class="hint" id="cf-script-gen-status"></div>
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
        <input type="text" id="cf-style" placeholder="例：纪录片沉稳，或点选可执行风格"></div>
    </div>
    <div class="hint">素材已移到上面的「输入上下文」区。</div>
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
      <b style="font-size:13px">创作检查点</b>
      <div class="btn-row" style="gap:10px;flex-wrap:wrap;margin-top:6px">
        <div><label>自动做到</label>
          <select id="cf-stop-at">
            <option value="video" selected>草稿预览后停住（默认）</option>
            <option value="materials">先准备好素材</option>
            <option value="subtitle">先生成字幕</option>
            <option value="audio">先生成旁白</option>
            <option value="script">先完成文案</option>
            <option value="terms">先完成分镜（审分镜后再继续）</option>
          </select>
        </div>
      </div>
      <div class="hint">停在检查点不会丢掉已经生成的产物；之后从项目继续，不需要从头生成。</div>
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
  </div>
  </div>`;
}

/** §37（已废弃 2026-10-07）：旧 source-picker 单值输入模型被并行输入卡取代。
 * setCreateSource/cf-source-area 动态模板整段删除——它的动态 HTML 与并行
 * 输入卡渲染同名 id（cf-reference/cf-repo-root…），既造成重复控件又让
 * DOM 查询错位。四张并行输入卡见 _createPanelHtml「输入上下文」区。 */

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
    _rebuildSelect('cf-bgm', [['auto', '自动按内容与风格匹配现有音乐'], ['', '（不配乐）'],
                              ...bgms.map(b => [b.id, b.label])]);
    const sfxs = await apiGet('/api/sfx');
    _rebuildSelect('cf-sfx', [['auto', '自动（本地音效库）'],
                              ['openverse-auto', '自动搜索匹配（Openverse 免Key · freesound · CC0）'],
                              ['', '关闭'],
                              ...sfxs.map(s => [s.id, s.label])]);
  } catch (e) { /* 下拉加载失败不阻塞创建 */ }
  // 风格只使用 Studio 能力目录返回的可执行 StyleContract，避免两个来源互相覆盖。
  await cfLoadStudioCatalog();
}

const CF_STYLE_NAMES = {
  'documentary-calm':'沉稳纪录片',
  'knowledge-explainer':'知识讲解',
  'energetic-social':'快节奏社交视频',
  'cinematic-story':'电影叙事'
};
let CF_STUDIO_CATALOG = null;
async function cfLoadStudioCatalog(){
  const status=document.getElementById('cf-studio-catalog-status');
  const box=document.getElementById('cf-style-presets');
  const detail=document.getElementById('cf-style-contract-detail');
  const graphics=document.getElementById('cf-studio-graphics-status');
  const templates=document.getElementById('cf-studio-template-list');
  try{
    const catalog=await apiGet('/api/studio/catalog');
    if(!catalog||!catalog.ok)throw new Error((catalog&&catalog.error)||'目录服务未就绪');
    CF_STUDIO_CATALOG=catalog;
    window.CF_STUDIO_CATALOG=catalog;
    const engineSelect=document.getElementById('cf-render-engine');
    if(engineSelect){
      const available=(catalog.engines||[]).filter(e=>e.scope==='full_video'&&e.selectable);
      const previous=engineSelect.value;
      engineSelect.innerHTML=available.length
        ?available.map(e=>'<option value="'+esc(e.id)+'">'+esc(e.name)+' · 可执行</option>').join('')
        :'<option value="">没有可执行的最终视频引擎</option>';
      if(available.some(e=>e.id===previous))engineSelect.value=previous;
      engineSelect.disabled=!available.length;
    }
    if(status)status.textContent=(catalog.engines||[]).some(e=>e.scope==='full_video'&&e.selectable)
      ?'已核验当前可执行的成片引擎':'未找到可执行的成片引擎，提交会被拒绝';
    const graphicEngines=(catalog.engines||[]).filter(e=>e.scope==='graphics_layer');
    if(graphics)graphics.textContent='动态图形：'+(graphicEngines.length
      ?graphicEngines.map(e=>e.name+' '+e.status+'（'+e.detail+'）').join('；')
      :'没有声明的图形执行器');
    const availableStyles=(catalog.styles||[]).filter(item=>item.selectable);
    if(box){
      box.innerHTML='<span>可执行风格：</span>'+(
        availableStyles.length?availableStyles.map(item=>{
          const label=CF_STYLE_NAMES[item.id]||item.id;
          const contract=item.contract||{};
          const description=(item.detail||'')+' '+Object.entries(contract).map(([k,v])=>k+': '+v).join(' · ');
          return '<button type="button" class="btn-secondary btn-sm" data-studio-style="'+esc(item.id)+'" title="'+esc(description)+'">'+esc(label)+'</button>';
        }).join(''):'<span>无可执行风格</span>');
    }
    if(detail)detail.textContent=availableStyles.length
      ?'所选风格由 StyleContract 写入正式生成链：字幕布局/字号、图片运动、转场预算、配乐音量和音效密度。'
      :'风格契约未通过环境检查，不提供未验证选项。';
    if(templates){
      const all=catalog.templates||[];
      templates.innerHTML=all.length
        ?'<div class="cf-studio-template-heading">模板目录 · 点击可执行模板进入素材填写与真实样片验证</div>'+
          all.map(t=>{
            const ready=!!t.selectable&&!!t.can_instantiate;
            const status=t.status==='BLOCKED'?'阻断：依赖/引擎未满足':
              t.status==='PARTIAL'?'可实例化：部分效果尚未实现':
              t.status==='READY'?'依赖通过：样片仍需验证':(t.status||'状态未知');
            const name=esc(t.name||t.id||'未命名模板');
            const detail=esc(t.detail||'');
            const button=ready
              ?'<button type="button" class="btn-secondary btn-sm" data-studio-template="'+esc(t.id)+'" data-template-version="'+esc(t.version||'')+'">使用此模板</button>'
              :'<button type="button" class="btn-secondary btn-sm" disabled>暂不可用</button>';
            return '<div class="cf-studio-template-row"><div class="cf-studio-template-title"><strong>'+name+'</strong><span>'+esc(status)+'</span></div>'+
              '<small>'+detail+'</small>'+button+'</div>';
          }).join('')
        :'当前没有注册模板';
    }
  }catch(error){
    if(status)status.textContent='引擎/风格目录读取失败：'+(error.message||error);
    if(box)box.textContent='未能核验风格执行能力；不会回退到未验证的风格列表。';
    if(templates)templates.textContent='';
  }
}
document.addEventListener('click',function(event){
  const templateButton=event.target.closest('[data-studio-template]');
  if(!templateButton)return;
  cfOpenStudioTemplate(templateButton.dataset.studioTemplate,templateButton.dataset.templateVersion||'');
});

async function cfOpenStudioTemplate(templateId, templateVersion){
  const status=document.getElementById('cf-studio-catalog-status');
  try{
    if(typeof templateGalleryOpen!=='function'||typeof templateGallerySelectByIndex!=='function'){
      throw new Error('模板画廊尚未加载');
    }
    await templateGalleryOpen();
    const visible=(window.TG&&window.TG.visible)||[];
    const index=visible.findIndex(item=>String(item.id)===String(templateId)&&String(item.version||'')===String(templateVersion||''));
    if(index<0)throw new Error('模板已不在当前清单中，请刷新创建面板后重试');
    templateGallerySelectByIndex(index);
    const selected=visible[index];
    const galleryStatus=document.getElementById('tg-status');
    if(galleryStatus)galleryStatus.textContent='已定位到 '+(selected.name||selected.id)+'；请填素材并执行真实样片验证。';
  }catch(error){
    if(status)status.textContent='打开模板失败：'+(error.message||error);
    if(typeof editorToast==='function')editorToast('打开模板失败：'+(error.message||error),true);
  }
}

document.addEventListener('click',function(event){
  const button=event.target.closest('[data-studio-style]');
  if(!button)return;
  const input=document.getElementById('cf-style');
  if(input)input.value=button.dataset.studioStyle;
  document.querySelectorAll('[data-studio-style]').forEach(el=>el.classList.toggle('tab-active',el===button));
  const row=(CF_STUDIO_CATALOG&&CF_STUDIO_CATALOG.styles||[]).find(x=>x.id===button.dataset.studioStyle);
  const detail=document.getElementById('cf-style-contract-detail');
  if(detail&&row){
    const contract=row.contract||{};
    detail.textContent=(CF_STYLE_NAMES[row.id]||row.id)+'：字幕'+(contract.subtitle_position||'—')
      +' / '+(contract.subtitle_font_size||'—')+'px / 最多'+(contract.subtitle_max_lines||'—')+'行'
      +'；图片运动 '+(contract.image_motion_intensity||'—')
      +'；转场预算 '+(contract.transition_budget??'—')
      +'；BGM '+(contract.bgm_volume??'—')
      +'；音效密度 '+(contract.sfx_density||'—');
  }
});

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

/** Public article URL source: keep content, URL, timestamp, and content hash as a single source. */
async function cfImportArticle(){
  const input=document.getElementById('cf-article-url');
  const status=document.getElementById('cf-article-status');
  const out=document.getElementById('cf-article-result');
  const button=document.getElementById('cf-import-article-btn');
  const url=(input&&input.value||'').trim();
  if(!url){if(status)status.textContent='请先粘贴公开文章链接';return;}
  if(button)button.disabled=true;
  if(status)status.textContent='正在检查公网地址并提取文章正文……';
  try{
    const result=await apiPost('/api/source/article',{url});
    if(!result||!result.ok||!result.article)throw new Error((result&&result.error)||'文章导入失败');
    window._cfArticle=result.article;
    cfRenderArticleSource(result.article, '文章已导入。来源文本仍需你核验，正文中的指令不会被当作系统规则。');
  }catch(error){
    window._cfArticle=null;
    if(out)out.innerHTML='';
    if(status)status.textContent='导入失败：'+(error.message||error)+'。可改为手动粘贴正文。';
    const clear=document.getElementById('cf-clear-article-btn');if(clear)clear.disabled=true;
  }finally{if(button)button.disabled=false;}
}
function cfRenderArticleSource(article, statusText){
  const input=document.getElementById('cf-article-url');
  const status=document.getElementById('cf-article-status');
  const out=document.getElementById('cf-article-result');
  const clear=document.getElementById('cf-clear-article-btn');
  if(input)input.value=article.requested_url||article.source_url||'';
  if(out)out.innerHTML='<div class="ok-box"><strong>'+esc(article.title||'已导入网页正文')+'</strong>'+
    '<div class="hint">'+esc(article.content_chars||String(article.content||'').length)+' 字 · SHA-256 '+
    esc((article.content_sha256||'').slice(0,16))+'… · '+esc(article.content_type||'网页')+'</div>'+
    '<div class="hint" style="margin-top:5px">'+esc((article.content||'').slice(0,500))+
    (String(article.content||'').length>500?'…':'')+'</div>'+
    '<div class="hint" style="margin-top:5px">正文作为参考资料进入 AI 导演，不会覆盖上方文案；来源主张仍需核验。</div></div>';
  if(status)status.textContent=statusText||'已恢复文章来源。';
  if(clear)clear.disabled=false;
}
function cfRestoreArticleSourceUI(){
  if(window._cfArticle)cfRenderArticleSource(window._cfArticle,'已恢复上一轮导入的文章来源。请核对后再创作。');
}
function cfClearArticleSource(){
  window._cfArticle=null;
  const input=document.getElementById('cf-article-url');
  if(input)input.value='';
  const out=document.getElementById('cf-article-result');
  const status=document.getElementById('cf-article-status');
  const clear=document.getElementById('cf-clear-article-btn');
  if(out)out.innerHTML='';
  if(status)status.textContent='已移除文章来源。';
  if(clear)clear.disabled=true;
}

/** §37 参考视频输入：分析结果保留为结构化 Director Input，不再压扁成简报字符串。 */
async function cfAnalyzeReference(){
  const f = document.getElementById('cf-reference');
  const st = document.getElementById('cf-reference-status');
  const out = document.getElementById('cf-reference-result');
  if (!f || !f.files || !f.files.length) { if (st) st.textContent = '请先选择参考视频'; return false; }
  const file = f.files[0];
  window._cfReference = null;
  window._cfReferenceFileObject = null;
  if (st) st.textContent = '分析中……（镜头检测 + 语法提取，约 10-40 秒）';
  const fd = new FormData();
  fd.append('reference', file, file.name);
  try {
    const r = await apiUpload('/api/reference', fd);
    if (!r || !r.ok) {
      if (st) st.textContent = '分析失败：' + ((r && r.error) || '未知错误');
      return false;
    }
    const rep = r.report || {};
    const vl = rep.visual_language || {};
    const sg = (rep.shot_grammar || []).slice(0, 8);
    window._cfReference = {
      id: `ref-${r.project_id || Date.now()}`,
      project_id: r.project_id || '',
      video: rep.video || '',
      report_path: rep.report_path || '',
      report: rep,
      apply: ['pace', 'density', 'motion', 'transition', 'shot_grammar']
    };
    window._cfReferenceFileObject = file;
    if (out) out.innerHTML = `<div class="ok-box">分析完成 · ${sg.length} 个镜头 · 节奏 ${esc(String(vl.pace||'—'))} · 运动语言 ${esc(String(vl.motion||'—'))}
      <div class="hint">参考视频已作为结构化风格输入保存，AI 导演创作时与文案、素材、软件项目一起使用；不会复制参考内容。</div></div>`;
    if (st) st.textContent = '分析完成，已加入 AI 导演输入。';
    return true;
  } catch (e) {
    if (st) st.textContent = '分析请求失败：' + (e.message || e);
    return false;
  }
}
/** §37 软件项目输入：保存完整 Evidence Graph，Director 只消费 VERIFIED 事实。 */
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
    window._cfRepo = {
      id: `repo-${Date.now()}`,
      root: root.trim(),
      evidence_graph: r.evidence_graph || {},
      claims: [],
      usage: ['claims', 'evidence', 'software_promotion']
    };
    out.innerHTML = '<div class="hint">项目已加入 AI 导演输入，但当前没有 VERIFIED 卖点；Director 不会编造功能。</div>';
    if (st) st.textContent = '项目已加入，但没有可验证卖点。';
    return;
  }
  window._cfRepo = {
    id: `repo-${Date.now()}`,
    root: root.trim(),
    evidence_graph: r.evidence_graph || {},
    claims: claims,
    usage: ['claims', 'evidence', 'software_promotion']
  };
  out.innerHTML = `<div class="ok-box">发现 ${claims.length} 条可验证卖点（全部 VERIFIED 证据会随 AI 导演请求一起发送）：
    ${claims.slice(0, 6).map(c => `<div class="hint">· ${esc(c.text)}</div>`).join('')}${claims.length > 6 ? '<div class="hint">· 其余 VERIFIED 卖点已保留，交给 Director 按场景选择。</div>' : ''}</div>`;
  if (st) st.textContent = '软件项目证据已加入 AI 导演输入。';
}
/** §38 状态机：提交 → WorkflowRun（§52 run_id + §53 真实步骤事件轮询）→ 工作台。 */
async function createProject(){
  const btn = document.getElementById('cf-go');
  const scope = (document.querySelector('input[name="cf-scope"]:checked') || {}).value || 'single';
  if (scope === 'series') {
    if (window._cfArticle) {
      const seriesStatus = document.getElementById('cf-status');
      if (seriesStatus) seriesStatus.textContent = '已导入的文章来源目前尚未接入系列批量 API；未提交任务。请切回“单条视频”，或移除文章来源后再批量创作。';
      return;
    }
    return createSeriesFromCreatePanel();
  }
  const status = document.getElementById('cf-status');
  const script = (document.getElementById('cf-script') || {}).value || '';
  const brief = (document.getElementById('cf-director-brief') || {}).value || '';
  const assets = document.getElementById('cf-assets');
  const hasFiles = !!((assets && assets.files.length) || (window.CF_DRAFT_CONTEXT && window.CF_DRAFT_CONTEXT.assetRefs && window.CF_DRAFT_CONTEXT.assetRefs.length));
  const referenceEl = document.getElementById('cf-reference');
  const selectedReference = !!(referenceEl && referenceEl.files && referenceEl.files.length);
  const referenceFile = selectedReference ? referenceEl.files[0] : null;
  // 差距 1（终审 R1）：已选参考文件但分析结果为空（_cfReference=null）——
  // 旧代码只在「换了文件」时触发自动分析，首次选中直接提交时静默走无参考分支，
  // 用户以为参考生效了，成片里却没有任何参考风格输入。
  // 阻止提交并让用户先点「分析参考视频」——比这里自动触发分析更可控
  // （自动触发会把 10-40 秒分析塞进提交流程，引入时序竞态与取消歧义）。
  if (referenceFile && !window._cfReference) {
    CF.state = 'failed';
    status.textContent = '已选择参考视频但尚未分析：请先点「分析参考视频」并等待分析完成（约 10-40 秒），再提交创作任务。';
    btn.disabled = false;
    return;
  }
  if (selectedReference && window._cfReferenceFileObject !== referenceFile) {
    btn.disabled = true;
    status.textContent = '检测到参考视频尚未分析，正在自动分析；分析成功后才会提交创作任务……';
    const analyzed = await cfAnalyzeReference();
    if (!analyzed) {
      CF.state = 'failed';
      status.textContent = '参考视频分析未成功，创作任务未提交。请重试分析或移除参考视频。';
      btn.disabled = false;
      return;
    }
  } else if (!selectedReference && window._cfReferenceFileObject) {
    window._cfReference = null;
    window._cfReferenceFileObject = null;
  }
  const hasReference = !!window._cfReference;
  const hasArticle = !!window._cfArticle;
  const hasSoftwareProject = !!window._cfRepo;
  const visualSource = (document.getElementById('cf-visual-source') || {}).value || 'auto';
  const hasDirectorSource = !!(brief.trim() || script.trim() || hasFiles || hasReference || hasArticle || hasSoftwareProject);
  if (!hasDirectorSource) {
    status.textContent = '请告诉 AI 想做什么，或添加文案、素材、参考视频、软件项目';
    return;
  }
  CF.state = 'submitting';
  btn.disabled = true;
  status.textContent = '正在提交创作任务……';
  // 所有来源进入同一个 DirectorInputBundle；creative_brief 仅作为兼容字段，不再承担来源存储职责。
  const tp = document.getElementById('cf-platform');
  const td = document.getElementById('cf-duration');
  const st = document.getElementById('cf-style');

  // 素材先进入统一 Asset Library；随后把 asset_id/file 作为 Director 的 Asset Pool 引用。
  // 路径暂存后传路径——这里直接上传到项目暂存目录再引用）
  let videoNames = [], imageNames = [], assetRefs = [];
  try {
    const ctx = window.CF_DRAFT_CONTEXT || {};
    if (ctx.assetUploadPromise) await ctx.assetUploadPromise;
    assetRefs = Array.isArray(ctx.assetRefs) ? ctx.assetRefs.slice() : [];
    if (assets && assets.files && assets.files.length && !assetRefs.length) {
      const fd = new FormData(); for (const f of assets.files) fd.append('files', f, f.name);
      const imported = await apiUpload('/api/assets/upload', fd);
      if (!imported || !imported.ok) throw new Error((imported && imported.error) || '素材入库失败');
      assetRefs = (imported.imported || []).map(a => ({asset_id:a.asset_id,type:a.type,file:a.file,width:a.width,height:a.height,duration:a.duration,commercial_status:a.commercial_status}));
      ctx.assetRefs = assetRefs;
    }
    videoNames = assetRefs.filter(a => a.type === 'video' && a.file).map(a => a.file);
    imageNames = assetRefs.filter(a => a.type === 'image' && a.file).map(a => a.file);
  } catch (e) { CF.state='failed'; status.textContent='本地素材暂存失败：'+(e.message||e); btn.disabled=false; return; }

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
  let uploadedNarration = (window.CF_DRAFT_CONTEXT && window.CF_DRAFT_CONTEXT.narrationUpload) || window._cfNarrationUpload || '';
  if (voiceMode === 'upload' && window.CF_DRAFT_CONTEXT?.narrationPromise) {
    try { uploadedNarration = await window.CF_DRAFT_CONTEXT.narrationPromise; }
    catch (e) { CF.state='failed'; status.textContent='配音暂存失败：'+(e.message||e); btn.disabled=false; return; }
  }
  if (voiceMode === 'upload' && !uploadedNarration && voiceFileEl && voiceFileEl.files.length) {
    try { const fd2=new FormData(); fd2.append('files',voiceFileEl.files[0],voiceFileEl.files[0].name); const st2=await apiUpload('/api/assets/stage',fd2); if(st2&&st2.ok&&(st2.audio||[]).length){uploadedNarration=st2.audio[0];window.CF_DRAFT_CONTEXT.narrationUpload=uploadedNarration;} } catch (_) {}
  }
  if (voiceMode==='upload'&&!uploadedNarration) { CF.state='failed';status.textContent='上传配音模式需要先选择并成功暂存配音文件';btn.disabled=false;return; }
  const directorBrief = brief.trim();
  const referenceInput = window._cfReference ? [window._cfReference] : [];
  const articleInput = window._cfArticle ? [window._cfArticle] : [];
  const softwareProjectInput = window._cfRepo ? window._cfRepo : {};
  const directorInputs = {
    brief: directorBrief,
    script: {
      text: script.trim(),
      source: script.trim() ? 'user' : 'director',
      authority: script.trim() ? 'authoritative' : 'generated',
      editable: true
    },
    assets: {
      asset_refs: assetRefs,
      policy: visualSource === 'local' ? 'user_only' : 'user_first',
      required: visualSource === 'local'
    },
    references: referenceInput,
    articles: articleInput,
    software_project: softwareProjectInput,
    constraints: {
      visual_source: visualSource,
      allow_stock: visualSource !== 'local',
      allow_generation: true,
      render_engine: (document.getElementById('cf-render-engine') || {}).value || 'ffmpeg'
    },
    platform: (tp || {}).value || '',
    duration: (td || {}).value || '',
    style: (st || {}).value || ''
  };
  const requestOptions = {
    brief: directorBrief,
    script: script.trim(),
    creative_brief: directorBrief,
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
    // 音效：'openverse-auto' = 本地未命中自动去 Openverse（freesound CC0）搜配；
    // 'auto' = 只用本地库；'' = 关闭；具体 id = 全程铺该音效。
    sfx: (() => { const v = (document.getElementById('cf-sfx') || {}).value; return v === undefined ? 'auto' : v; })(),
    sfx_plan: null,
    auto_sfx_search: ((document.getElementById('cf-sfx') || {}).value === 'openverse-auto'),
    subtitle_mode: (document.getElementById('cf-subtitle') || {}).value || 'plain',
    fit_mode: (document.getElementById('cf-fit-mode') || {}).value || 'cover',
    clip_duration: (() => { const v = (document.getElementById('cf-clip-duration') || {}).value; return v ? parseFloat(v) : null; })(),
    clip_speed: (() => { const v = (document.getElementById('cf-clip-speed') || {}).value; return v ? parseFloat(v) : null; })(),
    concat_mode: (document.getElementById('cf-concat-mode') || {}).value || 'sequential',
    transition_mode: (document.getElementById('cf-transition-mode') || {}).value || 'cut',
    match_materials_to_script: !!(document.getElementById('cf-match-materials') || {}).checked,
    stop_at: (document.getElementById('cf-stop-at') || {}).value || 'video',
    fps: parseInt((document.getElementById('cf-fps') || {}).value || '30'),
    watermark: (document.getElementById('cf-watermark') || {}).value || '',
    lower_third_name: (document.getElementById('cf-lower-name') || {}).value || '',
    lower_third_role: (document.getElementById('cf-lower-role') || {}).value || '',
    confirm_mode: (document.getElementById('cf-confirm') || {}).value || 'preview',
    visual_source: visualSource,
    auto_confirm: ((document.getElementById('cf-confirm') || {}).value || 'preview') === 'direct',
    video_paths: videoNames, image_paths: imageNames,
  };
  // 所有创建请求统一进入 AI Director；没有“文案模式 / 素材模式 / 参考模式 / 项目模式”。
  const conversation = await apiPost('/api/director/conversations', {
    title: (directorBrief || script || 'AI 导演创作').slice(0,80),
    initial_message: [directorBrief, script ? '\n\n【权威文案】\n' + script : ''].filter(Boolean).join('') || '请根据我提供的素材、参考资料和其他上下文完成导演创作。',
    snapshot: {form:{fields:(function(){const out={};document.querySelectorAll('#cf-form input[id^="cf-"],#cf-form textarea[id^="cf-"],#cf-form select[id^="cf-"]').forEach(el=>{if(el.type==='file')return;if(el.type==='checkbox')out[el.id]=el.checked;else if(el.type==='radio'){if(el.checked)out[el.name||el.id]=el.value;}else out[el.id]=el.value;});return out;})(),context:{asset_refs:assetRefs,reference:window._cfReference||null,article:window._cfArticle||null,repo:window._cfRepo||null,narration_upload:uploadedNarration}},director_inputs:directorInputs,request_options:requestOptions}
  });
  if(!conversation||!conversation.ok||!conversation.conversation_id){CF.state='failed';status.textContent='历史对话保存失败，任务未提交：'+((conversation&&conversation.error)||'未知错误');btn.disabled=false;return;}
  window._cfConversationId=conversation.conversation_id;
  const wfResp = await apiPost('/api/director/create', {
    ...requestOptions,
    conversation_id: conversation.conversation_id,
    brief: directorBrief,
    platform: (tp || {}).value || '',
    duration: (td || {}).value || '',
    style: (st || {}).value || '',
    director_inputs: directorInputs,
    _explicit_fields: Array.from(CF.explicitFields || [])
  });
  if (!wfResp.ok || !wfResp.run_id) {
    CF.state = 'failed';
    status.textContent = '任务提交失败：' + (wfResp.error || '未知') + '（创作输入仍保留在草稿和历史对话中）';
    await apiPost('/api/director/conversations/' + encodeURIComponent(conversation.conversation_id) + '/messages',{role:'assistant',content:'创作任务提交失败：'+(wfResp.error||'未知错误')});
    btn.disabled = false;
    return;
  }
  // 2026-10-25 用户裁决：点击创作后弹窗不关、变身「创作流程窗口」——
  // 步骤清单实时可见（在干什么/有什么问题），出问题可取消并返回修改要求。
  const runId = wfResp.run_id;
  await apiPost('/api/director/conversations/' + encodeURIComponent(conversation.conversation_id) + '/link',{project_id:wfResp.project_id||'',workflow_run_id:runId});
  await apiPost('/api/director/drafts/active/clear',{});
  window.CF_DRAFT_CONTEXT.projectId=wfResp.project_id||'';
  cfEnterProgressMode(runId);
}

/** 创作流程窗口模式：表单隐藏，弹窗变成进度面板（步骤+警告+取消返回修改）。 */
let _cfProgressTimer = null;
function cfEnterProgressMode(runId){
  window._cfActiveRunId = runId;
  const form = document.getElementById('cf-form');
  const status = document.getElementById('cf-status');
  if (!form || !status) return;
  // 流程面板注入（表单之后）
  let panel = document.getElementById('cf-progress');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'cf-progress';
    panel.style.cssText = 'margin-top:10px;border-top:1px solid var(--border);padding-top:10px;max-height:46vh;overflow-y:auto';
    form.parentNode.insertBefore(panel, form.nextSibling);
  }
  form.style.display = 'none';
  panel.innerHTML = '<div class="hint">创作已开始，正在执行……每一步做什么都列在下面；<b>停止创作</b>会立即中断（已完成步骤保留在检查点）。</div>' +
    '<div id="cf-progress-steps"></div>' +
    '<div class="btn-row" style="margin-top:8px">' +
    '<button class="btn-sm" style="background:var(--danger,#c33);color:#fff" onclick="cfStopCreation()">停止创作</button>' +
    '<button class="btn-secondary btn-sm" onclick="cfCancelAndEdit()">停止并返回修改要求</button>' +
    '<button class="btn-secondary btn-sm" onclick="cfDismissProgress()">后台继续，关闭窗口</button>' +
    '</div>';
  if (_cfProgressTimer) clearInterval(_cfProgressTimer);
  _cfProgressTimer = setInterval(async () => {
    try {
      const run = await apiGet('/api/workflows/' + encodeURIComponent(runId));
      if (!run || run.error) return;
      const stepsBox = document.getElementById('cf-progress-steps');
      if (stepsBox) stepsBox.innerHTML = cfRenderProgressSteps(run);
      if (run.status !== 'RUNNING') {
        clearInterval(_cfProgressTimer); _cfProgressTimer = null;
        const st = document.getElementById('cf-progress');
        if (st) st.querySelector('.hint').textContent =
          run.status === 'COMPLETED' || run.status === 'WAITING_CONFIRMATION'
            ? '创作完成，正在打开工作台……'
            : run.status === 'CANCELLED'
              ? '已停止创作。已完成步骤保留在检查点；可点「停止并返回修改要求」调整后重新创作。'
              : '创作失败：' + (run.error || '未知') + '。可点「停止并返回修改要求」调整后重试。';
        if (run.status === 'COMPLETED' || run.status === 'WAITING_CONFIRMATION') {
          const result = run.result || {};
          CF.state = run.status === 'WAITING_CONFIRMATION' ? 'awaiting_confirm' : 'editing';
          cfFinalizeCreateDialog();
          if (result.project_id) {
            editorToast(run.status === 'WAITING_CONFIRMATION' ? '方案已生成，正在打开工作台……' : '创作完成，已打开工作台');
            await workspaceOpen(result.project_id);
            await directorLoadRun(runId);
          }
          loadProjects();
        } else if (run.status === 'CANCELLED') {
          CF.state = 'idle';  // 已停止：表单可改可重提
        } else {
          CF.state = 'failed';
        }
      }
    } catch (e) { /* 下次轮询再试 */ }
  }, 1500);
}

function cfRenderProgressSteps(run){
  const labels = run.labels || {};
  const stCn = {DONE: '✅ 完成', RUNNING: '⏳ 执行中', FAILED: '❌ 失败',
                SKIPPED: '⚪ 跳过', PENDING: '· 待处理', INSPECTED: '✅ 完成'};
  // 2026-10-26 用户裁决「每一步动态展示在干什么」：RUNNING 节点若正处 LLM
  // 重试，实时显示「第 N/10 次尝试失败：xxx（X 秒前）」；否则显示该节点正在
  // 做的事（动作说明）。后端 get_run 已在 RUNNING 步骤的 detail 里写入动作。
  const llmRetry = run.llm_retry || {};
  const liveLine = (llmRetry.attempt || 0) > 0
    ? '<div style="color:#c77700;font-size:12px;margin:2px 0 6px 14px">'
      + '⏳ 文本模型正在重试：第 ' + esc(llmRetry.attempt) + '/' + esc(llmRetry.max)
      + ' 次失败（' + esc(llmRetry.last_error || '未知') + '，'
      + esc(llmRetry.age_s || 0) + ' 秒前更新）</div>'
    : '';
  return (run.steps || []).map(s => {
    const line = '<div style="margin:4px 0"><b>' + esc(labels[s.id] || s.id) + '</b> ' +
      (stCn[s.status] || s.status) + '</div>';
    // RUNNING 节点挂实时 LLM 重试进度（橙字=进行中）；FAILED/降级 detail 红字=问题
    const live = (s.status === 'RUNNING' && (llmRetry.attempt || 0) > 0) ? liveLine : '';
    // RUNNING 的动作说明是中性灰（正在做什么），FAILED/完成后降级原因才是红⚠
    let warn = '';
    if (s.detail) {
      if (s.status === 'RUNNING') {
        warn = '<div style="color:var(--muted,#889);font-size:12px;margin:2px 0 6px 14px">'
          + esc(s.detail) + '</div>';
      } else {
        warn = '<div style="color:var(--danger,#c33);font-size:12px;margin:2px 0 6px 14px">⚠ '
          + esc(s.detail) + '</div>';
      }
    }
    return line + live + warn;
  }).join('');
}

/** 停止创作：请求服务端取消（run→CANCELLED，正在执行的 LLM 调用立即中断）。 */
async function cfStopCreation(){
  const rid = window._cfActiveRunId;
  if (!rid) { editorToast('没有正在进行的创作'); return; }
  const r = await apiPost('/api/workflows/cancel', {run_id: rid});
  if (r && r.ok) {
    editorToast(r.note || '已请求停止：正在立即中断，请稍候……');
    const hint = document.querySelector('#cf-progress .hint');
    if (hint) hint.textContent = '已请求停止——正在立即中断（已完成步骤保留在检查点）。';
  } else {
    editorToast('停止失败：' + ((r && r.error) || '未知'), true);
  }
}

/** 取消创作并返回修改要求：停止轮询，弹窗还原表单（用户要求原样保留）。 */
function cfCancelAndEdit(){
  if (_cfProgressTimer) { clearInterval(_cfProgressTimer); _cfProgressTimer = null; }
  const panel = document.getElementById('cf-progress');
  if (panel) panel.remove();
  const form = document.getElementById('cf-form');
  if (form) form.style.display = '';
  const status = document.getElementById('cf-status');
  if (status) status.textContent = '已取消创作。你的要求和素材都保留在表单里，修改后可重新点「AI 开始创作」。';
  const btn = document.getElementById('cf-go');
  if (btn) { btn.disabled = false; btn.textContent = 'AI 开始创作'; }
  CF.state = 'idle';
}

/** 后台继续：关窗口，创作不停（工作台「AI 导演流程」面板可继续看）。 */
function cfDismissProgress(){
  if (_cfProgressTimer) { clearInterval(_cfProgressTimer); _cfProgressTimer = null; }
  closeCreate();
  editorToast('创作在后台继续；打开项目后可在「AI 导演流程」面板查看每步进展');
}

function cfSeriesSync(){
  const scope=(document.querySelector('input[name="cf-scope"]:checked')||{}).value||'single';
  const box=document.getElementById('cf-series-options');
  if(box)box.style.display=scope==='series'?'':'none';
  const btns=['cf-go'].map(id=>document.getElementById(id)).filter(Boolean);btns.forEach(b=>b.textContent=scope==='series'?'AI 开始批量创作':'AI 开始创作');
}
async function createSeriesFromCreatePanel(){
  const status=document.getElementById('cf-status');
  const name=(document.getElementById('cf-series-name')||{}).value||'';
  const scripts=(document.getElementById('cf-script')||{}).value||'';
  const files=Array.from((document.getElementById('cf-assets')||{}).files||[]);
  const parts=scripts.split('------------').map(x=>x.trim()).filter(Boolean);
  if(!name.trim()){status.textContent='系列模式请填写系列名称';return;}
  if(!parts.length){status.textContent='系列模式请至少输入一集文案，并用 ------------ 分隔多集';return;}
  CF.state='submitting';
  const bs=['cf-go'].map(id=>document.getElementById(id)).filter(Boolean);bs.forEach(b=>b.disabled=true);
  status.textContent='正在建立系列并提交批量任务……';
  try{
    const res=(document.getElementById('cf-resolution')||{}).value||'1080x1920';
    const [width,height]=res.split('x').map(Number);
    const r1=await apiPost('/api/series_create',{name:name.trim(),spec:{
      voice:(document.getElementById('cf-voice')||{}).value||'zh-CN-XiaoxiaoNeural',
      subtitle_mode:(document.getElementById('cf-subtitle')||{}).value||'plain',
      bgm:(document.getElementById('cf-bgm')||{}).value||'auto',
      bgm_volume:parseFloat((document.getElementById('cf-bgm-volume')||{}).value||'0.3'),
      watermark:(document.getElementById('cf-watermark')||{}).value||'',
      style_intent:(document.getElementById('cf-style')||{}).value||'',
      width,height,visual_source:(document.getElementById('cf-visual-source')||{}).value||'auto'
    }});
    if(!r1.ok)throw new Error(r1.error||'系列创建失败');
    const fd=new FormData();fd.append('series_id',r1.series_id);fd.append('scripts',parts.join('\n------------\n'));
    for(const f of files){const v=/\.(mp4|mov|mkv|webm|m4v)$/i.test(f.name);fd.append(v?'videos':'images',f,f.name);}
    const r2=await apiUpload('/api/series_batch',fd);
    if(!r2.ok)throw new Error(r2.error||'批量任务提交失败');
    closeCreate();loadProjects();loadSeries();editorToast('已建立「'+name.trim()+'」，提交 '+r2.episodes+' 集批量任务');
  }catch(e){CF.state='failed';status.textContent='系列提交失败：'+(e.message||e);bs.forEach(b=>b.disabled=false);}
}

/** 润色（走原 enhance API，面板内联呈现）。 */
async function cfGenerateScript(){
  const topic = (document.getElementById('cf-director-brief') || {}).value || '';
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
  openCreate, closeCreate, createLoadDefaults,
  createPlatformSync, createProject, createEnhance, createAcceptEnhanced,
  cfMakeGraphicPlan, cfAnalyzeReference, cfImportArticle, cfClearArticleSource, cfScanRepo, cfVoiceModeSync, cfSeriesSync, createSeriesFromCreatePanel, CF,
  cfEnterProgressMode, cfRenderProgressSteps, cfCancelAndEdit, cfDismissProgress, cfPrefillFromProject, cfStopCreation,
});


/* Durable Create lifecycle: never destroy a draft on backdrop clicks or temporary dismissal. */
window.CF_DRAFT_CONTEXT = window.CF_DRAFT_CONTEXT || {assetRefs:[],reference:null,article:null,repo:null,narrationUpload:''};
(function installCreateLifecycle(){
  const baseOpen=window.openCreate, baseClose=window.closeCreate;
  if(typeof baseOpen!=='function'||typeof baseClose!=='function')return;
  let timer=null, saving=Promise.resolve(), restored=false;
  function collectFields(){
    const fields={};
    document.querySelectorAll('#cf-form input[id^="cf-"],#cf-form textarea[id^="cf-"],#cf-form select[id^="cf-"]').forEach(el=>{
      if(el.type==='file')return;
      if(el.type==='radio'){if(el.checked)fields[el.name||el.id]=el.value;}
      else if(el.type==='checkbox')fields[el.id]=!!el.checked;
      else fields[el.id]=el.value;
    });
    const scope=document.querySelector('input[name="cf-scope"]:checked');if(scope)fields['cf-scope']=scope.value;
    return fields;
  }
  function snapshot(){
    const c=window.CF_DRAFT_CONTEXT||{};
    return {version:1,fields:collectFields(),context:{
      asset_refs:Array.isArray(c.assetRefs)?c.assetRefs:[],
      reference:c.reference||window._cfReference||null,
      article:c.article||window._cfArticle||null,
      repo:c.repo||window._cfRepo||null,
      narration_upload:c.narrationUpload||window._cfNarrationUpload||'',
      project_id:c.projectId||''
    }};
  }
  async function saveNow(){
    if(!document.getElementById('cf-form'))return;
    const data=snapshot();
    saving=saving.catch(()=>{}).then(()=>apiPost('/api/director/drafts/active',{data}));
    try{
      const r=await saving,st=document.getElementById('cf-draft-status');
      if(st)st.textContent=r&&r.ok?'已保存 '+new Date(r.updated_at*1000).toLocaleTimeString():'保存失败：'+((r&&r.error)||'服务未确认');
      return r;
    }catch(e){const st=document.getElementById('cf-draft-status');if(st)st.textContent='保存失败：'+(e.message||e);return {ok:false,error:String(e)};}
  }
  function scheduleSave(){
    if(timer)clearTimeout(timer);
    const st=document.getElementById('cf-draft-status');if(st)st.textContent='正在保存…';
    timer=setTimeout(saveNow,450);
  }
  function applySnapshot(data){
    if(!data||typeof data!=='object')return;
    for(const [id,value] of Object.entries(data.fields||{})){
      if(id==='cf-scope'){const radio=document.querySelector('input[name="cf-scope"][value="'+CSS.escape(String(value))+'"]');if(radio)radio.checked=true;continue;}
      const el=document.getElementById(id);if(!el||el.type==='file')continue;
      if(el.type==='checkbox')el.checked=!!value;else el.value=value==null?'':String(value);
    }
    const c=data.context||{};
    window.CF_DRAFT_CONTEXT={assetRefs:Array.isArray(c.asset_refs)?c.asset_refs:[],reference:c.reference||null,
      article:c.article||null,repo:c.repo||null,narrationUpload:c.narration_upload||'',projectId:c.project_id||''};
    window._cfReference=c.reference||null;window._cfReferenceFileObject=null;
    window._cfArticle=c.article||null;window._cfRepo=c.repo||null;window._cfNarrationUpload=c.narration_upload||'';
    const summary=document.getElementById('cf-assets-summary');
    if(summary)summary.textContent=window.CF_DRAFT_CONTEXT.assetRefs.length?
      '已暂存 '+window.CF_DRAFT_CONTEXT.assetRefs.length+' 个素材，可直接用于创作':'未添加本地素材';
    if(window._cfArticle&&typeof cfRenderArticleSource==='function')cfRenderArticleSource(window._cfArticle,'已从草稿恢复文章来源。');
    if(window._cfReference){const el=document.getElementById('cf-reference-result');if(el)el.innerHTML='<div class="ok-box">已恢复参考视频分析，可直接用于创作。</div>';}
    if(window._cfRepo){const el=document.getElementById('cf-repo-result');if(el)el.innerHTML='<div class="ok-box">已恢复软件项目证据与已验证卖点。</div>';}
    if(typeof cfVoiceModeSync==='function')cfVoiceModeSync();
    if(typeof cfVisualSourceSync==='function')cfVisualSourceSync();
    if(typeof cfSeriesSync==='function')cfSeriesSync();
    const st=document.getElementById('cf-draft-status');if(st)st.textContent='已恢复上次保存的创作草稿';
  }
  async function restoreDraft(){
    if(restored)return;restored=true;
    try{const r=await apiGet('/api/director/drafts/active');if(r&&r.ok&&r.draft&&r.draft.data)applySnapshot(r.draft.data);}
    catch(e){const st=document.getElementById('cf-draft-status');if(st)st.textContent='草稿读取失败：'+(e.message||e);}
  }
  function arrangeForm(){
    const form=document.getElementById('cf-form');if(!form||form.dataset.arranged==='1')return;form.dataset.arranged='1';
    const header=form.firstElementChild;
    if(header){
      const history=document.createElement('button');history.type='button';history.className='btn-secondary btn-sm';history.textContent='历史对话';history.onclick=cfHistoryToggle;
      header.insertBefore(history,header.querySelector('span[style*="flex:1"]'));
      const status=document.createElement('span');status.id='cf-draft-status';status.className='hint';status.style.marginLeft='8px';status.textContent='草稿待保存';
      header.insertBefore(status,history);
      const close=header.querySelector('button[onclick="closeCreate()"]');if(close)close.textContent='收起';
    }
    const brief=document.getElementById('cf-director-brief');
    if(brief&&!document.getElementById('cf-quick-actions')){
      const actions=document.createElement('div');actions.id='cf-quick-actions';actions.className='btn-row';actions.style.marginTop='8px';
      actions.innerHTML='<button type="button" class="btn-secondary btn-sm" onclick="cfGenerateScript()">AI 生成文案</button><button type="button" class="btn-secondary btn-sm" onclick="createEnhance()">AI 润色文案</button><button type="button" class="btn-secondary btn-sm" onclick="cfMakeGraphicPlan()">图形方案预览</button>';
      brief.parentElement.appendChild(actions);
    }
    // 上方快捷栏已承载这两项动作，移除高级区的旧副本。
    const quickActions=document.getElementById('cf-quick-actions');
    form.querySelectorAll('button[onclick="createEnhance()"],button[onclick="cfMakeGraphicPlan()"]').forEach(button=>{
      if(!quickActions || !quickActions.contains(button)) button.remove();
    });
    document.getElementById('cf-topic')?.closest('.btn-row')?.remove();
    const manual=document.getElementById('cf-manual-options');
    if(manual){
      const row=document.getElementById('cf-platform')?.closest('.btn-row');
      const visual=document.getElementById('cf-visual-source')?.closest('.btn-row');
      const studio=document.getElementById('cf-studio-catalog-status')?.closest('.panel');
      const panel=document.createElement('section');panel.id='cf-output-requirements';panel.className='panel';panel.style.marginTop='10px';
      panel.innerHTML='<div class="btn-row" style="align-items:center;margin:0"><b>成片要求</b><span class="hint">只设置明确要求，其余交给 AI 决定。</span></div>';
      manual.parentNode.insertBefore(panel,manual);
      if(row)panel.appendChild(row);if(visual)panel.appendChild(visual);
      const styles=document.getElementById('cf-style-presets');if(styles)panel.appendChild(styles);
      if(studio){const detail=document.createElement('details');detail.style.marginTop='8px';detail.innerHTML='<summary style="cursor:pointer;color:var(--muted);font-size:12px">本机执行能力与模板状态</summary>';detail.appendChild(studio);panel.appendChild(detail);}
    }
    document.getElementById('cf-style-gallery-btn')?.remove();
    const templateList=document.getElementById('cf-studio-template-list');if(templateList)templateList.style.display='none';
  }
  async function uploadAssets(input){
    const files=Array.from(input.files||[]);if(!files.length)return;
    const status=document.getElementById('cf-assets-summary');if(status)status.textContent='正在把素材暂存到素材库…';
    const fd=new FormData();files.forEach(f=>fd.append('files',f,f.name));
    try{
      const r=await apiUpload('/api/assets/upload',fd);if(!r||!r.ok)throw new Error(r?.error||'上传失败');
      const refs=window.CF_DRAFT_CONTEXT.assetRefs,seen=new Set(refs.map(x=>x.asset_id||x.file));
      for(const a of (r.imported||[])){const key=a.asset_id||a.file;if(key&&!seen.has(key)){refs.push({asset_id:a.asset_id,type:a.type,file:a.file,width:a.width,height:a.height,duration:a.duration,commercial_status:a.commercial_status});seen.add(key);}}
      if(status)status.textContent='已暂存 '+refs.length+' 个素材，关闭窗口或刷新后仍可恢复。';await saveNow();
    }catch(e){if(status)status.textContent='素材暂存失败：'+(e.message||e)+'。请重试后再关闭。';throw e;}
  }
  async function uploadNarration(file){
    const fd=new FormData();fd.append('files',file,file.name);
    const r=await apiUpload('/api/assets/stage',fd);if(!r||!r.ok||!(r.audio||[]).length)throw new Error(r?.error||'配音暂存失败');
    window.CF_DRAFT_CONTEXT.narrationUpload=r.audio[0];window._cfNarrationUpload=r.audio[0];await saveNow();return r.audio[0];
  }
  function installHandlers(){
    const form=document.getElementById('cf-form');if(!form||form.dataset.draftHandlers==='1')return;form.dataset.draftHandlers='1';
    form.addEventListener('input',scheduleSave,true);
    form.addEventListener('change',e=>{
      scheduleSave();
      if(e.target?.id==='cf-assets')window.CF_DRAFT_CONTEXT.assetUploadPromise=uploadAssets(e.target).catch(()=>false);
      if(e.target?.id==='cf-reference'&&e.target.files?.length){
        window._cfReference=null;window.CF_DRAFT_CONTEXT.reference=null;
        cfAnalyzeReference().then(ok=>{if(ok){window.CF_DRAFT_CONTEXT.reference=window._cfReference;saveNow();}});
      }
      if(e.target?.id==='cf-voice-file'&&e.target.files?.length)
        window.CF_DRAFT_CONTEXT.narrationPromise=uploadNarration(e.target.files[0]).catch(error=>{const st=document.getElementById('cf-status');if(st)st.textContent='配音暂存失败：'+(error.message||error);return '';});
    },true);
  }
  window.openCreate=function(projectId){
    const existing=document.getElementById('cf-mask');
    if(existing){existing.style.display='flex';existing.setAttribute('aria-hidden','false');if(window.CF&&document.getElementById('cf-form')?.style.display!=='none')window.CF.state='editing';if(projectId)cfPrefillFromProject(projectId);else restoreDraft();installHandlers();return;}
    restored=false;baseOpen(projectId);arrangeForm();installHandlers();if(!projectId)restoreDraft();
  };
  window.closeCreate=async function(){
    const mask=document.getElementById('cf-mask');if(!mask)return baseClose();
    // 素材/配音正在上传时先等入库结果，避免收起后刷新导致文件引用尚未写入草稿。
    const ctx=window.CF_DRAFT_CONTEXT||{};
    if(timer)clearTimeout(timer);
    const pending=[ctx.assetUploadPromise,ctx.narrationPromise].filter(Boolean);
    if(pending.length)await Promise.allSettled(pending);
    await saveNow();
    mask.style.display='none';mask.setAttribute('aria-hidden','true');
    if(window.CF)window.CF.state='idle';
  };
  window.cfFinalizeCreateDialog=async function(){
    if(timer)clearTimeout(timer);document.getElementById('cf-mask')?.remove();
    for(const id of ['sg-mask','tg-mask'])document.getElementById(id)?.remove();
    try{await apiPost('/api/director/drafts/active/clear',{});}catch(_){}
    if(window.CF)window.CF.state='idle';restored=false;
  };
  window.cfHistoryToggle=async function(){
    let panel=document.getElementById('cf-history-panel');if(panel){panel.remove();return;}
    panel=document.createElement('section');panel.id='cf-history-panel';panel.className='panel';panel.style.maxHeight='45vh';panel.style.overflow='auto';
    panel.innerHTML='<div class="btn-row"><b>历史创作对话</b><span class="tb-sep"></span><button class="btn-secondary btn-sm" onclick="cfHistoryToggle()">收起</button></div><div id="cf-history-list" class="hint">正在读取…</div>';
    const anchor=document.getElementById('cf-quick-actions')||document.getElementById('cf-director-plan-preview');
    (anchor?.parentElement||document.getElementById('cf-form')).insertAdjacentElement('afterend',panel);
    try{
      const r=await apiGet('/api/director/conversations?limit=50'),list=document.getElementById('cf-history-list');
      const rows=(r&&r.conversations)||[];
      if(list)list.innerHTML=rows.length?rows.map(x=>'<div class="proj-row"><span style="flex:1"><b>'+esc(x.title||'未命名对话')+'</b><span class="hint">'+esc(x.project_id||'未关联项目')+' · '+new Date((x.updated_at||0)*1000).toLocaleString()+' · '+esc(x.status)+'</span></span><button class="btn-secondary btn-sm" onclick="cfHistoryOpenConversation(\''+esc(x.conversation_id)+'\')">查看</button></div>').join(''):'<div class="hint">暂无历史对话。第一次提交创作后会自动记录。</div>';
    }catch(e){const list=document.getElementById('cf-history-list');if(list)list.textContent='历史对话读取失败：'+(e.message||e);}
  };
  window.cfHistoryOpenConversation=async function(id){
    const list=document.getElementById('cf-history-list');if(!list)return;list.textContent='读取对话…';
    try{
      const r=await apiGet('/api/director/conversations/'+encodeURIComponent(id));if(!r||!r.ok||!r.conversation)throw new Error(r?.error||'对话不存在');
      const c=r.conversation;window._cfHistorySelected=c;
      const messages=(c.messages||[]).map(m=>'<div class="panel" style="padding:8px;margin-top:6px"><b>'+({user:'用户',assistant:'AI',tool:'执行记录',system:'系统'}[m.role]||m.role)+'</b><div style="white-space:pre-wrap;margin-top:4px">'+esc(m.content)+'</div></div>').join('');
      list.innerHTML='<div class="btn-row"><b>'+esc(c.title)+'</b><span class="tb-sep"></span><button class="btn-secondary btn-sm" onclick="cfHistoryToggle()">关闭历史</button></div>'+messages+
        '<div class="btn-row" style="margin-top:8px"><button class="btn-sm" onclick="cfHistoryRestoreSnapshot(\''+esc(id)+'\')">恢复为新草稿</button>'+
        (c.project_id?'<button class="btn-secondary btn-sm" onclick="openCreate(\''+esc(c.project_id)+'\')">打开关联项目</button>':'')+'</div>';
    }catch(e){list.textContent='对话读取失败：'+(e.message||e);}
  };
  window.cfHistoryRestoreSnapshot=async function(id){
    const c=window._cfHistorySelected;if(!c||c.conversation_id!==id)return;
    const s=c.snapshot||{};const form=s.form||s;if(form.fields)applySnapshot(form);
    else if(s.director_inputs){const d=s.director_inputs;const b=document.getElementById('cf-director-brief'),t=document.getElementById('cf-script');if(b)b.value=d.brief||'';if(t)t.value=(d.script&&d.script.text)||'';window.CF_DRAFT_CONTEXT.assetRefs=(d.assets&&d.assets.asset_refs)||[];window._cfReference=(d.references||[])[0]||null;window._cfArticle=(d.articles||[])[0]||null;window._cfRepo=d.software_project||null;}
    await saveNow();const list=document.getElementById('cf-history-list');if(list)list.innerHTML='<div class="ok-box">已恢复为新草稿，原历史对话不会被覆盖。</div>';
  };
  window.addEventListener('beforeunload',()=>{
    const mask=document.getElementById('cf-mask');
    // Dialog 真正销毁/从未打开时不能重建已清除的 draft。
    if(mask && mask.style.display!=='none'){
      try{navigator.sendBeacon('/api/director/drafts/active',new Blob([JSON.stringify({data:snapshot()})],{type:'application/json'}));}catch(_){}
    }
  });
  const originalCancelAndEdit=window.cfCancelAndEdit;
  window.cfCancelAndEdit=function(){
    originalCancelAndEdit();
    // create 成功后 active draft 已清除；用户停止任务并返回编辑时重新建立草稿。
    saveNow();
  };
  const originalGenerate=window.cfGenerateScript;
  window.cfGenerateScript=async function(){
    const brief=document.getElementById('cf-director-brief');if(!brief||!brief.value.trim()){const st=document.getElementById('cf-script-gen-status');if(st)st.textContent='请先填写上方创作要求';return;}
    return originalGenerate();
  };
  const originalRepo=window.cfScanRepo;window.cfScanRepo=async function(){const r=await originalRepo();await saveNow();return r;};
  const originalReference=window.cfAnalyzeReference;window.cfAnalyzeReference=async function(){const r=await originalReference();if(r){window.CF_DRAFT_CONTEXT.reference=window._cfReference;await saveNow();}return r;};
  const originalArticle=window.cfImportArticle;window.cfImportArticle=async function(){await originalArticle();window.CF_DRAFT_CONTEXT.article=window._cfArticle;await saveNow();};
  const originalClear=window.cfClearArticleSource;window.cfClearArticleSource=function(){originalClear();window.CF_DRAFT_CONTEXT.article=null;scheduleSave();};
})();
