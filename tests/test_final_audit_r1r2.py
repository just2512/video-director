# -*- coding: utf-8 -*-
"""2026-10-21 终审 R1/R2 回归锁：retry 意图解析 params 引用序 + 锁定回填顶级化。"""
import json
from pathlib import Path

import pytest


class TestR1RetryIntentOrder:
    """R1：retry 的意图解析在 params 定义前引用 → NameError 被吞 → 意图全灭。

    修法是 params 上移；测试锁「params_json 含 brief 时 intent 解析能拿到」。
    """

    def test_retry_resolves_intent_with_params_brief(self, tmp_path, monkeypatch):
        from video_director import config, store, model_config
        monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
        monkeypatch.setattr(model_config, "_cache", None)
        import video_director.llm_provider as lp
        monkeypatch.setattr(lp, "get_llm", lambda: None)
        monkeypatch.setattr(lp, "_provider_cache", None)

        pid = "proj-r1"
        from video_director import director_inputs as di
        config.init_project_dirs(config.project_dir(pid))
        bundle = {
            "version": di.VERSION, "brief": "时长40秒以上，加个水印「测试」",
            "script": {"text": "文案", "source": "user", "authority": "authoritative"},
            "assets": {"asset_refs": []}, "references": [],
            "software_project": {}, "constraints": {}, "creative_settings": {},
        }
        di.persist_bundle(pid, bundle)
        store.create_project(pid, "文案", {
            "creative_brief": "时长40秒以上，加个水印「测试」"})

        captured = {}
        monkeypatch.setattr(
            "video_director.workflow_run.run_director_create_async",
            lambda spec, plan=None: captured.update(spec=spec) or "run-r1")
        from video_director import workflow_run
        run_id = workflow_run.run_director_retry_async(pid)
        assert run_id, "retry 被拒绝"
        # R1 修复前：NameError 吞掉 → _intent={} → 意图全灭
        assert captured["spec"].get("watermark") == "测试"
        assert captured["spec"].get("_intent_duration_s") == "40+"


class TestR2LockBackfillTopLevel:
    """R2：锁定回填必须顶级执行——stock 成功补足（_gen_missing 空）时锁定
    绑定不得丢失。直接验证回填段与生成的相对位置：模拟 stock 全成功路径。
    """

    def test_lock_backfill_survives_stock_success(self, tmp_path, monkeypatch):
        """锁定 S03（有绑定），S02 走 stock 成功补位 → S03 绑定保留。"""
        monkeypatch.setattr("video_director.config.composer_storage_root",
                            lambda: tmp_path)
        from video_director.editor import save_composition, load_composition, _cmd_set_scene_locked
        from video_director.models import CompositionIR
        ir = CompositionIR.model_validate({
            "project": {"fps": 30, "width": 1080, "height": 1920, "duration": 12.0},
            "scenes": [
                {"id": "S01", "start": 0.0, "duration": 4.0, "text": "a",
                 "layers": [{"type": "gap", "start": 0.0, "duration": 4.0}]},
                {"id": "S02", "start": 4.0, "duration": 4.0, "text": "b",
                 "layers": [{"type": "gap", "start": 0.0, "duration": 4.0}]},
                {"id": "S03", "start": 8.0, "duration": 4.0, "text": "c",
                 "layers": [{"type": "video", "asset_id": "vid-1", "start": 0.0,
                             "duration": 4.0, "source_in": 0.0, "source_out": 4.0}]},
            ],
            "assets": [{"id": "vid-1", "type": "video", "file": "a.mp4",
                        "duration": 10.0}],
            "audio": [], "subtitle_track": {"cues": []},
        })
        save_composition("proj-r2", ir)
        ir2 = load_composition("proj-r2")
        _cmd_set_scene_locked(ir2, "S03", {"locked": True})
        save_composition("proj-r2", ir2)

        from video_director import director_agent, matcher as matcher_mod
        import video_director.llm_provider as lp
        monkeypatch.setattr(lp, "get_llm", lambda: None)
        monkeypatch.setattr(lp, "_provider_cache", None)
        monkeypatch.setattr("video_director.vision.analyze_assets",
                            lambda vv, *a, **k: vv)
        monkeypatch.setattr("video_director.audio.scan_library", lambda: {})

        def fake_match(scenes, assets, llm_call=None, **kw):
            # S01/S02 按顺序给 vid-1（S03 被剔除，锁定）
            return {s["id"]: "vid-1" for s in scenes}
        monkeypatch.setattr(matcher_mod, "match_assets", fake_match)
        # stock 全成功（下载一个文件）→ S02 不再 missing → 生成段不进 → 旧回填不触发
        monkeypatch.setattr("video_director.stock.auto_download_for_scenes",
                            lambda *a, **k: {
                                "files": [], "metadata": {},
                                "warnings": []})
        monkeypatch.setattr("video_director.assets.build_manifest",
                            lambda videos, images, asset_metadata=None: (
                                {"vid-1": type("R", (), {
                                    "id": "vid-1", "type": "video", "file": "a.mp4",
                                    "duration": 10.0})()},
                                ["vid-1"]),
                            )
        state = {
            "project_id": "proj-r2",
            "narration_scenes": [
                {"id": "S01", "text": "a", "start": 0.0, "duration": 4.0},
                {"id": "S02", "text": "b", "start": 4.0, "duration": 4.0},
                {"id": "S03", "text": "c", "start": 8.0, "duration": 4.0},
            ],
            "assets": {}, "asset_order": [], "assignments": {},
            "scene_plan": {"scenes": []}, "constraints": {},
            "bundle": {"assets": {"asset_refs": []}},
            "video_files": ["__placeholder__.mp4"], "image_files": [],
            "style_contract": __import__(
                "video_director.style", fromlist=["StyleContract"]
            ).StyleContract(style_id="documentary-calm"),
        }
        spec = {"match_materials_to_script": True}
        r = director_agent._tool_material(spec, state)
        # R2 修复前：回填在 generated_files 块内不执行 → S03 丢失 → coverage FAIL
        assert state["assignments"].get("S03") == "vid-1", (
            f"锁定 S03 绑定丢失（R2 回归）: {state.get('assignments')}")
        assert r.get("ok"), r.get("error")
