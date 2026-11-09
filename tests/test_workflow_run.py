"""最终方案 §52/§53/§70-4 WorkflowRun 契约测试。

覆盖：
- §52 start_run → run_id → get_run 返回 {run_id, project_id, status, current, steps[]}
- 步骤持久化（跨调用读取）+ 真实事件（step_update 更新 DB，非动画）
- finish_run 终态（COMPLETED / WAITING_CONFIRMATION / FAILED）
- §70-4 唯一任务形态：runs_of_project 按 project 关联
"""

import pytest


@pytest.fixture()
def _iso_storage(tmp_path, monkeypatch):
    from video_director import config
    (tmp_path / "composer").mkdir()
    monkeypatch.setattr(config, "composer_storage_root",
                        lambda: tmp_path / "composer")
    yield


class TestWorkflowRun:
    def test_start_and_get_fingerprint(self, _iso_storage):
        """§52 响应体指纹：run_id/project_id/status/current/steps。"""
        from video_director import workflow_run as wf
        run_id = wf.start_run("compose", project_id="wf-01")
        r = wf.get_run(run_id)
        assert r["run_id"] == run_id
        assert r["kind"] == "compose"
        assert r["project_id"] == "wf-01"
        assert r["status"] == "RUNNING"
        assert r["current"]  # 当前步骤非空
        assert r["steps"] and all("id" in s and "status" in s for s in r["steps"])
        # §20 一键链步骤序（DEFAULT_STEPS）
        ids = [s["id"] for s in r["steps"]]
        assert ids[:4] == ["script", "scene_plan", "narration", "material_match"]

    def test_step_updates_persist(self, _iso_storage):
        from video_director import workflow_run as wf
        run_id = wf.start_run("compose", project_id="wf-02")
        wf.step_update(run_id, "script", "DONE")
        wf.step_update(run_id, "narration", "RUNNING")
        r = wf.get_run(run_id)
        steps = {s["id"]: s["status"] for s in r["steps"]}
        assert steps["script"] == "DONE"
        assert steps["narration"] == "RUNNING"
        assert r["current"] == "narration"

    def test_finish_states(self, _iso_storage):
        from video_director import workflow_run as wf
        rid = wf.start_run("compose", project_id="wf-03")
        wf.finish_run(rid, wf.WAITING_CONFIRMATION, {"project_id": "wf-03"})
        assert wf.get_run(rid)["status"] == "WAITING_CONFIRMATION"
        rid2 = wf.start_run("render_final", project_id="wf-03")
        wf.finish_run(rid2, wf.FAILED, error="渲染器不可用")
        r2 = wf.get_run(rid2)
        assert r2["status"] == "FAILED"
        assert "渲染器" in r2["error"]

    def test_terminal_finish_is_idempotent_and_ignores_late_steps(self, _iso_storage):
        from video_director import director_history, workflow_run as wf

        conv = director_history.create_conversation(
            conversation_id="conv-terminal-once", title="只收口一次",
            initial_message="用户请求",
        )
        rid = wf.start_run(
            "director_create", project_id="wf-terminal-once",
            steps=["understand", "render"], conversation_id=conv["conversation_id"],
        )
        assert wf.finish_run(rid, wf.COMPLETED, result={"answer": "first"})
        assert not wf.finish_run(rid, wf.FAILED, result={"answer": "late"},
                                 error="late worker exception")
        wf.step_update(rid, "render", "DONE", detail="late callback")

        run = wf.get_run(rid)
        loaded = director_history.get_conversation(conv["conversation_id"])
        assert run["status"] == wf.COMPLETED
        assert run["result"] == {"answer": "first"}
        assert [s["id"] for s in run["steps"]] == ["understand", "render"]
        assert loaded["status"] == wf.COMPLETED
        assert [m["role"] for m in loaded["messages"]] == ["user", "assistant"]
        assert loaded["messages"][-1]["metadata"]["status"] == wf.COMPLETED

    def test_cancel_request_wins_over_a_racing_finish(self, _iso_storage):
        from video_director import workflow_run as wf

        rid = wf.start_run("compose", project_id="wf-cancel-race")
        assert wf.request_cancel(rid)
        # A racing success callback cannot override an accepted stop request.
        assert wf.finish_run(rid, wf.COMPLETED, result={"completed": True})
        run = wf.get_run(rid)
        assert run["status"] == wf.CANCELLED
        assert run["error"] == "用户请求停止"
        assert run["result"] == {}
        assert not wf.request_cancel(rid)

    def test_unsupported_run_kind_is_not_reported_as_cancellable(self, _iso_storage):
        from video_director import workflow_run as wf

        for kind in ("render_draft", "render_final", "film_render_final"):
            rid = wf.start_run(kind, project_id="wf-uncancellable")
            assert not wf.request_cancel(rid)
            assert wf.get_run(rid)["status"] == wf.RUNNING
            assert wf.get_run(rid)["cancellable"] is False
            assert wf.list_runs(project_id="wf-uncancellable")[0]["cancellable"] is False
            assert wf.finish_run(rid, wf.COMPLETED)


    def test_runs_of_project(self, _iso_storage):
        """§70-4：项目关联 run 可查（删除验证/历史都依赖）。"""
        from video_director import workflow_run as wf
        wf.start_run("compose", project_id="wf-04")
        wf.start_run("render_draft", project_id="wf-04")
        wf.start_run("compose", project_id="wf-05")
        assert len(wf.runs_of_project("wf-04")) == 2
        assert len(wf.runs_of_project("wf-05")) == 1

    def test_get_missing_run_honest(self, _iso_storage):
        from video_director import workflow_run as wf
        assert wf.get_run("run-nonexistent") is None

    def test_conversation_id_is_persisted(self, _iso_storage):
        from video_director import workflow_run as wf
        rid=wf.start_run("director_create",project_id="wf-chat",conversation_id="conv-001")
        assert wf.get_run(rid)["conversation_id"]=="conv-001"
        assert wf.list_runs(project_id="wf-chat")[0]["conversation_id"]=="conv-001"

    def test_step_labels_human(self, _iso_storage):
        """§53 步骤人话（真实事件标注，非技术词裸奔）。"""
        from video_director import workflow_run as wf
        rid = wf.start_run("compose", project_id="wf-06")
        r = wf.get_run(rid)
        assert r["labels"]["script"] == "理解文案"
        assert r["labels"]["material_match"] == "匹配画面"


