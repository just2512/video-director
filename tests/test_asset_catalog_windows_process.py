"""Windows-native cross-process Asset Library reference/deletion lock regression."""
from __future__ import annotations

import multiprocessing
from pathlib import Path
import queue

import pytest


def _spawn_manifest_writer(project_id, row, started, finished, errors):
    from video_director import project_manifest

    started.set()
    try:
        project_manifest.merge_project_manifest_entries(project_id, [row])
    except Exception as exc:
        errors.put(repr(exc))
    finally:
        finished.set()


def test_windows_spawn_manifest_writer_serializes_with_library_deletion(tmp_path, monkeypatch):
    """The Windows msvcrt lock must serialize a spawned manifest writer and delete."""
    storage = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))

    from video_director import asset_catalog as ac

    asset_id = "windows-lock-race-asset"
    asset_file = storage / "assets" / "local" / "windows-lock-race-asset.jpg"
    asset_file.parent.mkdir(parents=True)
    asset_file.write_bytes(b"test-image-payload")
    with ac._catalog_lock():
        ac._save_catalog({
            asset_id: {
                "asset_id": asset_id,
                "type": "image",
                "file": str(asset_file),
                "content_hash": "windows-lock-race-hash",
            }
        })

    project_id = "windows-lock-race-project"
    row = {"id": asset_id, "type": "image", "file": str(asset_file)}
    context = multiprocessing.get_context("spawn")
    started = context.Event()
    finished = context.Event()
    errors = context.Queue()
    process = context.Process(
        target=_spawn_manifest_writer,
        args=(project_id, row, started, finished, errors),
    )

    with ac._catalog_lock():
        process.start()
        assert started.wait(timeout=15), "spawned manifest writer did not start"
        assert not finished.wait(timeout=0.5), (
            "manifest writer bypassed the Windows cross-process catalog lock"
        )

    assert finished.wait(timeout=20), "manifest writer did not resume after lock release"
    process.join(timeout=15)
    assert not process.is_alive(), "spawned manifest writer did not exit"
    assert process.exitcode == 0
    with pytest.raises(queue.Empty):
        errors.get(timeout=0.2)

    result = ac.delete_asset(asset_id)
    assert result["ok"] is False
    assert result["code"] == "ASSET_IN_USE"
    assert ac.get_asset(asset_id) is not None
    assert asset_file.is_file()
