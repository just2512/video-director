// §49 workflow.js：旧 Revision 链已收口（无第二编辑态）。
// 本文件只保留 compose 表单 API 对接与旧入口兼容路由；业务中枢已分散到
// project.js / create.js / review.js / workspace.js（§49 职责表）。

// 平台 → 画幅联动（新建面板内 createPlatformSync 已有；此函数保留给旧调用）
function platformAspectSync(){
  const p = (document.getElementById('target_platform') || {}).value;
  const map = { douyin: '1080x1920', bilibili: '1920x1080', xhs: '1080x1440', square: '1080x1080' };
  const aspectEl = document.getElementById('aspect');
  if (p && map[p] && aspectEl) aspectEl.value = map[p];
}

// compose 主链保留（旧首页表单调用；新建面板 create.js 走同一 API）
async function compose(){
  const btn = document.getElementById('go');
  if (!btn) { openCreate(); return; }  // 新 UI：无旧表单时打开统一创建面板
  const out = document.getElementById('result');
  const fd = new FormData();
  let brief = document.getElementById('creative_brief') ? document.getElementById('creative_brief').value : '';
  const briefParts = [];
  const tp = document.getElementById('target_platform');
  if (tp && tp.value) briefParts.push('目标平台：' + tp.options[tp.selectedIndex].text);
  const td = document.getElementById('target_duration');
  if (td && td.value) briefParts.push(`目标时长：约 ${td.value} 秒`);
  const au = document.getElementById('audience');
  if (au && au.value.trim()) briefParts.push('受众：' + au.value.trim());
  if (briefParts.length) brief = (brief ? brief + '\n' : '') + '【创作简报】' + briefParts.join('；');
  fd.append('script', document.getElementById('script').value);
  fd.append('creative_brief', brief);
  fd.append('mode', document.getElementById('mode').value);
  fd.append('voice', document.getElementById('voice').value);
  fd.append('voice_rate', document.getElementById('voice_rate').value);
  fd.append('style_intent', document.getElementById('style_intent').value);
  fd.append('aspect', document.getElementById('aspect').value);
  fd.append('bgm', document.getElementById('bgm').value);
  fd.append('sfx', document.getElementById('sfx').value);
  fd.append('subtitle_mode', document.getElementById('subtitle_mode').value);
  fd.append('watermark', document.getElementById('watermark').value);
  fd.append('title', document.getElementById('title') ? document.getElementById('title').value : '');
  fd.append('lower_third_name', document.getElementById('lower_third_name') ? document.getElementById('lower_third_name').value : '');
  fd.append('lower_third_role', document.getElementById('lower_third_role') ? document.getElementById('lower_third_role').value : '');
  fd.append('confirm_mode', document.getElementById('confirm_mode').value);
  const vidEl = document.getElementById('videos');
  if (vidEl) for (const f of vidEl.files) fd.append('videos', f, f.name);
  const imgEl = document.getElementById('images');
  if (imgEl) for (const f of imgEl.files) fd.append('images', f, f.name);

  btn.disabled = true;
  out.innerHTML = '<div class="panel">正在生成……（配音+匹配+渲染，约 30-120 秒）</div>';
  const ctrl = new AbortController();
  const tm = setTimeout(() => ctrl.abort(), 300000);
  try {
    const r = await apiUpload('/api/compose', fd, {signal: ctrl.signal});
    if (!r.ok && r.error) throw new Error(r.error);
    renderResult(r);
  } catch (e) {
    out.innerHTML = `<div class="err-box">生成失败：${esc(e.message)}（内容未保存，可重试）</div>`;
  } finally {
    clearTimeout(tm);
    btn.disabled = false;
  }
}

function renderResult(j){
  const out = document.getElementById('result');
  if (!out) return;
  if (j.pending_confirm) {
    out.innerHTML = `
      <div class="ok-box" data-pid="${esc(j.project_id || '')}">
        <b>方案已生成，正在打开工作台……</b>
        <div class="hint">分镜审阅、改稿、渲染都在工作台完成。</div>
      </div>`;
    workspaceOpen(j.project_id);  // §56 确定性等待
    return;
  }
  const QC_CN = {PASS:'通过', FAIL:'未通过', SKIP:'跳过'};
  const qcRows = (j.qc?.checks||[]).map(c =>
    `<div class="qc-line ${c.result==='PASS'?'qc-pass':'qc-fail'}">${QC_CN[c.result]||esc(c.result)} · ${esc(c.check)} — ${esc(c.detail)}</div>`).join('');
  const videoUrl = '/file?path=' + encodeURIComponent(j.final_path);
  const fbRow = j.case_id ? `
      <div class="feedback-row" id="fb-row">
        <button class="btn fb-btn fb-btn-liked" onclick="sendFeedback('${esc(j.case_id)}', true)">喜欢</button>
        <button class="btn fb-btn fb-btn-disliked" onclick="sendFeedback('${esc(j.case_id)}', false)">不喜欢</button>
        <span class="fb-note"><input type="text" id="fb-note" placeholder="一句话说明（可选）"></span>
      </div>` : '';
  out.innerHTML = `
    <div class="ok-box">
      <b>生成成功</b> · 耗时 ${j.elapsed_s}s · 旁白 ${j.narration_duration.toFixed(1)}s · 质检：${j.qc?.verdict === 'PASS' ? '通过' : (j.qc?.verdict === 'SKIP' ? '（本模式不质检）' : esc(String(j.qc?.verdict || '—')))}
      <video controls src="${videoUrl}"></video><br>
      <a class="dl" href="${videoUrl}" download="final.mp4">下载 MP4</a>
      <div style="margin-top:12px">${qcRows}</div>
      ${j.warnings?.length ? `<div class="hint">提示：${esc(j.warnings.join('；'))}</div>` : ''}
      ${fbRow}
    </div>`;
}

Object.assign(window, {
  platformAspectSync, compose, renderResult,
});
