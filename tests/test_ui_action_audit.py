"""方案 §77/§78 UI Action Audit 契约测试（机械检查按钮功能完整性）。

- 每个业务按钮必须 data-action 或属纯 UI 白名单（§77 机械规则）
- 每个 data-action 必须在 Action Registry（MISSING_ACTION=0）
- UI 型动作必须在 context groups 暴露（dispatcher uiMap 可达）
"""
import pytest

from video_director import action_registry as ar
from video_director.ui_action_audit import audit_ui


class TestUiActionAudit:
    def test_zero_missing_action(self):
        """§78：所有 data-action 均在 Registry（无 MISSING_ACTION）。"""
        rep = audit_ui()
        assert not rep["missing_action"], rep["missing_action"][:5]

    def test_zero_orphan_button(self):
        """§77：无白名单外业务 onclick（ORPHAN_BUTTON=0）。"""
        rep = audit_ui()
        assert not rep["orphan_button"], rep["orphan_button"][:5]

    def test_audit_covers_all_sources(self):
        """§78：扫描 index.html + 全部 js（23 文件量级）。"""
        rep = audit_ui()
        assert rep["scanned_files"] >= 20

    def test_ui_actions_dispatchable(self):
        """§58：ui 型动作全部在 context groups（前端 uiMap 可达）。"""
        groups = ar.manifest_for_project(None)
        exposed = {a["id"] for g in groups.values() for a in g}
        missing = [a.id for a in ar.registry().values()
                   if a.ui and a.id not in exposed]
        assert not missing, f"ui 型动作未暴露给 dispatcher: {missing}"

    def test_add_edit_all_tracks_is_registered_and_uses_timeline_command(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        action = ar.get_action("ui.add_edit_all_tracks")
        assert action is not None
        assert action.ui == "add_edit_all_tracks"
        assert "add_edit_all_tracks" in (root / "webui" / "static" / "js" / "action-dispatcher.js").read_text(encoding="utf-8")
        timeline = (root / "webui" / "static" / "js" / "timeline.js").read_text(encoding="utf-8")
        assert "editorAddEditAllTracks" in timeline
        html = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
        assert 'data-action="ui.add_edit_all_tracks"' in html

    def test_registry_backrefs_alive(self):
        """§71 四层证据静态层：Action→Capability 全在 manifest。"""
        from video_director.capability_registry.manifest import manifest
        mf = manifest()
        bad = [a.id for a in ar.registry().values()
               if a.capability not in ("noop", "preview_voice")
               and a.capability not in mf and not a.ui]
        assert not bad, bad


def test_lift_layer_to_playhead_is_registered_and_connected():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    action = ar.get_action("ui.lift_layer_to_playhead")
    assert action is not None
    assert action.ui == "lift_layer_to_playhead"
    dispatcher = (root / "webui" / "static" / "js" / "action-dispatcher.js").read_text(encoding="utf-8")
    timeline = (root / "webui" / "static" / "js" / "timeline.js").read_text(encoding="utf-8")
    html = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "lift_layer_to_playhead" in dispatcher
    assert "editorLiftSelectedLayerToPlayhead" in timeline
    assert 'data-action="ui.lift_layer_to_playhead"' in html
