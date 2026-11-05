"""Serialized, atomic updates to a project's shared assets/manifest.json."""
from __future__ import annotations

from contextlib import nullcontext
import json
import os
import tempfile
from pathlib import Path
from typing import Callable, Iterable

from video_director import config
from video_director.project_lock import project_lock


class ProjectManifestError(RuntimeError):
    """The project's asset manifest cannot be safely read or updated."""


def manifest_path(project_id: str) -> Path:
    return config.project_dir(project_id) / "assets" / "manifest.json"


def _read_unlocked(project_id: str) -> list[dict]:
    path = manifest_path(project_id)
    # A symlink or non-regular manifest is not a trustworthy reference record.
    # Callers that use the manifest for destructive decisions must fail closed.
    if path.is_symlink():
        raise ProjectManifestError("项目素材清单是符号链接，已拒绝读取")
    if path.exists() and not path.is_file():
        raise ProjectManifestError("项目素材清单不是普通文件，已拒绝读取")
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as exc:
        raise ProjectManifestError(
            f"项目素材清单无法读取，已拒绝覆盖：{exc}"
        ) from exc
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProjectManifestError(
            f"项目素材清单 JSON 损坏，已拒绝覆盖：{exc}"
        ) from exc
    if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
        raise ProjectManifestError("项目素材清单结构无效；预期为对象数组，已拒绝覆盖")
    return entries


def read_project_manifest(project_id: str) -> list[dict]:
    """Return a consistent copy of the project's current asset manifest."""
    with project_lock(project_id):
        return _read_unlocked(project_id)


def _write_atomic(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(entries, ensure_ascii=False, indent=2)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def mutate_project_manifest(
    project_id: str,
    mutate: Callable[[list[dict]], object],
    *,
    _catalog_lock_held: bool = False,
) -> object:
    """Run a read/modify/write transaction under catalog + project locks.

    The catalog lock coordinates manifest references with global Asset Library
    deletion. The project lock then serializes writers to this one manifest.
    Callers already holding the catalog transaction may set the private flag to
    avoid re-acquiring the process-level file lock.
    """
    if _catalog_lock_held:
        catalog_lock = nullcontext()
    else:
        from video_director.asset_catalog import _catalog_lock
        catalog_lock = _catalog_lock()
    with catalog_lock:
        with project_lock(project_id):
            entries = _read_unlocked(project_id)
            before = json.dumps(entries, ensure_ascii=False, sort_keys=True)
            result = mutate(entries)
            after = json.dumps(entries, ensure_ascii=False, sort_keys=True)
            if after != before:
                _write_atomic(manifest_path(project_id), entries)
            return result


def merge_project_manifest_entries(
    project_id: str,
    additions: Iterable[dict],
    *,
    replace_ids: Iterable[str] = (),
    reject_conflicting_ids: bool = False,
    _catalog_lock_held: bool = False,
) -> list[str]:
    """Merge rows without dropping concurrent additions; replacement is explicit."""
    rows = [dict(item) for item in additions]
    replacements = {str(item) for item in replace_ids}
    for item in rows:
        asset_id = item.get("id")
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise ProjectManifestError("素材清单条目缺少有效 id")

    added: list[str] = []

    def apply(entries: list[dict]) -> list[str]:
        index_by_id = {
            item.get("id"): index
            for index, item in enumerate(entries)
            if isinstance(item.get("id"), str)
        }
        for item in rows:
            asset_id = item["id"]
            index = index_by_id.get(asset_id)
            if index is not None and asset_id in replacements:
                entries[index] = item
                added.append(asset_id)
                continue
            if index is not None:
                if reject_conflicting_ids and entries[index] != item:
                    raise ProjectManifestError(
                        f"素材 ID 冲突：{asset_id} 已指向另一条素材记录"
                    )
                continue
            index_by_id[asset_id] = len(entries)
            entries.append(item)
            added.append(asset_id)
        return list(added)

    return mutate_project_manifest(
        project_id, apply, _catalog_lock_held=_catalog_lock_held
    )  # type: ignore[return-value]
