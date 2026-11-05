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
