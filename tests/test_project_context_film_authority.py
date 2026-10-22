from __future__ import annotations

from video_director import config, film_document
from video_director.agent_gateway.context import project_context_snapshot
from video_director.project_context import inspect_project_context


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


def _write_film(project_id: str, source: str) -> None:
    project_dir = config.project_dir(project_id)
    (project_dir / film_document.FILM_NAME).write_text(source, encoding="utf-8")


def test_project_context_uses_film_revision_without_requiring_composition(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    project_id = "context-film-project"
    _write_film(project_id, FILM_HTML)

    selection = {"type": "clip", "id": "shot-1"}
    context = inspect_project_context(project_id, selection)

    expected_revision = film_document.revision_of(FILM_HTML)
    assert context["authority"] == "film.html"
    assert context["revision"] == expected_revision
    assert context["film"]["revision"] == expected_revision
    assert context["film"]["valid"] is True
    assert context["project"]["width"] == 640
    assert context["project"]["height"] == 360
    assert context["project"]["duration"] == 6
    assert context["timeline"]["clip_count"] == 2
    assert context["assets"] == {"video": 1, "image": 0, "audio": 1}
    assert context["selection"]["ids"] == ["shot-1"]
    assert context["film"]["tracks"][0]["clips"][0]["id"] == "shot-1"


def test_invalid_film_context_never_falls_back_to_legacy_composition(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    project_id = "context-invalid-film"
    project_dir = config.project_dir(project_id)
    (project_dir / "plans").mkdir(parents=True, exist_ok=True)
    (project_dir / "plans" / "composition.json").write_text('{"stale": true}', encoding="utf-8")
    malformed = "<html><head></head><body><section id='broken'><video"
    _write_film(project_id, malformed)

    context = inspect_project_context(project_id, {"type": "clip", "id": "gone"})

    assert context["authority"] == "film.html"
    assert context["film"]["exists"] is True
    assert context["film"]["valid"] is False
    assert context["film"]["revision"] == film_document.revision_of(malformed)
    assert context["film"]["error"]["code"] == "INVALID_FILM"
    assert context["timeline"]["valid"] is False
    assert context["project"]["duration"] is None


def test_agent_gateway_context_exposes_same_film_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    project_id = "context-agent-film"
    _write_film(project_id, FILM_HTML)

    shared = inspect_project_context(project_id, {"type": "clip", "id": "voice-1"})
    gateway = project_context_snapshot(project_id, {"type": "clip", "id": "voice-1"})

    assert gateway["authority"] == "film.html"
    assert gateway["revision"] == shared["revision"]
    assert gateway["film"]["revision"] == shared["film"]["revision"]
    assert gateway["composition_version"] is None
    assert gateway["selection"]["ids"] == ["voice-1"]
