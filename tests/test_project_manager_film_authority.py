from __future__ import annotations

import json

import pytest

from video_director import config, film_document, project_manager as pm, store


FILM_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=640,height=360">
<meta name="film-fps" content="30">
</head>
<body>
<section id="v1" data-label="主画面">
  <video id="shot-1" src="assets/a.mp4#t=0,5" at="1"></video>
</section>
<section id="a1" data-label="旁白">
  <audio id="voice-1" src="assets/voice.wav#t=0,3" at="0"></audio>
</section>
</body>
</html>
"""


@pytest.fixture(autouse=True)
def _isolated_project_store(tmp_path, monkeypatch):
    root = tmp_path / "composer"
    root.mkdir()
    monkeypatch.setattr(config, "composer_storage_root", lambda: root)
    store.init_db()


def _project_with_film(project_id: str, film_text: str, *, stale_duration: float = 99.0) -> None:
    root = config.project_dir(project_id)
    config.init_project_dirs(root)
    (root / "plans" / "composition.json").write_text(json.dumps({
        "project": {"title": project_id, "width": 1080, "height": 1920,
                    "fps": 30, "duration": stale_duration},
        "scenes": [{"id": "stale-scene", "start": 0, "duration": stale_duration,
                    "text": "stale", "layers": []}],
        "audio": [], "watermarks": [],
    }), encoding="utf-8")
    (root / film_document.FILM_NAME).write_text(film_text, encoding="utf-8")
    store.create_project(project_id, "Film authority fixture", {"name": project_id})


def test_project_index_reports_film_revision_and_timeline_instead_of_stale_composition():
    project_id = "web-film-authority"
    _project_with_film(project_id, FILM_HTML)

    entry = next(item for item in pm.list_index() if item["project_id"] == project_id)

    assert entry["editable"] is True
    assert entry["timeline_authority"] == "film.html"
    assert entry["revision"] == film_document.revision_of(FILM_HTML)
    assert entry["film_exists"] is True
    assert entry["film_valid"] is True
    assert entry["timeline_valid"] is True
    assert entry["scene_count"] == 1
    assert entry["duration"] == 6.0
    assert entry["clip_count"] == 2
    assert entry["track_count"] == 2


def test_project_index_never_uses_stale_composition_when_film_html_is_invalid():
    project_id = "web-film-invalid"
    malformed = "<html><head></head><body><section id='broken'><video"
    _project_with_film(project_id, malformed, stale_duration=77.0)

    entry = next(item for item in pm.list_index() if item["project_id"] == project_id)

    assert entry["editable"] is True
    assert entry["timeline_authority"] == "film.html"
    assert entry["revision"] == film_document.revision_of(malformed)
    assert entry["film_exists"] is True
    assert entry["film_valid"] is False
    assert entry["timeline_valid"] is False
    assert entry["timeline_error"]["code"] == "INVALID_FILM"
    assert entry["scene_count"] == 0
    assert entry["duration"] == 0.0
