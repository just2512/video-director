"""§十八/§二十六 UI 闭环契约：generation motion 类型必须端到端真实产出。

2026-10-08 核查发现：material.motion.list/create 进了 Registry 与 Tool
Executor，但 /api/editor/generate 不支持 motion 类型、前端零暴露——§26
「Registry→Tool→Artifact→UI 全闭环」断链。本文件钉住 generation 面
motion 分流的端到端行为（真实渲染，非 mock——黑屏事故说明 mock 会说谎）。
"""

from pathlib import Path

import pytest

from video_director import config, generation


def test_motion_is_a_supported_generation_type():
    """SUPPORTED_TYPES 必须含 motion（§十八 艺术动画进生成面板）。"""
    assert "motion" in generation.SUPPORTED_TYPES


def test_generate_motion_renders_real_video(monkeypatch, tmp_path):
    """motion 分流 → 真实 Huashu 渲染 → manifest 登记完整 lineage 字段。

    小尺寸 + 低帧率 + 2 秒，真跑 Chromium+FFmpeg（不 mock render——
    2026-10-08 黑屏事故正是 mock/子串测试掩盖了 bundle 语法破坏）。
    """
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    project_id = "p-gen-motion"
    base = config.project_dir(project_id)
    base.mkdir(parents=True, exist_ok=True)

    results = generation.generate(
        project_id=project_id,
        prompt="第一句。第二句。第三句。",
        gen_type="motion",
        width=270,
        height=480,
        reference="grammar:y5_kinetic_type",
    )
    assert len(results) == 1, "motion 是确定性单产物（variant=M），不是 3 变体"
    r = results[0]
    assert r.type == "video"
    assert r.provider_id == "huashu-motion"
    out = Path(r.file)
    assert out.is_file() and out.stat().st_size > 1000, "渲染产物必须真实非空"

    # manifest lineage：方案 §十九 要求 origin/generator/program_ref 可追溯
    manifest = generation._load_manifest(project_id)
    entry = next(m for m in manifest if m["id"] == r.asset_id)
    assert entry["origin"] == "huashu"
    assert entry["generator"] == "material.motion.create"
    assert entry["program_ref"].startswith("motion-")
    assert entry["grammar"] == "y5_kinetic_type"
    assert entry["duration"] == 5.0

    # 方案 §十九 Artifact 持久化：plans/motion/<program>.json + artifact.json 落盘
    plan_path = base / "plans" / "motion" / f"{entry['program_ref']}.json"
    assert plan_path.is_file(), "MotionProgram 未持久化到 plans/motion"
    artifact_path = (base / "assets" / "generated" / "motion"
                     / entry["program_ref"] / "artifact.json")
    assert artifact_path.is_file(), "生成 manifest 缺 artifact.json（Resume 依赖）"

    # Asset Catalog 登记（create_motion_asset 内部 register_generated 完成）：
    # artifact.json 里必须有 asset_id 且与生成结果一致（一条事实一个来源）
    import json as _json
    artifact = _json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["asset_id"] == r.asset_id
    assert artifact["grammar"] == "y5_kinetic_type"
    assert artifact["engine"] == "huashu"


def test_generate_motion_unknown_grammar_honest_error(monkeypatch, tmp_path):
    """未知 grammar 诚实报错，不静默换默认（§23 REJECT 而非假装支持）。"""
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    project_id = "p-gen-motion-bad"
    base = config.project_dir(project_id)
    base.mkdir(parents=True, exist_ok=True)
    with pytest.raises(Exception) as ei:
        generation.generate(
            project_id=project_id,
            prompt="测试",
            gen_type="motion",
            width=270,
            height=480,
            reference="grammar:no_such_grammar",
        )
    assert "UNKNOWN_GRAMMAR" in str(ei.value) or "未知 grammar" in str(ei.value)
