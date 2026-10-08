"""Editor Session + OpenCut interaction fusion contracts."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "webui" / "static"


def test_editor_session_is_part_of_the_single_workbench():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '/static/js/editor-session.js' in index
    assert 'id="tl-snap-toggle"' in index
    assert 'id="tl-box-toggle"' in index


def test_session_owns_snap_box_select_without_creating_a_second_selection():
    js = (STATIC / "js" / "editor-session.js").read_text(encoding="utf-8")
    assert "window.editorSessionSnapTime = snapTime" in js
    assert "select('scene'" in js
    assert "select('layer'" in js
    assert "localStorage" in js
    assert "stopImmediatePropagation" in js


def test_multi_scene_move_uses_one_command_revision():
    js = (STATIC / "js" / "editor-session.js").read_text(encoding="utf-8")
    editor = (ROOT / "video_director" / "editor.py").read_text(encoding="utf-8")
    assert 'subcommand: ' + "'move'" in js
    assert 'payload: {subcommand: ' + "'move'" in js
    assert 'if sub == "move"' in editor
    assert 'anchorId' in editor
