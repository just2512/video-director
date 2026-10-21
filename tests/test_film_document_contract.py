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


INSERT_SAMPLE = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"><style>body{margin:0}</style></head>
<body>
<section id="video-main" data-label="主画面">
<video id="video-before" src="../assets/a.mp4#t=0,4" at="0"></video>
<video id="video-later" src="../assets/c.mp4#t=0,2" at="4"></video>
</section>
<section id="audio-bed" data-label="BGM" sync-lock>
<audio id="audio-before" src="../assets/music.wav#t=0,3" at="0"></audio>
<audio id="audio-later" src="../assets/other.wav#t=0,4" at="3"></audio>
</section>
</body></html>'''


def test_insert_ripple_splits_crossing_clips_and_shifts_sync_locked_tracks():
    doc = fd.parse_film(INSERT_SAMPLE)
    inserted, normalized = fd.apply_ops(doc, [{
        "op": "insert_ripple", "track_id": "video-main", "at": 2,
        "clip": {"id": "inserted", "tag": "video", "attrs": {"src": "../assets/new.mp4#t=0,1", "at": "0"}},
    }])
    tracks = {track["id"]: track for track in inserted["tracks"]}
    video = {clip["id"]: clip for clip in tracks["video-main"]["clips"]}
    audio = {clip["id"]: clip for clip in tracks["audio-bed"]["clips"]}
    assert video["video-before"]["attrs"]["src"].endswith("#t=0,2")
    right_video_id = normalized[0]["split_ids"]["video-before"]
    assert video[right_video_id]["attrs"]["src"].endswith("#t=2,4")
    assert float(video[right_video_id]["attrs"]["at"]) == pytest.approx(3)
    assert float(video["inserted"]["attrs"]["at"]) == pytest.approx(2)
    assert float(video["video-later"]["attrs"]["at"]) == pytest.approx(5)
    right_audio_id = normalized[0]["split_ids"]["audio-before"]
    assert audio["audio-before"]["attrs"]["src"].endswith("#t=0,2")
    assert audio[right_audio_id]["attrs"]["src"].endswith("#t=2,3")
    assert float(audio[right_audio_id]["attrs"]["at"]) == pytest.approx(3)
    assert float(audio["audio-later"]["attrs"]["at"]) == pytest.approx(4)
    assert normalized[0]["affected_tracks"] == ["video-main", "audio-bed"]


def test_insert_ripple_rejects_locked_sync_tracks_unknown_crossing_duration_and_bad_new_clip():
    doc = fd.parse_film(INSERT_SAMPLE)
    locked = fd.copy.deepcopy(doc)
    locked["tracks"][1]["attrs"]["locked"] = ""
    with pytest.raises(fd.FilmError, match="已锁定"):
        fd.apply_ops(locked, [{
            "op": "insert_ripple", "track_id": "video-main", "at": 2,
            "clip": {"id": "new-locked", "tag": "video", "attrs": {"src": "../assets/new.mp4#t=0,1", "at": "0"}},
        }])
    unknown = fd.parse_film(INSERT_SAMPLE.replace(
        '<video id="video-before" src="../assets/a.mp4#t=0,4" at="0"></video>',
        '<video id="video-before" src="../assets/a.mp4" at="0"></video>'
    ))
    with pytest.raises(fd.FilmError, match="时长未知"):
        fd.apply_ops(unknown, [{
            "op": "insert_ripple", "track_id": "video-main", "at": 2,
            "clip": {"id": "new-unknown", "tag": "video", "attrs": {"src": "../assets/new.mp4#t=0,1", "at": "0"}},
        }])
    with pytest.raises(fd.FilmError, match="时长"):
        fd.apply_ops(doc, [{
            "op": "insert_ripple", "track_id": "video-main", "at": 2,
            "clip": {"id": "new-bad", "tag": "video", "attrs": {"src": "../assets/new.mp4", "at": "0"}},
        }])


def test_sync_lock_and_insert_mode_are_exposed_in_the_studio_and_api():
    root = Path(__file__).resolve().parents[1]
    script = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    index = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    server = (root / "webui" / "server.py").read_text(encoding="utf-8")
    module = (root / "video_director" / "film_document.py").read_text(encoding="utf-8")
    assert 'data-track-flag="sync-lock"' in script
    assert 'data-film-action="insert-mode"' in index
    assert "request.placement_mode='ripple'" in script
    assert 'elif kind == "insert_ripple"' in module
    assert 'if placement_mode == "ripple"' in server


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

    root = Path(__file__).resolve().parents[1]
    runtime = (root / "webui" / "static" / "js" / "film-runtime.js").read_text(encoding="utf-8")
    react_inspector = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    assert "clipFadeAt(entry,t)" in runtime
    assert "baseVolume*volumeEnvelopeAt(entry.volumeEnvelope,localTime)*fadeFactor" in runtime
    assert 'field:"fade"' in react_inspector
    assert "淡入（秒）" in react_inspector and "淡出（秒）" in react_inspector


def test_split_partitions_fades_without_restarting_them():
    source = SAMPLE.replace('speed="2" volume="1"', 'speed="2" volume="1" fade="0.5,0.75"')
    doc = fd.parse_film(source)
    changed, _ = fd.apply_ops(doc, [{"op": "split", "clip": "clip-a", "at": 1.0, "new_id": "clip-a-right"}])
    clips = {clip["id"]: clip for track in changed["tracks"] for clip in track["clips"]}
    assert clips["clip-a"]["attrs"]["fade"] == "0.5,0"
    assert clips["clip-a-right"]["attrs"]["fade"] == "0,0.75"
    for unsafe_at in (0.2, 4.5):
        with pytest.raises(fd.FilmError, match="切点落在淡入或淡出区间"):
            fd.apply_ops(doc, [{"op": "split", "clip": "clip-a", "at": unsafe_at, "new_id": "unsafe-right"}])


def test_trim_preserves_only_outer_fade_and_rejects_cutting_through_fade():
    source = SAMPLE.replace('speed="2" volume="1"', 'speed="2" volume="1" fade="0.5,0.75"')
    doc = fd.parse_film(source)
    trimmed_start, _ = fd.apply_ops(doc, [{"op": "trim", "clip": "clip-a", "edge": "start", "at": 1.0}])
    clip = next(c for t in trimmed_start["tracks"] for c in t["clips"] if c["id"] == "clip-a")
    assert clip["attrs"]["fade"] == "0,0.75"
    trimmed_end, _ = fd.apply_ops(doc, [{"op": "trim", "clip": "clip-a", "edge": "end", "end": 3.0}])
    clip = next(c for t in trimmed_end["tracks"] for c in t["clips"] if c["id"] == "clip-a")
    assert clip["attrs"]["fade"] == "0.5,0"
    with pytest.raises(fd.FilmError, match="切入了淡入"):
        fd.apply_ops(doc, [{"op": "trim", "clip": "clip-a", "edge": "start", "at": 0.2}])
    with pytest.raises(fd.FilmError, match="切入了淡出"):
        fd.apply_ops(doc, [{"op": "trim", "clip": "clip-a", "edge": "end", "end": 4.5}])


def test_insert_ripple_and_ripple_delete_partition_fades_on_retained_edges():
    inserted_source = INSERT_SAMPLE.replace(
        'src="../assets/a.mp4#t=0,4" at="0"',
        'src="../assets/a.mp4#t=0,4" at="0" fade="0.5,0.5"',
    )
    inserted_doc = fd.parse_film(inserted_source)
    inserted, normalized = fd.apply_ops(inserted_doc, [{
        "op": "insert_ripple", "track_id": "video-main", "at": 2,
        "clip": {"id": "new-fade-insert", "tag": "video", "attrs": {"src": "../assets/new.mp4#t=0,1", "at": "0"}},
    }])
    video = {clip["id"]: clip for track in inserted["tracks"] if track["id"] == "video-main" for clip in track["clips"]}
    right_id = normalized[0]["split_ids"]["video-before"]
    assert video["video-before"]["attrs"]["fade"] == "0.5,0"
    assert video[right_id]["attrs"]["fade"] == "0,0.5"

    ripple_source = SAMPLE.replace(
        '<audio id="audio-1" src="../assets/music.wav#t=0,20" at="0" volume="0.2"></audio>',
        '<audio id="audio-1" src="../assets/music.wav#t=0,20" at="0" volume="0.2" fade="0.5,0.5"></audio>',
    )
    ripple_doc = fd.parse_film(ripple_source)
    ripple, normalized = fd.apply_ops(ripple_doc, [{"op": "ripple_delete", "clip": "clip-b", "scope": "all"}])
    audio = {clip["id"]: clip for track in ripple["tracks"] if track["id"] == "audio-bed" for clip in track["clips"]}
    right_id = normalized[0]["split_ids"]["audio-1"]
    assert audio["audio-1"]["attrs"]["fade"] == "0.5,0"
    assert audio[right_id]["attrs"]["fade"] == "0,0.5"


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


SLIDE_SAMPLE = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"><style>body{margin:0}</style></head>
<body><section id="video-main" data-label="主画面">
<video id="slide-left" src="../assets/a.mp4#t=0,2" at="0" speed="1" volume="1"></video>
<video id="slide-middle" src="../assets/a.mp4#t=2,5" at="2" speed="1" volume="1"></video>
<video id="slide-right" src="../assets/a.mp4#t=5,9" at="5" speed="1" volume="1"></video>
</section></body></html>'''


