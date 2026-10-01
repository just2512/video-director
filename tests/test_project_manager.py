"""Project Management Contract 测试（方案 §5/§6/§52/§53/§69，IG-PROJECT-MANAGEMENT）。

覆盖：
- §52 Project Index：status/editable/legacy/actions 字段 + UNKNOWN 目录项目列出
- rename / clone（成片不复制）/ archive / restore
- §6.3 delete：五层验证（§69）、ACTIVE_JOB REJECT、路径穿越 REJECT
- §53 legacy_detect / legacy_convert（新项目 editable + origin 保留 + 旧的原样在）
- filter_index 纯函数（§8 搜索/筛选）

storage 隔离：monkeypatch config.composer_storage_root → tmp_path。
"""

import json

import pytest

from video_director import project_manager as pm
from video_director import store
from video_director.project_manager import ProjectActionError


@pytest.fixture(autouse=True)
def _iso_storage(tmp_path, monkeypatch):
    """全部测试隔离在 tmp storage（config.project_dir 同根，调用链自动一致）。"""
    from video_director import config
    (tmp_path / "composer").mkdir()
    monkeypatch.setattr(config, "composer_storage_root",
                        lambda: tmp_path / "composer")
    store.init_db()
    yield


def _mk_project(pid, stage="DONE", name=None, params=None):
    """造一个真实形状的项目：目录 + composition.json + DB 行。"""
    from video_director import config
    base = config.project_dir(pid)
    config.init_project_dirs(base)
    (base / "plans" / "composition.json").write_text(json.dumps({
        "project": {"title": pid, "width": 1080, "height": 1920,
                    "fps": 30, "duration": 6.0},
        "scenes": [
            {"id": "scene-1", "start": 0.0, "duration": 3.0, "text": "一", "layers": []},
            {"id": "scene-2", "start": 3.0, "duration": 3.0, "text": "二", "layers": []},
        ],
        "audio": [], "watermarks": [],
    }), encoding="utf-8")
    (base / "output" / "final.mp4").write_bytes(b"fake-final")
    p = dict(params or {})
    if name:
        p["name"] = name
    store.create_project(pid, "项目文案" + pid, p)
    from video_director.store import Stage
    store.set_stage(pid, Stage[stage] if stage in Stage.__members__ else Stage.CREATED)
    if stage == "DONE":
        store.finish_project(pid, str(base / "output" / "final.mp4"), 6.0,
                             {"verdict": "PASS"})
    return pid


def _mk_legacy(pid):
    """造一个旧 gfx 渲染项目：只有 graphic_plan.json，无 composition.json。"""
    from video_director import config
    base = config.project_dir(pid)
    config.init_project_dirs(base)
    (base / "plans" / "graphic_plan.json").write_text(json.dumps({
        "content_type": "knowledge", "style_id": "editorial",
        "width": 1080, "height": 1920,
        "scenes": [
            {"id": "s1", "role": "problem", "text": "旧文案一", "narration": "旧文案一",
             "duration": 2.5, "block_id": "kb.title", "block_name": "标题排版",
             "layout": "center", "source": "Generated Graphic",
             "motion_id": "graphics.fade-up", "visual_intent": "吸引注意"},
            {"id": "s2", "role": "statistic", "text": "旧文案二", "narration": "旧文案二",
             "duration": 2.5, "block_id": "kb.statistic", "block_name": "数字冲击",
             "layout": "left", "source": "Generated Graphic",
             "motion_id": "graphics.scale-punch", "visual_intent": "数字冲击"},
        ],
    }), encoding="utf-8")
    (base / "output" / "final.mp4").write_bytes(b"legacy-final")
    store.create_project(pid, "[图形] knowledge", {"mode": "graphic_workspace"})
    return pid


# ---------------------------------------------------------------------------
# §52 Project Index
# ---------------------------------------------------------------------------

