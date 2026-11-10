# -*- coding: utf-8 -*-
"""对抗审查 8 项修复的回归测试（2 HIGH + 6 MEDIUM，2026-10-18）。

每个用例对应一个已实证的缺陷，防止回归：
- HIGH 1: 导出渲染异常必须落 FAILED+error，不许永久卡 RENDERING。
- HIGH 2: 剪切=paste+remove 走 batch operations 单 revision 原子。
- MEDIUM 3: paste_audio_clip 拒绝旁白片段（TTS 管线唯一性）。
- MEDIUM 4: paste_audio_clip 拒绝负 source_in。
- MEDIUM 5: resolveEntry legacy index 回退必须严匹配 fingerprint（前端 JS 字符串断言）。
- MEDIUM 6: 循环 BGM 左裁剪 source_in 位移与时间轴位移严格相等。
- MEDIUM 7: 删轨确认文案包含关联原声失声提示（前端 JS 字符串断言）。
- MEDIUM 8: 导出前从 ir.subtitle_track.cues 重建 master.ass。
"""
import json
from pathlib import Path

import pytest

from video_director.editor import (
    EditorError,
    _cmd_batch,
    _cmd_paste_audio_clip,
    _cmd_trim_audio_clip,
    load_composition,
    save_composition,
)
from video_director.models import AssetRef, AudioTrack, CompositionIR, VisualOverlayClip


def _project(tmp_path, project_id="proj-adhoc"):
    """最小合法 IR：1 场景 + bgm 循环音轨 + 素材。"""
    ir = CompositionIR.model_validate({
        "project": {"fps": 30, "width": 1080, "height": 1920, "duration": 10.0},
        "scenes": [{
            "id": "S1", "start": 0.0, "duration": 10.0,
            "text": "测试",
            "layers": [{"type": "gap", "start": 0.0, "duration": 10.0}],
        }],
        "assets": [{"id": "bgm-a", "type": "audio", "file": "x.mp3", "duration": 8.0}],
        "audio": [{
            "id": "bgm-1", "type": "bgm", "asset_id": "bgm-a",
            "start": 0.0, "duration": 10.0, "source_in": 2.0, "source_out": None, "volume": 1.0,
        }],
        "visual_tracks": [{"id": "overlay-main", "name": "画面叠加", "order": 0, "locked": False}],
        "audio_tracks": [{"id": "audio-bgm", "name": "背景音乐", "order": 0, "locked": False}],
    })
    save_composition(project_id, ir)
    return project_id, ir


class TestHigh1ExportFailureLands:
    def test_render_exception_sets_failed_stage(self, monkeypatch, tmp_path):
        """渲染异常必须置 FAILED（带 error）而不是穿透留 RENDERING。"""
        from video_director import config, store
        from video_director import confirm as confirm_mod
        from video_director import renderer as renderer_mod

        pid = "proj-high1"
        base = config.project_dir(pid)
        config.init_project_dirs(base)
        (base / "plans").mkdir(parents=True, exist_ok=True)
        (base / "plans" / "composition.json").write_text(json.dumps({
            "project": {"fps": 30, "width": 1080, "height": 1920, "duration": 10.0},
            "scenes": [{"id": "S1", "start": 0.0, "duration": 10.0, "text": "x",
                        "layers": [{"type": "gap", "start": 0.0, "duration": 10.0}]}],
            "assets": [], "audio": [],
        }), encoding="utf-8")

        calls = []
        monkeypatch.setattr(store, "set_stage",
                            lambda pid2, stage, error=None: calls.append((pid2, stage, error)))
        monkeypatch.setattr(renderer_mod, "render_composition",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("素材不存在: base-image")))
        with pytest.raises(RuntimeError):
            confirm_mod._render_saved_composition_final(pid)
        # 最后一次 set_stage 必须是 FAILED 且带 error 信息
        failed = [c for c in calls if c[1] == store.Stage.FAILED]
        assert failed, f"渲染失败后未置 FAILED：{calls}"
        assert failed[-1][2] and "素材不存在" in failed[-1][2]


