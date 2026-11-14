from __future__ import annotations

from pathlib import Path

import pytest

from video_director import film_document as fd
from tests.test_film_document_contract import SAMPLE


def _ready_project(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-transaction"
    root.mkdir(parents=True)
    # write_bytes 保持 LF：Windows write_text 会转 CRLF，与 SAMPLE 常量 revision 不一致
    (root / fd.FILM_NAME).write_bytes(SAMPLE.encode("utf-8"))
    return root


def _edit(project_id="film-transaction"):
    state = fd.read_project_film(project_id)
    return fd.edit_project_film(
        project_id, state["rev"],
        [{"op": "set", "clip": "clip-b", "field": "at", "value": 8}],
        source="transaction-test",
    )


def test_recovery_finishes_journal_when_process_stops_before_film_replace(tmp_path, monkeypatch):
    root = _ready_project(tmp_path, monkeypatch)
    before = fd.read_project_film("film-transaction")
    real_write = fd._write_atomic

    def crash_before_film(path, payload):
        if Path(path).name == fd.FILM_NAME:
            raise OSError("simulated process interruption before film replace")
        return real_write(path, payload)

    monkeypatch.setattr(fd, "_write_atomic", crash_before_film)
    with pytest.raises(OSError, match="simulated"):
        _edit()
    assert (root / ".film-transaction.json").is_file()
    assert fd.revision_of((root / fd.FILM_NAME).read_text(encoding="utf-8")) == before["rev"]

    monkeypatch.setattr(fd, "_write_atomic", real_write)
    undone = fd.undo_redo_project_film("film-transaction", None, "undo")
    assert undone["rev"] == before["rev"]
    assert not (root / ".film-transaction.json").exists()


def test_recovery_finishes_journal_when_history_replace_fails_and_is_retryable(tmp_path, monkeypatch):
    root = _ready_project(tmp_path, monkeypatch)
    real_history_write = fd._history_write

    def crash_history(*args, **kwargs):
        raise OSError("simulated history interruption")

    monkeypatch.setattr(fd, "_history_write", crash_history)
    with pytest.raises(OSError, match="history interruption"):
        _edit()
    assert (root / fd.FILM_NAME).read_text(encoding="utf-8") != ""
    assert (root / ".film-transaction.json").is_file()

    with pytest.raises(OSError, match="history interruption"):
        fd.undo_redo_project_film("film-transaction", None, "undo")
    assert (root / ".film-transaction.json").is_file()

    monkeypatch.setattr(fd, "_history_write", real_history_write)
    result = fd.undo_redo_project_film("film-transaction", None, "undo")
    assert result["action"] == "undo"
    assert not (root / ".film-transaction.json").exists()


def test_recovery_refuses_to_overwrite_external_film_change(tmp_path, monkeypatch):
    root = _ready_project(tmp_path, monkeypatch)
    real_write = fd._write_atomic

    def crash_before_film(path, payload):
        if Path(path).name == fd.FILM_NAME:
            raise OSError("simulated interruption")
        return real_write(path, payload)

    monkeypatch.setattr(fd, "_write_atomic", crash_before_film)
    with pytest.raises(OSError):
        _edit()
    monkeypatch.setattr(fd, "_write_atomic", real_write)
    (root / fd.FILM_NAME).write_text(SAMPLE.replace('at="5"', 'at="6"'), encoding="utf-8")

    with pytest.raises(fd.FilmError) as error:
        fd.undo_redo_project_film("film-transaction", None, "undo")
    assert error.value.code == "FILM_TRANSACTION_CONFLICT"
    assert (root / ".film-transaction.json").is_file()
