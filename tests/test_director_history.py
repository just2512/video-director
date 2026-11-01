from video_director import config, director_history, workflow_run


def test_draft_round_trip_and_clear(tmp_path, monkeypatch):
    root=tmp_path/"composer";root.mkdir()
    monkeypatch.setattr(config,"composer_storage_root",lambda:root)
    first=director_history.save_draft("active",{"fields":{"cf-director-brief":"原要求"},"context":{"asset_refs":[{"asset_id":"a1"}]}})
    second=director_history.save_draft("active",{"fields":{"cf-director-brief":"更新要求"}})
    loaded=director_history.get_draft("active")
    assert first["revision"]==1 and second["revision"]==2
    assert loaded["data"]["fields"]["cf-director-brief"]=="更新要求"
    assert director_history.delete_draft("active")
    assert director_history.get_draft("active") is None


def test_conversation_records_user_tool_and_assistant_messages(tmp_path, monkeypatch):
    root=tmp_path/"composer";root.mkdir()
    monkeypatch.setattr(config,"composer_storage_root",lambda:root)
    conv=director_history.create_conversation(
        conversation_id="conv-test-001",project_id="project-01",title="软件介绍",
        initial_message="制作一条软件介绍视频",
        snapshot={"form":{"fields":{"cf-style":"documentary-calm"}}})
    rid=workflow_run.start_run("director_create",project_id="project-01",
        steps=["understand","render"],conversation_id=conv["conversation_id"])
    director_history.link_workflow(conv["conversation_id"],project_id="project-01",workflow_run_id=rid)
    workflow_run.step_update(rid,"understand","DONE")
    workflow_run.finish_run(rid,workflow_run.WAITING_CONFIRMATION,result={
        "project_id":"project-01",
        "director_inputs":{"script":{"source":"director","text":"这是一段 AI 生成文案。"}},
        "warnings":["素材来源已按用户优先策略处理"],
        "director_plan":{"nodes":[{"id":"understand","status":"DONE"},{"id":"render","status":"PENDING"}]},
    })
    loaded=director_history.get_conversation(conv["conversation_id"])
    assert loaded["workflow_run_id"]==rid
    assert loaded["snapshot"]["form"]["fields"]["cf-style"]=="documentary-calm"
    assert [x["role"] for x in loaded["messages"]]==["user","tool","assistant"]
    assert "等待人工确认" in loaded["messages"][-1]["content"]
    assert "这是一段 AI 生成文案。" in loaded["messages"][-1]["content"]
    assert "素材来源已按用户优先策略处理" in loaded["messages"][-1]["content"]
    assert "1 个完成" in loaded["messages"][-1]["content"]
    assert loaded["messages"][-1]["metadata"]["project_id"] == "project-01"
    assert loaded["messages"][-1]["metadata"]["nodes"][0]["id"] == "understand"
    assert "director_inputs" not in loaded["messages"][-1]["metadata"]


def test_workflow_terminal_record_reconciles_link_without_client_callback(tmp_path, monkeypatch):
    root=tmp_path/"composer";root.mkdir()
    monkeypatch.setattr(config,"composer_storage_root",lambda:root)
    conv=director_history.create_conversation(
        conversation_id="conv-no-client-link",title="浏览器断线场景")
    rid=workflow_run.start_run("director_create",project_id="project-server-link",
        steps=["understand"],conversation_id=conv["conversation_id"])
    # No follow-up /link request is sent by the frontend.
    workflow_run.finish_run(
        rid,workflow_run.COMPLETED,result={"project_id":"project-server-link"})
    loaded=director_history.get_conversation(conv["conversation_id"])
    assert loaded["project_id"]=="project-server-link"
    assert loaded["workflow_run_id"]==rid
    assert loaded["status"]=="COMPLETED"


def test_restart_reconciliation_marks_linked_conversation_interrupted(tmp_path, monkeypatch):
    root=tmp_path/"composer";root.mkdir()
    monkeypatch.setattr(config,"composer_storage_root",lambda:root)
    conv=director_history.create_conversation(
        conversation_id="conv-restart-interrupted",project_id="project-restart",
        title="服务重启恢复")
    rid=workflow_run.start_run("director_create",project_id="project-restart",
        steps=["understand","render"],conversation_id=conv["conversation_id"])
    result=workflow_run.reconcile_runs("测试模拟服务重启")
    run=workflow_run.get_run(rid)
    loaded=director_history.get_conversation(conv["conversation_id"])
    assert rid in result["interrupted_runs"]
    assert run["status"]==workflow_run.INTERRUPTED
    assert loaded["status"]==workflow_run.INTERRUPTED
    assert loaded["workflow_run_id"]==rid
    assert loaded["project_id"]=="project-restart"
    assert loaded["messages"][-1]["role"]=="assistant"
    assert "测试模拟服务重启" in loaded["messages"][-1]["content"]


