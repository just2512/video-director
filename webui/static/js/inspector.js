const LAY_TYPE_CN = {video:'视频', image:'图片', person:'人物', text:'文本', title:'标题', subtitle:'字幕', lowerThird:'下三栏', shape:'形状', svg:'SVG', icon:'图标', chart:'图表', diagram:'示意图', graphic:'图形', background:'背景', logo:'标识', lottie:'动画', pip:'画中画', html:'网页', canvas:'画布', webgl:'3D', three_scene:'3D场景', broll:'B-roll'};
function layerTypeCn(t){ return LAY_TYPE_CN[t] || t; }
const MOTION_TYPE_CN = {enter:'入场', emphasis:'强调', exit:'出场', transition:'转场', idle:'静态', ambient:'环境'};
const COMMERCIAL_CN = {CLEAR:'✓ 可商用', UNKNOWN:'? 待确认', BLOCKED:'× 禁止商用', REVIEW:'⚠ 需复核'};

function edShowInspector(){
  const card = document.getElementById('editor-inspector-card');
  const box = document.getElementById('editor-inspector');
  if (!ED.sel) { card.style.display='none'; return; }
  card.style.display='';
  if (edSelectedCount() > 1) {
    const _ctxTag3 = document.getElementById('ed-inspector-ctx');
    if (_ctxTag3) _ctxTag3.textContent = '上下文：多选';
    // 多选聚合视图（§23/§47：批量命令作用于选中集合，单 revision）
    const sceneIds = ED.comp.scenes.filter(s => edIsSelected('scene', s.id)).map(s => s.id);
    const layerIds = [...ED.selSet].filter(k => k.startsWith('layer:')).map(k => k.slice(6));
    box.innerHTML = `<b>已选 ${edSelectedCount()} 项</b>`+
      `<div class="hint">场景：${sceneIds.length} 个 · 图层：${layerIds.length} 个</div>`+
      `<div class="btn-row">`+
      `<button class="btn-secondary btn-sm" onclick="editorBatchCmd('duplicate')">⧉ 批量复制场景</button>`+
      `<button class="btn-secondary btn-sm" onclick="editorBatchCmd('delete')">🗑 批量删除场景</button>`+
      `<button class="btn-secondary btn-sm" onclick="editorBatchCmd('lock')">🔒 批量锁定图层</button>`+
      `<button class="btn-secondary btn-sm" onclick="edClearSelection()">取消选择</button></div>`;
    return;
  }
  const _ctxTag = document.getElementById('ed-inspector-ctx');
  if (ED.sel.type === 'scene') {
    if (_ctxTag) _ctxTag.textContent = '上下文：场景';
    const sc = ED.comp.scenes.find(s=>s.id===ED.sel.id);
    if (!sc) { // 场景已被删/重命名（split 产生 a/b）：清选择防 undefined
      edClearSelection();
      document.getElementById('editor-inspector').innerHTML = '<div class="hint">场景已变化（拆分/删除），请重新选择</div>';
      return;
    }
    box.innerHTML =
      `<b>场景 ${esc(ED.sel.id)}</b><div class="hint">文案：${esc(sc.text)}<br>时长：${sc.duration.toFixed(1)}s · ${sc.layers.length} 层` +
      (sc.origin_recipe_id ? `<br>镜头配方：${esc(sc.origin_recipe_id)}` : '') + `</div>` +
      `<label>改时长（秒）</label><div class="btn-row"><input type="number" id="ed-dur" value="${sc.duration.toFixed(1)}" step="0.5" style="width:80px">`+
      `<button class="btn-secondary btn-sm" onclick="editorApplyDuration('${sc.id}')">应用</button></div>`+
      `<button class="btn-secondary btn-sm" onclick="editorOpenShotPicker('${sc.id}')">🎬 换镜头</button>`+
      `<div id="editor-picker-box"></div>`;
    return;
  }
  // layer（§55：按选中对象类型显示字段组）
  const _ctxTag2 = document.getElementById('ed-inspector-ctx');
  if (_ctxTag2) _ctxTag2.textContent = '上下文：' + (layerTypeCn((ED.comp.scenes.flatMap(sc=>sc.layers)[parseInt(ED.sel.id.split('#')[1])] || {}).type) || '图层');
  const [sceneId, idxS] = ED.sel.id.split('#');
  const idx = parseInt(idxS);
  const sc = ED.comp.scenes.find(s=>s.id===sceneId);
  const lay = sc && sc.layers[idx];
  if (!lay) {  // 层索引越界（split/删除后）：防御防 JS error
    edClearSelection();
    document.getElementById('editor-inspector').innerHTML = '<div class="hint">图层已变化，请重新选择</div>';
    return;
  }
  box.innerHTML =
    `<b>图层 ${ED.sel.id}</b>（${layerTypeCn(lay.type)}）`+
    `<div class="hint">时长：${lay.duration.toFixed(1)}s · 素材：${esc(lay.asset_id || '—')}</div>`+
    `<div class="role-block"><b>内容</b><div class="hint">素材：${esc(lay.asset_id || '无')}</div></div>`+
    `<div class="role-block"><b>布局</b><div class="hint">层时长 ${lay.duration.toFixed(1)}s · 场景内起点 ${lay.start.toFixed(1)}s</div></div>`+
    `<div class="role-block"><b>变换</b>（§24 Transform）<div class="btn-row">`+
    `<input type="number" id="ed-tf-x" placeholder="水平 0-1" step="0.05" min="0" max="1" style="width:76px" value="${lay.transform ? lay.transform.x : ''}">`+
    `<input type="number" id="ed-tf-y" placeholder="垂直 0-1" step="0.05" min="0" max="1" style="width:76px" value="${lay.transform ? lay.transform.y : ''}">`+
    `<input type="number" id="ed-tf-rot" placeholder="旋转°" step="5" style="width:64px" value="${lay.transform && lay.transform.rotation ? lay.transform.rotation : ''}">`+
    `<input type="number" id="ed-opacity" placeholder="透明度 0-1" step="0.1" min="0" max="1" style="width:90px" value="${lay.transform ? lay.transform.opacity : ''}">`+
    `<input type="number" id="ed-scale" placeholder="缩放 0.1-3" step="0.1" style="width:76px" value="${lay.transform ? lay.transform.scale : ''}">`+
    `<button class="btn-secondary btn-sm" onclick="editorSetVisualTransform('${sceneId}',${idx})">应用变换</button></div>`+
    `<div class="hint">水平/垂直为画幅归一坐标（0.5=居中）；旋转单位为度。</div></div>`+
    `<div class="role-block"><b>关键帧</b>（§24 点击 ● 在当前时间打帧）<div id="ed-kf-list">${renderKeyframeList(lay, sceneId, idx)}</div>`+
    `<div class="btn-row">`+
    `<select id="ed-kf-prop" style="width:96px"><option value="opacity">透明度</option><option value="scale">缩放</option><option value="x">水平</option><option value="y">垂直</option><option value="rotation">旋转</option></select>`+
    `<input type="number" id="ed-kf-at" placeholder="时间秒" step="0.1" min="0" style="width:72px">`+
    `<input type="number" id="ed-kf-val" placeholder="数值" step="0.1" style="width:64px">`+
    `<button class="btn-secondary btn-sm" onclick="editorAddKeyframe('${sceneId}',${idx})">● 打关键帧</button></div></div>`+
    `<div class="role-block"><b>蒙版</b>（§63 Mask）${lay.mask ? `<div class="hint">当前：${maskShapeCn(lay.mask.shape)} · 羽化 ${lay.mask.feather}${lay.mask.inverted ? ' · 反转' : ''}</div>` : ''}`+
    `<div class="btn-row"><select id="ed-mask-shape" style="width:100px"><option value="">形状…</option><option value="rectangle">矩形</option><option value="rounded_rect">圆角矩形</option><option value="ellipse">椭圆</option></select>`+
    `<input type="number" id="ed-mask-feather" placeholder="羽化px" min="0" style="width:72px">`+
    `<label style="display:flex;align-items:center;gap:4px;font-size:12px"><input type="checkbox" id="ed-mask-invert"> 反转</label>`+
    `<button class="btn-secondary btn-sm" onclick="editorApplyMask('${sceneId}',${idx})">应用蒙版</button>`+
    (lay.mask ? `<button class="btn-secondary btn-sm" onclick="editorCmd('remove_mask', {}, '${ED.sel.id}')">移除蒙版</button>` : '')+`</div></div>`+
    (lay.type === 'video' ? `<div class="role-block"><b>抠像</b>（§64 Matte：绿幕/蓝幕抠底，产物入素材库）<div class="btn-row">`+
    `<select id="ed-mat-color" style="width:110px"><option value="0x00FF00">绿幕</option><option value="0x0000FF">蓝幕</option></select>`+
    `<button class="btn-secondary btn-sm" onclick="editorRunMatting('${sceneId}',${idx})">🎞 生成抠像</button></div>`+
    `<div class="hint" id="ed-mat-status">ChromaKey（ffmpeg colorkey）；无 Provider 时诚实不可用。</div></div>` : '')+
    `<div class="role-block"><b>动效</b><div class="hint">当前：${lay.motion_recipe_id ? esc(lay.motion_recipe_id) : '无'}`+
    (lay.motion_version ? ` @${esc(lay.motion_version)}` : '') + `</div>`+
    `<label>动效时长（秒）</label><div class="btn-row"><input type="number" id="ed-mot-dur" value="0.5" step="0.1" min="0.1" style="width:80px">`+
    `<select id="ed-mot-easing" style="width:110px"><option value="">缓动（默认）</option>`+
    ['linear','quad','cubic','quart','expo','back','sine'].map(e=>`<option value="${e}">${e}</option>`).join('') + `</select></div>`+
    `<div class="btn-row"><button class="btn-secondary btn-sm" onclick="editorPreviewMotionParam('${sceneId}',${idx})">👁 预览参数</button>`+
    `<button class="btn-sm" onclick="editorApplyMotionParam('${sceneId}',${idx})">✅ 应用参数</button></div>`+
    `<div id="ed-motion-preview" style="margin-top:6px"></div></div>`+
    `<div class="btn-row"><button class="btn-secondary btn-sm" onclick="editorOpenMotionPicker('${sceneId}',${idx})">✨ 换动效</button>`+
    `<button class="btn-secondary btn-sm" onclick="editorOpenAssetPicker('${sceneId}',${idx})">🖼 换素材</button>`+
    `<button class="btn-secondary btn-sm" onclick="editorToggleLock('${sceneId}',${idx})">${lay.locked?'🔓 解锁':'🔒 锁定'}</button></div>`+
    `<div id="editor-picker-box"></div>`+
    `<div id="ed-asset-inspector" class="hint" style="margin-top:6px">素材授权……</div>`;
  // EDT-005 AssetInspector：异步加载 Source/License/Commercial/UsedIn
  if (lay.asset_id) loadAssetInspector(lay.asset_id);
  else document.getElementById('ed-asset-inspector').textContent = '';
}