class TestListIndex:
    def test_index_fields_and_states(self):
        _mk_project("web-done-01", stage="DONE", name="产品宣传片")
        _mk_legacy("gfx-legacy-01")
        # 目录存在但 DB 无行 → UNKNOWN 也要列出（用户磁盘上看得到）
        from video_director import config
        (config.project_dir("tpl-orph-01") / "plans").mkdir(parents=True, exist_ok=True)

        idx = {p["project_id"]: p for p in pm.list_index()}
        assert set(idx) >= {"web-done-01", "gfx-legacy-01", "tpl-orph-01"}

        done = idx["web-done-01"]
        assert done["name"] == "产品宣传片"
        assert done["status"] == "DONE"
        assert done["editable"] is True
        assert done["legacy"] is False
        assert done["archived"] is False
        assert done["has_final"] is True
        assert done["active_job"] is False
        # §5 DONE 状态矩阵
        assert done["actions"]["open"] and done["actions"]["delete"]
        assert done["actions"]["duplicate"] and done["actions"]["rename"]

        legacy = idx["gfx-legacy-01"]
        assert legacy["legacy"] is True
        assert legacy["editable"] is False
        # §5 LEGACY：convert/preview/archive/delete，无 open
        assert legacy["actions"]["convert"] is True
        assert legacy["actions"]["open"] is False
        assert legacy["actions"]["delete"] is True

        orphan = idx["tpl-orph-01"]
        assert orphan["status"] == "UNKNOWN"
        assert orphan["editable"] is False

    def test_active_job_delete_rejected_in_actions(self):
        _mk_project("web-run-01", stage="RENDERING")
        idx = {p["project_id"]: p for p in pm.list_index()}
        p = idx["web-run-01"]
        assert p["active_job"] is True
        assert p["actions"]["delete"] is False  # §5 RUNNING：删除禁用
        assert p["actions"]["open"] is True     # 可查看进度

    def test_filter_index_pure_function(self):
        _mk_project("web-a", stage="DONE", name="甲项目")
        _mk_project("web-b", stage="AWAITING_CONFIRM", name="乙项目")
        _mk_project("web-c", stage="FAILED", name="丙项目")
        _mk_legacy("gfx-l")
        pm.archive("web-a")
        full = pm.list_index()
        assert len(pm.filter_index(full, "", "all")) >= 4
        assert any(p["project_id"] == "web-b"
                   for p in pm.filter_index(full, "", "pending"))
        assert all(p["status"] == "FAILED"
                   for p in pm.filter_index(full, "", "failed"))
        arch = pm.filter_index(full, "", "archived")
        assert {p["project_id"] for p in arch} == {"web-a"}
        leg = pm.filter_index(full, "", "legacy")
        assert {p["project_id"] for p in leg} == {"gfx-l"}
        # 搜索：名称模糊（大小写不敏感）+ pid
        assert any("甲" in p["name"] for p in pm.filter_index(full, "甲", "all"))
        assert any(p["project_id"] == "web-c"
                   for p in pm.filter_index(full, "web-c", "all"))


# ---------------------------------------------------------------------------
# rename / clone / archive / restore
# ---------------------------------------------------------------------------

class TestProjectActions:
    def test_rename(self):
        _mk_project("web-rn-01")
        r = pm.rename("web-rn-01", "新名字")
        assert r["ok"] is True
        assert store.get_project_name("web-rn-01") == "新名字"
        assert any(p["name"] == "新名字" for p in pm.list_index())

    def test_rename_empty_rejected(self):
        _mk_project("web-rn-02")
        with pytest.raises(ProjectActionError) as e:
            pm.rename("web-rn-02", "  ")
        assert e.value.code == "INVALID_NAME"

    def test_rename_orphan_dir_upserts_row(self):
        """目录存在但 DB 无行 → rename 先 upsert（不能静默失败）。"""
        from video_director import config
        (config.project_dir("web-orph-rn") / "plans").mkdir(parents=True, exist_ok=True)
        r = pm.rename("web-orph-rn", "补录项目")
        assert r["ok"] is True
        assert store.get_project_name("web-orph-rn") == "补录项目"

    def test_clone(self):
        _mk_project("web-cl-01", name="原项目")
        r = pm.clone("web-cl-01")
        assert r["ok"] is True
        new_pid = r["project_id"]
        assert new_pid.startswith("web-") and new_pid != "web-cl-01"
        from video_director import config
        # composition 复制；成片不复制（副本是待编辑项目，不是已发布项目）
        assert (config.composer_storage_root() / new_pid / "plans" /
                "composition.json").is_file()
        assert not (config.composer_storage_root() / new_pid / "output" /
                    "final.mp4").is_file()
        row = store.get_project(new_pid)
        assert row["stage"] == "CREATED"
        params = json.loads(row["params_json"])
        assert "（副本）" in params["name"]
        assert params["cloned_from"] == "web-cl-01"

    def test_archive_restore(self):
        _mk_project("web-ar-01")
        assert pm.archive("web-ar-01")["ok"] is True
        p = next(x for x in pm.list_index() if x["project_id"] == "web-ar-01")
        assert p["archived"] is True
        # 归档项目可恢复/删除，open 保持（回得来）
        assert p["actions"]["delete"] is True
        assert pm.restore("web-ar-01")["ok"] is True
        p2 = next(x for x in pm.list_index() if x["project_id"] == "web-ar-01")
        assert p2["archived"] is False

    def test_archive_active_job_rejected(self):
        _mk_project("web-ar-run", stage="RENDERING")
        with pytest.raises(ProjectActionError) as e:
            pm.archive("web-ar-run")
        assert e.value.code == "ACTIVE_JOB"


