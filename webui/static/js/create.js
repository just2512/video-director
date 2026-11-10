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

let _cfDialogReturnFocus = null;
const CF_DIALOG_FOCUSABLE = [
  'a[href]', 'area[href]',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])', 'textarea:not([disabled])', 'button:not([disabled])',
  '[contenteditable="true"]', '[tabindex]:not([tabindex="-1"])'
].join(',');
function cfDialogFocusableElements(panel){
  if(!panel)return [];
  return Array.from(panel.querySelectorAll(CF_DIALOG_FOCUSABLE)).filter(element=>
    element.getAttribute('aria-hidden')!=='true' &&
    getComputedStyle(element).visibility!=='hidden' &&
    element.getClientRects().length>0
  );
}
function cfFocusCreateDialog(){
  const panel=document.getElementById('cf-panel');
  if(!panel)return false;
  const preferred=panel.querySelector('#cf-director-brief');
  const target=preferred||cfDialogFocusableElements(panel)[0]||panel;
  target.focus({preventScroll:true});
  return true;
}
function cfRestoreCreateDialogFocus(){
  const target=_cfDialogReturnFocus;
  _cfDialogReturnFocus=null;
  // Prefer the exact invoking element. If the shell replaced it while the
  // dialog was open, restore to the stable top-bar create action instead.
  const fallback=document.querySelector('header button[onclick="openCreate()"]');
  const candidates=[target,fallback];
  for(const candidate of candidates){
    if(!candidate||!candidate.isConnected||typeof candidate.focus!=='function')continue;
    if(candidate.disabled||getComputedStyle(candidate).visibility==='hidden')continue;
    candidate.focus({preventScroll:true});
    if(document.activeElement===candidate)return true;
  }
  return false;
}

/** 打开创建面板（顶栏「+ 新建项目」唯一入口 §3.1）。 */
function openCreate(projectId){
  if(!document.getElementById('cf-mask')){
    const active=document.activeElement;
    _cfDialogReturnFocus=active instanceof HTMLElement?active:null;
  }
  closeCreate({restoreFocus:false});
  const mask = document.createElement('div');
  mask.id = 'cf-mask';
  mask.style.cssText = 'position:fixed;inset:0;background:var(--bg,#111);z-index:12000;display:flex;align-items:stretch;justify-content:stretch;pointer-events:auto';
  mask.setAttribute('aria-hidden','false');
  // The backdrop is deliberately inert. Only the explicit header action may
  // dismiss the dialog; pointer sequences outside the panel must never reach
  // workspace-level handlers or be interpreted as a close request.
  const blockBackdropDismiss = event => {
    if(event.target!==mask)return;
    event.preventDefault();
    event.stopPropagation();
    if(typeof event.stopImmediatePropagation==='function')event.stopImmediatePropagation();
  };
  for(const type of ['pointerdown','mousedown','mouseup','click','touchstart']){
    mask.addEventListener(type,blockBackdropDismiss,true);
  }
  mask.addEventListener('keydown',event=>{
    if(event.key==='Escape'){
      event.preventDefault();
      event.stopPropagation();
      const status=document.getElementById('cf-status');
      if(status)status.textContent='创作窗口不会因点击遮罩或按 Escape 关闭；请使用右上角「收起」保存草稿。';
      return;
    }
    if(event.key!=='Tab')return;
    const focusable=cfDialogFocusableElements(panel);
    if(!focusable.length){
      event.preventDefault();
      panel.focus({preventScroll:true});
      return;
    }
    const first=focusable[0],last=focusable[focusable.length-1],active=document.activeElement;
    if(!panel.contains(active)||active===panel){
      event.preventDefault();
      (event.shiftKey?last:first).focus();
    }else if(event.shiftKey&&active===first){
      event.preventDefault();
      last.focus();
    }else if(!event.shiftKey&&active===last){
      event.preventDefault();
      first.focus();
    }
  },true);
  const panel = document.createElement('div');
  panel.id = 'cf-panel';
  panel.className = 'card cf-create-workspace';
  panel.setAttribute('role','dialog');
  panel.setAttribute('aria-modal','true');
  panel.setAttribute('aria-label','创建视频项目工作区');
  panel.tabIndex=-1;
  panel.style.cssText = 'width:100%;max-width:none;height:100vh;max-height:100vh;min-height:100vh;overflow:auto;padding:clamp(18px,3vw,36px);border-radius:0;box-sizing:border-box';
  panel.innerHTML = _createPanelHtml();
  mask.appendChild(panel);
  document.body.appendChild(mask);
  CF.state = 'editing';
  CF.explicitFields = new Set();
  // 先暴露默认值加载 Promise；草稿/项目回填必须等待 Select 选项就绪，避免被异步默认值覆盖。
  window.CF_DEFAULTS_READY = createLoadDefaults();
  cfRestoreArticleSourceUI();
  // 2026-10-25 用户裁决：随时可回到新建项目对话框——带 projectId 时回填
  // 该项目的创作要求/文案（素材在项目里保留，file input 无法回填但清单提示）。
  if (projectId) cfPrefillFromProject(projectId);
  cfFocusCreateDialog();
}

