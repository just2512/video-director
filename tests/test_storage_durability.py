from __future__ import annotations

import os
from pathlib import Path

import pytest

from video_director import config, storage_durability


def _isolate(monkeypatch, tmp_path: Path) -> Path:
    storage = tmp_path / "composer"
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)
    return storage


def test_atomic_replace_round_trip_and_directory_sync(tmp_path, monkeypatch):
    source = tmp_path / ".state.tmp"
    destination = tmp_path / "state.json"
    source.write_bytes(b"new-state")
    destination.write_bytes(b"old-state")
    synced = []

    if os.name != "nt":
        monkeypatch.setattr(
            storage_durability,
            "fsync_directory",
            lambda path: synced.append(Path(path)) or True,
        )

    assert storage_durability.atomic_replace(source, destination) is True
    assert destination.read_bytes() == b"new-state"
    assert not source.exists()
    if os.name != "nt":
        assert synced == [tmp_path]


def test_atomic_replace_reports_unconfirmed_posix_directory_sync(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("Windows uses MoveFileExW write-through instead of directory fsync")

    source = tmp_path / ".uncertain.tmp"
    destination = tmp_path / "uncertain.json"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    monkeypatch.setattr(storage_durability, "fsync_directory", lambda _path: False)

    assert storage_durability.atomic_replace(source, destination) is False
    # Rename happened, but callers must not advance their transaction state.
    assert destination.read_bytes() == b"new"
    assert not source.exists()


def test_generation_reservation_durability_failure_blocks_paid_submission(
    tmp_path, monkeypatch
):
    _isolate(monkeypatch, tmp_path)
    from video_director import generation as gen, model_config
    monkeypatch.setattr(
        model_config,
        "get_role_config",
        lambda role: {
            "api_key": "test-key",
            "base_url": "https://durability-test.example/v1",
            "model": "model",
        } if role == "video" else {},
    )
    identity = gen._video_provider_identity(gen._role_cfg("video"), "model")

    project_id = "durable-submit-gate"
    config.init_project_dirs(config.project_dir(project_id))

    class Provider:
        post_calls = 0

        def post(self, *_args, **_kwargs):
            self.post_calls += 1
            raise AssertionError("provider POST must not run before durable reservation")

    provider = Provider()
    monkeypatch.setattr(gen, "atomic_replace", lambda _source, _destination: False)

    with pytest.raises(gen.GenerationError) as caught:
        gen._create_poll_and_persist_video(
            provider,
            project_id,
            "model",
            "prompt",
            320,
            180,
            "f" * 64,
            "A",
            str(config.project_dir(project_id) / "assets" / "generated" / "result.mp4"),
            timeout=1,
            provider_identity=identity,
        )

    assert caught.value.code == "VIDEO_JOB_DURABILITY_UNCONFIRMED"
    assert provider.post_calls == 0


def test_video_output_stays_recoverable_until_directory_sync_is_confirmed(
    tmp_path, monkeypatch
):
    _isolate(monkeypatch, tmp_path)
    from video_director import generation as gen

    project_id = "durable-output-recovery"
    config.init_project_dirs(config.project_dir(project_id))
    output = config.project_dir(project_id) / "assets" / "generated" / "result.mp4"
    real_replace = gen.atomic_replace
    failed_once = {"value": False}

    def fail_first_output_sync(source, destination):
        if Path(destination) == output and not failed_once["value"]:
            failed_once["value"] = True
            assert real_replace(source, destination) is True
            return False
        return real_replace(source, destination)

    monkeypatch.setattr(gen, "atomic_replace", fail_first_output_sync)
    monkeypatch.setattr(gen, "_download_bytes", lambda _url: b"durable-video")
    with pytest.raises(gen.GenerationError) as caught:
        gen._persist_video_output(project_id, "job-output", str(output), "https://cdn/video.mp4")

    assert caught.value.code == "VIDEO_OUTPUT_DURABILITY_UNCONFIRMED"
    assert output.read_bytes() == b"durable-video"
    assert gen._load_jobs(project_id)["job-output"]["status"] == "downloading"

    monkeypatch.setattr(gen, "atomic_replace", real_replace)
    gen._persist_video_output(project_id, "job-output", str(output), "https://cdn/video.mp4")
    assert gen._load_jobs(project_id)["job-output"]["status"] == "done"


def test_manifest_and_asset_catalog_fail_closed_when_durability_is_unconfirmed(
    tmp_path, monkeypatch
):
    _isolate(monkeypatch, tmp_path)
    from video_director import asset_catalog, project_manifest

    project_id = "durable-manifest-catalog"
    config.init_project_dirs(config.project_dir(project_id))

    monkeypatch.setattr(project_manifest, "atomic_replace", lambda _source, _destination: False)
    with pytest.raises(project_manifest.ProjectManifestError, match="目录持久化未确认"):
        project_manifest.merge_project_manifest_entries(
            project_id,
            [{"id": "asset-manifest", "type": "image", "file": "assets/generated/a.png"}],
        )

    monkeypatch.setattr(asset_catalog, "atomic_replace", lambda _source, _destination: False)
    with asset_catalog._catalog_lock():
        with pytest.raises(asset_catalog.AssetLibraryError) as caught:
            asset_catalog._save_catalog({"asset-catalog": {"asset_id": "asset-catalog"}})
    assert caught.value.code == "CATALOG_DURABILITY_UNCONFIRMED"


def test_manifest_and_asset_catalog_writers_use_shared_atomic_replace(
    tmp_path, monkeypatch
):
    _isolate(monkeypatch, tmp_path)
    from video_director import asset_catalog, project_manifest

    project_id = "durable-writers"
    config.init_project_dirs(config.project_dir(project_id))
    manifest_calls = []
    manifest_replace = project_manifest.atomic_replace

    def record_manifest_replace(source, destination):
        manifest_calls.append(Path(destination))
        return manifest_replace(source, destination)

    monkeypatch.setattr(project_manifest, "atomic_replace", record_manifest_replace)
    project_manifest.merge_project_manifest_entries(
        project_id,
        [{"id": "manifest-asset", "type": "image", "file": "assets/generated/a.png"}],
    )
    assert manifest_calls == [project_manifest.manifest_path(project_id)]

    catalog_calls = []
    catalog_replace = asset_catalog.atomic_replace

    def record_catalog_replace(source, destination):
        catalog_calls.append(Path(destination))
        return catalog_replace(source, destination)

    monkeypatch.setattr(asset_catalog, "atomic_replace", record_catalog_replace)
    with asset_catalog._catalog_lock():
        asset_catalog._save_catalog({"catalog-asset": {"asset_id": "catalog-asset"}})
    assert catalog_calls == [asset_catalog._catalog_path()]


def test_generation_jobs_and_video_output_use_shared_atomic_replace(
    tmp_path, monkeypatch
):
    _isolate(monkeypatch, tmp_path)
    from video_director import generation as gen

    project_id = "durable-generation-writers"
    config.init_project_dirs(config.project_dir(project_id))
    calls = []
    replace = gen.atomic_replace

    def record_replace(source, destination):
        calls.append(Path(destination))
        return replace(source, destination)

    monkeypatch.setattr(gen, "atomic_replace", record_replace)
    ledger = gen._jobs_path(project_id)
    gen._save_jobs(project_id, {"reservation": {"status": "submitting"}})

    output = config.project_dir(project_id) / "assets" / "generated" / "result.mp4"
    monkeypatch.setattr(gen, "_download_bytes", lambda _url: b"video-bytes")
    gen._persist_video_output(project_id, "job-finalize", str(output), "https://cdn/video.mp4")

    assert ledger in calls
    assert output in calls
    assert output.read_bytes() == b"video-bytes"
    assert gen._load_jobs(project_id)["job-finalize"]["status"] == "done"


def test_legacy_atomic_v1_output_is_rewritten_before_becoming_done(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    from video_director import generation as gen

    project_id = "legacy-output-v1"
    config.init_project_dirs(config.project_dir(project_id))
    output = config.project_dir(project_id) / "assets" / "generated" / "legacy.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"legacy-output")
    gen._update_job(project_id, "legacy-job", {
        "provider_task_id": "provider-legacy",
        "gen_type": "video",
        "status": "downloading",
        "output_file": str(output),
        "output_commit_protocol": "atomic-v1",
    })
    monkeypatch.setattr(gen, "_download_bytes", lambda _url: b"rewritten-with-current-protocol")

    gen._persist_video_output(project_id, "legacy-job", str(output), "https://cdn/video.mp4")

    assert output.read_bytes() == b"rewritten-with-current-protocol"
    assert gen._load_jobs(project_id)["legacy-job"]["output_commit_protocol"] == "atomic-v2"
    assert gen._load_jobs(project_id)["legacy-job"]["status"] == "done"
