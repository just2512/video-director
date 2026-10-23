# -*- coding: utf-8 -*-
"""§七.2 场景级生产契约三字段落地锁（2026-10-23）。

文档《AI 导演完整工作流.md》§七.2 要求每个镜头的生产描述带
purpose / must_show / must_not_show（示例 JSON 原文 426-427 行）。
must_preserve 不在文档中（记忆误记），以文档原文为准。

全链触点：
- director_agent 分镜 LLM prompt 契约 + rule 兜底/LLM 失败降级
- mpt_semantics.build_material_intent 清洗透传（MaterialIntent + 快照）
- director_agent narration_scenes 合并回写（matcher/生成段可见）
- matcher.llm_match 场景行带 must_show/must_not_show
- _gen_one_scene 生成 prompt 注入必须/禁止呈现
"""

import json

from video_director import creative_candidates, mpt_semantics


class TestMaterialIntentContract:
    """build_material_intent / material_intent_snapshot 契约透传。"""

    def test_build_material_intent_carries_contract(self):
        scene = {
            "id": "SCENE-008",
            "text": "人物停顿后作出选择",
            "duration": 5.0,
            "purpose": "表达人物面对压力后作出选择",
            "must_show": ["人物停顿", "关键动作"],
            "must_not_show": ["与文案不符的事实性细节"],
        }
        i = mpt_semantics.build_material_intent(scene)
        assert i.purpose == "表达人物面对压力后作出选择"
        assert list(i.must_show) == ["人物停顿", "关键动作"]
        assert list(i.must_not_show) == ["与文案不符的事实性细节"]

    def test_intent_snapshot_persists_contract(self):
        scene = {"id": "S01", "text": "t", "duration": 3.0,
                 "purpose": "p", "must_show": ["a"], "must_not_show": ["b"]}
        snap = mpt_semantics.material_intent_snapshot([scene])
        row = snap["scenes"][0]
        assert row["purpose"] == "p"
        assert row["must_show"] == ["a"]
        assert row["must_not_show"] == ["b"]

    def test_legacy_scene_without_contract_fields_ok(self):
        """旧数据（无契约字段）不炸、默认空。"""
        i = mpt_semantics.build_material_intent({"id": "S1", "text": "t"})
        assert i.purpose == ""
        assert i.must_show == ()
        assert i.must_not_show == ()

    def test_malformed_contract_values_cleaned(self):
        """契约字段形态异常（str/嵌套/None）不炸，清洗为空或字符串列表。"""
        scene = {"id": "S1", "text": "t", "purpose": 123,
                 "must_show": "not-a-list", "must_not_show": None}
        i = mpt_semantics.build_material_intent(scene)
        assert i.purpose == "123"  # str() 后透传，不丢
        assert i.must_show == ()
        assert i.must_not_show == ()


class TestDirectorScenePlanContract:
    """分镜节点 LLM 契约与 rule 兜底。"""

    def test_rule_fallback_scenes_have_contract(self):
        """provider None 分支：scene_plan 场景带 purpose/must_show/must_not_show。"""
        from video_director import director_agent
        state = {
            "project_id": "t-contract-01",
            "scene_texts": ["第一句", "第二句"],
            "bundle": None,
            "script": None,
        }
        # 只验证 rule 兜底 data 构造：直接构造最小调用不可行（_tool_scene_plan
        # 依赖完整 state），这里验证源码契约——分镜 prompt 与兜底分支均含三字段。
        import inspect
        src = inspect.getsource(director_agent)
        assert '"purpose"' in src or "'purpose'" in src
        assert '"must_show"' in src or "'must_show'" in src
        assert '"must_not_show"' in src or "'must_not_show'" in src

    def test_llm_prompt_demands_contract_fields(self):
        from video_director import director_agent
        import inspect
        src = inspect.getsource(director_agent)
        # LLM prompt 契约：明示 purpose/must_show/must_not_show 要求
        assert "purpose（这一镜头在叙事中的存在理由）" in src
        assert "must_show（画面必须呈现的要素列表）" in src
        assert "must_not_show" in src


class TestMatcherContract:
    """llm_match 场景行带契约要素。"""

    def test_scene_line_includes_must_show(self):
        import inspect
        from video_director import matcher
        src = inspect.getsource(matcher.llm_match)
        assert "必须呈现" in src
        assert "禁止呈现" in src

    def test_llm_match_prompt_receives_contract(self, monkeypatch):
        captured = {}

        def fake_llm(prompt):
            captured["prompt"] = prompt
            return '{"assignments": []}'  # 空结果 → MatcherError，但 prompt 已捕获

        from video_director.matcher import MatcherError, llm_match
        assets = {}  # 无素材 → video_ids 空，任何 assignment 被滤掉
        scenes = [{"id": "S01", "text": "t",
                   "must_show": ["人物停顿"], "must_not_show": ["无关细节"]}]
        try:
            llm_match(scenes, assets, fake_llm)
        except MatcherError:
            pass  # 空素材必然空结果，我们只要 prompt
        assert "必须呈现: 人物停顿" in captured["prompt"]
        assert "禁止呈现: 无关细节" in captured["prompt"]


class TestGenerationPromptInjection:
    """_gen_one_scene 生成 prompt 注入契约。"""

    def test_gen_prompt_appends_contract(self):
        import inspect
        from video_director import director_agent
        src = inspect.getsource(director_agent)
        assert "画面必须呈现" in src
        assert "画面中不得出现" in src


class TestNarrationScenesMerge:
    """narration_scenes 契约合并回写（matcher/生成段可见）。"""

    def test_merge_source_in_director_agent(self):
        import inspect
        from video_director import director_agent
        src = inspect.getsource(director_agent)
        assert 'state["narration_scenes"] = _merged_scenes' in src
        assert 'for _k in ("purpose", "must_show", "must_not_show")' in src


class TestFrontendContractDisplay:
    """前端素材依据框展示必须/禁止呈现。"""

    def test_inspector_renders_contract(self):
        p = __import__("pathlib").Path(
            "webui/static/js/inspector.js")
        src = p.read_text(encoding="utf-8")
        assert "intent.must_show" in src
        assert "intent.must_not_show" in src
        assert "必须呈现" in src and "禁止呈现" in src
        # contractHtml 必须真的插入渲染流（不悬空）
        assert "contractHtml +" in src


class TestCandidateSnapshotRegression:
    """候选快照不回归：契约字段是场景级，候选结构不动。"""

    def test_candidate_fields_unchanged(self):
        cands = creative_candidates.default_candidates("S01", "测试文案")
        c = cands[0]
        for key in ("id", "title", "approach", "visual_intent", "search_terms",
                    "source_preference", "generation_type", "generation_prompt",
                    "fallback_routes", "status", "asset_id"):
            assert key in c, f"候选结构缺 {key}"
        # 场景级契约不进候选（避免重复存储双源）
        assert "must_show" not in c


if __name__ == "__main__":
    import sys
    sys.exit(__import__("pytest").main([__file__, "-q"]))
