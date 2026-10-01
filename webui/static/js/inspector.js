// §12/§66 Inspector（方案《FRONTEND_DEEP_UNIFICATION》）：
// Inspector 完全由 Selection 决定。resolveInspector(selection, composition) 分发：
//   project   → 内容/画幅/风格/默认声音/版本 + 预览/发布预检
//   scene      → 内容/画面(镜头·动效)/镜头关系/声音/字幕
//   layer      → 按层类型：素材裁剪速度/变换/动效/画中画/蒙版/关键帧/原声
//   scene_pair → 八类型镜头关系 + 预览/应用 + 连续性检查
// 多选 → 批量操作聚合视图。

const LAY_TYPE_CN = {video:'视频', image:'图片', person:'人物', text:'文本', title:'标题', subtitle:'字幕', lowerThird:'人物条', shape:'形状', svg:'图形', icon:'图标', chart:'图表', diagram:'示意图', graphic:'图形', background:'背景', logo:'标识', lottie:'动画', pip:'画中画', html:'网页', canvas:'画布', webgl:'3D', three_scene:'3D场景', broll:'B-roll'};
function layerTypeCn(t){ return LAY_TYPE_CN[t] || t; }
const MOTION_TYPE_CN = {enter:'入场', emphasis:'强调', exit:'出场', transition:'转场', idle:'静态', ambient:'环境'};
const COMMERCIAL_CN = {CLEAR:'可商用', UNKNOWN:'待确认', BLOCKED:'禁止商用', REVIEW:'需复核'};

// §47 八类型镜头关系（Morph/Continuity/Transition/Camera 唯一前端入口）
const CONT_CN = {carry:'元素延续', zoom_through:'推镜穿越', morph:'形变过渡', pan_follow:'平移跟随',
  camera_dive:'镜头俯冲', materialize:'具象成形', gather:'聚拢收束', hard_cut:'硬切'};

/** §66 Section Resolver：Selection → Inspector 分区集合。 */
function resolveInspector(selection, composition){
  if (!selection) return null;
  if (selection.type === 'project') return {kind: 'project'};
  if (selection.type === 'scene') return {kind: 'scene', id: selection.id};
  if (selection.type === 'layer') return {kind: 'layer', id: selection.id};
  if (selection.type === 'scene_pair') return {kind: 'scene_pair', id: selection.id};
  return null;
}

/** 入口：Selection 变化 → Inspector 重渲染（selection:changed 订阅者）。 */
function edShowInspector(){
  const card = document.getElementById('editor-inspector-card');
  const box = document.getElementById('editor-inspector');
  if (!card || !box) return;
  const ctx = resolveInspector(SEL.selection, ED.comp);
  if (!ctx) {
    // §12 未选择：整项目概览（不是空白）
    card.style.display = '';
    _inspectorProject();
    return;
  }
  card.style.display = '';
  const tag = document.getElementById('ed-inspector-ctx');
  if (edSelectedCount() > 1) {
    if (tag) tag.textContent = '多选';
    _inspectorMulti();
    return;
  }
  if (ctx.kind === 'project') { if (tag) tag.textContent = '项目'; _inspectorProject(); return; }
  if (ctx.kind === 'scene') { if (tag) tag.textContent = '场景'; _inspectorScene(ctx.id); return; }
  if (ctx.kind === 'layer') { if (tag) tag.textContent = '图层'; _inspectorLayer(ctx.id); return; }
  if (ctx.kind === 'scene_pair') { if (tag) tag.textContent = '镜头关系'; _inspectorScenePair(ctx.id); return; }
}

/** §12 Inspector 分区打开（Review Locate 用：滚动到指定分组）。 */
function inspectorOpenSection(name){
  const sec = document.querySelector(`#editor-inspector [data-section="${name}"]`);
  if (sec) sec.scrollIntoView({behavior: 'smooth', block: 'center'});
}

// ---------------------------------------------------------------------------
// Project 上下文（§12 选择 Project）
// ---------------------------------------------------------------------------
function _inspectorProject(){
  const box = document.getElementById('editor-inspector');
  const c = ED.comp;
  if (!c) { box.innerHTML = '<div class="hint">打开项目后显示项目属性。</div>'; return; }
  box.innerHTML = `
    <b>项目</b>
    <div class="role-block" data-section="content"><b>内容</b>
      <div class="hint">${c.scenes.length} 场景 · 总长 ${c.duration.toFixed(1)}s · 画幅 ${c.width}×${c.height}</div></div>
    <div class="role-block" data-section="style"><b>风格</b>
      <div class="hint">${esc(c.origin_template_id ? '模板 ' + c.origin_template_id : '自由创作')}</div></div>
    <div class="role-block" data-section="audio"><b>默认声音</b>
      <div class="hint">配音/音乐/音效在右侧「声音与字幕」面板调整</div></div>
    <div class="role-block" data-section="version"><b>版本</b>
      <div class="hint">当前 V${c.version} · 顶栏 V 号可看历史</div></div>
    <div class="btn-row">
      <button class="btn-secondary btn-sm" onclick="editorPreviewBar()">预览</button>
      <button class="btn-secondary btn-sm" onclick="editorPreflight()">发布预检</button>
    </div>`;
}

