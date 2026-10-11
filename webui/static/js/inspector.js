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

/** §62/§16 Inspector 消费 /api/actions：当前上下文可执行动作 + Availability。
 *  §63 不可用 → 按钮禁用但显示原因 + 下一步（不是哑灰）。 */
async function inspectorActionsBox(){
  if (!ED.pid) return '';
  const sel = SEL.selection || {type: 'project'};
  let layerType = '';
  if (sel.type === 'layer') {
    const [sceneId, idxS] = (sel.id || '').split('#');
    const sc = (ED.comp.scenes || []).find(s => s.id === sceneId);
    const lay = sc && sc.layers[parseInt(idxS)];
    layerType = lay ? lay.type : '';
  }
  try {
    const r = await apiGet(`/api/actions?project_id=${encodeURIComponent(ED.pid)}` +
      `&selection_type=${encodeURIComponent(sel.type || 'project')}` +
      `&selection_id=${encodeURIComponent(sel.id || '')}` +
      `&layer_type=${encodeURIComponent(layerType)}`);
    if (!r || !r.actions) return '';
    const avail = r.actions.filter(a => a.available);
    const blocked = r.actions.filter(a => !a.available);
    return `<div class="hint" style="margin-top:4px">可执行 ${avail.length} 个动作`+
      (blocked.length ? ` · 不可用 ${blocked.length} 个（原因见悬停）` : '') + '</div>';
  } catch (e) { return ''; }
}

