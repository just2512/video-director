# -*- coding: utf-8 -*-
"""2026-10-19 系统性修复回归：意图时长预算（执行点+校验点）+ vision 三守卫。

方案文档节点 08/20/21 指纹：时长是确定性校验器职责，不能只活在 prompt 提示里。
"""
import json
from pathlib import Path

from video_director import narration, qc


def _ir(minimal=True):
    from video_director.models import CompositionIR
    return CompositionIR.model_validate({
        "project": {"fps": 30, "width": 1080, "height": 1920, "duration": 10.0},
        "scenes": [{"id": "S1", "start": 0.0, "duration": 10.0, "text": "x",
                    "layers": [{"type": "gap", "start": 0.0, "duration": 10.0}]}],
        "assets": [], "audio": [], "subtitle_track": {"cues": []},
    })


class TestDurationBudget:
    """意图时长预算填充：40+ 短了要补足；超上限诚实警告；无意图不动。"""

    def _scenes(self, total, n=2):
        per = total / n
        return [{"id": f"S{i+1:02d}", "text": f"句{i+1}", "start": round(i * per, 3),
                 "duration": round(per, 3)} for i in range(n)]

    def test_floor_gap_padded_evenly(self, tmp_path, monkeypatch):
        """「40+」实际 36s → 均摊 2s/场景 → 新总长 40s，start 重排连续。"""
        scenes = self._scenes(36.0)
        words = [{"text": "a", "start": 1.0, "end": 1.5},
                 {"text": "b", "start": 19.0, "end": 19.4}]
        # 挡住真实 ffmpeg：静音补齐走 mock，同时模拟 padded 产物落盘走通主路径
        calls = {}
        def fake_run(cmd):
            calls.update(cmd=cmd)
            out = [x for x in cmd if str(x).endswith(".padded.mp3")]
            if out:
                Path(out[0]).write_bytes(b"P" * 2048)
        monkeypatch.setattr("video_director.renderer._run", fake_run)
        out_audio, out_words, out_scenes, warns = narration.apply_duration_budget(
            str(tmp_path / "narration.mp3"), words, scenes, tmp_path, "40+")
        assert abs(sum(s["duration"] for s in out_scenes) - 40.0) < 0.01
        assert out_scenes[1]["start"] == out_scenes[0]["duration"]
        # apad 命令带 whole_dur=40
        assert any("apad" in str(x) for x in calls.get("cmd", []))
        assert any("40.000" in str(x) for x in calls.get("cmd", []))
        # 词时间轴场景内偏移不变、全局平移正确
        assert abs(out_words[0]["start"] - 1.0) < 0.01
        assert abs(out_words[1]["start"] - 21.0) < 0.01
        assert warns and "40" in warns[0]

    def test_ceiling_overshoot_warns_only(self, tmp_path):
        """「30-」实际 36s：TTS 不可压缩，只警告不改时间轴。"""
        scenes = self._scenes(36.0)
        _, _, out_scenes, warns = narration.apply_duration_budget(
            str(tmp_path / "x.mp3"), [], scenes, tmp_path, "30-")
        assert abs(sum(s["duration"] for s in out_scenes) - 36.0) < 0.01
        assert warns and "超过目标上限" in warns[0]

    def test_no_intent_no_change(self, tmp_path):
        """无意图值：原样返回。"""
        scenes = self._scenes(36.0)
        _, _, out_scenes, warns = narration.apply_duration_budget(
            str(tmp_path / "x.mp3"), [], scenes, tmp_path, "")
        assert abs(sum(s["duration"] for s in out_scenes) - 36.0) < 0.01
        assert warns == []

    def test_garbage_intent_ignored_with_warning(self, tmp_path):
        scenes = self._scenes(36.0)
        _, _, out_scenes, warns = narration.apply_duration_budget(
            str(tmp_path / "x.mp3"), [], scenes, tmp_path, "大概四十几秒")
        assert abs(sum(s["duration"] for s in out_scenes) - 36.0) < 0.01


class TestQcDurationVsIntent:
    """QC 校验点：成片时长 vs 用户意图目标，FAIL 走既有 REJECT 通道。"""

    def test_floor_met_passes(self, tmp_path, monkeypatch):
        from video_director import prober
        fake_final = tmp_path / "final.mp4"
        fake_final.write_bytes(b"x")
        monkeypatch.setattr(prober, "probe_media", lambda p: type(
            "P", (), {"has_video_stream": True, "width": 1080, "height": 1920,
                      "codec": "h264", "fps": 30.0, "duration": 41.0,
                      "has_audio_stream": True})())
        report = qc.run_qc(_ir(), str(fake_final), {}, 41.0,
                           expect_subtitle=False, expect_audio=False,
                           intent_duration="40+")
        check = next(c for c in report.checks if c["check"] == "duration_vs_intent")
        assert check["result"] == "PASS"

    def test_floor_missed_rejects(self, tmp_path, monkeypatch):
        from video_director import prober
        fake_final = tmp_path / "final.mp4"
        fake_final.write_bytes(b"x")
        monkeypatch.setattr(prober, "probe_media", lambda p: type(
            "P", (), {"has_video_stream": True, "width": 1080, "height": 1920,
                      "codec": "h264", "fps": 30.0, "duration": 38.0, "has_audio_stream": True})())
        report = qc.run_qc(_ir(), str(fake_final), {}, 38.0,
                           expect_subtitle=False, expect_audio=False,
                           intent_duration="40+")
        check = next(c for c in report.checks if c["check"] == "duration_vs_intent")
        assert check["result"] == "FAIL"
        assert report.verdict == "REJECT"

    def test_no_intent_no_check(self, tmp_path, monkeypatch):
        from video_director import prober
        fake_final = tmp_path / "final.mp4"
        fake_final.write_bytes(b"x")
        monkeypatch.setattr(prober, "probe_media", lambda p: type(
            "P", (), {"has_video_stream": True, "width": 1080, "height": 1920,
                      "codec": "h264", "fps": 30.0, "duration": 5.0, "has_audio_stream": True})())
        report = qc.run_qc(_ir(), str(fake_final), {}, 5.0,
                           expect_subtitle=False, expect_audio=False)
        assert all(c["check"] != "duration_vs_intent" for c in report.checks)