// ---------------------------------------------------------------------------
// Scene 上下文（§12 内容/画面/镜头关系/声音/字幕）
// ---------------------------------------------------------------------------
function _inspectorScene(sceneId){
  const box = document.getElementById('editor-inspector');
  const sc = ED.comp.scenes.find(s => s.id === sceneId);
  if (!sc) { clearSelection('inspector'); box.innerHTML = '<div class="hint">场景已变化，请重新选择</div>'; return; }
  const si = ED.comp.scenes.indexOf(sc);
  const cont = sc.continuity_in;
  const audioOf = ty => (ED.comp.audio || []).find(a => a.type === ty);
  const narr = audioOf('narration');
  box.innerHTML = `
    <b>场景 ${si + 1} · ${esc(sc.id)}</b>
    <div class="role-block" data-section="content"><b>内容</b>
      <div class="hint">${esc(sc.text || '')}</div>
      <div class="btn-row"><input type="number" id="ed-dur" value="${sc.duration.toFixed(1)}" step="0.5" style="width:80px" title="场景时长（秒）">
      <button class="btn-secondary btn-sm" onclick="editorApplyDuration('${sc.id}')">应用时长</button></div>
      <div class="hint">${sc.origin_recipe_id ? '镜头方案：' + esc(shotCnName(sc.origin_recipe_id)) : '自由镜头'}</div>
    </div>
    <div class="role-block" data-section="visual"><b>画面</b>
      <div class="btn-row" style="flex-wrap:wrap">
        <button class="btn-secondary btn-sm" onclick="editorOpenShotPicker('${sc.id}')">换镜头</button>
        <button class="btn-secondary btn-sm" onclick="editorApplyCamera('${sc.id}','push_in')">镜头推进</button>
        <button class="btn-secondary btn-sm" onclick="editorApplyCamera('${sc.id}','pull_out')">镜头拉远</button>
      </div>
      <div id="editor-picker-box"></div>
    </div>
    ${si > 0 ? `
    <div class="role-block" data-section="relation"><b>镜头关系</b>
      <span class="hint">上一镜（场景 ${si}）→ 当前镜（场景 ${si + 1}）</span>
      <div class="hint">当前：${cont ? (CONT_CN[cont.type] || cont.type) : '硬切（默认）'}</div>
      <div class="btn-row"><select id="ed-cont-type" style="width:110px" onchange="edContTypeChanged()">
        ${Object.entries(CONT_CN).map(([v, l]) => `<option value="${v}"${cont && cont.type === v ? ' selected' : ''}>${l}</option>`).join('')}
      </select><button class="btn-secondary btn-sm" onclick="editorApplyContinuity('${sc.id}')">应用</button></div>
      <div id="ed-morph-row" ${cont && cont.type === 'morph' ? '' : 'style="display:none"'}>
        ${_morphBlockRow(ED.comp.scenes[si - 1], sc)}
      </div>
      <div class="btn-row" style="margin-top:4px">
        <button class="btn-secondary btn-sm" onclick="editorVerifyContinuity('${sc.id}')">检查当前镜头</button>
        <button class="btn-secondary btn-sm" onclick="editorVerifyContinuity('')">检查全片</button>
      </div>
      <div id="ed-cont-verify"></div>
    </div>` : ''}
    <div class="role-block" data-section="audio"><b>声音</b>
      <div class="hint">${narr ? '旁白轨（音量在右侧「声音与字幕」调整）' : '无旁白轨'} · 本镜文案 ${sc.duration.toFixed(1)}s</div></div>
    <div class="role-block" data-section="generate"><b>画面生成</b>
      <div class="hint">在时间线下方「生成画面」面板，为这个场景生成新画面。</div></div>`;
}

