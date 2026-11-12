from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from video_director import config, project_manifest


def _project(monkeypatch, tmp_path: Path, project_id: str) -> Path:
    root = tmp_path / "composer"
    monkeypatch.setattr(config, "composer_storage_root", lambda: root)
    base = config.project_dir(project_id)
    (base / "assets").mkdir(parents=True, exist_ok=True)
    return base


def test_concurrent_manifest_merges_do_not_drop_entries(monkeypatch, tmp_path):
    project_id = "manifest-concurrent-01"
    base = _project(monkeypatch, tmp_path, project_id)
    barrier = threading.Barrier(3)
    errors = []

    def add(asset_id: str):
        try:
            barrier.wait(timeout=5)
            project_manifest.merge_project_manifest_entries(project_id, [{
                "id": asset_id,
                "type": "image",
                "file": f"assets/generated/{asset_id}.png",
            }])
        except Exception as exc:
            errors.append(exc)

    workers = [
        threading.Thread(target=add, args=("generated-a",), daemon=True),
        threading.Thread(target=add, args=("generated-b",), daemon=True),
    ]
    for worker in workers:
        worker.start()
    barrier.wait(timeout=5)
    for worker in workers:
        worker.join(timeout=10)
        assert not worker.is_alive(), "manifest transaction did not release its project lock"

    assert errors == []
    manifest = json.loads((base / "assets" / "manifest.json").read_text(encoding="utf-8"))
    assert {entry["id"] for entry in manifest} == {"generated-a", "generated-b"}


def test_corrupt_manifest_is_not_treated_as_empty_or_overwritten(monkeypatch, tmp_path):
    project_id = "manifest-corrupt-01"
    base = _project(monkeypatch, tmp_path, project_id)
    path = base / "assets" / "manifest.json"
    original = "{ definitely not valid json"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(project_manifest.ProjectManifestError, match="JSON 损坏"):
        project_manifest.merge_project_manifest_entries(project_id, [{
            "id": "new-asset",
            "type": "image",
            "file": "assets/generated/new-asset.png",
        }])

    assert path.read_text(encoding="utf-8") == original
    assert list(path.parent.glob(".manifest.json.*.tmp")) == []


def test_manifest_merge_is_idempotent_and_replace_is_explicit(monkeypatch, tmp_path):
    project_id = "manifest-merge-01"
    base = _project(monkeypatch, tmp_path, project_id)
    first = {"id": "matte-source", "type": "video", "file": "old.mp4"}
    replacement = {"id": "matte-source", "type": "video", "file": "new.mp4"}

    assert project_manifest.merge_project_manifest_entries(project_id, [first]) == ["matte-source"]
    assert project_manifest.merge_project_manifest_entries(project_id, [first]) == []
    assert project_manifest.merge_project_manifest_entries(
        project_id, [replacement], replace_ids={"matte-source"},
    ) == ["matte-source"]

    manifest = json.loads((base / "assets" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == [replacement]


def test_assets_symlink_is_rejected_for_manifest_read_and_write(monkeypatch, tmp_path):
    project_id = "manifest-assets-link-01"
    base = _project(monkeypatch, tmp_path, project_id)
    outside = tmp_path / "outside-assets"
    outside.mkdir()
    (outside / "manifest.json").write_text('[{"id":"outside"}]', encoding="utf-8")
    assets = base / "assets"
    assets.rmdir()
    try:
        assets.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")
    with pytest.raises(project_manifest.ProjectManifestError, match="assets 目录是符号链接"):
        project_manifest.read_project_manifest(project_id)
    with pytest.raises(project_manifest.ProjectManifestError, match="assets 目录是符号链接"):
        project_manifest.merge_project_manifest_entries(project_id, [{"id":"new"}])
    assert (outside / "manifest.json").read_text(encoding="utf-8") == '[{"id":"outside"}]'


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs Windows developer mode; covered by Linux CI")
def test_project_root_symlink_is_rejected_before_project_lock_side_effect(monkeypatch, tmp_path):
    root = tmp_path / "composer"
    target = tmp_path / "real-project"
    target.mkdir()
    (target / "assets").mkdir()
    root.mkdir()
    (root / "manifest-root-link").symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(config, "composer_storage_root", lambda: root)
    with pytest.raises(project_manifest.ProjectManifestError, match="项目目录不是可信的普通目录"):
        project_manifest.read_project_manifest("manifest-root-link")
    assert not (target / "plans").exists()
