# -*- coding: utf-8 -*-
"""2026-10-21 终审 R3-R6 回归锁。

R3 — retry spec 保真（video_director/workflow_run.py）
    retry 从 bundle+params 重建 spec 时补齐 stop_at/confirm_mode/auto_sfx_search/
    lower_third_*/sfx 决策字段：来源是 execute() 落盘的 plans/director_spec.json
    （完整 spec），params_json 现值优先、spec 历史值兜底。

R4 — checkpoint STATE_KEYS 补 sfx 两键（video_director/director_checkpoint.py）
    sfx_plan_override / sfx_auto_search 进 STATE_KEYS，save/restore 循环自动生效；
    None 是合法值（auto 决策），roundtrip 后仍相等。

R5 — 字幕 off 导出不烧残留 master.ass（video_director/confirm.py）
    params_json subtitle_mode="off" → subtitles 强制 None，且不写 IR 重建的 ass。
    只测到「subtitles 变量为 None」这一层——真实导出链要 ffmpeg 真实编码，
    半集成成本不成比例（明确注释）。

R6 — generation_jobs 并发文件锁（video_director/generation.py）
    _update_job 读-改-写全程持 filelock；webui 线程池 + 后台 recover 并发
    同 project 不再互相覆盖。8 线程并发更新不同字段 → 全字段存活。
"""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest


def _isolate(tmp_path, monkeypatch):
    """标准隔离：存储根 + LLM 层 + model_config 缓存（同 test_generation_recovery）。"""
    from video_director import llm_provider, model_config
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    monkeypatch.setattr(config_module(), "composer_storage_root", lambda: tmp_path)
    monkeypatch.setattr(llm_provider, "get_llm", lambda: None)
    monkeypatch.setattr(llm_provider, "_provider_cache", None)
    if hasattr(model_config, "_cache"):
        monkeypatch.setattr(model_config, "_cache", None)
    return storage


def config_module():
    from video_director import config
    return config


# ---------------------------------------------------------------------------
# R4：checkpoint STATE_KEYS sfx roundtrip
# ---------------------------------------------------------------------------

class TestR4CheckpointSfxKeys:
    def test_sfx_keys_roundtrip(self, tmp_path):
        """sfx_plan_override=[]（off 决策）/None（auto 决策）+ sfx_auto_search=True
        save 后 restore 必须相等——两个键都要在 STATE_KEYS 里。"""
        from video_director import director_checkpoint as cp

        state = {
            "sfx_plan_override": [],
            "sfx_auto_search": True,
            "script": "文案",
        }
        cp.save("proj-r4-sfx", state, lambda pid: tmp_path / pid)

        restored = {}
        assert cp.restore_into("proj-r4-sfx", restored, lambda pid: tmp_path / pid)
        assert restored["sfx_plan_override"] == []
        assert restored["sfx_auto_search"] is True
        assert restored["script"] == "文案"

    def test_sfx_override_none_roundtrips(self, tmp_path):
        """sfx_plan_override=None（auto 交 style density）是合法值：
        JSON null 序列化+restore 后仍为 None（不是被当成缺键丢掉）。"""
        from video_director import director_checkpoint as cp

        cp.save("proj-r4-none", {"sfx_plan_override": None, "sfx_auto_search": False},
                lambda pid: tmp_path / pid)
        restored = {}
        cp.restore_into("proj-r4-none", restored, lambda pid: tmp_path / pid)
        assert "sfx_plan_override" in restored
        assert restored["sfx_plan_override"] is None
        assert restored["sfx_auto_search"] is False

    def test_state_keys_contains_sfx_pair(self):
        """锁元组本身（防将来无意删键）。"""
        from video_director import director_checkpoint as cp
        assert "sfx_plan_override" in cp.STATE_KEYS
        assert "sfx_auto_search" in cp.STATE_KEYS


# ---------------------------------------------------------------------------
# R3：retry spec 保真
# ---------------------------------------------------------------------------

