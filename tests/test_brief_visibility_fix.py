# -*- coding: utf-8 -*-
"""2026-10-25 用户报障根修回归锁：brief 在 rule 兜底可见 + 步骤 detail 透出。

用户质问：①AI 没按自然语言要求剪、按什么指令在剪、为何不汇报；
②创作后要求消失不可改。本文件锁修复1/2 的后端通道：
- rule 兜底分镜必须记录 creative_brief + brief_acknowledged=False
  （要求未被理解，仅原样保留——诚实，不假装理解）
- workflow step_update 存 detail 进 steps_status（前端步骤面板可读）
- get_run 兼容读新结构 {status, detail} 与旧纯字符串
- 节点 warnings 透传 detail（「你的要求未被 AI 理解」用户必须能看到）
"""
import json

import pytest

from video_director import workflow_run as wr


class TestStepDetailChannel:
    """step_update detail 存储 + get_run 读出 + 新旧格式兼容。"""

    def test_detail_stored_and_read(self):
        run_id = wr.start_run("detail_test", project_id="proj-detail",
                              steps=["a", "b"])
        try:
            wr.step_update(run_id, "a", "DONE",
                           detail="⚠ 分镜规划降级：要求未被理解")
            wr.step_update(run_id, "b", "DONE")
            run = wr.get_run(run_id)
            by_id = {s["id"]: s for s in run["steps"]}
            assert by_id["a"]["detail"].startswith("⚠ 分镜")
            assert by_id["b"]["detail"] == ""
        finally:
            wr.delete_run(run_id, allow_waiting=True)

    def test_next_pending_compat_dict_status(self):
        """_next_pending 兼容 {status, detail} 结构（不能因新格式找不到 PENDING）。"""
        assert wr._next_pending({"a": {"status": "DONE", "detail": ""},
                                  "b": {"status": "PENDING", "detail": ""}}) == "b"
        assert wr._next_pending({"a": "DONE", "b": "RUNNING"}) == "b"
        assert wr._next_pending({"a": {"status": "DONE", "detail": ""}}) is None

    def test_detail_survives_later_status_update(self):
        """步骤先带 detail 标 DONE，后续再 update（如 stop_at SKIPPED 流程）
        不能把已存的 detail 冲掉。"""
        run_id = wr.start_run("detail_keep", project_id="proj-keep",
                              steps=["a"])
        try:
            wr.step_update(run_id, "a", "DONE", detail="降级说明保留")
            wr.step_update(run_id, "a", "DONE")
            run = wr.get_run(run_id)
            assert run["steps"][0]["detail"] == "降级说明保留"
        finally:
            wr.delete_run(run_id, allow_waiting=True)


class TestRuleFallbackBriefRecord:
    """rule 兜底分镜必须记录 brief + 诚实标注未被理解。"""

    def test_director_agent_rule_fallback_records_brief(self):
        """_tool_scene_plan provider=None 路径：brief 进 scene_plan +
        brief_acknowledged=False + source=rule_fallback。"""
        import video_director.director_agent as da
        from video_director import config, mpt_semantics

        state = {
            "project_id": "proj-brief-record",
            "bundle": {"script": {"text": "句一。句二。", "authority": "authoritative"},
                       "brief": "用我的素材，节奏快一点，突出产品特写"},
            "scene_texts": ["句一。", "句二。"],
        }
        config.init_project_dirs(config.project_dir("proj-brief-record"))
        # provider=None 强制 rule 路径
        import video_director.llm_provider as lp
        orig = lp.get_llm
        lp.get_llm = lambda: None
        try:
            r = da._tool_scene_plan({"brief": ""}, state)
            plan = state["scene_plan"]
            assert r["ok"]
            assert plan["source"] == "rule_fallback"
            assert plan["creative_brief"] == "用我的素材，节奏快一点，突出产品特写"
            assert plan["brief_acknowledged"] is False
            assert len(plan["scenes"]) == 2
        finally:
            lp.get_llm = orig

    def test_director_update_node_detail_passthrough(self, monkeypatch):
        """节点 warnings 经 detail 通道进 WorkflowRun steps（不在后端 log 里消失）。"""
        run_id = wr.start_run("warn_pass", project_id="proj-warn",
                              steps=["scene_plan"])
        try:
            plan = {"nodes": [{"id": "scene_plan", "status": "PENDING"}]}
            wr._director_update_node(
                run_id, "proj-warn", plan, "scene_plan", "DONE",
                detail="⚠ 分镜规划降级：你的自然语言剪辑要求未被 AI 理解执行",
            )
            run = wr.get_run(run_id)
            step = {s["id"]: s for s in run["steps"]}["scene_plan"]
            assert "未被 AI 理解执行" in step["detail"]
            assert step["status"] == "DONE"
        finally:
            wr.delete_run(run_id, allow_waiting=True)

    def test_director_update_node_detail_stripped_from_plan_kwargs(self):
        """detail 不进 plan 节点结构（_di.update_node 不接受该 kwarg，必须剥出）。"""
        import video_director.director_inputs as di
        captured = {}
        orig = di.update_node
        di.update_node = (
            lambda pid, plan, nid, st, **kw: captured.update(kwargs=kw, called=True))
        try:
            wr._director_update_node(
                "r-x", "p-x", {"nodes": []}, "scene_plan", "DONE",
                detail="说明文本", outputs=["a"])
            assert "detail" not in captured["kwargs"]  # 剥出，不炸 update_node
            assert captured["kwargs"]["outputs"] == ["a"]
        finally:
            di.update_node = orig
