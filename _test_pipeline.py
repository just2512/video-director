"""_test_pipeline.py — Pipeline Manifest + Checkpoint 验证脚本（临时，可留）。

用 D:/autocut/venv/Scripts/python.exe 执行：
    D:/autocut/venv/Scripts/python.exe _test_pipeline.py
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from video_director import config as vd_config
from video_director import pipeline_defs
from video_director.checkpoint import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    CheckpointStore,
)

PASS_COUNT = 0
FAIL_COUNT = 0


def check(cond: bool, label: str) -> None:
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
        print(f"  PASS  {label}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL  {label}")


def test_pipeline_defs() -> None:
    print("[1] pipeline_defs")
    pm = pipeline_defs.get_pipeline("script-media")
    check(len(pm.stages) == 10, f"script-media stages 数量==10（实际 {len(pm.stages)}）")
    check(pm.stage_names() == [
        "capability_check", "script", "tts", "alignment", "subtitle",
        "asset", "match", "plan", "render", "qc",
    ], f"script-media 顺序正确: {pm.stage_names()}")

    so = pipeline_defs.get_pipeline("script-only")
    check(so.stage_by_name("text_scenes") is not None, "script-only 有 text_scenes stage")
    check("asset" not in so.stage_names() and "match" not in so.stage_names(),
          f"script-only 无 asset/match: {so.stage_names()}")

    # stage 三件套字段非空
    s0 = pm.stages[0]
    check(all([s0.name, s0.skill, s0.produces, s0.review_focus, s0.success_criteria]),
          f"stage 三件套字段齐全: {s0.name}/skill={s0.skill}")

    # 依赖链：qc 依赖前面全部
    qc = pm.stage_by_name("qc")
    check(qc.depends_on == tuple(pm.stage_names()[:-1]),
          f"qc.depends_on == 前面全部（线性链，{len(qc.depends_on)} 个上游）")
    check(pm.upstream_of("tts") == ["capability_check", "script"],
          f"upstream_of(tts) == [capability_check, script]")

    # list_pipelines
    check(set(pipeline_defs.list_pipelines()) == {"script-media", "script-only"},
          f"list_pipelines(): {pipeline_defs.list_pipelines()}")

    # 未知流水线抛 KeyError
    try:
        pipeline_defs.get_pipeline("no-such-pipeline")
        check(False, "get_pipeline(未知) 应抛 KeyError")
    except KeyError:
        check(True, "get_pipeline(未知) 抛 KeyError")

    # load_yaml 接口：构造 YAML 临时文件读回，验证解析
    try:
        import yaml
        pm2_src = {
            "name": "yaml-test",
            "version": "1.0",
            "description": "yaml 往返测试",
            "stages": [
                {"name": "a", "skill": "skill-a", "produces": "out-a",
                 "review_focus": "r-a", "success_criteria": "c-a"},
                {"name": "b", "skill": "skill-b", "produces": "out-b",
                 "review_focus": "r-b", "success_criteria": "c-b",
                 "depends_on": ["a"]},
                {"name": "c"},
            ],
        }
        tmp = Path(tempfile.gettempdir()) / "vd_test_pipeline.yaml"
        tmp.write_text(yaml.safe_dump(pm2_src, allow_unicode=True), encoding="utf-8")
        pm2 = pipeline_defs.load_yaml(tmp)
        check(pm2.name == "yaml-test" and len(pm2.stages) == 3, "load_yaml 往返解析成功")
        check(pm2.stage_by_name("b").depends_on == ("a",), "load_yaml 显式 depends_on 保留")
        check(pm2.stage_by_name("c").depends_on == ("a", "b"),
              "load_yaml 缺省 depends_on 按线性链推断")
    except ImportError:
        check(True, "load_yaml 跳过（pyyaml 未装，内置流水线不受影响）")


def test_checkpoint() -> None:
    print("[2] checkpoint")
    store = CheckpointStore()
    # 隔离 project_id（毫秒时间戳），避免污染真实项目目录
    pid = f"_test_ckpt_{int(time.time() * 1000)}"

    # 用临时 root，避免写入真实 storage/
    tmp_root = Path(tempfile.mkdtemp(prefix="vd_ckpt_"))
    vd_config.composer_storage_root = lambda: tmp_root
    vd_config.project_dir = lambda p: tmp_root / p

    # 无记录 → 需要执行
    check(store.stage_upstream_changed(pid, "tts", {"script": "h1"}) is True,
          "无记录 → stage_upstream_changed True")

    # 记录 completed（输入指纹 script=h1）
    rec = store.record_stage(pid, "tts", STATUS_COMPLETED,
                             fingerprints={"script": "h1"},
                             artifact_paths=["audio/narration.wav"])
    check(rec["status"] == STATUS_COMPLETED and rec["fingerprints"] == {"script": "h1"},
          "record_stage 写入 fingerprints + artifact_paths")

    # 指纹不变 → False
    check(store.stage_upstream_changed(pid, "tts", {"script": "h1"}) is False,
          "指纹不变 → False")

    # 指纹变化 → True
    check(store.stage_upstream_changed(pid, "tts", {"script": "h2"}) is True,
          "指纹变化 → True")

    # 部分对比：传的集合之外的指纹不参与（换风格只重算 style 下游）
    store.record_stage(pid, "style", STATUS_COMPLETED,
                       fingerprints={"subtitle": "h1", "script": "h1"},
                       artifact_paths=["plans/style.json"])
    check(store.stage_upstream_changed(pid, "style", {"subtitle": "h1"}) is False,
          "换风格场景：只传 subtitle 指纹（未变）→ style 不重算")
    check(store.stage_upstream_changed(pid, "style", {"subtitle": "h1", "script": "h2"}) is True,
          "换风格场景：script 指纹也变 → style 重算")

    # 再次记录 → 旧记录进 history
    store.record_stage(pid, "tts", STATUS_FAILED,
                       fingerprints={"script": "h3"}, artifact_paths=[])
    hist = store.get_history(pid, "tts")
    check(len(hist) == 1 and hist[0]["fingerprints"] == {"script": "h1"},
          f"旧记录进 history（len={len(hist)}，旧指纹 h1 保留）")
    cur = store.get_stage(pid, "tts")
    check(cur["status"] == STATUS_FAILED and cur["fingerprints"] == {"script": "h3"},
          "当前记录已更新为最新（failed）")

    # 非 completed 状态 → 需要重算
    check(store.stage_upstream_changed(pid, "tts", {"script": "h3"}) is True,
          "status=failed → 需要重算")

    # 新上游引入 → 视为变化
    check(store.stage_upstream_changed(pid, "tts", {"script": "h3", "extra": "x"}) is True,
          "新上游指纹（已存无）→ True")

    # 非法 status 抛 ValueError
    try:
        store.record_stage(pid, "tts", "bogus")
        check(False, "非法 status 应抛 ValueError")
    except ValueError:
        check(True, "非法 status 抛 ValueError")

    # invalidate_downstream：上游变了 → 下游全失效
    store.record_stage(pid, "script", STATUS_COMPLETED, {"script": "h0"}, [])
    store.record_stage(pid, "alignment", STATUS_COMPLETED, {"script": "h0"}, [])
    store.record_stage(pid, "subtitle", STATUS_COMPLETED, {"script": "h0"}, [])
    invalidated = store.invalidate_downstream(pid, "script",
                                              ["script", "alignment", "subtitle"])
    check(invalidated == ["script", "alignment", "subtitle"],
          f"invalidate_downstream(script) 失效 3 个: {invalidated}")
    check(store.get_stage(pid, "alignment") is None, "失效后 get_stage 返回 None")

    # 持久化：新实例读同一文件（跨会话一致）
    store2 = CheckpointStore()
    check(store2.get_stage(pid, "subtitle") is None,
          "新实例读盘一致（subtitle 已失效）")
    store2.record_stage(pid, "subtitle", STATUS_COMPLETED, {"script": "h9"}, [])
    check(store2.get_stage(pid, "subtitle")["fingerprints"] == {"script": "h9"},
          "新实例写入后可读回")

    # history 不覆盖：subtitle 旧记录保留
    hist2 = store2.get_history(pid, "subtitle")
    check(len(hist2) >= 1, f"subtitle history 保留（{len(hist2)} 条）")
    print(f"  INFO  临时 checkpoint 目录: {tmp_root}")


if __name__ == "__main__":
    test_pipeline_defs()
    test_checkpoint()
    print()
    print(f"结果: PASS={PASS_COUNT} FAIL={FAIL_COUNT}")
    sys.exit(0 if FAIL_COUNT == 0 else 1)