// ---------------------------------------------------------------------------
// Layer 上下文（§12 按层类型分区）
// ---------------------------------------------------------------------------
function _inspectorLayer(layerId){
  const box = document.getElementById('editor-inspector');
  const [sceneId, idxS] = layerId.split('#');
  const idx = parseInt(idxS);
  const sc = ED.comp.scenes.find(s => s.id === sceneId);
  const lay = sc && sc.layers[idx];
  if (!lay) { clearSelection('inspector'); box.innerHTML = '<div class="hint">图层已变化，请重新选择</div>'; return; }
  box.innerHTML = `
    <b>${layerTypeCn(lay.type)}</b> <span class="hint">${esc(layerId)}</span>
    <div class="role-block" data-section="source"><b>素材</b>
      <div class="hint">${esc(lay.asset_id || '程序化画面（无外部素材）')} · 层时长 ${lay.duration.toFixed(1)}s · 场景内起点 ${lay.start.toFixed(1)}s</div>
      ${lay.type === 'video' ? `
      <div class="hint">源区间 ${lay.source_in.toFixed(1)}s → ${lay.source_out ? lay.source_out.toFixed(1) + 's' : '结尾'} · 速度 ${lay.speed.toFixed(1)}x</div>
      <div class="btn-row"><input type="number" id="ed-trim-in" placeholder="起点秒" step="0.5" min="0" style="width:76px">
      <input type="number" id="ed-trim-out" placeholder="终点秒" step="0.5" min="0" style="width:76px">
      <button class="btn-secondary btn-sm" onclick="editorApplyTrim('${sceneId}',${idx})">应用裁剪</button>
      <select id="ed-speed" style="width:70px">${[0.5,1,1.5,2].map(s2=>`<option value="${s2}"${Math.abs(lay.speed-s2)<0.01?' selected':''}>${s2}x</option>`).join('')}</select>
      <button class="btn-secondary btn-sm" onclick="editorApplySpeed('${sceneId}',${idx})">应用速度</button></div>` : ''}
      <div class="btn-row">
        <button class="btn-secondary btn-sm" onclick="editorOpenAssetPicker('${sceneId}',${idx})">换素材</button>
        <button class="btn-secondary btn-sm" onclick="editorToggleLock('${sceneId}',${idx})">${lay.locked ? '解锁' : '锁定'}</button>
      </div>
      <div id="ed-asset-inspector" class="hint"></div>
    </div>
    <div class="role-block" data-section="transform"><b>变换</b>
      <div class="btn-row" style="flex-wrap:wrap">
        <input type="number" id="ed-tf-x" placeholder="水平 0-1" step="0.05" min="0" max="1" style="width:72px" value="${lay.transform ? lay.transform.x : ''}">
        <input type="number" id="ed-tf-y" placeholder="垂直 0-1" step="0.05" min="0" max="1" style="width:72px" value="${lay.transform ? lay.transform.y : ''}">
        <input type="number" id="ed-tf-rot" placeholder="旋转°" step="5" style="width:60px" value="${lay.transform && lay.transform.rotation ? lay.transform.rotation : ''}">
        <input type="number" id="ed-opacity" placeholder="透明度 0-1" step="0.1" min="0" max="1" style="width:84px" value="${lay.transform ? lay.transform.opacity : ''}">
        <input type="number" id="ed-scale" placeholder="缩放 0.1-3" step="0.1" style="width:72px" value="${lay.transform ? lay.transform.scale : ''}">
        <button class="btn-secondary btn-sm" onclick="editorSetVisualTransform('${sceneId}',${idx})">应用变换</button>
      </div>
      <div class="hint" style="font-size:11px">水平/垂直为画幅归一坐标（0.5=居中）。</div>
    </div>
    <div class="role-block" data-section="motion"><b>动效</b>
      <div class="hint">当前：${lay.motion_recipe_id ? esc(motionCnName(lay.motion_recipe_id)) : '无'}</div>
      <div class="btn-row">
        <button class="btn-secondary btn-sm" onclick="editorOpenMotionPicker('${sceneId}',${idx})">换动效</button>
        <button class="btn-secondary btn-sm" onclick="editorPreviewMotionParam('${sceneId}',${idx})">预览参数</button>
      </div>
      <label>动效时长（秒）</label>
      <div class="btn-row"><input type="number" id="ed-mot-dur" value="0.5" step="0.1" min="0.1" style="width:80px">
      <select id="ed-mot-easing" style="width:110px"><option value="">缓动（默认）</option>
      ${['linear','quad','cubic','quart','expo','back','sine'].map(e=>`<option value="${e}">${e}</option>`).join('')}</select>
      <button class="btn-sm" onclick="editorApplyMotionParam('${sceneId}',${idx})">应用参数</button></div>
      <div id="ed-motion-preview" style="margin-top:6px"></div>
    </div>
    ${lay.type === 'video' ? `
    <div class="role-block" data-section="pip"><b>画中画</b>
      ${((lay.motion_instance||{}).parameters||{}).pip
        ? `<div class="hint">已配置画中画（可在「变换」继续调整位置大小）</div>
           <div class="btn-row"><button class="btn-secondary btn-sm" onclick="editorRemovePip('${sceneId}',${idx})">取消画中画</button></div>`
        : `<div class="btn-row"><button class="btn-secondary btn-sm" onclick="editorApplyPip('${sceneId}',${idx})">设为画中画（右上角小窗）</button></div>`}
    </div>
    <div class="role-block" data-section="matting"><b>抠像</b>
      <div class="btn-row">
        <select id="ed-mat-color" style="width:100px"><option value="0x00FF00">绿幕</option><option value="0x0000FF">蓝幕</option></select>
        <button class="btn-secondary btn-sm" onclick="editorRunMatting('${sceneId}',${idx})">生成抠像</button>
      </div>
      <div class="hint" id="ed-mat-status">绿幕/蓝幕抠底，产物入素材库并替换本层。</div>
    </div>` : ''}
    <div class="role-block" data-section="mask"><b>蒙版</b>
      ${lay.mask ? `<div class="hint">当前：${maskShapeCn(lay.mask.shape)}${lay.mask.feather ? ' · 羽化 ' + lay.mask.feather : ''}${lay.mask.inverted ? ' · 反转' : ''}</div>` : ''}
      <div class="btn-row" style="flex-wrap:wrap">
        <select id="ed-mask-shape" style="width:100px"><option value="">形状…</option><option value="rectangle">矩形</option><option value="rounded_rect">圆角矩形</option><option value="ellipse">椭圆</option></select>
        <input type="number" id="ed-mask-feather" placeholder="羽化px" min="0" style="width:72px">
        <label style="display:flex;align-items:center;gap:4px;font-size:12px;margin:0"><input type="checkbox" id="ed-mask-invert"> 反转</label>
        <button class="btn-secondary btn-sm" onclick="editorApplyMask('${sceneId}',${idx})">应用蒙版</button>
        <button class="btn-secondary btn-sm" onclick="maskEditorOpen('${sceneId}',${idx})">编辑路径</button>
        ${lay.mask ? `<button class="btn-secondary btn-sm" onclick="editorCmd('remove_mask', {}, '${layerId}')">移除蒙版</button>` : ''}
      </div>
      ${MASK_ED.open ? `<div class="btn-row">
        <button class="btn-sm" onclick="maskEditorSave()">保存路径</button>
        <button class="btn-secondary btn-sm" onclick="maskEditorUndo()">撤销点</button>
        <button class="btn-secondary btn-sm" onclick="maskEditorRedo()">重做点</button></div>` : ''}
    </div>
    <div class="role-block" data-section="keyframes"><b>关键帧</b>
      <div id="ed-kf-list">${renderKeyframeList(lay, sceneId, idx)}</div>
      <div class="btn-row" style="flex-wrap:wrap">
        <select id="ed-kf-prop" style="width:96px"><option value="opacity">透明度</option><option value="scale">缩放</option><option value="x">水平</option><option value="y">垂直</option><option value="rotation">旋转</option></select>
        <input type="number" id="ed-kf-at" placeholder="时间秒" step="0.1" min="0" style="width:72px">
        <input type="number" id="ed-kf-val" placeholder="数值" step="0.1" style="width:64px">
        <button class="btn-secondary btn-sm" onclick="editorAddKeyframe('${sceneId}',${idx})">打关键帧</button>
      </div>
    </div>
    <div id="editor-picker-box"></div>`;
  if (lay.asset_id) loadAssetInspector(lay.asset_id);
}

