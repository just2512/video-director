async function loadAssetsLibrary(){
  if (!document.getElementById('assets-list')) return;  // 平铺页已收进 Library 左栏
  try {
    const assets = await (await fetch('/api/assets_library')).json();
    if (!Array.isArray(assets)) throw new Error(assets.error || '读取失败');
    const filter = (document.getElementById('asset-filter') || {}).value || 'all';
    const view = (document.getElementById('asset-view') || {}).value || 'grid';
    let list = assets;
    if (filter.startsWith('type:')) list = list.filter(a => a.type === filter.slice(5));
    if (filter.startsWith('src:')) list = list.filter(a => (a.source || 'user') === filter.slice(4));
    const grid = document.getElementById('assets-list');
    grid.classList.toggle('list-mode', view === 'list');
    const lic = (a) => licenseBadge(a);
    const rows = list.slice(0, 120).map(a => `
      <div class="asset-card${view==='list' ? ' proj-row' : ''}" onclick="inspectAsset('${esc(a.asset_id)}')">
        <div style="font-size:12px"><b>${esc(a.asset_id)}</b></div>
        <div class="hint" style="font-size:11px">${esc(a.type)}${a.duration ? ' · '+a.duration.toFixed(1)+'s' : ''}</div>
        <div style="font-size:11px">${lic(a)}</div>
        <div class="hint" style="font-size:11px">用于 ${a.used_in.length} 项目</div>
      </div>`).join('');
    grid.innerHTML = rows || '<div class="hint">无匹配素材</div>';

    // §44 冻结资产台账（asset_resolver manifest：source/license/复用）
    try {
      const frozen = await (await fetch('/api/media_library')).json();
      const frows = (frozen || []).map(f => `
        <div class="scene-card">
          <div class="scene-head">
            <span class="scene-id">${esc(f.id)}</span>
            <span class="scene-dur">${esc(f.type)}</span>
            <span class="scene-dur">复用 ${f.reuse_count || 1} 次</span>
            <span class="scene-dur" style="margin-left:auto">${f.commercial_status === 'CLEAR' ? '<span class="lic-badge lic-clear">✓ CLEAR</span>' : '<span class="lic-badge lic-unknown">⚠ ' + esc(f.commercial_status) + '</span>'}</span>
          </div>
          <div class="scene-asset">source：${esc(f.source)}${f.hash ? ' · hash ' + esc(f.hash.slice(0,8)) : ''}</div>
        </div>`).join('');
      document.getElementById('media-frozen-list').innerHTML =
        frows ? `<b>冻结资产</b>${frows}` : '';
    } catch (e) { /* 冻结台账为空不阻塞 */ }
    // §43 Graphic Library 计数（Blocks/Motions/Templates）
    try {
      const [blocks, motions, tpls] = await Promise.all([
        fetch('/api/catalog/blocks').then(r=>r.json()),
        fetch('/api/registry/motions').then(r=>r.json()),
        fetch('/api/registry/templates').then(r=>r.json()),
      ]);
      document.getElementById('graphic-lib-counts').innerHTML =
        `<span class="hint">Blocks ×${(blocks||[]).length}</span>`+
        `<span class="hint">Motions ×${(motions||[]).length}</span>`+
        `<span class="hint">Templates ×${(tpls||[]).length}</span>`;
    } catch (e) { /* 计数失败不阻塞 */ }
  } catch (e) {
    document.getElementById('assets-list').innerHTML =
      `<div class="err-box">加载失败：${esc(e.message)}</div>`;
  }
}

