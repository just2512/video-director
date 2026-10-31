"""Chromium regressions for project-bound preview, review and render responses."""
from __future__ import annotations

import importlib
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright


def test_late_preview_review_and_render_responses_do_not_replace_active_project(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    from video_director import config
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)

    import webui.server as web_server
    web_server = importlib.reload(web_server)
    monkeypatch.setattr(web_server, "COMPOSER_ROOT", storage)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                response = page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
                assert response and response.status == 200
                page.wait_for_function(
                    "() => typeof window.previewLoadDraft === 'function' && "
                    "typeof window.reviewModeScan === 'function' && "
                    "typeof window.editorPreflight === 'function' && "
                    "typeof window.editorDraftRender === 'function' && "
                    "typeof window.executeAction === 'function'"
                )
                result = page.evaluate("""async () => {
                  const nativeFetch = window.fetch.bind(window);
                  const nativeSetTimeout = window.setTimeout.bind(window);
                  const holds = {}, counts = {};
                  const json = body => new Response(JSON.stringify(body), {
                    status:200,headers:{'Content-Type':'application/json'}
                  });
                  const tick = () => new Promise(resolve => queueMicrotask(resolve));
                  const selector = document.getElementById('editor-project') || document.createElement('select');
                  selector.id='editor-project';if(!selector.isConnected)document.body.appendChild(selector);
                  const setProject=(pid,version)=>{ED.pid=pid;ED.version=version;selector.value=pid;};
                  const ensure=id=>{let el=document.getElementById(id);if(!el){el=document.createElement('div');el.id=id;document.body.appendChild(el);}return el;};
                  ensure('review-mode-list');ensure('review-mode-summary');ensure('review-mode-fix');
                  const stage=ensure('ed-preview-stage'),preflight=ensure('editor-preflight-box');
                  window.fetch=(input,init={})=>{
                    const url=new URL(typeof input==='string'?input:input.url,location.href);
                    const method=String(init.method||'GET').toUpperCase();
                    if(url.pathname.endsWith('/draft_info')){
                      const pid=url.pathname.split('/').slice(-2)[0];
                      counts['draft:'+pid]=(counts['draft:'+pid]||0)+1;
                      if(pid==='preview-a')return new Promise(resolve=>{holds.previewA=resolve;});
                      if(pid==='preview-b')return Promise.resolve(json({draft_path:'/tmp/B.mp4',final_path:'/tmp/B-final.mp4'}));
                    }
                    if(url.pathname==='/api/continuity'){
                      const pid=url.searchParams.get('project_id');
                      if(pid==='review-a')return new Promise(resolve=>{holds.reviewA=resolve;});
                      if(pid==='review-b')return Promise.resolve(json({issues:[{issue_code:'NO_CONTINUITY_DECLARED',message:'B project issue',scene_id:'scene-b'}]}));
                    }
                    if(url.pathname==='/api/preflight'&&method==='GET'){
                      const pid=url.searchParams.get('project_id');counts['preflight:'+pid]=(counts['preflight:'+pid]||0)+1;
                      if(pid==='review-b')return Promise.resolve(json({status:'PASS',issues:[]}));
                      if(pid==='preflight-a')return new Promise(resolve=>{holds.preflightA=resolve;});
                    }
                    if(url.pathname==='/api/draft'&&method==='POST')return new Promise(resolve=>{holds.draftA=resolve;});
                    if(url.pathname==='/api/workflows'&&method==='POST')return new Promise(resolve=>{holds.workflowA=resolve;});
                    if(url.pathname==='/api/workflows/run-a'&&method==='GET')return Promise.resolve(json({status:'COMPLETED'}));
                    return nativeFetch(input,init);
                  };
                  const output={};
                  try{
                    setProject('preview-a',1);
                    const previewA=window.previewLoadDraft('preview-a',1);await tick();
                    setProject('preview-b',2);
                    const previewB=await window.previewLoadDraft('preview-b',2);
                    holds.previewA(json({draft_path:'/tmp/A.mp4',final_path:'/tmp/A-final.mp4'}));
                    output.preview={b:previewB,a:await previewA,draftPath:PV.draftPath};

                    setProject('review-a',3);
                    const reviewA=window.reviewModeScan();await tick();
                    setProject('review-b',4);
                    await window.reviewModeScan();
                    const bReview={project:RV.projectId,codes:RV.issues.map(i=>i.code)};
                    holds.reviewA(json({issues:[{issue_code:'ANCHOR_JUMP',message:'late A issue',scene_id:'scene-a'}]}));
                    await reviewA;
                    output.review={bReview,afterLate:{project:RV.projectId,codes:RV.issues.map(i=>i.code)},
                      summary:document.getElementById('review-mode-summary').textContent};

                    setProject('preflight-a',5);preflight.innerHTML='B project must remain';
                    const pfA=window.editorPreflight();await tick();setProject('project-b',6);
                    holds.preflightA(json({status:'PASS',issues:[]}));
                    output.preflight={result:await pfA,html:preflight.innerHTML};

                    setProject('draft-a',7);stage.innerHTML='B preview marker';preflight.innerHTML='B delivery marker';
                    const draftA=window.editorDraftRender();await tick();setProject('project-b',8);
                    holds.draftA(json({ok:true,draft_path:'/tmp/A-draft.mp4'}));
                    output.draft={result:await draftA,stage:stage.innerHTML,preflight:preflight.innerHTML,pv:PV.draftPath};

                    setProject('workflow-a',9);stage.innerHTML='B workflow preview marker';preflight.innerHTML='B workflow delivery marker';
                    window.editorToast=()=>{};window.setTimeout=(fn)=>{queueMicrotask(fn);return 1;};
                    const workflowA=window.executeAction('render.draft',{});
                    await tick();if(!holds.workflowA)throw new Error('workflow creation request was not held');
                    setProject('project-b',10);holds.workflowA(json({ok:true,run_id:'run-a'}));
                    output.workflowResult=await workflowA;await tick();await tick();await tick();
                    output.workflow={stage:stage.innerHTML,preflight:preflight.innerHTML,pv:PV.draftPath,
                      draftInfoReads:counts['draft:workflow-a']||0};
                    return output;
                  }finally{window.fetch=nativeFetch;window.setTimeout=nativeSetTimeout;}
                }""")
                assert result["preview"]["b"]["draft_path"] == "/tmp/B.mp4", result
                assert result["preview"]["draftPath"] == "/tmp/B.mp4", result
                assert result["preview"]["a"]["status"] == "PROJECT_CHANGED", result
                assert result["review"]["bReview"] == {"project":"review-b","codes":["NO_CONTINUITY_DECLARED"]}, result
                assert result["review"]["afterLate"] == result["review"]["bReview"], result
                assert "1 个问题" in result["review"]["summary"], result
                assert result["preflight"]["result"]["status"] == "PROJECT_CHANGED", result
                assert result["preflight"]["html"] == "B project must remain", result
                assert result["draft"]["result"]["status"] == "PROJECT_CHANGED", result
                assert result["draft"]["stage"] == "B preview marker", result
                assert result["draft"]["preflight"] == "B delivery marker", result
                assert result["workflowResult"]["status"] == "PROJECT_CHANGED", result
                assert result["workflow"]["stage"] == "B workflow preview marker", result
                assert result["workflow"]["preflight"] == "B workflow delivery marker", result
                assert result["workflow"]["draftInfoReads"] == 0, result
                assert not errors, errors
            finally:browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
