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
    workflow_run.finish_run(rid,workflow_run.WAITING_CONFIRMATION,result={"project_id":"project-01"})
    loaded=director_history.get_conversation(conv["conversation_id"])
    assert loaded["workflow_run_id"]==rid
    assert loaded["snapshot"]["form"]["fields"]["cf-style"]=="documentary-calm"
    assert [x["role"] for x in loaded["messages"]]==["user","tool","assistant"]
    assert "等待人工确认" in loaded["messages"][-1]["content"]


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
