/* File-backed Studio UI. Each edit commits by stable clip ID and authoritative film revision. */
(function () {
  'use strict';
  var S = {open:false, projectId:'', rev:'', runtimeRev:'', runtimeReady:false, runtimeDocument:null, runtimeCommand:'', data:null, migration:null, selectedId:'', selectedIds:[], selectionAnchor:'', skipNextClipClick:false, playhead:0, duration:10, pxPerSec:36, busy:false, rendering:false, drag:null, poll:null, messages:[], assetMode:'clips', libraryAssets:[]};
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
    var frame=$('fs-preview-composition');
    if(frame){try{frame.contentWindow.postMessage({type:'film-runtime:pause'},window.location.origin);}catch(_){}frame.style.display='none';}
    var ph=$('fs-monitor-stage')&&$('fs-monitor-stage').querySelector('.fs-monitor-placeholder');
    if(ph){ph.textContent='选择项目并初始化影片文档';ph.style.display='';}
    S.runtimeRev='';S.runtimeCommand='';window.filmStudioPlaying=false;
    var button=$('fs-play-toggle');if(button)button.textContent='播放';
  }
  function syncRuntime(type) {
    var frame=$('fs-preview-composition');
    if(type)S.runtimeCommand=type;
    else if(!S.runtimeCommand)S.runtimeCommand=window.filmStudioPlaying?'play':'seek';
    if(!frame||!frame.contentWindow||!S.runtimeReady||!S.data||!S.data.exists)return;
    frame.style.display='block';
    var ph=$('fs-monitor-stage').querySelector('.fs-monitor-placeholder');if(ph)ph.style.display='none';
    if(S.runtimeRev!==S.rev){
      frame.contentWindow.postMessage({type:'film-runtime:load',data:S.data,rev:S.rev},window.location.origin);
      S.runtimeRev=S.rev;
    }
    var command=S.runtimeCommand||'seek';
    if(command==='play')frame.contentWindow.postMessage({type:'film-runtime:play'},window.location.origin);
    else if(command==='pause')frame.contentWindow.postMessage({type:'film-runtime:pause'},window.location.origin);
    else if(command==='seek-pause')frame.contentWindow.postMessage({type:'film-runtime:seek-pause',time:S.playhead},window.location.origin);
    else frame.contentWindow.postMessage({type:'film-runtime:seek',time:S.playhead},window.location.origin);
    S.runtimeCommand='';
    var button=$('fs-play-toggle');if(button)button.textContent=window.filmStudioPlaying?'暂停':'播放';
  }
  function filmStudioRuntimeFrameLoaded() {
    var frame=$('fs-preview-composition');
    if(!frame)return;
    var doc=null;
    try{doc=frame.contentDocument;}catch(_){}
    if(S.runtimeReady&&doc&&doc===S.runtimeDocument)return;
    S.runtimeReady=true;S.runtimeDocument=doc;S.runtimeRev='';
    if(S.data&&S.data.exists)syncRuntime(S.runtimeCommand||(window.filmStudioPlaying?'play':'seek'));
  }
  var runtimeFrame=$('fs-preview-composition');
  if(runtimeFrame){
    runtimeFrame.addEventListener('load',filmStudioRuntimeFrameLoaded);
    try{
      if(runtimeFrame.contentDocument&&runtimeFrame.contentDocument.readyState==='complete'){
        filmStudioRuntimeFrameLoaded();
      }
    }catch(_){}
  }
  function previewCurrent() {
    if(!S.data||!S.data.exists){resetPreview();return;}
    syncRuntime();
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
  function renderLibraryAssets() {
    var supported=S.libraryAssets.filter(function(a){return ['video','image','audio'].indexOf(String(a.type||'').toLowerCase())>=0;});
    if(!supported.length){$('fs-assets-list').innerHTML='<div class="fs-empty">素材库没有可直接加入时间线的视频或图片。已上传的项目素材和全局素材会在此显示。</div>';return;}
    $('fs-assets-list').innerHTML=supported.map(function(a){
      var id=String(a.asset_id||a.id||''),kind=String(a.type||'').toLowerCase(),label=a.description||a.name||a.filename||id;
      var meta=kind.toUpperCase()+(a.duration?' · '+Number(a.duration).toFixed(2)+'s':'')+(a.source?' · '+String(a.source):'');
      return '<div class="fs-asset-item"><span class="fs-asset-icon">'+esc(kind.toUpperCase())+'</span><span class="fs-asset-name" title="'+esc(label)+'">'+esc(label)+'<span class="fs-asset-meta">'+esc(meta)+'</span></span><button type="button" class="fs-btn fs-primary" data-add-asset="'+esc(id)+'" title="在播放头位置新建轨道并添加">添加</button></div>';
    }).join('');
    $('fs-assets-list').querySelectorAll('[data-add-asset]').forEach(function(el){
      el.addEventListener('click',function(e){e.stopPropagation();window.filmStudioAddAsset(el.getAttribute('data-add-asset'));});
    });
  }
  function renderAssets() {
    var all=clips();$('fs-clip-count').textContent=String(all.length);
    if(S.assetMode==='library'){renderLibraryAssets();return;}
    if(!all.length){$('fs-assets-list').innerHTML='<div class="fs-empty">此影片没有片段。可从素材库添加视频或图片。</div>';return;}
    $('fs-assets-list').innerHTML=all.map(function(c){
      var label=c.attrs.title||c.attrs['data-role']||c.id, aid=c.attrs['data-asset-id']||'', thumb=aid&&c.tag!=='audio'?'/api/asset_thumbnail?asset_id='+encodeURIComponent(aid)+'&project_id='+encodeURIComponent(S.projectId):'';
      var thumbHtml=thumb?'<img class="fs-asset-thumb" src="'+esc(thumb)+'" loading="lazy" alt="" onerror="this.remove()">':esc(c.tag.toUpperCase());
      return '<button type="button" class="fs-asset-item '+(c.id===S.selectedId?'fs-selected':'')+'" data-clip="'+esc(c.id)+'"><span class="fs-asset-icon">'+thumbHtml+'</span><span class="fs-asset-name">'+esc(label)+'<span class="fs-asset-meta">'+fmtTime(c.at)+' · '+(c.duration==null?'时长未知':c.duration.toFixed(2)+'s')+'</span></span></button>';
    }).join('');
    $('fs-assets-list').querySelectorAll('[data-clip]').forEach(function(el){el.addEventListener('click',function(){window.filmStudioSelectClip(el.getAttribute('data-clip'));});});
  }
  window.filmStudioShowAssetMode=function(mode){
    S.assetMode=mode==='library'?'library':'clips';
    var clipTab=$('fs-mode-clips'),libTab=$('fs-mode-library');
    if(clipTab)clipTab.className='fs-asset-tab'+(S.assetMode==='clips'?' fs-asset-tab-active':'');
    if(libTab)libTab.className='fs-asset-tab'+(S.assetMode==='library'?' fs-asset-tab-active':'');
    renderAssets();
  };
  window.filmStudioLoadLibrary=async function(){
    window.filmStudioShowAssetMode('library');
    showStatus('正在读取素材库……');
    var response=await apiGet('/api/assets_library');
    if(!Array.isArray(response)){showStatus((response&&response.error)||'素材库读取失败。','error');return;}
    S.libraryAssets=response;
    renderAssets();
    showStatus('已加载 '+response.length+' 条素材目录记录。','success');
  };
  window.filmStudioAddAsset=async function(assetId){
    if(!S.data||!S.data.exists||S.busy){showStatus('请先初始化影片文档，且等待当前编辑完成。','error');return;}
    S.busy=true;
    try{
      var response=await apiPost('/api/editor/film/insert-asset',{
        project_id:S.projectId,base:S.rev,asset_id:assetId,at:S.playhead,still_duration:5
      });
      if(!response||!response.ok){showStatus((response&&response.error)||'素材加入时间线失败。','error');return;}
      S.data=response;S.rev=response.rev;S.selectedId=response.inserted_clip_id||'';
      S.assetMode='clips';window.filmStudioShowAssetMode('clips');render();
      showStatus('已在播放头位置新建轨道并添加素材。', 'success');
    }finally{S.busy=false;}
  };
  function syncClipSelectionVisual(){
    var selected=S.selectedIds||[];
    document.querySelectorAll('#fs-timeline [data-clip]').forEach(function(el){
      el.classList.toggle('fs-clip-selected',selected.indexOf(el.getAttribute('data-clip'))>=0);
    });
  }
  function selectClipWithModifiers(id,event){
    var all=clips().slice().sort(function(a,b){
      return a.at-b.at || S.data.tracks.findIndex(function(t){return t.id===a.track_id;})-S.data.tracks.findIndex(function(t){return t.id===b.track_id;});
    });
    var index=all.findIndex(function(c){return c.id===id;});
    if(index<0)return false;
    if(event&&(event.ctrlKey||event.metaKey)){
      var existing=S.selectedIds.indexOf(id);
      if(existing>=0)S.selectedIds.splice(existing,1);else S.selectedIds.push(id);
      if(S.selectedIds.indexOf(id)<0){
        S.selectedId=S.selectedIds[0]||'';
        syncClipSelectionVisual();renderInspector();renderAssets();
        return false;
      }
    }else if(event&&event.shiftKey){
      var anchor=S.selectionAnchor||S.selectedId||id;
      var anchorIndex=all.findIndex(function(c){return c.id===anchor;});
      if(anchorIndex<0)anchorIndex=index;
      var lo=Math.min(anchorIndex,index),hi=Math.max(anchorIndex,index);
      S.selectedIds=all.slice(lo,hi+1).map(function(c){return c.id;});
    }else{
      S.selectionAnchor=id;
      if(S.selectedIds.indexOf(id)<0)S.selectedIds=[id];
    }
    if(!S.selectedIds.length)S.selectedIds=[id];
    S.selectedId=id;
    syncClipSelectionVisual();renderInspector();renderAssets();
    return S.selectedIds.indexOf(id)>=0;
  }
  function clearDragTrackTarget(d){
    if(d&&d.targetRow)d.targetRow.classList.remove('fs-track-drop-target');
    if(d)d.targetRow=null;
  }
  function renderTimeline() {
    if(!S.data||!S.data.exists)return;
    var laneWidth=Math.max(720,Math.ceil(S.duration*S.pxPerSec)),ticks=Math.min(240,Math.ceil(S.duration/5));
    var ruler='<div class="fs-ruler" style="width:'+(132+laneWidth)+'px"><div class="fs-ruler-head">时码</div><div class="fs-ruler-body" id="fs-ruler-body" style="width:'+laneWidth+'px">';
    for(var i=0;i<=ticks;i++){var sec=Math.min(S.duration,i*5);ruler+='<div class="fs-ruler-mark" data-at="'+sec+'" style="left:'+(sec*S.pxPerSec)+'px">'+esc(fmtTime(sec))+'</div>';}
    ruler+='</div></div>';
    var body=S.data.tracks.map(function(track){
      var attrs=track.attrs||{},locked=Object.prototype.hasOwnProperty.call(attrs,'locked'),
          hidden=Object.prototype.hasOwnProperty.call(attrs,'hidden'),muted=Object.prototype.hasOwnProperty.call(attrs,'muted'),
          label=attrs['data-label']||track.id;
      var lane='<div class="fs-track-body" style="width:'+laneWidth+'px">';
      (track.clips||[]).forEach(function(clip){
        var width=Math.max(24,(clip.duration==null?2:clip.duration)*S.pxPerSec),left=Math.max(0,clip.at*S.pxPerSec),title=clip.attrs.title||clip.id;
        var aid=clip.attrs['data-asset-id']||'',thumb=aid&&clip.tag!=='audio'?'/api/asset_thumbnail?asset_id='+encodeURIComponent(aid)+'&project_id='+encodeURIComponent(S.projectId):'';
        var thumbHtml=thumb?'<img class="fs-clip-thumb" src="'+esc(thumb)+'" loading="lazy" alt="" onerror="this.remove()">':'';
        var handles=clip.duration==null?'':'<span class="fs-trim-handle fs-trim-left" data-trim="start" title="修剪入点"></span><span class="fs-trim-handle fs-trim-right" data-trim="end" title="修剪出点"></span>';
        lane+='<div class="fs-clip '+((S.selectedIds||[]).indexOf(clip.id)>=0?'fs-clip-selected ':'')+'" data-clip="'+esc(clip.id)+'" data-track="'+esc(track.id)+'" data-kind="'+esc(clip.tag)+'" style="left:'+left+'px;width:'+width+'px" title="'+esc(title)+'">'+thumbHtml+handles+'<span class="fs-clip-title">'+esc(title)+'</span><span class="fs-clip-subtitle">'+esc(fmtTime(clip.at))+(clip.duration==null?'':' · '+clip.duration.toFixed(2)+'s')+'</span></div>';
      });
      lane+='</div>';
      return '<div class="fs-track '+(locked?'fs-track-locked ':'')+(hidden?'fs-hidden-track ':'')+(muted?'fs-muted-track':'')+'" data-track="'+esc(track.id)+'"><div class="fs-track-head"><span class="fs-track-label" title="'+esc(label)+'">'+esc(label)+'</span><span class="fs-track-controls"><button type="button" class="fs-btn" data-track-lock="'+esc(track.id)+'" title="'+(locked?'解锁轨道':'锁定轨道')+'">'+(locked?'🔒':'锁')+'</button><button type="button" class="fs-btn" data-track-flag="hidden" data-track-key="'+esc(track.id)+'" title="'+(hidden?'显示轨道':'隐藏轨道')+'">'+(hidden?'显':'隐')+'</button><button type="button" class="fs-btn" data-track-flag="muted" data-track-key="'+esc(track.id)+'" title="'+(muted?'取消静音':'静音轨道')+'">'+(muted?'🔊':'🔇')+'</button></span></div>'+lane+'</div>';
    }).join('');
    $('fs-timeline').innerHTML=ruler+body+'<div class="fs-playhead" id="fs-playhead-line"></div>';
    $('fs-timeline').style.width=(132+laneWidth)+'px';$('fs-timeline-meta').textContent=fmtTime(S.duration)+' · '+S.data.tracks.length+' 轨';
    $('fs-timeline').querySelectorAll('[data-clip]').forEach(function(el){
      el.addEventListener('click',function(e){
        e.stopPropagation();
        if(S.skipNextClipClick){S.skipNextClipClick=false;return;}
        var id=el.getAttribute('data-clip');
        if(selectClipWithModifiers(id,e)){var selected=findClip(id);if(selected){S.playhead=selected.at;setPlayheadVisual();previewCurrent();}}
      });
      el.addEventListener('pointerdown',function(e){
        if(e.button!==0||e.target.closest('button'))return;
        var clip=findClip(el.getAttribute('data-clip'));if(!clip)return;
        var canDrag=selectClipWithModifiers(clip.id,e);
        if(!canDrag){e.preventDefault();return;}
        S.playhead=clip.at;setPlayheadVisual();previewCurrent();
        var trim=e.target.closest('[data-trim]'),mode=trim?(trim.getAttribute('data-trim')==='start'?'trim-start':'trim-end'):'move';
        if(mode!=='move'&&clip.duration==null){e.preventDefault();return;}
        var chosen=mode==='move'?(S.selectedIds||[clip.id]):[clip.id];
        var trackOrder=(S.data.tracks||[]).map(function(t){return t.id;});
        var members=chosen.map(function(id){
          var selected=findClip(id);if(!selected)return null;
          var element=Array.from($('fs-timeline').querySelectorAll('[data-clip]')).find(function(node){return node.getAttribute('data-clip')===id;});
          return selected&&element?{id:id,originalAt:selected.at,originalEnd:selected.end==null?selected.at+num(selected.duration,2):selected.end,
            trackId:selected.track_id,trackIndex:trackOrder.indexOf(selected.track_id),element:element}:null;
        }).filter(Boolean);
        if(!members.length)return;
        if(mode!=='move')members=members.filter(function(m){return m.id===clip.id;});
        S.drag={id:clip.id,mode:mode,startX:e.clientX,startY:e.clientY,originalAt:clip.at,
          originalEnd:clip.end==null?clip.at+num(clip.duration,2):clip.end,trackId:clip.track_id,
          originalTrackIndex:trackOrder.indexOf(clip.track_id),trackOrder:trackOrder,
          startScrollLeft:$('fs-timeline-scroll').scrollLeft,active:false,element:el,members:members,
          targetTrackId:clip.track_id,targetRow:null};
        members.forEach(function(member){member.element.classList.add('fs-dragging');});
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
    $('fs-timeline').querySelectorAll('[data-track-flag]').forEach(function(el){
      el.addEventListener('click',function(e){
        e.stopPropagation();
        var track=(S.data.tracks||[]).find(function(t){return t.id===el.getAttribute('data-track-key');});
        if(!track)return;
        var field=el.getAttribute('data-track-flag'),on=Object.prototype.hasOwnProperty.call(track.attrs||{},field);
        filmStudioCommit([{op:'track',track_id:track.id,field:field,value:!on}]);
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
    $('fs-project-label').textContent=S.projectId+' · rev '+S.rev;S.duration=Math.max(0.001,num(S.data.duration,10));
    var available=clips().map(function(c){return c.id;});
    S.selectedIds=(S.selectedIds||[]).filter(function(id){return available.indexOf(id)>=0;});
    if(!findClip(S.selectedId)){var first=clips().filter(function(c){return c.tag!=='audio';})[0];S.selectedId=first?first.id:'';}
    if(S.selectedIds.indexOf(S.selectedId)<0&&S.selectedId)S.selectedIds.push(S.selectedId);
    if(!S.selectedIds.length&&S.selectedId)S.selectedIds=[S.selectedId];
    renderAssets();renderTimeline();renderInspector();setPlayheadVisual();previewCurrent();
    var warnings=S.data.migration&&S.data.migration.warnings||[];
    if(warnings.length)showStatus('迁移警告：'+warnings.join('；'),'');
    else{var el=$('film-studio-alert');el.className='fs-alert';el.textContent='';}
  }
  async function filmStudioLoad(force) {
    if(!S.open&&!force)return;
    var selector=$('editor-project'),nextId=(selector&&selector.value)||(typeof ED!=='undefined'&&ED.pid)||S.projectId;
    if(!nextId){showStatus('请先在工作台顶部选择并打开一个可编辑项目。','error');return;}
    if(S.busy)return;
    var changed=nextId!==S.projectId;
    if(changed){S.data=null;S.rev='';S.runtimeRev='';S.runtimeCommand='';S.migration=null;S.selectedId='';S.selectedIds=[];S.selectionAnchor='';window.filmStudioPlaying=false;}
    S.projectId=nextId;
    var requestProjectId=nextId;
    var r=await apiGet('/api/editor/film?project_id='+encodeURIComponent(requestProjectId));
    if(requestProjectId!==S.projectId)return;
    if(r&&r.ok&&r.exists){S.data=r;S.rev=r.rev;S.migration=null;$('film-studio-panel').setAttribute('data-project-id',S.projectId);render();}
    else if(r&&r.ok&&!r.exists){S.data=null;S.rev=r.rev;S.runtimeRev='';window.filmStudioPlaying=false;S.migration=r;renderMigration();}
    else {S.data=null;S.rev='';showStatus((r&&r.error)||'读取影片文档失败；为防止覆盖，不写入文件。','error');}
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
    S.data=r;S.rev=r.rev;S.migration=null;S.selectedId='';S.selectedIds=[];S.selectionAnchor='';
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
    var scroll=$('fs-timeline-scroll'),rect=scroll.getBoundingClientRect();
    if(e.clientX<rect.left+28)scroll.scrollLeft=Math.max(0,scroll.scrollLeft-18);
    else if(e.clientX>rect.right-28)scroll.scrollLeft=Math.min(scroll.scrollWidth,scroll.scrollLeft+18);
    var dx=e.clientX-d.startX+(scroll.scrollLeft-d.startScrollLeft);
    if(!d.active&&Math.sqrt(dx*dx+(e.clientY-d.startY)*(e.clientY-d.startY))<5)return;
    d.active=true;
    var target=document.elementFromPoint(e.clientX,e.clientY),row=target&&target.closest('.fs-track');
    if(row){
      var nextTrack=row.getAttribute('data-track');
      if(nextTrack!==d.targetTrackId){clearDragTrackTarget(d);d.targetTrackId=nextTrack;d.targetRow=row;row.classList.add('fs-track-drop-target');}
    }
    if(d.mode==='trim-start'){
      var newStart=Math.max(d.originalAt+0.04,Math.min(d.originalEnd-0.04,d.originalAt+dx/S.pxPerSec));
      d.element.style.left=(newStart*S.pxPerSec)+'px';
      d.element.style.width=Math.max(3,(d.originalEnd-newStart)*S.pxPerSec)+'px';
    }else if(d.mode==='trim-end'){
      var newEnd=Math.max(d.originalAt+0.04,Math.min(d.originalEnd-0.04,d.originalEnd+dx/S.pxPerSec));
      d.element.style.width=Math.max(3,(newEnd-d.originalAt)*S.pxPerSec)+'px';
    }else{
      (d.members||[]).forEach(function(member){member.element.style.left=Math.max(0,member.originalAt*S.pxPerSec+dx)+'px';});
    }
  }
  async function onUp(e) {
    var d=S.drag;
    if(!d){S.skipNextClipClick=true;setTimeout(function(){S.skipNextClipClick=false;},0);return;}
    S.drag=null;S.skipNextClipClick=true;setTimeout(function(){S.skipNextClipClick=false;},0);
    clearDragTrackTarget(d);
    (d.members||[]).forEach(function(member){member.element.classList.remove('fs-dragging');});
    if(!d.active){syncClipSelectionVisual();return;}
    var deltaPx=e.clientX-d.startX+($('fs-timeline-scroll').scrollLeft-d.startScrollLeft);
    if(d.mode==='trim-start'){
      var trimAt=Math.max(d.originalAt+0.04,Math.min(d.originalEnd-0.04,d.originalAt+deltaPx/S.pxPerSec));
      await filmStudioCommit([{op:'trim',clip:d.id,edge:'start',at:Number(trimAt.toFixed(3))}]);return;
    }
    if(d.mode==='trim-end'){
      var trimEnd=Math.max(d.originalAt+0.04,Math.min(d.originalEnd-0.04,d.originalEnd+deltaPx/S.pxPerSec));
      await filmStudioCommit([{op:'trim',clip:d.id,edge:'end',end:Number(trimEnd.toFixed(3))}]);return;
    }
    var chosen=(d.members||[]).map(function(member){return member.id;});
    var lead=(d.members||[]).find(function(member){return member.id===d.id;})||d.members[0];
    var rawAt=Math.max(0,lead.originalAt+deltaPx/S.pxPerSec),candidates=[S.playhead];
    clips().forEach(function(c){if(chosen.indexOf(c.id)>=0)return;candidates.push(c.at);if(c.end!=null)candidates.push(c.end);});
    var nearest=candidates.reduce(function(best,value){return Math.abs(value-rawAt)<Math.abs(best-rawAt)?value:best;},rawAt);
    var targetAt=Math.abs(nearest-rawAt)*S.pxPerSec<=8?nearest:rawAt;
    var delta=targetAt-lead.originalAt;
    var minStart=Math.min.apply(null,(d.members||[]).map(function(member){return member.originalAt;}));
    delta=Math.max(-minStart,delta);
    var targetIndex=d.trackOrder.indexOf(d.targetTrackId||d.trackId);
    if(targetIndex<0)targetIndex=d.originalTrackIndex;
    var trackDelta=targetIndex-d.originalTrackIndex,operations=[];
    (d.members||[]).forEach(function(member){
      var index=member.trackIndex+trackDelta;
      if(index<0||index>=d.trackOrder.length){showStatus('多选移动超出轨道范围，操作已取消。','error');return;}
      operations.push({op:'move',clip:member.id,track_id:d.trackOrder[index],at:Number(Math.max(0,member.originalAt+delta).toFixed(3))});
    });
    if(!operations.length||(operations.length!==(d.members||[]).length))return;
    await filmStudioCommit(operations);
  }
  document.addEventListener('pointermove',onMove);document.addEventListener('pointerup',onUp);
  document.addEventListener('pointercancel',function(){if(S.drag){S.drag.element.classList.remove('fs-dragging');S.drag=null;}});
  window.filmStudioSetSideMode=function(mode){
    S.sideMode=mode==='ai'?'ai':'inspector';
    var inspector=$('fs-side-inspector'),ai=$('fs-side-ai');
    var inspectorTab=$('fs-side-tab-inspector'),aiTab=$('fs-side-tab-ai');
    if(inspector)inspector.hidden=S.sideMode!=='inspector';
    if(ai)ai.hidden=S.sideMode!=='ai';
    if(inspectorTab){inspectorTab.classList.toggle('fs-side-tab-active',S.sideMode==='inspector');inspectorTab.setAttribute('aria-selected',String(S.sideMode==='inspector'));}
    if(aiTab){aiTab.classList.toggle('fs-side-tab-active',S.sideMode==='ai');aiTab.setAttribute('aria-selected',String(S.sideMode==='ai'));}
    if(S.sideMode==='ai'&&$('fs-chat-input'))$('fs-chat-input').focus();
  };
  window.filmStudioToggle=function(){
    if(S.open){window.filmStudioClose();return;}
    S.open=true;
    var panel=$('film-studio-panel'),launcher=document.querySelector('.film-studio-launch');
    panel.classList.add('fs-studio-expanded');panel.style.display='flex';
    document.documentElement.classList.add('fs-studio-open');
    if(launcher)launcher.style.display='none';
    $('film-studio-toggle').textContent='重新打开 Studio';
    $('editor-body').style.display='none';$('editor-empty').style.display='none';
    window.filmStudioSetSideMode(S.sideMode);
    filmStudioLoad(true);
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
    var panel=$('film-studio-panel'),launcher=document.querySelector('.film-studio-launch');
    if(panel){panel.classList.remove('fs-studio-expanded');panel.style.display='none';}
    document.documentElement.classList.remove('fs-studio-open');
    if(launcher)launcher.style.display='flex';
    $('film-studio-toggle').textContent='重新打开 Studio';
    // Exiting the Studio never resumes the old CompositionIR editor for an active film.html project.
    var migrated=!!(S.data&&S.data.exists);
    $('editor-body').style.display=migrated?'none':((typeof ED!=='undefined'&&ED.pid)?'':'none');
    $('editor-empty').style.display=migrated?'none':((typeof ED!=='undefined'&&ED.pid)?'none':'');
  };
  window.filmStudioLoad=filmStudioLoad;window.filmStudioInitialize=filmStudioInitialize;
  window.filmStudioSelectClip=function(id){S.selectedId=id;S.selectedIds=id?[id]:[];S.selectionAnchor=id;var c=findClip(id);if(c)S.playhead=c.at;render();};
  window.filmStudioSeek=function(value){
    var wasPlaying=!!window.filmStudioPlaying;
    S.playhead=Math.max(0,Math.min(S.duration,num(value,0)));window.filmStudioPlaying=false;setPlayheadVisual();
    syncRuntime(wasPlaying?'seek-pause':undefined);
  };
  window.filmStudioNudge=function(direction){window.filmStudioSeek(S.playhead+direction/30);};
  window.filmStudioTogglePlayback=function(){
    if(!S.data||!S.data.exists){showStatus('请先初始化影片文档。','error');return;}
    window.filmStudioPlaying=!window.filmStudioPlaying;
    syncRuntime(window.filmStudioPlaying?'play':'pause');
  };
  window.addEventListener('message',function(event){
    var frame=$('fs-preview-composition');
    if(!frame||event.source!==frame.contentWindow||event.origin!==window.location.origin)return;
    var message=event.data||{};
    if(message.type==='film-runtime:ready'){
      if(message.rev&&S.data&&S.data.exists&&message.rev!==S.rev){
        S.runtimeRev='';
        syncRuntime(S.runtimeCommand||(window.filmStudioPlaying?'play':'seek'));
      }else if(message.rev&&S.data&&S.data.exists&&message.rev===S.rev){
        S.runtimeRev=S.rev;
      }
    }else if(message.type==='film-runtime:time'){
      S.playhead=Math.max(0,Math.min(S.duration,num(message.time,S.playhead)));
      window.filmStudioPlaying=!!message.playing;setPlayheadVisual();
      var button=$('fs-play-toggle');if(button)button.textContent=window.filmStudioPlaying?'暂停':'播放';
    }else if(message.type==='film-runtime:ended'||message.type==='film-runtime:paused'){
      S.playhead=Math.max(0,Math.min(S.duration,num(message.time,S.playhead)));
      window.filmStudioPlaying=false;setPlayheadVisual();
      var button=$('fs-play-toggle');if(button)button.textContent='播放';
    }
  });
  window.filmStudioZoom=function(direction,anchorClientX){
    var scroll=$('fs-timeline-scroll'),rect=scroll&&scroll.getBoundingClientRect();
    var localX=rect?(Number.isFinite(anchorClientX)?anchorClientX-rect.left:rect.width/2):0;
    var anchorTime=scroll?Math.max(0,(scroll.scrollLeft+localX-132)/S.pxPerSec):S.playhead;
    S.pxPerSec=Math.max(12,Math.min(120,S.pxPerSec*(direction>0?1.25:0.8)));
    if(S.data&&S.data.exists){
      renderTimeline();
      if(scroll&&rect)scroll.scrollLeft=Math.max(0,132+anchorTime*S.pxPerSec-localX);
    }
  };
  (function bindFilmTimelineWheel(){
    var scroll=$('fs-timeline-scroll');if(!scroll)return;
    scroll.addEventListener('wheel',function(e){
      if(!(e.ctrlKey||e.metaKey))return;
      e.preventDefault();
      window.filmStudioZoom(e.deltaY<0?1:-1,e.clientX);
    },{passive:false});
  })();
  window.filmStudioAddTrack=async function(){
    if(!S.data||!S.data.exists||S.busy){showStatus('请先初始化影片文档。','error');return;}
    var label=window.prompt('新轨道名称','画面轨道');if(label==null)return;label=label.trim();
    if(!label){showStatus('轨道名称不能为空。','error');return;}
    var id='track-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8);
    await filmStudioCommit([{op:'track_add',track_id:id,label:label,index:0}]);
  };
  window.filmStudioRemoveEmptyTrack=async function(){
    if(!S.data||!S.data.exists||S.busy)return;
    var empty=(S.data.tracks||[]).filter(function(t){return !(t.clips||[]).length&&!Object.prototype.hasOwnProperty.call(t.attrs||{},'locked');});
    if(!empty.length){showStatus('当前没有可删除的空轨道。');return;}
    var track=empty[0];
    if(!window.confirm('删除空轨道「'+((track.attrs||{})['data-label']||track.id)+'」？'))return;
    await filmStudioCommit([{op:'track_remove',track_id:track.id}]);
  };
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
  window.filmStudioRemove=async function(){var ids=(S.selectedIds||[]).filter(function(id){return !!findClip(id);});if(!ids.length&&S.selectedId)ids=[S.selectedId];if(!ids.length){showStatus('请先选择片段。','error');return;}if(!window.confirm('删除选中的 '+ids.length+' 个片段？此操作可撤销。'))return;await filmStudioCommit(ids.map(function(id){return {op:'remove',clip:id};}));S.selectedId='';S.selectedIds=[];S.selectionAnchor='';render();};
  window.filmStudioUndo=function(){filmStudioHistory('undo');};window.filmStudioRedo=function(){filmStudioHistory('redo');};
  window.filmStudioRender=async function(){
    if(!S.projectId||!S.data||!S.data.exists){showStatus('请先载入或初始化 film.html。','error');return;}
    if(S.rendering){showStatus('当前已有导出任务。');return;}
    S.rendering=true;
    var pid=S.projectId,revision=S.rev,button=document.querySelector('[data-film-action="render"]');
    if(button)button.disabled=true;
    var resultBox=$('fs-render-result'),link=$('fs-render-download');
    if(resultBox)resultBox.hidden=true;
    showStatus('正在提交 film.html revision '+revision+' 的 MP4 导出任务……');
    try{
      var started=await apiPost('/api/editor/film/render',{project_id:pid,expected_revision:revision,fps:30});
      if(!started||!started.ok||!started.run_id){
        showStatus((started&&started.error)||'导出任务启动失败。','error');S.rendering=false;if(button)button.disabled=false;return;
      }
      showStatus('导出任务已启动。文档 revision：'+revision+'；渲染进度可从任务状态查看。');
      var terminal=false,readFailures=0;
      while(!terminal){
        await new Promise(function(resolve){setTimeout(resolve,1600);});
        var run=await apiGet('/api/workflows/'+encodeURIComponent(started.run_id));
        if(!run){
          readFailures++;
          if(readFailures>=8)throw new Error('无法读取导出任务状态；任务可能仍在后台运行，可从任务列表查看。');
          continue;
        }
        readFailures=0;
        if(run.status==='COMPLETED'){
          terminal=true;S.rendering=false;if(button)button.disabled=false;
          var out=run.result||{},path=out.mp4_path||'';
          if(S.projectId===pid&&path&&link&&resultBox){
            link.href='/file?path='+encodeURIComponent(path);
            link.textContent=out.current===false?'打开该版本 MP4（文档已更新）':'打开 MP4';
            var resultText=$('fs-render-result-text');
            if(resultText)resultText.textContent=(out.width||'—')+'×'+(out.height||'—')+' · '+(Number(out.duration||0).toFixed(2))+' 秒 · revision '+(out.rendered_revision||revision);
            resultBox.hidden=false;
          }
          if(S.projectId===pid){
            showStatus(out.current===false
              ?'MP4 已完成，但导出期间影片文档发生变化。成片对应 revision '+(out.rendered_revision||revision)+'，不是当前 revision。'
              :'MP4 已完成并通过视频流/时长检查。导出 revision：'+(out.rendered_revision||revision),
              out.current===false?'':'success');
          }
        }else if(run.status==='FAILED'||run.status==='INTERRUPTED'||run.status==='CANCELLED'){
          terminal=true;S.rendering=false;if(button)button.disabled=false;
          if(S.projectId===pid)showStatus((run.error&&String(run.error))||'MP4 导出失败，请检查素材范围与任务详情。','error');
        }
      }
    }catch(error){
      S.rendering=false;if(button)button.disabled=false;
      showStatus('MP4 导出请求失败：'+String(error&&error.message||error),'error');
    }
  };
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
  document.addEventListener('keydown',function(e){
    if(!S.open)return;
    var target=e.target;
    if(target&&target.matches&&target.matches('input,textarea,select,[contenteditable="true"]'))return;
    if((e.ctrlKey||e.metaKey)&&String(e.key).toLowerCase()==='a'){
      e.preventDefault();S.selectedIds=clips().map(function(c){return c.id;});S.selectedId=S.selectedIds[0]||'';S.selectionAnchor=S.selectedId;syncClipSelectionVisual();renderInspector();renderAssets();
    }else if((e.ctrlKey||e.metaKey)&&String(e.key).toLowerCase()==='z'){
      e.preventDefault();
      if(e.shiftKey)window.filmStudioRedo();else window.filmStudioUndo();
    }else if((e.ctrlKey||e.metaKey)&&String(e.key).toLowerCase()==='y'){
      e.preventDefault();window.filmStudioRedo();
    }else if(e.key==='Delete'||e.key==='Backspace'){
      e.preventDefault();window.filmStudioRemove();
    }else if(e.code==='Space'){
      e.preventDefault();window.filmStudioTogglePlayback();
    }else if(e.key==='ArrowLeft'){
      e.preventDefault();window.filmStudioNudge(e.shiftKey?-10:-1);
    }else if(e.key==='ArrowRight'){
      e.preventDefault();window.filmStudioNudge(e.shiftKey?10:1);
    }else if(e.key==='Home'){
      e.preventDefault();window.filmStudioSeek(0);
    }else if(e.key==='End'){
      e.preventDefault();window.filmStudioSeek(S.duration);
    }
  });
  var dock=$('fs-workspace-dock')||document.querySelector('.fs-workspace');
  function restoreDock(){
    try{
      var left=localStorage.getItem('film-studio-left-pane'),right=localStorage.getItem('film-studio-right-pane');
      if(left)dock.style.setProperty('--fs-left',left+'%');
      if(right)dock.style.setProperty('--fs-right',right+'%');
    }catch(_){}
  }
  function beginDivider(e){
    var divider=e.currentTarget,side=divider.getAttribute('data-divider');
    if(e.button!==0)return;
    var workspace=$('fs-workspace-dock');if(!workspace)return;
    S.paneDrag={side:side,divider:divider,rect:workspace.getBoundingClientRect()};
    divider.classList.add('fs-divider-dragging');
    try{divider.setPointerCapture(e.pointerId);}catch(_){}
    e.preventDefault();
  }
  function moveDivider(e){
    if(!S.paneDrag)return;
    var d=S.paneDrag,rect=d.rect,w=rect.width;
    if(!w)return;
    if(d.side==='left'){
      var left=Math.max(15,Math.min(38,(e.clientX-rect.left)/w*100));
      dock.style.setProperty('--fs-left',left.toFixed(2)+'%');
      try{localStorage.setItem('film-studio-left-pane',left.toFixed(2));}catch(_){}
    }else{
      var right=Math.max(18,Math.min(38,(rect.right-e.clientX)/w*100));
      dock.style.setProperty('--fs-right',right.toFixed(2)+'%');
      try{localStorage.setItem('film-studio-right-pane',right.toFixed(2));}catch(_){}
    }
  }
  function endDivider(){
    if(!S.paneDrag)return;S.paneDrag.divider.classList.remove('fs-divider-dragging');S.paneDrag=null;
  }
  document.querySelectorAll('.fs-pane-divider').forEach(function(el){
    el.addEventListener('pointerdown',beginDivider);
    el.addEventListener('pointermove',moveDivider);
    el.addEventListener('pointerup',endDivider);
    el.addEventListener('pointercancel',endDivider);
    el.addEventListener('keydown',function(e){
      if(e.key!=='ArrowLeft'&&e.key!=='ArrowRight')return;
      e.preventDefault();
      var side=el.getAttribute('data-divider'),delta=(e.key==='ArrowLeft'?-1:1)*(e.shiftKey?4:1);
      var current=parseFloat(getComputedStyle(dock).getPropertyValue(side==='left'?'--fs-left':'--fs-right'))||24;
      var next=Math.max(side==='left'?15:18,Math.min(side==='left'?38:38,current+(side==='left'?delta:-delta)));
      dock.style.setProperty(side==='left'?'--fs-left':'--fs-right',next+'%');
      try{localStorage.setItem(side==='left'?'film-studio-left-pane':'film-studio-right-pane',String(next));}catch(_){}
    });
  });
  restoreDock();
  var projectSelect=$('editor-project');if(projectSelect)projectSelect.addEventListener('change',function(){if(S.open)setTimeout(function(){filmStudioLoad(true);},50);});
  var legacyEditorLoad=window.editorLoad;
  if(typeof legacyEditorLoad==='function'){
    window.editorLoad=function(){
      var result=legacyEditorLoad.apply(this,arguments);
      Promise.resolve(result).finally(async function(){
        var selector=$('editor-project'),pid=(selector&&selector.value)||(typeof ED!=='undefined'&&ED.pid)||'';
        if(!pid)return;
        var state=await apiGet('/api/editor/film?project_id='+encodeURIComponent(pid));
        if(!state||!state.ok)return;
        if(state.exists&&!S.open)window.filmStudioToggle();
        else if(S.open)filmStudioLoad(true);
      });
      return result;
    };
  }

  const FILM_STUDIO_ACTIONS = {
    toggle: () => window.filmStudioToggle(),
    undo: () => window.filmStudioUndo(),
    redo: () => window.filmStudioRedo(),
    reload: () => window.filmStudioLoad(true),
    close: () => window.filmStudioClose(),
    'clips-mode': () => window.filmStudioShowAssetMode('clips'),
    'library-mode': () => window.filmStudioLoadLibrary(),
    'frame-prev': () => window.filmStudioNudge(-1),
    'play-toggle': () => window.filmStudioTogglePlayback(),
    'frame-next': () => window.filmStudioNudge(1),
    split: () => window.filmStudioSplit(),
    remove: () => window.filmStudioRemove(),
    'add-track': () => window.filmStudioAddTrack(),
    'remove-empty-track': () => window.filmStudioRemoveEmptyTrack(),
    'zoom-out': () => window.filmStudioZoom(-1),
    'zoom-in': () => window.filmStudioZoom(1),
    'apply-inspector': () => window.filmStudioSaveInspector(),
    'show-inspector': () => window.filmStudioSetSideMode('inspector'),
    'show-ai': () => window.filmStudioSetSideMode('ai'),
    render: () => window.filmStudioRender(),
    ask: () => window.filmStudioAsk(),
  };
  document.addEventListener('click', function(event) {
    var button = event.target && event.target.closest ? event.target.closest('[data-film-action]') : null;
    if (!button || button.disabled) return;
    var action = button.getAttribute('data-film-action');
    var handler = FILM_STUDIO_ACTIONS[action];
    if (typeof handler !== 'function') return;
    event.preventDefault();
    handler();
  });

})();
