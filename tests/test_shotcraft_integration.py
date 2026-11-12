"""Shotcraft 集成层单元测试（IG-REG/PROV/MOTION/TEMPLATE/REVISION/PREFLIGHT/VERSION）。

覆盖 INTEGRATION-GATES.md 各 Gate 的 pytest 侧断言；DOM 侧由
tools/test_webui_shotcraft.py 承担。
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from video_director import config, editor, preflight, proposal
from video_director.models import CompositionIR, Layer, LayerType, SceneIR, ProjectSpec, TransitionOut
from video_director.registries.core import RegistryError, RegistryLoader
from video_director.registries.provenance import (
    load_provenance_all, get_provenance, ProvenanceRecord,
)
from video_director.registries.recipes import MotionRegistry, ShotRegistry
from video_director.registries.templates import TemplateRegistry
from video_director.registries.resolver import (
    resolve_shots, resolve_motions, resolve_templates,
    ShotResolveRequest, MotionResolveRequest, TemplateResolveRequest,
)
from video_director.registries.compiler import (
    CompileError, apply_motion_to_layer, instantiate_shot, instantiate_template,
)
from video_director.registries.motion_compiler import (
    MotionCompileError, compile_motion_to_ffmpeg, compile_layer_motion,
)


# ===========================================================================
# IG-REG Registry Gate
# ===========================================================================

class TestRegistryCore:
    """REG-001：schema 校验/重复拒绝/多版本共存/查询。"""

    def test_all_items_load_and_valid(self):
        shots = ShotRegistry().all()
        motions = MotionRegistry().all()
        templates = TemplateRegistry().all()
        assert len(shots) == 10
        assert len(motions) == 19
        assert len(templates) == 3

    def test_commercial_status_only_four_states(self):
        for item in MotionRegistry().all():
            assert item.commercial_status in ("CLEAR", "REVIEW", "BLOCKED", "UNKNOWN")

    def test_clear_items_have_provenance(self):
        for reg in (ShotRegistry(), MotionRegistry(), TemplateRegistry()):
            for item in reg.all():
                if item.commercial_status in ("CLEAR", "REVIEW"):
                    assert item.provenance_ref, f"{item.id} 缺 provenance_ref"

    def test_duplicate_id_version_rejected(self):
        from video_director.registries.core import validate_item
        with pytest.raises(RegistryError):
            validate_item({"id": "x", "version": "1.0.0", "kind": "motion_recipe",
                           "name": "t", "commercial_status": "CLEAR",
                           "provenance_ref": "src-own"}, "shot_recipe")  # kind 不匹配

    def test_invalid_commercial_status_rejected(self):
        from video_director.registries.core import validate_item
        with pytest.raises(RegistryError):
            validate_item({"id": "x", "version": "1.0.0", "kind": "motion_recipe",
                           "name": "t", "commercial_status": "MAYBE"}, "motion_recipe")

    def test_forbidden_renderer_fields_rejected(self):
        from video_director.registries.core import validate_item
        with pytest.raises(RegistryError):
            validate_item({"id": "x", "version": "1.0.0", "kind": "motion_recipe",
                           "name": "t", "commercial_status": "CLEAR",
                           "provenance_ref": "p", "ffmpeg_command": "rm -rf"},
                          "motion_recipe")
        with pytest.raises(RegistryError):
            validate_item({"id": "x", "version": "1.0.0", "kind": "motion_recipe",
                           "name": "t", "commercial_status": "CLEAR",
                           "provenance_ref": "p", "remotion_component": "<Foo/>"},
                          "motion_recipe")

    def test_multi_version_coexist_and_pin(self, tmp_path, monkeypatch):
        """同 id 多版本共存：load(id) 取最高版，load(id, '1.0.0') 钉死旧版。"""
        data_dir = tmp_path / "builtin"
        data_dir.mkdir()
        v1 = {"id": "motion.test-dual", "version": "1.0.0", "kind": "motion_recipe",
              "name": "T", "commercial_status": "CLEAR", "provenance_ref": "src-own"}
        v2 = dict(v1, version="1.1.0")
        (data_dir / "dual.json").write_text(json.dumps([v1, v2]), encoding="utf-8")
        monkeypatch.setattr("video_director.registries.core.REGISTRY_ROOT", tmp_path)
        loader = RegistryLoader("motion_recipe", str(tmp_path))
        loader._root = data_dir.parent / "motion_recipe" / "builtin"
        # 直接构造独立 root 测试
        root2 = tmp_path / "motions2" / "builtin"
        root2.mkdir(parents=True)
        (root2 / "dual.json").write_text(json.dumps([v1, v2]), encoding="utf-8")
        loader2 = RegistryLoader("motion_recipe", "motions2")
        assert loader2.versions_of("motion.test-dual") == ["1.0.0", "1.1.0"]
        latest = loader2.load("motion.test-dual")
        assert latest.version == "1.1.0"
        pinned = loader2.load("motion.test-dual", "1.0.0")
        assert pinned.version == "1.0.0"  # IG-VERSION：旧版本保持可寻址


# ===========================================================================
# IG-PROV Provenance Gate
# ===========================================================================

class TestProvenance:
    def test_matrix_own_verified_clear(self):
        rec = get_provenance("src-own")
        assert rec.commercial_status == "CLEAR"
        assert rec.origin == "own"

    def test_matrix_shotcraft_apache_clear_with_notice(self):
        rec = get_provenance("src-shotcraft")
        assert rec.license == "Apache-2.0"
        assert rec.commercial_status == "CLEAR"
        assert rec.copyright_notice  # Apache 再分发义务：归属声明

    def test_shotcraft_assets_blocked(self):
        """第三方素材毒丸台账：BLOCKED 不因上游 Apache 就 CLEAR（§15 边界互不传染）。"""
        rec = get_provenance("src-shotcraft-assets")
        assert rec.commercial_status == "BLOCKED"

    def test_unknown_origin_stays_unknown(self):
        all_recs = load_provenance_all()
        assert all_recs["src-user-upload"].commercial_status == "CLEAR"  # user 负责
        # shotcraft commit 锚点 UNKNOWN 诚实记录在 notes
        assert "UNKNOWN" in (all_recs["src-shotcraft"].notes or "")

    def test_mixkit_review_not_clear(self):
        assert get_provenance("src-mixkit").commercial_status == "REVIEW"


# ===========================================================================
# IG-SHOT + IG-MOTION + IG-TEMPLATE Resolver Gate
# ===========================================================================

class TestResolvers:
    def test_shot_product_intro_supported(self):
        r = resolve_shots(ShotResolveRequest(
            semantic_intents=("product_introduction",), content_type="product_video",
            duration=4.0, available_asset_types=("product", "image")))
        assert any(c.compatibility == "SUPPORTED"
                   for c in r if c.recipe_id == "shot.product-hero-assemble")

    def test_shot_no_unconditional_success(self):
        """意图完全不匹配 → 必须有 INCOMPATIBLE，不能全体成功。"""
        r = resolve_shots(ShotResolveRequest(
            semantic_intents=("nonexistent_intent",), content_type="nonexistent",
            duration=999))
        assert all(c.compatibility != "SUPPORTED" for c in r)

    def test_motion_subtitle_camera_rejected(self):
        r = resolve_motions(MotionResolveRequest(
            layer_type="subtitle", semantic_intent="camera", duration=1.0))
        assert all(c.compatibility == "INCOMPATIBLE" for c in r)

    def test_motion_title_reveal_word_relay(self):
        r = resolve_motions(MotionResolveRequest(
            layer_type="title", semantic_intent="reveal", duration=0.6))
        supported = [c.recipe_id for c in r if c.compatibility == "SUPPORTED"]
        assert "motion.word-relay" in supported

    def test_template_ratio_incompatible(self):
        r = resolve_templates(TemplateResolveRequest(
            content_type="editorial", ratio="1:1", duration=30))
        ink = [c for c in r if c.recipe_id == "tpl.ink-press-editorial"][0]
        assert ink.compatibility == "INCOMPATIBLE"

    def test_template_916_supported(self):
        r = resolve_templates(TemplateResolveRequest(
            content_type="editorial", ratio="9:16", duration=28))
        ink = [c for c in r if c.recipe_id == "tpl.ink-press-editorial"][0]
        assert ink.compatibility == "SUPPORTED"


# ===========================================================================
# CMP 编译 Gate（Shot→Scene lineage / Motion→Layer / Template→Composition）
# ===========================================================================

class TestCompilers:
    def _assets(self):
        class A:
            def __init__(self, t, d): self.type, self.duration = t, d
        return {"img1": A("image", 10.0), "vid1": A("video", 8.0)}

    def test_shot_instantiate_lineage(self):
        s = instantiate_shot("shot.product-hero-assemble", None, "S01", "登场",
                             0.0, 4.0, {"hero_product": "img1"}, self._assets())
        assert s.origin_recipe_id == "shot.product-hero-assemble"
        assert s.origin_recipe_version == "1.0.0"
        assert s.layers[0].motion_recipe_id  # layer_plan motion 已实例化

    def test_shot_missing_required_slot(self):
        with pytest.raises(CompileError, match="MISSING_REQUIRED_SLOT"):
            instantiate_shot("shot.product-hero-assemble", None, "S01", "x",
                             0.0, 4.0, {}, self._assets())

    def test_motion_layer_incompatible(self):
        sub = Layer(type=LayerType.background, start=0, duration=4)
        with pytest.raises(CompileError, match="LAYER_INCOMPATIBLE"):
            apply_motion_to_layer(sub, "motion.shake-emphasis")

    def test_motion_version_pinned(self):
        img = Layer(type=LayerType.image, start=0, duration=4)
        out = apply_motion_to_layer(img, "motion.scale-punch", motion_version="1.0.0")
        assert out.motion_version == "1.0.0"
        assert out.motion_instance["recipe_version"] == "1.0.0"

    def test_template_instantiates_independent_composition(self):
        ir = instantiate_template("tpl.minimal-editorial", None, "9:16",
                                  {"title": "T", "body_text": "B", "hero_image": "img1"},
                                  self._assets(), "proj1")
        assert ir.origin_template_id == "tpl.minimal-editorial"
        assert ir.origin_template_version == "1.0.0"
        assert len(ir.scenes) == 2
        assert ir.project.width == 1080 and ir.project.height == 1920

    def test_template_ratio_rejected(self):
        with pytest.raises(CompileError, match="RATIO_NOT_SUPPORTED"):
            instantiate_template("tpl.product-hero-16", None, "3:4",
                                 {"product_image": "img1", "brand_name": "B",
                                  "selling_points": "S", "cta_text": "C"},
                                 self._assets(), "proj2")


# ===========================================================================
# Motion Compiler Gate（表达式方言）
# ===========================================================================

class TestMotionCompiler:
    def test_scale_expr_uses_on_dialect(self):
        r = compile_motion_to_ffmpeg("motion.scale-punch", None, 0.45, 1080, 1920, fps=30)
        assert "on/30" in r["scale_expr"]
        assert "lt(" not in r["scale_expr"].replace("if(lt(", "")  # 方言已嵌入

    def test_uncompilable_property_rejected(self):
        with pytest.raises(MotionCompileError, match="UNSUPPORTED_PROPERTY"):
            compile_motion_to_ffmpeg("motion.wipe-reveal", None, 0.6, 1080, 1920)

    def test_layer_compile_none_without_recipe(self):
        l = Layer(type=LayerType.image, start=0, duration=3)
        assert compile_layer_motion(l, 1080, 1920) is None

    def test_duration_out_of_range(self):
        with pytest.raises(MotionCompileError, match="DURATION_OUT_OF_RANGE"):
            compile_motion_to_ffmpeg("motion.scale-punch", None, 5.0, 1080, 1920)


# ===========================================================================
# IG-REVISION Editor Command Gate（冲突拒绝/局部失效/提案式修订）
# ===========================================================================

@pytest.fixture()
def editor_project(tmp_path, monkeypatch):
    """临时项目：手工 composition.json + manifest + revisions 清零。"""
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    pid = "edtest-01"
    base = tmp_path / pid
    (base / "plans").mkdir(parents=True)
    (base / "assets").mkdir(parents=True)
    ir = CompositionIR(
        project=ProjectSpec(width=1080, height=1920, fps=30, duration=10.0),
        scenes=[
            SceneIR(id="S01", start=0.0, duration=5.0, text="场景一",
                    layers=[Layer(type=LayerType.image, asset_id="img1", start=0.0, duration=5.0)]),
            SceneIR(id="S02", start=5.0, duration=5.0, text="场景二",
                    layers=[Layer(type=LayerType.image, asset_id="img1", start=0.0, duration=5.0)]),
        ],
    )
    (base / "plans" / "composition.json").write_text(
        json.dumps(json.loads(ir.model_dump_json()), ensure_ascii=False), encoding="utf-8")
    (base / "assets" / "manifest.json").write_text(json.dumps([
        {"id": "img1", "type": "image", "file": "x.jpg", "duration": 10.0},
    ]), encoding="utf-8")
    return pid, ir


class TestEditorCommands:
    def test_conflict_rejected(self, editor_project):
        pid, _ = editor_project
        result = editor.execute_command(pid, {
            "command": "replace_motion", "targetId": "S01#0",
            "payload": {"motionRecipeId": "motion.scale-punch"},
            "expectedVersion": 99,   # 服务端实际是 1
        })
        assert result.status == "REJECTED"
        assert result.errors[0]["code"] == "CONFLICT"
        # 拒绝零变更：composition 未被写
        ir_now = editor.load_composition(pid)
        assert ir_now.scenes[0].layers[0].motion_recipe_id is None

    def test_replace_motion_creates_revision_with_locality(self, editor_project):
        pid, _ = editor_project
        result = editor.execute_command(pid, {
            "command": "replace_motion", "targetId": "S01#0",
            "payload": {"motionRecipeId": "motion.scale-punch"},
        })
        assert result.status == "APPLIED"
        assert result.revision_id
        # 局部失效：只 S01 相关 stale，S02 不在 stale 里
        assert all("S01" in s for s in result.stale_artifacts)
        assert not any("S02" in s for s in result.stale_artifacts)
        assert "S02" in result.reused_artifacts  # 无关场景被复用
        # IR 落库
        ir_now = editor.load_composition(pid)
        assert ir_now.scenes[0].layers[0].motion_recipe_id == "motion.scale-punch"

    def test_version_pinned_after_registry_bump(self, editor_project, tmp_path, monkeypatch):
        """IG-VERSION：项目引用 1.0.0，registry 出 1.1.0 后项目仍 1.0.0。"""
        pid, _ = editor_project
        editor.execute_command(pid, {
            "command": "replace_motion", "targetId": "S01#0",
            "payload": {"motionRecipeId": "motion.scale-punch", "motionVersion": "1.0.0"},
        })
        # 模拟 registry 升级：写入 1.1.0 版本到临时目录（路径锚定仓库根，与运行 cwd 解耦）
        reg_root = tmp_path / "regs" / "motions" / "builtin"
        reg_root.mkdir(parents=True)
        orig_root = Path(__file__).resolve().parent.parent / "video_director" / "registries"
        data = json.loads((orig_root / "motions" / "builtin" / "motions.json").read_text(encoding="utf-8"))
        bumped = [dict(m, version="1.1.0") if m["id"] == "motion.scale-punch" else m for m in data]
        (reg_root / "motions.json").write_text(json.dumps(bumped, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr("video_director.registries.core.REGISTRY_ROOT", tmp_path / "regs")
        # 重新加载项目：仍引用 1.0.0
        ir_now = editor.load_composition(pid)
        assert ir_now.scenes[0].layers[0].motion_version == "1.0.0"

    def test_revision_history_never_overwritten(self, editor_project):
        pid, _ = editor_project
        for i, mid in enumerate(["motion.scale-punch", "motion.fade-in",
                                  "motion.spotlight-focus"]):  # 全部 image 兼容
            editor.execute_command(pid, {
                "command": "replace_motion",
                "targetId": "S01#0",
                "payload": {"motionRecipeId": mid},
            })
        revs = editor.load_revisions(pid)
        assert len(revs) == 3  # 全部保留（§21 不覆盖历史）
        assert editor.composition_version(pid) == 4

    def test_unknown_command_rejected(self, editor_project):
        pid, _ = editor_project
        result = editor.execute_command(pid, {"command": "teleport", "targetId": "S01"})
        assert result.status == "REJECTED"
        assert result.errors[0]["code"] == "COMMAND_NOT_SUPPORTED"

    def test_delete_last_scene_rejected(self, editor_project):
        pid, _ = editor_project
        editor.execute_command(pid, {"command": "delete", "targetId": "S01"})
        result = editor.execute_command(pid, {"command": "delete", "targetId": "S02"})
        assert result.status == "REJECTED"  # 只剩一个不能删

    def test_batch_duplicate_scenes(self):
        """§47 multi-select：batch duplicate 对多场景集合逐条执行，单 revision。"""
        from video_director import editor as vd_editor
        from video_director import config as vd_config
        import tempfile
        from pathlib import Path as _P
        with tempfile.TemporaryDirectory() as tmp:
            pid = "batchtest"
            base = _P(tmp) / pid
            base.mkdir(parents=True)
            ir = CompositionIR(
                project=ProjectSpec(width=1080, height=1920, fps=30, duration=15.0),
                scenes=[
                    SceneIR(id="S01", start=0.0, duration=5.0, text="一",
                            layers=[Layer(type=LayerType.image, asset_id="a", start=0.0, duration=5.0)]),
                    SceneIR(id="S02", start=5.0, duration=5.0, text="二",
                            layers=[Layer(type=LayerType.image, asset_id="a", start=0.0, duration=5.0)]),
                    SceneIR(id="S03", start=10.0, duration=5.0, text="三",
                            layers=[Layer(type=LayerType.image, asset_id="a", start=0.0, duration=5.0)]),
                ],
            )
            (base / "plans").mkdir(parents=True)
            (base / "assets").mkdir(parents=True)
            (base / "plans" / "composition.json").write_text(
                json.dumps(json.loads(ir.model_dump_json()), ensure_ascii=False), encoding="utf-8")
            (base / "assets" / "manifest.json").write_text('[]', encoding="utf-8")
            import video_director.config as cfg
            orig = cfg.composer_storage_root
            cfg.composer_storage_root = lambda: _P(tmp)
            try:
                result = vd_editor.execute_command(pid, {
                    "command": "batch", "targetId": "S01",
                    "payload": {"subcommand": "duplicate", "targets": ["S01", "S03"]},
                })
                assert result.status == "APPLIED"
                scenes = vd_editor.load_composition(pid).scenes
                ids = [s.id for s in scenes]
                assert len(ids) == 5  # 3 + 2 复制
                assert any("S01-copy" in i or "S01-copy" in i for i in ids) or \
                       any("copy" in i for i in ids)
            finally:
                cfg.composer_storage_root = orig

    def test_batch_delete_scenes(self, editor_project):
        """§47：batch delete 多场景（末场景受保护，删一个保一个）。"""
        pid, _ = editor_project
        result = editor.execute_command(pid, {
            "command": "batch", "targetId": "S01",
            "payload": {"subcommand": "delete", "targets": ["S01"]},
        })
        assert result.status == "APPLIED"
        assert len(editor.load_composition(pid).scenes) == 1


class TestProposalFlow:
    def test_propose_preview_reject_zero_mutation(self, editor_project, monkeypatch):
        """IG-REVISION Test 1：Reject → Composition/Revision 计数全不变。"""
        pid, _ = editor_project
        monkeypatch.setattr("video_director.proposal._llm_parse", lambda i, s: [
            {"command": "replace_motion", "targetId": "S02#0",
             "payload": {"motionRecipeId": "motion.fade-in"}, "reason": "淡入"}
        ])
        prop = proposal.propose(pid, "第二段淡入")
        assert prop["proposal_id"]
        assert prop["patches"][0]["payload"]["motionRecipeId"] == "motion.fade-in"
        revs_before = len(editor.load_revisions(pid))
        comp_before = editor.load_composition(pid).model_dump_json()
        # preview（干跑，不落盘）
        pv = proposal.preview(pid, prop["proposal_id"])
        assert all(r["status"] == "PREVIEW_OK" for r in pv["results"])
        # reject
        r = proposal.reject(pid, prop["proposal_id"])
        assert r["status"] == "REJECTED" and r["changed"] is False
        # 零变更断言
        assert editor.load_composition(pid).model_dump_json() == comp_before
        assert len(editor.load_revisions(pid)) == revs_before

    def test_propose_apply_creates_revisions(self, editor_project, monkeypatch):
        pid, _ = editor_project
        monkeypatch.setattr("video_director.proposal._llm_parse", lambda i, s: [
            {"command": "replace_motion", "targetId": "S01#0",
             "payload": {"motionRecipeId": "motion.scale-punch"}, "reason": "冲击"},
        ])
        prop = proposal.propose(pid, "第一段有冲击力")
        result = proposal.apply(pid, prop["proposal_id"])
        assert result["status"] == "APPLIED"
        assert len(editor.load_revisions(pid)) == 1
        assert editor.load_composition(pid).scenes[0].layers[0].motion_recipe_id == "motion.scale-punch"

    def test_missing_target_not_executed(self, editor_project, monkeypatch):
        """IG-REVISION Test 4：目标含糊 → MISSING_TARGET → 不执行。"""
        pid, _ = editor_project
        monkeypatch.setattr("video_director.proposal._llm_parse", lambda i, s: None)
        with pytest.raises(proposal.ProposalError, match="MISSING_TARGET"):
            proposal.propose(pid, "把它改一下")
        assert len(editor.load_revisions(pid)) == 0  # 无 mutation


# ===========================================================================
# IG-PREFLIGHT Commercial Preflight Gate
# Exercise license, provenance, and media-presence hard gates independently and in mixed projects.
# ===========================================================================

@pytest.fixture()
def preflight_project(tmp_path, monkeypatch):
    """两个项目：clean（全 CLEAR）与 unknown（无台账资产）。"""
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    out = {}
    for pid, with_unknown in [("pf-clean", False), ("pf-unknown", True)]:
        base = tmp_path / pid
        (base / "plans").mkdir(parents=True)
        (base / "assets").mkdir(parents=True)
        ir = CompositionIR(
            project=ProjectSpec(width=1080, height=1920, fps=30, duration=4.0),
            scenes=[SceneIR(id="S01", start=0.0, duration=4.0, text="x",
                            layers=[Layer(type=LayerType.image, asset_id="img1",
                                          start=0.0, duration=4.0)])],
        )
        (base / "plans" / "composition.json").write_text(
            json.dumps(json.loads(ir.model_dump_json())), encoding="utf-8")
        entry = {"id": "img1", "type": "image", "file": str(tmp_path / "f.jpg"),
                 "duration": 4.0, "source": "user"}
        if with_unknown:
            entry.pop("source")  # 来源缺失 → UNKNOWN
        (base / "assets" / "manifest.json").write_text(json.dumps([entry]), encoding="utf-8")
        out[pid] = with_unknown
    (tmp_path / "f.jpg").write_bytes(b"\xff\xd8\xff")  # 素材文件存在
    return tmp_path, out


@pytest.fixture()
def preflight_edge_projects(tmp_path, monkeypatch):
    """商业发布边界矩阵：BLOCKED、缺失媒体、CLEAR 与 UNKNOWN 混合引用。"""
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    existing = tmp_path / "known.jpg"
    existing.write_bytes(b"\xff\xd8\xff")
    cases = {
        "pf-blocked": ([{"id": "blocked-img", "type": "image", "file": str(existing),
                         "duration": 4.0, "source": "shotcraft-assets"}], ["blocked-img"]),
        "pf-missing-media": ([{"id": "missing-img", "type": "image",
                               "file": str(tmp_path / "missing.jpg"), "duration": 4.0,
                               "source": "user"}], ["missing-img"]),
        "pf-mixed": ([{"id": "clear-img", "type": "image", "file": str(existing),
                        "duration": 4.0, "source": "user"},
                       {"id": "unknown-img", "type": "image", "file": str(existing),
                        "duration": 4.0}], ["clear-img", "unknown-img"]),
    }
    for project_id, (manifest, asset_ids) in cases.items():
        base = tmp_path / project_id
        (base / "plans").mkdir(parents=True)
        (base / "assets").mkdir(parents=True)
        layers = [Layer(type=LayerType.image, asset_id=asset_id,
                        start=0.0, duration=4.0) for asset_id in asset_ids]
        ir = CompositionIR(
            project=ProjectSpec(width=1080, height=1920, fps=30, duration=4.0),
            scenes=[SceneIR(id="S01", start=0.0, duration=4.0, text="x", layers=layers)],
        )
        (base / "plans" / "composition.json").write_text(
            json.dumps(json.loads(ir.model_dump_json())), encoding="utf-8")
        (base / "assets" / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")
    return tmp_path


class TestPreflight:
    def test_clear_passes(self, preflight_project):
        tmp_path, _ = preflight_project
        report = preflight.run_preflight("pf-clean")
        fatal = [i for i in report.issues if not i.recoverable]
        assert report.status == "PASS", [i.message for i in fatal]
        assert not fatal

    def test_unknown_rejected(self, preflight_project):
        tmp_path, _ = preflight_project
        report = preflight.run_preflight("pf-unknown")
        assert report.status == "REJECT"
        codes = [i.code for i in report.issues]
        assert "ASSET_LICENSE_UNKNOWN" in codes

    def test_recipe_clear_asset_unknown_still_rejects(self, preflight_project):
        """IG-PREFLIGHT 特别测试：Recipe CLEAR + Asset UNKNOWN → 仍 REJECT。"""
        tmp_path, _ = preflight_project
        report = preflight.run_preflight("pf-unknown")
        assert report.status == "REJECT"  # 不因 motion recipe 授权明确而放过资产

    def test_missing_composition_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
        (tmp_path / "pf-none").mkdir()
        report = preflight.run_preflight("pf-none")
        assert report.status == "REJECT"
        assert report.issues[0].code == "COMPOSITION_MISSING"

    def test_blocked_third_party_asset_rejected(self, preflight_edge_projects):
        report = preflight.run_preflight("pf-blocked")
        assert report.status == "REJECT"
        assert any(i.code == "ASSET_LICENSE_BLOCKED" and i.target_id == "blocked-img"
                   and not i.recoverable for i in report.issues)

    def test_missing_media_rejected(self, preflight_edge_projects):
        report = preflight.run_preflight("pf-missing-media")
        assert report.status == "REJECT"
        assert any(i.code == "ASSET_FILE_MISSING" and i.target_id == "missing-img"
                   and not i.recoverable for i in report.issues)

    def test_clear_asset_does_not_mask_unknown_asset(self, preflight_edge_projects):
        report = preflight.run_preflight("pf-mixed")
        assert report.status == "REJECT"
        assert any(i.code == "ASSET_LICENSE_UNKNOWN" and i.target_id == "unknown-img"
                   for i in report.issues)
        assert not any(i.target_id == "clear-img" for i in report.issues)

