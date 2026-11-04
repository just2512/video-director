"""Migration diagnostics must expose unsupported CompositionIR fields."""
from __future__ import annotations

import json
from pathlib import Path

from video_director import config, film_document as fd
from video_director.models import CompositionIR


def test_migration_maps_audio_fades_and_lane_mute_and_warns_for_unmapped_fields(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "migration-loss-diagnostics"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-needed-by-migration")
    (assets / "music.wav").write_bytes(b"not-needed-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "asset-image", "file": "assets/still.png"},
        {"id": "asset-music", "file": "assets/music.wav"},
    ]), encoding="utf-8")

    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 24, "duration": 1},
        "style_id": "old-style-id",
        "scenes": [{
            "id": "scene-opening", "start": 0, "duration": 1, "text": "开场",
            "intent": "先给出结论",
            "layers": [{
                "id": "legacy-layer-opening-01",
                "track_id": "legacy-visual-track",
                "type": "image", "asset_id": "asset-image", "start": 0, "duration": 1,
                "motion": "slow_zoom_in", "locked": True, "fit_mode": "contain",
                "animation": "legacy-fade", "text": "不应自动生成字幕",
            }],
        }],
        "audio_tracks": [{
            "id": "audio-bgm", "name": "自定义 BGM", "order": 0,
            "muted": True, "locked": True, "hidden": True,
        }],
        "audio": [{
            "id": "music-bed", "track_id": "audio-bgm", "type": "bgm",
            "asset_id": "asset-music", "start": 0, "duration": 1, "volume": 0.5,
            "fadeIn": 0.2, "fadeOut": 0.3, "ducking": True,
        }],
        "watermarks": [{"id": "brand-mark", "kind": "text", "text": "测试水印"}],
        "subtitle_style": {
            "font": "Brand Subtitle Font", "size": 1.4, "position": "top", "animation": "fade",
            "stroke_color": "#333333", "background": "#101010@0.5",
            "alignment": "right", "max_width": 0.7,
        },
        "variants": [{"id": "alternative"}],
        "active_variant_id": "alternative",
        "origin_template_id": "template-old",
        "camera_tracks": [{"id": "camera-main"}],
    })

    result = fd.build_film_from_composition(project_id, ir)
    snapshot_text = result["pages"]["migration/source-composition.json"]
    archive_sha256 = __import__("hashlib").sha256(snapshot_text.encode("utf-8")).hexdigest()
    assert result["source_archive"] == {
        "path": "migration/source-composition.json",
        "sha256": archive_sha256,
        "status": "pending_initialization",
    }
    snapshot = json.loads(snapshot_text)
    assert snapshot["active_variant_id"] == "alternative"
    assert snapshot["variants"][0]["id"] == "alternative"
    assert snapshot["watermarks"][0]["text"] == "测试水印"
    assert snapshot["scenes"][0]["layers"][0]["animation"] == "legacy-fade"
    assert snapshot["scenes"][0]["layers"][0]["text"] == "不应自动生成字幕"
    assert snapshot["subtitle_style"]["font"] == "Brand Subtitle Font"
    warnings = "\n".join(result["warnings"])
    assert "24 FPS" not in warnings
    assert result["document"]["fps"] == 24
    migrated_html = fd.serialize_film(result["document"])
    assert '<meta name="film-fps" content="24">' in migrated_html
    assert fd.parse_film(migrated_html)["fps"] == 24
    assert "style_id" in warnings
    assert "variants" in warnings
    assert "camera_tracks" in warnings
    assert "水印" in warnings
    assert "ducking" in warnings
    assert "animation" in warnings
    assert "字幕 font-family='Brand Subtitle Font' 已映射到字幕 HTML" in warnings
    assert "字幕样式字段 font" not in warnings
    assert "模板来源 lineage" in warnings
    assert "字幕样式字段 size" not in warnings
    assert "字幕样式字段 stroke" not in warnings
    subtitle_html = fd._subtitle_page(320, 180, 1.0, [{"start": 0, "end": 1, "text": "字幕"}], {
        "font": "Brand Subtitle Font", "size": 1.4, "position": "top", "animation": "fade",
        "stroke_color": "#333333", "background": "#101010@0.5", "alignment": "right", "max_width": 0.7,
    })
    assert 'font-family:"Brand Subtitle Font","WenQuanYi Micro Hei",sans-serif' in subtitle_html
    assert "font-size:75px" in subtitle_html
    assert "top:18px" in subtitle_html and "text-align:right" in subtitle_html
    assert "-webkit-text-stroke:2px #333333" in subtitle_html
    assert "rgba(16,16,16,0.5)" in subtitle_html and "width:70%" in subtitle_html
    assert 'const animation="fade"' in subtitle_html

    visual_track = next(track for track in result["document"]["tracks"] if track["id"] == "track-main")
    image_clip = next(clip for clip in visual_track["clips"] if clip["id"] == "main-scene-opening-1")
    assert "locked" not in visual_track["attrs"]
    assert "data-clip-locked" in image_clip["attrs"]
    assert image_clip["attrs"]["data-source-id"] == "legacy-layer-opening-01"
    assert image_clip["attrs"]["data-source-scene-id"] == "scene-opening"
    assert image_clip["attrs"]["data-source-track-id"] == "legacy-visual-track"
    transform = json.loads(image_clip["attrs"]["data-film-transform"])
    channels = json.loads(image_clip["attrs"]["data-film-keyframes"])
    assert transform["anchor"] == "center"
    assert transform["base_width"] == 320 and transform["base_height"] == 180
    assert any(channel["property"] == "scale" for channel in channels)
    assert "object-fit:contain" in image_clip["attrs"]["style"]
    assert "animation" not in image_clip["attrs"]
    assert "text" not in image_clip["attrs"]

    audio_track = next(track for track in result["document"]["tracks"]
                       if track["id"] == "track-audio-music-bed")
    audio_clip = audio_track["clips"][0]
    assert audio_track["attrs"]["data-label"] == "自定义 BGM"
    assert audio_clip["attrs"]["muted"] == ""
    assert audio_clip["attrs"]["fade"] == "0.2,0.3"
    assert audio_clip["attrs"]["data-source-id"] == "music-bed"
    assert audio_clip["attrs"]["data-source-track-id"] == "audio-bgm"

    # Initialization persists the archive and a digest that can be rechecked later.
    from video_director import editor as vd_editor
    monkeypatch.setattr(vd_editor, "composition_version", lambda _pid: 1)
    initialized = fd.initialize_project_film(
        project_id, result["document"], result["pages"], 1, result["warnings"]
    )
    assert initialized["migration"]["source_snapshot_path"] == "migration/source-composition.json"
    archive_file = config.project_dir(project_id) / "migration" / "source-composition.json"
    manifest_file = config.project_dir(project_id) / fd.MIGRATION_NAME
    persisted_snapshot = archive_file.read_text(encoding="utf-8")
    persisted_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert persisted_snapshot == snapshot_text
    assert persisted_manifest["source_snapshot_sha256"] == __import__("hashlib").sha256(
        persisted_snapshot.encode("utf-8")
    ).hexdigest()

