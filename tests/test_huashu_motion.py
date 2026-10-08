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


def test_bundle_is_valid_javascript():
    """2026-10-08 事故：打包裁剪把 util IIFE 收尾 `})();` 删掉，整份 bundle
    成为 SyntaxError，8 个 grammar 全渲染成 2817 字节黑屏 mp4 而所有只查
    子串的测试全绿。bundle 必须能通过 node --check（CI 之外本地也兜底）。"""
    import os
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        # nvm-windows / ProgramData 安装不在 PATH 时按常见位置探测（本机实际形状）
        for cand in (
            os.path.expandvars(r"%NVM_SYMLINK%\node.exe"),
            os.path.expandvars(r"%NVM_HOME%\node.exe"),
            r"D:\ProgramData\nodejs\node.exe",
            r"C:\Program Files\nodejs\node.exe",
        ):
            if cand and Path(cand).is_file():
                node = cand
                break
    if not node:
        pytest.skip("node 不可用，无法做 bundle 语法闸门")
    r = subprocess.run(
        [node, "--check", str(procedural_motion._BUNDLE_PATH)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"bundle 语法损坏: {r.stderr[:400]}"


def test_compiled_html_boots_in_browser():
    """真实浏览器闸门：编译出的 HTML 必须执行出 window.__gf 且画布有真实
    像素（防止任何一层把 JS 变成不执行的死文本——黑屏事故的通用特征）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright 不可用")

    p = procedural_motion.build_motion_program(
        "第一句。第二句。", "y5_kinetic_type", duration=2, fps=6,
        width=270, height=480,
    )
    html = procedural_motion.compile_motion_html(p)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 270, "height": 480})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)[:200]))
        page.set_content(html, wait_until="load")
        page.wait_for_timeout(200)
        gf_type = page.evaluate("typeof window.__gf")
        ready = page.evaluate("!!window.__gfx_ready")
        variance = page.evaluate("""
          () => {
            const cv = document.getElementById('c');
            if (!cv || !cv.width) return -1;
            const d = cv.getContext('2d').getImageData(
              0, 0, cv.width, cv.height).data;
            const n = d.length / 4;
            let sum = 0, sumsq = 0;
            for (let i = 0; i < d.length; i += 4) {
              const v = (d[i] + d[i+1] + d[i+2]) / 3;
              sum += v; sumsq += v * v;
            }
            const mean = sum / n;
            return Math.sqrt(sumsq / n - mean * mean);
          }""")
        browser.close()
    assert gf_type == "object", "runtime 未执行：__gf 不存在"
    assert ready is True, "__gfx_ready 未置位"
    assert variance > 0, f"画布全黑（std={variance}）：动画没有真实渲染"
    assert not errors, "浏览器报错: " + "; ".join(errors[:3])


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


def test_motion_program_has_exact_integer_frame_contract():
    p = procedural_motion.build_motion_program(
        "一句话。",
        "y5_kinetic_type",
        duration=4,
        fps=30,
    )
    assert p.fps == 30
    assert p.total_frames == 120
    with pytest.raises(Exception):
        procedural_motion.MotionProgram(
            id="bad-fps",
            grammar="y5_kinetic_type",
            duration=1,
            fps=29.97,
            width=1080,
            height=1920,
        )
    # 入口同样拒绝非整数 fps——int() 静默截断会掩盖时间轴错误
    with pytest.raises(procedural_motion.ProceduralMotionError) as ei:
        procedural_motion.build_motion_program(
            "一句话。", "y5_kinetic_type", duration=4, fps=29.97,
        )
    assert ei.value.code == "NON_INTEGER_FPS"


def test_all_huashu_grammars_compile_through_provider():
    for grammar in procedural_motion.HUASHU_GRAMMARS:
        if grammar == "t3_finance_chart":
            p = procedural_motion.build_motion_program(
                "数据变化。",
                grammar,
                duration=4,
                data={
                    "title": "测试",
                    "series": [
                        {"label": "A", "value": 10},
                        {"label": "B", "value": 20},
                    ],
                },
                cues=[
                    {"at": 0.0, "kind": "title", "text": "测试"},
                    {"at": 1.0, "kind": "bar"},
                ],
            )
        else:
            p = procedural_motion.build_motion_program(
                "第一句。第二句。",
                grammar,
                duration=4,
            )
        html = procedural_motion.compile_motion_html(p)
        assert grammar in html
        assert "window.__gf" in html
        assert deterministic_preflight(html).status == "PASS"