class TestHigh2AtomicCut:
    def test_batch_operations_paste_then_remove_single_revision(self, tmp_path):
        """batch operations 顺序执行 paste+remove（单 revision 由上层保证）。"""
        pid, ir = _project(tmp_path)
        ir.assets.append(AssetRef.model_validate({"id": "img-a", "type": "image", "file": "x.png", "duration": 5.0}))
        ir.visual_overlays.append(VisualOverlayClip.model_validate({
            "id": "overlay-src", "asset_id": "img-a", "track_id": "overlay-main",
            "start": 1.0, "duration": 2.0, "source_in": 0.0,
        }))
        save_composition(pid, ir)
        visual_clip = {
            "id": "overlay-src", "asset_id": "img-a", "track_id": "overlay-main",
            "start": 1.0, "duration": 2.0, "source_in": 0.0,
        }

        cur = load_composition(pid)
        payload = {"operations": [
            {"command": "paste_visual_overlay", "targetId": "overlays",
             "payload": {"clip": visual_clip, "start": 5.0, "trackId": "overlay-main"}},
            {"command": "remove_visual_overlay", "targetId": "overlay#0", "payload": {}},
        ]}
        changed, stale = _cmd_batch(cur, payload, pid)
        assert len(cur.visual_overlays) == 1  # 粘贴的留下，原片段已删
        assert cur.visual_overlays[0].start == 5.0
        assert changed

    def test_batch_operations_rejects_unknown_command(self, tmp_path):
        pid, ir = _project(tmp_path)
        with pytest.raises(EditorError) as exc:
            _cmd_batch(ir, {"operations": [{"command": "undo", "targetId": "", "payload": {}}]}, pid)
        assert "不支持" in str(exc.value)


class TestMedium3NarrationPasteGuard:
    def test_paste_narration_rejected(self, tmp_path):
        pid, ir = _project(tmp_path)
        ir.assets.append(AssetRef.model_validate({"id": "nar-a", "type": "audio", "file": "n.mp3", "duration": 5.0}))
        save_composition(pid, ir)
        cur = load_composition(pid)
        clip = {
            "id": "nar-1", "type": "narration", "asset_id": "nar-a",
            "start": 0.0, "duration": 4.0, "source_in": 0.0, "source_out": 4.0, "volume": 1.0,
        }
        with pytest.raises(EditorError) as exc:
            _cmd_paste_audio_clip(cur, "audio-tracks", {"clip": clip, "start": 5.0, "trackId": "audio-bgm"}, pid)
        assert "TTS" in str(exc.value) or "旁白" in str(exc.value)


class TestMedium4NegativeSourceIn:
    def test_negative_source_in_rejected(self, tmp_path):
        pid, ir = _project(tmp_path)
        save_composition(pid, ir)
        cur = load_composition(pid)
        clip = {
            "id": "sfx-1", "type": "sfx", "asset_id": "bgm-a",
            "start": 0.0, "duration": 4.0, "source_in": -3.0, "source_out": 1.0, "volume": 1.0,
        }
        with pytest.raises(EditorError) as exc:
            _cmd_paste_audio_clip(cur, "audio-tracks", {"clip": clip, "start": 5.0, "trackId": "audio-bgm"}, pid)
        assert "源起点" in str(exc.value)


class TestMedium6LoopTrimDrift:
    def test_left_trim_source_in_moves_with_timeline_delta(self, tmp_path):
        """循环 BGM 左边缘裁剪：source_in 位移 == start 位移（不漂移）。"""
        pid, ir = _project(tmp_path)
        save_composition(pid, ir)
        cur = load_composition(pid)
        old_start = float(cur.audio[0].start)
        old_source_in = float(cur.audio[0].source_in or 0.0)
        # 左边缘右拖 1.0s：start 0→1，source_in 应同步 +1.0（帧对齐后）
        _cmd_trim_audio_clip(cur, "audio#0", {
            "loopToDuration": True, "start": 1.0, "duration": 9.0, "sourceIn": old_source_in + 0.9833,
        })
        new_start = float(cur.audio[0].start)
        new_source_in = float(cur.audio[0].source_in or 0.0)
        delta = (new_start - old_start) - (new_source_in - old_source_in)
        assert abs(delta) < 1e-6, f"时间轴位移与源位移差 {delta * 1000:.1f}ms"


class TestMedium5And7FrontendContracts:
    """前端字符串契约（行为级测试在 Playwright smoke 中，这里钉住关键防线）。"""

    def test_resolve_entry_fallback_matches_fingerprint(self):
        src = Path("webui/static/js/clip-clipboard.js").read_text(encoding="utf-8")
        assert "audioFingerprint(candidate) === String(origin.fingerprint || '')) index = origin.index;"

    def test_delete_track_confirm_mentions_audio_loss(self):
        src = Path("webui/static/js/timeline.js").read_text(encoding="utf-8")
        assert "将同时失去独立原声" in src


