from __future__ import annotations

import os
from pathlib import Path

import pytest

# Windows 创建 symlink 需开发者模式/管理员权限（WinError 1314），本地跳过由 Linux CI 覆盖
requires_symlink = pytest.mark.skipif(os.name == "nt", reason="symlink needs Windows developer mode")

from video_director import config, project_lock


@requires_symlink
def test_project_dir_rejects_preexisting_directory_symlink_before_creating_children(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    storage.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (storage / "linked-project").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)

    with pytest.raises(ValueError, match="不能是符号链接|不是可信的普通目录"):
        config.project_dir("linked-project")
    with pytest.raises(ValueError, match="不能是符号链接|不是可信的普通目录"):
        with project_lock.project_lock("linked-project"):
            pass
    assert not (outside / "plans").exists()


def test_project_dir_rejects_path_traversal_before_creating_directory(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    storage.mkdir()
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)

    for project_id in ("../outside", "nested/project", "", ".", "a\\b"):
        with pytest.raises(ValueError, match="项目编号格式无效"):
            config.project_dir(project_id)
    assert list(storage.iterdir()) == []


def test_project_dir_rejects_existing_regular_file(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    storage.mkdir()
    (storage / "not-a-directory").write_text("sentinel", encoding="utf-8")
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)

    with pytest.raises(ValueError, match="不是可信的普通目录"):
        config.project_dir("not-a-directory")
    assert (storage / "not-a-directory").read_text(encoding="utf-8") == "sentinel"

@requires_symlink
def test_project_lock_rejects_plans_directory_symlink(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    storage.mkdir()
    outside = tmp_path / "outside-plans"
    outside.mkdir()
    (storage / "linked-project").mkdir()
    (storage / "linked-project" / "plans").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)

    with pytest.raises(ValueError, match="plans 目录不能是符号链接|plans 路径不是可信的普通目录"):
        with project_lock.project_lock("linked-project"):
            pass
    assert not (outside / "project.lock").exists()


@requires_symlink
def test_project_lock_rejects_lock_file_symlink_without_touching_target(tmp_path, monkeypatch):
    storage = tmp_path / "composer"
    storage.mkdir()
    project = storage / "linked-lock"
    plans = project / "plans"
    plans.mkdir(parents=True)
    outside = tmp_path / "outside-lock"
    outside.write_bytes(b"keep")
    (plans / "project.lock").symlink_to(outside)
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)

    with pytest.raises(ValueError, match="锁文件不能是符号链接"):
        with project_lock.project_lock("linked-lock"):
            pass
    assert outside.read_bytes() == b"keep"


@requires_symlink
def test_project_dir_creates_missing_storage_root_without_following_root_symlink(tmp_path, monkeypatch):
    storage = tmp_path / "not-created" / "composer"
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)

    project = config.project_dir("fresh-project")
    assert project.is_dir()
    assert project.parent == storage

    outside = tmp_path / "outside-storage"
    outside.mkdir()
    linked_root = tmp_path / "linked-composer"
    linked_root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(config, "composer_storage_root", lambda: linked_root)
    with pytest.raises(ValueError, match="存储根目录不能是符号链接|存储根目录不是可信的普通目录"):
        config.project_dir("must-not-escape")
    assert list(outside.iterdir()) == []
