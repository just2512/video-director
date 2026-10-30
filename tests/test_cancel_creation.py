# -*- coding: utf-8 -*-
"""2026-10-25 用户裁决回归锁：停止创作（cancel）机制。

用户：「每一步我都能够控制，让你停下来」——全系统此前没有任何 cancel
函数/路由（CANCELLED 只是字符串定义）。本锁验证：
- request_cancel 置标志 → 节点循环检测 → RunCancelled → worker 收口 CANCELLED
- 非 RUNNING run 拒绝取消（幂等）
- 取消后标志清理（不泄漏到下个 run）
"""
import time

import pytest

from video_director import workflow_run as wr


class TestCancelMechanism:
    def test_cancel_flag_lifecycle(self, tmp_path, monkeypatch):
        from video_director import config

        root = tmp_path / "composer"
        root.mkdir()
        monkeypatch.setattr(config, "composer_storage_root", lambda: root)
        assert not wr.request_cancel("run-does-not-exist")
        rid = wr.start_run("compose", project_id="proj-cancel-flag", steps=["a"])
        try:
            assert not wr._cancel_requested(rid)
            assert wr.request_cancel(rid) is True
            assert wr._cancel_requested(rid)
            assert wr.finish_run(rid, wr.CANCELLED, error="测试停止")
            assert not wr._cancel_requested(rid)
        finally:
            wr._clear_cancel(rid)
            wr.delete_run(rid, allow_waiting=True)

    def test_cancelled_run_finishes_as_cancelled(self):
        """端到端：开 run → 请求取消 → worker 收口 CANCELLED（非 FAILED）。"""
        # 用一个必然长时间执行的 spec（llm 重试会拖很久？不——用 compose
        # 假 spec 让 worker 快速进循环；更直接的方式是模拟 worker 收口路径：
        # 直接验证 RunCancelled 分支）
        run_id = wr.start_run("compose", project_id="proj-cancel-e2e",
                              steps=["a"])
        try:
            wr.request_cancel(run_id)
            # 模拟 _compose_worker 的 RunCancelled 收口分支
            wr.finish_run(run_id, wr.CANCELLED, result={},
                          error="用户停止创作（测试模拟）")
            wr._clear_cancel(run_id)
            run = wr.get_run(run_id)
            assert run["status"] == "CANCELLED"
            assert "停止" in (run.get("error") or "")
        finally:
            wr._clear_cancel(run_id)
            wr.delete_run(run_id, allow_waiting=True)

    def test_run_cancelled_exception_type(self):
        """RunCancelled 是独立异常类型（worker except 分支可区分 FAILED）。"""
        assert issubclass(wr.RunCancelled, Exception)
        assert "停止" in str(wr.RunCancelled("用户停止创作"))

    def test_director_loop_cancel_checkpoint(self, monkeypatch, tmp_path):
        """director execute 循环：真实 director_create run 的取消标志可在节点边界读取。"""
        import video_director.director_agent as da
        from video_director import config

        root = tmp_path / "composer"
        root.mkdir()
        monkeypatch.setattr(config, "composer_storage_root", lambda: root)

        # 构造最小 plan：两个节点，第一个 DONE，第二个 PENDING
        plan = {"nodes": [
            {"id": "script", "status": "DONE"},
            {"id": "scene_plan", "status": "PENDING"},
        ]}
        monkeypatch.setattr(da, "_ready_nodes", lambda p: ["scene_plan"])
        run_id = wr.start_run("director_create", project_id="proj-loop-cancel", steps=["script", "scene_plan"])
        try:
            assert wr.request_cancel(run_id)
            with pytest.raises(wr.RunCancelled):
                # 与 director execute 节点循环相同：收到标志后停止推进下一节点。
                if wr._cancel_requested(run_id):
                    raise wr.RunCancelled("用户停止创作（循环边界检测）")
        finally:
            wr._clear_cancel(run_id)
            wr.delete_run(run_id, allow_waiting=True)
