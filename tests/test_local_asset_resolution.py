"""本地/上传素材解析契约：Library 未收录的项目 manifest 素材必须可用。

背景（Playwright 实测 2026-10-15）：项目里 `local-*` 上传素材只登记在
项目 assets/manifest.json，不进全局 Library（asset_catalog）。而
`_overlay_source_duration` 只查 Library → 「加画面叠加」「移到播放头轨道」
「展开为自由时间线」「拆分原声轨」对上传素材整体 ASSET_NOT_FOUND。
"""
from __future__ import annotations

import json
from pathlib import Path

from video_director import config
from video_director.editor import _asset_ref_from_project_manifest, execute_command, load_composition, save_composition
from video_director.models import CameraTrack, CompositionIR

ROOT = Path(__file__).resolve().parents[1]


def _project_with_local_asset(tmp_path: Path, monkeypatch):
    """建一个只把素材写进项目 manifest（不进 Library）的项目。"""
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "local-asset-fallback"
    base = config.project_dir(project_id)
    (base / "plans").mkdir(parents=True, exist_ok=True)
    (base / "assets").mkdir(parents=True, exist_ok=True)

    # 真实文件（内容无关紧要，编辑器只校验存在性）
    media = base / "assets" / "local-testclip.mp4"
    media.write_bytes(b"\x00" * 2048)

    (base / "assets" / "manifest.json").write_text(json.dumps([{
        "id": "local-testclip", "type": "video", "file": str(media),
        "duration": 4.0, "width": 1080, "height": 1920, "source": "uploaded",
    }], ensure_ascii=False), encoding="utf-8")

    ir = CompositionIR.model_validate({
        "project": {"duration": 10.0, "fps": 30, "width": 1080, "height": 1920},
        "scenes": [{
            "id": "S01", "start": 0.0, "duration": 10.0, "text": "local asset",
            "layers": [{"type": "image", "asset_id": "img-placeholder",
                        "start": 0.0, "duration": 10.0}],
        }],
        "assets": [{"id": "img-placeholder", "type": "image",
                    "file": str(media), "width": 1080, "height": 1920}],
        "visual_tracks": [{"id": "overlay-main", "name": "Overlay", "order": 0}],
        "audio_tracks": [{"id": "audio-sfx", "name": "SFX"}],
        "audio": [],
    })
    save_composition(project_id, ir)
    (base / "plans" / "revisions.jsonl").write_text("", encoding="utf-8")
    return project_id, media


def test_manifest_asset_resolves_without_library_entry(tmp_path, monkeypatch):
    project_id, media = _project_with_local_asset(tmp_path, monkeypatch)
    ref = _asset_ref_from_project_manifest(project_id, "local-testclip")
    assert ref is not None, "项目 manifest 里的上传素材解析失败"
    assert ref.id == "local-testclip"
    assert str(ref.type).lower() == "video"
    assert Path(ref.file).is_file()


def test_manifest_asset_missing_returns_none(tmp_path, monkeypatch):
    project_id, _media = _project_with_local_asset(tmp_path, monkeypatch)
    assert _asset_ref_from_project_manifest(project_id, "does-not-exist") is None
    assert _asset_ref_from_project_manifest(None, "local-testclip") is None


def test_add_visual_overlay_accepts_project_local_uploaded_asset(tmp_path, monkeypatch):
    """回归：上传素材必须能加为画面叠加（此前 ASSET_NOT_FOUND）。

    用图片素材保持测试自足（视频分支要走 ffprobe，真实性由 8502 面
    Playwright 实测覆盖：上传 mp4 → add_visual_overlay APPLIED）。
    """
    project_id, _media = _project_with_local_asset(tmp_path, monkeypatch)
    # 把 manifest 素材改成图片，避免测试依赖 ffprobe
    base = config.project_dir(project_id)
    png = base / "assets" / "local-testimg.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 512)
    (base / "assets" / "manifest.json").write_text(json.dumps([{
        "id": "local-testimg", "type": "image", "file": str(png),
        "width": 1080, "height": 1920, "source": "uploaded",
    }], ensure_ascii=False), encoding="utf-8")

    result = execute_command(project_id, {
        "command": "add_visual_overlay", "targetId": "overlays",
        "payload": {"assetId": "local-testimg", "start": 2.0, "duration": 2.0,
                    "x": 0.55, "y": 0.1, "width": 0.35, "height": 0.3},
        "expectedVersion": 1,
    })
    assert result.status == "APPLIED", result.errors
    comp = load_composition(project_id)
    assert len(comp.visual_overlays) == 1
    assert comp.visual_overlays[0].asset_id == "local-testimg"
    # 解析后的素材被提升进 IR，后续命令不必再次回落
    assert any(a.id == "local-testimg" for a in comp.assets)


def test_thumbnail_accepts_explicit_source_without_library_entry(tmp_path, monkeypatch):
    """回归：Library 未收录的素材，显式传入源文件也必须能出缩略图。

    此前 make_thumbnail 只认 Library 条目 → 上传素材缩略图恒 404
    （服务端日志实测：GET /api/asset_thumbnail?asset_id=local-... 404）。
    """
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    from video_director import asset_catalog

    aid = "local-uploaded-no-library"
    assert asset_catalog.get_asset(aid) is None, "前置：该素材不在 Library"

    src = tmp_path / "src.mp4"
    src.write_bytes(b"\x00" * 64)

    # Library 未命中且未给 src_file → None（不假装成功）
    assert asset_catalog.make_thumbnail(aid) is None

    # 预置缓存缩略图，验证显式 src_file 能让解析路径走通并命中缓存
    thumb_dir = asset_catalog.library_root() / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    cached = thumb_dir / f"{aid}.jpg"
    cached.write_bytes(b"\xff\xd8\xff\xe0cached-thumb")
    got = asset_catalog.make_thumbnail(aid, src_file=str(src), media_type="video")
    assert got == str(cached), f"显式源文件未命中缩略图缓存: {got!r}"


def test_thumbnail_endpoint_accepts_project_id():
    """源码契约：缩略图端点必须接受 project_id 并回落到项目 manifest。"""
    src = (ROOT / "webui" / "server.py").read_text(encoding="utf-8")
    idx = src.index('route == "/api/asset_thumbnail"')
    branch = src[idx:idx + 2200]
    assert 'qs.get("project_id"' in branch, "缩略图端点未接受 project_id"
    assert "_asset_ref_from_project_manifest" in branch, (
        "缩略图端点未回落到项目 manifest——上传素材缩略图会恒 404"
    )
