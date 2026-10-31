"""Real Chromium coverage for Film Studio's revision-bound AI proposal review."""
from __future__ import annotations

import importlib
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from video_director import film_document as fd


SAMPLE = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"><style>body{margin:0}</style></head>
<body>
<section id="video-main" data-label="主画面">
<video id="clip-a" src="../assets/a.mp4#t=2,12" at="0" speed="2" volume="1"></video>
<video id="clip-b" src="../assets/b.mp4#t=0,5" at="5"></video>
</section>
<section id="audio-bed" data-label="BGM">
<audio id="audio-1" src="../assets/music.wav#t=0,20" at="0" volume="0.2"></audio>
</section>
</body></html>'''


def test_ai_proposal_diff_is_readable_and_deleted_target_blocks_preview_apply(tmp_path, monkeypatch):
    """An Agent deleting the target clip invalidates its outstanding revision-bound proposal."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-ai-proposal-stale-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    before = fd.read_project_film(project_id)
    assert before["exists"]

    operations = [{"op": "move", "clip": "clip-b", "track_id": "video-main", "at": 6.0}]
    candidate_doc, normalized = fd.apply_ops(before["doc"], operations)
    candidate_text = fd.serialize_film(candidate_doc)
    candidate_state = {
        "exists": True,
        "project_id": project_id,
        "text": candidate_text,
        "rev": fd.revision_of(candidate_text),
        "doc": candidate_doc,
    }
    candidate = fd.film_payload(project_id, candidate_state)
    proposal = {
        "id": "browser-stale-proposal",
        "project_id": project_id,
        "base_revision": before["rev"],
        "operations": normalized,
        "summary": "把第二个片段后移一秒",
        "candidate": candidate,
        "affected_clip_ids": ["clip-b"],
    }

    command_requests: list[dict] = []
    apply_requests: list[dict] = []

    def handle_command(route):
        command_requests.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "ok": True,
                "proposal": proposal,
                "message": "已生成并验证候选修改；尚未写入正式影片文档。",
            }, ensure_ascii=False),
        )

    def handle_apply(route):
        apply_requests.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=409, content_type="application/json", body=json.dumps({
            "ok": False,
            "code": "REVISION_CONFLICT",
            "error": "proposal must not be submitted once stale",
        }, ensure_ascii=False))

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.route("**/api/editor/film/command", handle_command)
            page.route("**/api/editor/film/apply-proposal", handle_apply)
            try:
                page.set_default_navigation_timeout(10_000)
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="domcontentloaded",
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.filmStudioAsk === 'function'")
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function(
                    "() => { const select=document.querySelector('#editor-project'); return !!select && select.options.length>0; }"
                )
                page.evaluate("""pid => {
                  const select=document.querySelector('#editor-project');
                  let option=[...select.options].find(item=>item.value===pid);
                  if(!option){option=new Option(pid,pid);select.add(option);}
                  select.value=pid;
                  select.dispatchEvent(new Event('change',{bubbles:true}));
                }""", project_id)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]').wait_for(state="visible")

                page.evaluate("window.filmStudioSetSideMode('ai')")
                page.locator("#fs-chat-input").wait_for(state="visible")
                page.locator("#fs-chat-input").fill("把第二个片段后移一秒")
                page.locator('[data-film-action="ask"]:visible').click()
                page.locator('#fs-proposal-panel[data-react-ai-proposal="true"]').wait_for(state="visible", timeout=10_000)
                assert len(command_requests) == 1, command_requests
                assert command_requests[0]["base"] == before["rev"], command_requests
                assert command_requests[0]["project_id"] == project_id, command_requests

                details = page.locator('[data-react-proposal-details="true"]')
                details.locator("summary").click()
                rendered_diff = details.inner_text()
                assert "clip-b" in rendered_diff, rendered_diff
                assert "修改前：" in rendered_diff, rendered_diff
                assert "修改后：" in rendered_diff, rendered_diff
                assert "00:05" in rendered_diff and "00:06" in rendered_diff, rendered_diff
                assert fd.read_project_film(project_id)["rev"] == before["rev"], "proposal generation must not write FilmDocument"
                assert apply_requests == [], apply_requests

                # Simulate a concurrent Agent/CLI film.edit deleting the proposal target.
                # The production endpoint advances revision and broadcasts revision_changed.
                edit_response = page.evaluate("""async ({projectId,base}) => {
                  const response = await fetch('/api/editor/film/edit', {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({
                      project_id:projectId,
                      base,
                      ops:[{op:'remove',clip:'clip-b'}]
                    })
                  });
                  return {status:response.status,body:await response.json()};
                }""", {"projectId": project_id, "base": before["rev"]})
                assert edit_response["status"] == 200, edit_response
                changed_revision = edit_response["body"]["rev"]
                assert changed_revision and changed_revision != before["rev"], edit_response

                stale_warning = page.locator("#fs-proposal-stale-warning")
                stale_warning.wait_for(state="visible", timeout=10_000)
                warning_text = stale_warning.inner_text()
                assert before["rev"] in warning_text, warning_text
                assert changed_revision in warning_text, warning_text
                assert page.locator('[data-film-action="preview-proposal"]:visible').is_disabled()
                assert page.locator('[data-film-action="apply-proposal"]:visible').is_disabled()
                assert page.locator('[data-film-action="reject-proposal"]:visible').is_enabled()
                page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]').wait_for(state="detached", timeout=10_000)
                assert apply_requests == [], apply_requests

                # Rejected/stale proposals remain read-only; only the manual edit changes revision.
                final = fd.read_project_film(project_id)
                assert final["rev"] == changed_revision, final
                final_clip_ids = {
                    clip["id"] for track in final["doc"]["tracks"] for clip in track["clips"]
                }
                assert "clip-b" not in final_clip_ids, final_clip_ids
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_ai_proposal_timeline_preview_and_apply_are_one_undoable_transaction(tmp_path, monkeypatch):
    """Real Chromium verifies visual-only preview plus one apply/undo/redo transaction."""
    root=tmp_path/"storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT",str(root))
    import webui.server as web_server
    web_server=importlib.reload(web_server)
    web_server.COMPOSER_ROOT=root
    project_id="film-studio-ai-proposal-apply-browser"
    project_root=root/project_id
    project_root.mkdir(parents=True)
    film_path=project_root/fd.FILM_NAME
    film_path.write_text(
        """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"></head><body>
<section id="video-main" data-label="主画面">
<video id="clip-a" src="../assets/a.mp4#t=0,4" at="0"></video>
<video id="clip-b" src="../assets/b.mp4#t=0,4" at="4"></video>
<video id="clip-c" src="../assets/c.mp4#t=0,4" at="8"></video></section>
<section id="video-spare" data-label="备用空轨道"></section>
<section id="audio-bed" data-label="背景音乐">
<audio id="audio-1" src="../assets/music.wav#t=0,20" at="0" volume="0.2"></audio></section></body></html>""",
        encoding="utf-8")
    original=film_path.read_bytes()
    before=fd.read_project_film(project_id)
    ops=[
        {"op":"trim","clip":"clip-b","edge":"end","end":7.0},
        {"op":"move","clip":"clip-c","track_id":"video-main","at":7.0},
        {"op":"set","clip":"audio-1","field":"fade","value":"0,1"},
        {"op":"set","clip":"audio-1","field":"volume_envelope","value":[{"t":0,"v":1},{"t":20,"v":0}]},
        {"op":"remove","clip":"clip-a"},
        {"op":"split","clip":"clip-c","at":8.5,"new_id":"clip-c-right"},
        {"op":"track_add","track_id":"track-ai-added","label":"AI 提案新轨道","kind":"video","index":2},
        {"op":"move","clip":"clip-b","track_id":"track-ai-added","at":4.0},
        {"op":"track_remove","track_id":"video-spare"},
    ]
    class FakeModel:
        def generate(self,_prompt):
            return json.dumps({"summary":"缩短第二镜头、闭合空隙并让背景音乐结尾渐弱","operations":ops},ensure_ascii=False)
    monkeypatch.setattr(web_server.vd_llm,"get_llm",lambda:FakeModel())
    server=ThreadingHTTPServer(("127.0.0.1",0),web_server.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True)
            context=browser.new_context(viewport={"width":1440,"height":1000})
            page=context.new_page()
            errors=[];applies=[]
            page.on("pageerror",lambda error:errors.append(str(error)))
            page.on("request",lambda request: applies.append(json.loads(request.post_data or "{}"))
                    if request.method=="POST" and request.url.endswith("/api/editor/film/apply-proposal") else None)
            try:
                response=page.goto(f"http://127.0.0.1:{server.server_port}/",wait_until="domcontentloaded")
                assert response and response.status==200
                page.wait_for_function("typeof window.filmStudioAsk==='function'")
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function("() => !!document.querySelector('#editor-project')?.options.length")
                page.evaluate("""pid=>{const s=document.querySelector('#editor-project');let o=[...s.options].find(x=>x.value===pid);
                  if(!o){o=new Option(pid,pid);s.add(o);}s.value=pid;s.dispatchEvent(new Event('change',{bubbles:true}));}""",project_id)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator(".fs-react-timeline-surface").wait_for(state="visible")
                env=page.locator('#fs-timeline canvas[data-envelope-clip="audio-1"]')
                env.wait_for(state="visible")
                initial_png=env.evaluate("(c)=>c.toDataURL()")
                initial_geom=page.evaluate("""()=>Object.fromEntries(['clip-b','clip-c','audio-1'].map(id=>{
                  const n=document.querySelector('#fs-timeline .fs-clip[data-clip="'+id+'"]');
                  return [id,{left:getComputedStyle(n).left,width:getComputedStyle(n).width}];}))""")
                assert page.evaluate("window.filmStudioStore.getSnapshot().revision")==before["rev"]

                page.evaluate("window.filmStudioSetSideMode('ai')")
                page.locator("#fs-chat-input").fill("缩短第二镜头一秒，闭合空隙，背景音乐结尾渐弱")
                page.locator('[data-film-action="ask"]:visible').click()
                page.locator('#fs-proposal-panel[data-react-ai-proposal="true"]').wait_for(state="visible",timeout=10000)
                page.wait_for_function("""()=>{const s=window.filmStudioStore?.getSnapshot(),p=s?.timelineGesturePreview,e=s?.timelineEnvelopePreview;
                  return !!p?.['clip-b']&&!!p?.['clip-c']&&p['audio-1']?.fadeOut===1&&
                    p['clip-a']?.removed===true&&p['__proposal_added__:clip-c-right']?.added===true&&
                    p['__proposal_track_added__:track-ai-added']?.previewTrack===true&&
                    p['__proposal_track_removed__:video-spare']?.removed===true&&
                    p['clip-b']?.targetTrackId==='track-ai-added'&&
                    e?.clipId==='audio-1'&&e.points.length===2;}""",timeout=10000)
                assert film_path.read_bytes()==original
                assert fd.read_project_film(project_id)["rev"]==before["rev"]
                state=page.evaluate("window.filmStudioStore.getSnapshot()")
                initial_scale=float(state["pixelsPerSecond"])
                assert abs(state["timelineGesturePreview"]["clip-b"]["width"]-3*initial_scale)<0.01
                assert abs(state["timelineGesturePreview"]["clip-c"]["left"]-7*initial_scale)<0.01
                assert state["timelineEnvelopePreview"]["points"]==[{"t":0,"v":1},{"t":20,"v":0}]

                # Zoom must keep the proposal anchored to timeline seconds, not stale pixel offsets.
                page.evaluate("window.filmStudioZoom(1, 500)")
                page.wait_for_function("scale => window.filmStudioStore.getSnapshot().pixelsPerSecond > scale",
                                       arg=initial_scale, timeout=5000)
                state=page.evaluate("window.filmStudioStore.getSnapshot()")
                scale=float(state["pixelsPerSecond"])
                assert abs(float(page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]').evaluate(
                    "(n)=>parseFloat(getComputedStyle(n).width)"))-3*scale)<0.01
                assert abs(float(page.locator('#fs-timeline .fs-clip[data-clip="clip-c"]').evaluate(
                    "(n)=>parseFloat(getComputedStyle(n).left)"))-7*scale)<0.01
                removed=page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]')
                assert removed.get_attribute("data-proposal-removed")=="true"
                assert removed.locator('[data-proposal-remove-label="clip-a"]').inner_text()=="候选删除"
                added=page.locator('#fs-timeline [data-proposal-added="clip-c-right"]')
                assert added.is_visible()
                assert abs(float(added.evaluate("(n)=>parseFloat(getComputedStyle(n).left)"))-8.5*scale)<0.01
                assert abs(float(added.evaluate("(n)=>parseFloat(getComputedStyle(n).width)"))-2.5*scale)<0.01
                added_track=page.locator('#fs-timeline [data-proposal-track-added="track-ai-added"]')
                assert added_track.is_visible()
                assert "AI 提案新轨道" in added_track.inner_text()
                visual_track_order=page.locator('#fs-timeline .fs-track[data-track]').evaluate_all("(nodes)=>nodes.map(node=>node.getAttribute('data-track'))")
                assert visual_track_order[:3]==["video-main","track-ai-added","audio-bed"],visual_track_order
                assert visual_track_order[-1]=="video-spare",visual_track_order
                moved_ghost=added_track.locator('[data-gesture-ghost-for="clip-b"]')
                assert moved_ghost.is_visible()
                assert abs(float(moved_ghost.evaluate("(n)=>parseFloat(getComputedStyle(n).left)"))-4*scale)<0.01
                assert abs(float(moved_ghost.evaluate("(n)=>parseFloat(getComputedStyle(n).width)"))-3*scale)<0.01
                removed_track=page.locator('#fs-timeline [data-proposal-track-removed="video-spare"]')
                assert removed_track.is_visible()
                assert "候选删除" in removed_track.inner_text()
                clip=page.locator('#fs-timeline .fs-clip[data-clip="audio-1"]')
                assert clip.get_attribute("data-preview-fade-out")=="1"
                assert clip.locator('[data-fade-preview="out"]').is_visible()
                assert env.get_attribute("data-react-envelope-source")=="preview"
                assert env.evaluate("(c)=>c.toDataURL()")!=initial_png
                b=page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]')
                c=page.locator('#fs-timeline .fs-clip[data-clip="clip-c"]')
                assert abs(float(b.evaluate("(n)=>getComputedStyle(n).width.replace('px','')"))-3*scale)<0.01
                assert abs(float(c.evaluate("(n)=>getComputedStyle(n).left.replace('px','')"))-7*scale)<0.01
                assert applies==[]

                page.locator('[data-film-action="apply-proposal"]:visible').click()
                page.wait_for_function("""r=>{const s=window.filmStudioStore?.getSnapshot();return !!s&&!s.busy&&s.pendingTransaction===null&&s.revision!==r&&s.timelineGesturePreview===null&&s.timelineEnvelopePreview===null;}""",
                                       arg=before["rev"],timeout=10000)
                after=fd.read_project_film(project_id);after_bytes=film_path.read_bytes()
                assert len(applies)==1 and applies[0]["base_revision"]==before["rev"] and "ops" not in applies[0]
                clips={x["id"]:x for t in after["doc"]["tracks"] for x in t["clips"]}
                assert "clip-a" not in clips
                assert "clip-c-right" in clips
                assert not any(track["id"]=="video-spare" for track in after["doc"]["tracks"])
                added_track_state=next(track for track in after["doc"]["tracks"] if track["id"]=="track-ai-added")
                assert any(clip["id"]=="clip-b" for clip in added_track_state["clips"])
                assert clips["clip-b"]["attrs"]["at"] == "4"
                assert abs(float(clips["clip-c"]["attrs"]["at"])-7)<0.001
                assert abs(float(clips["clip-c-right"]["attrs"]["at"])-8.5)<0.001
                assert abs(fd.clip_duration(clips["clip-c"])-1.5)<0.001
                assert abs(fd.clip_duration(clips["clip-c-right"])-2.5)<0.001
                assert clips["audio-1"]["attrs"]["fade"]=="0,1"
                assert json.loads(clips["audio-1"]["attrs"]["volume_envelope"])==[{"t":0.0,"v":1.0},{"t":20.0,"v":0.0}]
                assert not errors,errors

                page.evaluate("window.filmStudioUndo()")
                page.wait_for_function("r=>{const s=window.filmStudioStore?.getSnapshot();return !!s&&!s.busy&&s.pendingTransaction===null&&s.revision===r;}",arg=before["rev"],timeout=10000)
                assert film_path.read_bytes()==original
                assert page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').is_visible()
                assert page.locator('#fs-timeline .fs-clip[data-clip="clip-c-right"]').count()==0
                undo_geometry=page.evaluate("""()=>Object.fromEntries(['clip-b','clip-c','audio-1'].map(id=>{
                  const n=document.querySelector('#fs-timeline .fs-clip[data-clip="'+id+'"]');
                  return [id,{left:parseFloat(getComputedStyle(n).left),width:parseFloat(getComputedStyle(n).width)}];}))""")
                # Undo restores source timing/duration at the new zoom scale, not the old pre-zoom pixels.
                assert abs(undo_geometry["clip-b"]["left"]-4*scale)<0.01,undo_geometry
                assert abs(undo_geometry["clip-b"]["width"]-4*scale)<0.01,undo_geometry
                assert abs(undo_geometry["clip-c"]["left"]-8*scale)<0.01,undo_geometry
                assert abs(undo_geometry["clip-c"]["width"]-4*scale)<0.01,undo_geometry
                assert abs(undo_geometry["audio-1"]["left"])<0.01,undo_geometry
                assert abs(undo_geometry["audio-1"]["width"]-20*scale)<0.01,undo_geometry
                page.evaluate("window.filmStudioRedo()")
                page.wait_for_function("r=>{const s=window.filmStudioStore?.getSnapshot();return !!s&&!s.busy&&s.pendingTransaction===null&&s.revision===r;}",arg=after["rev"],timeout=10000)
                assert film_path.read_bytes()==after_bytes
                assert page.locator('#fs-timeline .fs-clip[data-clip="clip-a"]').count()==0
                assert page.locator('#fs-timeline .fs-clip[data-clip="clip-c-right"]').is_visible()
                redo_geometry=page.evaluate("""()=>Object.fromEntries(['clip-b','clip-c','audio-1'].map(id=>{
                  const n=document.querySelector('#fs-timeline .fs-clip[data-clip="'+id+'"]');
                  return [id,{left:parseFloat(getComputedStyle(n).left),width:parseFloat(getComputedStyle(n).width)}];}))""")
                assert abs(redo_geometry["clip-b"]["left"]-4*scale)<0.01,redo_geometry
                assert abs(redo_geometry["clip-b"]["width"]-3*scale)<0.01,redo_geometry
                assert abs(redo_geometry["clip-c"]["left"]-7*scale)<0.01,redo_geometry
                assert abs(redo_geometry["audio-1"]["left"])<0.01,redo_geometry
                assert abs(redo_geometry["audio-1"]["width"]-20*scale)<0.01,redo_geometry
                assert not errors,errors
            finally:
                context.close();browser.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_ai_director_renders_streamed_tokens_before_final_proposal_without_writing_film(
    tmp_path, monkeypatch
):
    """Real Chromium verifies partial SSE text reaches React before the final proposal."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-ai-live-token-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    original_bytes = film_path.read_bytes()
    before = fd.read_project_film(project_id)
    assert before["exists"]

    operations = [
        {"op": "move", "clip": "clip-b", "track_id": "video-main", "at": 6.0}
    ]
    candidate_doc, normalized = fd.apply_ops(before["doc"], operations)
    candidate_text = fd.serialize_film(candidate_doc)
    candidate_state = {
        "exists": True,
        "project_id": project_id,
        "text": candidate_text,
        "rev": fd.revision_of(candidate_text),
        "doc": candidate_doc,
    }
    candidate = fd.film_payload(project_id, candidate_state)
    proposal = {
        "id": "browser-live-stream-proposal",
        "project_id": project_id,
        "base_revision": before["rev"],
        "operations": normalized,
        "summary": "这是一条实时生成的提案摘要",
        "candidate": candidate,
        "affected_clip_ids": ["clip-b"],
    }

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page_errors: list[str] = []
            apply_requests: list[dict] = []

            page.add_init_script(
                """(() => {
                  const realFetch = window.fetch.bind(window);
                  window.__filmAiStreamPush = null;
                  window.__filmAiStreamRequest = null;
                  window.fetch = async (input, init = {}) => {
                    const rawUrl = typeof input === "string" ? input : input.url;
                    const pathname = new URL(rawUrl, window.location.href).pathname;
                    if (pathname !== "/api/editor/film/command") {
                      return realFetch(input, init);
                    }
                    window.__filmAiStreamRequest = JSON.parse(init.body || "{}");
                    const encoder = new TextEncoder();
                    const stream = new ReadableStream({
                      start(controller) {
                        window.__filmAiStreamPush = (eventName, data) => {
                          controller.enqueue(encoder.encode(
                            "event: " + eventName + "\\r\\ndata: " +
                            JSON.stringify(data) + "\\r\\n\\r\\n"
                          ));
                        };
                        // Intentionally withhold the final result. The test must
                        // observe this partial summary in the live React chat first.
                        window.__filmAiStreamPush("token", {
                          fragment: '{"summary":"这是一条实时生成的提案摘要"'
                        });
                      }
                    });
                    return new Response(stream, {
                      status: 200,
                      headers: {"Content-Type": "text/event-stream; charset=utf-8"}
                    });
                  };
                })();"""
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on(
                "request",
                lambda request: apply_requests.append(
                    json.loads(request.post_data or "{}")
                )
                if request.method == "POST"
                and request.url.endswith("/api/editor/film/apply-proposal")
                else None,
            )
            try:
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="domcontentloaded",
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.filmStudioAsk === 'function'")
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function(
                    "() => !!document.querySelector('#editor-project')?.options.length"
                )
                page.evaluate(
                    """pid => {
                      const select = document.querySelector('#editor-project');
                      let option = [...select.options].find(item => item.value === pid);
                      if (!option) { option = new Option(pid, pid); select.add(option); }
                      select.value = pid;
                      select.dispatchEvent(new Event('change', {bubbles:true}));
                    }""",
                    project_id,
                )
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.wait_for_function(
                    "() => !!document.querySelector('.fs-react-timeline-surface')",
                    timeout=10000,
                )
                page.evaluate("window.filmStudioSetSideMode('ai')")
                page.locator("#fs-chat-input").wait_for(state="visible")
                page.locator("#fs-chat-input").fill("把第二个片段后移一秒")
                page.locator('[data-film-action="ask"]:visible').click()

                # No final/result event has been delivered yet. This proves the
                # visible React chat updates from an actual ReadableStream token.
                page.wait_for_function(
                    """() => document.querySelector('#fs-chat-log')?.innerText
                      .includes('这是一条实时生成的提案摘要 …')""",
                    timeout=5000,
                )
                request = page.evaluate("window.__filmAiStreamRequest")
                assert request["stream"] is True, request
                assert request["project_id"] == project_id, request
                assert request["base"] == before["rev"], request
                during = fd.read_project_film(project_id)
                assert during["rev"] == before["rev"]
                assert film_path.read_bytes() == original_bytes
                assert apply_requests == []

                # Deliver the remainder of the model text and then the validated
                # proposal event, keeping proposal generation separate from apply.
                page.evaluate(
                    """() => {
                      window.__filmAiStreamPush('token', {
                        fragment: ',"operations":[{"op":"move","clip":"clip-b","track_id":"video-main","at":6}]}'
                      });
                    }"""
                )
                page.evaluate(
                    """proposal => window.__filmAiStreamPush('result', {
                      ok: true,
                      proposal,
                      message: '已生成并验证候选修改；尚未写入正式影片文档。'
                    })""",
                    proposal,
                )
                page.locator(
                    '#fs-proposal-panel[data-react-ai-proposal="true"]'
                ).wait_for(state="visible", timeout=10000)
                assert page.locator('#fs-proposal-panel[data-react-ai-proposal="true"] #fs-proposal-summary').inner_text() == proposal["summary"]
                assert fd.read_project_film(project_id)["rev"] == before["rev"]
                assert film_path.read_bytes() == original_bytes
                assert apply_requests == []
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_ai_director_steering_cancels_upstream_and_replans_without_writing(tmp_path, monkeypatch):
    """A new instruction must cancel the active upstream model stream, then replan."""
    import time

    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root
    project_id = "film-studio-ai-steer-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    original_bytes = film_path.read_bytes()
    before = fd.read_project_film(project_id)
    assert before["exists"]

    first_stream_started = threading.Event()
    upstream_cancelled = threading.Event()
    upstream_closed = threading.Event()
    seen_prompts: list[str] = []
    prompt_lock = threading.Lock()

    class FakeUpstreamStream:
        def close(self):
            upstream_closed.set()

    class SteeringAwareLLM:
        supports_stream_cancellation = True

        def generate_stream(self, prompt, *, on_delta=None, on_stream_open=None, cancel_event=None):
            with prompt_lock:
                request_index = len(seen_prompts)
                seen_prompts.append(prompt)
            stream = FakeUpstreamStream()
            assert callable(on_stream_open)
            on_stream_open(stream)
            if request_index == 0:
                assert callable(on_delta)
                on_delta('{"summary":"第一轮生成中"')
                first_stream_started.set()
                if cancel_event is None or not cancel_event.wait(timeout=8):
                    return json.dumps({"summary": "首轮未收到取消", "operations": []}, ensure_ascii=False)
                upstream_cancelled.set()
                raise RuntimeError("upstream generation cancelled by steering request")

            output = json.dumps({
                "summary": "已按新方向生成提案",
                "operations": [
                    {"op": "move", "clip": "clip-b", "track_id": "video-main", "at": 6.0}
                ],
            }, ensure_ascii=False)
            # Stream the second response in multiple deltas before returning its JSON.
            for fragment in (output[:18], output[18:]):
                on_delta(fragment)
                time.sleep(0.03)
            return output

    monkeypatch.setattr(web_server.vd_llm, "get_llm", lambda: SteeringAwareLLM())
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page_errors: list[str] = []
            command_requests: list[dict] = []
            cancel_requests: list[dict] = []
            edit_requests: list[dict] = []
            apply_requests: list[dict] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            def capture_request(request):
                if request.method != "POST":
                    return
                try:
                    payload = json.loads(request.post_data or "{}")
                except json.JSONDecodeError:
                    payload = {"_invalid_json": True}
                if request.url.endswith("/api/editor/film/command"):
                    command_requests.append(payload)
                elif request.url.endswith("/api/editor/film/command/cancel"):
                    cancel_requests.append(payload)
                elif request.url.endswith("/api/editor/film/edit"):
                    edit_requests.append(payload)
                elif request.url.endswith("/api/editor/film/apply-proposal"):
                    apply_requests.append(payload)

            page.on("request", capture_request)
            try:
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="domcontentloaded",
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.filmStudioAsk === 'function'")
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function(
                    "() => !!document.querySelector('#editor-project')?.options.length"
                )
                page.evaluate(
                    """pid => {
                      const select = document.querySelector('#editor-project');
                      let option = [...select.options].find(item => item.value === pid);
                      if (!option) { option = new Option(pid, pid); select.add(option); }
                      select.value = pid;
                      select.dispatchEvent(new Event('change', {bubbles:true}));
                    }""",
                    project_id,
                )
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.wait_for_function(
                    "() => !!document.querySelector('.fs-react-timeline-surface')",
                    timeout=10000,
                )
                page.evaluate("window.filmStudioSetSideMode('ai')")
                page.locator("#fs-chat-input").wait_for(state="visible")
                page.locator("#fs-chat-input").fill("把第二个片段移到备用轨道")
                page.locator('[data-film-action="ask"]:visible').click()
                page.wait_for_function(
                    "() => document.querySelector('#fs-chat-log')?.innerText.includes('第一轮生成中 …')",
                    timeout=10000,
                )
                assert first_stream_started.wait(timeout=2)
                assert len(command_requests) == 1, command_requests
                first_request_id = command_requests[0]["request_id"]

                # Sending a new instruction while busy must not start a competing
                # model request: it steers by cancelling this stream, then replans.
                page.locator("#fs-chat-input").fill("改为仅将第二个片段后移一秒")
                page.locator('[data-film-action="ask"]:visible').click()
                page.locator(
                    '#fs-proposal-panel[data-react-ai-proposal="true"]'
                ).wait_for(state="visible", timeout=10000)

                assert upstream_cancelled.wait(timeout=3), "the model stream did not observe cancellation"
                assert upstream_closed.wait(timeout=3), "the upstream stream object was not closed"
                assert len(command_requests) == 2, command_requests
                assert command_requests[0]["request_id"] == first_request_id
                assert command_requests[1]["request_id"] != first_request_id
                assert "把第二个片段移到备用轨道" in command_requests[0]["message"]
                assert "原指令：把第二个片段移到备用轨道" in command_requests[1]["message"]
                assert "执行中转向/补充要求：改为仅将第二个片段后移一秒" in command_requests[1]["message"]
                assert any(item.get("request_id") == first_request_id for item in cancel_requests), cancel_requests
                assert page.locator("#fs-proposal-summary").count() >= 1
                assert "已按新方向生成提案" in page.locator(
                    '#fs-proposal-panel[data-react-ai-proposal="true"]'
                ).inner_text()
                assert fd.read_project_film(project_id)["rev"] == before["rev"]
                assert film_path.read_bytes() == original_bytes
                assert edit_requests == []
                assert apply_requests == []
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_ai_proposal_returned_after_external_revision_during_generation_is_never_previewed_or_applied(
    tmp_path, monkeypatch
):
    """A delayed AI response must not preview/apply against a revision changed while it was pending."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-ai-generation-stale-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    before = fd.read_project_film(project_id)
    assert before["exists"]

    operations = [{"op": "move", "clip": "clip-b", "track_id": "video-main", "at": 6.0}]
    candidate_doc, normalized = fd.apply_ops(before["doc"], operations)
    candidate_text = fd.serialize_film(candidate_doc)
    candidate_state = {
        "exists": True,
        "project_id": project_id,
        "text": candidate_text,
        "rev": fd.revision_of(candidate_text),
        "doc": candidate_doc,
    }
    proposal = {
        "id": "browser-delayed-stale-proposal",
        "project_id": project_id,
        "base_revision": before["rev"],
        "operations": normalized,
        "summary": "把第二个片段后移一秒",
        "candidate": fd.film_payload(project_id, candidate_state),
        "affected_clip_ids": ["clip-b"],
    }

    command_requests: list[dict] = []
    command_routes = []
    apply_requests: list[dict] = []

    def hold_command(route):
        command_requests.append(json.loads(route.request.post_data or "{}"))
        command_routes.append(route)
        # Keep the browser request pending until the concurrent edit has advanced
        # the authoritative revision. This is a deterministic API-level delay;
        # no live model credentials or mocked "real token stream" are involved.

    def handle_apply(route):
        apply_requests.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=409,
            content_type="application/json",
            body=json.dumps({
                "ok": False,
                "code": "REVISION_CONFLICT",
                "error": "stale proposal must not be submitted",
            }, ensure_ascii=False),
        )

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.route("**/api/editor/film/command", hold_command)
            page.route("**/api/editor/film/apply-proposal", handle_apply)
            try:
                page.set_default_navigation_timeout(10_000)
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="domcontentloaded",
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.filmStudioAsk === 'function'")
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function(
                    "() => { const select=document.querySelector('#editor-project'); "
                    "return !!select && select.options.length>0; }"
                )
                page.evaluate("""pid => {
                  const select=document.querySelector('#editor-project');
                  let option=[...select.options].find(item=>item.value===pid);
                  if(!option){option=new Option(pid,pid);select.add(option);}
                  select.value=pid;
                  select.dispatchEvent(new Event('change',{bubbles:true}));
                }""", project_id)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]').wait_for(state="visible")
                page.evaluate("window.filmStudioSetSideMode('ai')")
                chat_input = page.locator("#fs-chat-input")
                chat_input.wait_for(state="visible")
                chat_input.fill("把第二个片段后移一秒")
                page.locator('[data-film-action="ask"]:visible').click()

                # The request is intercepted and intentionally held before any response
                # reaches the app. Do not wait for text in the hidden legacy chat DOM:
                # the built React AI panel renders its own message tree.
                for _ in range(100):
                    if command_routes:
                        break
                    page.wait_for_timeout(20)
                assert len(command_requests) == 1, command_requests
                assert command_requests[0].get("base") == before["rev"], command_requests
                assert fd.read_project_film(project_id)["rev"] == before["rev"]

                # An external Agent moves the same target while AI generation is pending.
                external = page.evaluate("""async ({projectId,base}) => {
                  const response = await fetch('/api/editor/film/edit', {
                    method:'POST',
                    headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({
                      project_id:projectId,
                      base,
                      ops:[{op:'move',clip:'clip-b',track_id:'video-main',at:7}]
                    })
                  });
                  return {status:response.status,body:await response.json()};
                }""", {"projectId": project_id, "base": before["rev"]})
                assert external["status"] == 200, external
                changed_revision = external["body"].get("rev")
                assert changed_revision and changed_revision != before["rev"], external

                page.wait_for_function(
                    "revision => window.filmStudioStore?.getSnapshot()?.pendingExternalRevision === revision",
                    arg=changed_revision,
                    timeout=5000,
                )
                pending = page.evaluate("""() => {
                  const s=window.filmStudioStore.getSnapshot();
                  return {revision:s.revision,pendingExternalRevision:s.pendingExternalRevision};
                }""")
                assert pending == {
                    "revision": before["rev"],
                    "pendingExternalRevision": changed_revision,
                }, pending
                assert fd.read_project_film(project_id)["rev"] == changed_revision

                # Only now return the candidate calculated from the old revision.
                command_routes[0].fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "ok": True,
                        "proposal": proposal,
                        "message": "候选修改已生成，尚未写入正式影片。",
                    }, ensure_ascii=False),
                )

                stale_warning = page.locator("#fs-proposal-stale-warning")
                stale_warning.wait_for(state="visible", timeout=10_000)
                warning_text = stale_warning.inner_text()
                assert before["rev"] in warning_text, warning_text
                assert changed_revision in warning_text, warning_text
                page.locator('[data-film-action="preview-proposal"]:visible').wait_for(state="visible")
                page.locator('[data-film-action="apply-proposal"]:visible').wait_for(state="visible")
                assert page.locator('[data-film-action="preview-proposal"]:visible').is_disabled()
                assert page.locator('[data-film-action="apply-proposal"]:visible').is_disabled()
                assert page.locator('[data-film-action="reject-proposal"]:visible').is_enabled()

                final = fd.read_project_film(project_id)
                assert final["rev"] == changed_revision, final
                final_clips = {clip["id"]: clip for track in final["doc"]["tracks"] for clip in track["clips"]}
                assert abs(float(final_clips["clip-b"]["attrs"]["at"]) - 7.0) < 0.001
                assert apply_requests == [], apply_requests
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_active_film_director_tab_submits_revision_bound_ai_proposal_without_legacy_writes(tmp_path, monkeypatch):
    """Director planning remains usable after initialization without reopening CompositionIR writers."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-studio-director-bridge-browser"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    plans_dir = project_root / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "shotbook.json").write_text(json.dumps({
        "schema_version":1,"project_id":project_id,"version":4,"updated_at":1,
        "shots":[{
            "scene_id":"old-scene-2","start":4.0,"end":7.5,"text":"旧分镜：解释第二个镜头的认知转折",
            "semantic_role":"turn","visual_intent":"contrast","shot_type":"footage",
            "shot_recipe_id":"old-shot-recipe","motion_recipe_id":"old-motion-recipe",
            "overlay_text":"旧版强调字卡","evidence_ref":"old-evidence","sfx_cue":"old-sfx",
            "notes":"这是旧版 sidecar 参考，不是当前时间线。","reviewed":True,
            "characters":[],"editable_layers":[]
        }]
    },ensure_ascii=False),encoding="utf-8")
    from video_director import creative_pipeline as vd_creative
    from video_director.creative_contracts import CreativePlan
    legacy_plan=CreativePlan(project_id=project_id,script_hash="legacy-script-hash",timing_source="missing")
    vd_creative.save_plan(legacy_plan,expected_version=0)
    before = fd.read_project_film(project_id)
    assert before["exists"]

    candidate_doc, normalized = fd.apply_ops(before["doc"], [
        {"op": "move", "clip": "clip-b", "track_id": "video-main", "at": 6.0}
    ])
    candidate_text = fd.serialize_film(candidate_doc)
    candidate_state = {
        "exists": True,
        "project_id": project_id,
        "text": candidate_text,
        "rev": fd.revision_of(candidate_text),
        "doc": candidate_doc,
    }
    proposal = {
        "id": "director-bridge-proposal",
        "project_id": project_id,
        "base_revision": before["rev"],
        "operations": normalized,
        "summary": "导演审查：调整第二个镜头节奏",
        "candidate": fd.film_payload(project_id, candidate_state),
        "affected_clip_ids": ["clip-b"],
    }
    command_requests: list[dict] = []

    def handle_command(route):
        command_requests.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "ok": True,
                "proposal": proposal,
                "message": "已生成绑定 revision 的候选修改；尚未写入正式影片。",
            }, ensure_ascii=False),
        )

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.route("**/api/editor/film/command", handle_command)
            try:
                page.set_default_navigation_timeout(10_000)
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="domcontentloaded",
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.filmStudioAsk === 'function'")
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function(
                    "() => { const select=document.querySelector('#editor-project'); return !!select && select.options.length>0; }"
                )
                page.evaluate("""pid => {
                  const select=document.querySelector('#editor-project');
                  let option=[...select.options].find(item=>item.value===pid);
                  if(!option){option=new Option(pid,pid);select.add(option);}
                  select.value=pid;
                  select.dispatchEvent(new Event('change',{bubbles:true}));
                }""", project_id)
                page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                page.locator('#fs-timeline .fs-clip[data-clip="clip-b"]').wait_for(state="visible")

                page.locator('[data-film-action="director-mode"]').click()
                page.locator("#fs-director-authority-note").wait_for(state="visible")
                page.locator("#fs-director-prompt-review").click()
                prompt = page.locator("#fs-director-film-prompt").input_value()
                assert "导演审查" in prompt and "当前 revision" in prompt
                assert page.locator("#fs-director-planning-host #director-bar-card").count() == 0
                assert page.locator("#fs-director-planning-host #director-control-grid").count() == 0
                assert page.locator("#fs-director-planning-host #shotbook-details").count() == 1
                assert page.locator("#fs-director-planning-host #creative-plan-panel").count() == 1
                assert page.locator("#shotbook-film-authority-note").is_visible()
                assert page.locator("#creative-plan-film-authority-note").is_visible()

                base_url = f"http://127.0.0.1:{server.server_port}"
                shotbook_response = page.request.get(base_url + "/api/editor/shotbook?project_id=" + project_id)
                assert shotbook_response.status == 200
                shotbook_reference = shotbook_response.json()
                assert shotbook_reference["reference_only"] is True and shotbook_reference["stale"] is True
                assert shotbook_reference["version"] == 4 and shotbook_reference["saved"] is True
                assert shotbook_reference["shots"][0]["scene_id"] == "old-scene-2"
                assert "旧 CompositionIR" in shotbook_reference["timing_note"]
                creative_response = page.request.get(base_url + "/api/editor/creative-plan?project_id=" + project_id)
                assert creative_response.status == 200
                creative_reference = creative_response.json()
                assert creative_reference["reference_only"] is True and creative_reference["stale"] is True
                assert creative_reference["exists"] is True
                assert creative_reference["plan"]["project_id"] == project_id
                creative_save = page.request.post(base_url + "/api/editor/creative-plan", data=json.dumps({
                    "project_id":project_id,"action":"save","expected_version":creative_reference["plan"]["version"],
                    "plan":creative_reference["plan"]
                }),headers={"Content-Type":"application/json"})
                assert creative_save.status == 200 and creative_save.json().get("reference_only") is True, creative_save.text()
                assert creative_save.json()["plan"]["version"] == creative_reference["plan"]["version"] + 1
                assert fd.read_project_film(project_id)["rev"] == before["rev"]

                legacy_read_probes = [
                    "/api/editor/composition?project_id=" + project_id,
                    "/api/editor/material_match?project_id=" + project_id,
                ]
                for route in legacy_read_probes:
                    result = page.request.get(base_url + route)
                    body = result.json()
                    assert result.status == 409 and body.get("code") == "FILM_DOCUMENT_AUTHORITATIVE", (
                        route, result.status, body
                    )

                legacy_write_probes = [
                    ("/api/editor/film/initialize", {"project_id":project_id,"expected_composition_version":1}),
                    ("/api/editor/command", {"project_id":project_id,"command":"set_duration","targetId":"clip-b","payload":{"duration":4.0}}),
                    ("/api/editor/generate/add", {"project_id":project_id,"scene_id":"clip-b","asset_id":"legacy-asset"}),
                    ("/api/editor/matting", {"project_id":project_id,"target_id":"old-scene#0"}),
                    ("/api/editor/shotbook", {"project_id":project_id,"action":"generate"}),
                    ("/api/editor/shotbook", {"project_id":project_id,"action":"save","base_version":4,"shots":[]}),
                    ("/api/editor/creative-plan", {"project_id":project_id,"action":"initialize"}),
                    ("/api/editor/creative-plan", {"project_id":project_id,"action":"validate"}),
                    ("/api/proposal/propose", {"project_id":project_id,"instruction":"把第二个镜头缩短一秒"}),
                    ("/api/proposal/preview", {"project_id":project_id,"proposal_id":"legacy-proposal"}),
                    ("/api/proposal/apply", {"project_id":project_id,"proposal_id":"legacy-proposal"}),
                    ("/api/proposal/reject", {"project_id":project_id,"proposal_id":"legacy-proposal"}),
                    ("/api/director/create", {"project_id":project_id,"brief":"旧版导演写入测试","script":"测试脚本"}),
                    ("/api/director/continue", {"project_id":project_id,"target":"preview"}),
                    ("/api/director/retry", {"project_id":project_id}),
                    ("/api/director/execute", {"project_id":project_id,"instruction":"移动第二个镜头"}),
                    ("/api/action/execute", {"project_id":project_id,"action_id":"timeline.move","params":{"clip_id":"clip-b"}}),
                    ("/api/workflows", {"kind":"compose","spec":{"project_id":project_id,"script":"旧版合成测试"}}),
                    ("/api/project_action", {"project_id":project_id,"action":"convert"}),
                ]
                for route, payload in legacy_write_probes:
                    result = page.request.post(
                        base_url + route,
                        data=json.dumps(payload),
                        headers={"Content-Type":"application/json"},
                    )
                    body = result.json()
                    assert result.status == 409 and body.get("code") == "FILM_DOCUMENT_AUTHORITATIVE", (
                        route, result.status, body
                    )
                assert fd.read_project_film(project_id)["rev"] == before["rev"]

                legacy_post_requests: list[str] = []
                def record_legacy_post(request):
                    if request.method == "POST" and any(path in request.url for path in (
                        "/api/editor/shotbook", "/api/editor/creative-plan", "/api/commands", "/api/editor/command"
                    )):
                        legacy_post_requests.append(request.url)
                page.on("request", record_legacy_post)
                page.locator('[data-shotbook-action="load"]').click()
                page.wait_for_function("() => document.querySelector('#shotbook-status')?.textContent.includes('只读参考')")
                assert "旧分镜：解释第二个镜头" in page.locator("#shotbook-storyboard").inner_text()
                assert page.locator('[data-shotbook-action="generate"]').is_disabled()
                assert page.locator('[data-shotbook-action="save"]').is_disabled()
                page.wait_for_function("() => document.querySelector('#creative-plan-status')?.textContent.includes('只读作导演参考')",timeout=10000)
                assert "只读作导演参考" in page.locator("#creative-plan-status").inner_text()
                page.evaluate("""() => {
                  const button=document.createElement('button');
                  button.type='button';button.dataset.shotbookAction='generate';button.textContent='legacy generate probe';
                  document.querySelector('#shotbook-details').appendChild(button);
                  button.click();button.remove();
                }""")
                initialize_button = page.locator('#creative-plan-panel [data-action="ui.creative_plan_initialize"]')
                assert initialize_button.is_disabled()
                # The button is disabled in reference-only mode. A synthetic DOM click must
                # also stay local; the direct API probe above separately proves server rejection.
                page.evaluate("""() => {
                  const button=document.querySelector('#creative-plan-panel [data-action="ui.creative_plan_initialize"]');
                  button?.click();
                }""")
                assert legacy_post_requests == [], legacy_post_requests
                assert fd.read_project_film(project_id)["rev"] == before["rev"]

                page.evaluate("""pid => {
                  window.shotbookExportForFilmDirector = () => ({
                    project_id:pid,version:4,saved:true,
                    shots:[{legacy_scene_id:'old-scene-2',start:4,end:7.5,text:'第二个镜头讲清认知转折',
                      semantic_role:'turn',visual_intent:'contrast',shot_type:'footage',
                      shot_recipe_id:'old-shot-recipe',motion_recipe_id:'old-motion-recipe',
                      overlay_text:'旧版强调字卡',evidence_ref:'old-evidence',sfx_cue:'old-sfx',
                      notes:'旧分镜备注必须作为参考重新核对。',reviewed:true}]
                  });
                  window.creativePlanExportForFilmDirector = () => ({
                    project_id:pid,version:2,stale:true,stale_reasons:['旧 CompositionIR 时间线可能已变化'],
                    semantic_beats:[{sentence_id:'old-sentence-2',text:'第二句旁白',intent:'解释转折',entities:['主角'],
                      visual_need:['对比画面'],shot_id:'old-shot-2'}],
                    shots:[{shot_id:'old-shot-2',intent:'用一个可验证画面解释转折',
                      unresolved_items:['素材仍需复核'],layers:[{role:'主视觉',recipe_id:'old-recipe',asset_ids:['old-asset'],evidence_refs:['old-ref']}]}]
                  });
                }""", project_id)
                page.locator('[data-film-action="director-propose-plan"]').click()
                page.locator('#fs-proposal-panel[data-react-ai-proposal="true"]').wait_for(
                    state="visible", timeout=10_000
                )
                assert len(command_requests) == 1, command_requests
                request = command_requests[0]
                assert request["project_id"] == project_id, request
                assert request["base"] == before["rev"], request
                assert prompt in request["message"], request
                assert "旧分镜备注必须作为参考重新核对" in request["message"], request
                assert "旧 CompositionIR 时间线可能已变化" in request["message"], request
                assert "CompositionIR 旧版导演参考资料" in request["message"], request
                assert "当前 film.html" in request["message"] and "revision" in request["message"], request
                assert request["stream"] is True, request
                assert legacy_post_requests == [], legacy_post_requests
                assert fd.read_project_film(project_id)["rev"] == before["rev"], (
                    "generating a director proposal must not write the authoritative film"
                )
                assert before["rev"] in page.locator('#fs-proposal-panel[data-react-ai-proposal="true"] #fs-proposal-detail').inner_text()
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_late_ai_response_from_previous_project_cannot_clear_new_project_busy_state(tmp_path, monkeypatch):
    """A superseded project's response/finally must not mutate the newly active AI request."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root
    project_a = "film-studio-ai-switch-a"
    project_b = "film-studio-ai-switch-b"
    for project_id in (project_a, project_b):
        project_root = storage_root / project_id
        project_root.mkdir(parents=True)
        (project_root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            try:
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/",
                    wait_until="domcontentloaded",
                )
                assert response and response.status == 200
                page.wait_for_function("typeof window.filmStudioAsk === 'function'")
                page.locator("#tab-editor").click()
                page.locator("#pane-editor").wait_for(state="visible")
                page.wait_for_function(
                    "() => !!document.querySelector('#editor-project')?.options.length"
                )

                def select_project(project_id: str) -> None:
                    page.evaluate(
                        """pid => {
                          const select = document.querySelector('#editor-project');
                          let option = [...select.options].find(item => item.value === pid);
                          if (!option) { option = new Option(pid, pid); select.add(option); }
                          select.value = pid;
                          select.dispatchEvent(new Event('change', {bubbles:true}));
                        }""",
                        project_id,
                    )
                    page.wait_for_function(
                        "pid => document.querySelector('#editor-project')?.value === pid",
                        arg=project_id,
                    )
                    page.locator("#film-studio-panel.fs-studio-expanded").wait_for(state="visible")
                    page.wait_for_function(
                        "() => !!document.querySelector('#fs-timeline .fs-clip[data-clip=\"clip-a\"]')",
                        timeout=10_000,
                    )
                    page.evaluate("window.filmStudioSetSideMode('ai')")
                    page.locator("#fs-chat-input").wait_for(state="visible")

                select_project(project_a)
                page.evaluate("""() => {
                  const nativeFetch = window.fetch.bind(window);
                  window.__aiDeferredRequests = [];
                  window.fetch = (input, init = {}) => {
                    const url = typeof input === 'string' ? input : input.url;
                    if (url.includes('/api/editor/film/command/cancel')) {
                      return Promise.resolve(new Response('{"ok":true}', {
                        status: 200, headers: {'Content-Type':'application/json'}
                      }));
                    }
                    if (url.includes('/api/editor/film/command')) {
                      return new Promise(resolve => window.__aiDeferredRequests.push({
                        resolve, body: JSON.parse(init.body || '{}')
                      }));
                    }
                    return nativeFetch(input, init);
                  };
                }""")

                page.locator("#fs-chat-input").fill("项目 A 的旧请求")
                page.locator('[data-film-action="ask"]:visible').click()
                page.wait_for_function("window.__aiDeferredRequests.length === 1")

                select_project(project_b)
                page.locator("#fs-chat-input").fill("项目 B 的当前请求")
                page.locator('[data-film-action="ask"]:visible').click()
                page.wait_for_function("window.__aiDeferredRequests.length === 2")

                # The old fetch deliberately ignores AbortSignal and returns late,
                # simulating an upstream/proxy that cannot be cancelled in time.
                page.evaluate("""() => window.__aiDeferredRequests[0].resolve(new Response(
                  JSON.stringify({ok:true, no_op:true, message:'项目 A 的迟到响应'}),
                  {status:200, headers:{'Content-Type':'application/json'}}
                ))""")
                page.wait_for_timeout(100)

                # The second request must still own busy state. A third instruction
                # should steer/cancel it, not start a competing third model request.
                page.locator("#fs-chat-input").fill("项目 B 的补充方向")
                page.locator('[data-film-action="ask"]:visible').click()
                assert page.evaluate("window.__aiDeferredRequests.length") == 2
                assert page.evaluate(
                    "window.__aiDeferredRequests[1].body.project_id"
                ) == project_b
                assert not page_errors, f"browser JavaScript errors: {page_errors}"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
