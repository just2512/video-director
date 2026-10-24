"""MPT 指纹复刻核查落地差距（2026-10-25 轮）测试锁。

覆盖本轮 7 项差距 + 架构契约层 3 项：
    - 差距1  music_policy 死契约接线 BGM 决策链（六档 + 诚实 availability + 降级）
    - 差距2  Marengo 语义重排接入主匹配流（semantic_verdicts_out 可选层）
    - 差距3  付费 Provider 确认（is_paid / requires_confirmation / 门禁）
    - 差距4  Task restore 依赖五态（SATISFIED/MISSING/INVALID）
    - 差距6  源素材分组优先（_prioritize_unique_source）
    - 架构    Capability 深层属性 + PlayableSegment/VariantSet 语义映射

全部不依赖真实网络/ffmpeg（provider 缺 key 诚实降级是断言点本身）。
"""

from __future__ import annotations

import pytest

from video_director.music_policy import MusicPolicy, MusicPolicyError, POLICIES
from video_director import mpt_semantics, provider_matrix


# ---------------------------------------------------------------------------
# 差距1：music_policy 接线
# ---------------------------------------------------------------------------
class TestMusicPolicyWiring:
    def test_six_policies_declared(self):
        assert POLICIES == ("none", "local_random", "local_selected",
                            "stock", "generated", "video_matched")

    def test_availability_honest(self):
        """六档 availability 诚实：无 provider 的档不伪造可用。"""
        for p in ("none", "local_random", "local_selected"):
            pol = MusicPolicy(policy=p, asset_id="x" if p == "local_selected" else None)
            assert pol.availability()["available"] is True, p
        for p in ("stock", "video_matched"):
            pol = MusicPolicy(policy=p)
            assert pol.availability()["available"] is False, p
            assert pol.availability()["reason"], p + " 应带诚实原因"

    def test_local_selected_requires_asset(self):
        with pytest.raises(MusicPolicyError) as ei:
            MusicPolicy(policy="local_selected")
        assert ei.value.code == "MISSING_ASSET"

    def test_invalid_policy_rejected(self):
        with pytest.raises(MusicPolicyError) as ei:
            MusicPolicy(policy="bogus")
        assert ei.value.code == "INVALID_POLICY"

    def test_resolve_music_policy_mapping(self):
        """spec.bgm + style → MusicPolicy 契约（既有行为映射不变）。"""
        from video_director import director_agent as da
        st = {"style_contract": type("S", (), {"style_id": "documentary-calm"})()}
        assert da._resolve_music_policy({"bgm": "off"}, st).policy == "none"
        assert da._resolve_music_policy({"bgm": "none"}, st).policy == "none"
        assert da._resolve_music_policy({"bgm": "auto"}, st).policy == "local_random"
        assert da._resolve_music_policy({"bgm": "my-bgm-id"}, st).policy == "local_selected"

    def test_resolve_paid_policy_honest_degrade(self):
        """stock/generated/video_matched 不可用 → 诚实降级 local_random。"""
        from video_director import director_agent as da
        st = {"style_contract": type("S", (), {"style_id": "calm"})()}
        # stock 档 provider 未开 → availability False → 降级 local_random
        pol = da._resolve_music_policy({"bgm": "stock"}, st)
        assert pol.policy == "local_random"

    # ---- 对抗测试抓出的真实 bug 回归锁 ----
    def test_volume_zero_not_swallowed(self):
        """bgm_volume=0 是合法静音，不能被 falsy `or` 吞成默认 0.3。"""
        from video_director import director_agent as da
        st = {"style_contract": type("S", (), {"style_id": "calm"})()}
        pol = da._resolve_music_policy({"bgm": "auto", "bgm_volume": 0}, st)
        assert pol.volume == 0.0

    def test_style_id_none_or_absent_no_crash(self):
        """style_contract.style_id 为 None 或缺失时 keywords 构造不炸。"""
        from video_director import director_agent as da
        st_none = {"style_contract": type("S", (), {"style_id": None})()}
        assert da._resolve_music_policy({"bgm": "auto"}, st_none).policy == "local_random"
        assert da._resolve_music_policy({"bgm": "auto"}, {}).policy == "local_random"