async function loadAssetInspector(assetId){
  const el = document.getElementById('ed-asset-inspector');
  if (!el) return;
  try {
    const d = await (await fetch('/api/asset_detail?asset_id=' + encodeURIComponent(assetId) + '&project_id=' + encodeURIComponent(ED.pid||''))).json();
    if (!d || d.error) { el.textContent = ''; return; }
    const statusCn = {CLEAR:'✓ 商用可用', REVIEW:'⚠ 需复核', BLOCKED:'× 已阻断', UNKNOWN:'⚠ 状态未知'};
    el.innerHTML = `<b>素材信息</b><br>来源：${esc(d.source||'—')} · 授权：<b>${statusCn[d.commercial_status]||esc(d.commercial_status)}</b>`+
      (d.license_note ? `<br>许可：${esc(d.license_note)}` : '') +
      (d.used_in && d.used_in.length ? `<br>用于：${esc(d.used_in.slice(0,3).join('、'))}` : '');
  } catch(e) { el.textContent = ''; }
}

function editorSetVisualTransform(sceneId, idx){
  // §72.1 结构化变换：任填几项，增量更新（服务端 model_validate fail-closed）
  const payload = {};
  const fields = [['ed-tf-x','x'],['ed-tf-y','y'],['ed-tf-rot','rotation'],['ed-opacity','opacity'],['ed-scale','scale']];
  for (const [id, key] of fields) {
    const el = document.getElementById(id);
    if (el && el.value !== '') payload[key] = parseFloat(el.value);
  }
  if (!Object.keys(payload).length) { alert('至少填一项（水平/垂直/旋转/透明度/缩放）'); return; }
  editorCmd('set_visual_transform', payload, `${sceneId}#${idx}`);
}