def test_slide_trim_moves_middle_and_keeps_outer_timeline_bounds():
    doc = fd.parse_film(SLIDE_SAMPLE)
    slid, operations = fd.apply_ops(doc, [{"op": "slide", "clip": "slide-middle", "delta": 0.5}])
    clips = {clip["id"]: clip for track in slid["tracks"] for clip in track["clips"]}
    left, middle, right = clips["slide-left"], clips["slide-middle"], clips["slide-right"]
    assert left["attrs"]["src"].endswith("#t=0,2.5")
    assert left["attrs"]["at"] == "0"
    assert middle["attrs"]["src"].endswith("#t=2,5")
    assert float(middle["attrs"]["at"]) == pytest.approx(2.5)
    assert right["attrs"]["src"].endswith("#t=5.5,9")
    assert float(right["attrs"]["at"]) == pytest.approx(5.5)
    assert fd.clip_end(right) == pytest.approx(9)
    assert fd.clip_end(left) == pytest.approx(float(middle["attrs"]["at"]))
    assert fd.clip_end(middle) == pytest.approx(float(right["attrs"]["at"]))
    assert operations == [{"op": "slide", "clip": "slide-middle", "delta": "0.5"}]


def test_slide_trim_negative_delta_extends_right_head_and_preserves_total_duration():
    doc = fd.parse_film(SLIDE_SAMPLE)
    slid, _ = fd.apply_ops(doc, [{"op": "slide", "clip": "slide-middle", "delta": -0.5}])
    clips = {clip["id"]: clip for track in slid["tracks"] for clip in track["clips"]}
    assert clips["slide-left"]["attrs"]["src"].endswith("#t=0,1.5")
    assert float(clips["slide-middle"]["attrs"]["at"]) == pytest.approx(1.5)
    assert clips["slide-right"]["attrs"]["src"].endswith("#t=4.5,9")
    assert float(clips["slide-right"]["attrs"]["at"]) == pytest.approx(4.5)
    assert fd.clip_end(clips["slide-right"]) == pytest.approx(9)


