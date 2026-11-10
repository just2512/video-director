"""最终方案 §25/§26/§27/§32/§39/§十 Asset Library 契约测试。

覆盖：
- §25 Asset Graph：统一 Asset 结构（hash/source/commercial/溯源字段）
- §26 上传链：magic bytes 拒假文件 → probe → 内容 hash 查重 → Library 分区
- §32 Commercial：用户素材 = USER_SUPPLIED（不默认 CLEAR）；UNKNOWN ≠ CLEAR
- §27 自动检测：尺寸/方向/时长/帧率/含音频/编码
- §39 生成/Stock 产物入 Registry
- §十 ratio 由项目画幅推导（不硬编码 9:16）

媒体：ffmpeg lavfi 运行时生成（git 里的 sample-assets 是 36B 占位物，
checkout 即还原——真媒体必须测试内现场造，不依赖工作区状态）。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from video_director import config


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    """ffmpeg lavfi 生成真实测试媒体（视频 540x960 + 两张图）。"""
    d = tmp_path_factory.mktemp("media")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not Path(ffmpeg).is_file():
        try:
            from video_director.prober import _resolve_ffmpeg
            cand = _resolve_ffmpeg()
            if cand and Path(cand).is_file():
                ffmpeg = cand
        except Exception:
            ffmpeg = None
    if not ffmpeg or not Path(ffmpeg).is_file():
        pytest.skip("本机无 ffmpeg，跳过 Asset Library 媒体探测测试")
    clip = d / "clip.mp4"
    img = d / "frame.jpg"
    img2 = d / "frame2.jpg"
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=duration=2:size=540x960:rate=10", "-pix_fmt", "yuv420p",
                    str(clip)], check=True)
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=320x480:d=0.04", "-frames:v", "1", str(img)], check=True)
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=480x320:d=0.04", "-frames:v", "1", str(img2)], check=True)
    return {"clip": str(clip), "img": str(img), "img2": str(img2)}


@pytest.fixture()
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "composer_storage_root", lambda: tmp_path / "composer")
    yield


class TestAssetLibrary:
    def test_upload_fingerprint_25(self, _iso, fixtures):
        """§25 完整 Asset 元数据指纹。"""
        from video_director import asset_catalog as ac
        entry = ac.import_asset(fixtures["clip"])
        for key in ("asset_id", "type", "source", "file", "width", "height",
                    "duration", "content_hash", "commercial_status",
                    "license_note", "created_at", "orientation"):
            assert key in entry, f"§25 字段缺失: {key}"
        assert entry["type"] == "video"
        assert entry["content_hash"]
        assert (config.composer_storage_root() / "assets" / "local").is_dir(), "§26 分区 local"

    def test_dedup_by_hash(self, _iso, fixtures):
        """§25 同一文件重复上传 = 同一 Asset（内容 hash 查重）。"""
        from video_director import asset_catalog as ac
        e1 = ac.import_asset(fixtures["clip"])
        e2 = ac.import_asset(fixtures["clip"])
        assert e1["asset_id"] == e2["asset_id"]
        assert len(ac.list_assets()) == 1

    def test_concurrent_import_same_content_creates_one_library_asset(
        self, _iso, fixtures
    ):
        """并发导入同一内容必须在最终写入事务内再次查重。"""
        from concurrent.futures import ThreadPoolExecutor
        from video_director import asset_catalog as ac

        with ThreadPoolExecutor(max_workers=4) as pool:
            entries = list(pool.map(
                lambda _: ac.import_asset(fixtures["clip"]),
                range(4),
            ))
        assert len({entry["asset_id"] for entry in entries}) == 1
        assert len(ac.list_assets()) == 1

    def test_catalog_replace_failure_preserves_previous_json(self, _iso, monkeypatch):
        """catalog 原子替换失败时，旧 JSON 保持完整且临时文件清理。"""
        from video_director import asset_catalog as ac

        path = ac._catalog_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        original = {"existing": {"asset_id": "existing"}}
        path.write_text(json.dumps(original), encoding="utf-8")

        def fail_replace(source, destination):
            raise OSError("simulated catalog replace failure")

        monkeypatch.setattr(ac.os, "replace", fail_replace)
        with pytest.raises(OSError, match="simulated catalog replace failure"):
            ac._save_catalog({"new": {"asset_id": "new"}})

        assert json.loads(path.read_text(encoding="utf-8")) == original
        assert list(path.parent.glob(".catalog-*.tmp")) == []

    def test_concurrent_semantic_updates_do_not_drop_other_assets(
        self, _iso, fixtures
    ):
        """两个并发语义更新不得因 read/modify/write 覆盖而丢失另一条记录。"""
        from concurrent.futures import ThreadPoolExecutor
        from video_director import asset_catalog as ac

        first = ac.import_asset(fixtures["img"])
        second = ac.import_asset(fixtures["img2"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda args: ac.update_asset_semantics(
                    args[0], description=args[1]
                ),
                [(first["asset_id"], "first semantic"),
                 (second["asset_id"], "second semantic")],
            ))
        stored = {entry["asset_id"]: entry for entry in ac.list_assets()}
        assert stored[first["asset_id"]]["description"] == "first semantic"
        assert stored[second["asset_id"]]["description"] == "second semantic"
        assert {item["description"] for item in results} == {
            "first semantic", "second semantic"
        }

    def test_magic_bytes_reject_fake(self, _iso, tmp_path):
        """§26 安检：内容与扩展名不符拒绝（不进库）。"""
        from video_director import asset_catalog as ac
        fake = tmp_path / "evil.mp4"
        fake.write_bytes(b"plain text pretending to be a video")
        with pytest.raises(ac.AssetLibraryError) as e:
            ac.import_asset(str(fake))
        assert e.value.code == "BAD_FILE_TYPE"

    def test_unsupported_ext_rejected(self, _iso, tmp_path):
        from video_director import asset_catalog as ac
        f = tmp_path / "x.gif"
        f.write_bytes(b"GIF89a")
        with pytest.raises(ac.AssetLibraryError) as e:
            ac.import_asset(str(f))
        assert e.value.code == "UNSUPPORTED_EXT"

    def test_user_supplied_not_clear_32(self, _iso, fixtures):
        """§32 用户素材 = USER_SUPPLIED（用户承担权利，不默认可商用）。"""
        from video_director import asset_catalog as ac
        entry = ac.import_asset(fixtures["clip"])
        assert entry["commercial_status"] == ac.USER_SUPPLIED
        assert ac.USER_SUPPLIED != ac.CLEAR

    def test_probe_autodetect_27(self, _iso, fixtures):
        """§27 自动检测：尺寸/方向/时长/编码。"""
        from video_director import asset_catalog as ac
        v = ac.import_asset(fixtures["clip"])
        assert v["width"] and v["height"]
        assert v["orientation"] in ("portrait", "landscape", "square")
        assert v["duration"] is not None
        img = ac.import_asset(fixtures["img"])
        assert img["type"] == "image" and img["width"]

    def test_make_thumbnail_real_render_26(self, _iso, fixtures):
        """§26 缩略图真实渲染：视频抽帧 + 图片直缩 + 幂等。

        回归锚点：旧实现依赖 ffmpeg-python（requirements 从未包含）→
        import 即 ModuleNotFoundError 被吞 → 恒 None → /api/asset_thumbnail
        404。此测试用真 ffmpeg 生成真 JPEG，锁住「缩略图能力可用」。
        """
        from video_director import asset_catalog as ac
        v = ac.import_asset(fixtures["clip"])
        thumb = ac.make_thumbnail(v["asset_id"])
        assert thumb and Path(thumb).is_file(), "视频缩略图必须真实生成"
        assert Path(thumb).stat().st_size > 500, "缩略图应为真实 JPEG（非空壳）"
        assert Path(thumb).suffix == ".jpg"
        # 幂等：再次请求命中缓存路径（同一路径，不重复生成）
        assert ac.make_thumbnail(v["asset_id"]) == thumb

        img = ac.import_asset(fixtures["img"])
        thumb2 = ac.make_thumbnail(img["asset_id"])
        assert thumb2 and Path(thumb2).is_file(), "图片缩略图必须真实生成"
        assert Path(thumb2).stat().st_size > 500

        # 幽灵 id / 无文件 → 诚实 None（不抛异常不阻断入库链）
        assert ac.make_thumbnail("ghost-id-xyz") is None

    def test_make_thumbnail_short_video_first_frame(self, _iso, tmp_path, fixtures):
        """§26 短视频（<1s）在 1s 处无帧 → 回退首帧，缩略图仍生成。"""
        import subprocess as sp
        import shutil
        from video_director import asset_catalog as ac
        from video_director.prober import _resolve_ffmpeg
        ff = _resolve_ffmpeg()
        if not ff:
            pytest.skip("本机无 ffmpeg，跳过短视频缩略图回退测试")
        tiny = tmp_path / "tiny.mp4"
        sp.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i",
                "testsrc=duration=0.3:size=270x480:rate=10", "-pix_fmt", "yuv420p",
                str(tiny)], check=True)
        entry = ac.import_asset(str(tiny))
        thumb = ac.make_thumbnail(entry["asset_id"])
        assert thumb and Path(thumb).is_file(), "短视频必须回退首帧生成缩略图"
        assert Path(thumb).stat().st_size > 500

    def test_generated_registers_39(self, _iso, fixtures):
        """§39 AI 生成产物 = 普通 Asset（source=generated）。"""
        from video_director import asset_catalog as ac
        entry = ac.register_generated(fixtures["img2"], prompt="测试生成")
        assert entry["source"] == "generated"
        assert entry["description"] == "测试生成"
        assert entry["commercial_status"] == ac.CLEAR  # 自产素材可商用

    def test_stock_registers_31(self, _iso, fixtures):
        """§31 Stock 登记：provider/creator/许可可追溯。"""
        from video_director import asset_catalog as ac
        entry = ac.register_stock(fixtures["clip"], provider="pexels",
                                 source_id="12345", creator="Alice",
                                 source_page="https://pexels.com/video/12345")
        assert entry["source"] == "pexels"
        assert entry["source_id"] == "12345"
        assert entry["creator"] == "Alice"
        assert "Pexels" in entry["license_note"]
        assert entry["commercial_status"] == ac.CLEAR

    def test_project_reference_idempotent(self, _iso, fixtures):
        """§26 项目只存引用；重复引用幂等。"""
        from video_director import asset_catalog as ac
        entry = ac.import_asset(fixtures["clip"])
        pid = "al-ref-01"
        assert ac.project_reference(pid, entry["asset_id"]) is True
        assert ac.project_reference(pid, entry["asset_id"]) is False  # 幂等
        man = json.loads((config.project_dir(pid) / "assets" / "manifest.json")
                         .read_text(encoding="utf-8"))
        assert any(a["id"] == entry["asset_id"] for a in man)

    def test_referenced_asset_cannot_be_deleted(self, _iso, fixtures):
        """项目引用建立后，单项删除必须拒绝且 Library 条目保持存在。"""
        from video_director import asset_catalog as ac

        entry = ac.import_asset(fixtures["clip"])
        assert ac.project_reference("al-delete-guard", entry["asset_id"]) is True
        result = ac.delete_asset(entry["asset_id"])
        assert result["ok"] is False
        assert result["code"] == "ASSET_IN_USE"
        assert ac.get_asset(entry["asset_id"]) is not None
        assert Path(entry["file"]).is_file()

    def test_batch_delete_preflight_is_all_or_nothing(self, _iso, fixtures):
        """批量删除发现任一被引用素材时，不得移除批次中的其他素材。"""
        from video_director import asset_catalog as ac

        first = ac.import_asset(fixtures["img"])
        second = ac.import_asset(fixtures["img2"])
        ac.project_reference("al-batch-delete-guard", second["asset_id"])
        result = ac.delete_assets([first["asset_id"], second["asset_id"]])
        assert result["ok"] is False
        assert result["code"] == "ASSET_IN_USE"
        assert ac.get_asset(first["asset_id"]) is not None
        assert ac.get_asset(second["asset_id"]) is not None
        assert Path(first["file"]).is_file()
        assert Path(second["file"]).is_file()

    def test_project_manifest_writer_serializes_with_library_deletion(self, _iso, fixtures):
        """项目引用写入必须与全局素材删除共用事务锁，避免检查后新增引用。"""
        import threading
        from video_director import asset_catalog as ac, project_manifest

        entry = ac.import_asset(fixtures["img"])
        project_id = "al-reference-delete-race"
        started = threading.Event()
        finished = threading.Event()
        errors = []

        def add_reference():
            started.set()
            try:
                project_manifest.merge_project_manifest_entries(
                    project_id,
                    [{"id": entry["asset_id"], "type": "image", "file": entry["file"]}],
                )
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        with ac._catalog_lock():
            writer = threading.Thread(target=add_reference, daemon=True)
            writer.start()
            assert started.wait(timeout=5)
            assert not finished.wait(timeout=0.1), "manifest writer bypassed catalog transaction lock"

        assert finished.wait(timeout=5), "manifest writer did not resume after catalog lock release"
        writer.join(timeout=5)
        assert not errors, errors

        result = ac.delete_asset(entry["asset_id"])
        assert result["ok"] is False
        assert result["code"] == "ASSET_IN_USE"
        assert ac.get_asset(entry["asset_id"]) is not None
        assert Path(entry["file"]).is_file()

    def test_delete_fails_closed_when_project_manifest_is_corrupt(self, _iso, fixtures):
        """无法读取项目引用清单时，单项和批量删除都必须拒绝。"""
        from video_director import asset_catalog as ac

        first = ac.import_asset(fixtures["img"])
        second = ac.import_asset(fixtures["img2"])
        project_dir = config.project_dir("al-corrupt-manifest")
        manifest = project_dir / "assets" / "manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('{"broken":', encoding="utf-8")

        single = ac.delete_asset(first["asset_id"])
        assert single["ok"] is False
        assert single["code"] == "ASSET_USAGE_UNKNOWN"
        assert ac.get_asset(first["asset_id"]) is not None
        assert Path(first["file"]).is_file()

        batch = ac.delete_assets([first["asset_id"], second["asset_id"]])
        assert batch["ok"] is False
        assert batch["code"] == "ASSET_USAGE_UNKNOWN"
        assert ac.get_asset(first["asset_id"]) is not None
        assert ac.get_asset(second["asset_id"]) is not None
        assert Path(first["file"]).is_file()
        assert Path(second["file"]).is_file()

    def test_delete_fails_closed_for_symlinked_project_manifest(self, _iso, fixtures):
        """项目引用清单指向外部文件时，不得把无法核验当成未引用。"""
        from video_director import asset_catalog as ac

        entry = ac.import_asset(fixtures["img"])
        project_dir = config.project_dir("al-symlink-manifest")
        manifest = project_dir / "assets" / "manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        outside = project_dir / "outside.json"
        outside.write_text("[]", encoding="utf-8")
        try:
            manifest.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("当前运行环境不支持创建符号链接")

        result = ac.delete_asset(entry["asset_id"])
        assert result["ok"] is False
        assert result["code"] == "ASSET_USAGE_UNKNOWN"
        assert ac.get_asset(entry["asset_id"]) is not None
        assert Path(entry["file"]).is_file()



    def test_delete_fails_closed_for_symlinked_project_directory(self, _iso, fixtures):
        """项目目录本身是符号链接时，不能把外部 manifest 当成不存在。"""
        from video_director import asset_catalog as ac

        entry = ac.import_asset(fixtures["img"])
        outside = config.composer_storage_root().parent / "outside-project"
        (outside / "assets").mkdir(parents=True)
        (outside / "assets" / "manifest.json").write_text("[]", encoding="utf-8")
        project_link = config.composer_storage_root() / "linked-project"
        try:
            project_link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("当前运行环境不支持创建符号链接")

        result = ac.delete_asset(entry["asset_id"])
        assert result["ok"] is False
        assert result["code"] == "ASSET_USAGE_UNKNOWN"
        assert ac.get_asset(entry["asset_id"]) is not None
        assert Path(entry["file"]).is_file()

    def test_delete_fails_closed_for_symlinked_composer_root(self, _iso, fixtures, monkeypatch, tmp_path):
        """素材根目录被替换成符号链接时，不得把引用扫描当成可信结果。"""
        from video_director import asset_catalog as ac

        entry = ac.import_asset(fixtures["img"])
        original_root = config.composer_storage_root()
        external_root = tmp_path / "external-composer"
        original_root.rename(external_root)
        root_link = tmp_path / "composer-link"
        try:
            root_link.symlink_to(external_root, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("当前运行环境不支持创建符号链接")
        monkeypatch.setattr(config, "composer_storage_root", lambda: root_link)

        result = ac.delete_asset(entry["asset_id"])
        assert result["ok"] is False
        assert result["code"] == "ASSET_PATH_INVALID"
        stored_path = Path(entry["file"])
        assert (external_root / stored_path.relative_to(original_root)).is_file()
        catalog_path = external_root / "assets" / "catalog.json"
        assert entry["asset_id"] in json.loads(catalog_path.read_text(encoding="utf-8"))

        # The batch endpoint must translate lock-acquisition failures into the
        # same fail-closed API result rather than leaking an exception.
        batch_result = ac.delete_assets([entry["asset_id"]])
        assert batch_result["ok"] is False
        assert batch_result["code"] == "ASSET_PATH_INVALID"
        assert (external_root / stored_path.relative_to(original_root)).is_file()
        assert entry["asset_id"] in json.loads(catalog_path.read_text(encoding="utf-8"))


    @pytest.mark.parametrize("batch", [False, True])
    def test_delete_rejects_catalog_payload_outside_library(self, _iso, fixtures, tmp_path, batch):
        """被篡改的 catalog 不能授权删除素材库之外的任意文件。"""
        from video_director import asset_catalog as ac

        entry = ac.import_asset(fixtures["img"])
        external = tmp_path / "valuable-user-file.bin"
        external.write_bytes(b"must survive")
        catalog = ac._load_catalog()
        catalog[entry["asset_id"]]["file"] = str(external)
        ac._save_catalog(catalog)

        result = (ac.delete_assets([entry["asset_id"]]) if batch
                  else ac.delete_asset(entry["asset_id"]))
        assert result["ok"] is False
        assert result["code"] == "ASSET_PATH_INVALID"
        assert external.read_bytes() == b"must survive"
        assert ac.get_asset(entry["asset_id"]) is not None
        assert Path(entry["file"]).is_file()

    def test_delete_rejects_payload_symlink_inside_library(self, _iso, fixtures, tmp_path):
        """素材文件本身被替换成指向外部的符号链接时，删除必须拒绝。"""
        from video_director import asset_catalog as ac

        entry = ac.import_asset(fixtures["img"])
        payload = Path(entry["file"])
        external = tmp_path / payload.name
        payload.rename(external)
        try:
            payload.symlink_to(external)
        except (OSError, NotImplementedError):
            pytest.skip("当前运行环境不支持创建符号链接")

        result = ac.delete_asset(entry["asset_id"])
        assert result["ok"] is False
        assert result["code"] == "ASSET_PATH_INVALID"
        assert external.is_file()
        assert ac.get_asset(entry["asset_id"]) is not None

    def test_thumbnail_rejects_path_traversal_asset_id(self, _iso):
        from video_director import asset_catalog as ac

        assert ac.make_thumbnail("../../outside", src_file="unused.jpg") is None


    def test_list_filters(self, _iso, fixtures):
        from video_director import asset_catalog as ac
        ac.import_asset(fixtures["clip"], tags=["咖啡"])
        ac.register_generated(fixtures["img"], prompt="标题卡")
        assert len(ac.list_assets(kind="video")) == 1
        assert len(ac.list_assets(source="generated")) == 1
        assert len(ac.list_assets(q="咖啡")) == 1


class TestRatioFromProject:
    """§十：搜索方向由当前项目画幅决定。"""

    def _mk_project(self, pid, w, h):
        base = config.project_dir(pid)
        config.init_project_dirs(base)
        (base / "plans" / "composition.json").write_text(json.dumps({
            "project": {"title": "t", "width": w, "height": h,
                        "fps": 30, "duration": 4.0},
            "scenes": [{"id": "s1", "start": 0.0, "duration": 4.0, "text": "x",
                        "layers": [{"type": "image", "start": 0.0, "duration": 4.0,
                                    "asset_id": "a.jpg"}]}],
            "audio": [], "watermarks": [],
        }), encoding="utf-8")

    def test_ratio_portrait(self, _iso):
        from video_director import asset_catalog as ac
        self._mk_project("ratio-p-01", 1080, 1920)
        sp = ac.stock_search_params("ratio-p-01")
        assert sp["ratio"] == "9:16" and sp["orientation"] == "portrait"

    def test_ratio_landscape(self, _iso):
        from video_director import asset_catalog as ac
        self._mk_project("ratio-l-01", 1920, 1080)
        sp = ac.stock_search_params("ratio-l-01")
        assert sp["ratio"] == "16:9" and sp["orientation"] == "landscape"

    def test_ratio_square(self, _iso):
        from video_director import asset_catalog as ac
        self._mk_project("ratio-s-01", 1080, 1080)
        sp = ac.stock_search_params("ratio-s-01")
        assert sp["ratio"] == "1:1" and sp["orientation"] == "square"


class TestAssetCatalogCrossProcess:
    """进程级并发回归：文件锁必须覆盖独立 Python 进程，而不只是线程。"""

    def test_concurrent_process_import_and_semantic_updates(
        self, _iso, fixtures, tmp_path, monkeypatch
    ):
        import os
        import sys

        from video_director import asset_catalog as ac

        storage = tmp_path / "composer"
        monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))

        script_import = (
            "import json,sys; "
            "from video_director import asset_catalog as ac; "
            "print(json.dumps(ac.import_asset(sys.argv[1]), ensure_ascii=False))"
        )
        env = os.environ.copy()
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script_import, fixtures["clip"]],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for _ in range(4)
        ]
        imported = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=90)
            assert process.returncode == 0, stderr
            imported.append(json.loads(stdout.strip().splitlines()[-1]))

        assert len({entry["asset_id"] for entry in imported}) == 1
        assert len(ac.list_assets()) == 1

        first = ac.import_asset(fixtures["img"])
        second = ac.import_asset(fixtures["img2"])
        script_update = (
            "import sys; "
            "from video_director import asset_catalog as ac; "
            "ac.update_asset_semantics(sys.argv[1], description=sys.argv[2])"
        )
        updates = [
            subprocess.Popen(
                [sys.executable, "-c", script_update, first["asset_id"], "first-process"],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ),
            subprocess.Popen(
                [sys.executable, "-c", script_update, second["asset_id"], "second-process"],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ),
        ]
        for process in updates:
            _stdout, stderr = process.communicate(timeout=60)
            assert process.returncode == 0, stderr

        stored = {entry["asset_id"]: entry for entry in ac.list_assets()}
        assert stored[first["asset_id"]]["description"] == "first-process"
        assert stored[second["asset_id"]]["description"] == "second-process"



def test_load_catalog_rejects_corrupt_json_without_replacing_it(monkeypatch, tmp_path):
    from video_director import asset_catalog as ac

    catalog = tmp_path / "catalog.json"
    original = b'{"preserve": '
    catalog.write_bytes(original)
    monkeypatch.setattr(ac, "_catalog_path", lambda: catalog)

    with pytest.raises(ac.AssetLibraryError) as exc:
        ac._load_catalog()

    assert exc.value.code == "CATALOG_CORRUPT"
    assert catalog.read_bytes() == original


def test_load_catalog_rejects_non_object_schema(monkeypatch, tmp_path):
    from video_director import asset_catalog as ac

    catalog = tmp_path / "catalog.json"
    catalog.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(ac, "_catalog_path", lambda: catalog)

    with pytest.raises(ac.AssetLibraryError) as exc:
        ac._load_catalog()

    assert exc.value.code == "CATALOG_CORRUPT"
    assert catalog.read_text(encoding="utf-8") == "[]"


def test_load_catalog_rejects_symlink_without_reading_target(monkeypatch, tmp_path):
    from video_director import asset_catalog as ac

    target = tmp_path / "external-catalog.json"
    target.write_text('{"external": true}', encoding="utf-8")
    catalog = tmp_path / "catalog.json"
    try:
        catalog.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("当前运行环境不支持创建符号链接")
    monkeypatch.setattr(ac, "_catalog_path", lambda: catalog)

    with pytest.raises(ac.AssetLibraryError) as exc:
        ac._load_catalog()

    assert exc.value.code == "CATALOG_UNSAFE"
    assert target.read_text(encoding="utf-8") == '{"external": true}'

def test_library_root_rejects_symlink_without_creating_catalog_outside(monkeypatch, tmp_path):
    from video_director import asset_catalog as ac

    storage = tmp_path / "composer"
    storage.mkdir()
    outside = tmp_path / "outside-library"
    outside.mkdir()
    (storage / "assets").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(ac.config, "composer_storage_root", lambda: storage)

    with pytest.raises(ac.AssetLibraryError) as exc:
        ac._catalog_path()
    assert exc.value.code == "ASSET_PATH_INVALID"
    assert not (outside / "catalog.json").exists()


def test_library_subdirectories_reject_symlinks_before_following_them(monkeypatch, tmp_path):
    from video_director import asset_catalog as ac

    storage = tmp_path / "composer"
    root = storage / "assets"
    root.mkdir(parents=True)
    outside = tmp_path / "outside-local"
    outside.mkdir()
    (root / "local").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(ac.config, "composer_storage_root", lambda: storage)

    with pytest.raises(ac.AssetLibraryError) as exc:
        ac._ensure_dirs()
    assert exc.value.code == "ASSET_PATH_INVALID"
    assert list(outside.iterdir()) == []


def _catalog_lock_process_worker(storage_root, acquired, release=None, attempting=None):
    """Spawn-safe worker for the native per-platform Asset Library lock test."""
    import os

    os.environ["VIDEO_DIRECTOR_STORAGE_ROOT"] = str(storage_root)
    from video_director import asset_catalog as ac

    if attempting is not None:
        attempting.set()
    with ac._catalog_lock():
        acquired.set()
        if release is not None and not release.wait(timeout=15):
            raise RuntimeError("timed out waiting to release Asset Library lock")


def test_catalog_file_lock_serializes_independent_processes(tmp_path):
    """Exercise the actual msvcrt branch on Windows, not only MoveFileExW."""
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    storage = tmp_path / "composer"
    first_acquired = context.Event()
    release_first = context.Event()
    second_attempting = context.Event()
    second_acquired = context.Event()

    first = context.Process(
        target=_catalog_lock_process_worker,
        args=(storage, first_acquired, release_first, None),
    )
    second = context.Process(
        target=_catalog_lock_process_worker,
        args=(storage, second_acquired, None, second_attempting),
    )

    first.start()
    try:
        assert first_acquired.wait(timeout=15), "first process did not acquire catalog lock"
        second.start()
        assert second_attempting.wait(timeout=15), "second process did not attempt catalog lock"
        assert not second_acquired.wait(timeout=0.5), (
            "second process entered the catalog transaction while first process held the lock"
        )
        release_first.set()
        assert second_acquired.wait(timeout=15), "second process did not acquire released catalog lock"
    finally:
        release_first.set()
        if first.is_alive():
            first.join(timeout=15)
        if second.pid is not None and second.is_alive():
            second.join(timeout=15)

    assert first.exitcode == 0
    assert second.exitcode == 0
