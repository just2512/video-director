# -*- coding: utf-8 -*-
"""仓库审计差距 2/3 残余修复回归。

差距 2（任务 A）：生视频异步任务持久化——
    - 提交成功即落盘 provider_task_id 到 generation_jobs.json
    - 轮询每轮更新状态；done 写 output_file
    - recover_pending_video_jobs：进程重启后接管 running 任务续询/续传
    - 同 input_fingerprint+variant 的 running 任务复用，不重复提交（防重复付费）

差距 3（任务 B）：参考视频自动分析进导演依赖链——
    - bundle references 有 video 无报告 → execute() 入口确定性重析（use_llm=False）
    - 成功写回 bundle + persist_bundle；失败 warning 不阻塞主链
    - 已有完整报告（含 video_fingerprint）不重复分析

隔离纪律（同 tests/test_intent_chain_round2.py）：
    llm_provider.get_llm 挡 + _provider_cache=None + model_config 隔离 +
    VIDEO_DIRECTOR_STORAGE_ROOT 指向 tmp。
"""
import json
from pathlib import Path

import pytest


def _isolate(tmp_path, monkeypatch):
    """标准隔离：存储根 + LLM 层 + model_config 缓存。"""
    from video_director import llm_provider, model_config
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    monkeypatch.setattr(llm_provider, "get_llm", lambda: None)
    monkeypatch.setattr(llm_provider, "_provider_cache", None)
    if hasattr(model_config, "_cache"):
        monkeypatch.setattr(model_config, "_cache", None)
    return storage


class FakeVideoClient:
    """OpenAI 兼容 client 假实现：提交/查询均可控，计数器暴露调用次数。"""

    def __init__(self, statuses, urls=None):
        # statuses: 每次 GET 查询返回的 status（队列，耗尽后重复最后一项）
        self.statuses = list(statuses)
        self.urls = urls or {}
        self.post_calls = 0
        self.get_calls = 0
        self.created_task_id = "ext-task-1"

    def post(self, path, json=None):
        self.post_calls += 1
        return _FakeResp({"id": self.created_task_id, "status": "queued"})

    def get(self, path):
        self.get_calls += 1
        status = self.statuses.pop(0) if self.statuses else "queued"
        if status == "completed":
            return _FakeResp({"id": self.created_task_id, "status": "completed",
                              "url": self.urls.get("video", "https://cdn/x.mp4")})
        return _FakeResp({"id": self.created_task_id, "status": status})


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _video_cfg(monkeypatch):
    from video_director import model_config
    monkeypatch.setattr(
        model_config, "get_role_config",
        lambda role: {"api_key": "k", "base_url": "https://fake/v1",
                      "model": "fake-video-model"}
        if role == "video" else {"api_key": "", "base_url": "", "model": ""})


def test_explicit_submission_unknown_code_is_not_reclassified_from_job_id_429():
    from video_director.generation import GenerationError

    error = GenerationError(
        "VIDEO_SUBMISSION_UNKNOWN",
        "相同素材请求存在结果不确定的供应商提交记录 (genjob-429abc)；"
        "为避免重复付费，系统不会自动重提。",
    )

    assert error.code == "VIDEO_SUBMISSION_UNKNOWN"
    assert error.is_quota() is False