// ---------------------------------------------------------------------------
// Scene Pair 上下文（§12：Morph/Continuity/Transition/Camera 的共同上下文）
// ---------------------------------------------------------------------------
function _inspectorScenePair(pairId){
  const box = document.getElementById('editor-inspector');
  const [fromId, toId] = pairId.split('->');
  const from = ED.comp.scenes.find(s => s.id === fromId);
  const to = ED.comp.scenes.find(s => s.id === toId);
  if (!from || !to) { clearSelection('inspector'); box.innerHTML = '<div class="hint">镜头关系对象已变化</div>'; return; }
  const cont = to.continuity_in;
  box.innerHTML = `
    <b>镜头关系</b>
    <div class="hint">${esc(fromId)} → ${esc(toId)}（${(from.text||'').slice(0,10)}… → ${(to.text||'').slice(0,10)}…）</div>
    <div class="role-block" data-section="relation">
      <div class="btn-row"><select id="ed-cont-type" style="width:120px" onchange="edContTypeChanged()">
        ${Object.entries(CONT_CN).map(([v, l]) => `<option value="${v}"${cont && cont.type === v ? ' selected' : ''}>${l}</option>`).join('')}
      </select>
      <button class="btn-secondary btn-sm" onclick="editorApplyContinuity('${toId}')">预览</button>
      <button class="btn-sm" onclick="editorApplyContinuity('${toId}')">应用</button></div>
      <div id="ed-morph-row" ${cont && cont.type === 'morph' ? '' : 'style="display:none"'}>
        ${_morphBlockRow(from, to)}
      </div>
    </div>
    <div class="role-block" data-section="continuity">
      <b>连续性检查</b>
      <div class="btn-row"><button class="btn-secondary btn-sm" onclick="editorVerifyContinuity('${toId}')">检查这一对镜头</button></div>
      <div id="ed-cont-verify"></div>
    </div>`;
}

// ---------------------------------------------------------------------------
// 多选聚合（§47 批量命令）
// ---------------------------------------------------------------------------
function _inspectorMulti(){
  const box = document.getElementById('editor-inspector');
  const sceneIds = ED.comp.scenes.filter(s => edIsSelected('scene', s.id)).map(s => s.id);
  const layerIds = [...SEL.selectionSet].filter(k => k.startsWith('layer:')).map(k => k.slice(6));
  box.innerHTML = `<b>已选 ${edSelectedCount()} 项</b>`+
    `<div class="hint">场景：${sceneIds.length} 个 · 图层：${layerIds.length} 个</div>`+
    `<div class="btn-row">`+
    `<button class="btn-secondary btn-sm" onclick="editorBatchCmd('duplicate')">批量复制场景</button>`+
    `<button class="btn-secondary btn-sm" onclick="editorBatchCmd('delete')">批量删除场景</button>`+
    `<button class="btn-secondary btn-sm" onclick="editorBatchCmd('lock')">批量锁定图层</button>`+
    `<button class="btn-secondary btn-sm" onclick="clearSelection()">取消选择</button></div>`;
}

// selection:changed → Inspector 自动同步（§18 双向契约）
document.addEventListener('selection:changed', ev => {
  if (ED.pid && document.getElementById('pane-editor').style.display !== 'none') {
    edShowInspector();
    const cnt = document.getElementById('editor-selection-count');
    if (cnt) cnt.textContent = SEL.selectionSet.size ? `已选 ${SEL.selectionSet.size} 项` : '未选中';
  }
});

// ---------------------------------------------------------------------------
// 场景/镜头操作（走 Selection Service 定位，全部 Command Bus 落 Revision）
// ---------------------------------------------------------------------------
async function loadAssetInspector(assetId){
  const el = document.getElementById('ed-asset-inspector');
  if (!el) return;
  try {
    const d = await apiGet('/api/asset_detail?asset_id=' + encodeURIComponent(assetId) + '&project_id=' + encodeURIComponent(ED.pid||''));
    if (!d || d.error) { el.textContent = ''; return; }
    const statusCn = {CLEAR:'可商用', REVIEW:'需复核', BLOCKED:'已阻断', UNKNOWN:'状态未知'};
    el.innerHTML = `<b>素材信息</b><br>来源：${esc(d.source||'—')} · 授权：<b>${statusCn[d.commercial_status]||esc(d.commercial_status)}</b>`+
      (d.license_note ? `<br>许可：${esc(d.license_note)}` : '') +
      (d.used_in && d.used_in.length ? `<br>用于：${esc(d.used_in.slice(0,3).join('、'))}` : '');
  } catch(e) { el.textContent = ''; }
}

function editorSetVisualTransform(sceneId, idx){
  const payload = {};
  const fields = [['ed-tf-x','x'],['ed-tf-y','y'],['ed-tf-rot','rotation'],['ed-opacity','opacity'],['ed-scale','scale']];
  for (const [id, key] of fields) {
    const el = document.getElementById(id);
    if (el && el.value !== '') payload[key] = parseFloat(el.value);
  }
  if (!Object.keys(payload).length) { editorToast('至少填一项（水平/垂直/旋转/透明度/缩放）'); return; }
  editorCmd('set_visual_transform', payload, `${sceneId}#${idx}`);
}

function renderKeyframeList(lay, sceneId, idx){
  if (!lay.keyframes || !lay.keyframes.length) return '<div class="hint">暂无关键帧</div>';
  const propCn = {opacity:'透明度', scale:'缩放', x:'水平', y:'垂直', rotation:'旋转', volume:'音量', blur:'模糊', mask_path:'蒙版路径', camera_x:'机位X', camera_y:'机位Y', camera_scale:'机位缩放'};
  return lay.keyframes.map(t =>
    `<div class="hint">${propCn[t.property]||t.property}：` +
    t.keyframes.map(k => `<span style="margin-right:6px">${k.at.toFixed(1)}s=${esc(String(k.value))}`+
      ` <a href="#" onclick="editorRemoveKeyframe('${sceneId}',${idx},'${t.property}',${k.at});return false" title="删除该帧">删</a></span>`).join('') +
    `</div>`).join('');
}

function editorAddKeyframe(sceneId, idx){
  const prop = document.getElementById('ed-kf-prop').value;
  const at = document.getElementById('ed-kf-at').value;
  const val = document.getElementById('ed-kf-val').value;
  if (at === '' || val === '') { editorToast('填时间（秒）和数值'); return; }
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
  if (!shape) { editorToast('先选蒙版形状'); return; }
  const payload = {shape: shape};
  const f = document.getElementById('ed-mask-feather');
  if (f && f.value !== '') payload.feather = parseFloat(f.value);
  const inv = document.getElementById('ed-mask-invert');
  if (inv && inv.checked) payload.inverted = true;
  editorCmd('apply_mask', payload, `${sceneId}#${idx}`);
}