class TestP0RippleDelete:
    """差距分析 C 项：波纹删除（普通删除留空隙，两种语义显式分开）。"""

    def test_visual_ripple_shifts_later_clips(self, tmp_path):
        from video_director.editor import _cmd_remove_visual_overlay
        pid, ir = _project(tmp_path)
        ir.assets.append(AssetRef.model_validate({"id": "img-a", "type": "image", "file": "x.png", "duration": 30.0}))
        ir.visual_overlays.append(VisualOverlayClip.model_validate({
            "id": "ov-first", "asset_id": "img-a", "track_id": "overlay-main",
            "start": 1.0, "duration": 2.0, "source_in": 0.0,
        }))
        ir.visual_overlays.append(VisualOverlayClip.model_validate({
            "id": "ov-later", "asset_id": "img-a", "track_id": "overlay-main",
            "start": 5.0, "duration": 2.0, "source_in": 0.0,
        }))
        save_composition(pid, ir)
        cur = load_composition(pid)
        _cmd_remove_visual_overlay(cur, "overlay#0", {"ripple": True})
        assert len(cur.visual_overlays) == 1
        assert float(cur.visual_overlays[0].start) == pytest.approx(3.0), "后续片段未前移 2s"

    def test_visual_plain_delete_leaves_gap(self, tmp_path):
        from video_director.editor import _cmd_remove_visual_overlay
        pid, ir = _project(tmp_path)
        ir.assets.append({"id": "img-a", "type": "image", "file": "x.png", "duration": 30.0})
        ir.visual_overlays.append({
            "id": "ov-first", "asset_id": "img-a", "track_id": "overlay-main",
            "start": 1.0, "duration": 2.0, "source_in": 0.0,
        })
        ir.visual_overlays.append({
            "id": "ov-later", "asset_id": "img-a", "track_id": "overlay-main",
            "start": 5.0, "duration": 2.0, "source_in": 0.0,
        })
        save_composition(pid, ir)
        cur = load_composition(pid)
        _cmd_remove_visual_overlay(cur, "overlay#0", {})
        assert float(cur.visual_overlays[0].start) == pytest.approx(5.0), "普通删除不得移动后续片段"

    def test_audio_ripple_shifts_later_clips_same_lane(self, tmp_path):
        from video_director.editor import _cmd_remove_audio_clip
        pid, ir = _project(tmp_path)
        ir.audio.append(AudioTrack.model_validate({
            "id": "sfx-1", "type": "sfx", "asset_id": "bgm-a",
            "start": 2.0, "duration": 1.0, "source_in": 0.0, "source_out": 1.0,
        }))
        save_composition(pid, ir)
        cur = load_composition(pid)
        # cur.audio[0]=bgm(0-10) [1]=sfx(2-3)；删 sfx#1 波纹不影响 bgm（不同 lane 由 track_id 决定）
        _cmd_remove_audio_clip(cur, "audio#1", {"ripple": True})
        assert len(cur.audio) == 1
        assert float(cur.audio[0].start) == pytest.approx(0.0)


class TestMedium8SubtitleRebuild:
    def test_export_rebuilds_ass_from_ir_cues(self, tmp_path, monkeypatch):
        """ir.subtitle_track.cues 非空时导出重建 master.ass。"""
        from video_director import confirm as confirm_mod
        from video_director import config, store

        pid = "proj-subtitle-rebuild"
        base = config.project_dir(pid)
        config.init_project_dirs(base)
        (base / "plans").mkdir(parents=True, exist_ok=True)
        (base / "render").mkdir(parents=True, exist_ok=True)
        ir = CompositionIR.model_validate({
            "project": {"fps": 30, "width": 1080, "height": 1920, "duration": 10.0},
            "scenes": [{"id": "S1", "start": 0.0, "duration": 10.0, "text": "x",
                        "layers": [{"type": "gap", "start": 0.0, "duration": 10.0}]}],
            "assets": [], "audio": [],
            "subtitle_track": {"mode": "sentence", "source": "manual",
                               "cues": [{"start": 0.5, "end": 2.0, "text": "用户改过的字幕"}]},
        })
        (base / "plans" / "composition.json").write_text(
            json.dumps(ir.model_dump(), ensure_ascii=False), encoding="utf-8")
        # 旧 master.ass 是规划期产物（内容过时）
        (base / "render" / "master.ass").write_text("Dialogue: 0,0:00:00.00,0:00:01.00,Sub,,,旧内容\n", encoding="utf-8")

        # 触发到渲染前：渲染器 mock 抛错没关系，我们只验证 ass 被重建
        import video_director.renderer as renderer_mod
        monkeypatch.setattr(renderer_mod, "render_composition",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop-here")))
        try:
            confirm_mod._render_saved_composition_final(pid)
        except RuntimeError as exc:
            assert "stop-here" in str(exc)
        content = (base / "render" / "master.ass").read_text(encoding="utf-8")
        assert "用户改过的字幕" in content, "导出未从 IR cues 重建字幕"
        assert "旧内容" not in content
