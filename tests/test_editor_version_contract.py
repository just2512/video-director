"""编辑器版本契约：前端拿到的版本号必须与冲突检查同源。

背景（Playwright 实测 2026-10-15）：`/api/editor/composition` 曾返回
`len(revs) + 1`，而命令冲突检查用 `editor.composition_version()`（取
`max(len(revs)+1, 持久化版本)`）。撤销历史累积后两者脱节 → 前端拿旧版本号
发命令 → 每条命令 CONFLICT（实测 9 vs 11），编辑器对所有保存操作失能，
只有整页刷新才恢复。
"""
from __future__ import annotations

import copy
from pathlib import Path

from tests.fixtures.fixture_data import RECIPE_PROJECT
from video_director.editor import (
    composition_version,
    execute_command,
    load_composition,
    load_revisions,
    save_composition,
)
from video_director.models import CompositionIR

ROOT = Path(__file__).resolve().parents[1]


def _project(tmp_path: Path, monkeypatch, name: str = "version-contract"):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path))
    plan_dir = tmp_path / name / "plans"
    plan_dir.mkdir(parents=True)
    data = copy.deepcopy(RECIPE_PROJECT["composition"])
    data["project"]["duration"] = 10.0
    save_composition(name, CompositionIR(**data))
    (plan_dir / "revisions.jsonl").write_text("", encoding="utf-8")
    return name


def _apply(pid: str, command: str, target: str, payload: dict, version: int):
    return execute_command(pid, {
        "command": command, "targetId": target, "payload": payload,
        "expectedVersion": version,
    })


def test_composition_api_version_is_authoritative(tmp_path, monkeypatch):
    """源码契约：IR 与冲突版本必须从同一项目锁定快照取得。"""
    src = (ROOT / "webui" / "server.py").read_text(encoding="utf-8")
    idx = src.index('route == "/api/editor/composition"')
    branch = src[idx:idx + 6000]
    assert "load_composition_snapshot(pid)" in branch, (
        "composition 端点必须从同一锁定快照读取 IR、revision 和版本"
    )
    assert '"version": composition_version' in branch, (
        "composition 端点必须回传该快照的权威版本——不能重新读取或自算"
    )
    assert '"version": len(revs) + 1' not in branch, (
        "composition 端点仍自算版本号，会与冲突检查脱节"
    )


def test_version_survives_undo_and_stays_monotonic(tmp_path, monkeypatch):
    """撤销不得回退版本号（ABA），且始终 >= 修订条数 + 1。"""
    pid = _project(tmp_path, monkeypatch)
    versions = [composition_version(pid)]

    v = composition_version(pid)
    for i in range(3):
        r = _apply(pid, "set_duration", "S01", {"duration": 6.0 + i}, v)
        assert r.status == "APPLIED", r.errors
        v = composition_version(pid)
        versions.append(v)

    undo = execute_command(pid, {"command": "undo", "targetId": "", "payload": {},
                                 "expectedVersion": v})
    assert undo.status == "APPLIED", undo.errors
    after_undo = composition_version(pid)

    # 单调不减：撤销不得把版本号降回去（否则迟到的旧请求会被误判为最新）
    assert after_undo >= max(versions), f"版本号回退: {versions} → {after_undo}"
    # 权威版本不得低于修订条数推导值
    assert after_undo >= len(load_revisions(pid)) + 1


def test_command_accepts_version_reported_by_composition_version(tmp_path, monkeypatch):
    """前端拿 composition_version() 的值发命令，必须不被 CONFLICT 拒绝。"""
    pid = _project(tmp_path, monkeypatch, name="version-accept")
    for i in range(3):
        v = composition_version(pid)
        r = _apply(pid, "set_duration", "S01", {"duration": 6.0 + i}, v)
        assert r.status == "APPLIED", r.errors
    # 经过若干次编辑后，权威版本仍是可用的乐观锁令牌
    v = composition_version(pid)
    r = _apply(pid, "set_duration", "S01", {"duration": 9.0}, v)
    assert r.status == "APPLIED", r.errors
    assert abs(float(load_composition(pid).scenes[0].duration) - 9.0) < 1e-6