function renderKeyframeList(lay, sceneId, idx){
  // §24 关键帧轨列表（每轨一行，帧点可删）
  if (!lay.keyframes || !lay.keyframes.length) return '<div class="hint">暂无关键帧</div>';
  const propCn = {opacity:'透明度', scale:'缩放', x:'水平', y:'垂直', rotation:'旋转', volume:'音量', blur:'模糊', mask_path:'蒙版路径', camera_x:'机位X', camera_y:'机位Y', camera_scale:'机位缩放'};
  return lay.keyframes.map(t =>
    `<div class="hint">◆ ${propCn[t.property]||t.property}：` +
    t.keyframes.map(k => `<span style="margin-right:6px">●${k.at.toFixed(1)}s=${esc(String(k.value))}`+
      ` <a href="#" onclick="editorRemoveKeyframe('${sceneId}',${idx},'${t.property}',${k.at});return false" title="删除该帧">✕</a></span>`).join('') +
    `</div>`).join('');
}

function editorAddKeyframe(sceneId, idx){
  const prop = document.getElementById('ed-kf-prop').value;
  const at = document.getElementById('ed-kf-at').value;
  const val = document.getElementById('ed-kf-val').value;
  if (at === '' || val === '') { alert('填时间（秒）和数值'); return; }
  editorCmd('add_keyframe', {property: prop, at: parseFloat(at), value: parseFloat(val)}, `${sceneId}#${idx}`);
}

