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
