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
  if(shot.semantic_role){const compatible=recipes.filter(function(r){return (r.semantic_intents||[]).includes(shot.semantic_role);});if(compatible.length)list=compatible;}
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
  const note=document.getElementById('shotbook-timing-note');
  if(meta)meta.textContent='V'+SHOTBOOK.version+' · '+(SHOTBOOK.saved?'已保存':'尚未保存')+' · '+SHOTBOOK.shots.length+' 个镜头';
  if(note)note.textContent=SHOTBOOK.timing_note||'字级时间来自当前可用对齐数据。';
  if(gateBox)gateBox.innerHTML=(SHOTBOOK.gates||[]).map(function(g){
    return '<div class="sb-gate"><b>'+sbEsc(g.label)+'</b> <span class="'+sbStatusClass(g.status)+'">'+sbEsc(g.status)+'</span><div class="hint">'+sbEsc(g.detail)+'</div></div>';
  }).join('');
  if(!list)return;
  if(!SHOTBOOK.shots.length){list.innerHTML='<div class="hint">当前项目还没有可编辑场景。先在时间线上建立场景或导入旁白。</div>';return;}
  list.innerHTML=SHOTBOOK.shots.map(function(s,idx){
    const allChars=s.characters||[],chars=allChars.slice(0,240);
    const timingDetails=chars.length?'<details class="sb-char-details"><summary>校正字级锚点（显示 '+chars.length+(allChars.length>chars.length?' / '+allChars.length:'')+' 字；时间戳不是原生字级对齐）</summary><div class="sb-char-list">'+chars.map(function(c,i){
      return '<div class="sb-char" data-index="'+i+'" data-char="'+sbEsc(c.char)+'"><span class="sb-char-glyph">'+sbEsc(c.char)+'</span><label>起 <input class="sb-char-start" type="number" step="0.01" min="'+Number(s.start).toFixed(3)+'" max="'+Number(s.end).toFixed(3)+'" value="'+Number(c.start).toFixed(3)+'"></label><label>止 <input class="sb-char-end" type="number" step="0.01" min="'+Number(s.start).toFixed(3)+'" max="'+Number(s.end).toFixed(3)+'" value="'+Number(c.end).toFixed(3)+'"></label><span class="hint">'+(c.method==='manual'?'手工校正':c.method==='token_interpolation'?'词内插值':'场景估算')+'</span></div>';
    }).join('')+'</div>'+(allChars.length>chars.length?'<div class="hint">为避免一次渲染过多输入框，当前界面只显示前 240 字的可编辑锚点；剩余字符保持原估算。</div>':'')+'</details>':'<div class="hint">无可显示的字级锚点。</div>';
    return '<article class="sb-shot panel" data-scene-key="'+sbEsc(s.scene_id)+'" style="margin-top:8px;padding:10px"><div class="sb-shot-head"><b>'+(idx+1)+'. '+sbEsc(s.scene_id)+' · '+Number(s.start).toFixed(2)+'–'+Number(s.end).toFixed(2)+'s</b><button class="btn-secondary btn-sm" type="button" data-shotbook-action="locate" data-scene-key="'+sbEsc(s.scene_id)+'">在时间线上定位</button></div><div class="sb-script">'+sbEsc(s.text)+'</div><div class="hint">当前时间线实际动效：'+sbEsc(s.applied_motion_recipe_id||'未应用')+'</div><div class="sb-label">语义角色</div><select class="sb-role">'+shotbookSelectOptions(Object.keys(SB_ROLE_LABELS),s.semantic_role,SB_ROLE_LABELS)+'</select><div class="sb-label">画面意图</div><select class="sb-intent">'+shotbookSelectOptions(Object.keys(SB_INTENT_LABELS),s.visual_intent,SB_INTENT_LABELS)+'</select><div class="sb-label">镜头类型</div><select class="sb-type">'+shotbookSelectOptions(Object.keys(SB_SHOT_LABELS),s.shot_type,SB_SHOT_LABELS)+'</select><div class="sb-label">镜头配方（来自当前项目 Shot Registry）</div><select class="sb-recipe">'+shotbookRecipeOptions(s)+'</select><div class="sb-label">动效配方</div><select class="sb-motion">'+shotbookMotionOptions(s)+'</select><div class="sb-label">应用到图层</div><select class="sb-motion-layer">'+shotbookMotionLayerOptions(s)+'</select><div class="hint sb-motion-note">'+sbEsc(shotbookMotionSupportNote(s,s.motion_recipe_id,((s.editable_layers||[]).find(function(l){return l.target_id===s.motion_layer_target;})||{}).type))+'</div><div class="btn-row"><button class="btn-secondary btn-sm" type="button" data-shotbook-action="apply-motion" data-scene-key="'+sbEsc(s.scene_id)+'">应用动效到时间线</button></div><div class="hint">仅当前渲染器明确支持的动效/图层组合可应用；其他配方只作为设计参考，不会被假称已渲染。</div><div class="sb-form-grid"><label>动态字卡文案<input class="sb-overlay" maxlength="120" value="'+sbEsc(s.overlay_text)+'" placeholder="可留空；不会自动覆盖旁白字幕"></label><label>证据引用<input class="sb-evidence" maxlength="300" value="'+sbEsc(s.evidence_ref)+'" placeholder="截图/文档/URL/项目素材 ID"></label><label>音效提示<input class="sb-sfx" maxlength="160" value="'+sbEsc(s.sfx_cue)+'" placeholder="如：落点轻击、翻页声；仅记录设计意图"></label></div><label class="sb-notes-label">镜头说明<textarea class="sb-notes" maxlength="500" rows="2" placeholder="画面要证明什么、主体/构图/动作、避开的表达">'+sbEsc(s.notes)+'</textarea></label>'+timingDetails+'<label class="sb-review"><input class="sb-reviewed" type="checkbox"'+(s.reviewed?' checked':'')+'> 我已核对这镜的语义、画面意图与时间锚点</label><div class="btn-row" style="margin-top:8px"><button class="btn-secondary btn-sm" type="button" data-shotbook-action="apply-recipe" data-scene-key="'+sbEsc(s.scene_id)+'">应用镜头配方到时间线</button></div>'+(!s.has_bound_asset?'<div class="hint">提示：当前场景没有绑定素材。应用配方时将检查槽位；缺少素材不会被静默补成假素材。</div>':'')+'</article>';
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
  shot.motion_recipe_id=motion.id;shot.motion_layer_target=targetId;
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
  const shot=SHOTBOOK.shots.find(function(s){return s.scene_id===sceneId;});if(shot)shot.shot_recipe_id=recipeId;
  if(typeof refreshComposition==='function')await refreshComposition();
  await shotbookSave();
}
function shotbookLocate(sceneId){
  if(typeof selectScene==='function')selectScene(sceneId,null,'shotbook');
  if(typeof timelineScrollToScene==='function')timelineScrollToScene(sceneId);
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
    if(target.matches('.sb-role')){
      const select=card.querySelector('.sb-recipe'),previous=select.value;
      select.innerHTML=shotbookRecipeOptions(Object.assign({},shot,{semantic_role:target.value}));
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
Object.assign(window,{shotbookLoad,shotbookGenerate,shotbookSave,shotbookApplyRecipe,shotbookApplyMotion,shotbookLocate,shotbookReloadConflict});
