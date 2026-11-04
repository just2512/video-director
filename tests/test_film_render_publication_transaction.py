from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest


REVISION = "b" * 64


def _publication_layout(tmp_path: Path, monkeypatch, *, had_previous: bool):
    from video_director import film_render

    project_root = tmp_path / "project-transaction"
    render_dir = project_root / "render"
    qc_dir = project_root / "qc"
    workdir = render_dir / f".film-render-{REVISION}-987654321-1.work"
    render_dir.mkdir(parents=True)
    qc_dir.mkdir()
    workdir.mkdir()

    output = render_dir / f"film-final-{REVISION}.mp4"
    report_path = qc_dir / f"film-final-{REVISION}.json"
    output_temp = workdir / "final.mp4"
    output_temp.write_bytes(b"new-mp4")
    if had_previous:
        output.write_bytes(b"old-mp4")
        report_path.write_bytes(b'{"status":"OLD"}')

    monkeypatch.setattr(film_render.config, "project_dir", lambda _project_id: project_root)
    monkeypatch.setattr(film_render.fd, "project_lock", lambda _project_id: nullcontext())
    monkeypatch.setattr(
        film_render.fd,
        "read_project_film",
        lambda _project_id: {"rev": REVISION},
    )
    return film_render, project_root, workdir, output_temp, output, report_path


@pytest.mark.parametrize("had_previous", [False, True])
def test_qc_replace_failure_rolls_back_already_published_mp4(
    tmp_path, monkeypatch, had_previous,
):
    film_render, _root, workdir, output_temp, output, report_path = _publication_layout(
        tmp_path, monkeypatch, had_previous=had_previous
    )
    real_replace = film_render.os.replace

    def fail_qc_replace(source, destination):
        if Path(destination) == report_path:
            raise OSError("injected QC publication failure")
        return real_replace(source, destination)

    monkeypatch.setattr(film_render.os, "replace", fail_qc_replace)

    with pytest.raises(OSError, match="injected QC publication failure"):
        film_render._publish_render_artifacts(
            "project-transaction",
            REVISION,
            output_temp,
            output,
            {"source_revision": REVISION, "status": "PASS"},
            workdir,
        )

    if had_previous:
        assert output.read_bytes() == b"old-mp4"
        assert report_path.read_bytes() == b'{"status":"OLD"}'
    else:
        assert not output.exists()
        assert not report_path.exists()
    assert not (workdir / "publish-transaction.json").exists()
    assert not (workdir / "previous-output.mp4").exists()
    assert not (workdir / "previous-qc.json").exists()


@pytest.mark.parametrize("had_previous", [False, True])
def test_commit_journal_failure_rolls_back_both_published_artifacts(
    tmp_path, monkeypatch, had_previous,
):
    film_render, _root, workdir, output_temp, output, report_path = _publication_layout(
        tmp_path, monkeypatch, had_previous=had_previous
    )
    real_replace = film_render.os.replace
    journal = workdir / "publish-transaction.json"
    journal_replacements = 0

    def fail_commit_journal(source, destination):
        nonlocal journal_replacements
        if Path(destination) == journal:
            journal_replacements += 1
            if journal_replacements == 2:
                raise OSError("injected committed-journal failure")
        return real_replace(source, destination)

    monkeypatch.setattr(film_render.os, "replace", fail_commit_journal)

    with pytest.raises(OSError, match="injected committed-journal failure"):
        film_render._publish_render_artifacts(
            "project-transaction",
            REVISION,
            output_temp,
            output,
            {"source_revision": REVISION, "status": "PASS"},
            workdir,
        )

    assert journal_replacements == 2
    if had_previous:
        assert output.read_bytes() == b"old-mp4"
        assert report_path.read_bytes() == b'{"status":"OLD"}'
    else:
        assert not output.exists()
        assert not report_path.exists()
    assert not journal.exists()
    assert not (workdir / "previous-output.mp4").exists()
    assert not (workdir / "previous-qc.json").exists()