async function editorRunMatting(sceneId, idx){
  const st = document.getElementById('ed-mat-status');
  if (st) st.textContent = '抠像处理中……';
  const r = await apiPost('/api/editor/matting', {
    project_id: ED.pid, target_id: `${sceneId}#${idx}`,
    color: (document.getElementById('ed-mat-color') || {}).value || '0x00FF00',
  });
  if (!r.ok) { if (st) st.textContent = (r.error || '抠像失败'); return; }
  if (st) st.textContent = `抠像完成（${r.asset_id}），已替换当前层素材`;
  editorCmd('replace_source', {assetId: r.asset_id}, `${sceneId}#${idx}`);
}

// §9.3 内联输入组件（替代 prompt()）
const _UI_CN_ANCHORS = [['top_left','左上'],['top_right','右上'],['bottom_left','左下'],['bottom_right','右下'],['center','居中']];
function uiInlineInput(hostId, opts, applyFn){
  const host = document.getElementById(hostId);
  if (!host) { editorToast('界面元素缺失，请刷新页面'); return; }
  const id = hostId + '-inline';
  const exist = document.getElementById(id);
  if (exist) { exist.remove(); return; }
  const row = document.createElement('div');
  row.id = id;
  row.className = 'btn-row';
  row.style.cssText = 'margin-top:6px;align-items:center;gap:6px;flex-wrap:wrap';
  if (opts.type === 'select') {
    row.innerHTML = `<select id="${id}-v" style="width:110px">`+
      opts.options.map(([v, l]) => `<option value="${esc(v)}"${v === (opts.def ?? opts.options[0][0]) ? ' selected' : ''}>${esc(l)}</option>`).join('')+
      `</select><button class="btn-sm">应用</button>`;
  } else {
    row.innerHTML = `<input type="number" id="${id}-v" value="${esc(String(opts.def))}" min="${opts.min}" max="${opts.max}" step="${opts.step || 0.1}" style="width:90px">`+
      (opts.unit ? `<span class="hint" style="margin:0">${esc(opts.unit)}</span>` : '')+
      `<button class="btn-sm">应用</button>`;
  }
  host.appendChild(row);
  window._uiInlineApplies[id] = (v) => { row.remove(); delete window._uiInlineApplies[id]; applyFn(v); };
  const btn = row.querySelector('button');
  const input = row.querySelector('#' + id + '-v');
  btn.onclick = () => { window._uiInlineApplies[id](opts.type === 'number' ? parseFloat(input.value) : input.value); };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') btn.click();
    if (e.key === 'Escape') { row.remove(); delete window._uiInlineApplies[id]; }
  });
  input.focus();
  if (input.select) input.select();
}
window._uiInlineApplies = {};
function _ensureInlineHost(hostId){
  if (document.getElementById(hostId)) return;
  const btn = document.activeElement;
  const span = document.createElement('span');
  span.id = hostId;
  span.style.display = 'inline-block';
  if (btn && btn.parentElement) btn.parentElement.appendChild(span);
}

function editorToggleLock(sceneId, idx){
  editorCmd('lock', {locked: undefined}, `${sceneId}#${idx}`);
}
function editorAudioVolume(trackType){
  const hostId = 'ed-audio-volume-host';
  _ensureInlineHost(hostId);
  uiInlineInput(hostId, {type: 'number', def: 1.0, min: 0, max: 2, step: 0.1, unit: '倍'},
    (v) => { if (!isNaN(v)) editorCmd('set_audio_volume', {volume: v}, trackType, true); });
}

