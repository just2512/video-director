"""Direct external agent writes are recorded in the same undo/redo revision history."""
from __future__ import annotations

import time

from video_director import config, film_document as fd
from video_director.film_watch import FilmRevisionWatcher


FILM = '''<!doctype html>
<html><head><meta charset="utf-8"></head><body>
<section id="visual" data-label="画面">
  <video id="clip" src="assets/video.mp4#t=0,1" at="0"></video>
</section>
</body></html>
'''


def _poll_changed_file(watcher: FilmRevisionWatcher) -> None:
    now = time.monotonic() + 1
    watcher.poll_once(now=now)
    watcher.poll_once(now=now + 1)


def test_external_agent_edit_enters_shared_history_and_can_undo_redo(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "external-agent-history"
    project = config.project_dir(project_id)
    project.mkdir(parents=True)
    film_path = fd._film_path(project_id)
    film_path.write_text(FILM, encoding="utf-8")
    initial = fd.read_project_film(project_id)

    events = []
    watcher = FilmRevisionWatcher(lambda topic, payload: events.append((topic, payload)))
    watcher.watch(project_id, initial_revision=initial["rev"])

    external = FILM.replace('at="0"', 'at="0.25"')
    film_path.write_text(external, encoding="utf-8")
    _poll_changed_file(watcher)

    history = fd._history_read(project_id)
    assert len(history["past"]) == 1
    entry = history["past"][-1]
    assert entry["before"] == FILM
    assert entry["after"] == external
    assert entry["before_rev"] == initial["rev"]
    assert entry["after_rev"] == fd.revision_of(external)
    assert entry["ops"] == [{"op": "external_file", "source": "external_file"}]
    assert events == [("film", {
        "phase": "revision_changed",
        "project_id": project_id,
        "revision": fd.revision_of(external),
        "source": "external_file",
    })]

    fd.undo_redo_project_film(project_id, None, "undo")
    assert film_path.read_text(encoding="utf-8") == FILM
    fd.undo_redo_project_film(project_id, None, "redo")
    assert film_path.read_text(encoding="utf-8") == external


def test_watcher_does_not_duplicate_an_edit_already_recorded_by_studio_api(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "external-watch-api-dedup"
    project = config.project_dir(project_id)
    project.mkdir(parents=True)
    film_path = fd._film_path(project_id)
    film_path.write_text(FILM, encoding="utf-8")
    initial = fd.read_project_film(project_id)

    events = []
    watcher = FilmRevisionWatcher(lambda topic, payload: events.append((topic, payload)))
    watcher.watch(project_id, initial_revision=initial["rev"])

    fd.edit_project_film(project_id, initial["rev"], [
        {"op": "set", "clip": "clip", "field": "title", "value": "Saved by Studio"},
    ])
    _poll_changed_file(watcher)

    history = fd._history_read(project_id)
    assert len(history["past"]) == 1
    assert history["past"][0]["ops"][0]["op"] == "set"
    assert events == []



def test_external_write_between_initial_read_and_watcher_registration_is_not_lost(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "watch-registration-race"
    project = config.project_dir(project_id)
    project.mkdir(parents=True)
    film_path = fd._film_path(project_id)
    film_path.write_text(FILM, encoding="utf-8")
    initial = fd.read_project_film(project_id)

    # An external agent writes after the GET snapshot but before watcher.watch().
    external = FILM.replace('at="0"', 'at="0.75"')
    film_path.write_text(external, encoding="utf-8")
    events = []
    watcher = FilmRevisionWatcher(lambda topic, payload: events.append((topic, payload)))
    watcher.watch(project_id, initial["rev"], initial_text=initial["text"])
    _poll_changed_file(watcher)

    history = fd._history_read(project_id)
    assert len(history["past"]) == 1
    assert history["past"][0]["before"] == initial["text"]
    assert history["past"][0]["after"] == external
    assert events and events[-1][1]["revision"] == fd.revision_of(external)