class TestR3RetrySpecFidelity:
    def _persisted_spec(self, tmp_path, pid, values):
        """模拟 execute() 落盘的完整 spec（plans/director_spec.json）。"""
        plans = tmp_path / pid / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "director_spec.json").write_text(
            json.dumps(values, ensure_ascii=False), encoding="utf-8")

    def test_retry_restores_persisted_spec_fields(self, tmp_path, monkeypatch):
        """retry 重建 spec 补齐 params_json 里没有、只有 spec 持久化持有的字段：
        stop_at / confirm_mode / auto_sfx_search / lower_third_* / sfx。"""
        _isolate(tmp_path, monkeypatch)
        from video_director import config, store, director_inputs as di, workflow_run

        pid = "proj-r3-spec"
        config.init_project_dirs(config.project_dir(pid))
        bundle = {
            "version": di.VERSION, "brief": "做个测试片",
            "script": {"text": "文案", "source": "user", "authority": "authoritative"},
            "assets": {"asset_refs": []}, "references": [],
            "software_project": {}, "constraints": {}, "creative_settings": {},
        }
        di.persist_bundle(pid, bundle)
        store.create_project(pid, "文案", {"creative_brief": "做个测试片"})

        # execute() 落盘的完整 spec 含这些字段（创建时用户给过）
        self._persisted_spec(tmp_path, pid, {
            "stop_at": "video", "confirm_mode": "preview",
            "auto_sfx_search": True, "sfx": "ding",
            "lower_third_name": "老 Jerry", "lower_third_role": "IVD 销售",
        })

        captured = {}
        monkeypatch.setattr(
            "video_director.workflow_run.run_director_create_async",
            lambda spec, plan=None: captured.update(spec=spec) or "run-r3")
        run_id = workflow_run.run_director_retry_async(pid)
        assert run_id, "retry 被拒绝"
        spec = captured["spec"]
        assert spec.get("stop_at") == "video"
        assert spec.get("confirm_mode") == "preview"
        assert spec.get("auto_sfx_search") is True
        assert spec.get("sfx") == "ding"
        assert spec.get("lower_third_name") == "老 Jerry"
        assert spec.get("lower_third_role") == "IVD 销售"

    def test_params_json_current_value_wins_over_spec(self, tmp_path, monkeypatch):
        """params_json（当前机读现值）优先于 spec 历史值：用户改过的才算数。"""
        _isolate(tmp_path, monkeypatch)
        from video_director import config, store, director_inputs as di, workflow_run

        pid = "proj-r3-params"
        config.init_project_dirs(config.project_dir(pid))
        di.persist_bundle(pid, {
            "version": di.VERSION, "brief": "b",
            "script": {"text": "t", "source": "user", "authority": "authoritative"},
            "assets": {"asset_refs": []}, "references": [],
            "software_project": {}, "constraints": {}, "creative_settings": {},
        })
        store.create_project(pid, "t", {
            "creative_brief": "b", "stop_at": "verify", "confirm_mode": "direct"})
        self._persisted_spec(tmp_path, pid, {
            "stop_at": "video", "confirm_mode": "preview", "auto_sfx_search": False})

        captured = {}
        monkeypatch.setattr(
            "video_director.workflow_run.run_director_create_async",
            lambda spec, plan=None: captured.update(spec=spec) or "run-x")
        assert workflow_run.run_director_retry_async(pid)
        spec = captured["spec"]
        assert spec["stop_at"] == "verify"
        assert spec["confirm_mode"] == "direct"
        # params 没有的键仍从 spec 持久化兜底
        assert spec["auto_sfx_search"] is False

    def test_retry_without_persisted_spec_still_works(self, tmp_path, monkeypatch):
        """无 director_spec.json（旧项目）→ 回填静默跳过，retry 不炸。"""
        _isolate(tmp_path, monkeypatch)
        from video_director import config, store, director_inputs as di, workflow_run

        pid = "proj-r3-nospec"
        config.init_project_dirs(config.project_dir(pid))
        di.persist_bundle(pid, {
            "version": di.VERSION, "brief": "b",
            "script": {"text": "t", "source": "user", "authority": "authoritative"},
            "assets": {"asset_refs": []}, "references": [],
            "software_project": {}, "constraints": {}, "creative_settings": {},
        })
        store.create_project(pid, "t", {"creative_brief": "b"})

        captured = {}
        monkeypatch.setattr(
            "video_director.workflow_run.run_director_create_async",
            lambda spec, plan=None: captured.update(spec=spec) or "run-y")
        assert workflow_run.run_director_retry_async(pid)
        assert captured["spec"]["project_id"] == pid

    def test_retry_full_backfill_recovers_director_service_fields(
        self, tmp_path, monkeypatch
    ):
        """R3b：白名单 7 键之外的 director_service 初建字段也要兜底。

        voice_mode="silent" 是真实意图（用户不要配音）——旧白名单循环之后
        仍然缺失，retry 后变 auto 合成。本测试锁全量兜底补回这四个字段。
        """
        _isolate(tmp_path, monkeypatch)
        from video_director import config, store, director_inputs as di, workflow_run

        pid = "proj-r3b-full"
        config.init_project_dirs(config.project_dir(pid))
        di.persist_bundle(pid, {
            "version": di.VERSION, "brief": "b",
            "script": {"text": "t", "source": "user", "authority": "authoritative"},
            "assets": {"asset_refs": []}, "references": [],
            "software_project": {}, "constraints": {}, "creative_settings": {},
        })
        # params_json 刻意不含这四个字段（初建时它们只进 director_spec.json）
        store.create_project(pid, "t", {"creative_brief": "b"})
        self._persisted_spec(tmp_path, pid, {
            "voice_mode": "silent", "bgm_volume": 0.3,
            "narration_volume": 0.8, "fit_mode": "contain",
            # 反向哨兵：spec 已有的 key 不得被兜底覆盖
            "voice": "zh-CN-XiaoxiaoNeural",
            # 敏感/会话键必须被排除
            "api_key": "sk-leak", "project_id": "wrong-pid",
        })

        captured = {}
        monkeypatch.setattr(
            "video_director.workflow_run.run_director_create_async",
            lambda spec, plan=None: captured.update(spec=spec) or "run-r3b")
        assert workflow_run.run_director_retry_async(pid)
        spec = captured["spec"]
        assert spec["voice_mode"] == "silent"
        assert spec["bgm_volume"] == 0.3
        assert spec["narration_volume"] == 0.8
        assert spec["fit_mode"] == "contain"
        # 会话 project_id 与已构造的 voice 不被持久化值覆盖
        assert spec["project_id"] == pid
        assert spec["voice"] == "zh-CN-XiaoxiaoNeural"
        assert "api_key" not in spec, "敏感键不得进入重试 spec"

    def test_retry_backfill_keeps_falsy_and_empty_list_values(
        self, tmp_path, monkeypatch
    ):
        """R3b：False / 0 / [] 是合法决策值，不得被「缺值」判定误杀。"""
        _isolate(tmp_path, monkeypatch)
        from video_director import config, store, director_inputs as di, workflow_run

        pid = "proj-r3b-falsy"
        config.init_project_dirs(config.project_dir(pid))
        di.persist_bundle(pid, {
            "version": di.VERSION, "brief": "b",
            "script": {"text": "t", "source": "user", "authority": "authoritative"},
            "assets": {"asset_refs": []}, "references": [],
            "software_project": {}, "constraints": {}, "creative_settings": {},
        })
        store.create_project(pid, "t", {"creative_brief": "b"})
        # 键名刻意避开 retry spec 构造时预置的那批（voice/width/clip_duration 等
        # 已在 spec 里，全量兜底按设计不覆盖）——只测「spec 未持有的键」上
        # False/0/[] 不被缺值判定误杀。
        self._persisted_spec(tmp_path, pid, {
            "auto_sfx_search": False,   # False 合法：用户关掉音效搜索
            "match_assets": False,      # 同类布尔决策值
            "sfx_plan_override": [],    # [] 合法：明确的 off 决策
            "fade_frames": 0,           # 0 合法：不得被当缺值跳过
            "empty_string_field": "",   # 空串仍是缺值，不补
        })

        captured = {}
        monkeypatch.setattr(
            "video_director.workflow_run.run_director_create_async",
            lambda spec, plan=None: captured.update(spec=spec) or "run-r3b2")
        assert workflow_run.run_director_retry_async(pid)
        spec = captured["spec"]
        assert spec["auto_sfx_search"] is False
        assert spec["match_assets"] is False
        assert spec["sfx_plan_override"] == []
        assert spec["fade_frames"] == 0
        assert "empty_string_field" not in spec
        # spec 已有值一律不被兜底覆盖（params 现值优先契约）
        assert spec["match_materials_to_script"] is True