// 项目级：声音与字幕面板
async function edVoiceLoadOptions(){
  const sel = document.getElementById('ed-voice-sel');
  if (!sel || sel.options.length) return;
  try {
    const voices = await apiGet('/api/voices');
    const list = Array.isArray(voices) ? voices : (voices.voices || []);
    sel.innerHTML = list.slice(0, 20).map(v =>
      `<option value="${esc(v.voice_id || v.id || v)}">${esc(v.name || v.label || v.voice_id || v)}</option>`).join('');
  } catch (e) { /* 下拉空态保留 */ }
}
async function edVoicePreview(){
  const sel = document.getElementById('ed-voice-sel');
  const st = document.getElementById('ed-voice-status');
  if (!sel || !sel.value) { if (st) st.textContent = '先选音色'; return; }
  if (st) st.textContent = '试听播放中……';
  try {
    const audio = new Audio('/api/voice_preview?voice=' + encodeURIComponent(sel.value));
    audio.play();
    if (st) st.textContent = '试听播放中';
  } catch (e) {
    if (st) st.textContent = '试听失败：' + e;
  }
}
function editorOriginalAudio(mode){
  editorCmd('set_original_audio', {mode, volume: mode === 'preserve' ? 0.8 : 0.0, duckAgainst: 'narration'}, '', true);
}
function editorOriginalAudioVolume(){
  const hostId = 'ed-origvol-host';
  _ensureInlineHost(hostId);
  uiInlineInput(hostId, {type: 'number', def: 0.8, min: 0, max: 2, step: 0.1, unit: '倍'},
    (v) => { if (!isNaN(v)) editorCmd('set_original_audio_volume', {volume: v}, '', true); });
}
async function editorWatermarkAdd(){
  const hostId = 'editor-watermark-list';
  const box = document.getElementById(hostId);
  if (!box) { editorToast('界面元素缺失，请刷新页面'); return; }
  const id = hostId + '-inline';
  if (document.getElementById(id)) { document.getElementById(id).remove(); return; }
  const row = document.createElement('div');
  row.id = id;
  row.className = 'btn-row';
  row.style.cssText = 'margin-top:6px;align-items:center;gap:6px;flex-wrap:wrap';
  row.innerHTML =
    `<input type="text" id="${id}-text" placeholder="水印文字（例：品牌名）" style="width:130px">`+
    `<select id="${id}-anchor" style="width:86px">`+
    _UI_CN_ANCHORS.map(([v, l]) => `<option value="${v}"${v === 'top_right' ? ' selected' : ''}>${l}</option>`).join('')+
    `</select><button class="btn-sm">加水印</button>`;
  box.appendChild(row);
  const btn = row.querySelector('button');
  btn.onclick = () => {
    const text = row.querySelector('#' + id + '-text').value.trim();
    const anchor = row.querySelector('#' + id + '-anchor').value;
    row.remove();
    if (!text) { editorToast('水印文字为空，未添加'); return; }
    editorCmd('set_watermark', {kind: 'text', text, anchor, opacity: 0.75}, '', true);
  };
  row.querySelector('#' + id + '-text').focus();
}
async function editorWatermarkRemove(){
  editorCmd('remove_watermark', {}, '', true);
}
function renderWatermarkList(){
  const box = document.getElementById('editor-watermark-list');
  if (!box) return;
  const wms = (ED.comp && ED.comp.watermarks) || [];
  if (!wms.length) { box.textContent = '当前无水印'; return; }
  const anchorCn = Object.fromEntries(_UI_CN_ANCHORS);
  box.innerHTML = wms.map((w, i) =>
    `<div class="proj-row"><span><b>${esc(w.text || w.asset_id || w.kind)}</b>`+
    `<span class="hint">${anchorCn[w.anchor] || esc(w.anchor || '')} · 透明 ${w.opacity ?? '—'}</span></span>`+
    `<span class="btn-row">`+
    `<button class="btn-secondary btn-sm" onclick="editorWatermarkMove(${i})">移位</button>`+
    `<button class="btn-secondary btn-sm" onclick="editorWatermarkOpacity(${i})">透明度</button>`+
    `<button class="btn-secondary btn-sm" onclick="editorCmd('remove_watermark',{},'',true)">删</button></span></div>`).join('');
}
function editorWatermarkMove(i){
  const hostId = 'editor-watermark-list';
  const w = (ED.comp.watermarks || [])[i] || {};
  uiInlineInput(hostId, {type: 'select', options: _UI_CN_ANCHORS, def: w.anchor || 'top_right'},
    (v) => editorCmd('set_watermark', {kind: w.kind || 'text', text: w.text || '水印',
                                       anchor: v, opacity: w.opacity ?? 0.75}, '', true));
}
function editorWatermarkOpacity(i){
  const hostId = 'editor-watermark-list';
  const w = (ED.comp.watermarks || [])[i] || {};
  uiInlineInput(hostId, {type: 'number', def: 0.75, min: 0, max: 1, step: 0.05},
    (v) => { if (!isNaN(v)) editorCmd('set_watermark',
      {kind: w.kind || 'text', text: w.text || '水印',
       anchor: w.anchor || 'top_right', opacity: v}, '', true); });
}
async function editorSubtitleStyleApply(){
  const payload = {};
  const mode = document.getElementById('sub-mode');
  const size = document.getElementById('sub-size');
  const color = document.getElementById('sub-color');
  const pos = document.getElementById('sub-pos');
  if (mode && mode.value) payload.mode = mode.value;
  if (size && size.value) payload.size = parseFloat(size.value);
  if (color && color.value) payload.color = color.value;
  if (pos && pos.value) payload.position = pos.value;
  if (!Object.keys(payload).length) { editorToast('先调整样式字段'); return; }
  editorCmd('set_subtitle_style', payload, '', true);
}
async function editorSubtitleGenerate(){
  const r = await apiPost('/api/director/execute', {
    project_id: ED.pid,
    instruction: '生成字幕（word 模式）',
  });
  if (r.ok) {
    const d = r.director || {};
    const gen = (d.executed||[]).find(e => e.tool === 'subtitle.generate');
    editorToast(gen && gen.ok ? '字幕轨已生成' : '字幕轨生成失败：' + ((gen||{}).error||''));
  } else {
    editorToast((r.error || ''), true);
  }
}
async function editorBgmVolume(){
  const hostId = 'ed-bgmvol-host';
  _ensureInlineHost(hostId);
  uiInlineInput(hostId, {type: 'number', def: 0.3, min: 0, max: 2, step: 0.1, unit: '倍'},
    (v) => { if (!isNaN(v)) editorCmd('set_audio_volume', {volume: v}, 'bgm', true); });
}

// EDT-007 动效参数：本地预览 → 应用（Revision）
async function editorPreviewMotionParam(sceneId, idx){
  const dur = parseFloat((document.getElementById('ed-mot-dur')||{}).value) || 0.5;
  const box = document.getElementById('ed-motion-preview');
  if (!box) return;
  box.innerHTML = `<div class="motion-preview" style="animation:mpPreview ${Math.max(0.4,Math.min(2.5,dur))}s ease-in-out infinite alternate">`+
    `<div class="hint">预览时长 ${dur}s（本地预览，未写入项目）</div></div>`;
}
async function editorApplyMotionParam(sceneId, idx){
  const dur = parseFloat((document.getElementById('ed-mot-dur')||{}).value);
  const easing = (document.getElementById('ed-mot-easing')||{}).value;
  const payload = {};
  if (dur && dur > 0.1) payload.duration = dur;
  if (easing) payload.parameters = {easing: easing};
  editorCmd('set_motion_parameter', payload, `${sceneId}#${idx}`);
}

async function editorOpenAssetPicker(sceneId, idx, intent){
  AP.intent = intent || 'replace';
  const box = document.getElementById('editor-picker-box');
  box.innerHTML =
    '<div class="btn-row" style="margin-bottom:6px;flex-wrap:wrap">'+
    (AP.intent === 'addbroll' ? '<span class="hint">选择要加入的素材：</span>' : '')+
    '<button class="btn-secondary btn-sm" data-aptab="project">项目素材</button>'+
    '<button class="btn-secondary btn-sm" data-aptab="local">本地导入</button>'+
    '<button class="btn-secondary btn-sm" data-aptab="recent">最近使用</button>'+
    '<button class="btn-secondary btn-sm" data-aptab="favorites">收藏</button>'+
    '<button class="btn-secondary btn-sm" data-aptab="stock">图库</button>'+
    '<button class="btn-secondary btn-sm" data-aptab="generated">AI 生成</button></div>'+
    '<div class="btn-row" style="margin-bottom:6px;flex-wrap:wrap;font-size:12px" id="ap-filters">'+
    '<span class="hint">筛选：</span>'+
    '<select id="ap-f-type" style="width:90px"><option value="">全部</option><option value="video">视频</option><option value="image">图片</option></select>'+
    '<label><input type="checkbox" id="ap-f-clear"> 只看可商用素材</label>'+
    '</div>'+
    '<div id="asset-picker-body">加载素材……</div>';
  box.querySelectorAll('[data-aptab]').forEach(btn => {
    btn.onclick = () => assetPickerTab(btn.getAttribute('data-aptab'), idx, sceneId);
  });
  ['ap-f-type', 'ap-f-clear'].forEach(fid => {
    const el = document.getElementById(fid);
    if (el) el.onchange = () => assetPickerTab(AP.lastTab || 'project', idx, sceneId);
  });
  assetPickerTab('project', idx, sceneId);
}

