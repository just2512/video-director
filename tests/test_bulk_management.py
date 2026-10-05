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

    assert "tests/test_director_unified_inputs.py" in ci
    assert "tests/test_bulk_management.py" in ci
    assert "python -m compileall video_director webui" in ci
    assert 'node --check "$f"' in ci
