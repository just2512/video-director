from __future__ import annotations

from pathlib import Path

import pytest

from video_director import config, film_document, tool_executor
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

def test_unreadable_film_authority_is_unknown_and_blocks_legacy_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    project_id = "context-unreadable-film"
    _write_film(project_id, FILM_HTML)
    film_path = config.project_dir(project_id) / film_document.FILM_NAME
    original_read_text = Path.read_text

    def deny_film_read(path, *args, **kwargs):
        if path == film_path:
            raise PermissionError("film.html read denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_film_read)
    context = inspect_project_context(project_id, {"type": "clip", "id": "shot-1"})

    assert context["authority"] == "unknown"
    assert "PermissionError" in context["authority_error"]
    rejected = tool_executor.film_authority_rejection(project_id, "timeline.move")
    assert rejected is not None
    assert rejected["status"] == "REJECTED"
    assert rejected["code"] == "PROJECT_AUTHORITY_UNKNOWN"


def test_legacy_authority_check_rejects_filesystem_uncertainty(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    project_id = "context-film-stat-denied"
    _write_film(project_id, FILM_HTML)
    film_path = config.project_dir(project_id) / film_document.FILM_NAME
    original_stat = Path.stat

    def deny_film_stat(path, *args, **kwargs):
        if path == film_path:
            raise PermissionError("film.html stat denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", deny_film_stat)
    with pytest.raises(film_document.FilmError) as caught:
        film_document.assert_legacy_composition_access(project_id, "编辑旧时间线")
    assert caught.value.code == "PROJECT_AUTHORITY_UNKNOWN"
    assert caught.value.status == 503



def test_film_authority_allows_isolated_voice_preview_but_blocks_legacy_narration(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    project_id = "context-film-voice-preview"
    _write_film(project_id, FILM_HTML)

    assert tool_executor.film_authority_rejection(project_id, "tts.preview_voice") is None
    rejected = tool_executor.film_authority_rejection(project_id, "tts.preview_narration")
    assert rejected is not None
    assert rejected["status"] == "REJECTED"
    assert rejected["code"] == "FILM_AUTHORITY_ACTIVE"

def test_legacy_project_context_uses_one_composition_revision_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    from video_director import editor
    from video_director.models import CompositionIR, Layer, LayerType, ProjectSpec, SceneIR

    project_id = "context-legacy-snapshot"
    ir = CompositionIR(
        project=ProjectSpec(width=160, height=90, fps=30, duration=1.0),
        scenes=[SceneIR(
            id="legacy-scene", start=0, duration=1.0, text="legacy",
            layers=[Layer(type=LayerType.image, duration=1.0)],
        )],
    )
    editor.save_composition(project_id, ir)
    original_snapshot = editor.load_composition_snapshot
    calls = []

    def tracked_snapshot(pid):
        calls.append(pid)
        return original_snapshot(pid)

    monkeypatch.setattr(editor, "load_composition_snapshot", tracked_snapshot)
    context = inspect_project_context(project_id)

    assert calls == [project_id]
    assert context["authority"] == "composition.json"
    assert context["revision"] == original_snapshot(project_id)[1]
    assert context["composition_version"] == context["revision"]

def test_project_save_reports_version_from_the_same_composition_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    from video_director import editor
    from video_director.models import CompositionIR, Layer, LayerType, ProjectSpec, SceneIR

    project_id = "project-save-snapshot"
    ir = CompositionIR(
        project=ProjectSpec(width=160, height=90, fps=30, duration=1.0),
        scenes=[SceneIR(
            id="save-scene", start=0, duration=1.0, text="save snapshot",
            layers=[Layer(type=LayerType.image, duration=1.0)],
        )],
    )
    editor.save_composition(project_id, ir)
    original_snapshot = editor.load_composition_snapshot
    snapshots = []

    def tracked_snapshot(pid):
        snapshots.append(pid)
        return original_snapshot(pid)

    def forbidden_split_read(*_args, **_kwargs):
        raise AssertionError("project.save must not split CompositionIR and version reads")

    monkeypatch.setattr(editor, "load_composition_snapshot", tracked_snapshot)
    monkeypatch.setattr(editor, "load_composition", forbidden_split_read)
    monkeypatch.setattr(editor, "composition_version", forbidden_split_read)
    result = tool_executor.execute_tool(project_id, {"tool": "project.save"})

    assert result["ok"] is True, result
    assert result["status"] == "OK"
    assert result["result"]["version"] == original_snapshot(project_id)[1]
    assert result["result"]["duration"] == 1.0
    assert snapshots == [project_id, project_id]


def test_all_legacy_pipeline_and_renderer_entrypoints_fail_closed_for_film_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    from video_director import pipeline, renderer

    project_id = "context-legacy-entrypoint-gate"
    _write_film(project_id, FILM_HTML)
    project_dir = config.project_dir(project_id)
    composition_path = project_dir / "plans" / "composition.json"
    composition_path.parent.mkdir(parents=True, exist_ok=True)
    composition_path.write_text('{"must_remain":"unchanged"}', encoding="utf-8")
    before = composition_path.read_bytes()
    render_workdir = tmp_path / "legacy-render-work"

    calls = [
        lambda: pipeline.compose_project("legacy", [], [], project_id),
        lambda: pipeline.compose_auto("legacy", [], [], project_id),
        lambda: pipeline.resume_after_confirm(project_id),
        lambda: pipeline._recompose_with_size(project_id, 640, 360),
        lambda: renderer.render_composition(None, {}, render_workdir, project_id=project_id),
    ]
    for call in calls:
        with pytest.raises(film_document.FilmError) as caught:
            call()
        assert caught.value.code == "FILM_DOCUMENT_AUTHORITATIVE"

    assert composition_path.read_bytes() == before
    assert not (project_dir / "input" / "script.txt").exists()
    assert not (project_dir / "plans" / "scenes.json").exists()
    assert not render_workdir.exists()


def test_dangling_film_symlink_never_reactivates_legacy_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    project_id = "context-dangling-film-link"
    _write_film(project_id, FILM_HTML)
    project_dir = config.project_dir(project_id)
    film_path = project_dir / film_document.FILM_NAME
    film_path.unlink()
    film_path.symlink_to("missing-film-target.html")
    (project_dir / "plans").mkdir(parents=True, exist_ok=True)
    (project_dir / "plans" / "composition.json").write_text('{"stale":true}', encoding="utf-8")

    context = inspect_project_context(project_id)

    assert context["authority"] == "unknown"
    with pytest.raises(film_document.FilmError) as caught:
        film_document.assert_legacy_composition_access(project_id, "旧时间线写入")
    assert caught.value.code == "FILM_DOCUMENT_AUTHORITATIVE"