async function inspectAsset(assetId){
  // §31 Asset Library 右侧 Inspector（Preview/Metadata/Source/Rights/Used In）
  const box = document.getElementById('asset-inspector');
  if (!box) return;
  box.innerHTML = '加载中……';
  try {
    const d = await (await fetch('/api/asset_detail?asset_id=' + encodeURIComponent(assetId))).json();
    if (d && d.error) throw new Error(d.error);
    const statusCn = {CLEAR:'✓ 商用可用', REVIEW:'⚠ 需复核', BLOCKED:'× 已阻断', UNKNOWN:'⚠ 状态未知'};
    box.innerHTML = `<b>${esc(assetId)}</b>
      ${d.type ? `<div class="hint">类型：${esc(d.type)}</div>` : ''}
      ${d.duration ? `<div class="hint">时长：${d.duration.toFixed(1)}s</div>` : ''}
      ${d.width ? `<div class="hint">尺寸：${d.width}×${d.height}</div>` : ''}
      <div class="hint">来源：${esc(d.source || '—')}</div>
      <div class="hint">授权：<b>${statusCn[d.commercial_status] || esc(d.commercial_status || '—')}</b></div>
      ${d.license_note ? `<div class="hint">许可：${esc(d.license_note)}</div>` : ''}
      <div class="hint">用于 ${(d.used_in || []).length} 个项目</div>
      ${(d.used_in || []).length ? `<div class="hint" style="font-size:11px">${esc(d.used_in.slice(0,5).join('、'))}${d.used_in.length>5?'…':''}</div>` : ''}`;
    // 高亮选中卡片
    document.querySelectorAll('#assets-list .asset-card').forEach(c => c.classList.remove('sel'));
    document.querySelectorAll('#assets-list .asset-card').forEach(c => {
      if (c.textContent.includes(assetId)) c.classList.add('sel');
    });
  } catch (e) {
    box.innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
  }
}
// 许可证四态徽标（§二十一 License UI）：用户上传 CLEAR / 内置自有 CLEAR /
// 无来源 UNKNOWN（⚠ 不能用于商业发布）
function licenseBadge(a){
  const id = a.asset_id || '';
  // source 字段前端已有；无 source 的资产按来源推断
  const src = a.source || (id.includes('upload') ? 'user' : (id ? 'user' : 'unknown'));
  if (src === 'user') return `<span class="lic-badge lic-clear" title="用户上传，授权由你负责">✓ 商用可用</span>`;
  if (src === 'own') return `<span class="lic-badge lic-clear" title="系统自有素材">✓ 商用可用</span>`;
  if (src === 'generated') return `<span class="lic-badge lic-clear" title="AI 生成">✓ 商用可用</span>`;
  if (src === 'blocked' || src === 'third-party') return `<span class="lic-badge lic-blocked" title="第三方素材，禁止商业发布">× 禁止</span>`;
  return `<span class="lic-badge lic-unknown" title="来源不明，商业发布前需核查">⚠ 需核查</span>`;
}

async function analyzeReference(){
  const f = document.getElementById('reference_video');
  const out = document.getElementById('reference-result');
  if (!f || !out) return;
  const file = f.files[0];
  if (!file) { out.innerHTML = '<div class="err-box">请先选择参考视频</div>'; return; }
  out.innerHTML = '<div class="card">分析中……（镜头检测 + 抽帧 + 语法提取，约 10-40 秒）</div>';
  const fd = new FormData();
  fd.append('reference', file, file.name);
  try {
    const r = await fetch('/api/reference', {method:'POST', body: fd});
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || '分析失败');
    const rep = j.report || {};
    const vl = rep.visual_language || {};
    const sg = (rep.shot_grammar || []).slice(0, 30);
    const rows = sg.map(s=>`<tr><td>${esc(String(s.role||''))}</td><td>${(s.duration||0).toFixed(1)}s</td><td>${esc(String(s.motion||''))}</td></tr>`).join('');
    out.innerHTML = `
      <div class="ok-box">
        <b>分析完成</b> · ${sg.length} 个镜头 · 节奏 ${esc(String(vl.pace||'—'))} · 镜头密度 ${esc(String(vl.density||'—'))} · 运动语言 ${esc(String(vl.motion||'—'))} · 转场 ${esc(String(vl.transition||'—'))}
        ${rep.report_path ? `<div class="hint">报告已存：${esc(rep.report_path)}</div>` : ''}
        <table class="cap-table"><tr><td>角色</td><td>时长</td><td>运动</td></tr>${rows}</table>
      </div>`;
  } catch (e) {
    out.innerHTML = `<div class="err-box">分析失败：${esc(e.message)}</div>`;
  }
}

