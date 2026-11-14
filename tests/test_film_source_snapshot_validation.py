"""The Film CLI validates the migration source archive contract."""
from __future__ import annotations

import hashlib
import json

from video_director import config, film_document as fd
from video_director.__main__ import _validate_migration_source_snapshot


def _write_archive(project_id: str, snapshot: dict) -> tuple[object, str]:
    root = config.project_dir(project_id)
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "migration" / "source-composition.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    # write_bytes 保持 LF：Windows write_text 转 CRLF 会让文件哈希 ≠ text 哈希
    archive.write_bytes(text.encode("utf-8"))
    manifest = {
        "format": "video-director-film-migration/1",
        "source_snapshot_path": "migration/source-composition.json",
        "source_snapshot_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    (root / fd.MIGRATION_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    return archive, text


def test_film_validate_accepts_intact_source_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "snapshot-cli-valid"
    snapshot = {"project": {"width": 320, "height": 180}, "variants": [{"id": "alt"}]}
    _write_archive(project_id, snapshot)

    checked, issues = _validate_migration_source_snapshot(project_id)
    assert checked is True
    assert issues == []


def test_film_validate_reports_modified_source_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "snapshot-cli-tampered"
    archive, _ = _write_archive(project_id, {"project": {"width": 320, "height": 180}})
    archive.write_text('{"project":{"width":1,"height":1}}', encoding="utf-8")

    checked, issues = _validate_migration_source_snapshot(project_id)
    assert checked is False
    assert issues[0]["code"] == "SOURCE_SNAPSHOT_DIGEST_MISMATCH"


def test_film_validate_rejects_source_snapshot_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "snapshot-cli-path"
    root = config.project_dir(project_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / fd.MIGRATION_NAME).write_text(json.dumps({
        "source_snapshot_path": "../outside.json",
        "source_snapshot_sha256": "0" * 64,
    }), encoding="utf-8")

    checked, issues = _validate_migration_source_snapshot(project_id)
    assert checked is False
    assert issues[0]["code"] == "INVALID_SOURCE_SNAPSHOT_PATH"
