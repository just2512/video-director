"""Chromium regressions for user-visible WorkflowRun cancellation paths."""
from __future__ import annotations

import importlib
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright


def test_create_stop_and_edit_waits_for_server_terminal_state(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))

    import webui.server as web_server
    web_server = importlib.reload(web_server)
    monkeypatch.setattr(web_server, "COMPOSER_ROOT", storage)

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            try:
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="networkidle",
                )
                assert response and response.status == 200
                page.evaluate("openCreate()")
                page.locator("#cf-form").wait_for(state="visible")
                page.locator("#cf-director-brief").fill("保留这段创作要求")
                page.locator("#cf-script").fill("保留这段权威文案")

                page.evaluate("""() => {
                  window.__cancelApiCalls = [];
                  window.__workflowStatus = "RUNNING";
                  window.__taskListStatus = "RUNNING";
                  window.__cancelApiResult = {ok:true, note:"已请求停止"};
                  window.apiPost = async (path, payload) => {
                    window.__cancelApiCalls.push({path, payload});
                    return window.__cancelApiResult;
                  };
                  window.apiGet = async (path) => {
                    if (path === "/api/workflows/run-stop-and-edit") {
                      return {run_id:"run-stop-and-edit",status:window.__workflowStatus,
                        steps:[{id:"script",status:"DONE"}],labels:{script:"理解文案"},result:{}};
                    }
                    if (path === "/api/workflows/run-noncancellable") {
                      return {run_id:"run-noncancellable",status:"RUNNING",steps:[],labels:{},result:{}};
                    }
                    if (path === "/api/workflows?limit=50") return {runs:[{
                      run_id:"run-background-stop",kind:"director_create",project_id:"project-background",
                      status:window.__taskListStatus,current:"scene_plan",updated_at:Date.now()/1000,steps:[]
                    }]};
                    return {ok:true};
                  };
                }""")
                page.evaluate("cfEnterProgressMode('run-stop-and-edit')")
                page.locator("#cf-progress").wait_for(state="visible")
                page.evaluate("cfCancelAndEdit()")

                page.wait_for_function("""() => {
                  const form=document.getElementById('cf-form');
                  const submit=document.getElementById('cf-go');
                  return form && form.style.display !== 'none' && submit && submit.disabled;
                }""", timeout=10000)
                accepted = page.evaluate("window.__cancelApiCalls")
                assert accepted == [{
                    "path": "/api/workflows/cancel",
                    "payload": {"run_id": "run-stop-and-edit"},
                }]
                assert page.locator("#cf-progress").count() == 0
                assert page.locator("#cf-director-brief").input_value() == "保留这段创作要求"
                assert page.locator("#cf-script").input_value() == "保留这段权威文案"
                assert "等待服务端确认" in (page.locator("#cf-status").text_content() or "")

                # Form editing is available, but a second submission is blocked
                # until the backend confirms that the previous run is terminal.
                page.evaluate("window.__workflowStatus = 'CANCELLED'")
                page.wait_for_function("""() => {
                  const submit=document.getElementById('cf-go');
                  return submit && !submit.disabled &&
                    document.getElementById('cf-progress') === null &&
                    (document.getElementById('cf-status')?.textContent || '').includes('任务已确认停止');
                }""", timeout=8000)
                assert page.evaluate("window._cfActiveRunId") == ""
                assert page.locator("#cf-director-brief").input_value() == "保留这段创作要求"
                assert page.locator("#cf-script").input_value() == "保留这段权威文案"

                # A rejected cancel request must leave progress visible and must
                # not restore an editable form or claim that the task has stopped.
                page.evaluate("""() => {
                  window.__workflowStatus = "RUNNING";
                  window.__cancelApiResult = {ok:false,error:"当前任务类型暂不支持中断，任务仍在运行"};
                }""")
                page.evaluate("cfEnterProgressMode('run-noncancellable')")
                page.locator("#cf-progress").wait_for(state="visible")
                page.evaluate("cfCancelAndEdit()")
                page.wait_for_function("""() =>
                  document.getElementById('cf-progress') &&
                  document.querySelector('#cf-progress button[onclick="cfCancelAndEdit()"]')?.disabled === false
                """, timeout=10000)
                assert page.locator("#cf-form").evaluate("(el) => el.style.display") == "none"
                assert page.locator("#cf-progress").is_visible()
                assert "已取消创作。" not in (page.locator("#cf-status").text_content() or "")

                # A task dismissed to background remains stoppable from the task
                # drawer, and the button remains disabled after acceptance until
                # the refreshed run list reports a terminal state.
                page.evaluate("""() => {
                  window.__cancelApiResult = {ok:true,note:"已请求停止，等待终态"};
                  const drawer=document.getElementById("task-drawer");
                  drawer.style.display="";
                  window.WFUI.drawerOpen=true;
                  window.WFUI.runs=[{
                    run_id:"run-background-stop",kind:"director_create",
                    project_id:"project-background",status:"RUNNING",current:"scene_plan",
                    updated_at:Date.now()/1000,steps:[]
                  }];
                  renderTaskDrawer();
                }""")
                stop_button = page.locator('#task-drawer-list button[onclick^="taskCancelRun("]')
                stop_button.wait_for(state="visible")
                stop_button.click()
                page.wait_for_function("""() =>
                  window.__cancelApiCalls.some(call =>
                    call.path === "/api/workflows/cancel" &&
                    call.payload.run_id === "run-background-stop")
                """, timeout=10000)
                page.wait_for_function("""() => {
                  const button=document.querySelector('#task-drawer-list button[onclick^="taskCancelRun("]');
                  return button && button.disabled && button.textContent.includes("正在请求");
                }""", timeout=10000)
                page.evaluate("window.__taskListStatus='CANCELLED'; taskRefresh()")
                page.wait_for_function("""() =>
                  !document.querySelector('#task-drawer-list button[onclick^="taskCancelRun("]') &&
                  (document.getElementById('task-drawer-list')?.textContent || '').includes('已停止') &&
                  (document.getElementById('task-drawer-list')?.textContent || '').includes('project-background')
                """, timeout=10000)
                cancelled_row = page.locator("#task-drawer-list .proj-row").last
                assert cancelled_row.locator('input[type="checkbox"]').is_visible()
                assert cancelled_row.locator('button[onclick^="taskShowDetail("]').is_visible()
                assert not page_errors
            finally:
                page.evaluate("""() => {
                  if (typeof _cfProgressTimer !== "undefined" && _cfProgressTimer) {
                    clearInterval(_cfProgressTimer); _cfProgressTimer = null;
                  }
                  if (window.WFUI && window.WFUI.refreshing) {
                    clearInterval(window.WFUI.refreshing); window.WFUI.refreshing=null;
                  }
                  if (typeof closeCreate === "function") closeCreate();
                }""")
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