async function loadMemoryView(){
  const out = document.getElementById('memory-view');
  out.innerHTML = '加载中……';
  try {
    const j = await (await fetch('/api/style_dna')).json();
    if (j.error) throw new Error(j.error);
    const dna = j.style_dna;
    const prefs = j.recent_preferences || [];
    let dnaHtml = '<div class="hint">暂无风格记忆——完成成片并在结果页点 👍/👎 后开始积累。</div>';
    if (dna) {
      const doms = dna.domains || {};
      const domRows = Object.entries(doms).map(([k,v]) => {
        const pref = Object.entries(v.prefer||{}).map(([n,c])=>`<span class="cap-ok">✓${esc(n)}×${c}</span>`).join(' ');
        const av = Object.entries(v.avoid||{}).map(([n,c])=>`<span class="cap-off">✗${esc(n)}×${c}</span>`).join(' ');
        return `<tr><td>${esc(k)}</td><td>${pref||'—'}</td><td>${av||'—'}</td></tr>`;
      }).join('');
      dnaHtml = `<div><b>${esc(dna.name||'个人风格')}</b>（源自 ${j.case_count||0} 次创作 · 👍 ${j.liked_count||0}）</div>
        <table class="cap-table"><tr><td>偏好域</td><td>喜欢</td><td>避免</td></tr>${domRows}</table>`;
    }
    const prefRows = prefs.map(p=>`<tr><td>${p.signal==='prefer'?'👍':(p.signal==='avoid'?'👎':'·')}</td><td>${esc(String(p.field||''))}</td><td>${esc(String(p.normalized||p.value_to||''))}</td></tr>`).join('');
    out.innerHTML = dnaHtml + (prefRows ? `<div style="margin-top:12px"><b>最近偏好信号</b><table class="cap-table"><tr><td>信号</td><td>维度</td><td>值</td></tr>${prefRows}</table></div>` : '');
  } catch (e) {
    out.innerHTML = `<div class="err-box">加载失败：${esc(e.message)}</div>`;
  }
}

async function sendFeedback(caseId, liked){
  const noteEl = document.getElementById('fb-note');
  const note = noteEl ? noteEl.value : '';
  try {
    const r = await fetch('/api/feedback', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({case_id: caseId, liked: liked, note: note})
    });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || '反馈失败');
    const box = document.getElementById('fb-row');
    box.innerHTML = liked
      ? '<span class="cap-ok">已记录 👍 —— 你的偏好正在沉淀进个人风格记忆</span>'
      : '<span class="cap-off">已记录 👎 —— 系统将避免这类风格</span>';
  } catch (e) {
    editorToast('反馈失败：' + e.message, true);
  }
}

async function uploadAudio(kind, input){
  const status = document.getElementById(kind + '-upload-status');
  const f = input.files && input.files[0];
  if (!f) return;
  status.textContent = '上传中……';
  try {
    const fd = new FormData();
    fd.append('kind', kind);
    fd.append('audio_file', f, f.name);
    const r = await fetch('/api/audio_upload', { method:'POST', body: fd });
    const j = await r.json();
    if (!j.ok && j.error) throw new Error(j.error);
    status.textContent = `✅ 已上传「${j.filename}」（${(j.duration||0).toFixed(1)} 秒），已加入${kind==='bgm'?'背景音乐':'音效'}列表`;
    await loadOptions();  // 重扫库刷新下拉
    // 选中刚上传的
    const sel = document.getElementById(kind);
    if (sel) {
      for (let i=0;i<sel.options.length;i++) {
        if (sel.options[i].value === j.asset_id) { sel.selectedIndex = i; break; }
      }
    }
  } catch (e) {
    status.textContent = '上传失败：' + e.message;
  } finally {
    input.value = '';
  }
}

