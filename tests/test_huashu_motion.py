from pathlib import Path

import pytest

from video_director import procedural_motion
from video_director.graphics.preflight import deterministic_preflight


def test_huashu_grammars_are_fixed():
    assert procedural_motion.HUASHU_GRAMMARS == (
        "y1_kurzgesagt",
        "y2_vox",
        "y3_whiteboard",
        "y4_storytime",
        "y5_kinetic_type",
        "t1_3b1b",
        "t2_keynote_ui",
        "t3_finance_chart",
    )


def test_build_motion_program_from_script():
    p = procedural_motion.build_motion_program(
        "第一句。第二句。第三句。",
        "y5_kinetic_type",
        duration=6,
    )
    assert p.engine == "huashu"
    assert p.grammar == "y5_kinetic_type"
    assert len(p.cues) == 3
    assert p.cues[0]["kind"] == "title"


def test_t3_requires_real_data():
    with pytest.raises(procedural_motion.ProceduralMotionError) as ei:
        procedural_motion.build_motion_program(
            "做一张图表",
            "t3_finance_chart",
            duration=6,
        )
    assert ei.value.code == "MISSING_REQUIREMENT"


def test_compile_is_self_contained_and_deterministic():
    p = procedural_motion.build_motion_program(
        "AI 从聊天走向代理。",
        "y5_kinetic_type",
        duration=4,
    )
    html = procedural_motion.compile_motion_html(p)
    assert 'window.__gf = {' in html
    assert 'CLIPS.y5_kinetic_type' in html
    rep = deterministic_preflight(html)
    assert rep.status == "PASS", rep.issues


def test_compile_rejects_bad_bundle(monkeypatch, tmp_path):
    bad = tmp_path / "bundle.js"
    bad.write_text("Math.random()", encoding="utf-8")
    monkeypatch.setattr(procedural_motion, "_BUNDLE_PATH", bad)
    p = procedural_motion.build_motion_program(
        "测试", "y5_kinetic_type", duration=3
    )
    with pytest.raises(procedural_motion.ProceduralMotionError) as ei:
        procedural_motion.compile_motion_html(p)
    assert ei.value.code == "NONDETERMINISTIC_BUNDLE"


def test_create_motion_asset_persists_artifact(monkeypatch, tmp_path):
    from video_director import config

    project = "p-huashu-test"
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    monkeypatch.setattr(
        procedural_motion,
        "render_graphics",
        lambda **kwargs: Path(kwargs["out_path"]).write_bytes(b"fake-mp4"),
    )
    monkeypatch.setattr(
        "video_director.asset_catalog.register_generated",
        lambda file_path, prompt="", tags=None: {
            "asset_id": "asset-huashu-test",
            "file": file_path,
            "type": "video",
        },
    )

    out = procedural_motion.create_motion_asset(
        project,
        {
            "grammar": "y5_kinetic_type",
            "prompt": "第一句。第二句。",
            "duration": 4,
            "width": 1080,
            "height": 1920,
        },
    )
    assert out["asset"]["asset_id"] == "asset-huashu-test"
    assert Path(out["artifact"]["output"]).is_file()
    assert Path(out["artifact"]["plan"]).is_file()


def test_material_motion_capability_and_executor(monkeypatch):
    from video_director import tool_executor

    assert tool_executor._is_material_motion("material.motion.list")
    assert tool_executor._is_material_motion("material.motion.create")

    listed = tool_executor.execute_tool(
        "p-huashu-list",
        {"tool": "material.motion.list", "payload": {}},
    )
    assert listed["ok"] is True
    assert any(x["id"] == "y5_kinetic_type" for x in listed["result"]["grammars"])

    monkeypatch.setattr(
        procedural_motion,
        "create_motion_asset",
        lambda project_id, payload: {
            "program": {"id": "motion-test", "grammar": payload["grammar"]},
            "asset": {"asset_id": "asset-test", "file": "/tmp/motion.mp4"},
            "artifact": {"output": "/tmp/motion.mp4"},
        },
    )
    created = tool_executor.execute_tool(
        "p-huashu-create",
        {
            "tool": "material.motion.create",
            "payload": {
                "grammar": "y5_kinetic_type",
                "prompt": "第一句。第二句。",
                "duration": 4,
                "width": 1080,
                "height": 1920,
            },
        },
    )
    assert created["ok"] is True
    assert created["result"]["asset"]["asset_id"] == "asset-test"
