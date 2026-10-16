import json
from pathlib import Path

import pytest

import video_director.asset_catalog as asset_catalog
import video_director.config as config
import video_director.project_manager as project_manager
import video_director.store as store
import video_director.workflow_run as workflow_run


@pytest.fixture
def isolated_storage(monkeypatch, tmp_path):
    root = tmp_path / "composer"
    monkeypatch.setattr(config, "composer_storage_root", lambda: root)
    # 屏蔽队列 worker 守护线程：入队不应在隔离 storage 下起真 worker 抢跑（会撞 jobs 表缺失）
    from video_director import studio as vd_studio
    monkeypatch.setattr(vd_studio, "_kick_worker", lambda: None)
    return root


def _make_project(pid: str):
    base = config.project_dir(pid)
    config.init_project_dirs(base)
    store.create_project(pid, "测试项目", {})
    return base


def test_project_bulk_delete_removes_workflow_runs_and_files(isolated_storage):
    pid = "web-bulk-delete"
    _make_project(pid)
    run_id = workflow_run.start_run("director_create", project_id=pid, steps=["understand"])
    workflow_run.finish_run(run_id, workflow_run.COMPLETED, result={"ok": True})

    result = project_manager.bulk_delete([pid])

    assert result["ok"] is True
    assert not (isolated_storage / pid).exists()
    assert workflow_run.runs_of_project(pid) == []
    assert project_manager.delete_verify(pid)["project_dir"] is True


def test_project_bulk_delete_rejects_active_batch_without_partial_delete(isolated_storage):
    active = "web-bulk-active"
    safe = "web-bulk-safe"
    _make_project(active)
    _make_project(safe)
    workflow_run.start_run("director_create", project_id=active, steps=["understand"])

    with pytest.raises(project_manager.ProjectActionError) as exc:
        project_manager.bulk_delete([active, safe])

    assert exc.value.code == "ACTIVE_JOB"
    assert (isolated_storage / active).is_dir()
    assert (isolated_storage / safe).is_dir()


def test_workflow_reconcile_marks_running_interrupted_and_project_failed(isolated_storage):
    pid = "web-reconcile"
    _make_project(pid)
    store.set_stage(pid, store.Stage.PLANNING)
    run_id = workflow_run.start_run("director_create", project_id=pid, steps=["understand"])

    result = workflow_run.reconcile_runs()

    assert run_id in result["interrupted_runs"]
    run = workflow_run.get_run(run_id)
    assert run["status"] == workflow_run.INTERRUPTED
    assert run["error"] == "服务重启，上一任务已中断"
    assert store.get_project(pid)["stage"] == store.Stage.FAILED.value


def test_asset_bulk_delete_rejects_used_asset_then_deletes_unused(isolated_storage, tmp_path):
    unused_file = tmp_path / "unused.png"
    unused_file.write_bytes(b"png-placeholder")
    used_file = tmp_path / "used.png"
    used_file.write_bytes(b"png-placeholder-2")
    catalog = {
        "local-unused": {
            "asset_id": "local-unused", "type": "image", "source": "user",
            "file": str(unused_file), "created_at": 1, "content_hash": "unused",
        },
        "local-used": {
            "asset_id": "local-used", "type": "image", "source": "user",
            "file": str(used_file), "created_at": 1, "content_hash": "used",
        },
    }
    asset_catalog._save_catalog(catalog)

    pid = "web-asset-owner"
    base = _make_project(pid)
    (base / "assets" / "manifest.json").write_text(
        json.dumps([{"id": "local-used"}], ensure_ascii=False), encoding="utf-8"
    )

    blocked = asset_catalog.delete_assets(["local-unused", "local-used"])
    assert blocked["ok"] is False
    assert blocked["code"] == "ASSET_IN_USE"
    assert asset_catalog.get_asset("local-unused") is not None
    assert asset_catalog.get_asset("local-used") is not None

    deleted = asset_catalog.delete_assets(["local-unused"])
    assert deleted["ok"] is True
    assert asset_catalog.get_asset("local-unused") is None
    assert not unused_file.exists()


def test_enqueue_series_batch_accepts_shared_images_and_videos(isolated_storage):
    """PR #5 系列：共享素材含图片时不能在入队层 NameError（CI 漏跑点）。"""
    from video_director import studio as vd_studio

    job_ids = vd_studio.enqueue_series_batch(
        "ser-img-01", {"voice": "zh-CN-XiaoxiaoNeural"},
        [{"episode": 1, "script": "第一集"}],
        video_files=["/tmp/a.mp4"], image_files=["/tmp/b.png"],
    )

    assert len(job_ids) == 1
    job = vd_studio.job_get(job_ids[0])
    spec = job["spec"]
    assert spec["video_files"] == ["/tmp/a.mp4"]
    assert spec["image_files"] == ["/tmp/b.png"]


