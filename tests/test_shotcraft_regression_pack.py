"""§43 Regression Fixture Pack + 新命令回归（补做项的契约测试）。

Fixture Pack（§43）：fixtures/recipe-project.json / motion-project.json /
template-project.json + sample-assets/。回归序列（14 项中可在单测层覆盖的）：
    1. Apply ShotRecipe（replace_recipe）
    2. Apply Motion（replace_motion）
    4. Change Motion parameter（set_motion_parameter）
    9. Generate media in selected range（generate_media——图形运行时层）
    14. License preflight（见 test_shotcraft_integration）
覆盖新增能力：
    - §39 三层 Registry 加载 + 跨层同 id 同 version 冲突显式报错
    - §三十二 Style DNA 行为记录（used/pinned 白名单/拒收）
    - §20 新命令（set_transform/add_broll/set_audio_volume/lock/lock 后拒绝改动）
    - EDT-008 generation lineage（input/output fingerprint + generated CLEAR）
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_director import config, editor, memory, preflight  # noqa: E402
from video_director.models import (  # noqa: E402
    CompositionIR,
    Layer,
    LayerType,
    ProjectSpec,
    SceneIR,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def fixture_project(tmp_path, monkeypatch):
    """§43 fixture：sample-assets 落盘 + recipe-project 建临时项目。"""
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
    from tests.fixtures.fixture_data import (
        RECIPE_PROJECT, SAMPLE_BINARY_MAGIC)
    # sample-assets 落盘（§43 目录结构）
    sa = FIXTURES / "sample-assets"
    sa.mkdir(parents=True, exist_ok=True)
    for name, blob in SAMPLE_BINARY_MAGIC.items():
        (sa / name).write_bytes(blob)
    data = RECIPE_PROJECT
    pid = data["project_id"]
    base = tmp_path / pid
    (base / "plans").mkdir(parents=True)
    (base / "assets").mkdir(parents=True)
    ir = CompositionIR(**data["composition"])
    (base / "plans" / "composition.json").write_text(
        json.dumps(json.loads(ir.model_dump_json()), ensure_ascii=False), encoding="utf-8")
    (base / "assets" / "manifest.json").write_text(
        json.dumps(data["manifest"], ensure_ascii=False), encoding="utf-8")
    return pid, ir


class TestFixturePack:
    def test_fixture_files_exist(self):
        """§43：fixture 数据 + sample-assets 齐备。"""
        from tests.fixtures import fixture_data as fd
        assert fd.RECIPE_PROJECT["composition"]["scenes"]
        assert fd.MOTION_PROJECT["composition"]["scenes"]
        assert fd.TEMPLATE_PROJECT["composition"]["origin_template_id"]
        assert set(fd.SAMPLE_BINARY_MAGIC) == {"frame.jpg", "frame2.jpg", "clip.mp4"}

    def test_fixture_project_loadable(self, fixture_project):
        pid, ir = fixture_project
        assert editor.load_composition(pid).project.duration == ir.project.duration


class TestStyleDnaBehaviors:
    def test_used_pinned_rejected_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
        memory.init_db()
        assert memory.record_motion_behavior("motion.fade-in", "used")
        assert memory.record_motion_behavior("motion.fade-in", "pinned")
        assert memory.record_motion_behavior("motion.blur-slide", "rejected")
        summary = memory.motion_behavior_summary()
        assert summary["motion.fade-in"]["used"] == 1
        assert summary["motion.fade-in"]["pinned"] == 1
        assert summary["motion.blur-slide"]["rejected"] == 1

    def test_invalid_behavior_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
        memory.init_db()
        assert not memory.record_motion_behavior("motion.fade-in", "score_99")
        assert not memory.record_motion_behavior("", "used")


class TestLayeredRegistry:
    def test_user_layer_loads_and_merges(self, tmp_path, monkeypatch):
        """§39：user 层 item 与 system 层合并可见。"""
        from video_director.registries.core import LayeredRegistryLoader
        user_root = tmp_path / "registries" / "motions"
        user_root.mkdir(parents=True)
        (user_root / "motions.json").write_text(json.dumps([{
            "id": "motion.user-custom", "version": "1.0.0", "kind": "motion_recipe",
            "name": "用户自建动效", "commercial_status": "CLEAR",
            "provenance_ref": "src-own",
        }]), encoding="utf-8")
        monkeypatch.setattr(
            "video_director.registries.core.user_registry_root",
            lambda: tmp_path / "registries")
        loader = LayeredRegistryLoader("motion_recipe", "motions")
        items = loader.load_all()
        ids = [i.id for i in items]
        assert "motion.user-custom" in ids
        assert "motion.fade-in" in ids  # system 层仍在
        assert loader.layer_of("motion.user-custom", "1.0.0") == "user"
        assert loader.layer_of("motion.fade-in", "1.0.0") == "system"

    def test_same_id_version_across_layers_conflict(self, tmp_path, monkeypatch):
        """§39：同 id 同 version 跨层重复 → REGISTRY_CONFLICT（禁静默覆盖）。"""
        from video_director.registries.core import (
            LayeredRegistryLoader, RegistryError)
        # user 层复制 system 层的 motion.fade-in@1.0.0
        # user 层复制 system 层的 motion.fade-in@1.0.0（路径锚定仓库根，与运行 cwd 解耦）
        src = Path(__file__).resolve().parent.parent / "video_director" / "registries" / "motions" / "builtin" / "motions.json"
        dup = [x for x in json.loads(src.read_text(encoding="utf-8"))
               if x["id"] == "motion.fade-in"]
        user_root = tmp_path / "registries" / "motions"
        user_root.mkdir(parents=True)
        (user_root / "motions.json").write_text(json.dumps(dup), encoding="utf-8")
        monkeypatch.setattr(
            "video_director.registries.core.user_registry_root",
            lambda: tmp_path / "registries")
        loader = LayeredRegistryLoader("motion_recipe", "motions")
        with pytest.raises(RegistryError) as ei:
            loader.load_all()
        assert ei.value.code == "REGISTRY_CONFLICT"


class TestNewEditorCommands:
    def test_set_transform_static_scale_opacity(self, fixture_project):
        pid, _ = fixture_project
        r = editor.execute_command(pid, {
            "command": "set_transform", "targetId": "S01#0",
            "payload": {"scale": 1.5, "opacity": 0.8},
        })
        assert r.status == "APPLIED"
        ir = editor.load_composition(pid)
        tr = ir.scenes[0].layers[0].motion_instance["parameters"]["transform"]
        assert tr["scale"] == 1.5 and tr["opacity"] == 0.8
        # S02 未动（局部失效）
        assert ir.scenes[1].layers[0].motion_instance is None

    def test_set_transform_rejects_xy(self, fixture_project):
        pid, _ = fixture_project
        r = editor.execute_command(pid, {
            "command": "set_transform", "targetId": "S01#0",
            "payload": {"x": 120},
        })
        assert r.status == "REJECTED"
        assert r.errors[0]["code"] == "INVALID_PAYLOAD"

    def test_add_broll_image(self, fixture_project):
        pid, _ = fixture_project
        r = editor.execute_command(pid, {
            "command": "add_broll", "targetId": "S01",
            "payload": {"assetId": "img2", "assetType": "image"},
        })
        assert r.status == "APPLIED"
        ir = editor.load_composition(pid)
        assert len(ir.scenes[0].layers) == 2
        # 无缝铺满约束保持：两 layer 加起来 = scene.duration
        total = sum(l.duration for l in ir.scenes[0].layers)
        assert abs(total - ir.scenes[0].duration) < 0.05

    def test_add_broll_video_requires_source_out(self, fixture_project):
        pid, _ = fixture_project
        r = editor.execute_command(pid, {
            "command": "add_broll", "targetId": "S01",
            "payload": {"assetId": "vid1", "assetType": "video"},
        })
        assert r.status == "REJECTED"
        assert r.errors[0]["code"] == "INVALID_PAYLOAD"

    def test_set_audio_volume_creates_preset_track(self, fixture_project):
        pid, _ = fixture_project
        r = editor.execute_command(pid, {
            "command": "set_audio_volume", "targetId": "bgm",
            "payload": {"volume": 0.6},
        })
        assert r.status == "APPLIED"
        ir = editor.load_composition(pid)
        bgm = [t for t in ir.audio if t.type.value == "bgm"]
        assert bgm and abs(bgm[0].volume - 0.6) < 1e-6

    def test_lock_then_edit_rejected(self, fixture_project):
        pid, _ = fixture_project
        editor.execute_command(pid, {
            "command": "lock", "targetId": "S01#0", "payload": {"locked": True},
        })
        r = editor.execute_command(pid, {
            "command": "replace_motion", "targetId": "S01#0",
            "payload": {"motionRecipeId": "motion.fade-in"},
        })
        assert r.status == "REJECTED"
        assert r.errors[0]["code"] == "LAYER_LOCKED"
        # 解锁后可改
        editor.execute_command(pid, {
            "command": "lock", "targetId": "S01#0", "payload": {"locked": False},
        })
        r2 = editor.execute_command(pid, {
            "command": "replace_motion", "targetId": "S01#0",
            "payload": {"motionRecipeId": "motion.fade-in"},
        })
        assert r2.status == "APPLIED"

    def test_generate_media_command_appends_layer(self, fixture_project):
        """EDT-008：generate_media 命令把已生成素材落为层（revision 版本化）。"""
        pid, _ = fixture_project
        r = editor.execute_command(pid, {
            "command": "generate_media", "targetId": "S01",
            "payload": {"assetId": "gen-abc", "assetType": "image"},
        })
        assert r.status == "APPLIED"
        ir = editor.load_composition(pid)
        assert any(l.asset_id == "gen-abc" for l in ir.scenes[0].layers)


class TestGenerationLineage:
    def test_generate_image_variants_and_manifest(self, tmp_path, monkeypatch):
        """EDT-008：生成 3 变体 → manifest 带 fingerprint/provider/generated 溯源。"""
        monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            pytest.skip("playwright 不可用（图形渲染依赖）")
        from video_director import generation as vd_generation
        # This test verifies deterministic local fallback output, not a live LLM call.
        # CI may inherit developer/provider credentials; keep the test hermetic.
        from video_director import llm_provider
        monkeypatch.setattr(llm_provider, "get_llm", lambda: None)
        pid = "gentest-01"
        base = tmp_path / pid
        (base / "assets").mkdir(parents=True)
        results = vd_generation.generate(
            pid, "测试生成：效率提升", gen_type="image",
            width=320, height=180,
        )
        assert len(results) == 3
        assert [r.variant for r in results] == ["A", "B", "C"]
        manifest = json.loads(
            (base / "assets" / "manifest.json").read_text(encoding="utf-8"))
        assert len(manifest) == 3
        for m in manifest:
            assert m["origin"] == "generated"
            assert m["provider_id"] == "graphics-runtime"
            assert m["input_fingerprint"] and m["output_fingerprint"]
            assert Path(m["file"]).is_file()
        # generated → CLEAR
        assert preflight.commercial_status_of_origin("generated") == "CLEAR"

    def test_sfx_generation_honest_unavailable(self, tmp_path, monkeypatch):
        """EDT-008：sfx 无 provider → GENERATION_UNAVAILABLE（禁 fake）。"""
        monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
        from video_director import generation as vd_generation
        with pytest.raises(vd_generation.GenerationError) as ei:
            vd_generation.generate("gentest-02", "whoosh 音效", gen_type="sfx")
        assert ei.value.code == "GENERATION_UNAVAILABLE"


class TestAIProviderDispatch:
    def test_image_size_mapping(self):
        from video_director import generation as vd_generation
        assert vd_generation._image_size(1920, 1080) == "1792x1024"
        assert vd_generation._image_size(1080, 1920) == "1024x1792"
        assert vd_generation._image_size(1080, 1080) == "1024x1024"

    def test_image_provider_unconfigured_raises(self, monkeypatch):
        """§五十一 Capability G：roles.image 无 key → IMAGE_PROVIDER_UNAVAILABLE。"""
        from video_director import generation as vd_generation
        import video_director.generation as gmod
        # The fallback contract is deterministic and must not depend on CI secrets
        # or make network requests to a configured text-generation provider.
        from video_director import llm_provider
        monkeypatch.setattr(llm_provider, "get_llm", lambda: None)
        orig = gmod._role_cfg
        gmod._role_cfg = lambda role: {"api_key": ""}
        try:
            with pytest.raises(vd_generation.GenerationError) as ei:
                vd_generation.generate_image_variants("测试", 1080, 1920, count=1)
            assert ei.value.code == "IMAGE_PROVIDER_UNAVAILABLE"
        finally:
            gmod._role_cfg = orig

    def test_video_generation_unconfigured_honest(self, tmp_path, monkeypatch):
        """EDT-008：roles.video 无 key → 诚实报错（不 fake 成程序化卡）。"""
        monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
        import video_director.generation as gmod
        orig = gmod._role_cfg
        gmod._role_cfg = lambda role: {"api_key": ""}
        try:
            with pytest.raises(gmod.GenerationError) as ei:
                gmod.generate("gentest-vid", "夜景", gen_type="video")
            assert ei.value.code == "VIDEO_PROVIDER_UNAVAILABLE"
        finally:
            gmod._role_cfg = orig

    def test_image_unconfigured_falls_back_to_graphic(self, tmp_path, monkeypatch):
        """image 无 key → 兜底程序化视觉卡（仍真正成片，source=generated）。"""
        monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path)
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            pytest.skip("playwright 不可用")
        import video_director.generation as gmod
        orig = gmod._role_cfg
        gmod._role_cfg = lambda role: {"api_key": ""}
        try:
            pid = "genimg-fallback"
            base = tmp_path / pid
            (base / "assets").mkdir(parents=True)
            results = gmod.generate(pid, "测试配图", gen_type="image",
                                    width=320, height=180)
            assert len(results) == 3
            assert all(r.provider_id == "graphics-runtime" for r in results)
        finally:
            gmod._role_cfg = orig
