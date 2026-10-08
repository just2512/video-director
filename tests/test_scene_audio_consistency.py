"""场景结构编辑（move/delete/duplicate）与音频轨一致性对抗测试。

背景（2026-10-09 对抗性核查发现）：_cmd_move/_cmd_delete/_cmd_duplicate/
_cmd_reorder 重排场景 start 时从不调整 ir.audio。PR #9 起 original 轨按
每个 video layer 分段入轨（数量与场景/镜头同量级），场景重排/删除后
音频仍挂在旧时间点——预览与渲染全部错位。此文件锁定「场景结构编辑必须
同步音频轨时间」契约。
"""

import pytest

from video_director import config, editor
from video_director.models import CompositionIR


def _ir_with_audio() -> CompositionIR:
    """2 场景 + narration 2 段 + original 轨绑定各场景视频层。"""
    return CompositionIR.model_validate({
        "project": {"width": 1080, "height": 1920, "fps": 30, "duration": 8.0},
        "scenes": [
            {"id": "s1", "start": 0.0, "duration": 4.0, "text": "一",
             "layers": [{"type": "video", "duration": 4.0, "asset_id": "vid-a",
                         "source_in": 0.0, "source_out": 4.0, "speed": 1.0}]},
            {"id": "s2", "start": 4.0, "duration": 4.0, "text": "二",
             "layers": [{"type": "video", "duration": 4.0, "asset_id": "vid-b",
                         "source_in": 0.0, "source_out": 4.0, "speed": 1.0}]},
        ],
        "audio": [
            {"type": "narration", "start": 0.0, "duration": 4.0},
            {"type": "narration", "start": 4.0, "duration": 4.0},
            {"type": "original", "asset_id": "vid-a", "start": 0.0, "duration": 4.0,
             "source_in": 0.0, "source_out": 4.0},
            {"type": "original", "asset_id": "vid-b", "start": 4.0, "duration": 4.0,
             "source_in": 0.0, "source_out": 4.0},
        ],
    })


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    pid = "vd-scene-audio-consistency"
    editor.save_composition(pid, _ir_with_audio())
    return pid


def test_delete_scene_drops_orphaned_audio(project):
    """删除场景必须清掉该场景时间窗内的音频轨（否则渲染出幽灵原声）。"""
    editor.execute_command(project, {
        "command": "delete", "targetId": "s1", "author": "test"})
    ir = editor.load_composition(project)
    assert len(ir.scenes) == 1
    # 只剩 s2：任何 start >= 剩余总时长(4.0) 的轨、以及绑定被删场景素材的
    # original 轨都必须消失
    total = ir.project.duration
    for t in ir.audio:
        assert t.start < total + 0.01, f"轨 {t.type}@{t.start} 超出剩余时间轴 {total}"
        if t.type.value == "original":
            assert t.asset_id != "vid-a", "被删场景的素材原声轨残留"


def test_move_scene_repositions_audio(project):
    """场景重排后音频轨必须跟随新场景顺序（原 s2 的声音不能还播在 4.0s）。"""
    editor.execute_command(project, {
        "command": "move", "targetId": "s2", "author": "test",
        "payload": {"toIndex": 0}})
    ir = editor.load_composition(project)
    assert [sc.id for sc in ir.scenes] == ["s2", "s1"]
    # s2 现在在 0-4s，其 original 轨（vid-b）应从 0.0 开始；
    # s1 的（vid-a）应挪到 4.0。按素材对齐断言。
    vid_b_starts = [t.start for t in ir.audio
                    if t.type.value == "original" and t.asset_id == "vid-b"]
    vid_a_starts = [t.start for t in ir.audio
                    if t.type.value == "original" and t.asset_id == "vid-a"]
    assert vid_b_starts and abs(vid_b_starts[0] - 0.0) < 0.01, \
        f"s2 原声轨应在新场景起点 0，实际 {vid_b_starts}"
    assert vid_a_starts and abs(vid_a_starts[0] - 4.0) < 0.01, \
        f"s1 原声轨应在新场景起点 4，实际 {vid_a_starts}"
    # narration 同步重排（按场景顺序逐段重铺）
    nar_starts = sorted(t.start for t in ir.audio
                         if t.type.value == "narration")
    assert nar_starts == [0.0, 4.0], f"narration 应重铺 [0,4]，实际 {nar_starts}"


def test_duplicate_scene_clones_audio_segment(project):
    """复制场景后新场景窗口内的音频轨也要有对应段（或至少不错位）。"""
    editor.execute_command(project, {
        "command": "duplicate", "targetId": "s1", "author": "test"})
    ir = editor.load_composition(project)
    assert len(ir.scenes) == 3
    total = ir.project.duration
    for t in ir.audio:
        assert t.start + t.duration <= total + 0.01, \
            f"轨 {t.type}@{t.start}+{t.duration} 超出复制后时间轴 {total}"