def test_running_series_episode_run_protects_generated_project_from_delete(
        isolated_storage):
    """系列单集渲染中（ser-* pid 入队时预生成）：镜像 run 必须挂上真实 pid。

    pid 运行期随机时（PR #5 原实现），镜像 run 无 project_id → 渲染中的
    ser-* 项目查不到 RUNNING → mid-render 可删。预生成后 run/job/项目三者
    同源，删除保护恢复。
    """
    from video_director import studio as vd_studio

    job_ids = vd_studio.enqueue_series_batch(
        "ser-del-01", {}, [{"episode": 1, "script": "第一集"}],
        video_files=[], image_files=[])
    job = vd_studio.job_get(job_ids[0])
    spec = job["spec"]

    # pid 已在 enqueue 时预生成进 spec（镜像与 compose 同源）
    pid = spec["project_id"]
    assert pid and pid.startswith("ser-")
    run_id = vd_studio._mirror_to_workflow_run(job["job_id"], "series_episode", spec)
    assert run_id, "series 镜像 run 未登记"
    run = workflow_run.get_run(run_id)
    assert run["project_id"] == pid, "镜像 run 缺 project_id（mid-render 删除保护失效）"

    # 渲染中（run RUNNING + job running）：delete 必须被拒
    config.init_project_dirs(config.project_dir(pid))
    store.create_project(pid, "系列单集", {})
    from video_director import project_manager as pm
    with pytest.raises(pm.ProjectActionError) as exc:
        pm.delete(pid)
    assert exc.value.code == "ACTIVE_JOB"

    # 真实生命周期结束（job done + run COMPLETED）→ 删除放行
    vd_studio._set_job(job_ids[0], "done", result={"project_id": pid})
    workflow_run.finish_run(run_id, workflow_run.COMPLETED, result={})
    assert pm.delete(pid)["ok"] is True


def test_sync_confirm_render_registers_running_workflow_run(isolated_storage):
    """§四 /api/confirm 同步渲染也必须登记 RUNNING run（渲染中删除保护）。"""
    pid = "web-confirm-run"
    base = config.project_dir(pid)
    config.init_project_dirs(base)
    store.create_project(pid, "确认渲染测试", {})

    from webui import server as web_server
    run_id = web_server._run_confirm_as_workflow(pid)
    assert run_id, "confirm 渲染未登记 WorkflowRun"
    run = workflow_run.get_run(run_id)
    assert run["status"] == workflow_run.RUNNING
    assert run["project_id"] == pid

    # 渲染中：删除被拒（run 是 active_job 事实源）
    import video_director.project_manager as pm
    with pytest.raises(pm.ProjectActionError) as exc:
        pm.delete(pid)
    assert exc.value.code == "ACTIVE_JOB"

    # 渲染结束：run COMPLETED → 不再锁项目
    workflow_run.finish_run(run_id, workflow_run.COMPLETED, result={})
    assert pm.delete(pid)["ok"] is True


def test_frontend_bulk_contracts_and_ci_coverage():
    root = Path(__file__).resolve().parents[1]
    index = (root / "webui/static/index.html").read_text(encoding="utf-8")
    project_js = (root / "webui/static/js/project.js").read_text(encoding="utf-8")
    graphics_js = (root / "webui/static/js/graphics.js").read_text(encoding="utf-8")
    workflow_js = (root / "webui/static/js/workflow.js").read_text(encoding="utf-8")
    library_js = (root / "webui/static/js/library.js").read_text(encoding="utf-8")
    create_js = (root / "webui/static/js/create.js").read_text(encoding="utf-8")
    ci = (root / ".github/workflows/ai-director.yml").read_text(encoding="utf-8")

    assert 'id="pm-bulk-bar"' in index
    assert "/api/bulk_action" in project_js
    assert "/api/bulk_action" in graphics_js
    assert "/api/bulk_action" in workflow_js
    assert "/api/bulk_action" in library_js
    assert "PM.selected" in project_js
    assert "INBOX_UI.selected" in graphics_js
    assert "WFUI.selected" in workflow_js
    assert "ED_LIB.selectedAssets" in library_js

    assert "输入上下文" in create_js
    for marker in ('id="cf-script"', 'id="cf-assets"', 'id="cf-reference"', 'id="cf-repo-root"', 'name="cf-scope"'):
        assert marker in create_js

    assert "pytest -q" in ci
    # Self-hosted runner workflow calls compileall through a pinned
    # interpreter path ("$PY311_BIN/python3.11"); assert on the module
    # invocation itself, not the python prefix.
    assert "m compileall video_director webui" in ci
    assert 'node --check "$f"' in ci


def test_webui_storage_root_isolation_not_hardcoded():
    """§五：server 项目数据根必须走 config.composer_storage_root()（env 隔离），
    不得用硬编码 STORAGE/composer（否则 VIDEO_DIRECTOR_STORAGE_ROOT 下测试
    服务器会把素材/缓存写进生产 storage）。"""
    root = Path(__file__).resolve().parents[1]
    server = (root / "webui" / "server.py").read_text(encoding="utf-8")
    blocks = (root / "video_director" / "graphics" / "blocks.py").read_text(encoding="utf-8")

    assert 'COMPOSER_ROOT = vd_config.composer_storage_root()' in server
    # 数据路径不再有硬编码 STORAGE/composer
    assert 'STORAGE / "composer"' not in server
    # 自定义块读取不再内部拼 /composer（隔离根已是 composer root）
    assert 'Path(storage_root) / "custom_blocks.json"' in blocks