// ---------------------------------------------------------------------------
// §5/§6/§7 Project 上下文（叠加层=水印/Logo + 全局声音策略 + 字幕策略）
// ---------------------------------------------------------------------------
function _inspectorProject(){
  const box = document.getElementById('editor-inspector');
  const c = ED.comp;
  if (!c) { box.innerHTML = '<div class="hint">打开项目后显示项目属性。</div>'; return; }
  const wms = c.watermarks || [];
  const anchorCn = Object.fromEntries(_UI_CN_ANCHORS);
  const sub = c.subtitle_style || {};
  const narr = (c.audio || []).some(a => a.type === 'narration');
  const orig = (c.audio || []).find(a => a.type === 'original');
  box.innerHTML = `
    <b>项目</b>
    <div class="role-block" data-section="overview"><b>概况</b>
      <div class="hint">${c.scenes.length} 场景 · 总长 ${c.duration.toFixed(1)}s · 画幅 ${c.width}×${c.height}</div></div>
    <div class="role-block" data-section="overlays"><b>叠加层（Logo / 水印）</b>
      ${wms.length ? wms.map((w, i) => `
        <div class="proj-row"><span><b>${esc(w.text || w.asset_id || '水印')}</b>
          <span class="hint">${anchorCn[w.anchor] || esc(w.anchor || '')} · 透明 ${w.opacity ?? '—'}</span></span>
          <span class="btn-row">
            <button class="btn-secondary btn-sm" onclick="editorWatermarkMove(${i})">调整</button>
            <button class="btn-secondary btn-sm" onclick="editorWatermarkRemoveAt(${i})">删除</button>
          </span></div>`).join('') : '<div class="hint">还没有 Logo / 水印</div>'}
      <div class="btn-row" style="flex-wrap:wrap">
        <button class="btn-secondary btn-sm" data-action="watermark.add" data-target="project" title="添加品牌 Logo / 文字水印">添加 Logo / 水印</button>
        <button class="btn-secondary btn-sm" data-action="watermark.remove" data-target="project" ${wms.length ? '' : 'disabled title="当前没有水印"'}>移除水印</button>
      </div>
    </div>
    <div class="role-block" data-section="audio"><b>声音策略</b>
      <div class="hint">旁白：${narr ? '已生成' : '无'} · 原声：${orig ? (orig.muted ? '已静音' : (orig.volume * 100).toFixed(0) + '%') : '未启用'}</div>
      <div class="btn-row" style="flex-wrap:wrap">
        <button class="btn-secondary btn-sm" data-action="ui.open_voice_settings">配音设置</button>
        <button class="btn-secondary btn-sm" data-action="ui.open_audio_bus">整体调音</button>
      </div>
    </div>
    <div class="role-block" data-section="subtitle"><b>字幕策略</b>
      <div class="hint">${c.subtitle_track && c.subtitle_track.cues.length
        ? `字幕轨：${c.subtitle_track.cues.length} 条（${c.subtitle_track.mode === 'word' ? '逐词' : '句子'}）` : '无字幕轨'}${sub.color ? ' · 样式已设' : ''}</div>
      <div class="btn-row" style="flex-wrap:wrap">
        <button class="btn-secondary btn-sm" data-action="ui.open_subtitle_style">字幕样式</button>
        <button class="btn-secondary btn-sm" data-action="subtitle.generate" data-target="project">生成字幕轨</button>
      </div>
    </div>
    <div class="role-block" data-section="version"><b>版本</b>
      <div class="hint">当前 V${c.version} · 顶栏 V 号可看历史</div></div>
    <div class="btn-row">
      <button class="btn-secondary btn-sm" data-action="ui.preview_draft">预览</button>
      <button class="btn-secondary btn-sm" data-action="verify.run" data-target="project">发布预检</button>
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
  const mainLayer = (sc.layers || []).find(l => l.type === 'video' || l.type === 'image');
  const mainName = mainLayer ? (mainLayer.asset_id || '程序化画面') : '无主画面';
  box.innerHTML = `
    <b>场景 ${si + 1} · ${esc(sc.id)}</b>
    <div class="role-block" data-section="content"><b>内容</b>
      <div class="hint">${esc(sc.text || '')}</div>
      <div class="btn-row"><input type="number" id="ed-dur" value="${sc.duration.toFixed(1)}" step="0.5" style="width:80px" title="场景时长（秒）">
      <button class="btn-secondary btn-sm" onclick="editorApplyDuration('${sc.id}')">应用时长</button></div>
      <div class="hint">${sc.origin_recipe_id ? '镜头方案：' + esc(shotCnName(sc.origin_recipe_id)) : '自由镜头'}</div>
    </div>
    <div class="role-block" data-section="visual"><b>画面</b>
      <div class="hint">当前素材：${esc(mainName)}</div>
      <div class="btn-row" style="flex-wrap:wrap">
        <button class="btn-secondary btn-sm" data-action="ui.open_asset_picker">换素材</button>
        <button class="btn-secondary btn-sm" data-action="asset.search" data-target="project">自动找素材</button>
        <button class="btn-secondary btn-sm" data-action="ui.generate_scene_visual">AI 生成</button>
        <button class="btn-secondary btn-sm" data-action="ui.open_shot_picker">换镜头</button>
        <button class="btn-secondary btn-sm" data-action="visual.camera" data-params='{"preset":"push_in"}'>镜头推进</button>
        <button class="btn-secondary btn-sm" data-action="visual.camera" data-params='{"preset":"pull_out"}'>镜头拉远</button>
      </div>
      <div id="editor-picker-box"></div><div class="role-block" data-section="mpt-material">
        <b>素材依据</b>
        <div id="mpt-scene-context" class="hint">正在读取本镜素材检索与使用记录……</div>
        <div class="btn-row" style="flex-wrap:wrap">
          <button class="btn-secondary btn-sm" data-action="asset.search" data-target="project">按本镜语义重新找素材</button>
        </div>
      </div>
      ${_sceneGenBox(sc.id)}
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
      <div class="hint">本镜文案时长 ${sc.duration.toFixed(1)}s${mainLayer && mainLayer.type === 'video' ? ' · 主素材含视频（可保留原声）' : ''}</div>
      ${mainLayer && mainLayer.type === 'video' ? `
      <div class="btn-row" style="flex-wrap:wrap">
        <button class="btn-secondary btn-sm" data-action="audio.preserve">保留本镜原声</button>
        <button class="btn-secondary btn-sm" data-action="audio.mute_original">静音本镜原声</button>
      </div>` : ''}
    </div>`;
  _loadMptSceneContext(sc.id);
}

/** §8 生成入 Scene→画面：场景内联生成区（不再有页面底部「生成画面」面板）。 */
async function _loadMptSceneContext(sceneId){
  const box = document.getElementById('mpt-scene-context');
  if (!box || !ED.pid) return;
  try {
    const ctx = await apiGet('/api/director/context?project_id=' + encodeURIComponent(ED.pid));
    const mpt = (ctx && ctx.mpt) || {};
    const intent = (mpt.material_intents || []).find(x => x.scene_id === sceneId);
    const usage = (mpt.material_usage || []).filter(x => x.scene_id === sceneId);
    if (!intent) {
      box.innerHTML = '暂无素材意图记录。首次完成 Director 创作后会自动建立。';
      return;
    }
    const terms = (intent.search_terms || []).map(function(t){
      return '<span style="display:inline-block;padding:2px 6px;border:1px solid var(--border);border-radius:10px;margin:2px 3px 2px 0">' + esc(t) + '</span>';
    }).join('');
    const candidateRow = (mpt.creative_candidates || []).find(function(x){ return x.scene_id === sceneId; });
    const candidates = (candidateRow && candidateRow.candidates) || intent.candidates || [];
    const selectedCandidate = (candidateRow && candidateRow.selected_candidate_id) ||
      intent.selected_candidate_id || '';
    function candidateParam(candidate){
      return JSON.stringify({
        candidateId: candidate.id,
        visualIntent: candidate.visual_intent,
        approach: candidate.approach || '',
        sourcePreference: candidate.source_preference || '',
      }).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/'/g, '&#39;');
    }
    const candidateHtml = candidates.map(function(candidate){
      const selected = candidate.id === selectedCandidate;
      const sourceText = {
        user_asset: '优先用我的素材',
        stock: '按检索词找素材',
        generated: 'AI 生成画面',
        reference_style: '参考风格',
        software_evidence: '项目证据'
      }[candidate.source_preference] || candidate.source_preference || '未指定';
      const termPreview = (candidate.search_terms || []).slice(0, 3)
        .map(function(t){ return '<span class="hint">' + esc(t) + '</span>'; }).join(' · ');
      const statusText = {
        READY: '已有可用素材',
        INTENT_ONLY: '方案已生成，待执行',
        APPLIED: '已采用',
        FAILED: '执行失败',
      }[candidate.status] || candidate.status || '待执行';
      const executeHtml = candidate.source_preference === 'stock'
        ? '<button class="btn-secondary btn-sm" data-action="asset.search_stock" data-target="scene" data-params=\'' +
          JSON.stringify({query: (candidate.search_terms || [])[0] || ''}).replace(/&/g, '&amp;').replace(/'/g, '&#39;') + '\'>找素材</button>'
        : candidate.source_preference === 'generated'
          ? '<button class="btn-secondary btn-sm" data-action="ui.generate_scene_visual" data-params=\'' +
            JSON.stringify({
              prompt: candidate.generation_prompt || candidate.visual_intent || '',
              type: candidate.generation_type || 'image'
            }).replace(/&/g, '&amp;').replace(/'/g, '&#39;') + '\'>准备生成</button>'
          : '<button class="btn-secondary btn-sm" data-action="ui.open_asset_picker">选我的素材</button>';
      return '<div style="border:1px solid ' + (selected ? 'var(--accent)' : 'var(--border)') +
        ';border-radius:6px;padding:8px;margin:6px 0;background:var(--panel)">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px">' +
        '<b>' + esc(candidate.title || candidate.id) + '</b>' +
        '<div class="btn-row" style="gap:5px">' +
        '<button class="btn-secondary btn-sm" data-action="creative.select_candidate" data-target="scene" ' +
        'data-params=\'' + candidateParam(candidate) + '\'' + (selected ? ' disabled' : '') + '>' +
        (selected ? '当前方案' : '采用') + '</button>' +
        executeHtml +
        '</div>' +
        '</div>' +
        '<div style="margin-top:4px">' + esc(candidate.visual_intent || '') + '</div>' +
        '<div style="margin-top:4px">' + esc(sourceText) + (termPreview ? ' · ' + termPreview : '') + '</div>' +
        '<div class="hint" style="margin-top:4px">状态：' + esc(statusText) + '</div>' +
        '</div>';
    }).join('');
    const windows = usage.length
      ? usage.map(function(u){
          return esc(u.asset_id) + ' · ' + Number(u.source_in || 0).toFixed(1) + 's → ' +
            Number(u.source_out || 0).toFixed(1) + 's';
        }).join('<br>')
      : '尚未形成 source-window 使用记录';
    box.innerHTML =
      '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><b>创作方案</b> <span class="hint">先选表达方式，再决定用什么素材</span>' +
      '<button class="btn-secondary btn-sm" onclick="editorSemanticRerank(\'' + sceneId + '\')">按画面语义重排候选</button></div>' +
      '<div style="margin-top:4px">' + (candidateHtml || '<span class="hint">暂无候选方案；重新执行 Director 后建立。</span>') + '</div>' +
      '<div style="margin-top:8px;border-top:1px solid var(--border);padding-top:7px">检索词（按叙事顺序）</div>' +
      '<div style="margin:3px 0 6px">' + (terms || '<span class="hint">未记录</span>') + '</div>' +
      '<div>目标时长：' + Number(intent.target_duration || 0).toFixed(1) + 's' +
      ' · 单段上限：' + (intent.max_clip_duration ? Number(intent.max_clip_duration).toFixed(1) + 's' : '自动') + '</div>' +
      '<div>策略：' + esc(intent.source_preference || 'user_first') +
      ' · ' + esc(intent.diversity_policy || 'prefer_unique_source') +
      ' · ' + esc(intent.reuse_policy || 'reuse_when_needed') + '</div>' +
      '<div style="margin-top:5px">实际消费：</div><div>' + windows + '</div>' +
      (intent.selection_reason ? '<div style="margin-top:5px">选择依据：' + esc(intent.selection_reason) + '</div>' : '');
  } catch (e) {
    box.textContent = '素材依据暂不可读，不影响编辑。';
  }
}

function _sceneGenBox(sceneId){
  return `
    <div id="gen-inline-box" style="margin-top:6px">
      <details>
        <summary style="cursor:pointer;font-size:12px;color:var(--muted)">AI 生成新画面（图片 / 图形 / 视频 / 艺术动画 → 变体）</summary>
        <div class="btn-row" style="flex-wrap:wrap;margin-top:6px">
          <select id="gen-type" style="width:96px" onchange="applyGenerationAvailability()">
            <option value="image">图片</option>
            <option value="graphic">图形（程序化）</option>
            <option value="video">视频</option>
            <option value="motion">艺术动画</option>
          </select>
          <select id="gen-grammar" style="width:150px;display:none"
                  onchange="onMotionGrammarChanged()"></select>
          <input type="text" id="gen-prompt" class="rev-input" style="flex:1;min-width:120px"
                 placeholder="画面描述（例：极简标题卡：效率提升80%）">
          <button class="btn-sm" onclick="editorGenerate()">生成 3 变体</button>
        </div>
        <div class="hint" id="gen-status"></div>
        <div id="gen-results"></div>
      </details>
    </div>`;
}

// ---------------------------------------------------------------------------
// Layer 上下文（§12 按层类型分区）
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// §61 配置驱动 Inspector：Selection → Object Schema → Section Schema → Renderer。
// section 定义在 LAYER_SECTION_SCHEMA（类型过滤 + 内容工厂），渲染器只做组装，
// 不再手写几十段 if(layer.type===video){...} HTML。
// ---------------------------------------------------------------------------
const LAYER_SECTION_SCHEMA = [
  {id: 'source', label: '素材', types: null, factory: (ctx) => _secSource(ctx)},
  {id: 'sound', label: '声音', types: ['video'], factory: (ctx) => _secSound(ctx)},
  {id: 'transform', label: '变换', types: null, factory: (ctx) => _secTransform(ctx)},
  {id: 'motion', label: '动效', types: null, factory: (ctx) => _secMotion(ctx)},
  {id: 'pip', label: '画中画', types: ['video'], factory: (ctx) => _secPip(ctx)},
  {id: 'matting', label: '抠像', types: ['video'], factory: (ctx) => _secMatting(ctx)},
  {id: 'mask', label: '蒙版', types: null, factory: (ctx) => _secMask(ctx)},
  {id: 'keyframes', label: '关键帧', types: null, factory: (ctx) => _secKeyframes(ctx)},
];

function _inspectorLayer(layerId){
  const box = document.getElementById('editor-inspector');
  const [sceneId, idxS] = layerId.split('#');
  const idx = parseInt(idxS);
  const sc = ED.comp.scenes.find(s => s.id === sceneId);
  const lay = sc && sc.layers[idx];
  if (!lay) { clearSelection('inspector'); box.innerHTML = '<div class="hint">图层已变化，请重新选择</div>'; return; }
  const ctx = {sceneId, idx, layerId, lay};
  // §61 Renderer：schema 过滤 + 组装
  const sections = LAYER_SECTION_SCHEMA
    .filter(s => !s.types || s.types.includes(lay.type))
    .map(s => `<div class="role-block" data-section="${s.id}">${s.factory(ctx)}</div>`)
    .join('');
  box.innerHTML = `<b>${layerTypeCn(lay.type)}</b> <span class="hint">${esc(layerId)}</span>${sections}`+
    '<div id="editor-picker-box"></div>';
  if (lay.asset_id) loadAssetInspector(lay.asset_id);
}

// ---- section 内容工厂（§61：每 section 一个纯函数，ctx = {sceneId, idx, layerId, lay}）----
function _secSource({sceneId, idx, lay}){
  return `<b>素材</b>
    <div class="hint">${esc(lay.asset_id || '程序化画面（无外部素材）')} · 层时长 ${lay.duration.toFixed(1)}s · 场景内起点 ${lay.start.toFixed(1)}s</div>
    ${lay.type === 'video' ? `
    <div class="hint">源区间 ${lay.source_in.toFixed(1)}s → ${lay.source_out ? lay.source_out.toFixed(1) + 's' : '结尾'} · 速度 ${lay.speed.toFixed(1)}x</div>
    <div class="btn-row"><input type="number" id="ed-trim-in" placeholder="起点秒" step="0.5" min="0" style="width:76px">
    <input type="number" id="ed-trim-out" placeholder="终点秒" step="0.5" min="0" style="width:76px">
    <button class="btn-secondary btn-sm" onclick="editorApplyTrim('${sceneId}',${idx})">应用裁剪</button>
    <select id="ed-speed" style="width:70px">${[0.5,1,1.5,2].map(s2=>`<option value="${s2}"${Math.abs(lay.speed-s2)<0.01?' selected':''}>${s2}x</option>`).join('')}</select>
    <button class="btn-secondary btn-sm" onclick="editorApplySpeed('${sceneId}',${idx})">应用速度</button></div>` : ''}
    <div class="btn-row">
      <button class="btn-secondary btn-sm" data-action="ui.open_asset_picker">换素材</button>
      ${lay.asset_id && lay.type === 'video' ? "<button class=\"btn-secondary btn-sm\" onclick=\"editorVisualQA('" + sceneId + "'," + idx + ")\">视觉验证</button>" : ''}
      <button class="btn-secondary btn-sm" onclick="editorToggleLock('${sceneId}',${idx})">${lay.locked ? '解锁' : '锁定'}</button>
    </div>
    <div id="ed-asset-inspector" class="hint"></div>`;
}
function _secSound({layerId}){
  return `<b>声音</b>
    ${_layerOriginalAudioState()}
    <div class="btn-row" style="flex-wrap:wrap">
      <button class="btn-secondary btn-sm" data-action="audio.preserve">保留原声</button>
      <button class="btn-secondary btn-sm" data-action="audio.mute_original">静音原声</button>
      <button class="btn-secondary btn-sm" onclick="editorLayerOriginalVolume('${layerId}')">原声音量</button>
    </div>`;
}
function _secTransform({sceneId, idx, lay}){
  return `<b>变换</b>
    <div class="btn-row" style="flex-wrap:wrap">
      <input type="number" id="ed-tf-x" placeholder="水平 0-1" step="0.05" min="0" max="1" style="width:72px" value="${lay.transform ? lay.transform.x : ''}">
      <input type="number" id="ed-tf-y" placeholder="垂直 0-1" step="0.05" min="0" max="1" style="width:72px" value="${lay.transform ? lay.transform.y : ''}">
      <input type="number" id="ed-tf-rot" placeholder="旋转°" step="5" style="width:60px" value="${lay.transform && lay.transform.rotation ? lay.transform.rotation : ''}">
      <input type="number" id="ed-opacity" placeholder="透明度 0-1" step="0.1" min="0" max="1" style="width:84px" value="${lay.transform ? lay.transform.opacity : ''}">
      <input type="number" id="ed-scale" placeholder="缩放 0.1-3" step="0.1" style="width:72px" value="${lay.transform ? lay.transform.scale : ''}">
      <button class="btn-secondary btn-sm" onclick="editorSetVisualTransform('${sceneId}',${idx})">应用变换</button>
    </div>
    <div class="hint" style="font-size:11px">水平/垂直为画幅归一坐标（0.5=居中）。</div>`;
}
function _secMotion({sceneId, idx, lay}){
  return `<b>动效</b>
    <div class="hint">当前：${lay.motion_recipe_id ? esc(motionCnName(lay.motion_recipe_id)) : '无'}</div>
    <div class="btn-row">
      <button class="btn-secondary btn-sm" data-action="ui.open_motion_picker">换动效</button>
      <button class="btn-secondary btn-sm" onclick="editorPreviewMotionParam('${sceneId}',${idx})">预览参数</button>
    </div>
    <label>动效时长（秒）</label>
    <div class="btn-row"><input type="number" id="ed-mot-dur" value="0.5" step="0.1" min="0.1" style="width:80px">
    <select id="ed-mot-easing" style="width:110px"><option value="">缓动（默认）</option>
    ${['linear','quad','cubic','quart','expo','back','sine'].map(e=>`<option value="${e}">${e}</option>`).join('')}</select>
    <button class="btn-sm" onclick="editorApplyMotionParam('${sceneId}',${idx})">应用参数</button></div>
    <div id="ed-motion-preview" style="margin-top:6px"></div>`;
}
function _secPip({sceneId, idx, lay}){
  return `<b>画中画</b>
    ${((lay.motion_instance||{}).parameters||{}).pip
      ? `<div class="hint">已配置画中画（可在「变换」继续调整位置大小）</div>
         <div class="btn-row"><button class="btn-secondary btn-sm" onclick="editorRemovePip('${sceneId}',${idx})">取消画中画</button></div>`
      : `<div class="btn-row"><button class="btn-secondary btn-sm" onclick="editorApplyPip('${sceneId}',${idx})">设为画中画（右上角小窗）</button></div>`}`;
}
function _secMatting({sceneId, idx}){
  return `<b>抠像</b>
    <div class="btn-row">
      <select id="ed-mat-color" style="width:100px"><option value="0x00FF00">绿幕</option><option value="0x0000FF">蓝幕</option></select>
      <button class="btn-secondary btn-sm" onclick="editorRunMatting('${sceneId}',${idx})">生成抠像</button>
    </div>
    <div class="hint" id="ed-mat-status">绿幕/蓝幕抠底，产物入素材库并替换本层。</div>`;
}
function _secMask({sceneId, idx, lay, layerId}){
  return `<b>蒙版</b>
    ${lay.mask ? `<div class="hint">当前：${maskShapeCn(lay.mask.shape)}${lay.mask.feather ? ' · 羽化 ' + lay.mask.feather : ''}${lay.mask.inverted ? ' · 反转' : ''}</div>` : ''}
    <div class="btn-row" style="flex-wrap:wrap">
      <select id="ed-mask-shape" style="width:100px"><option value="">形状…</option><option value="rectangle">矩形</option><option value="rounded_rect">圆角矩形</option><option value="ellipse">椭圆</option></select>
      <input type="number" id="ed-mask-feather" placeholder="羽化px" min="0" style="width:72px">
      <label style="display:flex;align-items:center;gap:4px;font-size:12px;margin:0"><input type="checkbox" id="ed-mask-invert"> 反转</label>
      <button class="btn-secondary btn-sm" onclick="editorApplyMask('${sceneId}',${idx})">应用蒙版</button>
      <button class="btn-secondary btn-sm" data-action="ui.edit_mask">编辑路径</button>
      ${lay.mask ? `<button class="btn-secondary btn-sm" data-action="visual.mask.remove">移除蒙版</button>` : ''}
    </div>
    ${MASK_ED.open ? `<div class="btn-row">
      <button class="btn-sm" onclick="maskEditorSave()">保存路径</button>
      <button class="btn-secondary btn-sm" onclick="maskEditorCancel()">取消（还原）</button>
      <button class="btn-secondary btn-sm" onclick="maskEditorUndo()">撤销点</button>
      <button class="btn-secondary btn-sm" onclick="maskEditorRedo()">重做点</button></div>` : ''}`;
}
function _secKeyframes({sceneId, idx, lay}){
  return `<b>关键帧</b>
    <div id="ed-kf-list">${renderKeyframeList(lay, sceneId, idx)}</div>
    <div class="btn-row" style="flex-wrap:wrap">
      <select id="ed-kf-prop" style="width:96px"><option value="opacity">透明度</option><option value="scale">缩放</option><option value="x">水平</option><option value="y">垂直</option><option value="rotation">旋转</option></select>
      <input type="number" id="ed-kf-at" placeholder="时间秒" step="0.1" min="0" style="width:72px">
      <input type="number" id="ed-kf-val" placeholder="数值" step="0.1" style="width:64px">
      <button class="btn-secondary btn-sm" onclick="editorAddKeyframe('${sceneId}',${idx})">打关键帧</button>
    </div>`;
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
  const videoLayers = layerIds.filter(lid => {
    const [sceneId, idxS] = lid.split('#');
    const sc = ED.comp.scenes.find(s => s.id === sceneId);
    const lay = sc && sc.layers[parseInt(idxS)];
    return lay && lay.type === 'video';
  });
  box.innerHTML = `<b>已选 ${edSelectedCount()} 项</b>`+
    `<div class="hint">场景：${sceneIds.length} 个 · 图层：${layerIds.length} 个（视频 ${videoLayers.length}）</div>`+
    `<div class="role-block"><b>声音（§5.3 多选视频批量）</b>
      ${videoLayers.length ? `
      <div class="hint">对选中的 ${videoLayers.length} 个视频素材统一设置原声：</div>
      <div class="btn-row" style="flex-wrap:wrap">
        <button class="btn-secondary btn-sm" data-action="audio.preserve">全部保留原声</button>
        <button class="btn-secondary btn-sm" data-action="audio.mute_original">全部静音原声</button>
        <button class="btn-secondary btn-sm" onclick="editorMultiOriginalVolume()">统一音量</button>
      </div>` : '<div class="hint">选中视频素材后可批量设置原声。</div>'}
    </div>`+
    `<div class="btn-row">`+
    `<button class="btn-secondary btn-sm" data-action="timeline.duplicate">批量复制场景</button>`+
    `<button class="btn-secondary btn-sm" data-action="timeline.delete">批量删除场景</button>`+
    `<button class="btn-secondary btn-sm" data-action="visual.lock">批量锁定图层</button>`+
    `<button class="btn-secondary btn-sm" onclick="clearSelection()">取消选择</button></div>`;
}

/** §5.3 多选视频层批量原声（AI 与 UI 走同一 set_original_audio 语义）。 */
function editorMultiOriginal(mode){
  return executeAction(mode === 'preserve' ? 'audio.preserve' : 'audio.mute_original',
                       {volume: mode === 'preserve' ? 0.8 : 0.0}, 'project');
}
function editorMultiOriginalVolume(){
  const hostId = 'ed-multivol-host';
  _ensureInlineHost(hostId);
  uiInlineInput(hostId, {type: 'number', def: 0.2, min: 0, max: 2, step: 0.1, unit: '倍'},
    (v) => { if (!isNaN(v)) executeAction('audio.original_volume', {volume: v}, 'project'); });
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
// §5 声音入对象上下文：视频层 / 场景 / 原声轨的统一实现
// （原 editorOriginalAudio/editorOriginalAudioVolume 从项目级面板迁移而来）
// ---------------------------------------------------------------------------

/** §5.1 选中视频层：原声状态行（与「这个视频素材」明确关联）。 */
function _layerOriginalAudioState(){
  const orig = (ED.comp.audio || []).find(a => a.type === 'original');
  if (!orig) return '<div class="hint">原声：未启用（点「保留原声」建立此素材的原声轨）</div>';
  return `<div class="hint">原声：${orig.muted ? '已静音' : '保留'} · 音量 ${(orig.volume * 100).toFixed(0)}%`+
    `${orig.ducking ? ' · 旁白时自动压低' : ''}${orig.fadeIn ? ' · 淡入 ' + orig.fadeIn + 's' : ''}</div>`;
}

/** §5.1 视频层「保留原声 / 静音原声」（走 Action 语义 = set_original_audio）。 */
function editorLayerOriginalAudio(layerId, mode){
  const vol = mode === 'preserve' ? 0.8 : 0.0;
  editorCmd('set_original_audio', {mode, volume: vol, duckAgainst: 'narration'}, '', true);
}
/** §5.1 视频层原声音量（含本层定位语义提示）。 */
function editorLayerOriginalVolume(layerId){
  const hostId = 'ed-origvol-host';
  _ensureInlineHost(hostId);
  uiInlineInput(hostId, {type: 'number', def: 0.8, min: 0, max: 2, step: 0.1, unit: '倍'},
    (v) => { if (!isNaN(v)) executeAction('audio.original_volume', {volume: v}, 'project'); });
}
/** §5 场景级原声（Scene Inspector 的本镜操作）。 */
function editorSceneOriginalAudio(sceneId, mode){
  editorLayerOriginalAudio(sceneId, mode);
}

/** §8 Scene→画面「自动找素材」：打开 Asset Picker 的图库 tab（搜索入口）。 */
function editorAutoFindAsset(sceneId){
  editorOpenAssetPicker(sceneId, 0, 'autoFind');
}

/** §8 Scene→画面「AI 生成」：滚动展开内联生成区并聚焦输入。 */
function editorSceneGenerate(sceneId){
  const det = document.querySelector('#gen-inline-box details');
  if (det) { det.open = true; det.scrollIntoView({behavior: 'smooth', block: 'center'}); }
  const inp = document.getElementById('gen-prompt');
  if (inp) inp.focus();
  const sc = ED.comp.scenes.find(s => s.id === sceneId);
  if (inp && sc && !inp.value) inp.value = (sc.text || '').slice(0, 40);
}

/** §5.2/§7 配音设置（原声音与字幕面板的配音区迁移到项目上下文）。 */
function editorVoiceOpen(){
  const box = document.getElementById('editor-inspector');
  const voicesHtml = `<div id="ed-voice-sel-host" style="margin-top:6px"></div>`;
  box.insertAdjacentHTML('beforeend', `<div class="role-block" data-section="voice" id="ed-voice-panel">
    <b>配音</b>${voicesHtml}<span class="hint" id="ed-voice-status"></span></div>`);
  const host = document.getElementById('ed-voice-sel-host');
  if (host && !host.querySelector('select')) {
    // §46 旁白操作面四按钮：试听 / 完整试听 / 重新生成 / 替换音频
    host.innerHTML = `<div class="btn-row" style="margin-top:4px;flex-wrap:wrap">
      <select id="ed-voice-sel" style="flex:1;min-width:120px"></select>
      <button class="btn-secondary btn-sm" onclick="edVoicePreview()" title="单句试听当前音色">试听</button>
      <button class="btn-secondary btn-sm" onclick="edVoiceFullPreview()" title="整段旁白合成试听">完整试听</button>
    </div>
    <div class="btn-row" style="margin-top:4px;flex-wrap:wrap">
      <button class="btn-secondary btn-sm" onclick="edVoiceRegenerate()" title="按当前音色重新生成全部旁白（渲染草稿后生效）">重新生成旁白</button>
      <button class="btn-secondary btn-sm" onclick="edVoiceReplaceUpload()" title="上传配音文件替换 AI 旁白（mp3/wav/m4a）">替换音频</button>
    </div>
    <div id="ed-voice-upload-row" style="display:none;margin-top:6px">
      <input type="file" id="ed-voice-file" accept=".mp3,.wav,.m4a,.aac">
      <div class="btn-row" style="margin-top:4px">
        <button class="btn-sm" onclick="edVoiceReplaceApply()">确认替换</button>
        <button class="btn-secondary btn-sm" onclick="document.getElementById('ed-voice-upload-row').style.display='none'">取消</button>
      </div>
      <div class="hint" id="ed-voice-upload-status"></div>
    </div>`;
    edVoiceLoadOptions();
  }
  document.getElementById('ed-voice-panel').scrollIntoView({behavior: 'smooth', block: 'center'});
}

/** §46 重新生成旁白：换音色后点此 → WorkflowRun 重渲（真链路，非静默）。 */
async function edVoiceRegenerate(){
  const sel = document.getElementById('ed-voice-sel');
  const st = document.getElementById('ed-voice-status');
  if (!ED.pid) { editorToast('先打开项目', true); return; }
  if (!sel || !sel.value) { if (st) st.textContent = '先选音色'; return; }
  if (st) st.textContent = '重新生成旁白已提交（按当前音色渲染新草稿）……';
  try {
    const r = await apiPost('/api/workflows', {kind: 'render_draft',
                                                 project_id: ED.pid});
    if (r.ok) {
      if (st) st.textContent = '旁白重新生成中（完成后预览区可见新草稿）';
      editorToast('已提交重新生成（草稿渲染完成后自动预览）');
    } else {
      if (st) st.textContent = '提交失败：' + (r.error || '');
    }
  } catch (e) {
    if (st) st.textContent = '提交失败：' + (e.message || e);
  }
}

/** §46 替换音频：上传配音 → /api/voice_upload（Probe→ASR→Word Timing→Narration 轨）。 */
function edVoiceReplaceUpload(){
  const row = document.getElementById('ed-voice-upload-row');
  if (row) row.style.display = '';
}
async function edVoiceReplaceApply(){
  const fi = document.getElementById('ed-voice-file');
  const st = document.getElementById('ed-voice-upload-status');
  if (!fi || !fi.files || !fi.files.length) { if (st) st.textContent = '先选择配音文件'; return; }
  if (!ED.pid) { if (st) st.textContent = '先打开项目'; return; }
  if (st) st.textContent = '上传并解析配音中（探测 → 语音识别 → 词级时间轴）……';
  try {
    const fd = new FormData();
    fd.append('file', fi.files[0], fi.files[0].name);
    fd.append('project_id', ED.pid);
    const r = await apiUpload('/api/voice_upload', fd);
    if (r.ok) {
      if (st) st.textContent = `已替换：旁白 ${r.duration || ''}s（来源 ${r.source || '已解析'}）`;
      editorToast('配音已替换（TTS 跳过；渲染草稿后生效）');
      document.getElementById('ed-voice-upload-row').style.display = 'none';
    } else {
      if (st) st.textContent = '替换失败：' + (r.error || '未知');
    }
  } catch (e) {
    if (st) st.textContent = '替换失败：' + (e.message || e);
  }
}

/** §37 整体调音（Audio Bus）：原声/BGM/SFX/旁白 一屏多轨调整 → audio_mix。 */
function editorAudioBusOpen(){
  const box = document.getElementById('editor-inspector');
  const audioOf = ty => (ED.comp.audio || []).find(a => a.type === ty);
  const row = (ty, label) => {
    const t = audioOf(ty);
    return `<div class="btn-row" style="margin-top:4px;align-items:center">
      <span class="hint" style="flex:0 0 64px">${label}</span>
      <input type="number" id="bus-${ty}-vol" value="${t ? t.volume : ''}" placeholder="音量0-2" step="0.1" min="0" max="2" style="width:82px">
      <label style="font-size:12px;margin:0"><input type="checkbox" id="bus-${ty}-mute"${t && t.muted ? ' checked' : ''}> 静音</label>
      <label style="font-size:12px;margin:0"><input type="checkbox" id="bus-${ty}-duck"${t && t.ducking ? ' checked' : ''}> 旁白时压低</label>
    </div>`;
  };
  box.insertAdjacentHTML('beforeend', `<div class="role-block" data-section="audiobus" id="ed-audiobus-panel">
    <b>整体调音（Audio Bus）</b>
    <div class="hint">一次调整多轨（原声/BGM/音效/旁白），空值轨不动。</div>
    ${row('original', '原声')}${row('bgm', '背景音乐')}${row('sfx', '音效')}${row('narration', '旁白')}
    <div class="btn-row"><button class="btn-sm" onclick="editorAudioBusApply()">应用混音</button></div>
  </div>`);
  document.getElementById('ed-audiobus-panel').scrollIntoView({behavior: 'smooth', block: 'center'});
}
/** §37 audio_mix 提交：只收集有输入的轨。 */
function editorAudioBusApply(){
  const tracks = {};
  for (const ty of ['original', 'bgm', 'sfx', 'narration']) {
    const volEl = document.getElementById('bus-' + ty + '-vol');
    const muteEl = document.getElementById('bus-' + ty + '-mute');
    const duckEl = document.getElementById('bus-' + ty + '-duck');
    const opts = {};
    if (volEl && volEl.value !== '') opts.volume = parseFloat(volEl.value);
    if (muteEl && muteEl.checked) opts.muted = true;
    if (duckEl && duckEl.checked) opts.ducking = true;
    if (Object.keys(opts).length) tracks[ty] = opts;
  }
  if (!Object.keys(tracks).length) { editorToast('至少填一个轨的音量/静音/压低'); return; }
  executeCommand({command: 'audio_mix', targetId: '', payload: {tracks}})
    .then(r => {
      editorToast(r.ok ? '混音已应用' : '混音未完成：' + (r.error || ''), !r.ok);
      if (r.ok) refreshWorkspace();
    });
}

/** §7 字幕样式（对象上下文版：不再有独立面板字段）。 */
function editorSubtitleStyleOpen(){
  const box = document.getElementById('editor-inspector');
  const st = ED.comp.subtitle_style || {};
  box.insertAdjacentHTML('beforeend', `<div class="role-block" data-section="subtitlestyle" id="ed-substyle-panel">
    <b>字幕样式</b>
    <div class="btn-row" style="flex-wrap:wrap;margin-top:4px">
      <select id="sub-mode" style="width:76px">
        <option value="sentence"${st.mode === 'sentence' ? ' selected' : ''}>句子</option>
        <option value="word"${st.mode === 'word' ? ' selected' : ''}>逐词</option>
      </select>
      <input type="text" id="sub-font" style="width:120px" placeholder="字体（可选）" value="${esc(st.font || '')}">
      <input type="number" id="sub-size" step="0.1" min="0.3" max="3" style="width:64px" placeholder="字号" value="${st.size ?? ''}">
      <input type="color" id="sub-color" value="${st.color || '#ffffff'}" style="width:40px" title="字幕颜色">
      <input type="color" id="sub-background" value="${st.background || '#000000'}" style="width:40px" title="字幕背景颜色">
    </div>
    <div class="btn-row" style="flex-wrap:wrap;margin-top:4px">
      <select id="sub-pos" style="width:72px">
        <option value="bottom"${st.position === 'bottom' || !st.position ? ' selected' : ''}>底部</option>
        <option value="center"${st.position === 'center' ? ' selected' : ''}>居中</option>
        <option value="top"${st.position === 'top' ? ' selected' : ''}>顶部</option>
      </select>
      <select id="sub-align" style="width:72px">
        <option value="left"${st.alignment === 'left' ? ' selected' : ''}>左对齐</option>
        <option value="center"${st.alignment === 'center' || !st.alignment ? ' selected' : ''}>居中</option>
        <option value="right"${st.alignment === 'right' ? ' selected' : ''}>右对齐</option>
      </select>
      <select id="sub-animation" style="width:92px">
        <option value="none"${st.animation === 'none' || !st.animation ? ' selected' : ''}>无动画</option>
        <option value="fade"${st.animation === 'fade' ? ' selected' : ''}>淡入</option>
        <option value="pop_spring"${st.animation === 'pop_spring' ? ' selected' : ''}>弹簧高亮</option>
      </select>
      <input type="number" id="sub-stroke-width" min="0" max="12" step="1" style="width:56px" placeholder="描边宽" value="${st.stroke_width ?? 2}">
      <input type="number" id="sub-bg-opacity" min="0" max="1" step="0.05" style="width:64px" placeholder="背景透明" value="${st.background_opacity ?? 0.5}">
    </div>
    <div class="btn-row" style="flex-wrap:wrap;margin-top:4px">
      <input type="text" id="sub-stroke" placeholder="描边色（例：#000000）" style="width:110px" value="${esc(st.stroke_color || '')}">
      <input type="number" id="sub-max-width" min="0.3" max="1" step="0.05" style="width:64px" placeholder="最大宽度" value="${st.max_width ?? 0.9}">
      <button class="btn-secondary btn-sm" onclick="editorSubtitleStyleApply()">应用样式</button>
      <button class="btn-secondary btn-sm" onclick="editorSubtitleWhiteOnBlack()">白字黑边</button>
      <button class="btn-secondary btn-sm" onclick="editorSubtitleStyleApply('no-background')">无背景</button>
    </div>
    <div class="hint">字幕样式直接写入 Composition：字体、字号、颜色、描边、背景、位置、对齐、宽度与动画均可由 AI 和人工共同修改。</div>
  </div>`);
  document.getElementById('ed-substyle-panel').scrollIntoView({behavior: 'smooth', block: 'center'});
}
function editorSubtitleWhiteOnBlack(){
  const c = document.getElementById('sub-color');
  if (c) c.value = '#ffffff';
  const s = document.getElementById('sub-stroke');
  if (s) s.value = '#000000';
  editorToast('已填入白字黑边，点「应用样式」生效');
}
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

async function editorSemanticRerank(sceneId){
  const sc = (ED.comp.scenes || []).find(s => s.id === sceneId);
  const box = document.getElementById('mpt-scene-context');
  if (!sc || !box) return;
  const oldText = box.querySelector('.semantic-rerank-result');
  if (oldText) oldText.remove();
  const host = document.createElement('div');
  host.className = 'hint semantic-rerank-result';
  host.textContent = '语义重排中……';
  box.appendChild(host);
  try {
    const r = await apiPost('/api/action/execute', {
      project_id: ED.pid || '',
      action: 'visual.semantic_rerank',
      target: 'current_selection',
      params: {sceneId: sceneId, sceneText: sc.text || '', limit: 5},
      selection: {type:'scene', id:sceneId}
    });
    if (r && r.result && Array.isArray(r.result.candidates)) {
      const items = r.result.candidates.map((x, i) =>
        '<div>' + (i + 1) + '. <b>' + esc(x.asset_id || x.assetId || '未命名') + '</b>' +
        (x.semantic_status ? ' · ' + esc(x.semantic_status) : '') +
        (x.semantic_reason ? ' · ' + esc(x.semantic_reason) : '') + '</div>'
      ).join('');
      host.innerHTML = '<b>语义候选顺序</b>' +
        (items || '<br>没有可供重排的视频素材。') +
        (r.result.reason ? '<br><span class="hint">' + esc(r.result.reason) + '</span>' : '');
    } else {
      host.textContent = '语义重排失败：' + esc((r && (r.error || r.reason)) || '未知原因');
    }
  } catch (e) {
    host.textContent = '语义重排失败：' + esc(e.message || e);
  }
}

async function editorVisualQA(sceneId, idx){
  const sc = (ED.comp.scenes || []).find(s => s.id === sceneId);
  const lay = sc && sc.layers ? sc.layers[idx] : null;
  const el = document.getElementById('ed-asset-inspector');
  if (!lay || !lay.asset_id || !el) return;
  el.innerHTML = '视觉验证中……';
  try {
    const d = await apiGet('/api/asset_detail?asset_id=' + encodeURIComponent(lay.asset_id) +
      '&project_id=' + encodeURIComponent(ED.pid || ''));
    if (!d || d.error || !d.file) {
      el.innerHTML = '<span class="hint">素材文件不可用，无法验证。</span>';
      return;
    }
    const r = await apiPost('/api/action/execute', {
      project_id: ED.pid || '',
      action: 'visual.verify_asset',
      target: 'current_selection',
      params: {
        sceneId: sceneId,
        sceneText: sc.text || '',
        assetId: lay.asset_id,
        assetFile: d.file
      },
      selection: {type:'layer', id:sceneId + '#' + idx, layer_type:lay.type || 'video'}
    });
    if (r && r.result && r.result.verdict) {
      const verdictCn = {DIRECT_MATCH:'直接匹配', SUPPORTING:'辅助匹配', CONFLICT:'明显冲突', UNKNOWN:'无法确认'};
      const v = r.result.verdict;
      el.innerHTML = '<b>视觉验证</b>：<b>' + esc(verdictCn[v] || v) + '</b>' +
        (r.result.evidence ? '<br>' + esc(r.result.evidence) : '') +
        (r.result.reason ? '<br><span class="hint">' + esc(r.result.reason) + '</span>' : '') +
        (!r.ok && r.error ? '<br><span class="hint">' + esc(r.error) + '</span>' : '');
    } else {
      el.innerHTML = '<span class="hint">验证失败：' + esc((r && (r.error || r.reason)) || '未知错误') + '</span>';
    }
  } catch (e) {
    el.innerHTML = '<span class="hint">验证失败：' + esc(e.message || e) + '</span>';
  }
}

function editorSetVisualTransform(sceneId, idx){
  const payload = {};
  const fields = [['ed-tf-x','x'],['ed-tf-y','y'],['ed-tf-rot','rotation'],['ed-opacity','opacity'],['ed-scale','scale']];
  for (const [id, key] of fields) {
    const el = document.getElementById(id);
    if (el && el.value !== '') payload[key] = parseFloat(el.value);
  }
  if (!Object.keys(payload).length) { editorToast('至少填一项（水平/垂直/旋转/透明度/缩放）'); return; }
  return executeAction('visual.transform', payload, 'current_selection');
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
  return executeAction('visual.keyframe_add', {property: prop, at: parseFloat(at), value: parseFloat(val)}, 'current_selection');
}
function editorRemoveKeyframe(sceneId, idx, prop, at){
  return executeAction('visual.keyframe_remove', {property: prop, at: at}, 'current_selection');
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
  return executeAction('visual.mask', payload, 'current_selection');
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

// §11 下拉三态：READY（可选项）/ EMPTY（暂无数据+原因）/ UNAVAILABLE（服务不可用+去设置）
// 不再出现空 <select> 让用户猜是没数据、没配置还是接口挂了。
async function edVoiceLoadOptions(){
  const sel = document.getElementById('ed-voice-sel');
  if (!sel || sel.options.length) return;
  sel.innerHTML = '<option value="">加载音色……</option>';
  try {
    const r = await apiGet('/api/voices');
    const list = Array.isArray(r) ? r : (r.voices || r && []) || [];
    if (list.length) {
      sel.innerHTML = list.slice(0, 30).map(v =>
        `<option value="${esc(v.voice_id || v.id || v)}">${esc(v.name || v.label || v.voice_id || v)}</option>`).join('');
    } else {
      sel.innerHTML = '<option value="">（暂无可用音色）</option>';
      const st = document.getElementById('ed-voice-status');
      if (st) st.innerHTML = '没有配置可用的配音服务 <a href="#" onclick="switchTab(\'settings\');return false" style="color:var(--accent)">去设置</a>';
    }
  } catch (e) {
    sel.innerHTML = '<option value="">（配音服务不可用）</option>';
    const st = document.getElementById('ed-voice-status');
    if (st) st.innerHTML = '配音服务读取失败：' + esc(String(e).slice(0, 60)) + ' <a href="#" onclick="switchTab(\'settings\');return false" style="color:var(--accent)">去设置</a>';
  }
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
/** §35 完整旁白试听：整段文案合成试听（全项目文案一次听完）。 */
async function edVoiceFullPreview(){
  const sel = document.getElementById('ed-voice-sel');
  const st = document.getElementById('ed-voice-status');
  if (!sel || !sel.value) { if (st) st.textContent = '先选音色'; return; }
  const texts = (ED.comp.scenes || []).map(s => s.text || '').filter(Boolean).join('。');
  if (!texts) { if (st) st.textContent = '项目没有旁白文案'; return; }
  if (st) st.textContent = '完整旁白试听合成中……（整段文案，约需数秒）';
  try {
    const r = await apiPost('/api/voice_preview_full', {voice: sel.value, text: texts});
    if (r && r.ok && r.audio_url) {
      new Audio(r.audio_url).play();
      if (st) st.textContent = '完整旁白试听播放中';
    } else {
      if (st) st.textContent = '完整试听失败：' + (r.error || '未知');
    }
  } catch (e) {
    if (st) st.textContent = '完整试听失败：' + (e.message || e);
  }
}
function editorOriginalAudio(mode){
  editorCmd('set_original_audio', {mode, volume: mode === 'preserve' ? 0.8 : 0.0, duckAgainst: 'narration'}, '', true);
}
function editorOriginalAudioVolume(){
  const hostId = 'ed-origvol-host';
  _ensureInlineHost(hostId);
  uiInlineInput(hostId, {type: 'number', def: 0.8, min: 0, max: 2, step: 0.1, unit: '倍'},
    (v) => { if (!isNaN(v)) executeAction('audio.original_volume', {volume: v}, 'project'); });
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
    editorCmd('set_watermark', {kind: 'text', text, anchor, opacity: 0.75, createNew: true}, '', true);
  };
  row.querySelector('#' + id + '-text').focus();
}
function editorWatermarkClipOpen(index, ev){
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  const wm = (ED.comp && ED.comp.watermarks || [])[index];
  if (!wm) { editorToast('覆盖层已变化，请刷新时间线', true); return; }
  const total = Number(ED.comp.duration) || 1;
  const start = Math.max(0, Number(wm.start) || 0);
  const duration = wm.duration == null ? Math.max(0.05, total - start)
    : Math.max(0.05, Number(wm.duration) || 0.05);
  const label = wm.text || wm.asset_id || wm.kind || '覆盖层';
  const anchor = wm.anchor || 'bottom_right';
  const mask = document.createElement('div');
  mask.id = 'watermark-clip-inspector-mask';
  mask.className = 'audio-clip-inspector-mask';
  mask.innerHTML = `
    <section class="audio-clip-inspector" role="dialog" aria-modal="true" aria-labelledby="watermark-clip-inspector-title">
      <header>
        <div><b id="watermark-clip-inspector-title">覆盖层 · 片段设置</b>
          <div class="hint">${esc(label)} · ${esc(wm.kind || 'text')} · 成片 ${total.toFixed(2)}s</div>
        </div>
        <button type="button" class="btn-secondary btn-sm" onclick="editorWatermarkClipClose()" aria-label="关闭覆盖层设置">关闭</button>
      </header>
      ${wm.kind === 'text' ? `<label for="watermark-clip-text">显示文字</label>
        <input id="watermark-clip-text" type="text" value="${esc(wm.text || '')}" maxlength="300">` : `<div class="hint">素材：${esc(wm.asset_id || '未绑定')}（素材引用保持不变）</div>`}
      <label for="watermark-clip-start">开始时间（秒）</label>
      <input id="watermark-clip-start" type="number" min="0" max="${total}" step="0.05" value="${start.toFixed(2)}">
      <label for="watermark-clip-duration">显示时长（秒）</label>
      <input id="watermark-clip-duration" type="number" min="0.05" max="${Math.max(0.05,total-start).toFixed(2)}" step="0.05" value="${duration.toFixed(2)}">
      <label for="watermark-clip-anchor">位置</label>
      <select id="watermark-clip-anchor">${_UI_CN_ANCHORS.map(([v,l]) => `<option value="${v}"${v === anchor ? ' selected' : ''}>${l}</option>`).join('')}</select>
      <label for="watermark-clip-opacity">透明度（0–1）</label>
      <input id="watermark-clip-opacity" type="number" min="0" max="1" step="0.05" value="${Number(wm.opacity ?? 0.75)}">
      <footer>
        <button type="button" class="btn-secondary" onclick="editorWatermarkClipClose()">取消</button>
        <button type="button" class="btn-secondary" onclick="editorWatermarkRemoveAt(${index})">删除覆盖层</button>
        <button type="button" class="btn-primary" onclick="editorWatermarkClipApply(${index})">保存修改</button>
      </footer>
    </section>`;
  mask.addEventListener('click', event => {
    if (event.target === mask) editorWatermarkClipClose();
  });
  document.body.appendChild(mask);
  const first = mask.querySelector('#watermark-clip-start');
  if (first) first.focus();
}

function editorWatermarkClipClose(){
  const mask = document.getElementById('watermark-clip-inspector-mask');
  if (mask) mask.remove();
}

async function editorWatermarkClipApply(index){
  const wm = (ED.comp && ED.comp.watermarks || [])[index];
  const mask = document.getElementById('watermark-clip-inspector-mask');
  if (!wm || !mask) { editorWatermarkClipClose(); editorToast('覆盖层已变化，请重新选择', true); return; }
  const total = Number(ED.comp.duration) || 1;
  const start = Number(mask.querySelector('#watermark-clip-start').value);
  const duration = Number(mask.querySelector('#watermark-clip-duration').value);
  const opacity = Number(mask.querySelector('#watermark-clip-opacity').value);
  if (![start,duration,opacity].every(Number.isFinite) || start < 0 || duration < 0.05
      || start + duration > total + 0.001 || opacity < 0 || opacity > 1) {
    editorToast('参数无效：时间区间须位于成片范围内，时长至少 0.05 秒，透明度在 0–1', true);
    return;
  }
  const payload = {
    kind: wm.kind || 'text',
    anchor: mask.querySelector('#watermark-clip-anchor').value,
    opacity,
    scale: Number(wm.scale ?? 1),
    safeArea: Number(wm.safe_area ?? 32),
    start, duration,
  };
  if (wm.kind === 'text') {
    payload.text = (mask.querySelector('#watermark-clip-text').value || '').trim();
    if (!payload.text) { editorToast('显示文字不能为空', true); return; }
  } else {
    payload.assetId = wm.asset_id;
  }
  try {
    const result = await editorCmd('set_watermark', payload, 'watermark#' + index, true);
    if (!result || !result.ok) {
      editorToast('覆盖层未保存：' + ((result && result.error) || '未知错误'), true);
      return;
    }
    editorWatermarkClipClose();
    editorToast('覆盖层已保存');
    await refreshWorkspace();
  } catch (error) {
    editorToast('覆盖层未保存：' + (error.message || error), true);
  }
}

async function editorWatermarkRemoveAt(index){
  try {
    const result = await editorCmd('remove_watermark', {}, 'watermark#' + index, true);
    if (!result || !result.ok) {
      editorToast('删除覆盖层失败：' + ((result && result.error) || '未知错误'), true);
      return;
    }
    editorWatermarkClipClose();
    editorToast('覆盖层已删除');
    await refreshWorkspace();
  } catch (error) {
    editorToast('删除覆盖层失败：' + (error.message || error), true);
  }
}

async function editorWatermarkRemove(){
  return executeAction('watermark.remove', {}, 'project');
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
    `<button class="btn-secondary btn-sm" onclick="editorWatermarkRemoveAt(${i})">删</button></span></div>`).join('');
}
function editorWatermarkMove(i){
  const hostId = 'editor-watermark-list';
  const w = (ED.comp.watermarks || [])[i] || {};
  uiInlineInput(hostId, {type: 'select', options: _UI_CN_ANCHORS, def: w.anchor || 'top_right'},
    (v) => editorCmd('set_watermark', {kind: w.kind || 'text', text: w.text || '水印',
                                       anchor: v, opacity: w.opacity ?? 0.75}, 'watermark#' + i, true));
}
function editorWatermarkOpacity(i){
  const hostId = 'editor-watermark-list';
  const w = (ED.comp.watermarks || [])[i] || {};
  uiInlineInput(hostId, {type: 'number', def: 0.75, min: 0, max: 1, step: 0.05},
    (v) => { if (!isNaN(v)) editorCmd('set_watermark',
      {kind: w.kind || 'text', text: w.text || '水印',
       anchor: w.anchor || 'top_right', opacity: v}, 'watermark#' + i, true); });
}
async function editorSubtitleStyleApply(mode){
  const payload = {};
  if (mode === 'no-background') {
    editorCmd('set_subtitle_style', {background: null, backgroundOpacity: 0}, '', true);
    return;
  }
  const fields = [
    ['sub-mode','mode','raw'], ['sub-font','font','raw'], ['sub-size','size','num'],
    ['sub-color','color','raw'], ['sub-stroke','strokeColor','raw'],
    ['sub-stroke-width','strokeWidth','num'], ['sub-background','background','raw'],
    ['sub-bg-opacity','backgroundOpacity','num'], ['sub-pos','position','raw'],
    ['sub-align','alignment','raw'], ['sub-max-width','maxWidth','num'],
    ['sub-animation','animation','raw'],
  ];
  for (const [id,key,kind] of fields) {
    const el = document.getElementById(id);
    if (!el || el.value === '') continue;
    payload[key] = kind === 'num' ? parseFloat(el.value) : el.value;
  }
  if (payload.background) payload.background = payload.background;
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
  inspectorActionsBox, loadAssetInspector, editorSemanticRerank, editorVisualQA, editorSetVisualTransform,
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
  // §5/§7/§8 对象化声音/字幕/生成（inspector.js 本轮新增）
  _layerOriginalAudioState, editorLayerOriginalAudio, editorLayerOriginalVolume,
  editorSceneOriginalAudio, editorAutoFindAsset, editorSceneGenerate,
  editorVoiceOpen, edVoiceFullPreview, editorAudioBusOpen, editorAudioBusApply,
  editorSubtitleStyleOpen, editorSubtitleWhiteOnBlack,
  editorMultiOriginal, editorMultiOriginalVolume, _sceneGenBox,
  edVoiceRegenerate, edVoiceReplaceUpload, edVoiceReplaceApply,
});

