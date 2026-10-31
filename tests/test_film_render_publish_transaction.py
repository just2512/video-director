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