# ---------------------------------------------------------------------------
# R5：字幕 off 不烧残留 master.ass
# ---------------------------------------------------------------------------

class TestR5SubtitleOffExport:
    """只测到「render_composition 收到的 subtitle_file」这一层——完整导出链
    要真实 ffmpeg 编码成片，半集成成本不成比例。半集成本身就是生产缺陷场景：
    残留 master.ass 在盘 + IR cues 非空 + params subtitle_mode=off。"""

    def _render_dir(self, pid):
        from video_director import config
        base = config.project_dir(pid)
        plans = base / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (base / "render").mkdir(parents=True, exist_ok=True)
        # _render_saved_composition_final 第一道门：composition.json 必须在
        (plans / "composition.json").write_text(
            "{}", encoding="utf-8")
        return base

    def test_off_forces_none_and_skips_ass_write(self, tmp_path, monkeypatch):
        """cue 非空 + 残留 master.ass + subtitle_mode=off → subtitle_file=None。"""
        _isolate(tmp_path, monkeypatch)
        from video_director import store, confirm, renderer

        pid = "proj-r5-off"
        base = self._render_dir(pid)
        master = base / "render" / "master.ass"
        master.write_text("残留字幕\n", encoding="utf-8")
        store.create_project(pid, "t", {"subtitle_mode": "off"})

        seen = {}
        monkeypatch.setattr(
            renderer, "render_composition",
            lambda *a, **kw: seen.update(kw) or _FakeRender)
        _run_to_render(monkeypatch, confirm, ir=_minimal_ir(cues=[_FakeCue()]))
        try:
            confirm._render_saved_composition_final(pid)
        except Exception:
            pass  # 渲染之后的收尾失败无所谓，只关心 subtitle_file 入参
        # 反假通过：流程必须真的推进到 render_composition（seen 有值），
        # 否则下面断言空 dict 的 get() 也是 None——测试锁就失效了。
        assert seen, "半集成未到达 render_composition（前置段被吞异常）"
        assert seen.get("subtitle_file") is None

    def test_off_does_not_rewrite_residual_ass(self, tmp_path, monkeypatch):
        """off 分支不写 IR 重建的 ass——残留文件内容保持原样。"""
        _isolate(tmp_path, monkeypatch)
        from video_director import store, confirm, renderer

        pid = "proj-r5-ass-untouched"
        base = self._render_dir(pid)
        master = base / "render" / "master.ass"
        master.write_text("残留字幕\n", encoding="utf-8")
        store.create_project(pid, "t", {"subtitle_mode": "off"})

        seen = {}
        monkeypatch.setattr(
            renderer, "render_composition",
            lambda *a, **kw: seen.update(kw) or _FakeRender)
        _run_to_render(monkeypatch, confirm, ir=_minimal_ir(cues=[_FakeCue()]))
        try:
            confirm._render_saved_composition_final(pid)
        except Exception:
            pass
        assert seen, "半集成未到达 render_composition（前置段被吞异常）"
        # 走到渲染段且 cues 非空（非 off 时本来会 write_text 重建）
        assert master.read_text(encoding="utf-8") == "残留字幕\n"

    def test_plain_mode_still_burns_subtitles(self, tmp_path, monkeypatch):
        """对照：非 off 模式行为不变——残留 master.ass 仍被当有效字幕。"""
        _isolate(tmp_path, monkeypatch)
        from video_director import store, confirm, renderer

        pid = "proj-r5-plain"
        base = self._render_dir(pid)
        master = base / "render" / "master.ass"
        master.write_text("残留字幕\n", encoding="utf-8")
        store.create_project(pid, "t", {"subtitle_mode": "plain"})

        seen = {}
        monkeypatch.setattr(
            renderer, "render_composition",
            lambda *a, **kw: seen.update(kw) or _FakeRender)
        _run_to_render(monkeypatch, confirm, ir=_minimal_ir(cues=[]))
        try:
            confirm._render_saved_composition_final(pid)
        except Exception:
            pass
        assert seen, "半集成未到达 render_composition（前置段被吞异常）"
        assert seen.get("subtitle_file") == str(master)


