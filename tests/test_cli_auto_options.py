"""CLI contract tests for auto confirm semantics."""
import json

from video_director import pipeline
from video_director.__main__ import main


class _Result:
    final_path = "out/final.mp4"
    project_dir = "out/project"
    narration_duration = 1.0
    qc_report = {"ok": True}
    warnings = []


def test_auto_confirm_flag_is_forwarded(tmp_path, monkeypatch, capsys):
    script = tmp_path / "script.txt"
    script.write_text("测试文案", encoding="utf-8")
    seen = {}

    def fake_compose_auto(**kwargs):
        seen.update(kwargs)
        return _Result()

    monkeypatch.setattr(pipeline, "compose_auto", fake_compose_auto)
    # Isolate the CLI's implicit auto-run project id from persistent runner data;
    # this test verifies argument forwarding, not reopening an existing project.
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage"))
    code = main(["auto", "--script", str(script), "--confirm", "--json"])
    assert code is None or code == 0
    assert seen["auto_confirm"] is False
    assert seen["voice_rate"] == "+0%"
    json.loads(capsys.readouterr().out)


def test_auto_confirm_and_auto_flags_are_mutually_exclusive(tmp_path):
    script = tmp_path / "script.txt"
    script.write_text("测试文案", encoding="utf-8")
    import pytest
    with pytest.raises(SystemExit):
        main(["auto", "--script", str(script), "--confirm", "--auto"])
