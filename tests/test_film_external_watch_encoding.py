"""External file watch must surface decode failures and recover after a valid rewrite."""
from __future__ import annotations

import time
from pathlib import Path

from video_director import film_document as fd
from video_director.film_watch import FilmRevisionWatcher


FILM = '''<!doctype html>
<html><head></head><body>
<section id="visual">
  <video id="clip" src="assets/video.mp4#t=0,1" at="0"></video>
</section>
</body></html>'''


def test_invalid_utf8_external_write_publishes_broken_state_then_recovers(tmp_path: Path):
    project_id = "film-watch-utf8-recovery"
    film_path = tmp_path / "film.html"
    film_path.write_text(FILM, encoding="utf-8")
    events = []
    watcher = FilmRevisionWatcher(
        lambda topic, payload: events.append((topic, payload)),
        path_for_project=lambda _project_id: film_path,
        settle_seconds=0.05,
        interval_seconds=0.02,
    )
    watcher.watch(project_id)

    now = time.monotonic() + 1
    film_path.write_bytes(b"<!doctype html><html>\xff</html>")
    watcher.poll_once(now=now)
    watcher.poll_once(now=now + 1)

    assert events
    topic, broken = events[-1]
    assert topic == "film"
    assert broken["phase"] == "document_broken"
    assert broken["source"] == "external_file"
    assert "UTF-8" in broken["error"]

    repaired = FILM.replace('at="0"', 'at="0.5"')
    film_path.write_text(repaired, encoding="utf-8")
    watcher.poll_once(now=now + 2)
    watcher.poll_once(now=now + 3)

    topic, recovered = events[-1]
    assert topic == "film"
    assert recovered["phase"] == "revision_changed"
    assert recovered["source"] == "external_file"
    assert recovered["revision"] == fd.revision_of(repaired)



def test_first_external_write_invalid_utf8_is_reported_even_when_revision_matches_empty_baseline(tmp_path: Path):
    project_id = "film-watch-first-write-invalid-utf8"
    film_path = tmp_path / "film.html"
    events = []
    watcher = FilmRevisionWatcher(
        lambda topic, payload: events.append((topic, payload)),
        path_for_project=lambda _project_id: film_path,
        settle_seconds=0.05,
        interval_seconds=0.02,
    )
    watcher.watch(project_id)

    now = time.monotonic() + 1
    film_path.write_bytes(b"<!doctype html><html>\xff</html>")
    watcher.poll_once(now=now)
    watcher.poll_once(now=now + 1)

    assert events, "document_broken must be emitted even when the fallback text hashes to the empty baseline"
    topic, broken = events[-1]
    assert topic == "film"
    assert broken["phase"] == "document_broken"
    assert broken["error"] and "UTF-8" in broken["error"]


def test_same_project_id_on_new_storage_path_does_not_reuse_broken_state(tmp_path: Path):
    project_id = "film-watch-path-reuse"
    first_path = tmp_path / "first" / "film.html"
    second_path = tmp_path / "second" / "film.html"
    first_path.parent.mkdir(parents=True)
    second_path.parent.mkdir(parents=True)
    first_path.write_text(FILM, encoding="utf-8")
    active_path = {"value": first_path}
    events = []
    watcher = FilmRevisionWatcher(
        lambda topic, payload: events.append((topic, payload)),
        path_for_project=lambda _project_id: active_path["value"],
        settle_seconds=0.05,
        interval_seconds=0.02,
    )
    watcher.watch(project_id)

    broken_bytes = b"<html><body>partial write</body></html>"
    first_path.write_bytes(broken_bytes)
    now = time.monotonic() + 1
    watcher.poll_once(now=now)
    watcher.poll_once(now=now + 1)
    assert events and events[-1][1]["phase"] == "document_broken"
    first_broken_event = events[-1][1]

    # The same project ID can be re-created in a new storage root. The new
    # monitor state must not inherit the previous document_broken revision/error.
    events.clear()
    second_path.write_text(FILM, encoding="utf-8")
    active_path["value"] = second_path
    watcher.watch(project_id)
    second_path.write_bytes(broken_bytes)
    watcher.poll_once(now=now + 2)
    watcher.poll_once(now=now + 3)

    assert events, "path changes must reset watcher state so repeated broken files still emit document_broken"
    topic, second_broken_event = events[-1]
    assert topic == "film"
    assert second_broken_event["phase"] == "document_broken"
    assert second_broken_event["revision"] == first_broken_event["revision"]
    assert second_broken_event["error"] == first_broken_event["error"]