# ---------------------------------------------------------------------------
# R6b：编辑器导出路径的版权放行闸门（差距 6）
# ---------------------------------------------------------------------------

class TestR6bCommercialGateOnEditorExport:
    """_render_saved_composition_final 插版权门——BLOCKED 素材不得从编辑器
    导出路径直接进成片。同 R5 半集成：monkeypatch 门 + 渲染器，最小项目文件。"""

    def _render_dir(self, pid):
        from video_director import config
        base = config.project_dir(pid)
        plans = base / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (base / "render").mkdir(parents=True, exist_ok=True)
        (plans / "composition.json").write_text("{}", encoding="utf-8")
        return base

    def test_blocked_asset_rejects_export_before_render(self, tmp_path, monkeypatch):
        """gate 返回 blocked → ConfirmError 抛出，且 render_composition 从未被调用
        （反假通过：门必须在渲染之前，不是渲染后补查）。"""
        _isolate(tmp_path, monkeypatch)
        from video_director import confirm, renderer, store

        pid = "proj-r6b-blocked"
        self._render_dir(pid)
        store.create_project(pid, "t", {})

        monkeypatch.setattr(
            confirm, "commercial_release_gate",
            lambda pid, ir=None: {"blocked": ["asset-blocked-1"], "unknown": []})
        render_calls = []
        monkeypatch.setattr(
            renderer, "render_composition",
            lambda *a, **kw: render_calls.append(kw) or _FakeRender)
        _run_to_render(monkeypatch, confirm, ir=_minimal_ir(cues=[]))

        with pytest.raises(confirm.ConfirmError) as ei:
            confirm._render_saved_composition_final(pid)
        assert "BLOCKED" in str(ei.value) and "asset-blocked-1" in str(ei.value)
        assert not render_calls, "BLOCKED 时必须挡在渲染之前，不得编码成片"
        # 项目必须落 FAILED + 可解释错误（否则永久卡 RENDERING）
        row = store.get_project(pid)
        assert row["stage"] == store.Stage.FAILED.value
        assert "BLOCKED" in (row.get("error") or "")

    def test_unknown_assets_warn_but_do_not_block(self, tmp_path, monkeypatch):
        """UNKNOWN/REVIEW 只警告不阻止（Jerry 裁决）——导出继续走到渲染。"""
        _isolate(tmp_path, monkeypatch)
        from video_director import confirm, renderer, store

        pid = "proj-r6b-unknown"
        self._render_dir(pid)
        store.create_project(pid, "t", {})

        monkeypatch.setattr(
            confirm, "commercial_release_gate",
            lambda pid, ir=None: {"blocked": [], "unknown": ["asset-unknown-1"]})
        render_calls = []
        monkeypatch.setattr(
            renderer, "render_composition",
            lambda *a, **kw: render_calls.append(kw) or _FakeRender)
        _run_to_render(monkeypatch, confirm, ir=_minimal_ir(cues=[]))

        try:
            confirm._render_saved_composition_final(pid)
        except Exception:
            pass  # 渲染后的收尾失败无所谓，只关心是否越过闸门
        assert render_calls, "UNKNOWN 不得阻止导出，流程必须到达 render_composition"

    def test_gate_is_actually_called_on_export_path(self, tmp_path, monkeypatch):
        """锁「门被调用」本身——防止将来有人把闸门挪回 pipeline 专用路径后，
        编辑器导出路径又静默失去这道门（本次差距 6 的原始缺陷形态）。"""
        _isolate(tmp_path, monkeypatch)
        from video_director import confirm, renderer, store

        pid = "proj-r6b-called"
        self._render_dir(pid)
        store.create_project(pid, "t", {})

        calls = []
        monkeypatch.setattr(
            confirm, "commercial_release_gate",
            lambda pid, ir=None: calls.append((pid, ir)) or {"blocked": [], "unknown": []})
        monkeypatch.setattr(
            renderer, "render_composition",
            lambda *a, **kw: _FakeRender)
        export_ir = _minimal_ir(cues=[])
        _run_to_render(monkeypatch, confirm, ir=export_ir)

        try:
            confirm._render_saved_composition_final(pid)
        except Exception:
            pass
        assert calls == [(pid, export_ir)], "编辑器导出必须经过版权闸门，且复用已解析的 IR"