function editorRemoveKeyframe(sceneId, idx, prop, at){
  editorCmd('remove_keyframe', {property: prop, at: at}, `${sceneId}#${idx}`);
}

function maskShapeCn(shape){
  return {rectangle:'矩形', rounded_rect:'圆角矩形', ellipse:'椭圆', polygon:'多边形', path:'路径'}[shape] || shape;
}

function editorApplyMask(sceneId, idx){
  const shape = document.getElementById('ed-mask-shape').value;
  if (!shape) { alert('先选蒙版形状'); return; }
  const payload = {shape: shape};
  const f = document.getElementById('ed-mask-feather');
  if (f && f.value !== '') payload.feather = parseFloat(f.value);
  const inv = document.getElementById('ed-mask-invert');
  if (inv && inv.checked) payload.inverted = true;
  editorCmd('apply_mask', payload, `${sceneId}#${idx}`);
}

async function editorRunMatting(sceneId, idx){
  // §64 Matting：ChromaKey 抠像 → 产物入项目素材库 → 替换当前层素材
  const st = document.getElementById('ed-mat-status');
  if (st) st.textContent = '抠像处理中……（ffmpeg colorkey）';
  try {
    const r = await apiPost('/api/editor/matting', {
      project_id: ED.pid, target_id: `${sceneId}#${idx}`,
      color: (document.getElementById('ed-mat-color') || {}).value || '0x00FF00',
    });
    if (!r.ok) {
      if (st) st.textContent = '❌ ' + (r.error || '抠像失败');
      return;
    }
    if (st) st.textContent = `✅ 抠像完成（${r.asset_id}），已替换当前层素材`;
    // 抠像产物替换当前层（走标准 replace_source 命令）
    editorCmd('replace_source', {assetId: r.asset_id}, `${sceneId}#${idx}`);
  } catch (e) {
    if (st) st.textContent = '❌ ' + String(e);
  }
}

