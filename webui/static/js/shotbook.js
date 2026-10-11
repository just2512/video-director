// Editable Shotbook workbench: semantic beats + qualified character-time anchors.
let SHOTBOOK = null;
let SHOTBOOK_DIRTY = false;
const SB_ROLE_LABELS = {hook:'开场钩子',context:'背景交代',problem:'问题冲突',evidence:'证据呈现',turn:'认知转折',resolution:'解决方案',cta:'结尾行动',neutral:'普通叙述'};
const SB_INTENT_LABELS = {establish:'建立场景',illustrate:'解释/举例',contrast:'对比反差',prove:'证据支撑',emphasize:'重点强调',transition:'转场过渡',breathe:'留白停顿',close:'收束结尾'};
const SB_SHOT_LABELS = {footage:'实拍/素材',document:'文件/文献',screenshot:'屏幕截图',chart:'图表数据',title_card:'动态字卡',diagram:'示意图',product:'产品/物件',map:'地图',split_screen:'分屏对照',still:'静帧'};
function sbEsc(value){return String(value==null?'':value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function sbStatusClass(status){return status==='PASS'?'cap-ok':(status==='REJECT'||status==='MISSING'?'cap-off':'cap-warn');}
function sbGetProjectId(){return (window.ED&&ED.pid)||(document.getElementById('editor-project')||{}).value||'';}
async function shotbookLoad(discardDirty){
  const pid=sbGetProjectId(), status=document.getElementById('shotbook-status');
  if(!pid){if(status)status.textContent='先打开一个项目，再生成分镜。';return false;}
  if(SHOTBOOK_DIRTY&&!discardDirty&&!window.confirm('分镜有未保存修改。重新载入会丢弃这些修改，是否继续？'))return false;
  if(status)status.textContent='正在读取当前时间线……';
  const data=await apiGet('/api/editor/shotbook?project_id='+encodeURIComponent(pid));
  if(!data||data.ok===false||data.error){if(status)status.textContent='读取失败：'+((data&&data.error)||'服务暂不可用');return false;}
  SHOTBOOK=data;SHOTBOOK_DIRTY=false;shotbookRender();
  if(status)status.textContent=data.saved?'已载入已保存的分镜。修改后点“保存分镜”。':'当前项目还没有保存过分镜；可从当前时间线生成草案。';
  return true;
}
async function shotbookGenerate(){
  const pid=sbGetProjectId(),status=document.getElementById('shotbook-status');
  if(!pid){if(status)status.textContent='先打开一个项目，再生成分镜。';return;}
  if(SHOTBOOK_DIRTY&&!window.confirm('当前分镜有未保存修改；继续生成会丢弃页面上的修改，是否继续？'))return;
  if(!SHOTBOOK||SHOTBOOK.project_id!==pid){
    const loaded=await shotbookLoad(true);
    if(!loaded)return;
  }
  SHOTBOOK_DIRTY=false;
  if(status)status.textContent='正在按当前时间线生成分镜草案……';
  const r=await apiPost('/api/editor/shotbook',{project_id:pid,action:'generate',base_version:SHOTBOOK.version});
  if(!r||!r.ok){if(status)status.textContent='生成未完成：'+((r&&r.error)||'请求失败');if(r&&r.code==='CONFLICT')await shotbookLoad(true);return;}
  SHOTBOOK=r;SHOTBOOK_DIRTY=false;shotbookRender();
  if(status)status.textContent='已生成草案（版本 V'+r.version+'）。自动推断尚未通过人工验收，请逐镜核对后保存。';
}
function shotbookSelectOptions(values,current,labels){
  return values.map(function(v){return '<option value="'+sbEsc(v)+'"'+(v===current?' selected':'')+'>'+sbEsc(labels[v]||v)+'</option>';}).join('');
}
function shotbookRecipeOptions(shot){
  const recipes=(SHOTBOOK&&SHOTBOOK.recipes)||[],current=shot.shot_recipe_id||'';
  let list=recipes;
  // Registry 的 semantic_intents 值域（opening/data_highlight/…）与 visual_intent
  //（emphasize/transition/…）相交，与 semantic_role（hook/…/neutral）零相交——
  // 按 visual_intent 过滤，semantic_role 无匹配时保持全量，不隐藏任何可用配方。
  if(shot.visual_intent){const compatible=recipes.filter(function(r){return (r.semantic_intents||[]).includes(shot.visual_intent);});if(compatible.length)list=compatible;}
  return '<option value="">暂不指定配方</option>'+list.map(function(r){
    return '<option value="'+sbEsc(r.id)+'"'+(r.id===current?' selected':'')+'>'+sbEsc(r.name)+' · '+sbEsc(r.category)+' ('+sbEsc(r.id)+')</option>';
  }).join('');
}
function shotbookMotionOptions(shot){
  const motions=(SHOTBOOK&&SHOTBOOK.motions)||[],current=shot.motion_recipe_id||'';
  return '<option value="">不指定动效</option>'+motions.map(function(m){
    const note=(m.supported_for||[]).length?'':' · 当前渲染器不可直接执行';
    return '<option value="'+sbEsc(m.id)+'"'+(m.id===current?' selected':'')+'>'+sbEsc(m.name)+' · '+sbEsc(m.motion_type)+note+'</option>';
  }).join('');
}
function shotbookMotionLayerOptions(shot){
  const layers=shot.editable_layers||[],current=shot.motion_layer_target||'';
  if(!layers.length)return '<option value="">当前镜头没有图层</option>';
  const firstPlayable=layers.find(function(x){return !x.locked&&['image','video'].includes(x.type);});
  return layers.map(function(layer){
    const typeName=layer.type==='image'?'图片':layer.type==='video'?'视频':layer.type;
    const disabled=layer.locked?' disabled':'';
    const selected=(current?layer.target_id===current:firstPlayable&&layer.target_id===firstPlayable.target_id);
    return '<option value="'+sbEsc(layer.target_id)+'"'+(selected?' selected':'')+disabled+'>'+sbEsc(typeName)+' · '+sbEsc(layer.asset_id||'无素材')+(layer.locked?'（已锁定）':'')+'</option>';
  }).join('');
}
function shotbookMotionSupportNote(shot,motionId,layerType){
  const motions=(SHOTBOOK&&SHOTBOOK.motions)||[];
  const selected=motions.find(function(m){return m.id===(motionId===undefined?(shot.motion_recipe_id||''):motionId);});
  if(!selected)return '动效配方未指定。';
  if(selected.preview_only)return '该配方标记为仅预览，不能按已渲染效果交付。';
  if(!layerType){
    const candidate=(shot.editable_layers||[]).find(function(l){return !l.locked&&['image','video'].includes(l.type);});
    layerType=candidate&&candidate.type;
  }
  if(!(selected.supported_for||[]).includes(layerType||''))return '当前选择的动效/图层组合未被渲染器支持；应用按钮会拒绝执行。';
  return '当前选择组合可由媒体渲染器执行。点击“应用动效到时间线”才会真正修改当前图层。';
}
function shotbookRender(){
  if(!SHOTBOOK)return;
  const openTiming=new Set(Array.from(document.querySelectorAll('#shotbook-list .sb-char-details[open]')).map(function(item){const card=item.closest('.sb-shot');return card&&card.dataset.sceneKey;}).filter(Boolean));
  const status=document.getElementById('shotbook-status'),meta=document.getElementById('shotbook-meta');
  const gateBox=document.getElementById('shotbook-gates'),list=document.getElementById('shotbook-list');
  const board=document.getElementById('shotbook-storyboard');
  const note=document.getElementById('shotbook-timing-note');
  if(meta)meta.textContent='V'+SHOTBOOK.version+' · '+(SHOTBOOK.saved?'已保存':'尚未保存')+' · '+SHOTBOOK.shots.length+' 个镜头';
  if(note)note.textContent=SHOTBOOK.timing_note||'字级时间来自当前可用对齐数据。';
  if(gateBox)gateBox.innerHTML=(SHOTBOOK.gates||[]).map(function(g){
    return '<div class="sb-gate"><b>'+sbEsc(g.label)+'</b> <span class="'+sbStatusClass(g.status)+'">'+sbEsc(g.status)+'</span><div class="hint">'+sbEsc(g.detail)+'</div></div>';
  }).join('');
  if(!SHOTBOOK.shots.length){
    if(board)board.innerHTML='<div class="hint">当前项目还没有分镜帧。先在时间线上建立场景或导入旁白，再生成分镜。</div>';
    if(list)list.innerHTML='<div class="hint">当前项目还没有可编辑场景。先在时间线上建立场景或导入旁白。</div>';
    return;
  }
  // Storyboard thumbnails and the editable Shotbook share the saved plan and Composition assets.
  if(board)board.innerHTML=SHOTBOOK.shots.map(function(s,idx){
    const imageLayer=(s.editable_layers||[]).find(function(l){return l.asset_id&&l.type==='image';});
    const asset=imageLayer&&window.ED&&ED.comp&&ED.comp.assets&&ED.comp.assets[imageLayer.asset_id];
    const rawUrl=asset&&typeof asset.file_url==='string'?asset.file_url:'';
    const safeUrl=rawUrl.startsWith('/')||/^https?:\/\//i.test(rawUrl);
    const thumbnail=imageLayer&&asset&&safeUrl
      ? '<img loading="lazy" src="'+sbEsc(rawUrl)+'" alt="分镜 '+(idx+1)+' 的图片素材">'
      : '<div class="sb-board-placeholder"><b>'+String(idx+1).padStart(2,'0')+'</b><span>'+sbEsc(SB_SHOT_LABELS[s.shot_type]||s.shot_type||'镜头')+'</span><small>'+sbEsc(imageLayer?'图片缩略图不可用':'无已绑定图片；可在中央预览区查看')+'</small></div>';
    return '<article class="sb-board-card" data-scene-key="'+sbEsc(s.scene_id)+'"><div class="sb-board-thumb">'+thumbnail+'</div>' +
      '<div class="sb-board-info"><div class="sb-board-heading"><b>'+String(idx+1).padStart(2,'0')+'. '+sbEsc(s.scene_id)+'</b><span>'+Number(s.start).toFixed(2)+'–'+Number(s.end).toFixed(2)+'s</span></div>' +
      '<div class="sb-board-role">'+sbEsc(SB_ROLE_LABELS[s.semantic_role]||s.semantic_role||'未分类')+' · '+sbEsc(SB_INTENT_LABELS[s.visual_intent]||s.visual_intent||'未指定意图')+'</div>' +
      '<p>'+sbEsc(s.text||'暂无旁白/镜头文案')+'</p><div class="sb-board-actions">' +
      '<button class="btn-sm" type="button" data-shotbook-action="preview" data-scene-key="'+sbEsc(s.scene_id)+'">预览本镜</button>' +
      '<button class="btn-secondary btn-sm" type="button" data-shotbook-action="locate" data-scene-key="'+sbEsc(s.scene_id)+'">定位</button></div></div></article>';
  }).join('');
  if(!list)return;
  list.innerHTML=SHOTBOOK.shots.map(function(s,idx){
    const allChars=s.characters||[],chars=allChars.slice(0,240);
    const timingDetails=chars.length?'<details class="sb-char-details"><summary>校正字级锚点（显示 '+chars.length+(allChars.length>chars.length?' / '+allChars.length:'')+' 字；时间戳不是原生字级对齐）</summary><div class="sb-char-list">'+chars.map(function(c,i){
      return '<div class="sb-char" data-index="'+i+'" data-char="'+sbEsc(c.char)+'"><span class="sb-char-glyph">'+sbEsc(c.char)+'</span><label>起 <input class="sb-char-start" type="number" step="0.01" min="'+Number(s.start).toFixed(3)+'" max="'+Number(s.end).toFixed(3)+'" value="'+Number(c.start).toFixed(3)+'"></label><label>止 <input class="sb-char-end" type="number" step="0.01" min="'+Number(s.start).toFixed(3)+'" max="'+Number(s.end).toFixed(3)+'" value="'+Number(c.end).toFixed(3)+'"></label><span class="hint">'+(c.method==='manual'?'手工校正':c.method==='token_interpolation'?'词内插值':'场景估算')+'</span></div>';
    }).join('')+'</div>'+(allChars.length>chars.length?'<div class="hint">为避免一次渲染过多输入框，当前界面只显示前 240 字的可编辑锚点；剩余字符保持原估算。</div>':'')+'</details>':'<div class="hint">无可显示的字级锚点。</div>';
    return '<article class="sb-shot panel" data-scene-key="'+sbEsc(s.scene_id)+'" style="margin-top:8px;padding:10px"><div class="sb-shot-head"><b>'+(idx+1)+'. '+sbEsc(s.scene_id)+' · '+Number(s.start).toFixed(2)+'–'+Number(s.end).toFixed(2)+'s</b><button class="btn-secondary btn-sm" type="button" data-shotbook-action="locate" data-scene-key="'+sbEsc(s.scene_id)+'">在时间线上定位</button></div><div class="sb-script">'+sbEsc(s.text)+'</div><div class="hint">当前时间线实际动效：'+sbEsc(s.applied_motion_recipe_id||'未应用')+'</div><div class="sb-label">语义角色</div><select class="sb-role">'+shotbookSelectOptions(Object.keys(SB_ROLE_LABELS),s.semantic_role,SB_ROLE_LABELS)+'</select><div class="sb-label">画面意图</div><select class="sb-intent">'+shotbookSelectOptions(Object.keys(SB_INTENT_LABELS),s.visual_intent,SB_INTENT_LABELS)+'</select><div class="sb-label">镜头类型</div><select class="sb-type">'+shotbookSelectOptions(Object.keys(SB_SHOT_LABELS),s.shot_type,SB_SHOT_LABELS)+'</select><div class="sb-label">镜头配方（来自当前项目 Shot Registry）</div><select class="sb-recipe">'+shotbookRecipeOptions(s)+'</select><div class="sb-label">动效配方</div><select class="sb-motion">'+shotbookMotionOptions(s)+'</select><div class="sb-label">应用到图层</div><select class="sb-motion-layer">'+shotbookMotionLayerOptions(s)+'</select><div class="hint sb-motion-note">'+sbEsc(shotbookMotionSupportNote(s,s.motion_recipe_id,((s.editable_layers||[]).find(function(l){return l.target_id===s.motion_layer_target;})||{}).type))+'</div><div class="btn-row"><button class="btn-secondary btn-sm" type="button" data-shotbook-action="apply-motion" data-scene-key="'+sbEsc(s.scene_id)+'">应用动效到时间线</button></div><div class="hint">仅当前渲染器明确支持的动效/图层组合可应用；其他配方只作为设计参考，不会被假称已渲染。</div><div class="sb-form-grid"><label>动态字卡文案<input class="sb-overlay" maxlength="120" value="'+sbEsc(s.overlay_text)+'" placeholder="可留空；不会自动覆盖旁白字幕"></label><label>证据引用<input class="sb-evidence" maxlength="300" value="'+sbEsc(s.evidence_ref)+'" placeholder="截图/文档/URL/项目素材 ID"></label><label>音效提示<input class="sb-sfx" maxlength="160" value="'+sbEsc(s.sfx_cue)+'" placeholder="如：落点轻击、翻页声；这是设计提示，不是已选音频"></label><button class="btn-secondary btn-sm" type="button" data-shotbook-action="open-sfx" data-scene-key="'+sbEsc(s.scene_id)+'">选择真实音效并加入时间线</button></div><label class="sb-notes-label">镜头说明<textarea class="sb-notes" maxlength="500" rows="2" placeholder="画面要证明什么、主体/构图/动作、避开的表达">'+sbEsc(s.notes)+'</textarea></label>'+timingDetails+'<label class="sb-review"><input class="sb-reviewed" type="checkbox"'+(s.reviewed?' checked':'')+'> 我已核对这镜的语义、画面意图与时间锚点</label><div class="btn-row" style="margin-top:8px"><button class="btn-secondary btn-sm" type="button" data-shotbook-action="apply-recipe" data-scene-key="'+sbEsc(s.scene_id)+'">应用镜头配方到时间线</button></div>'+(!s.has_bound_asset?'<div class="hint">提示：当前场景没有绑定素材。应用配方时将检查槽位；缺少素材不会被静默补成假素材。</div>':'')+'</article>';
  }).join('');
  Array.from(list.querySelectorAll('.sb-char-details')).forEach(function(item){const card=item.closest('.sb-shot');if(card&&openTiming.has(card.dataset.sceneKey))item.open=true;});
}
function shotbookCollect(){
  if(!SHOTBOOK)return [];
  return Array.from(document.querySelectorAll('#shotbook-list .sb-shot')).map(function(card){
    const old=SHOTBOOK.shots.find(function(s){return s.scene_id===card.dataset.sceneKey;})||{};
    const anchors=(old.characters||[]).map(function(anchor,i){
      const row=card.querySelector('.sb-char[data-index="'+i+'"]');if(!row)return anchor;
      return Object.assign({},anchor,{start:Number(row.querySelector('.sb-char-start').value),end:Number(row.querySelector('.sb-char-end').value)});
    });
    return {scene_id:card.dataset.sceneKey,semantic_role:card.querySelector('.sb-role').value,visual_intent:card.querySelector('.sb-intent').value,shot_type:card.querySelector('.sb-type').value,shot_recipe_id:card.querySelector('.sb-recipe').value,motion_recipe_id:card.querySelector('.sb-motion').value,motion_layer_target:card.querySelector('.sb-motion-layer').value,overlay_text:card.querySelector('.sb-overlay').value,evidence_ref:card.querySelector('.sb-evidence').value,sfx_cue:card.querySelector('.sb-sfx').value,notes:card.querySelector('.sb-notes').value,reviewed:card.querySelector('.sb-reviewed').checked,characters:anchors};
  });
}
async function shotbookSave(){
  const pid=sbGetProjectId(),status=document.getElementById('shotbook-status');
  if(!pid||!SHOTBOOK){if(status)status.textContent='先载入或生成分镜草案。';return;}
  if(SHOTBOOK.project_id!==pid){if(status)status.textContent='当前编辑器已切换到其他项目；先载入该项目的分镜，避免把修改写错项目。';return;}
  const r=await apiPost('/api/editor/shotbook',{project_id:pid,action:'save',base_version:SHOTBOOK.version,shots:shotbookCollect()});
  if(!r||!r.ok){if(status)status.textContent='保存未完成：'+((r&&r.error)||'请求失败')+'。当前修改仍留在页面中。';const reload=document.getElementById('shotbook-reload-conflict');if(reload&&r&&r.code==='CONFLICT')reload.style.display='';return;}
  SHOTBOOK=r;SHOTBOOK_DIRTY=false;shotbookRender();if(status)status.textContent='分镜已保存为 V'+r.version+'。语义确认、字级校正和配方关联已写入项目。';
}
async function shotbookApplyMotion(sceneId){
  const card=document.querySelector('#shotbook-list .sb-shot[data-scene-key="'+CSS.escape(sceneId)+'"]');
  const status=document.getElementById('shotbook-status');
  if(!card||!SHOTBOOK)return;
  const motionId=card.querySelector('.sb-motion').value;
  const targetId=card.querySelector('.sb-motion-layer').value;
  const motion=(SHOTBOOK.motions||[]).find(function(m){return m.id===motionId;});
  const shot=SHOTBOOK.shots.find(function(s){return s.scene_id===sceneId;});
  const layer=(shot&&shot.editable_layers||[]).find(function(l){return l.target_id===targetId;});
  if(!motionId||!motion){if(status)status.textContent='先选择有效的动效配方。';return;}
  if(!layer){if(status)status.textContent='当前场景没有可用目标图层。';return;}
  if(layer.locked){if(status)status.textContent='目标图层已锁定；先在 Inspector 解锁，再应用动效。';return;}
  if(!(motion.supported_for||[]).includes(layer.type)){
    if(status)status.textContent='当前渲染器不能把该配方应用到此图层（'+layer.type+'）；时间线未修改。';
    return;
  }
  if(status)status.textContent='正在通过 Editor Command 应用动效并创建 Revision……';
  const result=await editorCmd('replace_motion',{motionRecipeId:motion.id,motionVersion:motion.version,duration:motion.duration_recommended},targetId);
  if(!result||!result.ok){if(status)status.textContent='动效未应用：'+((result&&result.error)||'应用失败')+'。时间线保持原样。';return;}
  shot.motion_recipe_id=motion.id;shot.motion_layer_target=targetId;shot.reviewed=false;
  const reviewed=card.querySelector('.sb-reviewed');if(reviewed)reviewed.checked=false;
  SHOTBOOK_DIRTY=true;
  if(typeof refreshComposition==='function')await refreshComposition();
  await shotbookSave();
}
async function shotbookApplyRecipe(sceneId){
  const card=document.querySelector('#shotbook-list .sb-shot[data-scene-key="'+CSS.escape(sceneId)+'"]'),status=document.getElementById('shotbook-status');
  if(!card)return;
  const recipeId=card.querySelector('.sb-recipe').value,recipe=(SHOTBOOK.recipes||[]).find(function(r){return r.id===recipeId;});
  if(!recipeId||!recipe){if(status)status.textContent='先为这镜选择一个有效的 Shot Registry 配方。';return;}
  if(status)status.textContent='正在检查素材槽位并应用配方……';
  const result=await editorCmd('replace_recipe',{recipeId:recipeId,recipeVersion:recipe.version},sceneId);
  if(!result||!result.ok){if(status)status.textContent='配方未应用，时间线没有因此被覆盖：'+((result&&result.error)||'应用失败');return;}
  const shot=SHOTBOOK.shots.find(function(s){return s.scene_id===sceneId;});if(shot){shot.shot_recipe_id=recipeId;shot.reviewed=false;}
  const reviewed=card.querySelector('.sb-reviewed');if(reviewed)reviewed.checked=false;
  SHOTBOOK_DIRTY=true;
  if(typeof refreshComposition==='function')await refreshComposition();
  await shotbookSave();
}
function shotbookPreviewScene(sceneId){
  const shot=SHOTBOOK&&SHOTBOOK.shots&&SHOTBOOK.shots.find(function(s){return s.scene_id===sceneId;});
  const status=document.getElementById('shotbook-status');
  if(!shot){if(status)status.textContent='找不到这镜的分镜数据；请重新载入分镜。';return;}
  if(window.SB_PREVIEW_TIMER){clearInterval(window.SB_PREVIEW_TIMER);window.SB_PREVIEW_TIMER=null;}
  if(typeof previewPause==='function')previewPause();
  if(typeof shotbookLocate==='function')shotbookLocate(sceneId);
  const start=Math.max(0,Number(shot.start)||0),end=Math.max(start,Number(shot.end)||start);
  if(end<=start){if(status)status.textContent='该镜头时间区间无效，无法逐镜预览。';return;}
  if(typeof previewSeek!=='function'||typeof previewPlay!=='function'){if(status)status.textContent='中央预览控制尚未就绪。';return;}
  previewSeek(start);
  previewPlay();
  if(status)status.textContent='正在预览 '+sceneId+'（'+start.toFixed(2)+'–'+end.toFixed(2)+'s）；到本镜结束时会自动停止。';
  const begun=Date.now();
  window.SB_PREVIEW_TIMER=window.setInterval(function(){
    const pv=window.PV||{};
    const video=pv.mode==='draft'?document.querySelector('#ed-preview-stage video'):null;
    const at=video?(Number(video.currentTime)||0):(Number(pv.playhead)||0);
    const finished=at>=end-0.025;
    const stopped=Date.now()-begun>200&&(video?video.paused:!pv.playing);
    if(!finished&&!stopped)return;
    if(window.SB_PREVIEW_TIMER){clearInterval(window.SB_PREVIEW_TIMER);window.SB_PREVIEW_TIMER=null;}
    if(finished&&typeof previewPause==='function')previewPause();
    if(status)status.textContent=finished?'本镜预览结束：'+sceneId+'。可预览下一镜或回到时间线继续编辑。':'本镜预览已停止。';
  },40);
}

let SHOTBOOK_SFX_ASSETS = [];
let SHOTBOOK_SFX_SCENE = "";

function shotbookSfxModalClose() {
  const modal = document.getElementById("shotbook-sfx-modal");
  if (modal) modal.remove();
}
function shotbookSfxCurrentScene() {
  return (SHOTBOOK && SHOTBOOK.shots || []).find(function(shot) {
    return shot.scene_id === SHOTBOOK_SFX_SCENE;
  }) || null;
}
function shotbookSfxRefreshPreview() {
  const select = document.getElementById("shotbook-sfx-asset");
  const audio = document.getElementById("shotbook-sfx-preview");
  const durationInput = document.getElementById("shotbook-sfx-duration");
  if (!select) return;
  const asset = SHOTBOOK_SFX_ASSETS.find(function(item) { return item.id === select.value; });
  if (audio) audio.src = asset ? "/api/audio_preview?asset_id=" + encodeURIComponent(asset.id) : "";
  if (durationInput && asset && Number.isFinite(Number(asset.duration)) && Number(asset.duration) > 0) {
    const scene = shotbookSfxCurrentScene();
    const total = Number((window.ED && ED.comp && ED.comp.duration) || 0);
    const start = Number((document.getElementById("shotbook-sfx-start") || {}).value || (scene && scene.start) || 0);
    const maxOnTimeline = total > start ? total - start : Number(asset.duration);
    durationInput.max = String(Math.min(Number(asset.duration), maxOnTimeline));
    if (!durationInput.value || Number(durationInput.value) > Number(durationInput.max)) {
      durationInput.value = Math.min(Number(asset.duration), maxOnTimeline).toFixed(2);
    }
  }
}
async function shotbookOpenSfx(sceneId) {
  const shot = (SHOTBOOK && SHOTBOOK.shots || []).find(function(item) { return item.scene_id === sceneId; });
  const status = document.getElementById("shotbook-status");
  if (!shot) {
    if (status) status.textContent = "找不到对应分镜；请重新载入分镜后再加音效。";
    return;
  }
  shotbookSfxModalClose();
  SHOTBOOK_SFX_SCENE = sceneId;
  if (status) status.textContent = "正在读取本机音效库……";
  try {
    const data = await apiGet("/api/sfx");
    if (!Array.isArray(data)) throw new Error((data && data.error) || "音效清单读取失败");
    SHOTBOOK_SFX_ASSETS = data.filter(function(asset) { return asset.available !== false; });
    const remaining = Math.max(0.05, Number((window.ED && ED.comp && ED.comp.duration) || shot.end) - Number(shot.start || 0));
    const cueCard = Array.from(document.querySelectorAll("#shotbook-list .sb-shot")).find(function(card) { return card.dataset.sceneKey === sceneId; });
    const cue = cueCard ? (cueCard.querySelector(".sb-sfx") || {}).value || shot.sfx_cue || "" : (shot.sfx_cue || "");
    const modal = document.createElement("div");
    modal.id = "shotbook-sfx-modal";
    modal.className = "sb-sfx-mask";
    const options = SHOTBOOK_SFX_ASSETS.map(function(asset, index) {
      const duration = Number(asset.duration);
      return '<option value="' + sbEsc(asset.id) + '"' + (index === 0 ? " selected" : "") + '>' +
        sbEsc(asset.label || asset.id) + (Number.isFinite(duration) && duration > 0 ? " · " + duration.toFixed(2) + "s" : "") +
        (asset.tags && asset.tags.length ? " · " + sbEsc(asset.tags.join("、")) : "") + '</option>';
    }).join("");
    modal.innerHTML = '<section class="sb-sfx-dialog" role="dialog" aria-modal="true" aria-labelledby="sb-sfx-title">' +
      '<header class="sb-sfx-head"><div><h3 id="sb-sfx-title">为 ' + sbEsc(sceneId) + ' 添加真实音效</h3><p>分镜提示：' + sbEsc(cue || "未填写") + '。请试听并选择实际音频素材；提示文字本身不会被当成声音。</p></div>' +
      '<button class="btn-secondary btn-sm" type="button" data-sfx-close>关闭</button></header>' +
      (SHOTBOOK_SFX_ASSETS.length ? '<div class="sb-sfx-fields"><label>音效素材<select id="shotbook-sfx-asset">' + options + '</select></label>' +
      '<audio id="shotbook-sfx-preview" controls preload="none"></audio>' +
      '<div class="sb-sfx-grid"><label>时间线起点（秒）<input id="shotbook-sfx-start" type="number" min="0" max="' + Number((window.ED && ED.comp && ED.comp.duration) || shot.end).toFixed(3) + '" step="0.01" value="' + Number(shot.start || 0).toFixed(2) + '"></label>' +
      '<label>片段时长（秒）<input id="shotbook-sfx-duration" type="number" min="0.05" max="' + remaining.toFixed(3) + '" step="0.01" value="0.50"></label>' +
      '<label>音量<input id="shotbook-sfx-volume" type="number" min="0" max="2" step="0.05" value="0.60"></label>' +
      '<label>淡入（秒）<input id="shotbook-sfx-fade-in" type="number" min="0" step="0.01" value="0.02"></label>' +
      '<label>淡出（秒）<input id="shotbook-sfx-fade-out" type="number" min="0" step="0.01" value="0.08"></label></div>' +
      '<div id="shotbook-sfx-status" class="hint" role="status" aria-live="polite">试听后确认起点与片段长度。添加后音效将出现在统一时间线上，可继续拖动、修剪与调整混音。</div>' +
      '<footer class="sb-sfx-actions"><button class="btn-secondary btn-sm" type="button" data-sfx-close>取消</button><button class="btn-sm" type="button" data-sfx-add>加入可编辑音轨</button></footer></div>' :
      '<div class="sb-sfx-empty">本机音效库没有可用 SFX 文件。请先导入或配置真实音效素材；不会用提示文字或静音片段代替。</div>') +
      '</section>';
    modal.addEventListener("click", function(event) { if (event.target === modal || event.target.closest("[data-sfx-close]")) shotbookSfxModalClose(); });
    modal.addEventListener("change", function(event) {
      if (event.target.id === "shotbook-sfx-asset" || event.target.id === "shotbook-sfx-start") shotbookSfxRefreshPreview();
    });
    modal.addEventListener("click", function(event) {
      if (event.target.closest("[data-sfx-add]")) shotbookAddSfx();
    });
    document.body.appendChild(modal);
    shotbookSfxRefreshPreview();
    if (status) status.textContent = SHOTBOOK_SFX_ASSETS.length
      ? "已打开真实音效选择器。分镜提示仅用于辅助判断，必须选择音效库中的实际文件。"
      : "音效库为空；没有创建占位音轨。";
  } catch (error) {
    if (status) status.textContent = "音效库读取失败：" + String(error.message || error);
  }
}
async function shotbookAddSfx() {
  const modal = document.getElementById("shotbook-sfx-modal");
  const status = document.getElementById("shotbook-sfx-status");
  const pid = sbGetProjectId();
  const scene = shotbookSfxCurrentScene();
  const assetSelect = document.getElementById("shotbook-sfx-asset");
  if (!modal || !scene || !pid || !assetSelect || !assetSelect.value) {
    if (status) status.textContent = "请先选择一个真实音效素材并确认当前项目已打开。";
    return;
  }
  const asset = SHOTBOOK_SFX_ASSETS.find(function(item) { return item.id === assetSelect.value; });
  if (!asset) {
    if (status) status.textContent = "所选音效已不在当前音效清单中，请重新选择。";
    return;
  }
  const numeric = function(id, fallback) {
    const element = document.getElementById(id);
    const value = Number(element ? element.value : fallback);
    return Number.isFinite(value) ? value : NaN;
  };
  const payload = {
    assetId: asset.id,
    start: numeric("shotbook-sfx-start", scene.start),
    duration: numeric("shotbook-sfx-duration", Math.min(Number(asset.duration) || 0.5, Number(ED.comp.duration) - scene.start)),
    volume: numeric("shotbook-sfx-volume", 0.6),
    fadeIn: numeric("shotbook-sfx-fade-in", 0.02),
    fadeOut: numeric("shotbook-sfx-fade-out", 0.08)
  };
  if (Object.values(payload).some(function(value) { return typeof value === "number" && !Number.isFinite(value); })) {
    if (status) status.textContent = "请检查起点、时长、音量和淡入淡出，必须填写有效数字。";
    return;
  }
  const total = Number((window.ED && ED.comp && ED.comp.duration) || 0);
  if (payload.start < 0 || payload.start >= total || payload.duration <= 0 ||
      payload.start + payload.duration > total + 1e-6 ||
      (Number(asset.duration) > 0 && payload.duration > Number(asset.duration)) ||
      payload.volume < 0 || payload.volume > 2 ||
      payload.fadeIn < 0 || payload.fadeIn > payload.duration ||
      payload.fadeOut < 0 || payload.fadeOut > payload.duration) {
    if (status) status.textContent = "时间或混音参数越界；请保证片段处于项目和素材时长范围内。";
    return;
  }
  const version = Number(window.ED && ED.version);
  if (!Number.isInteger(version) || version < 1) {
    if (status) status.textContent = "无法读取当前时间线版本；请重新载入工作台后重试，避免覆盖旧修改。";
    return;
  }
  const button = modal.querySelector("[data-sfx-add]");
  if (button) { button.disabled = true; button.textContent = "正在加入……"; }
  if (status) status.textContent = "服务端正在校验音效文件并写入 Composition Revision……";
  try {
    const result = await apiPost("/api/editor/command", {
      project_id: pid,
      command: "add_audio_clip",
      targetId: "sfx",
      payload,
      expectedVersion: version,
      author: "user"
    });
    if (!result || !result.ok) {
      if (button) { button.disabled = false; button.textContent = "加入可编辑音轨"; }
      const details = (result && result.errors || []).map(function(item) { return item.message || item.code; }).filter(Boolean).join("；");
      if (status) status.textContent = "音效没有加入：" + (details || (result && result.error) || "服务端拒绝操作");
      if (result && (result.status === "CONFLICT" || (result.errors || []).some(function(item) { return item.code === "CONFLICT"; }))) {
        if (typeof refreshWorkspace === "function") await refreshWorkspace();
      }
      return;
    }
    if (typeof result.composition_version === "number" && window.ED) ED.version = result.composition_version;
    if (result.composition && window.ED) ED.comp = result.composition;
    shotbookSfxModalClose();
    if (typeof refreshWorkspace === "function") await refreshWorkspace();
    else if (typeof editorLoad === "function") await editorLoad();
    const finalStatus = document.getElementById("shotbook-status");
    if (finalStatus) finalStatus.textContent = "已把真实音效 " + (asset.label || asset.id) + " 加入统一时间线（" +
      payload.start.toFixed(2) + "–" + (payload.start + payload.duration).toFixed(2) + " 秒）。现在可以拖动、修剪、调音量与淡入淡出；分镜的音效提示仍是独立的设计备注。";
  } catch (error) {
    if (button) { button.disabled = false; button.textContent = "加入可编辑音轨"; }
    if (status) status.textContent = "添加失败：" + String(error.message || error);
  }
}

function shotbookLocate(sceneId){
  if(typeof selectScene==='function')selectScene(sceneId,null,'shotbook');
  if(typeof timelineScrollToScene==='function')timelineScrollToScene(sceneId);
  // 定位=播放头也跳到该场景起点（预览帧与时间轴一起指到用户定位的镜头）
  if(typeof previewSeek==='function'){
    const sc=(window.ED&&ED.comp&&ED.comp.scenes||[]).find(function(s){return s.id===sceneId;});
    if(sc&&typeof previewPause==='function')previewPause();
    if(sc)previewSeek(Number(sc.start)||0);
  }
  if(typeof edShowInspector==='function')edShowInspector();
}
async function shotbookReloadConflict(){if(SHOTBOOK_DIRTY&&!window.confirm('重新载入会丢弃当前未保存修改。是否继续？'))return;SHOTBOOK_DIRTY=false;const button=document.getElementById('shotbook-reload-conflict');if(button)button.style.display='none';await shotbookLoad(true);}
// Delegate UI handlers so the existing UI Action Audit covers this workbench.
const shotbookPanel=document.getElementById('shotbook-details');
if(shotbookPanel&&!shotbookPanel.dataset.autoLoadBound){
  shotbookPanel.addEventListener('toggle',function(){if(shotbookPanel.open&&(!SHOTBOOK||SHOTBOOK.project_id!==sbGetProjectId()))shotbookLoad();});
  shotbookPanel.dataset.autoLoadBound='1';
}
if(!window.__shotbookDelegated){
  function markShotbookDirty(event, requireReview){
    const target=event.target,card=target.closest&&target.closest('#shotbook-list .sb-shot');
    SHOTBOOK_DIRTY=true;
    if(card&&requireReview){const reviewed=card.querySelector('.sb-reviewed');if(reviewed)reviewed.checked=false;}
    const status=document.getElementById('shotbook-status');
    if(status)status.textContent='有未保存修改。修改语义、时间或配方会取消这镜的已确认状态；保存后再做分镜验收。';
    const meta=document.getElementById('shotbook-meta');
    if(meta&&SHOTBOOK)meta.textContent='V'+SHOTBOOK.version+' · 有未保存修改 · '+SHOTBOOK.shots.length+' 个镜头';
  }
  document.addEventListener('input',function(event){
    if(event.target.matches('#shotbook-list .sb-char-start,#shotbook-list .sb-char-end,#shotbook-list .sb-overlay,#shotbook-list .sb-evidence,#shotbook-list .sb-sfx,#shotbook-list .sb-notes'))markShotbookDirty(event,true);
  });

  document.addEventListener('click',function(event){
    const button=event.target.closest('[data-shotbook-action]');
    if(!button)return;
    const action=button.dataset.shotbookAction,sceneId=button.dataset.sceneKey||'';
    if(action==='load')shotbookLoad();
    else if(action==='generate')shotbookGenerate();
    else if(action==='save')shotbookSave();
    else if(action==='reload-conflict')shotbookReloadConflict();
    else if(action==='preview')shotbookPreviewScene(sceneId);
    else if(action==='open-sfx')shotbookOpenSfx(sceneId);
    else if(action==='locate')shotbookLocate(sceneId);
    else if(action==='apply-recipe')shotbookApplyRecipe(sceneId);
    else if(action==='apply-motion')shotbookApplyMotion(sceneId);
  });
  document.addEventListener('change',function(event){
    const target=event.target,card=target.closest('#shotbook-list .sb-shot');
    if(!card)return;
    const shot=SHOTBOOK&&SHOTBOOK.shots.find(function(item){return item.scene_id===card.dataset.sceneKey;});
    if(!shot)return;
    if(target.matches('.sb-reviewed')){markShotbookDirty(event,false);return;}
    if(target.matches('.sb-role,.sb-intent,.sb-type,.sb-recipe,.sb-motion,.sb-motion-layer'))markShotbookDirty(event,true);
    if(target.matches('.sb-intent')){
      const select=card.querySelector('.sb-recipe'),previous=select.value;
      select.innerHTML=shotbookRecipeOptions(Object.assign({},shot,{visual_intent:target.value}));
      if(Array.from(select.options).some(function(opt){return opt.value===previous;}))select.value=previous;
    }
    if(target.matches('.sb-motion')||target.matches('.sb-motion-layer')){
      const motionId=card.querySelector('.sb-motion').value,layerId=card.querySelector('.sb-motion-layer').value;
      const layer=(shot.editable_layers||[]).find(function(item){return item.target_id===layerId;});
      const note=card.querySelector('.sb-motion-note');
      if(note)note.textContent=shotbookMotionSupportNote(shot,motionId,layer&&layer.type);
    }
  });
  window.__shotbookDelegated=true;
}
window.addEventListener('beforeunload',function(event){if(SHOTBOOK_DIRTY){event.preventDefault();event.returnValue='';}});
Object.assign(window,{shotbookLoad,shotbookGenerate,shotbookSave,shotbookApplyRecipe,shotbookApplyMotion,shotbookPreviewScene,shotbookLocate,shotbookReloadConflict,shotbookOpenSfx,shotbookAddSfx,shotbookSfxModalClose});
