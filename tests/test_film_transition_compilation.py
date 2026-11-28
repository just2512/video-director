# -*- coding: utf-8 -*-
"""2026-11-28 第二批回归锁：CompositionIR transition_out 编译为 Film 转场。

此前迁移时 4 个 crossfade 只写警告（「尚未编译为 Film 转场」），成片实际
没有转场。现在 crossfade/fade 编译为：下一场景首个视觉片段提前重叠时长
开始 + fade="重叠时长,0"，与 test_visual_crossfade_overlap 的既有合法
形态一致（Film 视觉重叠规则：前段 fade_out=0、后段 fade_in=重叠时长）。
"""
import json

from video_director import film_document as fd
from video_director.models import CompositionIR


def _project_with_transitions(tmp_path, monkeypatch, transition_type="crossfade", duration=0.5):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    from video_director import config
    project_id = "transition-compile-test"
    assets = config.project_dir(project_id) / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-a", "file": "assets/still.png", "type": "image"},
        {"id": "asset-b", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")
    raw = {
        "project": {"width": 320, "height": 180, "fps": 30, "duration": 8},
        "scenes": [
            {"id": "S01", "start": 0, "duration": 4, "text": "甲",
             "transition_out": {"type": transition_type, "duration": duration},
             "layers": [{"type": "image", "asset_id": "asset-a", "start": 0, "duration": 4}]},
            {"id": "S02", "start": 4, "duration": 4, "text": "乙",
             "layers": [{"type": "image", "asset_id": "asset-b", "start": 0, "duration": 4}]},
        ],
    }
    ir = CompositionIR.model_validate(raw)
    result = fd.build_film_from_composition(project_id, ir)
    return project_id, result


class TestTransitionCompilation:
    def test_crossfade_compiles_to_overlap_and_fade(self, tmp_path, monkeypatch):
        _, result = _project_with_transitions(tmp_path, monkeypatch)
        clips = [
            clip for track in result["document"]["tracks"] for clip in track["clips"]
            if str(clip["attrs"].get("data-source-scene-id") or "") == "S02"
        ]
        assert clips, "S02 场景必须有可迁移画面片段"
        first = min(clips, key=lambda c: float(c["attrs"]["at"]))
        # S02 原始 start=4，crossfade 0.5s → at=3.5 且 fade="0.5,0"
        assert abs(float(first["attrs"]["at"]) - 3.5) < 0.001
        assert fd._parse_fade(first["attrs"]["fade"]) == (0.5, 0.0)

    def test_compiled_warning_is_explicit(self, tmp_path, monkeypatch):
        _, result = _project_with_transitions(tmp_path, monkeypatch)
        joined = "\n".join(result["warnings"])
        assert "已编译为 Film 转场" in joined
        assert "尚未编译为 Film 转场" not in joined

    def test_fade_type_also_compiles(self, tmp_path, monkeypatch):
        _, result = _project_with_transitions(tmp_path, monkeypatch, transition_type="fade")
        clips = [
            clip for track in result["document"]["tracks"] for clip in track["clips"]
            if str(clip["attrs"].get("data-source-scene-id") or "") == "S02"
        ]
        first = min(clips, key=lambda c: float(c["attrs"]["at"]))
        assert abs(float(first["attrs"]["at"]) - 3.5) < 0.001

    def test_cut_transition_not_compiled(self, tmp_path, monkeypatch):
        _, result = _project_with_transitions(tmp_path, monkeypatch, transition_type="cut", duration=0.5)
        # cut 不编译：S02 保持原位 at=4
        clips = [
            clip for track in result["document"]["tracks"] for clip in track["clips"]
            if str(clip["attrs"].get("data-source-scene-id") or "") == "S02"
        ]
        first = min(clips, key=lambda c: float(c["attrs"]["at"]))
        assert abs(float(first["attrs"]["at"]) - 4.0) < 0.001
        assert not first["attrs"].get("fade")

    def test_unknown_type_reports_not_compiled(self, tmp_path, monkeypatch):
        _, result = _project_with_transitions(tmp_path, monkeypatch, transition_type="wipe", duration=0.5)
        joined = "\n".join(result["warnings"])
        assert "无法编译为 Film 转场" in joined

    def test_last_scene_no_next_reports_not_compiled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
        from video_director import config
        project_id = "transition-last-scene"
        assets = config.project_dir(project_id) / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "still.png").write_bytes(b"not-decoded")
        (assets / "manifest.json").write_text(json.dumps([
            {"id": "asset-a", "file": "assets/still.png", "type": "image"},
        ]), encoding="utf-8")
        raw = {
            "project": {"width": 320, "height": 180, "fps": 30, "duration": 4},
            "scenes": [
                {"id": "S01", "start": 0, "duration": 4, "text": "甲",
                 "transition_out": {"type": "crossfade", "duration": 0.5},
                 "layers": [{"type": "image", "asset_id": "asset-a", "start": 0, "duration": 4}]},
            ],
        }
        ir = CompositionIR.model_validate(raw)
        result = fd.build_film_from_composition(project_id, ir)
        joined = "\n".join(result["warnings"])
        # 末场景没有下一场景：如实报无法编译，不静默丢弃
        assert "无法编译为 Film 转场" in joined

    def test_roundtrip_preserves_compiled_fade(self, tmp_path, monkeypatch):
        _, result = _project_with_transitions(tmp_path, monkeypatch)
        text = fd.serialize_film(result["document"])
        roundtrip = fd.parse_film(text)
        fades = [
            clip["attrs"]["fade"]
            for track in roundtrip["tracks"] for clip in track["clips"]
            if clip["attrs"].get("fade")
        ]
        assert fades == ["0.5,0"]

    def test_overlap_is_film_legal(self, tmp_path, monkeypatch):
        """编译出的重叠必须满足 Film 视觉重叠合法性（前段 fade_out=0、后段 fade_in=重叠时长）。

        _clip_overlap_is_valid 是同轨规则：只检查同一轨道内按 at 排序的相邻片段。
        """
        _, result = _project_with_transitions(tmp_path, monkeypatch)
        doc = result["document"]
        checked = 0
        for track in doc["tracks"]:
            visual = sorted(
                [clip for clip in track["clips"]
                 if clip["tag"] in {"video", "img", "iframe"}],
                key=lambda c: float(c["attrs"]["at"]),
            )
            for prev, cur in zip(visual, visual[1:]):
                prev_end = fd.clip_end(prev)
                at = float(cur["attrs"]["at"])
                if prev_end is not None and at < prev_end - 0.001:
                    overlap = prev_end - at
                    assert fd._clip_overlap_is_valid(prev, cur, overlap), (
                        f"编译的重叠不合法: {prev['id']} → {cur['id']} @ {track['id']}"
                    )
                    checked += 1
        # 主轨 S01→S02 重叠 0.5s；S02 片段若被分流到独立轨也各自成轨检查
        assert checked >= 1, "应该至少有一处编译出的重叠"
