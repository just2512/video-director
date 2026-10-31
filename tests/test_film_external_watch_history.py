"""Direct external agent writes are recorded in the same undo/redo revision history."""
from __future__ import annotations

import os
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
    # Tests inject monotonic times rather than sleep; keep them ahead of prior
    # injected polls even when the helper is called repeatedly in one test.
    now = time.monotonic() + 1
    for state in watcher._projects.values():
        for key in ("changed_at", "scene_last_scan_at", "scene_changed_at", "last_access"):
            value = state.get(key)
            if isinstance(value, (int, float)):
                now = max(now, float(value) + 1)
    watcher.poll_once(now=now)
    watcher.poll_once(now=now + 1)


def test_external_agent_edit_enters_shared_history_and_can_undo_redo(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "external-agent-history"
    project = config.project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
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
    assert entry["source"] == "external_file"
    assert entry["ops"] == [{"op": "external_file", "source": "external_file"}]
    assert events == [("film", {
        "phase": "revision_changed",
        "project_id": project_id,
        "revision": fd.revision_of(external),
        "source": "external_file",
    })]

    fd.undo_redo_project_film(project_id, None, "undo")
    assert film_path.read_text(encoding="utf-8") == FILM
    history_after_undo = fd._history_read(project_id)
    assert history_after_undo["future"][-1]["source"] == "external_file"
    fd.undo_redo_project_film(project_id, None, "redo")
    assert film_path.read_text(encoding="utf-8") == external
    history_after_redo = fd._history_read(project_id)
    assert history_after_redo["past"][-1]["source"] == "external_file"


def test_watcher_does_not_duplicate_an_edit_already_recorded_by_studio_api(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "external-watch-api-dedup"
    project = config.project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
    film_path = fd._film_path(project_id)
    film_path.write_text(FILM, encoding="utf-8")
    initial = fd.read_project_film(project_id)

    events = []
    watcher = FilmRevisionWatcher(lambda topic, payload: events.append((topic, payload)))
    watcher.watch(project_id, initial_revision=initial["rev"])

    fd.edit_project_film(project_id, initial["rev"], [
        {"op": "set", "clip": "clip", "field": "title", "value": "Saved by Studio"},
    ], source="studio")
    _poll_changed_file(watcher)

    history = fd._history_read(project_id)
    assert len(history["past"]) == 1
    assert history["past"][0]["ops"][0]["op"] == "set"
    assert history["past"][0]["source"] == "studio"
    assert events == []



def test_external_write_between_initial_read_and_watcher_registration_is_not_lost(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "watch-registration-race"
    project = config.project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
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


def test_external_edit_then_studio_undo_redo_does_not_corrupt_shared_history(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "external-history-undo-redo"
    project = config.project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
    film_path = fd._film_path(project_id)
    film_path.write_text(FILM, encoding="utf-8")
    initial = fd.read_project_film(project_id)

    events = []
    watcher = FilmRevisionWatcher(lambda topic, payload: events.append((topic, payload)))
    watcher.watch(project_id, initial["rev"], initial_text=initial["text"])
    external = FILM.replace('at="0"', 'at="0.4"')
    film_path.write_text(external, encoding="utf-8")
    _poll_changed_file(watcher)
    assert len(fd._history_read(project_id)["past"]) == 1

    # The regular Studio undo handler has already moved the recorded entry to future.
    events.clear()
    fd.undo_redo_project_film(project_id, None, "undo")
    _poll_changed_file(watcher)
    history_after_undo = fd._history_read(project_id)
    assert len(history_after_undo["past"]) == 0
    assert len(history_after_undo["future"]) == 1
    assert history_after_undo["future"][0]["source"] == "external_file"
    assert film_path.read_text(encoding="utf-8") == FILM
    assert events == []

    fd.undo_redo_project_film(project_id, None, "redo")
    _poll_changed_file(watcher)
    history_after_redo = fd._history_read(project_id)
    assert len(history_after_redo["past"]) == 1
    assert history_after_redo["past"][0]["source"] == "external_file"
    assert history_after_redo["past"][0]["after"] == external
    assert history_after_redo["future"] == []
    assert film_path.read_text(encoding="utf-8") == external
    assert events == []


def test_external_agent_can_restore_an_old_snapshot_without_clearing_undoability(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "external-history-restored-snapshot"
    project = config.project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
    film_path = fd._film_path(project_id)
    film_path.write_text(FILM, encoding="utf-8")
    original = fd.read_project_film(project_id)

    events = []
    watcher = FilmRevisionWatcher(lambda topic, payload: events.append((topic, payload)))
    watcher.watch(project_id, original["rev"], initial_text=original["text"])
    version_b = FILM.replace('at="0"', 'at="0.25"')
    film_path.write_text(version_b, encoding="utf-8")
    _poll_changed_file(watcher)
    assert len(fd._history_read(project_id)["past"]) == 1

    # A new external write, not a Studio undo/redo action, even though these bytes
    # equal the old snapshot already present in history.
    version_a_again = FILM
    film_path.write_text(version_a_again, encoding="utf-8")
    _poll_changed_file(watcher)

    history = fd._history_read(project_id)
    assert len(history["past"]) == 2
    assert history["past"][1]["before"] == version_b
    assert history["past"][1]["after"] == version_a_again
    assert history["past"][1]["ops"][0]["op"] == "external_file"
    assert history["future"] == []
    assert film_path.read_text(encoding="utf-8") == version_a_again

    fd.undo_redo_project_film(project_id, None, "undo")
    assert film_path.read_text(encoding="utf-8") == version_b



def test_external_edit_that_restores_a_historical_revision_is_still_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "external-edit-restores-old-revision"
    project = config.project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
    film_path = fd._film_path(project_id)

    initial = FILM
    restored = FILM.replace('at="0"', 'at="0.5"')
    film_path.write_text(restored, encoding="utf-8")
    first = fd.record_external_film_revision(project_id, initial, restored)
    assert first["status"] == "recorded"

    # An independent external write restores the original bytes/revision. It is
    # a new edit event, not a duplicate of the first entry's 'before' snapshot.
    film_path.write_text(initial, encoding="utf-8")
    second = fd.record_external_film_revision(project_id, restored, initial)
    assert second["status"] == "recorded"

    history = fd._history_read(project_id)
    assert len(history["past"]) == 2
    assert history["past"][0]["before"] == initial
    assert history["past"][0]["after"] == restored
    assert history["past"][1]["before"] == restored
    assert history["past"][1]["after"] == initial


def test_external_in_place_write_with_restored_mtime_is_detected_by_content_audit(tmp_path, monkeypatch):
    """Metadata-preserving in-place writes must not bypass the film revision watcher."""
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "external-agent-preserved-metadata"
    project = config.project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
    film_path = fd._film_path(project_id)
    film_path.write_text(FILM, encoding="utf-8")
    initial = fd.read_project_film(project_id)
    events = []

    watcher = FilmRevisionWatcher(
        lambda topic, payload: events.append((topic, payload)),
        content_audit_interval_seconds=0.1,
    )
    watcher.watch(project_id, initial_revision=initial["rev"], initial_text=initial["text"])

    before_stat = film_path.stat()
    external = FILM.replace('at="0"', 'at="1"')
    assert len(external.encode("utf-8")) == before_stat.st_size
    with film_path.open("r+b") as handle:
        handle.write(external.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    os.utime(film_path, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
    after_stat = film_path.stat()
    assert (after_stat.st_dev, after_stat.st_ino, after_stat.st_size, after_stat.st_mtime_ns) == (
        before_stat.st_dev, before_stat.st_ino, before_stat.st_size, before_stat.st_mtime_ns
    )

    _poll_changed_file(watcher)

    current = fd.read_project_film(project_id)
    assert current["rev"] != initial["rev"]
    assert current["text"] == external
    history = fd._history_read(project_id)
    assert len(history["past"]) == 1
    assert history["past"][0]["source"] == "external_file"
    assert history["past"][0]["before"] == initial["text"]
    assert history["past"][0]["after"] == external
    assert len(events) == 1, events
    assert events[0][1]["phase"] == "revision_changed"
    assert events[0][1]["revision"] == current["rev"]


def test_external_scene_file_change_publishes_separate_refresh_event_without_film_revision_change(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "external-scene-resource-watch"
    project = config.project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
    film_path = fd._film_path(project_id)
    film_path.write_text(FILM, encoding="utf-8")
    scene_root = project / "scenes" / "titles"
    scene_root.mkdir(parents=True)
    scene_path = scene_root / "opening.html"
    scene_path.write_text("<html><body>Version one</body></html>", encoding="utf-8")
    initial = fd.read_project_film(project_id)
    events = []

    watcher = FilmRevisionWatcher(lambda topic, payload: events.append((topic, payload)))
    watcher.watch(project_id, initial_revision=initial["rev"], initial_text=initial["text"])
    scene_path.write_text("<html><body>Version two has changed</body></html>", encoding="utf-8")
    _poll_changed_file(watcher)

    current = fd.read_project_film(project_id)
    assert current["rev"] == initial["rev"]
    assert current["text"] == initial["text"]
    history = fd._history_read(project_id)
    assert history["past"] == []
    assert len(events) == 1, events
    topic, event = events[0]
    assert topic == "film"
    assert event["phase"] == "scene_changed"
    assert event["project_id"] == project_id
    assert event["source"] == "external_file"
    assert event["scene_revision"] != ""
    assert len(event["scene_revision"]) == 64
    assert event["paths"] == ["scenes/titles/opening.html"]
    assert event["changes"] == [
        {"path": "scenes/titles/opening.html", "kind": "modified"}
    ]

def test_scene_watcher_ignores_scenes_root_symlink_to_external_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "scene-root-symlink-boundary"
    project = config.project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
    film_path = fd._film_path(project_id)
    film_path.write_text(FILM, encoding="utf-8")

    outside = tmp_path / "outside-scenes"
    outside.mkdir()
    outside_file = outside / "private-scene.html"
    outside_file.write_text("<html>outside version one</html>", encoding="utf-8")
    (project / "scenes").symlink_to(outside, target_is_directory=True)

    initial = fd.read_project_film(project_id)
    events = []
    watcher = FilmRevisionWatcher(lambda topic, payload: events.append((topic, payload)))
    watcher.watch(project_id, initial_revision=initial["rev"], initial_text=initial["text"])
    assert watcher._projects[project_id]["scene_files"] == {}

    outside_file.write_text("<html>outside version two is longer</html>", encoding="utf-8")
    _poll_changed_file(watcher)

    assert events == [], events
    assert fd.read_project_film(project_id)["rev"] == initial["rev"]


def test_scene_watcher_does_not_follow_nested_symlink_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "nested-scene-symlink-boundary"
    project = config.project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
    film_path = fd._film_path(project_id)
    film_path.write_text(FILM, encoding="utf-8")

    scenes = project / "scenes"
    scenes.mkdir()
    local_scene = scenes / "opening.html"
    local_scene.write_text("<html>local version one</html>", encoding="utf-8")
    outside = tmp_path / "outside-nested-scenes"
    outside.mkdir()
    outside_file = outside / "private-scene.html"
    outside_file.write_text("<html>outside version one</html>", encoding="utf-8")
    (scenes / "linked").symlink_to(outside, target_is_directory=True)

    initial = fd.read_project_film(project_id)
    events = []
    watcher = FilmRevisionWatcher(lambda topic, payload: events.append((topic, payload)))
    watcher.watch(project_id, initial_revision=initial["rev"], initial_text=initial["text"])
    assert sorted(watcher._projects[project_id]["scene_files"]) == ["opening.html"]

    outside_file.write_text("<html>outside version two is longer</html>", encoding="utf-8")
    _poll_changed_file(watcher)
    assert events == [], events

    local_scene.write_text("<html>local version two is longer</html>", encoding="utf-8")
    _poll_changed_file(watcher)
    assert len(events) == 1, events
    assert events[0][1]["phase"] == "scene_changed"
    assert events[0][1]["paths"] == ["scenes/opening.html"]