// ---------------------------------------------------------------------------
// §13/§47 镜头关系（Camera/Continuity/Morph/Transition 唯一入口）
// ---------------------------------------------------------------------------
const CAMERA_CN = {push_in:'缓慢推进', pull_out:'缓慢拉远', pan_left:'向左平移', pan_right:'向右平移',
  tilt_up:'向上摇', tilt_down:'向下摇', follow:'跟拍'};

async function editorApplyCamera(sceneId, preset){
  const r = await executeCommand({command: 'apply_camera', targetId: sceneId,
    payload: {preset, strength: 0.6}});
  if (r.ok) {
    editorToast(`镜头运动已应用：${CAMERA_CN[preset] || preset}`);
    refreshWorkspace();
  } else {
    editorToast('镜头应用失败：' + (r.error || ''), true);
  }
}

async function editorApplyContinuity(sceneId){
  const type = (document.getElementById('ed-cont-type') || {}).value || 'hard_cut';
  if (type === 'morph') {
    const fromBlock = (document.getElementById('ed-morph-from') || {}).value || '';
    const toBlock = (document.getElementById('ed-morph-to') || {}).value || '';
    const pairSel = SEL.selection && SEL.selection.type === 'scene_pair'
      ? SEL.selection.id.split('->') : null;
    const prevSceneId = pairSel ? pairSel[0]
      : (() => { const i = ED.comp.scenes.findIndex(s => s.id === sceneId);
                 return i > 0 ? ED.comp.scenes[i-1].id : ''; })();
    await editorApplyMorph(prevSceneId, sceneId, fromBlock, toBlock);
    return;
  }
  const payload = {type};
  if (type === 'carry') {
    const src = (document.getElementById('ed-cont-src') || {}).value || '';
    const dst = (document.getElementById('ed-cont-dst') || {}).value || '';
    if (src) payload.sourceLayerId = src;
    if (dst) payload.targetLayerId = dst;
  }
  const r = await executeCommand({command: 'set_scene_continuity', targetId: sceneId, payload});
  if (r.ok) {
    editorToast(`镜头关系已设置：${CONT_CN[type] || type}`);
    refreshWorkspace();
  } else {
    editorToast('镜头关系设置失败：' + (r.error || ''), true);
  }
}

function edContTypeChanged(){
  const type = (document.getElementById('ed-cont-type') || {}).value;
  const morphRow = document.getElementById('ed-morph-row');
  if (morphRow) morphRow.style.display = (type === 'morph') ? '' : 'none';
}

// 自有 Graphic Block 形变映射（与后端 MORPHABLE_BLOCKS 一致，前端只做候选展示）
const MORPH_BLOCKS = {
  'ui.button': { label: '按钮', targets: ['ui.arrow','flow.node'] },
  'ui.arrow': { label: '箭头', targets: ['ui.button','flow.node'] },
  'flow.node': { label: '流程节点', targets: ['flow.arrow','ui.card'] },
  'flow.arrow': { label: '流程箭头', targets: ['flow.node'] },
  'ui.card': { label: '卡片', targets: ['flow.node','metric.card'] },
  'metric.card': { label: '指标卡', targets: ['ui.card','chart.node'] },
  'chart.node': { label: '图表节点', targets: ['metric.card','flow.node'] },
};
function _blockCn(id){ const b = MORPH_BLOCKS[id]; return b ? b.label : (id || ''); }
function _graphicBlockOf(scene){
  if (!scene || !scene.layers) return null;
  const g = scene.layers.find(l => l.type === 'graphic');
  if (!g) return null;
  const gp = ((g.motion_instance || {}).parameters || {}).graphic;
  return (gp && gp.block_id) || null;
}
function _morphBlockRow(prevScene, curScene){
  const fromCur = _graphicBlockOf(prevScene);
  const toCur = _graphicBlockOf(curScene);
  const fromOpts = Object.keys(MORPH_BLOCKS).map(id =>
    `<option value="${id}"${id === fromCur ? ' selected' : ''}>${_blockCn(id)}</option>`).join('');
  const fromId = fromCur || 'ui.button';
  const targets = (MORPH_BLOCKS[fromId] || {}).targets || Object.keys(MORPH_BLOCKS);
  const toOpts = targets.map(id =>
    `<option value="${id}"${id === toCur ? ' selected' : ''}>${_blockCn(id)}</option>`).join('');
  return `<div class="hint">形变过渡：当前图形 → 目标图形（自有图形块间结构化形变）</div>`+
    `<div class="btn-row"><select id="ed-morph-from" style="width:120px" onchange="edMorphFromChanged()">${fromOpts}</select>`+
    `<span class="hint">→</span>`+
    `<select id="ed-morph-to" style="width:120px">${toOpts}</select></div>`;
}
function edMorphFromChanged(){
  const fromId = (document.getElementById('ed-morph-from') || {}).value || 'ui.button';
  const targets = (MORPH_BLOCKS[fromId] || {}).targets || Object.keys(MORPH_BLOCKS);
  const toSel = document.getElementById('ed-morph-to');
  if (toSel) toSel.innerHTML = targets.map(id => `<option value="${id}">${_blockCn(id)}</option>`).join('');
}