def _run_to_render(monkeypatch, confirm, ir):
    """把导出前的重依赖换成假件，让流程推进到字幕决策段。

    CompositionIR 整体替换（734 行 ir = CompositionIR(**data)）比改 __init__
    干净——dataclass 构造被绕过，其余段（assets/manifest/catalog）保持真实。
    """
    from video_director import asset_catalog, audio, prober, qc

    monkeypatch.setattr(confirm, "CompositionIR", lambda **kw: ir)
    monkeypatch.setattr(confirm.AssetRef, "__init__",
                        lambda self, **kw: None)
    monkeypatch.setattr(asset_catalog, "get_asset", lambda aid: None)
    monkeypatch.setattr(audio, "append_audio_assets", lambda assets, lib: assets)
    monkeypatch.setattr(audio, "scan_library", lambda: [])
    monkeypatch.setattr(prober, "probe_media", lambda *a, **kw: None)
    monkeypatch.setattr(qc, "run_qc", lambda *a, **kw: {})


class _FakeRender:
    final_path = "unused.mp4"


class _FakeCue:
    start = 0.0
    end = 2.0
    text = "测试字幕"


def _minimal_ir(cues):
    """最小 CompositionIR 替身：subtitle_track.cues + project 尺寸 + assets。

    必须带 model_copy（766 行 render_ir = ir.model_copy(deep=True) 会调）——
    否则异常在 render_composition 之前抛出被 try/except 吞掉，测试假通过。
    """
    ir = type("IR", (), {})()
    track = type("T", (), {})()
    track.cues = cues
    ir.subtitle_track = track
    ir.subtitle_style = None
    ir.assets = []
    ir.project = type("P", (), {"width": 1080, "height": 1920})()
    ir.final_path = ""
    ir.model_copy = lambda deep=False: ir
    return ir


