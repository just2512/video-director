"""MPT 吸收方案收口批契约测试：§31/§33 SubtitleTrack / §26-27 Crawler /
§16 上传配音 / §37 version.create / §48 SSE 总线。

全部零网络/零 ffmpeg（本地确定性验证契约本身）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_director import config, crawler, editor, voice_upload
from video_director.models import CompositionIR, SubtitleCue, SubtitleTrack


@pytest.fixture()
def pid(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    from tests.fixtures.fixture_data import RECIPE_PROJECT
    data = RECIPE_PROJECT
    pid = data["project_id"]
    base = tmp_path / pid
    (base / "plans").mkdir(parents=True)
    (base / "assets").mkdir(parents=True)
    ir = CompositionIR(**data["composition"])
    (base / "plans" / "composition.json").write_text(
        json.dumps(json.loads(ir.model_dump_json()), ensure_ascii=False), encoding="utf-8")
    return pid


class TestSubtitleTrack:
    def test_track_in_ir_and_word_mode(self):
        """§31 字幕=Track 入 IR；§33 mode=word 逐词。"""
        st = SubtitleTrack(mode="word", source="tts_word_boundary",
                           cues=[SubtitleCue(start=0.0, end=0.4, text="效率"),
                                 SubtitleCue(start=0.4, end=0.9, text="提升")])
        assert st.mode == "word"
        assert len(st.cues) == 2
        assert "subtitle_track" in CompositionIR.model_fields

    def test_track_rejects_bad_cue(self):
        with pytest.raises(Exception):
            SubtitleTrack(cues=[SubtitleCue(start=1.0, end=0.5, text="倒序")])

    def test_track_rejects_bad_mode(self):
        with pytest.raises(Exception):
            SubtitleTrack(mode="char")


class TestCrawler:
    def test_candidate_source_is_not_asset(self):
        """§27：Crawler 返回 CandidateSource（含 licenseEvidence），非 Asset。"""
        c = crawler.CandidateSource(url="https://example.com/v.mp4", title="x",
                                    source_site="example.com", license_evidence="CC0")
        d = c.to_dict()
        assert {"url", "title", "sourceSite", "licenseEvidence",
                "commercialStatus", "mediaType"} <= set(d)

    def test_rights_resolver_matrix(self):
        """§26：License Evidence → Commercial Status（CC0→CLEAR，
        all rights reserved→BLOCKED，无证据→UNKNOWN）。"""
        c0 = crawler.CandidateSource("u", "t", "s", "CC0 public domain")
        assert crawler.resolve_rights(c0) == "CLEAR"
        cb = crawler.CandidateSource("u", "t", "s", "All Rights Reserved 版权所有")
        assert crawler.resolve_rights(cb) == "BLOCKED"
        cu = crawler.CandidateSource("u", "t", "s", "")
        assert crawler.resolve_rights(cu) == "UNKNOWN"

    def test_only_clear_auto_publishes(self):
        """§26：仅 CLEAR 可自动进 Commercial Publish。"""
        clear = crawler.CandidateSource("u", "t", "s", "CC0", commercial_status="CLEAR")
        review = crawler.CandidateSource("u", "t", "s", "cc by", commercial_status="REVIEW")
        assert crawler.can_auto_publish(clear) is True
        assert crawler.can_auto_publish(review) is False

    def test_confirm_use_blocked_rejected(self):
        blocked = crawler.CandidateSource("u", "t", "s", "all rights reserved",
                                          commercial_status="BLOCKED")
        with pytest.raises(crawler.CrawlerError) as ei:
            crawler.confirm_use(blocked)
        assert ei.value.code == "BLOCKED_SOURCE"


class TestVoiceUpload:
    def test_upload_skips_tts_and_estimates(self, pid, tmp_path, monkeypatch):
        """§16/§12：上传配音 → TTS=SKIP + word timing（ASR 不可用诚实 estimated）。"""
        # ASR 走不通（模型下载重）→ mock 掉，验证估算路径契约
        from video_director.alignment import FasterWhisperProvider
        def _fake_align(self, audio_file, scenes):
            raise RuntimeError("ASR 不可用（测试 mock）")
        monkeypatch.setattr(FasterWhisperProvider, "align", _fake_align)
        # 构造一个最小 wav（RIFF 头 + 静音）
        import struct
        wav = tmp_path / "voice.wav"
        sample_rate = 8000
        n_seconds = 2
        data = b"\x00\x00" * (sample_rate * n_seconds)
        hdr = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + \
            struct.pack("<IHHIIHH", 16, 1, 1, sample_rate,
                        sample_rate * 2, 2, 16) + b"data" + struct.pack("<I", len(data))
        wav.write_bytes(hdr + data)
        out = voice_upload.import_uploaded_voice(
            pid, str(wav), scene_texts=["第一句。", "第二句。"])
        assert out["tts_skipped"] is True          # §12 SKIP
        assert out["source"] == "estimated"         # ASR 不可用诚实标注
        assert abs(out["duration"] - 2.0) < 0.3
        assert len(out["narration_scenes"]) == 2

    def test_missing_file_honest(self, pid):
        with pytest.raises(voice_upload.VoiceUploadError) as ei:
            voice_upload.import_uploaded_voice(pid, "no/such/file.wav")
        assert ei.value.code == "FILE_NOT_FOUND"

    def test_silent_mode_legal(self):
        """§17：silent 合法工作流（Image Story/Music Video 等）。"""
        s = voice_upload.silent_mode_legal()
        assert s["tts_skipped"] and s["bgm_allowed"] and s["subtitle_allowed"]
        assert "Image Story" in s["legal_workflows"]


class TestProjectMgmt:
    def test_version_create_snapshot(self, pid):
        """§37 Variant System：version.create → 项目目录快照 -v1。"""
        from video_director import tool_executor
        res = tool_executor.execute_tool(pid, {"tool": "version.create", "payload": {}})
        assert res["ok"], res.get("error")
        assert res["result"]["variant_id"] == f"{pid}-v1"
        assert res["verify"]["variant_exists"] is True
        # 快照里 composition.json 存在（真快照非空目录）
        from video_director import config as cfg
        snap = cfg.project_dir(f"{pid}-v1") / "plans" / "composition.json"
        assert snap.is_file()

    def test_project_save_reports_version(self, pid):
        from video_director import tool_executor
        res = tool_executor.execute_tool(pid, {"tool": "project.save", "payload": {}})
        assert res["ok"]
        assert res["result"]["version"] >= 1

    def test_project_create_ratio(self, pid):
        from video_director import tool_executor
        res = tool_executor.execute_tool(pid, {"tool": "project.create",
                                               "payload": {"ratio": "16:9"}})
        assert res["ok"] and res["result"]["width"] == 1920
        bad = tool_executor.execute_tool(pid, {"tool": "project.create",
                                               "payload": {"ratio": "21:9"}})
        assert not bad["ok"]


class TestSSEBus:
    def test_publish_without_subscribers_noop(self):
        """§48：无订阅者广播不炸（事件即丢）。"""
        import webui.server as srv
        srv.sse_publish("test", {"x": 1})  # 无订阅者 → 静默
        q = srv.sse_subscribe()
        srv.sse_publish("director", {"phase": "planning"})
        msg = q.get(timeout=1)
        payload = json.loads(msg)
        assert payload["event"] == "director"
        assert payload["data"]["phase"] == "planning"
        srv.sse_unsubscribe(q)
