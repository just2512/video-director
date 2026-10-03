"""最终方案 §23 VisualSearchIntent 契约测试。

覆盖：
- §23 结构指纹：scene_id/script/subject/visual_concepts/actions/environment/
  shot/required_duration/ratio/asset_types
- 规则兜底产可检索英文词（「很多人每天都很忙」→ busy person/money/finance，
  不是「赚钱」也不是中文长句）
- LLM 契约（FakeProvider 验证 JSON 解析层）
- search_queries 多组词（§四十回退链逐组尝试）
- §24 零评分（intent 无数值评分字段）
"""

import pytest

from video_director import visual_search_intent as vsi


class TestIntentStructure:
    def test_intent_fingerprint_23(self):
        i = vsi.build_intent("scene-3", "很多人每天都很忙，却还是赚不到钱",
                             4.5, "9:16", "problem", use_llm=False)
        for key in ("scene_id", "script", "subject", "visual_concepts",
                    "actions", "environment", "shot", "required_duration",
                    "ratio", "asset_types"):
            assert key in i, f"§23 字段缺失: {key}"
        assert i["required_duration"] == 4.5
        assert i["ratio"] == "9:16"
        assert set(i["asset_types"]) == {"video", "image"}
        assert i["source"] == "rule"

    def test_no_numeric_score_24(self):
        """§24 零评分：intent 不含 0.91 式数值匹配。"""
        i = vsi.build_intent("s", "文案", 3.0, "9:16", use_llm=False)
        for k, v in i.items():
            assert not (isinstance(v, float) and 0 < v < 1), f"§24 禁止评分字段 {k}={v}"
            if isinstance(v, list):
                for x in v:
                    assert not (isinstance(x, (int, float)) and 0 < x < 1)

    def test_rule_path_english_searchable(self):
        """§23 核心：中文文案 → 英文检索词（Pexels 可搜），非整句非中文。"""
        i = vsi.build_intent("scene-3", "很多人每天都很忙，却还是赚不到钱",
                             4.5, "9:16", "problem", use_llm=False)
        queries = vsi.search_queries(i)
        assert len(queries) >= 3, "多组检索词（§四十逐组回退的前提）"
        for q in queries:
            assert q, "检索词非空"
            assert not any("一" <= ch <= "鿿" for ch in q), \
                f"规则兜底检索词必须英文可检索（Pexels/Pixabay 中文不可搜）: {q}"
        assert "busy person" in queries or any("busy" in q for q in queries)

    def test_llm_intent_parsed(self):
        """§23 LLM 优先：JSON 契约解析（FakeProvider；失败走规则）。"""
        class FakeProvider:
            def generate(self, prompt, system=""):
                return ('{"subject": "busy office worker", '
                        '"visual_concepts": ["busy office worker", "working late"], '
                        '"actions": ["typing"], "environment": ["office"], '
                        '"shot": "closeup"}')
        import video_director.llm_provider as lp
        orig = lp.get_llm
        lp.get_llm = lambda: FakeProvider()
        try:
            i = vsi.build_intent("s9", "深夜加班", 3.0, "16:9", use_llm=True)
            assert i["source"] == "llm"
            assert i["subject"] == "busy office worker"
            assert i["visual_concepts"][0] == "busy office worker"
            assert i["shot"] == "closeup"
        finally:
            lp.get_llm = orig

    def test_llm_failure_falls_to_rule(self):
        class BrokenProvider:
            def generate(self, prompt, system=""):
                raise RuntimeError("network down")
        import video_director.llm_provider as lp
        orig = lp.get_llm
        lp.get_llm = lambda: BrokenProvider()
        try:
            i = vsi.build_intent("s9", "深夜加班", 3.0, "9:16", use_llm=True)
            assert i["source"] == "rule", "LLM 故障必须规则兜底（不编造）"
        finally:
            lp.get_llm = orig

    def test_empty_script_minimal_intent(self):
        """空文案：最小 intent（回退链仍需形态），不抛。"""
        i = vsi.build_intent("s0", "", 2.0, "9:16", use_llm=False)
        assert i["scene_id"] == "s0"
        assert i["visual_concepts"]

    def test_queries_priority_order(self):
        """§23 检索词优先级：concepts 在前，subject 组合词其后。"""
        i = vsi.build_intent("s", "地铁上刷手机的通勤族", 3.0, "9:16", use_llm=False)
        qs = vsi.search_queries(i)
        assert qs, "至少一组检索词"
        assert len(qs) <= 4, "检索词上限（不无限尝试）"
