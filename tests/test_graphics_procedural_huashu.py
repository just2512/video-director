from pathlib import Path

import pytest

from video_director.graphics.compiler import CompileError, compile_to_html
from video_director.graphics.preflight import deterministic_preflight
from video_director.graphics.program import GraphicProgram, GraphicScene


GRAMMARS = (
    "y1_kurzgesagt",
    "y2_vox",
    "y3_whiteboard",
    "y4_storytime",
    "y5_kinetic_type",
    "t1_3b1b",
    "t2_keynote_ui",
    "t3_finance_chart",
)

FORBIDDEN = (
    "Date.now",
    "performance.now",
    "Math.random",
    "requestAnimationFrame",
    "crypto.getRandomValues",
    "fetch(",
    "XMLHttpRequest",
    "WebSocket",
    "@import",
    "url(http",
    "url(https",
    '<script src',
)


def _program(grammar: str, *, duration: float = 2.0, cues=None) -> GraphicProgram:
    scene = GraphicScene(
        id="proc-01",
        role="body",
        start=0.0,
        duration=duration,
        engine="procedural",
        provider_id="huashu",
        program_id=grammar,
        program_data={
            "cues": cues if cues is not None else [
                {"at": 0.0, "kind": "title", "text": "测试", "sub": "程序化动画"}
            ],
            "data": {},
        },
    )
    return GraphicProgram(
        id=f"test-{grammar}",
        width=640,
        height=360,
        fps=30,
        duration=duration,
        scenes=[scene],
    )


@pytest.mark.parametrize("grammar", GRAMMARS)
def test_huashu_grammar_compiles_into_shared_graphics_runtime(grammar):
    html = compile_to_html(_program(grammar))
    assert 'data-engine="procedural"' in html
    assert f'data-procedural-id="{grammar}"' in html
    assert 'window.__gf' in html
    assert deterministic_preflight(html).status == "PASS"


def test_native_and_procedural_scenes_share_one_program():
    native = GraphicScene(
        id="native-01",
        role="hook",
        start=0.0,
        duration=2.5,
        block_id="kb.title",
        slot_values={"title": "原生图形", "body": ""},
    )
    procedural = GraphicScene(
        id="proc-01",
        role="body",
        start=2.5,
        duration=2.0,
        engine="procedural",
        provider_id="huashu",
        program_id="y5_kinetic_type",
        program_data={
            "cues": [
                {"at": 0.0, "kind": "title", "text": "程序化", "sub": "动画"}
            ]
        },
    )
    program = GraphicProgram(
        id="mixed-test",
        width=640,
        height=360,
        fps=30,
        duration=4.5,
        scenes=[native, procedural],
    )
    html = compile_to_html(program)
    assert 'data-engine="procedural"' in html
    assert 'data-block="kb.title"' in html
    assert deterministic_preflight(html).status == "PASS"


def test_missing_procedural_provider_is_rejected():
    scene = GraphicScene(
        id="proc-01",
        role="body",
        start=0.0,
        duration=2.0,
        engine="procedural",
        provider_id="missing-provider",
        program_id="y5_kinetic_type",
        program_data={"cues": []},
    )
    program = GraphicProgram(
        id="missing-provider-test",
        width=640,
        height=360,
        fps=30,
        duration=2.0,
        scenes=[scene],
    )
    with pytest.raises(CompileError, match="missing-provider"):
        compile_to_html(program)


def test_huashu_vendored_source_is_network_free():
    root = Path(__file__).resolve().parents[1] / "third_party" / "huashu-art-motion" / "clip"
    files = list((root / "lib").glob("*.js")) + list((root / "clips").glob("*.js"))
    assert files
    for path in files:
        source = path.read_text(encoding="utf-8")
        bad = [token for token in FORBIDDEN if token in source]
        assert not bad, f"{path}: forbidden runtime token(s): {bad}"


def test_y5_kinetic_type_browser_smoke(tmp_path):
    from video_director.graphics.render import render_still

    program = _program(
        "y5_kinetic_type",
        duration=2.0,
        cues=[
            {"at": 0.0, "kind": "title", "text": "AI 导演", "sub": "程序化动画"},
            {"at": 1.0, "kind": "highlight", "data": {"word": "AI"}},
        ],
    )
    out = tmp_path / "huashu-y5.png"
    render_still(
        compile_to_html(program),
        str(out),
        width=320,
        height=180,
        frame=10,
    )
    assert out.is_file() and out.stat().st_size > 0
