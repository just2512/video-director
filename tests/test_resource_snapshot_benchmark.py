"""Functional contract for the opt-in real-media resource snapshot benchmark."""
from __future__ import annotations

import hashlib

from video_director import config
from scripts.benchmark_resource_snapshot import benchmark_snapshot


def test_benchmark_reports_real_project_resource_copy_and_hash_metrics(tmp_path, monkeypatch):
    """Small fixture verifies the harness contract only; it is not performance evidence."""
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "resource-snapshot-benchmark-contract"
    config.init_project_dirs(config.project_dir(project_id))
    source = config.project_dir(project_id) / "assets" / "benchmark-contract.bin"
    source.parent.mkdir(parents=True, exist_ok=True)
    payload = (b"project-media-contract-" * 8192) + b"end"
    source.write_bytes(payload)

    result = benchmark_snapshot(project_id, ["assets/benchmark-contract.bin"])

    assert result["resource_count"] == 1
    assert result["source_bytes"] == len(payload)
    assert result["snapshot_logical_bytes"] == len(payload)
    assert result["snapshot_allocated_bytes"] >= len(payload)
    assert result["disk_increment_bytes"] == result["snapshot_allocated_bytes"]
    assert result["copy_and_source_hash_ms"] >= 0
    assert result["source_validation_hash_ms"] >= 0
    assert result["copied_hash_verification_ms"] >= 0
    expected = hashlib.sha256(payload).hexdigest()
    assert result["source_hashes_before_copy"][0]["sha256_before"] == expected
    assert result["snapshot_hashes"][0]["sha256"] == expected
    assert "not a production-wide" in result["measurement_scope"]


def test_benchmark_rejects_missing_or_out_of_project_resource(tmp_path, monkeypatch):
    import pytest

    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "resource-snapshot-benchmark-invalid"
    config.init_project_dirs(config.project_dir(project_id))

    with pytest.raises(Exception, match="existing regular file"):
        benchmark_snapshot(project_id, ["assets/missing.mp4"])
