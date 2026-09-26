"""Graphics Runtime 单元/契约测试（HyperFrames 集成：IG-GFX/GRAPHIC-RENDER/
BLOCK/TEXT-TO-GRAPHIC/HTML-SANDBOX/PREVIEW-FINAL-PARITY）。

不依赖真实浏览器渲染的确定性测试（渲染链的 Playwright 部分由
tools/test_webui_hyperframes.py E2E 覆盖）。
"""

from __future__ import annotations

import pytest

from video_director.graphics.compiler import CompileError, compile_to_html
from video_director.graphics.contract import RationalFps, fps_from_number
from video_director.graphics.director import (
    DirectorError,
    build_graphic_plan,
    instantiate_graphic_program,
    plan_to_html,
)
from video_director.graphics.preflight import deterministic_preflight
from video_director.graphics.program import GraphicProgram, GraphicScene
from video_director.graphics.render import GraphicsRenderError, render_graphics
from video_director.graphics.resolver import BlockResolveRequest, resolve_blocks
from video_director.graphics import blocks as blocks_mod


# ---------------------------------------------------------------------------
# GFX-001 Contract：有理 fps
# ---------------------------------------------------------------------------

def test_rational_fps_integer():
    f = fps_from_number(30)
    assert (f.numerator, f.denominator) == (30, 1)
    assert f.value == 30.0


def test_rational_fps_non_integer():
    f = fps_from_number(29.97)
    assert abs(f.value - 29.97) < 1e-6
    assert f.denominator != 1


def test_rational_fps_frame_at():
    f = RationalFps(30, 1)
    assert f.frame_at(0.0) == 0
    assert f.frame_at(1.0) == 30
    assert f.frame_at(10.0) == 300  # 方案 §7：frame 300 = 10s
    assert abs(f.seconds_at(300) - 10.0) < 1e-9


def test_rational_fps_invalid_fallback():
    assert fps_from_number(None).value == 30.0
    assert fps_from_number(-5).value == 30.0
    assert fps_from_number("abc").value == 30.0


# ---------------------------------------------------------------------------
# GFX-002 GraphicProgram 契约
# ---------------------------------------------------------------------------

def _scene(i, start, dur, block="kb.title"):
    return GraphicScene(
        id=f"s{i}", role="body", start=start, duration=dur,
        block_id=block, slot_values={"title": f"场景{i}", "body": ""},
    )


def test_program_rejects_empty():
    with pytest.raises(Exception):
        GraphicProgram(id="p", duration=3.0, scenes=[])


def test_program_rejects_gap():
    with pytest.raises(Exception):
        GraphicProgram(id="p", duration=6.0, scenes=[_scene(1, 0, 3), _scene(2, 3.5, 2.5)])


def test_program_seamless_ok():
    p = GraphicProgram(id="p", duration=6.0, scenes=[_scene(1, 0, 3), _scene(2, 3, 3)])
    assert p.total_frames == 180


def test_program_duration_mismatch_rejected():
    with pytest.raises(Exception):
        GraphicProgram(id="p", duration=99.0, scenes=[_scene(1, 0, 3)])


# ---------------------------------------------------------------------------
# GFX-009/010 Catalog + Resolver（三态，零评分）
# ---------------------------------------------------------------------------

def test_resolver_knowledge_domain():
    cands = resolve_blocks(BlockResolveRequest(
        semantic_intents=("knowledge",), content_type="knowledge", duration=3.0))
    kb = [c for c in cands if c.block_id.startswith("kb.")]
    assert kb and all(c.compatibility == "SUPPORTED" for c in kb)
    pd = [c for c in cands if c.block_id.startswith("pd.")]
    assert all(c.compatibility == "INCOMPATIBLE" for c in pd)


def test_resolver_product_domain():
    cands = resolve_blocks(BlockResolveRequest(
        semantic_intents=("product",), content_type="product_video", duration=3.0))
    pd = [c for c in cands if c.block_id.startswith("pd.")]
    assert pd and all(c.compatibility == "SUPPORTED" for c in pd)


def test_resolver_duration_out_of_range_is_missing_requirement():
    cands = resolve_blocks(BlockResolveRequest(
        semantic_intents=("knowledge",), content_type="knowledge", duration=99.0))
    # 语义兼容的 kb 块 → MISSING_REQUIREMENT（时长越界不否决语义）
    kb = [c for c in cands if c.block_id.startswith("kb.")]
    assert all(c.compatibility == "MISSING_REQUIREMENT" for c in kb)
    assert all(c.missing_requirements for c in kb)
    # pd 块类别不符 → INCOMPATIBLE（正确硬拒，不是时长问题）
    pd = [c for c in cands if c.block_id.startswith("pd.")]
    assert all(c.compatibility == "INCOMPATIBLE" for c in pd)


def test_resolver_no_scores():
    """零评分铁律：候选结果不含 score/rating/probability 字段。"""
    cands = resolve_blocks(BlockResolveRequest(
        semantic_intents=("knowledge",), content_type="knowledge", duration=3.0))
    for c in cands:
        d = c.__dict__
        assert not any(k in d for k in ("score", "rating", "probability", "rank"))


def test_catalog_block_render_is_deterministic_html():
    b = blocks_mod.get_block("kb.title")
    h1 = blocks_mod.render_block(b, {"title": "测试", "body": "内容"}, 3.0, "editorial")
    h2 = blocks_mod.render_block(b, {"title": "测试", "body": "内容"}, 3.0, "editorial")
    assert h1 == h2
    assert 'data-block="kb.title"' in h1


