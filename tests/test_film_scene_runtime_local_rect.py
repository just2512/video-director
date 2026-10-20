"""Film scene inspector distinguishes CSS layout coordinates from painted viewport bounds."""
from pathlib import Path


def test_scene_inspector_reports_css_local_rect_separately_from_painted_rect():
    source = (Path(__file__).resolve().parents[1] / "video_director" / "film_scene_runtime.py").read_text(encoding="utf-8")
    assert "var localRect = {" in source
    assert "x: cssPixel(style.left, rect.x)" in source
    assert "y: cssPixel(style.top, rect.y)" in source
    assert "width: cssPixel(style.width, rect.width)" in source
    assert "height: cssPixel(style.height, rect.height)" in source
    assert "localRect: localRect" in source