// ---- §65 Repo Promo：发现项目 → 扫描 → 功能证据 ----
function promoTogglePanel(){
  const p = document.getElementById('promo-panel');
  if (p) p.style.display = p.style.display === 'none' ? '' : 'none';
}

async function promoDiscover(){
  const root = (document.getElementById('promo-root').value || '').trim();
  if (!root) { alert('先填开发项目根目录'); return; }
  const box = document.getElementById('promo-repos');
  box.textContent = '扫描中……';
  try {
    const r = await apiPost('/api/promo/discover', {root: root});
    if (!r.ok) { box.textContent = '❌ ' + (r.error || '未发现项目'); return; }
    const repos = r.repos || [];
    if (!repos.length) { box.textContent = '该目录下未发现开发项目（需要 .git / package.json 等指纹）'; return; }
    box.innerHTML = repos.map(x =>
      `<div class="btn-row"><span class="hint">📁 ${esc(x.name)}${x.frameworks.length ? ' · ' + x.frameworks.join('/') : ''}${x.has_git ? ' · git' : ''}</span>`+
      `<button class="btn-secondary btn-sm" onclick="promoScan('${esc(x.path)}')">扫描</button></div>`).join('');
  } catch (e) { box.textContent = '❌ ' + String(e); }
}

async function promoScan(path){
  const scanBox = document.getElementById('promo-scan-result');
  const featBox = document.getElementById('promo-features');
  scanBox.textContent = '项目扫描中……';
  try {
    const r = await apiPost('/api/promo/scan', {root: path});
    if (!r.ok) { scanBox.textContent = '❌ ' + (r.error || '扫描失败'); return; }
    const s = r.snapshot || {};
    scanBox.innerHTML = `<div class="hint">✅ <b>${esc(s.project_id)}</b> · 框架：${esc((s.framework||[]).join('、') || '未识别')} · `+
      `入口：${(s.entry_points||[]).length} · 路由：${(s.routes||[]).length} · 组件：${(s.components||[]).length}`+
      (s.design_system && Object.keys(s.design_system).length ? ` · 设计令牌：${Object.keys(s.design_system).length}` : '')+`</div>`;
    // 功能发现（证据链）
    featBox.textContent = '功能发现中……';
    const f = await apiPost('/api/promo/features', {root: path});
    if (!f.ok) { featBox.textContent = '❌ ' + (f.error || '功能发现失败'); return; }
    const g = f.evidence_graph || {};
    const claims = g.claims || [];
    const verified = claims.filter(c => c.status === 'VERIFIED');
    featBox.innerHTML = `<b>功能卖点（${verified.length} 条已验证，只显示可追溯证据的）</b>`+
      (verified.length ? verified.slice(0, 12).map(c =>
        `<div class="hint">✓ ${esc(c.text)}<br><span style="font-size:11px;color:#6b7a86">证据：${(c.evidence_refs||[]).map(id => { const e = (g.evidence||[]).find(x=>x.id===id); return e ? esc(e.source_path || e.evidence_type) : id; }).join('、')}</span></div>`).join('')
        : '<div class="hint">未在 README/CHANGELOG 中发现可验证的功能声明（可先补文档或直接用「从文字开始」创作）</div>');
  } catch (e) { scanBox.textContent = '❌ ' + String(e); }
}