def test_unknown_nested_composition_fields_are_archived_but_never_rehydrated(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "migration-unknown-field-archive"
    root = config.project_dir(project_id)
    plans = root / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "unknown-field-still", "file": "assets/still.png", "type": "image"},
    ]), encoding="utf-8")

    raw = {
        "project": {"width": 320, "height": 180, "fps": 24, "duration": 1},
        "scenes": [{
            "id": "scene-unknown-field", "start": 0, "duration": 1, "text": "archive only",
            "layers": [{
                "id": "layer-unknown-field", "type": "image", "asset_id": "unknown-field-still",
                "start": 0, "duration": 1,
                "future_blend_mode": {"mode": "spectral", "strength": 0.75},
            }],
        }],
        "future_project_contract": {
            "schema": "composition-next",
            "must_not_execute": ["blend", "camera"],
        },
    }
    ir = CompositionIR.model_validate(raw)
    # Simulate a persisted document written by a newer/foreign producer. The
    # current Pydantic model intentionally ignores these unknown extension keys.
    (plans / "composition.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    result = fd.build_film_from_composition(project_id, ir)
    archived = json.loads(result["pages"]["migration/source-composition.json"])
    assert archived["future_project_contract"] == raw["future_project_contract"]
    assert archived["scenes"][0]["layers"][0]["future_blend_mode"] == {
        "mode": "spectral", "strength": 0.75,
    }
    warning_text = "\\n".join(result["warnings"])
    assert "CompositionIR.future_project_contract" in warning_text
    assert "CompositionIR.scenes[0].layers[0].future_blend_mode" in warning_text
    assert "完整原始值已归档" in warning_text

    film_html = fd.serialize_film(result["document"])
    assert "future_project_contract" not in film_html
    assert "future_blend_mode" not in film_html
    assert "spectral" not in film_html


def test_unknown_fields_are_detected_through_nested_model_lists_and_transform_objects(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "migration-deep-unknown-fields"
    root = config.project_dir(project_id)
    plans = root / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-decoded-by-migration")
    # The manifest deliberately omits this asset: AssetRef.id/file must be
    # sufficient for the migration resolver to locate the referenced file.
    (assets / "audio.wav").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text("[]", encoding="utf-8")

    raw = {
        "project": {"width": 320, "height": 180, "fps": 24, "duration": 1},
        "assets": [{
            "id": "nested-asset", "type": "image", "file": "assets/still.png",
            "duration": 3.5, "width": 640, "height": 360, "fps": 30,
            "prompt": "a quiet studio", "description": "source still",
            "keywords": ["studio", "product"], "source": "generated",
            "license_note": "internal license", "provenance": {"provider": "test-provider"},
            "rights_status": "CLEAR",
            "future_rights_contract": {"status": "review", "evidence": ["provider-note"]},
        }, {
            "id": "nested-audio", "type": "audio", "file": "assets/audio.wav",
        }],
        "scenes": [{
            "id": "scene-deep-unknown", "start": 0, "duration": 1, "text": "nested archive",
            "layers": [{
                "id": "layer-deep-unknown", "type": "image", "asset_id": "nested-asset",
                "start": 0, "duration": 1,
                "transform": {
                    "x": 0.5, "y": 0.5,
                    "future_curve": {"kind": "bezier", "control_points": [[0.1, 0.2], [0.8, 0.9]]},
                },
            }],
        }],
        "audio": [{
            "type": "bgm", "asset_id": "nested-audio", "duration": 1,
            "future_audio_layout": {"mode": "immersive", "channels": 8},
        }],
        "camera_tracks": [{
            "id": "camera-deep-unknown",
            "keyframes": [{
                "at": 0, "position": [0, 0, 0], "target": [0, 0, 0],
                "future_easing_curve": {"kind": "spring", "damping": 0.4},
            }],
        }],
        "variants": [{
            "id": "variant-deep-unknown",
            "future_variant_contract": {"layout": "multi-canvas", "revision": 3},
        }],
        "watermarks": [{
            "id": "watermark-deep-unknown", "kind": "text", "text": "safe watermark",
            "future_logo_effect": {"shader": "must-not-execute"},
        }],
    }
    ir = CompositionIR.model_validate(raw)
    (plans / "composition.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    result = fd.build_film_from_composition(project_id, ir)
    archived = json.loads(result["pages"]["migration/source-composition.json"])
    expected = {
        "CompositionIR.assets[0].future_rights_contract",
        "CompositionIR.scenes[0].layers[0].transform.future_curve",
        "CompositionIR.audio[0].future_audio_layout",
        "CompositionIR.camera_tracks[0].keyframes[0].future_easing_curve",
        "CompositionIR.variants[0].future_variant_contract",
        "CompositionIR.watermarks[0].future_logo_effect",
    }
    warning_text = "\\n".join(result["warnings"])
    for path in expected:
        assert path in warning_text, path
    assert archived["assets"][0]["future_rights_contract"] == raw["assets"][0]["future_rights_contract"]
    main_track = next(track for track in result["document"]["tracks"] if track["id"] == "track-main")
    image_clip = next(clip for clip in main_track["clips"] if clip["attrs"].get("data-asset-id") == "nested-asset")
    assert image_clip["attrs"]["src"].startswith("assets/still.png#t=")
    source_asset_metadata = json.loads(image_clip["attrs"]["data-source-asset-metadata"])
    assert source_asset_metadata == {
        "type": "image", "duration": 3.5, "width": 640, "height": 360, "fps": 30,
        "prompt": "a quiet studio", "description": "source still",
        "keywords": ["studio", "product"], "source": "generated",
        "license_note": "internal license", "provenance": {"provider": "test-provider"},
        "rights_status": "CLEAR",
    }
    assert archived["scenes"][0]["layers"][0]["transform"]["future_curve"] == raw["scenes"][0]["layers"][0]["transform"]["future_curve"]
    assert archived["audio"][0]["future_audio_layout"] == raw["audio"][0]["future_audio_layout"]
    assert archived["camera_tracks"][0]["keyframes"][0]["future_easing_curve"] == raw["camera_tracks"][0]["keyframes"][0]["future_easing_curve"]
    assert archived["variants"][0]["future_variant_contract"] == raw["variants"][0]["future_variant_contract"]
    assert archived["watermarks"][0]["future_logo_effect"] == raw["watermarks"][0]["future_logo_effect"]

    film_html = fd.serialize_film(result["document"])
    for marker in ("future_rights_contract", "future_curve", "future_audio_layout",
                   "future_easing_curve", "future_variant_contract", "future_logo_effect",
                   "must-not-execute", "multi-canvas", "immersive"):
        assert marker not in film_html

def test_migrated_film_document_lifecycle_preserves_authority_and_source_archive(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "migration-lifecycle-authority"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "still.png").write_bytes(b"not-needed-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "lifecycle-image", "file": "assets/still.png"},
    ]), encoding="utf-8")

    ir = CompositionIR.model_validate({
        "project": {"width": 320, "height": 180, "fps": 24, "duration": 1},
        "scenes": [{
            "id": "scene-opening", "start": 0, "duration": 1, "text": "生命周期",
            "layers": [{
                "id": "lifecycle-layer", "type": "image", "asset_id": "lifecycle-image",
                "start": 0, "duration": 1,
            }],
        }],
    })
    result = fd.build_film_from_composition(project_id, ir)
    snapshot_text = result["pages"]["migration/source-composition.json"]
    from video_director import editor as vd_editor
    monkeypatch.setattr(vd_editor, "composition_version", lambda _pid: 1)
    initialized = fd.initialize_project_film(
        project_id, result["document"], result["pages"], 1, result["warnings"]
    )
    archive_file = root / "migration" / "source-composition.json"
    manifest_file = root / fd.MIGRATION_NAME
    persisted_snapshot = archive_file.read_text(encoding="utf-8")
    persisted_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert persisted_snapshot == snapshot_text
    archive_sha256 = __import__("hashlib").sha256(persisted_snapshot.encode("utf-8")).hexdigest()
    assert persisted_manifest["source_snapshot_sha256"] == archive_sha256

    # Exercise the first authoritative lifecycle transition: the migrated
    # FilmDocument becomes the project context, accepts a revision-pinned edit,
    # and remains authoritative when reopened. The source archive is immutable.
    from video_director.project_context import inspect_project_context

    initial = fd.read_project_film(project_id)
    assert initial["exists"] is True
    assert initial["rev"] == initialized["rev"]
    context = inspect_project_context(project_id, {"type": "clip", "id": "main-scene-opening-1"})
    assert context["authority"] == "film.html"
    assert context["revision"] == initial["rev"]
    assert context["selection"]["ids"] == ["main-scene-opening-1"]

    edited = fd.edit_project_film(project_id, initial["rev"], [{
        "op": "set", "clip": "main-scene-opening-1", "field": "at", "value": 0.25,
    }], require_base=True, source="migration-lifecycle-test")
    assert edited["base"] == initial["rev"]
    assert edited["rev"] != initial["rev"]
    reopened = fd.read_project_film(project_id)
    assert reopened["rev"] == edited["rev"]
    assert reopened["doc"]["tracks"][0]["clips"][0]["attrs"]["at"] == "0.25"
    reopened_context = inspect_project_context(
        project_id, {"type": "clip", "id": "main-scene-opening-1"}
    )
    assert reopened_context["authority"] == "film.html"
    assert reopened_context["revision"] == edited["rev"]
    assert reopened_context["selection"]["ids"] == ["main-scene-opening-1"]

    # A migrated project must not let the legacy CompositionIR writer mutate
    # the stale pre-migration document after FilmDocument takes authority.
    with __import__("pytest").raises(fd.FilmError) as legacy_write:
        fd.assert_legacy_composition_access(project_id, "迁移后旧 CompositionIR 写入")
    assert legacy_write.value.code == "FILM_DOCUMENT_AUTHORITATIVE"

    # User edits do not rewrite or invalidate the immutable source snapshot.
    assert archive_file.read_text(encoding="utf-8") == persisted_snapshot
    assert persisted_manifest["source_snapshot_sha256"] == __import__("hashlib").sha256(
        archive_file.read_bytes()
    ).hexdigest()


def test_reference_graph_is_archived_without_implicitly_inserting_reference_assets(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project_id = "reference-graph-archive-only"
    root = config.project_dir(project_id)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "base.png").write_bytes(b"not-decoded-by-migration")
    (assets / "manifest.json").write_text(json.dumps([
        {"id": "base-image", "file": "assets/base.png", "type": "image"},
    ]), encoding="utf-8")
    raw_references = {
        "references": [
            {
                "id": "reference-first-frame", "ref_type": "first_frame",
                "target_asset_id": "reference-only-image", "role": "opening",
                "note": "仅供生成参考，不应自动插入时间线",
            },
            {
                "id": "reference-artifact", "ref_type": "visual_reference",
                "target_artifact_id": "artifact-reference-42", "role": "visual-style",
                "note": "仅保存来源关系",
            },
        ],
    }
    ir = CompositionIR.model_validate({
        "project": {"width": 640, "height": 360, "fps": 25, "duration": 2},
        "scenes": [{
            "id": "reference-scene", "start": 0, "duration": 1, "text": "reference",
            "layers": [{
                "id": "base-layer", "type": "image", "asset_id": "base-image",
                "start": 0, "duration": 1,
            }],
        }],
        "references": raw_references,
    })
    result = fd.build_film_from_composition(project_id, ir)
    main = next(track for track in result["document"]["tracks"] if track["id"] == "track-main")
    assert json.loads(main["attrs"]["data-source-reference-graph"]) == json.loads(ir.model_dump_json())["references"]
    clip_asset_ids = {
        clip["attrs"].get("data-asset-id")
        for track in result["document"]["tracks"]
        for clip in track.get("clips", [])
    }
    assert "reference-only-image" not in clip_asset_ids
    assert any("引用图不会自动生成或插入" in warning for warning in result["warnings"])
    assert result["document"]["fps"] == 25
    assert result["document"]["stage"]["width"] == 640
    assert result["document"]["stage"]["height"] == 360
