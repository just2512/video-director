"""Contracts for the OpenFilm-inspired film document editor."""
from __future__ import annotations

import json

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


def test_attribute_edit_preserves_unrelated_html_text_and_comments(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-preserve"
    root.mkdir(parents=True)
    source = SAMPLE.replace(
        "<head>",
        "<head><!-- keep head formatting and comments -->",
    ).replace(
        '<video id="clip-a"',
        '<!-- retain this clip marker -->\n<video id="clip-a"',
    ).replace(
        '<video id="clip-b"',
        '<!-- untouched clip bytes must survive -->\n<video  id = "clip-b"',
    )
    (root / fd.FILM_NAME).write_text(source, encoding="utf-8")
    before = fd.read_project_film("film-preserve")
    untouched = '<video  id = "clip-b" src="../assets/b.mp4#t=0,5" at="5"></video>'
    assert untouched in source

    updated = fd.edit_project_film(
        "film-preserve", before["rev"],
        [{"op": "set", "clip": "clip-a", "field": "speed", "value": 2.1}],
    )
    written = (root / fd.FILM_NAME).read_text(encoding="utf-8")
    assert '<!-- keep head formatting and comments -->' in written
    assert '<!-- retain this clip marker -->' in written
    assert '<!-- untouched clip bytes must survive -->' in written
    assert untouched in written
    assert '<video id="clip-a" src="../assets/a.mp4#t=2,12" at="0" speed="2.1" volume="1"></video>' in written
    assert fd.read_project_film("film-preserve")["rev"] == updated["rev"]


def test_split_preserves_unrelated_comments_and_clip_markup(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-structure"
    root.mkdir(parents=True)
    source = SAMPLE.replace(
        '<video id="clip-a"',
        '<!-- retain near split clip -->\n<video id="clip-a"',
    ).replace(
        '<video id="clip-b"',
        '<!-- untouched clip comment -->\n<video  id = "clip-b"',
    )
    (root / fd.FILM_NAME).write_text(source, encoding="utf-8")
    before = fd.read_project_film("film-structure")
    untouched = '<video  id = "clip-b" src="../assets/b.mp4#t=0,5" at="5"></video>'

    updated = fd.edit_project_film(
        "film-structure", before["rev"],
        [{"op": "split", "clip": "clip-a", "at": 2, "new_id": "clip-a-right"}],
    )
    written = (root / fd.FILM_NAME).read_text(encoding="utf-8")
    assert '<!-- retain near split clip -->' in written
    assert '<!-- untouched clip comment -->' in written
    assert untouched in written
    assert 'id="clip-a-right"' in written
    assert '<video id="clip-a" src="../assets/a.mp4#t=2,6" at="0" speed="2" volume="1"></video>' in written
    assert fd.read_project_film("film-structure")["rev"] == updated["rev"]


def test_insert_into_existing_track_preserves_other_nodes(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-insert"
    root.mkdir(parents=True)
    source = SAMPLE.replace(
        '<video id="clip-b"',
        '<!-- keep target clip bytes -->\n<video  id = "clip-b"',
    )
    (root / fd.FILM_NAME).write_text(source, encoding="utf-8")
    before = fd.read_project_film("film-insert")
    untouched = '<video  id = "clip-b" src="../assets/b.mp4#t=0,5" at="5"></video>'
    updated = fd.edit_project_film(
        "film-insert", before["rev"],
        [{"op": "insert", "track_id": "video-main", "clip": {
            "id": "inserted-still", "tag": "img",
            "attrs": {"src": "../assets/still.png#t=0,2", "at": "11", "title": "新增静帧"}
        }}],
    )
    written = (root / fd.FILM_NAME).read_text(encoding="utf-8")
    assert '<!-- keep target clip bytes -->' in written
    assert untouched in written
    assert '<img ' in written and 'id="inserted-still"' in written
    assert fd.read_project_film("film-insert")["rev"] == updated["rev"]


def test_film_proposal_store_is_project_scoped_single_use_and_expires():
    from video_director.film_proposals import FilmProposalStore

    now = [100.0]
    store = FilmProposalStore(ttl_seconds=15, clock=lambda: now[0])
    operations = [{"op": "set", "clip": "clip-a", "field": "speed", "value": 1.5}]
    store.put("proposal-1", "project-a", "rev-1", operations)
    operations[0]["value"] = 3.0

    assert store.take("project-b", "proposal-1", "rev-1") is None
    assert store.take("project-a", "proposal-1", "wrong-rev") is None
    consumed = store.take("project-a", "proposal-1", "rev-1")
    assert consumed["operations"][0]["value"] == 1.5
    assert store.take("project-a", "proposal-1", "rev-1") is None

    store.put("proposal-expired", "project-a", "rev-2", [{"op": "remove", "clip": "clip-b"}])
    now[0] = 116.0
    assert store.take("project-a", "proposal-expired", "rev-2") is None


def test_apply_proposal_uses_server_registered_operations_not_request_ops():
    root = Path(__file__).resolve().parents[1]
    server = (root / "webui" / "server.py").read_text(encoding="utf-8")
    client = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert '_FILM_PROPOSALS.put(proposal["id"], pid, state["rev"], normalized)' in server
    assert '_FILM_PROPOSALS.take(pid, proposal_id, base_revision)' in server
    assert 'approved["operations"], require_base=True' in server
    apply_client = client.split("'/api/editor/film/apply-proposal'", 1)[1].split(");", 1)[0]
    assert "ops:proposal.operations" not in apply_client


def test_proposal_apply_requires_exact_revision_without_breaking_manual_rebase(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-proposal"
    root.mkdir(parents=True)
    (root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    original = fd.read_project_film("film-proposal")
    fd.edit_project_film(
        "film-proposal", original["rev"],
        [{"op": "set", "clip": "clip-a", "field": "speed", "value": 2.5}],
    )
    current = fd.read_project_film("film-proposal")
    with pytest.raises(fd.FilmError, match="提案已过期"):
        fd.edit_project_film(
            "film-proposal", original["rev"],
            [{"op": "set", "clip": "clip-b", "field": "volume", "value": 0.5}],
            require_base=True,
        )
    assert fd.read_project_film("film-proposal")["rev"] == current["rev"]


def test_ai_command_has_explicit_apply_proposal_route_and_sse():
    root = Path(__file__).resolve().parents[1]
    server = (root / "webui" / "server.py").read_text(encoding="utf-8")
    script = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    index = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    assert '"/api/editor/film/apply-proposal"' in server
    assert "candidate_doc, normalized = fd.apply_ops" in server
    assert "require_base=True" in server
    assert 'sse_publish("film"' in server
    assert "filmStudioPreviewProposal" in script
    assert "filmStudioApplyProposal" in script
    assert "filmStudioRejectProposal" in script
    assert 'id="fs-proposal-panel"' in index


def test_atomic_edits_revision_rebase_and_undo_redo(tmp_path,monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT",str(tmp_path/"composer"))
    root=tmp_path/"composer"/"film-test";root.mkdir(parents=True)
    (root/fd.FILM_NAME).write_text(SAMPLE,encoding="utf-8")
    original=fd.read_project_film("film-test")
    first=fd.edit_project_film("film-test",original["rev"],[{"op":"set","clip":"clip-b","field":"at","value":7}])
    assert first["rebased"] is False
    rebased=fd.edit_project_film("film-test",original["rev"],[{"op":"set","clip":"clip-b","field":"at","value":8}])
    assert rebased["rebased"] is True
    undone=fd.undo_redo_project_film("film-test",rebased["rev"],"undo")
    assert undone["rev"]==first["rev"]
    redone=fd.undo_redo_project_film("film-test",undone["rev"],"redo")
    assert redone["rev"]==rebased["rev"]


def test_head_script_is_rejected():
    with pytest.raises(fd.FilmError,match="head 禁止 script"):
        fd.parse_film(SAMPLE.replace("</head>","<script>alert(1)</script></head>"))


def test_api_ui_routes_are_wired():
    root=Path(__file__).resolve().parents[1]
    server=(root/"webui"/"server.py").read_text(encoding="utf-8")
    index=(root/"webui"/"static"/"index.html").read_text(encoding="utf-8")
    js=(root/"webui"/"static"/"js"/"film-studio.js").read_text(encoding="utf-8")
    assert "/api/editor/film/initialize" in server
    assert "/api/editor/film/edit" in server
    assert "/api/editor/film/command" in server
    assert 'data-film-action="toggle"' in index
    assert "filmStudioCommit" in js
    assert "filmStudioAddAsset" in js
    assert "/api/editor/film/insert-asset" in server
    assert "base:S.rev" in js
    assert "/static/css/film-studio.css" in index


def test_film_timeline_renders_real_audio_waveforms_with_decode_guards():
    root = Path(__file__).resolve().parents[1]
    script = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    styles = (root / "webui" / "static" / "css" / "film-studio.css").read_text(encoding="utf-8")
    assert 'class="fs-clip-waveform" data-waveform-clip=' in script
    assert "decodeAudioData(bytes.slice(0))" in script
    assert "declared>8*1024*1024" in script
    assert "buffer.duration>600" in script
    assert "clip.src_in" in script and "clip.src_out" in script
    assert "renderAudioWaveforms();" in script
    assert ".fs-clip-waveform" in styles


def test_film_timeline_supports_group_selection_drag_and_pointer_anchored_zoom():
    root = Path(__file__).resolve().parents[1]
    script = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    styles = (root / "webui" / "static" / "css" / "film-studio.css").read_text(encoding="utf-8")

    assert "selectedIds:[]" in script
    assert "selectClipWithModifiers" in script
    assert "operations.push({op:'move',clip:member.id" in script
    assert "ids.map(function(id){return {op:'remove',clip:id};})" in script
    assert "scrollLeft+localX-132" in script
    assert "scroll.addEventListener('wheel'" in script
    assert "{passive:false}" in script
    assert ".fs-track.fs-track-drop-target" in styles


def test_external_film_revision_watcher_debounces_and_reports_broken_documents(tmp_path):
    from video_director.film_watch import FilmRevisionWatcher

    project_root = tmp_path / "film-watch"
    project_root.mkdir()
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    events = []
    watcher = FilmRevisionWatcher(
        lambda name, payload: events.append((name, payload)),
        path_for_project=lambda _pid: film_path,
        settle_seconds=0.12,
        interval_seconds=1.0,
    )
    watcher.watch("film-watch", fd.revision_of(SAMPLE))

    changed = SAMPLE.replace('speed="2"', 'speed="2.1"')
    film_path.write_text(changed, encoding="utf-8")
    watcher.poll_once(now=100.0)
    watcher.poll_once(now=100.05)
    assert events == []
    watcher.poll_once(now=100.13)
    assert len(events) == 1
    assert events[0][0] == "film"
    assert events[0][1]["phase"] == "revision_changed"
    assert events[0][1]["revision"] == fd.revision_of(changed)

    broken = "<html><body>partial write</body></html>"
    film_path.write_text(broken, encoding="utf-8")
    watcher.poll_once(now=200.0)
    watcher.poll_once(now=200.13)
    assert len(events) == 2
    assert events[-1][1]["phase"] == "document_broken"
    assert "error" in events[-1][1]

    repaired = SAMPLE.replace('speed="2"', 'speed="2.5"')
    film_path.write_text(repaired, encoding="utf-8")
    watcher.poll_once(now=300.0)
    watcher.poll_once(now=300.13)
    assert len(events) == 3
    assert events[-1][1]["phase"] == "revision_changed"
    assert events[-1][1]["revision"] == fd.revision_of(repaired)


def test_server_registers_external_film_watcher_for_open_projects():
    root = Path(__file__).resolve().parents[1]
    server = (root / "webui" / "server.py").read_text(encoding="utf-8")
    watcher = (root / "video_director" / "film_watch.py").read_text(encoding="utf-8")
    assert "_FILM_REVISION_WATCHER = get_film_watcher(sse_publish)" in server
    assert '_FILM_REVISION_WATCHER.watch(pid, state.get("rev"))' in server
    assert "settle_seconds" in watcher
    assert '"phase": phase' in watcher


def test_frame_determinism_audit_accepts_identical_pixels_and_rejects_real_difference():
    from io import BytesIO
    from PIL import Image
    from video_director.film_render import _frame_diff_stats

    def png(color):
        buffer = BytesIO()
        Image.new("RGB", (32, 24), color).save(buffer, format="PNG")
        return buffer.getvalue()

    same = _frame_diff_stats(png((30, 50, 70)), png((30, 50, 70)))
    changed = _frame_diff_stats(png((30, 50, 70)), png((200, 5, 15)))
    assert same[0] == 0
    assert changed[0] == 32 * 24
    assert changed[1:] == (32 * 24, 32, 24)


def test_volume_envelope_filtergraph_executes_in_real_ffmpeg():
    import subprocess
    from video_director import renderer
    from video_director.film_render import _volume_envelope_filter

    ffmpeg = renderer._ffmpeg()
    expression = _volume_envelope_filter(0.4, [
        {"t": 0.0, "v": 1.0},
        {"t": 0.075, "v": 0.2},
        {"t": 0.15, "v": 0.8},
    ])
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=0.15", "-af", expression, "-f", "null", "-"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_clip_fade_contract_applies_to_runtime_and_ffmpeg():
    from video_director.film_render import _audio_fade_filters

    doc = fd.parse_film(SAMPLE)
    faded, _ = fd.apply_ops(doc, [
        {"op": "set", "clip": "clip-a", "field": "fade", "value": "0.5,0.75"}
    ])
    clip = next(c for t in faded["tracks"] for c in t["clips"] if c["id"] == "clip-a")
    assert clip["attrs"]["fade"] == "0.5,0.75"
    assert fd._parse_fade(clip["attrs"]["fade"]) == (0.5, 0.75)
    filters = _audio_fade_filters(0.5, 0.75, 5.0)
    assert filters == ["afade=t=in:st=0:d=0.5", "afade=t=out:st=4.25:d=0.75"]

    runtime = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-runtime.js").read_text(encoding="utf-8")
    studio = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "clipFadeAt(entry,t)" in runtime
    assert "baseVolume*volumeEnvelopeAt(entry.volumeEnvelope,localTime)*fadeFactor" in runtime
    assert "field:'fade'" in studio
    assert "淡入（秒）" in studio and "淡出（秒）" in studio


def test_clip_fade_rejects_invalid_or_overlong_fades():
    doc = fd.parse_film(SAMPLE)
    with pytest.raises(fd.FilmError, match="超过片段时长"):
        fd.apply_ops(doc, [{"op": "set", "clip": "clip-a", "field": "fade", "value": "3,3"}])
    with pytest.raises(fd.FilmError, match="fade 必须"):
        fd.apply_ops(doc, [{"op": "set", "clip": "clip-a", "field": "fade", "value": "0.5"}])


def test_volume_envelope_mode_does_not_steal_normal_clip_dragging():
    root = Path(__file__).resolve().parents[1]
    script = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    styles = (root / "webui" / "static" / "css" / "film-studio.css").read_text(encoding="utf-8")
    index = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "window.filmStudioToggleEnvelopeMode" in script
    assert "panel.classList.toggle('fs-envelope-mode',S.envelopeMode)" in script
    assert ".fs-clip-envelope{position:absolute;inset:0;width:100%;height:100%;z-index:3;pointer-events:none" in styles
    assert ".fs-envelope-mode .fs-clip-envelope{pointer-events:auto}" in styles
    assert 'data-film-action="envelope-mode"' in index


def test_volume_envelope_contract_drives_ffmpeg_and_preview_consistently():
    from video_director.film_render import _volume_envelope_filter

    doc = fd.parse_film(SAMPLE)
    points = [{"t": 0, "v": 1}, {"t": 2.5, "v": 0.2}, {"t": 5, "v": 0.75}]
    edited, operations = fd.apply_ops(doc, [
        {"op": "set", "clip": "audio-1", "field": "volume_envelope", "value": points}
    ])
    stored = next(c for t in edited["tracks"] for c in t["clips"] if c["id"] == "audio-1")
    assert json.loads(stored["attrs"]["volume_envelope"]) == points
    assert operations[0]["field"] == "volume_envelope"
    serialized = fd.serialize_film(edited)
    reparsed = fd.parse_film(serialized)
    audio = next(c for t in reparsed["tracks"] for c in t["clips"] if c["id"] == "audio-1")
    assert json.loads(audio["attrs"]["volume_envelope"]) == points

    expression = _volume_envelope_filter(0.4, points)
    assert expression.startswith("volume='0.4*(")
    assert "eval=frame" in expression
    assert "if(lt(t,2.5)" in expression

    runtime = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-runtime.js").read_text(encoding="utf-8")
    studio = (Path(__file__).resolve().parents[1] / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    assert "volumeEnvelopeAt(entry.volumeEnvelope,localTime)" in runtime
    assert 'data-envelope-clip=' in studio
    assert "field:'volume_envelope'" in studio
    assert "S.envelopeDrag" in studio


def test_volume_envelope_is_sliced_and_rebased_by_split_trim_and_ripple():
    doc = fd.parse_film(SAMPLE)
    envelope = [{"t": 0, "v": 1}, {"t": 5, "v": 0.2}, {"t": 12, "v": 0.8}, {"t": 20, "v": 0.6}]
    with_envelope, _ = fd.apply_ops(doc, [
        {"op": "set", "clip": "audio-1", "field": "volume_envelope", "value": envelope}
    ])

    split_doc, _ = fd.apply_ops(with_envelope, [
        {"op": "split", "clip": "audio-1", "at": 10, "new_id": "audio-right"}
    ])
    split_clips = {c["id"]: c for t in split_doc["tracks"] for c in t["clips"]}
    left_points = json.loads(split_clips["audio-1"]["attrs"]["volume_envelope"])
    right_points = json.loads(split_clips["audio-right"]["attrs"]["volume_envelope"])
    assert left_points[0] == {"t": 0.0, "v": 1.0}
    assert left_points[-1] == {"t": 10.0, "v": 0.628571}
    assert right_points[0] == {"t": 0.0, "v": 0.628571}
    assert right_points[-1] == {"t": 10.0, "v": 0.6}
    assert right_points[1] == {"t": 2.0, "v": 0.8}

    trim_doc, _ = fd.apply_ops(with_envelope, [
        {"op": "trim", "clip": "audio-1", "edge": "start", "at": 5}
    ])
    trimmed = next(c for t in trim_doc["tracks"] for c in t["clips"] if c["id"] == "audio-1")
    trim_points = json.loads(trimmed["attrs"]["volume_envelope"])
    assert trim_points[0] == {"t": 0.0, "v": 0.2}
    assert trim_points[-1] == {"t": 15.0, "v": 0.6}
    assert trim_points[1] == {"t": 7.0, "v": 0.8}

    ripple_doc, _ = fd.apply_ops(with_envelope, [
        {"op": "ripple_delete", "clip": "clip-b", "scope": "all",
         "split_ids": {"audio-1": "audio-ripple-right"}}
    ])
    ripple_audio = [c for t in ripple_doc["tracks"] for c in t["clips"] if c["tag"] == "audio"]
    ripple_by_id = {c["id"]: c for c in ripple_audio}
    ripple_right = json.loads(ripple_by_id["audio-ripple-right"]["attrs"]["volume_envelope"])
    assert ripple_right[0] == {"t": 0.0, "v": 0.628571}
    assert ripple_right[-1] == {"t": 10.0, "v": 0.6}
    assert ripple_right[1] == {"t": 2.0, "v": 0.8}


def test_volume_envelope_rejects_duplicate_times_and_non_finite_gain(tmp_path, monkeypatch):
    doc = fd.parse_film(SAMPLE)
    with pytest.raises(fd.FilmError, match="时间点必须严格递增"):
        fd.apply_ops(doc, [{"op": "set", "clip": "audio-1", "field": "volume_envelope",
                           "value": [{"t": 0, "v": 1}, {"t": 0, "v": 0.3}]}])
    with pytest.raises(fd.FilmError, match="不能大于 1"):
        fd.apply_ops(doc, [{"op": "set", "clip": "audio-1", "field": "volume_envelope",
                           "value": [{"t": 0, "v": 1}, {"t": 1, "v": 1.5}]}])


def test_join_through_edit_restores_split_source_range_and_preserves_undoable_document():
    doc = fd.parse_film(SAMPLE)
    split, _ = fd.apply_ops(doc, [
        {"op": "split", "clip": "clip-a", "at": 2, "new_id": "clip-a-right"}
    ])
    joined, operations = fd.apply_ops(split, [
        {"op": "join", "clip": "clip-a", "other_clip": "clip-a-right"}
    ])
    clips_by_id = {clip["id"]: clip for track in joined["tracks"] for clip in track["clips"]}
    assert "clip-a-right" not in clips_by_id
    assert clips_by_id["clip-a"]["attrs"]["src"].endswith("#t=2,12")
    assert float(clips_by_id["clip-a"]["attrs"]["at"]) == 0
    assert fd.clip_duration(clips_by_id["clip-a"]) == pytest.approx(5)
    assert operations == [{"op": "join", "clip": "clip-a", "other_clip": "clip-a-right"}]


def test_join_through_edit_rejects_gaps_mismatched_sources_and_different_attributes():
    doc = fd.parse_film(SAMPLE)
    split, _ = fd.apply_ops(doc, [
        {"op": "split", "clip": "clip-a", "at": 2, "new_id": "clip-a-right"}
    ])
    gap = fd.copy.deepcopy(split)
    next(c for t in gap["tracks"] for c in t["clips"] if c["id"] == "clip-a-right")["attrs"]["at"] = "2.1"
    next(c for t in gap["tracks"] for c in t["clips"] if c["id"] == "clip-b")["attrs"]["at"] = "5.2"
    with pytest.raises(fd.FilmError, match="时间线上不相邻"):
        fd.apply_ops(gap, [{"op": "join", "clip": "clip-a", "other_clip": "clip-a-right"}])

    different = fd.copy.deepcopy(split)
    next(c for t in different["tracks"] for c in t["clips"] if c["id"] == "clip-a-right")["attrs"]["volume"] = "0.5"
    with pytest.raises(fd.FilmError, match="属性 volume 不一致"):
        fd.apply_ops(different, [{"op": "join", "clip": "clip-a", "other_clip": "clip-a-right"}])


def test_join_through_edit_ui_and_ai_contract_are_wired():
    root = Path(__file__).resolve().parents[1]
    script = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    index = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    module = (root / "video_director" / "film_document.py").read_text(encoding="utf-8")
    assert "window.filmStudioJoinThroughEdit" in script
    assert 'data-film-action="join-cut"' in index
    assert 'elif kind == "join"' in module
    assert "愈合切口" in module


def test_roll_trim_moves_cut_and_preserves_combined_timeline_duration():
    doc = fd.parse_film(SAMPLE)
    split, _ = fd.apply_ops(doc, [
        {"op": "split", "clip": "clip-a", "at": 2, "new_id": "clip-a-right"}
    ])
    rolled, operations = fd.apply_ops(split, [
        {"op": "roll", "clip": "clip-a", "other_clip": "clip-a-right", "delta": 0.5}
    ])
    clips = {clip["id"]: clip for track in rolled["tracks"] for clip in track["clips"]}
    left, right = clips["clip-a"], clips["clip-a-right"]
    assert left["attrs"]["src"].endswith("#t=2,7")
    assert right["attrs"]["src"].endswith("#t=7,12")
    assert float(right["attrs"]["at"]) == pytest.approx(2.5)
    assert fd.clip_duration(left) == pytest.approx(2.5)
    assert fd.clip_duration(right) == pytest.approx(2.5)
    assert fd.clip_end(left) == pytest.approx(float(right["attrs"]["at"]))
    assert fd.clip_end(right) == pytest.approx(5)
    assert operations == [{"op": "roll", "clip": "clip-a", "other_clip": "clip-a-right", "delta": "0.5"}]


def test_roll_trim_rejects_nonadjacent_different_source_locked_track_and_empty_side():
    doc = fd.parse_film(SAMPLE)
    split, _ = fd.apply_ops(doc, [
        {"op": "split", "clip": "clip-a", "at": 2, "new_id": "clip-a-right"}
    ])
    with pytest.raises(fd.FilmError, match="时间线上相邻"):
        fd.apply_ops(split, [{"op": "roll", "clip": "clip-a", "other_clip": "clip-b", "delta": 0.2}])
    with pytest.raises(fd.FilmError, match="时长不足"):
        fd.apply_ops(split, [{"op": "roll", "clip": "clip-a", "other_clip": "clip-a-right", "delta": 3}])
    locked = fd.copy.deepcopy(split)
    locked["tracks"][0]["attrs"]["locked"] = ""
    with pytest.raises(fd.FilmError, match="已锁定"):
        fd.apply_ops(locked, [{"op": "roll", "clip": "clip-a", "other_clip": "clip-a-right", "delta": 0.1}])


def test_roll_trim_rejects_different_playback_speeds():
    doc = fd.parse_film(SAMPLE)
    split, _ = fd.apply_ops(doc, [
        {"op": "split", "clip": "clip-a", "at": 2, "new_id": "clip-a-right"}
    ])
    right = next(c for t in split["tracks"] for c in t["clips"] if c["id"] == "clip-a-right")
    right["attrs"]["speed"] = "1"
    next(c for t in split["tracks"] for c in t["clips"] if c["id"] == "clip-b")["attrs"]["at"] = "8"
    with pytest.raises(fd.FilmError, match="左右片段速度一致"):
        fd.apply_ops(split, [{"op": "roll", "clip": "clip-a", "other_clip": "clip-a-right", "delta": 0.1}])


def test_film_studio_buttons_override_global_full_width_button_defaults():
    root = Path(__file__).resolve().parents[1]
    css = (root / "webui" / "static" / "css" / "film-studio.css").read_text(encoding="utf-8")
    assert ".film-studio-panel .fs-btn{width:auto;margin-top:0;flex:0 0 auto}" in css
    assert ".film-studio-panel .fs-timeline-toolbar .fs-toolbar .fs-btn{width:auto;margin:0;flex:0 0 auto}" in css


def test_roll_trim_ctrl_drag_and_operation_contract():
    root = Path(__file__).resolve().parents[1]
    script = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    index = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    module = (root / "video_director" / "film_document.py").read_text(encoding="utf-8")
    assert "rollGesture=!!(trim&&e.ctrlKey)" in script
    assert "data-roll-delta" in script
    assert "members:members,targetTrackId:clip.track_id,targetRow:null" in script
    assert 'elif kind == "roll"' in module
    assert "Ctrl 拖动切口滚动修剪" in index


def test_slip_changes_source_range_without_changing_timeline_position_or_duration():
    doc = fd.parse_film(SAMPLE)
    original = next(c for t in doc["tracks"] for c in t["clips"] if c["id"] == "clip-a")
    original_at = float(original["attrs"]["at"])
    original_duration = fd.clip_duration(original)
    changed, operations = fd.apply_ops(doc, [
        {"op": "slip", "clip": "clip-a", "delta_source": 1.25}
    ])
    moved = next(c for t in changed["tracks"] for c in t["clips"] if c["id"] == "clip-a")
    assert moved["attrs"]["src"].endswith("#t=3.25,13.25")
    assert float(moved["attrs"]["at"]) == original_at
    assert fd.clip_duration(moved) == original_duration
    assert operations == [{"op": "slip", "clip": "clip-a", "delta_source": "1.25"}]


def test_slip_rejects_negative_source_in_and_locked_tracks():
    doc = fd.parse_film(SAMPLE)
    with pytest.raises(fd.FilmError, match="源入点不能小于零"):
        fd.apply_ops(doc, [{"op": "slip", "clip": "clip-a", "delta_source": -3}])
    doc["tracks"][0]["attrs"]["locked"] = ""
    with pytest.raises(fd.FilmError, match="已锁定"):
        fd.apply_ops(doc, [{"op": "slip", "clip": "clip-a", "delta_source": 1}])


def test_slip_and_speed_preset_controls_are_wired_into_timeline():
    root = Path(__file__).resolve().parents[1]
    script = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    styles = (root / "webui" / "static" / "css" / "film-studio.css").read_text(encoding="utf-8")
    index = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    module = (root / "video_director" / "film_document.py").read_text(encoding="utf-8")
    assert "e.altKey&&(clip.tag==='video'||clip.tag==='audio')" in script
    assert "field == \"slip\"" not in module  # slip is a first-class operation, not an inspector field
    assert 'data-speed-preset="'+"" in script
    assert ".fs-speed-chip-active" in styles
    assert "Alt 拖动滑移源区间" in index


def test_ripple_delete_closes_gap_across_tracks_and_replays_identical_split_ids():
    doc = fd.parse_film(SAMPLE)
    updated, operations = fd.apply_ops(doc, [
        {"op": "ripple_delete", "clip": "clip-b", "scope": "all"}
    ])
    normalized = operations[0]
    assert normalized["op"] == "ripple_delete"
    assert "audio-1" in normalized["split_ids"]
    tracks = {track["id"]: track for track in updated["tracks"]}
    main = {clip["id"]: clip for clip in tracks["video-main"]["clips"]}
    assert "clip-b" not in main
    audio = tracks["audio-bed"]["clips"]
    assert len(audio) == 2
    by_id = {clip["id"]: clip for clip in audio}
    assert by_id["audio-1"]["attrs"]["src"].endswith("#t=0,5")
    right_id = normalized["split_ids"]["audio-1"]
    assert by_id[right_id]["attrs"]["src"].endswith("#t=10,20")
    assert float(by_id[right_id]["attrs"]["at"]) == 5
    replayed, _ = fd.apply_ops(fd.parse_film(SAMPLE), operations)
    assert fd.serialize_film(replayed) == fd.serialize_film(updated)


def test_ripple_delete_rejects_locked_affected_track_without_mutating_original():
    doc = fd.parse_film(SAMPLE)
    doc["tracks"][1]["attrs"]["locked"] = ""
    before = fd.serialize_film(doc)
    with pytest.raises(fd.FilmError, match="锁定轨道"):
        fd.apply_ops(doc, [{"op": "ripple_delete", "clip": "clip-b", "scope": "all"}])
    assert fd.serialize_film(doc) == before


def test_film_studio_is_fullscreen_and_ai_dock_shares_inspector_column():
    root = Path(__file__).resolve().parents[1]
    index = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    script = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    styles = (root / "webui" / "static" / "css" / "film-studio.css").read_text(encoding="utf-8")

    assert 'id="fs-workspace-dock"' in index
    assert 'id="fs-side-inspector"' in index
    assert 'id="fs-side-ai"' in index
    assert 'data-film-action="show-ai"' in index
    assert 'data-film-action="show-inspector"' in index
    assert "fs-studio-expanded" in script
    assert "filmStudioSetSideMode" in script
    assert "position:fixed;inset:0" in styles
    assert "fs-timeline-pane" in styles
    # Exiting full-screen must not enable the obsolete CompositionIR editor for film.html projects.
    assert "var migrated=!!(S.data&&S.data.exists)" in script


def test_migration_warns_and_never_overwrites_existing_film():
    root=Path(__file__).resolve().parents[1]
    server=(root/"webui"/"server.py").read_text(encoding="utf-8")
    module=(root/"video_director"/"film_document.py").read_text(encoding="utf-8")
    assert "MIGRATION_WARNINGS_REQUIRE_CONFIRMATION" in server
    assert "FILM_ALREADY_INITIALIZED" in module
    assert "document_revision" in module


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
    assert "message.rev!==expectedRuntimeRev" in studio
    assert "hasRuntimeData" in studio


def test_preview_and_final_contract_follows_per_project_authority():
    root = Path(__file__).resolve().parents[1]
    contract = (root / "CONTRACT.md").read_text(encoding="utf-8")
    assert "未迁移旧项目共享 CompositionIR revision" in contract
    assert "film.html 活动项目共享 film.html revision" in contract
    assert "Preview 与 Final 必须共享 Composition IR。" not in contract


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