def test_link_workflow_does_not_downgrade_a_fast_completed_run(tmp_path, monkeypatch):
    root=tmp_path/"composer";root.mkdir()
    monkeypatch.setattr(config,"composer_storage_root",lambda:root)
    conv=director_history.create_conversation(
        conversation_id="conv-fast-run",title="快速结束的创作")
    rid=workflow_run.start_run("director_create",project_id="project-fast",
        steps=["understand"],conversation_id=conv["conversation_id"])
    # Worker completes before the HTTP response handler executes /link.
    workflow_run.finish_run(rid,workflow_run.COMPLETED,result={"project_id":"project-fast"})
    assert director_history.get_conversation(conv["conversation_id"])["status"]=="COMPLETED"
    # Frontend then links project_id/run_id; this must not revert a terminal state.
    assert director_history.link_workflow(
        conv["conversation_id"],project_id="project-fast",workflow_run_id=rid)
    loaded=director_history.get_conversation(conv["conversation_id"])
    assert loaded["status"]=="COMPLETED"
    assert loaded["workflow_run_id"]==rid
    assert loaded["project_id"]=="project-fast"
    assert loaded["messages"][-1]["metadata"]["status"]=="COMPLETED"


def test_conversation_messages_keep_insert_order_when_timestamps_collide(tmp_path, monkeypatch):
    root=tmp_path/"composer";root.mkdir()
    monkeypatch.setattr(config,"composer_storage_root",lambda:root)
    monkeypatch.setattr(director_history.time,"time",lambda:1700000000.0)
    conv=director_history.create_conversation(
        conversation_id="conv-stable-order",title="同一时间戳",initial_message="用户消息")
    director_history.append_message(conv["conversation_id"],"tool","第一个工具结果")
    director_history.append_message(conv["conversation_id"],"tool","第二个工具结果")
    director_history.append_message(conv["conversation_id"],"assistant","最终答复")
    loaded=director_history.get_conversation(conv["conversation_id"])
    assert [m["content"] for m in loaded["messages"]]==[
        "用户消息","第一个工具结果","第二个工具结果","最终答复"
    ]


def test_conversation_list_can_filter_project(tmp_path, monkeypatch):
    root=tmp_path/"composer";root.mkdir()
    monkeypatch.setattr(config,"composer_storage_root",lambda:root)
    director_history.create_conversation(project_id="p1",title="P1")
    director_history.create_conversation(project_id="p2",title="P2")
    assert len(director_history.list_conversations(project_id="p1"))==1
    assert len(director_history.list_conversations())==2


def test_create_dialog_lifecycle_and_single_style_catalog():
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/"webui/static/js/create.js").read_text(encoding="utf-8")
    assert "mask.addEventListener('click', ev => { if (ev.target === mask) closeCreate(); });" not in source
    assert "apiPost('/api/director/drafts/active'" in source
    assert "apiGet('/api/director/drafts/active')" in source
    assert "beforeunload" in source and "cfHistoryOpenConversation" in source
    assert "apiPost('/api/director/conversations'" in source
    assert "id=\"cf-topic\"" not in source
    assert "apiGet('/api/registry/styles')" not in source
    assert "CF_DRAFT_CONTEXT.assetRefs = refs" in source
    assert "spec_settings" in source
    server_source=(Path(__file__).resolve().parents[1]/"webui/server.py").read_text(encoding="utf-8")
    assert '"explicit_fields": spec.get("_explicit_fields") if "_explicit_fields" in spec else None' in server_source
    assert "Array.isArray(spec.explicit_fields)" in source
    assert '"visual_source", "platform", "width", "height"' in server_source
    assert '"platform": effective_platform' in (Path(__file__).resolve().parents[1] / "video_director" / "director_service.py").read_text(encoding="utf-8")
    assert "'cf-platform': spec.platform || ''" in source
    assert "'cf-resolution': restoredAspect || undefined" in source
    assert "await Promise.allSettled(pending)" in source
    assert "#cf-form input[id],#cf-form textarea[id],#cf-form select[id]" in source
    assert "const isProgress=!!document.getElementById('cf-progress')" in source
    assert "await saving;" in source
    assert "window.CF_DEFAULTS_READY = createLoadDefaults();" in source
    assert "if(window.CF_DEFAULTS_READY) await window.CF_DEFAULTS_READY;" in source
    assert "if (window.CF_DEFAULTS_READY) await window.CF_DEFAULTS_READY;" in source
    assert "cfClearSavedDraft" in source
    assert "window.CF_DRAFT_CONTEXT.assetRefs = refs" in source
    assert 'if(!scriptActions?.contains(button))button.remove();' in source
    assert 'if(!quickActions?.contains(button))button.remove();' in source
    assert "actions.innerHTML='<button type=" in source
    assert '#cf-quick-actions button[onclick="cfGenerateScript()"]' in source