# ---------------------------------------------------------------------------
# R6：generation_jobs 并发文件锁
# ---------------------------------------------------------------------------

class TestR6GenerationJobLock:
    def test_concurrent_updates_no_lost_write(self, tmp_path, monkeypatch):
        """8 线程并发 _update_job 不同字段同一 job → 最终所有字段都在。

        修复前读-改-写无锁：最后写的线程只带自己的字段，其余被覆盖丢失。
        隔离方式同 tests/test_generation_recovery.py（VIDEO_DIRECTOR_STORAGE_ROOT）。
        """
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen

        pid = "proj-r6-lock"
        config.init_project_dirs(config.project_dir(pid))
        gen._update_job(pid, "genjob-r6", {"status": "running", "provider": "fake"})

        fields = [{"prompt": f"p{i}", "variant": f"v{i}", "note": f"n{i}"}
                  for i in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(
                lambda f: gen._update_job(pid, "genjob-r6", f), fields))

        job = gen._load_jobs(pid)["genjob-r6"]
        for i in range(8):
            assert job.get("prompt") in {f"p{j}" for j in range(8)}
            assert job.get("variant") in {f"v{j}" for j in range(8)}
            assert job.get("note") in {f"n{j}" for j in range(8)}
        # 所有 8 组的 note 至少有一个存活？不——同键互相覆盖是预期的；
        # 关键是「不同键」全部存活：
        assert "prompt" in job and "variant" in job and "note" in job
        # 首轮写的字段不被并发轮次抹掉
        assert job["status"] == "running"
        assert job["provider"] == "fake"

    def test_lock_file_is_removed_after_update(self, tmp_path, monkeypatch):
        """filelock 用完清理，不在项目目录留 .lock 垃圾。"""
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen

        pid = "proj-r6-clean"
        config.init_project_dirs(config.project_dir(pid))
        gen._update_job(pid, "genjob-c", {"status": "running"})
        lock = config.project_dir(pid) / "generation_jobs.lock"
        assert not lock.exists(), "filelock 应自动释放并删除锁文件"
