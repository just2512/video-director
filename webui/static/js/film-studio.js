/* File-backed Studio UI. Each edit commits by stable clip ID and authoritative film revision. */
(function () {
  'use strict';
  var S = {open:false, projectId:'', rev:'', runtimeRev:'', runtimeReady:false, runtimeDocument:null, runtimeCommand:'', data:null, migration:null, selectedId:'', selectedIds:[], selectionAnchor:'', skipNextClipClick:false, proposal:null, previewProposalActive:false, events:null, broken:false, waveformCache:Object.create(null), waveformPending:Object.create(null), envelopeMode:false, slideMode:false, playhead:0, duration:10, pxPerSec:36, busy:false, rendering:false, drag:null, poll:null, messages:[], assetMode:'clips', libraryAssets:[], insertMode:false, elementInspect:false, selectedElement:null, hoveredElement:null};
  var $ = function (id) { return document.getElementById(id); };
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]); }); }
  function num(v, fallback) { var n = Number(v); return Number.isFinite(n) ? n : fallback; }
  function fmtTime(v) { var n = Math.max(0, Number(v) || 0), m = Math.floor(n / 60), s = n - m * 60; return String(m).padStart(2,'0') + ':' + s.toFixed(3).padStart(6,'0'); }
  function clips() { return (S.data && S.data.tracks || []).reduce(function (a,t) { return a.concat((t.clips || []).map(function (c) { c.track_id = t.id; c.trackLocked = Object.prototype.hasOwnProperty.call(t.attrs || {}, 'locked'); return c; })); }, []); }
  function findClip(id) { return clips().find(function (c) { return c.id === id; }) || null; }
  function showStatus(message, kind) { var el=$('film-studio-alert'); if(!el)return; el.className='fs-alert fs-visible'+(kind==='error'?' fs-error':kind==='success'?' fs-success':''); el.textContent=message||''; }
  function renderAiDirector() {
    var bridge=window.filmStudioReactAi,root=$('fs-react-ai-director'),legacy=$('fs-static-ai-director');
    if(bridge&&typeof bridge.render==='function'&&root){
      var proposal=S.proposal?{
        summary:String(S.proposal.summary||'AI 候选修改'),
        operationCount:(S.proposal.operations||[]).length,
        baseRevision:String(S.proposal.base_revision||'')
      }:null;
      try{
        bridge.render({
          messages:(S.messages||[]).map(function(item){return{text:String(item.text||''),role:String(item.role||'assistant')};}),
          proposal:proposal
        });
        root.style.display='flex';
        if(legacy)legacy.style.display='none';
        return true;
      }catch(error){
        console.error('React AI director mount failed; using static fallback.',error);
        root.style.display='none';
        if(legacy)legacy.style.display='';
      }
    }
    return false;
  }
  function appendMessage(text, role) {
    S.messages.push({text:String(text),role:role});
    if(renderAiDirector()){
      var reactLog=$('fs-chat-log');if(reactLog)reactLog.scrollTop=reactLog.scrollHeight;
      return;
    }
    var log=$('fs-chat-log'); if (!log) return;
    log.innerHTML=S.messages.map(function(m){return '<div class="fs-chat-message '+(m.role==='user'?'fs-user':'fs-assistant')+'">'+esc(m.text)+'</div>';}).join('');
    log.scrollTop=log.scrollHeight;
  }
  function resetPreview() {
    var frame=$('fs-preview-composition');
    if(frame){try{frame.contentWindow.postMessage({type:'film-runtime:pause'},window.location.origin);}catch(_){}frame.style.display='none';}
    var ph=$('fs-monitor-stage')&&$('fs-monitor-stage').querySelector('.fs-monitor-placeholder');
    if(ph){ph.textContent='选择项目并初始化影片文档';ph.style.display='';}
    S.runtimeRev='';S.runtimeCommand='';S.previewProposalActive=false;window.filmStudioPlaying=false;
    S.elementInspect=false;S.selectedElement=null;S.hoveredElement=null;
    renderElementInspector(null);
    var selection=$('fs-element-selection');if(selection)selection.hidden=true;
    var inspectButton=$('fs-element-inspect-toggle');if(inspectButton){inspectButton.setAttribute('aria-pressed','false');inspectButton.textContent='选择画面元素';}
    var button=$('fs-play-toggle');if(button)button.textContent='播放';
  }
  function syncRuntime(type) {
    var frame=$('fs-preview-composition');
    if(type)S.runtimeCommand=type;
    else if(!S.runtimeCommand)S.runtimeCommand=window.filmStudioPlaying?'play':'seek';
    var candidateActive=!!(S.previewProposalActive&&S.proposal);
    var runtimeData=candidateActive?S.proposal.candidate:S.data;
    var runtimeRevision=candidateActive?'proposal:'+S.proposal.id:S.rev;
    if(!frame||!frame.contentWindow||!S.runtimeReady||!runtimeData||!runtimeData.exists)return;
    frame.style.display='block';
    var ph=$('fs-monitor-stage').querySelector('.fs-monitor-placeholder');if(ph)ph.style.display='none';
    if(S.runtimeRev!==runtimeRevision){
      frame.contentWindow.postMessage({type:'film-runtime:load',data:runtimeData,rev:runtimeRevision},window.location.origin);
      S.runtimeRev=runtimeRevision;
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
  function renderElementSelection(element) {
    var box=$('fs-element-selection'),monitor=$('fs-monitor-stage');
    if(!box||!monitor||!element||!element.rect||!S.data||!S.data.stage){if(box)box.hidden=true;return;}
    var stageWidth=num(S.data.stage.width,1920),stageHeight=num(S.data.stage.height,1080);
    var monitorRect=monitor.getBoundingClientRect(),scale=Math.min(monitorRect.width/stageWidth,monitorRect.height/stageHeight);
    var offsetX=(monitorRect.width-stageWidth*scale)/2,offsetY=(monitorRect.height-stageHeight*scale)/2;
    var r=element.rect;
    box.style.left=(offsetX+num(r.x,0)*scale)+'px';
    box.style.top=(offsetY+num(r.y,0)*scale)+'px';
    box.style.width=Math.max(2,num(r.width,2)*scale)+'px';
    box.style.height=Math.max(2,num(r.height,2)*scale)+'px';
    box.hidden=false;
  }
  function hexColor(value) {
    var text=String(value||'').trim(),match=text.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
    if(match)return '#'+[match[1],match[2],match[3]].map(function(v){return Math.max(0,Math.min(255,Number(v))).toString(16).padStart(2,'0');}).join('');
    if(/^#[0-9a-f]{6}$/i.test(text))return text;
    if(/^#[0-9a-f]{3}$/i.test(text))return '#'+text.slice(1).split('').map(function(v){return v+v;}).join('');
    return '#ffffff';
  }
  function renderElementInspector(element) {
    var mount=$('fs-react-canvas-inspector');
    if(!mount)return;
    if(window.filmStudioReactInspector&&typeof window.filmStudioReactInspector.render==='function'){
      window.filmStudioReactInspector.render(element||null);
      return;
    }
    // A source checkout can run before npm build:studio has produced the React
    // bundle. Keep the same form contract live instead of leaving an empty pane.
    if(!element){mount.innerHTML='';return;}
    var rect=element.localRect||element.rect||{},styles=element.styles||{},rotateMatch=String(styles.rotate||'').match(/-?\d+(?:\.\d+)?/);
    mount.innerHTML='<section class="fs-inspector-element" data-canvas-inspector-fallback="true">'+
      '<h4>画布元素 · '+esc(element.tag)+' · '+esc(element.selector)+'</h4>'+
      '<label class="fs-inspector-field">文本<textarea id="fs-element-text" rows="2" '+(element.textEditable===false?'disabled title="该元素包含子元素，为避免破坏内部结构，暂不允许整体替换文本"':'')+'>'+esc(element.text||'')+'</textarea></label>'+
      '<label class="fs-inspector-field">左坐标<input id="fs-element-left" type="number" step="1" value="'+esc(num(rect.x,0).toFixed(1))+'"></label>'+
      '<label class="fs-inspector-field">上坐标<input id="fs-element-top" type="number" step="1" value="'+esc(num(rect.y,0).toFixed(1))+'"></label>'+
      '<label class="fs-inspector-field">宽度<input id="fs-element-width" type="number" min="1" max="10000" step="1" value="'+esc(num(rect.width,1).toFixed(1))+'"></label>'+
      '<label class="fs-inspector-field">高度<input id="fs-element-height" type="number" min="1" max="10000" step="1" value="'+esc(num(rect.height,1).toFixed(1))+'"></label>'+
      '<label class="fs-inspector-field">文字颜色<input id="fs-element-color" type="color" value="'+esc(hexColor(styles.color||'#ffffff'))+'"></label>'+
      '<label class="fs-inspector-field">字号（px）<input id="fs-element-font-size" type="number" min="1" max="1000" step="1" value="'+esc(num(parseFloat(styles.fontSize),24))+'"></label>'+
      '<label class="fs-inspector-field">旋转（度）<input id="fs-element-rotate" type="number" min="-3600" max="3600" step="1" value="'+esc(rotateMatch?Number(rotateMatch[0]):0)+'"></label>'+
      '<button type="button" class="fs-btn fs-primary" id="fs-apply-element">应用画布修改</button></section>';
    var apply=$('fs-apply-element');
    if(apply)apply.addEventListener('click',function(){if(window.filmStudioSaveSelectedElement)window.filmStudioSaveSelectedElement();});
  }
  function syncElementInspectorFields(element) {
    if(!element)return;
    if(window.filmStudioReactInspector&&typeof window.filmStudioReactInspector.sync==='function'){
      window.filmStudioReactInspector.sync(element);
      return;
    }
    var rect=element.localRect||element.rect||{},styles=element.styles||{},rotateMatch=String(styles.rotate||'').match(/-?\d+(?:\.\d+)?/);
    var values={
      'fs-element-text':String(element.text||''),
      'fs-element-left':num(rect.x,0).toFixed(1),
      'fs-element-top':num(rect.y,0).toFixed(1),
      'fs-element-width':num(rect.width,1).toFixed(1),
      'fs-element-height':num(rect.height,1).toFixed(1),
      'fs-element-color':hexColor(styles.color||'#ffffff'),
      'fs-element-font-size':String(num(parseFloat(styles.fontSize),24)),
      'fs-element-rotate':String(rotateMatch?Number(rotateMatch[0]):0)
    };
    Object.keys(values).forEach(function(id){
      var input=$(id);if(input&&!(id==='fs-element-text'&&input.disabled))input.value=values[id];
    });
  }
  function renderMigration() {
    var data=S.migration||{}, preview=data.migration_preview||{}, warnings=preview.warnings||[];
    $('fs-project-label').textContent=S.projectId;$('fs-clip-count').textContent='—';$('fs-timeline-meta').textContent='迁移预览';
    $('fs-assets-list').innerHTML='<div class="fs-empty">当前项目还没有 film.html。初始化将生成可编辑影片文档，不会覆盖旧 Composition。</div>';
    $('fs-timeline').innerHTML='<div class="fs-empty">先检查迁移提示，再初始化。</div>';
    if(window.filmStudioReactInspector&&typeof window.filmStudioReactInspector.renderClip==='function')window.filmStudioReactInspector.renderClip(null,'初始化影片文档后才能编辑片段。');
    else $('fs-inspector').innerHTML='<div class="fs-empty">初始化影片文档后才能编辑片段。</div>';
    resetPreview();
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
    if(window.filmStudioReactAssets&&typeof window.filmStudioReactAssets.render==='function'){
      var assetClips=all.map(function(c){
        var attrs=c.attrs||{},aid=attrs['data-asset-id']||'',label=attrs.title||attrs['data-role']||c.id;
        var thumbnail=aid&&c.tag!=='audio'?'/api/asset_thumbnail?asset_id='+encodeURIComponent(aid)+'&project_id='+encodeURIComponent(S.projectId):'';
        return {
          id:c.id,tag:c.tag,title:String(label),
          meta:fmtTime(c.at)+' · '+(c.duration==null?'时长未知':c.duration.toFixed(2)+'s'),
          thumbnail:thumbnail,selected:c.id===S.selectedId
        };
      });
      var libraryAssets=S.libraryAssets.filter(function(a){return ['video','image','audio'].indexOf(String(a.type||'').toLowerCase())>=0;})
        .map(function(a){
          var id=String(a.asset_id||a.id||''),kind=String(a.type||'').toLowerCase(),label=String(a.description||a.name||a.filename||id);
          return {id:id,kind:kind,label:label,meta:kind.toUpperCase()+(a.duration?' · '+Number(a.duration).toFixed(2)+'s':'')+(a.source?' · '+String(a.source):'')};
        });
      window.filmStudioReactAssets.render({
        mode:S.assetMode==='library'?'library':'clips',
        clips:assetClips,libraryAssets:libraryAssets
      });
      return;
    }
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
    var selected=findClip(S.selectedId),request={project_id:S.projectId,base:S.rev,asset_id:assetId,at:S.playhead,still_duration:5};
    if(S.insertMode){
      if(!selected){showStatus('插入编辑模式下，请先选择目标轨道上的片段。','error');return;}
      request.placement_mode='ripple';request.track_id=selected.track_id;
    }
    S.busy=true;
    try{
      var response=await apiPost('/api/editor/film/insert-asset',request);
      if(!response||!response.ok){showStatus((response&&response.error)||'素材加入时间线失败。','error');return;}
      S.data=response;S.rev=response.rev;S.selectedId=response.inserted_clip_id||'';
      S.assetMode='clips';window.filmStudioShowAssetMode('clips');render();
      showStatus(S.insertMode?'插入编辑已完成：目标轨道及同步锁定轨道已按素材时长推移。':'已在播放头位置新建轨道并添加素材。', 'success');
    }finally{S.busy=false;}
  };
  window.filmStudioToggleInsertMode=function(){
    S.insertMode=!S.insertMode;
    var button=document.querySelector('[data-film-action="insert-mode"]');
    if(button){button.textContent='插入编辑：'+(S.insertMode?'开':'关');button.classList.toggle('fs-primary',S.insertMode);button.setAttribute('aria-pressed',S.insertMode?'true':'false');}
    showStatus(S.insertMode?'插入编辑已开启：选择目标轨道上的片段，再从素材库添加素材。':'插入编辑已关闭。','success');
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
    var reactTimeline=window.filmStudioReactTimeline&&typeof window.filmStudioReactTimeline.renderSurface==='function';
    if(reactTimeline){
      S.timelineRenderSequence=(S.timelineRenderSequence||0)+1;
      var renderModel={
        renderKey:String(S.timelineRenderSequence),laneWidth:laneWidth,totalWidth:132+laneWidth,
        pixelsPerSecond:S.pxPerSec,
        ticks:(function(){var items=[];for(var i=0;i<=ticks;i++){var sec=Math.min(S.duration,i*5);items.push({at:sec,label:fmtTime(sec),left:sec*S.pxPerSec});}return items;})(),
        tracks:S.data.tracks.map(function(track){
          var attrs=track.attrs||{},locked=Object.prototype.hasOwnProperty.call(attrs,'locked'),
              syncLocked=Object.prototype.hasOwnProperty.call(attrs,'sync-lock'),
              hidden=Object.prototype.hasOwnProperty.call(attrs,'hidden'),muted=Object.prototype.hasOwnProperty.call(attrs,'muted');
          return {
            id:track.id,label:String(attrs['data-label']||track.id),locked:locked,syncLocked:syncLocked,hidden:hidden,muted:muted,
            clips:(track.clips||[]).map(function(clip){
              var clipAttrs=clip.attrs||{},aid=clipAttrs['data-asset-id']||'';
              var thumbnail=aid&&clip.tag!=='audio'?'/api/asset_thumbnail?asset_id='+encodeURIComponent(aid)+'&project_id='+encodeURIComponent(S.projectId):'';
              return {
                id:clip.id,trackId:track.id,tag:clip.tag,title:String(clipAttrs.title||clip.id),
                left:Math.max(0,clip.at*S.pxPerSec),width:Math.max(24,(clip.duration==null?2:clip.duration)*S.pxPerSec),
                atLabel:fmtTime(clip.at),durationLabel:clip.duration==null?'':' · '+clip.duration.toFixed(2)+'s',
                selected:(S.selectedIds||[]).indexOf(clip.id)>=0,thumbnail:thumbnail,
                waveform:clip.tag==='audio',envelope:(clip.tag==='audio'||clip.tag==='video')&&clip.duration!=null,
                locked:Object.prototype.hasOwnProperty.call(clipAttrs,'data-clip-locked'),
                trimHandles:clip.duration!=null&&!Object.prototype.hasOwnProperty.call(clipAttrs,'data-clip-locked')
              };
            })
          };
        })
      };
      window.filmStudioReactTimeline.renderSurface(renderModel);
    }else{
      var ruler='<div class="fs-ruler" style="width:'+(132+laneWidth)+'px"><div class="fs-ruler-head">时码</div><div class="fs-ruler-body" id="fs-ruler-body" style="width:'+laneWidth+'px">';
      for(var i=0;i<=ticks;i++){var sec=Math.min(S.duration,i*5);ruler+='<div class="fs-ruler-mark" data-at="'+sec+'" style="left:'+(sec*S.pxPerSec)+'px">'+esc(fmtTime(sec))+'</div>';}
      ruler+='</div></div>';
      var body=S.data.tracks.map(function(track){
        var attrs=track.attrs||{},locked=Object.prototype.hasOwnProperty.call(attrs,'locked'),
            syncLocked=Object.prototype.hasOwnProperty.call(attrs,'sync-lock'),
            hidden=Object.prototype.hasOwnProperty.call(attrs,'hidden'),muted=Object.prototype.hasOwnProperty.call(attrs,'muted'),
            label=attrs['data-label']||track.id;
        var lane='<div class="fs-track-body" style="width:'+laneWidth+'px">';
        (track.clips||[]).forEach(function(clip){
          var clipLocked=Object.prototype.hasOwnProperty.call(clip.attrs||{},'data-clip-locked');
          var width=Math.max(24,(clip.duration==null?2:clip.duration)*S.pxPerSec),left=Math.max(0,clip.at*S.pxPerSec),title=(clipLocked?'🔒 ':'')+(clip.attrs.title||clip.id);
          var aid=clip.attrs['data-asset-id']||'',thumb=aid&&clip.tag!=='audio'?'/api/asset_thumbnail?asset_id='+encodeURIComponent(aid)+'&project_id='+encodeURIComponent(S.projectId):'';
          var thumbHtml=thumb?'<img class="fs-clip-thumb" src="'+esc(thumb)+'" loading="lazy" alt="" onerror="this.remove()">':'';
          var waveformHtml=clip.tag==='audio'?'<canvas class="fs-clip-waveform" data-waveform-clip="'+esc(clip.id)+'" aria-label="音频波形"></canvas>':'';
          var envelopeHtml=(clip.tag==='audio'||clip.tag==='video')&&clip.duration!=null?'<canvas class="fs-clip-envelope" data-envelope-clip="'+esc(clip.id)+'" aria-label="音量包络：拖动添加或调整关键帧；Shift 或右键删除内部点"></canvas>':'';
          var handles=clip.duration==null||clipLocked?'':'<span class="fs-trim-handle fs-trim-left" data-trim="start" title="修剪入点"></span><span class="fs-trim-handle fs-trim-right" data-trim="end" title="修剪出点"></span>';
          lane+='<div class="fs-clip '+(clipLocked?'fs-clip-locked ':'')+((S.selectedIds||[]).indexOf(clip.id)>=0?'fs-clip-selected ':'')+'" data-clip="'+esc(clip.id)+'" data-track="'+esc(track.id)+'" data-kind="'+esc(clip.tag)+'" style="left:'+left+'px;width:'+width+'px" title="'+esc(title)+'">'+thumbHtml+waveformHtml+envelopeHtml+handles+'<span class="fs-clip-title">'+esc(title)+'</span><span class="fs-clip-subtitle">'+esc(fmtTime(clip.at))+(clip.duration==null?'':' · '+clip.duration.toFixed(2)+'s')+'</span></div>';
        });
        lane+='</div>';
        return '<div class="fs-track '+(locked?'fs-track-locked ':'')+(syncLocked?'fs-sync-locked ':'')+(hidden?'fs-hidden-track ':'')+(muted?'fs-muted-track':'')+'" data-track="'+esc(track.id)+'"><div class="fs-track-head"><span class="fs-track-label" title="'+esc(label)+'">'+esc(label)+'</span><span class="fs-track-controls"><button type="button" class="fs-btn" data-track-lock="'+esc(track.id)+'" title="'+(locked?'解锁轨道':'锁定轨道')+'">'+(locked?'🔒':'锁')+'</button><button type="button" class="fs-btn" data-track-flag="sync-lock" data-track-key="'+esc(track.id)+'" aria-pressed="'+(syncLocked?'true':'false')+'" title="'+(syncLocked?'取消同步锁定':'启用同步锁定')+'">'+(syncLocked?'⇄':'同步')+'</button><button type="button" class="fs-btn" data-track-flag="hidden" data-track-key="'+esc(track.id)+'" title="'+(hidden?'显示轨道':'隐藏轨道')+'">'+(hidden?'显':'隐')+'</button><button type="button" class="fs-btn" data-track-flag="muted" data-track-key="'+esc(track.id)+'" title="'+(muted?'取消静音':'静音轨道')+'">'+(muted?'🔊':'🔇')+'</button></span></div>'+lane+'</div>';
      }).join('');
      $('fs-timeline').innerHTML=ruler+body+'<div class="fs-playhead" id="fs-playhead-line"></div>';
    }
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
        if(S.busy){e.preventDefault();showStatus('上一项编辑仍在保存，暂时不能开始下一次拖动。');return;}
        var clip=findClip(el.getAttribute('data-clip'));if(!clip)return;
        if(Object.prototype.hasOwnProperty.call(clip.attrs||{},'data-clip-locked')){
          selectClipWithModifiers(clip.id,e);
          S.playhead=clip.at;setPlayheadVisual();previewCurrent();renderInspector();renderAssets();
          e.preventDefault();showStatus('该片段已锁定，只能查看属性；如需编辑，请在属性栏显式解锁。');return;
        }
        var trim=e.target.closest('[data-trim]'),rollGesture=!!(trim&&e.ctrlKey),
            slideGesture=!!(!trim&&(S.slideMode||(e.altKey&&e.shiftKey))&&(clip.tag==='video'||clip.tag==='audio')),canDrag;
        if(rollGesture||slideGesture){S.selectedId=clip.id;S.selectedIds=[clip.id];S.selectionAnchor=clip.id;syncClipSelectionVisual();renderInspector();renderAssets();canDrag=true;}
        else{canDrag=selectClipWithModifiers(clip.id,e);}
        if(!canDrag){e.preventDefault();return;}
        S.playhead=clip.at;setPlayheadVisual();previewCurrent();
        var mode=trim?(rollGesture?'roll':(trim.getAttribute('data-trim')==='start'?'trim-start':'trim-end'))
          :(slideGesture?'slide':((e.altKey&&(clip.tag==='video'||clip.tag==='audio'))?'slip':'move'));
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
        if(mode==='slide'){
          var slideTrack=(S.data.tracks||[]).find(function(t){return t.id===clip.track_id;});
          var slideClips=(slideTrack&&slideTrack.clips||[]).slice().sort(function(a,b){return a.at-b.at;});
          var slideIndex=slideClips.findIndex(function(item){return item.id===clip.id;});
          var leftClip=slideIndex>0?slideClips[slideIndex-1]:null;
          var rightClip=slideIndex>=0&&slideIndex+1<slideClips.length?slideClips[slideIndex+1]:null;
          if(!leftClip||!rightClip||leftClip.end==null||clip.end==null
             ||Math.abs(leftClip.end-clip.at)>0.001||Math.abs(clip.end-rightClip.at)>0.001){
            showStatus('Alt+Shift 滑移式修剪要求所选片段左右都有时间上连续的邻接片段。','error');e.preventDefault();return;
          }
          var elements=Array.from($('fs-timeline').querySelectorAll('[data-clip]'));
          var leftElement=elements.find(function(n){return n.getAttribute('data-clip')===leftClip.id;});
          var rightElement=elements.find(function(n){return n.getAttribute('data-clip')===rightClip.id;});
          if(!leftElement||!rightElement){showStatus('无法定位滑移式修剪的邻接片段。','error');return;}
          S.drag={id:clip.id,mode:'slide',startX:e.clientX,startY:e.clientY,
            leftClipId:leftClip.id,centerClipId:clip.id,rightClipId:rightClip.id,
            leftDuration:leftClip.duration,centerDuration:clip.duration,rightDuration:rightClip.duration,
            centerAt:clip.at,rightAt:rightClip.at,pxPerSec:S.pxPerSec,
            startScrollLeft:$('fs-timeline-scroll').scrollLeft,active:false,element:el,
            slideElements:[leftElement,el,rightElement],members:members,targetTrackId:clip.track_id,targetRow:null};
        }else if(mode==='roll'){
          var trackData=(S.data.tracks||[]).find(function(t){return t.id===clip.track_id;});
          var ordered=(trackData&&trackData.clips||[]).slice().sort(function(a,b){return a.at-b.at;});
          var index=ordered.findIndex(function(item){return item.id===clip.id;}),leftClip=null,rightClip=null;
          var edge=trim.getAttribute('data-trim');
          if(edge==='end'&&index>=0&&index+1<ordered.length&&clip.end!=null&&Math.abs(clip.end-ordered[index+1].at)<=0.001){leftClip=clip;rightClip=ordered[index+1];}
          else if(edge==='start'&&index>0&&ordered[index-1].end!=null&&Math.abs(ordered[index-1].end-clip.at)<=0.001){leftClip=ordered[index-1];rightClip=clip;}
          else{showStatus('Ctrl 拖动切口只支持同轨相邻片段共用的切口。','error');e.preventDefault();return;}
          var leftElement=Array.from($('fs-timeline').querySelectorAll('[data-clip]')).find(function(n){return n.getAttribute('data-clip')===leftClip.id;});
          var rightElement=Array.from($('fs-timeline').querySelectorAll('[data-clip]')).find(function(n){return n.getAttribute('data-clip')===rightClip.id;});
          if(!leftElement||!rightElement){showStatus('无法定位切口两侧片段。','error');return;}
          S.drag={id:clip.id,mode:'roll',startX:e.clientX,startY:e.clientY,leftClipId:leftClip.id,rightClipId:rightClip.id,
            rightAt:rightClip.at,leftDuration:leftClip.duration,rightDuration:rightClip.duration,pxPerSec:S.pxPerSec,
            startScrollLeft:$('fs-timeline-scroll').scrollLeft,active:false,element:el,rollElements:[leftElement,rightElement],
            members:members,targetTrackId:clip.track_id,targetRow:null};
        }else{
          if(mode!=='move')members=members.filter(function(m){return m.id===clip.id;});
          S.drag={id:clip.id,mode:mode,startX:e.clientX,startY:e.clientY,originalAt:clip.at,
            originalEnd:clip.end==null?clip.at+num(clip.duration,2):clip.end,trackId:clip.track_id,
            originalTrackIndex:trackOrder.indexOf(clip.track_id),trackOrder:trackOrder,
            startScrollLeft:$('fs-timeline-scroll').scrollLeft,active:false,element:el,members:members,
            targetTrackId:clip.track_id,targetRow:null};
        }
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
    renderAudioWaveforms();
  }
  async function decodeAudioWaveform(url){
    var key=String(url||'').split('#',1)[0];
    if(S.waveformCache[key])return S.waveformCache[key];
    if(S.waveformPending[key])return S.waveformPending[key];
    var task=(async function(){
      var response=await fetch(key,{credentials:'same-origin'});
      if(!response.ok)throw new Error('音频素材读取失败');
      var declared=Number(response.headers.get('content-length')||0);
      if(declared>8*1024*1024)throw new Error('素材超过 8 MiB，跳过波形解码');
      var bytes=await response.arrayBuffer();
      if(bytes.byteLength>8*1024*1024)throw new Error('素材超过 8 MiB，跳过波形解码');
      var AudioCtx=window.AudioContext||window.webkitAudioContext;
      if(!AudioCtx)throw new Error('当前浏览器不支持音频解码');
      var context=new AudioCtx();
      try{
        var buffer=await context.decodeAudioData(bytes.slice(0));
        if(!Number.isFinite(buffer.duration)||buffer.duration<=0||buffer.duration>600)
          throw new Error('音频时长超过 600 秒波形解码限制');
        var bins=8192,peaks=new Uint8Array(bins),channels=[];
        for(var c=0;c<buffer.numberOfChannels;c++)channels.push(buffer.getChannelData(c));
        var sampleCount=buffer.length;
        for(var b=0;b<bins;b++){
          var start=Math.floor(b*sampleCount/bins);
          var end=Math.max(start+1,Math.floor((b+1)*sampleCount/bins));
          var stride=Math.max(1,Math.floor((end-start)/96)),peak=0;
          for(var i=start;i<end;i+=stride){
            for(var ch=0;ch<channels.length;ch++){
              var value=Math.abs(channels[ch][i]);
              if(value>peak)peak=value;
            }
          }
          peaks[b]=Math.max(0,Math.min(255,Math.round(peak*255)));
        }
        var result={duration:buffer.duration,peaks:peaks};
        S.waveformCache[key]=result;
        var keys=Object.keys(S.waveformCache);
        while(keys.length>8){delete S.waveformCache[keys.shift()];}
        return result;
      }finally{try{await context.close();}catch(_){}}
    })();
    S.waveformPending[key]=task;
    try{return await task;}finally{delete S.waveformPending[key];}
  }
  async function drawAudioWaveform(canvas){
    var id=canvas.getAttribute('data-waveform-clip'),clip=findClip(id);
    if(!clip||!clip.media_url)return;
    try{
      var data=await decodeAudioWaveform(clip.media_url);
      if(!canvas.isConnected)return;
      var width=Math.max(16,Math.min(1800,Math.ceil((clip.duration||1)*S.pxPerSec)));
      var height=Math.max(18,Math.min(40,canvas.parentElement?canvas.parentElement.clientHeight-6:30));
      canvas.width=width;canvas.height=height;
      var ctx=canvas.getContext('2d');if(!ctx)return;
      ctx.clearRect(0,0,width,height);
      var sourceStart=Math.max(0,Number(clip.src_in)||0);
      var sourceEnd=Number.isFinite(clip.src_out)?Math.min(data.duration,clip.src_out):data.duration;
      var first=Math.max(0,Math.min(data.peaks.length-1,Math.floor(sourceStart/data.duration*data.peaks.length)));
      var last=Math.max(first+1,Math.min(data.peaks.length,Math.ceil(sourceEnd/data.duration*data.peaks.length)));
      var count=last-first,mid=height/2;
      ctx.fillStyle='rgba(211,250,241,.72)';
      for(var x=0;x<width;x++){
        var idx=first+Math.min(count-1,Math.floor(x/width*count));
        var amplitude=(data.peaks[idx]||0)/255;
        var bar=Math.max(1,amplitude*(height-4));
        ctx.fillRect(x,mid-bar/2,1,bar);
      }
      canvas.title='素材波形 · 源区间 '+sourceStart.toFixed(2)+'–'+sourceEnd.toFixed(2)+' 秒';
    }catch(error){
      if(canvas.isConnected)canvas.title='波形暂不可用：'+String(error&&error.message||error);
    }
  }
  function renderAudioWaveforms(){
    var timeline=$('fs-timeline');if(!timeline)return;
    timeline.querySelectorAll('[data-waveform-clip]').forEach(function(canvas){drawAudioWaveform(canvas);});
  }
  function envelopePointsForClip(clip){
    var raw=clip&&clip.attrs&&clip.attrs.volume_envelope;
    if(raw){try{return JSON.parse(raw);}catch(_){}}
    var duration=Math.max(0.04,num(clip&&clip.duration,0.04));
    return [{t:0,v:1},{t:duration,v:1}];
  }
  function drawVolumeEnvelope(canvas,points){
    if(!canvas.isConnected)return;
    var clip=findClip(canvas.getAttribute('data-envelope-clip'));
    if(!clip||clip.duration==null||clip.duration<=0)return;
    var width=Math.max(24,Math.min(1800,Math.ceil(clip.duration*S.pxPerSec)));
    var height=Math.max(24,canvas.parentElement?canvas.parentElement.clientHeight-2:40);
    canvas.width=width;canvas.height=height;
    var ctx=canvas.getContext('2d');if(!ctx)return;
    ctx.clearRect(0,0,width,height);
    function xy(point){return {x:Math.max(0,Math.min(width,point.t/clip.duration*width)),
      y:height-4-Math.max(0,Math.min(1,point.v))*(height-8)};}
    var plotted=points.filter(function(p){return p.t>=0&&p.t<=clip.duration;});
    if(!plotted.length)plotted=[{t:0,v:1},{t:clip.duration,v:1}];
    var first=plotted[0],last=plotted[plotted.length-1];
    if(first.t>0)plotted.unshift({t:0,v:first.v});
    if(last.t<clip.duration)plotted.push({t:clip.duration,v:last.v});
    ctx.strokeStyle='rgba(255,220,134,.95)';ctx.lineWidth=1.5;ctx.beginPath();
    plotted.forEach(function(point,index){var p=xy(point);if(index===0)ctx.moveTo(p.x,p.y);else ctx.lineTo(p.x,p.y);});
    ctx.stroke();
    ctx.fillStyle='#fff0b3';ctx.strokeStyle='#4a3620';ctx.lineWidth=1;
    points.filter(function(p){return p.t>=0&&p.t<=clip.duration;}).forEach(function(point){
      var p=xy(point);ctx.beginPath();ctx.arc(p.x,p.y,3,0,Math.PI*2);ctx.fill();ctx.stroke();
    });
    canvas.title='音量包络倍率 0–1 · '+points.length+' 个关键帧。拖动曲线添加/调整，Shift 或右键删除内部点。';
  }
  function volumeEnvelopeAtStudio(points,time){
    if(!points||!points.length)return 1;
    if(time<=points[0].t)return points[0].v;
    for(var i=0;i<points.length-1;i++){
      var left=points[i],right=points[i+1];
      if(time<=right.t)return left.v+(right.v-left.v)*Math.max(0,Math.min(1,(time-left.t)/(right.t-left.t)));
    }
    return points[points.length-1].v;
  }
  function bindVolumeEnvelopeCanvas(canvas){
    var clip=findClip(canvas.getAttribute('data-envelope-clip'));
    if(!clip||clip.duration==null||clip.duration<=0)return;
    canvas.addEventListener('contextmenu',function(e){e.preventDefault();});
    canvas.addEventListener('pointerdown',function(e){
      if(e.button!==0&&e.button!==2)return;
      e.preventDefault();e.stopPropagation();
      if(S.busy||S.broken){showStatus('当前无法编辑音量包络。','error');return;}
      var duration=clip.duration,rect=canvas.getBoundingClientRect();
      if(duration<=0.02){showStatus('片段短于 0.02 秒，无法安全添加内部音量关键帧。','error');return;}
      if(!rect.width||!rect.height)return;
      var time=Math.max(0,Math.min(duration,(e.clientX-rect.left)/rect.width*duration));
      var value=Math.max(0,Math.min(1,1-((e.clientY-rect.top)-4)/Math.max(1,rect.height-8)));
      var points=envelopePointsForClip(clip).map(function(p){return {t:Number(p.t),v:Number(p.v)};});
      var nearest=-1,nearestPx=Infinity;
      points.forEach(function(p,i){
        if(p.t<=0.0001||p.t>=duration-0.0001)return;
        var px=Math.abs(p.t/duration*rect.width-(e.clientX-rect.left));
        if(px<nearestPx){nearestPx=px;nearest=i;}
      });
      if((e.button===2||e.shiftKey)&&nearest>=0&&nearestPx<=12){
        points.splice(nearest,1);
        if(points.length<2){showStatus('至少需要两个音量关键帧。','error');return;}
        filmStudioCommit([{op:'set',clip:clip.id,field:'volume_envelope',value:JSON.stringify(points)}]);
        return;
      }
      if(e.button!==0)return;
      var index=-1;
      points.forEach(function(p,i){
        var px=Math.abs(p.t/duration*rect.width-(e.clientX-rect.left));
        var renderedY=rect.height-4-p.v*(rect.height-8);
        var py=Math.abs(renderedY-(e.clientY-rect.top));
        if(px<=10&&py<=10){index=i;nearestPx=px;}
      });
      var added=false;
      if(index<0){
        value=volumeEnvelopeAtStudio(points,time);
        // Clicking the envelope canvas inserts a new local keyframe at the clicked
        // time and value; dragging immediately after insertion edits its gain.
        value=Math.max(0,Math.min(1,1-((e.clientY-rect.top)-4)/Math.max(1,rect.height-8)));
        time=Math.max(0.01,Math.min(duration-0.01,time));
        index=points.findIndex(function(p){return p.t>time;});
        if(index<0)index=points.length;
        points.splice(index,0,{t:time,v:value});added=true;
      }
      S.envelopeDrag={clipId:clip.id,canvas:canvas,points:points,index:index,duration:duration,active:added,changed:added};
      if(canvas.setPointerCapture)try{canvas.setPointerCapture(e.pointerId);}catch(_){}
      drawVolumeEnvelope(canvas,points);
    });
  }
  window.filmStudioSetEnvelopeMode=function(enabled){
    S.envelopeMode=!!enabled;
    if(S.envelopeMode)S.slideMode=false;
    var panel=$('film-studio-panel'),button=document.querySelector('[data-film-action="envelope-mode"]'),
        slideButton=document.querySelector('[data-film-action="slide-mode"]');
    if(panel){
      panel.classList.toggle('fs-envelope-mode',S.envelopeMode);
      panel.classList.toggle('fs-slide-mode',S.slideMode);
    }
    if(button){
      button.classList.toggle('fs-envelope-mode-active',S.envelopeMode);
      button.textContent=S.envelopeMode?'音量曲线：开':'音量曲线：关';
      button.setAttribute('aria-pressed',String(S.envelopeMode));
    }
    if(slideButton){
      slideButton.classList.toggle('fs-slide-mode-active',S.slideMode);
      slideButton.textContent=S.slideMode?'滑移修剪：开':'滑移修剪：关';
      slideButton.setAttribute('aria-pressed',String(S.slideMode));
    }
    showStatus(S.envelopeMode
      ?'音量曲线模式：点击曲线添加关键帧；拖动关键帧调整；Shift 或右键点击内部点删除。切回“音量曲线：关”即可正常移动片段。'
      :'普通剪辑模式已恢复；片段可直接拖动、修剪和切分。');
  };
  window.filmStudioToggleEnvelopeMode=function(){window.filmStudioSetEnvelopeMode(!S.envelopeMode);};
  window.filmStudioSetSlideMode=function(enabled){
    S.slideMode=!!enabled;
    if(S.slideMode)S.envelopeMode=false;
    var panel=$('film-studio-panel'),button=document.querySelector('[data-film-action="slide-mode"]'),
        envelopeButton=document.querySelector('[data-film-action="envelope-mode"]');
    if(panel){
      panel.classList.toggle('fs-slide-mode',S.slideMode);
      panel.classList.toggle('fs-envelope-mode',S.envelopeMode);
    }
    if(button){
      button.classList.toggle('fs-slide-mode-active',S.slideMode);
      button.textContent=S.slideMode?'滑移修剪：开':'滑移修剪：关';
      button.setAttribute('aria-pressed',String(S.slideMode));
    }
    if(envelopeButton){
      envelopeButton.classList.toggle('fs-envelope-mode-active',S.envelopeMode);
      envelopeButton.textContent=S.envelopeMode?'音量曲线：开':'音量曲线：关';
      envelopeButton.setAttribute('aria-pressed',String(S.envelopeMode));
    }
    showStatus(S.slideMode
      ?'滑移修剪模式已开启：拖动视频/音频片段主体，片段长度不变并联动左右相邻片段。点击按钮关闭；Alt+Shift 拖动也可直接使用。'
      :'普通剪辑模式已恢复；片段可直接拖动、修剪和切分。');
  };
  window.filmStudioToggleSlideMode=function(){window.filmStudioSetSlideMode(!S.slideMode);};
  function renderVolumeEnvelopes(){
    var timeline=$('fs-timeline');if(!timeline)return;
    timeline.querySelectorAll('[data-envelope-clip]').forEach(function(canvas){
      var clip=findClip(canvas.getAttribute('data-envelope-clip'));
      if(!clip)return;
      drawVolumeEnvelope(canvas,envelopePointsForClip(clip));
      bindVolumeEnvelopeCanvas(canvas);
    });
  }
  function renderInspector() {
    var clip=findClip(S.selectedId);
    if(!clip){
      if(window.filmStudioReactInspector&&typeof window.filmStudioReactInspector.renderClip==='function')window.filmStudioReactInspector.renderClip(null);
      else $('fs-inspector').innerHTML='<div class="fs-empty">选择时间线片段后编辑属性。</div>';
      renderElementInspector(null);
      return;
    }
    var a=clip.attrs||{};
    var projectFps=Math.max(1,Math.min(60,num(S.data&&S.data.fps,30)));
    var frameStep=1/projectFps;
    var fields='<div class="fs-inspector-description"><strong>'+esc(clip.id)+'</strong><br>'+esc(clip.tag)+'<br>'+esc(a.src||'')+'</div><div class="fs-inspector-section">'+
      '<label class="fs-inspector-field">时间位置（秒）<input id="fs-field-at" type="number" min="0" step="'+esc(frameStep)+'" value="'+esc(a.at||clip.at||0)+'"></label>';
    if(clip.tag==='video'||clip.tag==='audio'){
      fields+='<label class="fs-inspector-field">速度<input id="fs-field-speed" type="number" min="0.25" max="4" step="0.05" value="'+esc(a.speed||1)+'"></label>';
      fields+='<div class="fs-speed-presets"><span>预设倍率</span><div>'+[0.5,1,1.5,2,4].map(function(value){return '<button type="button" class="fs-speed-chip '+(Math.abs(num(a.speed,1)-value)<0.001?'fs-speed-chip-active':'')+'" data-speed-preset="'+value+'">'+value+'×</button>';}).join('')+'</div></div>';
      fields+='<label class="fs-inspector-field">音量<input id="fs-field-volume" type="number" min="0" max="2" step="0.05" value="'+esc(a.volume==null?1:a.volume)+'"></label>';
      fields+='<label class="fs-inspector-field">静音<select id="fs-field-muted"><option value="false">否</option><option value="true"'+(Object.prototype.hasOwnProperty.call(a,'muted')?' selected':'')+'>是</option></select></label>';
    }
    if(clip.duration!=null){
      var fadeValues=String(a.fade||'0,0').split(',');
      fields+='<label class="fs-inspector-field">淡入（秒）<input id="fs-field-fade-in" type="number" min="0" max="60" step="0.05" value="'+esc(fadeValues[0]||0)+'"></label>';
      fields+='<label class="fs-inspector-field">淡出（秒）<input id="fs-field-fade-out" type="number" min="0" max="60" step="0.05" value="'+esc(fadeValues[1]||0)+'"></label>';
    }
    fields+='<label class="fs-inspector-field">片段标题<input id="fs-field-title" type="text" maxlength="200" value="'+esc(a.title||'')+'"></label></div><div class="fs-inspector-description fs-inspector-section">更改经统一编辑 API 写入 film.html。锁定轨道会拒绝修改；同轨片段不可重叠。</div>';
    var element=S.selectedElement&&S.selectedElement.clipId===clip.id?S.selectedElement:null;
    if(window.filmStudioReactInspector&&typeof window.filmStudioReactInspector.renderClip==='function'){
      window.filmStudioReactInspector.renderClip(Object.assign({},clip,{fps:Math.max(1,Math.min(60,num(S.data&&S.data.fps,30)))}));
      renderElementInspector(element);
      return;
    }
    $('fs-inspector').innerHTML=fields;
    var clipIsLocked=Object.prototype.hasOwnProperty.call(clip.attrs||{},'data-clip-locked');
    if(clipIsLocked){
      $('fs-inspector').querySelectorAll('input,select,button').forEach(function(control){control.disabled=true;});
      var lockMessage=document.createElement('div');lockMessage.className='fs-alert fs-visible fs-error';lockMessage.textContent='该片段来自已锁定图层，只读保留其原始时间线位置与画面。';
      $('fs-inspector').appendChild(lockMessage);
    }
    var clipLockButton=document.createElement('button');
    clipLockButton.type='button';clipLockButton.id='fs-toggle-clip-lock';clipLockButton.className='fs-btn';
    clipLockButton.textContent=clipIsLocked?'解锁片段':'锁定片段';
    clipLockButton.addEventListener('click',function(){void window.filmStudioSetClipLock(clip.id,!clipIsLocked);});
    $('fs-inspector').appendChild(clipLockButton);
    renderElementInspector(element);
    $('fs-inspector').querySelectorAll('[data-speed-preset]').forEach(function(button){
      button.addEventListener('click',function(e){
        e.preventDefault();e.stopPropagation();
        var input=$('fs-field-speed');if(!input)return;
        input.value=button.getAttribute('data-speed-preset');
        $('fs-inspector').querySelectorAll('[data-speed-preset]').forEach(function(other){other.classList.toggle('fs-speed-chip-active',other===button);});
        input.focus();
      });
    });
  }
  function render() {
    if(!S.data||!S.data.exists){renderMigration();return;}
    $('fs-project-label').textContent=S.projectId+' · rev '+S.rev;S.duration=Math.max(0.001,num(S.data.duration,10));
    var available=clips().map(function(c){return c.id;});
    if(S.selectedElement&&available.indexOf(S.selectedElement.clipId)<0){
      S.selectedElement=null;S.hoveredElement=null;
      var elementBox=$('fs-element-selection');if(elementBox)elementBox.hidden=true;
    }
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
    if(changed){
      S.data=null;S.rev='';S.runtimeRev='';S.runtimeCommand='';S.migration=null;
      S.selectedId='';S.selectedIds=[];S.selectionAnchor='';S.selectedElement=null;S.hoveredElement=null;
      var elementBox=$('fs-element-selection');if(elementBox)elementBox.hidden=true;
      S.proposal=null;S.previewProposalActive=false;S.broken=false;window.filmStudioPlaying=false;
    }
    S.projectId=nextId;
    var requestProjectId=nextId;
    var r=await apiGet('/api/editor/film?project_id='+encodeURIComponent(requestProjectId));
    if(requestProjectId!==S.projectId)return;
    if(r&&r.ok&&r.exists){
      if(S.rev&&r.rev!==S.rev){
        S.selectedElement=null;S.hoveredElement=null;
        var elementBox=$('fs-element-selection');if(elementBox)elementBox.hidden=true;
      }
      S.data=r;S.rev=r.rev;S.broken=false;S.migration=null;$('film-studio-panel').setAttribute('data-project-id',S.projectId);render();
    }
    else if(r&&r.ok&&!r.exists){S.data=null;S.rev=r.rev;S.runtimeRev='';window.filmStudioPlaying=false;S.migration=r;renderMigration();}
    else {if(r&&r.code==='INVALID_FILM')S.broken=true;S.data=null;S.rev='';showStatus((r&&r.error)||'读取影片文档失败；为防止覆盖，不写入文件。','error');}
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
    if(!S.projectId||!S.data||!S.data.exists)return false;
    if(S.busy){showStatus('上一项编辑仍在保存，本次操作未提交；请保存结束后重试。','error');return false;}
    if(S.broken){showStatus('film.html 无效，暂时不能写入；修复后重新载入。','error');return false;}
    if(S.proposal){S.proposal=null;S.previewProposalActive=false;renderProposalPanel();S.runtimeRev='';}
    S.busy=true;
    var studioPanel=$('film-studio-panel'),busyTimeline=$('fs-timeline');
    if(studioPanel)studioPanel.setAttribute('data-busy','true');
    if(busyTimeline)busyTimeline.style.pointerEvents='none';
    try{
      var r=await apiPost('/api/editor/film/edit',{project_id:S.projectId,base:S.rev,ops:ops});
      if(!r||!r.ok){
        var failureMessage=(r&&r.error)||'编辑提交失败。';
        if(S.data&&S.data.exists)render();
        showStatus(failureMessage,'error');
        return false;
      }
      S.data=r;S.rev=r.rev;
      if(r.rebased){
        S.selectedElement=null;S.hoveredElement=null;
        var elementBox=$('fs-element-selection');if(elementBox)elementBox.hidden=true;
      }else if(S.selectedElement)S.selectedElement.baseRevision=S.rev;
      var savedMessage=r.rebased?'操作已按片段 ID 重放到较新 revision；请检查结果。':'已保存 '+(r.ops||[]).length+' 项编辑。';
      render();
      var migrationWarnings=S.data.migration&&S.data.migration.warnings||[];
      showStatus(migrationWarnings.length?'迁移警告：'+migrationWarnings.join('；')+'；'+savedMessage:savedMessage,r.rebased?'':'success');
      return true;
    }catch(error){
      var editFailure='编辑提交失败：'+String(error&&error.message||error);
      if(S.data&&S.data.exists)render();
      showStatus(editFailure,'error');
      return false;
    }finally{
      S.busy=false;
      if(studioPanel)studioPanel.setAttribute('data-busy','false');
      if(busyTimeline)busyTimeline.style.pointerEvents='';
    }
  }
  async function filmStudioHistory(action) {
    if(!S.data||S.busy)return;
    if(S.broken){showStatus('影片文件无效，暂时不能撤销或重做。','error');return;}
    if(S.proposal){S.proposal=null;S.previewProposalActive=false;renderProposalPanel();S.runtimeRev='';}
    S.busy=true;
    try{
      var r=await apiPost('/api/editor/film/'+action,{project_id:S.projectId,base:S.rev});
      if(!r||!r.ok){showStatus((r&&r.error)||'历史操作失败。','error');return;}
      S.data=r;S.rev=r.rev;render();showStatus(action==='undo'?'已撤销。':'已重做。','success');
    }finally{S.busy=false;}
  }
  function onMove(e) {
    if(S.envelopeDrag){
      var ed=S.envelopeDrag,rect=ed.canvas.getBoundingClientRect(),point=ed.points[ed.index];
      if(!point||!rect.width||!rect.height)return;
      var x=Math.max(0,Math.min(rect.width,(e.clientX-rect.left)));
      var y=Math.max(0,Math.min(rect.height,(e.clientY-rect.top)));
      if(ed.index>0&&ed.index<ed.points.length-1){
        var lower=ed.points[ed.index-1].t+0.01,upper=ed.points[ed.index+1].t-0.01;
        point.t=Math.max(lower,Math.min(upper,x/rect.width*ed.duration));
      }
      point.v=Math.max(0,Math.min(1,1-(y-4)/Math.max(1,rect.height-8)));
      ed.active=true;ed.changed=true;drawVolumeEnvelope(ed.canvas,ed.points);e.preventDefault();return;
    }
    var d=S.drag;if(!d)return;
    var scroll=$('fs-timeline-scroll'),rect=scroll.getBoundingClientRect();
    if(e.clientX<rect.left+28)scroll.scrollLeft=Math.max(0,scroll.scrollLeft-18);
    else if(e.clientX>rect.right-28)scroll.scrollLeft=Math.min(scroll.scrollWidth,scroll.scrollLeft+18);
    var dx=e.clientX-d.startX+(scroll.scrollLeft-d.startScrollLeft);
    if(!d.active&&Math.sqrt(dx*dx+(e.clientY-d.startY)*(e.clientY-d.startY))<5)return;
    d.active=true;
    // Only plain clip moves can change tracks. Trim/roll/slip/slide gestures
    // must remain bound to their original track even if the pointer crosses rows.
    if(d.mode==='move'){
      var target=document.elementFromPoint(e.clientX,e.clientY),row=target&&target.closest('.fs-track');
      if(row){
        var nextTrack=row.getAttribute('data-track');
        if(nextTrack!==d.targetTrackId){clearDragTrackTarget(d);d.targetTrackId=nextTrack;d.targetRow=row;row.classList.add('fs-track-drop-target');}
      }
    }
    if(d.mode==='slide'){
      var deltaSlide=dx/S.pxPerSec;
      var slideMin=-Math.min(d.leftDuration,d.centerDuration)+0.04;
      var slideMax=Math.min(d.centerDuration,d.rightDuration)-0.04;
      deltaSlide=Math.max(slideMin,Math.min(slideMax,deltaSlide));
      d.slideElements[0].style.width=Math.max(3,(d.leftDuration+deltaSlide)*S.pxPerSec)+'px';
      d.slideElements[1].style.left=Math.max(0,(d.centerAt+deltaSlide)*S.pxPerSec)+'px';
      d.slideElements[2].style.left=Math.max(0,(d.rightAt+deltaSlide)*S.pxPerSec)+'px';
      d.slideElements[2].style.width=Math.max(3,(d.rightDuration-deltaSlide)*S.pxPerSec)+'px';
      d.element.setAttribute('data-slide-delta','滑移 '+(deltaSlide>=0?'+':'')+deltaSlide.toFixed(2)+'s');
      d.element.classList.add('fs-sliding');
    }else if(d.mode==='roll'){
      var deltaRoll=dx/S.pxPerSec;
      deltaRoll=Math.max(-d.leftDuration+0.04,Math.min(d.rightDuration-0.04,deltaRoll));
      d.rollElements[0].style.width=Math.max(3,(d.leftDuration+deltaRoll)*S.pxPerSec)+'px';
      d.rollElements[1].style.left=Math.max(0,(d.rightAt+deltaRoll)*S.pxPerSec)+'px';
      d.rollElements[1].style.width=Math.max(3,(d.rightDuration-deltaRoll)*S.pxPerSec)+'px';
      d.element.setAttribute('data-roll-delta','切口 '+(deltaRoll>=0?'+':'')+deltaRoll.toFixed(2)+'s');
      d.element.classList.add('fs-rolling');
    }else if(d.mode==='slip'){
      var slipClip=findClip(d.id),slipSpeed=slipClip?num(slipClip.attrs.speed,1):1;
      var sourceDelta=dx/S.pxPerSec*slipSpeed;
      d.element.setAttribute('data-slip-delta','源偏移 '+(sourceDelta>=0?'+':'')+sourceDelta.toFixed(2)+'s');
      d.element.classList.add('fs-slipping');
    }else if(d.mode==='trim-start'){
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
    if(S.envelopeDrag){
      var envelopeEdit=S.envelopeDrag;S.envelopeDrag=null;
      if(envelopeEdit.changed){
        var normalized=envelopeEdit.points.map(function(p){return {t:Number(p.t.toFixed(3)),v:Number(p.v.toFixed(3))};})
          .sort(function(a,b){return a.t-b.t;});
        // Coalesce points which became the same time after rounding; preserve the last gesture value.
        var unique=[];
        normalized.forEach(function(p){if(unique.length&&Math.abs(unique[unique.length-1].t-p.t)<0.001)unique[unique.length-1]=p;else unique.push(p);});
        if(unique.length>=2)await filmStudioCommit([{op:'set',clip:envelopeEdit.clipId,field:'volume_envelope',value:JSON.stringify(unique)}]);
      }else if(S.data&&S.data.exists)renderTimeline();
      return;
    }
    var d=S.drag;
    if(!d){S.skipNextClipClick=true;setTimeout(function(){S.skipNextClipClick=false;},0);return;}
    S.drag=null;S.skipNextClipClick=true;setTimeout(function(){S.skipNextClipClick=false;},0);
    clearDragTrackTarget(d);
    (d.members||[]).forEach(function(member){member.element.classList.remove('fs-dragging');});
    if(!d.active){syncClipSelectionVisual();return;}
    var deltaPx=e.clientX-d.startX+($('fs-timeline-scroll').scrollLeft-d.startScrollLeft);
    d.element.removeAttribute('data-slip-delta');d.element.classList.remove('fs-slipping');
    d.element.removeAttribute('data-roll-delta');d.element.classList.remove('fs-rolling');
    d.element.removeAttribute('data-slide-delta');d.element.classList.remove('fs-sliding');
    if(d.mode==='slide'){
      var slideDelta=deltaPx/S.pxPerSec;
      slideDelta=Math.max(-Math.min(d.leftDuration,d.centerDuration)+0.04,
        Math.min(Math.min(d.centerDuration,d.rightDuration)-0.04,slideDelta));
      if(Math.abs(slideDelta)<0.001){renderTimeline();return;}
      await filmStudioCommit([{op:'slide',clip:d.centerClipId,delta:Number(slideDelta.toFixed(3))}]);return;
    }
    if(d.mode==='roll'){
      var rollDelta=deltaPx/S.pxPerSec;
      rollDelta=Math.max(-d.leftDuration+0.04,Math.min(d.rightDuration-0.04,rollDelta));
      if(Math.abs(rollDelta)<0.001){renderTimeline();return;}
      await filmStudioCommit([{op:'roll',clip:d.leftClipId,other_clip:d.rightClipId,delta:Number(rollDelta.toFixed(3))}]);return;
    }
    if(d.mode==='slip'){
      var slipClip=findClip(d.id);
      if(!slipClip||slipClip.duration==null){renderTimeline();return;}
      var sourceDelta=deltaPx/S.pxPerSec*num(slipClip.attrs.speed,1);
      if(Math.abs(sourceDelta)<0.001){renderTimeline();return;}
      await filmStudioCommit([{op:'slip',clip:d.id,delta_source:Number(sourceDelta.toFixed(3))}]);return;
    }
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
  document.addEventListener('pointercancel',function(){
    if(S.envelopeDrag)S.envelopeDrag=null;
    if(S.drag){
      var drag=S.drag;S.drag=null;clearDragTrackTarget(drag);
      (drag.members||[]).forEach(function(member){
        member.element.classList.remove('fs-dragging','fs-slipping','fs-rolling','fs-sliding');
        member.element.removeAttribute('data-slip-delta');member.element.removeAttribute('data-roll-delta');member.element.removeAttribute('data-slide-delta');
      });
    }
    if(S.data&&S.data.exists)renderTimeline();
  });
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
      if(r&&r.ok&&r.exists&&r.rev!==S.rev){
        if(S.proposal){appendMessage('影片已被外部修改，旧 AI 提案已作废。','assistant');S.proposal=null;S.previewProposalActive=false;renderProposalPanel();}
        S.data=r;S.rev=r.rev;S.broken=false;S.runtimeRev='';render();showStatus('检测到外部文件修改，已按新 revision 刷新。');
      }
      else if(r&&!r.ok&&r.code==='INVALID_FILM'){S.broken=true;if(S.proposal){S.proposal=null;S.previewProposalActive=false;renderProposalPanel();}showStatus('film.html 当前无法解析。已暂停编辑；修复后重新载入。','error');}
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
  window.filmStudioToggleElementInspect=function(){
    if(S.busy){showStatus('当前影片正在保存或生成提案，请完成后再操作画布。','error');return;}
    if(!S.data||!S.data.exists){showStatus('请先初始化 film.html 后再选取画面元素。','error');return;}
    S.elementInspect=!S.elementInspect;
    var frame=$('fs-preview-composition'),button=$('fs-element-inspect-toggle');
    if(S.elementInspect&&window.filmStudioPlaying){
      window.filmStudioPlaying=false;syncRuntime('pause');
    }
    if(button){button.setAttribute('aria-pressed',String(S.elementInspect));button.textContent=S.elementInspect?'点选/拖动画面元素…':'选择画面元素';}
    if(S.elementInspect){
      // Inspect must operate on the currently loaded, visible preview. A nested
      // iframe can be present in the DOM while its outer monitor is still hidden.
      // Sync first (reveals the monitor and loads the exact current revision),
      // then send inspect; the runtime replays inspect again after its ready ack.
      // Inspection only works while an HTML clip is active on the timeline.
      // After reload the playhead may be before a moved/trimmed HTML clip; the
      // Runtime correctly hides inactive media. Align to the selected HTML clip,
      // or the first active/upcoming HTML clip, before enabling pointer hit-testing.
      var htmlClips=clips().filter(function(clip){
        return clip.tag==='iframe'&&clip.duration!=null&&Number.isFinite(Number(clip.at));
      });
      var targetClip=S.selectedId?findClip(S.selectedId):null;
      if(!targetClip||targetClip.tag!=='iframe')targetClip=null;
      if(!targetClip){
        targetClip=htmlClips.find(function(clip){
          return S.playhead>=num(clip.at,0)-0.001&&(clip.end==null||S.playhead<num(clip.end,0)-0.001);
        })||htmlClips[0]||null;
      }
      if(targetClip){
        var targetStart=Math.max(0,num(targetClip.at,0));
        var targetEnd=targetClip.end==null?targetStart+Math.max(0.1,num(targetClip.duration,1)):num(targetClip.end,targetStart+1);
        var epsilon=Math.min(0.01,Math.max(0.001,(targetEnd-targetStart)/2));
        if(S.playhead<targetStart-0.001||S.playhead>=targetEnd-0.001){
          S.playhead=Math.max(0,Math.min(targetEnd-epsilon,targetStart+epsilon));
          setPlayheadVisual();
        }
      }
      if(frame){
        frame.style.display='block';
        var monitorPlaceholder=$('fs-monitor-stage')&&$('fs-monitor-stage').querySelector('.fs-monitor-placeholder');
        if(monitorPlaceholder)monitorPlaceholder.style.display='none';
      }
      syncRuntime('seek');
      if(frame&&frame.contentWindow)frame.contentWindow.postMessage({type:'film-runtime:inspect',enabled:true},window.location.origin);
      window.filmStudioSetSideMode('inspector');
      showStatus('已开启画面元素选取：在预览中点击标题、文字或其他 HTML 图层。','success');
    }else{
      if(frame&&frame.contentWindow)frame.contentWindow.postMessage({type:'film-runtime:inspect',enabled:false},window.location.origin);
      showStatus('已退出画面元素选取。');
      renderElementSelection(S.selectedElement);
    }
  };
  window.filmStudioShowInspectorError=function(message){showStatus(String(message||''),'error');};
  window.filmStudioCommitCanvasElement=async function(operation){
    if(S.busy){showStatus('当前影片正在保存；请等待完成后再提交画布修改。','error');return;}
    var selected=S.selectedElement;
    if(!selected){showStatus('画布选择已经失效，请重新选取元素。','error');return;}
    if(selected.baseRevision!==S.rev){
      showStatus('影片 revision 已变化；为防止覆盖旧画布状态，请重新选择元素。','error');
      S.selectedElement=null;renderInspector();return;
    }
    if(!operation||operation.op!=='element_set'||operation.clip!==selected.clipId||operation.selector!==selected.selector){
      showStatus('画布编辑目标与当前选择不一致；本次没有写入。','error');return;
    }
    var previousRevision=S.rev;
    await filmStudioCommit([operation]);
    if(!S.data||!S.data.exists||S.rev===previousRevision)return;
    S.selectedElement=null;S.hoveredElement=null;
    var selectionBox=$('fs-element-selection');if(selectionBox)selectionBox.hidden=true;
    renderInspector();renderAssets();
    showStatus('画布修改已保存到 film.html。若需继续编辑或让 AI 修改该元素，请在当前画面中重新选择。','success');
  };
  window.filmStudioSaveSelectedElement=async function(){
    if(window.filmStudioReactInspector&&typeof window.filmStudioReactInspector.saveSelected==='function'){
      await window.filmStudioReactInspector.saveSelected();
      return;
    }
    if(S.busy){showStatus('当前影片正在保存；请等待完成后再提交画布修改。','error');return;}
    var selected=S.selectedElement;
    if(!selected){showStatus('请先在预览画布中选中一个 HTML 元素。','error');return;}
    var clip=findClip(selected.clipId);
    if(!clip){showStatus('所选元素所属片段已不存在，请重新选择。','error');return;}
    var textInput=$('fs-element-text'),leftInput=$('fs-element-left'),topInput=$('fs-element-top'),
        widthInput=$('fs-element-width'),heightInput=$('fs-element-height'),colorInput=$('fs-element-color'),
        fontInput=$('fs-element-font-size'),rotateInput=$('fs-element-rotate');
    var values=[leftInput,topInput,widthInput,heightInput,fontInput,rotateInput].filter(Boolean).map(function(el){return Number(el.value);});
    if(values.some(function(value){return !Number.isFinite(value);})
      ||!widthInput||!heightInput||Number(widthInput.value)<=0||Number(heightInput.value)<=0
      ||Number(fontInput.value)<=0||Number(widthInput.value)>10000||Number(heightInput.value)>10000
      ||Number(fontInput.value)>1000||Math.abs(Number(rotateInput.value))>3600){
      showStatus('画布坐标、尺寸、字号或旋转角度超出有效范围。','error');return;
    }
    var elementStyle={
      position:'fixed',
      left:Number(leftInput.value).toFixed(2)+'px',
      top:Number(topInput.value).toFixed(2)+'px',
      width:Number(widthInput.value).toFixed(2)+'px',
      height:Number(heightInput.value).toFixed(2)+'px',
      color:colorInput.value,
      fontSize:Number(fontInput.value).toFixed(2)+'px',
      rotate:Number(rotateInput.value).toFixed(2)+'deg'
    };
    var elementOp={op:'element_set',clip:clip.id,selector:selected.selector,style:elementStyle};
    if(textInput&&!textInput.disabled&&textInput.value!==null)elementOp.text=textInput.value;
    await filmStudioCommit([elementOp]);
    // The commit triggers a full, revision-bound reload. Keep selection context
    // visible, but the user can reselect when CSS/structure changes the selector.
    if(S.data&&S.data.exists){
      // The runtime and the parent selection geometry were just rebuilt against
      // the new revision. Drop the old geometry/context instead of showing stale
      // text or letting a subsequent AI turn target an obsolete element snapshot.
      S.selectedElement=null;S.hoveredElement=null;
      var selectionBox=$('fs-element-selection');if(selectionBox)selectionBox.hidden=true;
      renderInspector();renderAssets();
      showStatus('画布修改已保存到 film.html。若需继续编辑或让 AI 修改该元素，请在当前画面中重新选择。','success');
    }
  };
  window.addEventListener('message',function(event){
    var frame=$('fs-preview-composition');
    if(!frame||event.source!==frame.contentWindow||event.origin!==window.location.origin)return;
    var message=event.data||{};
    if(message.type==='film-runtime:ready'){
      var expectedRuntimeRev=S.previewProposalActive&&S.proposal?'proposal:'+S.proposal.id:S.rev;
      var hasRuntimeData=!!(S.previewProposalActive&&S.proposal?S.proposal.candidate:S.data);
      // The runtime's first ready message has no revision. It can race the
      // iframe load event (or arrive before a project is selected), so use it
      // as a real handshake and replay the current document after that event.
      if(!message.rev){
        S.runtimeReady=true;
        try{S.runtimeDocument=frame.contentDocument||S.runtimeDocument;}catch(_){}
        if(hasRuntimeData){
          S.runtimeRev='';
          syncRuntime(S.runtimeCommand||(window.filmStudioPlaying?'play':'seek'));
        }
        return;
      }
      if(message.rev&&hasRuntimeData&&message.rev!==expectedRuntimeRev){
        S.runtimeRev='';
        syncRuntime(S.runtimeCommand||(window.filmStudioPlaying?'play':'seek'));
      }else if(message.rev&&hasRuntimeData&&message.rev===expectedRuntimeRev){
        S.runtimeRev=expectedRuntimeRev;
        if(S.elementInspect)frame.contentWindow.postMessage({type:'film-runtime:inspect',enabled:true},window.location.origin);
      }
    }else if(message.type==='film-runtime:inspect-ready'){
      // A late ready acknowledgement must not overwrite the more specific
      // "element selected" state after a pointer event has already arrived.
      if(S.elementInspect&&message.enabled&&!S.selectedElement)showStatus('预览中的 HTML 镜头已进入元素选取模式。','success');
      else if(S.elementInspect&&message.enabled===false)showStatus('预览中的 HTML 镜头无法进入元素选取模式：'+String(message.error||'绘制队列未能完成'),'error');
    }else if(['film-runtime:element-selected','film-runtime:element-hover',
                'film-runtime:element-preview','film-runtime:element-drag-end'].indexOf(message.type)>=0){
      var element={
        clipId:String(message.clipId||''),selector:String(message.selector||''),tag:String(message.tag||''),
        text:String(message.text||''),textEditable:message.textEditable!==false,
        styles:message.styles||{},rect:message.rect||{},localRect:message.localRect||{},
        baseRevision:S.rev
      };
      if(message.type==='film-runtime:element-selected'){
        if(!findClip(element.clipId)){showStatus('所选画面元素对应的片段已不存在。','error');return;}
        S.selectedElement=element;S.selectedId=element.clipId;S.selectedIds=[element.clipId];S.selectionAnchor=element.clipId;
        window.filmStudioSetSideMode('inspector');
        syncClipSelectionVisual();renderInspector();renderAssets();renderElementSelection(element);
        showStatus('已选中 '+element.tag+' 元素：'+element.selector+'；拖动元素可移动，拖动边缘可缩放。','success');
      }else if(message.type==='film-runtime:element-hover'){
        S.hoveredElement=element;
        if(S.elementInspect)renderElementSelection(S.selectedElement||element);
      }else if(message.type==='film-runtime:element-preview'){
        if(S.selectedElement&&S.selectedElement.clipId===element.clipId&&S.selectedElement.selector===element.selector){
          S.selectedElement.rect=element.rect;S.selectedElement.localRect=element.localRect;
          S.selectedElement.styles=element.styles;S.selectedElement.baseRevision=S.rev;
          renderElementSelection(S.selectedElement);syncElementInspectorFields(S.selectedElement);
        }
      }else if(message.type==='film-runtime:element-drag-end'){
        if(S.selectedElement&&S.selectedElement.clipId===element.clipId&&S.selectedElement.selector===element.selector){
          S.selectedElement.rect=element.rect;S.selectedElement.localRect=element.localRect;
          S.selectedElement.styles=element.styles;S.selectedElement.text=element.text;
          S.selectedElement.baseRevision=S.rev;
        }else{
          S.selectedElement=element;S.selectedId=element.clipId;S.selectedIds=[element.clipId];
        }
        renderElementSelection(S.selectedElement);syncElementInspectorFields(S.selectedElement);
        window.filmStudioSaveSelectedElement();
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
  window.filmStudioSetClipLock=async function(clipId,locked){
    var clip=findClip(clipId||S.selectedId);
    if(!clip){showStatus('请先选择片段。','error');return;}
    if(typeof locked!=='boolean'){showStatus('锁定状态必须是明确的布尔值。','error');return;}
    await filmStudioCommit([{op:'clip_lock',clip:clip.id,locked:locked}]);
  };
  window.filmStudioCommitInspectorOperations=async function(ops){
    if(!Array.isArray(ops)||!ops.length)return false;
    if(!S.projectId||!S.data||!S.data.exists)throw new Error('当前没有可编辑的 film.html。');
    if(S.busy)throw new Error('上一项编辑仍在保存；本次修改未提交，请重试。');
    if(S.broken)throw new Error('film.html 无效，当前不能写入。');
    var committed=await filmStudioCommit(ops);
    if(!committed){
      var status=$('film-studio-alert');
      throw new Error(status&&status.textContent||'片段属性没有保存，请检查影片状态后重试。');
    }
    return true;
  };
  window.filmStudioSplit=async function(){var clip=findClip(S.selectedId);if(!clip){showStatus('请先选择需要切分的片段。','error');return;}await filmStudioCommit([{op:'split',clip:clip.id,at:S.playhead}]);};
  window.filmStudioRemove=async function(){var ids=(S.selectedIds||[]).filter(function(id){return !!findClip(id);});if(!ids.length&&S.selectedId)ids=[S.selectedId];if(!ids.length){showStatus('请先选择片段。','error');return;}if(!window.confirm('删除选中的 '+ids.length+' 个片段？此操作可撤销。'))return;await filmStudioCommit(ids.map(function(id){return {op:'remove',clip:id};}));S.selectedId='';S.selectedIds=[];S.selectionAnchor='';render();};
  window.filmStudioJoinThroughEdit=async function(){
    var clip=findClip(S.selectedId);
    if(!clip){showStatus('请先选择切分后的左段或右段。','error');return;}
    var track=(S.data.tracks||[]).find(function(t){return t.id===clip.track_id;});
    if(!track){showStatus('无法定位所选片段的轨道。','error');return;}
    var ordered=(track.clips||[]).slice().sort(function(a,b){return a.at-b.at;});
    var index=ordered.findIndex(function(candidate){return candidate.id===clip.id;});
    if(index<0){showStatus('所选片段已变化，请重新选择。','error');return;}
    var current=ordered[index],left=null,right=null;
    if(index+1<ordered.length&&current.end!=null&&Math.abs(current.end-ordered[index+1].at)<=0.001){
      left=current;right=ordered[index+1];
    }else if(index>0&&ordered[index-1].end!=null&&Math.abs(ordered[index-1].end-current.at)<=0.001){
      left=ordered[index-1];right=current;
    }else{
      showStatus('所选片段附近没有时间上相邻的切口。','error');return;
    }
    await filmStudioCommit([{op:'join',clip:left.id,other_clip:right.id}]);
  };
  window.filmStudioRippleDelete=async function(){
    var clip=findClip(S.selectedId);
    if(!clip){showStatus('请选择一个有明确时长的片段作为波纹删除范围。','error');return;}
    if(clip.end==null||clip.end<=clip.at){showStatus('目标片段时长未知，不能执行波纹删除。','error');return;}
    if(!window.confirm('将删除 '+fmtTime(clip.at)+'–'+fmtTime(clip.end)+'，并收拢全部轨道后续内容。跨越该时间段的片段会被切分；锁定轨道或未知时长会导致整笔拒绝。继续？'))return;
    await filmStudioCommit([{op:'ripple_delete',clip:clip.id,scope:'all'}]);
  };
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
      var started=await apiPost('/api/editor/film/render',{project_id:pid,expected_revision:revision});
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
            if(resultText)resultText.textContent=(out.width||'—')+'×'+(out.height||'—')+' · '+(Number(out.duration||0).toFixed(2))+' 秒 · revision '+(out.rendered_revision||revision)+' · QC '+(out.qc_status||'UNKNOWN');
            var qcLink=$('fs-qc-download'),qcPath=out.qc_report_path||'';
            if(qcLink){
              qcLink.hidden=!qcPath;
              qcLink.href=qcPath?'/file?path='+encodeURIComponent(qcPath):'#';
              qcLink.textContent=out.qc_status==='PASS'?'打开 QC 记录（PASS）':'打开 QC 记录';
            }
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
  function renderProposalPanel(){
    if(renderAiDirector())return;
    var panel=$('fs-proposal-panel'),proposal=S.proposal;
    if(!panel)return;
    panel.hidden=!proposal;
    if(!proposal)return;
    var summary=$('fs-proposal-summary'),detail=$('fs-proposal-detail');
    if(summary)summary.textContent=proposal.summary||'AI 候选修改';
    if(detail)detail.textContent=(proposal.operations||[]).length+' 项操作 · 基于 revision '+proposal.base_revision+' · 尚未写入正式文档';
  }
  window.filmStudioPreviewProposal=function(){
    var proposal=S.proposal,frame=$('fs-preview-composition');
    if(!proposal||!proposal.candidate||!frame||!frame.contentWindow){showStatus('没有可预览的 AI 提案。','error');return;}
    window.filmStudioSetSideMode('ai');
    S.previewProposalActive=true;S.runtimeRev='';
    syncRuntime('seek-pause');
    showStatus('正在预览 AI 候选版本；正式 film.html 尚未更改。');
  };
  window.filmStudioRejectProposal=function(){
    if(!S.proposal)return;
    appendMessage('已拒绝本次 AI 提案。正式影片文档与 revision 未改变。','assistant');
    S.proposal=null;S.previewProposalActive=false;renderProposalPanel();S.runtimeRev='';
    syncRuntime('seek-pause');
    showStatus('已拒绝提案；未修改正式影片。','success');
  };
  window.filmStudioApplyProposal=async function(){
    var proposal=S.proposal;if(!proposal||S.busy)return;
    if(S.broken){showStatus('影片文件无效，不能应用 AI 提案。','error');return;}
    if(proposal.project_id!==S.projectId){showStatus('提案所属项目已变化，不能应用。','error');return;}
    S.busy=true;
    try{
      var result=await apiPost('/api/editor/film/apply-proposal',{
        project_id:S.projectId,proposal_id:proposal.id,base_revision:proposal.base_revision
      });
      if(!result||!result.ok){
        appendMessage((result&&result.error)||'提案应用失败。','assistant');
        showStatus((result&&result.error)||'提案应用失败。','error');
        if(result&&result.code==='REVISION_CONFLICT'){
          S.proposal=null;S.previewProposalActive=false;renderProposalPanel();S.runtimeRev='';
          S.busy=false;await filmStudioLoad(true);
        }
        return;
      }
      S.proposal=null;S.previewProposalActive=false;renderProposalPanel();S.data=result;S.rev=result.rev;S.runtimeRev='';
      appendMessage('已确认并应用提案，提交 '+(result.ops||[]).length+' 项操作。','assistant');
      render();showStatus('AI 提案已应用并写入 revision '+S.rev+'。','success');
    }finally{S.busy=false;}
  };
  window.filmStudioAsk=async function(){
    var input=$('fs-chat-input')||$('fs-static-chat-input');if(!input)return;
    var message=(input.value||'').trim();if(!message||S.busy)return;
    if(!S.data||!S.data.exists){showStatus('请先初始化影片文档。','error');return;}
    if(S.broken){showStatus('影片文件无效，已暂停 AI 编辑；修复后重新载入。','error');return;}
    if(S.proposal){window.filmStudioRejectProposal();}
    appendMessage(message,'user');input.value='';S.busy=true;
    try{
      var request={project_id:S.projectId,base:S.rev,message:message,selected_clip_id:S.selectedId};
      if(S.selectedElement&&S.selectedElement.clipId===S.selectedId){
        if(S.selectedElement.baseRevision!==S.rev){
          S.busy=false;
          appendMessage('画布元素上下文已过期，请在当前预览中重新选中该元素后再指挥修改。','assistant');
          showStatus('画布元素上下文已过期，请重新选择元素。','error');
          return;
        }
        request.selected_element={
          base_revision:S.rev,clip_id:S.selectedElement.clipId,selector:S.selectedElement.selector,
          tag:S.selectedElement.tag,text:S.selectedElement.text,textEditable:S.selectedElement.textEditable,
          styles:S.selectedElement.styles,localRect:S.selectedElement.localRect
        };
      }
      var r=await apiPost('/api/editor/film/command',request);
      if(!r||!r.ok){appendMessage((r&&r.error)||'AI 提案生成失败。','assistant');showStatus((r&&r.error)||'AI 提案生成失败。','error');return;}
      if(r.no_op){appendMessage(r.message||'AI 没有生成可安全应用的操作，请补充目标。','assistant');showStatus(r.message||'没有产生候选操作。');return;}
      if(!r.proposal){appendMessage('服务器没有返回结构化提案，未修改正式影片。','assistant');showStatus('AI 响应缺少提案对象。','error');return;}
      S.proposal=r.proposal;renderProposalPanel();
      appendMessage((r.proposal.summary||'AI 已生成候选编辑。')+'（'+r.proposal.operations.length+' 项，尚未应用）','assistant');
      window.filmStudioPreviewProposal();
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
    }else if(!e.ctrlKey&&!e.metaKey&&!e.altKey&&!e.shiftKey&&!e.repeat&&String(e.key).toLowerCase()==='s'){
      e.preventDefault();window.filmStudioSplit();
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
  function connectFilmStudioEvents(){
    if(S.events||typeof EventSource==='undefined')return;
    try{
      S.events=new EventSource('/api/events');
      S.events.onmessage=function(event){
        var packet;try{packet=JSON.parse(event.data||'{}');}catch(_){return;}
        var data=packet.data||{};
        if(data.project_id&&S.projectId&&data.project_id!==S.projectId)return;
        if(packet.event==='film'&&data.phase==='document_broken'&&S.open){
          S.broken=true;
          if(S.proposal){S.proposal=null;S.previewProposalActive=false;renderProposalPanel();}
          showStatus('影片文件校验失败，已暂停编辑：'+String(data.error||'文档解析失败'),'error');
        }else if(packet.event==='film'&&data.phase==='revision_changed'&&data.source==='external_file'&&S.open){
          S.broken=false;
          if(S.proposal){S.proposal=null;S.previewProposalActive=false;renderProposalPanel();}
          if(data.revision&&data.revision!==S.rev&&!S.busy&&!S.drag){S.runtimeRev='';filmStudioLoad(true);}
        }else if(packet.event==='film'&&data.phase==='revision_changed'&&S.open&&data.revision&&data.revision!==S.rev&&!S.busy&&!S.drag){
          if(S.proposal){appendMessage('影片 revision 已变化，待确认提案已作废。','assistant');S.proposal=null;S.previewProposalActive=false;renderProposalPanel();}
          filmStudioLoad(true);
        }else if(packet.event==='film_ai'&&S.open&&data.project_id===S.projectId&&data.phase==='planning'){
          showStatus('AI 正在分析影片与编辑约束……');
        }else if(packet.event==='film_ai'&&S.open&&data.project_id===S.projectId&&data.phase==='proposed'){
          showStatus('AI 提案已通过服务端编辑规则验证，等待预览与确认。');
        }
      };
      S.events.onerror=function(){/* EventSource reconnects automatically; document polling remains a fallback. */};
    }catch(_){S.events=null;}
  }
  connectFilmStudioEvents();
  var dock=$('fs-workspace-dock')||document.querySelector('.fs-workspace');
  var studioPanel=$('film-studio-panel');
  function restoreDock(){
    try{
      var left=localStorage.getItem('film-studio-left-pane'),right=localStorage.getItem('film-studio-right-pane');
      if(left&&Number.isFinite(Number(left)))dock.style.setProperty('--fs-left',Math.max(15,Math.min(38,Number(left)))+'%');
      if(right&&Number.isFinite(Number(right)))dock.style.setProperty('--fs-right',Math.max(18,Math.min(38,Number(right)))+'%');
      var timelineHeight=Number(localStorage.getItem('film-studio-timeline-height'));
      if(studioPanel&&Number.isFinite(timelineHeight)&&timelineHeight>=160){
        studioPanel.style.setProperty('--fs-timeline-height',Math.max(160,Math.min(window.innerHeight*0.45,timelineHeight))+'px');
      }
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
  function beginTimelineDivider(e){
    if(e.button!==0||!studioPanel)return;
    var timeline=$('fs-timeline-divider'),pane=document.querySelector('.fs-timeline-pane');
    if(!timeline||!pane)return;
    S.paneDrag={
      side:'timeline',divider:timeline,startY:e.clientY,
      startHeight:pane.getBoundingClientRect().height,
      panelHeight:studioPanel.getBoundingClientRect().height
    };
    timeline.classList.add('fs-divider-dragging');
    try{timeline.setPointerCapture(e.pointerId);}catch(_){}
    e.preventDefault();
  }
  function moveDivider(e){
    if(!S.paneDrag)return;
    var d=S.paneDrag;
    if(d.side==='timeline'){
      var panelHeight=studioPanel?studioPanel.getBoundingClientRect().height:d.panelHeight;
      var maxHeight=Math.max(160,Math.min(window.innerHeight*0.45,panelHeight*0.45));
      var height=Math.max(160,Math.min(maxHeight,d.startHeight+d.startY-e.clientY));
      if(studioPanel)studioPanel.style.setProperty('--fs-timeline-height',height.toFixed(0)+'px');
      try{localStorage.setItem('film-studio-timeline-height',height.toFixed(0));}catch(_){}
      return;
    }
    var rect=d.rect,w=rect.width;
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
    if(!S.paneDrag)return;
    if(S.paneDrag.divider)S.paneDrag.divider.classList.remove('fs-divider-dragging');
    S.paneDrag=null;
  }
  document.querySelectorAll('.fs-pane-divider').forEach(function(el){
    el.addEventListener('pointerdown',beginDivider);
    el.addEventListener('pointermove',moveDivider);
    el.addEventListener('pointerup',endDivider);
    el.addEventListener('pointercancel',endDivider);
    el.addEventListener('lostpointercapture',endDivider);
    el.addEventListener('keydown',function(e){
      if(e.key!=='ArrowLeft'&&e.key!=='ArrowRight')return;
      e.preventDefault();
      var side=el.getAttribute('data-divider'),delta=(e.key==='ArrowLeft'?-1:1)*(e.shiftKey?4:1);
      var current=parseFloat(getComputedStyle(dock).getPropertyValue(side==='left'?'--fs-left':'--fs-right'))||24;
      var next=Math.max(side==='left'?15:18,Math.min(38,current+(side==='left'?delta:-delta)));
      dock.style.setProperty(side==='left'?'--fs-left':'--fs-right',next+'%');
      try{localStorage.setItem(side==='left'?'film-studio-left-pane':'film-studio-right-pane',String(next));}catch(_){}
    });
  });
  var timelineDivider=$('fs-timeline-divider');
  if(timelineDivider){
    timelineDivider.addEventListener('pointerdown',beginTimelineDivider);
    timelineDivider.addEventListener('pointermove',moveDivider);
    timelineDivider.addEventListener('pointerup',endDivider);
    timelineDivider.addEventListener('pointercancel',endDivider);
    timelineDivider.addEventListener('lostpointercapture',endDivider);
    timelineDivider.addEventListener('keydown',function(e){
      if(e.key!=='ArrowUp'&&e.key!=='ArrowDown')return;
      e.preventDefault();
      var pane=document.querySelector('.fs-timeline-pane');
      if(!pane||!studioPanel)return;
      var current=pane.getBoundingClientRect().height;
      var delta=(e.key==='ArrowUp'?1:-1)*(e.shiftKey?48:16);
      var maxHeight=Math.max(160,Math.min(window.innerHeight*0.45,studioPanel.getBoundingClientRect().height*0.45));
      var height=Math.max(160,Math.min(maxHeight,current+delta));
      studioPanel.style.setProperty('--fs-timeline-height',height.toFixed(0)+'px');
      try{localStorage.setItem('film-studio-timeline-height',height.toFixed(0));}catch(_){}
    });
  }
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
    'canvas-inspect': () => window.filmStudioToggleElementInspect(),
    split: () => window.filmStudioSplit(),
    'join-cut': () => window.filmStudioJoinThroughEdit(),
    remove: () => window.filmStudioRemove(),
    'ripple-delete': () => window.filmStudioRippleDelete(),
    'envelope-mode': () => window.filmStudioToggleEnvelopeMode(),
    'slide-mode': () => window.filmStudioToggleSlideMode(),
    'insert-mode': () => window.filmStudioToggleInsertMode(),
    'add-track': () => window.filmStudioAddTrack(),
    'remove-empty-track': () => window.filmStudioRemoveEmptyTrack(),
    'zoom-out': () => window.filmStudioZoom(-1),
    'zoom-in': () => window.filmStudioZoom(1),
    'apply-inspector': () => window.filmStudioSaveInspector(),
    'show-inspector': () => window.filmStudioSetSideMode('inspector'),
    'show-ai': () => window.filmStudioSetSideMode('ai'),
    render: () => window.filmStudioRender(),
    ask: () => window.filmStudioAsk(),
    'preview-proposal': () => window.filmStudioPreviewProposal(),
    'apply-proposal': () => window.filmStudioApplyProposal(),
    'reject-proposal': () => window.filmStudioRejectProposal(),
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

  function mountReactToolbar(){
    var reactMount=$('fs-react-toolbar'),legacy=$('fs-static-toolbar'),
        reactTimeline=$('fs-react-timeline-toolbar'),legacyTimeline=$('fs-static-timeline-toolbar'),
        reactScrub=$('fs-react-scrub-row'),legacyScrub=$('fs-static-scrub-row');
    var bridge=window.filmStudioReactToolbar;
    if(bridge&&typeof bridge.render==='function'&&typeof bridge.renderTimeline==='function'
        &&typeof bridge.renderScrub==='function'&&reactMount&&reactTimeline&&reactScrub){
      try{
        bridge.render();
        bridge.renderTimeline();
        bridge.renderScrub();
        reactMount.style.display='flex';
        reactTimeline.style.display='flex';
        reactScrub.style.display='flex';
        if(legacy)legacy.style.display='none';
        if(legacyTimeline)legacyTimeline.style.display='none';
        // Remove the legacy row once React has rendered successfully: both
        // versions contain stable IDs consumed by the playback runtime.
        if(legacyScrub)legacyScrub.remove();
        return;
      }catch(error){
        console.error('React Studio toolbar mount failed; using static fallback.',error);
      }
    }
    if(reactMount)reactMount.style.display='none';
    if(reactTimeline)reactTimeline.style.display='none';
    if(reactScrub)reactScrub.style.display='none';
    if(legacy)legacy.style.display='';
    if(legacyTimeline)legacyTimeline.style.display='';
    if(legacyScrub)legacyScrub.style.display='';
  }
  mountReactToolbar();
  renderAiDirector();

})();