// ---- 试听（一次只播一个，共享 audio 元素）----
let _previewEl = null;
function _ensurePreviewAudio(){
  if (!_previewEl) {
    window._previewEl = document.createElement('audio');
    _previewEl.id = 'preview-audio';
    _previewEl.controls = true;
    document.getElementById('pane-home').appendChild(_previewEl);
  }
  return _previewEl;
}
function _play(url, label){
  const a = _ensurePreviewAudio();
  a.pause();
  a.src = url;
  a.play().catch(()=>{});
  a.setAttribute('data-label', label || '');
}
function previewVoice(){
  const v = document.getElementById('voice').value;
  if (!v) return;
  _play('/api/voice_preview?voice=' + encodeURIComponent(v), '配音 ' + v);
}
// §14/§16/§17 NarrationConfig 三态：auto/upload/silent（前端切换互斥显隐）
function narrationModeChange(){
  const mode = document.getElementById('narration_mode').value;
  document.getElementById('voice-auto-row').style.display = mode === 'auto' ? '' : 'none';
  document.getElementById('voice-upload-row').style.display = mode === 'upload' ? '' : 'none';
  document.getElementById('voice-silent-note').style.display = mode === 'silent' ? '' : 'none';
}
// §16 上传配音：Import → Probe → ASR → Word Timing → Narration（TTS=SKIP）
async function uploadVoice(){
  const fi = document.getElementById('voice_upload_file');
  const st = document.getElementById('voice-upload-status');
  if (!fi.files || !fi.files.length) { st.textContent = '请先选择音频文件'; return; }
  const pid = (window.currentProject && window.currentProject.project_id) ||
              (document.getElementById('editor-project')||{}).value || '';
  if (!pid) { st.textContent = '需要项目：先在创作页生成或编辑器打开一个项目'; return; }
  st.textContent = '导入中（探测→对齐）……';
  const fd = new FormData();
  fd.append('project_id', pid);
  fd.append('voice_file', fi.files[0]);
  try {
    const resp = await fetch('/api/voice_upload', {method:'POST', body: fd});
    const r = await resp.json();
    if (r.ok) {
      st.textContent = `✅ 已导入 ${r.duration}s（来源 ${r.source}${r.source==='estimated'?'（估算时间轴）':''}，TTS 已跳过 §12）`;
    } else {
      st.textContent = '❌ ' + (r.error || '导入失败');
    }
  } catch(e) { st.textContent = '❌ ' + e; }
}
function previewAudio(kind){
  const sel = document.getElementById(kind);
  const v = sel.value;
  if (!v) { editorToast('当前选项无音频可播（关闭/不配乐状态）'); return; }
  if (v === 'auto') { editorToast('AI 自动匹配在成片时按风格选曲，试听请选择具体曲目'); return; }
  _play('/api/audio_preview?asset_id=' + encodeURIComponent(v), v);
}
function previewSfx(){
  const sel = document.getElementById('sfx');
  const v = sel.value;
  if (!v || v === 'auto') {
    // 自动模式试听随机一条 SFX 供参考
    const opts = sel.options;
    const ids = [];
    for (let i = 0; i < opts.length; i++) { const ov = opts[i].value; if (ov && ov !== 'auto') ids.push(ov); }
    if (!ids.length) { editorToast('音效库为空', true); return; }
    _play('/api/audio_preview?asset_id=' + encodeURIComponent(ids[0]), ids[0]);
    return;
  }
  previewAudio('sfx');
}

// §8 拆分导出：inline onclick/window 引用兼容（零行为改动）
function referenceTogglePanel(){
  const p = document.getElementById('reference-panel');
  if (!p) return;
  p.style.display = p.style.display === 'none' ? '' : 'none';
}

Object.assign(window, {
  referenceTogglePanel,
  loadAssetsLibrary,
  inspectAsset,
  licenseBadge,
  analyzeReference,
  loadMemoryView,
  sendFeedback,
  uploadAudio,
  _ensurePreviewAudio,
  _play,
  previewVoice,
  narrationModeChange,
  uploadVoice,
  previewAudio,
  previewSfx,
  _previewEl,
});