def test_director_retry_prefers_current_project_metadata_over_original_spec(monkeypatch):
    import json

    from video_director import director_inputs, director_intent, store, workflow_run as wf

    project_id = "web-retry-metadata"
    bundle = {
        "brief": "继续制作知识科普视频",
        "script": {"text": "当前文案"},
        "assets": {"asset_refs": []},
    }
    monkeypatch.setattr(store, "get_project", lambda _pid: {
        "project_id": project_id,
        "script": "当前文案",
        "params_json": json.dumps({
            "name": "已重命名项目",
            "project_type": "知识科普",
            "creative_brief": "继续制作知识科普视频",
        }),
    })
    monkeypatch.setattr(wf, "list_runs", lambda **_kwargs: [])
    monkeypatch.setattr(director_inputs, "load_bundle", lambda _pid: bundle)
    monkeypatch.setattr(director_intent, "resolve_rule_intents", lambda _brief: {})
    monkeypatch.setattr(
        wf, "_load_persisted_director_spec",
        lambda _pid: {"project_name": "最初的项目名称", "project_type": "最初的类型"},
    )
    monkeypatch.setattr(store, "set_stage", lambda *_args, **_kwargs: None)
    captured = {}

    def capture_retry_spec(spec):
        captured["spec"] = spec
        return "run-retry-metadata"

    monkeypatch.setattr(wf, "run_director_create_async", capture_retry_spec)

    run_id = wf.run_director_retry_async(project_id)

    assert run_id == "run-retry-metadata"
    assert captured["spec"]["project_name"] == "已重命名项目"
    assert captured["spec"]["project_type"] == "知识科普"

def test_director_retry_falls_back_to_persisted_metadata_for_legacy_params(monkeypatch):
    import json

    from video_director import director_inputs, director_intent, store, workflow_run as wf

    project_id = "web-retry-legacy-metadata"
    bundle = {
        "brief": "继续制作旧项目",
        "script": {"text": "原项目文案"},
        "assets": {"asset_refs": []},
    }
    monkeypatch.setattr(store, "get_project", lambda _pid: {
        "project_id": project_id,
        "script": "原项目文案",
        # Legacy rows predate the explicit name/type params_json keys.
        "params_json": json.dumps({"creative_brief": "继续制作旧项目"}),
    })
    monkeypatch.setattr(wf, "list_runs", lambda **_kwargs: [])
    monkeypatch.setattr(director_inputs, "load_bundle", lambda _pid: bundle)
    monkeypatch.setattr(director_intent, "resolve_rule_intents", lambda _brief: {})
    monkeypatch.setattr(
        wf, "_load_persisted_director_spec",
        lambda _pid: {"project_name": "历史项目名称", "project_type": "旧项目类型"},
    )
    monkeypatch.setattr(store, "set_stage", lambda *_args, **_kwargs: None)
    captured = {}

    def capture_retry_spec(spec):
        captured["spec"] = spec
        return "run-retry-legacy-metadata"

    monkeypatch.setattr(wf, "run_director_create_async", capture_retry_spec)

    run_id = wf.run_director_retry_async(project_id)

    assert run_id == "run-retry-legacy-metadata"
    assert captured["spec"]["project_name"] == "历史项目名称"
    assert captured["spec"]["project_type"] == "旧项目类型"
