/* Editor UX enhancement: OpenCut-style visual editing surface on top of Composition IR.
 * No second editor/core: existing Command Bus + Composition API remain canonical.
 */
(function(){
  'use strict';

  window.EDX = window.EDX || {
    draftRunId: '',
    draftPolling: null,
    materialMatch: null,
    transitionProposal: null,
    liveMode: true,
  };

  function escx(s){ return typeof esc === 'function' ? esc(s) : String(s ?? '').replace(/[&<>"]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m])); }
  function comp(){ return window.ED && ED.comp; }
  function sceneOf(id){
    const c=comp(); return c && c.scenes ? c.scenes.find(s=>s.id===id) : null;
  }
  function primaryLayer(sc){
    if(!sc || !Array.isArray(sc.layers)) return null;
    return sc.layers.find(l=>l.type==='video') || sc.layers.find(l=>l.type==='image') || sc.layers[0] || null;
  }
  function layerAt(sceneId, idx){
    const sc=sceneOf(sceneId); return sc && sc.layers ? sc.layers[idx] : null;
  }
  function assetSrc(lay){
    return lay && lay.asset_file ? '/file?path=' + encodeURIComponent(lay.asset_file) : '';
  }
  function thumbMarkup(lay, cls='edx-media'){
    if(!lay) return '<div class="edx-media edx-empty">暂无画面</div>';
    const src=assetSrc(lay);
    if(!src) return '<div class="edx-media edx-empty">素材未找到</div>';
    if(lay.type==='video') return '<video class="'+cls+'" data-edx-thumb muted playsinline preload="metadata" src="'+src+'"></video>';
    if(lay.type==='image') return '<img class="'+cls+'" loading="lazy" src="'+src+'" alt="">';
    return '<div class="edx-media edx-empty">'+escx(lay.type || '未知画面')+'</div>';
  }

  function sceneHasOriginalAudio(sc){
    const c=comp(); if(!c) return false;
    return (c.audio||[]).some(a=>a.type==='original' &&
      ((sc && sc.layers||[]).some(l=>l.asset_id===a.asset_id)));
  }
  function transitionText(sc){
    const t=sc && sc.transition_out;
    if(!t) return '';
    const type=t.type==='crossfade'?'溶解':(t.type==='fade'?'淡化':t.type);
    return type ? '转场 · '+type+(t.duration?(' '+Number(t.duration).toFixed(1)+'s'):'') : '';
  }

  function selectSceneUx(id, ev){
    if(ev) ev.stopPropagation();
    if(typeof editorSelectScene==='function') editorSelectScene(id, ev || null);
    renderStoryboard();
    refreshDirectorControls();
    renderMaterialMatch();
    renderTransitionProposal();
  }

  function renderStoryboard(){
    const box=document.getElementById('editor-storyboard');
    const c=comp(); if(!box || !c || !Array.isArray(c.scenes)) return;
    box.innerHTML =
      '<div class="edx-strip-head"><b>分镜</b><span class="hint">拖动场景调整顺序；点击进入画面、声音和文本编辑</span></div>'+
      '<div class="edx-storyboard-scroll">'+
      c.scenes.map((sc,i)=>{
        const lay=primaryLayer(sc), selected=typeof edIsSelected==='function'&&edIsSelected('scene',sc.id);
        const t=transitionText(sc);
        return '<div class="edx-story-card'+(selected?' selected':'')+'" data-edx-scene="'+escx(sc.id)+'" draggable="true">'+
          '<button class="edx-story-media" type="button" onclick="edxSelectScene(\''+escx(sc.id)+'\',event)">'+thumbMarkup(lay)+'</button>'+
          '<div class="edx-story-overlay"><span>'+String(i+1).padStart(2,'0')+'</span><span>'+Number(sc.duration||0).toFixed(1)+'s</span></div>'+
          '<div class="edx-story-body"><div class="edx-story-text">'+escx(sc.text||'（无文案）')+'</div>'+
          '<div class="edx-story-meta">'+
            (sceneHasOriginalAudio(sc)?'<span class="edx-badge">原声</span>':'')+
            (t?'<span class="edx-badge edx-transition">'+escx(t)+'</span>':'')+
          '</div></div></div>';
      }).join('')+
      '</div>';
    box.querySelectorAll('[data-edx-scene]').forEach(card=>{
      card.addEventListener('dragstart',ev=>{
        ev.dataTransfer.setData('text/plain',card.dataset.edxScene);
        ev.dataTransfer.effectAllowed='move';
      });
      card.addEventListener('dragover',ev=>ev.preventDefault());
      card.addEventListener('drop',async ev=>{
        ev.preventDefault();
        const from=ev.dataTransfer.getData('text/plain'), to=card.dataset.edxScene;
        if(!from || from===to) return;
        const idx=Array.from(c.scenes).findIndex(s=>s.id===to);
        if(idx<0 || typeof executeCommand!=='function') return;
        const r=await executeCommand({command:'move',targetId:from,payload:{toIndex:idx}});
        if(r.ok){ await refreshComposition(); }
      });
    });
    box.querySelectorAll('video[data-edx-thumb]').forEach(v=>{
      v.addEventListener('loadedmetadata',()=>{
        try{ v.currentTime=Math.min(0.15,Math.max(0,v.duration-0.05)); }catch(e){}
      },{once:true});
    });
  }

  function decorateTimeline(){
    const root=document.getElementById('editor-timeline');
    const c=comp(); if(!root || !c) return;
    root.querySelectorAll('.ed-scene[data-scene]').forEach(el=>{
      const sc=sceneOf(el.dataset.scene), lay=primaryLayer(sc);
      if(!sc || !lay || el.querySelector('.edx-tl-thumb')) return;
      const header=el.querySelector('.ed-scene-header');
      if(!header) return;
      const thumb=document.createElement('div');
      thumb.className='edx-tl-thumb';
      thumb.innerHTML=thumbMarkup(lay,'edx-tl-media');
      header.insertBefore(thumb,header.firstChild);
      const tv=thumb.querySelector('video');
      if(tv) tv.addEventListener('loadedmetadata',()=>{try{tv.currentTime=Math.min(.12,Math.max(0,tv.duration-.04));}catch(e){}},{once:true});
    });
    root.querySelectorAll('.ed-layer[data-scene][data-idx]').forEach(el=>{
      const sc=sceneOf(el.dataset.scene), lay=layerAt(el.dataset.scene,Number(el.dataset.idx));
      if(!sc || !lay || el.querySelector('.edx-layer-thumb')) return;
      const src=assetSrc(lay);
      if(!src) return;
      const node=document.createElement('span');
      node.className='edx-layer-thumb';
      node.innerHTML=thumbMarkup(lay,'edx-layer-media');
      el.insertBefore(node,el.firstChild);
      const tv=node.querySelector('video');
      if(tv) tv.addEventListener('loadedmetadata',()=>{try{tv.currentTime=.1;}catch(e){}},{once:true});
    });
  }

  function liveSceneAt(sec){
    const c=comp(); if(!c || !c.scenes.length) return;
    let sc=c.scenes[c.scenes.length-1];
    for(const s of c.scenes){
      if(sec >= s.start && sec < s.start+s.duration){ sc=s; break; }
    }
    const lay=primaryLayer(sc);
    const stage=document.getElementById('ed-preview-stage');
    const meta=document.getElementById('ed-preview-meta');
    if(!stage) return;
    if(!lay || !assetSrc(lay)){
      stage.innerHTML='<div class="edx-live-empty"><b>'+escx(sc.text||'当前场景')+'</b><span>该场景暂无可预览画面</span></div>';
      if(meta) meta.textContent='编辑预览 · '+sc.id;
      return;
    }
    const rel=Math.max(0,sec-sc.start);
    if(lay.type==='image'){
      stage.innerHTML='<img class="edx-live-image" src="'+assetSrc(lay)+'" alt="">';
    } else if(lay.type==='video'){
      stage.innerHTML='<video class="edx-live-video" controls playsinline preload="metadata" src="'+assetSrc(lay)+'"></video>';
      const v=stage.querySelector('video');
      v.addEventListener('loadedmetadata',()=>{
        const sourceIn=Number(lay.source_in||0), max=Math.max(0,(v.duration||999999)-.02);
        try{v.currentTime=Math.min(max,sourceIn+rel*(Number(lay.speed||1)));}catch(e){}
      },{once:true});
    } else {
      stage.innerHTML='<div class="edx-live-empty"><b>'+escx(sc.text||'当前场景')+'</b><span>'+escx(layerTypeCn?layerTypeCn(lay.type):lay.type)+'</span></div>';
    }
    if(meta) meta.textContent='编辑预览 · '+sc.id+' · '+Number(sc.duration||0).toFixed(1)+'s';
    stage.classList.add('edx-live');
  }

  async function enhancedRefreshPreview(){
    if(!ED || !ED.pid) return;
    try{
      const info=await apiGet('/api/projects/'+encodeURIComponent(ED.pid)+'/draft_info');
      PV.draftPath=info&&info.draft_path||'';
      PV.finalPath=info&&info.final_path||'';
      if(PV.draftPath){
        if(typeof previewShowVideo==='function') previewShowVideo(PV.draftPath,'草稿预览');
      }else{
        liveSceneAt(Number(PV.playhead||0));
      }
    }catch(e){
      liveSceneAt(Number(PV.playhead||0));
    }
    renderStoryboard();
    decorateTimeline();
    refreshDirectorControls();
  }

  function patchPreview(){
    if(window.__edxPreviewPatched) return;
    window.__edxPreviewPatched=true;
    const oldSeek=window.previewSeek, oldSelectOverlay=window.previewShowSelectionOverlay;
    window.previewSeek=function(sec){
      if(oldSeek) oldSeek(sec);
      PV.playhead=Number(sec)||0;
      if(!PV.draftPath) liveSceneAt(PV.playhead);
    };
    window.previewShowSelectionOverlay=function(){
      if(PV.draftPath && oldSelectOverlay) return oldSelectOverlay();
      const s=SEL&&SEL.selection;
      const c=comp();
      const sc=s&&s.type==='scene'?sceneOf(s.id):(s&&s.type==='layer'?sceneOf(String(s.id).split('#')[0]):null);
      if(sc) liveSceneAt(Number(sc.start||0));
      else liveSceneAt(Number(PV.playhead||0));
    };
  }

  function setButtonBusy(id,busy,label){
    const b=document.getElementById(id); if(!b) return;
    b.disabled=!!busy;
    if(busy){ b.dataset.edxOldText=b.textContent; b.textContent=label||'处理中…';}
    else if(b.dataset.edxOldText){b.textContent=b.dataset.edxOldText;delete b.dataset.edxOldText;}
  }

  async function renderDraft(){
    if(!ED||!ED.pid){editorToast('先打开项目',true);return;}
    if(EDX.draftPolling){editorToast('草稿正在渲染中');return;}
    setButtonBusy('edx-render-draft',true,'渲染中…');
    try{
      const r=await apiPost('/api/workflows',{kind:'render_draft',project_id:ED.pid});
      if(!r||!r.run_id) throw new Error((r&&r.error)||'未创建渲染任务');
      EDX.draftRunId=r.run_id;
      editorToast('草稿渲染已开始，可继续编辑');
      EDX.draftPolling=setInterval(async()=>{
        try{
          const run=await apiGet('/api/workflows/'+encodeURIComponent(EDX.draftRunId));
          if(!run) return;
          if(run.status==='COMPLETED'){
            clearInterval(EDX.draftPolling);EDX.draftPolling=null;
            setButtonBusy('edx-render-draft',false);
            await enhancedRefreshPreview();
            editorToast('草稿已生成，中央预览已更新');
          }else if(run.status==='FAILED'){
            clearInterval(EDX.draftPolling);EDX.draftPolling=null;
            setButtonBusy('edx-render-draft',false);
            editorToast('草稿渲染失败：'+(run.error||'未知错误'),true);
          }
        }catch(e){}
      },900);
    }catch(e){
      setButtonBusy('edx-render-draft',false);
      editorToast('草稿渲染未启动：'+(e.message||e),true);
    }
  }

  async function materialMatch(){
    if(!ED||!ED.pid){editorToast('先打开项目',true);return;}
    const box=document.getElementById('director-control-grid');
    if(box) box.classList.add('loading');
    try{
      const r=await apiGet('/api/editor/material_match?project_id='+encodeURIComponent(ED.pid));
      if(!r || r.error) throw new Error((r&&r.error)||'素材匹配失败');
      EDX.materialMatch=r;
      renderMaterialMatch();
      editorToast((r.matches||[]).length?'已生成素材匹配建议，尚未修改时间线':'没有可形成的素材匹配');
    }catch(e){editorToast('素材匹配失败：'+(e.message||e),true);}
    finally{if(box)box.classList.remove('loading');}
  }

  async function applyMaterialMatch(){
    const m=EDX.materialMatch;
    if(!m || !m.matches || !m.matches.length){editorToast('先生成素材匹配建议');return;}
    const ops=[];
    for(const x of m.matches){
      if(!x.layer_target || !x.asset_id) continue;
      ops.push({command:'replace_source',targetId:x.layer_target,payload:{assetId:x.asset_id}});
      const sc=sceneOf(x.scene_id), lay=sc&&primaryLayer(sc);
      if(lay && lay.asset_id===x.asset_id){
        ops.push({command:'set_original_audio',targetId:'project',
          payload:{mode:'preserve',assetId:x.asset_id,start:Number(sc.start||0),
            duration:Number(Math.min(sc.duration||0,x.source_duration||sc.duration||0)),
            sourceIn:Number(x.source_in||0),sourceOut:x.source_out==null?null:Number(x.source_out),
            volume:0.8}});
      }else{
        ops.push({command:'set_original_audio',targetId:'project',
          payload:{mode:'preserve',assetId:x.asset_id,start:Number(sc&&sc.start||0),
            duration:Number(sc&&sc.duration||0),sourceIn:Number(x.source_in||0),
            sourceOut:x.source_out==null?null:Number(x.source_out),volume:0.8}});
      }
    }
    if(!ops.length){editorToast('没有可以应用的匹配项');return;}
    // executeCommand 不支持任意子命令 batch，顺序提交并在每次成功后同步版本。
    for(const op of ops){
      const r=await executeCommand(op);
      if(!r.ok){editorToast('应用在 '+(op.targetId||'项目')+' 处停止，已保留已成功的修改',true);return;}
    }
    EDX.materialMatch=null;
    await refreshComposition();
    editorToast('素材匹配已应用；原声已按素材片段保留');
  }

  function renderMaterialMatch(){
    const box=document.getElementById('director-material-match');
    if(!box)return;
    const m=EDX.materialMatch;
    if(!m){box.innerHTML='<div class="hint">未生成匹配建议。点击「按文案匹配我的素材」后，这里先展示建议，不会直接改时间线。</div>';return;}
    box.innerHTML='<div class="edx-match-head"><b>素材匹配建议</b><button class="btn-secondary btn-sm" onclick="edxApplyMaterialMatch()">全部采用</button></div>'+
      (m.matches||[]).map(x=>'<div class="edx-match-row">'+
        thumbMarkup({type:'video',asset_file:x.asset_file})+
        '<div><b>'+escx(x.scene_id)+'</b><div class="hint">'+escx(x.scene_text||'')+'</div><div>→ '+escx(x.asset_name||x.asset_id)+'</div></div>'+
      '</div>').join('')+
      ((m.unmatched||[]).length?'<div class="hint">未形成匹配：'+escx(m.unmatched.join('、'))+'。这些场景不会被自动替换。</div>':'');
  }

  function proposeTransitions(){
    const c=comp(); if(!c||c.scenes.length<2)return;
    EDX.transitionProposal=c.scenes.slice(0,-1).map((sc,i)=>({
      from:sc.id,to:c.scenes[i+1].id,type:'crossfade',duration:.5,status:'PROPOSED'
    }));
    renderTransitionProposal();
  }
  function renderTransitionProposal(){
    const box=document.getElementById('director-transition-proposal'); if(!box)return;
    const p=EDX.transitionProposal;
    if(!p){box.innerHTML='<div class="hint">转场默认不自动应用。点击「生成转场建议」后逐处确认。</div>';return;}
    box.innerHTML=p.map((x,i)=>{
      const a=sceneOf(x.from),b=sceneOf(x.to);
      return '<div class="edx-transition-row">'+
        thumbMarkup(primaryLayer(a),'edx-pair-thumb')+'<span>→</span>'+thumbMarkup(primaryLayer(b),'edx-pair-thumb')+
        '<div class="edx-transition-actions"><b>'+escx(x.from)+' → '+escx(x.to)+'</b>'+
        '<span class="hint">'+(x.status==='APPROVED'?'已确认溶解':'待确认 · 溶解 0.5s')+'</span>'+
        '<button class="btn-sm" onclick="edxApproveTransition('+i+')">'+(x.status==='APPROVED'?'已确认':'确认溶解')+'</button>'+
        '<button class="btn-secondary btn-sm" onclick="edxRejectTransition('+i+')">保持硬切</button></div></div>';
    }).join('');
  }
  async function approveTransition(i){
    const p=EDX.transitionProposal&&EDX.transitionProposal[i]; if(!p)return;
    if(p.status==='APPROVED')return;
    // 复用既有 continuity/transition UI command path：这里直接走 registered action
    // relation.set 不同实现可能要求 continuity type，因此先用 natural action only when available.
    // 跨场景转场本身由渲染器读取 scene.transition_out，已有 action 没有专用 transition command。
    // 为避免猜 payload，不直接改 IR；给出明确提示并保留 proposal。
    p.status='APPROVED_PENDING'; renderTransitionProposal();
    editorToast('转场已标记为待应用；当前 Command Bus 没有专用“transition”命令，因此未擅自写入 IR。请在 Inspector 的转场控件确认。',true);
  }
  function rejectTransition(i){
    if(!EDX.transitionProposal)return;
    EDX.transitionProposal[i].status='REJECTED';
    renderTransitionProposal();
  }

  function scrollTo(id){
    const el=document.getElementById(id); if(el) el.scrollIntoView({behavior:'smooth',block:'nearest'});
  }
  function refreshDirectorControls(){
    const box=document.getElementById('director-control-grid');
    if(!box) return;
    const sel=window.SEL&&SEL.selection;
    const sceneId=sel ? (sel.type==='scene'?sel.id:String(sel.id||'').split('#')[0]) : '';
    const sc=sceneOf(sceneId), lay=sc&&primaryLayer(sc);
    box.querySelectorAll('[data-edx-selected-scene]').forEach(e=>e.textContent=sc?sc.id:'未选择');
    const raw=document.getElementById('director-plan-detail');
    if(raw && !(window.DEV&&DEV.mode)) raw.style.display='none';
    if(box.dataset.ready==='1') return;
    box.dataset.ready='1';
    renderMaterialMatch(); renderTransitionProposal();
  }
  function directorEdit(action){
    const sel=window.SEL&&SEL.selection, sceneId=sel?(sel.type==='scene'?sel.id:String(sel.id||'').split('#')[0]):'';
    if(action==='script'){
      const ta=document.getElementById('director-input'); if(ta){ta.focus();ta.scrollIntoView({behavior:'smooth',block:'center'});}
    }else if(action==='scene'){selectSceneUx(sceneId);}
    else if(action==='visual'){
      if(typeof editorOpenAssetPicker==='function') editorOpenAssetPicker(sceneId,0);
    }else if(action==='voice'&&typeof editorVoiceOpen==='function')editorVoiceOpen();
    else if(action==='audio'&&typeof editorAudioBusOpen==='function')editorAudioBusOpen();
    else if(action==='subtitle'&&typeof editorSubtitleStyleOpen==='function')editorSubtitleStyleOpen();
    else if(action==='timeline')scrollTo('editor-timeline');
    else if(action==='preview')scrollTo('ed-preview-stage');
    else if(action==='review'&&typeof reviewModeToggle==='function')reviewModeToggle();
  }

  function installDirectorControls(){
    const box=document.getElementById('director-control-grid');
    if(!box || box.dataset.html) return;
    box.dataset.html='1';
    box.innerHTML='<div class="edx-control-head"><div><b>AI 导演控制</b><span class="hint">节点是入口，真正编辑都落到同一个工作台</span></div><span class="edx-selected">当前：<span data-edx-selected-scene>未选择</span></span></div>'+
      '<div class="edx-control-grid">'+
      '<button onclick="edxDirectorEdit(\'script\')"><b>文案</b><span>修改当前文案</span></button>'+
      '<button onclick="edxDirectorEdit(\'scene\')"><b>分镜</b><span>缩略图 / 顺序 / 时长</span></button>'+
      '<button onclick="edxDirectorEdit(\'visual\')"><b>画面</b><span>换素材 / 查看匹配</span></button>'+
      '<button onclick="edxMaterialMatch()"><b>智能匹配</b><span>按文案匹配我的素材</span></button>'+
      '<button onclick="edxDirectorEdit(\'voice\')"><b>配音</b><span>音色 / 试听 / 上传</span></button>'+
      '<button onclick="edxDirectorEdit(\'audio\')"><b>声音</b><span>原声 / BGM / 混音</span></button>'+
      '<button onclick="edxDirectorEdit(\'subtitle\')"><b>字幕</b><span>样式 / 位置 / 对齐</span></button>'+
      '<button onclick="edxProposeTransitions()"><b>转场</b><span>先建议，逐处确认</span></button>'+
      '<button onclick="edxDirectorEdit(\'timeline\')"><b>时间线</b><span>拖动 / 裁剪 / 拆分</span></button>'+
      '<button onclick="edxDirectorEdit(\'preview\')"><b>预览</b><span>实时编辑预览</span></button>'+
      '<button onclick="edxDirectorEdit(\'review\')"><b>预检</b><span>检查并定位问题</span></button>'+
      '</div>'+
      '<div id="director-material-match" class="edx-inline-panel"></div>'+
      '<div id="director-transition-proposal" class="edx-inline-panel"></div>';
  }

  function installUi(){
    installDirectorControls();
    patchPreview();
    if(typeof editorRenderTimeline==='function'&&!window.__edxTimelinePatched){
      const old=editorRenderTimeline;
      window.__edxTimelinePatched=true;
      window.editorRenderTimeline=function(){
        old.apply(this,arguments);
        requestAnimationFrame(decorateTimeline);
      };
    }
    if(typeof refreshWorkspace==='function'&&!window.__edxWorkspacePatched){
      const old=refreshWorkspace;
      window.__edxWorkspacePatched=true;
      window.refreshWorkspace=async function(){
        const r=old.apply(this,arguments);
        if(r&&typeof r.then==='function') await r;
        await enhancedRefreshPreview();
        return r;
      };
    }
    if(typeof selectSceneUx==='function'){}
    enhancedRefreshPreview();
  }

  const boot=()=>{
    try{ installUi(); }catch(e){ console.error('[editor-enhanced]',e); }
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();

  Object.assign(window,{
    edxSelectScene:selectSceneUx,
    edxRenderDraft:renderDraft,
    edxMaterialMatch:materialMatch,
    edxApplyMaterialMatch:applyMaterialMatch,
    edxProposeTransitions:proposeTransitions,
    edxApproveTransition:approveTransition,
    edxRejectTransition:rejectTransition,
    edxDirectorEdit:directorEdit,
    edxRefreshPreview:enhancedRefreshPreview,
  });
})();