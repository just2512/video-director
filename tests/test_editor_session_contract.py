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


def test_box_select_is_single_transaction_via_selection_service():
    """B1/B3 契约（2026-10-09 对抗核查）：框选不得在 Session 内手写 SEL 赋值，
    必须经唯一 Selection 写入者 select() 的批量事务；拖动全程零
    selection:changed（防整树重渲打掉框选框），提交只在 endBox 一次。"""
    session = (STATIC / "js" / "editor-session.js").read_text(encoding="utf-8")
    selection = (STATIC / "js" / "selection.js").read_text(encoding="utf-8")
    # Session 侧：提交走 select()，不允许第二套写入（SEL.selectionSet = 出现在
    # selectRect/commitBoxSelection 里就是违规）
    assert "select('scene'" in session
    assert "select('layer'" in session
    assert "SEL.selectionSet = " not in session, \
        "Session 不得手写 SEL.selectionSet（第二套 Selection 写入器，违反 §63）"
    # selection.js 提供批量事务模式（boxIds）且仍只 emit 一次
    assert "boxIds" in selection
    assert selection.count("_emitSelectionChanged") >= 1
    # 拖动路径（updateBox）只做视觉高亮，不提交；提交只在 endBox
    assert "tl-box-hit" in session and "tl-box-hit" in \
        (STATIC / "css" / "timeline.css").read_text(encoding="utf-8")
    import re
    update_block = session[session.index("function updateBox"):session.index("function endBox")]
    assert "CustomEvent" not in update_block and "dispatchEvent" not in update_block, \
        "拖框中不得派发 selection:changed（updateBox 只允许视觉高亮）"
    assert "commitBoxSelection(" not in update_block, "提交只允许出现在 endBox"
    end_block = session[session.index("function endBox"):session.index("function boxHitIds")] \
        if session.index("function endBox") < session.index("function boxHitIds") \
        else session[session.index("function endBox"):]
    assert "commitBoxSelection(" in end_block
    # commitBoxSelection 必须走 select()（唯一写入者），不得手写 SEL
    commit_block = session[session.index("function commitBoxSelection"):session.index("function startBox")]
    assert "SEL.selectionSet" not in commit_block and "SEL.selection" not in commit_block
    assert "select('layer'" in commit_block and "select('scene'" in commit_block


def test_snap_candidates_cover_five_families_and_shift_disables():
    """§2 snap 契约：候选=开头/结尾/场景边/图层边/关键帧/播放头 五族齐全；
    Shift 临时关闭；播放头拖动消费 editorSessionSnapTime（真实接线）。"""
    session = (STATIC / "js" / "editor-session.js").read_text(encoding="utf-8")
    timeline = (STATIC / "js" / "timeline.js").read_text(encoding="utf-8")
    for token in ("'开头'", "'结尾'", "场景 ' + (i + 1) + ' 起点",
                  "图层起点", "关键帧 · ", "'播放头'"):
        assert token in session, f"snap 候选缺 {token}"
    assert "options.shiftKey" in session, "Shift 临时关闭吸附缺失"
    # 播放头真实消费（不是只有导出）
    assert "editorSessionSnapTime(at" in timeline


def test_session_ui_actions_exposed_in_manifest():
    """§7/§58 契约：ui.editor_session_snap/box 必须在 manifest 全上下文组里
    可达（dispatcher 可发现），否则 ORPHAN/不可派发。"""
    import sys
    sys.path.insert(0, str(ROOT))
    from video_director import action_registry as ar
    exposed = {a["id"] for g in ar.manifest_for_project(None).values() for a in g}
    assert "ui.editor_session_snap" in exposed
    assert "ui.editor_session_box" in exposed
