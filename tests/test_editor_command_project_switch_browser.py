"""Real Chromium regressions for stale project-scoped editor responses."""
from __future__ import annotations

import importlib
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright


def test_late_editor_responses_never_mutate_the_new_project_workspace(tmp_path, monkeypatch):
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
                    "() => typeof window.editorCmd === 'function' && "
                    "typeof window.executeCommand === 'function' && "
                    "typeof window.executeAction === 'function' && "
                    "typeof window.refreshComposition === 'function'"
                )
                result = page.evaluate("""async () => {
                  const nativeFetch = window.fetch.bind(window);
                  const nativeRefresh = window.refreshWorkspace;
                  const nativeToast = window.editorToast;
                  let refreshCalls = 0, toastCalls = 0, projectBCompositionReads = 0;
                  const holds = {};
                  const json = body => new Response(JSON.stringify(body), {
                    status: 200, headers: {'Content-Type':'application/json'}
                  });
                  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
                  window.refreshWorkspace = () => { refreshCalls += 1; };
                  window.editorToast = () => { toastCalls += 1; };
                  window.fetch = (input, init = {}) => {
                    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
                    const method = String(init.method || 'GET').toUpperCase();
                    if (url.pathname === '/api/editor/command' && method === 'POST') {
                      const body = JSON.parse(init.body || '{}');
                      if (body.command === 'delayed-success') return new Promise(resolve => { holds.commandSuccess = resolve; });
                      if (body.command === 'delayed-conflict') return new Promise(resolve => { holds.commandConflict = resolve; });
                      if (body.command === 'immediate-conflict-for-resync') {
                        return Promise.resolve(json({ok:false,status:'CONFLICT',errors:[{code:'CONFLICT'}]}));
                      }
                    }
                    if (url.pathname === '/api/editor/composition' && method === 'GET') {
                      const pid = url.searchParams.get('project_id');
                      if (pid === 'project-b') projectBCompositionReads += 1;
                      if (pid === 'resync-a') return new Promise(resolve => { holds.resyncComposition = resolve; });
                      if (pid === 'refresh-a') return new Promise(resolve => { holds.refreshComposition = resolve; });
                    }
                    if (url.pathname === '/api/action/execute' && method === 'POST') {
                      return new Promise(resolve => { holds.actionSuccess = resolve; });
                    }
                    return nativeFetch(input, init);
                  };
                  const setProject = (pid, version, marker) => { ED.pid=pid; ED.version=version; ED.comp={marker}; };
                  const state = () => ({pid:ED.pid,version:ED.version,marker:ED.comp && ED.comp.marker});
                  const snapshots = {};
                  try {
                    setProject('command-a',3,'command-a-before');
                    const commandPending = window.editorCmd('delayed-success',{},'clip-a',true);
                    await tick();
                    if (!holds.commandSuccess) throw new Error('success request was not held');
                    setProject('project-b',10,'project-b-state');
                    holds.commandSuccess(json({ok:true,composition_version:4,composition:{marker:'command-a-response'}}));
                    snapshots.command={result:await commandPending,state:state()};

                    setProject('conflict-a',6,'before-conflict');
                    const conflictPending=window.executeCommand({command:'delayed-conflict',targetId:'clip-a',payload:{}});
                    await tick();
                    if(!holds.commandConflict)throw new Error('conflict request was not held');
                    setProject('project-b',11,'project-b-after-conflict-switch');
                    holds.commandConflict(json({ok:false,status:'CONFLICT',errors:[{code:'CONFLICT'}]}));
                    snapshots.conflict={result:await conflictPending,state:state(),projectBReads:projectBCompositionReads};

                    setProject('resync-a',12,'resync-a-before');
                    snapshots.resyncConflict=await window.executeCommand({command:'immediate-conflict-for-resync',targetId:'clip-a',payload:{}});
                    await tick();
                    if(!holds.resyncComposition)throw new Error('conflict resync GET was not held');
                    setProject('project-b',22,'project-b-after-resync-switch');
                    holds.resyncComposition(json({ok:true,scenes:[],version:13}));
                    await tick();
                    snapshots.resyncState=state();

                    setProject('refresh-a',20,'refresh-a-before');
                    const refreshPending=window.refreshComposition();
                    await tick();
                    if(!holds.refreshComposition)throw new Error('composition refresh GET was not held');
                    setProject('project-b',23,'project-b-after-refresh-switch');
                    holds.refreshComposition(json({ok:true,scenes:[],version:21}));
                    snapshots.refreshResult=await refreshPending;
                    snapshots.refreshState=state();

                    setProject('action-a',30,'action-a-before');
                    const actionPending=window.executeAction('editor.test_action',{});
                    await tick();
                    if(!holds.actionSuccess)throw new Error('semantic action request was not held');
                    setProject('project-b',31,'project-b-after-action-switch');
                    holds.actionSuccess(json({ok:true,composition_version:31,result:{composition:{marker:'action-a-response'}},message:'A action applied'}));
                    snapshots.actionResult=await actionPending;
                    snapshots.actionState=state();
                    snapshots.refreshCalls=refreshCalls;
                    snapshots.toastCalls=toastCalls;
                    return snapshots;
                  } finally {
                    window.fetch=nativeFetch;
                    window.refreshWorkspace=nativeRefresh;
                    window.editorToast=nativeToast;
                  }
                }""")
                assert result["command"]["result"]["status"] == "PROJECT_CHANGED", result
                assert result["command"]["result"]["applied"] is True, result
                assert result["command"]["state"] == {"pid":"project-b","version":10,"marker":"project-b-state"}, result
                assert result["conflict"]["result"]["staleProjectResponse"] is True, result
                assert result["conflict"]["state"] == {"pid":"project-b","version":11,"marker":"project-b-after-conflict-switch"}, result
                assert result["conflict"]["projectBReads"] == 0, result
                assert result["resyncConflict"]["status"] == "CONFLICT", result
                assert result["resyncState"] == {"pid":"project-b","version":22,"marker":"project-b-after-resync-switch"}, result
                assert result["refreshResult"]["status"] == "PROJECT_CHANGED", result
                assert result["refreshState"] == {"pid":"project-b","version":23,"marker":"project-b-after-refresh-switch"}, result
                assert result["actionResult"]["staleProjectResponse"] is True, result
                assert result["actionState"] == {"pid":"project-b","version":31,"marker":"project-b-after-action-switch"}, result
                assert result["refreshCalls"] == 0, result
                assert result["toastCalls"] == 0, result
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_library_legacy_command_helper_does_not_shadow_shared_editor_command():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    commands = (root / "webui/static/js/commands.js").read_text(encoding="utf-8")
    library = (root / "webui/static/js/library.js").read_text(encoding="utf-8")

    assert "async function editorCmd(" in commands
    assert "  editorCmd," in commands
    assert "async function editorLegacyCmd(" in library
    assert "async function editorCmd(" not in library
    assert "editorLegacyCmd," in library
    assert "  editorCmd," not in library