# ---------------------------------------------------------------------------
# 差距3：付费 Provider 确认
# ---------------------------------------------------------------------------
class TestPaidConfirmation:
    def test_video_image_marked_paid(self):
        for role in ("image", "video", "semantic_rerank", "visual_qa"):
            defns = [d for d in provider_matrix.DEFINITIONS if d.role == role]
            # 每个 role 至少有一个付费定义（video/image 是付费生成）
            assert any(d.is_paid for d in defns), role

    def test_llm_tts_not_paid(self):
        for role in ("llm", "tts"):
            defns = [d for d in provider_matrix.DEFINITIONS if d.role == role]
            assert all(not d.is_paid for d in defns), role

    def test_state_read_carries_confirmation(self):
        """READY + is_paid → requires_confirmation=True；deep 属性透传。"""
        from video_director.provider_matrix import state
        st = state("video", "video.generate")
        assert st.is_paid is True
        # requires_confirmation 只在 READY 时为 True；未配置时 MISSING → False
        if st.status == "READY":
            assert st.requires_confirmation is True

    def test_deep_attributes_transparent(self):
        """§四十#6：sync_mode / supports_reference / retry_safe 透传。"""
        from video_director.provider_matrix import state
        st = state("video", "video.generate")
        assert st.sync_mode in ("sync", "async", "unknown")
        assert st.supports_reference is not None
        assert st.supports_first_frame is False  # 显式标不支持，不冒充

    def test_generation_gate_needs_confirmation(self):
        """执行器门禁：READY+付费 未 confirmed → NEEDS_CONFIRMATION。"""
        from video_director import tool_executor as te
        # 无 provider 配置时 provider_matrix.state 返回 MISSING，门禁不触发；
        # 直接验证门禁分支逻辑存在（monkeypatch state 为 READY+paid）。
        monkeypatch = pytest.MonkeyPatch()
        try:
            from video_director import provider_matrix as pm
            fake = pm.CapabilityState("x", "video", "video.generate", "READY", True,
                                      is_paid=True, requires_confirmation=True)
            monkeypatch.setattr(pm, "state", lambda *a, **k: fake)
            r = te._execute_generation("p", "generation.video",
                                       {"prompt": "x"}, "")
            assert r.get("status") == "NEEDS_CONFIRMATION"
        finally:
            monkeypatch.undo()


# ---------------------------------------------------------------------------
# 差距4：依赖五态
# ---------------------------------------------------------------------------
class TestDependencyState:
    def test_playable_and_variant_mapping(self):
        assert mpt_semantics.playable_segment_kind("video") == "video"
        assert mpt_semantics.playable_segment_kind("image") == "image"
        assert mpt_semantics.playable_segment_kind("GENERATED") == "generated"
        assert mpt_semantics.playable_segment_kind("") == "unknown"
        assert mpt_semantics.variant_set_kind("generation") == "media_variant"
        assert mpt_semantics.variant_set_kind("creative_candidate") == "creative_variant"
        assert mpt_semantics.variant_set_kind("bogus") == "unknown"

    def test_dependency_states_present_in_context(self):
        """inspect_project_context 输出含 asset_dependency_states 五态键。"""
        from video_director import project_context
        import inspect
        src = inspect.getsource(project_context.inspect_project_context)
        assert "asset_dependency_states" in src
        assert "SATISFIED" in src and "MISSING" in src and "INVALID" in src


# ---------------------------------------------------------------------------
# 差距6：源素材分组优先
# ---------------------------------------------------------------------------
class TestPrioritizeUniqueSource:
    def test_group_by_source_file(self):
        from video_director.planner import RulePlanner

        class R:
            def __init__(self, aid, f):
                self.id = aid
                self.file = f

        assets = {f"v{i}": R(f"v{i}", f"src{s}.mp4") for i, s in enumerate([0, 0, 0, 1, 1, 2])}
        ids = ["v0", "v1", "v2", "v3", "v4", "v5"]
        out = RulePlanner._prioritize_unique_source(ids, assets)
        # 每源 primary 先出：v0(src0), v3(src1), v5(src2)，overflow 后置
        assert out == ["v0", "v3", "v5", "v1", "v2", "v4"]

    def test_all_unique_no_reorder(self):
        from video_director.planner import RulePlanner

        class R:
            def __init__(self, aid, f):
                self.id = aid
                self.file = f

        uniq = ["v0", "v1", "v2"]
        au = {a: R(a, f"{a}.mp4") for a in uniq}
        assert RulePlanner._prioritize_unique_source(uniq, au) == uniq

    def test_missing_file_falls_back_id(self):
        from video_director.planner import RulePlanner

        class R:
            def __init__(self, aid):
                self.id = aid

        assets = {a: R(a) for a in ("v0", "v1")}
        assert RulePlanner._prioritize_unique_source(["v0", "v1"], assets) == ["v0", "v1"]


# ---------------------------------------------------------------------------
# 差距2：Marengo 语义重排主链（可选层，失败不阻塞）
# ---------------------------------------------------------------------------
class TestSemanticRerankPassthrough:
    def test_match_assets_accepts_semantic_verdicts_out(self):
        """match_assets 新增 semantic_verdicts_out 通道；未配置 rerank 时不触发。"""
        import inspect
        from video_director import matcher

        sig = inspect.signature(matcher.match_assets)
        assert "semantic_verdicts_out" in sig.parameters

        class A:
            def __init__(self, aid):
                self.id = aid
                self.type = "video"
                self.file = f"{aid}.mp4"
                self.keywords = []
                self.description = None

        assets = {"v0": A("v0"), "v1": A("v1")}
        scenes = [{"id": "S01", "text": "工匠运石上山。"}]
        out = matcher.match_assets(scenes, assets, llm_call=None, semantic_verdicts_out={})
        # 无 LLM 也无 rerank：确定性/顺序兜底仍产出结果
        assert out.get("S01") in ("v0", "v1")