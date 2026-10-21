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