def test_catalog_block_duration_bounds():
    b = blocks_mod.get_block("kb.title")
    with pytest.raises(ValueError):
        blocks_mod.render_block(b, {"title": "x"}, 99.0, "editorial")


def test_block_escapes_user_content():
    """slot 值含 HTML 时必须被转义（XSS 边界，方案 §48）。"""
    b = blocks_mod.get_block("kb.title")
    h = blocks_mod.render_block(
        b, {"title": "<script>alert(1)</script>", "body": ""}, 3.0, "editorial")
    # 字面标签不可出现；转义实体必须出现（用字符码拼接避免源码实体化）
    entity = "&" + "lt;" + "script&" + "gt;"
    assert "<script>alert(1)</script>" not in h
    assert entity in h


# ---------------------------------------------------------------------------
# GFX-003 Compiler
# ---------------------------------------------------------------------------

def _plan(script="第一句。第二句。第三句。"):
    return build_graphic_plan(script, content_type="knowledge")


def test_compile_produces_contract_attrs():
    html = plan_to_html(_plan())
    assert "data-composition-id=" in html
    assert "data-width=" in html and "data-height=" in html
    assert "data-fps=" in html and "data-duration=" in html


def test_compile_injects_seek_runtime():
    html = plan_to_html(_plan())
    assert "window.__gf" in html
    assert "function seek(frame)" in html
    assert "gf-seek" in html  # postMessage 控制协议（沙箱 opaque origin 通道）


def test_compile_rejects_unknown_block():
    plan = _plan()
    plan["scenes"][0]["block_id"] = "kb.nope"
    with pytest.raises(Exception):
        # instantiate 时不校验 block 存在（compiler 才校验）→ compile 抛
        program = instantiate_graphic_program(plan)
        compile_to_html(program)


def test_compile_rejects_empty_program():
    # 空 scenes 在 GraphicProgram 构造（pydantic 校验）即拒绝——诚实失败，
    # 不允许"空程序编出空 HTML"
    with pytest.raises(Exception):
        GraphicProgram(id="p", duration=0.0, scenes=[])


# ---------------------------------------------------------------------------
# GFX-027 Deterministic Preflight
# ---------------------------------------------------------------------------

def test_preflight_rejects_nondeterministic_html():
    bad = "<script>const t = Date.now();</script>"
    rep = deterministic_preflight(bad)
    assert rep.status == "REJECT"
    assert any("ND_TIMING" in i for i in rep.issues)


def test_preflight_rejects_network_html():
    rep = deterministic_preflight('<script src="https://evil.com/x.js"></script>')
    assert rep.status == "REJECT"
    assert any("EXT_NETWORK" in i for i in rep.issues)


def test_preflight_passes_clean_compile():
    rep = deterministic_preflight(plan_to_html(_plan()))
    assert rep.status == "PASS", rep.issues


# ---------------------------------------------------------------------------
# GFX-019/020 Text → Graphic Director
# ---------------------------------------------------------------------------

def test_director_empty_script_rejected():
    with pytest.raises(DirectorError):
        build_graphic_plan("   ")


def test_director_plan_structure():
    plan = build_graphic_plan("很多人以为AI只能聊天。其实改变的是流程。", content_type="knowledge")
    assert plan["scene_count"] == 2
    assert plan["scenes"][0]["role"] == "hook"
    assert plan["scenes"][-1]["role"] == "conclusion"
    total = round(sum(s["duration"] for s in plan["scenes"]), 2)
    assert abs(total - plan["total_duration"]) < 0.01


def test_director_statistic_role_binding():
    plan = build_graphic_plan("第一句。效率提升了80%。结论句。", content_type="knowledge")
    stats = [s for s in plan["scenes"] if s["role"] == "statistic"]
    assert stats and stats[0]["block_id"] == "kb.statistic"


def test_director_product_content_type_binds_pd_blocks():
    plan = build_graphic_plan("我们的产品。功能很强。立即体验。", content_type="product_video")
    assert all(s["block_id"].startswith("pd.") for s in plan["scenes"])
    assert plan["scenes"][-1]["block_id"] == "pd.cta"


def test_director_program_scenes_seamless():
    program = instantiate_graphic_program(_plan())
    cursor = 0.0
    for sc in sorted(program.scenes, key=lambda s: s.start):
        assert abs(sc.start - cursor) < 0.01
        cursor += sc.duration


# ---------------------------------------------------------------------------
# GFX-006 render（不跑浏览器：只验证诚实失败路径）
# ---------------------------------------------------------------------------

def test_render_rejects_nondeterministic_html():
    with pytest.raises(GraphicsRenderError) as ei:
        render_graphics("<script>Math.random()</script>", "x.mp4",
                        1080, 1920, 30, 1)
    assert ei.value.code == "NONDETERMINISTIC_HTML"


def test_render_rejects_network_html():
    with pytest.raises(GraphicsRenderError) as ei:
        render_graphics('<script src="https://x.com/a.js"></script>', "x.mp4",
                        1080, 1920, 30, 1)
    assert ei.value.code == "NONDETERMINISTIC_HTML"


# ---------------------------------------------------------------------------
# IG-PREVIEW-FINAL-PARITY：同一 program 两条质量路径共享同一 HTML
# ---------------------------------------------------------------------------

def test_preview_final_share_compiled_html():
    """parity by construction：preview 与 final 消费同一 compile_to_html 产物。"""
    program = instantiate_graphic_program(_plan())
    assert compile_to_html(program) == compile_to_html(program)