def test_slide_trim_rejects_discontinuous_sources_fades_locks_and_bounds():
    doc = fd.parse_film(SLIDE_SAMPLE)
    mismatch = fd.copy.deepcopy(doc)
    next(c for t in mismatch["tracks"] for c in t["clips"] if c["id"] == "slide-right")["attrs"]["src"] = "../assets/b.mp4#t=5,9"
    with pytest.raises(fd.FilmError, match="同源连续"):
        fd.apply_ops(mismatch, [{"op": "slide", "clip": "slide-middle", "delta": 0.2}])
    faded = fd.copy.deepcopy(doc)
    next(c for t in faded["tracks"] for c in t["clips"] if c["id"] == "slide-middle")["attrs"]["fade"] = "0.1,0.1"
    with pytest.raises(fd.FilmError, match="淡入淡出"):
        fd.apply_ops(faded, [{"op": "slide", "clip": "slide-middle", "delta": 0.2}])
    locked = fd.copy.deepcopy(doc)
    locked["tracks"][0]["attrs"]["locked"] = ""
    with pytest.raises(fd.FilmError, match="已锁定"):
        fd.apply_ops(locked, [{"op": "slide", "clip": "slide-middle", "delta": 0.2}])
    with pytest.raises(fd.FilmError, match="剩余时长"):
        fd.apply_ops(doc, [{"op": "slide", "clip": "slide-middle", "delta": 3}])


def test_slide_trim_alt_shift_drag_and_ai_contract_are_wired():
    root = Path(__file__).resolve().parents[1]
    script = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    index = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    module = (root / "video_director" / "film_document.py").read_text(encoding="utf-8")
    assert "window.filmStudioToggleSlideMode" in script
    assert "slideGesture=!!(!trim&&(S.slideMode||(e.altKey&&e.shiftKey))" in script
    assert 'data-film-action="slide-mode"' in index
    assert ".fs-btn.fs-slide-mode-active" in (root / "webui" / "static" / "css" / "film-studio.css").read_text(encoding="utf-8")
    assert "e.altKey&&e.shiftKey" in script
    assert "data-slide-delta" in script
    assert "if(d.mode==='move'){\n      var target=document.elementFromPoint" in script
    assert 'elif kind == "slide"' in module
    assert "Alt+Shift 拖动滑移式修剪" in index
    assert "可用 slide 操作" in module



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


