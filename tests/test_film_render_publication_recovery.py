from __future__ import annotations

import json
from pathlib import Path

import pytest


REVISION = "a" * 64
PID = 987654321


def _project_layout(tmp_path: Path):
    project_root = tmp_path / "project-one"
    render_dir = project_root / "render"
    qc_dir = project_root / "qc"
    workdir = render_dir / f".film-render-{REVISION}-{PID}-1.work"
    render_dir.mkdir(parents=True)
    qc_dir.mkdir()
    workdir.mkdir()
    output = render_dir / f"film-final-{REVISION}.mp4"
    report = qc_dir / f"film-final-{REVISION}.json"
    return project_root, workdir, output, report


@pytest.mark.parametrize(
    ("had_output", "had_report"),
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_prepared_publication_recovers_both_artifacts_as_one_transaction(
    tmp_path, had_output, had_report,
):
    from video_director.film_render import (
        _recover_render_publication,
        _write_publish_journal,
    )

    project_root, workdir, output, report = _project_layout(tmp_path)
    if had_output:
        (workdir / "previous-output.mp4").write_bytes(b"previous-mp4")
    if had_report:
        (workdir / "previous-qc.json").write_bytes(b'{"status":"previous"}')
    output.write_bytes(b"partially-published-mp4")
    report.write_bytes(b'{"status":"partially-published"}')
    qc_temp_name = f".film-final-{REVISION}.json.{PID}.123.tmp"
    (project_root / "qc" / qc_temp_name).write_text("partial", encoding="utf-8")
    _write_publish_journal(workdir, {
        "schema_version": 1,
        "revision": REVISION,
        "phase": "prepared",
        "had_output": had_output,
        "had_report": had_report,
        "qc_temp_name": qc_temp_name,
    })

    _recover_render_publication(project_root, workdir, REVISION, PID)

    assert output.read_bytes() == b"previous-mp4" if had_output else not output.exists()
    assert report.read_bytes() == b'{"status":"previous"}' if had_report else not report.exists()
    assert not (project_root / "qc" / qc_temp_name).exists()


def test_committed_publication_recovery_preserves_new_mp4_and_qc(tmp_path):
    from video_director.film_render import _recover_render_publication, _write_publish_journal

    project_root, workdir, output, report = _project_layout(tmp_path)
    output.write_bytes(b"committed-mp4")
    report.write_text('{"status":"PASS"}', encoding="utf-8")
    _write_publish_journal(workdir, {
        "schema_version": 1,
        "revision": REVISION,
        "phase": "committed",
        "had_output": True,
        "had_report": True,
        "qc_temp_name": None,
    })

    _recover_render_publication(project_root, workdir, REVISION, PID)

    assert output.read_bytes() == b"committed-mp4"
    assert report.read_text(encoding="utf-8") == '{"status":"PASS"}'


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"schema_version": 2, "revision": REVISION, "phase": "prepared",
         "had_output": False, "had_report": False},
        {"schema_version": 1, "revision": "b" * 64, "phase": "prepared",
         "had_output": False, "had_report": False},
        {"schema_version": 1, "revision": REVISION, "phase": "unknown",
         "had_output": False, "had_report": False},
        {"schema_version": 1, "revision": REVISION, "phase": "prepared",
         "had_output": "false", "had_report": False},
    ],
)
def test_invalid_publication_journal_fails_closed(tmp_path, payload):
    from video_director.film_render import _recover_render_publication

    project_root, workdir, output, report = _project_layout(tmp_path)
    output.write_bytes(b"do-not-touch")
    report.write_bytes(b'{"status":"do-not-touch"}')
    (workdir / "publish-transaction.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises((RuntimeError, ValueError)):
        _recover_render_publication(project_root, workdir, REVISION, PID)

    assert output.read_bytes() == b"do-not-touch"
    assert report.read_bytes() == b'{"status":"do-not-touch"}'


def test_orphaned_prepared_publication_is_recovered_and_workdir_removed(
    tmp_path, monkeypatch,
):
    from video_director import film_render

    project_root, workdir, output, report = _project_layout(tmp_path)
    (workdir / "previous-output.mp4").write_bytes(b"old-mp4")
    (workdir / "previous-qc.json").write_bytes(b'{"status":"old"}')
    output.write_bytes(b"new-mp4-before-crash")
    report.write_bytes(b'{"status":"new-before-crash"}')
    film_render._write_publish_journal(workdir, {
        "schema_version": 1,
        "revision": REVISION,
        "phase": "prepared",
        "had_output": True,
        "had_report": True,
        "qc_temp_name": None,
    })
    monkeypatch.setattr(film_render, "_render_process_is_alive", lambda _pid: False)

    summary = film_render.cleanup_orphaned_render_artifacts(tmp_path)

    assert output.read_bytes() == b"old-mp4"
    assert report.read_bytes() == b'{"status":"old"}'
    assert not workdir.exists()
    assert summary["transactions_recovered"] == 1
    assert summary["workdirs_removed"] == 1
    assert summary["errors"] == []


def test_orphaned_committed_publication_keeps_new_artifacts_and_removes_workdir(
    tmp_path, monkeypatch,
):
    from video_director import film_render

    project_root, workdir, output, report = _project_layout(tmp_path)
    output.write_bytes(b"committed-mp4")
    report.write_text('{"status":"PASS"}', encoding="utf-8")
    film_render._write_publish_journal(workdir, {
        "schema_version": 1,
        "revision": REVISION,
        "phase": "committed",
        "had_output": False,
        "had_report": False,
        "qc_temp_name": None,
    })
    monkeypatch.setattr(film_render, "_render_process_is_alive", lambda _pid: False)

    summary = film_render.cleanup_orphaned_render_artifacts(tmp_path)

    assert output.read_bytes() == b"committed-mp4"
    assert report.read_text(encoding="utf-8") == '{"status":"PASS"}'
    assert not workdir.exists()
    assert summary["transactions_recovered"] == 0
    assert summary["workdirs_removed"] == 1
    assert summary["errors"] == []


def test_live_render_workdir_is_not_recovered_or_removed(tmp_path, monkeypatch):
    from video_director import film_render

    _project_root, workdir, _output, _report = _project_layout(tmp_path)
    monkeypatch.setattr(film_render, "_render_process_is_alive", lambda _pid: True)

    summary = film_render.cleanup_orphaned_render_artifacts(tmp_path)

    assert workdir.is_dir()
    assert summary["workdirs_removed"] == 0
    assert summary["transactions_recovered"] == 0
    assert summary["errors"] == []


def test_invalid_orphaned_transaction_is_preserved_for_inspection(tmp_path, monkeypatch):
    from video_director import film_render

    _project_root, workdir, output, report = _project_layout(tmp_path)
    output.write_bytes(b"keep-output")
    report.write_bytes(b"keep-report")
    (workdir / "publish-transaction.json").write_text(
        json.dumps({
            "schema_version": 99,
            "revision": REVISION,
            "phase": "prepared",
            "had_output": True,
            "had_report": True,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(film_render, "_render_process_is_alive", lambda _pid: False)

    summary = film_render.cleanup_orphaned_render_artifacts(tmp_path)

    assert workdir.is_dir()
    assert output.read_bytes() == b"keep-output"
    assert report.read_bytes() == b"keep-report"
    assert summary["workdirs_removed"] == 0
    assert summary["transactions_recovered"] == 0
    assert len(summary["errors"]) == 1
