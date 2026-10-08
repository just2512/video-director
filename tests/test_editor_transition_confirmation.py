"""Editor transition confirmation regression tests.

The key contract is intentionally narrow: proposing a transition must not mutate the
Composition IR; only the canonical editor command writes Scene.transition_out.
"""

import pytest

from video_director import config, editor
from video_director.models import CompositionIR


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    pid = "vd-transition-test"
    ir = CompositionIR.model_validate({
        "project": {"width": 1080, "height": 1920, "fps": 30, "duration": 8.0},
        "scenes": [
            {"id": "scene-1", "start": 0.0, "duration": 4.0, "text": "第一镜",
             "layers": [{"type": "graphic", "duration": 4.0}],
             "transition_out": {"type": "cut", "duration": 0.0}},
            {"id": "scene-2", "start": 4.0, "duration": 4.0, "text": "第二镜",
             "layers": [{"type": "graphic", "duration": 4.0}],
             "transition_out": {"type": "cut", "duration": 0.0}},
        ],
        "audio": [],
    })
    editor.save_composition(pid, ir)
    return pid


def test_transition_is_cut_before_confirmation(project):
    ir = editor.load_composition(project)
    assert ir.scenes[0].transition_out.type == "cut"
    assert ir.scenes[0].transition_out.duration == 0.0


def test_transition_confirmation_writes_canonical_ir(project):
    result = editor.execute_command(project, {
        "command": "set_transition_out",
        "targetId": "scene-1",
        "payload": {"type": "crossfade", "duration": 0.5},
        "author": "test",
    })
    assert result.status == "APPLIED"
    assert result.revision_id

    ir = editor.load_composition(project)
    assert ir.scenes[0].transition_out.type == "crossfade"
    assert abs(ir.scenes[0].transition_out.duration - 0.5) < 1e-9


def test_transition_confirmation_supports_undo(project):
    editor.execute_command(project, {
        "command": "set_transition_out",
        "targetId": "scene-1",
        "payload": {"type": "fade", "duration": 0.4},
        "author": "test",
    })
    result = editor.execute_command(project, {"command": "undo", "author": "test"})
    assert result.status == "APPLIED"

    ir = editor.load_composition(project)
    assert ir.scenes[0].transition_out.type == "cut"
    assert ir.scenes[0].transition_out.duration == 0.0


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"type": "wipe", "duration": 0.5}, "INVALID_PAYLOAD"),
        ({"type": "crossfade", "duration": -0.1}, "INVALID_PARAMETER"),
        ({"type": "crossfade", "duration": 3.0}, "INVALID_PARAMETER"),
    ],
)
def test_invalid_transition_is_rejected_without_mutation(project, payload, code):
    result = editor.execute_command(project, {
        "command": "set_transition_out",
        "targetId": "scene-1",
        "payload": payload,
        "author": "test",
    })
    assert result.status == "REJECTED"
    assert result.errors[0]["code"] == code

    ir = editor.load_composition(project)
    assert ir.scenes[0].transition_out.type == "cut"