def test_slip_write_path_probes_actual_media_duration(monkeypatch):
    from video_director import prober

    doc = fd.parse_film(SAMPLE)
    changed, operations = fd.apply_ops(doc, [{"op": "slip", "clip": "clip-a", "delta_source": 1.25}])
    monkeypatch.setattr(fd, "media_path_for_src", lambda project_id, src: Path("validated-media.mp4"))
    monkeypatch.setattr(prober, "probe_media", lambda path: prober.ProbeResult(
        duration=12.5, has_video_stream=True, has_audio_stream=True,
    ))
    with pytest.raises(fd.FilmError, match="超出素材时长"):
        fd._validate_slip_source_bounds("project", changed, operations)

    monkeypatch.setattr(prober, "probe_media", lambda path: prober.ProbeResult(
        duration=13.5, has_video_stream=True, has_audio_stream=True,
    ))
    fd._validate_slip_source_bounds("project", changed, operations)


def test_slip_write_path_rejects_unknown_media_duration(monkeypatch):
    from video_director import prober

    doc = fd.parse_film(SAMPLE)
    changed, operations = fd.apply_ops(doc, [{"op": "slip", "clip": "clip-a", "delta_source": 1.25}])
    monkeypatch.setattr(fd, "media_path_for_src", lambda project_id, src: Path("validated-media.mp4"))
    monkeypatch.setattr(prober, "probe_media", lambda path: prober.ProbeResult(
        duration=None, has_video_stream=True, has_audio_stream=True,
    ))
    with pytest.raises(fd.FilmError, match="无法确认.*真实素材时长"):
        fd._validate_slip_source_bounds("project", changed, operations)


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