# ---------------------------------------------------------------------------
# §6.3 delete + §69 五层验证
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_five_layers_all_missing(self):
        pid = _mk_project("web-del-01")
        r = pm.delete(pid)
        assert r["ok"] is True
        v = pm.delete_verify(pid)
        # §69：DB / 目录 / composition / 产物 / 旧方案 全 MISSING
        assert all(v.values()), f"删除后残留: {v}"

    def test_delete_active_job_rejected(self):
        _mk_project("web-del-run", stage="TTS")
        with pytest.raises(ProjectActionError) as e:
            pm.delete("web-del-run")
        assert e.value.code == "ACTIVE_JOB"
        # §60 禁止 4：REJECT 后项目原样（未删半截）
        assert store.get_project("web-del-run") is not None

    def test_delete_path_traversal_rejected(self):
        for evil in ("../evil", "..\\evil", "a/b/c", "x" * 65, ""):
            with pytest.raises(ProjectActionError) as e:
                pm.delete(evil)
            assert e.value.code in ("INVALID_PROJECT_ID", "NOT_FOUND")

    def test_delete_missing_project(self):
        with pytest.raises(ProjectActionError) as e:
            pm.delete("web-never-exists")
        assert e.value.code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# §53 legacy
# ---------------------------------------------------------------------------

class TestLegacy:
    def test_legacy_detect(self):
        _mk_legacy("gfx-leg-01")
        _mk_project("web-norm-01")
        assert pm.legacy_detect("gfx-leg-01")["legacy"] is True
        assert pm.legacy_detect("web-norm-01")["legacy"] is False
        assert pm.legacy_detect("web-norm-01")["editable"] is True

    def test_legacy_convert(self):
        old_pid = _mk_legacy("gfx-leg-02")
        r = pm.legacy_convert(old_pid)
        assert r["ok"] is True
        new_pid = r["project_id"]
        assert new_pid != old_pid and new_pid.startswith("gfx-")
        # 新项目可编辑（composition 就位）+ AWAITING_CONFIRM + origin 记录
        from video_director import config
        assert (config.composer_storage_root() / new_pid / "plans" /
                "composition.json").is_file()
        row = store.get_project(new_pid)
        assert row["stage"] == "AWAITING_CONFIRM"
        assert json.loads(row["params_json"])["origin"] == old_pid
        idx = {p["project_id"]: p for p in pm.list_index()}
        assert idx[new_pid]["editable"] is True
        assert idx[new_pid]["legacy"] is False
        # §36 旧项目原样保留（不破坏旧数据）
        assert pm.legacy_detect(old_pid)["legacy"] is True
        assert idx[old_pid]["has_final"] is True

    def test_legacy_convert_missing_plan(self):
        from video_director import config
        (config.project_dir("gfx-leg-empty") / "plans").mkdir(parents=True, exist_ok=True)
        with pytest.raises(ProjectActionError) as e:
            pm.legacy_convert("gfx-leg-empty")
        assert e.value.code == "CONVERSION_ERROR"

    def test_legacy_editor_not_polluted(self):
        """§71 Legacy Gate：Editor 项目选择器只显示 editable=true（Index 层保证）。"""
        _mk_legacy("gfx-leg-03")
        _mk_project("web-ed-01")
        editable = [p for p in pm.list_index() if p["editable"]]
        assert all(not p["legacy"] for p in editable)
        assert any(p["project_id"] == "web-ed-01" for p in editable)
        assert not any(p["project_id"] == "gfx-leg-03" for p in editable)


# ---------------------------------------------------------------------------
# server 路由层（action 分发）
# ---------------------------------------------------------------------------

class TestServerProjectActionRoute:
    """project_action 分发通过 project_manager 的公共函数间接验证（HTTP 层
    在 webui/server.py，本套件测业务契约；路由分发逻辑是薄壳，Playwright
    验收覆盖端到端）。"""

    def test_action_via_manager_matches_route_contract(self):
        """rename/clone/delete/convert 四动作走 project_manager 同一路径。"""
        _mk_project("web-srv-01", name="服务层测试")
        _mk_legacy("gfx-srv-leg")
        assert pm.rename("web-srv-01", "改名后")["ok"] is True
        assert pm.clone("web-srv-01")["ok"] is True
        assert pm.delete("web-srv-01")["ok"] is True
        assert pm.legacy_convert("gfx-srv-leg")["ok"] is True
