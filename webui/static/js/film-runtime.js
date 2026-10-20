/* Multi-track browser preview from the authoritative film.html projection. */
(function(){
  'use strict';
  var state={data:null,time:0,playing:false,renderMode:false,inspectMode:false,originTime:0,originClock:0,lastParentUpdate:0,rev:'',tracks:[],duration:0,raf:0};
  var stage=document.getElementById('stage'),viewport=document.getElementById('viewport'),notice=document.getElementById('notice');
  var byId=Object.create(null);
  function finite(v,fallback){var n=Number(v);return Number.isFinite(n)?n:fallback;}
  function clamp(v,min,max){return Math.max(min,Math.min(max,v));}
  function sourceBase(src){return String(src||'').split('#',1)[0];}
  function has(attrs,name){return !!attrs&&Object.prototype.hasOwnProperty.call(attrs,name);}
  function post(message){try{parent.postMessage(message,window.location.origin);}catch(_){}}
  function fitStage(){
    if(!state.data||!state.data.stage)return;
    var w=Math.max(1,finite(state.data.stage.width,1920)),h=Math.max(1,finite(state.data.stage.height,1080));
    var scale=Math.min(viewport.clientWidth/w,viewport.clientHeight/h);
    stage.style.width=w+'px';stage.style.height=h+'px';
    stage.style.transform='translate(-50%,-50%) scale('+scale+')';
  }
  function safeStyle(styleText,el){
    var allowed={left:1,top:1,width:1,height:1,opacity:1,'object-fit':1,'border-radius':1,rotate:1,'transform-origin':1,'clip-path':1,'z-index':1};
    String(styleText||'').split(';').forEach(function(part){
      var colon=part.indexOf(':');if(colon<1)return;
      var key=part.slice(0,colon).trim().toLowerCase(),value=part.slice(colon+1).trim();
      if(!allowed[key]||!value||/[{};]/.test(value)||/url\s*\(|expression\s*\(|javascript:/i.test(value))return;
      if(['left','top','width','height'].indexOf(key)>=0&&!/^-?[0-9]+(?:\.[0-9]+)?px$/.test(value))return;
      if(key==='opacity'&&(!Number.isFinite(Number(value))||Number(value)<0||Number(value)>1))return;
      if(key==='object-fit'&&['cover','contain','fill','none','scale-down'].indexOf(value)<0)return;
      if(key==='transform-origin'&&!/^(0|50|100)%\s+(0|50|100)%$/.test(value))return;
      if(key==='z-index'&&(!/^-?\d{1,6}$/.test(value)||Math.abs(Number(value))>100000))return;
      if(key==='clip-path'){
        var inset=/^inset\(\s*-?(?:\d+(?:\.\d+)?|\.\d+)%(?:\s+-?(?:\d+(?:\.\d+)?|\.\d+)%){0,3}\s*\)$/;
        var ellipse=/^ellipse\(\s*\d+(?:\.\d+)?%\s+\d+(?:\.\d+)?%\s+at\s+\d+(?:\.\d+)?%\s+\d+(?:\.\d+)?%\s*\)$/;
        var polygon=/^polygon\(\s*\d+(?:\.\d+)?%\s+\d+(?:\.\d+)?%(?:\s*,\s*\d+(?:\.\d+)?%\s+\d+(?:\.\d+)?%){2,}\s*\)$/;
        if(!inset.test(value)&&!ellipse.test(value)&&!polygon.test(value))return;
      }
      el.style.setProperty(key,value);
    });
  }
  function parseClipFade(value){
    if(value==null||value==='')return {in:0,out:0};
    var parts=String(value).split(',');
    if(parts.length!==2)return {in:0,out:0};
    var fadeIn=Number(parts[0]),fadeOut=Number(parts[1]);
    if(!Number.isFinite(fadeIn)||!Number.isFinite(fadeOut)||fadeIn<0||fadeOut<0)return {in:0,out:0};
    return {in:fadeIn,out:fadeOut};
  }
  function clipFadeAt(entry,t){
    var fade=entry.fade||{in:0,out:0},clip=entry.clip;
    var local=Math.max(0,t-finite(clip.at,0)),factor=1;
    if(fade.in>0)factor=Math.min(factor,clamp(local/fade.in,0,1));
    if(fade.out>0&&clip.end!=null)factor=Math.min(factor,clamp((clip.end-t)/fade.out,0,1));
    return factor;
  }
  function mediaTime(clip,t){
    var at=finite(clip.at,0),speed=(clip.tag==='video'||clip.tag==='audio')?clamp(finite(clip.attrs&&clip.attrs.speed,1),0.25,4):1;
    return Math.max(0,finite(clip.src_in,0)+Math.max(0,t-at)*speed);
  }
  function parseVolumeEnvelope(value){
    if(!value)return [];
    try{
      var points=typeof value==='string'?JSON.parse(value):value;
      if(!Array.isArray(points)||points.length<2||points.length>128)return [];
      var last=-1;
      for(var i=0;i<points.length;i++){
        if(!points[i]||!Number.isFinite(Number(points[i].t))||!Number.isFinite(Number(points[i].v))
            ||Number(points[i].t)<0||Number(points[i].t)>600||Number(points[i].v)<0||Number(points[i].v)>1
            ||Number(points[i].t)<=last)return [];
        last=Number(points[i].t);
      }
      return points.map(function(point){return {t:Number(point.t),v:Number(point.v)};});
    }catch(_){return [];}
  }
  function volumeEnvelopeAt(points,localTime){
    if(!points||!points.length)return 1;
    if(localTime<=points[0].t)return points[0].v;
    for(var i=0;i<points.length-1;i++){
      var left=points[i],right=points[i+1];
      if(localTime<=right.t){
        var ratio=(localTime-left.t)/(right.t-left.t);
        return left.v+(right.v-left.v)*clamp(ratio,0,1);
      }
    }
    return points[points.length-1].v;
  }
  function makeClip(track,clip){
    var el,iframeLoaded=false,iframeLoadError=false,entry=null;
    if(clip.tag==='video')el=document.createElement('video');
    else if(clip.tag==='audio')el=document.createElement('audio');
    else if(clip.tag==='img')el=document.createElement('img');
    else if(clip.tag==='iframe')el=document.createElement('iframe');
    else return null;
    if(clip.tag==='iframe'){
      el.addEventListener('load',function(){
        iframeLoaded=true;if(entry)entry.iframeLoaded=true;
        // Only enable pointer hit-testing for HTML scenes while Inspect mode is on.
        // The normal preview deliberately uses pointer-events:none to keep the
        // monitor from stealing timeline/editor gestures.
        el.style.pointerEvents=state.inspectMode?'auto':'none';
        el.style.cursor=state.inspectMode?'crosshair':'';
        // Replay the current mode on load so a one-time postMessage cannot be lost.
        if(state.inspectMode&&el.contentWindow){
          try{el.contentWindow.postMessage({type:'film-inspect',enabled:true},'*');}catch(_){}
        }
      });
      el.addEventListener('error',function(){iframeLoadError=true;if(entry)entry.iframeLoadError=true;});
    }
    el.className='fs-runtime-media';el.id=String(clip.id||'');el.setAttribute('data-clip-id',clip.id);
    var clipClasses=String(clip.attrs&&clip.attrs.class||'').split(/\s+/).filter(function(name){return /^[A-Za-z_][A-Za-z0-9_-]{0,79}$/.test(name);});
    if(clipClasses.length)el.classList.add.apply(el.classList,clipClasses);
    if(clip.tag==='video'||clip.tag==='audio'){
      el.preload='auto';el.playsInline=true;
      el.volume=clamp(finite(clip.attrs&&clip.attrs.volume,1),0,1);
      el.muted=has(clip.attrs,'muted')||has(track.attrs,'muted')||has(track.attrs,'hidden');
      el.playbackRate=clamp(finite(clip.attrs&&clip.attrs.speed,1),0.25,4);
    }else if(clip.tag==='img'){
      el.alt=String(clip.attrs&&clip.attrs.alt||'');el.draggable=false;el.style.objectFit='contain';
    }else{
      el.setAttribute('title',String(clip.attrs&&clip.attrs.title||'影片页面'));
      el.setAttribute('sandbox','allow-scripts');
      el.style.border='0';
    }
    safeStyle(clip.attrs&&clip.attrs.style,el);
    if(clip.tag==='video'||clip.tag==='img'){
      if(!String(clip.attrs&&clip.attrs.style||'').match(/(?:^|;)\s*object-fit\s*:/i))el.style.objectFit='contain';
    }
    var x=finite(clip.attrs&&clip.attrs.x,NaN),y=finite(clip.attrs&&clip.attrs.y,NaN);
    var w=finite(clip.attrs&&clip.attrs.width,NaN),h=finite(clip.attrs&&clip.attrs.height,NaN);
    if(Number.isFinite(x))el.style.left=clamp(x,0,1)*state.data.stage.width+'px';
    if(Number.isFinite(y))el.style.top=clamp(y,0,1)*state.data.stage.height+'px';
    if(Number.isFinite(w))el.style.width=clamp(w,0.001,1)*state.data.stage.width+'px';
    if(Number.isFinite(h))el.style.height=clamp(h,0.001,1)*state.data.stage.height+'px';
    if(!el.style.left)el.style.left='0px';
    if(!el.style.top)el.style.top='0px';
    if(!el.style.width)el.style.width=state.data.stage.width+'px';
    if(!el.style.height)el.style.height=state.data.stage.height+'px';
    var rotate=finite(clip.attrs&&clip.attrs.rotate,NaN),opacity=finite(clip.attrs&&clip.attrs.opacity,NaN);
    if(Number.isFinite(rotate))el.style.rotate=rotate+'deg';
    if(Number.isFinite(opacity))el.style.opacity=String(clamp(opacity,0,1));
    var src=sourceBase(clip.media_url||'');
    if(src)el.src=src;
    var filmTransform=null,filmKeyframes=[];
    try{filmTransform=JSON.parse(String(clip.attrs&&clip.attrs['data-film-transform']||'null'));}catch(_){}
    try{filmKeyframes=JSON.parse(String(clip.attrs&&clip.attrs['data-film-keyframes']||'[]'));}catch(_){}
    if(!Array.isArray(filmKeyframes))filmKeyframes=[];
    var baseClipPath=String(el.style.clipPath||'');
    var maskKeyframes=parseMaskKeyframes(clip.attrs&&clip.attrs['data-film-mask-keyframes']);
    entry={track:track,clip:clip,el:el,active:false,iframeLoaded:iframeLoaded,iframeLoadError:iframeLoadError,
      volumeEnvelope:parseVolumeEnvelope(clip.attrs&&clip.attrs.volume_envelope),
      filmTransform:filmTransform&&typeof filmTransform==='object'?filmTransform:null,
      filmKeyframes:filmKeyframes,maskKeyframes:maskKeyframes,baseClipPath:baseClipPath,
      fade:parseClipFade(clip.attrs&&clip.attrs.fade),baseOpacity:clamp(finite(clip.attrs&&clip.attrs.opacity,finite(el.style.opacity||1,1)),0,1)};
    byId[clip.id]=entry;return entry;
  }
  function pauseAll(){
    state.tracks.forEach(function(track){
      track.clips.forEach(function(entry){
        if(entry.el.tagName==='VIDEO'||entry.el.tagName==='AUDIO'){try{entry.el.pause();}catch(_){}}
      });
    });
  }
  function loadDocument(data,rev){
    if(!data||!data.exists||!Array.isArray(data.tracks)){
      pauseAll();state.data=null;stage.innerHTML='';notice.style.display='grid';notice.textContent='等待 Studio 影片文档';return;
    }
    var time=state.time,playing=state.playing;
    pauseAll();stage.innerHTML='';byId=Object.create(null);
    state.data=data;state.rev=rev||data.rev||'';state.duration=Math.max(0,finite(data.duration,0));state.tracks=[];
    var oldStyle=document.getElementById('film-document-style');
    if(oldStyle)oldStyle.remove();
    if(data.render_css){
      var filmStyle=document.createElement('style');filmStyle.id='film-document-style';filmStyle.textContent=String(data.render_css);document.head.appendChild(filmStyle);
    }
    var tracks=data.tracks;
    tracks.forEach(function(track,index){
      var lane=document.createElement('div');lane.className='fs-runtime-track';lane.id=String(track.id||'');lane.setAttribute('data-track-id',track.id);
      var trackClasses=String(track.attrs&&track.attrs.class||'').split(/\s+/).filter(function(name){return /^[A-Za-z_][A-Za-z0-9_-]{0,79}$/.test(name);});
      if(trackClasses.length)lane.classList.add.apply(lane.classList,trackClasses);
      var requestedTrackZ=finite(track.attrs&&track.attrs["data-render-z-index"],NaN);
      // Last unnumbered timeline track is the foreground. Explicit migrated
      // data-render-z-index always overrides this fallback.
      lane.style.zIndex=Number.isFinite(requestedTrackZ)&&Math.floor(requestedTrackZ)===requestedTrackZ
        ?String(clamp(requestedTrackZ,-1000000,1000000)):String(index);
      if(has(track.attrs,'hidden'))lane.style.display='none';
      var entries=[];
      (track.clips||[]).forEach(function(clip){var entry=makeClip(track,clip);if(entry){lane.appendChild(entry.el);entries.push(entry);}});
      stage.appendChild(lane);
      // Capture class-based opacity after attachment so a fade multiplies, rather
      // than overwrites, any opacity supplied by the document's authored CSS.
      entries.forEach(function(entry){
        var attrs=entry.clip.attrs||{},styleText=String(attrs.style||'');
        if(!has(attrs,'opacity')&&!/(?:^|;)\s*opacity\s*:/i.test(styleText)){
          entry.baseOpacity=clamp(finite(window.getComputedStyle(entry.el).opacity,1),0,1);
        }
      });
      state.tracks.push({data:track,el:lane,clips:entries});
    });
    notice.style.display=state.tracks.length?'none':'grid';
    if(!state.tracks.length)notice.textContent='当前影片没有可显示的轨道';
    fitStage();state.time=clamp(time,0,state.duration);renderTime(state.time,true);
    state.playing=playing;
    if(playing){state.originTime=state.time;state.originClock=performance.now();}
    post({type:'film-runtime:loaded',rev:state.rev});
  }
  function parseMaskKeyframes(value){
    if(!value)return [];
    try{
      var points=typeof value==='string'?JSON.parse(value):value;
      if(!Array.isArray(points)||points.length<1||points.length>128)return [];
      var normalized=[],vertexCount=-1,last=-1;
      for(var i=0;i<points.length;i++){
        var point=points[i],at=Number(point&&point.at),easing=String(point&&point.easing||'linear'),
            value=point&&point.value;
        if(!Number.isFinite(at)||at<0||at>600||at<=last||!Array.isArray(value)||value.length<3||value.length>64)return [];
        if(vertexCount<0)vertexCount=value.length;
        if(value.length!==vertexCount)return [];
        var vertices=[];
        for(var v=0;v<value.length;v++){
          var pair=value[v];
          if(!Array.isArray(pair)||pair.length!==2)return [];
          var x=Number(pair[0]),y=Number(pair[1]);
          if(!Number.isFinite(x)||!Number.isFinite(y)||x<0||x>1||y<0||y>1)return [];
          vertices.push([x,y]);
        }
        if(['linear','ease_in','ease_out','ease_in_out'].indexOf(easing)<0)easing='linear';
        normalized.push({at:at,value:vertices,easing:easing});
        last=at;
      }
      return normalized;
    }catch(_){return [];}
  }
  function polygonFromVertices(vertices){
    return 'polygon('+vertices.map(function(pair){
      return (Number(pair[0])*100).toFixed(6).replace(/0+$/,'').replace(/\.$/,'')+'% '+
        (Number(pair[1])*100).toFixed(6).replace(/0+$/,'').replace(/\.$/,'')+'%';
    }).join(', ')+')';
  }
  function applyFilmMaskKeyframes(entry,t){
    var points=entry.maskKeyframes||[];
    if(!points.length){entry.el.style.clipPath=entry.baseClipPath;return;}
    var local=Math.max(0,t-finite(entry.clip.at,0));
    if(local<points[0].at){entry.el.style.clipPath=entry.baseClipPath;return;}
    var left=points[0],right=null;
    for(var i=1;i<points.length;i++){
      if(local<=points[i].at){left=points[i-1];right=points[i];break;}
      left=points[i];
    }
    var vertices=left.value;
    if(right){
      var span=right.at-left.at;
      var ratio=span<=0?1:filmEase((local-left.at)/span,right.easing);
      vertices=left.value.map(function(pair,index){
        return [
          pair[0]+(right.value[index][0]-pair[0])*ratio,
          pair[1]+(right.value[index][1]-pair[1])*ratio
        ];
      });
    }
    entry.el.style.clipPath=polygonFromVertices(vertices);
  }
  function filmEase(value,mode){
    var x=clamp(value,0,1);
    if(typeof mode==='string'){
      if(mode==='ease_in')return x*x;
      if(mode==='ease_out')return 1-(1-x)*(1-x);
      if(mode==='ease_in_out')return x<0.5?2*x*x:1-Math.pow(-2*x+2,2)/2;
      return x;
    }
    if(!mode||typeof mode!=='object')return x;
    var type=String(mode.type||'linear');
    var direction=String(mode.mode||'in').toLowerCase().replace(/_/g,'');
    var params=mode.parameters||{};
    function easeIn(t){
      if(type==='quad')return t*t;
      if(type==='cubic')return t*t*t;
      if(type==='quart')return t*t*t*t;
      if(type==='quint')return t*t*t*t*t;
      if(type==='sine')return 1-Math.cos(t*Math.PI/2);
      if(type==='expo')return t===0?0:Math.pow(2,10*t-10);
      if(type==='circ')return 1-Math.sqrt(Math.max(0,1-t*t));
      if(type==='back'){
        var overshoot=Number.isFinite(Number(params.overshoot))?Number(params.overshoot):1.70158;
        return (overshoot+1)*t*t*t-overshoot*t*t;
      }
      if(type==='elastic'){
        if(t===0||t===1)return t;
        var amplitude=Number.isFinite(Number(params.amplitude))?Math.max(1,Number(params.amplitude)):1;
        var period=Number.isFinite(Number(params.period))?Math.max(0.01,Number(params.period)):0.3;
        var shift=period/(2*Math.PI)*Math.asin(1/amplitude);
        return -amplitude*Math.pow(2,10*t-10)*Math.sin((t*10-10-shift*10)*2*Math.PI/period);
      }
      return t;
    }
    if(type==='linear')return x;
    if(direction==='out')return 1-easeIn(1-x);
    if(direction==='inout')return x<0.5?easeIn(x*2)/2:1-easeIn((1-x)*2)/2;
    return easeIn(x);
  }
  function filmKeyframeValue(channels,property,time,fallback){
    var channel=(channels||[]).find(function(item){return item&&item.property===property;});
    var points=channel&&Array.isArray(channel.keyframes)?channel.keyframes:[];
    if(!points.length)return fallback;
    if(time<finite(points[0].at,0))return fallback;
    if(time===finite(points[0].at,0))return finite(points[0].value,fallback);
    for(var i=1;i<points.length;i++){
      var right=points[i],left=points[i-1],rightAt=finite(right.at,0),leftAt=finite(left.at,0);
      if(time<=rightAt){
        var span=rightAt-leftAt;
        if(span<=0)return finite(right.value,fallback);
        var ratio=filmEase((time-leftAt)/span,right.easing);
        return finite(left.value,fallback)+(finite(right.value,fallback)-finite(left.value,fallback))*ratio;
      }
    }
    return finite(points[points.length-1].value,fallback);
  }
  function applyFilmTransform(entry,t){
    var cfg=entry.filmTransform;
    if(!cfg)return entry.baseOpacity;
    var channels=entry.filmKeyframes||[],local=Math.max(0,t-finite(entry.clip.at,0));
    var x=clamp(filmKeyframeValue(channels,'x',local,finite(cfg.x,0.5)),0,1);
    var y=clamp(filmKeyframeValue(channels,'y',local,finite(cfg.y,0.5)),0,1);
    var scale=clamp(filmKeyframeValue(channels,'scale',local,finite(cfg.scale,1)),0.1,5);
    // Camera-space channels are deterministic 2D view transforms. Keep a default
    // camera centered at (0.5, 0.5) with unit scale; movement shifts clips in the
    // opposite direction to camera travel, while zoom scales around frame center.
    var cameraX=clamp(filmKeyframeValue(channels,'camera_x',local,0.5),0,1);
    var cameraY=clamp(filmKeyframeValue(channels,'camera_y',local,0.5),0,1);
    var cameraScale=clamp(filmKeyframeValue(channels,'camera_scale',local,1),0.1,5);
    if(cameraX!==0.5||cameraY!==0.5||cameraScale!==1){
      x=clamp(0.5+(x-cameraX)*cameraScale,0,1);
      y=clamp(0.5+(y-cameraY)*cameraScale,0,1);
      scale=clamp(scale*cameraScale,0.1,5);
    }
    var rotation=clamp(filmKeyframeValue(channels,'rotation',local,finite(cfg.rotation,0)),-3600,3600);
    var opacity=clamp(filmKeyframeValue(channels,'opacity',local,finite(cfg.opacity,1)),0,1);
    var blur=clamp(filmKeyframeValue(channels,'blur',local,0),0,100);
    var offsetX=clamp(filmKeyframeValue(channels,'offset_x',local,0),-10000,10000);
    var offsetY=clamp(filmKeyframeValue(channels,'offset_y',local,0),-10000,10000);
    var stageW=Math.max(1,finite(cfg.stage_width,state.data.stage.width));
    var stageH=Math.max(1,finite(cfg.stage_height,state.data.stage.height));
    var baseW=Math.max(1,finite(cfg.base_width,stageW));
    var baseH=Math.max(1,finite(cfg.base_height,stageH));
    var anchors={
      top_left:[0,0],top_center:[0.5,0],top_right:[1,0],
      center_left:[0,0.5],center:[0.5,0.5],center_right:[1,0.5],
      bottom_left:[0,1],bottom_center:[0.5,1],bottom_right:[1,1]
    };
    var anchor=anchors[cfg.anchor]||anchors.center;
    var width=baseW*scale,height=baseH*scale;
    entry.el.style.left=(x*stageW-anchor[0]*width)+'px';
    entry.el.style.top=(y*stageH-anchor[1]*height)+'px';
    entry.el.style.width=width+'px';entry.el.style.height=height+'px';
    entry.el.style.transformOrigin=(anchor[0]*100)+'% '+(anchor[1]*100)+'%';
    entry.el.style.rotate=rotation+'deg';
    entry.el.style.translate=offsetX+'px '+offsetY+'px';
    entry.el.style.filter=blur>0?'blur('+blur+'px)':'none';
    return opacity;
  }
  function syncMedia(entry,t,active,forceSeek){
    var clip=entry.clip,track=entry.track,el=entry.el,tag=clip.tag,attrs=clip.attrs||{};
    var muted=has(track.attrs,'hidden')||has(track.attrs,'muted')||has(attrs,'muted');
    if(!active||has(track.attrs,'hidden')){
      el.style.display='none';entry.active=false;
      if(tag==='video'||tag==='audio'){try{el.pause();}catch(_){}}
      return;
    }
    el.style.display='block';
    var fadeFactor=clipFadeAt(entry,t);
    var transformOpacity=applyFilmTransform(entry,t);
    applyFilmMaskKeyframes(entry,t);
    if(tag!=='audio')el.style.opacity=String(clamp(transformOpacity*fadeFactor,0,1));
    if(tag==='iframe'){
      var localTime=mediaTime(clip,t);
      // During offline frame capture, prepareFrameEntry sends the strict frame(t)
      // request and waits for its acknowledgement; avoid a duplicate legacy seek.
      if(!state.renderMode){
        try{
          if(el.contentWindow)el.contentWindow.postMessage({
            type:'film-time',time:localTime,overrides:attrs.overrides||null
          },'*');
        }catch(_){}
      }
      entry.active=true;return;
    }
    if(tag==='img'){entry.active=true;return;}
    var desired=mediaTime(clip,t),speed=clamp(finite(attrs.speed,1),0.25,4);
    el.playbackRate=speed;
    var baseVolume=clamp(finite(attrs.volume,1),0,1);
    var localTime=Math.max(0,t-finite(clip.at,0));
    el.volume=clamp(baseVolume*volumeEnvelopeAt(entry.volumeEnvelope,localTime)*fadeFactor,0,1);
    el.muted=state.renderMode||muted;
    var drift=Math.abs(finite(el.currentTime,0)-desired);
    if(forceSeek||!entry.active||drift>(state.playing?0.45:0.04)){try{el.currentTime=desired;}catch(_){}}
    if(state.playing){
      if(el.paused){var promise=el.play();if(promise&&promise.catch)promise.catch(function(){});}
      else if(drift>0.8){try{el.currentTime=desired;}catch(_){}}
    }else{try{el.pause();}catch(_){}}
    entry.active=true;
  }
  function renderTime(value,forceSeek){
    if(!state.data)return;
    state.time=clamp(finite(value,0),0,state.duration);
    state.tracks.forEach(function(track){
      track.clips.forEach(function(entry){
        var clip=entry.clip,at=finite(clip.at,0),end=clip.end;
        var active=at<=state.time&&(end==null||state.time<end||(!state.renderMode&&Math.abs(state.time-end)<0.0001));
        syncMedia(entry,state.time,active,!!forceSeek);
      });
    });
    var now=performance.now();
    if(now-state.lastParentUpdate>90){state.lastParentUpdate=now;post({type:'film-runtime:time',time:state.time,playing:state.playing,duration:state.duration,rev:state.rev});}
  }
  function frame(){
    if(!state.playing)return;
    var t=state.originTime+(performance.now()-state.originClock)/1000;
    if(t>=state.duration){state.playing=false;state.time=state.duration;renderTime(state.time,true);pauseAll();post({type:'film-runtime:ended',time:state.time});return;}
    renderTime(t,false);state.raf=requestAnimationFrame(frame);
  }
  function play(){
    if(!state.data)return;
    if(state.time>=state.duration)state.time=0;
    state.playing=true;state.originTime=state.time;state.originClock=performance.now();
    renderTime(state.time,true);state.raf=requestAnimationFrame(frame);
  }
  function waitForEvent(element,eventName,timeoutMs){
    return new Promise(function(resolve){
      var done=false;
      function finish(){if(done)return;done=true;element.removeEventListener(eventName,finish);resolve();}
      element.addEventListener(eventName,finish,{once:true});
      setTimeout(finish,timeoutMs||8000);
    });
  }
  function nextFrame(){return new Promise(function(resolve){requestAnimationFrame(function(){resolve();});});}
  async function prepareFrameEntry(entry,t,frameNumber){
    var clip=entry.clip,el=entry.el,tag=clip.tag;
    var at=finite(clip.at,0),end=clip.end;
    if(!(at<=t&&(end==null||t<end||Math.abs(t-end)<0.0001)))return;
    if(tag==='video'||tag==='audio'){
      el.muted=true;
      try{el.pause();}catch(_){}
      if(el.readyState===0)await waitForEvent(el,'loadedmetadata',12000);
      if(el.readyState===0)throw new Error('素材元数据加载失败：'+clip.id);
      var desired=mediaTime(clip,t);
      if(Number.isFinite(el.duration)&&el.duration>0)desired=Math.min(desired,Math.max(0,el.duration-0.001));
      if(Math.abs(finite(el.currentTime,0)-desired)>0.015){
        var seeked=waitForEvent(el,'seeked',12000);
        try{el.currentTime=desired;}catch(error){throw new Error('无法定位素材时间：'+clip.id+' '+error.message);}
        await seeked;
      }
      if(tag==='video'&&typeof el.requestVideoFrameCallback==='function'){
        await Promise.race([new Promise(function(resolve){el.requestVideoFrameCallback(function(){resolve();});}),new Promise(function(resolve){setTimeout(resolve,12000);})]);
      }
    }else if(tag==='img'){
      if(!el.complete)await waitForEvent(el,'load',12000);
      if(typeof el.decode==='function')try{await el.decode();}catch(_){}
    }else if(tag==='iframe'){
      var localTime=mediaTime(clip,t);
      if(entry.iframeLoadError)throw new Error('HTML 镜头加载失败：'+clip.id);
      if(!entry.iframeLoaded){
        await new Promise(function(resolve,reject){
          var timer=setTimeout(function(){cleanup();reject(new Error('HTML 镜头加载超时：'+clip.id));},15000);
          function cleanup(){clearTimeout(timer);el.removeEventListener('load',loaded);el.removeEventListener('error',failed);}
          function loaded(){cleanup();entry.iframeLoaded=true;resolve();}
          function failed(){cleanup();entry.iframeLoadError=true;reject(new Error('HTML 镜头加载失败：'+clip.id));}
          el.addEventListener('load',loaded,{once:true});el.addEventListener('error',failed,{once:true});
          if(entry.iframeLoaded){cleanup();resolve();}
        });
      }
      var sceneSrc=String(clip.attrs&&clip.attrs.src||'').split('#',1)[0].replace(/\\/g,'/');
      if(sceneSrc.endsWith('scenes/subtitles.html')){
        // Generated subtitle pages retain their backward-compatible exact-time
        // acknowledgement; authored scenes use the explicit frame(t) contract.
        await new Promise(function(resolve,reject){
          var timer=setTimeout(function(){window.removeEventListener('message',onMessage);reject(new Error('字幕页未确认指定帧：'+clip.id));},6000);
          function onMessage(event){
            if(event.source!==el.contentWindow||!event.data||event.data.type!=='film-time:ready')return;
            if(Math.abs(Number(event.data.time)-localTime)>0.0001)return;
            clearTimeout(timer);window.removeEventListener('message',onMessage);resolve();
          }
          window.addEventListener('message',onMessage);
          el.contentWindow.postMessage({type:'film-time',time:localTime,overrides:(clip.attrs||{}).overrides||null},'*');
        });
      }else{
        await new Promise(function(resolve,reject){
          var timer=setTimeout(function(){
            window.removeEventListener('message',onMessage);
            reject(new Error('HTML 镜头 '+clip.id+' 未在规定时间内响应 window.film.frame(t)'));
          },15000);
          function onMessage(event){
            var reply=event.data;
            if(event.source!==el.contentWindow||!reply||reply.type!=='film-frame:ready')return;
            if(Number(reply.frame)!==Number(frameNumber)||Math.abs(Number(reply.time)-localTime)>0.0001)return;
            clearTimeout(timer);window.removeEventListener('message',onMessage);
            if(!reply.supported)reject(new Error(String(reply.error||('HTML 镜头 '+clip.id+' 未实现 window.film.frame(t)'))));
            else if(reply.error)reject(new Error(String(reply.error)));
            else resolve();
          }
          window.addEventListener('message',onMessage);
          try{
            if(!el.contentWindow)throw new Error('镜头 iframe 尚未创建');
            el.contentWindow.postMessage({type:'film-frame',time:localTime,frame:frameNumber,overrides:(clip.attrs||{}).overrides||null},'*');
          }catch(error){
            clearTimeout(timer);window.removeEventListener('message',onMessage);reject(error);
          }
        });
      }
    }
  }
  async function renderFrame(message){
    state.renderMode=true;state.playing=false;
    if(state.raf)cancelAnimationFrame(state.raf);state.raf=0;
    state.time=clamp(finite(message.time,0),0,state.duration);
    renderTime(state.time,true);
    try{
      var jobs=[];
      state.tracks.forEach(function(track){track.clips.forEach(function(entry){jobs.push(prepareFrameEntry(entry,state.time,message.frame));});});
      await Promise.all(jobs);
      await nextFrame();await nextFrame();
      post({type:'film-runtime:frame-ready',frame:message.frame,time:state.time,rev:state.rev});
    }catch(error){
      post({type:'film-runtime:frame-ready',frame:message.frame,time:state.time,rev:state.rev,error:String(error&&error.message||error)});
    }
  }
  function pause(){
    state.playing=false;if(state.raf)cancelAnimationFrame(state.raf);state.raf=0;
    renderTime(state.time,true);pauseAll();post({type:'film-runtime:paused',time:state.time});
  }
  window.addEventListener('resize',fitStage);
  function findEntryForFrame(source){
    for(var i=0;i<state.tracks.length;i++){
      for(var j=0;j<state.tracks[i].clips.length;j++){
        var entry=state.tracks[i].clips[j];
        if(entry.clip.tag==='iframe'&&entry.el.contentWindow===source)return entry;
      }
    }
    return null;
  }
  function forwardSceneElementEvent(event,message){
    var mappedType={
      'film-element-selected':'film-runtime:element-selected',
      'film-element-hover':'film-runtime:element-hover',
      'film-element-preview':'film-runtime:element-preview',
      'film-element-drag-end':'film-runtime:element-drag-end',
      'film-inspect-ready':'film-runtime:inspect-ready'
    };
    if(!message||!mappedType[message.type])return false;
    var entry=findEntryForFrame(event.source);
    if(!entry)return false;
    var rect=message.rect||{},viewportSize=message.viewport||{};
    var localWidth=Math.max(1,finite(viewportSize.width,entry.el.clientWidth));
    var localHeight=Math.max(1,finite(viewportSize.height,entry.el.clientHeight));
    var scaleX=entry.el.clientWidth/localWidth,scaleY=entry.el.clientHeight/localHeight;
    var left=entry.el.offsetLeft,top=entry.el.offsetTop;
    post({
      type:mappedType[message.type],
      rev:state.rev,clipId:entry.clip.id,selector:message.selector,tag:message.tag,text:message.text,
      textEditable:message.textEditable!==false,styles:message.styles||{},
      rect:{
        x:left+finite(rect.x,0)*scaleX,y:top+finite(rect.y,0)*scaleY,
        width:finite(rect.width,0)*scaleX,height:finite(rect.height,0)*scaleY
      },
      // Keep preview-space rect for the selection overlay, but preserve the child document's CSS design-space box for inspector round-tripping.
      localRect:message.localRect||rect,enabled:message.enabled===true
    });
    return true;
  }
  function sendInspectMode(enabled){
    state.tracks.forEach(function(track){
      track.clips.forEach(function(entry){
        if(entry.clip.tag!=='iframe')return;
        // HTML scenes must be hit-testable in Inspect mode or browser pointer
        // events never reach the injected child-page selection bridge.
        entry.el.style.pointerEvents=enabled?'auto':'none';
        entry.el.style.cursor=enabled?'crosshair':'';
        if(entry.el.contentWindow){
          try{entry.el.contentWindow.postMessage({type:'film-inspect',enabled:!!enabled},'*');}catch(_){}
        }
      });
    });
  }
  window.addEventListener('message',function(event){
    if(event.source!==parent){
      forwardSceneElementEvent(event,event.data||{});
      return;
    }
    if(event.origin!==window.location.origin)return;
    var message=event.data||{};
    if(message.type==='film-runtime:load'){loadDocument(message.data,message.rev);post({type:'film-runtime:ready',rev:state.rev});}
    else if(message.type==='film-runtime:seek'){
      if(Number.isFinite(Number(message.time))){
        state.time=clamp(Number(message.time),0,state.duration);
        if(state.playing){state.originTime=state.time;state.originClock=performance.now();}
        renderTime(state.time,true);
      }
    }
    else if(message.type==='film-runtime:seek-pause'){
      state.playing=false;if(state.raf)cancelAnimationFrame(state.raf);state.raf=0;
      state.time=clamp(finite(message.time,0),0,state.duration);
      renderTime(state.time,true);pauseAll();post({type:'film-runtime:paused',time:state.time});
    }
    else if(message.type==='film-runtime:play')play();
    else if(message.type==='film-runtime:pause')pause();
    else if(message.type==='film-runtime:render-frame')renderFrame(message);
    else if(message.type==='film-runtime:inspect'){
      state.inspectMode=!!message.enabled;
      sendInspectMode(state.inspectMode);
    }
    else if(message.type==='film-runtime:element-edit'){
      var target=byId[message.clipId];
      if(target&&target.clip.tag==='iframe'&&target.el.contentWindow){
        try{target.el.contentWindow.postMessage({type:'film-element-edit',override:message.override},'*');}catch(_){}
      }
    }
  });
  post({type:'film-runtime:ready'});
})();
