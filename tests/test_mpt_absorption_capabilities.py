"""MPT 吸收方案 §86-§94 大批能力契约测试（Stock/TTS/WorkflowJob/GenerationRequest）。

覆盖：
    - §86 CAP-ASSET-003~005：Stock 三 Provider 商业安全矩阵 + AssetCandidate 四态零评分
    - §87 CAP-AUDIO-001~006：TTSProvider Registry + VoiceProfile + NarrationConfig silent
    - §40 WorkflowJob 八态状态机 + errorCode 分类
    - §29 GenerationRequest 首帧/末帧诚实报错 + §39 CacheKey 指纹
    全部不依赖真实网络/ffmpeg（provider 缺 key 诚实降级是断言点本身）。
"""

from __future__ import annotations

import pytest

from video_director import stock, voice, workflow_job
from video_director.stock import (
    INCOMPATIBLE, MISSING_REQUIREMENT, SUPPORTED, UNKNOWN, StockAsset, StockError,
    resolve_candidates, search_stock,
)


class TestStockProviders:
    def test_policy_matrix_scheme_25(self):
        """§25 商业安全矩阵：Pexels CLEAR+attribution，Pixabay 禁 standalone，
        Coverr 默认 REVIEW。"""
        assert stock.PROVIDER_POLICY["pexels"]["default_status"] == "CLEAR"
        assert stock.PROVIDER_POLICY["pexels"]["requires_attribution"] is True
        assert stock.PROVIDER_POLICY["pixabay"]["default_status"] == "CLEAR"
        assert stock.PROVIDER_POLICY["pixabay"]["standalone_export_allowed"] is False
        assert stock.PROVIDER_POLICY["coverr"]["default_status"] == "REVIEW"

    def test_missing_key_honest_unavailable(self, monkeypatch):
        """§86：三 provider 全缺 key → STOCK_UNAVAILABLE（不伪造结果）。"""
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
        monkeypatch.delenv("COVERR_API_KEY", raising=False)
        with pytest.raises(StockError) as ei:
            search_stock("coffee")
        assert ei.value.code == "STOCK_UNAVAILABLE"

    def test_candidate_four_state_no_score(self):
        """§23：四态零评分——候选不含相似度分数。"""
        a = StockAsset(provider="pexels", source_id="1", source_page="p", media_url="u",
                       width=1080, height=1920, duration=14, commercial_status="CLEAR")
        b = StockAsset(provider="pixabay", source_id="2", source_page="p", media_url="u",
                       width=1920, height=1080, duration=8, commercial_status="CLEAR")
        cands = resolve_candidates([a, b], target_ratio="9:16", target_duration=10)
        by_provider = {c.asset.provider: c for c in cands}
        assert by_provider["pexels"].status == SUPPORTED
        assert by_provider["pixabay"].status == INCOMPATIBLE
        # 零评分：to_dict 不含 score/rating/probability/rank
        d = by_provider["pexels"].to_dict()
        assert not any(k in d for k in ("score", "rating", "probability", "rank"))

    def test_review_status_candidate_is_unknown(self):
        """§26：REVIEW/UNKNOWN 素材候选 status=UNKNOWN（不自动进商用）。"""
        a = StockAsset(provider="coverr", source_id="x", source_page="p", media_url="u",
                       width=1080, height=1920, duration=10, commercial_status="REVIEW")
        cands = resolve_candidates([a], target_ratio="9:16")
        assert cands[0].status == UNKNOWN


class TestVoiceProviders:
    def test_tts_provider_registry(self):
        """§13：TTSProvider Registry——edge-tts 无 key 可用，其余需 key。"""
        provs = {p["id"]: p for p in voice.list_providers()}
        assert provs["edge-tts"]["requires_key"] is False
        assert provs["edge-tts"]["available"] is True
        assert provs["gemini"]["requires_key"] is True

    def test_voice_profile_fields(self):
        """§14：VoiceProfile 含 provider/voiceId/name/language/gender/style。"""
        v = voice.get_voice("zh-CN-XiaoxiaoNeural")
        assert v["provider"] == "edge-tts"
        assert v["voiceId"] == "zh-CN-XiaoxiaoNeural"
        assert v["name"] == "晓晓"
        assert v["language"] == "zh-CN"
        assert {"gender", "style"} <= set(v)

    def test_narration_config_silent(self):
        """§17：silent 模式 No TTS No fake，但 subtitle/BGM/SFX/visual 仍允许。"""
        cfg = voice.make_narration_config("silent")
        assert voice.is_silent_legal(cfg)["tts_skipped"] is True
        assert voice.is_silent_legal(cfg)["subtitle_allowed"] is True
        assert voice.is_silent_legal(cfg)["bgm_allowed"] is True

    def test_narration_config_invalid_mode(self):
        with pytest.raises(ValueError):
            voice.make_narration_config("fake_mode")


class TestWorkflowJob:
    def test_eight_states(self):
        """§40：八态状态机。"""
        assert set(s.value for s in workflow_job.WorkflowJobStatus) == {
            "QUEUED", "RUNNING", "WAITING_INPUT", "WAITING_CONFIRMATION",
            "RETRYABLE_FAILURE", "FAILED", "COMPLETED", "CANCELLED",
        }

    def test_legacy_mapping(self):
        assert workflow_job.from_legacy("waiting") == "QUEUED"
        assert workflow_job.from_legacy("running") == "RUNNING"
        assert workflow_job.from_legacy("done") == "COMPLETED"
        assert workflow_job.from_legacy("failed") == "FAILED"
        assert workflow_job.from_legacy("failed", retryable=True) == "RETRYABLE_FAILURE"

    def test_error_code_classification(self):
        assert workflow_job._error_code("ffmpeg 超时(600s)") == "CAPABILITY_TIMEOUT"
        assert workflow_job._error_code("provider 不可用") == "PROVIDER_UNAVAILABLE"
        assert workflow_job._error_code("xxx") == "GENERIC_ERROR"

    def test_normalize_job(self):
        j = workflow_job.normalize_job({
            "job_id": "j1", "kind": "series_episode", "status": "done",
            "error": None, "spec": {},
        })
        assert j["status"] == "COMPLETED"
        assert "currentCapability" in j and "errorCode" in j and "retryable" in j


class TestGenerationRequest:
    def test_first_frame_unavailable_honest(self, tmp_path, monkeypatch):
        """§29：video 生成带首帧 → FIRST_FRAME_UNSUPPORTED（provider 协议不支持）。"""
        from video_director import config, generation
        monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
        with pytest.raises(generation.GenerationError) as ei:
            generation.generate("p", "test", gen_type="video", first_frame="frame0")
        assert ei.value.code == "FIRST_FRAME_UNSUPPORTED"

    def test_input_fingerprint_includes_reference(self, tmp_path, monkeypatch):
        """§39 CacheKey：reference/首末帧进入 input_fingerprint（指纹差异）。"""
        from video_director import config, generation
        monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
        # graphic 类型确定性（不依赖 provider key）
        r1 = generation.generate("p1", "同一提示词", gen_type="graphic")
        r2 = generation.generate("p2", "同一提示词", gen_type="graphic",
                                 reference="style:editorial")
        assert r1[0].input_fingerprint != r2[0].input_fingerprint