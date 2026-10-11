"""§46 Undo/Redo：revision 快照恢复式撤销/重做（前端顶栏按钮的后端支撑）。

契约：
- 每条 revision 附 ir_before/ir_after 快照（undo/redo 恢复点）
- undo → 恢复最后未撤销 revision 的 ir_before；redo → 恢复其 ir_after
- 旧 revision 无快照 → UNDO_UNAVAILABLE 诚实报错（不猜不 fake）
- undo_stack.json 记被撤销的 revision_id（redo 弹栈）
"""

import json
from pathlib import Path

import pytest

from video_director import config, editor
from tests.fixtures.fixture_data import RECIPE_PROJECT


@pytest.fixture()
def pid(tmp_path, monkeypatch):
    """用既有 RECIPE_PROJECT fixture 构造可编辑项目（与 closeout 测试同法）。"""
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    data = RECIPE_PROJECT
    p = data["project_id"]
    base = tmp_path / p
    (base / "plans").mkdir(parents=True)
    (base / "assets").mkdir(parents=True)
    from video_director.models import CompositionIR
    ir = CompositionIR(**data["composition"])
    (base / "plans" / "composition.json").write_text(
        json.dumps(json.loads(ir.model_dump_json()), ensure_ascii=False), encoding="utf-8")
    return p


def _scene_duration(p: str) -> float:
    return editor.load_composition(p).scenes[0].duration


def test_revision_has_ir_snapshots(pid):
    """§46：命令后 revision 必须附 ir_before/ir_after 快照。"""
    scene = editor.load_composition(pid).scenes[0]
    before = scene.duration
    r = editor.execute_command(pid, {
        "command": "set_duration", "targetId": scene.id,
        "payload": {"duration": 2.5}})
    assert r.status == "APPLIED"
    revs = editor.load_revisions(pid)
    assert revs, "revision 应已落盘"
    assert revs[-1].get("ir_before"), "revision 必须附 ir_before 快照"
    assert revs[-1].get("ir_after"), "revision 必须附 ir_after 快照"
    assert abs(revs[-1]["ir_before"]["scenes"][0]["duration"] - before) < 0.01


def test_undo_restores_previous_state(pid):
    scene = editor.load_composition(pid).scenes[0]
    before = scene.duration
    editor.execute_command(pid, {
        "command": "set_duration", "targetId": scene.id,
        "payload": {"duration": 2.5}})
    assert abs(_scene_duration(pid) - 2.5) < 0.01

    r_undo = editor.execute_command(pid, {"command": "undo"})
    assert r_undo.status == "APPLIED"
    assert abs(_scene_duration(pid) - before) < 0.01, "undo 后应回到 set_duration 前状态"


def test_redo_reapplies_state(pid):
    scene = editor.load_composition(pid).scenes[0]
    editor.execute_command(pid, {
        "command": "set_duration", "targetId": scene.id,
        "payload": {"duration": 2.5}})
    after = _scene_duration(pid)
    assert editor.execute_command(pid, {"command": "undo"}).status == "APPLIED"
    r_redo = editor.execute_command(pid, {"command": "redo"})
    assert r_redo.status == "APPLIED"
    assert abs(_scene_duration(pid) - after) < 0.01, "redo 后应回到 set_duration 后状态"


def test_undo_without_history_rejected(pid):
    r = editor.execute_command(pid, {"command": "undo"})
    assert r.status == "REJECTED"
    assert r.errors[0]["code"] == "NOTHING_TO_UNDO"


def test_redo_without_undo_rejected(pid):
    r = editor.execute_command(pid, {"command": "redo"})
    assert r.status == "REJECTED"
    assert r.errors[0]["code"] == "NOTHING_TO_REDO"


def test_undo_on_snapshotless_revision_honest(pid):
    """旧 revision（Undo 上线前）无快照 → UNDO_UNAVAILABLE 诚实报错。"""
    scene = editor.load_composition(pid).scenes[0]
    editor.execute_command(pid, {
        "command": "set_duration", "targetId": scene.id,
        "payload": {"duration": 2.5}})
    # 手工抹掉快照（模拟历史记录）
    p = editor._revisions_path(pid)
    lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in lines:
        r.pop("ir_before", None)
        r.pop("ir_after", None)
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in lines) + "\n",
                 encoding="utf-8")
    r = editor.execute_command(pid, {"command": "undo"})
    assert r.status == "REJECTED"
    assert r.errors[0]["code"] == "UNDO_UNAVAILABLE"



def test_new_edit_after_undo_invalidates_redo_branch(pid):
    """A divergent edit must never be overwritten by redo of the abandoned branch."""
    scene_id = editor.load_composition(pid).scenes[0].id
    initial = _scene_duration(pid)
    first = editor.execute_command(pid, {
        "command": "set_duration", "targetId": scene_id,
        "payload": {"duration": 2.5}})
    assert first.status == "APPLIED"
    assert editor.execute_command(pid, {"command": "undo"}).status == "APPLIED"
    assert abs(_scene_duration(pid) - initial) < 0.01

    branch_edit = editor.execute_command(pid, {
        "command": "set_duration", "targetId": scene_id,
        "payload": {"duration": 3.5}})
    assert branch_edit.status == "APPLIED"
    assert abs(_scene_duration(pid) - 3.5) < 0.01

    redo = editor.execute_command(pid, {"command": "redo"})
    assert redo.status == "REJECTED"
    assert redo.errors[0]["code"] == "NOTHING_TO_REDO"
    assert abs(_scene_duration(pid) - 3.5) < 0.01

    # Undo operates on the current branch, not the stale revision that was abandoned.
    undo_branch_edit = editor.execute_command(pid, {"command": "undo"})
    assert undo_branch_edit.status == "APPLIED"
    assert abs(_scene_duration(pid) - initial) < 0.01


def test_undo_advances_version_and_rejects_pre_undo_browser_write(pid):
    """Undo changes content but must also advance the optimistic concurrency token."""
    scene_id = editor.load_composition(pid).scenes[0].id
    first_version = editor.composition_version(pid)
    applied = editor.execute_command(pid, {
        "command": "set_duration", "targetId": scene_id,
        "expectedVersion": first_version,
        "payload": {"duration": 2.5}})
    assert applied.status == "APPLIED"
    assert applied.composition_version == first_version + 1

    undone = editor.execute_command(pid, {
        "command": "undo", "expectedVersion": applied.composition_version})
    assert undone.status == "APPLIED"
    assert undone.composition_version == applied.composition_version + 1

    stale = editor.execute_command(pid, {
        "command": "set_duration", "targetId": scene_id,
        "expectedVersion": applied.composition_version,
        "payload": {"duration": 3.25}})
    assert stale.status == "REJECTED"
    assert stale.errors[0]["code"] == "CONFLICT"
    assert editor.composition_version(pid) == undone.composition_version
