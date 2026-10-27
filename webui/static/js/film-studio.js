/* File-backed Studio UI. Each edit commits by stable clip ID and authoritative film revision. */
(function () {
  'use strict';
  var S = {open:false, projectId:'', rev:'', runtimeRev:'', runtimeReady:false, runtimeDocument:null, runtimeCommand:'', seekGeneration:0, data:null, migration:null, selectedId:'', selectedIds:[], selectionAnchor:'', skipNextClipClick:false, proposal:null, previewProposalActive:false, events:null, broken:false, waveformPending:Object.create(null), envelopeMode:false, slideMode:false, slipMode:false, playhead:0, duration:10, pxPerSec:36, busy:false, pendingTransaction:null, rendering:false, drag:null, envelopeDrag:null, pendingExternalRevision:'', loadGeneration:0, poll:null, messages:[], aiController:null, aiRequestId:'', aiActiveMessage:'', aiPendingSteer:'', aiStreamText:'', aiLiveMessageIndex:-1, snapEnabled:true, assetMode:'clips', libraryAssets:[], directorNodes:[], insertMode:false, overwriteMode:false, clipClipboard:null, elementInspect:false, selectedElement:null};
  var gestureDomRefs = null;
  var envelopeDomCanvas = null;
  var pendingRevisionSource = '';
  if(window.filmStudioStore&&typeof window.filmStudioStore.bindLegacyState==='function')window.filmStudioStore.bindLegacyState(S);
  function bindPlaybackCompatibilityAlias(){
    var store=window.filmStudioStore;
    if(!store||typeof store.getField!=='function'||typeof store.setField!=='function')return;
    var initial=typeof window.filmStudioPlaying==='boolean'?window.filmStudioPlaying:store.getField('playing')===true;
    store.setField('playing',initial);
    try{
      Object.defineProperty(window,'filmStudioPlaying',{
        configurable:true,
        enumerable:true,
        get:function(){return store.getField('playing')===true;},
        set:function(value){store.setField('playing',value===true);}
      });
    }catch(error){
      window.filmStudioPlaying=initial;
    }
  }
  bindPlaybackCompatibilityAlias();
  var $ = function (id) { return document.getElementById(id); };
  function dispatchStudioTransaction(kind,baseRevision,operations,submitter){
    var list=Array.isArray(operations)?operations.map(function(operation){
      return operation&&typeof operation==='object'?Object.assign({},operation):operation;
    }):[];
    var context={
      kind:String(kind||'film-edit'),
      baseRevision:String(baseRevision||''),
      operations:list,
      operationCount:list.length
    };
    var store=window.filmStudioStore;
    if(store&&typeof store.dispatchTransaction==='function'){
      return store.dispatchTransaction(context,submitter);
    }
    if(S.busy)return Promise.reject(new Error('FILM_TRANSACTION_BUSY'));
    S.pendingTransaction=context;S.busy=true;
    return Promise.resolve().then(submitter).finally(function(){
      S.pendingTransaction=null;S.busy=false;
    });
  }
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]); }); }
  function num(v, fallback) { var n = Number(v); return Number.isFinite(n) ? n : fallback; }
  function syncPlaybackButtonText(){
    var bridge=window.filmStudioReactToolbar;
    if(bridge&&typeof bridge.renderScrub==='function')return;
    var button=$('fs-play-toggle');
    if(button)button.textContent=window.filmStudioPlaying?'暂停':'播放';
  }
  function setPlaybackState(playing){
    window.filmStudioPlaying=!!playing;
    S.playing=!!playing;
    syncPlaybackButtonText();
  }
  function timelineMath(){return window.filmStudioTimelineMath||null;}
  function setTimelineGesturePreview(preview){
    var bridge=window.filmStudioReactTimeline;
    if(!bridge||typeof bridge.setGesturePreview!=='function')return false;
    bridge.setGesturePreview(preview);
    return true;
  }
  function clearTimelineGesturePreview(){setTimelineGesturePreview(null);}
  function setTimelineEnvelopePreview(clipId,points){
    var bridge=window.filmStudioReactTimeline;
    if(!bridge||typeof bridge.setEnvelopePreview!=='function')return false;
    if(!clipId||!Array.isArray(points))bridge.setEnvelopePreview(null);
    else bridge.setEnvelopePreview({clipId:String(clipId),points:points.map(function(point){return {t:Number(point.t),v:Number(point.v)};})});
    return true;
  }
  function clearTimelineEnvelopePreview(){return setTimelineEnvelopePreview('',null);}
  function setTimelineActiveDragIds(ids){
    var bridge=window.filmStudioReactTimeline;
    if(!bridge||typeof bridge.setActiveDragIds!=='function')return false;
    bridge.setActiveDragIds(ids&&ids.length?ids.slice():null);
    return true;
  }
  function updateTimelineGestureContext(patch){
    var current=S.drag;
    if(!current)return null;
    S.drag=Object.assign({},current,patch||{});
    return S.drag;
  }
  function updateTimelineEnvelopeGesture(patch){
    var current=S.envelopeDrag;
    if(!current)return null;
    S.envelopeDrag=Object.assign({},current,patch||{});
    return S.envelopeDrag;
  }

  function timelineSecondsToPixels(seconds,scale){var m=timelineMath();return m?m.secondsToPixels(seconds,scale):num(seconds,0)*Math.max(0.000001,num(scale,1));}
  function timelinePixelsToSeconds(pixels,scale){var m=timelineMath();return m?m.pixelsToSeconds(pixels,scale):num(pixels,0)/Math.max(0.000001,num(scale,1));}
  function timelineDragDeltaSeconds(pointerDelta,scrollDelta,scale){var m=timelineMath();return m&&typeof m.dragDeltaSeconds==='function'?m.dragDeltaSeconds(pointerDelta,scrollDelta,scale):timelinePixelsToSeconds(num(pointerDelta,0)+num(scrollDelta,0),scale);}
  function timelineClipLeft(at,scale){var m=timelineMath();return m?m.clipLeft(at,scale):Math.max(0,timelineSecondsToPixels(at,scale));}
  function timelineClipWidth(duration,scale,minWidth,unknownDuration,maxWidth,roundUp){var m=timelineMath();if(m)return m.clipWidth(duration,scale,minWidth,unknownDuration,maxWidth,roundUp);var width=timelineSecondsToPixels(duration==null?unknownDuration:Math.max(0,num(duration,0)),scale);if(roundUp)width=Math.ceil(width);if(maxWidth>0)width=Math.min(maxWidth,width);return Math.max(minWidth,width);}
  function timelineZoomScale(current,direction){var m=timelineMath();return m?m.zoomScale(current,direction,12,120):Math.max(12,Math.min(120,num(current,36)*(direction>0?1.25:0.8)));}
  function timelineZoomAnchorTime(scrollLeft,localX,scale,headerWidth){var m=timelineMath();return m?m.zoomAnchorTime(scrollLeft,localX,scale,headerWidth):Math.max(0,(scrollLeft+localX-headerWidth)/Math.max(0.000001,scale));}
  function timelineZoomScrollLeft(anchorTime,localX,scale,headerWidth){var m=timelineMath();return m?m.zoomScrollLeft(anchorTime,localX,scale,headerWidth):Math.max(0,headerWidth+anchorTime*scale-localX);}
  function timelineSnapToFrame(seconds,fps){var m=timelineMath();var safeFps=Math.max(1,Math.min(120,num(fps,30)));return m?m.snapToFrame(seconds,safeFps):Math.round(seconds*safeFps)/safeFps;}
  function fmtTime(v) { var n = Math.max(0, Number(v) || 0), m = Math.floor(n / 60), s = n - m * 60; return String(m).padStart(2,'0') + ':' + s.toFixed(3).padStart(6,'0'); }
  function clips() { return (S.data && S.data.tracks || []).reduce(function (a,t) { return a.concat((t.clips || []).map(function (c) { c.track_id = t.id; c.trackLocked = Object.prototype.hasOwnProperty.call(t.attrs || {}, 'locked'); return c; })); }, []); }
  function findClip(id) { return clips().find(function (c) { return c.id === id; }) || null; }
  function syncDirectorSelection(){
    if(!S.projectId||!S.data||!S.data.exists)return;
    var ids=(S.selectedIds||[]).filter(function(id,index,all){return !!findClip(id)&&all.indexOf(id)===index;});
    var selected=findClip(S.selectedId)||findClip(ids[0]);
    var selection=selected
      ?{type:'clip',id:selected.id,ids:ids,track_id:selected.track_id,layer_type:selected.tag}
      :{type:'project',ids:[]};
    try{
      var request=apiPost('/api/director/context',{project_id:S.projectId,selection:selection});
      if(request&&typeof request.catch==='function')request.catch(function(){});
    }catch(_){}
  }
  function timelineKeyframeMarks(clip){
    var attrs=clip&&clip.attrs||{},duration=Number(clip&&clip.duration),raw=attrs['data-film-keyframes'];
    if(clip.tag==='audio'||typeof raw!=='string'||!raw.trim()||!Number.isFinite(duration)||duration<=0)return [];
    var channels;try{channels=JSON.parse(raw);}catch(_){return [];}
    if(!Array.isArray(channels))return [];
    var grouped=Object.create(null);
    channels.forEach(function(channel){
      if(!channel||!Array.isArray(channel.keyframes))return;
      var property=String(channel.property||'');
      channel.keyframes.forEach(function(point){
        var at=Number(point&&point.at);
        if(!Number.isFinite(at)||at<0||at>duration+0.001)return;
        var key=String(Math.max(0,Math.min(duration,Number(at.toFixed(3)))));
        if(!grouped[key])grouped[key]=[];
        if(property&&grouped[key].indexOf(property)<0)grouped[key].push(property);
      });
    });
    return Object.keys(grouped).map(function(key){return {at:Number(key),properties:grouped[key]};})
      .sort(function(a,b){return a.at-b.at;}).slice(0,500);
  }
  var FALLBACK_KEYFRAME_PROPERTIES=[
    {value:'x',label:'水平位置 X',min:0,max:1,initial:0.5},
    {value:'y',label:'垂直位置 Y',min:0,max:1,initial:0.5},
    {value:'scale',label:'整体缩放',min:0.1,max:5,initial:1},
    {value:'scale_x',label:'水平缩放',min:0.1,max:5,initial:1},
    {value:'scale_y',label:'垂直缩放',min:0.1,max:5,initial:1},
    {value:'rotation',label:'旋转角度',min:-3600,max:3600,initial:0},
    {value:'opacity',label:'不透明度',min:0,max:1,initial:1},
    {value:'blur',label:'模糊',min:0,max:100,initial:0},
    {value:'camera_x',label:'镜头中心 X',min:0,max:1,initial:0.5},
    {value:'camera_y',label:'镜头中心 Y',min:0,max:1,initial:0.5},
    {value:'camera_scale',label:'镜头缩放',min:0.1,max:5,initial:1},
    {value:'offset_x',label:'水平偏移',min:-10000,max:10000,initial:0},
    {value:'offset_y',label:'垂直偏移',min:-10000,max:10000,initial:0}
  ];
  function fallbackKeyframeInfo(property){
    return FALLBACK_KEYFRAME_PROPERTIES.find(function(item){return item.value===property;})||FALLBACK_KEYFRAME_PROPERTIES[0];
  }
  function parseFallbackKeyframeChannels(raw){
    if(!raw)return [];
    var input=typeof raw==='string'?JSON.parse(raw):raw;
    if(!Array.isArray(input))throw new Error('视觉关键帧必须是 JSON 数组。');
    var result=[];
    input.forEach(function(channel){
      if(!channel||typeof channel!=='object'||!Array.isArray(channel.keyframes))throw new Error('视觉关键帧通道格式无效。');
      var info=FALLBACK_KEYFRAME_PROPERTIES.find(function(item){return item.value===channel.property;});
      if(!info)throw new Error('不支持的视觉关键帧属性：'+String(channel.property||'未知'));
      if(result.some(function(item){return item.property===channel.property;}))throw new Error('同一属性不能存在重复关键帧通道。');
      var points=channel.keyframes.map(function(point){
        if(!point||!Number.isFinite(Number(point.at))||!Number.isFinite(Number(point.value)))throw new Error('关键帧时间和值必须是有限数字。');
        var easing=point.easing||'linear';
        function normalizeBezier(raw){
          if(!raw||typeof raw!=='object'||raw.type!=='bezier'||
            Object.keys(raw).sort().join(',')!=='type,x1,x2,y1,y2'){
            throw new Error('关键帧贝塞尔曲线结构无效。');
          }
          var curve={type:'bezier',x1:Number(raw.x1),y1:Number(raw.y1),x2:Number(raw.x2),y2:Number(raw.y2)};
          if(['x1','y1','x2','y2'].some(function(key){return !Number.isFinite(curve[key])||curve[key]<0||curve[key]>1;})||curve.x1>curve.x2){
            throw new Error('关键帧贝塞尔控制点必须在 0–1 范围，且 x1 不大于 x2。');
          }
          return curve;
        }
        function normalizeWindow(raw){
          if(!raw||typeof raw!=='object'||raw.type!=='window'||
            !Number.isFinite(Number(raw.start))||!Number.isFinite(Number(raw.end))||
            Number(raw.start)<0||Number(raw.end)>1||Number(raw.end)<=Number(raw.start)){
            throw new Error('关键帧曲线分段格式无效。');
          }
          if(Object.keys(raw).sort().join(',')==='end,preset,start,type'){
            if(['linear','ease_in','ease_out','ease_in_out'].indexOf(raw.preset)<0)throw new Error('关键帧分段缓动预设无效。');
            return {type:'window',preset:raw.preset,start:Number(raw.start),end:Number(raw.end)};
          }
          if(Object.keys(raw).sort().join(',')==='curve,end,start,type'){
            return {type:'window',curve:normalizeBezier(raw.curve),start:Number(raw.start),end:Number(raw.end)};
          }
          throw new Error('关键帧曲线分段格式无效。');
        }
        if(typeof easing==='string'){
          if(['linear','ease_in','ease_out','ease_in_out'].indexOf(easing)<0)throw new Error('关键帧缓动类型无效。');
        }else if(easing&&typeof easing==='object'&&easing.type==='bezier'){
          easing=normalizeBezier(easing);
        }else if(easing&&typeof easing==='object'&&easing.type==='window'){
          easing=normalizeWindow(easing);
        }else{
          throw new Error('关键帧缓动格式无效。');
        }
        var at=Number(point.at),value=Number(point.value);
        if(at<0||value<info.min||value>info.max)throw new Error('属性 '+info.label+' 的关键帧时间或数值超出范围。');
        return {at:at,value:value,easing:easing};
      }).sort(function(a,b){return a.at-b.at;});
      for(var i=1;i<points.length;i++)if(points[i].at<=points[i-1].at)throw new Error('同一通道的关键帧时间必须严格递增。');
      result.push({property:info.value,keyframes:points});
    });
    return result;
  }
  function fallbackKeyframeEase(ratio,easing){
    var x=Math.max(0,Math.min(1,ratio));
    function base(t,preset){
      t=Math.max(0,Math.min(1,t));
      if(preset==='ease_in')return t*t;
      if(preset==='ease_out')return 1-(1-t)*(1-t);
      if(preset==='ease_in_out')return t<0.5?2*t*t:1-Math.pow(-2*t+2,2)/2;
      return t;
    }
    function bezier(t,curve){
      var target=Math.max(0,Math.min(1,t));
      function cubic(u,a,b){var inverse=1-u;return 3*inverse*inverse*u*a+3*inverse*u*u*b+u*u*u;}
      if(target<=0||target>=1)return target;
      var low=0,high=1;
      for(var i=0;i<28;i++){
        var middle=(low+high)*0.5;
        if(cubic(middle,curve.x1,curve.x2)<target)low=middle;else high=middle;
      }
      return Math.max(0,Math.min(1,cubic((low+high)*0.5,curve.y1,curve.y2)));
    }
    function evaluate(t,spec){return typeof spec==='string'?base(t,spec):bezier(t,spec);}
    if(typeof easing==='string')return base(x,easing);
    if(easing.type==='bezier')return bezier(x,easing);
    var start=Math.max(0,Math.min(1,Number(easing.start))),end=Math.max(0,Math.min(1,Number(easing.end)));
    if(end<=start)return x;
    var spec=easing.curve||easing.preset;
    var low=evaluate(start,spec),high=evaluate(end,spec);
    if(Math.abs(high-low)<1e-12)return x;
    return Math.max(0,Math.min(1,(evaluate(start+(end-start)*x,spec)-low)/(high-low)));
  }
  function fallbackKeyframeValue(points,at){
    if(!points.length||at<points[0].at)return 0;
    if(Math.abs(at-points[0].at)<1e-9)return points[0].value;
    for(var i=1;i<points.length;i++){
      var left=points[i-1],right=points[i];
      if(at<=right.at+1e-9){
        var span=right.at-left.at;if(span<=1e-12)return right.value;
        var eased=fallbackKeyframeEase((at-left.at)/span,right.easing);
        return left.value+(right.value-left.value)*eased;
      }
    }
    return points[points.length-1].value;
  }
  function fallbackKeyframeCurve(channel,duration,info){
    if(!channel||channel.keyframes.length<2||duration<=0)return '';
    var x=function(t){return 8+Math.max(0,Math.min(1,t/duration))*344;};
    var y=function(v){return 8+(1-(Math.max(info.min,Math.min(info.max,v))-info.min)/Math.max(1e-9,info.max-info.min))*72;};
    var commands=[];
    for(var i=1;i<channel.keyframes.length;i++){
      var a=channel.keyframes[i-1],b=channel.keyframes[i],span=b.at-a.at;if(span<=0)continue;
      for(var j=0;j<=12;j++){
        if(i>1&&j===0)continue;
        var t=j/12,e=fallbackKeyframeEase(t,b.easing);
        commands.push((commands.length?'L':'M')+x(a.at+span*t).toFixed(2)+' '+y(a.value+(b.value-a.value)*e).toFixed(2));
      }
    }
    return commands.join(' ');
  }
  function renderFallbackKeyframeControls(clip){
    var panel=$('fs-keyframe-fallback-controls');if(!panel)return;
    var textarea=$('fs-field-keyframes');
    var raw=textarea?textarea.value:String((clip.attrs||{})['data-film-keyframes']||'');
    var channels=[];
    try{channels=parseFallbackKeyframeChannels(raw);}catch(error){
      panel.innerHTML='<div class="fs-alert fs-visible fs-error" role="status">'+esc(String(error&&error.message||error))+'</div>';
      return;
    }
    var available=FALLBACK_KEYFRAME_PROPERTIES;
    var property=S.keyframeSelectedProperty||'opacity';
    if(!available.some(function(item){return item.value===property;}))property='opacity';
    S.keyframeSelectedProperty=property;
    var info=fallbackKeyframeInfo(property);
    var channel=channels.find(function(item){return item.property===property;})||{property:property,keyframes:[]};
    var duration=Number(clip.duration)||0,frameStep=1/Math.max(1,Math.min(60,num(S.data&&S.data.fps,30)));
    var html='<label class="fs-inspector-field">动画属性<select id="fs-keyframe-fallback-property">'+
      available.map(function(item){return '<option value="'+esc(item.value)+'"'+(item.value===property?' selected':'')+'>'+esc(item.label)+'</option>';}).join('')+
      '</select></label><div class="fs-keyframe-range"><span>'+info.min+' 至 '+info.max+'</span><span>'+duration.toFixed(3)+' 秒</span></div>';
    html+='<svg id="fs-keyframe-fallback-graph" class="fs-keyframe-graph" viewBox="0 0 360 96" preserveAspectRatio="none" role="img" aria-label="'+esc(info.label)+' 关键帧曲线，可拖动节点修改">';
    [0,.25,.5,.75,1].forEach(function(ratio){
      html+='<line class="fs-keyframe-grid" x1="8" x2="352" y1="'+(8+ratio*72)+'" y2="'+(8+ratio*72)+'"></line>';
      html+='<line class="fs-keyframe-grid" x1="'+(8+ratio*344)+'" x2="'+(8+ratio*344)+'" y1="8" y2="80"></line>';
    });
    html+='<line class="fs-keyframe-playhead" x1="'+(8+Math.max(0,Math.min(1,(S.playhead-clip.at)/duration))*344)+'" x2="'+(8+Math.max(0,Math.min(1,(S.playhead-clip.at)/duration))*344)+'" y1="4" y2="84"></line>';
    var path=fallbackKeyframeCurve(channel,duration,info);
    if(path)html+='<path class="fs-keyframe-curve" d="'+path+'" fill="none"></path>';
    channel.keyframes.forEach(function(point,index){
      var cx=8+Math.max(0,Math.min(1,point.at/duration))*344;
      var cy=8+(1-(Math.max(info.min,Math.min(info.max,point.value))-info.min)/Math.max(1e-9,info.max-info.min))*72;
      html+='<circle class="fs-keyframe-point" data-fallback-kf-point="'+index+'" cx="'+cx+'" cy="'+cy+'" r="4.5" tabindex="0" role="slider" aria-label="关键帧 '+(index+1)+'"></circle>';
    });
    html+='</svg><div class="fs-keyframe-labels"><span>0.000s</span><span>'+duration.toFixed(3)+'s</span></div>';
    html+='<div class="fs-keyframe-list">';
    if(channel.keyframes.length){
      channel.keyframes.forEach(function(point,index){
        var easingValue=typeof point.easing==='string'?point.easing:(point.easing&&point.easing.type==='bezier'?'bezier':'window');
        html+='<div class="fs-keyframe-row"><label>时间<input type="number" min="0" max="'+duration+'" step="'+frameStep+'" data-fallback-kf-at="'+index+'" value="'+point.at.toFixed(3)+'"></label>'+
          '<label>数值<input type="number" min="'+info.min+'" max="'+info.max+'" step="0.01" data-fallback-kf-value="'+index+'" value="'+point.value+'"></label>'+
          '<label>缓动<select data-fallback-kf-easing="'+index+'" aria-label="关键帧 '+(index+1)+' 缓动">'+
          '<option value="linear"'+(easingValue==='linear'?' selected':'')+'>线性</option>'+
          '<option value="ease_in"'+(easingValue==='ease_in'?' selected':'')+'>缓入</option>'+
          '<option value="ease_out"'+(easingValue==='ease_out'?' selected':'')+'>缓出</option>'+
          '<option value="ease_in_out"'+(easingValue==='ease_in_out'?' selected':'')+'>缓入缓出</option>'+
          '<option value="bezier" disabled'+(easingValue==='bezier'?' selected':'')+'>自定义贝塞尔（请使用 React Studio 调整控制柄）</option>'+
          '<option value="window" disabled'+(easingValue==='window'?' selected':'')+'>裁剪保真的曲线分段</option></select></label>'+
          '<button type="button" class="fs-btn" data-fallback-kf-remove="'+index+'" aria-label="删除关键帧 '+(index+1)+'">删除</button></div>';
      });
    }else html+='<div class="fs-empty">该属性还没有关键帧。</div>';
    html+='</div><div class="fs-keyframe-add">'+
      '<label class="fs-inspector-field">时间（相对秒）<input id="fs-keyframe-fallback-new-at" type="number" min="0" max="'+duration+'" step="'+frameStep+'" value="'+Math.max(0,Math.min(duration,S.playhead-clip.at)).toFixed(3)+'"></label>'+
      '<label class="fs-inspector-field">数值<input id="fs-keyframe-fallback-new-value" type="number" min="'+info.min+'" max="'+info.max+'" step="0.01" value="'+info.initial+'"></label>'+
      '<label class="fs-inspector-field">缓动<select id="fs-keyframe-fallback-new-easing"><option value="linear">线性</option><option value="ease_in">缓入</option><option value="ease_out">缓出</option><option value="ease_in_out">缓入缓出</option></select></label>'+
      '<button type="button" class="fs-btn" data-fallback-kf-add>添加关键帧</button></div>'+
      '<button type="button" class="fs-btn" data-fallback-kf-clear'+(channels.length?'':' disabled')+'>清空全部视觉关键帧</button>';
    panel.innerHTML=html;
  }
  function fallbackKeyframeSetDraft(clip,channels){
    var textarea=$('fs-field-keyframes');
    if(!textarea)return;
    textarea.value=channels&&channels.length?JSON.stringify(channels):'';
    renderFallbackKeyframeControls(clip);
  }
  function bindFallbackKeyframeEditor(clip){
    var panel=$('fs-keyframe-fallback-controls');if(!panel||panel.__filmStudioKeyframeBound)return;
    panel.__filmStudioKeyframeBound=true;
    function getDraft(){
      var textarea=$('fs-field-keyframes');
      return parseFallbackKeyframeChannels(textarea?textarea.value:'');
    }
    function updatePoint(index,patch){
      var channels=getDraft(),property=S.keyframeSelectedProperty||'opacity';
      var channel=channels.find(function(item){return item.property===property;});
      if(!channel||!channel.keyframes[index])return;
      var point=channel.keyframes[index],info=fallbackKeyframeInfo(property),step=1/Math.max(1,Math.min(60,num(S.data&&S.data.fps,30)));
      if(typeof patch.value==='number')point.value=Math.max(info.min,Math.min(info.max,patch.value));
      if(patch.easing)point.easing=patch.easing;
      if(typeof patch.at==='number'){
        var lower=index>0?channel.keyframes[index-1].at+step:0;
        var upper=index<channel.keyframes.length-1?channel.keyframes[index+1].at-step:Number(clip.duration);
        if(upper<lower){showStatus('相邻关键帧间隔不足一帧，不能移动该节点。','error');return;}
        point.at=Math.max(lower,Math.min(upper,Math.round(patch.at/step)*step));
      }
      fallbackKeyframeSetDraft(clip,channels);
    }
    panel.addEventListener('change',function(event){
      var target=event.target;if(!target)return;
      if(target.id==='fs-keyframe-fallback-property'){
        S.keyframeSelectedProperty=target.value;renderFallbackKeyframeControls(clip);return;
      }
      var match=target.getAttribute&&target.getAttribute('data-fallback-kf-at');
      if(match!=null){var at=Number(target.value);if(target.value.trim()&&Number.isFinite(at))updatePoint(Number(match),{at:at});return;}
      match=target.getAttribute&&target.getAttribute('data-fallback-kf-value');
      if(match!=null){var value=Number(target.value);if(target.value.trim()&&Number.isFinite(value))updatePoint(Number(match),{value:value});return;}
      match=target.getAttribute&&target.getAttribute('data-fallback-kf-easing');
      if(match!=null&&target.value!=='window')updatePoint(Number(match),{easing:target.value});
    });
    panel.addEventListener('click',function(event){
      var target=event.target&&event.target.closest?event.target.closest('[data-fallback-kf-add],[data-fallback-kf-remove],[data-fallback-kf-clear]'):null;
      if(!target)return;
      event.preventDefault();
      var channels;try{channels=getDraft();}catch(error){showStatus(String(error&&error.message||error),'error');return;}
      var property=S.keyframeSelectedProperty||'opacity',info=fallbackKeyframeInfo(property),channel=channels.find(function(item){return item.property===property;});
      if(target.hasAttribute('data-fallback-kf-clear')){fallbackKeyframeSetDraft(clip,[]);return;}
      if(target.hasAttribute('data-fallback-kf-remove')){
        var index=Number(target.getAttribute('data-fallback-kf-remove'));
        if(!channel)return;
        channel.keyframes.splice(index,1);
        if(!channel.keyframes.length)channels=channels.filter(function(item){return item.property!==property;});
        fallbackKeyframeSetDraft(clip,channels);return;
      }
      var atInput=$('fs-keyframe-fallback-new-at'),valueInput=$('fs-keyframe-fallback-new-value'),easingInput=$('fs-keyframe-fallback-new-easing');
      var at=Number(atInput&&atInput.value),value=Number(valueInput&&valueInput.value),duration=Number(clip.duration);
      if(!atInput||!valueInput||!atInput.value.trim()||!valueInput.value.trim()||!Number.isFinite(at)||!Number.isFinite(value)||at<0||at>duration||value<info.min||value>info.max){
        showStatus('关键帧时间或数值超出当前片段/属性范围。','error');return;
      }
      var step=1/Math.max(1,Math.min(60,num(S.data&&S.data.fps,30))),snapped=Math.round(at/step)*step;
      if(channel&&channel.keyframes.some(function(point){return Math.abs(point.at-snapped)<step*0.5;})){
        showStatus('该时间点已经存在关键帧。','error');return;
      }
      if(!channel){channel={property:property,keyframes:[]};channels.push(channel);}
      channel.keyframes.push({at:snapped,value:value,easing:easingInput?easingInput.value:'linear'});
      channel.keyframes.sort(function(a,b){return a.at-b.at;});
      fallbackKeyframeSetDraft(clip,channels);
    });
    var dragging=null;
    panel.addEventListener('pointerdown',function(event){
      var point=event.target&&event.target.closest?event.target.closest('[data-fallback-kf-point]'):null;
      if(!point)return;
      event.preventDefault();event.stopPropagation();dragging=Number(point.getAttribute('data-fallback-kf-point'));
      try{panel.setPointerCapture(event.pointerId);}catch(_){}
    });
    panel.addEventListener('pointermove',function(event){
      if(dragging==null)return;
      var graph=$('fs-keyframe-fallback-graph');if(!graph)return;
      var rect=graph.getBoundingClientRect();if(!rect.width||!rect.height)return;
      var info=fallbackKeyframeInfo(S.keyframeSelectedProperty||'opacity'),duration=Number(clip.duration),step=1/Math.max(1,Math.min(60,num(S.data&&S.data.fps,30)));
      var x=(event.clientX-rect.left)/rect.width*360,y=(event.clientY-rect.top)/rect.height*96;
      updatePoint(dragging,{at:Math.max(0,Math.min(duration,(x-8)/344*duration)),value:info.max-(y-8)/72*(info.max-info.min)});
      event.preventDefault();
    });
    panel.addEventListener('pointerup',function(){dragging=null;});
    panel.addEventListener('pointercancel',function(){dragging=null;});
  }
  function showStatus(message, kind) { var el=$('film-studio-alert'); if(!el)return; el.className='fs-alert fs-visible'+(kind==='error'?' fs-error':kind==='success'?' fs-success':''); el.textContent=message||''; }
  function currentFilmProposalRevision(proposal){
    var pending=String(S.pendingExternalRevision||'');
    if(pending&&pending!=='__reload__')return pending;
    return String(proposal&&proposal.observed_revision||S.rev||'');
  }
  function clearFilmProposalTimelinePreview(){
    clearTimelineGesturePreview();
    clearTimelineEnvelopePreview();
  }
  function markFilmProposalStale(revision,message){
    var proposal=S.proposal;
    if(!proposal)return false;
    var observed=String(revision||'');
    if(observed)proposal.observed_revision=observed;
    S.previewProposalActive=false;S.runtimeRev='';clearFilmProposalTimelinePreview();
    if(observed&&proposal.stale_notified_revision!==observed){
      proposal.stale_notified_revision=observed;
      appendMessage(message||'影片 revision 已变化。旧 AI 提案仍可查看或拒绝，但不能预览/应用。','assistant');
    }else{
      renderProposalPanel();
    }
    return true;
  }
  function blockStaleFilmProposalAction(proposal,action){
    if(!proposal)return false;
    var pending=String(S.pendingExternalRevision||'');
    var current=currentFilmProposalRevision(proposal);
    var stale=!!pending||!current||String(proposal.base_revision||'')!==current;
    if(!stale)return false;
    if(current&&current!=='__reload__')proposal.observed_revision=current;
    S.previewProposalActive=false;S.runtimeRev='';clearFilmProposalTimelinePreview();
    renderProposalPanel();
    showStatus('当前影片 revision 已变化（当前：'+(current&&current!=='__reload__'?current:'未知')+'）。旧提案不能'+action+'；请重新生成，或拒绝旧提案。','error');
    return true;
  }
  function renderAiDirector() {
    var bridge=window.filmStudioReactAi,root=$('fs-react-ai-director'),legacy=$('fs-static-ai-director');
    if(bridge&&typeof bridge.render==='function'&&root){
      var proposalPreview=S.proposal?proposalOperationPreviews(S.proposal):null;
      var proposal=S.proposal?{
        summary:String(S.proposal.summary||'AI 候选修改'),
        operationCount:(S.proposal.operations||[]).length,
        baseRevision:String(S.proposal.base_revision||''),
        operationPreviews:proposalPreview.entries,
        affectedClips:proposalPreview.affected,
        truncatedCount:proposalPreview.truncatedCount
      }:null;
      try{
        bridge.render({
          messages:(S.messages||[]).map(function(item){return{text:String(item.text||''),role:String(item.role||'assistant')};}),
          proposal:proposal,
          currentRevision:currentFilmProposalRevision(S.proposal)
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
    clearFilmProposalTimelinePreview();
    S.runtimeRev='';S.runtimeCommand='';S.previewProposalActive=false;setPlaybackState(false);
    S.elementInspect=false;S.selectedElement=null;
    renderElementInspector(null);
    var selection=$('fs-element-selection');if(selection)selection.hidden=true;
    var inspectButton=$('fs-element-inspect-toggle');if(inspectButton){inspectButton.setAttribute('aria-pressed','false');inspectButton.textContent='选择画面元素';}
    syncPlaybackButtonText();
  }
  function migrationPreviewPayload(){
    var migration=S.migration,preview=migration&&migration.migration_preview;
    var payload=preview&&preview.film_payload;
    return payload&&payload.exists===true&&Array.isArray(payload.tracks)?payload:null;
  }
  function migrationPreviewRevision(){
    var migration=S.migration||{},preview=migration.migration_preview||{};
    return 'migration-preview:'+S.projectId+':'+
      String(migration.source_composition_version||preview.source_composition_version||'unknown')+':'+
      String(preview.selected_variant_id||'default');
  }
  function syncRuntime(type) {
    var frame=$('fs-preview-composition');
    if(type)S.runtimeCommand=type;
    else if(!S.runtimeCommand)S.runtimeCommand=window.filmStudioPlaying?'play':'seek';
    var candidateActive=!!(S.previewProposalActive&&S.proposal);
    var migrationPayload=(!S.data||!S.data.exists)?migrationPreviewPayload():null;
    var runtimeData=candidateActive?S.proposal.candidate:(migrationPayload||S.data);
    var runtimeRevision=candidateActive?'proposal:'+S.proposal.id:(migrationPayload?migrationPreviewRevision():S.rev);
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
    else if(command==='seek-pause')frame.contentWindow.postMessage({type:'film-runtime:seek-pause',time:S.playhead,seekId:S.seekGeneration},window.location.origin);
    else frame.contentWindow.postMessage({type:'film-runtime:seek',time:S.playhead,seekId:S.seekGeneration},window.location.origin);
    S.runtimeCommand='';
    syncPlaybackButtonText();
  }
  function filmStudioRuntimeFrameLoaded() {
    var frame=$('fs-preview-composition');
    if(!frame)return;
    var doc=null;
    try{doc=frame.contentDocument;}catch(_){}
    if(S.runtimeReady&&doc&&doc===S.runtimeDocument)return;
    S.runtimeReady=true;S.runtimeDocument=doc;S.runtimeRev='';
    if((S.data&&S.data.exists)||migrationPreviewPayload())
      syncRuntime(S.runtimeCommand||(window.filmStudioPlaying?'play':'seek'));
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
    if(!S.data||!S.data.exists){
      if(migrationPreviewPayload()){syncRuntime();return;}
      resetPreview();return;
    }
    syncRuntime();
  }
  function setPlayheadVisual() {
    var range=$('fs-playhead'),reactScrub=$('fs-react-scrub-row');
    var reactOwnsScrub=!!(range&&reactScrub&&reactScrub.contains(range)&&reactScrub.style.display!=='none');
    if(!reactOwnsScrub){
      if(range){range.max=String(S.duration);range.value=String(Math.min(S.duration,Math.max(0,S.playhead)));}
      var val=$('fs-playhead-value');if(val)val.textContent=fmtTime(S.playhead);
    }
    var monitor=$('fs-monitor-time');if(monitor)monitor.textContent=fmtTime(S.playhead);
    var line=$('fs-playhead-line');
    var reactTimeline=window.filmStudioReactTimeline&&typeof window.filmStudioReactTimeline.renderSurface==='function';
    if(line&&!reactTimeline)line.style.left=(132+timelineSecondsToPixels(S.playhead,S.pxPerSec))+'px';
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
  function isTransparentBackground(value) {
    var text=String(value||'').trim().toLowerCase();
    if(!text||text==='transparent')return true;
    var rgba=text.match(/^rgba\(\s*[^,]+,\s*[^,]+,\s*[^,]+,\s*([+-]?(?:\d+\.?\d*|\.\d+))\s*\)$/i);
    return !!rgba&&Number(rgba[1])<=0;
  }
  function normalizedFontWeight(value) {
    var text=String(value||'').trim().toLowerCase();
    if(!text||text==='normal'||text==='lighter')return 400;
    if(text==='bold'||text==='bolder')return 700;
    var number=Number(text);
    return Number.isFinite(number)?Math.max(100,Math.min(900,Math.round(number/100)*100)):400;
  }
  function validEditableElementCssLength(value,property){
    var text=String(value==null?'':value).trim();
    if(!text)return true;
    if(text.length>120)return false;
    var match=text.match(/^(-?(?:\d+(?:\.\d+)?|\.\d+))(px|%|em|rem|vh|vw)?$/i);
    if(!match)return false;
    var magnitude=Number(match[1]);
    if(!Number.isFinite(magnitude))return false;
    if(property==='lineHeight'&&magnitude<=0)return false;
    if(property==='letterSpacing'&&Math.abs(magnitude)>1000)return false;
    return true;
  }
  function editableElementCssLength(value,property){
    var text=String(value==null?'':value).trim();
    return validEditableElementCssLength(text,property)?text:'';
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
      '<div class="fs-inspector-field"><span>背景填色</span><div style="display:flex;align-items:center;gap:8px"><label style="display:flex;align-items:center;gap:4px"><input id="fs-element-background-enabled" type="checkbox" '+(!isTransparentBackground(String(styles.backgroundColor||'transparent'))?'checked':'')+'>启用</label><input id="fs-element-background-color" type="color" aria-label="背景颜色" value="'+esc(hexColor(styles.backgroundColor||'#ffffff'))+'"></div></div>'+
      '<label class="fs-inspector-field">不透明度（0–1）<input id="fs-element-opacity" type="number" min="0" max="1" step="0.05" value="'+esc(num(styles.opacity,1))+'"></label>'+
      '<label class="fs-inspector-field">字重<select id="fs-element-font-weight">'+[100,200,300,400,500,600,700,800,900].map(function(weight){var selected=weight===normalizedFontWeight(styles.fontWeight)?' selected':'';return '<option value="'+weight+'"'+selected+'>'+weight+(weight===400?'（常规）':weight===700?'（粗体）':'')+'</option>';}).join('')+'</select></label>'+
      '<label class="fs-inspector-field">文字对齐<select id="fs-element-text-align">'+[['left','左对齐'],['center','居中'],['right','右对齐'],['justify','两端对齐'],['start','起始对齐'],['end','末端对齐']].map(function(option){var current=String(styles.textAlign||'left'),selected=option[0]===current?' selected':'';return '<option value="'+option[0]+'"'+selected+'>'+option[1]+'</option>';}).join('')+'</select></label>'+
      '<label class="fs-inspector-field">行高（CSS 长度）<input id="fs-element-line-height" type="text" maxlength="120" inputmode="decimal" placeholder="'+esc('留空不修改；当前值：'+String(styles.lineHeight||'未提供'))+'" value="'+esc(editableElementCssLength(styles.lineHeight,'lineHeight'))+'"></label>'+
      '<label class="fs-inspector-field">字间距（CSS 长度）<input id="fs-element-letter-spacing" type="text" maxlength="120" inputmode="decimal" placeholder="'+esc('留空不修改；当前值：'+String(styles.letterSpacing||'未提供'))+'" value="'+esc(editableElementCssLength(styles.letterSpacing,'letterSpacing'))+'"></label>'+
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
      'fs-element-background-color':hexColor(styles.backgroundColor||'#ffffff'),
      'fs-element-opacity':String(num(styles.opacity,1)),
      'fs-element-font-weight':String(normalizedFontWeight(styles.fontWeight)),
      'fs-element-text-align':['left','center','right','justify','start','end'].indexOf(String(styles.textAlign||'left'))>=0?String(styles.textAlign||'left'):'left',
      'fs-element-line-height':editableElementCssLength(styles.lineHeight,'lineHeight'),
      'fs-element-letter-spacing':editableElementCssLength(styles.letterSpacing,'letterSpacing'),
      'fs-element-font-size':String(num(parseFloat(styles.fontSize),24)),
      'fs-element-rotate':String(rotateMatch?Number(rotateMatch[0]):0)
    };
    Object.keys(values).forEach(function(id){
      var input=$(id);if(input&&!(id==='fs-element-text'&&input.disabled))input.value=values[id];
    });
    var backgroundToggle=$('fs-element-background-enabled');
    if(backgroundToggle)backgroundToggle.checked=!isTransparentBackground(String(styles.backgroundColor||'transparent'));
  }
  function renderMigration() {
    // #fs-timeline is a React portal target when the verified bundle is loaded.
    // Unmount that portal before legacy migration markup mutates its DOM children.
    var timelineBridge=window.filmStudioReactTimeline;
    if(timelineBridge&&typeof timelineBridge.clearSurface==='function')timelineBridge.clearSurface();
    var data=S.migration||{}, preview=data.migration_preview||{}, warnings=preview.warnings||[];
    S.duration=Math.max(0.001,num(preview.duration,10));
    S.playhead=Math.max(0,Math.min(S.playhead,S.duration));
    S.assetMode='director';
    $('fs-project-label').textContent=S.projectId;$('fs-clip-count').textContent='—';$('fs-timeline-meta').textContent='迁移预览';
    $('fs-assets-list').innerHTML='<div class="fs-empty">当前项目还没有 film.html。初始化将生成可编辑影片文档，不会覆盖旧 Composition。</div>';
    $('fs-timeline').innerHTML='<div class="fs-empty">先检查迁移提示，再初始化。</div>';
    if(window.filmStudioReactInspector&&typeof window.filmStudioReactInspector.renderClip==='function')window.filmStudioReactInspector.renderClip('初始化影片文档后才能编辑片段。');
    else $('fs-inspector').innerHTML='<div class="fs-empty">初始化影片文档后才能编辑片段。</div>';
    resetPreview();
    var variants=Array.isArray(data.variants)?data.variants:[];
    var needsVariantChoice=variants.length>1&&!preview.selected_variant_id
      &&(data.migration_error_code==='ACTIVE_VARIANT_REQUIRED'||data.migration_error_code==='ACTIVE_VARIANT_NOT_FOUND');
    var message=data.migration_available
      ?'可从当前 Composition 初始化 film.html。预计 '+(preview.duration||0)+' 秒、'+(preview.track_count||0)+' 条轨道、'+(preview.clip_count||0)+' 个片段。'
      :(needsVariantChoice?'旧项目包含多个候选版本，请先选择本次要迁移的版本。':'无法初始化：'+(data.migration_error||'当前项目没有可编辑的 CompositionIR。'));
    if(warnings.length)message+=' 迁移提示：'+warnings.join('；');
    if(data.migration_available&&!migrationPreviewPayload())
      message+=' 迁移候选画面尚未成功载入；在预览可见前不会创建 film.html。';
    showStatus(message,data.migration_available?'':'error');
    var selectedVariantId=String(preview.selected_variant_id||data.active_variant_id||'');
    var variantPicker='';
    if(variants.length>1){
      variantPicker='<label class="fs-migrate-variant" for="fs-migration-variant"><span>选择迁移版本</span><select id="fs-migration-variant">';
      if(!selectedVariantId)variantPicker+='<option value="" disabled selected>请选择一个版本</option>';
      variantPicker+=variants.map(function(variant){
        var variantId=String(variant.id||''),label=String(variant.label||variantId);
        return '<option value="'+esc(variantId)+'"'+(variantId===selectedVariantId?' selected':'')+'>'+esc(label)+' · v'+esc(String(variant.version||1))+'</option>';
      }).join('');
      variantPicker+='</select><small>本次只迁移所选版本；其余版本完整保留在源 Composition 归档中。</small></label>';
    }
    if(data.migration_available||variants.length>1){
      $('fs-timeline').innerHTML='<div class="fs-migrate-action"><strong>从旧项目迁移到 film.html</strong><p>初始化会创建影片文档。旧工作台不会自动同步 film.html 的编辑。迁移存在警告时会再次要求确认。</p>'+variantPicker+(data.migration_available?'<button type="button" class="fs-btn fs-primary" id="fs-initialize-button">初始化影片文档</button>':'')+'</div>';
      var variantSelect=$('fs-migration-variant');
      if(variantSelect)variantSelect.addEventListener('change',async function(){
        variantSelect.disabled=true;
        var migrationProjectId=S.projectId;
        var previousVariant=String(preview.selected_variant_id||'');
        var url='/api/editor/film?project_id='+encodeURIComponent(migrationProjectId)+
          '&selected_variant_id='+encodeURIComponent(variantSelect.value)+'&preview=1';
        try{
          var next=await apiGet(url);
          if(migrationProjectId!==S.projectId||!S.open)return;
          if(next&&next.ok&&next.exists===false&&next.migration_available===true&&
             next.migration_preview&&next.migration_preview.film_payload){
            S.migration=next;renderMigration();
          }else{
            variantSelect.value=previousVariant;
            variantSelect.disabled=false;
            showStatus((next&&next.error)||'读取所选版本的可视候选失败；未初始化影片文档。','error');
          }
        }catch(error){
          if(migrationProjectId!==S.projectId)return;
          variantSelect.value=previousVariant;
          variantSelect.disabled=false;
          showStatus('读取所选版本预览失败：'+String(error&&error.message||error),'error');
        }
      });
      var initializeButton=$('fs-initialize-button');
      if(initializeButton)initializeButton.addEventListener('click',function(){filmStudioInitialize(false);});
    }
    syncAssetPaneMode();
    setPlayheadVisual();
    if(migrationPreviewPayload())syncRuntime('seek-pause');
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
  function syncAssetPaneMode(){
    var mode=S.assetMode==='director'?'director':(S.assetMode==='library'?'library':'clips');
    var list=$('fs-assets-list'),director=$('fs-director-planning-host');
    if(list)list.style.display=mode==='director'?'none':'flex';
    if(director)director.style.display=mode==='director'?'block':'none';
    [['clips',$('fs-mode-clips')],['library',$('fs-mode-library')],['director',$('fs-mode-director')]].forEach(function(entry){
      if(entry[1])entry[1].className='fs-asset-tab'+(mode===entry[0]?' fs-asset-tab-active':'');
    });
  }
  function restoreDirectorPlanningNode(id){
    var node=$(id);
    if(!node)return;
    var index=S.directorNodes.findIndex(function(item){return item.node===node;});
    if(index<0)return;
    var item=S.directorNodes.splice(index,1)[0];
    if(!item.parent)return;
    var anchor=item.nextSibling&&item.nextSibling.parentNode===item.parent?item.nextSibling:null;
    item.parent.insertBefore(item.node,anchor);
  }
  function mountDirectorPlanning(allowLegacyCompositionCommands){
    var host=$('fs-director-planning-host');
    if(!host)return;
    ['director-plan-card'].concat(allowLegacyCompositionCommands?['director-control-grid','director-bar-card']:[]).forEach(function(id){
      var node=$(id);
      if(!node||host.contains(node))return;
      if(!S.directorNodes.some(function(item){return item.node===node;})){
        S.directorNodes.push({node:node,parent:node.parentNode,nextSibling:node.nextSibling});
      }
      host.appendChild(node);
    });
    if(allowLegacyCompositionCommands){
      ['director-control-grid','director-bar-card'].forEach(function(id){
        var node=$(id);
        if(node&&host.contains(node))node.style.display='';
      });
    }else{
      // A project with film.html must not surface controls that write CompositionIR.
      // Restore them to the hidden legacy editor instead of merely disabling buttons.
      // Restore in reverse DOM order so each original nextSibling already exists in its parent.
      restoreDirectorPlanningNode('director-bar-card');
      restoreDirectorPlanningNode('director-control-grid');
    }
    var legacyNote=$('fs-director-legacy-note'),authorityNote=$('fs-director-authority-note');
    if(legacyNote)legacyNote.style.display=allowLegacyCompositionCommands?'':'none';
    if(authorityNote)authorityNote.style.display=allowLegacyCompositionCommands?'none':'';
    syncAssetPaneMode();
  }
  function restoreDirectorPlanning(){
    S.directorNodes.slice().reverse().forEach(function(item){
      if(!item.node||!item.parent)return;
      var anchor=item.nextSibling&&item.nextSibling.parentNode===item.parent?item.nextSibling:null;
      item.parent.insertBefore(item.node,anchor);
    });
    S.directorNodes=[];
    syncAssetPaneMode();
  }
  function renderAssets() {
    var all=clips();$('fs-clip-count').textContent=String(all.length);
    if(S.assetMode==='director'){syncAssetPaneMode();return;}
    syncAssetPaneMode();
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
      window.filmStudioReactAssets.render({clips:assetClips});
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
    S.assetMode=mode==='library'?'library':(mode==='director'?'director':'clips');
    syncAssetPaneMode();
    if(S.assetMode==='director')return;
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
    if(hasUncommittedInspectorEdits()){showStatus('请先应用 Inspector 属性草稿，或重新载入并确认丢弃，再添加素材。','error');return;}
    var selected=findClip(S.selectedId),request={project_id:S.projectId,base:S.rev,asset_id:assetId,at:S.playhead,still_duration:5};
    if(S.insertMode||S.overwriteMode){
      if(!selected){showStatus((S.overwriteMode?'覆盖':'插入')+'编辑模式下，请先选择目标轨道上的片段。','error');return;}
      request.placement_mode=S.overwriteMode?'overwrite':'ripple';request.track_id=selected.track_id;
    }
    var placementMode=request.placement_mode||'new-track';
    var transactionKind=placementMode==='overwrite'?'asset-overwrite':placementMode==='ripple'?'asset-ripple-insert':'asset-insert';
    try{
      await dispatchStudioTransaction(transactionKind,S.rev,[{
        op:'insert-asset',asset_id:String(assetId),placement_mode:placementMode,at:S.playhead,track_id:request.track_id||null
      }],async function(){
        var response=await apiPost('/api/editor/film/insert-asset',request);
        if(!response||!response.ok){showStatus((response&&response.error)||'素材加入时间线失败。','error');return false;}
        S.data=response;S.rev=response.rev;S.selectedId=response.inserted_clip_id||'';
        S.assetMode='clips';window.filmStudioShowAssetMode('clips');render();
        showStatus(response.placement_mode==='overwrite'
          ?'覆盖编辑已完成：仅替换目标轨道的重叠区间，其他轨道和非重叠片段未被推移。'
          :response.placement_mode==='ripple'
            ?'插入编辑已完成：目标轨道及同步锁定轨道已按素材时长推移。'
            :'已在播放头位置新建轨道并添加素材。', 'success');
        return true;
      });
    }catch(error){
      showStatus('素材加入时间线失败：'+String(error&&error.message||error),'error');
    }finally{flushPendingExternalRevision();}
  };
  function reactOwnsTimelineToolbar(){
    var bridge=window.filmStudioReactToolbar;
    return !!(bridge&&typeof bridge.renderTimeline==='function');
  }
  function syncPlacementModeButtons(){
    if(reactOwnsTimelineToolbar())return;
    document.querySelectorAll('[data-film-action="insert-mode"]').forEach(function(button){
      button.textContent='插入编辑：'+(S.insertMode?'开':'关');
      button.classList.toggle('fs-primary',S.insertMode);
      button.setAttribute('aria-pressed',S.insertMode?'true':'false');
    });
    document.querySelectorAll('[data-film-action="overwrite-mode"]').forEach(function(button){
      button.textContent='覆盖编辑：'+(S.overwriteMode?'开':'关');
      button.classList.toggle('fs-primary',S.overwriteMode);
      button.setAttribute('aria-pressed',S.overwriteMode?'true':'false');
    });
  }
  window.filmStudioToggleInsertMode=function(){
    S.insertMode=!S.insertMode;
    if(S.insertMode)S.overwriteMode=false;
    syncPlacementModeButtons();
    showStatus(S.insertMode?'插入编辑已开启：选择目标轨道上的片段，再从素材库添加素材；后续片段会推移。':'插入编辑已关闭。','success');
  };
  window.filmStudioToggleOverwriteMode=function(){
    S.overwriteMode=!S.overwriteMode;
    if(S.overwriteMode)S.insertMode=false;
    syncPlacementModeButtons();
    showStatus(S.overwriteMode?'覆盖编辑已开启：素材只替换所选轨道的重叠区间，不推移其他片段或音频轨道。':'覆盖编辑已关闭。','success');
  };
  function syncClipSelectionVisual(){
    var bridge=window.filmStudioReactTimeline;
    // React derives selected styling from FilmStore.selectedIdsVersion.
    // Mutate classes imperatively only for the no-bundle native timeline fallback.
    if(bridge&&typeof bridge.renderSurface==='function')return;
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
        syncClipSelectionVisual();renderInspector();renderAssets();syncDirectorSelection();
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
    syncClipSelectionVisual();renderInspector();renderAssets();syncDirectorSelection();
    return S.selectedIds.indexOf(id)>=0;
  }
  function setTimelineDropTarget(trackId,invalid){
    var bridge=window.filmStudioReactTimeline;
    if(bridge&&typeof bridge.setTrackDropTarget==='function'){
      bridge.setTrackDropTarget(trackId?{trackId:String(trackId),invalid:!!invalid}:null);
      return true;
    }
    return false;
  }
  function clearDragTrackTarget(d){
    var bridge=window.filmStudioReactTimeline;
    var reactOwnsTarget=bridge&&typeof bridge.setTrackDropTarget==='function';
    var targetRow=gestureDomRefs&&gestureDomRefs.targetRow;
    if(targetRow&&!reactOwnsTarget)targetRow.classList.remove('fs-track-drop-target','fs-track-drop-invalid');
    if(gestureDomRefs)gestureDomRefs.targetRow=null;
    setTimelineDropTarget(null,false);
  }
  function groupMoveTargetError(d,targetTrackId,rawDeltaSeconds){
    var controller=window.filmStudioTimelineController;
    if(controller&&typeof controller.validateMoveTarget==='function')
      return controller.validateMoveTarget({
        gesture:d,targetTrackId:targetTrackId,tracks:S.data&&S.data.tracks||[],
        minimumStart:(d&&d.members||[]).reduce(function(value,member){return Math.min(value,member.originalAt);},Infinity),
        rawDeltaSeconds:Number(rawDeltaSeconds)||0,candidates:timelineSnapCandidates((d&&d.members||[]).map(function(member){return member.id;})),
        pixelsPerSecond:S.pxPerSec,thresholdPixels:8,snapEnabled:S.snapEnabled,frameRate:timelineFrameRate()
      });
    var order=d&&d.trackOrder||[],targetIndex=order.indexOf(targetTrackId);
    if(targetIndex<0)return '目标轨道不存在，多选移动已取消。';
    var originIndex=Number.isInteger(d.originalTrackIndex)?d.originalTrackIndex:order.indexOf(d.trackId);
    if(originIndex<0)return '无法确认原轨道位置，多选移动已取消。';
    var trackDelta=targetIndex-originIndex,members=d.members||[];
    var selectedIds=members.map(function(member){return member.id;});
    var minStart=members.reduce(function(value,member){return Math.min(value,member.originalAt);},Infinity);
    var resolved=resolveTimelineMove(minStart,Number(rawDeltaSeconds)||0,selectedIds,8);
    var horizontalDelta=resolved.deltaSeconds;
    for(var i=0;i<members.length;i++){
      var member=members[i],destinationIndex=member.trackIndex+trackDelta;
      if(destinationIndex<0||destinationIndex>=order.length)return '多选移动超出轨道范围，操作已取消。';
      var destinationId=order[destinationIndex];
      var destination=(S.data&&S.data.tracks||[]).find(function(track){return track.id===destinationId;});
      if(!destination)return '目标轨道 '+destinationId+' 已不存在，多选移动已取消。';
      if(Object.prototype.hasOwnProperty.call(destination.attrs||{},'locked'))
        return '目标轨道 '+destinationId+' 已锁定，多选移动已取消。';

      // FilmDocument rejects overlapping clips at transaction validation time.
      // Reject that drop during preview instead of submitting an operation set
      // that can only return HTTP 400.
      var sourceClip=findClip(member.id);
      if(!sourceClip||sourceClip.end==null||!Number.isFinite(Number(sourceClip.end)))continue;
      var targetStart=Math.max(0,member.originalAt+horizontalDelta);
      var duration=Math.max(0,Number(sourceClip.end)-Number(sourceClip.at));
      var targetEnd=targetStart+duration;
      var stationary=destination.clips||[];
      for(var j=0;j<stationary.length;j++){
        var other=stationary[j];
        if(selectedIds.indexOf(other.id)>=0||other.end==null)continue;
        var otherStart=Number(other.at),otherEnd=Number(other.end);
        if(!Number.isFinite(otherStart)||!Number.isFinite(otherEnd))continue;
        if(targetStart<otherEnd-0.001&&otherStart<targetEnd-0.001)
          return '多选移动后片段 '+member.id+' 与目标轨道 '+destinationId+' 上的片段 '+other.id+' 时间重叠，操作已取消。';
      }
    }
    return null;
  }
  function timelineFrameRate(){
    var fps=Number(S.data&&S.data.fps);
    return Number.isFinite(fps)&&fps>0?Math.max(1,Math.min(120,fps)):30;
  }
  function timelineIsFrameAligned(value){
    var number=Number(value),fps=timelineFrameRate();
    return S.snapEnabled&&Number.isFinite(number)&&Math.abs(number*fps-Math.round(number*fps))<0.00002;
  }
  function timelineOperationNumber(value,preserveFramePrecision){
    var number=Number(value);
    if(!Number.isFinite(number))return number;
    return Number(number.toFixed(preserveFramePrecision?6:3));
  }
  function timelineSnapCandidates(excludedIds){
    var ids=excludedIds||[],candidates=[{at:0,kind:'片头'}];
    // Clip selection may move the playhead to the dragged clip's start. Do not
    // snap that clip straight back to its own origin while the pointer is moving.
    var playheadIsSelectedClipStart=ids.some(function(id){
      var selected=findClip(id);
      return selected&&Number.isFinite(Number(selected.at))&&Math.abs(timelineSecondsToPixels(Number(selected.at)-S.playhead,S.pxPerSec))<=0.5;
    });
    if(!playheadIsSelectedClipStart)candidates.push({at:S.playhead,kind:'播放头'});
    clips().forEach(function(clip){
      if(ids.indexOf(clip.id)>=0)return;
      if(Number.isFinite(Number(clip.at)))candidates.push({at:Number(clip.at),kind:'片段入点'});
      if(Number.isFinite(Number(clip.end)))candidates.push({at:Number(clip.end),kind:'片段出点'});
    });
    return candidates;
  }
  function findTimelineSnap(rawAt, excludedIds, thresholdPx){
    if(!S.snapEnabled)return null;
    var candidates=timelineSnapCandidates(excludedIds),math=timelineMath();
    if(math&&typeof math.resolveSnapCandidate==='function')
      return math.resolveSnapCandidate(rawAt,candidates,S.pxPerSec,thresholdPx,timelineFrameRate());
    var best=null,bestDistance=Infinity;
    candidates.forEach(function(candidate){
      if(!Number.isFinite(candidate.at))return;
      var distance=Math.abs(timelineSecondsToPixels(candidate.at-rawAt,S.pxPerSec));
      if(distance<=thresholdPx&&distance<bestDistance){best=candidate;bestDistance=distance;}
    });
    if(best)return best;
    var fps=timelineFrameRate(),frameAt=Math.max(0,Math.round(Math.max(0,Number(rawAt)||0)*fps)/fps);
    var frameDistance=Math.abs(timelineSecondsToPixels(frameAt-rawAt,S.pxPerSec));
    return frameDistance<=thresholdPx?{at:frameAt,kind:'帧'}:null;
  }
  function resolveTimelineMove(minimumStart,deltaSeconds,excludedIds,thresholdPx){
    var candidates=timelineSnapCandidates(excludedIds),math=timelineMath(),controller=window.filmStudioTimelineController;
    if(controller&&typeof controller.resolveMoveGesture==='function')
      return controller.resolveMoveGesture({
        minimumStart:minimumStart,rawDeltaSeconds:deltaSeconds,candidates:candidates,
        pixelsPerSecond:S.pxPerSec,thresholdPixels:thresholdPx,snapEnabled:S.snapEnabled,frameRate:timelineFrameRate()
      });
    if(math&&typeof math.resolveMoveGesture==='function')
      return math.resolveMoveGesture(minimumStart,deltaSeconds,candidates,S.pxPerSec,thresholdPx,S.snapEnabled,timelineFrameRate());
    var start=Math.max(0,num(minimumStart,0)),rawAt=Math.max(0,start+num(deltaSeconds,0));
    var snap=findTimelineSnap(rawAt,excludedIds,thresholdPx);
    var proposedAt=snap?Math.max(0,snap.at):rawAt;
    var delta=Math.max(-start,proposedAt-start),targetAt=Math.max(0,start+delta);
    var usedSnap=snap&&Math.abs(targetAt-snap.at)<0.001?snap:null;
    return {targetAt:targetAt,deltaSeconds:delta,snapCandidate:usedSnap};
  }
  function resolveTimelineTrim(edge,originalAt,originalEnd,deltaSeconds,excludedIds,thresholdPx){
    var candidates=timelineSnapCandidates(excludedIds),math=timelineMath(),method=edge==='start'?'resolveTrimStart':'resolveTrimEnd';
    if(math&&typeof math[method]==='function')
      return math[method](originalAt,originalEnd,deltaSeconds,candidates,S.pxPerSec,thresholdPx,S.snapEnabled,timelineFrameRate());
    var raw=edge==='start'?originalAt+deltaSeconds:originalEnd+deltaSeconds;
    var snap=findTimelineSnap(raw,excludedIds,thresholdPx),proposed=snap?snap.at:raw;
    var target=Math.max(originalAt+0.04,Math.min(originalEnd-0.04,proposed));
    var usedSnap=snap&&Math.abs(target-snap.at)<0.001?snap:null;
    return {target:target,deltaSeconds:target-(edge==='start'?originalAt:originalEnd),snapCandidate:usedSnap};
  }
  function resolveTimelineRoll(d,deltaSeconds,thresholdPx){
    var excluded=[d.leftClipId,d.rightClipId],candidates=timelineSnapCandidates(excluded),math=timelineMath();
    var controller=window.filmStudioTimelineController;
    if(controller&&typeof controller.resolveRollGesture==='function'){
      var managed=controller.resolveRollGesture({
        leftClipId:d.leftClipId,rightClipId:d.rightClipId,activeClipId:d.id,rightAt:d.rightAt,
        leftDuration:d.leftDuration,rightDuration:d.rightDuration,rawDeltaSeconds:deltaSeconds,
        candidates:candidates,pixelsPerSecond:S.pxPerSec,thresholdPixels:thresholdPx,
        snapEnabled:S.snapEnabled,frameRate:timelineFrameRate()
      });
      if(managed)return Object.assign({},managed.resolution,{operation:managed.operation,preview:managed.preview});
    }
    if(math&&typeof math.resolveRollGesture==='function')
      return math.resolveRollGesture(d.rightAt,deltaSeconds,d.leftDuration,d.rightDuration,candidates,S.pxPerSec,thresholdPx,S.snapEnabled,timelineFrameRate());
    var snap=findTimelineSnap(d.rightAt+deltaSeconds,excluded,thresholdPx);
    var proposed=snap?snap.at-d.rightAt:deltaSeconds;
    var delta=Math.max(-d.leftDuration+0.04,Math.min(d.rightDuration-0.04,proposed));
    var usedSnap=snap&&Math.abs(d.rightAt+delta-snap.at)<0.001?snap:null;
    return {targetAt:d.rightAt+delta,deltaSeconds:delta,snapCandidate:usedSnap};
  }
  function resolveTimelineSlide(d,deltaSeconds,thresholdPx){
    var excluded=[d.leftClipId,d.centerClipId,d.rightClipId],candidates=timelineSnapCandidates(excluded),math=timelineMath();
    var controller=window.filmStudioTimelineController;
    if(controller&&typeof controller.resolveSlideGesture==='function'){
      var managed=controller.resolveSlideGesture({
        leftClipId:d.leftClipId,centerClipId:d.centerClipId,rightClipId:d.rightClipId,
        centerAt:d.centerAt,rightAt:d.rightAt,leftDuration:d.leftDuration,centerDuration:d.centerDuration,rightDuration:d.rightDuration,
        rawDeltaSeconds:deltaSeconds,candidates:candidates,pixelsPerSecond:S.pxPerSec,
        thresholdPixels:thresholdPx,snapEnabled:S.snapEnabled,frameRate:timelineFrameRate()
      });
      if(managed)return Object.assign({},managed.resolution,{operation:managed.operation,preview:managed.preview});
    }
    if(math&&typeof math.resolveSlideGesture==='function')
      return math.resolveSlideGesture(d.centerAt,deltaSeconds,d.leftDuration,d.centerDuration,d.rightDuration,candidates,S.pxPerSec,thresholdPx,S.snapEnabled,timelineFrameRate());
    var snap=findTimelineSnap(d.centerAt+deltaSeconds,excluded,thresholdPx);
    var proposed=snap?snap.at-d.centerAt:deltaSeconds;
    var min=-Math.min(d.leftDuration,d.centerDuration)+0.04,max=Math.min(d.centerDuration,d.rightDuration)-0.04;
    var delta=Math.max(min,Math.min(max,proposed));
    var usedSnap=snap&&Math.abs(d.centerAt+delta-snap.at)<0.001?snap:null;
    return {targetAt:d.centerAt+delta,deltaSeconds:delta,snapCandidate:usedSnap};
  }
  function resolveTimelineSlip(rawDeltaSeconds, playbackSpeed, clipId){
    var math=timelineMath(),controller=window.filmStudioTimelineController;
    if(controller&&typeof controller.resolveSlipGesture==='function'){
      var managed=controller.resolveSlipGesture({
        clipId:clipId,rawDeltaSeconds:rawDeltaSeconds,playbackSpeed:playbackSpeed,
        snapEnabled:S.snapEnabled,frameRate:timelineFrameRate()
      });
      if(managed)return Object.assign({},managed.resolution,{operation:managed.operation,preview:managed.preview});
    }
    if(math&&typeof math.resolveSlipGesture==='function')
      return math.resolveSlipGesture(rawDeltaSeconds,playbackSpeed,S.snapEnabled,timelineFrameRate());
    var sourceDelta=num(rawDeltaSeconds,0)*num(playbackSpeed,1),fps=timelineFrameRate();
    if(S.snapEnabled)sourceDelta=Math.round(sourceDelta*fps)/fps;
    return {sourceDeltaSeconds:sourceDelta};
  }
  function showTimelineSnapGuide(at, kind){
    var timeline=$('fs-timeline'),bridge=window.filmStudioReactTimeline;
    if(bridge&&typeof bridge.setSnapGuide==='function'){
      bridge.setSnapGuide(at==null||!timeline?null:{
        at:Math.max(0,Number(at)||0),
        kind:String(kind||'时间点'),
        title:'吸附到 '+(kind||'时间点')+' · '+fmtTime(at)
      });
      return;
    }
    if(at==null||!timeline){var stale=$('fs-snap-guide');if(stale)stale.remove();return;}
    var guide=$('fs-snap-guide');
    if(!guide){guide=document.createElement('div');guide.id='fs-snap-guide';guide.className='fs-snap-guide';timeline.appendChild(guide);}
    else if(guide.parentElement!==timeline)timeline.appendChild(guide);
    guide.style.left=(132+timelineSecondsToPixels(Math.max(0,at),S.pxPerSec))+'px';
    guide.setAttribute('title','吸附到 '+(kind||'时间点')+' · '+fmtTime(at));
  }
  window.filmStudioToggleSnap=function(){
    S.snapEnabled=!S.snapEnabled;
    if(!reactOwnsTimelineToolbar()){
      document.querySelectorAll('[data-film-action="toggle-snap"]').forEach(function(button){
        button.setAttribute('aria-pressed',String(S.snapEnabled));
        button.textContent=S.snapEnabled?'吸附：开':'吸附：关';
      });
    }
    if(!S.snapEnabled)showTimelineSnapGuide(null);
    showStatus(S.snapEnabled?'时间线吸附已开启（阈值 8 屏幕像素）。':'时间线吸附已关闭。','success');
  };
  // Shared gesture logic; React owns event dispatch in the built Studio while
  // the same functions preserve behavior for the native fallback.
  window.filmStudioClickClip = function(id,e){
    if(e&&typeof e.stopPropagation==='function')e.stopPropagation();
    if(S.skipNextClipClick){S.skipNextClipClick=false;return;}
    if(selectClipWithModifiers(id,e)){
      var selected=findClip(id);
      if(selected){S.playhead=selected.at;setPlayheadVisual();previewCurrent();}
    }
  };
  function beginClipGesture(e,context,el){
        // A clip or envelope edit is one pointer-owned transaction. Do not let a
        // second pointer replace the active gesture or alter selection underneath it.
        if(S.drag||S.envelopeDrag){
          if(e&&typeof e.preventDefault==='function')e.preventDefault();
          if(e&&typeof e.stopPropagation==='function')e.stopPropagation();
          return false;
        }
        context=context||{};
        var clipId=String(context.clipId||(el&&el.getAttribute('data-clip'))||'');
        if(e.button!==0||context.targetIsButton)return;
        if(S.busy){e.preventDefault();showStatus('上一项编辑仍在保存，暂时不能开始下一次拖动。');return;}
        if(S.previewProposalActive){e.preventDefault();showStatus('AI 候选时间线预览期间，请先应用或拒绝提案，再直接编辑时间线。');return;}
        var clip=findClip(clipId);if(!clip)return;
        if(Object.prototype.hasOwnProperty.call(clip.attrs||{},'data-clip-locked')){
          selectClipWithModifiers(clip.id,e);
          S.playhead=clip.at;setPlayheadVisual();previewCurrent();renderInspector();renderAssets();
          e.preventDefault();showStatus('该片段已锁定，只能查看属性；如需编辑，请在属性栏显式解锁。');return;
        }
        var trimEdge=context.trimEdge==='start'||context.trimEdge==='end'?context.trimEdge:null,
            rollGesture=!!(trimEdge&&e.ctrlKey),
            mediaCanSlip=(clip.tag==='video'||clip.tag==='audio'),
            slideGesture=!!(!trimEdge&&!S.slipMode&&(S.slideMode||(e.altKey&&e.shiftKey))&&mediaCanSlip),
            slipGesture=!!(!trimEdge&&(S.slipMode||(e.altKey&&!e.shiftKey))&&mediaCanSlip),canDrag;
        if(rollGesture||slideGesture||slipGesture){S.selectedId=clip.id;S.selectedIds=[clip.id];S.selectionAnchor=clip.id;syncClipSelectionVisual();renderInspector();renderAssets();syncDirectorSelection();canDrag=true;}
        else{canDrag=selectClipWithModifiers(clip.id,e);}
        if(!canDrag){e.preventDefault();return;}
        var mode=trimEdge?(rollGesture?'roll':(trimEdge==='start'?'trim-start':'trim-end'))
          :(slideGesture?'slide':(slipGesture?'slip':'move'));
        // Moving a clip keeps the legacy seek-to-clip behavior. Editing a cut or source range
        // preserves the chosen playhead, so it remains a valid snap target during the gesture.
        if(mode==='move'){S.playhead=clip.at;setPlayheadVisual();previewCurrent();}
        if(mode!=='move'&&clip.duration==null){e.preventDefault();return;}
        var gestureBridge=window.filmStudioReactTimeline;
        var reactOwnsGestureSurface=!!(gestureBridge&&typeof gestureBridge.setGesturePreview==='function'&&typeof gestureBridge.setActiveDragIds==='function');
        var chosen=mode==='move'?(S.selectedIds||[clip.id]):[clip.id];
        if(mode==='move'){
          var blockedMember=null;
          chosen.some(function(id){
            var selectedClip=findClip(id);
            if(!selectedClip){blockedMember={kind:'片段',id:id,reason:'已不存在'};return true;}
            var selectedTrack=(S.data.tracks||[]).find(function(item){return item.id===selectedClip.track_id;});
            if(!selectedTrack){blockedMember={kind:'轨道',id:selectedClip.track_id||id,reason:'已不存在'};return true;}
            if(Object.prototype.hasOwnProperty.call(selectedTrack.attrs||{},'locked')){
              blockedMember={kind:'轨道',id:selectedTrack.id,reason:'已锁定'};return true;
            }
            if(Object.prototype.hasOwnProperty.call(selectedClip.attrs||{},'data-clip-locked')){
              blockedMember={kind:'片段',id:selectedClip.id,reason:'已锁定'};return true;
            }
            return false;
          });
          if(blockedMember){
            e.preventDefault();
            var blockedLabel=blockedMember.reason==='已锁定'?'锁定':'不存在的';
            showStatus('多选移动包含'+blockedLabel+blockedMember.kind+' '+blockedMember.id+'，整组移动已取消。','error');
            return;
          }
        }
        var trackOrder=(S.data.tracks||[]).map(function(t){return t.id;}),memberDomElements=Object.create(null);
        var members=chosen.map(function(id){
          var selected=findClip(id);if(!selected)return null;
          // Store only stable IDs and numeric edit baselines; DOM references stay outside FilmStore.
          var element=null;
          if(!reactOwnsGestureSurface)
            element=Array.from($('fs-timeline').querySelectorAll('[data-clip]')).find(function(node){return node.getAttribute('data-clip')===id;});
          if(element)memberDomElements[id]=element;
          return selected&&(reactOwnsGestureSurface||element)?{id:id,originalAt:selected.at,originalEnd:selected.end==null?selected.at+num(selected.duration,2):selected.end,
            trackId:selected.track_id,trackIndex:trackOrder.indexOf(selected.track_id)}:null;
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
          var leftElement=null,rightElement=null;
          if(!reactOwnsGestureSurface){
            var elements=Array.from($('fs-timeline').querySelectorAll('[data-clip]'));
            leftElement=elements.find(function(n){return n.getAttribute('data-clip')===leftClip.id;});
            rightElement=elements.find(function(n){return n.getAttribute('data-clip')===rightClip.id;});
          }
          if(!reactOwnsGestureSurface&&(!leftElement||!rightElement)){showStatus('无法定位滑移式修剪的邻接片段。','error');return;}
          gestureDomRefs=reactOwnsGestureSurface?null:{clipElement:el,memberElements:memberDomElements,
            slideElements:[leftElement,el,rightElement],rollElements:null,targetRow:null};
          S.drag={id:clip.id,mode:'slide',pointerId:e.pointerId,startX:e.clientX,startY:e.clientY,
            leftClipId:leftClip.id,centerClipId:clip.id,rightClipId:rightClip.id,
            leftDuration:leftClip.duration,centerDuration:clip.duration,rightDuration:rightClip.duration,
            centerAt:clip.at,rightAt:rightClip.at,startScrollLeft:$('fs-timeline-scroll').scrollLeft,active:false,
            members:members,targetTrackId:clip.track_id,trackOrder:trackOrder};
        }else if(mode==='roll'){
          var trackData=(S.data.tracks||[]).find(function(t){return t.id===clip.track_id;});
          var ordered=(trackData&&trackData.clips||[]).slice().sort(function(a,b){return a.at-b.at;});
          var index=ordered.findIndex(function(item){return item.id===clip.id;}),leftClip=null,rightClip=null;
          var edge=trimEdge;
          if(edge==='end'&&index>=0&&index+1<ordered.length&&clip.end!=null&&Math.abs(clip.end-ordered[index+1].at)<=0.001){leftClip=clip;rightClip=ordered[index+1];}
          else if(edge==='start'&&index>0&&ordered[index-1].end!=null&&Math.abs(ordered[index-1].end-clip.at)<=0.001){leftClip=ordered[index-1];rightClip=clip;}
          else{showStatus('Ctrl 拖动切口只支持同轨相邻片段共用的切口。','error');e.preventDefault();return;}
          var leftElement=null,rightElement=null;
          if(!reactOwnsGestureSurface){
            var elements=Array.from($('fs-timeline').querySelectorAll('[data-clip]'));
            leftElement=elements.find(function(n){return n.getAttribute('data-clip')===leftClip.id;});
            rightElement=elements.find(function(n){return n.getAttribute('data-clip')===rightClip.id;});
          }
          if(!reactOwnsGestureSurface&&(!leftElement||!rightElement)){showStatus('无法定位切口两侧片段。','error');return;}
          gestureDomRefs=reactOwnsGestureSurface?null:{clipElement:el,memberElements:memberDomElements,
            slideElements:null,rollElements:[leftElement,rightElement],targetRow:null};
          S.drag={id:clip.id,mode:'roll',pointerId:e.pointerId,startX:e.clientX,startY:e.clientY,
            leftClipId:leftClip.id,rightClipId:rightClip.id,rightAt:rightClip.at,
            leftDuration:leftClip.duration,rightDuration:rightClip.duration,
            startScrollLeft:$('fs-timeline-scroll').scrollLeft,active:false,members:members,
            targetTrackId:clip.track_id,trackOrder:trackOrder};
        }else{
          if(mode!=='move')members=members.filter(function(m){return m.id===clip.id;});
          gestureDomRefs=reactOwnsGestureSurface?null:{clipElement:el,memberElements:memberDomElements,
            slideElements:null,rollElements:null,targetRow:null};
          var trimSourceMatch=String((clip.attrs||{}).src||'').match(/#t=([0-9]+(?:\.[0-9]+)?)(?:,|$)/);
          S.drag={id:clip.id,mode:mode,pointerId:e.pointerId,startX:e.clientX,startY:e.clientY,
            originalAt:clip.at,originalEnd:clip.end==null?clip.at+num(clip.duration,2):clip.end,trackId:clip.track_id,
            originalSourceIn:trimSourceMatch?Number(trimSourceMatch[1]):0,
            originalSpeed:(clip.tag==='video'||clip.tag==='audio')?num((clip.attrs||{}).speed,1):1,
            clipTag:clip.tag,
            originalTrackIndex:trackOrder.indexOf(clip.track_id),trackOrder:trackOrder,
            startScrollLeft:$('fs-timeline-scroll').scrollLeft,active:false,members:members,
            targetTrackId:clip.track_id};
        }
        var reactOwnsActiveDrag=setTimelineActiveDragIds(members.map(function(member){return member.id;}));
        if(!reactOwnsActiveDrag)members.forEach(function(member){
          var element=gestureDomRefs&&gestureDomRefs.memberElements[member.id];
          if(element)element.classList.add('fs-dragging');
        });
        if(el&&el.setPointerCapture)try{el.setPointerCapture(e.pointerId);}catch(_){}
        e.preventDefault();
    return Boolean(S.drag&&S.drag.id===clip.id&&S.drag.pointerId===e.pointerId&&Number.isInteger(e.pointerId));
  }
  window.filmStudioBeginClipGesture=function(e,context){
    return beginClipGesture(e,context,null);
  };
  window.filmStudioBeginClipDrag = function(e,el){
    if(!el)return false;
    var target=e&&e.target,trim=target&&target.closest?target.closest('[data-trim]'):null;
    return beginClipGesture(e,{
      clipId:el.getAttribute('data-clip'),
      trimEdge:trim?trim.getAttribute('data-trim'):null,
      targetIsButton:!!(target&&target.closest&&target.closest('button'))
    },el);
  };

  window.filmStudioToggleTrackLock = function(trackId){
    var track=(S.data&&S.data.tracks||[]).find(function(item){return item.id===String(trackId);});
    if(!track)return Promise.resolve(false);
    var locked=Object.prototype.hasOwnProperty.call(track.attrs||{},'locked');
    return filmStudioCommit([{op:'track',track_id:track.id,field:'locked',value:!locked}]);
  };
  window.filmStudioToggleTrackFlag = function(trackId,field){
    if(['sync-lock','hidden','muted'].indexOf(field)<0){
      showStatus('不支持的轨道标志：'+String(field),'error');
      return Promise.resolve(false);
    }
    var track=(S.data&&S.data.tracks||[]).find(function(item){return item.id===String(trackId);});
    if(!track)return Promise.resolve(false);
    var enabled=Object.prototype.hasOwnProperty.call(track.attrs||{},field);
    return filmStudioCommit([{op:'track',track_id:track.id,field:field,value:!enabled}]);
  };
  function renderTimeline() {
    if(!S.data||!S.data.exists)return;
    var reactTimeline=window.filmStudioReactTimeline&&typeof window.filmStudioReactTimeline.renderSurface==='function';
    if(reactTimeline){
      var timelineBridge=window.filmStudioReactTimeline;
      // React must remount after migration/error placeholders were written outside its root.
      // Replace those placeholder children only when the React subtree is known to be unmounted.
      if(timelineBridge&&typeof timelineBridge.hasSurface==='function'&&!timelineBridge.hasSurface()){
        var timelineHost=$('fs-timeline');
        if(timelineHost)timelineHost.replaceChildren();
      }
      timelineBridge.renderSurface();
    }else{
      var laneWidth=Math.max(720,Math.ceil(timelineSecondsToPixels(S.duration,S.pxPerSec))),ticks=Math.min(240,Math.ceil(S.duration/5));
      var ruler='<div class="fs-ruler" style="width:'+(132+laneWidth)+'px"><div class="fs-ruler-head">时码</div><div class="fs-ruler-body" id="fs-ruler-body" style="width:'+laneWidth+'px">';
      for(var i=0;i<=ticks;i++){var sec=Math.min(S.duration,i*5);ruler+='<div class="fs-ruler-mark" data-at="'+sec+'" style="left:'+(timelineSecondsToPixels(sec,S.pxPerSec))+'px">'+esc(fmtTime(sec))+'</div>';}
      ruler+='</div></div>';
      var body=S.data.tracks.map(function(track){
        var attrs=track.attrs||{},locked=Object.prototype.hasOwnProperty.call(attrs,'locked'),
            syncLocked=Object.prototype.hasOwnProperty.call(attrs,'sync-lock'),
            hidden=Object.prototype.hasOwnProperty.call(attrs,'hidden'),muted=Object.prototype.hasOwnProperty.call(attrs,'muted'),
            label=attrs['data-label']||track.id;
        var lane='<div class="fs-track-body" style="width:'+laneWidth+'px">';
        (track.clips||[]).forEach(function(clip){
          var clipLocked=Object.prototype.hasOwnProperty.call(clip.attrs||{},'data-clip-locked');
          var width=timelineClipWidth(clip.duration,S.pxPerSec,24,2,0,false),left=timelineClipLeft(clip.at,S.pxPerSec),title=(clipLocked?'🔒 ':'')+(clip.attrs.title||clip.id);
          var aid=clip.attrs['data-asset-id']||'',thumb=aid&&clip.tag!=='audio'?'/api/asset_thumbnail?asset_id='+encodeURIComponent(aid)+'&project_id='+encodeURIComponent(S.projectId):'';
          var thumbHtml=thumb?'<img class="fs-clip-thumb" src="'+esc(thumb)+'" loading="lazy" alt="" onerror="this.remove()">':'';
          var waveformHtml=clip.tag==='audio'?'<canvas class="fs-clip-waveform" data-waveform-clip="'+esc(clip.id)+'" aria-label="音频波形"></canvas>':'';
          var envelopeHtml=(clip.tag==='audio'||clip.tag==='video')&&clip.duration!=null?'<canvas class="fs-clip-envelope" data-envelope-clip="'+esc(clip.id)+'" aria-label="音量包络：拖动添加或调整关键帧；Shift 或右键删除内部点"></canvas>':'';
          var keyframeHtml=timelineKeyframeMarks(clip).map(function(mark){
            var position=clip.duration>0?Math.max(0,Math.min(100,mark.at/clip.duration*100)):0;
            return '<button type="button" class="fs-clip-keyframe-marker" data-film-keyframe-marker="'+esc(clip.id)+'" data-keyframe-at="'+mark.at+'" style="left:'+position+'%" title="关键帧 '+mark.at.toFixed(3)+'s · '+esc(mark.properties.join(', '))+'" aria-label="跳转到 '+mark.at.toFixed(3)+' 秒关键帧"></button>';
          }).join('');
          var handles=clip.duration==null||clipLocked?'':'<span class="fs-trim-handle fs-trim-left" data-trim="start" title="修剪入点"></span><span class="fs-trim-handle fs-trim-right" data-trim="end" title="修剪出点"></span>';
          lane+='<div class="fs-clip '+(clipLocked?'fs-clip-locked ':'')+((S.selectedIds||[]).indexOf(clip.id)>=0?'fs-clip-selected ':'')+'" data-clip="'+esc(clip.id)+'" data-track="'+esc(track.id)+'" data-kind="'+esc(clip.tag)+'" style="left:'+left+'px;width:'+width+'px" title="'+esc(title)+'">'+thumbHtml+waveformHtml+envelopeHtml+keyframeHtml+handles+'<span class="fs-clip-title">'+esc(title)+'</span><span class="fs-clip-subtitle">'+esc(fmtTime(clip.at))+(clip.duration==null?'':' · '+clip.duration.toFixed(2)+'s')+'</span></div>';
        });
        lane+='</div>';
        return '<div class="fs-track '+(locked?'fs-track-locked ':'')+(syncLocked?'fs-sync-locked ':'')+(hidden?'fs-hidden-track ':'')+(muted?'fs-muted-track':'')+'" data-track="'+esc(track.id)+'"><div class="fs-track-head"><span class="fs-track-label" title="'+esc(label)+'">'+esc(label)+'</span><span class="fs-track-controls"><button type="button" class="fs-btn" data-track-lock="'+esc(track.id)+'" title="'+(locked?'解锁轨道':'锁定轨道')+'">'+(locked?'🔒':'锁')+'</button><button type="button" class="fs-btn" data-track-flag="sync-lock" data-track-key="'+esc(track.id)+'" aria-pressed="'+(syncLocked?'true':'false')+'" title="'+(syncLocked?'取消同步锁定':'启用同步锁定')+'">'+(syncLocked?'⇄':'同步')+'</button><button type="button" class="fs-btn" data-track-flag="hidden" data-track-key="'+esc(track.id)+'" title="'+(hidden?'显示轨道':'隐藏轨道')+'">'+(hidden?'显':'隐')+'</button><button type="button" class="fs-btn" data-track-flag="muted" data-track-key="'+esc(track.id)+'" title="'+(muted?'取消静音':'静音轨道')+'">'+(muted?'🔊':'🔇')+'</button></span></div>'+lane+'</div>';
      }).join('');
      $('fs-timeline').innerHTML=ruler+body+'<div class="fs-playhead" id="fs-playhead-line"></div>';
    }
    if(!reactTimeline)$('fs-timeline').style.width=(132+laneWidth)+'px';
    $('fs-timeline-meta').textContent=fmtTime(S.duration)+' · '+S.data.tracks.length+' 轨';
    var timelineRoot=$('fs-timeline');
    var reactOwnsTimeline=!!(window.filmStudioReactTimeline&&typeof window.filmStudioReactTimeline.renderSurface==="function");
    if(timelineRoot&&!reactOwnsTimeline&&!timelineRoot.__filmStudioKeyframeDelegationBound){
      // Native click delegation is only the no-bundle fallback. The verified React
      // surface handles marker pointer/click itself and must not be shadowed by
      // capture-phase listeners on its Portal host.
      timelineRoot.__filmStudioKeyframeDelegationBound=true;
      timelineRoot.addEventListener('click',function(event){
        var marker=event.target&&event.target.closest?event.target.closest('[data-film-keyframe-marker]'):null;
        if(!marker)return;
        event.preventDefault();event.stopPropagation();
        window.filmStudioJumpToKeyframe(
          marker.getAttribute('data-film-keyframe-marker'),
          Number(marker.getAttribute('data-keyframe-at')||0)
        );
      });
    }
    $('fs-timeline').querySelectorAll('[data-clip]').forEach(function(el){
      // React owns clip selection and gesture initiation when the verified bundle
      // is present; the native binding remains only for the no-bundle fallback.
      if(el.__filmStudioClipBound)return;
      el.__filmStudioClipBound=true;
      if(!reactTimeline){
        el.addEventListener('click',function(e){
          window.filmStudioClickClip(el.getAttribute('data-clip'),e);
        });
        el.addEventListener('pointerdown',function(e){
          window.filmStudioBeginClipDrag(e,el);
        });
      }
    });
    if(!reactTimeline){
    $('fs-timeline').querySelectorAll('[data-track-lock]').forEach(function(el){
      if(el.__filmStudioTrackLockBound)return;
      el.__filmStudioTrackLockBound=true;
      el.addEventListener('click',function(e){
        e.stopPropagation();var track=(S.data.tracks||[]).find(function(t){return t.id===el.getAttribute('data-track-lock');});if(!track)return;
        var locked=Object.prototype.hasOwnProperty.call(track.attrs||{},'locked');
        filmStudioCommit([{op:'track',track_id:track.id,field:'locked',value:!locked}]);
      });
    });
    $('fs-timeline').querySelectorAll('[data-track-flag]').forEach(function(el){
      if(el.__filmStudioTrackFlagBound)return;
      el.__filmStudioTrackFlagBound=true;
      el.addEventListener('click',function(e){
        e.stopPropagation();
        var track=(S.data.tracks||[]).find(function(t){return t.id===el.getAttribute('data-track-key');});
        if(!track)return;
        var field=el.getAttribute('data-track-flag'),on=Object.prototype.hasOwnProperty.call(track.attrs||{},field);
        filmStudioCommit([{op:'track',track_id:track.id,field:field,value:!on}]);
      });
    });
    }
    if(!reactTimeline){
    $('fs-ruler-body').querySelectorAll('[data-at]').forEach(function(el){
      if(el.__filmStudioRulerBound)return;
      el.__filmStudioRulerBound=true;
      el.addEventListener('click',function(){window.filmStudioSeek(el.getAttribute('data-at'));});
    });
    }
    setPlayheadVisual();
    renderAudioWaveforms();
  }
  var fallbackWaveformSources=Object.create(null);
  function readTimelineWaveformSources(){
    var bridge=window.filmStudioStore;
    if(bridge&&typeof bridge.getField==='function'&&typeof bridge.setField==='function')
      return bridge.getField('timelineWaveformSources')||{};
    return fallbackWaveformSources;
  }
  function writeTimelineWaveformSource(key,source){
    var bridge=window.filmStudioStore;
    var current=readTimelineWaveformSources(),next={},keys;
    Object.keys(current).forEach(function(existingKey){
      if(existingKey!==key)next[existingKey]=current[existingKey];
    });
    next[key]=source;
    keys=Object.keys(next);
    while(keys.length>8){delete next[keys.shift()];}
    if(bridge&&typeof bridge.getField==='function'&&typeof bridge.setField==='function')
      bridge.setField('timelineWaveformSources',next);
    else fallbackWaveformSources=next;
    return readTimelineWaveformSources()[key]||source;
  }
  async function decodeAudioWaveform(url){
    var key=String(url||'').split('#',1)[0];
    var cachedSources=readTimelineWaveformSources();
    if(cachedSources[key])return cachedSources[key];
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
        return writeTimelineWaveformSource(key,result);
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
      var reactTimeline=window.filmStudioReactTimeline&&typeof window.filmStudioReactTimeline.renderSurface==='function';
      if(reactTimeline)return;
      var width=timelineClipWidth(clip.duration||1,S.pxPerSec,16,1,1800,true);
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
      var reactTimeline=window.filmStudioReactTimeline&&typeof window.filmStudioReactTimeline.renderSurface==='function';
      if(canvas.isConnected&&!reactTimeline)canvas.title='波形暂不可用：'+String(error&&error.message||error);
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
    var width=timelineClipWidth(clip.duration,S.pxPerSec,24,1,1800,true);
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
  function beginVolumeEnvelopeGesture(e,surface,legacyCanvas){
    // Envelope-point edits and clip moves share one transaction slot.
    if(S.drag||S.envelopeDrag){
      if(e&&typeof e.preventDefault==='function')e.preventDefault();
      if(e&&typeof e.stopPropagation==='function')e.stopPropagation();
      return false;
    }
    var canvas=legacyCanvas||(surface&&typeof surface.getAttribute==='function'?surface:null);
    var context=canvas?{clipId:canvas.getAttribute('data-envelope-clip')}:surface||{};
    var clip=findClip(String(context.clipId||''));
    if(!clip||clip.duration==null||clip.duration<=0)return false;
    if(e.button!==0&&e.button!==2)return false;
    e.preventDefault();e.stopPropagation();
    if(S.busy||S.broken){showStatus('当前无法编辑音量包络。','error');return false;}
    var duration=clip.duration,sourceRect=canvas?canvas.getBoundingClientRect():context.rect;
    if(!sourceRect)return false;
    var rect={left:Number(sourceRect.left),top:Number(sourceRect.top),width:Number(sourceRect.width),height:Number(sourceRect.height)};
    if(![rect.left,rect.top,rect.width,rect.height].every(Number.isFinite))return false;
    if(duration<=0.02){showStatus('片段短于 0.02 秒，无法安全添加内部音量关键帧。','error');return false;}
    if(!rect.width||!rect.height)return false;
    var math=timelineMath(),points=envelopePointsForClip(clip).map(function(p){return {t:Number(p.t),v:Number(p.v)};}),index=-1,added=false;
    if(math&&typeof math.resolveEnvelopePointerDown==='function'){
      var resolution=math.resolveEnvelopePointerDown(points,duration,rect.width,rect.height,
        e.clientX-rect.left,e.clientY-rect.top,e.button,!!e.shiftKey);
      if(resolution.kind==='none')return false;
      if(resolution.kind==='minimum-points'){showStatus('至少需要两个音量关键帧。','error');return false;}
      if(resolution.kind==='remove'){
        filmStudioCommit([{op:'set',clip:clip.id,field:'volume_envelope',value:JSON.stringify(resolution.points)}]);
        return false;
      }
      points=resolution.points;index=resolution.index;added=resolution.added;
    }else{
      var time=Math.max(0,Math.min(duration,(e.clientX-rect.left)/rect.width*duration));
      var value=Math.max(0,Math.min(1,1-((e.clientY-rect.top)-4)/Math.max(1,rect.height-8)));
      var nearest=-1,nearestPx=Infinity;
      points.forEach(function(p,i){
        if(p.t<=0.0001||p.t>=duration-0.0001)return;
        var px=Math.abs(p.t/duration*rect.width-(e.clientX-rect.left));
        if(px<nearestPx){nearestPx=px;nearest=i;}
      });
      if((e.button===2||e.shiftKey)&&nearest>=0&&nearestPx<=12){
        points.splice(nearest,1);
        if(points.length<2){showStatus('至少需要两个音量关键帧。','error');return false;}
        filmStudioCommit([{op:'set',clip:clip.id,field:'volume_envelope',value:JSON.stringify(points)}]);
        return false;
      }
      if(e.button!==0)return false;
      points.forEach(function(p,i){
        var px=Math.abs(p.t/duration*rect.width-(e.clientX-rect.left));
        var renderedY=rect.height-4-p.v*(rect.height-8);
        var py=Math.abs(renderedY-(e.clientY-rect.top));
        if(px<=10&&py<=10){index=i;nearestPx=px;}
      });
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
  
    }
    // The Store receives only the stable clip/pointer IDs, numeric rect, and keyframe snapshot.
    // Legacy canvas references are isolated to the native fallback closure.
    envelopeDomCanvas=canvas||null;
    S.envelopeDrag={clipId:clip.id,pointerId:e.pointerId,rect:rect,points:points,index:index,
      duration:duration,active:added,changed:added};
    if(canvas&&canvas.setPointerCapture)try{canvas.setPointerCapture(e.pointerId);}catch(_){}
    if(!setTimelineEnvelopePreview(clip.id,points)&&envelopeDomCanvas)drawVolumeEnvelope(envelopeDomCanvas,points);
    return true;
  }
  window.filmStudioBeginEnvelopeGesture=function(e,context){
    if(S.previewProposalActive){showStatus('AI 候选时间线预览期间，请先应用或拒绝提案，再编辑音量曲线。');return false;}
    return beginVolumeEnvelopeGesture(e,context,null);
  };
  function bindVolumeEnvelopeCanvas(canvas){
    var initialClip=findClip(canvas.getAttribute('data-envelope-clip'));
    if(!initialClip||initialClip.duration==null||initialClip.duration<=0)return;
    if(canvas.__filmStudioEnvelopeBound)return;
    canvas.__filmStudioEnvelopeBound=true;
    if(window.filmStudioReactTimeline&&typeof window.filmStudioReactTimeline.renderSurface==='function')return;
    canvas.addEventListener('contextmenu',function(e){e.preventDefault();});
    canvas.addEventListener('pointerdown',function(e){beginVolumeEnvelopeGesture(e,canvas);});
  }
  window.filmStudioSetEnvelopeMode=function(enabled){
    S.envelopeMode=!!enabled;
    if(S.envelopeMode){S.slideMode=false;S.slipMode=false;}
    var panel=$('film-studio-panel'),reactToolbar=reactOwnsTimelineToolbar(),
        button=reactToolbar?null:document.querySelector('[data-film-action="envelope-mode"]'),
        slideButton=reactToolbar?null:document.querySelector('[data-film-action="slide-mode"]'),
        slipButton=reactToolbar?null:document.querySelector('[data-film-action="slip-mode"]');
    if(panel&&!reactToolbar){
      panel.classList.toggle('fs-envelope-mode',S.envelopeMode);
      panel.classList.toggle('fs-slide-mode',S.slideMode);
      panel.classList.toggle('fs-slip-mode',S.slipMode);
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
    if(slipButton){
      slipButton.classList.toggle('fs-slip-mode-active',S.slipMode);
      slipButton.textContent=S.slipMode?'源区间滑移：开':'源区间滑移：关';
      slipButton.setAttribute('aria-pressed',String(S.slipMode));
    }
    showStatus(S.envelopeMode
      ?'音量曲线模式：点击曲线添加关键帧；拖动关键帧调整；Shift 或右键点击内部点删除。切回“音量曲线：关”即可正常移动片段。'
      :'普通剪辑模式已恢复；片段可直接拖动、修剪和切分。');
  };
  window.filmStudioToggleEnvelopeMode=function(){window.filmStudioSetEnvelopeMode(!S.envelopeMode);};
  window.filmStudioSetSlideMode=function(enabled){
    S.slideMode=!!enabled;
    if(S.slideMode){S.envelopeMode=false;S.slipMode=false;}
    var panel=$('film-studio-panel'),reactToolbar=reactOwnsTimelineToolbar(),
        button=reactToolbar?null:document.querySelector('[data-film-action="slide-mode"]'),
        envelopeButton=reactToolbar?null:document.querySelector('[data-film-action="envelope-mode"]'),
        slipButton=reactToolbar?null:document.querySelector('[data-film-action="slip-mode"]');
    if(panel&&!reactToolbar){
      panel.classList.toggle('fs-slide-mode',S.slideMode);
      panel.classList.toggle('fs-envelope-mode',S.envelopeMode);
      panel.classList.toggle('fs-slip-mode',S.slipMode);
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
    if(slipButton){
      slipButton.classList.toggle('fs-slip-mode-active',S.slipMode);
      slipButton.textContent=S.slipMode?'源区间滑移：开':'源区间滑移：关';
      slipButton.setAttribute('aria-pressed',String(S.slipMode));
    }
    showStatus(S.slideMode
      ?'滑移修剪模式已开启：拖动视频/音频片段主体，片段长度不变并联动左右相邻片段。点击按钮关闭；Alt+Shift 拖动也可直接使用。'
      :'普通剪辑模式已恢复；片段可直接拖动、修剪和切分。');
  };
  window.filmStudioToggleSlideMode=function(){window.filmStudioSetSlideMode(!S.slideMode);};
  window.filmStudioSetSlipMode=function(enabled){
    S.slipMode=!!enabled;
    if(S.slipMode){S.slideMode=false;S.envelopeMode=false;}
    var panel=$('film-studio-panel'),reactToolbar=reactOwnsTimelineToolbar(),
        button=reactToolbar?null:document.querySelector('[data-film-action="slip-mode"]'),
        slideButton=reactToolbar?null:document.querySelector('[data-film-action="slide-mode"]'),
        envelopeButton=reactToolbar?null:document.querySelector('[data-film-action="envelope-mode"]');
    if(panel&&!reactToolbar){
      panel.classList.toggle('fs-slip-mode',S.slipMode);
      panel.classList.toggle('fs-slide-mode',S.slideMode);
      panel.classList.toggle('fs-envelope-mode',S.envelopeMode);
    }
    if(button){
      button.classList.toggle('fs-slip-mode-active',S.slipMode);
      button.textContent=S.slipMode?'源区间滑移：开':'源区间滑移：关';
      button.setAttribute('aria-pressed',String(S.slipMode));
    }
    if(slideButton){
      slideButton.classList.toggle('fs-slide-mode-active',S.slideMode);
      slideButton.textContent=S.slideMode?'滑移修剪：开':'滑移修剪：关';
      slideButton.setAttribute('aria-pressed',String(S.slideMode));
    }
    if(envelopeButton){
      envelopeButton.classList.toggle('fs-envelope-mode-active',S.envelopeMode);
      envelopeButton.textContent=S.envelopeMode?'音量曲线：开':'音量曲线：关';
      envelopeButton.setAttribute('aria-pressed',String(S.envelopeMode));
    }
    showStatus(S.slipMode
      ?'源区间滑移模式：拖动视频/音频片段主体，只改变素材源入点和源出点，不移动片段、不改变片段时长。Alt 拖动仍可作为快捷手势。'
      :'普通剪辑模式已恢复；片段可直接拖动、修剪和切分。');
  };
  window.filmStudioToggleSlipMode=function(){window.filmStudioSetSlipMode(!S.slipMode);};
  function renderVolumeEnvelopes(){
    var timeline=$('fs-timeline');if(!timeline)return;
    var reactTimeline=window.filmStudioReactTimeline&&typeof window.filmStudioReactTimeline.renderSurface==='function';
    if(reactTimeline)return;
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
      if(window.filmStudioReactInspector&&typeof window.filmStudioReactInspector.renderClip==='function')window.filmStudioReactInspector.renderClip();
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
    fields+='<label class="fs-inspector-field">片段标题<input id="fs-field-title" type="text" maxlength="200" value="'+esc(a.title||'')+'"></label>';
    if(clip.tag!=='audio'&&clip.duration!=null){
      fields+='<section class="fs-keyframe-editor fs-keyframe-fallback-editor"><h4>视觉关键帧</h4>'+
        '<p class="fs-inspector-description">时间使用片段相对秒数。可拖动曲线节点、编辑时间和值、添加/删除节点；保存仍经 FilmDocument 版本校验。</p>'+
        '<div id="fs-keyframe-fallback-controls"></div>'+
        '<details class="fs-keyframe-fallback"><summary>高级 JSON</summary>'+
        '<textarea id="fs-field-keyframes" rows="7" spellcheck="false" aria-label="视觉关键帧 JSON">'+esc(a['data-film-keyframes']||'')+'</textarea></details></section>';
    }
    fields+='</div><div class="fs-inspector-description fs-inspector-section">更改经统一编辑 API 写入 film.html。锁定轨道会拒绝修改；同轨片段不可重叠。</div>';
    var element=S.selectedElement&&S.selectedElement.clipId===clip.id?S.selectedElement:null;
    if(window.filmStudioReactInspector&&typeof window.filmStudioReactInspector.renderClip==='function'){
      window.filmStudioReactInspector.renderClip();
      renderElementInspector(element);
      return;
    }
    $('fs-inspector').innerHTML=fields;
    if(clip.tag!=='audio'&&clip.duration!=null){
      renderFallbackKeyframeControls(clip);
      bindFallbackKeyframeEditor(clip);
    }
    var clipIsLocked=Boolean(clip.trackLocked)||Object.prototype.hasOwnProperty.call(clip.attrs||{},'data-clip-locked');
    if(clipIsLocked){
      $('fs-inspector').querySelectorAll('input,select,textarea,button').forEach(function(control){control.disabled=true;});
      var lockMessage=document.createElement('div');lockMessage.className='fs-alert fs-visible fs-error';lockMessage.textContent=clip.trackLocked?'所在轨道已锁定；解锁后才能编辑该片段。':'该片段来自已锁定图层，只读保留其原始时间线位置与画面。';
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
    var elementBox=$('fs-element-selection');
    if(S.selectedElement&&available.indexOf(S.selectedElement.clipId)<0)S.selectedElement=null;
    // The selection outline lives outside React; always project the authoritative
    // selection state so a store reset cannot leave a floating stale rectangle.
    if(!S.selectedElement&&elementBox)elementBox.hidden=true;
    S.selectedIds=(S.selectedIds||[]).filter(function(id){return available.indexOf(id)>=0;});
    if(!findClip(S.selectedId)){var first=clips().filter(function(c){return c.tag!=='audio';})[0];S.selectedId=first?first.id:'';}
    if(S.selectedIds.indexOf(S.selectedId)<0&&S.selectedId)S.selectedIds.push(S.selectedId);
    if(!S.selectedIds.length&&S.selectedId)S.selectedIds=[S.selectedId];
    renderAssets();renderTimeline();renderInspector();setPlayheadVisual();previewCurrent();
    var migrationWarnings=S.data.migration&&S.data.migration.warnings||[];
    var filmWarnings=S.data.warnings||[];
    if(migrationWarnings.length)showStatus('迁移警告：'+migrationWarnings.join('；'),'');
    else if(filmWarnings.length)showStatus('影片格式提示：'+filmWarnings.join('；'),'');
    else{var el=$('film-studio-alert');el.className='fs-alert';el.textContent='';}
    syncDirectorSelection();
  }
  function renderBrokenFilmState(result){
    mountDirectorPlanning(false);
    var message=String(result&&result.error||'film.html 无法读取或解析。');
    S.broken=true;
    S.data={exists:true,project_id:S.projectId,tracks:[],duration:10,fps:30,
      stage:{width:1920,height:1080},warnings:[]};
    S.rev=String(result&&result.rev||'');
    S.runtimeRev='';S.runtimeCommand='';S.migration={
      migration_available:false,
      migration_error:'film.html 存在但无效；旧 CompositionIR 时间线已被权威门禁禁用。请修复 film.html 后重新载入。'+message,
      migration_error_code:String(result&&result.code||'INVALID_FILM'),
      migration_preview:{warnings:[]},variants:[]
    };
    S.selectedId='';S.selectedIds=[];S.selectionAnchor='';
    S.selectedElement=null;
    var elementBox=$('fs-element-selection');if(elementBox)elementBox.hidden=true;
    clearFilmProposalTimelinePreview();
    S.proposal=null;S.previewProposalActive=false;
    renderMigration();
    $('fs-project-label').textContent=S.projectId+' · film.html 无效';
    $('fs-clip-count').textContent='0';
    $('fs-timeline-meta').textContent='影片文档无效';
    $('fs-assets-list').innerHTML='<div class="fs-empty">film.html 存在但无法使用。旧时间线已禁用；修复文档后点“重新载入”。</div>';
    $('fs-timeline').innerHTML='<div class="fs-empty">剪辑已暂停：film.html 校验失败。不会加载或写入旧 CompositionIR 时间线。</div>';
    showStatus('影片文档无效，已阻止回退到旧时间线。'+message,'error');
    syncDirectorSelection();
  }
  async function filmStudioLoad(force,knownFilmDocument) {
    if(!S.open&&!force)return false;
    var selector=$('editor-project'),nextId=(selector&&selector.value)||(typeof ED!=='undefined'&&ED.pid)||S.projectId;
    if(!nextId){showStatus('请先在工作台顶部选择并打开一个可编辑项目。','error');return false;}
    if(S.busy){showStatus('上一项影片操作仍在保存；本次项目切换未执行。','error');return false;}
    var changed=nextId!==S.projectId;
    if(changed&&S.rendering){
      if(selector&&S.projectId)selector.value=S.projectId;
      showStatus('当前项目正在导出；为避免混淆导出结果与另一条时间线，导出结束前不能切换项目。','error');
      return false;
    }
    if(changed&&(S.drag||S.envelopeDrag)){
      if(selector&&S.projectId)selector.value=S.projectId;
      showStatus('时间线手势尚未结束；本次项目切换未执行。请先完成或取消拖动。','error');
      return false;
    }
    if(changed&&hasUncommittedInspectorEdits()){
      if(selector&&S.projectId)selector.value=S.projectId;
      showStatus('当前项目的 Inspector 有未提交草稿。请先应用属性，或重新载入当前项目并确认丢弃，再切换项目。','error');
      return false;
    }
    if(knownFilmDocument&&knownFilmDocument.project_id&&String(knownFilmDocument.project_id)!==String(nextId)){
      showStatus('影片预检结果属于另一个项目；未切换时间线。请刷新后重试。','error');
      return false;
    }
    if(changed){
      mountDirectorPlanning(false);
      // Chat messages and streaming metadata are project context, not global UI
      // history. Do not republish the previous project's conversation after load.
      S.assetMode='clips';
      S.messages=[];
      S.aiActiveMessage='';S.aiStreamText='';S.aiPendingSteer='';
      S.aiLiveMessageIndex=-1;S.aiRequestId='';S.aiController=null;
      S.data=null;S.rev='';S.runtimeRev='';S.runtimeCommand='';S.migration=null;
      S.selectedId='';S.selectedIds=[];S.selectionAnchor='';S.selectedElement=null;
      var elementBox=$('fs-element-selection');if(elementBox)elementBox.hidden=true;
      clearFilmProposalTimelinePreview();
      S.proposal=null;S.previewProposalActive=false;S.broken=false;setPlaybackState(false);
    }
    S.projectId=nextId;
    // The project transition intentionally clears aiDirectorModel. Re-render the
    // empty, new-project chat immediately so the React portal is not left unmounted
    // while the authoritative film.html request is in flight.
    if(changed)renderAiDirector();
    var requestProjectId=nextId;
    var requestGeneration=++S.loadGeneration;
    var r=knownFilmDocument||null;
    if(!r){
      try{r=await apiGet('/api/editor/film?project_id='+encodeURIComponent(requestProjectId));}
      catch(error){
        if(requestProjectId===S.projectId&&requestGeneration===S.loadGeneration){
          if(changed){S.data=null;S.rev='';S.runtimeRev='';S.migration=null;}
          showStatus('读取影片文档失败；为防止覆盖，不写入文件。'+String(error&&error.message||error),'error');
        }
        return false;
      }
    }
    if(requestProjectId!==S.projectId||requestGeneration!==S.loadGeneration)return false;
    if(!r||typeof r.exists!=='boolean'){
      S.data=null;S.rev='';S.runtimeRev='';
      showStatus((r&&r.error)||'无法确认 film.html 状态；为防止覆盖，不写入文件。','error');
      return false;
    }
    // A user can start a drag while this GET is in flight. Never replace the
    // timeline DOM mid-gesture; defer an external reload and tell the caller it
    // has not completed as an authoritative view switch.
    if(S.busy||S.drag||S.envelopeDrag||hasUncommittedInspectorEdits()){
      S.pendingExternalRevision=(r&&r.rev&&r.rev!==S.rev)?r.rev:'__reload__';
      pendingRevisionSource=(r&&r.rev&&r.rev!==S.rev)?'external_file':'';
      if(hasUncommittedInspectorEdits())showStatus('影片读取结果已暂存；Inspector 有未提交的属性草稿。应用修改或选择重新载入。','error');
      else showStatus('影片读取结果已暂存；时间线操作结束后再完成载入。','error');
      return false;
    }
    if(r&&r.ok&&r.exists===false&&r.migration_available===true&&
       !(r.migration_preview&&r.migration_preview.film_payload)){
      var migrationVariant=r.migration_preview&&r.migration_preview.selected_variant_id;
      var previewUrl='/api/editor/film?project_id='+encodeURIComponent(requestProjectId)+'&preview=1'+
        (migrationVariant?'&selected_variant_id='+encodeURIComponent(migrationVariant):'');
      try{
        var candidateResponse=await apiGet(previewUrl);
        if(requestProjectId!==S.projectId||requestGeneration!==S.loadGeneration)return false;
        if(candidateResponse&&candidateResponse.ok&&candidateResponse.exists===false&&
           candidateResponse.migration_available===true&&candidateResponse.migration_preview&&
           candidateResponse.migration_preview.film_payload){
          r=candidateResponse;
        }else{
          r.migration_available=false;
          r.migration_error=(candidateResponse&&candidateResponse.error)||'候选画面无法安全载入';
          r.migration_error_code=(candidateResponse&&candidateResponse.code)||'MIGRATION_PREVIEW_UNAVAILABLE';
        }
      }catch(error){
        if(requestProjectId!==S.projectId||requestGeneration!==S.loadGeneration)return false;
        r.migration_available=false;
        r.migration_error='候选画面读取失败：'+String(error&&error.message||error);
        r.migration_error_code='MIGRATION_PREVIEW_UNAVAILABLE';
      }
    }
    if(r&&r.ok&&r.exists){
      mountDirectorPlanning(false);
      if(S.rev&&r.rev!==S.rev){
        S.selectedElement=null;
        var elementBox=$('fs-element-selection');if(elementBox)elementBox.hidden=true;
      }
      S.data=r;S.rev=r.rev;S.broken=false;S.migration=null;
      $('film-studio-panel').setAttribute('data-project-id',S.projectId);render();renderAiDirector();
      return true;
    }
    if(r&&r.ok&&!r.exists){
      mountDirectorPlanning(true);
      S.data=null;S.rev=r.rev;S.runtimeRev='';setPlaybackState(false);S.migration=r;renderMigration();renderAiDirector();
      return true;
    }
    if(r&&!r.ok&&r.exists===true){
      // An invalid authoritative film still suppresses the legacy Composition writer.
      mountDirectorPlanning(false);
      renderBrokenFilmState(r);renderAiDirector();
      return true;
    }
    if(r&&r.code==='INVALID_FILM')S.broken=true;
    S.data=null;S.rev='';S.runtimeRev='';
    showStatus((r&&r.error)||'读取影片文档失败；为防止覆盖，不写入文件。','error');
    renderAiDirector();
    return false;
  }
  async function filmStudioInitialize(acceptWarnings) {
    if(!S.projectId){showStatus('缺少项目编号。','error');return;}
    var expected=S.migration?S.migration.source_composition_version:null;
    if(expected==null){showStatus('迁移预览缺少源版本号，请重新载入。','error');return;}
    var selectedVariantId=S.migration&&S.migration.migration_preview
      ?(S.migration.migration_preview.selected_variant_id||null):null;
    var r=await apiPost('/api/editor/film/initialize',{
      project_id:S.projectId,expected_composition_version:expected,
      selected_variant_id:selectedVariantId,accept_warnings:!!acceptWarnings
    });
    if(r&&r.code==='MIGRATION_WARNINGS_REQUIRE_CONFIRMATION'){
      var message='以下内容目前不能保证无损迁移：\n\n'+(r.warnings||[]).join('\n')+'\n\n确认后才写入 film.html。是否继续？';
      if(window.confirm(message))return filmStudioInitialize(true);
      showStatus('已取消初始化，没有创建 film.html。');return;
    }
    if(!r||!r.ok){showStatus((r&&r.error)||'初始化失败。','error');return;}
    S.data=r;S.rev=r.rev;S.migration=null;S.assetMode='clips';S.selectedId='';S.selectedIds=[];S.selectionAnchor='';
    mountDirectorPlanning(false);
    showStatus('已初始化 film.html。旧 Composition 与旧工作台不自动双向同步。',r.warnings&&r.warnings.length?'':'success');render();renderAiDirector();
  }
  function inspectorFieldDiffersNumber(id, expected, tolerance){
    var input=$(id);if(!input)return false;
    var actual=Number(input.value),baseline=Number(expected);
    if(!input.value.trim()||!Number.isFinite(actual)||!Number.isFinite(baseline))return true;
    return Math.abs(actual-baseline)>(tolerance==null?0.001:tolerance);
  }
  function inspectorFieldDiffersText(id, expected){
    var input=$(id);return !!input&&String(input.value)!==String(expected==null?'':expected);
  }
  function hasUncommittedClipInspectorEdits(){
    if(!S.data||!S.data.exists)return false;
    var clip=findClip(S.selectedId);
    if(!clip)return false;
    var attrs=clip.attrs||{};
    if(inspectorFieldDiffersNumber('fs-field-at',attrs.at==null?(clip.at||0):attrs.at))return true;
    if(inspectorFieldDiffersNumber('fs-field-speed',attrs.speed||1))return true;
    if(inspectorFieldDiffersNumber('fs-field-volume',attrs.volume==null?1:attrs.volume))return true;
    if(inspectorFieldDiffersText('fs-field-muted',Object.prototype.hasOwnProperty.call(attrs,'muted')?'true':'false'))return true;
    var fade=String(attrs.fade||'0,0').split(',');
    if(inspectorFieldDiffersNumber('fs-field-fade-in',fade[0]||0))return true;
    if(inspectorFieldDiffersNumber('fs-field-fade-out',fade[1]||0))return true;
    if(inspectorFieldDiffersText('fs-field-title',attrs.title||''))return true;
    if(inspectorFieldDiffersText('fs-field-keyframes',attrs['data-film-keyframes']||''))return true;
    return false;
  }
  function hasUncommittedCanvasInspectorEdits(){
    var element=S.selectedElement;
    if(!element)return false;
    var rect=element.localRect||element.rect||{},styles=element.styles||{};
    if(element.textEditable!==false&&inspectorFieldDiffersText('fs-element-text',element.text||''))return true;
    if(inspectorFieldDiffersNumber('fs-element-left',num(rect.x,0)))return true;
    if(inspectorFieldDiffersNumber('fs-element-top',num(rect.y,0)))return true;
    if(inspectorFieldDiffersNumber('fs-element-width',num(rect.width,1)))return true;
    if(inspectorFieldDiffersNumber('fs-element-height',num(rect.height,1)))return true;
    if(inspectorFieldDiffersText('fs-element-color',hexColor(styles.color||'#ffffff')))return true;
    if(inspectorFieldDiffersText('fs-element-line-height',editableElementCssLength(styles.lineHeight,'lineHeight')))return true;
    if(inspectorFieldDiffersText('fs-element-letter-spacing',editableElementCssLength(styles.letterSpacing,'letterSpacing')))return true;
    if(inspectorFieldDiffersNumber('fs-element-font-size',num(parseFloat(styles.fontSize),24)))return true;
    var rotateMatch=String(styles.rotate||'').match(/-?\d+(?:\.\d+)?/);
    if(inspectorFieldDiffersNumber('fs-element-rotate',rotateMatch?Number(rotateMatch[0]):0))return true;
    return false;
  }
  function hasUncommittedInspectorEdits(){
    return hasUncommittedClipInspectorEdits()||hasUncommittedCanvasInspectorEdits();
  }
  function resetInspectorDraft(){
    S.selectedElement=null;
    var selectionBox=$('fs-element-selection');if(selectionBox)selectionBox.hidden=true;
    if(window.filmStudioReactInspector&&typeof window.filmStudioReactInspector.renderClip==='function'){
      window.filmStudioReactInspector.renderClip('正在重新载入影片文档。');
    }
    if(S.data&&S.data.exists)renderInspector();
    else renderMigration();
    renderElementInspector(null);
  }
  function flushPendingExternalRevision(){
    if(!S.pendingExternalRevision||!S.open||S.broken||S.busy||S.drag||S.envelopeDrag)return false;
    if(hasUncommittedInspectorEdits()){
      showStatus('外部 revision 已变化，但 Inspector 中有未提交的属性草稿。请先应用修改，或点击“重新载入”并确认丢弃草稿。','error');
      return false;
    }
    var pending=S.pendingExternalRevision;
    var pendingSource=pending==='__reload__'?'':pendingRevisionSource;
    pendingRevisionSource='';
    S.pendingExternalRevision='';
    if(pending!==S.rev){
      var projectAtReload=S.projectId;
      S.runtimeRev='';
      filmStudioLoad(true).then(function(loaded){
        if(!loaded||!pendingSource||S.projectId!==projectAtReload||S.rev!==pending)return;
        var sourceLabels={
          external_file:'外部文件 / Agent 直接改写',
          studio:'Studio 编辑',
          manual_edit:'Studio 编辑',
          studio_asset_insert:'Studio 素材插入',
          asset_insert:'Studio 素材插入',
          ai_proposal:'AI 提案确认',
          cli:'CLI / Agent CLI',
          undo:'撤销',
          redo:'重做'
        };
        showStatus('已载入影片新 revision。修改来源：'+(sourceLabels[pendingSource]||pendingSource)+'。','success');
      });
      return true;
    }
    return false;
  }
  window.filmStudioReload=function(){
    if(S.busy||S.drag||S.envelopeDrag){
      showStatus('保存或拖动尚未结束；完成当前操作后再重新载入。','error');
      return;
    }
    var dirty=hasUncommittedInspectorEdits();
    if(dirty&&!window.confirm('Inspector 中有未提交的属性草稿。重新载入将放弃这些修改；是否继续？'))return;
    if(dirty)resetInspectorDraft();
    if(!S.open){S.pendingExternalRevision='';pendingRevisionSource='';filmStudioLoad(true);return;}
    S.pendingExternalRevision='__reload__';
    pendingRevisionSource='';
    flushPendingExternalRevision();
  };
  async function filmStudioCommit(ops, options) {
    options=options||{};
    var allowedDraft=options.allowInspectorDraft||'';
    var hasConflictingDraft=allowedDraft==='clip'
      ?hasUncommittedCanvasInspectorEdits()
      :allowedDraft==='canvas'
        ?hasUncommittedClipInspectorEdits()
        :hasUncommittedInspectorEdits();
    if(hasConflictingDraft){
      // A completed drag may already have updated transient preview state.
      // Rebuild only the timeline from S.data; a full render would wipe React form drafts.
      if(!S.drag&&!S.envelopeDrag&&S.data&&S.data.exists)renderTimeline();
      clearTimelineGesturePreview();
      showStatus('Inspector 还有另一组未提交草稿。请先应用或重新载入后，再执行其他编辑。','error');
      return false;
    }
    if(!S.projectId||!S.data||!S.data.exists){clearTimelineGesturePreview();return false;}
    if(S.busy){clearTimelineGesturePreview();showStatus('上一项编辑仍在保存，本次操作未提交；请保存结束后重试。','error');return false;}
    if(S.broken){clearTimelineGesturePreview();showStatus('film.html 无效，暂时不能写入；修复后重新载入。','error');return false;}
    if(S.proposal){clearFilmProposalTimelinePreview();S.proposal=null;S.previewProposalActive=false;renderProposalPanel();S.runtimeRev='';}
    var studioPanel=$('film-studio-panel'),busyTimeline=$('fs-timeline');
    if(studioPanel)studioPanel.setAttribute('data-busy','true');
    if(busyTimeline)busyTimeline.style.pointerEvents='none';
    try{
      return await dispatchStudioTransaction('film-edit',S.rev,ops,async function(){
        var r=await apiPost('/api/editor/film/edit',{project_id:S.projectId,base:S.rev,ops:ops});
        if(!r||!r.ok){
          var failureMessage=(r&&r.error)||'编辑提交失败。';
          if(S.data&&S.data.exists&&!allowedDraft)render();
          showStatus(failureMessage,'error');
          return false;
        }
        S.data=r;S.rev=r.rev;
        if(r.rebased){
          S.selectedElement=null;
          var elementBox=$('fs-element-selection');if(elementBox)elementBox.hidden=true;
        }else if(S.selectedElement)S.selectedElement=Object.assign({},S.selectedElement,{baseRevision:S.rev});
        var savedMessage=r.rebased?'操作已按片段 ID 重放到较新 revision；请检查结果。':'已保存 '+(r.ops||[]).length+' 项编辑。';
        render();
        var migrationWarnings=S.data.migration&&S.data.migration.warnings||[];
        showStatus(migrationWarnings.length?'迁移警告：'+migrationWarnings.join('；')+'；'+savedMessage:savedMessage,r.rebased?'':'success');
        return true;
      });
    }catch(error){
      var editFailure='编辑提交失败：'+String(error&&error.message||error);
      if(S.data&&S.data.exists&&!allowedDraft)render();
      showStatus(editFailure,'error');
      return false;
    }finally{
      if(studioPanel)studioPanel.setAttribute('data-busy','false');
      if(busyTimeline)busyTimeline.style.pointerEvents='';
      clearTimelineGesturePreview();
      flushPendingExternalRevision();
    }
  }
  async function filmStudioHistory(action) {
    if(!S.data||S.busy)return;
    if(hasUncommittedInspectorEdits()){
      showStatus('请先应用 Inspector 属性草稿，或重新载入并确认丢弃，再撤销/重做。','error');
      return;
    }
    if(S.broken){showStatus('影片文件无效，暂时不能撤销或重做。','error');return;}
    if(S.proposal){clearFilmProposalTimelinePreview();S.proposal=null;S.previewProposalActive=false;renderProposalPanel();S.runtimeRev='';}
    try{
      await dispatchStudioTransaction(action,S.rev,[],async function(){
        var r=await apiPost('/api/editor/film/'+action,{project_id:S.projectId,base:S.rev});
        if(!r||!r.ok){showStatus((r&&r.error)||'历史操作失败。','error');return false;}
        S.data=r;S.rev=r.rev;render();showStatus(action==='undo'?'已撤销。':'已重做。','success');
        return true;
      });
    }catch(error){
      showStatus('历史操作失败：'+String(error&&error.message||error),'error');
    }finally{flushPendingExternalRevision();}
  }
  function onMove(e) {
    if(S.envelopeDrag){
      var ed=S.envelopeDrag;
      if(!e||e.pointerId!==ed.pointerId)return;
      var rect=envelopeDomCanvas?envelopeDomCanvas.getBoundingClientRect():ed.rect;
      if(!rect||!rect.width||!rect.height)return;
      var math=timelineMath(),points;
      if(math&&typeof math.moveEnvelopeKeyframe==='function'){
        points=math.moveEnvelopeKeyframe(ed.points,ed.index,ed.duration,rect.width,rect.height,
          e.clientX-rect.left,e.clientY-rect.top);
        if(!points)return;
      }else{
        points=ed.points.map(function(item){return {t:item.t,v:item.v};});
        var point=points[ed.index];if(!point)return;
        var x=Math.max(0,Math.min(rect.width,(e.clientX-rect.left)));
        var y=Math.max(0,Math.min(rect.height,(e.clientY-rect.top)));
        if(ed.index>0&&ed.index<points.length-1){
          var lower=points[ed.index-1].t+0.01,upper=points[ed.index+1].t-0.01;
          point.t=Math.max(lower,Math.min(upper,x/rect.width*ed.duration));
        }
        point.v=Math.max(0,Math.min(1,1-(y-4)/Math.max(1,rect.height-8)));
      }
      updateTimelineEnvelopeGesture({
        rect:{left:Number(rect.left),top:Number(rect.top),width:Number(rect.width),height:Number(rect.height)},
        points:points,active:true,changed:true
      });
      if(!setTimelineEnvelopePreview(ed.clipId,points)&&envelopeDomCanvas)drawVolumeEnvelope(envelopeDomCanvas,points);
      e.preventDefault();return;
    }
    var d=S.drag;if(!d||!e||e.pointerId!==d.pointerId)return;
    var scroll=$('fs-timeline-scroll');
    if(!scroll)return;
    var pointerDelta=e.clientX-d.startX,scrollDelta=scroll.scrollLeft-d.startScrollLeft;
    var dx=pointerDelta+scrollDelta,deltaSeconds=timelineDragDeltaSeconds(pointerDelta,scrollDelta,S.pxPerSec);
    if(!d.active&&Math.sqrt(dx*dx+(e.clientY-d.startY)*(e.clientY-d.startY))<5)return;
    if(!d.active)d=updateTimelineGestureContext({active:true})||d;
    // Only plain clip moves can change tracks. Trim/roll/slip/slide gestures
    // must remain bound to their original track even if the pointer crosses rows.
    if(d.mode==='move'&&gestureDomRefs){
      var target=document.elementFromPoint(e.clientX,e.clientY),row=target&&target.closest('.fs-track');
      if(row){
        var nextTrack=row.getAttribute('data-track'),targetError=groupMoveTargetError(d,nextTrack,deltaSeconds);
        var rowChanged=!!gestureDomRefs&&gestureDomRefs.targetRow!==row;
        if(rowChanged||d.targetTrackId!==nextTrack||d.invalidTargetMessage!==targetError){
          clearDragTrackTarget(d);
          d=updateTimelineGestureContext({targetTrackId:nextTrack,invalidTargetMessage:targetError})||d;
          if(gestureDomRefs)gestureDomRefs.targetRow=row;
          if(!setTimelineDropTarget(nextTrack,!!targetError))
            row.classList.add(targetError?'fs-track-drop-invalid':'fs-track-drop-target');
        }
      }else{
        var outsideTrackMessage='请将片段放置到有效轨道上，多选移动已取消。';
        var staleDropTarget=!!(gestureDomRefs&&gestureDomRefs.targetRow);
        if(d.targetTrackId!==d.trackId||d.invalidTargetMessage!==outsideTrackMessage||staleDropTarget){
          clearDragTrackTarget(d);
          d=updateTimelineGestureContext({targetTrackId:d.trackId,invalidTargetMessage:outsideTrackMessage})||d;
        }
      }
    }
    if(d.mode==='slide'){
      var slideResolution=resolveTimelineSlide(d,deltaSeconds,8);
      var deltaSlide=slideResolution.deltaSeconds,slideSnapUsed=slideResolution.snapCandidate;
      showTimelineSnapGuide(slideSnapUsed?slideSnapUsed.at:null,slideSnapUsed&&slideSnapUsed.kind);
      var slidePreview=slideResolution.preview||null;
      if(!slidePreview){
        slidePreview={};
        slidePreview[d.leftClipId]={width:Math.max(3,timelineSecondsToPixels(d.leftDuration+deltaSlide,S.pxPerSec))};
        slidePreview[d.centerClipId]={left:Math.max(0,timelineSecondsToPixels(d.centerAt+deltaSlide,S.pxPerSec)),mode:'slide',deltaLabel:'滑移 '+(deltaSlide>=0?'+':'')+deltaSlide.toFixed(2)+'s'};
        slidePreview[d.rightClipId]={left:Math.max(0,timelineSecondsToPixels(d.rightAt+deltaSlide,S.pxPerSec)),width:Math.max(3,timelineSecondsToPixels(d.rightDuration-deltaSlide,S.pxPerSec))};
      }
      var slideLabel=slidePreview[d.centerClipId]&&slidePreview[d.centerClipId].deltaLabel||('滑移 '+(deltaSlide>=0?'+':'')+deltaSlide.toFixed(2)+'s');
      if(!setTimelineGesturePreview(slidePreview)){
        var slideElements=gestureDomRefs&&gestureDomRefs.slideElements,clipElement=gestureDomRefs&&gestureDomRefs.clipElement;
        if(!slideElements||!clipElement)return;
        slideElements[0].style.width=slidePreview[d.leftClipId].width+'px';
        slideElements[1].style.left=slidePreview[d.centerClipId].left+'px';
        slideElements[2].style.left=slidePreview[d.rightClipId].left+'px';
        slideElements[2].style.width=slidePreview[d.rightClipId].width+'px';
        clipElement.setAttribute('data-slide-delta',slideLabel);
        clipElement.classList.add('fs-sliding');
      }
    }else if(d.mode==='roll'){
      var rollResolution=resolveTimelineRoll(d,deltaSeconds,8);
      var deltaRoll=rollResolution.deltaSeconds,rollSnapUsed=rollResolution.snapCandidate;
      showTimelineSnapGuide(rollSnapUsed?rollSnapUsed.at:null,rollSnapUsed&&rollSnapUsed.kind);
      var rollPreview=rollResolution.preview||null;
      if(!rollPreview){
        rollPreview={};
        rollPreview[d.leftClipId]={width:Math.max(3,timelineSecondsToPixels(d.leftDuration+deltaRoll,S.pxPerSec))};
        rollPreview[d.rightClipId]={left:Math.max(0,timelineSecondsToPixels(d.rightAt+deltaRoll,S.pxPerSec)),width:Math.max(3,timelineSecondsToPixels(d.rightDuration-deltaRoll,S.pxPerSec))};
        var fallbackRollTarget=rollPreview[d.id]||{};
        fallbackRollTarget.mode='roll';
        fallbackRollTarget.deltaLabel='切口 '+(deltaRoll>=0?'+':'')+deltaRoll.toFixed(2)+'s';
        rollPreview[d.id]=fallbackRollTarget;
      }
      var rollTarget=rollPreview[d.id]||{};
      var rollLabel=rollTarget.deltaLabel||('切口 '+(deltaRoll>=0?'+':'')+deltaRoll.toFixed(2)+'s');
      if(!setTimelineGesturePreview(rollPreview)){
        var rollElements=gestureDomRefs&&gestureDomRefs.rollElements,clipElement=gestureDomRefs&&gestureDomRefs.clipElement;
        if(!rollElements||!clipElement)return;
        rollElements[0].style.width=rollPreview[d.leftClipId].width+'px';
        rollElements[1].style.left=rollPreview[d.rightClipId].left+'px';
        rollElements[1].style.width=rollPreview[d.rightClipId].width+'px';
        clipElement.setAttribute('data-roll-delta',rollLabel);
        clipElement.classList.add('fs-rolling');
      }
    }else if(d.mode==='slip'){
      var slipClip=findClip(d.id),slipSpeed=slipClip?num(slipClip.attrs.speed,1):1;
      var slipResolution=resolveTimelineSlip(deltaSeconds,slipSpeed,d.id);
      var sourceDelta=slipResolution.sourceDeltaSeconds;
      var slipPreview=slipResolution.preview||{};
      if(!slipResolution.preview)slipPreview[d.id]={mode:'slip',deltaLabel:'源偏移 '+(sourceDelta>=0?'+':'')+sourceDelta.toFixed(2)+'s'};
      if(!setTimelineGesturePreview(slipPreview)){
        var clipElement=gestureDomRefs&&gestureDomRefs.clipElement;
        if(!clipElement)return;
        clipElement.setAttribute('data-slip-delta',slipPreview[d.id].deltaLabel);
        clipElement.classList.add('fs-slipping');
      }
    }else if(d.mode==='trim-start'){
      var startTrimPreviewController=window.filmStudioTimelineController;
      var startTrimPreviewResult=startTrimPreviewController&&typeof startTrimPreviewController.resolveTrimGesture==='function'
        ?startTrimPreviewController.resolveTrimGesture({
          clipId:d.id,edge:'start',originalAt:d.originalAt,originalEnd:d.originalEnd,
          clipTag:d.clipTag,sourceIn:d.originalSourceIn,playbackSpeed:d.originalSpeed,
          rawDeltaSeconds:deltaSeconds,candidates:timelineSnapCandidates([d.id]),
          pixelsPerSecond:S.pxPerSec,thresholdPixels:8,snapEnabled:S.snapEnabled,frameRate:timelineFrameRate()
        })
        :null;
      if(startTrimPreviewResult){
        var startTrimGuide=startTrimPreviewResult.resolution.snapCandidate;
        showTimelineSnapGuide(startTrimGuide?startTrimGuide.at:null,startTrimGuide&&startTrimGuide.kind);
        var startTrimPreview={};startTrimPreview[d.id]=startTrimPreviewResult.preview;
        if(!setTimelineGesturePreview(startTrimPreview)){
          var startTrimElement=gestureDomRefs&&gestureDomRefs.clipElement;
          if(!startTrimElement)return;
          startTrimElement.style.left=startTrimPreviewResult.preview.left+'px';
          startTrimElement.style.width=startTrimPreviewResult.preview.width+'px';
        }
      }else{
        var trimStartResolution=resolveTimelineTrim('start',d.originalAt,d.originalEnd,deltaSeconds,[d.id],8);
        var newStart=trimStartResolution.target,startSnapUsed=trimStartResolution.snapCandidate;
        showTimelineSnapGuide(startSnapUsed?startSnapUsed.at:null,startSnapUsed&&startSnapUsed.kind);
        var trimStartPreview={};
        trimStartPreview[d.id]={left:timelineSecondsToPixels(newStart,S.pxPerSec),width:Math.max(3,timelineSecondsToPixels(d.originalEnd-newStart,S.pxPerSec))};
        if(!setTimelineGesturePreview(trimStartPreview)){
          var clipElement=gestureDomRefs&&gestureDomRefs.clipElement;
          if(!clipElement)return;
          clipElement.style.left=trimStartPreview[d.id].left+'px';
          clipElement.style.width=trimStartPreview[d.id].width+'px';
        }
      }
    }else if(d.mode==='trim-end'){
      var endTrimPreviewController=window.filmStudioTimelineController;
      var endTrimPreviewResult=endTrimPreviewController&&typeof endTrimPreviewController.resolveTrimGesture==='function'
        ?endTrimPreviewController.resolveTrimGesture({
          clipId:d.id,edge:'end',originalAt:d.originalAt,originalEnd:d.originalEnd,
          clipTag:d.clipTag,sourceIn:d.originalSourceIn,playbackSpeed:d.originalSpeed,
          rawDeltaSeconds:deltaSeconds,candidates:timelineSnapCandidates([d.id]),
          pixelsPerSecond:S.pxPerSec,thresholdPixels:8,snapEnabled:S.snapEnabled,frameRate:timelineFrameRate()
        })
        :null;
      if(endTrimPreviewResult){
        var endTrimGuide=endTrimPreviewResult.resolution.snapCandidate;
        showTimelineSnapGuide(endTrimGuide?endTrimGuide.at:null,endTrimGuide&&endTrimGuide.kind);
        var endTrimPreview={};endTrimPreview[d.id]=endTrimPreviewResult.preview;
        if(!setTimelineGesturePreview(endTrimPreview)){
          var endTrimElement=gestureDomRefs&&gestureDomRefs.clipElement;
          if(!endTrimElement)return;
          endTrimElement.style.left=endTrimPreviewResult.preview.left+'px';
          endTrimElement.style.width=endTrimPreviewResult.preview.width+'px';
        }
      }else{
        var trimEndResolution=resolveTimelineTrim('end',d.originalAt,d.originalEnd,deltaSeconds,[d.id],8);
        var newEnd=trimEndResolution.target,endSnapUsed=trimEndResolution.snapCandidate;
        showTimelineSnapGuide(endSnapUsed?endSnapUsed.at:null,endSnapUsed&&endSnapUsed.kind);
        var trimEndPreview={};
        trimEndPreview[d.id]={width:Math.max(3,timelineSecondsToPixels(newEnd-d.originalAt,S.pxPerSec))};
        if(!setTimelineGesturePreview(trimEndPreview)){
          var clipElement=gestureDomRefs&&gestureDomRefs.clipElement;
          if(!clipElement)return;
          clipElement.style.width=trimEndPreview[d.id].width+'px';
        }
      }
    }else{
      var chosenIds=(d.members||[]).map(function(member){return member.id;});
      var controller=window.filmStudioTimelineController;
      var managedPreview=controller&&typeof controller.buildMovePreview==='function'
        ?controller.buildMovePreview({
          gesture:d,tracks:S.data&&S.data.tracks||[],targetTrackId:d.targetTrackId||d.trackId,
          minimumStart:(d.members||[]).reduce(function(value,member){return Math.min(value,member.originalAt);},Infinity),
          rawDeltaSeconds:deltaSeconds,candidates:timelineSnapCandidates(chosenIds),
          pixelsPerSecond:S.pxPerSec,thresholdPixels:8,snapEnabled:S.snapEnabled,frameRate:timelineFrameRate()
        })
        :null;
      if(managedPreview){
        var managedSnap=managedPreview.resolution.snapCandidate;
        showTimelineSnapGuide(managedSnap?managedSnap.at:null,managedSnap&&managedSnap.kind);
        if(!setTimelineGesturePreview(managedPreview.preview)){
          (d.members||[]).forEach(function(member){
            var element=gestureDomRefs&&gestureDomRefs.memberElements[member.id];
            var entry=managedPreview.preview[member.id];
            if(element&&entry)element.style.left=entry.left+'px';
          });
        }
      }else{
        var minStart=(d.members||[]).reduce(function(value,member){return Math.min(value,member.originalAt);},Infinity);
        var moveResolution=resolveTimelineMove(minStart,deltaSeconds,chosenIds,8);
        var snappedDelta=moveResolution.deltaSeconds,moveSnapUsed=moveResolution.snapCandidate;
        showTimelineSnapGuide(moveSnapUsed?moveSnapUsed.at:null,moveSnapUsed&&moveSnapUsed.kind);
        var previewLeftByClipId={};
        var previewTargetIndex=d.trackOrder.indexOf(d.targetTrackId||d.trackId);
        if(previewTargetIndex<0)previewTargetIndex=d.originalTrackIndex;
        var previewTrackDelta=previewTargetIndex-d.originalTrackIndex;
        var canPreviewCrossTrack=!d.invalidTargetMessage&&Number.isInteger(previewTargetIndex)&&Number.isInteger(d.originalTrackIndex);
        (d.members||[]).forEach(function(member){
          var previewEntry={left:timelineClipLeft(member.originalAt+snappedDelta,S.pxPerSec)};
          var destinationIndex=member.trackIndex+previewTrackDelta;
          if(canPreviewCrossTrack&&destinationIndex>=0&&destinationIndex<d.trackOrder.length){
            var destinationTrackId=d.trackOrder[destinationIndex];
            if(destinationTrackId!==member.trackId)previewEntry.targetTrackId=destinationTrackId;
          }
          previewLeftByClipId[member.id]=previewEntry;
        });
        if(!setTimelineGesturePreview(previewLeftByClipId)){
          (d.members||[]).forEach(function(member){
            var element=gestureDomRefs&&gestureDomRefs.memberElements[member.id];
            if(element)element.style.left=previewLeftByClipId[member.id].left+'px';
          });
        }
      }
    }
  }
  async function finishReactClipGesture(e, preparedOperations) {
    var store=window.filmStudioStore;
    var snapshot=store&&typeof store.getSnapshot==='function'?store.getSnapshot():null;
    var d=snapshot&&snapshot.timelineGestureContext;
    if(!e||!d||d.pointerId!==e.pointerId)return;
    // Keep the compatibility path for environments where the React operation
    // resolver is not mounted; the built Studio always supplies an operation array.
    if(!Array.isArray(preparedOperations))return onUp(e);

    var dragDomRefs=gestureDomRefs;
    S.drag=null;
    S.skipNextClipClick=true;
    setTimeout(function(){S.skipNextClipClick=false;},0);
    showTimelineSnapGuide(null);
    clearDragTrackTarget(d);
    var reactOwnsActiveDrag=setTimelineActiveDragIds(null);
    if(!reactOwnsActiveDrag)(d.members||[]).forEach(function(member){
      var element=dragDomRefs&&dragDomRefs.memberElements[member.id];
      if(element)element.classList.remove('fs-dragging');
    });
    gestureDomRefs=null;

    if(S.pendingExternalRevision){
      syncClipSelectionVisual();
      renderTimeline();
      clearTimelineGesturePreview();
      showStatus('影片 revision 在拖动期间变化；本次旧画面拖动未提交，先载入外部版本。','error');
      flushPendingExternalRevision();
      return;
    }
    if(!d.active){
      syncClipSelectionVisual();
      if(d.mode==='slide'||d.mode==='slip'){
        var clickedClip=findClip(d.id);
        if(clickedClip){S.playhead=clickedClip.at;setPlayheadVisual();previewCurrent();}
      }
      clearTimelineGesturePreview();
      flushPendingExternalRevision();
      return;
    }
    if(d.invalidTargetMessage){
      renderTimeline();
      clearTimelineGesturePreview();
      showStatus(d.invalidTargetMessage,'error');
      flushPendingExternalRevision();
      return;
    }
    if(!preparedOperations.length){
      clearTimelineGesturePreview();
      renderTimeline();
      flushPendingExternalRevision();
      return;
    }
    await filmStudioCommit(preparedOperations);
  }

  async function onUp(e) {
    if(S.envelopeDrag){
      var envelopeEdit=S.envelopeDrag;
      if(!e||e.pointerId!==envelopeEdit.pointerId)return;
      S.envelopeDrag=null;envelopeDomCanvas=null;
      if(S.pendingExternalRevision){
        clearTimelineEnvelopePreview();
        renderTimeline();
        showStatus('影片 revision 在音量曲线拖动期间变化；本次拖动未提交，先载入外部版本。','error');
        flushPendingExternalRevision();
        return;
      }
      if(envelopeEdit.changed){
        var math=timelineMath(),unique;
        if(math&&typeof math.normalizeEnvelopePoints==='function'){
          unique=math.normalizeEnvelopePoints(envelopeEdit.points);
        }else{
          var normalized=envelopeEdit.points.map(function(p){return {t:Number(p.t.toFixed(3)),v:Number(p.v.toFixed(3))};})
            .sort(function(a,b){return a.t-b.t;});
          // Coalesce points which became the same time after rounding; preserve the last gesture value.
          unique=[];
          normalized.forEach(function(p){if(unique.length&&Math.abs(unique[unique.length-1].t-p.t)<0.001)unique[unique.length-1]=p;else unique.push(p);});
        }
        if(unique.length>=2)await filmStudioCommit([{op:'set',clip:envelopeEdit.clipId,field:'volume_envelope',value:JSON.stringify(unique)}]);
      }else if(S.data&&S.data.exists)renderTimeline();
      clearTimelineEnvelopePreview();
      flushPendingExternalRevision();
      return;
    }
    var d=S.drag;
    if(!d){showTimelineSnapGuide(null);clearDragTrackTarget(null);gestureDomRefs=null;setTimelineActiveDragIds(null);clearTimelineGesturePreview();S.skipNextClipClick=true;setTimeout(function(){S.skipNextClipClick=false;},0);flushPendingExternalRevision();return;}
    // Document-level fallbacks also observe pointer events from other contacts; only the
    // pointer that began this transaction may finish or commit it.
    if(!e||e.pointerId!==d.pointerId)return;
    var dragDomRefs=gestureDomRefs;
    S.drag=null;S.skipNextClipClick=true;setTimeout(function(){S.skipNextClipClick=false;},0);
    showTimelineSnapGuide(null);clearDragTrackTarget(d);
    var reactOwnsActiveDrag=setTimelineActiveDragIds(null);
    if(!reactOwnsActiveDrag)(d.members||[]).forEach(function(member){
      var element=dragDomRefs&&dragDomRefs.memberElements[member.id];
      if(element)element.classList.remove('fs-dragging');
    });
    gestureDomRefs=null;
    if(S.pendingExternalRevision){
      syncClipSelectionVisual();renderTimeline();clearTimelineGesturePreview();
      showStatus('影片 revision 在拖动期间变化；本次旧画面拖动未提交，先载入外部版本。','error');
      flushPendingExternalRevision();
      return;
    }
    if(!d.active){
      syncClipSelectionVisual();
      // A click in slide/slip mode is still a selection/seek, not an edit.
      // Actual drags keep the current playhead as their snap anchor.
      if(d.mode==='slide'||d.mode==='slip'){
        var clickedClip=findClip(d.id);
        if(clickedClip){S.playhead=clickedClip.at;setPlayheadVisual();previewCurrent();}
      }
      clearTimelineGesturePreview();flushPendingExternalRevision();return;
    }
    if(d.invalidTargetMessage){
      renderTimeline();clearTimelineGesturePreview();showStatus(d.invalidTargetMessage,'error');flushPendingExternalRevision();return;
    }
    var pointerDelta=e.clientX-d.startX,scrollDelta=$('fs-timeline-scroll').scrollLeft-d.startScrollLeft;
    var deltaSeconds=timelineDragDeltaSeconds(pointerDelta,scrollDelta,S.pxPerSec);
    if(!reactOwnsActiveDrag&&dragDomRefs&&dragDomRefs.clipElement){
      var dragElement=dragDomRefs.clipElement;
      dragElement.removeAttribute('data-slip-delta');dragElement.classList.remove('fs-slipping');
      dragElement.removeAttribute('data-roll-delta');dragElement.classList.remove('fs-rolling');
      dragElement.removeAttribute('data-slide-delta');dragElement.classList.remove('fs-sliding');
    }
    if(d.mode==='slide'){
      var slideCommitResolution=resolveTimelineSlide(d,deltaSeconds,8);
      var slideDelta=slideCommitResolution.deltaSeconds;
      if(Math.abs(slideDelta)<0.001){clearTimelineGesturePreview();renderTimeline();return;}
      await filmStudioCommit([slideCommitResolution.operation||{op:'slide',clip:d.centerClipId,delta:timelineOperationNumber(slideDelta,(slideCommitResolution.snapCandidate&&slideCommitResolution.snapCandidate.kind==='帧')||timelineIsFrameAligned(slideCommitResolution.targetAt))}]);return;
    }
    if(d.mode==='roll'){
      var rollCommitResolution=resolveTimelineRoll(d,deltaSeconds,8);
      var rollDelta=rollCommitResolution.deltaSeconds;
      if(Math.abs(rollDelta)<0.001){clearTimelineGesturePreview();renderTimeline();return;}
      await filmStudioCommit([rollCommitResolution.operation||{op:'roll',clip:d.leftClipId,other_clip:d.rightClipId,delta:timelineOperationNumber(rollDelta,(rollCommitResolution.snapCandidate&&rollCommitResolution.snapCandidate.kind==='帧')||timelineIsFrameAligned(rollCommitResolution.targetAt))}]);return;
    }
    if(d.mode==='slip'){
      var slipClip=findClip(d.id);
      if(!slipClip||slipClip.duration==null){clearTimelineGesturePreview();renderTimeline();return;}
      var slipResolution=resolveTimelineSlip(deltaSeconds,num(slipClip.attrs.speed,1),d.id);
      var sourceDelta=slipResolution.sourceDeltaSeconds;
      if(Math.abs(sourceDelta)<0.001){clearTimelineGesturePreview();renderTimeline();return;}
      await filmStudioCommit([slipResolution.operation||{op:'slip',clip:d.id,delta_source:timelineOperationNumber(sourceDelta,S.snapEnabled)}]);return;
    }
    if(d.mode==='trim-start'){
      var startTrimCommitController=window.filmStudioTimelineController;
      var startTrimCommitResult=startTrimCommitController&&typeof startTrimCommitController.resolveTrimGesture==='function'
        ?startTrimCommitController.resolveTrimGesture({
          clipId:d.id,edge:'start',originalAt:d.originalAt,originalEnd:d.originalEnd,
          clipTag:d.clipTag,sourceIn:d.originalSourceIn,playbackSpeed:d.originalSpeed,
          rawDeltaSeconds:deltaSeconds,candidates:timelineSnapCandidates([d.id]),
          pixelsPerSecond:S.pxPerSec,thresholdPixels:8,snapEnabled:S.snapEnabled,frameRate:timelineFrameRate()
        })
        :null;
      if(startTrimCommitResult){await filmStudioCommit([startTrimCommitResult.operation]);return;}
      var trimStartCommit=resolveTimelineTrim('start',d.originalAt,d.originalEnd,deltaSeconds,[d.id],8);
      await filmStudioCommit([{op:'trim',clip:d.id,edge:'start',at:timelineOperationNumber(trimStartCommit.target,(trimStartCommit.snapCandidate&&trimStartCommit.snapCandidate.kind==='帧')||timelineIsFrameAligned(trimStartCommit.target))}]);return;
    }
    if(d.mode==='trim-end'){
      var endTrimCommitController=window.filmStudioTimelineController;
      var endTrimCommitResult=endTrimCommitController&&typeof endTrimCommitController.resolveTrimGesture==='function'
        ?endTrimCommitController.resolveTrimGesture({
          clipId:d.id,edge:'end',originalAt:d.originalAt,originalEnd:d.originalEnd,
          clipTag:d.clipTag,sourceIn:d.originalSourceIn,playbackSpeed:d.originalSpeed,
          rawDeltaSeconds:deltaSeconds,candidates:timelineSnapCandidates([d.id]),
          pixelsPerSecond:S.pxPerSec,thresholdPixels:8,snapEnabled:S.snapEnabled,frameRate:timelineFrameRate()
        })
        :null;
      if(endTrimCommitResult){await filmStudioCommit([endTrimCommitResult.operation]);return;}
      var trimEndCommit=resolveTimelineTrim('end',d.originalAt,d.originalEnd,deltaSeconds,[d.id],8);
      await filmStudioCommit([{op:'trim',clip:d.id,edge:'end',end:timelineOperationNumber(trimEndCommit.target,(trimEndCommit.snapCandidate&&trimEndCommit.snapCandidate.kind==='帧')||timelineIsFrameAligned(trimEndCommit.target))}]);return;
    }
    var moveController=window.filmStudioTimelineController;
    if(moveController&&typeof moveController.buildMovePreview==='function'){
      // Resolve preview geometry and the authoritative move operations in one result.
      var moveControllerResult=moveController.buildMovePreview({
        gesture:d,tracks:S.data&&S.data.tracks||[],targetTrackId:d.targetTrackId||d.trackId,
        minimumStart:(d.members||[]).reduce(function(value,member){return Math.min(value,member.originalAt);},Infinity),
        rawDeltaSeconds:deltaSeconds,candidates:timelineSnapCandidates((d.members||[]).map(function(member){return member.id;})),
        pixelsPerSecond:S.pxPerSec,thresholdPixels:8,snapEnabled:S.snapEnabled,frameRate:timelineFrameRate()
      });
      var moveControllerOperations=moveControllerResult&&moveControllerResult.operations;
      if(!moveControllerOperations||(d.members||[]).length!==moveControllerOperations.length){
        clearTimelineGesturePreview();renderTimeline();return;
      }
      await filmStudioCommit(moveControllerOperations);return;
    }
    var chosen=(d.members||[]).map(function(member){return member.id;});
    var minStart=Math.min.apply(null,(d.members||[]).map(function(member){return member.originalAt;}));
    // Preview and commit must use identical clamping/snap resolution.
    var moveResolution=resolveTimelineMove(minStart,deltaSeconds,chosen,8);
    var delta=moveResolution.deltaSeconds;
    var targetIndex=d.trackOrder.indexOf(d.targetTrackId||d.trackId);
    if(targetIndex<0)targetIndex=d.originalTrackIndex;
    var trackDelta=targetIndex-d.originalTrackIndex,operations=[];
    (d.members||[]).forEach(function(member){
      var index=member.trackIndex+trackDelta;
      if(index<0||index>=d.trackOrder.length){showStatus('多选移动超出轨道范围，操作已取消。','error');return;}
      operations.push({op:'move',clip:member.id,track_id:d.trackOrder[index],at:timelineOperationNumber(Math.max(0,member.originalAt+delta),(moveResolution.snapCandidate&&moveResolution.snapCandidate.kind==='帧')||timelineIsFrameAligned(Math.max(0,member.originalAt+delta)))});
    });
    if(!operations.length||(operations.length!==(d.members||[]).length)){
      clearTimelineGesturePreview();renderTimeline();return;
    }
    await filmStudioCommit(operations);
  }
  // The React timeline owns move/up dispatch for clips that captured the pointer.
  // Native document listeners remain a fallback and still serve audio-envelope drags.
  window.filmStudioMoveClipGesture = function(e){
    if(!e||!window.filmStudioOwnsClipGesturePointer||
       !window.filmStudioOwnsClipGesturePointer(e.pointerId))return;
    var snapshot=window.filmStudioStore&&window.filmStudioStore.getSnapshot
      ?window.filmStudioStore.getSnapshot():null;
    var reactTimeline=window.filmStudioReactTimeline;
    if(snapshot&&snapshot.timelineGestureContext&&
       reactTimeline&&typeof reactTimeline.previewClipGesture==='function'){
      reactTimeline.previewClipGesture(e);
      return;
    }
    onMove(e);
  };
  window.filmStudioEndClipGesture = function(e){
    if(!e||!window.filmStudioOwnsClipGesturePointer||
       !window.filmStudioOwnsClipGesturePointer(e.pointerId))return;
    var snapshot=window.filmStudioStore&&window.filmStudioStore.getSnapshot
      ?window.filmStudioStore.getSnapshot():null;
    var timeline=window.filmStudioReactTimeline;
    var prepared=snapshot&&snapshot.timelineGestureContext&&timeline&&
      typeof timeline.prepareClipGestureCommit==='function'
      ?timeline.prepareClipGestureCommit(e):undefined;
    if(Array.isArray(prepared))return finishReactClipGesture(e,prepared);
    return onUp(e);
  };
  window.filmStudioMoveEnvelopeGesture=function(e,rect){
    var active=S.envelopeDrag;
    if(!e||!window.filmStudioOwnsEnvelopeGesturePointer||
       !window.filmStudioOwnsEnvelopeGesturePointer(e.pointerId)||!active||active.pointerId!==e.pointerId)return;
    if(rect&&[rect.left,rect.top,rect.width,rect.height].every(Number.isFinite))
      updateTimelineEnvelopeGesture({rect:{left:rect.left,top:rect.top,width:rect.width,height:rect.height}});
    onMove(e);
  };
  window.filmStudioEndEnvelopeGesture=function(e){
    if(!e||!window.filmStudioOwnsEnvelopeGesturePointer||
       !window.filmStudioOwnsEnvelopeGesturePointer(e.pointerId)||!S.envelopeDrag||
       S.envelopeDrag.pointerId!==e.pointerId)return;
    return onUp(e);
  };
  window.filmStudioCancelEnvelopeGesture=function(e){
    if(!e||!window.filmStudioOwnsEnvelopeGesturePointer||
       !window.filmStudioOwnsEnvelopeGesturePointer(e.pointerId)||!S.envelopeDrag||
       S.envelopeDrag.pointerId!==e.pointerId)return;
    cancelPointerGesture();
  };
  document.addEventListener('pointermove',function(e){
    if(window.filmStudioOwnsClipGesturePointer&&window.filmStudioOwnsClipGesturePointer(e.pointerId))return;
    if(window.filmStudioOwnsEnvelopeGesturePointer&&window.filmStudioOwnsEnvelopeGesturePointer(e.pointerId))return;
    onMove(e);
  });
  document.addEventListener('pointerup',function(e){
    if(window.filmStudioOwnsClipGesturePointer&&window.filmStudioOwnsClipGesturePointer(e.pointerId))return;
    if(window.filmStudioOwnsEnvelopeGesturePointer&&window.filmStudioOwnsEnvelopeGesturePointer(e.pointerId))return;
    onUp(e);
  });
  function cancelPointerGesture(){
    showTimelineSnapGuide(null);
    clearTimelineEnvelopePreview();
    var reactOwnsActiveDrag=setTimelineActiveDragIds(null);
    envelopeDomCanvas=null;
    if(S.envelopeDrag)S.envelopeDrag=null;
    if(S.drag){
      var drag=S.drag,dragDomRefs=gestureDomRefs;S.drag=null;clearDragTrackTarget(drag);
      (drag.members||[]).forEach(function(member){
        var element=dragDomRefs&&dragDomRefs.memberElements[member.id];
        if(!reactOwnsActiveDrag&&element)element.classList.remove('fs-dragging','fs-slipping','fs-rolling','fs-sliding');
        if(!reactOwnsActiveDrag&&element){
          element.removeAttribute('data-slip-delta');element.removeAttribute('data-roll-delta');element.removeAttribute('data-slide-delta');
        }
      });
    }
    gestureDomRefs=null;
    if(S.data&&S.data.exists)renderTimeline();
    clearTimelineGesturePreview();
    flushPendingExternalRevision();
  }
  window.filmStudioCancelClipGesture=function(e){
    if(!e||!window.filmStudioOwnsClipGesturePointer||
       !window.filmStudioOwnsClipGesturePointer(e.pointerId))return;
    cancelPointerGesture();
  };
  document.addEventListener('pointercancel',function(e){
    var target=e.target,reactTimeline=window.filmStudioReactTimeline&&
      typeof window.filmStudioReactTimeline.renderSurface==='function';
    var reactClip=target&&target.closest?target.closest('[data-react-timeline-clip]'):null;
    var nativeEnvelope=target&&target.closest?target.closest('[data-envelope-clip]'):null;
    // React already cancels timeline-clip gestures. Keep the document fallback for
    // native envelope-canvas gestures, which stop propagation before React sees them.
    if(reactTimeline&&reactClip&&!nativeEnvelope)return;
    if(window.filmStudioOwnsClipGesturePointer&&window.filmStudioOwnsClipGesturePointer(e.pointerId))return;
    if(window.filmStudioOwnsEnvelopeGesturePointer&&window.filmStudioOwnsEnvelopeGesturePointer(e.pointerId))return;
    // An unrelated pointercancel must never roll back another pointer's active gesture.
    if(S.drag&&S.drag.pointerId!==e.pointerId)return;
    if(S.envelopeDrag&&S.envelopeDrag.pointerId!==e.pointerId)return;
    cancelPointerGesture();
  });
  window.filmStudioSetSideMode=function(mode){
    // Both work surfaces stay mounted and visible. The mode only controls focus
    // for legacy/context-menu callers; it is not a visibility toggle.
    S.sideMode=mode==='ai'?'ai':'inspector';
    if(S.sideMode==='ai'){
      var input=$('fs-chat-input')||$('fs-static-chat-input');
      if(input)input.focus();
    }
  };
  function moveProjectSwitcherToStudio(inStudio){
    var row=$('editor-project-switch-row'),
        home=$('editor-project-switch-home'),
        host=$('fs-project-switch-host'),
        destination=inStudio?host:home;
    // Move the existing DOM node so project selection, inline onchange and
    // authority/revision guards stay single-owned; never mirror selector state.
    if(row&&destination&&row.parentNode!==destination)destination.appendChild(row);
  }
  window.filmStudioToggle=async function(knownFilmDocument){
    if(S.open){window.filmStudioClose();return false;}
    S.open=true;
    // Do not expose legacy Composition writers until film.html absence is confirmed.
    mountDirectorPlanning(false);
    var panel=$('film-studio-panel'),launcher=document.querySelector('.film-studio-launch');
    panel.classList.add('fs-studio-expanded');panel.style.display='flex';
    moveProjectSwitcherToStudio(true);
    document.documentElement.classList.add('fs-studio-open');
    if(launcher)launcher.style.display='none';
    $('film-studio-toggle').textContent='重新打开 Studio';
    $('editor-body').style.display='none';$('editor-empty').style.display='none';
    window.filmStudioSetSideMode(S.sideMode);
    var loaded=await filmStudioLoad(true,knownFilmDocument);
    if(S.poll)clearInterval(S.poll);
    S.poll=setInterval(async function(){
      if(!S.open||S.busy||S.drag||S.envelopeDrag)return;
      var r=await apiGet('/api/editor/film?project_id='+encodeURIComponent(S.projectId));
      if(r&&r.ok&&r.exists&&r.rev!==S.rev){
        if(S.proposal)markFilmProposalStale(r.rev,'影片已被外部修改。旧 AI 提案仍可查看或拒绝，但不能预览/应用。');
        S.pendingExternalRevision=r.rev;
        pendingRevisionSource='external_file';
        flushPendingExternalRevision();
      }
      else if(r&&!r.ok&&r.exists===true){
        if(!S.broken||(r.rev&&r.rev!==S.rev))renderBrokenFilmState(r);
      }
    },1600);
    return loaded;
  };
  // A project with film.html has one authoritative editor. Selecting it opens
  // Studio directly; legacy projects without film.html remain in the migration workspace.
  window.filmStudioOpenIfCurrentProjectHasFilm = async function(expectedProjectId,knownFilmDocument){
    var selector=$('editor-project');
    var projectId=String(expectedProjectId||selector&&selector.value||'').trim();
    if(!projectId||!selector||String(selector.value||'').trim()!==projectId)return false;
    var result=knownFilmDocument||null;
    if(!result){
      try{result=await apiGet('/api/editor/film?project_id='+encodeURIComponent(projectId));}
      catch(_){return false;}
    }
    if(!result||typeof result.exists!=='boolean'||result.exists!==true||
       (result.project_id&&String(result.project_id)!==projectId)||
       String(selector.value||'').trim()!==projectId)return false;
    if(S.open){
      if(S.projectId===projectId&&S.data&&S.data.exists&&S.rev===result.rev&&result.ok===true&&!S.broken)return true;
      // Reuse the mounted Studio and its draft/gesture guards. The known preflight
      // result is passed through, so the caller awaits the actual authority screen.
      return await filmStudioLoad(true,result);
    }
    if(document.documentElement.classList.contains('fs-studio-open')){
      window.filmStudioClose();
      if(S.open||document.documentElement.classList.contains('fs-studio-open'))return false;
    }
    return await window.filmStudioToggle(result);
  };
  window.filmStudioPrepareLegacyProjectOpen=function(projectId){
    var pid=String(projectId||'').trim();
    var studioVisible=S.open||document.documentElement.classList.contains('fs-studio-open');
    if(!studioVisible)return true;
    // Keep an existing Studio mounted when switching to a legacy project: the
    // editorLoad wrapper will load its explicit migration screen into this root.
    if(S.open)return S.projectId!==pid;
    window.filmStudioClose();
    return !S.open&&!document.documentElement.classList.contains('fs-studio-open');
  };
  // Project selection is routed by the single editorLoad wrapper above.
  // workspaceOpen() calls filmStudioOpenIfCurrentProjectHasFilm() explicitly;
  // do not also bind a change listener here or the initial selector change races
  // two independent FilmDocument preflights and Studio toggles.
  window.filmStudioClose=function(){
    if(hasUncommittedInspectorEdits()){
      if(!window.confirm('Inspector 中有未提交的属性草稿。退出 Studio 将放弃这些修改；是否继续？'))return;
      resetInspectorDraft();
    }
    S.open=false;if(S.poll){clearInterval(S.poll);S.poll=null;}
    // The React timeline menu is portaled to document.body, outside the hidden Studio panel.
    // Tear down the timeline surface so no menu or pointer-gesture state survives close.
    var timelineBridge=window.filmStudioReactTimeline;
    if(timelineBridge&&typeof timelineBridge.clearSurface==='function')timelineBridge.clearSurface();
    var panel=$('film-studio-panel'),launcher=document.querySelector('.film-studio-launch');
    if(panel){panel.classList.remove('fs-studio-expanded');panel.style.display='none';}
    moveProjectSwitcherToStudio(false);
    document.documentElement.classList.remove('fs-studio-open');
    if(launcher)launcher.style.display='flex';
    $('film-studio-toggle').textContent='重新打开 Studio';
    restoreDirectorPlanning();
    // Exiting the Studio never resumes the old CompositionIR editor for an active film.html project.
    var migrated=!!(S.data&&S.data.exists);
    $('editor-body').style.display=migrated?'none':((typeof ED!=='undefined'&&ED.pid)?'':'none');
    $('editor-empty').style.display=migrated?'none':((typeof ED!=='undefined'&&ED.pid)?'none':'');
  };
  window.filmStudioLoad=filmStudioLoad;window.filmStudioInitialize=filmStudioInitialize;
  window.filmStudioSelectClip=function(id){S.selectedId=id;S.selectedIds=id?[id]:[];S.selectionAnchor=id;var c=findClip(id);if(c)S.playhead=c.at;render();};
  window.filmStudioSeek=function(value){
    var wasPlaying=!!window.filmStudioPlaying;
    S.seekGeneration=Math.max(0,num(S.seekGeneration,0))+1;
    S.playhead=Math.max(0,Math.min(S.duration,num(value,0)));setPlaybackState(false);setPlayheadVisual();
    syncRuntime(wasPlaying?'seek-pause':undefined);
  };
  window.filmStudioJumpToKeyframe=function(clipId,at){
    var clip=findClip(String(clipId||''));
    if(!clip)return;
    var seekTo=Math.max(0,Number(clip.at)+Number(at||0));
    S.selectedId=clip.id;S.selectedIds=[clip.id];S.selectionAnchor=clip.id;
    syncClipSelectionVisual();renderInspector();renderAssets();syncDirectorSelection();
    window.filmStudioSeek(seekTo);
  };
  window.filmStudioNudge=function(direction){var fps=Math.max(1,Math.min(120,num(S.data&&S.data.fps,30)));window.filmStudioSeek(timelineSnapToFrame(S.playhead+direction/fps,fps));};
  window.filmStudioTogglePlayback=function(){
    if(!S.data||!S.data.exists){showStatus('请先初始化影片文档。','error');return;}
    setPlaybackState(!window.filmStudioPlaying);
    syncRuntime(window.filmStudioPlaying?'play':'pause');
  };
  window.filmStudioToggleElementInspect=function(){
    if(S.busy){showStatus('当前影片正在保存或生成提案，请完成后再操作画布。','error');return;}
    if(!S.data||!S.data.exists){showStatus('请先初始化 film.html 后再选取画面元素。','error');return;}
    S.elementInspect=!S.elementInspect;
    var frame=$('fs-preview-composition'),button=$('fs-element-inspect-toggle');
    if(S.elementInspect&&window.filmStudioPlaying){
      setPlaybackState(false);syncRuntime('pause');
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
  window.filmStudioCommitCanvasElement=async function(operation,options){
    if(S.busy){showStatus('当前影片正在保存；请等待完成后再提交画布修改。','error');return;}
    var selected=S.selectedElement;
    if(!selected){showStatus('画布选择已经失效，请重新选取元素。','error');return;}
    if(selected.baseRevision!==S.rev){
      showStatus('影片 revision 已变化；为防止覆盖旧画布状态，请重新选择元素。','error');
      S.selectedElement=null;
      var selectionBox=$('fs-element-selection');if(selectionBox)selectionBox.hidden=true;
      renderInspector();flushPendingExternalRevision();return;
    }
    if(!operation||operation.op!=='element_set'||operation.clip!==selected.clipId||operation.selector!==selected.selector){
      showStatus('画布编辑目标与当前选择不一致；本次没有写入。','error');return;
    }
    var previousRevision=S.rev;
    var committed=await filmStudioCommit([operation],{allowInspectorDraft:options&&options.allowInspectorDraft?'canvas':''});
    if(!committed||!S.data||!S.data.exists||S.rev===previousRevision)return;
    S.selectedElement=null;
    var selectionBox=$('fs-element-selection');if(selectionBox)selectionBox.hidden=true;
    renderInspector();renderAssets();
    showStatus('画布修改已保存到 film.html。若需继续编辑或让 AI 修改该元素，请在当前画面中重新选择。','success');
    flushPendingExternalRevision();
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
        backgroundEnabledInput=$('fs-element-background-enabled'),backgroundColorInput=$('fs-element-background-color'),
        opacityInput=$('fs-element-opacity'),fontWeightInput=$('fs-element-font-weight'),textAlignInput=$('fs-element-text-align'),
        lineHeightInput=$('fs-element-line-height'),letterSpacingInput=$('fs-element-letter-spacing'),
        fontInput=$('fs-element-font-size'),rotateInput=$('fs-element-rotate');
    if(!validEditableElementCssLength(lineHeightInput&&lineHeightInput.value,'lineHeight')){
      showStatus('行高必须是有效 CSS 长度值，且大于 0。','error');return;
    }
    if(!validEditableElementCssLength(letterSpacingInput&&letterSpacingInput.value,'letterSpacing')){
      showStatus('字间距必须是有效 CSS 长度值，且绝对值不能超过 1000。','error');return;
    }
    var values=[leftInput,topInput,widthInput,heightInput,fontInput,rotateInput,opacityInput,fontWeightInput].filter(Boolean).map(function(el){return Number(el.value);});
    if(values.some(function(value){return !Number.isFinite(value);})
      ||!widthInput||!heightInput||!fontInput||!rotateInput||!backgroundEnabledInput||!backgroundColorInput||!opacityInput||!fontWeightInput||!textAlignInput||!lineHeightInput||!letterSpacingInput
      ||!validEditableElementCssLength(lineHeightInput.value,'lineHeight')||!validEditableElementCssLength(letterSpacingInput.value,'letterSpacing')
      ||Number(widthInput.value)<=0||Number(heightInput.value)<=0
      ||Number(fontInput.value)<=0||Number(widthInput.value)>10000||Number(heightInput.value)>10000
      ||Number(fontInput.value)>1000||Math.abs(Number(rotateInput.value))>3600
      ||Number(opacityInput.value)<0||Number(opacityInput.value)>1
      ||Number(fontWeightInput.value)<100||Number(fontWeightInput.value)>900||Number(fontWeightInput.value)%100!==0
      ||['left','center','right','justify','start','end'].indexOf(textAlignInput.value)<0
      ||!/^#[0-9a-f]{6}$/i.test(backgroundColorInput.value)){
      showStatus('画布坐标、尺寸、字号或旋转角度超出有效范围。','error');return;
    }
    var rect=selected.localRect||selected.rect||{},styles=selected.styles||{},elementStyle={},
        rotateMatch=String(styles.rotate||'').match(/-?\d+(?:\.\d+)?/);
    var nextLeft=Number(leftInput.value),nextTop=Number(topInput.value),nextWidth=Number(widthInput.value),nextHeight=Number(heightInput.value);
    var leftChanged=Math.abs(nextLeft-num(rect.x,0))>0.11,topChanged=Math.abs(nextTop-num(rect.y,0))>0.11;
    var widthChanged=Math.abs(nextWidth-num(rect.width,1))>0.11,heightChanged=Math.abs(nextHeight-num(rect.height,1))>0.11;
    var geometryChanged=leftChanged||topChanged||widthChanged||heightChanged;
    if(geometryChanged&&String(styles.position||'')!=='fixed')elementStyle.position='fixed';
    if(leftChanged)elementStyle.left=nextLeft.toFixed(2)+'px';
    if(topChanged)elementStyle.top=nextTop.toFixed(2)+'px';
    if(widthChanged)elementStyle.width=nextWidth.toFixed(2)+'px';
    if(heightChanged)elementStyle.height=nextHeight.toFixed(2)+'px';
    if(hexColor(styles.color||'#ffffff').toLowerCase()!==colorInput.value.toLowerCase())elementStyle.color=colorInput.value;
    if(Math.abs(Number(fontInput.value)-num(parseFloat(styles.fontSize),24))>0.01)elementStyle.fontSize=Number(fontInput.value).toFixed(2)+'px';
    if(Math.abs(Number(rotateInput.value)-(rotateMatch?Number(rotateMatch[0]):0))>0.01)elementStyle.rotate=Number(rotateInput.value).toFixed(2)+'deg';
    var currentBackground=String(selected.styles&&selected.styles.backgroundColor||'transparent');
    var currentBackgroundTransparent=isTransparentBackground(currentBackground);
    if(backgroundEnabledInput.checked){
      if(currentBackgroundTransparent||hexColor(currentBackground).toLowerCase()!==backgroundColorInput.value.toLowerCase())elementStyle.backgroundColor=backgroundColorInput.value;
    }else if(!currentBackgroundTransparent){
      elementStyle.backgroundColor='transparent';
    }
    var currentOpacity=num(selected.styles&&selected.styles.opacity,1);
    if(Math.abs(Number(opacityInput.value)-currentOpacity)>1e-5)elementStyle.opacity=Number(opacityInput.value).toFixed(3);
    if(normalizedFontWeight(selected.styles&&selected.styles.fontWeight)!==Number(fontWeightInput.value))elementStyle.fontWeight=String(Number(fontWeightInput.value));
    var currentTextAlign=String(styles.textAlign||'left');
    if(currentTextAlign!==textAlignInput.value)elementStyle.textAlign=textAlignInput.value;
    var lineHeightValue=String(lineHeightInput.value||'').trim(),letterSpacingValue=String(letterSpacingInput.value||'').trim();
    if(lineHeightValue&&lineHeightValue!==String(styles.lineHeight||'').trim())elementStyle.lineHeight=lineHeightValue;
    if(letterSpacingValue&&letterSpacingValue!==String(styles.letterSpacing||'').trim())elementStyle.letterSpacing=letterSpacingValue;
    var elementTextChanged=!!(textInput&&!textInput.disabled&&textInput.value!==String(selected.text||''));
    if(!Object.keys(elementStyle).length&&!elementTextChanged)return;
    var elementOp={op:'element_set',clip:clip.id,selector:selected.selector,style:elementStyle};
    if(elementTextChanged)elementOp.text=textInput.value;
    var previousRevision=S.rev;
    var committed=await filmStudioCommit([elementOp],{allowInspectorDraft:'canvas'});
    if(!committed||S.rev===previousRevision)return;
    // The commit triggers a full, revision-bound reload. Keep selection context
    // visible, but the user can reselect when CSS/structure changes the selector.
    if(S.data&&S.data.exists){
      // The runtime and the parent selection geometry were just rebuilt against
      // the new revision. Drop the old geometry/context instead of showing stale
      // text or letting a subsequent AI turn target an obsolete element snapshot.
      S.selectedElement=null;
      var selectionBox=$('fs-element-selection');if(selectionBox)selectionBox.hidden=true;
      renderInspector();renderAssets();
      showStatus('画布修改已保存到 film.html。若需继续编辑或让 AI 修改该元素，请在当前画面中重新选择。','success');
      flushPendingExternalRevision();
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
        flushPendingSceneRefresh();
        return;
      }
      if(message.rev&&hasRuntimeData&&message.rev!==expectedRuntimeRev){
        S.runtimeRev='';
        syncRuntime(S.runtimeCommand||(window.filmStudioPlaying?'play':'seek'));
      }else if(message.rev&&hasRuntimeData&&message.rev===expectedRuntimeRev){
        S.runtimeRev=expectedRuntimeRev;
        if(S.elementInspect)frame.contentWindow.postMessage({type:'film-runtime:inspect',enabled:true},window.location.origin);
      }
      flushPendingSceneRefresh();
    }else if(message.type==='film-runtime:scenes-refreshed'&&message.project_id===S.projectId){
      var refreshedCount=Math.max(0,num(message.count,0));
      var failedCount=Math.max(0,num(message.failed,0));
      if(failedCount){
        showStatus('检测到 scenes/ 文件已变化，已尝试刷新 '+refreshedCount+' 个 HTML 镜头；'+failedCount+' 个镜头加载失败，请检查场景文件及其资源。','error');
      }else if(refreshedCount){
        showStatus('检测到外部场景文件变更，已刷新 '+refreshedCount+' 个 HTML 镜头预览；film.html revision 未变化。','success');
      }else{
        showStatus('检测到 scenes/ 文件变更，但当前影片没有引用 scenes/ 下的 HTML 镜头。','');
      }
    }else if(message.type==='film-runtime:inspect-ready'){
      // A late ready acknowledgement must not overwrite the more specific
      // "element selected" state after a pointer event has already arrived.
      if(S.elementInspect&&message.enabled&&!S.selectedElement)showStatus('预览中的 HTML 镜头已进入元素选取模式。','success');
      else if(S.elementInspect&&message.enabled===false)showStatus('预览中的 HTML 镜头无法进入元素选取模式：'+String(message.error||'绘制队列未能完成'),'error');
    }else if(message.type==='film-runtime:override-warning'){
      showStatus('HTML 镜头 overrides 提示：'+String(message.warning||'存在未执行的遗留修改'),'');
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
        
        if(S.elementInspect)renderElementSelection(S.selectedElement||element);
      }else if(message.type==='film-runtime:element-preview'){
        if(S.selectedElement&&S.selectedElement.clipId===element.clipId&&S.selectedElement.selector===element.selector){
          S.selectedElement=Object.assign({},S.selectedElement,{
            rect:element.rect,localRect:element.localRect,styles:element.styles,baseRevision:S.rev
          });
          renderElementSelection(S.selectedElement);syncElementInspectorFields(S.selectedElement);
        }
      }else if(message.type==='film-runtime:element-drag-end'){
        if(S.selectedElement&&S.selectedElement.clipId===element.clipId&&S.selectedElement.selector===element.selector){
          S.selectedElement=Object.assign({},S.selectedElement,{
            rect:element.rect,localRect:element.localRect,styles:element.styles,text:element.text,baseRevision:S.rev
          });
        }else{
          S.selectedElement=element;S.selectedId=element.clipId;S.selectedIds=[element.clipId];
        }
        renderElementSelection(S.selectedElement);syncElementInspectorFields(S.selectedElement);
        window.filmStudioSaveSelectedElement();
      }
    }else if(message.type==='film-runtime:time'||message.type==='film-runtime:ended'||message.type==='film-runtime:paused'){
      var responseSeekId=Number(message.seekId);
      // Runtime replies can arrive after a newer keyboard/pointer seek. Never let
      // an older (or pre-protocol) playback event overwrite the current playhead.
      if(S.seekGeneration>0&&(!Number.isFinite(responseSeekId)||responseSeekId<S.seekGeneration))return;
      S.playhead=Math.max(0,Math.min(S.duration,num(message.time,S.playhead)));
      setPlaybackState(message.type==='film-runtime:time'&&!!message.playing);setPlayheadVisual();
      syncPlaybackButtonText();
    }
  });
  window.filmStudioZoom=function(direction,anchorClientX){
    var timeline=window.filmStudioReactTimeline;
    if(timeline&&typeof timeline.zoom==='function'){
      timeline.zoom(direction,anchorClientX);
      return;
    }
    // Unbundled fallback only: the active React timeline owns zoom and anchoring.
    var scroll=$('fs-timeline-scroll'),rect=scroll&&scroll.getBoundingClientRect();
    var localX=rect?(Number.isFinite(anchorClientX)?anchorClientX-rect.left:rect.width/2):0;
    var anchorTime=scroll?Math.max(0,timelineZoomAnchorTime(scroll.scrollLeft,localX,S.pxPerSec,132)):S.playhead;
    S.pxPerSec=timelineZoomScale(S.pxPerSec,direction);
    if(S.data&&S.data.exists){
      renderTimeline();
      if(scroll&&rect)scroll.scrollLeft=timelineZoomScrollLeft(anchorTime,localX,S.pxPerSec,132);
    }
  };
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
  window.filmStudioSaveInspector=async function(){
    // The React inspector owns its own draft state; ask it to run its validated
    // save path rather than reading stale DOM values from the compatibility form.
    var reactApply=$('fs-apply-clip-inspector');
    if(window.filmStudioReactInspector&&reactApply){
      reactApply.click();
      return true;
    }
    var clip=findClip(S.selectedId);
    if(!clip){showStatus('请先选择需要应用属性的片段。','error');return false;}
    var attrs=clip.attrs||{},ops=[];
    if(clip.trackLocked||Object.prototype.hasOwnProperty.call(attrs,'data-clip-locked')){
      showStatus('片段或所在轨道已锁定，不能应用属性。','error');return false;
    }
    function numberField(id,field,minimum,maximum,fallback,label){
      var input=$(id);
      if(!input)return;
      var raw=input.value,value=Number(raw),previous=attrs[field]==null?fallback:Number(attrs[field]);
      if(!raw.trim()||!Number.isFinite(value)||value<minimum||(maximum!=null&&value>maximum)){
        throw new Error(label+'超出允许范围。');
      }
      if(!Number.isFinite(previous)||Math.abs(value-previous)>0.00001){
        ops.push({op:'set',clip:clip.id,field:field,value:value});
      }
    }
    try{
      numberField('fs-field-at','at',0,null,clip.at||0,'时间位置');
      if(clip.tag==='video'||clip.tag==='audio'){
        numberField('fs-field-speed','speed',0.25,4,1,'速度');
        numberField('fs-field-volume','volume',0,2,1,'音量');
        var muted=$('fs-field-muted');
        if(muted&&(muted.value==='true')!==Object.prototype.hasOwnProperty.call(attrs,'muted')){
          ops.push({op:'set',clip:clip.id,field:'muted',value:muted.value==='true'});
        }
      }
      var title=$('fs-field-title');
      if(title&&String(title.value)!==String(attrs.title||'')){
        ops.push({op:'set',clip:clip.id,field:'title',value:title.value||null});
      }
      if(clip.duration!=null){
        var fadeInInput=$('fs-field-fade-in'),fadeOutInput=$('fs-field-fade-out');
        if(fadeInInput&&fadeOutInput){
          var fadeIn=Number(fadeInInput.value),fadeOut=Number(fadeOutInput.value);
          if(!fadeInInput.value.trim()||!fadeOutInput.value.trim()||!Number.isFinite(fadeIn)||!Number.isFinite(fadeOut)
            ||fadeIn<0||fadeOut<0||fadeIn>60||fadeOut>60||fadeIn+fadeOut>clip.duration+0.001){
            throw new Error('淡入/淡出必须是有效数字，且时长之和不能超过片段时长。');
          }
          var oldFade=String(attrs.fade||'0,0').split(',');
          if(Math.abs(Number(oldFade[0]||0)-fadeIn)>0.001||Math.abs(Number(oldFade[1]||0)-fadeOut)>0.001){
            ops.push({op:'set',clip:clip.id,field:'fade',value:fadeIn>0||fadeOut>0?fadeIn.toFixed(3)+','+fadeOut.toFixed(3):null});
          }
        }
      }
      var keyframeInput=$('fs-field-keyframes');
      if(keyframeInput&&keyframeInput.value!==String(attrs['data-film-keyframes']||'')){
        var raw=keyframeInput.value.trim(),channels=null;
        if(raw){
          try{channels=JSON.parse(raw);}catch(_){throw new Error('视觉关键帧必须是有效 JSON。');}
          if(!Array.isArray(channels))throw new Error('视觉关键帧必须是 JSON 数组。');
        }
        ops.push({op:'set',clip:clip.id,field:'data-film-keyframes',value:channels});
      }
    }catch(error){
      showStatus(String(error&&error.message||error),'error');
      return false;
    }
    if(!ops.length){showStatus('属性没有变化。');return true;}
    try{
      return await window.filmStudioCommitInspectorOperations(ops);
    }catch(error){
      showStatus(String(error&&error.message||error),'error');
      return false;
    }
  };
  window.filmStudioCommitInspectorOperations=async function(ops){
    if(!Array.isArray(ops)||!ops.length)return false;
    if(!S.projectId||!S.data||!S.data.exists)throw new Error('当前没有可编辑的 film.html。');
    if(S.busy)throw new Error('上一项编辑仍在保存；本次修改未提交，请重试。');
    if(S.broken)throw new Error('film.html 无效，当前不能写入。');
    var committed=await filmStudioCommit(ops,{allowInspectorDraft:'clip'});
    if(!committed){
      var status=$('film-studio-alert');
      throw new Error(status&&status.textContent||'片段属性没有保存，请检查影片状态后重试。');
    }
    return true;
  };
  window.filmStudioAddTimelineMarker=async function(){
    if(!S.data||!S.data.exists||!S.projectId){showStatus('请先打开一个有效的 film.html 项目。','error');return false;}
    var at=Math.max(0,Number(S.playhead)||0);
    at=Math.round(at*1000)/1000;
    var label=window.prompt('影片标记名称（位置：'+fmtTime(at)+'）','标记 '+fmtTime(at));
    if(label===null)return false;
    label=String(label).trim();
    if(!label){showStatus('影片标记名称不能为空。','error');return false;}
    var id='marker-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8);
    try{return await filmStudioCommit([{op:'marker_add',id:id,at:at,label:label}]);}
    catch(error){showStatus(String(error&&error.message||error),'error');return false;}
  };
  window.filmStudioEditTimelineMarker=async function(markerId){
    var marker=(S.data&&Array.isArray(S.data.markers)?S.data.markers:[]).find(function(item){return item&&item.id===markerId;});
    if(!marker){showStatus('影片标记已不存在；请刷新后重试。','error');return false;}
    var label=window.prompt('修改标记名称；留空后确认可删除。',String(marker.label||''));
    if(label===null)return false;
    label=String(label).trim();
    try{
      if(!label){
        if(!window.confirm('删除影片标记“'+String(marker.label||marker.id)+'”？此操作可撤销。'))return false;
        return await filmStudioCommit([{op:'marker_remove',id:marker.id}]);
      }
      if(label===marker.label)return true;
      return await filmStudioCommit([{op:'marker_set',id:marker.id,label:label}]);
    }catch(error){showStatus(String(error&&error.message||error),'error');return false;}
  };
  window.filmStudioSplit=async function(){var clip=findClip(S.selectedId);if(!clip){showStatus('请先选择需要切分的片段。','error');return;}await filmStudioCommit([{op:'split',clip:clip.id,at:S.playhead}]);};
  window.filmStudioRemove=async function(){var ids=(S.selectedIds||[]).filter(function(id){return !!findClip(id);});if(!ids.length&&S.selectedId)ids=[S.selectedId];if(!ids.length){showStatus('请先选择片段。','error');return;}if(!window.confirm('删除选中的 '+ids.length+' 个片段并保留时间线空隙？此操作可撤销。'))return;var committed=await filmStudioCommit(ids.map(function(id){return {op:'remove',clip:id};}));if(!committed)return;S.selectedId='';S.selectedIds=[];S.selectionAnchor='';render();};
window.filmStudioCopyClips=function(){
    if(!S.data||!S.data.exists||!S.projectId){showStatus('请先打开一个有效的 film.html 项目。','error');return false;}
    var ids=(S.selectedIds||[]).filter(function(id,index,all){return !!findClip(id)&&all.indexOf(id)===index;});
    if(!ids.length&&S.selectedId&&findClip(S.selectedId))ids=[S.selectedId];
    if(!ids.length){showStatus('请先选择需要复制的时间线片段。','error');return false;}
    var selected=ids.map(function(id){return findClip(id);});
    var baseAt=Math.min.apply(null,selected.map(function(clip){return Number(clip.at);}));
    var items=[];
    for(var i=0;i<selected.length;i++){
      var clip=selected[i],duration=Number(clip.duration);
      if(!Number.isFinite(duration)||duration<=0){
        showStatus('片段 '+clip.id+' 没有可证明的有限时长，已拒绝复制。','error');return false;
      }
      if(!clip.track_id||!clip.attrs||!clip.attrs.src){
        showStatus('片段 '+clip.id+' 缺少轨道或素材信息，已拒绝复制。','error');return false;
      }
      items.push({
        sourceId:clip.id,trackId:clip.track_id,tag:clip.tag,duration:duration,
        offset:Math.max(0,Number(clip.at)-baseAt),attrs:Object.assign({},clip.attrs)
      });
    }
    items.sort(function(a,b){return a.offset-b.offset||a.trackId.localeCompare(b.trackId)||a.sourceId.localeCompare(b.sourceId);});
    S.clipClipboard={projectId:S.projectId,items:items,duration:Math.max.apply(null,items.map(function(item){return item.offset+item.duration;}))};
    showStatus('已复制 '+items.length+' 个片段。使用 Ctrl/⌘+V 粘贴到播放头；只在当前项目内有效。','success');
    return true;
  };
  window.filmStudioPasteClips=async function(){
    var clipboard=S.clipClipboard;
    if(!S.data||!S.data.exists||!S.projectId||S.busy){showStatus('当前没有可写入的有效 film.html 项目。','error');return false;}
    if(!clipboard||!Array.isArray(clipboard.items)||!clipboard.items.length){showStatus('剪辑板为空；请先选择片段并复制。','error');return false;}
    if(clipboard.projectId!==S.projectId){showStatus('剪辑板来自另一个项目。为避免引用失效的项目素材，请在来源项目重新复制。','error');return false;}
    var tracks=Object.create(null),existingIds=Object.create(null);
    (S.data.tracks||[]).forEach(function(track){tracks[track.id]=track;(track.clips||[]).forEach(function(clip){existingIds[clip.id]=true;});});
    for(var i=0;i<clipboard.items.length;i++){
      var item=clipboard.items[i],track=tracks[item.trackId];
      if(!track){showStatus('原轨道 '+item.trackId+' 已不存在；请重新复制或恢复原轨道。','error');return false;}
      if(Object.prototype.hasOwnProperty.call(track.attrs||{},'locked')){
        showStatus('原轨道 '+String((track.attrs||{})['data-label']||track.id)+' 已锁定，不能在该轨道粘贴。请先解锁。','error');return false;
      }
      if(!Number.isFinite(item.duration)||item.duration<=0){
        showStatus('剪辑板中有时长不明确的片段，已拒绝粘贴。','error');return false;
      }
      if((track.clips||[]).some(function(clip){return !Number.isFinite(Number(clip.duration))||Number(clip.duration)<=0;})){
        showStatus('轨道 '+track.id+' 含有未知时长片段，无法证明粘贴不重叠。','error');return false;
      }
    }
    var frameStep=1/Math.max(1,Number((S.data||{}).fps)||30);
    var candidate=Math.max(0,Number(S.playhead)||0),placementFound=false;
    for(var attempt=0;attempt<5000;attempt++){
      var nextCandidate=candidate,blocked=false;
      for(var ci=0;ci<clipboard.items.length;ci++){
        var copied=clipboard.items[ci],targetTrack=tracks[copied.trackId];
        var start=candidate+copied.offset,end=start+copied.duration;
        for(var ei=0;ei<(targetTrack.clips||[]).length;ei++){
          var old=targetTrack.clips[ei],oldStart=Number(old.at),oldEnd=oldStart+Number(old.duration);
          if(start<oldEnd-0.001&&end>oldStart+0.001){
            blocked=true;
            nextCandidate=Math.max(nextCandidate,oldEnd-copied.offset+frameStep);
          }
        }
      }
      if(!blocked){placementFound=true;break;}
      if(!Number.isFinite(nextCandidate)||nextCandidate<=candidate+0.0001){candidate+=frameStep;}
      else candidate=nextCandidate;
    }
    if(!placementFound){showStatus('无法在现有轨道中找到不重叠的粘贴区间；未写入任何修改。','error');return false;}
    var ops=[],newIds=Object.create(null),created=[];
    function createId(){
      var id='';
      do{id='clip-copy-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,10);}
      while(existingIds[id]||newIds[id]);
      newIds[id]=true;return id;
    }
    clipboard.items.forEach(function(item){
      var id=createId(),attrs=Object.assign({},item.attrs);
      attrs.id=id;attrs.at=String(Math.round((candidate+item.offset)*1000)/1000);
      ops.push({op:'insert',track_id:item.trackId,clip:{id:id,tag:item.tag,attrs:attrs}});
      created.push(id);
    });
    var committed=await filmStudioCommit(ops);
    if(!committed)return false;
    S.selectedIds=created.slice();S.selectedId=created[0]||'';S.selectionAnchor=S.selectedId;
    renderTimeline();syncClipSelectionVisual();renderInspector();renderAssets();
    showStatus('已粘贴 '+created.length+' 个片段，起始时间 '+fmtTime(candidate)+'；全部修改可作为一个事务撤销。','success');
    return true;
  };
  window.filmStudioDuplicateClips=async function(clipId){
    var targetId=clipId||S.selectedId;
    if(targetId&&(!S.selectedIds||S.selectedIds.indexOf(targetId)<0)){
      S.selectedId=targetId;S.selectedIds=[targetId];S.selectionAnchor=targetId;
      syncClipSelectionVisual();renderInspector();renderAssets();
    }
    if(!window.filmStudioCopyClips())return false;
    var selection=(S.selectedIds||[]).map(function(id){return findClip(id);}).filter(Boolean);
    if(!selection.length)return false;
    var originalPlayhead=S.playhead;
    S.playhead=Math.max.apply(null,selection.map(function(clip){return clip.end==null?clip.at+num(clip.duration,0):clip.end}));
    setPlayheadVisual();
    var result=false;
    try{result=await window.filmStudioPasteClips();}
    finally{S.playhead=originalPlayhead;setPlayheadVisual();}
    return result;
  };
  var activeTimelineContextMenu=null;
  function closeTimelineContextMenu(){
    if(activeTimelineContextMenu&&activeTimelineContextMenu.parentNode)activeTimelineContextMenu.parentNode.removeChild(activeTimelineContextMenu);
    activeTimelineContextMenu=null;
    var bridge=window.filmStudioReactTimeline;
    if(bridge&&typeof bridge.closeContextMenu==='function')bridge.closeContextMenu();
  }
  window.filmStudioOpenTimelineContextMenu=function(clipId,clientX,clientY){
    if((S.selectedIds||[]).indexOf(clipId)<0)selectClipWithModifiers(clipId,{});
    closeTimelineContextMenu();
    var bridge=window.filmStudioReactTimeline;
    if(bridge&&typeof bridge.openContextMenu==='function')
      bridge.openContextMenu({clipId:clipId,x:Number(clientX)||0,y:Number(clientY)||0});
  };
  window.filmStudioApplyFadeTransition=async function(clipId){
    var current=findClip(clipId);
    if(!current||!S.data||!S.data.exists){showStatus('请先选择一个有效的影片片段。','error');return false;}
    var track=(S.data.tracks||[]).find(function(item){return item.id===current.track_id;});
    if(!track){showStatus('无法定位所选片段的轨道。','error');return false;}
    if(Object.prototype.hasOwnProperty.call(track.attrs||{},'locked')
      ||Object.prototype.hasOwnProperty.call(current.attrs||{},'data-clip-locked')){
      showStatus('当前片段或轨道已锁定，不能添加淡化衔接。','error');return false;
    }
    var ordered=(track.clips||[]).slice().sort(function(a,b){return Number(a.at)-Number(b.at);});
    var index=ordered.findIndex(function(item){return item.id===current.id;});
    var next=index>=0?ordered[index+1]:null;
    if(!next){showStatus('所选片段后面没有同轨片段，不能添加淡化衔接。','error');return false;}
    if(Object.prototype.hasOwnProperty.call(next.attrs||{},'data-clip-locked')){
      showStatus('下一片段已锁定，不能添加淡化衔接。','error');return false;
    }
    var previousAt=Number(current.at),nextAt=Number(next.at);
    var previousDuration=Number(current.duration),nextDuration=Number(next.duration);
    var previousEnd=current.end==null?previousAt+previousDuration:Number(current.end);
    if(!Number.isFinite(previousAt)||!Number.isFinite(nextAt)
      ||!Number.isFinite(previousDuration)||!Number.isFinite(nextDuration)
      ||previousDuration<=0||nextDuration<=0||!Number.isFinite(previousEnd)){
      showStatus('两个片段必须有已知且有效的时长，才能添加淡化衔接。','error');return false;
    }
    if(Math.abs(previousEnd-nextAt)>0.001){
      showStatus('淡化衔接只适用于同轨道上时间相邻、没有空隙或重叠的两个片段；未更改影片。','error');return false;
    }
    function readFade(item){
      var values=String((item.attrs||{}).fade||'0,0').split(',');
      var fadeIn=Number(values[0]||0),fadeOut=Number(values[1]||0);
      if(!Number.isFinite(fadeIn)||!Number.isFinite(fadeOut)||fadeIn<0||fadeOut<0)return null;
      return {in:fadeIn,out:fadeOut};
    }
    var previousFade=readFade(current),nextFade=readFade(next);
    if(!previousFade||!nextFade
      ||previousFade.in+previousFade.out>previousDuration+0.001
      ||nextFade.in+nextFade.out>nextDuration+0.001){
      showStatus('现有淡入/淡出参数无效，请先在属性检查器修正。','error');return false;
    }
    var previousAvailable=previousDuration-previousFade.in;
    var nextAvailable=nextDuration-nextFade.out;
    var requested=Math.min(0.5,previousDuration/2,nextDuration/2,previousAvailable,nextAvailable);
    var duration=Math.max(requested,previousFade.out,nextFade.in);
    if(!Number.isFinite(duration)||duration<0.05
      ||duration>previousAvailable+0.001||duration>nextAvailable+0.001){
      showStatus('现有淡入/淡出占用了可用时长，无法形成两侧等长的淡化衔接；未更改影片。','error');return false;
    }
    duration=Number(duration.toFixed(3));
    if(duration<=0){showStatus('淡化时长必须大于零。','error');return false;}
    var previousValue=previousFade.in.toFixed(3)+','+duration.toFixed(3);
    var nextValue=duration.toFixed(3)+','+nextFade.out.toFixed(3);
    var ops=[];
    if(String((current.attrs||{}).fade||'0,0')!==previousValue){
      ops.push({op:'set',clip:current.id,field:'fade',value:previousValue});
    }
    if(String((next.attrs||{}).fade||'0,0')!==nextValue){
      ops.push({op:'set',clip:next.id,field:'fade',value:nextValue});
    }
    if(!ops.length){showStatus('这两个片段已经有对应的淡化衔接，无需更改。');return true;}
    showStatus('正在为两个相邻片段提交淡出/淡入衔接（各 '+duration.toFixed(3)+' 秒）；片段位置不变。');
    return await filmStudioCommit(ops);
  };
  window.filmStudioApplyCrossfadeTransition=async function(clipId){
    var current=findClip(clipId);
    if(!current||!S.data||!S.data.exists){showStatus('请先选择一个有效的影片片段。','error');return false;}
    var track=(S.data.tracks||[]).find(function(item){return item.id===current.track_id;});
    if(!track){showStatus('无法定位所选片段的轨道。','error');return false;}
    if(Object.prototype.hasOwnProperty.call(track.attrs||{},'locked')
      ||Object.prototype.hasOwnProperty.call(current.attrs||{},'data-clip-locked')){
      showStatus('当前片段或轨道已锁定，不能添加交叉淡化。','error');return false;
    }
    var ordered=(track.clips||[]).slice().sort(function(a,b){return Number(a.at)-Number(b.at);});
    var index=ordered.findIndex(function(item){return item.id===current.id;});
    var next=index>=0?ordered[index+1]:null;
    if(!next){showStatus('所选片段后面没有同轨片段，不能添加交叉淡化。','error');return false;}
    if(Object.prototype.hasOwnProperty.call(next.attrs||{},'data-clip-locked')){
      showStatus('下一片段已锁定，不能添加交叉淡化。','error');return false;
    }
    var currentTag=String(current.tag||''),nextTag=String(next.tag||'');
    var audioPair=currentTag==='audio'&&nextTag==='audio';
    var visualTags=['video','img','iframe'];
    var visualPair=visualTags.indexOf(currentTag)>=0&&visualTags.indexOf(nextTag)>=0;
    if(!audioPair&&!visualPair){
      showStatus('交叉淡化要求两个相邻片段属于同一种视觉或音频类型；未更改影片。','error');return false;
    }
    var previousAt=Number(current.at),nextAt=Number(next.at);
    var previousDuration=Number(current.duration),nextDuration=Number(next.duration);
    var previousEnd=current.end==null?previousAt+previousDuration:Number(current.end);
    if(!Number.isFinite(previousAt)||!Number.isFinite(nextAt)
      ||!Number.isFinite(previousDuration)||!Number.isFinite(nextDuration)
      ||previousDuration<=0||nextDuration<=0||!Number.isFinite(previousEnd)){
      showStatus('两个片段必须有已知且有效的时长，才能添加交叉淡化。','error');return false;
    }
    if(Math.abs(previousEnd-nextAt)>0.001){
      showStatus('交叉淡化只适用于同轨道上原本时间相邻、没有空隙或重叠的两个片段；未更改影片。','error');return false;
    }
    function readFade(item){
      var values=String((item.attrs||{}).fade||'0,0').split(',');
      var fadeIn=Number(values[0]||0),fadeOut=Number(values[1]||0);
      if(!Number.isFinite(fadeIn)||!Number.isFinite(fadeOut)||fadeIn<0||fadeOut<0)return null;
      return {in:fadeIn,out:fadeOut};
    }
    var previousFade=readFade(current),nextFade=readFade(next);
    if(!previousFade||!nextFade
      ||previousFade.in+previousFade.out>previousDuration+0.001
      ||nextFade.in+nextFade.out>nextDuration+0.001){
      showStatus('现有淡入/淡出参数无效，请先在属性检查器修正。','error');return false;
    }
    if(visualPair&&previousFade.out>0.001){
      showStatus('视觉交叉淡化需要当前片段在重叠段保持不透明；请先移除当前片段已有的淡出。','error');return false;
    }
    var previousAvailable=previousDuration-previousFade.in;
    var nextAvailable=nextDuration-nextFade.out;
    var limit=Math.min(previousDuration/2,nextDuration/2,previousAvailable,nextAvailable);
    var requested=Math.min(0.5,limit);
    var duration=Math.max(requested,audioPair?previousFade.out:0,nextFade.in);
    if(!Number.isFinite(duration)||duration<0.05||duration>limit+0.001){
      showStatus('现有淡入/淡出占用了可用时长，无法形成安全的交叉淡化重叠；未更改影片。','error');return false;
    }
    duration=Number(duration.toFixed(3));
    if(duration<=0){showStatus('交叉淡化时长必须大于零。','error');return false;}
    var newAt=Number((previousEnd-duration).toFixed(3));
    if(newAt<=previousAt+0.001){
      showStatus('交叉淡化会使片段可见时长过短；未更改影片。','error');return false;
    }
    // Visual alpha fade and native video audio are separate channels. Keep the
    // outgoing image opaque, but fade its source audio over the same interval.
    // A pre-existing gain envelope is authored data; never overwrite it silently.
    var outgoingAudioEnvelope=null;
    var currentVolume=Number(current.attrs&&current.attrs.volume!=null?current.attrs.volume:1);
    var audioAlreadyMuted=Object.prototype.hasOwnProperty.call(track.attrs||{},'muted')
      ||Object.prototype.hasOwnProperty.call(track.attrs||{},'hidden')
      ||Object.prototype.hasOwnProperty.call(current.attrs||{},'muted')
      ||!Number.isFinite(currentVolume)||currentVolume<=0;
    if(visualPair&&currentTag==='video'&&!audioAlreadyMuted){
      var existingEnvelope=current.attrs&&current.attrs.volume_envelope;
      if(existingEnvelope!=null&&String(existingEnvelope).trim()!==''){
        showStatus('当前视频已有自定义音量包络。为避免覆盖该曲线，不能自动添加交叉淡化；请先明确调整或清除音量包络。','error');return false;
      }
      if(previousDuration>600.001){
        showStatus('当前视频超过音量包络支持的 600 秒范围，无法安全生成原声音量淡出。','error');return false;
      }
      outgoingAudioEnvelope=[
        {t:0,v:1},
        {t:Number((previousDuration-duration).toFixed(3)),v:1},
        {t:Number(previousDuration.toFixed(3)),v:0}
      ];
    }
    // Keep this track contiguous: move every later clip by the same overlap
    // delta. A locked successor blocks the whole atomic transition.
    var successors=ordered.filter(function(item){
      return item.id!==current.id&&item.id!==next.id&&Number(item.at)>=nextAt-0.001;
    });
    var lockedSuccessor=successors.find(function(item){
      return Object.prototype.hasOwnProperty.call(item.attrs||{},'data-clip-locked');
    });
    if(lockedSuccessor){
      showStatus('后续片段 '+lockedSuccessor.id+' 已锁定；交叉淡化不会移动锁定内容。','error');return false;
    }
    var previousOut=audioPair?duration:0;
    var previousValue=previousFade.in.toFixed(3)+','+previousOut.toFixed(3);
    var nextValue=duration.toFixed(3)+','+nextFade.out.toFixed(3);
    var ops=[];
    if(outgoingAudioEnvelope){
      ops.push({op:'set',clip:current.id,field:'volume_envelope',value:outgoingAudioEnvelope});
    }
    if(Math.abs(previousFade.out-previousOut)>0.001||String((current.attrs||{}).fade||'')===''){
      if(Math.abs(previousFade.in)>0.001||Math.abs(previousOut)>0.001)
        ops.push({op:'set',clip:current.id,field:'fade',value:previousValue});
    }
    if(Math.abs(nextFade.in-duration)>0.001||String((next.attrs||{}).fade||'')===''){
      ops.push({op:'set',clip:next.id,field:'fade',value:nextValue});
    }
    ops.push({op:'move',clip:next.id,track_id:track.id,at:newAt});
    successors.forEach(function(item){
      ops.push({
        op:'move',clip:item.id,track_id:track.id,
        at:Number((Number(item.at)-duration).toFixed(3))
      });
    });
    showStatus('正在提交 '+duration.toFixed(3)+' 秒交叉淡化：下一片段与当前片段重叠，同轨后续片段随之左移；其他轨道不变。');
    return await filmStudioCommit(ops);
  };
  window.filmStudioContextMenuAction=function(action,clipId){
    closeTimelineContextMenu();
    if(action==='copy')return window.filmStudioCopyClips();
    if(action==='duplicate')return window.filmStudioDuplicateClips(clipId);
    if(action==='paste')return window.filmStudioPasteClips();
    if(action==='fade-transition')return window.filmStudioApplyFadeTransition(clipId);
    if(action==='crossfade-transition')return window.filmStudioApplyCrossfadeTransition(clipId);
    if(action==='split'){
      var clip=findClip(clipId);
      if(!clip||S.playhead<=clip.at+0.001||clip.end==null||S.playhead>=clip.end-0.001){
        showStatus('播放头必须位于选中片段内部才能切分。','error');return false;
      }
      S.selectedId=clipId;if((S.selectedIds||[]).indexOf(clipId)<0)S.selectedIds=[clipId];
      return window.filmStudioSplit();
    }
    if(action==='delete')return window.filmStudioRemove();
    if(action==='ripple')return window.filmStudioRippleDelete();
    if(action==='inspector')return window.filmStudioSetSideMode('inspector');
    return false;
  };
  document.addEventListener('contextmenu',function(event){
    var clipElement=event.target&&event.target.closest?event.target.closest('#fs-timeline .fs-clip[data-clip]'):null;
    var bridge=window.filmStudioReactTimeline;
    var reactTimeline=bridge&&typeof bridge.renderSurface==='function';
    if(reactTimeline){
      if(!clipElement)closeTimelineContextMenu();
      return;
    }
    if(!clipElement){closeTimelineContextMenu();return;}
    event.preventDefault();
    var clipId=clipElement.getAttribute('data-clip');
    if((S.selectedIds||[]).indexOf(clipId)<0)selectClipWithModifiers(clipId,{});
    closeTimelineContextMenu();
    var menu=document.createElement('div');
    menu.className='fs-context-menu';
    menu.setAttribute('role','menu');
    menu.innerHTML=[
      '<button type="button" role="menuitem" data-context-action="copy">复制所选片段</button>',
      '<button type="button" role="menuitem" data-context-action="duplicate">重复所选片段</button>',
      '<button type="button" role="menuitem" data-context-action="paste">粘贴到播放头</button>',
      '<div class="fs-context-separator"></div>',
      '<button type="button" role="menuitem" data-context-action="split">在播放头切分</button>',
      '<button type="button" role="menuitem" data-context-action="fade-transition" title="同轨时间相邻片段，淡出当前片段并淡入下一片段；不重叠、不改变位置">淡出并淡入下一片段</button>',
      '<button type="button" role="menuitem" data-context-action="crossfade-transition" title="两段视觉片段或两段音频重叠淡化；下一片段提前，影片总时长相应缩短">交叉淡化（重叠）</button>',
      '<button type="button" role="menuitem" data-context-action="delete" title="只移除所选片段，后续内容时间不变">删除并保留空隙</button>',
      '<button type="button" role="menuitem" data-context-action="ripple" title="删除所选时间段，并收拢所有受影响轨道">波纹删除（收拢时间线）</button>',
      '<div class="fs-context-separator"></div>',
      '<button type="button" role="menuitem" data-context-action="inspector">打开属性检查器</button>'
    ].join('');
    menu.style.left=Math.max(8,Math.min(event.clientX,window.innerWidth-300))+'px';
    menu.style.top=Math.max(8,Math.min(event.clientY,window.innerHeight-360))+'px';
    document.body.appendChild(menu);
    activeTimelineContextMenu=menu;
    menu.addEventListener('contextmenu',function(e){e.preventDefault();e.stopPropagation();});
    menu.addEventListener('click',function(e){
      var item=e.target&&e.target.closest?e.target.closest('[data-context-action]'):null;
      if(!item)return;
      e.preventDefault();e.stopPropagation();
      window.filmStudioContextMenuAction(item.getAttribute('data-context-action'),clipId);
    });
  });
  document.addEventListener('pointerdown',function(event){
    if(event.target&&event.target.closest&&event.target.closest('.fs-context-menu'))return;
    var bridge=window.filmStudioReactTimeline;
    var reactMenuOpen=bridge&&typeof bridge.isContextMenuOpen==='function'&&bridge.isContextMenuOpen();
    if(activeTimelineContextMenu||reactMenuOpen)closeTimelineContextMenu();
  },true);
  document.addEventListener('keydown',function(event){
    var bridge=window.filmStudioReactTimeline;
    var reactMenuOpen=bridge&&typeof bridge.isContextMenuOpen==='function'&&bridge.isContextMenuOpen();
    if(event.key==='Escape'&&(activeTimelineContextMenu||reactMenuOpen)){
      closeTimelineContextMenu();event.stopPropagation();
    }
  });
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
  window.filmStudioUndo=function(){return filmStudioHistory('undo');};window.filmStudioRedo=function(){return filmStudioHistory('redo');};
  window.filmStudioRender=async function(){
    if(!S.projectId||!S.data||!S.data.exists){showStatus('请先载入或初始化 film.html。','error');return;}
    if(hasUncommittedInspectorEdits()){showStatus('影片有未提交的 Inspector 草稿；先应用属性或重新载入后再导出，避免输出旧 revision。','error');return;}
    if(S.rendering){showStatus('当前已有导出任务。');return;}
    S.rendering=true;
    var pid=S.projectId,revision=S.rev;
    var resultBox=$('fs-render-result'),link=$('fs-render-download');
    if(resultBox)resultBox.hidden=true;
    showStatus('正在提交 film.html revision '+revision+' 的 MP4 导出任务……');
    try{
      var started=await apiPost('/api/editor/film/render',{project_id:pid,expected_revision:revision});
      if(!started||!started.ok||!started.run_id){
        showStatus((started&&started.error)||'导出任务启动失败。','error');S.rendering=false;return;
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
          terminal=true;S.rendering=false;
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
          terminal=true;S.rendering=false;
          if(S.projectId===pid)showStatus((run.error&&String(run.error))||'MP4 导出失败，请检查素材范围与任务详情。','error');
        }
      }
    }catch(error){
      S.rendering=false;
      showStatus('MP4 导出请求失败：'+String(error&&error.message||error),'error');
    }
  };
  function findProposalClip(payload,clipId){
    if(!payload||!Array.isArray(payload.tracks))return null;
    for(var ti=0;ti<payload.tracks.length;ti++){
      var track=payload.tracks[ti],items=track.clips||[];
      for(var ci=0;ci<items.length;ci++){
        if(String(items[ci].id)===String(clipId))return {clip:items[ci],track:track};
      }
    }
    return null;
  }
  function proposalClipLabel(payload,clipId){
    if(!clipId)return '未指定片段';
    var found=findProposalClip(payload,clipId);
    if(!found)return String(clipId);
    var attrs=found.clip.attrs||{},label=String(attrs.title||found.clip.id);
    var trackAttrs=found.track.attrs||{},trackLabel=String(trackAttrs['data-label']||found.track.id);
    return label+' · '+found.clip.id+'（'+trackLabel+'）';
  }
  function proposalFieldLabel(field){
    return ({
      at:'时间位置',src:'素材源区间',speed:'播放速度',volume:'音量',volume_envelope:'音量关键帧',
      fade:'淡入/淡出',muted:'静音状态',style:'样式',class:'CSS 类',title:'片段标题',
      'data-film-keyframes':'视觉关键帧',alt:'替代文本',lang:'语言',dir:'文字方向',size:'尺寸',overrides:'画面覆盖参数'
    })[field]||String(field||'属性');
  }
  function summarizeProposalValue(field,value){
    if(value==null)return '未设置';
    if(field==='muted')return 'muted'===String(value)||value===''?'已静音':'未静音';
    if(field==='at')return Number.isFinite(Number(value))?fmtTime(Number(value)):String(value);
    if(field==='speed')return Number.isFinite(Number(value))?Number(value).toFixed(2)+'×':String(value);
    if(field==='volume')return Number.isFinite(Number(value))?Number(value).toFixed(2):String(value);
    if(field==='fade'){
      var parts=String(value||'0,0').split(',');
      return '淡入 '+(Number(parts[0]||0).toFixed(2))+'s / 淡出 '+(Number(parts[1]||0).toFixed(2))+'s';
    }
    if(field==='volume_envelope'||field==='data-film-keyframes'){
      var values=value;
      if(typeof values==='string'){try{values=JSON.parse(values);}catch(_){return '已修改（内容需要复核）';}}
      if(Array.isArray(values)){
        if(field==='data-film-keyframes'){
          var keys=values.reduce(function(total,channel){return total+(Array.isArray(channel&&channel.keyframes)?channel.keyframes.length:0);},0);
          return values.length+' 个属性通道 / '+keys+' 个关键帧';
        }
        return values.length+' 个音量关键帧';
      }
      return '已修改';
    }
    var text=typeof value==='string'?value:JSON.stringify(value);
    if(text.length>140)text=text.slice(0,137)+'…';
    return text||'未设置';
  }
  function proposalClipRange(found){
    if(!found)return '片段不存在';
    var clip=found.clip,at=Number(clip.at),end=clip.end;
    if(!Number.isFinite(at))return '时间未知';
    return fmtTime(at)+' – '+(end==null?'出点未知':fmtTime(Number(end)));
  }
  function proposalOperationPreviews(proposal){
    var operations=Array.isArray(proposal&&proposal.operations)?proposal.operations:[];
    var shown=operations.slice(0,30),affected=[],affectedSet=Object.create(null);
    function snapshotMap(payload){
      var map=Object.create(null);
      (payload&&payload.tracks||[]).forEach(function(track){
        (track.clips||[]).forEach(function(clip){map[String(clip.id)]={clip:clip,track:track};});
      });
      return map;
    }
    function signature(found){
      if(!found)return '';
      var c=found.clip;
      return JSON.stringify({track:String(found.track.id),tag:c.tag,at:c.at,end:c.end,duration:c.duration,attrs:c.attrs||{}});
    }
    function affect(id,label){
      if(!id)return;
      var key=String(id);if(affectedSet[key])return;
      affectedSet[key]=true;affected.push(label||proposalClipLabel(S.data,key));
    }
    // Show effects after comparing complete before/after snapshots, not only the
    // explicitly named operation IDs; ripple delete and insert can affect many clips.
    var beforeMap=snapshotMap(S.data),afterMap=snapshotMap(proposal&&proposal.candidate);
    var allIds=Object.create(null);
    Object.keys(beforeMap).forEach(function(id){allIds[id]=true;});
    Object.keys(afterMap).forEach(function(id){allIds[id]=true;});
    Object.keys(allIds).forEach(function(id){
      if(!beforeMap[id])affect(id,'新增：'+proposalClipLabel(proposal.candidate,id));
      else if(!afterMap[id])affect(id,'删除：'+proposalClipLabel(S.data,id));
      else if(signature(beforeMap[id])!==signature(afterMap[id]))affect(id,'修改：'+proposalClipLabel(proposal.candidate,id));
    });
    shown.forEach(function(op){affect(op.clip);affect(op.other_clip);});
    (proposal&&proposal.affected_clip_ids||[]).forEach(affect);
    var completeAffectedCount=affected.length;
    var shownAffected=affected.slice(0,20);
    var entries=shown.map(function(op,index){
      var kind=String(op.op||'operation'),id=String(op.clip||''),current=findProposalClip(S.data,id);
      var candidate=findProposalClip(proposal.candidate,id),target=proposalClipLabel(S.data,id),action='修改片段',detail='',before='',after='';
      if(kind==='set'){
        action='修改属性';detail=proposalFieldLabel(op.field);
        var oldValue=current?(op.field==='at'?current.clip.at:(current.clip.attrs||{})[op.field]):undefined;
        var newValue=candidate?(op.field==='at'?candidate.clip.at:(candidate.clip.attrs||{})[op.field]):op.value;
        before=summarizeProposalValue(op.field,oldValue);
        after=summarizeProposalValue(op.field,newValue);
      }else if(kind==='move'){
        action='移动片段';detail='目标轨道 '+proposalClipLabel(proposal.candidate,id);
        before=proposalClipRange(current);
        after=fmtTime(Number(op.at))+' · '+proposalClipLabel(proposal.candidate,id);
      }else if(kind==='trim'){
        action=op.edge==='start'?'修剪入点':'修剪出点';
        detail=op.edge==='start'?'调整片段入点':'调整片段出点';
        before=proposalClipRange(current);after=proposalClipRange(candidate);
      }else if(kind==='split'){
        action='切分片段';detail='切分点 '+fmtTime(Number(op.at))+'；新片段 ID：'+String(op.new_id||'自动生成');
        before=proposalClipRange(current);after=candidate?'左段 '+proposalClipRange(candidate)+'；右段由新 ID 表示':'将在 '+fmtTime(Number(op.at))+' 处分为两段';
      }else if(kind==='remove'){
        action='删除片段';detail='删除后可通过撤销恢复';
        before=proposalClipRange(current);after='已删除';
      }else if(kind==='ripple_delete'){
        action='波纹删除';detail='范围：'+(op.scope==='all'?'全部轨道':'指定轨道');
        before=proposalClipRange(current);after='删除时间段并收拢受影响轨道';
      }else if(kind==='slip'){
        action='滑移素材源区间';detail='保持时间线位置和片段长度不变';
        before=current?String((current.clip.attrs||{}).src||'未知源区间'):'未知源区间';
        after=candidate?String((candidate.clip.attrs||{}).src||'未知源区间'):'候选源区间已变更';
      }else if(kind==='roll'){
        action='滚动修剪';detail='相邻切口位移 '+Number(op.delta||0).toFixed(3)+'s；总时长不变';
        target=proposalClipLabel(S.data,id)+' ↔ '+proposalClipLabel(S.data,op.other_clip);
        var otherBefore=findProposalClip(S.data,op.other_clip),otherAfter=findProposalClip(proposal.candidate,op.other_clip);
        before=proposalClipRange(current)+' / '+proposalClipRange(otherBefore);
        after=proposalClipRange(candidate)+' / '+proposalClipRange(otherAfter);
      }else if(kind==='slide'){
        action='滑移式修剪';detail='中间片段位移 '+Number(op.delta||0).toFixed(3)+'s；保持三段外侧边界';
        before=proposalClipRange(current);after=proposalClipRange(candidate);
      }else if(kind==='join'){
        action='愈合切口';detail='合并同源连续的相邻片段';
        target=proposalClipLabel(S.data,id)+' + '+proposalClipLabel(S.data,op.other_clip);
        before=proposalClipRange(current)+' / '+proposalClipRange(findProposalClip(S.data,op.other_clip));
        after=candidate?proposalClipRange(candidate):'合并后的片段待确认';
      }else if(kind==='element_set'){
        action='修改画面元素';detail='选择器：'+String(op.selector||'当前选中元素');
        before='当前选中元素';after=op.text!=null?'文本：'+String(op.text).slice(0,100):('样式字段：'+Object.keys(op.style||{}).join(', '));
      }else if(kind==='track_add'){
        action='新增轨道';detail='新轨道 ID：'+String(op.track_id||'');
      }else if(kind==='track_remove'){
        action='删除空轨道';detail='轨道 ID：'+String(op.track_id||'');
      }else{
        action=kind;detail='FilmDocument 操作';
      }
      return {id:String(index),action:action,target:target,detail:detail,before:before,after:after};
    });
    return {entries:entries,affected:shownAffected,affectedTruncatedCount:Math.max(0,completeAffectedCount-shownAffected.length),
      truncatedCount:Math.max(0,operations.length-shown.length),operationCount:operations.length};
  }
  function parseProposalEnvelopePoints(value,duration){
    var parsed=value;
    if(typeof parsed==='string'){try{parsed=JSON.parse(parsed);}catch(_){parsed=null;}}
    var points=Array.isArray(parsed)?parsed.map(function(p){return {t:Number(p&&p.t),v:Number(p&&p.v)};})
      .filter(function(p){return Number.isFinite(p.t)&&p.t>=0&&Number.isFinite(p.v);})
      .map(function(p){return {t:p.t,v:Math.max(0,Math.min(1,p.v))};})
      .sort(function(a,b){return a.t-b.t;}):[];
    var safeDuration=Math.max(0.04,Number.isFinite(Number(duration))?Number(duration):0.04);
    return points.length>=2?points:[{t:0,v:1},{t:safeDuration,v:1}];
  }
  function proposalFadeParts(value){
    var parts=String(value||'0,0').split(',');
    return {fadeIn:Math.max(0,num(parts[0],0)),fadeOut:Math.max(0,num(parts[1],0))};
  }
  function previewFilmProposalTimeline(proposal){
    clearFilmProposalTimelinePreview();
    if(!proposal||!proposal.candidate||!S.data||!Array.isArray(S.data.tracks)||
       !Array.isArray(proposal.candidate.tracks))return false;
    var before=Object.create(null),after=Object.create(null),beforeTracks=Object.create(null),
        afterTracks=Object.create(null),preview=Object.create(null),envelopes=[];
    (S.data.tracks||[]).forEach(function(t){
      beforeTracks[String(t.id)]=t;
      (t.clips||[]).forEach(function(c){before[String(c.id)]={clip:c,track:t};});
    });
    (proposal.candidate.tracks||[]).forEach(function(t){
      afterTracks[String(t.id)]=t;
      (t.clips||[]).forEach(function(c){after[String(c.id)]={clip:c,track:t};});
    });
    Object.keys(beforeTracks).forEach(function(id){
      if(afterTracks[id])return;
      var track=beforeTracks[id],attrs=track.attrs||{};
      preview["__proposal_track_removed__:"+id]={
        removed:true,previewTrack:true,targetTrackId:id,
        trackLabel:String(attrs["data-label"]||track.label||id),
        trackKind:(track.clips||[]).some(function(c){return c.tag==="audio";})?"audio":"video"
      };
    });
    Object.keys(afterTracks).forEach(function(id){
      if(beforeTracks[id])return;
      var track=afterTracks[id],attrs=track.attrs||{};
      preview["__proposal_track_added__:"+id]={
        added:true,previewTrack:true,targetTrackId:id,
        trackLabel:String(attrs["data-label"]||track.label||id),
        trackKind:(track.clips||[]).some(function(c){return c.tag==="audio";})?"audio":"video",
        trackIndex:(proposal.candidate.tracks||[]).findIndex(function(candidateTrack){return String(candidateTrack.id)===id;})
      };
    });
    Object.keys(before).forEach(function(id){
      if(!after[id])preview[id]={removed:true};
    });
    Object.keys(after).forEach(function(id){
      var a=before[id],b=after[id];if(!b)return;
      var newClip=b.clip,newAttrs=newClip.attrs||{};
      if(!a){
        var newAt=num(newClip.at,0),newDuration=newClip.duration==null?null:num(newClip.duration,0);
        preview["__proposal_added__:"+id]={
          added:true,previewTitle:String(newAttrs.title||id),previewTag:String(newClip.tag||"video"),
          targetTrackId:String(b.track.id),at:newAt,duration:newDuration
        };
        return;
      }
      var oldClip=a.clip,oldAt=num(oldClip.at,0),newAt=num(newClip.at,0);
      var oldDur=oldClip.duration==null?null:num(oldClip.duration,0),newDur=newClip.duration==null?null:num(newClip.duration,0);
      var trackChanged=String(a.track.id)!==String(b.track.id);
      var geometryChanged=trackChanged||Math.abs(oldAt-newAt)>0.0005||
        (oldDur==null)!==(newDur==null)||(oldDur!=null&&newDur!=null&&Math.abs(oldDur-newDur)>0.0005);
      if(geometryChanged){
        preview[id]={at:newAt,duration:newDur,left:timelineClipLeft(newAt,S.pxPerSec),width:timelineClipWidth(newDur,S.pxPerSec,24,2,0,false)};
        if(trackChanged)preview[id].targetTrackId=String(b.track.id);
      }
      var oldAttrs=oldClip.attrs||{};newAttrs=newClip.attrs||{};
      if(String(oldAttrs.fade||'0,0')!==String(newAttrs.fade||'0,0')){
        var fade=proposalFadeParts(newAttrs.fade);preview[id]=preview[id]||{};
        preview[id].fadeIn=fade.fadeIn;preview[id].fadeOut=fade.fadeOut;
      }
      var oldEnvelope=oldAttrs.volume_envelope==null?'':String(oldAttrs.volume_envelope);
      var newEnvelope=newAttrs.volume_envelope==null?'':String(newAttrs.volume_envelope);
      if(oldEnvelope!==newEnvelope&&(newClip.tag==='audio'||newClip.tag==='video'))
        envelopes.push({clipId:id,points:parseProposalEnvelopePoints(newAttrs.volume_envelope,newDur)});
    });
    setTimelineGesturePreview(Object.keys(preview).length?preview:null);
    if(envelopes.length)setTimelineEnvelopePreview(envelopes[0].clipId,envelopes[0].points);
    return true;
  }
  function renderProposalPanel(){
    if(renderAiDirector())return;
    var panel=$('fs-proposal-panel'),proposal=S.proposal;
    if(!panel)return;
    panel.hidden=!proposal;
    if(!proposal)return;
    var summary=$('fs-proposal-summary'),detail=$('fs-proposal-detail');
    if(summary)summary.textContent=proposal.summary||'AI 候选修改';
    if(detail)detail.textContent=(proposal.operations||[]).length+' 项操作 · 基于 revision '+proposal.base_revision+' · 尚未写入正式文档';
    var operationsRoot=$('fs-proposal-operations');
    if(operationsRoot){
      var preview=proposalOperationPreviews(proposal);
      operationsRoot.innerHTML='<details class="fs-proposal-operation-list"><summary>查看具体改动（'+preview.operationCount+' 项）</summary>'+
        '<ol>'+preview.entries.map(function(item){return '<li><strong>'+esc(item.action)+'</strong> · '+esc(item.target)+
          '<p>'+esc(item.detail)+'</p>'+
          (item.before?'<p class="fs-proposal-before">修改前：'+esc(item.before)+'</p>':'')+
          (item.after?'<p class="fs-proposal-after">修改后：'+esc(item.after)+'</p>':'')+
          '</li>';}).join('')+'</ol>'+
        (preview.truncatedCount?'<p>另有 '+preview.truncatedCount+' 项操作未在此展开。</p>':'')+
        (preview.affected.length?'<p class="fs-proposal-affected">影响对象：'+preview.affected.map(esc).join('；')+'</p>':'')+
        (preview.affectedTruncatedCount?'<p>另有 '+preview.affectedTruncatedCount+' 个受影响对象未列出。</p>':'')+
        '</details>';
    }
  }
  window.filmStudioPreviewProposal=function(){
    var proposal=S.proposal,frame=$('fs-preview-composition');
    if(!proposal||!proposal.candidate||!frame||!frame.contentWindow){showStatus('没有可预览的 AI 提案。','error');return;}
    if(blockStaleFilmProposalAction(proposal,'预览'))return;
    window.filmStudioSetSideMode('ai');
    S.previewProposalActive=true;S.runtimeRev='';
    previewFilmProposalTimeline(proposal);
    syncRuntime('seek-pause');
    showStatus('正在预览 AI 候选版本；正式 film.html 尚未更改。');
  };
  window.filmStudioRejectProposal=function(){
    if(!S.proposal)return;
    appendMessage('已拒绝本次 AI 提案。正式影片文档与 revision 未改变。','assistant');
    clearFilmProposalTimelinePreview();
    S.proposal=null;S.previewProposalActive=false;renderProposalPanel();S.runtimeRev='';
    syncRuntime('seek-pause');
    showStatus('已拒绝提案；未修改正式影片。','success');
  };
  window.filmStudioApplyProposal=async function(){
    var proposal=S.proposal;if(!proposal||S.busy)return;
    if(blockStaleFilmProposalAction(proposal,'应用'))return;
    if(hasUncommittedInspectorEdits()){showStatus('请先应用 Inspector 属性草稿，或重新载入并确认丢弃，再应用 AI 提案。','error');return;}
    if(S.broken){showStatus('影片文件无效，不能应用 AI 提案。','error');return;}
    if(proposal.project_id!==S.projectId){showStatus('提案所属项目已变化，不能应用。','error');return;}
    var reloadAfterConflict=false;
    try{
      await dispatchStudioTransaction('ai-proposal',proposal.base_revision,proposal.operations||[],async function(){
        var result=await apiPost('/api/editor/film/apply-proposal',{
          project_id:S.projectId,proposal_id:proposal.id,base_revision:proposal.base_revision
        });
        if(!result||!result.ok){
          appendMessage((result&&result.error)||'提案应用失败。','assistant');
          showStatus((result&&result.error)||'提案应用失败。','error');
          if(result&&result.code==='REVISION_CONFLICT'){
            S.previewProposalActive=false;S.runtimeRev='';clearFilmProposalTimelinePreview();
            delete proposal.observed_revision;delete proposal.stale_notified_revision;
            reloadAfterConflict=true;
          }
          return false;
        }
        clearFilmProposalTimelinePreview();
        S.proposal=null;S.previewProposalActive=false;renderProposalPanel();S.data=result;S.rev=result.rev;S.runtimeRev='';
        appendMessage('已确认并应用提案，提交 '+(result.ops||[]).length+' 项操作。','assistant');
        render();showStatus('AI 提案已应用并写入 revision '+S.rev+'。','success');
        return true;
      });
      if(reloadAfterConflict)await filmStudioLoad(true);
    }catch(error){
      var proposalErrorCode=error&&error.code||'';
      if(proposalErrorCode==='FILM_REVISION_CONFLICT'||proposalErrorCode==='FILM_EXTERNAL_REVISION_PENDING'){
        S.previewProposalActive=false;S.runtimeRev='';clearFilmProposalTimelinePreview();
        delete proposal.observed_revision;delete proposal.stale_notified_revision;
        await filmStudioLoad(true);
        renderProposalPanel();
        showStatus('影片 revision 已变化。旧提案仍可查看或拒绝，但不能预览/应用。','error');
      }else{
        showStatus('AI 提案应用失败：'+String(error&&error.message||error),'error');
      }
    }finally{flushPendingExternalRevision();}
  };
  function decodeSummaryPrefix(raw){
    var key='"summary"',keyAt=raw.indexOf(key);
    if(keyAt<0)return null;
    var colon=raw.indexOf(':',keyAt+key.length);
    if(colon<0)return null;
    var opening=raw.indexOf('"',colon+1);
    if(opening<0)return null;
    var output='',escaped=false;
    for(var i=opening+1;i<raw.length;i++){
      var ch=raw[i];
      if(escaped){
        if(ch==='"'||ch==='\\'||ch==='/')output+=ch;
        else if(ch==='n')output+='\n';
        else if(ch==='r')output+='\r';
        else if(ch==='t')output+='\t';
        else if(ch==='b')output+='\b';
        else if(ch==='f')output+='\f';
        else if(ch==='u'){
          var hex=raw.slice(i+1,i+5);
          if(!/^[0-9a-fA-F]{4}$/.test(hex))break;
          output+=String.fromCharCode(parseInt(hex,16));i+=4;
        }else output+=ch;
        escaped=false;
      }else if(ch==='\\')escaped=true;
      else if(ch==='"')return output;
      else output+=ch;
    }
    return output;
  }
  function updateAiLiveMessage(message){
    var index=S.aiLiveMessageIndex;
    if(index<0||index>=S.messages.length)return;
    S.messages[index].text=String(message||'');
    if(renderAiDirector()){
      var log=$('fs-chat-log');if(log)log.scrollTop=log.scrollHeight;
      return;
    }
    var staticLog=$('fs-chat-log');if(!staticLog)return;
    staticLog.innerHTML=S.messages.map(function(item){return '<div class="fs-chat-message '+(item.role==='user'?'fs-user':'fs-assistant')+'">'+esc(item.text)+'</div>';}).join('');
    staticLog.scrollTop=staticLog.scrollHeight;
  }
  async function readFilmAiStream(response, controller){
    if(!response.body||typeof response.body.getReader!=='function')throw new Error('当前浏览器不支持读取流式 AI 响应。');
    var reader=response.body.getReader(),decoder=new TextDecoder('utf-8'),buffer='',result=null;
    function processBlock(block){
      var eventName='message',dataLines=[];
      block.split('\n').forEach(function(line){
        if(line.endsWith('\r'))line=line.slice(0,-1);
        if(line.indexOf('event:')===0)eventName=line.slice(6).trim();
        else if(line.indexOf('data:')===0)dataLines.push(line.slice(5).replace(/^ /,''));
      });
      if(!dataLines.length)return;
      var data;
      try{data=JSON.parse(dataLines.join('\n'));}catch(_){throw new Error('AI 流式响应不是合法 JSON。');}
      if(eventName==='token'){
        var fragment=String(data.fragment||'');
        S.aiStreamText+=fragment;
        var summary=decodeSummaryPrefix(S.aiStreamText);
        updateAiLiveMessage(summary!==null&&summary.trim()?summary+' …':'AI 正在生成并校验编辑计划（已接收 '+S.aiStreamText.length+' 个字符）…');
      }else if(eventName==='result')result=data;
      else if(eventName==='error')throw new Error(String(data.error||'AI 规划失败。'));
    }
    while(true){
      var item=await reader.read();
      if(item.done)break;
      buffer+=decoder.decode(item.value,{stream:true});
      var boundary,separator;
      while((boundary=buffer.search(/\r?\n\r?\n/))>=0){
        separator=buffer.slice(boundary).match(/^\r?\n\r?\n/);
        if(!separator)break;
        var one=buffer.slice(0,boundary);buffer=buffer.slice(boundary+separator[0].length);
        processBlock(one);
        if(result){try{await reader.cancel();}catch(_){}return result;}
      }
    }
    buffer+=decoder.decode();
    if(buffer.trim())processBlock(buffer);
    if(controller&&controller.signal.aborted){
      var aborted=new Error('AI 流式请求已取消');aborted.name='AbortError';throw aborted;
    }
    if(!result)throw new Error('AI 流式响应结束，但没有返回最终提案结果。');
    return result;
  }
  window.filmStudioCancelAi=function(options){
    if(!S.aiController){showStatus('当前没有正在生成的 AI 指令。');return false;}
    options=options||{};
    if(!options.preserveSteer)S.aiPendingSteer='';
    var requestId=S.aiRequestId,projectId=S.projectId;
    if(requestId&&projectId){
      try{
        fetch('/api/editor/film/command/cancel',{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({project_id:projectId,request_id:requestId}),keepalive:true
        }).catch(function(){/* The AbortController remains the client-side fallback. */});
      }catch(_){}
    }
    S.aiController.abort();
    showStatus(options.preserveSteer?'已请求终止当前上游生成，随后按新方向重新规划。':'已请求停止上游模型生成；正式影片不会因取消而写入。');
    return true;
  };
  window.filmStudioAsk=async function(overrideMessage, alreadyShown){
    var input=$('fs-chat-input')||$('fs-static-chat-input');if(!input)return;
    var message=String(overrideMessage||input.value||'').trim();
    if(S.busy){
      if(S.aiController&&message){
        S.aiPendingSteer='原指令：'+S.aiActiveMessage+'\n执行中转向/补充要求：'+message;
        appendMessage(message,'user');input.value='';
        showStatus('已收到新的剪辑方向；正在停止当前生成并按新方向重新规划。');
        window.filmStudioCancelAi({preserveSteer:true});
      }
      return;
    }
    if(!message)return;
    if(hasUncommittedInspectorEdits()){showStatus('请先应用 Inspector 属性草稿，或重新载入并确认丢弃，再请求 AI 修改。','error');return;}
    if(!S.data||!S.data.exists){showStatus('请先初始化影片文档。','error');return;}
    if(S.broken){showStatus('影片文件无效，已暂停 AI 编辑；修复后重新载入。','error');return;}
    if(S.proposal){window.filmStudioRejectProposal();}
    if(!alreadyShown)appendMessage(message,'user');
    input.value='';S.busy=true;S.aiActiveMessage=message;S.aiStreamText='';
    S.aiRequestId=(window.crypto&&typeof window.crypto.randomUUID==='function')
      ?window.crypto.randomUUID()
      :'ai-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,12);
    var requestId=S.aiRequestId;
    S.aiLiveMessageIndex=S.messages.length;
    appendMessage('AI 正在读取影片上下文并生成修改计划…','assistant');
    var controller=new AbortController();S.aiController=controller;
    try{
      var request={project_id:S.projectId,base:S.rev,message:message,selected_clip_id:S.selectedId,stream:true,request_id:requestId};
      if(S.selectedElement&&S.selectedElement.clipId===S.selectedId){
        if(S.selectedElement.baseRevision!==S.rev){
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
      var response=await fetch('/api/editor/film/command',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(request),signal:controller.signal
      });
      var contentType=response.headers.get('Content-Type')||'',result;
      if(contentType.indexOf('text/event-stream')>=0){
        result=await readFilmAiStream(response,controller);
      }else{
        result=await response.json();
        if(!response.ok||!result||!result.ok)throw new Error((result&&result.error)||('AI 请求失败（HTTP '+response.status+'）'));
      }
      if(!result||!result.ok){
        updateAiLiveMessage((result&&result.error)||'AI 提案生成失败。');
        showStatus((result&&result.error)||'AI 提案生成失败。','error');return;
      }
      if(result.no_op){
        updateAiLiveMessage(result.message||'AI 没有生成可安全应用的操作，请补充目标。');
        showStatus(result.message||'没有产生候选操作。');return;
      }
      if(!result.proposal){
        updateAiLiveMessage('服务器没有返回结构化提案，未修改正式影片。');
        showStatus('AI 响应缺少提案对象。','error');return;
      }
      S.proposal=result.proposal;renderProposalPanel();
      updateAiLiveMessage((result.proposal.summary||'AI 已生成候选编辑。')+'（'+result.proposal.operations.length+' 项，尚未应用）');
      window.filmStudioPreviewProposal();
    }catch(error){
      if(error&&error.name==='AbortError'){
        updateAiLiveMessage(S.aiPendingSteer?'当前生成已停止，正在按新的剪辑方向重新规划…':'本轮已取消；正式影片没有修改。');
      }else{
        updateAiLiveMessage('AI 提案生成失败：'+String(error&&error.message||error));
        showStatus(String(error&&error.message||error),'error');
      }
    }finally{
      if(S.aiController===controller)S.aiController=null;
      if(S.aiRequestId===requestId)S.aiRequestId='';
      S.busy=false;flushPendingExternalRevision();
      var pending=S.aiPendingSteer;S.aiPendingSteer='';
      if(pending&&S.open&&S.data&&S.data.exists&&!S.broken){
        Promise.resolve().then(function(){return window.filmStudioAsk(pending,true);});
      }
    }
  };
  document.addEventListener('keydown',function(e){
    if(!S.open)return;
    var target=e.target;
    if(target&&target.matches&&target.matches('input,textarea,select,[contenteditable="true"]'))return;
    if((e.ctrlKey||e.metaKey)&&String(e.key).toLowerCase()==='a'){
      e.preventDefault();S.selectedIds=clips().map(function(c){return c.id;});S.selectedId=S.selectedIds[0]||'';S.selectionAnchor=S.selectedId;syncClipSelectionVisual();renderInspector();renderAssets();
    }else if((e.ctrlKey||e.metaKey)&&String(e.key).toLowerCase()==='c'){
      e.preventDefault();window.filmStudioCopyClips();
    }else if((e.ctrlKey||e.metaKey)&&String(e.key).toLowerCase()==='v'){
      e.preventDefault();window.filmStudioPasteClips();
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
  function flushPendingSceneRefresh(){
    var pending=S.pendingSceneRefresh;
    if(!pending||!S.open)return;
    if(!S.projectId||pending.project_id!==S.projectId)return;
    var frame=$('fs-preview-composition');
    var runtimeData=S.previewProposalActive&&S.proposal?S.proposal.candidate:
      ((!S.data||!S.data.exists)?migrationPreviewPayload():S.data);
    if(!S.runtimeReady||!frame||!frame.contentWindow||!runtimeData||!runtimeData.exists)return;
    var expectedRuntimeRev=S.previewProposalActive&&S.proposal?'proposal:'+S.proposal.id:
      (migrationPreviewPayload()&&(!S.data||!S.data.exists)?migrationPreviewRevision():S.rev);
    if(S.runtimeRev!==expectedRuntimeRev){
      syncRuntime(S.runtimeCommand||(window.filmStudioPlaying?'play':'seek'));
    }
    S.pendingSceneRefresh=null;
    frame.contentWindow.postMessage({
      type:'film-runtime:refresh-scenes',
      scene_revision:pending.scene_revision,
      paths:pending.paths
    },window.location.origin);
  }
  function connectFilmStudioEvents(){
    if(S.events||typeof EventSource==='undefined')return;
    try{
      S.events=new EventSource('/api/events');
      S.events.onmessage=function(event){
        var packet;try{packet=JSON.parse(event.data||'{}');}catch(_){return;}
        var data=packet.data||{};
        if(data.project_id&&S.projectId&&data.project_id!==S.projectId)return;
        if(packet.event==='film'&&data.phase==='scene_changed'&&S.open){
          S.pendingSceneRefresh={
            project_id:String(data.project_id||S.projectId||''),
            scene_revision:String(data.scene_revision||''),
            paths:Array.isArray(data.paths)?data.paths.slice(0,200):[]
          };
          flushPendingSceneRefresh();
        }else if(packet.event==='film'&&data.phase==='document_broken'&&S.open){
          // Preserve the last valid view while the agent is in the middle of
          // writing a broken/partial file. Only a later valid revision may reload.
          S.broken=true;
          if(S.proposal){clearFilmProposalTimelinePreview();S.proposal=null;S.previewProposalActive=false;renderProposalPanel();}
          showStatus('影片文件校验失败，已保留当前画面并暂停编辑：'+String(data.error||'文档解析失败'),'error');
        }else if(packet.event==='film'&&data.phase==='revision_changed'&&data.source==='external_file'&&S.open){
          S.broken=false;
          if(data.revision&&data.revision!==S.rev){
            if(S.proposal)markFilmProposalStale(data.revision,'影片已被外部修改。旧 AI 提案仍可查看或拒绝，但不能预览/应用。');
            S.pendingExternalRevision=data.revision;
            pendingRevisionSource=data.source||'external_file';
            flushPendingExternalRevision();
          }
        }else if(packet.event==='film'&&data.phase==='revision_changed'&&S.open&&data.revision&&data.revision!==S.rev){
          if(S.proposal)markFilmProposalStale(data.revision,'影片 revision 已变化。旧 AI 提案仍可查看或拒绝，但不能预览/应用。');
          S.pendingExternalRevision=data.revision;
          pendingRevisionSource=data.source||'unknown';
          flushPendingExternalRevision();
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
      var sideShare=Number(localStorage.getItem('film-studio-inspector-share'));
      applySideShare(Number.isFinite(sideShare)&&sideShare>0?sideShare:54,false);
    }catch(_){applySideShare(54,false);}
  }
  function applySideShare(raw,persist){
    var pane=$('fs-side-workbench');
    var divider=$('fs-side-divider');
    if(!pane)return;
    var height=pane.getBoundingClientRect().height||0;
    var minShare=height>0?Math.max(25,Math.min(45,80/height*100)):25;
    var maxShare=height>0?Math.min(75,Math.max(25,(1-96/height)*100)):75;
    var share=Math.max(minShare,Math.min(maxShare,Number(raw)||54));
    pane.style.setProperty('--fs-inspector-share',share.toFixed(2)+'%');
    if(divider)divider.setAttribute('aria-valuenow',String(Math.round(share)));
    if(persist!==false){try{localStorage.setItem('film-studio-inspector-share',share.toFixed(2));}catch(_){}}
  }
  function beginSideDivider(e){
    if(e.button!==0)return;
    var pane=$('fs-side-workbench');
    if(!pane)return;
    S.paneDrag={side:'side-split',divider:e.currentTarget,rect:pane.getBoundingClientRect()};
    e.currentTarget.classList.add('fs-divider-dragging');
    try{e.currentTarget.setPointerCapture(e.pointerId);}catch(_){}
    e.preventDefault();
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
    if(d.side==='side-split'){
      var sideHeight=d.rect.height;
      if(!sideHeight)return;
      applySideShare((e.clientY-d.rect.top)/sideHeight*100,true);
      return;
    }
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
  var sideDivider=$('fs-side-divider');
  if(sideDivider){
    sideDivider.addEventListener('pointerdown',beginSideDivider);
    sideDivider.addEventListener('pointermove',moveDivider);
    sideDivider.addEventListener('pointerup',endDivider);
    sideDivider.addEventListener('pointercancel',endDivider);
    sideDivider.addEventListener('lostpointercapture',endDivider);
    sideDivider.addEventListener('keydown',function(e){
      if(e.key!=='ArrowUp'&&e.key!=='ArrowDown')return;
      e.preventDefault();
      var pane=$('fs-side-workbench');
      if(!pane)return;
      var current=parseFloat(getComputedStyle(pane).getPropertyValue('--fs-inspector-share'))||54;
      applySideShare(current+(e.key==='ArrowDown'?1:-1)*(e.shiftKey?8:3),true);
    });
  }
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
  // The editor-project select already calls editorLoad() via its inline onchange.
  // That wrapper owns the sole Studio reload/open decision; a second change
  // listener here caused concurrent filmStudioLoad requests on every project switch.
  var legacyEditorLoad=window.editorLoad;
  var editorAuthorityRequestGeneration=0;
  if(typeof legacyEditorLoad==='function'){
    window.editorLoad=async function(knownFilmDocument,options){
      var legacyCompositionMode=!!(options&&options.legacyCompositionMode===true);
      var authorityRequestGeneration=++editorAuthorityRequestGeneration;
      var selector=$('editor-project'),pid=(selector&&selector.value)||(typeof ED!=='undefined'&&ED.pid)||'';
      if(!pid)return await legacyEditorLoad.apply(this,arguments);

      // Reuse an authority result supplied by workspaceOpen(); a result for a
      // different project is rejected instead of being trusted or silently re-fetched.
      var state=null;
      var hasProvidedAuthority=!!(knownFilmDocument&&typeof knownFilmDocument.exists==='boolean');
      if(hasProvidedAuthority){
        if(knownFilmDocument.project_id&&String(knownFilmDocument.project_id)!==String(pid)){
          showStatus('影片预检结果属于另一个项目；未打开旧工作台。','error');
          return {ok:false,code:'FILM_AUTHORITY_PROJECT_MISMATCH'};
        }
        state=knownFilmDocument;
      }
      if(!state){
        try{
          state=await apiGet('/api/editor/film?project_id='+encodeURIComponent(pid));
        }catch(error){
          if(authorityRequestGeneration!==editorAuthorityRequestGeneration){
            return {ok:false,code:'PROJECT_SELECTION_CHANGED'};
          }
          showStatus('无法确认影片文档状态；为避免绕过 film.html 权威门禁，暂不打开旧工作台。','error');
          return {ok:false,code:'FILM_STATE_UNAVAILABLE',error:String(error&&error.message||error)};
        }
      }
      // Authority is scoped to this editorLoad invocation, not to the selector's
      // value at the time a delayed request happens to resolve. A stale exists:false
      // response must never authorize the legacy loader for a newer selection.
      if(authorityRequestGeneration!==editorAuthorityRequestGeneration||
         (selector&&String(selector.value||'').trim()!==String(pid).trim())){
        return {ok:false,code:'PROJECT_SELECTION_CHANGED'};
      }
      if(state&&state.exists===true){
        var opened=await window.filmStudioOpenIfCurrentProjectHasFilm(pid,state);
        return {
          ok:opened&&state.ok===true,
          film_document:true,
          authority:'film.html',
          project_id:pid,
          revision:state.rev||'',
          error:opened?((state.ok===true)?null:(state.error||'INVALID_FILM')):'FILM_STUDIO_OPEN_FAILED'
        };
      }
      if(!state||state.ok!==true){
        showStatus('影片文档状态不可用；未加载旧 CompositionIR 工作台。','error');
        return {ok:false,code:'FILM_STATE_UNAVAILABLE'};
      }

      // Switch the authority surface before probing legacy CompositionIR.
      // The old loader can fail for an unavailable/invalid composition, but that
      // must not leave the previous FilmDocument timeline visible in an open Studio.
      // Reuse the confirmed exists:false snapshot; do not start another Film GET.
      // The compatibility editor is opt-in only. Normal project selection must
      // not populate ED/CompositionIR behind the Film Studio where hidden keyboard
      // handlers could still mutate the legacy timeline.
      if(legacyCompositionMode){
        if(S.open||document.documentElement.classList.contains('fs-studio-open')){
          showStatus('Film Studio 已打开；为避免双编辑器并行，本次未加载旧 CompositionIR。','error');
          return {ok:false,code:'STUDIO_OPEN_LEGACY_COMPAT_BLOCKED'};
        }
        var legacyArguments=hasProvidedAuthority?Array.prototype.slice.call(arguments,1):arguments;
        return await legacyEditorLoad.apply(this,legacyArguments);
      }
      // One authority wrapper owns both routes: existing FilmDocument opens directly;
      // legacy Composition projects open the same Studio with a read-only migration preview.
      if(authorityRequestGeneration!==editorAuthorityRequestGeneration||
         (selector&&String(selector.value||'').trim()!==String(pid).trim())){
        return {ok:false,code:'PROJECT_SELECTION_CHANGED'};
      }
      var migrationStudioOpened;
      if(S.open){
        migrationStudioOpened=await filmStudioLoad(true,state);
      }else{
        migrationStudioOpened=await window.filmStudioToggle(state);
      }
      if(!migrationStudioOpened){
        showStatus('旧项目的 Studio 迁移预览未能打开；没有创建 film.html。','error');
        return {ok:false,code:'FILM_STUDIO_OPEN_FAILED'};
      }
      if(authorityRequestGeneration!==editorAuthorityRequestGeneration||
         (selector&&String(selector.value||'').trim()!==String(pid).trim())||
         S.projectId!==pid){
        return {ok:false,code:'PROJECT_SELECTION_CHANGED'};
      }
      return {ok:true,mode:'migration',
        authority:'CompositionIR preview → film.html',project_id:pid};
    };
  }

  const FILM_STUDIO_ACTIONS = {
    toggle: () => window.filmStudioToggle(),
    undo: () => window.filmStudioUndo(),
    redo: () => window.filmStudioRedo(),
    reload: () => window.filmStudioReload(),
    close: () => window.filmStudioClose(),
    'clips-mode': () => window.filmStudioShowAssetMode('clips'),
    'library-mode': () => window.filmStudioLoadLibrary(),
    'director-mode': () => window.filmStudioShowAssetMode('director'),
    'frame-prev': () => window.filmStudioNudge(-1),
    'play-toggle': () => window.filmStudioTogglePlayback(),
    'frame-next': () => window.filmStudioNudge(1),
    'canvas-inspect': () => window.filmStudioToggleElementInspect(),
    split: () => window.filmStudioSplit(),
    'copy-clips': () => window.filmStudioCopyClips(),
    'paste-clips': () => window.filmStudioPasteClips(),
    'toggle-snap': () => window.filmStudioToggleSnap(),
    'join-cut': () => window.filmStudioJoinThroughEdit(),
    remove: () => window.filmStudioRemove(),
    'ripple-delete': () => window.filmStudioRippleDelete(),
    'envelope-mode': () => window.filmStudioToggleEnvelopeMode(),
    'slide-mode': () => window.filmStudioToggleSlideMode(),
    'slip-mode': () => window.filmStudioToggleSlipMode(),
    'insert-mode': () => window.filmStudioToggleInsertMode(),
    'overwrite-mode': () => window.filmStudioToggleOverwriteMode(),
    'add-track': () => window.filmStudioAddTrack(),
    'remove-empty-track': () => window.filmStudioRemoveEmptyTrack(),
    'zoom-out': () => window.filmStudioZoom(-1),
    'zoom-in': () => window.filmStudioZoom(1),
    'apply-inspector': () => window.filmStudioSaveInspector(),
    'show-inspector': () => window.filmStudioSetSideMode('inspector'),
    'show-ai': () => window.filmStudioSetSideMode('ai'),
    render: () => window.filmStudioRender(),
    ask: () => window.filmStudioAsk(),
    'cancel-ai': () => window.filmStudioCancelAi(),
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
