"""Contracts for the OpenFilm-inspired film document editor."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

from video_director import film_document as fd


SAMPLE = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"><style>body{margin:0}</style></head>
<body>
<section id="video-main" data-label="主画面">
<video id="clip-a" src="../assets/a.mp4#t=2,12" at="0" speed="2" volume="1"></video>
<video id="clip-b" src="../assets/b.mp4#t=0,5" at="5"></video>
</section>
<section id="audio-bed" data-label="BGM">
<audio id="audio-1" src="../assets/music.wav#t=0,20" at="0" volume="0.2"></audio>
</section>
</body></html>'''


def test_parse_serialize_preserves_stage_head_and_clip_ids():
    doc = fd.parse_film(SAMPLE)
    assert doc["stage"] == {"width": 1920, "height": 1080}
    assert [c["id"] for c in doc["tracks"][0]["clips"]] == ["clip-a", "clip-b"]
    assert fd.clip_duration(doc["tracks"][0]["clips"][0]) == 5
    serialized = fd.serialize_film(doc)
    again = fd.parse_film(serialized)
    assert again["tracks"][0]["clips"][0]["attrs"]["src"] == "../assets/a.mp4#t=2,12"
    assert "<style>body{margin:0}</style>" in serialized


def test_set_move_split_use_source_time_and_speed():
    doc = fd.parse_film(SAMPLE)
    moved, _ = fd.apply_ops(doc, [{"op":"move","clip":"clip-b","track_id":"video-main","at":7}])
    edited, ops = fd.apply_ops(moved, [
        {"op":"set","clip":"clip-a","field":"speed","value":1.5},
        {"op":"split","clip":"clip-a","at":2,"new_id":"clip-a-right"},
    ])
    first = next(c for c in edited["tracks"][0]["clips"] if c["id"]=="clip-a")
    right = next(c for c in edited["tracks"][0]["clips"] if c["id"]=="clip-a-right")
    assert first["attrs"]["src"].endswith("#t=2,5")
    assert right["attrs"]["src"].endswith("#t=5,12")
    assert right["attrs"]["at"] == "2"
    assert [item["op"] for item in ops] == ["set","split"]


def test_new_track_and_insert_are_one_stable_id_operation_batch():
    doc = fd.parse_film(SAMPLE)
    edited, ops = fd.apply_ops(doc, [
        {"op":"track_add","track_id":"track-overlay","label":"叠加画面","index":0},
        {"op":"insert","track_id":"track-overlay","clip":{
            "id":"new-image","tag":"img","attrs":{
                "src":"../assets/still.png#t=0,4","at":"1","title":"插入图片"
            }}},
    ])
    assert edited["tracks"][0]["id"] == "track-overlay"
    assert edited["tracks"][0]["clips"][0]["id"] == "new-image"
    assert [item["op"] for item in ops] == ["track_add","insert"]


def test_unknown_id_and_non_whitelisted_field_fail_closed():
    doc=fd.parse_film(SAMPLE)
    with pytest.raises(fd.FilmError,match="不存在或已被删除"):
        fd.apply_ops(doc,[{"op":"set","clip":"deleted","field":"at","value":1}])
    with pytest.raises(fd.FilmError,match="不允许修改字段"):
        fd.apply_ops(doc,[{"op":"set","clip":"clip-a","field":"innerHTML","value":"unsafe"}])


def test_locked_track_rejects_edits():
    doc=fd.parse_film(SAMPLE);doc["tracks"][0]["attrs"]["locked"]=""
    with pytest.raises(fd.FilmError,match="已锁定"):
        fd.apply_ops(doc,[{"op":"set","clip":"clip-a","field":"volume","value":0.5}])


def test_overlap_failure_does_not_mutate_original_document():
    doc=fd.parse_film(SAMPLE);before=fd.serialize_film(doc)
    with pytest.raises(fd.FilmError,match="重叠"):
        fd.apply_ops(doc,[{"op":"set","clip":"clip-b","field":"at","value":4}])
    assert fd.serialize_film(doc)==before


def test_atomic_edits_revision_rebase_and_undo_redo(tmp_path,monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT",str(tmp_path/"composer"))
    root=tmp_path/"composer"/"film-test";root.mkdir(parents=True)
    (root/fd.FILM_NAME).write_text(SAMPLE,encoding="utf-8")
    original=fd.read_project_film("film-test")
    first=fd.edit_project_film("film-test",original["rev"],[{"op":"set","clip":"clip-b","field":"at","value":7}])
    assert first["rebased"] is False
    rebased=fd.edit_project_film(
        "film-test", original["rev"],
        [{"op":"set","clip":"clip-b","field":"volume","value":0.4}],
        base_snapshot=original["doc"],
    )
    assert rebased["rebased"] is True
    latest=fd.read_project_film("film-test")
    clip=next(c for t in latest["doc"]["tracks"] for c in t["clips"] if c["id"]=="clip-b")
    assert clip["attrs"]["at"] == "7"
    assert clip["attrs"]["volume"] == "0.4"
    undone=fd.undo_redo_project_film("film-test",rebased["rev"],"undo")
    assert undone["rev"]==first["rev"]
    redone=fd.undo_redo_project_film("film-test",undone["rev"],"redo")
    assert redone["rev"]==rebased["rev"]


def test_edit_requires_explicit_base_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-test"
    root.mkdir(parents=True)
    (root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    with pytest.raises(fd.FilmError) as raised:
        fd.edit_project_film(
            "film-test", None,
            [{"op": "set", "clip": "clip-b", "field": "volume", "value": 0.4}],
        )
    assert raised.value.code == "BASE_REVISION_REQUIRED"
    assert raised.value.status == 409
    assert fd.read_project_film("film-test")["rev"]


def test_stale_edit_without_base_snapshot_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-test"
    root.mkdir(parents=True)
    (root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    original = fd.read_project_film("film-test")
    fd.edit_project_film(
        "film-test", original["rev"],
        [{"op": "set", "clip": "clip-b", "field": "at", "value": 7}],
    )
    with pytest.raises(fd.FilmError) as raised:
        fd.edit_project_film(
            "film-test", original["rev"],
            [{"op": "set", "clip": "clip-b", "field": "volume", "value": 0.4}],
        )
    assert raised.value.code == "BASE_SNAPSHOT_REQUIRED"
    assert raised.value.status == 409


def test_stale_same_field_edit_is_rejected_without_overwriting_latest(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-test"
    root.mkdir(parents=True)
    (root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    original = fd.read_project_film("film-test")
    first = fd.edit_project_film(
        "film-test", original["rev"],
        [{"op": "set", "clip": "clip-b", "field": "at", "value": 7}],
    )
    with pytest.raises(fd.FilmError) as raised:
        fd.edit_project_film(
            "film-test", original["rev"],
            [{"op": "set", "clip": "clip-b", "field": "at", "value": 8}],
            base_snapshot=original["doc"],
        )
    assert raised.value.code == "REVISION_CONFLICT"
    assert raised.value.status == 409
    latest = fd.read_project_film("film-test")
    clip = next(c for t in latest["doc"]["tracks"] for c in t["clips"] if c["id"] == "clip-b")
    assert clip["attrs"]["at"] == "7"
    assert latest["rev"] == first["rev"]


def test_external_file_edit_allows_disjoint_field_rebase_but_rejects_same_field(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-test"
    root.mkdir(parents=True)
    (root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    original = fd.read_project_film("film-test")
    externally_edited, _ = fd.apply_ops(
        original["doc"], [{"op": "set", "clip": "clip-a", "field": "speed", "value": 4}]
    )
    (root / fd.FILM_NAME).write_text(fd.serialize_film(externally_edited), encoding="utf-8")
    independent = fd.edit_project_film(
        "film-test", original["rev"],
        [{"op": "set", "clip": "clip-a", "field": "volume", "value": 0.3}],
        base_snapshot=original["doc"],
    )
    assert independent["rebased"] is True
    latest = fd.read_project_film("film-test")
    clip = next(c for t in latest["doc"]["tracks"] for c in t["clips"] if c["id"] == "clip-a")
    assert clip["attrs"]["speed"] == "4"
    assert clip["attrs"]["volume"] == "0.3"

    fresh = fd.read_project_film("film-test")
    new_base = fresh["doc"]
    changed, _ = fd.apply_ops(
        fresh["doc"], [{"op": "set", "clip": "clip-a", "field": "speed", "value": 2}]
    )
    (root / fd.FILM_NAME).write_text(fd.serialize_film(changed), encoding="utf-8")
    with pytest.raises(fd.FilmError) as raised:
        fd.edit_project_film(
            "film-test", fresh["rev"],
            [{"op": "set", "clip": "clip-a", "field": "speed", "value": 1.25}],
            base_snapshot=new_base,
        )
    assert raised.value.code == "REVISION_CONFLICT"


def test_revision_peek_hashes_malformed_document_without_parsing(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-test"
    root.mkdir(parents=True)
    (root / fd.FILM_NAME).write_text("<html><head><script>partial", encoding="utf-8")
    peek = fd.peek_project_revision("film-test")
    assert peek["ok"] is True and peek["exists"] is True
    assert peek["rev"]
    with pytest.raises(fd.FilmError):
        fd.read_project_film("film-test")


def test_head_script_is_rejected():
    with pytest.raises(fd.FilmError,match="head 禁止 script"):
        fd.parse_film(SAMPLE.replace("</head>","<script>alert(1)</script></head>"))


def test_api_ui_routes_are_wired():
    root=Path(__file__).resolve().parents[1]
    server=(root/"webui"/"server.py").read_text(encoding="utf-8")
    index=(root/"webui"/"static"/"index.html").read_text(encoding="utf-8")
    js=(root/"webui"/"static"/"js"/"film-studio.js").read_text(encoding="utf-8")
    module=(root/"video_director"/"film_document.py").read_text(encoding="utf-8")
    runtime=(root/"webui"/"static"/"js"/"film-runtime.js").read_text(encoding="utf-8")
    css=(root/"webui"/"static"/"css"/"film-studio.css").read_text(encoding="utf-8")
    assert "/api/editor/film/initialize" in server
    assert "/api/editor/film/edit" in server
    assert "/api/editor/film/command" in server
    assert 'data-film-action="toggle"' in index
    assert "filmStudioCommit" in js
    assert "filmStudioAddAsset" in js
    assert "/api/editor/film/insert-asset" in server
    assert "base:S.rev" in js
    assert "base_snapshot" in js
    assert "baseSnapshot()" in js
    assert "REVISION_CONFLICT" in js
    assert "BASE_REVISION_REQUIRED" in module
    assert 'payload.get("base_snapshot")' in server
    assert 'edit_project_film(pid, payload.get("base"), operations)' not in server
    assert server.count('edit_project_film(pid, payload.get("base"), operations, payload.get("base_snapshot"))') == 2
    assert "base_snapshot:snapshot" in js
    assert "_clip_rebase_conflicts" in module
    assert "/api/editor/film/revision" in server
    assert "peek_project_revision" in server
    assert "film/revision" in js
    assert "addEventListener('wheel',onTimelineWheel,{passive:false})" in js
    assert "Math.exp(-e.deltaY/360)" in js
    assert "function dragAutoScrollTick()" in js
    assert "requestAnimationFrame(dragAutoScrollTick)" in js
    assert "stopDragAutoScroll()" in js
    assert "ripple_remove" in js
    assert 'data-film-action="ripple-remove"' in index
    assert '"ripple_remove"' in module
    assert "fs-field-left" in js
    assert "effectiveClipGeometry" in js
    assert r"match(/^(-?\\d+(?:\\.\\d+)?)px$/i)" not in js
    assert r"match(/^(-?\d+(?:\.\d+)?)px$/i)" in js
    assert r"match(/^(-?\d+(?:\.\d+)?)deg$/i)" in js
    assert "画面变换" in js
    assert "fs-transform-overlay" in js
    assert "beginTransformDrag" in js
    assert "transformOpsForBox" in js
    assert "film-runtime:preview-transform" in js
    assert "previewTransform(message)" in runtime
    assert "fs-transform-overlay" in index
    assert ".fs-transform-overlay" in css
    assert "SETTABLE_FIELDS" in module
    assert "/static/css/film-studio.css" in index




def test_migration_preserves_scene_layer_and_project_semantics_through_film_roundtrip(tmp_path, monkeypatch):
    from video_director.models import AssetRef, CompositionIR, Layer, LayerType, ProjectSpec, SceneIR

    storage = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    project = storage / "film-metadata-roundtrip"
    assets = project / "assets"
    assets.mkdir(parents=True)
    source = assets / "still.png"
    source.write_bytes(b"test-image-bytes")
    asset = AssetRef(
        id="still-1", type="image", file=str(source), duration=2,
        width=320, height=180, source="uploaded",
        license_note="test license", rights_status="CLEAR",
    )
    layer = Layer(
        type=LayerType.image, asset_id="still-1", duration=2,
        fit_mode="contain",
    )
    scene = SceneIR(
        id="scene-1", start=0, duration=2, text="保留场景文本",
        layers=[layer], semantic_role="hook",
        intent="表达核心观点", visual_intent="固定机位展示产品",
    )
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=24, duration=2),
        scenes=[scene], assets=[asset], style_id="brand-style",
    )
    plan = fd.build_film_from_composition("film-metadata-roundtrip", composition)
    assert plan["warnings"] == []
    raw = fd.serialize_film(plan["document"])
    loaded = fd.parse_film(raw)
    saved_again = fd.serialize_film(loaded)
    roundtrip = fd.parse_film(saved_again)
    clip = next(c for track in roundtrip["tracks"] for c in track["clips"] if "data-vd-scene-id" in c["attrs"])
    scene_meta = json.loads(clip["attrs"]["data-vd-scene-meta"])
    layer_meta = json.loads(clip["attrs"]["data-vd-layer-meta"])
    project_meta_tag = re.search(
        r'<meta\b[^>]*\bname="video-director-project-meta"[^>]*>',
        roundtrip["head_html"], re.I | re.S,
    )
    assert project_meta_tag
    content = re.search(r'\bcontent="([^"]*)"', project_meta_tag.group(0), re.I | re.S)
    project_meta = json.loads(html.unescape(content.group(1)))
    assert scene_meta["intent"] == "表达核心观点"
    assert scene_meta["visual_intent"] == "固定机位展示产品"
    assert scene_meta["semantic_role"] == "hook"
    # fit_mode is mapped to executable CSS, not duplicated in opaque metadata.
    assert "fit_mode" not in layer_meta
    assert "object-fit:contain" in clip["attrs"].get("style", "")
    invalid_fit = fd.CompositionIR.model_validate_json(composition.model_dump_json())
    invalid_fit.scenes[0].layers[0].fit_mode = "scale-down"
    invalid_plan = fd.build_film_from_composition("film-metadata-roundtrip", invalid_fit)
    assert any("fit_mode=\'scale-down\'" in warning for warning in invalid_plan["warnings"])
    assert project_meta["schema"] == "video-director-composition-metadata/1"
    assert project_meta["fps"] == 24
    assert project_meta["style_id"] == "brand-style"
    assert project_meta["assets"][0]["license_note"] == "test license"
    assert loaded["stage"] == {"width": 320, "height": 180}
    assert clip["attrs"]["src"].endswith("#t=0,2")


def test_migration_warns_and_never_overwrites_existing_film():
    root=Path(__file__).resolve().parents[1]
    server=(root/"webui"/"server.py").read_text(encoding="utf-8")
    module=(root/"video_director"/"film_document.py").read_text(encoding="utf-8")
    assert "MIGRATION_UNSUPPORTED_FIELDS" in server
    assert "accept_warnings cannot bypass" not in server
    route_start = server.index('if route == "/api/editor/film/initialize":')
    route_end = server.index('if route == "/api/editor/film/render":', route_start)
    migration_route = server[route_start:route_end]
    assert 'if plan["warnings"]:' in migration_route
    # The option is mentioned in the rejection message, but is never read as an override.
    assert not re.search(r'\b(?:data|body|payload)\.get\(\s*["\']accept_warnings["\']', migration_route)
    assert "accept_warnings 不能绕过数据完整性门槛" in migration_route
    assert "FILM_ALREADY_INITIALIZED" in module
    assert "document_revision" in module
    assert "data-vd-scene-meta" in module
    assert "data-vd-layer-meta" in module
    assert "data-vd-layer-index" in module
    assert "composition_from_film" in module
    assert '"/api/editor/film/composition"' in server
    assert "video-director-project-meta" in module



def test_composition_ir_reverse_mapping_tracks_timeline_edits_and_preserves_asset_paths(tmp_path, monkeypatch):
    from video_director.models import AudioTrack, AudioTrackType, AssetRef, CompositionIR, Layer, LayerType, ProjectSpec, SceneIR

    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-reverse-roundtrip"
    (root / "assets").mkdir(parents=True)
    source = root / "assets" / "still.png"
    source.write_bytes(b"test-image-bytes")
    music_source = root / "assets" / "music.mp3"
    music_source.write_bytes(b"test-audio-bytes")
    asset = AssetRef(
        id="still-1", type="image", file=str(source), duration=2, width=320, height=180,
        source="uploaded", license_note="reverse test", rights_status="CLEAR",
    )
    music = AssetRef(id="music-1", type="audio", file=str(music_source), duration=2, source="uploaded")
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=24, duration=2),
        scenes=[SceneIR(
            id="scene-1", start=0, duration=2, text="场景语义",
            layers=[Layer(type=LayerType.image, asset_id="still-1", duration=2, fit_mode="contain")],
            semantic_role="hook", intent="保留语义",
        )],
        audio=[AudioTrack(type=AudioTrackType.bgm, asset_id="music-1", start=0, duration=2, source_out=2)],
        assets=[asset, music], style_id="reverse-style",
    )
    plan = fd.build_film_from_composition("film-reverse-roundtrip", composition)
    assert plan["warnings"] == []
    loaded = fd.parse_film(fd.serialize_film(plan["document"]))
    initial = fd.composition_from_film(loaded)
    assert initial["scenes"][0]["intent"] == "保留语义"
    assert initial["scenes"][0]["layers"][0]["fit_mode"] == "contain"
    assert initial["assets"][0]["file"] == "assets/still.png"
    assert len(initial["audio"]) == 1
    assert initial["audio"][0]["id"] is None
    assert initial["audio"][0]["track_id"] == "audio-bgm"
    assert initial["assets"][1]["file"] == "assets/music.mp3"

    clip = next(c for t in loaded["tracks"] for c in t["clips"] if c["attrs"].get("data-vd-scene-id"))
    edited, _ = fd.apply_ops(loaded, [{"op": "set", "clip": clip["id"], "field": "at", "value": 0.5}])
    synced = fd.composition_from_film(fd.parse_film(fd.serialize_film(edited)))
    assert synced["scenes"][0]["layers"][0]["start"] == 0.5
    assert synced["scenes"][0]["duration"] == 2.5
    assert synced["project"]["duration"] == 2.5

def test_composition_reverse_mapping_rejects_unbound_inserted_clip(tmp_path, monkeypatch):
    from video_director.models import AssetRef, CompositionIR, Layer, LayerType, ProjectSpec, SceneIR
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-unbound-clip"
    (root / "assets").mkdir(parents=True)
    source = root / "assets" / "still.png"
    source.write_bytes(b"test-image-bytes")
    composition = CompositionIR(
        project=ProjectSpec(width=320, height=180, fps=24, duration=2),
        scenes=[SceneIR(id="scene-unbound", start=0, duration=2, text="scene",
                        layers=[Layer(type=LayerType.image, asset_id="still-1", duration=2)])],
        assets=[AssetRef(id="still-1", type="image", file=str(source), duration=2, width=320, height=180)],
    )
    plan = fd.build_film_from_composition("film-unbound-clip", composition)
    assert plan["warnings"] == []
    doc = fd.parse_film(fd.serialize_film(plan["document"]))
    # This is a valid film edit, but it has no stable CompositionIR clip binding.
    edited, _ = fd.apply_ops(doc, [{
        "op": "insert", "track_id": doc["tracks"][0]["id"],
        "clip": {"id": "unbound-clip", "tag": "img",
                 "attrs": {"src": "../assets/unbound.png#t=0,1", "at": "20"}},
    }])
    with pytest.raises(fd.FilmError) as raised:
        fd.composition_from_film(edited)
    assert raised.value.code in {
        "COMPOSITION_TRACK_BINDING_MISSING", "COMPOSITION_BINDING_MISSING",
        "COMPOSITION_TRACK_ORDER_UNSUPPORTED",
    }


def test_trim_start_and_end_preserve_source_time_and_speed():
    doc = fd.parse_film(SAMPLE)
    edited, ops = fd.apply_ops(doc, [
        {"op": "trim", "clip": "clip-a", "edge": "start", "at": 1},
        {"op": "trim", "clip": "clip-a", "edge": "end", "end": 3},
    ])
    clip = next(c for c in edited["tracks"][0]["clips"] if c["id"] == "clip-a")
    assert clip["attrs"]["at"] == "1"
    assert clip["attrs"]["src"].endswith("#t=4,8")
    assert fd.clip_duration(clip) == 2
    assert [item["op"] for item in ops] == ["trim", "trim"]


def test_still_trim_keeps_zero_source_in_and_changes_visible_duration():
    still = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
    <section id="still-track"><img id="still" src="../assets/still.png#t=0,4" at="2"></section>
    </body></html>'''
    doc = fd.parse_film(still)
    edited, _ = fd.apply_ops(doc, [{"op": "trim", "clip": "still", "edge": "start", "at": 3}])
    clip = edited["tracks"][0]["clips"][0]
    assert clip["attrs"]["at"] == "3"
    assert clip["attrs"]["src"].endswith("#t=0,3")
    assert fd.clip_duration(clip) == 3


def test_external_absolute_and_encoded_protocol_src_are_rejected():
    for value in ("https://example.com/a.mp4#t=0,4", "/etc/passwd#t=0,4",
                  "%6a%61vascript%3aalert(1)#t=0,4"):
        doc_text = SAMPLE.replace("../assets/a.mp4#t=2,12", value)
        with pytest.raises(fd.FilmError, match="src|路径"):
            fd.parse_film(doc_text)


def test_empty_track_removal_is_safe_and_nonempty_track_cannot_be_removed():
    doc = fd.parse_film(SAMPLE)
    with pytest.raises(fd.FilmError, match="只能删除空轨道"):
        fd.apply_ops(doc, [{"op": "track_remove", "track_id": "video-main"}])
    edited, _ = fd.apply_ops(doc, [{"op": "track_add", "track_id": "empty-extra", "label": "待删除", "index": 0},
                                   {"op": "track_remove", "track_id": "empty-extra"}])
    assert all(track["id"] != "empty-extra" for track in edited["tracks"])


def test_media_endpoint_cannot_read_another_project_but_can_read_shared_libraries(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "storage" / "composer"))
    composer = tmp_path / "storage" / "composer"
    current = composer / "film-test"
    other = composer / "other-project"
    shared = composer / "assets" / "local"
    audio = composer / "audio_library" / "bgm"
    for folder in (current / "scenes", other, shared, audio):
        folder.mkdir(parents=True, exist_ok=True)
    (current / "scenes" / "subtitles.html").write_text("<html></html>", encoding="utf-8")
    (other / "secret.txt").write_text("not-for-this-project", encoding="utf-8")
    (shared / "shared.mp4").write_bytes(b"shared")
    (audio / "music.mp3").write_bytes(b"music")
    assert fd.media_path_for_src("film-test", "scenes/subtitles.html").is_file()
    assert fd.media_path_for_src("film-test", "../assets/local/shared.mp4").is_file()
    assert fd.media_path_for_src("film-test", "../audio_library/bgm/music.mp3").is_file()
    with pytest.raises(fd.FilmError, match="不能跨项目读取"):
        fd.media_path_for_src("film-test", "../other-project/secret.txt")


def test_project_symlink_cannot_escape_composer_root(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    external = tmp_path / "external"
    external.mkdir()
    composer = tmp_path / "composer"
    composer.mkdir()
    link = composer / "film-escape"
    try:
        link.symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不支持创建目录符号链接")
    with pytest.raises(fd.FilmError, match="超出 composer 存储根目录"):
        fd.media_path_for_src("film-escape", "secret.txt")


def test_ai_planner_contract_includes_trim_and_persisted_revision_guards():
    root = Path(__file__).resolve().parents[1]
    module = (root / "video_director" / "film_document.py").read_text(encoding="utf-8")
    ui = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert '"trim"' in module
    assert "data-trim" in ui
    assert "legacyEditorLoad" in ui
    assert "fs-pane-divider" in ui






def test_set_geometry_attributes_are_validated_and_removable():
    doc = fd.parse_film(SAMPLE)
    edited, _ = fd.apply_ops(doc, [
        {"op": "set", "clip": "clip-a", "field": "x", "value": 0.25},
        {"op": "set", "clip": "clip-a", "field": "y", "value": 0.1},
        {"op": "set", "clip": "clip-a", "field": "width", "value": 0.5},
        {"op": "set", "clip": "clip-a", "field": "height", "value": 0.4},
        {"op": "set", "clip": "clip-a", "field": "rotate", "value": 30},
        {"op": "set", "clip": "clip-a", "field": "opacity", "value": 0.7},
    ])
    clip = next(c for t in edited["tracks"] for c in t["clips"] if c["id"] == "clip-a")
    assert clip["attrs"]["x"] == "0.25"
    assert clip["attrs"]["width"] == "0.5"
    assert clip["attrs"]["rotate"] == "30"
    assert clip["attrs"]["opacity"] == "0.7"
    cleared, _ = fd.apply_ops(edited, [
        {"op": "set", "clip": "clip-a", "field": "x", "value": None},
        {"op": "set", "clip": "clip-a", "field": "width", "value": None},
    ])
    clip2 = next(c for t in cleared["tracks"] for c in t["clips"] if c["id"] == "clip-a")
    assert "x" not in clip2["attrs"] and "width" not in clip2["attrs"]
    with pytest.raises(fd.FilmError, match="不能大于"):
        fd.apply_ops(doc, [{"op": "set", "clip": "clip-a", "field": "x", "value": 1.5}])


def test_ripple_remove_shifts_only_following_clips_on_same_track():
    doc = fd.parse_film(SAMPLE)
    edited, ops = fd.apply_ops(doc, [{"op": "ripple_remove", "clip": "clip-a"}])
    video = next(track for track in edited["tracks"] if track["id"] == "video-main")
    audio = next(track for track in edited["tracks"] if track["id"] == "audio-bed")
    assert [clip["id"] for clip in video["clips"]] == ["clip-b"]
    assert video["clips"][0]["attrs"]["at"] == "0"
    assert audio["clips"][0]["attrs"]["at"] == "0"
    assert ops[0]["track_id"] == "video-main"
    assert ops[0]["shifted_clip_ids"] == ["clip-b"]
    assert fd.clip_duration(video["clips"][0]) == 5


def test_ripple_remove_respects_locked_tracks_and_requires_known_duration():
    doc = fd.parse_film(SAMPLE)
    doc["tracks"][0]["attrs"]["locked"] = ""
    with pytest.raises(fd.FilmError, match="已锁定"):
        fd.apply_ops(doc, [{"op": "ripple_remove", "clip": "clip-a"}])

    unknown_duration = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
    <section id="track-a"><video id="unknown" src="../assets/a.mp4" at="0"></video></section>
    </body></html>'''
    doc = fd.parse_film(unknown_duration)
    with pytest.raises(fd.FilmError, match="源区间"):
        fd.apply_ops(doc, [{"op": "ripple_remove", "clip": "unknown"}])


def test_ai_planner_requires_explicit_ripple_delete_intent():
    class RippleLLM:
        def generate(self, prompt):
            assert "ripple_remove" in prompt
            return json.dumps({
                "operations": [{"op": "ripple_remove", "clip": "clip-b"}],
                "summary": "波纹删除第二段",
            }, ensure_ascii=False)

    state = {"exists": True, "doc": fd.parse_film(SAMPLE), "rev": "test-revision"}
    allowed = fd.plan_film_command(RippleLLM(), state, "对第二段波纹删除")
    assert allowed == [{"op": "ripple_remove", "clip": "clip-b"}]
    with pytest.raises(fd.FilmError, match="波纹删除会移动同轨后续片段"):
        fd.plan_film_command(RippleLLM(), state, "删除第二段")


def test_ai_planner_gets_stable_timeline_ordinals_can_remove_and_preserves_summary():
    class FakeLLM:
        def __init__(self):
            self.prompt = ""

        def generate(self, prompt):
            self.prompt = prompt
            return '{"operations":[{"op":"remove","clip":"clip-b"}],"summary":"删除第二个视觉片段"}'

    llm = FakeLLM()
    state = {"exists": True, "doc": fd.parse_film(SAMPLE), "rev": "test-revision"}
    summary = {}
    operations = fd.plan_film_command(llm, state, "删除第 2 段", summary_out=summary)
    assert operations == [{"op": "remove", "clip": "clip-b"}]
    assert '"visual_index":2' in llm.prompt
    assert '"track_index":1' in llm.prompt
    assert summary["summary"] == "删除第二个视觉片段"


def test_ai_no_op_can_return_a_clarifying_question_for_the_chat():
    class FakeLLM:
        def generate(self, prompt):
            return '{"operations":[],"summary":"你说的第二段是主画面，还是叠加画面？"}'

    state = {"exists": True, "doc": fd.parse_film(SAMPLE), "rev": "test-revision"}
    summary = {}
    operations = fd.plan_film_command(FakeLLM(), state, "调整第二段", summary_out=summary)
    assert operations == []
    assert summary["summary"] == "你说的第二段是主画面，还是叠加画面？"




def test_ai_planner_cannot_change_source_path_or_page_overrides():
    class FakeLLM:
        def __init__(self, field):
            self.field = field

        def generate(self, prompt):
            value = "../assets/another.mp4#t=0,5" if self.field == "src" else '[{"at":"#caption","text":"injected"}]'
            return json.dumps({
                "operations": [{"op": "set", "clip": "clip-a", "field": self.field, "value": value}],
                "summary": "修改字段",
            }, ensure_ascii=False)

    state = {"exists": True, "doc": fd.parse_film(SAMPLE), "rev": "test-revision"}
    for field in ("src", "overrides"):
        with pytest.raises(fd.FilmError, match="AI 使用了不允许的片段字段"):
            fd.plan_film_command(FakeLLM(field), state, "修改选中片段")


def test_real_asset_thumbnails_are_wired_from_clip_asset_lineage():
    root = Path(__file__).resolve().parents[1]
    module = (root / "video_director" / "film_document.py").read_text(encoding="utf-8")
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    css = (root / "webui" / "static" / "css" / "film-studio.css").read_text(encoding="utf-8")
    assert '"data-asset-id": str(layer.get("asset_id") or "")' in module
    assert "fs-clip-thumb" in studio
    assert "fs-asset-thumb" in studio
    assert ".fs-clip-thumb" in css


def test_film_runtime_buffers_commands_until_iframe_load_and_acknowledges_revision():
    root = Path(__file__).resolve().parents[1]
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "runtimeReady:false" in studio
    assert "runtimeCommand:''" in studio
    assert "function filmStudioRuntimeFrameLoaded()" in studio
    assert "runtimeFrame.addEventListener('load',filmStudioRuntimeFrameLoaded)" in studio
    assert "S.runtimeCommand=type" in studio
    assert "message.rev!==S.rev" in studio


def test_film_revision_export_routes_to_film_renderer_not_legacy_ir():
    root = Path(__file__).resolve().parents[1]
    server = (root / "webui" / "server.py").read_text(encoding="utf-8")
    workflow = (root / "video_director" / "workflow_run.py").read_text(encoding="utf-8")
    renderer = (root / "video_director" / "film_render.py").read_text(encoding="utf-8")
    assert '"/api/editor/film/render"' in server
    assert "run_film_render_async" in server
    assert "render_project_film" in workflow
    assert "read_project_film" in renderer
    assert "editor.load_composition" not in renderer


def test_film_runtime_supports_frame_exact_video_seek_ack_and_css_projection():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "webui" / "static" / "js" / "film-runtime.js").read_text(encoding="utf-8")
    module = (root / "video_director" / "film_document.py").read_text(encoding="utf-8")
    assert "film-runtime:render-frame" in runtime
    assert "film-runtime:frame-ready" in runtime
    assert "requestVideoFrameCallback" in runtime
    assert '"render_css": render_css' in module
    assert "film-time:ready" in module


def test_browser_runtime_is_wired_to_authoritative_film_revision_and_multi_track_messages():
    root = Path(__file__).resolve().parents[1]
    index = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    runtime_html = (root / "webui" / "static" / "film-runtime.html").read_text(encoding="utf-8")
    runtime = (root / "webui" / "static" / "js" / "film-runtime.js").read_text(encoding="utf-8")
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "/static/film-runtime.html" in index
    assert "/static/js/film-runtime.js" in runtime_html
    assert "film-runtime:load" in runtime
    assert "film-runtime:seek" in runtime
    assert "film-runtime:seek-pause" in runtime
    assert "syncRuntime(wasPlaying?'seek-pause':undefined)" in studio
    assert "film-runtime:time" in runtime
    assert "fs-preview-composition" in studio
    assert 'data-film-action="play-toggle"' in index
    assert "event.source!==parent" in runtime


def test_legacy_composition_is_blocked_once_film_html_exists(tmp_path, monkeypatch):
    from video_director import editor, workflow_run

    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-test"
    root.mkdir(parents=True)
    (root / fd.FILM_NAME).write_text("not even valid HTML", encoding="utf-8")

    with pytest.raises(fd.FilmError) as raised:
        fd.assert_legacy_composition_access("film-test", "导出")
    assert raised.value.code == "FILM_DOCUMENT_AUTHORITATIVE"
    assert raised.value.status == 409

    command = editor.execute_command("film-test", {"command": "set_duration", "payload": {}})
    assert command.status == "REJECTED"
    assert command.errors[0]["code"] == "FILM_DOCUMENT_AUTHORITATIVE"

    with pytest.raises(fd.FilmError, match="film.html"):
        workflow_run.run_render_async("film-test", "final")


def test_legacy_save_and_render_endpoints_are_hard_gated_after_migration():
    root = Path(__file__).resolve().parents[1]
    editor = (root / "video_director" / "editor.py").read_text(encoding="utf-8")
    workflow = (root / "video_director" / "workflow_run.py").read_text(encoding="utf-8")
    confirm = (root / "video_director" / "confirm.py").read_text(encoding="utf-8")
    server = (root / "webui" / "server.py").read_text(encoding="utf-8")
    assert 'assert_legacy_composition_access(project_id, "保存 CompositionIR")' in editor
    assert 'assert_legacy_composition_access(project_id, f"渲染 {kind}")' in workflow
    assert 'assert_legacy_composition_access(project_id, "导出旧时间线成片")' in confirm
    assert '_reject_legacy_composition_access(pid, "启动旧时间线渲染")' in server
    assert '_reject_legacy_composition_access(pid, "导出旧时间线成片")' in server


