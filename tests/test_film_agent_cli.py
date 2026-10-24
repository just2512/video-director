"""Revision-safe film CLI contract for external coding agents."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from video_director import config, film_document as fd


FILM = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden}</style></head>
<body><section id="video-main" data-label="主画面">
<video id="footage" src="assets/footage.mp4#t=0,5" at="0"></video>
</section></body></html>'''


def _cli(repo_root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "video_director", *args],
        cwd=repo_root, env=env, capture_output=True, text=True, timeout=30,
    )


def test_agent_cli_edit_is_revision_guarded_and_enters_shared_history(tmp_path, monkeypatch):
    storage_root = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    project_id = "agent-cli-contract"
    project = config.project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
    (project / fd.FILM_NAME).write_text(FILM, encoding="utf-8")
    before = fd.read_project_film(project_id)
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["VIDEO_DIRECTOR_STORAGE_ROOT"] = str(storage_root)
    ops_path = tmp_path / "film-ops.json"
    ops_path.write_text(json.dumps([
        {"op": "set", "clip": "footage", "field": "at", "value": 0.5}
    ]), encoding="utf-8")

    applied = _cli(
        repo_root, env, "film", "edit", "--project-id", project_id,
        "--base-revision", before["rev"], "--ops-file", str(ops_path),
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr
    result = json.loads(applied.stdout)
    assert result["ok"] is True
    assert result["base_revision"] == before["rev"]
    assert result["revision"] != before["rev"]
    assert result["operation_count"] == 1
    assert result["operations"][0]["op"] == "set"
    committed_revision = result["revision"]

    # Reusing the old revision must not silently overwrite/rebase the new intent.
    ops_path.write_text(json.dumps([
        {"op": "set", "clip": "footage", "field": "at", "value": 0.75}
    ]), encoding="utf-8")
    stale = _cli(
        repo_root, env, "film", "edit", "--project-id", project_id,
        "--base-revision", before["rev"], "--ops-file", str(ops_path),
    )
    assert stale.returncode != 0
    failure = json.loads(stale.stdout)
    assert failure["ok"] is False
    assert failure["code"] == "REVISION_CONFLICT"

    current = fd.read_project_film(project_id)
    clip = current["doc"]["tracks"][0]["clips"][0]
    assert current["rev"] == committed_revision
    assert float(clip["attrs"]["at"]) == 0.5
    history = fd._history_read(project_id)
    assert len(history["past"]) == 1
    assert history["past"][0]["before_rev"] == before["rev"]
    assert history["past"][0]["after_rev"] == committed_revision
    assert history["past"][0]["source"] == "cli"
    assert result["source"] == "cli"


def test_agent_cli_diff_from_after_revision_uses_the_after_snapshot(tmp_path, monkeypatch):
    storage_root = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    project_id = "agent-cli-diff-after-revision"
    project = config.project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
    (project / fd.FILM_NAME).write_text(FILM, encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["VIDEO_DIRECTOR_STORAGE_ROOT"] = str(storage_root)
    ops_path = tmp_path / "film-ops.json"

    initial = fd.read_project_film(project_id)
    ops_path.write_text(json.dumps([
        {"op": "set", "clip": "footage", "field": "at", "value": 0.5}
    ]), encoding="utf-8")
    first = _cli(
        repo_root, env, "film", "edit", "--project-id", project_id,
        "--base-revision", initial["rev"], "--ops-file", str(ops_path),
    )
    assert first.returncode == 0, first.stdout + first.stderr
    first_revision = json.loads(first.stdout)["revision"]

    ops_path.write_text(json.dumps([
        {"op": "set", "clip": "footage", "field": "at", "value": 1.0}
    ]), encoding="utf-8")
    second = _cli(
        repo_root, env, "film", "edit", "--project-id", project_id,
        "--base-revision", first_revision, "--ops-file", str(ops_path),
    )
    assert second.returncode == 0, second.stdout + second.stderr

    diff = _cli(
        repo_root, env, "film", "diff", "--project-id", project_id,
        "--from-revision", first_revision,
    )
    assert diff.returncode == 0, diff.stdout + diff.stderr
    removed = [line for line in diff.stdout.splitlines()
               if line.startswith("-") and 'id="footage"' in line]
    added = [line for line in diff.stdout.splitlines()
             if line.startswith("+") and 'id="footage"' in line]
    assert len(removed) == 1, diff.stdout
    assert len(added) == 1, diff.stdout
    assert 'at="0.5"' in removed[0], diff.stdout
    assert 'at="1"' in added[0], diff.stdout
