from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from video_director import film_render


def _publish_fixture(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    qc_dir = project_root / "qc"
    render_dir = project_root / "render"
    qc_dir.mkdir(parents=True)
    render_dir.mkdir()
    monkeypatch.setattr(film_render.config, "project_dir", lambda _pid: project_root)
    monkeypatch.setattr(film_render.fd, "project_lock", lambda _pid: nullcontext())
    monkeypatch.setattr(
        film_render.fd, "read_project_film",
        lambda _pid: {"exists": True, "rev": "revision-1"},
    )
    output = render_dir / "film-final-revision-1.mp4"
    output_temp = render_dir / ".staged-final.mp4"
    temp_root = render_dir / ".film-render-work"
    temp_root.mkdir()
    output_temp.write_bytes(b"new-mp4")
    report = {"status": "PASS", "source_revision": "revision-1"}
    return output, output_temp, qc_dir / "film-final-revision-1.json", temp_root, report


def test_publish_render_artifacts_publishes_matching_mp4_and_qc(tmp_path, monkeypatch):
    output, output_temp, report_path, temp_root, report = _publish_fixture(tmp_path, monkeypatch)

    published_report, current = film_render._publish_render_artifacts(
        "project-1", "revision-1", output_temp, output, report, temp_root
    )

    assert published_report == report_path
    assert current["rev"] == "revision-1"
    assert output.read_bytes() == b"new-mp4"
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert not output_temp.exists()
    assert list(temp_root.iterdir()) == []


def test_qc_publish_failure_rolls_back_mp4_and_preserves_previous_qc(tmp_path, monkeypatch):
    output, output_temp, report_path, temp_root, report = _publish_fixture(tmp_path, monkeypatch)
    output.write_bytes(b"previous-mp4")
    report_path.write_text('{"status":"PREVIOUS"}', encoding="utf-8")
    real_replace = film_render.os.replace

    def fail_qc_replace(source, destination):
        if Path(destination) == report_path:
            raise OSError("simulated QC manifest publication failure")
        return real_replace(source, destination)

    monkeypatch.setattr(film_render.os, "replace", fail_qc_replace)

    with pytest.raises(OSError, match="simulated QC manifest"):
        film_render._publish_render_artifacts(
            "project-1", "revision-1", output_temp, output, report, temp_root
        )

    assert output.read_bytes() == b"previous-mp4"
    assert report_path.read_text(encoding="utf-8") == '{"status":"PREVIOUS"}'
    assert not output_temp.exists()
    assert list(temp_root.iterdir()) == []


def test_revision_conflict_publishes_neither_mp4_nor_qc(tmp_path, monkeypatch):
    output, output_temp, report_path, temp_root, report = _publish_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        film_render.fd, "read_project_film",
        lambda _pid: {"exists": True, "rev": "revision-2"},
    )

    with pytest.raises(film_render.FilmRenderError) as exc:
        film_render._publish_render_artifacts(
            "project-1", "revision-1", output_temp, output, report, temp_root
        )

    assert exc.value.code == "REVISION_CONFLICT"
    assert not output.exists()
    assert not report_path.exists()
    assert output_temp.exists()
    assert list(temp_root.iterdir()) == []


def test_startup_recovers_interrupted_mp4_qc_publication(tmp_path, monkeypatch):
    root = tmp_path / "composer"
    project_root = root / "interrupted-render-recovery"
    render_dir = project_root / "render"
    qc_dir = project_root / "qc"
    render_dir.mkdir(parents=True)
    qc_dir.mkdir()
    revision = "a" * 64
    dead_pid = 424240
    workdir = render_dir / f".film-render-{revision}-{dead_pid}-123.work"
    workdir.mkdir()
    output = render_dir / f"film-final-{revision}.mp4"
    report = qc_dir / f"film-final-{revision}.json"
    qc_temp_name = f".film-final-{revision}.json.{dead_pid}.456.tmp"

    # The old artifacts were backed up; the process died after replacing both
    # public paths but before changing the journal from prepared to committed.
    (workdir / "previous-output.mp4").write_bytes(b"previous-mp4")
    (workdir / "previous-qc.json").write_bytes(b'{"status":"PREVIOUS"}')
    output.write_bytes(b"new-mp4")
    report.write_bytes(b'{"status":"PASS"}')
    (qc_dir / qc_temp_name).write_text("staged", encoding="utf-8")
    (workdir / "publish-transaction.json").write_text(json.dumps({
        "schema_version": 1,
        "revision": revision,
        "phase": "prepared",
        "had_output": True,
        "had_report": True,
        "qc_temp_name": qc_temp_name,
    }), encoding="utf-8")
    monkeypatch.setattr(film_render, "_render_process_is_alive", lambda _pid: False)

    result = film_render.cleanup_orphaned_render_artifacts(root)

    assert result["workdirs_removed"] == 1
    assert result["transactions_recovered"] == 1
    assert output.read_bytes() == b"previous-mp4"
    assert report.read_bytes() == b'{"status":"PREVIOUS"}'
    assert not (qc_dir / qc_temp_name).exists()
    assert not workdir.exists()


