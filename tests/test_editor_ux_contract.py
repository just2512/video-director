"""Editor visual UX contract tests.

这些测试只锁定本轮新增的接线契约，不替代真实 Chromium E2E：
- 真实预览不依赖 draft.mp4
- 草稿按钮走 UI Action -> workflow polling
- Storyboard/Timeline 有真实素材入口
- AI 导演控制面板存在
- transition 走 Editor Command
- material match endpoint 已声明
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "webui" / "static"


def test_editor_ux_files_are_wired():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "js" / "editor-enhanced.js").read_text(encoding="utf-8")
    css = (STATIC / "css" / "editor-enhanced.css").read_text(encoding="utf-8")

    assert "/static/css/editor-enhanced.css" in index
    assert "/static/js/editor-enhanced.js" in index
    assert 'id="editor-storyboard"' in index
    assert 'id="director-control-grid"' in index
    assert 'id="edx-render-draft"' in index
    assert 'data-action="ui.render_draft"' in index

    assert "/api/editor/material_match" in js
    assert "/api/workflows" in js
    assert "command:'set_transition'" in js
    assert "window.editorRenderTimeline" in js
    assert "liveSceneAt" in js
    assert "enhancedRefreshPreview(false)" in js
    assert "Composition 有任何编辑后" in js
    assert "按文案匹配我的素材" in js
    assert "待确认" in js
    assert ".edx-story-card" in css


def test_draft_render_action_and_transition_command_are_registered():
    registry = (ROOT / "video_director" / "action_registry.py").read_text(encoding="utf-8")
    editor = (ROOT / "video_director" / "editor.py").read_text(encoding="utf-8")
    dispatcher = (STATIC / "js" / "action-dispatcher.js").read_text(encoding="utf-8")

    assert 'ActionSpec("ui.render_draft"' in registry
    assert 'render_draft: () =>' in dispatcher
    assert '"set_transition"' in editor
    assert 'elif cmd_name == "set_transition"' in editor
    assert "TransitionOut," in editor


def test_composition_and_material_match_expose_asset_preview_fields():
    server = (ROOT / "webui" / "server.py").read_text(encoding="utf-8")
    assert 'route == "/api/editor/composition"' in server
    assert '"asset_file"' in server
    assert 'route == "/api/editor/material_match"' in server
    assert '"layer_target"' in server
    assert '"asset_duration"' in server
    assert '"asset_has_audio"' in server


def test_renderer_supports_multiple_original_tracks():
    renderer = (ROOT / "video_director" / "renderer.py").read_text(encoding="utf-8")
    assert "for original_track in [" in renderer
    assert "AudioTrackType.original" in renderer
    assert "adelay=" in renderer