function editorToggleLock(sceneId, idx){
  editorCmd('lock', {locked: undefined}, `${sceneId}#${idx}`);
}
function editorAudioVolume(trackType){
  const v = prompt('音轨音量（0-2，1=原始）：', '1.0');
  if (v === null) return;
  editorCmd('set_audio_volume', {volume: parseFloat(v)}, trackType, true);
}

// ---- 项目级控制（§18 original / §19 watermark / §32 subtitle / §34 BGM）----
async function edVoiceLoadOptions(){
  // §51 Voice 下拉：从 /api/voices 拉音色清单（与创作页同源）
  const sel = document.getElementById('ed-voice-sel');
  if (!sel || sel.options.length) return;
  try {
    const voices = await (await fetch('/api/voices')).json();
    const list = Array.isArray(voices) ? voices : (voices.voices || []);
    sel.innerHTML = list.slice(0, 20).map(v =>
      `<option value="${esc(v.voice_id || v.id || v)}">${esc(v.name || v.label || v.voice_id || v)}</option>`).join('');
  } catch (e) { /* 诚实：加载失败保留下拉空态 */ }
}
async function edVoicePreview(){
  // §51 试听（VoicePreviewArtifact，非正式 Narration §15）
  const sel = document.getElementById('ed-voice-sel');
  const st = document.getElementById('ed-voice-status');
  if (!sel || !sel.value) { if (st) st.textContent = '先选音色'; return; }
  if (st) st.textContent = '合成试听中……';
  try {
    const audio = new Audio('/api/voice_preview?voice=' + encodeURIComponent(sel.value));
    audio.play();
    if (st) st.textContent = '▶ 试听播放中';
  } catch (e) {
    if (st) st.textContent = '试听失败：' + e;
  }
}
function editorOriginalAudio(mode){
  editorCmd('set_original_audio', {mode, volume: mode === 'preserve' ? 0.8 : 0.0, duckAgainst: 'narration'});
}
function editorOriginalAudioVolume(){
  const v = prompt('原声音量（0-2，1=原始）：', '0.8');
  if (v === null) return;
  editorCmd('set_original_audio_volume', {volume: parseFloat(v)});
}
async function editorWatermarkAdd(){
  const text = prompt('水印文本（例：品牌名）：', '水印');
  if (!text) return;
  const anchor = prompt('位置：top_left / top_right / bottom_left / bottom_right / center', 'top_right');
  editorCmd('set_watermark', {kind: 'text', text, anchor: anchor || 'top_right', opacity: 0.75});
}
async function editorWatermarkRemove(){
  editorCmd('remove_watermark', {});
}
// §55 Inspector context-aware：水印条目显 Asset/Position/Opacity/Scale（项目级卡内联）
function renderWatermarkList(){
  const box = document.getElementById('editor-watermark-list');
  if (!box) return;
  const wms = (ED.comp && ED.comp.watermarks) || [];
  if (!wms.length) { box.textContent = '当前无水印'; return; }
  box.innerHTML = wms.map((w, i) =>
    `<div class="proj-row"><span><b>${esc(w.text || w.asset_id || w.kind)}</b>`+
    `<span class="hint">kind=${esc(w.kind||'text')} · 位置=${esc(w.anchor||'bottom_right')} · 透明=${w.opacity??'—'} · 缩放=${w.scale??'—'}</span></span>`+
    `<span class="btn-row">`+
    `<button class="btn-secondary btn-sm" onclick="editorWatermarkMove(${i})">📍 移位</button>`+
    `<button class="btn-secondary btn-sm" onclick="editorWatermarkOpacity(${i})">🎚 透明度</button>`+
    `<button class="btn-secondary btn-sm" onclick="editorCmd('remove_watermark',{})">🗑 删</button></span></div>`).join('');
}
function editorWatermarkMove(i){
  const anchor = prompt('位置（top_left/top_right/bottom_left/bottom_right/center）：', 'top_right');
  if (!anchor) return;
  const w = (ED.comp.watermarks||[])[i] || {};
  editorCmd('set_watermark', {kind: w.kind||'text', text: w.text||'水印',
                              anchor, opacity: w.opacity ?? 0.75});
}
function editorWatermarkOpacity(i){
  const v = prompt('透明度（0-1）：', '0.75');
  if (v === null) return;
  const w = (ED.comp.watermarks||[])[i] || {};
  editorCmd('set_watermark', {kind: w.kind||'text', text: w.text||'水印',
                              anchor: w.anchor||'top_right', opacity: parseFloat(v)});
}
async function editorSubtitleStyle(which){
  const payload = which === 'white_black'
    ? {color: '#FFFFFF', strokeColor: '#000000'}
    : {position: 'bottom'};
  editorCmd('set_subtitle_style', payload);
}
// §52 完整字幕样式面板：Font/Size/Color/Outline/Background/Position/Animation
function editorSubtitleStyleApply(){
  const payload = {};
  const mode = document.getElementById('sub-mode');
  const size = document.getElementById('sub-size');
  const color = document.getElementById('sub-color');
  const stroke = document.getElementById('sub-stroke');
  const strokeW = document.getElementById('sub-stroke-w');
  const pos = document.getElementById('sub-pos');
  const anim = document.getElementById('sub-anim');
  const bg = document.getElementById('sub-bg');
  const align = document.getElementById('sub-align');
  if (mode && mode.value) payload.mode = mode.value;
  const font = document.getElementById('sub-font');
  if (font && font.value.trim()) payload.font = font.value.trim();  // §52 Font
  if (size && size.value) payload.size = parseFloat(size.value);
  if (color && color.value) payload.color = color.value;
  if (stroke && stroke.value) payload.strokeColor = stroke.value;
  if (strokeW && strokeW.value !== '') payload.strokeWidth = parseInt(strokeW.value);
  if (pos && pos.value) payload.position = pos.value;
  if (anim && anim.value) payload.animation = anim.value;
  if (bg && bg.value) { payload.background = bg.value; payload.backgroundOpacity = 0.5; }
  if (align && align.value) payload.alignment = align.value;
  if (!Object.keys(payload).length) { alert('先调整样式字段'); return; }
  editorCmd('set_subtitle_style', payload);
}
// §31/§33 生成字幕轨（word timing → SubtitleTrack 入 IR）
async function editorSubtitleGenerate(){
  const r = await apiPost('/api/director/execute', {
    project_id: ED.pid,
    instruction: '生成字幕（word 模式）',
  });
  if (r.ok) {
    const d = r.director || {};
    const gen = (d.executed||[]).find(e => e.tool === 'subtitle.generate');
    editorToast(gen && gen.ok ? '字幕轨已生成入 IR（§31）' : '字幕轨生成失败：' + ((gen||{}).error||''));
  } else {
    editorToast('❌ ' + (r.error || ''), true);
  }
}
async function editorBgmVolume(){
  const v = prompt('BGM 音量（0-2，1=原始）：', '0.3');
  if (v === null) return;
  editorCmd('set_audio_volume', {volume: parseFloat(v)}, 'bgm', true);
}

