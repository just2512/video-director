/* Multi-track browser preview from the authoritative film.html projection. */
(function(){
  'use strict';
  var state={data:null,time:0,playing:false,renderMode:false,originTime:0,originClock:0,lastParentUpdate:0,rev:'',tracks:[],duration:0,raf:0};
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
    var allowed={left:1,top:1,width:1,height:1,opacity:1,'object-fit':1,'border-radius':1,rotate:1};
    String(styleText||'').split(';').forEach(function(part){
      var colon=part.indexOf(':');if(colon<1)return;
      var key=part.slice(0,colon).trim().toLowerCase(),value=part.slice(colon+1).trim();
      if(!allowed[key]||!value||/[{};]/.test(value)||/url\s*\(|expression\s*\(|javascript:/i.test(value))return;
      if(['left','top','width','height'].indexOf(key)>=0&&!/^-?[0-9]+(?:\.[0-9]+)?px$/.test(value))return;
      if(key==='opacity'&&(!Number.isFinite(Number(value))||Number(value)<0||Number(value)>1))return;
      if(key==='object-fit'&&['cover','contain','fill','none','scale-down'].indexOf(value)<0)return;
      el.style.setProperty(key,value);
    });
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
    var el;
    if(clip.tag==='video')el=document.createElement('video');
    else if(clip.tag==='audio')el=document.createElement('audio');
    else if(clip.tag==='img')el=document.createElement('img');
    else if(clip.tag==='iframe')el=document.createElement('iframe');
    else return null;
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
    var entry={track:track,clip:clip,el:el,active:false,volumeEnvelope:parseVolumeEnvelope(clip.attrs&&clip.attrs.volume_envelope)};
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
      lane.style.zIndex=String(tracks.length-index);
      if(has(track.attrs,'hidden'))lane.style.display='none';
      var entries=[];
      (track.clips||[]).forEach(function(clip){var entry=makeClip(track,clip);if(entry){lane.appendChild(entry.el);entries.push(entry);}});
      stage.appendChild(lane);state.tracks.push({data:track,el:lane,clips:entries});
    });
    notice.style.display=state.tracks.length?'none':'grid';
    if(!state.tracks.length)notice.textContent='当前影片没有可显示的轨道';
    fitStage();state.time=clamp(time,0,state.duration);renderTime(state.time,true);
    state.playing=playing;
    if(playing){state.originTime=state.time;state.originClock=performance.now();}
    post({type:'film-runtime:loaded',rev:state.rev});
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
    if(tag==='iframe'){
      var localTime=mediaTime(clip,t);
      try{if(el.contentWindow)el.contentWindow.postMessage({type:'film-time',time:localTime},'*');}catch(_){}
      entry.active=true;return;
    }
    if(tag==='img'){entry.active=true;return;}
    var desired=mediaTime(clip,t),speed=clamp(finite(attrs.speed,1),0.25,4);
    el.playbackRate=speed;
    var baseVolume=clamp(finite(attrs.volume,1),0,1);
    var localTime=Math.max(0,t-finite(clip.at,0));
    el.volume=clamp(baseVolume*volumeEnvelopeAt(entry.volumeEnvelope,localTime),0,1);
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
  async function prepareFrameEntry(entry,t){
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
      try{
        if(el.contentWindow)el.contentWindow.postMessage({type:'film-time',time:localTime},'*');
      }catch(_){}
      if(String(clip.attrs&&clip.attrs.src||'').split('#',1)[0].replace(/\\/g,'/').endsWith('scenes/subtitles.html')){
        await new Promise(function(resolve,reject){
          var timer=setTimeout(function(){window.removeEventListener('message',onMessage);reject(new Error('字幕页未确认指定帧：'+clip.id));},6000);
          function onMessage(event){
            if(event.source!==el.contentWindow||!event.data||event.data.type!=='film-time:ready')return;
            if(Math.abs(Number(event.data.time)-localTime)>0.0001)return;
            clearTimeout(timer);window.removeEventListener('message',onMessage);resolve();
          }
          window.addEventListener('message',onMessage);
          el.contentWindow.postMessage({type:'film-time',time:localTime},'*');
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
      state.tracks.forEach(function(track){track.clips.forEach(function(entry){jobs.push(prepareFrameEntry(entry,state.time));});});
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
  window.addEventListener('message',function(event){
    if(event.source!==parent||event.origin!==window.location.origin)return;
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
  });
  post({type:'film-runtime:ready'});
})();
