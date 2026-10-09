// Editable Shotbook workbench: semantic beats + qualified character-time anchors.
let SHOTBOOK = null;
const SB_ROLE_LABELS = {hook:'开场钩子',context:'背景交代',problem:'问题冲突',evidence:'证据呈现',turn:'认知转折',resolution:'解决方案',cta:'结尾行动',neutral:'普通叙述'};
const SB_INTENT_LABELS = {establish:'建立场景',illustrate:'解释/举例',contrast:'对比反差',prove:'证据支撑',emphasize:'重点强调',transition:'转场过渡',breathe:'留白停顿',close:'收束结尾'};
const SB_SHOT_LABELS = {footage:'实拍/素材',document:'文件/文献',screenshot:'屏幕截图',chart:'图表数据',title_card:'动态字卡',diagram:'示意图',product:'产品/物件',map:'地图',split_screen:'分屏对照',still:'静帧'};
function sbEsc(value){return String(value==null?'':value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function sbStatusClass(status){return status==='PASS'?'cap-ok':(status==='REJECT'||status==='MISSING'?'cap-off':'cap-warn');}
function sbGetProjectId(){return (window.ED&&ED.pid)||(document.getElementById('editor-project')||{}).value||'';}
async function shotbookLoad(){
  const pid=sbGetProjectId(), status=document.getElementById('shotbook-status');
  if(!pid){if(status)status.textContent='先打开一个项目，再生成分镜。';return;}
  if(status)status.textContent='正在读取当前时间线……';
  const data=await apiGet('/api/editor/shotbook?project_id='+encodeURIComponent(pid));
  if(!data||data.ok===false||data.error){if(status)status.textContent='读取失败：'+((data&&data.error)||'服务暂不可用');return;}
  SHOTBOOK=data;shotbookRender();
  if(status)status.textContent=data.saved?'已载入已保存的分镜。修改后点“保存分镜”。':'当前为未保存草案；点“生成/更新草案”创建项目分镜。';
}
async function shotbookGenerate(){
  const pid=sbGetProjectId(),status=document.getElementById('shotbook-status');
  if(!pid){if(status)status.textContent='先打开一个项目，再生成分镜。';return;}
  if(status)status.textContent='正在按当前时间线生成分镜草案……';
  const r=await apiPost('/api/editor/shotbook',{project_id:pid,action:'generate',base_version:SHOTBOOK?SHOTBOOK.version:0});
  if(!r||!r.ok){if(status)status.textContent='生成未完成：'+((r&&r.error)||'请求失败');if(r&&r.code==='CONFLICT')await shotbookLoad();return;}
  SHOTBOOK=r;shotbookRender();
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
function shotbookRender(){
  if(!SHOTBOOK)return;
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
    return '<article class="sb-shot panel" data-scene-id="'+sbEsc(s.scene_id)+'" style="margin-top:8px;padding:10px"><div class="sb-shot-head"><b>'+(idx+1)+'. '+sbEsc(s.scene_id)+' · '+Number(s.start).toFixed(2)+'–'+Number(s.end).toFixed(2)+'s</b><button class="btn-secondary btn-sm" type="button" onclick="shotbookLocate(\''+sbEsc(s.scene_id)+'\')">在时间线上定位</button></div><div class="sb-script">'+sbEsc(s.text)+'</div><div class="sb-label">语义角色</div><select class="sb-role">'+shotbookSelectOptions(Object.keys(SB_ROLE_LABELS),s.semantic_role,SB_ROLE_LABELS)+'</select><div class="sb-label">画面意图</div><select class="sb-intent">'+shotbookSelectOptions(Object.keys(SB_INTENT_LABELS),s.visual_intent,SB_INTENT_LABELS)+'</select><div class="sb-label">镜头类型</div><select class="sb-type">'+shotbookSelectOptions(Object.keys(SB_SHOT_LABELS),s.shot_type,SB_SHOT_LABELS)+'</select><div class="sb-label">镜头配方（来自当前项目 Shot Registry）</div><select class="sb-recipe">'+shotbookRecipeOptions(s)+'</select><div class="sb-form-grid"><label>动态字卡文案<input class="sb-overlay" maxlength="120" value="'+sbEsc(s.overlay_text)+'" placeholder="可留空；不会自动覆盖旁白字幕"></label><label>证据引用<input class="sb-evidence" maxlength="300" value="'+sbEsc(s.evidence_ref)+'" placeholder="截图/文档/URL/项目素材 ID"></label><label>音效提示<input class="sb-sfx" maxlength="160" value="'+sbEsc(s.sfx_cue)+'" placeholder="如：落点轻击、翻页声；仅记录设计意图"></label></div><label class="sb-notes-label">镜头说明<textarea class="sb-notes" maxlength="500" rows="2" placeholder="画面要证明什么、主体/构图/动作、避开的表达">'+sbEsc(s.notes)+'</textarea></label>'+timingDetails+'<label class="sb-review"><input class="sb-reviewed" type="checkbox"'+(s.reviewed?' checked':'')+'> 我已核对这镜的语义、画面意图与时间锚点</label><div class="btn-row" style="margin-top:8px"><button class="btn-secondary btn-sm" type="button" onclick="shotbookApplyRecipe(\''+sbEsc(s.scene_id)+'\')">应用镜头配方到时间线</button></div>'+(!s.has_bound_asset?'<div class="hint">提示：当前场景没有绑定素材。应用配方时将检查槽位；缺少素材不会被静默补成假素材。</div>':'')+'</article>';
  }).join('');
}
function shotbookCollect(){
  if(!SHOTBOOK)return [];
  return Array.from(document.querySelectorAll('#shotbook-list .sb-shot')).map(function(card){
    const old=SHOTBOOK.shots.find(function(s){return s.scene_id===card.dataset.sceneId;})||{};
    const anchors=(old.characters||[]).map(function(anchor,i){
      const row=card.querySelector('.sb-char[data-index="'+i+'"]');if(!row)return anchor;
      return Object.assign({},anchor,{start:Number(row.querySelector('.sb-char-start').value),end:Number(row.querySelector('.sb-char-end').value)});
    });
    return {scene_id:card.dataset.sceneId,semantic_role:card.querySelector('.sb-role').value,visual_intent:card.querySelector('.sb-intent').value,shot_type:card.querySelector('.sb-type').value,shot_recipe_id:card.querySelector('.sb-recipe').value,overlay_text:card.querySelector('.sb-overlay').value,evidence_ref:card.querySelector('.sb-evidence').value,sfx_cue:card.querySelector('.sb-sfx').value,notes:card.querySelector('.sb-notes').value,reviewed:card.querySelector('.sb-reviewed').checked,characters:anchors};
  });
}
async function shotbookSave(){
  const pid=sbGetProjectId(),status=document.getElementById('shotbook-status');
  if(!pid||!SHOTBOOK){if(status)status.textContent='先载入或生成分镜草案。';return;}
  const r=await apiPost('/api/editor/shotbook',{project_id:pid,action:'save',base_version:SHOTBOOK.version,shots:shotbookCollect()});
  if(!r||!r.ok){if(status)status.textContent='保存未完成：'+((r&&r.error)||'请求失败')+'。当前修改仍留在页面中。';const reload=document.getElementById('shotbook-reload-conflict');if(reload&&r&&r.code==='CONFLICT')reload.style.display='';return;}
  SHOTBOOK=r;shotbookRender();if(status)status.textContent='分镜已保存为 V'+r.version+'。语义确认、字级校正和配方关联已写入项目。';
}
async function shotbookApplyRecipe(sceneId){
  const card=document.querySelector('#shotbook-list .sb-shot[data-scene-id="'+CSS.escape(sceneId)+'"]'),status=document.getElementById('shotbook-status');
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
async function shotbookReloadConflict(){const button=document.getElementById('shotbook-reload-conflict');if(button)button.style.display='none';await shotbookLoad();}
Object.assign(window,{shotbookLoad,shotbookGenerate,shotbookSave,shotbookApplyRecipe,shotbookLocate,shotbookReloadConflict});