// EDT-007：参数预览（local）→ 应用（revision）
async function editorPreviewMotionParam(sceneId, idx){
  const dur = parseFloat(document.getElementById('ed-mot-dur').value) || 0.5;
  const easing = document.getElementById('ed-mot-easing').value;
  const box = document.getElementById('ed-motion-preview');
  if (!box) return;
  // 本地 CSS 预览：不产生 revision（§26 参数改变→Preview→不立即永久 Revision）
  box.innerHTML = `<div class="motion-preview" style="animation:mpPreview ${Math.max(0.4,Math.min(2.5,dur))}s ease-in-out infinite alternate">`+
    `<div class="hint">预览时长 ${dur}s${easing?' · easing '+esc(easing):''}（本地预览，未写入项目）</div></div>`;
}
async function editorApplyMotionParam(sceneId, idx){
  const dur = parseFloat(document.getElementById('ed-mot-dur').value);
  const easing = document.getElementById('ed-mot-easing').value;
  const payload = {};
  if (dur && dur > 0.1) payload.duration = dur;
  if (easing) payload.parameters = {easing: easing};
  editorCmd('set_motion_parameter', payload, `${sceneId}#${idx}`);
}

async function editorOpenAssetPicker(sceneId, idx){
  // §49/§50 统一 AssetPicker：项目素材 / 本地导入 / 最近使用 / 收藏 / Stock / AI生成
  // （§24 六项同一组件；Stock 下 Pexels/Pixabay/Coverr 只是 Provider）
  const box = document.getElementById('editor-picker-box');
  box.innerHTML =
    '<div class="btn-row" style="margin-bottom:6px;flex-wrap:wrap">'+
    '<button class="btn-secondary btn-sm" data-aptab="project">🗂 项目素材</button>'+
    '<button class="btn-secondary btn-sm" data-aptab="local">📥 本地导入</button>'+
    '<button class="btn-secondary btn-sm" data-aptab="recent">🕘 最近使用</button>'+
    '<button class="btn-secondary btn-sm" data-aptab="favorites">⭐ 收藏</button>'+
    '<button class="btn-secondary btn-sm" data-aptab="stock">🌐 Stock</button>'+
    '<button class="btn-secondary btn-sm" data-aptab="generated">🪄 AI生成</button></div>'+
    '<div class="btn-row" style="margin-bottom:6px;flex-wrap:wrap;font-size:12px" id="ap-filters">'+
    '<span class="hint">筛选：</span>'+
    '<select id="ap-f-type" style="width:90px"><option value="">全部</option><option value="video">视频</option><option value="image">图片</option></select>'+
    '<select id="ap-f-ratio" style="width:80px"><option value="">比例</option><option value="16:9">16:9</option><option value="9:16">9:16</option><option value="1:1">1:1</option></select>'+
    '<label><input type="checkbox" id="ap-f-clear"> 只看可商用素材</label>'+
    '</div>'+
    '<div id="asset-picker-body">加载素材……</div>';
  box.querySelectorAll('[data-aptab]').forEach(btn => {
    btn.onclick = () => assetPickerTab(btn.getAttribute('data-aptab'), idx, sceneId);
  });
  ['ap-f-type', 'ap-f-ratio', 'ap-f-clear'].forEach(fid => {
    const el = document.getElementById(fid);
    if (el) el.onchange = () => assetPickerTab(AP.lastTab || 'project', idx, sceneId);
  });
  assetPickerTab('project', idx, sceneId);
}

// §8 拆分导出：inline onclick/window 引用兼容（零行为改动）
Object.assign(window, {
  layerTypeCn,
  edShowInspector,
  loadAssetInspector,
  editorSetVisualTransform,
  renderKeyframeList,
  editorAddKeyframe,
  editorRemoveKeyframe,
  maskShapeCn,
  editorApplyMask,
  editorRunMatting,
  promoTogglePanel,
  promoDiscover,
  promoScan,
  editorToggleLock,
  editorAudioVolume,
  edVoiceLoadOptions,
  edVoicePreview,
  editorOriginalAudio,
  editorOriginalAudioVolume,
  editorWatermarkAdd,
  editorWatermarkRemove,
  renderWatermarkList,
  editorWatermarkMove,
  editorWatermarkOpacity,
  editorSubtitleStyle,
  editorSubtitleStyleApply,
  editorSubtitleGenerate,
  editorBgmVolume,
  editorPreviewMotionParam,
  editorApplyMotionParam,
  editorOpenAssetPicker,
});
