"""Studio export and inspector must follow the FilmDocument FPS, not hard-code 30."""
from pathlib import Path


def test_film_studio_uses_saved_fps_for_export_and_frame_step():
    root = Path(__file__).resolve().parents[1]
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    assert "expected_revision:revision});" in studio
    assert "expected_revision:revision,fps:30" not in studio
    assert "var projectFps=Math.max(1,Math.min(60,num(S.data&&S.data.fps,30)))" in studio
    assert "step=\"'+esc(frameStep)+'\"" in studio
    assert "fps:Math.max(1,Math.min(60,num(S.data&&S.data.fps,30)))" in studio
    assert "fps?: number;" in component
    assert "step={1 / Math.max(1, Math.min(60, clip.fps || 30))}" in component