async function editorApplyMorph(prevSceneId, sceneId, fromB, toB){
  fromB = fromB || (document.getElementById('ed-morph-from') || {}).value || '';
  toB = toB || (document.getElementById('ed-morph-to') || {}).value || '';
  if (!fromB || !toB) { editorToast('请选择形变前后的图形块', true); return; }
  if (!prevSceneId || !sceneId) { editorToast('形变需要两个相邻镜头（在后一镜头上设置）', true); return; }
  const r = await executeCommand({
    command: 'apply_morph', targetId: `${prevSceneId}->${sceneId}`,
    payload: {fromBlockId: fromB, toBlockId: toB, duration: 0.6},
  });
  if (r.ok) {
    editorToast(`形变过渡已应用：${_blockCn(fromB)} → ${_blockCn(toB)}`);
    refreshWorkspace();
  } else {
    editorToast('形变应用失败：' + (r.error || ''), true);
  }
}

// §4.3 连续性验证（四态，零评分）
async function editorVerifyContinuity(sceneId){
  const box = document.getElementById('ed-cont-verify');
  if (!box) return;
  box.innerHTML = '连续性检查中……';
  const r = await apiGet('/api/continuity?project_id=' + encodeURIComponent(ED.pid));
  if (r.error) { box.innerHTML = `<div class="err-box">${esc(r.error)}</div>`; return; }
  const statusCN = {PASS:{t:'通过',cls:'cap-ok'}, WARNING:{t:'有提示',cls:'cap-warn'},
                   REJECT:{t:'不通过',cls:'cap-off'}, UNKNOWN:{t:'未知',cls:'cap-off'}};
  const s = (r.status || 'UNKNOWN').toUpperCase();
  const st = statusCN[s] || statusCN.UNKNOWN;
  const issues = (r.issues || []).filter(i => !sceneId || i.scene_id === sceneId || i.from_scene_id === sceneId);
  const issueCN = {CARRY_MISSING_SOURCE:'元素延续缺来源层', CARRY_MISSING_TARGET:'元素延续缺目标层',
    CAMERA_PATH_MISSING:'镜头穿越缺路径', MORPH_PATH_MISSING:'形变缺路径',
    STATIC_REPLACEMENT:'静态画面连续替换', ANCHOR_JUMP:'延续元素锚点跳变'};
  box.innerHTML = `<div class="hint"><b class="${st.cls}">${st.t}</b> · ${sceneId ? '当前镜头' : '全片'} · ${issues.length} 条`+
    (issues.length ? '</div>' + issues.map(i =>
      `<div class="hint" style="margin:2px 0 2px 14px">· ${esc(issueCN[i.issue_code] || i.issue_code)}：${esc(i.message || '')}</div>`).join('')
      : '</div><div class="hint" style="margin-left:14px">未发现问题。</div>');
}

// §23 画中画
async function editorApplyPip(sceneId, idx){
  const aid = (lay0 => lay0 && lay0.asset_id)(ED.comp.scenes.find(s => s.id === sceneId)?.layers[idx]);
  const r = await executeCommand({command: 'configure_pip',
    targetId: `${sceneId}#${idx}`,
    payload: {action: 'set', source: aid || '', fit: 'contain', radius: 12, shadow: true,
              transform: {x: 0.78, y: 0.22, scale: 0.35}, audioPolicy: 'auto'}});
  if (r.ok) {
    editorToast('已配置为画中画（可在「变换」组继续调整位置大小）');
    refreshWorkspace();
  } else {
    editorToast('画中画配置失败：' + (r.error || ''), true);
  }
}
async function editorRemovePip(sceneId, idx){
  const r = await executeCommand({command: 'configure_pip',
    targetId: `${sceneId}#${idx}`, payload: {action: 'remove'}});
  if (r.ok) { editorToast('已取消画中画'); refreshWorkspace(); }
  else editorToast('取消失败：' + (r.error || ''), true);
}

// 裁剪/速度
async function editorApplyTrim(sceneId, idx){
  const srcIn = parseFloat((document.getElementById('ed-trim-in') || {}).value);
  const srcOut = parseFloat((document.getElementById('ed-trim-out') || {}).value);
  if (isNaN(srcIn) || isNaN(srcOut) || srcOut <= srcIn) {
    editorToast('裁剪需填起点和终点秒数（终点需大于起点）', true); return;
  }
  const r = await executeCommand({command: 'trim',
    targetId: `${sceneId}#${idx}`, payload: {sourceIn: srcIn, sourceOut: srcOut}});
  if (r.ok) { editorToast(`已裁剪为源区间 ${srcIn}s–${srcOut}s`); refreshWorkspace(); }
  else editorToast('裁剪失败：' + (r.error || ''), true);
}
async function editorApplySpeed(sceneId, idx){
  const speed = parseFloat((document.getElementById('ed-speed') || {}).value);
  if (!(speed > 0)) { editorToast('速度需为正数', true); return; }
  const r = await executeCommand({command: 'set_transform',
    targetId: `${sceneId}#${idx}`, payload: {speed}});
  if (r.ok) { editorToast(`速度已改为 ${speed}x`); refreshWorkspace(); }
  else editorToast('速度调整失败：' + (r.error || ''), true);
}

// §8 拆分导出：inline onclick/window 引用兼容
Object.assign(window, {
  layerTypeCn, edShowInspector, resolveInspector, inspectorOpenSection,
  loadAssetInspector, editorSetVisualTransform,
  editorApplyCamera, editorApplyContinuity, edContTypeChanged,
  edMorphFromChanged, editorApplyMorph, editorVerifyContinuity,
  editorApplyPip, editorRemovePip, editorApplyTrim, editorApplySpeed,
  renderKeyframeList, editorAddKeyframe, editorRemoveKeyframe,
  maskShapeCn, editorApplyMask, editorRunMatting,
  editorToggleLock, uiInlineInput, _ensureInlineHost, editorAudioVolume,
  edVoiceLoadOptions, edVoicePreview, editorOriginalAudio, editorOriginalAudioVolume,
  editorWatermarkAdd, editorWatermarkRemove, renderWatermarkList,
  editorWatermarkMove, editorWatermarkOpacity,
  editorSubtitleStyleApply, editorSubtitleGenerate, editorBgmVolume,
  editorPreviewMotionParam, editorApplyMotionParam, editorOpenAssetPicker,
  CONT_CN, MORPH_BLOCKS,
});