/** 从已有项目回填表单（修改要求重新创作的入口）。 */
let _cfProjectPrefillRequest = 0;
function cfResetProjectPrefillState(projectId){
  const form=document.getElementById('cf-form');
  if(form){
    // #cf-form is a div host, not an HTMLFormElement. Reproduce native reset
    // semantics explicitly so project switches cannot inherit the prior form.
    for(const control of form.querySelectorAll('input,textarea,select')){
      if(control instanceof HTMLInputElement){
        if(control.type==='file'){
          control.value='';
          continue;
        }
        if(control.type==='checkbox'||control.type==='radio'){
          control.checked=control.defaultChecked;
          continue;
        }
      }
      if(control instanceof HTMLSelectElement){
        let hasDefaultSelection=false;
        for(const option of control.options){
          option.selected=option.defaultSelected;
          hasDefaultSelection=hasDefaultSelection||option.defaultSelected;
        }
        if(!hasDefaultSelection&&!control.multiple&&control.options.length){
          control.selectedIndex=0;
        }
        continue;
      }
      control.value=control.defaultValue;
    }
  }

  window.CF_DRAFT_CONTEXT={
    assetRefs:[],reference:null,article:null,repo:null,narrationUpload:'',
    projectId:String(projectId||''),projectPrefillBaseline:null
  };
  window._cfReference=null;
  window._cfReferenceFileObject=null;
  window._cfArticle=null;
  window._cfRepo=null;
  window._cfNarrationUpload='';
  window._cfAspect='';

  const textFields={
    'cf-project-name':'',
    'cf-project-type':'',
    'cf-director-brief':'',
    'cf-script':'',
    'cf-article-url':'',
    'cf-repo-root':'',
    'cf-series-name':'',
    'cf-watermark':'',
    'cf-lower-name':'',
    'cf-lower-role':'',
    'cf-enhanced':''
  };
  for(const [id,value] of Object.entries(textFields)){
    const node=document.getElementById(id);if(node)node.value=value;
  }
  for(const id of ['cf-director-plan-preview','cf-article-result','cf-reference-result','cf-repo-result']){
    const node=document.getElementById(id);if(node)node.innerHTML='';
  }
  const labels={
    'cf-article-status':'仅抓取公网 HTTP/HTTPS 页面；不访问内网、登录态或本机服务。',
    'cf-reference-status':'分析完成后加入导演上下文。',
    'cf-repo-status':'扫描 README / CHANGELOG 等证据文件。',
    'cf-assets-summary':'未添加本地素材',
    'cf-script-gen-status':'',
    'cf-enhance-status':'AI 润色结果会显示在权威文案区，可对比后采用。',
    'cf-status':'正在载入项目创作要求与素材……'
  };
  for(const [id,value] of Object.entries(labels)){
    const node=document.getElementById(id);if(node)node.textContent=value;
  }
  const clearArticle=document.getElementById('cf-clear-article-btn');
  if(clearArticle)clearArticle.disabled=true;
  const enhanceBox=document.getElementById('cf-enhance-box');
  if(enhanceBox)enhanceBox.style.display='none';
  const gfxArea=document.getElementById('cf-gfx-area');
  if(gfxArea)gfxArea.style.display='none';
  document.querySelectorAll('[data-studio-style]').forEach(node=>node.classList.remove('tab-active'));
  document.querySelectorAll('#cf-form details').forEach(node=>{node.open=false;});
  const styleDetail=document.getElementById('cf-style-contract-detail');
  if(styleDetail)styleDetail.textContent=CF_STUDIO_CATALOG?.styles?.length
    ?'所选风格由 StyleContract 写入正式生成链：字幕布局/字号、图片运动、转场预算、配乐音量和音效密度。'
    :'风格契约未通过环境检查，不提供未验证选项。';
  document.querySelectorAll('#cf-quick-actions button').forEach(button=>{button.disabled=false;});
  const go=document.getElementById('cf-go');
  if(go){go.disabled=false;go.textContent='AI 开始创作';}
  if(window.CF){window.CF.explicitFields=new Set();window.CF.state='editing';}
  if(typeof cfRefreshAssetSummary==='function')cfRefreshAssetSummary();
  if(typeof cfVoiceModeSync==='function')cfVoiceModeSync();
  if(typeof cfVisualSourceSync==='function')cfVisualSourceSync();
  if(typeof cfSeriesSync==='function')cfSeriesSync();
}
async function cfPrefillFromProject(pid){
  const requestId=++_cfProjectPrefillRequest;
  const status = document.getElementById('cf-status');
  try {
    if (window.CF_DEFAULTS_READY) await window.CF_DEFAULTS_READY;
    if(requestId!==_cfProjectPrefillRequest)return {ok:false,stale:true};
    cfResetProjectPrefillState(pid);
    const brief = await apiPost('/api/director/brief', {project_id: pid});
    if(requestId!==_cfProjectPrefillRequest)return {ok:false,stale:true};
    if(!brief||brief.ok!==true)
      throw new Error((brief&&brief.error)||'项目创作要求读取失败');
    const bundle = brief.bundle || {};
    {
      const el=document.getElementById('cf-director-brief');
      if(el)el.value=(brief&&brief.ok&&brief.brief)||'';
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
    const restoredAspect = spec.width && spec.height ? String(spec.width)+'x'+String(spec.height) : '';
    const settingFields = {
      'cf-project-name': spec.project_name,
      'cf-project-type': spec.project_type,
      'cf-platform': spec.platform || '',
      'cf-resolution': restoredAspect || undefined,
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
    // Older projects may have a platform but no persisted width/height pair.
    // Derive the same aspect ratio as the platform picker instead of retaining
    // the default portrait resolution from the previously mounted form.
    if(!restoredAspect&&spec.platform&&typeof createPlatformSync==='function')createPlatformSync();
    document.querySelectorAll('[data-studio-style]').forEach(button=>{
      button.classList.toggle('tab-active',!!spec.style_intent&&button.dataset.studioStyle===String(spec.style_intent));
    });

    if (window.CF) {
      // 新规格保留原始字段来源；旧项目规格缺少来源时才兼容推断非空字段。
      const knownFields = new Set(Object.values(CF_EXPLICIT_CONTROL_FIELDS));
      const restoredExplicit = Array.isArray(spec.explicit_fields)
        ? spec.explicit_fields
        : Object.entries(CF_EXPLICIT_CONTROL_FIELDS).filter(([id]) => {
            const value=settingFields[id];
            return value !== undefined && value !== null &&
              (typeof value !== 'string' || value.trim() !== '');
          }).map(([,field])=>field);
      window.CF.explicitFields = new Set(restoredExplicit.filter(field => knownFields.has(field)));
    }
    const scriptText = (bundle.script && bundle.script.text) || '';
    const ta = document.getElementById('cf-script');
    if (ta) ta.value = scriptText;
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
      if(requestId!==_cfProjectPrefillRequest)return {ok:false,stale:true};
      if (detail && !detail.error && detail.script) {
        const ta2 = document.getElementById('cf-script');
        if (ta2) ta2.value = detail.script;
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
    if(requestId!==_cfProjectPrefillRequest)return {ok:false,stale:true};
    if (typeof cfVoiceModeSync === 'function') cfVoiceModeSync();
    if (typeof cfVisualSourceSync === 'function') cfVisualSourceSync();
    if (typeof window.CF_DRAFT_CONTEXT !== 'undefined') window.CF_DRAFT_CONTEXT.projectId = pid;
    if (typeof window.CF_DRAFT_CONTEXT !== 'undefined') window.CF_DRAFT_CONTEXT.assetRefs = refs;
    const supplement=document.getElementById('cf-input-context-details');
    const restoredScript=(document.getElementById('cf-script')||{}).value||'';
    if(supplement&&(restoredScript.trim()||refs.length||window._cfReference||window._cfArticle||window._cfRepo))supplement.open=true;
    if(requestId!==_cfProjectPrefillRequest)return {ok:false,stale:true};
    if(typeof window.cfCaptureProjectPrefillBaseline==='function')
      window.cfCaptureProjectPrefillBaseline();
    if(typeof window.cfPersistCreateDraftNow==='function'){
      const saved=await window.cfPersistCreateDraftNow();
      if(!saved||!saved.ok)throw new Error(saved?.error||'项目草稿保存失败');
    }
    if (status) status.textContent =
      '已载入项目「' + pid + '」的创作要求、文案、素材与原始制作参数。修改后将创建新版本。';
  } catch (e) {
    if(requestId!==_cfProjectPrefillRequest)return {ok:false,stale:true};
    if (status) status.textContent = '项目信息载入部分失败（要求/文案仍可手动填写）。';
    return {ok:false,error:String(e)};
  }
  return {ok:true,project_id:pid};
}
function closeCreate(options){
  const restoreFocus=!options||options.restoreFocus!==false;
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
  if(restoreFocus)cfRestoreCreateDialogFocus();
}

function _createPanelHtml(){
  return `
  <div id="cf-form">
  <div style="display:flex;align-items:center;gap:10px">
    <div><h1 style="font-size:26px;margin:0">创建视频项目</h1><div class="hint" style="margin-top:4px">设置本次创作目标与素材输入；模型和 API 配置沿用系统设置，不会在这里修改。</div></div>
    <span style="flex:1"></span>
    <button class="btn-secondary btn-sm" onclick="closeCreate()">收起</button>
  </div>
  <div class="panel" id="cf-project-metadata" style="margin-top:12px;padding:14px">
    <div class="btn-row" style="gap:10px;align-items:flex-start;flex-wrap:wrap">
      <div style="flex:1;min-width:220px">
        <label for="cf-project-name">项目名称</label>
        <input type="text" id="cf-project-name" maxlength="80" placeholder="例如：品牌介绍短片 / 复利知识科普">
      </div>
      <div style="flex:1;min-width:220px">
        <label for="cf-project-type">项目类型</label>
        <input type="text" id="cf-project-type" maxlength="60" placeholder="自由填写，例如：产品介绍、知识科普、纪录片">
      </div>
    </div>
    <div class="hint" style="margin-top:6px">项目名称用于项目列表和后续检索；项目类型为自由文本，不限制固定分类。留空时沿用系统默认命名。</div>
  </div>
  <div class="panel" style="margin-top:12px;padding:14px">
    <div style="display:flex;align-items:center;gap:8px">
      <b style="font-size:15px">创作目标</b>
      <span class="hint">你只需要描述想做什么，其他步骤由 AI 自动编排</span>
    </div>
    <textarea id="cf-director-brief" style="min-height:82px;margin-top:8px"
      placeholder="例如：做一条 45 秒的视频，讲清楚为什么很多人越忙越没钱，口吻克制，画面以真实生活为主，适合抖音。"></textarea>
    <div class="hint" style="margin-top:8px">没有现成文案也可以直接开始；需要严格按原文制作时，再到「补充输入」粘贴权威文案。</div>
    <div id="cf-director-plan-preview" class="hint" style="margin-top:8px"></div>
  </div>
  <div class="panel" style="margin-top:10px;padding:12px">
    <div class="btn-row" style="align-items:center">
      <b>输入上下文</b><span class="hint">文案、素材、参考视频、软件项目可以同时提供，也可以只提供其中一部分。</span>
    </div>
    <div class="panel" style="margin-top:8px;padding:10px">
      <b>权威文案</b><span class="hint"> 可选；填写后按原文制作，不由 AI 改写</span>
      <textarea id="cf-script" style="min-height:110px;margin-top:6px" placeholder="粘贴你已经写好的文案；没有文案也可以留空，让 AI 根据目标和其他输入生成。"></textarea>
      <div class="hint" style="margin-top:5px">支持 [pause: 2s] / [停顿: 2s] / [silence: 1s]；停顿会真正进入旁白、字幕和镜头时间轴（0.1–10 秒）。</div>
    </div>
    <div class="panel" style="margin-top:8px;padding:10px">
      <b>文章链接</b><span class="hint"> 可选；提取公开网页正文作为创作资料，不会自动覆盖你已写的文案</span>
      <div class="btn-row" style="margin-top:6px;align-items:center">
        <input type="url" id="cf-article-url" class="rev-input" placeholder="公开文章或文档链接；软件仓库请使用下方「软件项目」扫描" style="flex:1;min-width:180px">
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
          <label>最终视频引擎</label>
          <span id="cf-render-engine-status" class="hint" role="status">FFmpeg · 当前生产渲染器（固定执行引擎）</span>
          <input type="hidden" id="cf-render-engine" value="ffmpeg">
        </div>
        <div id="cf-studio-graphics-status" class="hint" style="flex:2">动态图形引擎状态将由运行环境实际检查。</div>
      </div>
      <div id="cf-style-presets" class="btn-row" style="flex-wrap:wrap;gap:6px;font-size:12px;color:var(--muted);margin-top:7px"></div>
      <div id="cf-style-contract-detail" class="hint" style="margin-top:5px">只显示由现有 StyleContract 执行的风格。</div>
      <div id="cf-studio-template-list" class="hint" style="margin-top:7px"></div>
    </div>
  <details id="cf-manual-options" style="margin-top:12px">
    <summary style="cursor:pointer;color:var(--muted);font-size:13px">高级设置（AI 自动决定默认值；需要精确控制时再展开）</summary>
  <div id="cf-essentials">
    <div class="hint" id="cf-script-gen-status"></div>
    <div class="btn-row" style="align-items:center;flex-wrap:wrap">
      <span class="hint" id="cf-enhance-status">AI 润色结果会显示在权威文案区，可对比后采用。</span>
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
      <div style="flex:1"><label>成片总时长（秒，可选）</label>
        <input type="number" id="cf-duration" placeholder="例：30" min="3" step="1"></div>
      <div style="flex:1"><label>风格补充描述（选填）</label>
        <input type="text" id="cf-style" placeholder="补充画面质感或表达口吻；也可点选下方已核验风格"></div>
    </div>
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
            <option value="1080x1920" selected>1080×1920 · 9:16</option>
            <option value="1080x1440">1080×1440 · 3:4 小红书</option>
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
function cfUniqueAudioSelectOptions(items,prefix){
  const rows=Array.isArray(items)?items.filter(item=>item&&typeof item==='object'):[];
  const labelCounts=new Map();
  rows.forEach(item=>{
    const label=String(item.label||item.id||'');
    labelCounts.set(label,(labelCounts.get(label)||0)+1);
  });
  const used=new Set();
  return rows.flatMap((item,index)=>{
    const id=String(item.id||'').trim();
    if(!id)return [];
    const base=String(item.label||id).trim()||id;
    let label=base;
    if((labelCounts.get(base)||0)>1){
      const prefixValue=String(prefix||'').trim();
      const suffix=(prefixValue&&id.startsWith(prefixValue+'-')?id.slice(prefixValue.length+1):id)
        .replace(/[-_]+/g,' ').trim();
      label=base+' · '+(suffix||id);
    }
    if(used.has(label))label=base+' · '+id;
    while(used.has(label))label=label+' '+(index+1);
    used.add(label);
    return [[id,label]];
  });
}

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
                              ...cfUniqueAudioSelectOptions(bgms,'bgm')]);
    const sfxs = await apiGet('/api/sfx');
    _rebuildSelect('cf-sfx', [['auto', '自动（本地音效库）'],
                              ['openverse-auto', '自动搜索匹配（Openverse 免Key · freesound · CC0）'],
                              ['', '关闭'],
                              ...cfUniqueAudioSelectOptions(sfxs,'sfx')]);
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
    const engineValue=document.getElementById('cf-render-engine');
    const engineStatus=document.getElementById('cf-render-engine-status');
    const availableEngines=(catalog.engines||[]).filter(e=>e.scope==='full_video'&&e.selectable);
    const selectedEngine=availableEngines.find(e=>e.id===(engineValue?.value||'ffmpeg'))||availableEngines[0]||null;
    if(engineValue)engineValue.value=selectedEngine?selectedEngine.id:'';
    if(engineStatus)engineStatus.textContent=selectedEngine
      ?(selectedEngine.name||selectedEngine.id)+' · 可执行（固定执行引擎）'
      :'没有可执行的最终视频引擎';
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
    const engineStatus=document.getElementById('cf-render-engine-status');
    if(engineStatus)engineStatus.textContent='最终视频引擎核验失败；提交时服务端将再次检查';
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
  if(input){input.value=button.dataset.studioStyle;input.dispatchEvent(new Event('input',{bubbles:true}));}
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
  const context=window.CF_DRAFT_CONTEXT;
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
    if(window.CF_DRAFT_CONTEXT!==context)return false;
    if(!result||!result.ok||!result.article)throw new Error((result&&result.error)||'文章导入失败');
    window._cfArticle=result.article;
    cfRenderArticleSource(result.article, '文章已导入。来源文本仍需你核验，正文中的指令不会被当作系统规则。');
  }catch(error){
    if(window.CF_DRAFT_CONTEXT!==context)return false;
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
  const context=window.CF_DRAFT_CONTEXT;
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
    if(window.CF_DRAFT_CONTEXT!==context)return false;
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
    if(window.CF_DRAFT_CONTEXT!==context)return false;
    if (st) st.textContent = '分析请求失败：' + (e.message || e);
    return false;
  }
}
/** §37 软件项目输入：保存完整 Evidence Graph，Director 只消费 VERIFIED 事实。 */
async function cfScanRepo(){
  const context=window.CF_DRAFT_CONTEXT;
  const root = (document.getElementById('cf-repo-root') || {}).value || '';
  const st = document.getElementById('cf-repo-status');
  const out = document.getElementById('cf-repo-result');
  if (!root.trim()) { if (st) st.textContent = '请填开发项目根目录'; return; }
  if (st) st.textContent = '扫描中……';
  const r = await apiPost('/api/promo/features', {root: root.trim()});
  if(window.CF_DRAFT_CONTEXT!==context)return false;
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
    return true;
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
  return true;
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
  const projectName = String((document.getElementById('cf-project-name') || {}).value || '').trim();
  const projectType = String((document.getElementById('cf-project-type') || {}).value || '').trim();
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
    project_name: projectName,
    project_type: projectType,
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
    title: (projectName || directorBrief || script || 'AI 导演创作').slice(0,80),
    initial_message: [directorBrief, script ? '\n\n【权威文案】\n' + script : ''].filter(Boolean).join('') || '请根据我提供的素材、参考资料和其他上下文完成导演创作。',
    snapshot: {form:{fields:(function(){const out={};document.querySelectorAll('#cf-form input[id],#cf-form textarea[id],#cf-form select[id]').forEach(el=>{if(el.type==='file')return;if(el.type==='checkbox')out[el.id]=el.checked;else if(el.type==='radio'){if(el.checked)out[el.name||el.id]=el.value;}else out[el.id]=el.value;});return out;})(),explicit_fields:Array.from(CF.explicitFields||[]),context:{asset_refs:assetRefs,reference:window._cfReference||null,article:window._cfArticle||null,repo:window._cfRepo||null,narration_upload:uploadedNarration}},director_inputs:directorInputs,request_options:requestOptions}
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
  if (window.cfClearSavedDraft) await window.cfClearSavedDraft();
  window.CF_DRAFT_CONTEXT.projectId=wfResp.project_id||'';
  cfEnterProgressMode(runId);
}

/** 创作流程窗口模式：表单隐藏，弹窗变成进度面板（步骤+警告+取消返回修改）。 */
let _cfProgressTimer = null;
let _cfStopPendingRunId = "";
function cfEnterProgressMode(runId){
  window._cfActiveRunId = runId;
  _cfStopPendingRunId = "";
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
          if (_cfStopPendingRunId === runId) _cfStopPendingRunId = "";
          if (window._cfActiveRunId === runId) window._cfActiveRunId = "";
          cfFinalizeCreateDialog();
          if (result.project_id) {
            editorToast(run.status === 'WAITING_CONFIRMATION' ? '方案已生成，正在打开工作台……' : '创作完成，已打开工作台');
            await workspaceOpen(result.project_id);
            await directorLoadRun(runId);
          }
          loadProjects();
        } else if (run.status === 'CANCELLED') {
          CF.state = 'idle';  // 已停止：表单可改可重提
          if (_cfStopPendingRunId === runId) {
            cfRestoreCreateFormAfterStop(
              runId,
              '任务已确认停止。你的创作要求、文案和素材均已保留，可以修改后重新提交。'
            );
          }
        } else {
          CF.state = 'failed';
          if (_cfStopPendingRunId === runId) {
            cfRestoreCreateFormAfterStop(
              runId,
              '任务已先行失败：' + (run.error || '未知原因') + '。已恢复创作要求，可修改后重试。'
            );
          }
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

function cfRestoreCreateFormAfterStop(runId, message){
  if (_cfStopPendingRunId !== runId) return;
  if (_cfProgressTimer) { clearInterval(_cfProgressTimer); _cfProgressTimer = null; }
  const panel = document.getElementById('cf-progress');
  if (panel) panel.remove();
  const form = document.getElementById('cf-form');
  if (form) form.style.display = '';
  const status = document.getElementById('cf-status');
  if (status) status.textContent = message || '任务已结束。你的要求和素材都保留在表单里，可以修改后重新提交。';
  const btn = document.getElementById('cf-go');
  if (btn) { btn.disabled = false; btn.textContent = 'AI 开始创作'; }
  _cfStopPendingRunId = '';
  if (window._cfActiveRunId === runId) window._cfActiveRunId = '';
  CF.state = 'editing';
}

/** 停止当前 WorkflowRun，并在服务端确认终态后返回修改要求。 */
async function cfCancelAndEdit(){
  const rid = window._cfActiveRunId;
  if (!rid) {
    editorToast('没有可停止的创作任务', true);
    return;
  }
  const action = document.querySelector('#cf-progress button[onclick="cfCancelAndEdit()"]');
  if (action) { action.disabled = true; action.textContent = '正在请求停止…'; }
  let response;
  try {
    response = await apiPost('/api/workflows/cancel', {run_id: rid});
  } catch (error) {
    if (action) { action.disabled = false; action.textContent = '停止并返回修改要求'; }
    editorToast('停止请求失败：' + String(error && error.message || error), true);
    return;
  }
  if (!response || !response.ok) {
    if (action) { action.disabled = false; action.textContent = '停止并返回修改要求'; }
    editorToast('停止失败：' + ((response && response.error) || '任务仍在运行'), true);
    return;
  }
  // A terminal result may win the race before the cancellation request reaches
  // the worker. Only CANCELLED is reported as stopped; a failed task can still
  // return to editing, while a completed task remains on the normal success path.
  if (response.status && response.status !== 'RUNNING') {
    if (response.status === 'CANCELLED' || response.status === 'FAILED' ||
        response.status === 'INTERRUPTED') {
      _cfStopPendingRunId = rid;
      cfRestoreCreateFormAfterStop(
        rid,
        response.status === 'CANCELLED'
          ? '任务已确认停止。你的创作要求、文案和素材均已保留，可以修改后重新提交。'
          : '任务已先行结束（' + response.status + '）。已恢复创作要求，可修改后重试。'
      );
    } else if (action) {
      action.disabled = false;
      action.textContent = '停止并返回修改要求';
      editorToast(response.note || '任务已先行完成，请在工作台查看结果。');
    }
    return;
  }

  _cfStopPendingRunId = rid;
  // Close the small race where the worker reaches CANCELLED before the POST
  // response has returned and the periodic poller has already stopped.
  try {
    const current = await apiGet('/api/workflows/' + encodeURIComponent(rid));
    if (current && ['CANCELLED', 'FAILED', 'INTERRUPTED'].includes(current.status)) {
      cfRestoreCreateFormAfterStop(
        rid,
        current.status === 'CANCELLED'
          ? '任务已确认停止。你的创作要求、文案和素材均已保留，可以修改后重新提交。'
          : '任务已先行结束（' + current.status + '）。已恢复创作要求，可修改后重试。'
      );
      return;
    }
    if (current && ['COMPLETED', 'WAITING_CONFIRMATION'].includes(current.status)) {
      _cfStopPendingRunId = '';
      if (action) { action.disabled = false; action.textContent = '停止并返回修改要求'; }
      editorToast('任务已先行完成，请在工作台查看结果。');
      return;
    }
  } catch (_) {
    // Keep the active progress poller as the recovery path for transient GET errors.
  }

  const panel = document.getElementById('cf-progress');
  if (panel) panel.remove();
  const form = document.getElementById('cf-form');
  if (form) form.style.display = '';
  const status = document.getElementById('cf-status');
  if (status) status.textContent =
    '已请求停止，正在等待服务端确认任务终态。创作要求、文案和素材已保留；确认停止前不能重新提交。';
  const btn = document.getElementById('cf-go');
  if (btn) { btn.disabled = true; btn.textContent = '正在确认停止…'; }
  // The existing progress timer stays alive until it observes a terminal run.
  CF.state = 'editing';
}

/** 后台继续：关窗口，创作不停（工作台「AI 导演流程」面板可继续看）。 */
async function cfDismissProgress(){
  if (_cfProgressTimer) { clearInterval(_cfProgressTimer); _cfProgressTimer = null; }
  // “后台继续”与普通“收起”不同：先保存可编辑表单，再卸载全屏遮罩，
  // 让工作台任务抽屉重新可交互；绝不清除 active draft。
  const saved=await closeCreate();
  if(saved===false){
    editorToast('草稿保存未确认，窗口仍保持打开；请先处理保存状态',true);
    return false;
  }
  const mask=document.getElementById('cf-mask');
  if(mask)mask.remove();
  for(const id of ['sg-mask','tg-mask']){
    const child=document.getElementById(id);if(child)child.remove();
  }
  if(window.CF)window.CF.state='idle';
  editorToast('创作在后台继续；打开项目后可在「AI 导演流程」面板查看每步进展');
  return true;
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
  const context=window.CF_DRAFT_CONTEXT;
  const topic = (document.getElementById('cf-director-brief') || {}).value || '';
  const status = document.getElementById('cf-script-gen-status');
  if (!topic.trim()) { if (status) status.textContent = '请先填写主题'; return; }
  const btn = document.querySelector('#cf-quick-actions button[onclick="cfGenerateScript()"]');
  if (btn) btn.disabled = true;
  if (status) status.textContent = '正在生成文案……';
  try {
    const platform = (document.getElementById('cf-platform') || {}).value || '';
    const duration = (document.getElementById('cf-duration') || {}).value || '';
    const style = (document.getElementById('cf-style') || {}).value || '';
    const r = await apiPost('/api/script/generate', {topic: topic.trim(), platform, duration, style});
    if(window.CF_DRAFT_CONTEXT!==context)return false;
    if (!r.ok) throw new Error(r.error || '生成失败');
    const ta = document.getElementById('cf-script');
    if (ta) ta.value = r.script || '';
    if (status) status.textContent = '文案已生成，可直接修改后创作。';
  } catch (e) {
    if(window.CF_DRAFT_CONTEXT!==context)return false;
    if (status) status.textContent = '生成失败：' + (e.message || e);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function createEnhance(){
  const context=window.CF_DRAFT_CONTEXT;
  const script = (document.getElementById('cf-script') || {}).value;
  const status = document.getElementById('cf-enhance-status');
  if (!script.trim()) { status.textContent = '请先填文案'; return; }
  status.textContent = '润色中……';
  try {
    const r = await apiPost('/api/enhance', {script});
    if(window.CF_DRAFT_CONTEXT!==context)return false;
    if (!r.ok && r.error) throw new Error(r.error);
    document.getElementById('cf-enhance-box').style.display = '';
    document.getElementById('cf-enhanced').value = r.enhanced || script;
    status.textContent = r.changed ? 'AI 已润色（对比后采用）' : 'AI 认为原文已足够好，未改动';
  } catch (e) {
    if(window.CF_DRAFT_CONTEXT!==context)return false;
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
window.CF_DRAFT_CONTEXT = window.CF_DRAFT_CONTEXT || {assetRefs:[],reference:null,article:null,repo:null,narrationUpload:'',projectPrefillBaseline:null};
(function installCreateLifecycle(){
  const baseOpen=window.openCreate, baseClose=window.closeCreate;
  if(typeof baseOpen!=='function'||typeof baseClose!=='function')return;
  let timer=null, saving=Promise.resolve(), restored=false, restorePromise=null, restoredDraftAvailable=false;
  let draftClearPending=false,draftRestorePending=false,draftRestoreNeedsSync=false;
  let activeProjectPrefill=null;
  let historyCursor=null, historyHasMore=false, historyLoadingMore=false, historyRequestVersion=0;
  // Capture the live defaults only after asynchronous catalog/default hydration.
  // Comparing against raw HTML defaultSelected misses dynamically populated selects.
  let defaultFieldsSnapshot=null;
  const dirtyFields=new Set();
  const LOCAL_DRAFT_KEY='video-director:create-draft:v1';
  function writeLocalDraft(data){
    try{localStorage.setItem(LOCAL_DRAFT_KEY,JSON.stringify({version:1,saved_at:Date.now(),data}));return true;}
    catch(_){return false;}
  }
  function readLocalDraft(){
    try{const saved=JSON.parse(localStorage.getItem(LOCAL_DRAFT_KEY)||'null');return saved&&saved.version===1&&saved.data&&typeof saved.data==='object'?saved:null;}
    catch(_){return null;}
  }
  function clearLocalDraft(){try{localStorage.removeItem(LOCAL_DRAFT_KEY);}catch(_){}}

  function collectFields(){
    const fields={};
    document.querySelectorAll('#cf-form input[id],#cf-form textarea[id],#cf-form select[id]').forEach(el=>{
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
    return {version:1,fields:collectFields(),explicit_fields:Array.from(window.CF?.explicitFields||[]),context:{
      asset_refs:Array.isArray(c.assetRefs)?c.assetRefs:[],
      reference:c.reference||window._cfReference||null,
      article:c.article||window._cfArticle||null,
      repo:c.repo||window._cfRepo||null,
      narration_upload:c.narrationUpload||window._cfNarrationUpload||'',
      project_id:c.projectId||'',
      project_prefill_baseline:c.projectPrefillBaseline||null
    },ui:{disclosures:Object.fromEntries(Array.from(document.querySelectorAll('#cf-form details[id]')).map(node=>[node.id,!!node.open]))}};
  }
  function draftPersistenceBlocked(){
    if(draftClearPending||draftRestorePending)return true;
    const form=document.getElementById('cf-form');
    return !!(form&&form.style.display==='none'&&document.getElementById('cf-progress'));
  }
  async function saveNow(){
    if(draftPersistenceBlocked())return {ok:false,skipped:true,error:'draft persistence is suspended while a creation run is active'};
    if(!document.getElementById('cf-form'))return {ok:false,error:'create form is not mounted'};
    const data=snapshot();
    const localSaved=writeLocalDraft(data);
    if(localSaved)restoredDraftAvailable=true;
    saving=saving.catch(()=>{}).then(()=>apiPost('/api/director/drafts/active',{data}));
    try{
      const r=await saving,st=document.getElementById('cf-draft-status');
      if(r&&r.ok){
        if(st)st.textContent='已同步保存 '+new Date(r.updated_at*1000).toLocaleTimeString();
        return r;
      }
      if(st)st.textContent=localSaved?'本机草稿已保存，服务端同步失败：'+((r&&r.error)||'稍后重试'):'保存失败：'+((r&&r.error)||'服务未确认');
      return {ok:localSaved,localOnly:localSaved,error:(r&&r.error)||'server draft save failed'};
    }catch(e){
      const st=document.getElementById('cf-draft-status');
      if(st)st.textContent=localSaved?'已保存到本机；服务端暂不可用':'保存失败：'+(e.message||e);
      return {ok:localSaved,localOnly:localSaved,error:String(e)};
    }
  }
  window.cfCaptureProjectPrefillBaseline=function(){
    const data=snapshot(),context=window.CF_DRAFT_CONTEXT||{};
    const clone=value=>{
      if(value===undefined||value===null)return null;
      try{return JSON.parse(JSON.stringify(value));}catch(_){return null;}
    };
    context.projectPrefillBaseline={
      fields:{...(data.fields||{})},
      asset_refs:clone(context.assetRefs||[]),
      reference:clone(context.reference||window._cfReference||null),
      article:clone(context.article||window._cfArticle||null),
      repo:clone(context.repo||window._cfRepo||null),
      narration_upload:context.narrationUpload||window._cfNarrationUpload||''
    };
    return context.projectPrefillBaseline;
  };
  window.cfPersistCreateDraftNow=saveNow;
  function scheduleSave(){
    if(draftClearPending||cfHistoryInProgressMode()){
      if(timer){clearTimeout(timer);timer=null;}
      return false;
    }
    // Keep user input locally while the initial server draft is being read,
    // but do not POST a partial form over the server snapshot being restored.
    const localSaved=writeLocalDraft(snapshot());
    if(timer){clearTimeout(timer);timer=null;}
    const st=document.getElementById('cf-draft-status');
    if(draftRestorePending){
      if(st)st.textContent=localSaved?'本机草稿已暂存，正在合并已保存内容…':'正在合并已保存草稿…';
      return false;
    }
    if(st)st.textContent=localSaved?'本机已保存，正在同步…':'正在保存…';
    timer=setTimeout(()=>{timer=null;saveNow();},450);
    return true;
  }
  function persistImmediately(){
    if(timer){clearTimeout(timer);timer=null;}
    return saveNow();
  }
  function revealSupplementaryInput(targetId){
    const details=document.getElementById('cf-input-context-details');
    if(details)details.open=true;
    const target=document.getElementById(targetId||'cf-script');
    if(target){target.scrollIntoView({behavior:'smooth',block:'center'});target.focus();}
  }
  function applySnapshot(data,preserveDirty=false){
    if(!data||typeof data!=='object')return;
    for(const [id,value] of Object.entries(data.fields||{})){
      if(preserveDirty&&dirtyFields.has(id))continue;
      if(id==='cf-scope'){const radio=document.querySelector('input[name="cf-scope"][value="'+CSS.escape(String(value))+'"]');if(radio)radio.checked=true;continue;}
      const el=document.getElementById(id);if(!el||el.type==='file')continue;
      if(el.type==='checkbox')el.checked=!!value;else el.value=value==null?'':String(value);
    }
    for(const [id,isOpen] of Object.entries(data.ui?.disclosures||{})){
      const disclosure=document.getElementById(id);
      if(disclosure&&disclosure.tagName==='DETAILS')disclosure.open=!!isOpen;
    }
    const knownExplicit=new Set(Object.values(CF_EXPLICIT_CONTROL_FIELDS));
    const restoredExplicit=Array.isArray(data.explicit_fields)?data.explicit_fields.filter(field=>knownExplicit.has(field)):[];
    const currentExplicit=preserveDirty?Array.from(window.CF?.explicitFields||[]):[];
    if(window.CF)window.CF.explicitFields=new Set([...restoredExplicit,...currentExplicit]);
    const c=data.context||{};
    // Do not replace this object: in-flight asset/article/reference/narration uploads
    // capture it and discard their response if the active draft context changes.
    const liveContext=window.CF_DRAFT_CONTEXT||(window.CF_DRAFT_CONTEXT={
      assetRefs:[],reference:null,article:null,repo:null,narrationUpload:'',projectId:''
    });
    const contextIsDirty=id=>preserveDirty&&dirtyFields.has(id);
    const assetKey=item=>String(item?.asset_id||item?.file||JSON.stringify(item));
    if(contextIsDirty('cf-assets')){
      const merged=new Map();
      for(const item of [...(Array.isArray(c.asset_refs)?c.asset_refs:[]),...(Array.isArray(liveContext.assetRefs)?liveContext.assetRefs:[])]){
        if(!item)continue;
        merged.set(assetKey(item),item);
      }
      liveContext.assetRefs=Array.from(merged.values());
    }else{
      liveContext.assetRefs=Array.isArray(c.asset_refs)?c.asset_refs:[];
    }
    if(!contextIsDirty('cf-reference')){
      liveContext.reference=c.reference||null;
      window._cfReference=liveContext.reference;
      window._cfReferenceFileObject=null;
      if(window._cfReference){
        const node=document.getElementById('cf-reference-result');
        if(node)node.innerHTML='<div class="ok-box">已恢复参考视频分析，可直接用于创作。</div>';
      }
    }else{
      liveContext.reference=liveContext.reference||window._cfReference||null;
      window._cfReference=liveContext.reference;
    }
    if(!contextIsDirty('cf-article-url')){
      liveContext.article=c.article||null;
      window._cfArticle=liveContext.article;
      if(window._cfArticle&&typeof cfRenderArticleSource==='function')
        cfRenderArticleSource(window._cfArticle,'已恢复上一轮导入的文章来源。请核对后再创作。');
    }else{
      liveContext.article=liveContext.article||window._cfArticle||null;
      window._cfArticle=liveContext.article;
    }
    if(!contextIsDirty('cf-repo-root')){
      liveContext.repo=c.repo||null;
      window._cfRepo=liveContext.repo;
      if(window._cfRepo){
        const node=document.getElementById('cf-repo-result');
        if(node)node.innerHTML='<div class="ok-box">已恢复软件项目证据与已验证卖点。</div>';
      }
    }else{
      liveContext.repo=liveContext.repo||window._cfRepo||null;
      window._cfRepo=liveContext.repo;
    }
    if(!contextIsDirty('cf-voice-file')){
      liveContext.narrationUpload=c.narration_upload||'';
      window._cfNarrationUpload=liveContext.narrationUpload;
    }else{
      liveContext.narrationUpload=liveContext.narrationUpload||window._cfNarrationUpload||'';
      window._cfNarrationUpload=liveContext.narrationUpload;
    }
    liveContext.projectId=c.project_id||'';
    liveContext.projectPrefillBaseline=c.project_prefill_baseline||null;
    const summary=document.getElementById('cf-assets-summary');
    if(summary)summary.textContent=liveContext.assetRefs.length?
      '已暂存 '+liveContext.assetRefs.length+' 个素材，可直接用于创作':'未添加本地素材';
    if(typeof cfVoiceModeSync==='function')cfVoiceModeSync();
    if(typeof cfVisualSourceSync==='function')cfVisualSourceSync();
    if(typeof cfSeriesSync==='function')cfSeriesSync();
  }
  async function restoreDraft(){
    if(restored)return restorePromise||Promise.resolve();
    restored=true;
    draftRestorePending=true;
    restorePromise=(async()=>{
      try{
        if(window.CF_DEFAULTS_READY)await window.CF_DEFAULTS_READY;
        // This is the clean baseline for the current form generation. Capture it
        // before applying a persisted draft, so dynamic audio/voice options and
        // the default radio group are compared against their actual hydrated values.
        if(!defaultFieldsSnapshot)defaultFieldsSnapshot=collectFields();
        const local=readLocalDraft();
        let remote=null,remoteError=null;
        try{
          const r=await apiGet('/api/director/drafts/active');
          if(r&&r.ok&&r.draft&&r.draft.data)remote={data:r.draft.data,updated_at:Number(r.draft.updated_at||0)*1000};
        }catch(error){remoteError=error;}
        const useLocal=!!(local&&local.data&&(!remote||Number(local.saved_at||0)>Number(remote.updated_at||0)));
        const data=useLocal?local.data:(remote&&remote.data)||(local&&local.data)||null;
        restoredDraftAvailable=!!data;
        if(data){
          applySnapshot(data,true);
          const context=data.context||{};
          const hasSupplementaryContent=!!String(data.fields?.['cf-script']||'').trim()
            ||!!(context.asset_refs&&context.asset_refs.length)
            ||!!context.reference||!!context.article||!!context.repo;
          const supplement=document.getElementById('cf-input-context-details');
          const savedDisclosures=data.ui?.disclosures||{};
          const hasSavedSupplementState=Object.prototype.hasOwnProperty.call(
            savedDisclosures,'cf-input-context-details'
          );
          // Preserve an explicit user choice. Auto-open only for snapshots
          // written before disclosure state was persisted.
          if(supplement&&hasSupplementaryContent&&!hasSavedSupplementState)supplement.open=true;
          writeLocalDraft(snapshot());
          const st=document.getElementById('cf-draft-status');
          if(st)st.textContent=useLocal?'已恢复本机草稿，正在同步到项目服务':'已恢复上次保存的创作草稿';
          if(useLocal)draftRestoreNeedsSync=true;
        }else if(!remoteError&&!dirtyFields.size){
          // A finalized/submitted conversation may leave its last context object in memory.
          // When neither server nor local draft exists, a new create session starts clean.
          cfResetProjectPrefillState('');
          const st=document.getElementById('cf-draft-status');
          if(st)st.textContent='暂无已保存的创作草稿，可直接开始新创作。';
        }else if(remoteError){
          const st=document.getElementById('cf-draft-status');
          if(st)st.textContent='服务端读取失败；没有可恢复的本机草稿：'+(remoteError.message||remoteError);
        }
      }catch(e){
        const st=document.getElementById('cf-draft-status');
        if(st)st.textContent='草稿读取失败：'+(e.message||e);
      }finally{
        draftRestorePending=false;
        if(dirtyFields.size||draftRestoreNeedsSync){
          draftRestoreNeedsSync=false;
          if(timer){clearTimeout(timer);timer=null;}
          const saved=await saveNow();
          if(!saved?.ok){
            const st=document.getElementById('cf-draft-status');
            if(st)st.textContent='草稿已恢复到界面，但服务端同步未完成；本机副本仍保留。';
          }
        }
      }
    })();
    return restorePromise;
  }
  function arrangeForm(){
    const form=document.getElementById('cf-form');if(!form||form.dataset.arranged==='1')return;form.dataset.arranged='1';
    const header=form.firstElementChild;
    if(header){
      const history=document.createElement('button');history.id='cf-history-inline-launch';history.type='button';history.className='btn-secondary btn-sm';history.textContent='历史对话';history.title='打开历史创作对话';history.onclick=cfHistoryToggle;
      header.insertBefore(history,header.querySelector('span[style*="flex:1"]'));
      const status=document.createElement('span');status.id='cf-draft-status';status.className='hint';status.style.marginLeft='8px';status.textContent='草稿待保存';
      header.insertBefore(status,history);
      const close=header.querySelector('button[onclick="closeCreate()"]');if(close){close.textContent='收起';close.title='隐藏窗口，保留当前草稿';}
    }
    const brief=document.getElementById('cf-director-brief');
    const goalPanel=brief?.closest('.panel');
    const scriptPanel=document.getElementById('cf-script')?.closest('.panel');

    // 文案生成与图形预览属于创作目标操作，保持常驻可见。
    if(brief&&goalPanel&&!document.getElementById('cf-quick-actions')){
      const actions=document.createElement('div');actions.id='cf-quick-actions';actions.className='btn-row';actions.style.marginTop='8px';
      actions.innerHTML='<button type="button" class="btn-secondary btn-sm" onclick="cfGenerateScript()">单独生成文案</button><button type="button" class="btn-secondary btn-sm" onclick="cfMakeGraphicPlan()">图形方案预览</button>';
      goalPanel.appendChild(actions);
    }
    // 润色依赖权威文案，因此放在文案字段旁边。
    if(scriptPanel&&!document.getElementById('cf-script-actions')){
      const actions=document.createElement('div');actions.id='cf-script-actions';actions.className='btn-row';actions.style.marginTop='8px';
      actions.innerHTML='<button type="button" class="btn-secondary btn-sm" onclick="createEnhance()">AI 润色文案</button>';
      scriptPanel.appendChild(actions);
    }
    const quickActions=document.getElementById('cf-quick-actions');
    const scriptActions=document.getElementById('cf-script-actions');
    form.querySelectorAll('button[onclick="createEnhance()"]').forEach(button=>{
      if(!scriptActions?.contains(button))button.remove();
    });
    form.querySelectorAll('button[onclick="cfMakeGraphicPlan()"]').forEach(button=>{
      if(!quickActions?.contains(button))button.remove();
    });
    const scriptStatus=document.getElementById('cf-script-gen-status');
    const enhanceStatus=document.getElementById('cf-enhance-status');
    const enhanceStatusRow=enhanceStatus?.closest('.btn-row');
    if(quickActions&&scriptStatus){scriptStatus.style.flexBasis='100%';quickActions.appendChild(scriptStatus);}
    if(scriptActions&&enhanceStatus){
      enhanceStatus.style.flexBasis='100%';scriptActions.appendChild(enhanceStatus);
      if(enhanceStatusRow&&enhanceStatusRow!==scriptActions)enhanceStatusRow.remove();
    }
    const enhanceBox=document.getElementById('cf-enhance-box');
    if(scriptPanel&&enhanceBox&&!scriptPanel.contains(enhanceBox))scriptPanel.appendChild(enhanceBox);
    document.getElementById('cf-topic')?.closest('.btn-row')?.remove();

    // These five sources are complementary, not five competing creation modes.
    const contextPanel=Array.from(form.children).find(node=>
      node.classList?.contains('panel') &&
      node.querySelector(':scope > .btn-row > b')?.textContent.trim()==='输入上下文'
    );
    if(contextPanel&&!document.getElementById('cf-input-context-details')){
      const disclosure=document.createElement('details');disclosure.id='cf-input-context-details';
      const summary=document.createElement('summary');
      summary.style.cssText='cursor:pointer;color:var(--text);font-size:13px;font-weight:600';
      summary.textContent='补充输入（选填）：权威文案、图片/视频、参考视频、文章、软件项目';
      const content=document.createElement('div');content.style.marginTop='10px';
      Array.from(contextPanel.children).forEach((child,i)=>{if(i===0)child.remove();else content.appendChild(child);});
      disclosure.append(summary,content);contextPanel.replaceChildren(disclosure);
    }

    const manual=document.getElementById('cf-manual-options');
    if(manual){
      const formatRow=document.getElementById('cf-platform')?.closest('.btn-row');
      const visualRow=document.getElementById('cf-visual-source')?.closest('.btn-row');
      const enginePanel=document.getElementById('cf-studio-catalog-status')?.closest('.panel');
      const output=document.createElement('section');output.id='cf-output-requirements';output.className='panel';output.style.marginTop='10px';
      output.innerHTML='<div class="btn-row" id="cf-output-header" style="align-items:center;flex-wrap:wrap;margin:0"><b>成片要求</b><span class="hint">主要设置平台、时长和风格；其他参数可沿用 AI 默认值。</span></div>';
      const outputHeader=output.firstElementChild;
      manual.parentNode.insertBefore(output,manual);
      if(formatRow)output.appendChild(formatRow);
      if(visualRow)output.appendChild(visualRow);
      const styles=document.getElementById('cf-style-presets');if(styles)output.appendChild(styles);
      const styleDetail=document.getElementById('cf-style-contract-detail');if(styleDetail)output.appendChild(styleDetail);

      // Template selection is part of the visual direction, not a separate step.
      const templateButton=document.getElementById('cf-template-gallery-btn');
      if(templateButton){
        const originalTemplatePanel=templateButton.closest('.panel');
        templateButton.textContent='模板与真实样片';
        templateButton.style.marginLeft='auto';
        outputHeader.appendChild(templateButton);
        if(originalTemplatePanel&&originalTemplatePanel!==output)originalTemplatePanel.remove();
      }

      // Capability diagnostics are read-only information. Keep them accessible,
      // but do not present the fixed FFmpeg backend as a choice.
      if(enginePanel){
        const disclosure=document.createElement('details');disclosure.id='cf-capability-details';disclosure.style.marginTop='8px';
        const summary=document.createElement('summary');summary.style.cssText='cursor:pointer;color:var(--muted);font-size:12px';summary.textContent='本机执行能力核验';
        disclosure.appendChild(summary);
        for(const id of ['cf-studio-catalog-status','cf-studio-graphics-status','cf-render-engine','cf-studio-template-list']){
          const node=document.getElementById(id);if(node){if(id==='cf-studio-template-list')node.style.display='none';disclosure.appendChild(node);}
        }
        const engineStatus=document.getElementById('cf-render-engine-status');
        if(engineStatus)outputHeader.appendChild(engineStatus);
        output.appendChild(disclosure);
        enginePanel.remove();
      }

      // Merge voice / subtitle / music / SFX / watermark / export into the same
      // advanced disclosure as visual-source and timeline-generation controls.
      const more=document.getElementById('cf-more-settings');
      const moreDetails=more?.closest('details');
      const essentials=document.getElementById('cf-essentials');
      if(more&&moreDetails&&essentials&&!essentials.contains(more)){
        const heading=document.createElement('div');heading.className='hint';
        heading.style.cssText='margin-top:14px;padding-top:10px;border-top:1px solid var(--border)';
        heading.innerHTML='<b>配音、字幕、音乐与导出</b><span> · 按需设置</span>';
        essentials.append(heading,more);moreDetails.remove();
      }
    }
  }
  async function uploadAssets(input){
    const context=window.CF_DRAFT_CONTEXT;
    const files=Array.from(input.files||[]);if(!files.length)return;
    const status=document.getElementById('cf-assets-summary');if(status)status.textContent='正在把素材暂存到素材库…';
    const fd=new FormData();files.forEach(f=>fd.append('files',f,f.name));
    try{
      const r=await apiUpload('/api/assets/upload',fd);if(!r||!r.ok)throw new Error(r?.error||'上传失败');
      if(window.CF_DRAFT_CONTEXT!==context)return false;
      const refs=context.assetRefs,seen=new Set(refs.map(x=>x.asset_id||x.file));
      for(const a of (r.imported||[])){const key=a.asset_id||a.file;if(key&&!seen.has(key)){refs.push({asset_id:a.asset_id,type:a.type,file:a.file,width:a.width,height:a.height,duration:a.duration,commercial_status:a.commercial_status});seen.add(key);}}
      if(status)status.textContent='已暂存 '+refs.length+' 个素材，关闭窗口或刷新后仍可恢复。';
      await saveNow();
      return true;
    }catch(e){
      if(window.CF_DRAFT_CONTEXT!==context)return false;
      if(status)status.textContent='素材暂存失败：'+(e.message||e)+'。请重试后再关闭。';
      throw e;
    }
  }
  async function uploadNarration(file){
    const context=window.CF_DRAFT_CONTEXT;
    const fd=new FormData();fd.append('files',file,file.name);
    const r=await apiUpload('/api/assets/stage',fd);if(!r||!r.ok||!(r.audio||[]).length)throw new Error(r?.error||'配音暂存失败');
    if(window.CF_DRAFT_CONTEXT!==context)return '';
    context.narrationUpload=r.audio[0];window._cfNarrationUpload=r.audio[0];await saveNow();return r.audio[0];
  }
  function installHandlers(){
    const form=document.getElementById('cf-form');if(!form||form.dataset.draftHandlers==='1')return;form.dataset.draftHandlers='1';
    function markDraftDirty(event){
      const target=event.target;
      if(target?.id)dirtyFields.add(target.id);
      if(target?.name)dirtyFields.add(target.name);
      form.dataset.draftDirty='1';
      scheduleSave();
    }
    form.addEventListener('input',markDraftDirty,true);
    form.addEventListener('toggle',event=>{
      if(event.target?.tagName==='DETAILS')scheduleSave();
    },true);
    form.addEventListener('change',e=>{
      markDraftDirty(e);
      if(e.target?.id==='cf-assets')window.CF_DRAFT_CONTEXT.assetUploadPromise=uploadAssets(e.target).catch(()=>false);
      if(e.target?.id==='cf-reference'&&e.target.files?.length){
        window._cfReference=null;window.CF_DRAFT_CONTEXT.reference=null;
        cfAnalyzeReference().then(ok=>{if(ok){window.CF_DRAFT_CONTEXT.reference=window._cfReference;saveNow();}});
      }
      if(e.target?.id==='cf-voice-file'&&e.target.files?.length)
        window.CF_DRAFT_CONTEXT.narrationPromise=uploadNarration(e.target.files[0]).catch(error=>{const st=document.getElementById('cf-status');if(st)st.textContent='配音暂存失败：'+(error.message||error);return '';});
    },true);
  }
  async function cancelProjectPrefillAndRestore(){
    const transaction=activeProjectPrefill;
    if(!transaction)return {ok:true,cancelled:false};
    if(transaction.cancelPromise)return transaction.cancelPromise;
    transaction.cancelled=true;
    // cfPrefillFromProject checks this generation after every awaited request.
    _cfProjectPrefillRequest++;
    transaction.cancelPromise=(async()=>{
      // Do not wait for the project brief request: its generation checks make
      // any later response inert. If its final draft POST was already queued,
      // saveNow() appends the rollback after it on the serialized save chain.
      if(activeProjectPrefill!==transaction)return {ok:true,cancelled:true,stale:true};
      cfResetProjectPrefillState(transaction.previousProjectId);
      applySnapshot(transaction.snapshot);
      dirtyFields.clear();
      for(const id of transaction.dirtyFields)dirtyFields.add(id);
      restoredDraftAvailable=transaction.hadDraft;
      activeProjectPrefill=null;
      const saved=transaction.hadDraft?await saveNow():await clearSavedDraft();
      if(!saved||saved.ok!==true){
        return {ok:false,error:saved?.error||'无法恢复切换前的草稿'};
      }
      const status=document.getElementById('cf-status');
      if(status)status.textContent='项目载入已取消，已恢复切换前的草稿。';
      return {ok:true,cancelled:true};
    })();
    return transaction.cancelPromise;
  }

  async function openProjectWithDraftGuard(projectId){
    const target=String(projectId||'').trim();
    if(!target)return false;
    if(activeProjectPrefill){
      const cancelled=await cancelProjectPrefillAndRestore();
      if(!cancelled?.ok)return cancelled;
    }
    if(cfHistoryInProgressMode()){
      const message='当前创作任务仍在执行，不能从历史记录切换项目。请等待任务结束，或先停止并返回编辑。';
      cfHistorySetNotice(message);
      const status=document.getElementById('cf-status');
      if(status)status.textContent=message;
      return false;
    }
    const existing=document.getElementById('cf-mask');
    if(existing){
      if(existing.style.display==='none'||existing.getAttribute('aria-hidden')==='true'){
        const active=document.activeElement;
        _cfDialogReturnFocus=active instanceof HTMLElement?active:null;
      }
      existing.style.display='flex';existing.setAttribute('aria-hidden','false');
      if(window.CF&&document.getElementById('cf-form')?.style.display!=='none')window.CF.state='editing';
      cfFocusCreateDialog();
      installHandlers();
    }else{
      restored=false;restorePromise=null;restoredDraftAvailable=false;defaultFieldsSnapshot=null;dirtyFields.clear();
      baseOpen();
      arrangeForm();
      installHandlers();
    }
    if(window.CF_DEFAULTS_READY)await window.CF_DEFAULTS_READY;
    await restoreDraft();
    const current=snapshot();
    const hasCurrentDraft=cfHistoryHasCurrentDraft(current);
    if(hasCurrentDraft&&!window.confirm('打开关联项目会替换当前未提交草稿。取消可保留当前内容；继续将载入目标项目的创作要求。确认继续？'))
      return false;

    // Stop a pending debounce and let any already-issued save finish before the
    // target project starts writing. Otherwise an old snapshot could land late.
    if(timer){clearTimeout(timer);timer=null;}
    try{await saving;}catch(_){}
    document.getElementById('cf-history-drawer')?.remove();
    // The user explicitly approved replacing the previous draft. Its dirty
    // markers must not leak into the target project's clean baseline.
    const transaction={
      snapshot:current,
      previousProjectId:String(current.context?.project_id||''),
      dirtyFields:Array.from(dirtyFields),
      hadDraft:!!(hasCurrentDraft||restoredDraftAvailable),
      promise:null,
      cancelled:false,
      cancelPromise:null
    };
    dirtyFields.clear();
    activeProjectPrefill=transaction;
    transaction.promise=cfPrefillFromProject(target);
    const result=await transaction.promise;
    if(transaction.cancelled)return {ok:false,stale:true,cancelled:true};
    if(activeProjectPrefill===transaction)activeProjectPrefill=null;
    if(result?.stale)return result;
    if(!result||result.ok!==true){
      // Project loading is transactional from the user's perspective: restore
      // the previously visible/saved draft if the target cannot be opened.
      cfResetProjectPrefillState(current.context?.project_id||'');
      applySnapshot(current);
      if(hasCurrentDraft||restoredDraftAvailable){
        const restored=await saveNow();
        if(!restored?.ok){
          const status=document.getElementById('cf-status');
          if(status)status.textContent='目标项目载入失败，原草稿已恢复到界面，但持久化同步未完成。'+(result?.error?' '+result.error:'');
          return result;
        }
      }
      const status=document.getElementById('cf-status');
      if(status)status.textContent='目标项目载入失败，已恢复切换前的草稿。'+(result?.error?' '+result.error:'');
    }
    return result;
  }
  window.openCreate=function(projectId){
    if(projectId)return openProjectWithDraftGuard(projectId);
    const existing=document.getElementById('cf-mask');
    if(existing){
      if(existing.style.display==='none'||existing.getAttribute('aria-hidden')==='true'){
        const active=document.activeElement;
        _cfDialogReturnFocus=active instanceof HTMLElement?active:null;
      }
      existing.style.display='flex';existing.setAttribute('aria-hidden','false');
      if(window.CF&&document.getElementById('cf-form')?.style.display!=='none')window.CF.state='editing';
      cfFocusCreateDialog();
      if(window._cfArticle&&typeof cfRestoreArticleSourceUI==='function')cfRestoreArticleSourceUI();
      installHandlers();
      _cfProjectPrefillRequest++;
      return restoreDraft();
    }
    restored=false;restorePromise=null;restoredDraftAvailable=false;defaultFieldsSnapshot=null;dirtyFields.clear();
    baseOpen();
    arrangeForm();
    installHandlers();
    _cfProjectPrefillRequest++;
    return restoreDraft();
  };
  window.closeCreate=async function(){
    const mask=document.getElementById('cf-mask');
    if(!mask){
      const result=baseClose();
      restored=false;restorePromise=null;dirtyFields.clear();
      return result;
    }
    const form=document.getElementById('cf-form');
    const isProgress=!!document.getElementById('cf-progress')&&form&&form.style.display==='none';
    if(!isProgress){
      const cancelled=await cancelProjectPrefillAndRestore();
      if(!cancelled?.ok){
        const status=document.getElementById('cf-status');
        if(status)status.textContent='尚未收起窗口：切换前草稿恢复失败。'+(cancelled?.error||'');
        return false;
      }
      if(draftRestorePending&&restorePromise)await restorePromise;
      const ctx=window.CF_DRAFT_CONTEXT||{};
      if(timer){clearTimeout(timer);timer=null;}
      const pending=[ctx.assetUploadPromise,ctx.narrationPromise].filter(Boolean);
      if(pending.length)await Promise.allSettled(pending);
      const saved=await saveNow();
      if(!saved||!saved.ok){
        const status=document.getElementById('cf-status');
        if(status)status.textContent='草稿未能保存到本机，请检查浏览器存储权限后再收起窗口。';
        return false;
      }
    }
    mask.style.display='none';mask.setAttribute('aria-hidden','true');
    if(window.CF)window.CF.state='idle';
    cfRestoreCreateDialogFocus();
    return true;
  };
  async function clearSavedDraft(){
    draftClearPending=true;
    if(timer){clearTimeout(timer);timer=null;}
    try{
      try{await saving;}catch(_){}
      const result=await apiPost('/api/director/drafts/active/clear',{});
      if(result&&result.ok){
        clearLocalDraft();
        restoredDraftAvailable=false;
        dirtyFields.clear();
      }
      return result;
    }finally{
      draftClearPending=false;
    }
  }
  window.cfClearSavedDraft=clearSavedDraft;
  window.cfFinalizeCreateDialog=async function(){
    if(timer){clearTimeout(timer);timer=null;}
    document.getElementById('cf-mask')?.remove();
    for(const id of ['sg-mask','tg-mask'])document.getElementById(id)?.remove();
    cfRestoreCreateDialogFocus();
    try{await clearSavedDraft();}catch(_){}
    if(window.CF)window.CF.state='idle';restored=false;restorePromise=null;restoredDraftAvailable=false;defaultFieldsSnapshot=null;dirtyFields.clear();
  };
  function cfHistoryRenderList(){
    const list=document.getElementById('cf-history-list');if(!list)return;
    const rows=Array.isArray(window._cfHistoryRows)?window._cfHistoryRows:[];
    const content=rows.length?rows.map(x=>
      '<div class="proj-row"><span style="flex:1;min-width:0"><b>'+esc(x.title||'未命名对话')+'</b><span class="hint">'+
      esc(x.project_id||'未关联项目')+' · '+new Date((x.updated_at||0)*1000).toLocaleString()+' · '+esc(x.status||'')+
      '</span></span><button class="btn-secondary btn-sm" type="button" data-conversation-id="'+esc(x.conversation_id||'')+'">查看</button></div>'
    ).join(''):'<div class="hint">暂无历史对话。成功提交创作后会自动记录；未提交内容请从上方「当前草稿」继续。</div>';
    const more=historyHasMore
      ?'<div style="display:flex;justify-content:center;padding:10px 0"><button class="btn-secondary btn-sm" type="button" data-history-action="load-more"'+
        (historyLoadingMore?' disabled':'')+'>'+(historyLoadingMore?'正在加载…':'加载更多历史')+'</button></div>'
      :'';
    list.innerHTML=content+more;
  }
  function cfHistoryRenderDraft(draft){
    const host=document.getElementById('cf-history-draft');if(!host)return;
    const data=draft?.data||{},fields=data.fields||{},context=data.context||{};
    const brief=String(fields['cf-director-brief']||'').trim();
    const script=String(fields['cf-script']||'').trim();
    const projectName=String(fields['cf-project-name']||'').trim();
    const projectType=String(fields['cf-project-type']||'').trim();
    const refs=Array.isArray(context.asset_refs)?context.asset_refs:[];
    const projectId=String(context.project_id||'').trim();
    const hasContext=!!(refs.length||context.reference||context.article||context.repo||context.narration_upload||projectId||projectName||projectType);
    const explicitFields=Array.isArray(data.explicit_fields)?data.explicit_fields:[];
    const settingLabels={
      platform:'平台',duration:'时长',style:'风格',resolution:'分辨率',voice:'音色',
      voice_mode:'配音方式',voice_rate:'语速',narration_volume:'旁白音量',
      bgm:'背景音乐',bgm_volume:'背景音乐音量',sfx:'音效',subtitle_mode:'字幕',
      fit_mode:'画面适配',clip_duration:'单段时长',clip_speed:'播放速度',
      concat_mode:'素材顺序',transition_mode:'转场',watermark:'水印',
      lower_third_name:'人物条姓名',lower_third_role:'人物条头衔'
    };
    const changedSettings=explicitFields.map(field=>settingLabels[field]||'').filter(Boolean);
    const hasSettings=changedSettings.length>0;
    if(!draft||(!brief&&!script&&!hasContext&&!hasSettings)){host.innerHTML='';return;}
    const detail=projectName?'项目：'+projectName+(projectType?' · '+projectType:'')
      :projectType?'项目类型：'+projectType
      :refs.length?'已保存 '+refs.length+' 个素材'
      :projectId?'关联项目：'+projectId
      :hasContext?'已保存补充资料'
      :hasSettings?'已调整：'+changedSettings.join('、'):'已保存补充资料';
    const preview=[projectName,projectType,brief||script||detail].filter(Boolean).join(' · ').slice(0,160);
    const updated=Number(draft.updated_at||0);
    const source=draft.local_only?'本机草稿':'已同步草稿';
    host.innerHTML='<div class="panel" style="padding:10px;margin:0 0 10px">'+
      '<div class="btn-row" style="align-items:center;margin:0"><b>当前未提交草稿</b><span class="hint">'+source+
      (updated?' · '+new Date(updated*1000).toLocaleString():'')+'</span><span class="tb-sep"></span>'+
      '<button class="btn-sm" type="button" data-history-action="resume-draft">继续编辑</button></div>'+
      '<div class="hint" style="margin-top:6px">'+esc(preview)+'</div></div>';
  }
  function cfHistoryInProgressMode(){
    const form=document.getElementById('cf-form');
    return !!(form&&form.style.display==='none'&&document.getElementById('cf-progress'));
  }
  function cfHistorySetNotice(message){
    const notice=document.getElementById('cf-history-notice');
    if(notice)notice.textContent=message||'';
  }
  window.cfHistoryRefresh=async function(){
    const requestVersion=++historyRequestVersion;
    historyLoadingMore=false;
    historyCursor=null;
    historyHasMore=false;
    const list=document.getElementById('cf-history-list');if(list)list.textContent='正在读取历史对话…';
    const progressMode=cfHistoryInProgressMode();
    cfHistorySetNotice(progressMode
      ?'创作任务仍在执行。刷新历史不会保存或修改进度窗口中的隐藏表单。'
      :'');
    try{
      const mask=document.getElementById('cf-mask');
      const form=document.getElementById('cf-form');
      if(mask&&mask.style.display!=='none'&&form&&!progressMode){
        try{await persistImmediately();}catch(_){}
      }
      const [r,draftResult]=await Promise.all([
        apiGet('/api/director/conversations?limit=50'),
        apiGet('/api/director/drafts/active')
      ]);
      if(requestVersion!==historyRequestVersion)return;
      window._cfHistoryRows=(r&&r.conversations)||[];
      historyHasMore=!!(r&&r.has_more);
      historyCursor=r&&r.next_cursor||null;
      const remote=draftResult&&draftResult.draft||null;
      const local=readLocalDraft();
      const useLocal=!!(local&&local.data&&(!remote||Number(local.saved_at||0)>Number(remote.updated_at||0)*1000));
      cfHistoryRenderDraft(useLocal
        ?{data:local.data,updated_at:Number(local.saved_at||0)/1000,local_only:true}
        :remote);
      cfHistoryRenderList();
    }catch(e){if(list)list.textContent='历史对话读取失败：'+(e.message||e);}
  };
  window.cfHistoryLoadMore=async function(){
    if(historyLoadingMore||!historyHasMore||!historyCursor)return false;
    const requestVersion=historyRequestVersion,cursor=historyCursor;
    historyLoadingMore=true;
    cfHistoryRenderList();
    const url='/api/director/conversations?limit=50'
      +'&before_updated_at='+encodeURIComponent(String(cursor.updated_at))
      +'&before_conversation_id='+encodeURIComponent(cursor.conversation_id);
    try{
      const response=await apiGet(url);
      if(requestVersion!==historyRequestVersion)return false;
      if(!response||!response.ok)throw new Error(response?.error||'历史分页请求失败');
      const existing=new Set((window._cfHistoryRows||[]).map(row=>row.conversation_id));
      for(const row of (response.conversations||[])){
        if(!existing.has(row.conversation_id)){
          window._cfHistoryRows.push(row);
          existing.add(row.conversation_id);
        }
      }
      historyHasMore=!!response.has_more;
      historyCursor=response.next_cursor||null;
      return true;
    }catch(error){
      if(requestVersion===historyRequestVersion)
        cfHistorySetNotice('加载更多历史失败：'+(error.message||error));
      return false;
    }finally{
      if(requestVersion===historyRequestVersion){
        historyLoadingMore=false;
        cfHistoryRenderList();
      }
    }
  };

  window.cfHistoryToggle=async function(){
    const existing=document.getElementById('cf-history-drawer');
    if(existing){historyRequestVersion++;existing.remove();return;}
    const drawer=document.createElement('aside');drawer.id='cf-history-drawer';
    drawer.setAttribute('role','complementary');drawer.setAttribute('aria-label','历史创作对话');
    drawer.style.cssText='position:fixed;top:58px;right:12px;bottom:12px;width:min(520px,calc(100vw - 24px));max-width:calc(100vw - 24px);z-index:13000;display:flex;flex-direction:column;box-sizing:border-box;padding:14px;background:var(--surface-1,#181818);color:var(--text,#eee);border:1px solid var(--border,#444);border-radius:10px;box-shadow:0 14px 44px rgba(0,0,0,.42)';
    drawer.innerHTML='<div class="btn-row" style="align-items:center;margin:0"><b>草稿与历史对话</b><span class="tb-sep"></span><button class="btn-secondary btn-sm" type="button" id="cf-history-refresh">刷新</button><button class="btn-secondary btn-sm" type="button" id="cf-history-close">关闭</button></div><div id="cf-history-draft" style="margin-top:10px"></div><div id="cf-history-notice" class="hint" role="status" aria-live="polite" style="margin-top:4px"></div><div id="cf-history-list" style="flex:1;min-height:0;overflow:auto;margin-top:4px" class="hint">正在读取…</div>';
    document.body.appendChild(drawer);
    drawer.querySelector('#cf-history-close').addEventListener('click',()=>{historyRequestVersion++;drawer.remove();});
    drawer.querySelector('#cf-history-refresh').addEventListener('click',()=>window.cfHistoryRefresh());
    // The unsent-draft card is a sibling of the conversation list, so it needs
    // its own delegated handler rather than relying on the list's click listener.
    drawer.querySelector('#cf-history-draft').addEventListener('click',event=>{
      const actionButton=event.target.closest('[data-history-action="resume-draft"]');
      if(actionButton)window.cfHistoryResumeDraft();
    });
    drawer.querySelector('#cf-history-list').addEventListener('click',event=>{
      // Detail actions also carry the conversation ID; handle explicit actions first.
      const actionButton=event.target.closest('[data-history-action]');
      if(actionButton){
        const action=actionButton.dataset.historyAction;
        if(action==='list'){historyRequestVersion++;cfHistoryRenderList();}
        else if(action==='restore')window.cfHistoryRestoreSnapshot(actionButton.dataset.conversationId);
        else if(action==='resume-draft')window.cfHistoryResumeDraft();
        else if(action==='load-more')window.cfHistoryLoadMore();
        return;
      }
      const conversationButton=event.target.closest('[data-conversation-id]');
      if(conversationButton)window.cfHistoryOpenConversation(conversationButton.dataset.conversationId);
    });
    await window.cfHistoryRefresh();
  };
  window.cfHistoryResumeDraft=function(){
    historyRequestVersion++;
    if(cfHistoryInProgressMode()){
      cfHistorySetNotice('当前创作任务仍在执行，暂时不能继续编辑隐藏的草稿。请等待任务结束，或先停止并返回修改。');
      return false;
    }
    const drawer=document.getElementById('cf-history-drawer');
    const mask=document.getElementById('cf-mask');
    drawer?.remove();
    if(mask&&mask.style.display!=='none')return Promise.resolve(true);
    return window.openCreate();
  };
  window.cfHistoryOpenConversation=async function(id){
    const requestVersion=++historyRequestVersion;
    historyLoadingMore=false;
    const list=document.getElementById('cf-history-list');if(!list)return;
    // Keep the list interactive while detail is loading. Users can select a
    // different conversation; requestVersion below makes the latest choice win.
    cfHistorySetNotice('正在读取对话详情…');
    try{
      const r=await apiGet('/api/director/conversations/'+encodeURIComponent(id));
      // A later selection or refresh owns the drawer now. Never let an older
      // slow response replace the conversation currently being inspected.
      if(requestVersion!==historyRequestVersion)return false;
      if(!r||!r.ok||!r.conversation)throw new Error(r?.error||'对话不存在');
      cfHistorySetNotice('');
      const c=r.conversation;window._cfHistorySelected=c;
      const messages=(c.messages||[]).map(m=>
        '<div class="panel" style="padding:8px;margin-top:6px"><b>'+
        esc(({user:'用户',assistant:'AI',tool:'执行记录',system:'系统'}[m.role]||m.role))+
        '</b><div style="white-space:pre-wrap;margin-top:4px">'+esc(m.content)+'</div></div>'
      ).join('');
      list.innerHTML='<div class="btn-row" style="align-items:center"><button class="btn-secondary btn-sm" type="button" data-history-action="list">返回列表</button><b style="flex:1;min-width:0">'+
        esc(c.title||'未命名对话')+'</b><span class="hint">'+esc(c.status||'')+'</span></div>'+
        '<div class="hint" style="margin-top:6px">'+esc(c.project_id||'未关联项目')+' · '+new Date((c.updated_at||0)*1000).toLocaleString()+'</div>'+
        messages+
        '<div class="btn-row" style="margin-top:10px"><button class="btn-sm" type="button" data-history-action="restore" data-conversation-id="'+esc(id)+'">恢复为新草稿</button>'+
        (c.project_id?'<button class="btn-secondary btn-sm" type="button" data-open-project="'+esc(c.project_id)+'">打开关联项目</button>':'')+'</div>';
      const openProject=list.querySelector('[data-open-project]');
      if(openProject)openProject.addEventListener('click',()=>{
        window.cfHistoryOpenAssociatedProject(openProject.dataset.openProject);
      });
    }catch(e){
      if(requestVersion!==historyRequestVersion)return false;
      cfHistorySetNotice('对话读取失败：'+(e.message||e));
      return false;
    }
  };
  function cfHistoryHasCurrentDraft(currentSnapshot){
    const fields=currentSnapshot?.fields||{},context=currentSnapshot?.context||{};
    const defaults=defaultFieldsSnapshot||{};
    const hasNonDefaultField=Object.entries(fields).some(([id,value])=>{
      if(Object.prototype.hasOwnProperty.call(defaults,id)){
        const baseline=defaults[id];
        if(typeof baseline==='boolean')return value!==baseline;
        return String(value??'')!==String(baseline??'');
      }
      if(typeof value==='boolean')return value;
      if(Array.isArray(value))return value.length>0;
      return String(value??'').trim()!=='';
    });
    const hasContext=!!(
      (context.asset_refs||[]).length||context.reference||context.article
      ||context.repo||context.narration_upload
    );
    const baseline=context.project_prefill_baseline;
    const sameJson=(left,right)=>JSON.stringify(left??null)===JSON.stringify(right??null);
    let hasProjectChanges=false;
    if(context.project_id){
      if(!baseline||!baseline.fields){
        // Legacy project drafts have no clean baseline; preserve them conservatively.
        hasProjectChanges=true;
      }else{
        const baselineFields=baseline.fields||{};
        const fieldIds=new Set([...Object.keys(fields),...Object.keys(baselineFields)]);
        hasProjectChanges=[...fieldIds].some(id=>!sameJson(fields[id],baselineFields[id]));
        hasProjectChanges=hasProjectChanges
          ||!sameJson(context.asset_refs||[],baseline.asset_refs||[])
          ||!sameJson(context.reference||null,baseline.reference||null)
          ||!sameJson(context.article||null,baseline.article||null)
          ||!sameJson(context.repo||null,baseline.repo||null)
          ||!sameJson(context.narration_upload||'',baseline.narration_upload||'');
      }
    }
    const hasNonProjectDraft=!!(
      (Array.isArray(currentSnapshot?.explicit_fields)&&currentSnapshot.explicit_fields.length)
      ||hasNonDefaultField||hasContext
    );
    return !!(
      dirtyFields.size>0
      ||(restoredDraftAvailable&&(context.project_id?hasProjectChanges:hasNonProjectDraft))
    );
  }

  window.cfHistoryOpenAssociatedProject=function(projectId){
    // A project switch supersedes any pending historical restore operation.
    historyRequestVersion++;
    return window.openCreate(projectId);
  };

  window.cfHistoryRestoreSnapshot=async function(id){
    if(cfHistoryInProgressMode()){
      cfHistorySetNotice('当前创作任务仍在执行，不能把历史对话恢复到隐藏的创作表单。请等待任务结束，或先停止并返回编辑。');
      return false;
    }
    const c=window._cfHistorySelected;if(!c||c.conversation_id!==id)return false;
    const requestVersion=++historyRequestVersion;
    const drawer=document.getElementById('cf-history-drawer');
    const restoreStillCurrent=()=>requestVersion===historyRequestVersion
      &&(!drawer||document.getElementById('cf-history-drawer')===drawer)
      &&window._cfHistorySelected?.conversation_id===id;
    if(activeProjectPrefill){
      const cancelled=await cancelProjectPrefillAndRestore();
      if(!cancelled?.ok||!restoreStillCurrent())return false;
    }
    const mask=document.getElementById('cf-mask');
    if(!document.getElementById('cf-form')||(mask&&mask.style.display==='none')){
      await window.openCreate();
      if(!restoreStillCurrent())return false;
    }
    if(window.CF_DEFAULTS_READY){
      await window.CF_DEFAULTS_READY;
      if(!restoreStillCurrent())return false;
    }
    await restoreDraft();
    if(!restoreStillCurrent())return false;
    const currentSnapshot=snapshot();
    const hasCurrentDraft=cfHistoryHasCurrentDraft(currentSnapshot);
    if(hasCurrentDraft&&!window.confirm('当前草稿包含未提交内容。恢复历史对话会替换当前草稿；取消可保留当前内容。确认继续？')){
      const status=document.getElementById('cf-draft-status');
      if(status)status.textContent='已取消历史恢复，当前草稿保持不变。';
      return;
    }
    // The historical snapshot replaces the active form only after confirmation.
    // Old dirty markers describe the discarded draft and must not leak forward.
    dirtyFields.clear();
    // History snapshots can be older/partial. Start from a clean form so
    // fields absent from that snapshot cannot leak in from the active draft.
    cfResetProjectPrefillState('');
    const historySnapshot=c.snapshot||{},historyFormSnapshot=historySnapshot.form||historySnapshot;
    if(historyFormSnapshot.fields)applySnapshot(historyFormSnapshot);
    else if(historySnapshot.director_inputs){
      const d=historySnapshot.director_inputs,legacyOptions=historySnapshot.request_options||{};
      const brief=document.getElementById('cf-director-brief'),script=document.getElementById('cf-script');
      if(brief)brief.value=d.brief||'';
      if(script)script.value=(d.script&&d.script.text)||'';
      window.CF_DRAFT_CONTEXT.assetRefs=(d.assets&&d.assets.asset_refs)||[];
      window._cfReference=(d.references||[])[0]||null;
      window._cfArticle=(d.articles||[])[0]||null;
      window._cfRepo=d.software_project||null;
      window.CF_DRAFT_CONTEXT.reference=window._cfReference;
      window.CF_DRAFT_CONTEXT.article=window._cfArticle;
      window.CF_DRAFT_CONTEXT.repo=window._cfRepo;
      window.CF_DRAFT_CONTEXT.narrationUpload=legacyOptions.narration_upload||d.narration_upload||'';
      window._cfNarrationUpload=window.CF_DRAFT_CONTEXT.narrationUpload;
      const legacyFields={
        'cf-platform':d.platform,
        'cf-duration':d.duration,
        'cf-style':d.style,
        'cf-visual-source':d.constraints?.visual_source||(d.assets?.policy==='user_only'?'local':undefined),
        'cf-resolution':legacyOptions.resolution||(legacyOptions.width&&legacyOptions.height?(legacyOptions.width+'x'+legacyOptions.height):undefined),
        'cf-voice-mode':legacyOptions.voice_mode,
        'cf-voice':legacyOptions.voice,
        'cf-voice-rate':legacyOptions.voice_rate,
        'cf-voice-volume':legacyOptions.narration_volume,
        'cf-bgm':legacyOptions.bgm,
        'cf-bgm-volume':legacyOptions.bgm_volume,
        'cf-sfx':legacyOptions.sfx,
        'cf-subtitle':legacyOptions.subtitle_mode,
        'cf-fit-mode':legacyOptions.fit_mode,
        'cf-clip-duration':legacyOptions.clip_duration,
        'cf-clip-speed':legacyOptions.clip_speed,
        'cf-concat-mode':legacyOptions.concat_mode,
        'cf-transition-mode':legacyOptions.transition_mode,
        'cf-stop-at':legacyOptions.stop_at,
        'cf-fps':legacyOptions.fps,
        'cf-confirm':legacyOptions.confirm_mode,
        'cf-watermark':legacyOptions.watermark,
        'cf-lower-name':legacyOptions.lower_third_name,
        'cf-lower-role':legacyOptions.lower_third_role
      };
      const legacyExplicit=new Set();
      const explicitById={...CF_EXPLICIT_CONTROL_FIELDS};
      for(const [id,value] of Object.entries(legacyFields)){
        if(value===undefined||value===null||value==='')continue;
        const control=document.getElementById(id);
        if(control){
          if(control.type==='checkbox')control.checked=!!value;
          else control.value=String(value);
        }
        if(explicitById[id])legacyExplicit.add(explicitById[id]);
      }
      if(window.CF)window.CF.explicitFields=legacyExplicit;
      if(legacyOptions.match_materials_to_script!==undefined){
        const match=document.getElementById('cf-match-materials');
        if(match)match.checked=!!legacyOptions.match_materials_to_script;
      }
      const summary=document.getElementById('cf-assets-summary');
      if(summary)summary.textContent=window.CF_DRAFT_CONTEXT.assetRefs.length
        ?'已暂存 '+window.CF_DRAFT_CONTEXT.assetRefs.length+' 个素材，可直接用于创作':'未添加本地素材';
      if(window._cfArticle&&typeof cfRenderArticleSource==='function')
        cfRenderArticleSource(window._cfArticle,'已恢复上一轮导入的文章来源。请核对后再创作。');
      if(window._cfReference){
        const node=document.getElementById('cf-reference-result');
        if(node)node.innerHTML='<div class="ok-box">已恢复参考视频分析，可直接用于创作。</div>';
      }
      if(window._cfRepo){
        const node=document.getElementById('cf-repo-result');
        if(node)node.innerHTML='<div class="ok-box">已恢复软件项目证据与已验证卖点。</div>';
      }
      if(!legacyOptions.resolution&&d.platform&&typeof createPlatformSync==='function')createPlatformSync();
    }
    // Both snapshot generations must refresh dependent controls after restoration.
    if(typeof cfVoiceModeSync==='function')cfVoiceModeSync();
    if(typeof cfVisualSourceSync==='function')cfVisualSourceSync();
    if(typeof cfSeriesSync==='function')cfSeriesSync();
    const restoredContext=window.CF_DRAFT_CONTEXT||{};
    const restoredScript=(document.getElementById('cf-script')||{}).value||'';
    const savedDisclosures=historyFormSnapshot.ui?.disclosures||{};
    const hasSavedSupplementState=Object.prototype.hasOwnProperty.call(savedDisclosures,'cf-input-context-details');
    const supplement=document.getElementById('cf-input-context-details');
    if(supplement&&!hasSavedSupplementState&&(restoredScript.trim()||restoredContext.assetRefs?.length||restoredContext.reference||restoredContext.article||restoredContext.repo))
      supplement.open=true;

    // Historical records predating disclosure persistence should reveal restored
    // advanced settings. A modern snapshot's explicit collapsed state is authoritative.
    const hasSavedAdvancedState=Object.prototype.hasOwnProperty.call(savedDisclosures,'cf-manual-options');
    const legacyOptions=historySnapshot.request_options||{};
    const hasLegacyAdvancedSettings=Object.values(legacyOptions).some(value=>
      value!==undefined&&value!==null&&value!==''
    );
    const hasExplicitAdvancedSettings=(window.CF?.explicitFields?.size||0)>0;
    const advanced=document.getElementById('cf-manual-options');
    if(advanced&&!hasSavedAdvancedState&&(hasLegacyAdvancedSettings||hasExplicitAdvancedSettings))
      advanced.open=true;
    await saveNow();
    if(!restoreStillCurrent())return false;
    const status=document.getElementById('cf-draft-status');if(status)status.textContent='已恢复历史对话为新草稿；原记录不会被覆盖。';
    const list=document.getElementById('cf-history-list');if(list)list.innerHTML='<div class="ok-box">已恢复为新草稿，原历史对话不会被覆盖。</div>';
  };
  window.addEventListener('beforeunload',()=>{
    const mask=document.getElementById('cf-mask');
    // A hidden progress form or an in-flight draft clear must never recreate
    // the active draft through this direct localStorage/sendBeacon path.
    if(mask && mask.style.display!=='none'&&!draftPersistenceBlocked()){
      const data=snapshot();writeLocalDraft(data);
      try{navigator.sendBeacon('/api/director/drafts/active',new Blob([JSON.stringify({data})],{type:'application/json'}));}catch(_){}
    }
  });
  const originalCancelAndEdit=window.cfCancelAndEdit;
  window.cfCancelAndEdit=async function(){
    const result=await originalCancelAndEdit();
    // The original async stop flow restores the form only after the server
    // confirms a terminal state (or exposes the editable stop-pending form).
    // Save after that transition, not while the progress form is still hidden.
    const form=document.getElementById('cf-form');
    if(form&&form.style.display!=='none'&&!draftPersistenceBlocked())
      await saveNow();
    return result;
  };
  const originalGenerate=window.cfGenerateScript;
  window.cfGenerateScript=async function(){
    const context=window.CF_DRAFT_CONTEXT;
    const brief=document.getElementById('cf-director-brief');
    const status=document.getElementById('cf-script-gen-status');
    if(!brief||!brief.value.trim()){if(status)status.textContent='请先填写上方创作要求';return;}
    const script=document.getElementById('cf-script');
    const previous=script?.value||'';
    const result=await originalGenerate();
    if(window.CF_DRAFT_CONTEXT!==context)return result;
    const generated=script?.value||'';
    if(generated.trim()&&generated!==previous){
      revealSupplementaryInput('cf-script');
      script.dispatchEvent(new Event('input',{bubbles:true}));
      await persistImmediately();
    }
    return result;
  };
  const originalEnhance=window.createEnhance;
  window.createEnhance=async function(){
    const context=window.CF_DRAFT_CONTEXT;
    const script=document.getElementById('cf-script');
    if(!script?.value.trim()){revealSupplementaryInput('cf-script');return originalEnhance();}
    const result=await originalEnhance();
    if(window.CF_DRAFT_CONTEXT!==context)return result;
    const box=document.getElementById('cf-enhance-box');
    if(box&&box.style.display!=='none'){
      revealSupplementaryInput('cf-enhanced');
      await persistImmediately();
    }
    return result;
  };
  const originalAcceptEnhanced=window.createAcceptEnhanced;
  window.createAcceptEnhanced=function(){
    const result=originalAcceptEnhanced();
    const script=document.getElementById('cf-script');
    if(script)script.dispatchEvent(new Event('input',{bubbles:true}));
    persistImmediately();
    return result;
  };
  const originalGraphicPlan=window.cfMakeGraphicPlan;
  window.cfMakeGraphicPlan=function(){
    const script=document.getElementById('cf-script');
    if(!script?.value.trim()){revealSupplementaryInput('cf-script');return originalGraphicPlan();}
    return originalGraphicPlan();
  };
  const originalRepo=window.cfScanRepo;window.cfScanRepo=async function(){const context=window.CF_DRAFT_CONTEXT;const r=await originalRepo();if(window.CF_DRAFT_CONTEXT!==context)return r;await saveNow();return r;};
  const originalReference=window.cfAnalyzeReference;window.cfAnalyzeReference=async function(){const context=window.CF_DRAFT_CONTEXT;const r=await originalReference();if(window.CF_DRAFT_CONTEXT!==context)return r;if(r){window.CF_DRAFT_CONTEXT.reference=window._cfReference;await saveNow();}return r;};
  const originalArticle=window.cfImportArticle;window.cfImportArticle=async function(){const context=window.CF_DRAFT_CONTEXT;await originalArticle();if(window.CF_DRAFT_CONTEXT!==context)return;window.CF_DRAFT_CONTEXT.article=window._cfArticle;await saveNow();};
  const originalClear=window.cfClearArticleSource;window.cfClearArticleSource=function(){originalClear();window.CF_DRAFT_CONTEXT.article=null;scheduleSave();};
})();