def test_startup_removes_uncommitted_artifacts_when_no_previous_pair_existed(tmp_path, monkeypatch):
    root = tmp_path / "composer"
    project_root = root / "new-render-recovery"
    render_dir = project_root / "render"
    qc_dir = project_root / "qc"
    render_dir.mkdir(parents=True)
    qc_dir.mkdir()
    revision = "b" * 64
    dead_pid = 424241
    workdir = render_dir / f".film-render-{revision}-{dead_pid}-123.work"
    workdir.mkdir()
    output = render_dir / f"film-final-{revision}.mp4"
    report = qc_dir / f"film-final-{revision}.json"
    output.write_bytes(b"partial-new-mp4")
    report.write_text('{"status":"PASS"}', encoding="utf-8")
    (workdir / "publish-transaction.json").write_text(json.dumps({
        "schema_version": 1,
        "revision": revision,
        "phase": "prepared",
        "had_output": False,
        "had_report": False,
        "qc_temp_name": "",
    }), encoding="utf-8")
    monkeypatch.setattr(film_render, "_render_process_is_alive", lambda _pid: False)

    result = film_render.cleanup_orphaned_render_artifacts(root)

    assert result["transactions_recovered"] == 1
    assert not output.exists()
    assert not report.exists()
    assert not workdir.exists()


def test_startup_keeps_workdirs_and_qc_temps_owned_by_live_processes(tmp_path, monkeypatch):
    root = tmp_path / "composer"
    project_root = root / "active-render-preserved"
    render_dir = project_root / "render"
    qc_dir = project_root / "qc"
    render_dir.mkdir(parents=True)
    qc_dir.mkdir()
    revision = "c" * 64
    active_pid = 424242
    workdir = render_dir / f".film-render-{revision}-{active_pid}-123.work"
    workdir.mkdir()
    marker = workdir / "still-running"
    marker.write_text("do not remove", encoding="utf-8")
    qc_temp = qc_dir / f".film-final-{revision}.json.{active_pid}.456.tmp"
    qc_temp.write_text("still writing", encoding="utf-8")
    monkeypatch.setattr(film_render, "_render_process_is_alive", lambda pid: pid == active_pid)

    result = film_render.cleanup_orphaned_render_artifacts(root)

    assert result["workdirs_removed"] == 0
    assert result["qc_temps_removed"] == 0
    assert marker.is_file()
    assert qc_temp.is_file()


def test_audio_signal_qc_reports_warning_without_rejecting_a_render():
    for signal_status in ("SILENT", "NEAR_SILENT", "UNKNOWN"):
        finding = film_render._audio_signal_qc_finding(
            [{"clip_id": "voice-1"}],
            {"status": signal_status},
        )

        assert finding["name"] == "audio_signal"
        assert finding["status"] == "WARNING"
        assert signal_status in finding["detail"]
        assert film_render._qc_report_status([
            {"name": "video_stream", "status": "PASS"},
            finding,
        ]) == "WARNING"


def test_audio_signal_qc_is_pass_for_audible_audio_and_absent_when_no_audio_track():
    audible = film_render._audio_signal_qc_finding(
        [{"clip_id": "music-1"}],
        {"status": "AUDIBLE"},
    )

    assert audible == {"name": "audio_signal", "status": "PASS"}
    assert film_render._qc_report_status([audible]) == "PASS"
    assert film_render._audio_signal_qc_finding([], {"status": "NOT_PRESENT"}) is None