def test_split_hotkey_is_global_only_outside_text_entry():
    root = Path(__file__).resolve().parents[1]
    script = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    index = (root / "webui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "!e.ctrlKey&&!e.metaKey&&!e.altKey&&!e.shiftKey&&!e.repeat&&String(e.key).toLowerCase()==='s'" in script
    assert "e.preventDefault();window.filmStudioSplit();" in script
    assert "target.matches('input,textarea,select,[contenteditable=\"true\"]')" in script
    assert 'title="在播放头切分（S）"' in index


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



HTML_SCENE_SAMPLE = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"></head>
<body>
<section id="scene-track" data-label="网页镜头">
<iframe id="scene" src="scenes/title.html#t=0,4" at="0"></iframe>
</section>
</body></html>'''


def test_element_set_merges_selector_override_without_erasing_existing_entries():
    doc = fd.parse_film(HTML_SCENE_SAMPLE)
    doc["tracks"][0]["clips"][0]["attrs"]["overrides"] = json.dumps([
        {"selector": "#title", "text": "旧标题", "style": {"color": "#ffffff", "fontSize": "24px"}},
        {"selector": ".badge", "text": "保留"},
    ], ensure_ascii=False)
    updated, normalized = fd.apply_ops(doc, [{
        "op": "element_set", "clip": "scene", "selector": "#title",
        "text": "新标题", "style": {"color": "#ff0000", "left": "30px", "rotate": "12deg"},
    }])
    clip = updated["tracks"][0]["clips"][0]
    overrides = json.loads(clip["attrs"]["overrides"])
    title = next(item for item in overrides if item.get("selector") == "#title")
    assert title["text"] == "新标题"
    assert title["style"] == {"color": "#ff0000", "fontSize": "24px", "left": "30px", "rotate": "12deg"}
    assert any(item.get("selector") == ".badge" for item in overrides)
    assert normalized[0]["op"] == "element_set"
    assert normalized[0]["clip"] == "scene"


def test_element_set_rejects_non_html_target_and_unsafe_style_values():
    doc = fd.parse_film(SAMPLE)
    with pytest.raises(fd.FilmError, match="HTML 镜头"):
        fd.apply_ops(doc, [{
            "op": "element_set", "clip": "clip-a", "selector": "#title",
            "style": {"color": "#ff0000"},
        }])
    doc = fd.parse_film(HTML_SCENE_SAMPLE)
    with pytest.raises(fd.FilmError, match="不允许|unsafe|UNSAFE"):
        fd.apply_ops(doc, [{
            "op": "element_set", "clip": "scene", "selector": "#title",
            "style": {"backgroundColor": "url(javascript:alert(1))"},
        }])
    with pytest.raises(fd.FilmError, match="CSS selector"):
        fd.apply_ops(doc, [{
            "op": "element_set", "clip": "scene", "selector": "  ",
            "style": {"color": "#ff0000"},
        }])
    with pytest.raises(fd.FilmError, match="CSS 属性 width.*0 到 10000"):
        fd.apply_ops(doc, [{
            "op": "element_set", "clip": "scene", "selector": "#title",
            "style": {"width": "-12px"},
        }])
    with pytest.raises(fd.FilmError, match="画布字号必须在 0 到 1000"):
        fd.apply_ops(doc, [{
            "op": "element_set", "clip": "scene", "selector": "#title",
            "style": {"fontSize": "1001px"},
        }])


def test_ai_planner_can_only_modify_the_explicitly_selected_html_element():
    class FakeLLM:
        def __init__(self, output):
            self.output = output
            self.prompt = ""

        def generate(self, prompt):
            self.prompt = prompt
            return self.output

    state = {"exists": True, "doc": fd.parse_film(HTML_SCENE_SAMPLE)}
    selected = {
        "clip_id": "scene", "selector": "#title", "tag": "h1",
        "text": "Original", "textEditable": True,
        "styles": {"color": "rgb(255, 255, 255)"},
        "rect": {"x": 10, "y": 20, "width": 300, "height": 60},
    }
    llm = FakeLLM(json.dumps({
        "operations": [{
            "op": "element_set", "clip": "scene", "selector": "#title",
            "text": "Updated", "style": {"color": "#ff0000", "fontSize": "32px"},
        }],
        "summary": "修改当前标题",
    }, ensure_ascii=False))
    operations = fd.plan_film_command(
        llm, state, "把这个标题改成红色并增大字号", "scene",
        selected_element=selected,
    )
    assert operations[0]["op"] == "element_set"
    assert '"selector":"#title"' in llm.prompt
    assert "不得使用 element_set" not in llm.prompt

    wrong_target = FakeLLM(json.dumps({
        "operations": [{"op": "element_set", "clip": "scene", "selector": ".other",
                        "style": {"color": "#ff0000"}}],
        "summary": "wrong selector",
    }))
    with pytest.raises(fd.FilmError, match="当前选中的 HTML 镜头"):
        fd.plan_film_command(wrong_target, state, "改颜色", "scene", selected_element=selected)


def test_ai_planner_refuses_whole_text_replacement_for_nested_selected_element():
    class FakeLLM:
        def generate(self, _prompt):
            return json.dumps({
                "operations": [{"op": "element_set", "clip": "scene", "selector": "#title",
                                "text": "replace", "style": {"color": "#ff0000"}}],
                "summary": "nested",
            })

    state = {"exists": True, "doc": fd.parse_film(HTML_SCENE_SAMPLE)}
    selected = {"clip_id": "scene", "selector": "#title", "textEditable": False}
    with pytest.raises(fd.FilmError, match="包含子元素"):
        fd.plan_film_command(FakeLLM(), state, "改标题", "scene", selected_element=selected)



def test_canvas_inspector_submits_the_validated_element_set_operation():
    root = Path(__file__).resolve().parents[1]
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    document = (root / "video_director" / "film_document.py").read_text(encoding="utf-8")
    assert "var elementOp={op:'element_set',clip:clip.id,selector:selected.selector,style:elementStyle}" in studio
    assert "await filmStudioCommit([elementOp]);" in studio
    assert 'if kind == "element_set":' in document
    assert "field:'overrides',value:JSON.stringify(overrides)" not in studio

"""The FilmDocument keeps FPS authoritative across parse/serialize operations."""
from __future__ import annotations

import pytest

from video_director import config, film_document as fd


def test_film_fps_metadata_round_trip_and_range_validation():
    source = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=320,height=180">'
        '</head><body><section id="video">'
        '<video id="clip" src="assets/video.mp4#t=0,1" at="0"></video>'
        '</section></body></html>'
    )
    legacy = fd.parse_film(source)
    assert legacy["fps"] == config.DEFAULT_FPS
    legacy["fps"] = 24
    serialized = fd.serialize_film(legacy)
    assert '<meta name="film-fps" content="24">' in serialized
    assert fd.parse_film(serialized)["fps"] == 24
    assert fd.serialize_film(fd.parse_film(serialized)) == serialized

    invalid = dict(legacy)
    invalid["fps"] = 61
    with pytest.raises(fd.FilmError, match="帧率必须是 1–60 的整数"):
        fd.serialize_film(invalid)

    malformed = serialized.replace('content="24"', 'content="0"')
    with pytest.raises(fd.FilmError, match="film-fps=0 超出 1–60 范围"):
        fd.parse_film(malformed)

    decimal_fps = serialized.replace('content="24"', 'content="24.5"')
    with pytest.raises(fd.FilmError, match="film-fps 元数据必须是 1–60 的整数"):
        fd.parse_film(decimal_fps)

    text_fps = serialized.replace('content="24"', 'content="24fps"')
    with pytest.raises(fd.FilmError, match="film-fps 元数据必须是 1–60 的整数"):
        fd.parse_film(text_fps)


def test_parse_rejects_unknown_clip_attribute_and_reports_line_number():
    source = '''<!doctype html>
<html><head></head>
<body>
<section id="video">
  <video id="clip" src="assets/video.mp4#t=0,2" at="0" speeed="1"></video>
</section>
</body></html>'''
    with pytest.raises(fd.FilmError) as error:
        fd.parse_film(source)
    assert error.value.code == "INVALID_FILM"
    assert "第 5 行" in str(error.value)
    assert "未知属性 'speeed'" in str(error.value)


@pytest.mark.parametrize(
    "style, expected",
    [
        ("position:absolute;left:0px;top:0px;width:100px;height:100px", "CSS 属性 'position'"),
        ("transform:scale(2);left:0px;top:0px;width:100px;height:100px", "CSS 属性 'transform'"),
        ("margin:10px;left:0px;top:0px;width:100px;height:100px", "CSS 属性 'margin'"),
        ("left:calc(100% - 2px);top:0px;width:100px;height:100px", "必须使用 px 数值"),
    ],
)
def test_parse_rejects_unsupported_clip_layout_css(style, expected):
    source = (
        '<!doctype html><html><head></head><body>'
        '<section id="video"><video id="clip" src="assets/video.mp4#t=0,2" at="0" '
        + 'style="' + style + '"></video></section></body></html>'
    )
    with pytest.raises(fd.FilmError) as error:
        fd.parse_film(source)
    assert expected in str(error.value)


def test_parse_rejects_unknown_track_attribute_with_line_number():
    source = '''<!doctype html>
<html><head></head>
<body>
<section id="video" data-lable="typo">
  <video id="clip" src="assets/video.mp4#t=0,2" at="0"></video>
</section>
</body></html>'''
    with pytest.raises(fd.FilmError) as error:
        fd.parse_film(source)
    assert "第 4 行" in str(error.value)
    assert "未知属性 'data-lable'" in str(error.value)


def test_parse_rejects_stray_tracks_and_media_outside_body_with_line_numbers():
    sources = [
        (
            '<!doctype html>\n<html><head></head>\n'
            '<section id="orphan"><video id="clip" src="assets/video.mp4#t=0,1" at="0"></video></section>\n'
            '<body></body></html>',
            "第 3 行",
            "section",
        ),
        (
            '<!doctype html><html><head></head><body></body>\n'
            '<video id="orphan" src="assets/video.mp4#t=0,1" at="0"></video></html>',
            "第 2 行",
            "video",
        ),
    ]
    for source, line_text, tag in sources:
        with pytest.raises(fd.FilmError) as error:
            fd.parse_film(source)
        assert line_text in str(error.value)
        assert f"<{tag}>" in str(error.value)


def test_parse_rejects_unknown_head_element_and_typo_in_meta_attribute():
    unknown_tag = '<!doctype html><html><head>\n<div>invalid head node</div></head><body><section id="v"><video id="c" src="assets/v.mp4#t=0,1"></video></section></body></html>'
    with pytest.raises(fd.FilmError) as tag_error:
        fd.parse_film(unknown_tag)
    assert "head/html 结构之外" in str(tag_error.value)

    typo_attr = '<!doctype html><html><head><meta charsett="utf-8"></head><body><section id="v"><video id="c" src="assets/v.mp4#t=0,1"></video></section></body></html>'
    with pytest.raises(fd.FilmError) as attr_error:
        fd.parse_film(typo_attr)
    assert "未知属性 'charsett'" in str(attr_error.value)


@pytest.mark.parametrize(
    "style, expected",
    [
        ("transform-origin:25% 25%;left:0px;top:0px;width:100px;height:100px", "0/50/100%"),
        ("z-index:100001;left:0px;top:0px;width:100px;height:100px", "100000 范围"),
        ("clip-path:polygon(url(https://example.invalid/x));left:0px;top:0px;width:100px;height:100px", "Runtime 的"),
    ],
)
def test_parse_rejects_css_values_runtime_would_ignore(style, expected):
    source = (
        '<!doctype html><html><head></head><body>'
        '<section id="video"><video id="clip" src="assets/video.mp4#t=0,2" at="0" '
        + 'style="' + style + '"></video></section></body></html>'
    )
    with pytest.raises(fd.FilmError) as error:
        fd.parse_film(source)
    assert expected in str(error.value)


@pytest.mark.parametrize("tag", ["video", "audio", "img", "iframe"])
def test_parse_requires_explicit_source_range_for_every_clip_type(tag):
    element = (
        f'<{tag} id="clip" src="assets/source.mp4" at="0"></{tag}>'
        if tag != "img"
        else '<img id="clip" src="assets/source.png" at="0">'
    )
    source = '<!doctype html><html><head></head><body><section id="track">' + element + '</section></body></html>'
    with pytest.raises(fd.FilmError, match=r"#t=in,out"):
        fd.parse_film(source)


def test_set_src_rejects_video_or_audio_without_explicit_source_out():
    doc = fd.parse_film(SAMPLE)
    with pytest.raises(fd.FilmError, match=r"#t=in,out"):
        fd.apply_ops(doc, [{"op": "set", "clip": "clip-a", "field": "src", "value": "../assets/new.mp4"}])


def test_parse_accepts_migration_provenance_attributes_on_track_sections():
    source = '''<!doctype html>
<html><head></head><body>
<section id="overlay-track" data-label="叠加层"
         data-source-track-id="legacy-video" data-source-track-order="2"
         data-source-track-hidden="true" data-source-style-id="style-a"
         data-source-style-contract="{&quot;version&quot;:1}">
  <video id="overlay" src="assets/overlay.mp4#t=0,1" at="0"></video>
</section>
</body></html>'''
    document = fd.parse_film(source)
    track = document["tracks"][0]
    assert track["attrs"]["data-source-track-id"] == "legacy-video"
    assert track["attrs"]["data-source-style-id"] == "style-a"


def test_parse_accepts_safe_inline_border_radius_and_runtime_media_style():
    source = (
        '<!doctype html><html><head></head><body><section id="video">'
        '<video id="clip" src="assets/video.mp4#t=0,2" at="0" '
        'style="left:0px;top:0px;width:100px;height:100px;'
        'object-fit:cover;border-radius:24px 12px"></video>'
        '</section></body></html>'
    )
    parsed = fd.parse_film(source)
    assert "border-radius:24px 12px" in parsed["tracks"][0]["clips"][0]["attrs"]["style"]


def test_parse_accepts_migrated_overlay_track_z_index_but_rejects_runtime_ignored_clip_z_index():
    track_source = (
        '<!doctype html><html><head></head><body>'
        '<section id="overlay" data-render-z-index="100001" style="z-index:100001">'
        '<video id="clip" src="assets/video.mp4#t=0,1" at="0"></video>'
        '</section></body></html>'
    )
    parsed = fd.parse_film(track_source)
    assert parsed["tracks"][0]["attrs"]["style"] == "z-index:100001"

    clip_source = (
        '<!doctype html><html><head></head><body>'
        '<section id="track"><video id="clip" src="assets/video.mp4#t=0,1" at="0" '
        'style="left:0px;top:0px;width:100px;height:100px;z-index:100001"></video>'
        '</section></body></html>'
    )
    with pytest.raises(fd.FilmError, match="100000"):
        fd.parse_film(clip_source)


@pytest.mark.parametrize(
    "bad_head, expected",
    [
        ('<html><head><head></head></head><body><section id="t"></section></body></html>', "head 必须是 html 的直接子元素且不能嵌套"),
        ('<html><head><title><meta charset="utf-8"></title></head><body><section id="t"></section></body></html>', "meta 必须是 head 的直接子元素"),
        ('<html><body><head></head></body></html>', "body 中不允许 head"),
    ],
)
def test_parse_rejects_nested_or_misplaced_head_elements(bad_head, expected):
    source = '<!doctype html>' + bad_head
    with pytest.raises(fd.FilmError) as error:
        fd.parse_film(source)
    assert expected in str(error.value)


def test_parse_serialize_preserves_comments_outside_and_between_film_nodes():
    source = '''<!doctype html>
<!-- wrapper note -->
<html><head><meta charset="utf-8"><!-- retained head note --></head>
<body>
<!-- before first track -->
<section id="visual" data-label="画面">
<!-- before first clip -->
<video id="first" src="assets/first.mp4#t=0,1" at="0"></video>
<!-- between clips: don't delete this editor note -->
<video id="second" src="assets/second.mp4#t=0,1" at="1"></video>
<!-- after last clip -->
</section>
<!-- between tracks -->
<section id="audio" data-label="音轨">
<audio id="voice" src="assets/voice.wav#t=0,1" at="0"></audio>
</section>
<!-- after last track -->
</body></html>'''
    parsed = fd.parse_film(source)
    serialized = fd.serialize_film(parsed)
    for comment in (
        "<!-- wrapper note -->",
        "<!-- retained head note -->",
        "<!-- before first track -->",
        "<!-- before first clip -->",
        "<!-- between clips: don't delete this editor note -->",
        "<!-- after last clip -->",
        "<!-- between tracks -->",
        "<!-- after last track -->",
    ):
        assert serialized.count(comment) == 1, f"lost or duplicated comment: {comment}"
    assert serialized.index('id="first"') < serialized.index("<!-- between clips") < serialized.index('id="second"')
    assert serialized.index("</section>") < serialized.index("<!-- between tracks") < serialized.index('id="audio"')
    assert fd.serialize_film(fd.parse_film(serialized)) == serialized


@pytest.mark.parametrize(
    "body_text",
    [
        "text before a track",
        "text after a track",
        "<section id=\"track\">text beside media</section>",
    ],
)
def test_parse_rejects_bare_text_nodes_that_would_be_lost_on_save(body_text):
    if body_text.startswith("<section"):
        inner = body_text
    else:
        inner = '<section id="track"><video id="clip" src="assets/video.mp4#t=0,1" at="0"></video></section>' + body_text
    source = '<!doctype html><html><head></head><body>' + inner + '</body></html>'
    with pytest.raises(fd.FilmError, match="裸文本节点"):
        fd.parse_film(source)


def test_parse_rejects_comments_inside_media_clip_instead_of_dropping_them():
    source = (
        '<!doctype html><html><head></head><body><section id="track">'
        '<video id="clip" src="assets/video.mp4#t=0,1" at="0"><!-- clip note --></video>'
        '</section></body></html>'
    )
    with pytest.raises(fd.FilmError, match="媒体片段内部不允许注释"):
        fd.parse_film(source)


def test_unrelated_comments_survive_authoritative_film_edit():
    source = '''<!doctype html>
<!-- keep wrapper note -->
<html><head><!-- keep head note --></head><body>
<section id="visual">
<!-- keep edit note -->
<video id="first" src="assets/first.mp4#t=0,1" at="0"></video>
<!-- keep between-clip note -->
<video id="second" src="assets/second.mp4#t=0,1" at="1"></video>
</section>
<!-- keep between-track note -->
</body></html>'''
    parsed = fd.parse_film(source)
    edited, operations = fd.apply_ops(parsed, [
        {"op": "set", "clip": "first", "field": "title", "value": "Updated title"},
    ])
    assert len(operations) == 1
    serialized = fd.serialize_film(edited)
    assert 'title="Updated title"' in serialized
    for comment in (
        "<!-- keep wrapper note -->",
        "<!-- keep head note -->",
        "<!-- keep edit note -->",
        "<!-- keep between-clip note -->",
        "<!-- keep between-track note -->",
    ):
        assert serialized.count(comment) == 1, f"comment lost during film edit: {comment}"
    assert fd.serialize_film(fd.parse_film(serialized)) == serialized


def test_corrupt_non_utf8_history_is_reported_as_history_corrupt(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "history-invalid-utf8"
    project = fd.config.project_dir(project_id)
    project.mkdir(parents=True)
    history_path = fd._history_path(project_id)
    history_path.write_bytes(b"{\xff}")
    with pytest.raises(fd.FilmError) as error:
        fd._history_read(project_id)
    assert error.value.code == "HISTORY_CORRUPT"
    assert "历史文件损坏" in str(error.value)


@pytest.mark.parametrize(
    "source, expected",
    [
        (
            '<!doctype html><html><head></head><head><title>Lost</title></head>'
            '<body><section id="track"></section></body></html>',
            "存在多个 head 元素",
        ),
        (
            '<!doctype html><html><head></head><body><section id="track"></section></body>',
            "html 根元素不完整",
        ),
    ],
)
def test_parse_rejects_duplicate_head_or_incomplete_html_root(source, expected):
    with pytest.raises(fd.FilmError) as error:
        fd.parse_film(source)
    assert expected in str(error.value)
