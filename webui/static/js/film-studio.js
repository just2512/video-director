/* File-backed Studio UI. Each edit commits by stable clip ID and authoritative film revision. */
(function () {
  'use strict';
  var S = {open:false, projectId:'', rev:'', data:null, migration:null, selectedId:'', playhead:0, duration:10, pxPerSec:36, busy:false, drag:null, poll:null, messages:[]};
  var $ = function (id) { return document.getElementById(id); };
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]); }); }
  function num(v, fallback) { var n = Number(v); return Number.isFinite(n) ? n : fallback; }
  function fmtTime(v) { var n = Math.max(0, Number(v) || 0), m = Math.floor(n / 60), s = n - m * 60; return String(m).padStart(2,'0') + ':' + s.toFixed(3).padStart(6,'0'); }
  function clips() { return (S.data && S.data.tracks || []).reduce(function (a,t) { return a.concat((t.clips || []).map(function (c) { c.track_id = t.id; return c; })); }, []); }
  function findClip(id) { return clips().find(function (c) { return c.id === id; }) || null; }
  function showStatus(message, kind) { var el=$('film-studio-alert'); if(!el)return; el.className='fs-alert fs-visible'+(kind==='error'?' fs-error':kind==='success'?' fs-success':''); el.textContent=message||''; }
  function appendMessage(text, role) {
    S.messages.push({text:String(text),role:role});
    var log=$('fs-chat-log'); if (!log) return;
    log.innerHTML=S.messages.map(function(m){return '<div class="fs-chat-message '+(m.role==='user'?'fs-user':'fs-assistant')+'">'+esc(m.text)+'</div>';}).join('');
    log.scrollTop=log.scrollHeight;
  }
  function resetPreview() {
    ['fs-preview-video','fs-preview-image','fs-preview-page'].forEach(function(id){
      var el=$(id); if(!el)return;
      if(id==='fs-preview-video'){try{el.pause();}catch(_){} el.removeAttribute('src'); el.removeAttribute('data-source');}
      else {el.removeAttribute('src');el.removeAttribute('data-source');}
      el.style.display='none';
    });
    var ph=$('fs-monitor-stage')&&$('fs-monitor-stage').querySelector('.fs-monitor-placeholder');
    if(ph){ph.textContent='选择项目并初始化影片文档';ph.style.display='';}
  }
  function previewCurrent() {
    if(!S.data||!S.data.exists){resetPreview();return;}
    var clip=findClip(S.selectedId);
    if(!clip||clip.tag==='audio'){
      clip=clips().filter(function(c){return c.tag!=='audio'&&c.at<=S.playhead&&(c.end==null||S.playhead<c.end);}).sort(function(a,b){return a.at-b.at;}).pop();
    }
    var ph=$('fs-monitor-stage').querySelector('.fs-monitor-placeholder');
    if(!clip){resetPreview();return;}
    if(ph)ph.style.display='none';
    var id=clip.tag==='video'?'fs-preview-video':clip.tag==='img'?'fs-preview-image':clip.tag==='iframe'?'fs-preview-page':'';
    ['fs-preview-video','fs-preview-image','fs-preview-page'].forEach(function(key){
      var el=$(key);if(key!==id){if(key==='fs-preview-video'){try{el.pause();}catch(_){}}el.style.display='none';}
    });
    if(!id){if(ph){ph.textContent='当前所选片段没有可直接显示的画面';ph.style.display='';}return;}
    var target=$(id), source=clip.media_url||'';
    target.style.display='block';
    if(target.getAttribute('data-source')!==source){
      target.setAttribute('data-source',source);target.src=source;
      if(id==='fs-preview-video'){
        target.onloadedmetadata=function(){
          try{target.currentTime=Math.max(0,(S.playhead-clip.at)*num(clip.attrs.speed,1));target.playbackRate=num(clip.attrs.speed,1);target.pause();}catch(_){}
        };
      }
    }else if(id==='fs-preview-video'&&target.readyState>0){
      try{target.currentTime=Math.max(0,(S.playhead-clip.at)*num(clip.attrs.speed,1));target.playbackRate=num(clip.attrs.speed,1);target.pause();}catch(_){}
    }
  }
  function setPlayheadVisual() {
    var range=$('fs-playhead');if(range){range.max=String(S.duration);range.value=String(Math.min(S.duration,Math.max(0,S.playhead)));}
    var val=$('fs-playhead-value');if(val)val.textContent=fmtTime(S.playhead);
    var monitor=$('fs-monitor-time');if(monitor)monitor.textContent=fmtTime(S.playhead);
    var line=$('fs-playhead-line');if(line)line.style.left=(132+S.playhead*S.pxPerSec)+'px';
  }
  function renderMigration() {
    var data=S.migration||{}, preview=data.migration_preview||{}, warnings=preview.warnings||[];
    $('fs-project-label').textContent=S.projectId;$('fs-clip-count').textContent='—';$('fs-timeline-meta').textContent='迁移预览';
    $('fs-assets-list').innerHTML='<div class="fs-empty">当前项目还没有 film.html。初始化将生成可编辑影片文档，不会覆盖旧 Composition。</div>';
    $('fs-timeline').innerHTML='<div class="fs-empty">先检查迁移提示，再初始化。</div>';
    $('fs-inspector').innerHTML='<div class="fs-empty">初始化影片文档后才能编辑片段。</div>';resetPreview();
    var message=data.migration_available?'可从当前 Composition 初始化 film.html。预计 '+(preview.duration||0)+' 秒、'+(preview.track_count||0)+' 条轨道、'+(preview.clip_count||0)+' 个片段。':'无法初始化：'+(data.migration_error||'当前项目没有可编辑的 CompositionIR。');
    if(warnings.length)message+=' 迁移提示：'+warnings.join('；');
    showStatus(message,data.migration_available?'':'error');
    if(data.migration_available){
      $('fs-timeline').innerHTML='<div class="fs-migrate-action"><strong>从旧项目迁移到 film.html</strong><p>初始化会创建影片文档。旧工作台不会自动同步 film.html 的编辑。迁移存在警告时会再次要求确认。</p><button type="button" class="fs-btn fs-primary" id="fs-initialize-button">初始化影片文档</button></div>';
      $('fs-initialize-button').addEventListener('click',function(){filmStudioInitialize(false);});
    }
  }
  function renderAssets() {
    var all=clips();$('fs-clip-count').textContent=String(all.length);
    if(!all.length){$('fs-assets-list').innerHTML='<div class="fs-empty">此影片没有片段。可从内容生产流程重新生成影片。</div>';return;}
    $('fs-assets-list').innerHTML=all.map(function(c){
      var label=c.attrs.title||c.attrs['data-role']||c.id;
      return '<button type="button" class="fs-asset-item '+(c.id===S.selectedId?'fs-selected':'')+'" data-clip="'+esc(c.id)+'"><span class="fs-asset-icon">'+esc(c.tag.toUpperCase())+'</span><span class="fs-asset-name">'+esc(label)+'<span class="fs-asset-meta">'+fmtTime(c.at)+' · '+(c.duration==null?'时长未知':c.duration.toFixed(2)+'s')+'</span></span></button>';
    }).join('');
    $('fs-assets-list').querySelectorAll('[data-clip]').forEach(function(el){el.addEventListener('click',function(){window.filmStudioSelectClip(el.getAttribute('data-clip'));});});
  }
  function renderTimeline() {
    if(!S.data||!S.data.exists)return;
    var laneWidth=Math.max(720,Math.ceil(S.duration*S.pxPerSec)),ticks=Math.min(240,Math.ceil(S.duration/5));
    var ruler='<div class="fs-ruler" style="width:'+(132+laneWidth)+'px"><div class="fs-ruler-head">时码</div><div class="fs-ruler-body" id="fs-ruler-body" style="width:'+laneWidth+'px">';
    for(var i=0;i<=ticks;i++){var sec=Math.min(S.duration,i*5);ruler+='<div class="fs-ruler-mark" data-at="'+sec+'" style="left:'+(sec*S.pxPerSec)+'px">'+esc(fmtTime(sec))+'</div>';}
    ruler+='</div></div>';
    var body=S.data.tracks.map(function(track){
      var locked=Object.prototype.hasOwnProperty.call(track.attrs||{},'locked'),label=(track.attrs||{})['data-label']||track.id;
      var lane='<div class="fs-track-body" style="width:'+laneWidth+'px">';
      (track.clips||[]).forEach(function(clip){
        var width=Math.max(24,(clip.duration==null?2:clip.duration)*S.pxPerSec),left=Math.max(0,clip.at*S.pxPerSec),title=clip.attrs.title||clip.id;
        lane+='<div class="fs-clip '+(clip.id===S.selectedId?'fs-clip-selected ':'')+'" data-clip="'+esc(clip.id)+'" data-track="'+esc(track.id)+'" data-kind="'+esc(clip.tag)+'" style="left:'+left+'px;width:'+width+'px" title="'+esc(title)+'"><span class="fs-clip-title">'+esc(title)+'</span><span class="fs-clip-subtitle">'+esc(fmtTime(clip.at))+(clip.duration==null?'':' · '+clip.duration.toFixed(2)+'s')+'</span></div>';
      });
      lane+='</div>';
      return '<div class="fs-track '+(locked?'fs-track-locked':'')+'" data-track="'+esc(track.id)+'"><div class="fs-track-head"><span class="fs-track-label" title="'+esc(label)+'">'+esc(label)+'</span><button type="button" class="fs-btn" data-track-lock="'+esc(track.id)+'" title="'+(locked?'解锁轨道':'锁定轨道')+'">'+(locked?'🔒':'锁')+'</button></div>'+lane+'</div>';
    }).join('');
    $('fs-timeline').innerHTML=ruler+body+'<div class="fs-playhead" id="fs-playhead-line"></div>';
    $('fs-timeline').style.width=(132+laneWidth)+'px';$('fs-timeline-meta').textContent=fmtTime(S.duration)+' · '+S.data.tracks.length+' 轨';
    $('fs-timeline').querySelectorAll('[data-clip]').forEach(function(el){
      el.addEventListener('click',function(e){e.stopPropagation();window.filmStudioSelectClip(el.getAttribute('data-clip'));});
      el.addEventListener('pointerdown',function(e){
        if(e.button!==0||e.target.closest('button'))return;
        var clip=findClip(el.getAttribute('data-clip'));if(!clip)return;
        window.filmStudioSelectClip(clip.id);
        S.drag={id:clip.id,startX:e.clientX,startY:e.clientY,originalAt:clip.at,trackId:clip.track_id,active:false,element:el};
        if(el.setPointerCapture)try{el.setPointerCapture(e.pointerId);}catch(_){}
        e.preventDefault();
      });
    });
    $('fs-timeline').querySelectorAll('[data-track-lock]').forEach(function(el){
      el.addEventListener('click',function(e){
        e.stopPropagation();var track=(S.data.tracks||[]).find(function(t){return t.id===el.getAttribute('data-track-lock');});if(!track)return;
        var locked=Object.prototype.hasOwnProperty.call(track.attrs||{},'locked');
        filmStudioCommit([{op:'track',track_id:track.id,field:'locked',value:!locked}]);
      });
    });
    $('fs-ruler-body').querySelectorAll('[data-at]').forEach(function(el){el.addEventListener('click',function(e){window.filmStudioSeek(el.getAttribute('data-at'));});});
    setPlayheadVisual();
  }
  function renderInspector() {
    var clip=findClip(S.selectedId);
    if(!clip){$('fs-inspector').innerHTML='<div class="fs-empty">选择时间线片段后编辑属性。</div>';return;}
    var a=clip.attrs||{};
    var fields='<div class="fs-inspector-description"><strong>'+esc(clip.id)+'</strong><br>'+esc(clip.tag)+'<br>'+esc(a.src||'')+'</div><div class="fs-inspector-section">'+
      '<label class="fs-inspector-field">时间位置（秒）<input id="fs-field-at" type="number" min="0" step="0.033333" value="'+esc(a.at||clip.at||0)+'"></label>';
    if(clip.tag==='video'||clip.tag==='audio'){
      fields+='<label class="fs-inspector-field">速度<input id="fs-field-speed" type="number" min="0.25" max="4" step="0.05" value="'+esc(a.speed||1)+'"></label>';
      fields+='<label class="fs-inspector-field">音量<input id="fs-field-volume" type="number" min="0" max="2" step="0.05" value="'+esc(a.volume==null?1:a.volume)+'"></label>';
      fields+='<label class="fs-inspector-field">静音<select id="fs-field-muted"><option value="false">否</option><option value="true"'+(Object.prototype.hasOwnProperty.call(a,'muted')?' selected':'')+'>是</option></select></label>';
    }
    fields+='<label class="fs-inspector-field">片段标题<input id="fs-field-title" type="text" maxlength="200" value="'+esc(a.title||'')+'"></label></div><div class="fs-inspector-description fs-inspector-section">更改经统一编辑 API 写入 film.html。锁定轨道会拒绝修改；同轨片段不可重叠。</div>';
    $('fs-inspector').innerHTML=fields;
  }
  function render() {
    if(!S.data||!S.data.exists){renderMigration();return;}
    $('fs-project-label').textContent=S.projectId+' · rev '+S.rev;S.duration=Math.max(1,num(S.data.duration,10));
    if(!findClip(S.selectedId)){var first=clips().filter(function(c){return c.tag!=='audio';})[0];S.selectedId=first?first.id:'';}
    renderAssets();renderTimeline();renderInspector();setPlayheadVisual();previewCurrent();
    var warnings=S.data.migration&&S.data.migration.warnings||[];
    if(warnings.length)showStatus('迁移警告：'+warnings.join('；'),'');
    else{var el=$('film-studio-alert');el.className='fs-alert';el.textContent='';}
  }
  async function filmStudioLoad(force) {
    if(!S.open&&!force)return;
    var selector=$('editor-project');
    S.projectId=(selector&&selector.value)||(typeof ED!=='undefined'&&ED.pid)||S.projectId;
    if(!S.projectId){showStatus('请先在工作台顶部选择并打开一个可编辑项目。','error');return;}
    if(S.busy)return;
    var r=await apiGet('/api/editor/film?project_id='+encodeURIComponent(S.projectId));
    if(r&&r.ok&&r.exists){S.data=r;S.rev=r.rev;S.migration=null;$('film-studio-panel').setAttribute('data-project-id',S.projectId);render();}
    else if(r&&r.ok&&!r.exists){S.data=null;S.rev=r.rev;S.migration=r;renderMigration();}
    else showStatus((r&&r.error)||'读取影片文档失败；为防止覆盖，不写入文件。','error');
  }
  async function filmStudioInitialize(acceptWarnings) {
    if(!S.projectId){showStatus('缺少项目编号。','error');return;}
    var expected=S.migration?S.migration.source_composition_version:null;
    if(expected==null){showStatus('迁移预览缺少源版本号，请重新载入。','error');return;}
    var r=await apiPost('/api/editor/film/initialize',{project_id:S.projectId,expected_composition_version:expected,accept_warnings:!!acceptWarnings});
    if(r&&r.code==='MIGRATION_WARNINGS_REQUIRE_CONFIRMATION'){
      var message='以下内容目前不能保证无损迁移：\n\n'+(r.warnings||[]).join('\n')+'\n\n确认后才写入 film.html。是否继续？';
      if(window.confirm(message))return filmStudioInitialize(true);
      showStatus('已取消初始化，没有创建 film.html。');return;
    }
    if(!r||!r.ok){showStatus((r&&r.error)||'初始化失败。','error');return;}
    S.data=r;S.rev=r.rev;S.migration=null;S.selectedId='';
    showStatus('已初始化 film.html。旧 Composition 与旧工作台不自动双向同步。',r.warnings&&r.warnings.length?'':'success');render();
  }
  async function filmStudioCommit(ops) {
    if(!S.projectId||!S.data||!S.data.exists||S.busy)return;
    S.busy=true;
    try{
      var r=await apiPost('/api/editor/film/edit',{project_id:S.projectId,base:S.rev,ops:ops});
      if(!r||!r.ok){showStatus((r&&r.error)||'编辑提交失败。','error');return;}
      S.data=r;S.rev=r.rev;
      showStatus(r.rebased?'操作已按片段 ID 重放到较新 revision；请检查结果。':'已保存 '+(r.ops||[]).length+' 项编辑。',r.rebased?'':'success');render();
    }finally{S.busy=false;}
  }
  async function filmStudioHistory(action) {
    if(!S.data||S.busy)return;S.busy=true;
    try{
      var r=await apiPost('/api/editor/film/'+action,{project_id:S.projectId,base:S.rev});
      if(!r||!r.ok){showStatus((r&&r.error)||'历史操作失败。','error');return;}
      S.data=r;S.rev=r.rev;render();showStatus(action==='undo'?'已撤销。':'已重做。','success');
    }finally{S.busy=false;}
  }
  function onMove(e) {
    var d=S.drag;if(!d)return;
    var dx=e.clientX-d.startX,dy=e.clientY-d.startY;
    if(!d.active&&Math.sqrt(dx*dx+dy*dy)<5)return;
    d.active=true;d.element.classList.add('fs-dragging');
    d.element.style.left=Math.max(0,d.originalAt*S.pxPerSec+dx)+'px';
  }
  async function onUp(e) {
    var d=S.drag;if(!d)return;S.drag=null;d.element.classList.remove('fs-dragging');
    if(!d.active)return;
    var at=Math.max(0,d.originalAt+(e.clientX-d.startX)/S.pxPerSec),target=document.elementFromPoint(e.clientX,e.clientY),row=target&&target.closest('.fs-track');
    await filmStudioCommit([{op:'move',clip:d.id,track_id:row?row.getAttribute('data-track'):d.trackId,at:Number(at.toFixed(3))}]);
  }
  document.addEventListener('pointermove',onMove);document.addEventListener('pointerup',onUp);
  document.addEventListener('pointercancel',function(){if(S.drag){S.drag.element.classList.remove('fs-dragging');S.drag=null;}});
  window.filmStudioToggle=function(){
    if(S.open){window.filmStudioClose();return;}
    S.open=true;$('film-studio-panel').style.display='block';$('film-studio-toggle').textContent='关闭多轨工作台';
    $('editor-body').style.display='none';$('editor-empty').style.display='none';filmStudioLoad(true);
    if(S.poll)clearInterval(S.poll);
    S.poll=setInterval(async function(){
      if(!S.open||S.busy||S.drag)return;
      var r=await apiGet('/api/editor/film?project_id='+encodeURIComponent(S.projectId));
      if(r&&r.ok&&r.exists&&r.rev!==S.rev){S.data=r;S.rev=r.rev;render();showStatus('检测到外部 Agent/文件修改，已按新 revision 刷新。');}
      else if(r&&!r.ok&&r.code==='INVALID_FILM')showStatus('film.html 当前无法解析。已冻结编辑；修复文件后重新载入。','error');
    },1600);
  };
  window.filmStudioClose=function(){
    S.open=false;if(S.poll){clearInterval(S.poll);S.poll=null;}
    $('film-studio-panel').style.display='none';$('film-studio-toggle').textContent='打开多轨工作台';
    if(typeof ED!=='undefined'&&ED.pid)$('editor-body').style.display='';else $('editor-empty').style.display='';
  };
  window.filmStudioLoad=filmStudioLoad;window.filmStudioInitialize=filmStudioInitialize;
  window.filmStudioSelectClip=function(id){S.selectedId=id;var c=findClip(id);if(c)S.playhead=c.at;render();};
  window.filmStudioSeek=function(value){S.playhead=Math.max(0,Math.min(S.duration,num(value,0)));setPlayheadVisual();previewCurrent();};
  window.filmStudioNudge=function(direction){window.filmStudioSeek(S.playhead+direction/30);};
  window.filmStudioZoom=function(direction){S.pxPerSec=Math.max(12,Math.min(120,S.pxPerSec*(direction>0?1.25:0.8)));if(S.data&&S.data.exists)renderTimeline();};
  window.filmStudioSaveInspector=async function(){
    var clip=findClip(S.selectedId);if(!clip){showStatus('请先选择片段。','error');return;}
    var attrs=clip.attrs||{},ops=[];
    [['at','fs-field-at'],['speed','fs-field-speed'],['volume','fs-field-volume'],['title','fs-field-title']].forEach(function(pair){
      var el=$(pair[1]);if(!el)return;
      var value=pair[0]==='title'?el.value:Number(el.value),old=attrs[pair[0]];
      if(pair[0]==='at'||pair[0]==='speed'||pair[0]==='volume'){
        if(!Number.isFinite(value)){showStatus('属性值必须是有效数字。','error');return;}
        var defaultValue=pair[0]==='at'?clip.at:1;
        if(Math.abs(num(old,defaultValue)-value)>1e-5)ops.push({op:'set',clip:clip.id,field:pair[0],value:value});
      }else if(String(old||'')!==value)ops.push({op:'set',clip:clip.id,field:pair[0],value:value||null});
    });
    var muted=$('fs-field-muted');
    if(muted&&Object.prototype.hasOwnProperty.call(attrs,'muted')!==(muted.value==='true'))ops.push({op:'set',clip:clip.id,field:'muted',value:muted.value==='true'});
    if(!ops.length){showStatus('属性没有变化。');return;}await filmStudioCommit(ops);
  };
  window.filmStudioSplit=async function(){var clip=findClip(S.selectedId);if(!clip){showStatus('请先选择需要切分的片段。','error');return;}await filmStudioCommit([{op:'split',clip:clip.id,at:S.playhead}]);};
  window.filmStudioRemove=async function(){var clip=findClip(S.selectedId);if(!clip){showStatus('请先选择片段。','error');return;}if(!window.confirm('删除片段 '+clip.id+'？此操作可撤销。'))return;await filmStudioCommit([{op:'remove',clip:clip.id}]);S.selectedId='';render();};
  window.filmStudioUndo=function(){filmStudioHistory('undo');};window.filmStudioRedo=function(){filmStudioHistory('redo');};
  window.filmStudioAsk=async function(){
    var input=$('fs-chat-input'),message=(input.value||'').trim();if(!message||S.busy)return;
    if(!S.data||!S.data.exists){showStatus('请先初始化影片文档。','error');return;}
    appendMessage(message,'user');input.value='';S.busy=true;
    try{
      var r=await apiPost('/api/editor/film/command',{project_id:S.projectId,base:S.rev,message:message,selected_clip_id:S.selectedId});
      if(!r||!r.ok){appendMessage((r&&r.error)||'AI 编辑失败。','assistant');showStatus((r&&r.error)||'AI 编辑失败。','error');return;}
      if(r.no_op){appendMessage(r.message||'AI 没有执行编辑，请补充目标。','assistant');showStatus(r.message||'没有产生编辑操作。');return;}
      S.data=r;S.rev=r.rev;appendMessage(r.message||('已执行 '+(r.ops||[]).length+' 项操作。'),'assistant');render();
    }finally{S.busy=false;}
  };
  var projectSelect=$('editor-project');if(projectSelect)projectSelect.addEventListener('change',function(){if(S.open)setTimeout(function(){filmStudioLoad(true);},50);});
})();