def test_mp4_replace_failure_leaves_previous_artifacts_untouched(tmp_path, monkeypatch):
    film_render, _root, workdir, output_temp, output, report_path = _publication_layout(
        tmp_path, monkeypatch, had_previous=True
    )
    real_replace = film_render.os.replace

    def fail_mp4_replace(source, destination):
        if Path(destination) == output:
            raise OSError("injected MP4 publication failure")
        return real_replace(source, destination)

    monkeypatch.setattr(film_render.os, "replace", fail_mp4_replace)

    with pytest.raises(OSError, match="injected MP4 publication failure"):
        film_render._publish_render_artifacts(
            "project-transaction",
            REVISION,
            output_temp,
            output,
            {"source_revision": REVISION, "status": "PASS"},
            workdir,
        )

    assert output.read_bytes() == b"old-mp4"
    assert report_path.read_bytes() == b'{"status":"OLD"}'
    assert not (workdir / "publish-transaction.json").exists()
    assert not (workdir / "previous-output.mp4").exists()
    assert not (workdir / "previous-qc.json").exists()


def test_failed_rollback_retains_journal_and_backups_for_startup_recovery(
    tmp_path, monkeypatch,
):
    import os

    film_render, project_root, workdir, output_temp, output, report_path = _publication_layout(
        tmp_path, monkeypatch, had_previous=True
    )
    real_replace = film_render.os.replace
    output_replace_count = 0

    def fail_publication_and_rollback(source, destination):
        nonlocal output_replace_count
        destination = Path(destination)
        if destination == report_path:
            raise OSError("injected QC publication failure")
        if destination == output:
            output_replace_count += 1
            if output_replace_count == 2:
                raise OSError("injected rollback replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(film_render.os, "replace", fail_publication_and_rollback)

    with pytest.raises(RuntimeError, match="rollback was deferred"):
        film_render._publish_render_artifacts(
            "project-transaction",
            REVISION,
            output_temp,
            output,
            {"source_revision": REVISION, "status": "PASS"},
            workdir,
        )

    journal = workdir / "publish-transaction.json"
    assert journal.is_file()
    assert (workdir / "previous-output.mp4").read_bytes() == b"old-mp4"
    assert (workdir / "previous-qc.json").read_bytes() == b'{"status":"OLD"}'
    assert output.read_bytes() == b"new-mp4"
    assert report_path.read_bytes() == b'{"status":"OLD"}'

    monkeypatch.setattr(film_render.os, "replace", real_replace)
    film_render._recover_render_publication(
        project_root, workdir, REVISION, os.getpid()
    )

    assert output.read_bytes() == b"old-mp4"
    assert report_path.read_bytes() == b'{"status":"OLD"}'


@pytest.mark.parametrize(
    ("had_output", "had_report"),
    [(True, False), (False, True)],
)
def test_commit_failure_restores_mixed_prior_artifact_state(
    tmp_path, monkeypatch, had_output, had_report,
):
    film_render, _root, workdir, output_temp, output, report_path = _publication_layout(
        tmp_path, monkeypatch, had_previous=False
    )
    if had_output:
        output.write_bytes(b"old-mp4")
    if had_report:
        report_path.write_bytes(b'{"status":"OLD"}')

    real_replace = film_render.os.replace
    journal = workdir / "publish-transaction.json"
    journal_replacements = 0

    def fail_commit_journal(source, destination):
        nonlocal journal_replacements
        if Path(destination) == journal:
            journal_replacements += 1
            if journal_replacements == 2:
                raise OSError("injected committed-journal failure")
        return real_replace(source, destination)

    monkeypatch.setattr(film_render.os, "replace", fail_commit_journal)

    with pytest.raises(OSError, match="injected committed-journal failure"):
        film_render._publish_render_artifacts(
            "project-transaction",
            REVISION,
            output_temp,
            output,
            {"source_revision": REVISION, "status": "PASS"},
            workdir,
        )

    if had_output:
        assert output.read_bytes() == b"old-mp4"
    else:
        assert not output.exists()
    if had_report:
        assert report_path.read_bytes() == b'{"status":"OLD"}'
    else:
        assert not report_path.exists()
    assert not journal.exists()


def test_publication_flushes_backups_before_prepared_journal_and_syncs_renames(
    tmp_path, monkeypatch,
):
    film_render, project_root, workdir, output_temp, output, report_path = _publication_layout(
        tmp_path, monkeypatch, had_previous=True
    )
    events = []
    real_replace = film_render.os.replace
    real_fsync_file = film_render._fsync_file
    real_fsync_directory = film_render._fsync_directory

    def track_file(path):
        events.append(("file", Path(path).name))
        return real_fsync_file(path)

    def track_directory(path):
        events.append(("directory", Path(path)))
        return real_fsync_directory(path)

    def track_replace(source, destination):
        events.append(("replace", Path(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr(film_render, "_fsync_file", track_file)
    monkeypatch.setattr(film_render, "_fsync_directory", track_directory)
    monkeypatch.setattr(film_render.os, "replace", track_replace)

    film_render._publish_render_artifacts(
        "project-transaction",
        REVISION,
        output_temp,
        output,
        {"source_revision": REVISION, "status": "PASS"},
        workdir,
    )

    prepared_journal = next(
        index for index, event in enumerate(events)
        if event == ("replace", workdir / "publish-transaction.json")
    )
    assert events.index(("file", "previous-output.mp4")) < prepared_journal
    assert events.index(("file", "previous-qc.json")) < prepared_journal
    assert events.index(("directory", workdir)) < prepared_journal

    output_replace = events.index(("replace", output))
    assert events[output_replace + 1:output_replace + 3] == [
        ("directory", workdir), ("directory", output.parent)
    ]
    report_replace = events.index(("replace", report_path))
    assert events[report_replace + 1:report_replace + 3] == [
        ("directory", report_path.parent), ("directory", report_path.parent)
    ]


def test_directory_fsync_failure_after_mp4_replace_still_rolls_back(
    tmp_path, monkeypatch,
):
    film_render, _root, workdir, output_temp, output, report_path = _publication_layout(
        tmp_path, monkeypatch, had_previous=True
    )
    real_fsync_directory = film_render._fsync_directory
    failed = False

    def fail_once_after_mp4_replace(path):
        nonlocal failed
        if Path(path) == output.parent and not failed:
            failed = True
            raise OSError("injected render directory fsync failure")
        return real_fsync_directory(path)

    monkeypatch.setattr(film_render, "_fsync_directory", fail_once_after_mp4_replace)

    with pytest.raises(OSError, match="injected render directory fsync failure"):
        film_render._publish_render_artifacts(
            "project-transaction",
            REVISION,
            output_temp,
            output,
            {"source_revision": REVISION, "status": "PASS"},
            workdir,
        )

    assert failed
    assert output.read_bytes() == b"old-mp4"
    assert report_path.read_bytes() == b'{"status":"OLD"}'
    assert not (workdir / "publish-transaction.json").exists()
    assert not (workdir / "previous-output.mp4").exists()
    assert not (workdir / "previous-qc.json").exists()


def test_atomic_replace_uses_posix_os_replace(tmp_path, monkeypatch):
    from video_director import film_render

    source = tmp_path / "source.tmp"
    destination = tmp_path / "destination.bin"
    source.write_bytes(b"payload")
    calls = []
    real_replace = film_render.os.replace

    def record_replace(src, dst):
        calls.append((Path(src), Path(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(film_render, "_is_windows_platform", lambda: False)
    monkeypatch.setattr(film_render.os, "replace", record_replace)
    film_render._atomic_replace(source, destination)

    assert calls == [(source, destination)]
    assert destination.read_bytes() == b"payload"
    assert not source.exists()


def test_atomic_replace_uses_windows_write_through_flags(tmp_path, monkeypatch):
    import ctypes
    from types import SimpleNamespace
    from video_director import film_render

    source = tmp_path / "source.tmp"
    destination = tmp_path / "destination.bin"
    calls = []

    def move_file_ex(src, dst, flags):
        calls.append((src, dst, flags))
        return 1

    monkeypatch.setattr(film_render, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: SimpleNamespace(MoveFileExW=move_file_ex), raising=False)
    film_render._atomic_replace(source, destination)

    assert calls == [(str(source), str(destination), 0x00000001 | 0x00000008)]
    assert move_file_ex.argtypes == [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    assert move_file_ex.restype == ctypes.c_int


def test_atomic_replace_propagates_windows_api_error(tmp_path, monkeypatch):
    import ctypes
    from types import SimpleNamespace
    from video_director import film_render

    def move_file_ex(_src, _dst, _flags):
        return 0

    monkeypatch.setattr(film_render, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: SimpleNamespace(MoveFileExW=move_file_ex), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "access denied"), raising=False)

    with pytest.raises(OSError, match="access denied"):
        film_render._atomic_replace(tmp_path / "source.tmp", tmp_path / "destination.bin")
