from __future__ import annotations

import json
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