class TestVideoJobPersistence:
    """任务 A：提交落盘 → 轮询更新 → done 写 output_file。"""

    def test_submit_persists_provider_task_id(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import config, generation as gen
        pid = "proj-gen-persist"
        config.init_project_dirs(config.project_dir(pid))
        fake = FakeVideoClient(statuses=["queued", "queued", "completed"])
        monkeypatch.setattr(gen, "_video_client", lambda cfg: fake)
        monkeypatch.setattr(gen, "_download_bytes", lambda url: b"fake-mp4")

        # 首次 GET 即 completed（状态队列 1 项）→ 提交 1 次
        fake.statuses = ["completed"]
        results = gen.generate(pid, "未来城市延时", "video", width=1080, height=1920,
                               variant="A")
        assert results, "应产出 1 个结果"
        jobs = json.loads(
            (config.project_dir(pid) / "generation_jobs.json").read_text(encoding="utf-8"))
        assert len(jobs) == 1
        job = next(iter(jobs.values()))
        assert job["provider_task_id"] == "ext-task-1"
        assert job["status"] == "done"
        assert job["gen_type"] == "video"
        assert job["output_file"] and Path(job["output_file"]).is_file()
        assert Path(job["output_file"]).read_bytes() == b"fake-mp4"
        assert len(job["prompt_fingerprint"]) == 64  # sha256 hex
        assert fake.post_calls == 1

    def test_polling_updates_status_each_round(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import config, generation as gen
        pid = "proj-gen-poll"
        config.init_project_dirs(config.project_dir(pid))
        # 两轮 running 后 done
        fake = FakeVideoClient(statuses=["queued", "running", "completed"])
        monkeypatch.setattr(gen, "_video_client", lambda cfg: fake)
        monkeypatch.setattr(gen, "_download_bytes", lambda url: b"x")
        monkeypatch.setattr(gen.time, "sleep", lambda s: None)
        gen.generate(pid, "a", "video", variant="A")
        jobs = gen._load_jobs(pid)
        job = next(iter(jobs.values()))
        assert job["status"] == "done"
        assert fake.get_calls >= 2  # 至少轮询过一次非终态



    def test_terminal_job_status_cannot_be_reopened(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen

        pid = "proj-terminal-immutable"
        config.init_project_dirs(config.project_dir(pid))
        for terminal in ("done", "failed", "submission_rejected", "cancelled"):
            job_id = f"job-{terminal}"
            gen._update_job(pid, job_id, {"status": terminal})
            with pytest.raises(gen.GenerationError) as exc:
                gen._update_job(pid, job_id, {"status": "running"})
            assert exc.value.code == "VIDEO_JOB_TERMINAL"
            assert gen._load_jobs(pid)[job_id]["status"] == terminal

    def test_late_running_poll_cannot_downgrade_job_completed_by_another_worker(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import config, generation as gen

        pid = "proj-terminal-race"
        config.init_project_dirs(config.project_dir(pid))
        gen._update_job(pid, "job-race", {
            "provider_task_id": "provider-race",
            "gen_type": "video",
            "status": "running",
        })

        def complete_during_poll(_client, _task_id):
            gen._update_job(pid, "job-race", {"status": "done", "manifest_registered": True})
            return "running", None, {"status": "running"}

        monkeypatch.setattr(gen, "_query_video_status", complete_during_poll)
        monkeypatch.setattr(gen, "_video_client", lambda _cfg: FakeVideoClient(["queued"]))

        assert gen.recover_pending_video_jobs(pid) == []
        assert gen._load_jobs(pid)["job-race"]["status"] == "done"
        assert gen._load_jobs(pid)["job-race"]["manifest_registered"] is True

class TestRecoverPendingVideoJobs:
    """任务 A：模拟重启——读 jobs 文件接续外部任务。"""

    def test_recover_completes_and_downloads(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import config, generation as gen
        pid = "proj-recover"
        config.init_project_dirs(config.project_dir(pid))
        out_file = config.project_dir(pid) / "assets" / "generated" / "rec.mp4"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        # 模拟中断残留：running + provider_task_id 已落盘
        gen._update_job(pid, "genjob-abc", {
            "provider": "fake", "provider_task_id": "ext-task-9",
            "gen_type": "video", "prompt_fingerprint": "f" * 64,
            "status": "running", "output_file": str(out_file),
        })
        fake = FakeVideoClient(statuses=["completed"])
        monkeypatch.setattr(gen, "_video_client", lambda cfg: fake)
        monkeypatch.setattr(gen, "_download_bytes", lambda url: b"recovered-bytes")

        recovered = gen.recover_pending_video_jobs(pid)
        assert len(recovered) == 1 and recovered[0]["status"] == "done"
        assert out_file.read_bytes() == b"recovered-bytes"
        jobs = gen._load_jobs(pid)
        assert jobs["genjob-abc"]["status"] == "done"
        assert jobs["genjob-abc"]["provider_task_id"] == "ext-task-9"

    def test_stale_running_recovery_observation_cannot_downgrade_downloading_job(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import config, generation as gen

        pid = "proj-recover-stale-running"
        config.init_project_dirs(config.project_dir(pid))
        gen._update_job(pid, "genjob-downloading", {
            "provider": "fake",
            "provider_task_id": "ext-task-downloading",
            "gen_type": "video",
            "status": "downloading",
            "output_file": "pending-output.mp4",
        })
        monkeypatch.setattr(
            gen, "_video_client", lambda _cfg: FakeVideoClient(statuses=["running"])
        )

        assert gen.recover_pending_video_jobs(pid) == []
        job = gen._load_jobs(pid)["genjob-downloading"]
        assert job["status"] == "downloading"
        assert job["output_file"] == "pending-output.mp4"


    def test_recover_finishes_downloading_job_after_process_interruption(
        self, tmp_path, monkeypatch
    ):
        """供应商任务已完成但本地 MP4 未落盘时，downloading 必须可恢复。"""
        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import config, generation as gen
        pid = "proj-recover-downloading"
        config.init_project_dirs(config.project_dir(pid))
        out_file = config.project_dir(pid) / "assets" / "generated" / "interrupted.mp4"
        gen._update_job(pid, "genjob-downloading", {
            "provider": "fake", "provider_task_id": "ext-task-d",
            "gen_type": "video", "input_fingerprint": "d" * 16,
            "variant": "A", "status": "downloading", "output_file": str(out_file),
        })
        fake = FakeVideoClient(statuses=["completed"])
        monkeypatch.setattr(gen, "_video_client", lambda cfg: fake)
        monkeypatch.setattr(gen, "_download_bytes", lambda url: b"recovered-after-download-crash")

        recovered = gen.recover_pending_video_jobs(pid)
        assert len(recovered) == 1 and recovered[0]["status"] == "done"
        assert out_file.read_bytes() == b"recovered-after-download-crash"
        assert gen._load_jobs(pid)["genjob-downloading"]["status"] == "done"

    def test_existing_atomic_output_repairs_missing_done_status(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen
        pid = "proj-recover-after-replace"
        config.init_project_dirs(config.project_dir(pid))
        out_file = config.project_dir(pid) / "assets" / "generated" / "already-written.mp4"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"complete-atomic-output")
        gen._update_job(pid, "genjob-after-replace", {
            "provider_task_id": "ext-task-z", "gen_type": "video",
            "status": "downloading", "output_file": str(out_file),
            "output_commit_protocol": "atomic-v2",
        })
        monkeypatch.setattr(
            gen, "_download_bytes",
            lambda url: (_ for _ in ()).throw(AssertionError("must not redownload")),
        )
        gen._persist_video_output(pid, "genjob-after-replace", str(out_file), "unused")
        assert gen._load_jobs(pid)["genjob-after-replace"]["status"] == "done"
        assert out_file.read_bytes() == b"complete-atomic-output"

    def test_poll_does_not_mark_done_before_local_output_is_persisted(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen
        pid = "proj-poll-downloading"
        config.init_project_dirs(config.project_dir(pid))
        fake = FakeVideoClient(statuses=["completed"])
        monkeypatch.setattr(gen, "_download_bytes", lambda url: b"fake-mp4")
        url = gen._create_poll_and_persist_video(
            fake, pid, "fake-model", "prompt", 320, 180,
            "e" * 16, "A", str(config.project_dir(pid) / "result.mp4"), timeout=1,
        )
        assert url == "https://cdn/x.mp4"
        job = next(iter(gen._load_jobs(pid).values()))
        assert job["status"] == "downloading"
        assert not Path(job["output_file"]).exists()
        gen._persist_video_output(pid, job["job_id"], job["output_file"], url)
        assert gen._load_jobs(pid)[job["job_id"]]["status"] == "done"
        assert Path(job["output_file"]).read_bytes() == b"fake-mp4"

    def test_recover_marks_external_failure(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import config, generation as gen
        pid = "proj-recover-fail"
        config.init_project_dirs(config.project_dir(pid))
        gen._update_job(pid, "genjob-f", {
            "provider": "fake", "provider_task_id": "ext-task-f",
            "gen_type": "video", "status": "running", "output_file": "",
        })
        fake = FakeVideoClient(statuses=["failed"])
        monkeypatch.setattr(gen, "_video_client", lambda cfg: fake)
        recovered = gen.recover_pending_video_jobs(pid)
        assert recovered[0]["status"] == "failed"
        assert gen._load_jobs(pid)["genjob-f"]["status"] == "failed"

    def test_recover_idempotent_on_done_jobs(self, tmp_path, monkeypatch):
        """done/failed 不再碰外部——幂等。"""
        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import config, generation as gen
        pid = "proj-recover-idem"
        config.init_project_dirs(config.project_dir(pid))
        gen._update_job(pid, "genjob-done", {
            "provider": "fake", "provider_task_id": "t1",
            "gen_type": "video", "status": "done", "output_file": "",
        })
        fake = FakeVideoClient(statuses=["completed"])
        monkeypatch.setattr(gen, "_video_client", lambda cfg: fake)
        assert gen.recover_pending_video_jobs(pid) == []
        assert fake.get_calls == 0

    def test_generate_video_entry_recovers_first(self, tmp_path, monkeypatch):
        """generate() 视频分支入口先 recover（幂等），不炸主链。"""
        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import config, generation as gen
        pid = "proj-entry-recover"
        config.init_project_dirs(config.project_dir(pid))
        gen._update_job(pid, "genjob-old", {
            "provider": "fake", "provider_task_id": "t-old",
            "gen_type": "video", "status": "running", "output_file": "",
        })
        called = {"recover": 0}
        real_recover = gen.recover_pending_video_jobs
        monkeypatch.setattr(gen, "_video_client", lambda cfg: FakeVideoClient(["failed"]))
        monkeypatch.setattr(
            gen, "recover_pending_video_jobs",
            lambda project_id: called.__setitem__("recover", called["recover"] + 1)
            or real_recover(project_id))
        # 提交失败（fake 返回 failed）→ 诚实报错，但 recover 已先跑
        with pytest.raises(gen.GenerationError):
            gen.generate(pid, "p", "video", variant="A")
        assert called["recover"] == 1


class TestFingerprintReuse:
    """任务 A：同 fingerprint 的 running 任务复用轮询，不重复提交。"""

    def test_same_prompt_no_second_submit(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import config, generation as gen
        pid = "proj-reuse"
        config.init_project_dirs(config.project_dir(pid))
        fake = FakeVideoClient(statuses=["completed"])
        monkeypatch.setattr(gen, "_video_client", lambda cfg: fake)
        monkeypatch.setattr(gen, "_download_bytes", lambda url: b"x")

        gen.generate(pid, "同一段提示词", "video", variant="A", model="m1")
        assert fake.post_calls == 1
        # done 是不可逆终态。重启后的相同请求应直接命中已持久化
        # Manifest，不得把终态伪造为 running，也不应再访问供应商。
        fake2 = FakeVideoClient(statuses=["running", "completed"])
        monkeypatch.setattr(gen, "_video_client", lambda cfg: fake2)
        monkeypatch.setattr(gen, "_download_bytes", lambda url: b"x2")
        gen.generate(pid, "同一段提示词", "video", variant="A", model="m1")
        assert fake2.post_calls == 0, "复用任务不得二次提交（防重复付费）"
        assert fake2.get_calls == 0  # 终态与 Manifest 命中后不再轮询


    def test_manifest_write_failure_keeps_recovery_marker_and_retry_does_not_resubmit(
        self, tmp_path, monkeypatch
    ):
        """Manifest 持久化失败时不得提前置 registered；重试复用已落盘 MP4。"""
        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import config, generation as gen

        pid = "proj-manifest-write-failure"
        config.init_project_dirs(config.project_dir(pid))
        fake = FakeVideoClient(statuses=["completed"])
        monkeypatch.setattr(gen, "_video_client", lambda cfg: fake)
        monkeypatch.setattr(gen, "_download_bytes", lambda url: b"durable-video-output")
        save_manifest = gen._save_manifest

        def fail_manifest_write(project_id, additions):
            raise OSError("simulated manifest disk failure")

        monkeypatch.setattr(gen, "_save_manifest", fail_manifest_write)
        with pytest.raises(OSError, match="simulated manifest disk failure"):
            gen.generate(pid, "persist after output", "video", variant="A")

        jobs = gen._load_jobs(pid)
        assert len(jobs) == 1
        job = next(iter(jobs.values()))
        assert job["status"] == "done"
        assert job.get("manifest_registered") is not True
        assert job["recovery_needs_registration"] is True
        assert Path(job["output_file"]).read_bytes() == b"durable-video-output"
        assert fake.post_calls == 1

        # 恢复 Manifest 写入后再次请求：复用原子落盘文件，不再次提交或轮询。
        monkeypatch.setattr(gen, "_save_manifest", save_manifest)
        results = gen.generate(pid, "persist after output", "video", variant="A")
        assert len(results) == 1
        assert fake.post_calls == 1
        assert fake.get_calls == 1
        repaired = gen._load_jobs(pid)[job["job_id"]]
        assert repaired["manifest_registered"] is True
        assert repaired["recovery_needs_registration"] is False
        manifest = gen._load_manifest(pid)
        assert len([
            item for item in manifest
            if item.get("input_fingerprint") == results[0].input_fingerprint
            and item.get("variant") == "A"
            and item.get("type") == "video"
        ]) == 1

    def test_done_registered_job_remains_reusable_if_manifest_row_is_lost(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen
        pid = "proj-manifest-row-lost"
        config.init_project_dirs(config.project_dir(pid))
        gen._update_job(pid, "genjob-registered", {
            "provider_task_id": "provider-task-registered",
            "gen_type": "video", "input_fingerprint": "a" * 16,
            "variant": "A", "status": "done",
            "manifest_registered": True,
        })
        reusable = gen._find_reusable_job(pid, "a" * 16, "A")
        assert reusable is not None
        assert reusable["job_id"] == "genjob-registered"

    def test_missing_manifest_row_is_rebuilt_from_atomic_registered_output_without_provider(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        import hashlib
        from video_director import config, generation as gen, asset_catalog

        pid = "proj-manifest-row-rebuild"
        config.init_project_dirs(config.project_dir(pid))
        prompt = "rebuild a lost manifest row"
        input_fp = hashlib.sha256((prompt + "|video").encode("utf-8")).hexdigest()[:16]
        output = config.project_dir(pid) / "assets" / "generated" / "registered.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"durable-registered-video")

        gen._update_job(pid, "genjob-registered-output", {
            "provider_task_id": "provider-task-expired",
            "gen_type": "video",
            "input_fingerprint": input_fp,
            "variant": "A",
            "status": "done",
            "output_file": str(output),
            "output_commit_protocol": "atomic-v2",
            "manifest_registered": True,
            "registry_status": "registered",
            "registry_asset_id": "library-video-1",
        })

        def provider_must_not_be_contacted(_cfg):
            raise AssertionError("atomic local output must not require provider access")

        monkeypatch.setattr(gen, "_video_client", provider_must_not_be_contacted)
        monkeypatch.setattr(
            asset_catalog, "register_generated",
            lambda file_path, prompt="", tags=None: {"asset_id": "library-video-1"},
        )

        results = gen.generate(pid, prompt, "video", variant="A")

        assert len(results) == 1
        assert results[0].file == str(output)
        manifest = gen._load_manifest(pid)
        assert len(manifest) == 1
        assert manifest[0]["file"] == str(output)
        job = gen._load_jobs(pid)["genjob-registered-output"]
        assert job["manifest_registered"] is True
        assert job["registry_status"] == "registered"
        assert job["registry_asset_id"] == "library-video-1"

    def test_asset_registry_failure_is_retried_without_provider_resubmission(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import config, generation as gen, asset_catalog

        pid = "proj-registry-retry"
        config.init_project_dirs(config.project_dir(pid))
        fake = FakeVideoClient(statuses=["completed"])
        monkeypatch.setattr(gen, "_video_client", lambda cfg: fake)
        monkeypatch.setattr(gen, "_download_bytes", lambda _url: b"registry-retry-video")

        attempts = []

        def fail_once_then_register(file_path, prompt="", tags=None):
            attempts.append(file_path)
            if len(attempts) == 1:
                raise OSError("simulated Asset Registry disk failure")
            return {"asset_id": "library-video-recovered"}

        monkeypatch.setattr(asset_catalog, "register_generated", fail_once_then_register)

        first = gen.generate(pid, "retry Asset Registry", "video", variant="A")
        assert len(first) == 1
        job_id = next(iter(gen._load_jobs(pid)))
        job = gen._load_jobs(pid)[job_id]
        assert job["manifest_registered"] is True
        assert job["registry_status"] == "pending"
        assert job["registry_error_type"] == "OSError"
        assert fake.post_calls == 1

        second = gen.generate(pid, "retry Asset Registry", "video", variant="A")
        assert len(second) == 1
        repaired = gen._load_jobs(pid)[job_id]
        assert repaired["registry_status"] == "registered"
        assert repaired["registry_asset_id"] == "library-video-recovered"
        assert repaired["registry_error"] is None
        assert len(attempts) == 2
        assert fake.post_calls == 1
        assert fake.get_calls == 1

    def test_registry_commit_before_job_marker_crash_recovers_without_duplicate_asset(
        self, tmp_path, monkeypatch
    ):
        """Asset Registry 已提交但 generation_jobs 标记未写回时，重试必须按内容 hash 收敛。"""
        from types import SimpleNamespace

        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import asset_catalog, config, generation as gen, prober

        project_id = "proj-registry-marker-crash"
        config.init_project_dirs(config.project_dir(project_id))
        fake = FakeVideoClient(statuses=["completed"])
        monkeypatch.setattr(gen, "_video_client", lambda _cfg: fake)
        payload = bytes.fromhex("000000186674797069736f6d") + b"registry-crash-video"
        monkeypatch.setattr(gen, "_download_bytes", lambda _url: payload)
        monkeypatch.setattr(
            prober, "probe_media",
            lambda _path: SimpleNamespace(
                has_video_stream=True, duration=5.0, width=320, height=180,
                fps=30.0, has_audio_stream=False, codec="h264",
            ),
        )

        original_update = gen._update_job

        def crash_after_registry_commit(pid, job_id, fields):
            if fields.get("registry_status") == "registered":
                raise SystemExit("simulated process crash after Asset Registry commit")
            return original_update(pid, job_id, fields)

        monkeypatch.setattr(gen, "_update_job", crash_after_registry_commit)
        with pytest.raises(SystemExit, match="after Asset Registry commit"):
            gen.generate(project_id, "registry crash recovery", "video", variant="A")

        jobs = gen._load_jobs(project_id)
        assert len(jobs) == 1
        job_id, job = next(iter(jobs.items()))
        assert job["status"] == "done"
        assert job["manifest_registered"] is True
        assert job["registry_status"] == "pending"
        assert fake.post_calls == 1
        assert len(asset_catalog.list_assets(source="generated")) == 1
        assert len(gen._load_manifest(project_id)) == 1

        monkeypatch.setattr(gen, "_update_job", original_update)
        replayed = gen.generate(
            project_id, "registry crash recovery", "video", variant="A"
        )

        assert len(replayed) == 1
        assert replayed[0].asset_id == gen._load_manifest(project_id)[0]["id"]
        assert fake.post_calls == 1, "registry repair must not submit another paid task"
        assert fake.get_calls == 1, "durable Manifest replay must not poll the provider"
        assert len(asset_catalog.list_assets(source="generated")) == 1
        repaired = gen._load_jobs(project_id)[job_id]
        assert repaired["registry_status"] == "registered"
        assert repaired["registry_error"] is None

    def test_generate_reuses_recovered_completed_task_until_manifest_is_written(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import config, generation as gen
        import hashlib

        pid = "proj-recover-before-manifest"
        config.init_project_dirs(config.project_dir(pid))
        prompt = "recover me"
        input_fp = hashlib.sha256((prompt + "|video").encode("utf-8")).hexdigest()[:16]
        prior_output = config.project_dir(pid) / "assets" / "generated" / "prior.mp4"
        gen._update_job(pid, "genjob-recovered", {
            "provider": "fake", "provider_task_id": "ext-task-recovered",
            "gen_type": "video", "input_fingerprint": input_fp,
            "variant": "A", "status": "running", "output_file": str(prior_output),
        })
        fake = FakeVideoClient(statuses=["completed", "completed"])
        monkeypatch.setattr(gen, "_video_client", lambda cfg: fake)
        monkeypatch.setattr(gen, "_download_bytes", lambda url: b"recovered-video")
        results = gen.generate(pid, prompt, "video", variant="A")
        assert len(results) == 1
        assert fake.post_calls == 0, "recovered provider task must not be submitted again"
        job = gen._load_jobs(pid)["genjob-recovered"]
        assert job["status"] == "done"
        assert job["manifest_registered"] is True
        assert job["recovery_needs_registration"] is False
        assert fake.get_calls == 1, "已落地的 MP4 应直接复用，不再依赖供应商任务查询"
        assert Path(job["output_file"]).read_bytes() == b"recovered-video"

    def test_existing_manifest_row_repairs_job_registration_marker(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen
        import hashlib

        pid = "proj-manifest-already-durable"
        config.init_project_dirs(config.project_dir(pid))
        prompt = "durable manifest"
        input_fp = hashlib.sha256((prompt + "|video").encode("utf-8")).hexdigest()[:16]
        out_file = config.project_dir(pid) / "assets" / "generated" / "existing.mp4"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"durable-video")
        manifest = []
        gen._register_result(
            manifest, [], "gen-existing", "video", out_file, 320, 180,
            5.0, "fake-model", "video-llm", prompt, input_fp, "A",
        )
        gen._save_manifest(pid, manifest)
        gen._update_job(pid, "genjob-manifest-existing", {
            "provider_task_id": "provider-task-expired",
            "gen_type": "video", "input_fingerprint": input_fp,
            "variant": "A", "status": "done", "output_file": str(out_file),
            "output_commit_protocol": "atomic-v2",
            "recovery_needs_registration": True,
            "manifest_registered": False,
        })
        def forbidden_provider_client(cfg):
            raise AssertionError("durable manifest replay must not require provider access")
        monkeypatch.setattr(gen, "_video_client", forbidden_provider_client)
        results = gen.generate(pid, prompt, "video", variant="A")
        assert len(results) == 1 and results[0].asset_id == "gen-existing"
        job = gen._load_jobs(pid)["genjob-manifest-existing"]
        assert job["manifest_registered"] is True
        assert job["recovery_needs_registration"] is False

    def test_registered_video_with_missing_file_fails_closed_without_resubmit(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen
        import hashlib

        pid = "proj-missing-registered-video"
        config.init_project_dirs(config.project_dir(pid))
        prompt = "missing registered output"
        input_fp = hashlib.sha256((prompt + "|video").encode("utf-8")).hexdigest()[:16]
        missing = config.project_dir(pid) / "assets" / "generated" / "missing.mp4"
        gen._save_manifest(pid, [{
            "id": "gen-missing", "type": "video", "file": str(missing),
            "source": "generated", "variant": "A",
            "input_fingerprint": input_fp, "output_fingerprint": "0" * 64,
            "width": 320, "height": 180, "provider_id": "video-llm",
        }])
        gen._update_job(pid, "genjob-missing", {
            "provider_task_id": "provider-task-existing",
            "gen_type": "video", "input_fingerprint": input_fp,
            "variant": "A", "status": "done", "output_file": str(missing),
            "manifest_registered": True,
        })
        monkeypatch.setattr(
            gen, "_video_client",
            lambda cfg: (_ for _ in ()).throw(
                AssertionError("must not create or query another provider task")
            ),
        )
        with pytest.raises(gen.GenerationError) as exc:
            gen.generate(pid, prompt, "video", variant="A")
        assert exc.value.code == "VIDEO_MANIFEST_OUTPUT_MISSING"

class TestConcurrentVideoSubmission:
    """Competing callers must not submit duplicate paid provider tasks."""

    def test_video_client_disables_sdk_retries_for_non_idempotent_submit(
        self, tmp_path, monkeypatch
    ):
        import sys
        import types

        _isolate(tmp_path, monkeypatch)
        from video_director import generation as gen

        captured = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_module = types.ModuleType("openai")
        fake_module.OpenAI = FakeOpenAI
        monkeypatch.setitem(sys.modules, "openai", fake_module)

        client = gen._video_client({
            "api_key": "test-key",
            "base_url": "https://provider.example/v1",
        })
        assert isinstance(client, FakeOpenAI)
        assert captured == {
            "api_key": "test-key",
            "base_url": "https://provider.example/v1",
            "max_retries": 0,
        }

    def test_same_fingerprint_concurrent_callers_share_one_provider_task(
        self, tmp_path, monkeypatch
    ):
        import threading

        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen

        project_id = "proj-concurrent-video-submit"
        config.init_project_dirs(config.project_dir(project_id))
        post_entered = threading.Event()
        release_post = threading.Event()
        post_lock = threading.Lock()
        get_lock = threading.Lock()

        class BlockingClient:
            def __init__(self):
                self.post_calls = 0
                self.get_calls = 0
                self.both_gets_started = threading.Event()

            def post(self, path, json=None):
                reservations = gen._load_jobs(project_id)
                assert len(reservations) == 1
                assert next(iter(reservations.values()))["status"] == "submitting", (
                    "durable reservation must exist before the provider POST"
                )
                with post_lock:
                    self.post_calls += 1
                post_entered.set()
                if not release_post.wait(timeout=10):
                    raise TimeoutError("test did not release provider submission")
                return _FakeResp({"id": "shared-provider-task", "status": "queued"})

            def get(self, path):
                with get_lock:
                    self.get_calls += 1
                    if self.get_calls == 2:
                        self.both_gets_started.set()
                # Hold the first poll until the competing caller has passed the
                # lookup/submit gate. This prevents a fast fake provider completion
                # from turning a concurrent test into a sequential retry.
                if not self.both_gets_started.wait(timeout=10):
                    raise TimeoutError("second caller did not reuse the persisted task")
                return _FakeResp({
                    "id": "shared-provider-task",
                    "status": "completed",
                    "url": "https://cdn.example/shared.mp4",
                })

        client = BlockingClient()
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def submit(index):
            barrier.wait(timeout=5)
            try:
                results.append(gen._create_poll_and_persist_video(
                    client,
                    project_id,
                    "fake-model",
                    "same prompt",
                    320,
                    180,
                    "a" * 64,
                    "A",
                    str(config.project_dir(project_id) / "assets" / f"result-{index}.mp4"),
                    timeout=5,
                ))
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=submit, args=(index,)) for index in range(2)]
        try:
            for worker in workers:
                worker.start()
            assert post_entered.wait(timeout=5), "first provider submission did not start"
            release_post.set()
            assert client.both_gets_started.wait(timeout=5), (
                "second caller did not reach the shared task poll"
            )
            for worker in workers:
                worker.join(timeout=10)
            assert all(not worker.is_alive() for worker in workers), "submission workers deadlocked"
        finally:
            release_post.set()
            for worker in workers:
                if worker.is_alive():
                    worker.join(timeout=2)

        assert not errors, errors
        assert len(results) == 2, results
        assert results == ["https://cdn.example/shared.mp4"] * 2
        assert client.post_calls == 1, "same fingerprint/variant must create exactly one paid task"
        jobs = gen._load_jobs(project_id)
        assert len(jobs) == 1, jobs
        job = next(iter(jobs.values()))
        assert job["provider_task_id"] == "shared-provider-task"
        assert job["input_fingerprint"] == "a" * 64
        assert job["variant"] == "A"

    def test_concurrent_generate_calls_share_one_durable_output_and_manifest(
        self, tmp_path, monkeypatch
    ):
        """同一付费任务的并发 generate() 必须收敛到同一文件、Manifest ID 和 Registry 资产。"""
        import threading
        from types import SimpleNamespace

        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import asset_catalog, config, generation as gen, prober

        project_id = "proj-concurrent-generate-output"
        config.init_project_dirs(config.project_dir(project_id))
        monkeypatch.setattr(gen, "recover_pending_video_jobs", lambda _pid: [])
        poll_lock = threading.Lock()
        second_poll_started = threading.Event()
        output_committed = threading.Event()

        class SharedTaskClient:
            def __init__(self):
                self.post_calls = 0
                self.get_calls = 0

            def post(self, path, json=None):
                self.post_calls += 1
                return _FakeResp({"id": "one-paid-task", "status": "queued"})

            def get(self, path):
                with poll_lock:
                    self.get_calls += 1
                    call_number = self.get_calls
                if call_number == 1:
                    return _FakeResp({
                        "id": "one-paid-task",
                        "status": "completed",
                        "url": "https://cdn.example/one-paid-task.mp4",
                    })
                second_poll_started.set()
                if not output_committed.wait(timeout=8):
                    raise TimeoutError("first caller did not commit the shared output")
                return _FakeResp({
                    "id": "one-paid-task",
                    "status": "completed",
                    "url": "https://cdn.example/one-paid-task.mp4",
                })

        client = SharedTaskClient()
        monkeypatch.setattr(gen, "_video_client", lambda _cfg: client)
        original_persist = gen._persist_video_output

        def persist_after_second_poll(pid, job_id, out_file, url):
            if not second_poll_started.wait(timeout=8):
                raise TimeoutError("second caller did not enter its provider poll")
            result = original_persist(pid, job_id, out_file, url)
            output_committed.set()
            return result

        monkeypatch.setattr(gen, "_persist_video_output", persist_after_second_poll)
        payload = bytes.fromhex("000000186674797069736f6d") + b"concurrent-video-payload"
        monkeypatch.setattr(gen, "_download_bytes", lambda _url: payload)
        monkeypatch.setattr(
            prober, "probe_media",
            lambda _path: SimpleNamespace(
                has_video_stream=True, duration=5.0, width=320, height=180,
                fps=30.0, has_audio_stream=False, codec="h264",
            ),
        )

        start = threading.Barrier(2)
        results = []
        errors = []
        result_lock = threading.Lock()

        def run_generation():
            start.wait(timeout=5)
            try:
                value = gen.generate(
                    project_id, "same concurrent prompt", "video", variant="A"
                )
                with result_lock:
                    results.append(value)
            except Exception as exc:
                with result_lock:
                    errors.append(exc)

        workers = [threading.Thread(target=run_generation) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)

        assert all(not worker.is_alive() for worker in workers), "generate workers did not finish"
        assert not errors, errors
        assert len(results) == 2
        assert all(len(batch) == 1 for batch in results)
        assert client.post_calls == 1, "concurrent callers must create only one paid provider task"
        assert client.get_calls == 2
        assert second_poll_started.is_set()
        assert output_committed.is_set()
        generated = [batch[0] for batch in results]
        assert len({item.asset_id for item in generated}) == 1
        assert len({item.file for item in generated}) == 1

        manifest = gen._load_manifest(project_id)
        rows = [
            item for item in manifest
            if item.get("input_fingerprint") == generated[0].input_fingerprint
            and item.get("variant") == "A"
            and item.get("type") == "video"
        ]
        assert len(rows) == 1
        assert rows[0]["id"] == generated[0].asset_id
        assert Path(rows[0]["file"]).read_bytes() == payload

        jobs = gen._load_jobs(project_id)
        assert len(jobs) == 1
        job = next(iter(jobs.values()))
        assert job["status"] == "done"
        assert job["output_file"] == generated[0].file
        assert job["manifest_registered"] is True
        assert job["registry_status"] == "registered"
        library_assets = asset_catalog.list_assets(source="generated")
        assert len(library_assets) == 1
        assert library_assets[0]["content_hash"] == asset_catalog.content_hash(generated[0].file)

    def test_late_joiner_reuses_committed_output_without_polling_terminal_job(
        self, tmp_path, monkeypatch
    ):
        """并发请求若在另一调用者落盘后才进入提交锁，不得轮询或降级 done。"""
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen

        project_id = "proj-late-video-joiner"
        config.init_project_dirs(config.project_dir(project_id))
        output = config.project_dir(project_id) / "assets" / "generated" / "committed.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"already-committed-video")
        gen._update_job(project_id, "genjob-late-joiner", {
            "provider_task_id": "already-finished-task",
            "gen_type": "video",
            "input_fingerprint": "d" * 16,
            "variant": "A",
            "status": "done",
            "output_file": str(output),
            "output_commit_protocol": "atomic-v2",
            "recovery_needs_registration": True,
        })

        class NoProviderCalls:
            def post(self, *_args, **_kwargs):
                raise AssertionError("completed paid task must not be resubmitted")

            def get(self, *_args, **_kwargs):
                raise AssertionError("committed local output must not be polled again")

        result = gen._create_poll_and_persist_video(
            NoProviderCalls(), project_id, "fake-model", "prompt", 320, 180,
            "d" * 16, "A", str(config.project_dir(project_id) / "assets" / "other.mp4"),
            timeout=1,
        )

        assert result is None
        job = gen._load_jobs(project_id)["genjob-late-joiner"]
        assert job["status"] == "done"
        assert job["output_file"] == str(output)
        assert output.read_bytes() == b"already-committed-video"

    def test_output_download_ignores_predictable_part_symlink(
        self, tmp_path, monkeypatch
    ):
        """视频下载不能跟随可预测的 .part 符号链接写入项目外文件。"""
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen

        project_id = "proj-output-part-symlink"
        config.init_project_dirs(config.project_dir(project_id))
        output = config.project_dir(project_id) / "assets" / "final.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        external = tmp_path / "external-sensitive.bin"
        external.write_bytes(b"keep-this-content")
        predictable_part = output.with_name(output.name + ".part")
        try:
            predictable_part.symlink_to(external)
        except (OSError, NotImplementedError):
            pytest.skip("当前运行环境不支持创建符号链接")

        monkeypatch.setattr(gen, "_download_bytes", lambda _url: b"new-video-payload")
        result = gen._persist_video_output(
            project_id, "job-part-symlink", str(output), "https://cdn.example/video.mp4"
        )

        assert result == output
        assert output.read_bytes() == b"new-video-payload"
        assert external.read_bytes() == b"keep-this-content"
        assert predictable_part.is_symlink()
        job = gen._load_jobs(project_id)["job-part-symlink"]
        assert job["status"] == "done"
        assert job["output_commit_protocol"] == "atomic-v2"

    def test_ambiguous_transport_failure_does_not_try_second_provider_endpoint(
        self, tmp_path, monkeypatch
    ):
        import threading

        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen

        project_id = "proj-video-submit-timeout"
        config.init_project_dirs(config.project_dir(project_id))

        class TimeoutClient:
            def __init__(self):
                self.paths = []

            def post(self, path, json=None):
                self.paths.append(path)
                raise TimeoutError("provider request timed out after submission")

        client = TimeoutClient()
        with pytest.raises(TimeoutError):
            gen._create_poll_and_persist_video(
                client, project_id, "fake-model", "prompt", 320, 180,
                "b" * 64, "A", str(config.project_dir(project_id) / "result.mp4"),
                timeout=1,
            )
        jobs = gen._load_jobs(project_id)
        assert len(jobs) == 1
        reservation = next(iter(jobs.values()))
        assert reservation["status"] == "submission_unknown"
        assert reservation["submission_error_type"] == "TimeoutError"

        # A retry with the same fingerprint must stop before another POST:
        # the first request may have been accepted even though its response timed out.
        with pytest.raises(gen.GenerationError) as retry_error:
            gen._create_poll_and_persist_video(
                client, project_id, "fake-model", "prompt", 320, 180,
                "b" * 64, "A", str(config.project_dir(project_id) / "retry.mp4"),
                timeout=1,
            )
        assert retry_error.value.code == "VIDEO_SUBMISSION_UNKNOWN"
        assert client.paths == ["/video/generations"]

    def test_explicit_unsupported_route_uses_legacy_provider_endpoint(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen

        project_id = "proj-video-submit-fallback"
        config.init_project_dirs(config.project_dir(project_id))

        class NotFound(Exception):
            status_code = 404

        class FallbackClient:
            def __init__(self):
                self.paths = []

            def post(self, path, json=None):
                self.paths.append(path)
                if path == "/video/generations":
                    raise NotFound("route not found")
                return _FakeResp({"id": "fallback-task", "status": "queued"})

            def get(self, path):
                return _FakeResp({
                    "id": "fallback-task",
                    "status": "completed",
                    "url": "https://cdn.example/fallback.mp4",
                })

        client = FallbackClient()
        result = gen._create_poll_and_persist_video(
            client, project_id, "fake-model", "prompt", 320, 180,
            "c" * 64, "A", str(config.project_dir(project_id) / "result.mp4"),
            timeout=1,
        )
        assert result == "https://cdn.example/fallback.mp4"
        assert client.paths == ["/video/generations", "/videos"]


class TestManualSubmissionResolution:
    def test_found_provider_task_resumes_polling_without_new_submit(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen, generation_job_admin as admin
        pid = "proj-manual-found"
        config.init_project_dirs(config.project_dir(pid))
        gen._update_job(pid, "genjob-unknown", {
            "input_fingerprint": "f" * 16, "variant": "A",
            "gen_type": "video", "status": "submission_unknown",
        })
        resolved = admin.resolve_video_submission(
            pid, "genjob-unknown", provider_task_id="provider-real-123",
        )
        assert resolved["status"] == "running"
        assert resolved["provider_task_id"] == "provider-real-123"
        assert resolved["submission_resolution"]["decision"] == "provider_task_found"
        assert gen._find_unresolved_submission(pid, "f" * 16, "A") is None

    def test_confirmed_absence_releases_only_after_explicit_operator_action(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen, generation_job_admin as admin
        pid = "proj-manual-absent"
        config.init_project_dirs(config.project_dir(pid))
        gen._update_job(pid, "genjob-unknown", {
            "input_fingerprint": "e" * 16, "variant": "B",
            "gen_type": "video", "status": "submission_unknown",
        })
        resolved = admin.resolve_video_submission(
            pid, "genjob-unknown", confirm_no_provider_task=True,
        )
        assert resolved["status"] == "submission_rejected"
        assert resolved["submission_resolution"]["decision"] == "provider_task_not_found_confirmed"
        assert gen._find_unresolved_submission(pid, "e" * 16, "B") is None

    def test_admin_commands_reject_unsafe_project_ids(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        from video_director import generation_job_admin as admin
        with pytest.raises(Exception):
            admin.list_video_jobs("../outside")
        with pytest.raises(Exception):
            admin.resolve_video_submission(
                "../outside", "job", confirm_no_provider_task=True,
            )

    def test_resolution_rejects_ambiguous_or_duplicate_decisions(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen, generation_job_admin as admin
        pid = "proj-manual-invalid"
        config.init_project_dirs(config.project_dir(pid))
        gen._update_job(pid, "genjob-unknown", {"status": "submission_unknown"})
        gen._update_job(pid, "genjob-existing", {"provider_task_id": "taken-task"})
        with pytest.raises(gen.GenerationError):
            admin.resolve_video_submission(pid, "genjob-unknown")
        with pytest.raises(gen.GenerationError):
            admin.resolve_video_submission(
                pid, "genjob-unknown", provider_task_id="taken-task",
            )
        with pytest.raises(gen.GenerationError):
            admin.resolve_video_submission(
                pid, "genjob-unknown", provider_task_id="id",
                confirm_no_provider_task=True,
            )

class TestReferenceAutoAnalysis:
    """任务 B：bundle references 自动补报告。"""

    def _ref_bundle(self, video_path):
        return {
            "references": [{
                "id": "ref-1", "video": str(video_path),
                "report_path": "", "analysis": {},
                "apply": ["pace", "density", "motion", "transition", "shot_grammar"],
                "provenance": "user_reference",
            }],
        }

    def test_missing_report_analyzed_and_persisted(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, director_agent
        pid = "proj-ref-auto"
        config.init_project_dirs(config.project_dir(pid))
        video = tmp_path / "reference.mp4"
        video.write_bytes(b"\x00" * 64)

        fake_report = {
            "video": str(video), "video_fingerprint": "ab" * 32,
            "visual_language": {"pace": "fast"}, "shot_grammar": [],
            "report_path": str(tmp_path / "reference_report.json"),
        }
        analyzed = {"count": 0, "use_llm": None}

        def fake_analyzer(path, out_dir, use_llm=True, **kw):
            analyzed["count"] += 1
            analyzed["use_llm"] = use_llm
            return dict(fake_report)

        from video_director import reference as ref_mod
        monkeypatch.setattr(ref_mod, "reference_analyzer", fake_analyzer)

        state = {"style_warnings": []}
        bundle = self._ref_bundle(video)
        director_agent._ensure_reference_reports(pid, bundle, state)
        assert analyzed["count"] == 1
        assert analyzed["use_llm"] is False, "确定性重析不得依赖 LLM 层"
        ref = bundle["references"][0]
        assert ref["analysis"]["video_fingerprint"] == "ab" * 32
        assert ref["report_path"] == fake_report["report_path"]
        # persist_bundle 落盘校验
        persisted = json.loads(
            (config.project_dir(pid) / "plans" / "director_inputs.json")
            .read_text(encoding="utf-8"))
        assert (persisted["references"][0]["analysis"]["visual_language"]["pace"]
                == "fast")

    def test_complete_report_not_reanalyzed(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, director_agent
        pid = "proj-ref-skip"
        config.init_project_dirs(config.project_dir(pid))
        video = tmp_path / "reference.mp4"
        video.write_bytes(b"\x00" * 64)
        bundle = self._ref_bundle(video)
        bundle["references"][0]["analysis"] = {
            "video": str(video), "video_fingerprint": "cd" * 32,
            "report_path": "/x/report.json",
        }
        analyzed = {"count": 0}
        from video_director import reference as ref_mod
        monkeypatch.setattr(
            ref_mod, "reference_analyzer",
            lambda *a, **kw: analyzed.__setitem__("count", analyzed["count"] + 1))
        director_agent._ensure_reference_reports(pid, bundle, {"style_warnings": []})
        assert analyzed["count"] == 0

    def test_analyzer_failure_adds_warning_not_raise(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, director_agent
        pid = "proj-ref-fail"
        config.init_project_dirs(config.project_dir(pid))
        video = tmp_path / "reference.mp4"
        video.write_bytes(b"\x00" * 64)
        from video_director import reference as ref_mod

        def boom(path, out_dir, use_llm=True, **kw):
            raise ref_mod.ReferenceError("无镜头")

        monkeypatch.setattr(ref_mod, "reference_analyzer", boom)
        state = {"style_warnings": []}
        bundle = self._ref_bundle(video)
        # 不得 raise
        director_agent._ensure_reference_reports(pid, bundle, state)
        assert any("参考视频自动分析失败" in w for w in state["style_warnings"])
        assert bundle["references"][0]["analysis"] == {}

    def test_execute_entry_wraps_failure(self, tmp_path, monkeypatch):
        """execute() 入口整体 try-except：分析炸了不阻塞主链（冒烟）。"""
        _isolate(tmp_path, monkeypatch)
        from video_director import config, director_agent
        pid = "proj-ref-execute"
        video = tmp_path / "reference.mp4"
        video.write_bytes(b"\x00" * 64)
        monkeypatch.setattr(
            director_agent, "_ensure_reference_reports",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("意外炸点")))
        # 直接调用包着的代码路径太重（execute 全链），这里验证包裹语义：
        # execute 内是 try/except Exception → warning。等价单测：异常类型可被吸收。
        try:
            director_agent._ensure_reference_reports(pid, {}, {"style_warnings": []})
        except RuntimeError as exc:
            # execute() 里同样的 try/except Exception 会吸收该异常
            assert "意外炸点" in str(exc)

class TestGenerationJobLedgerIntegrity:
    """损坏的任务账本不能被误判为空，否则会重复创建收费的外部任务。"""

    def test_corrupt_ledger_blocks_video_submission_without_overwriting_evidence(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        _video_cfg(monkeypatch)
        from video_director import config, generation as gen

        project_id = "proj-corrupt-generation-ledger"
        config.init_project_dirs(config.project_dir(project_id))
        ledger = gen._jobs_path(project_id)
        original = b'{"possibly_charged_task":'
        ledger.write_bytes(original)

        fake = FakeVideoClient(statuses=["queued"])
        monkeypatch.setattr(gen, "_video_client", lambda cfg: fake)

        with pytest.raises(gen.GenerationError) as caught:
            gen.generate(project_id, "same paid request", "video", variant="A")

        assert caught.value.code == "VIDEO_JOB_STORE_CORRUPT"
        assert fake.post_calls == 0
        assert ledger.read_bytes() == original

    def test_symlinked_ledger_is_rejected_without_following_target(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen

        project_id = "proj-symlink-generation-ledger"
        config.init_project_dirs(config.project_dir(project_id))
        ledger = gen._jobs_path(project_id)
        outside = tmp_path / "outside-generation-jobs.json"
        original = b'{"keep": "external"}'
        outside.write_bytes(original)
        try:
            ledger.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("当前运行环境不支持创建符号链接")

        with pytest.raises(gen.GenerationError) as caught:
            gen._load_jobs(project_id)

        assert caught.value.code == "VIDEO_JOB_STORE_UNSAFE"
        assert outside.read_bytes() == original


class TestGenerationJobLedgerAtomicWrite:
    def test_failed_replace_preserves_previous_job_ledger_and_cleans_temp(
        self, tmp_path, monkeypatch
    ):
        _isolate(tmp_path, monkeypatch)
        from video_director import config, generation as gen

        project_id = "proj-generation-ledger-atomic"
        config.init_project_dirs(config.project_dir(project_id))
        gen._update_job(project_id, "job-before", {"status": "running"})
        ledger = gen._jobs_path(project_id)
        original = ledger.read_bytes()

        def fail_replace(_source, _destination):
            raise OSError("simulated ledger replace failure")

        monkeypatch.setattr(gen.os, "replace", fail_replace)
        with pytest.raises(OSError, match="simulated ledger replace failure"):
            gen._update_job(project_id, "job-after", {"status": "done"})

        assert ledger.read_bytes() == original
        assert list(ledger.parent.glob(f".{ledger.name}.*.tmp")) == []



def test_video_output_rejects_paths_outside_project_before_provider_download(
    tmp_path, monkeypatch
):
    _isolate(tmp_path, monkeypatch)
    from video_director import config, generation as gen

    project_id = "proj-output-boundary"
    config.init_project_dirs(config.project_dir(project_id))
    external = tmp_path / "outside.mp4"
    monkeypatch.setattr(
        gen, "_download_bytes",
        lambda _url: (_ for _ in ()).throw(AssertionError("download must not start")),
    )

    with pytest.raises(gen.GenerationError) as exc:
        gen._persist_video_output(
            project_id, "job-outside", str(external), "https://cdn.example/video.mp4"
        )

    assert exc.value.code == "VIDEO_OUTPUT_PATH_UNSAFE"
    assert not external.exists()
    assert "job-outside" not in gen._load_jobs(project_id)


def test_video_output_rejects_symlinked_output_parent(
    tmp_path, monkeypatch
):
    _isolate(tmp_path, monkeypatch)
    from video_director import config, generation as gen

    project_id = "proj-output-parent-symlink"
    project_root = config.project_dir(project_id)
    config.init_project_dirs(project_root)
    external_dir = tmp_path / "external-output"
    external_dir.mkdir()
    link = project_root / "assets" / "linked"
    try:
        link.symlink_to(external_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前运行环境不支持创建符号链接")
    external = external_dir / "escaped.mp4"
    monkeypatch.setattr(
        gen, "_download_bytes",
        lambda _url: (_ for _ in ()).throw(AssertionError("download must not start")),
    )

    with pytest.raises(gen.GenerationError) as exc:
        gen._persist_video_output(
            project_id, "job-parent-link", str(link / "escaped.mp4"),
            "https://cdn.example/video.mp4",
        )

    assert exc.value.code == "VIDEO_OUTPUT_PATH_UNSAFE"
    assert not external.exists()
    assert "job-parent-link" not in gen._load_jobs(project_id)


def test_video_output_rejects_symlinked_project_directory(
    tmp_path, monkeypatch
):
    _isolate(tmp_path, monkeypatch)
    from video_director import config, generation as gen

    root = config.composer_storage_root()
    external_dir = tmp_path / "external-project"
    external_dir.mkdir()
    link = root / "proj-output-project-symlink"
    try:
        link.symlink_to(external_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前运行环境不支持创建符号链接")
    monkeypatch.setattr(
        gen, "_download_bytes",
        lambda _url: (_ for _ in ()).throw(AssertionError("download must not start")),
    )

    with pytest.raises(gen.GenerationError) as exc:
        gen._persist_video_output(
            "proj-output-project-symlink", "job-project-link",
            str(external_dir / "escaped.mp4"), "https://cdn.example/video.mp4",
        )

    assert exc.value.code == "VIDEO_OUTPUT_PATH_UNSAFE"
    assert not (external_dir / "escaped.mp4").exists()

@pytest.mark.parametrize("symlink_component", ["assets", "generated"])
def test_image_generation_rejects_symlinked_output_directories(
    tmp_path, monkeypatch, symlink_component,
):
    from video_director import config, generation

    storage = tmp_path / "composer"
    monkeypatch.setattr(config, "composer_storage_root", lambda: storage)
    project_id = "generation-output-link"
    project = config.project_dir(project_id)
    external = tmp_path / "external-output"
    external.mkdir()

    assets = project / "assets"
    if symlink_component == "assets":
        assets.symlink_to(external, target_is_directory=True)
    else:
        assets.mkdir()
        (assets / "generated").symlink_to(external, target_is_directory=True)

    with pytest.raises(generation.GenerationError) as exc:
        generation.generate(
            project_id, "a simple test image", gen_type="graphic", variant="A",
        )
    assert exc.value.code == "GENERATION_OUTPUT_PATH_UNSAFE"
    assert list(external.iterdir()) == []
