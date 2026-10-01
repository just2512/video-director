// storyboard.js（§49 瘦身）：旧首页平铺页（素材库三栏/参考分析/创作记忆表单）
// 已收进四区（素材→Library 内容域、参考→新建面板起点、记忆→设置偏好）。
// 本文件保留：反馈沉淀（成片后 👍/👎 → 风格记忆）与上传配音（compose 链配套）。

async function sendFeedback(caseId, liked){
  const noteEl = document.getElementById('fb-note');
  const note = noteEl ? noteEl.value : '';
  try {
    const r = await apiPost('/api/feedback', {case_id: caseId, liked: liked, note: note});
    if (!r.ok && r.error) throw new Error(r.error);
    const box = document.getElementById('fb-row');
    if (box) box.innerHTML = liked
      ? '<span class="cap-ok">已记录——你的偏好正在沉淀进个人风格记忆</span>'
      : '<span class="cap-off">已记录——系统将避免这类风格</span>';
  } catch (e) {
    editorToast('反馈失败：' + e.message, true);
  }
}

// §16 上传配音：Import → Probe → ASR → Word Timing → Narration（TTS=SKIP）
async function uploadVoice(){
  const fi = document.getElementById('voice_upload_file');
  const st = document.getElementById('voice-upload-status');
  if (!fi || !fi.files || !fi.files.length) { if (st) st.textContent = '请先选择音频文件'; return; }
  const pid = (document.getElementById('editor-project')||{}).value || '';
  if (!pid) { if (st) st.textContent = '需要项目：先打开一个项目'; return; }
  st.textContent = '导入中（探测→对齐）……';
  const fd = new FormData();
  fd.append('project_id', pid);
  fd.append('voice_file', fi.files[0]);
  const r = await apiUpload('/api/voice_upload', fd);
  if (r.ok) {
    st.textContent = `已导入 ${r.duration}s（来源 ${r.source}${r.source==='estimated'?'（估算时间轴）':''}）`;
  } else {
    st.textContent = (r.error || '导入失败');
  }
}

// 创作记忆（§27：偏好的视觉/动效，不是统计数字）
async function loadMemoryView(){
  const out = document.getElementById('memory-view');
  if (!out) return;  // 记忆视图挂在设置页时可再加容器；无容器时静默
  out.innerHTML = '加载中……';
  try {
    const j = await apiGet('/api/style_dna');
    if (j.error) throw new Error(j.error);
    const dna = j.style_dna;
    const prefs = j.recent_preferences || [];
    let dnaHtml = '<div class="hint">暂无风格记忆——完成成片并在结果页反馈后开始积累。</div>';
    if (dna) {
      const doms = dna.domains || {};
      const domRows = Object.entries(doms).map(([k,v]) => {
        const pref = Object.entries(v.prefer||{}).map(([n,c])=>`<span class="cap-ok">${n}×${c}</span>`).join(' ');
        const av = Object.entries(v.avoid||{}).map(([n,c])=>`<span class="cap-off">${n}×${c}</span>`).join(' ');
        return `<tr><td>${esc(k)}</td><td>${pref||'—'}</td><td>${av||'—'}</td></tr>`;
      }).join('');
      dnaHtml = `<div><b>${esc(dna.name||'个人风格')}</b>（源自 ${j.case_count||0} 次创作）</div>
        <table class="cap-table"><tr><td>偏好域</td><td>喜欢</td><td>避免</td></tr>${domRows}</table>`;
    }
    const prefRows = prefs.map(p=>`<tr><td>${p.signal==='prefer'?'喜欢':(p.signal==='avoid'?'避免':'·')}</td><td>${esc(String(p.field||''))}</td><td>${esc(String(p.normalized||p.value_to||''))}</td></tr>`).join('');
    out.innerHTML = dnaHtml + (prefRows ? `<div style="margin-top:12px"><b>最近偏好信号</b><table class="cap-table"><tr><td>信号</td><td>维度</td><td>值</td></tr>${prefRows}</table></div>` : '');
  } catch (e) {
    out.innerHTML = `<div class="err-box">加载失败：${esc(e.message)}</div>`;
  }
}

Object.assign(window, {
  sendFeedback, uploadVoice, loadMemoryView,
});
