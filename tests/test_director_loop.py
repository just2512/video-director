"""最终方案 §15/§16/§17/§54/§55/§五十七 Director 闭环契约测试（新 director.py）。

覆盖：
- §16 build_contexts 四件套：System/Project/Selection/Actions
- §17 goal + steps 结构（LLM 输出契约由 resolver 测，这里测 direct() 返回形态）
- §54 闭环：direct 返回 {source, goal, plan, executed, verify_ok}
- §55 verify：执行后重读系统（watermark/preserve 落地验证）
- §五十七 多意图一条指令出多步
"""

import json

import pytest

from video_director import config, editor
from video_director import director as vd_director


@pytest.fixture()
def pid(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path / "composer")
    project_id = "dir-close-01"
    base = config.project_dir(project_id)
    config.init_project_dirs(base)
    (base / "plans" / "composition.json").write_text(json.dumps({
        "project": {"title": "闭环", "width": 1080, "height": 1920,
                    "fps": 30, "duration": 6.0},
        "scenes": [
            {"id": "scene-1", "start": 0.0, "duration": 3.0, "text": "一",
             "layers": [{"type": "video", "start": 0.0, "duration": 3.0,
                         "asset_id": "a.mp4", "source_in": 0.0, "source_out": 3.0,
                         "speed": 1.0}]},
            {"id": "scene-2", "start": 3.0, "duration": 3.0, "text": "二",
             "layers": [{"type": "image", "start": 0.0, "duration": 3.0,
                         "asset_id": "b.jpg"}]},
        ],
        "audio": [], "watermarks": [],
    }), encoding="utf-8")
    return project_id


def _no_llm(monkeypatch):
    monkeypatch.setattr(vd_director, "_llm_plan", lambda i, c: None)


class TestContexts:
    def test_build_contexts_four_pieces(self, pid):
        """§16 四件套指纹。"""
        ctx = vd_director.build_contexts(pid, {"type": "layer", "id": "scene-1#0"})
        for key in ("system", "project", "selection", "actions"):
            assert key in ctx, f"缺 {key}"
        assert ctx["system"]["build_id"], "§42 build_id"
        assert ctx["project"]["project"]["duration"] == 6.0
        assert ctx["selection"]["type"] == "layer"
        assert ctx["actions"]["context"] == "layer.video"
        # §16 Action Context 只发当前可执行动作
        assert all(a.get("available") is not None or "available" in a
                   for a in ctx["actions"]["actions"])

    def test_project_unreadable_honest(self):
        with pytest.raises(vd_director.DirectorError) as e:
            vd_director.build_contexts("no-such-project")
        assert e.value.code == "PROJECT_NOT_READABLE"


class TestClosedLoop:
    def test_direct_returns_loop_fingerprint(self, pid, monkeypatch):
        """§54 返回结构：source/goal/plan/executed/verify_ok。"""
        _no_llm(monkeypatch)
        out = vd_director.direct(pid, "加右下角水印")
        for key in ("source", "goal", "plan", "executed", "verify_ok"):
            assert key in out, f"缺 {key}"
        assert out["source"] == "rule"
        assert out["goal"]
        assert out["executed"], "必须真实执行"
        ir = editor.load_composition(pid)
        assert ir.watermarks, "水印必须真实落地"

    def test_verify_reloads_system(self, pid, monkeypatch):
        """§55 执行后重新读系统。"""
        _no_llm(monkeypatch)
        out = vd_director.direct(pid, "保留原声")
        step = out["executed"][0]
        assert step["ok"]
        assert "verify" in step, "成功步骤必须带 verify（重读系统）"
        assert step["verify"]["ok"] is True
        ir = editor.load_composition(pid)
        assert any(t.type.value == "original" for t in ir.audio)

    def test_multi_intent_one_instruction(self, pid, monkeypatch):
        """§五十七 多意图：「保留原声，加右上角水印，把 BGM 调小」→ 3 步。"""
        _no_llm(monkeypatch)
        out = vd_director.direct(pid, "保留原声，加右上角水印，把 BGM 调小。")
        tools = [e["tool"] for e in out["executed"]]
        assert "audio.original.preserve" in tools
        assert "watermark.add" in tools
        assert "music.volume" in tools
        assert out["verify_ok"] is True

    def test_rule_plan_splits_conjunctions_without_splitting_list_targets(self):
        """并列自然语言指令转成多个动作；列表顿号留在同一语义片段。"""
        plan = vd_director._rule_plan(
            "保留原声，同时加右下角水印，然后把音乐调小",
            {"type": "project", "id": ""},
        )
        assert plan is not None
        actions = [step["action"] for step in plan["steps"]]
        assert "audio.preserve" in actions
        assert "watermark.add" in actions
        assert "music.volume" in actions

        listed = vd_director._rule_plan("只保留第 2、4、7 段原声", {"type": "project", "id": ""})
        assert listed is not None
        assert any(step["action"] == "audio.preserve" for step in listed["steps"])

    def test_rule_plan_keeps_watermark_opacity_from_following_clause(self):
        plan = vd_director._rule_plan("右下角加我的水印，透明度 70%", {"type": "project", "id": ""})
        assert plan is not None
        watermark = next(step for step in plan["steps"] if step["action"] == "watermark.add")
        assert watermark["params"]["opacity"] == 0.7

    def test_unknown_intent_honest(self, pid, monkeypatch):
        """不编造：无法解析的动作给 MISSING_TARGET 而非瞎执行。"""
        _no_llm(monkeypatch)
        with pytest.raises(vd_director.DirectorError) as e:
            vd_director.direct(pid, "让这个视频火上热搜")
        assert e.value.code == "MISSING_TARGET"

    def test_empty_instruction_rejected(self, pid):
        with pytest.raises(vd_director.DirectorError):
            vd_director.direct(pid, "  ")

    def test_selection_volume_targeting(self, pid, monkeypatch):
        """「保留这个视频的现场声音，但压到 15%」→ preserve + volume 0.15。"""
        _no_llm(monkeypatch)
        out = vd_director.direct(pid, "保留这个视频的现场声音，但压到 15%",
                                 selection={"type": "layer", "id": "scene-1#0"})
        plan = out["plan"][0]
        assert plan["action"] == "audio.preserve"
        assert abs(plan["params"].get("volume", 0) - 0.15) < 1e-9


class TestLLMPlanContract:
    def test_llm_plan_goal_steps_shape(self, pid):
        """§17 LLM 输出契约：goal + steps[{action,target,params}]。
        用 monkeypatch 的 provider 验证解析层（不依赖真实 key）。"""
        class FakeProvider:
            def generate(self, prompt, system=""):
                return ('{"goal": "调整声音和品牌", "steps": ['
                        '{"action": "audio.preserve", "target": "current_selection", '
                        '"params": {"volume": 0.2}},'
                        '{"action": "watermark.add", "target": "project", '
                        '"params": {"anchor": "bottom_right", "opacity": 0.7}}]}')
        import video_director.llm_provider as lp
        orig = lp.get_llm
        lp.get_llm = lambda: FakeProvider()
        try:
            ctx = vd_director.build_contexts(pid)
            plan = vd_director._llm_plan("加 logo 保留原声", ctx)
            assert plan and plan["goal"]
            assert len(plan["steps"]) == 2
            assert plan["steps"][0]["action"] == "audio.preserve"
            assert plan["steps"][1]["target"] == "project"
        finally:
            lp.get_llm = orig
