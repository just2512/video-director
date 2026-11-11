#!/usr/bin/env python3
"""Measure Film Studio resource snapshots using explicitly supplied project media.

This command never manufactures benchmark media. Supply one or more real, existing
project-relative resources (for example assets/camera-original.mp4). The reported
numbers describe only the selected files and the current machine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable

from video_director import config, film_document as fd
from video_director.film_resource_snapshot import SceneResourceSnapshot


def _sha256(path: Path) -> tuple[str, float]:
    started = time.perf_counter()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest(), (time.perf_counter() - started) * 1000.0


def _allocated_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            metadata = item.stat()
            total += int(getattr(metadata, "st_blocks", 0)) * 512 or metadata.st_size
    return total


def benchmark_snapshot(project_id: str, resources: Iterable[str]) -> dict:
    """Snapshot and verify real project resources; no synthetic fallback exists."""
    selected = [str(item).strip() for item in resources if str(item).strip()]
    if not selected:
        raise ValueError("at least one --resource path is required")

    project_root = config.project_dir(project_id).resolve(strict=True)
    source_records = []
    for raw in selected:
        try:
            source = fd.media_path_for_src(project_id, raw)
        except fd.FilmError as exc:
            raise ValueError(f"resource must be an existing regular file: {raw}") from exc
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"resource must be an existing regular file: {raw}")
        try:
            source.resolve(strict=True).relative_to(project_root)
        except (OSError, ValueError) as exc:
            raise ValueError(f"resource escapes project root: {raw}") from exc
        digest, elapsed_ms = _sha256(source)
        source_records.append({
            "src": raw,
            "size_bytes": source.stat().st_size,
            "sha256_before": digest,
            "source_hash_elapsed_ms": round(elapsed_ms, 3),
        })

    with tempfile.TemporaryDirectory(prefix=".film-resource-snapshot-benchmark-", dir=project_root) as temp:
        snapshot_root = Path(temp) / "snapshot"
        snapshot = SceneResourceSnapshot(project_id, snapshot_root).build(selected)
        copy_report = snapshot.report()

        validation_started = time.perf_counter()
        snapshot.validate_sources()
        source_validation_ms = (time.perf_counter() - validation_started) * 1000.0

        copied_hash_started = time.perf_counter()
        copied_records = []
        for src, record in sorted(snapshot.sources.items()):
            copied = snapshot.path_for(src)
            if copied is None or not copied.is_file():
                raise RuntimeError(f"snapshot copy is missing: {src}")
            copied_digest, _ = _sha256(copied)
            if copied_digest != record["sha256"]:
                raise RuntimeError(f"snapshot hash mismatch: {src}")
            copied_records.append({
                "src": src,
                "size_bytes": copied.stat().st_size,
                "sha256": copied_digest,
            })
        copied_hash_ms = (time.perf_counter() - copied_hash_started) * 1000.0
        logical_bytes = sum(item["size_bytes"] for item in copied_records)
        allocated_bytes = _allocated_bytes(snapshot_root)

        return {
            "project_id": project_id,
            "resources_requested": selected,
            "resource_count": copy_report["resource_count"],
            "source_bytes": sum(item["size_bytes"] for item in copy_report["resources"]),
            "snapshot_logical_bytes": logical_bytes,
            "snapshot_allocated_bytes": allocated_bytes,
            "disk_increment_bytes": allocated_bytes,
            "copy_and_source_hash_ms": copy_report["copy_elapsed_ms"],
            "source_validation_hash_ms": round(source_validation_ms, 3),
            "copied_hash_verification_ms": round(copied_hash_ms, 3),
            "source_hashes_before_copy": source_records,
            "snapshot_hashes": copied_records,
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "filesystem_block_accounting": hasattr(Path(snapshot_root).stat(), "st_blocks"),
            },
            "measurement_scope": (
                "Only the explicitly selected project resources and their discovered local "
                "dependencies; the temporary copy is measured on the project filesystem, "
                "not as a production-wide benchmark."
            ),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument(
        "--resource", action="append", required=True,
        help="Existing project-relative media path; repeat for multiple resources.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = benchmark_snapshot(args.project_id, args.resource)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + os.linesep, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
