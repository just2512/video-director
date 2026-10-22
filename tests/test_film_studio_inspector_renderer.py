"""The legacy inspector renderer remains syntactically and structurally valid."""
from pathlib import Path


def test_film_studio_inspector_renderer_has_no_detached_else_branch():
    source = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "function renderInspector()" in source
    assert "renderElementInspector(element);" in source
    assert "renderElementInspector(element);\n    else{" not in source
