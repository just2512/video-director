"""Contracts for the OpenFilm-inspired film document editor."""
from __future__ import annotations

import concurrent.futures
import importlib
import json
import re
import urllib.error
import urllib.request
import subprocess
import threading

from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from video_director import film_document as fd, renderer


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


def test_asset_insert_and_overwrite_round_trip_in_canonical_timeline_order():
    doc = fd.parse_film(INSERT_SAMPLE)

    ripple, _ = fd.apply_ops(doc, [{
        "op": "insert_ripple", "track_id": "video-main", "at": 2,
        "clip": {
            "id": "ripple-roundtrip", "tag": "video",
            "attrs": {"src": "../assets/new.mp4#t=0,1", "at": "2"},
        },
    }])
    ripple_roundtrip = fd.parse_film(fd.serialize_film(ripple))
    assert fd._film_document_roundtrip_matches(ripple, ripple_roundtrip)
    ripple_clips = next(
        track for track in ripple["tracks"] if track["id"] == "video-main"
    )["clips"]
    ripple_ids = [clip["id"] for clip in ripple_clips]
    assert ripple_ids == [
        clip["id"] for clip in sorted(
            ripple_clips, key=lambda clip: float(clip["attrs"].get("at", 0))
        )
    ]
    assert ripple_ids.index("video-before") < ripple_ids.index("ripple-roundtrip")
    assert ripple_ids.index("ripple-roundtrip") < ripple_ids.index("video-later")

    overwrite_clip = {
        "id": "overwrite-roundtrip", "tag": "video",
        "attrs": {"src": "../assets/new.mp4#t=0,1", "at": "2"},
    }
    overwrite_ops = fd.build_overwrite_operations(doc, "video-main", overwrite_clip, 2)
    overwritten, _ = fd.apply_ops(doc, overwrite_ops, allow_source_extension=True)
    overwrite_roundtrip = fd.parse_film(fd.serialize_film(overwritten))
    assert fd._film_document_roundtrip_matches(overwritten, overwrite_roundtrip)
    overwrite_clips = next(
        track for track in overwritten["tracks"] if track["id"] == "video-main"
    )["clips"]
    overwrite_ids = [clip["id"] for clip in overwrite_clips]
    assert overwrite_ids == [
        clip["id"] for clip in sorted(
            overwrite_clips, key=lambda clip: float(clip["attrs"].get("at", 0))
        )
    ]
    assert overwrite_ids.index("video-before") < overwrite_ids.index("overwrite-roundtrip")
    assert overwrite_ids.index("overwrite-roundtrip") < overwrite_ids.index("video-later")


def test_insert_ripple_rejects_locked_sync_tracks_unknown_crossing_duration_and_bad_new_clip():
    doc = fd.parse_film(INSERT_SAMPLE)
    locked = fd.copy.deepcopy(doc)
    locked["tracks"][1]["attrs"]["locked"] = ""
    with pytest.raises(fd.FilmError, match="已锁定"):
        fd.apply_ops(locked, [{
            "op": "insert_ripple", "track_id": "video-main", "at": 2,
            "clip": {"id": "new-locked", "tag": "video", "attrs": {"src": "../assets/new.mp4#t=0,1", "at": "0"}},
        }])
    unknown_source = INSERT_SAMPLE.replace(
        '<video id="video-before" src="../assets/a.mp4#t=0,4" at="0"></video>',
        '<video id="video-before" src="../assets/a.mp4" at="0"></video>'
    )
    # Unknown-duration clips fail document validation before insert-ripple can run.
    with pytest.raises(fd.FilmError, match="必须使用 #t=in,out"):
        fd.parse_film(unknown_source)
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
    assert "request.placement_mode=S.overwriteMode?'overwrite':'ripple'" in script
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


def test_set_data_film_keyframes_roundtrips_and_rejects_invalid_channels():
    doc = fd.parse_film(HTML_SCENE_SAMPLE)
    channels = [
        {"property": "opacity", "keyframes": [
            {"at": 0, "value": 0.0, "easing": "ease_in"},
            {"at": 1.5, "value": 1.0, "easing": "ease_out"},
        ]},
        {"property": "rotation", "keyframes": [
            {"at": 0, "value": -10.0, "easing": "linear"},
            {"at": 4, "value": 10.0, "easing": "ease_in_out"},
        ]},
    ]
    edited, normalized = fd.apply_ops(doc, [{
        "op": "set", "clip": "scene", "field": "data-film-keyframes", "value": channels,
    }])
    attrs = edited["tracks"][0]["clips"][0]["attrs"]
    assert json.loads(attrs["data-film-keyframes"]) == channels
    assert normalized[0]["field"] == "data-film-keyframes"
    reparsed = fd.parse_film(fd.serialize_film(edited))
    assert reparsed["tracks"][0]["clips"][0]["attrs"]["data-film-keyframes"] == attrs["data-film-keyframes"]

    with pytest.raises(fd.FilmError):
        fd.apply_ops(doc, [{
            "op": "set", "clip": "scene", "field": "data-film-keyframes",
            "value": [{"property": "opacity", "keyframes": [
                {"at": 0, "value": 0}, {"at": 4, "value": 2.0},
            ]}],
        }])


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


def test_track_flag_operations_round_trip_all_supported_flags():
    doc = fd.parse_film(SAMPLE)
    edited, normalized = fd.apply_ops(doc, [
        {"op": "track", "track_id": "video-main", "field": "locked", "value": True},
        {"op": "track", "track_id": "audio-bed", "field": "hidden", "value": True},
        {"op": "track", "track_id": "audio-bed", "field": "muted", "value": True},
        {"op": "track", "track_id": "audio-bed", "field": "sync-lock", "value": True},
    ])
    tracks = {track["id"]: track for track in edited["tracks"]}
    assert "locked" in tracks["video-main"]["attrs"]
    assert all(flag in tracks["audio-bed"]["attrs"] for flag in ("hidden", "muted", "sync-lock"))
    assert [(op["field"], op["value"]) for op in normalized] == [
        ("locked", True), ("hidden", True), ("muted", True), ("sync-lock", True),
    ]

    reparsed = fd.parse_film(fd.serialize_film(edited))
    round_tripped = {track["id"]: track for track in reparsed["tracks"]}
    assert all(flag in round_tripped["audio-bed"]["attrs"] for flag in ("hidden", "muted", "sync-lock"))


def test_track_flag_batch_rejects_invalid_flags_atomically():
    doc = fd.parse_film(SAMPLE)
    before = fd.serialize_film(doc)
    with pytest.raises(fd.FilmError, match="轨道属性只允许"):
        fd.apply_ops(doc, [
            {"op": "track", "track_id": "video-main", "field": "locked", "value": True},
            {"op": "track", "track_id": "audio-bed", "field": "unknown", "value": True},
        ])
    assert fd.serialize_film(doc) == before

    with pytest.raises(fd.FilmError, match="轨道属性只允许"):
        fd.apply_ops(doc, [
            {"op": "track", "track_id": "video-main", "field": "muted", "value": "true"},
        ])
    assert fd.serialize_film(doc) == before


def test_overlap_failure_does_not_mutate_original_document():
    doc=fd.parse_film(SAMPLE);before=fd.serialize_film(doc)
    with pytest.raises(fd.FilmError,match="重叠"):
        fd.apply_ops(doc,[{"op":"set","clip":"clip-b","field":"at","value":4}])
    assert fd.serialize_film(doc)==before




def test_same_track_crossfade_overlap_requires_matching_clip_fades_and_never_allows_three_clips():
    adjacent = SAMPLE.replace(
        'id="clip-b" src="../assets/b.mp4#t=0,5" at="5"',
        'id="clip-b" src="../assets/b.mp4#t=0,5" at="4.5" fade="0.5,0"',
    )
    doc = fd.parse_film(adjacent)
    video = next(track for track in doc["tracks"] if track["id"] == "video-main")
    clips = {clip["id"]: clip for clip in video["clips"]}
    assert float(clips["clip-a"]["attrs"]["at"]) == 0
    assert float(clips["clip-b"]["attrs"]["at"]) == 4.5
    assert fd._parse_fade(clips["clip-a"]["attrs"].get("fade")) == (0.0, 0.0)
    assert fd._parse_fade(clips["clip-b"]["attrs"]["fade"]) == (0.5, 0.0)

    # The same overlap must pass transaction-time validation after an edit.
    source_doc = fd.parse_film(SAMPLE)
    crossed, normalized_ops = fd.apply_ops(source_doc, [
        {"op": "set", "clip": "clip-b", "field": "fade", "value": "0.5,0"},
        {"op": "move", "clip": "clip-b", "track_id": "video-main", "at": 4.5},
    ])
    assert len(normalized_ops) == 2
    crossed_clips = {
        clip["id"]: clip
        for track in crossed["tracks"]
        for clip in track["clips"]
    }
    assert fd._parse_fade(crossed_clips["clip-b"]["attrs"]["fade"]) == (0.5, 0.0)
    fd._validate_doc(crossed)

    mismatched_ops_doc = fd.parse_film(SAMPLE)
    with pytest.raises(fd.FilmError, match="没有匹配的交叉淡化"):
        fd.apply_ops(mismatched_ops_doc, [
            {"op": "set", "clip": "clip-b", "field": "fade", "value": "0.4,0"},
            {"op": "move", "clip": "clip-b", "track_id": "video-main", "at": 4.5},
        ])

    no_fade = adjacent.replace(' at="4.5" fade="0.5,0"', ' at="4.5"')
    with pytest.raises(fd.FilmError, match="片段重叠"):
        fd.parse_film(no_fade)

    mismatched = adjacent.replace('fade="0.5,0"', 'fade="0.4,0"')
    with pytest.raises(fd.FilmError, match="片段重叠"):
        fd.parse_film(mismatched)

    triple = adjacent.replace(
        '</section>\n<section id="audio-bed"',
        '<video id="clip-c" src="../assets/c.mp4#t=0,5" at="4.75" fade="4.75,0"></video>\n</section>\n<section id="audio-bed"',
    )
    with pytest.raises(fd.FilmError, match="三个片段同时重叠"):
        fd.parse_film(triple)


def test_same_track_audio_crossfade_requires_matching_fade_out_and_fade_in():
    audio = INSERT_SAMPLE.replace(
        'id="audio-before" src="../assets/music.wav#t=0,3" at="0"',
        'id="audio-before" src="../assets/music.wav#t=0,3" at="0" fade="0,0.5"',
    ).replace(
        'id="audio-later" src="../assets/other.wav#t=0,4" at="3"',
        'id="audio-later" src="../assets/other.wav#t=0,4" at="2.5" fade="0.5,0"',
    )
    doc = fd.parse_film(audio)
    track = next(item for item in doc["tracks"] if item["id"] == "audio-bed")
    clips = {clip["id"]: clip for clip in track["clips"]}
    assert fd._parse_fade(clips["audio-before"]["attrs"]["fade"]) == (0.0, 0.5)
    assert fd._parse_fade(clips["audio-later"]["attrs"]["fade"]) == (0.5, 0.0)

def test_cross_track_move_overlap_is_rejected_before_mutating_original_document():
    doc = fd.parse_film(SAMPLE)
    doc["tracks"].append({
        "id": "video-secondary",
        "attrs": {"id": "video-secondary", "data-label": "辅助画面"},
        "clips": [{
            "id": "clip-target",
            "tag": "video",
            "attrs": {
                "id": "clip-target",
                "src": "../assets/target.mp4#t=0,2",
                "at": "8",
            },
        }],
    })
    fd._validate_doc(doc)
    before = fd.serialize_film(doc)

    # clip-a spans 0–5 seconds with its 2× playback speed; placing it at 7
    # seconds would overlap the destination's existing 8–10 second clip.
    with pytest.raises(fd.FilmError, match="重叠"):
        fd.apply_ops(doc, [{
            "op": "move",
            "clip": "clip-a",
            "track_id": "video-secondary",
            "at": 7,
        }])
    assert fd.serialize_film(doc) == before


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
    assert '_FILM_PROPOSALS.get(pid, proposal_id, base_revision)' in server
    assert '_FILM_PROPOSALS.discard(pid, proposal_id)' in server
    apply_route = server.split('if route == "/api/editor/film/apply-proposal":', 1)[1].split(
        'except fd.FilmError', 1
    )[0]
    assert 'approved["base_revision"]' in apply_route
    assert 'approved["operations"]' in apply_route
    assert 'require_base=True, source="ai_proposal"' in apply_route
    assert apply_route.index('approved["operations"]') < apply_route.index("require_base=True")
    assert "payload.get(\"ops\")" not in apply_route
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


def test_film_edit_falls_back_when_source_patch_does_not_apply_new_track(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-source-patch-fallback"
    root.mkdir(parents=True)
    film_path = root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")

    original = fd.read_project_film("film-source-patch-fallback")
    # Simulate a source-span patcher that returns syntactically valid but stale
    # HTML. The edit must use the canonical serializer and persist the full model.
    monkeypatch.setattr(fd, "_preserve_film_text", lambda *_args: SAMPLE)

    result = fd.edit_project_film(
        "film-source-patch-fallback",
        original["rev"],
        [
            {"op": "track_add", "track_id": "track-overlay", "label": "叠加画面", "index": 0},
            {"op": "insert", "track_id": "track-overlay", "clip": {
                "id": "new-image", "tag": "img",
                "attrs": {"src": "../assets/still.png#t=0,4", "at": "1", "title": "插入图片"},
            }},
        ],
        require_base=True,
        source="test_source_patch_fallback",
    )

    persisted = fd.read_project_film("film-source-patch-fallback")
    assert result["rev"] != original["rev"]
    assert persisted["rev"] == result["rev"]
    assert persisted["doc"]["tracks"][0]["id"] == "track-overlay"
    assert persisted["doc"]["tracks"][0]["clips"][0]["id"] == "new-image"


def test_undo_redo_rejects_stale_client_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    root = tmp_path / "composer" / "film-history-stale-base"
    root.mkdir(parents=True)
    (root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")

    initial = fd.read_project_film("film-history-stale-base")
    first = fd.edit_project_film(
        "film-history-stale-base",
        initial["rev"],
        [{"op": "set", "clip": "clip-b", "field": "at", "value": 7}],
    )
    second = fd.edit_project_film(
        "film-history-stale-base",
        first["rev"],
        [{"op": "set", "clip": "clip-b", "field": "at", "value": 8}],
    )

    with pytest.raises(fd.FilmError) as stale_undo:
        fd.undo_redo_project_film("film-history-stale-base", first["rev"], "undo")
    assert stale_undo.value.code == "REVISION_CONFLICT"
    assert fd.read_project_film("film-history-stale-base")["rev"] == second["rev"]

    undone = fd.undo_redo_project_film("film-history-stale-base", second["rev"], "undo")
    assert undone["rev"] == first["rev"]

    with pytest.raises(fd.FilmError) as stale_redo:
        fd.undo_redo_project_film("film-history-stale-base", second["rev"], "redo")
    assert stale_redo.value.code == "REVISION_CONFLICT"
    assert fd.read_project_film("film-history-stale-base")["rev"] == first["rev"]

    redone = fd.undo_redo_project_film("film-history-stale-base", first["rev"], "redo")
    assert redone["rev"] == second["rev"]


def test_film_edit_http_route_can_require_exact_revision_without_disabling_manual_rebase(tmp_path, monkeypatch):
    import importlib
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    storage_root = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-edit-exact-gesture-revision"
    root = storage_root / project_id
    root.mkdir(parents=True)
    (root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    original = fd.read_project_film(project_id)

    # A concurrent writer advances the document after the browser's gesture snapshot.
    advanced = fd.edit_project_film(
        project_id, original["rev"],
        [{"op": "set", "clip": "clip-b", "field": "at", "value": 7}],
        source="external_file",
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/editor/film/edit"

    def post(payload):
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    try:
        status, rejected = post({
            "project_id": project_id,
            "base": original["rev"],
            "require_base": True,
            "ops": [{"op": "move", "clip": "clip-a", "track_id": "video-main", "at": 2}],
        })
        assert status == 409, rejected
        assert rejected["code"] == "REVISION_CONFLICT"
        after_rejection = fd.read_project_film(project_id)
        assert after_rejection["rev"] == advanced["rev"]
        assert after_rejection["doc"]["tracks"][0]["clips"][0]["attrs"]["at"] == "0"

        # Existing explicit manual-rebase behavior remains available when strict mode is absent.
        status, rebased = post({
            "project_id": project_id,
            "base": original["rev"],
            "ops": [{"op": "set", "clip": "clip-a", "field": "title", "value": "manual rebase"}],
        })
        assert status == 200, rebased
        assert rebased["rebased"] is True
        final = fd.read_project_film(project_id)
        assert final["rev"] == rebased["rev"]
        clips = {clip["id"]: clip for track in final["doc"]["tracks"] for clip in track["clips"]}
        assert clips["clip-b"]["attrs"]["at"] == "7"
        assert clips["clip-a"]["attrs"]["title"] == "manual rebase"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_insert_asset_route_requires_exact_revision_for_every_placement_mode(tmp_path, monkeypatch):
    import importlib
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    storage_root = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-asset-insert-exact-revision"
    root = storage_root / project_id
    root.mkdir(parents=True)
    film_path = root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    original = fd.read_project_film(project_id)
    advanced = fd.edit_project_film(
        project_id, original["rev"],
        [{"op": "set", "clip": "clip-b", "field": "at", "value": 7}],
        source="external_file",
    )
    external_bytes = film_path.read_bytes()

    def must_not_resolve_asset(*args, **kwargs):
        raise AssertionError("stale insert-asset request reached asset resolution")

    monkeypatch.setattr(fd, "library_asset_clip", must_not_resolve_asset)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/editor/film/insert-asset"

    def post(mode):
        payload = {
            "project_id": project_id,
            "base": original["rev"],
            "asset_id": "stale-fixture-asset",
            "placement_mode": mode,
            "track_id": "video-main",
            "at": 2,
            "still_duration": 5,
        }
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    try:
        for mode in ("new-track", "ripple", "overwrite"):
            status, body = post(mode)
            assert status == 409, (mode, body)
            assert body["code"] == "REVISION_CONFLICT", (mode, body)
            current = fd.read_project_film(project_id)
            assert current["rev"] == advanced["rev"], (mode, current)
            assert film_path.read_bytes() == external_bytes, mode
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("placement_mode", ["new-track", "ripple", "overwrite"])
def test_insert_asset_route_rejects_revision_change_during_asset_resolution(tmp_path, monkeypatch, placement_mode):
    import importlib
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    storage_root = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-asset-race-" + placement_mode
    root = storage_root / project_id
    root.mkdir(parents=True)
    film_path = root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    original = fd.read_project_film(project_id)
    asset_resolution_started = threading.Event()
    release_asset_resolution = threading.Event()

    def delayed_asset_clip(pid, asset_id, at, still_duration):
        asset_resolution_started.set()
        if not release_asset_resolution.wait(timeout=10):
            raise RuntimeError("test did not release asset resolution")
        clip_id = "race-asset-" + placement_mode
        return {
            "id": clip_id,
            "tag": "video",
            "attrs": {
                "id": clip_id,
                "src": "../assets/new.mp4#t=0,2",
                "at": str(at),
                "volume": "1",
            },
        }

    monkeypatch.setattr(fd, "library_asset_clip", delayed_asset_clip)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/editor/film/insert-asset"
    response_result = {}

    def send_insert():
        payload = {
            "project_id": project_id,
            "base": original["rev"],
            "asset_id": "fixture-asset",
            "placement_mode": placement_mode,
            "track_id": "video-main",
            "at": 2,
            "still_duration": 2,
        }
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                response_result.update(status=response.status,
                                       body=json.loads(response.read().decode("utf-8")))
        except urllib.error.HTTPError as error:
            response_result.update(status=error.code,
                                   body=json.loads(error.read().decode("utf-8")))

    request_thread = threading.Thread(target=send_insert, daemon=True)
    try:
        request_thread.start()
        assert asset_resolution_started.wait(timeout=5), "asset resolution was not reached"

        # The HTTP request passed its initial base check, then an external writer wins the race.
        advanced = fd.edit_project_film(
            project_id, original["rev"],
            [{"op": "set", "clip": "clip-b", "field": "title", "value": "external"}],
            source="external_file",
        )
        external_bytes = film_path.read_bytes()
    finally:
        release_asset_resolution.set()
        request_thread.join(timeout=10)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    assert not request_thread.is_alive(), "insert request did not finish"
    assert response_result.get("status") == 409, response_result
    assert response_result.get("body", {}).get("code") == "REVISION_CONFLICT", response_result
    final = fd.read_project_film(project_id)
    assert final["rev"] == advanced["rev"], final
    assert film_path.read_bytes() == external_bytes
    assert final["doc"]["tracks"][0]["clips"][1]["attrs"].get("title") == "external"


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
    assert "timelineZoomAnchorTime(scroll.scrollLeft,localX,S.pxPerSec,132)" in script
    assert "timelineZoomScrollLeft(anchorTime,localX,S.pxPerSec,132)" in script
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")

    # Ctrl/Meta-wheel input is React-owned now; the legacy script only exposes
    # the zoom bridge and must not be required to bind a second wheel listener.
    assert 'scroll.addEventListener("wheel", onWheel, {passive:false})' in component
    assert 'scroll.dataset.reactZoomReady = "true"' in component
    assert "scroll.addEventListener('wheel'" not in script
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
    assert '_FILM_REVISION_WATCHER.watch(' in server
    assert 'initial_text=state.get("text")' in server
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
    assert "slideGesture=!!(!trimEdge&&!S.slipMode&&(S.slideMode||(e.altKey&&e.shiftKey))&&mediaCanSlip)" in script
    assert 'data-film-action="slide-mode"' in index
    assert ".fs-btn.fs-slide-mode-active" in (root / "webui" / "static" / "css" / "film-studio.css").read_text(encoding="utf-8")
    assert "e.altKey&&e.shiftKey" in script
    assert "data-slide-delta" in script
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    assert "document.elementFromPoint(event.clientX,event.clientY)" in component
    assert "filmTimelineGestureController.validateMoveTarget(input)" in component
    assert "if(d.mode==='move'&&gestureDomRefs){" in script
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
    assert "rollGesture=!!(trimEdge&&e.ctrlKey)" in script
    assert "data-roll-delta" in script
    assert "S.drag={id:clip.id,mode:'roll',pointerId:e.pointerId" in script
    assert "return Boolean(S.drag&&S.drag.id===clip.id&&S.drag.pointerId===e.pointerId" in script

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
    assert "slipGesture=!!(!trimEdge&&(S.slipMode||(e.altKey&&!e.shiftKey))&&mediaCanSlip)" in script
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
    assert 'id="fs-side-workbench"' in index
    assert 'id="fs-side-divider"' in index
    assert 'id="fs-side-inspector" role="region" aria-label="属性检查器"' in index
    assert 'id="fs-side-ai" role="region" aria-label="AI 导演"' in index
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


def test_trim_edge_extension_is_bounded_by_real_media_duration(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage))
    media_root = storage / "assets"
    media_root.mkdir(parents=True)
    media_path = media_root / "b.mp4"
    generated = subprocess.run(
        [
            renderer._ffmpeg(), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=8",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "8",
            "-y", str(media_path),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert generated.returncode == 0, generated.stderr

    project_id = "film-trim-source-end"
    project_root = storage / project_id
    project_root.mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    before = fd.read_project_film(project_id)

    extended = fd.edit_project_film(
        project_id, before["rev"],
        [{"op": "trim", "clip": "clip-b", "edge": "end", "end": 10.5}],
        source="test",
    )
    clip = next(
        clip for track in extended["document"]["tracks"] for clip in track["clips"]
        if clip["id"] == "clip-b"
    )
    assert clip["attrs"]["src"].endswith("#t=0,5.5")
    assert fd.clip_end(clip) == pytest.approx(10.5)

    stable = fd.read_project_film(project_id)
    with pytest.raises(fd.FilmError, match="超出素材时长"):
        fd.edit_project_film(
            project_id, stable["rev"],
            [{"op": "trim", "clip": "clip-b", "edge": "end", "end": 13.5}],
            source="test",
        )
    after = fd.read_project_film(project_id)
    assert after["rev"] == stable["rev"], "rejected source extension must not mutate film.html"
    assert after["text"] == stable["text"]

    # A left extension can reveal the preselected source interval down to t=0, not before.
    start_project = "film-trim-source-start"
    start_root = storage / start_project
    start_root.mkdir(parents=True)
    start_markup = SAMPLE.replace(
        '<video id="clip-a" src="../assets/a.mp4#t=2,12" at="0" speed="2" volume="1"></video>',
        '<video id="clip-a" src="../assets/a.mp4#t=2,12" at="20" speed="2" volume="1"></video>',
    ).replace("../assets/b.mp4#t=0,5", "../assets/b.mp4#t=2,7").replace(
        '<video id="clip-b" src="../assets/b.mp4#t=2,7" at="5"></video>',
        '<video id="clip-b" src="../assets/b.mp4#t=2,7" at="5" '
        'volume_envelope=\'[{"t":0,"v":1},{"t":4,"v":0.2}]\' '
        'data-film-keyframes=\'[{"property":"opacity","keyframes":[{"at":0,"value":0,"easing":"linear"},{"at":4,"value":1,"easing":"linear"}]}]\'></video>',
    )
    (start_root / fd.FILM_NAME).write_text(start_markup, encoding="utf-8")
    start_before = fd.read_project_film(start_project)
    start_extended = fd.edit_project_film(
        start_project, start_before["rev"],
        [{"op": "trim", "clip": "clip-b", "edge": "start", "at": 3}],
        source="test",
    )
    start_clip = next(
        clip for track in start_extended["document"]["tracks"] for clip in track["clips"]
        if clip["id"] == "clip-b"
    )
    assert start_clip["attrs"]["src"].endswith("#t=0,7")
    assert float(start_clip["attrs"]["at"]) == pytest.approx(3)
    shifted_envelope = json.loads(start_clip["attrs"]["volume_envelope"])
    assert shifted_envelope == [{"t": 2.0, "v": 1.0}, {"t": 6.0, "v": 0.2}]
    shifted_keyframes = json.loads(start_clip["attrs"]["data-film-keyframes"])[0]["keyframes"]
    assert [point["at"] for point in shifted_keyframes] == [2.0, 6.0]
    stable_start = fd.read_project_film(start_project)
    with pytest.raises(fd.FilmError, match="不能小于"):
        fd.edit_project_film(
            start_project, stable_start["rev"],
            [{"op": "trim", "clip": "clip-b", "edge": "start", "at": 2}],
            source="test",
        )
    after_start = fd.read_project_film(start_project)
    assert after_start["rev"] == stable_start["rev"]
    assert after_start["text"] == stable_start["text"]


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
    external.mkdir(exist_ok=True)
    composer = tmp_path / "composer"
    composer.mkdir(exist_ok=True)
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


def test_ai_planner_can_add_track_move_existing_clip_and_remove_empty_track_only():
    class FakeLLM:
        def generate(self, _prompt):
            return json.dumps({
                "summary": "新建轨道并调整片段归属，删除空轨道",
                "operations": [
                    {"op": "track_add", "track_id": "track-ai-added", "label": "AI 新轨道", "kind": "video", "index": 0},
                    {"op": "move", "clip": "clip-b", "track_id": "track-ai-added", "at": 6},
                    {"op": "track_remove", "track_id": "empty-spare"},
                ],
            }, ensure_ascii=False)

    source = SAMPLE.replace("</body>", '<section id="empty-spare" data-label="空轨道"></section></body>')
    document = fd.parse_film(source)
    state = {"exists": True, "doc": document, "rev": fd.revision_of(source)}
    operations = fd.plan_film_command(FakeLLM(), state, "新增一条轨道，把第二段移动过去，并删除空轨道")
    assert [op["op"] for op in operations] == ["track_add", "move", "track_remove"]
    candidate, normalized = fd.apply_ops(document, operations)
    assert candidate["tracks"][0]["id"] == "track-ai-added"
    assert candidate["tracks"][0]["attrs"]["data-kind"] == "video"
    assert any(clip["id"] == "clip-b" for clip in candidate["tracks"][0]["clips"])
    assert all(track["id"] != "empty-spare" for track in candidate["tracks"])
    assert normalized[0]["kind"] == "video"


def test_ai_planner_rejects_asset_insertion_and_removing_nonempty_track():
    class FakeLLM:
        def __init__(self, operations):
            self.operations = operations
        def generate(self, _prompt):
            return json.dumps({"summary": "测试", "operations": self.operations}, ensure_ascii=False)

    state = {"exists": True, "doc": fd.parse_film(SAMPLE), "rev": "test-revision"}
    with pytest.raises(fd.FilmError, match="不允许的操作"):
        fd.plan_film_command(FakeLLM([
            {"op": "track_add", "track_id": "track-ai-added", "label": "安全测试", "index": 0},
            {"op": "insert", "track_id": "track-ai-added", "clip": {
                "id": "unauthorized-asset", "tag": "video",
                "attrs": {"id": "unauthorized-asset", "src": "../assets/unsafe.mp4#t=0,1", "at": "0"},
            }},
        ]), state, "新增并插入一段素材")
    with pytest.raises(fd.FilmError, match="预先为空的轨道"):
        fd.plan_film_command(FakeLLM([
            {"op": "track_remove", "track_id": "video-main"},
        ]), state, "删除主画面轨道")


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

    with pytest.raises(editor.EditorError) as read_error:
        editor.load_composition("film-test")
    assert read_error.value.code == "FILM_DOCUMENT_AUTHORITATIVE"

    with pytest.raises(editor.EditorError) as version_error:
        editor.composition_version("film-test")
    assert version_error.value.code == "FILM_DOCUMENT_AUTHORITATIVE"

    with pytest.raises(fd.FilmError, match="film.html"):
        workflow_run.run_render_async("film-test", "final")


def test_legacy_save_and_render_endpoints_are_hard_gated_after_migration():
    root = Path(__file__).resolve().parents[1]
    editor = (root / "video_director" / "editor.py").read_text(encoding="utf-8")
    workflow = (root / "video_director" / "workflow_run.py").read_text(encoding="utf-8")
    confirm = (root / "video_director" / "confirm.py").read_text(encoding="utf-8")
    server = (root / "webui" / "server.py").read_text(encoding="utf-8")
    studio = (root / "webui" / "static" / "js" / "film-studio.js").read_text(encoding="utf-8")
    creative = (root / "webui" / "static" / "js" / "creative-plan.js").read_text(encoding="utf-8")
    assert 'assert_legacy_composition_access(project_id, "保存 CompositionIR")' in editor
    assert 'assert_legacy_composition_access(project_id, "读取旧 CompositionIR")' in editor
    assert 'assert_legacy_composition_access(project_id, "读取旧 CompositionIR 版本")' in editor
    assert '_reject_legacy_composition_access(pid, "重复初始化已存在的 film.html")' in server
    assert '_reject_legacy_composition_access(str(pid).strip(), "使用旧 CompositionIR AI 提案")' in server
    assert 'assert_legacy_composition_access(project_id, f"渲染 {kind}")' in workflow
    assert 'assert_legacy_composition_access(project_id, "导出旧时间线成片")' in confirm
    assert '_reject_legacy_composition_access(pid, "启动旧时间线渲染")' in server
    assert '_reject_legacy_composition_access(pid, "导出旧时间线成片")' in server
    assert '_reject_legacy_composition_access(pid, "读取旧 CompositionIR 时间线")' in server
    assert '_reject_legacy_composition_access(pid, "读取旧 CompositionIR 素材匹配数据")' in server
    assert '_reject_legacy_composition_access(pid, "读取旧 CompositionIR 连续性报告")' in server
    assert '_reject_legacy_composition_access(pid, "对旧 CompositionIR 图层执行抠像")' in server
    assert re.search(
        r'_reject_legacy_composition_access\(\s*pid,\s*"生成或保存基于旧 CompositionIR 的分镜"\s*\)',
        server,
    )
    assert 'get_project_shotbook_reference(pid)' in server
    assert 'fd._film_path(pid).is_file()' in server
    assert 'await window.shotbookLoad(true)' in studio
    assert 'await window.creativePlanReload()' in studio
    assert 'button.disabled=state.referenceOnly' in creative
    assert '_reject_legacy_composition_access(str(pid).strip(), "执行旧 CompositionIR Editor Command")' in server
    assert '_reject_legacy_composition_access(str(pid).strip(), "将旧生成素材添加到 CompositionIR 时间线")' in server
    assert '_reject_legacy_composition_access(pid, "执行旧版 AI Director 指令")' in server
    assert '_reject_legacy_composition_access(pid, "执行旧版 Action Service 编辑动作")' in server
    assert re.search(r'_reject_legacy_composition_access\(\s*continue_pid,\s*"继续旧版 AI Director 检查点"\s*\)', server)
    assert re.search(r'_reject_legacy_composition_access\(\s*retry_pid,\s*"重新执行旧版 AI Director 创作"\s*\)', server)
    assert 'film_authoritative = fd._film_path(pid).is_file()' in server
    assert '"reference_only": True' in server
    assert 'plan = creative.load_plan(pid)' in server
    assert 'fd._film_path(pid).is_file()' in server



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


def test_element_set_can_explicitly_restore_transparent_background():
    doc = fd.parse_film(HTML_SCENE_SAMPLE)
    updated, normalized = fd.apply_ops(doc, [{
        "op": "element_set", "clip": "scene", "selector": "#title",
        "style": {"backgroundColor": "transparent", "opacity": "0.75",
                  "fontWeight": "500", "textAlign": "center"},
    }])
    clip = updated["tracks"][0]["clips"][0]
    overrides = json.loads(clip["attrs"]["overrides"])
    title = next(item for item in overrides if item.get("selector") == "#title")
    assert title["style"] == {
        "backgroundColor": "transparent", "opacity": "0.75",
        "fontWeight": "500", "textAlign": "center",
    }
    assert normalized[0]["op"] == "element_set"


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
    component = (root / "remotion_runtime" / "src" / "StudioCanvasInspector.tsx").read_text(encoding="utf-8")
    assert "window.filmStudioCommitCanvasElement=async function(operation,options)" in studio
    assert "result.operation, {allowInspectorDraft:true}" in component
    assert "await filmStudioCommitExactRevision([operation],{allowInspectorDraft:options&&options.allowInspectorDraft?'canvas':''})" in studio
    assert 'if kind == "element_set":' in document
    assert "field:'overrides',value:JSON.stringify(overrides)" not in studio

"""The FilmDocument keeps FPS authoritative across parse/serialize operations."""

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
    project.mkdir(parents=True, exist_ok=True)
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


@pytest.mark.parametrize(
    "source, expected",
    [
        (
            '<!doctype html><html><head></head><body>'
            '<section id="track"><video id="clip" src="assets/video.mp4#t=0,1" at="0">'
            '</section></body></html>',
            "媒体片段结束标签不完整",
        ),
        (
            '<!doctype html><html><head></head><body>'
            '<section id="track"><video id="clip" src="assets/video.mp4#t=0,1" at="0"></video>'
            '</body></html>',
            "轨道 section 标签不完整",
        ),
    ],
)
def test_parse_rejects_unclosed_track_or_media_elements(source, expected):
    with pytest.raises(fd.FilmError) as error:
        fd.parse_film(source)
    assert expected in str(error.value)


def test_head_extraction_ignores_tag_examples_inside_comments():
    source = '''<!doctype html>
<!-- documentation example: <head> and </head> are literal comment text -->
<html><head>
<!-- example script syntax: <script>alert(1)</script> is not executable here -->
<meta charset="utf-8">
</head><body>
<section id="track"><video id="clip" src="assets/video.mp4#t=0,1" at="0"></video></section>
</body></html>'''
    parsed = fd.parse_film(source)
    assert parsed["head_html"].startswith("<head>")
    assert parsed["head_html"].endswith("</head>")
    assert "<!-- example script syntax: <script>alert(1)</script> is not executable here -->" in parsed["head_html"]
    serialized = fd.serialize_film(parsed)
    assert serialized.count("<!-- documentation example: <head> and </head> are literal comment text -->") == 1
    assert serialized.count("<!-- example script syntax: <script>alert(1)</script> is not executable here -->") == 1
    assert fd.serialize_film(fd.parse_film(serialized)) == serialized



def _source_with_runtime_metadata(metadata: dict) -> str:
    document = fd.parse_film(SAMPLE)
    clip = next(item for track in document["tracks"] for item in track["clips"])
    clip["attrs"].update(metadata)
    return fd.serialize_film(document)


@pytest.mark.parametrize(
    "metadata",
    [
        {"data-film-transform": "{broken-json"},
        {"data-film-transform": json.dumps({"x": 0.5, "y": 0.5, "scale": 1})},
        {
            "data-film-transform": json.dumps({
                "x": 0.5, "y": 0.5, "scale": 1, "scale_x": 1, "scale_y": 1,
                "rotation": 0, "opacity": 1, "anchor": "center",
                "stage_width": 1920, "stage_height": 1080, "base_width": 1920,
                "base_height": 1080, "transform": "scale(2)",
            }),
        },
    ],
)
def test_parse_rejects_broken_or_unknown_film_transform_metadata(metadata):
    source = _source_with_runtime_metadata(metadata)
    with pytest.raises(fd.FilmError, match="data-film-transform 无效"):
        fd.parse_film(source)


@pytest.mark.parametrize(
    "metadata",
    [
        {"data-film-keyframes": "{broken-json"},
        {
            "data-film-keyframes": json.dumps([
                {"property": "perspective3d", "keyframes": [{"at": 0, "value": 1}]}
            ]),
        },
        {
            "data-film-keyframes": json.dumps([
                {"property": "scale_x", "keyframes": [
                    {"at": 0, "value": 1, "easing": "spring"},
                ]},
            ]),
        },
        {
            "data-film-keyframes": json.dumps([
                {"property": "scale_x", "keyframes": [
                    {"at": 0, "value": 1},
                    {"at": 0, "value": 1.2},
                ]},
            ]),
        },
        {
            "data-film-keyframes": json.dumps([
                {"property": "scale_x", "keyframes": [
                    {"at": 0, "value": 1},
                    {"at": 6, "value": 1.2},
                ]},
            ]),
        },
    ],
)
def test_parse_rejects_keyframe_metadata_that_runtime_would_silently_drop(metadata):
    source = _source_with_runtime_metadata(metadata)
    with pytest.raises(fd.FilmError, match="data-film-keyframes 无效"):
        fd.parse_film(source)


def test_parse_rejects_malformed_mask_keyframes_and_accepts_valid_transform_channels():
    valid_transform = {
        "x": 0.5, "y": 0.5, "scale": 1, "scale_x": 0.8, "scale_y": 1.2,
        "rotation": 0, "opacity": 1, "anchor": "center",
        "stage_width": 1920, "stage_height": 1080, "base_width": 1920,
        "base_height": 1080,
    }
    valid_channels = [
        {"property": "scale_x", "keyframes": [
            {"at": 0, "value": 0.8, "easing": "linear"},
            {"at": 1, "value": 1.4, "easing": "ease_in_out"},
        ]},
        {"property": "scale_y", "keyframes": [
            {"at": 0, "value": 1.2, "easing": "linear"},
            {"at": 1, "value": 0.9, "easing": "ease_out"},
        ]},
    ]
    good = _source_with_runtime_metadata({
        "data-film-transform": json.dumps(valid_transform),
        "data-film-keyframes": json.dumps(valid_channels),
        "data-film-mask-keyframes": json.dumps([
            {"at": 0, "value": [[0, 0], [1, 0], [1, 1], [0, 1]], "easing": "linear"},
            {"at": 1, "value": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]], "easing": "ease_in_out"},
        ]),
        "data-film-mask-space": "composition",
    })
    parsed = fd.parse_film(good)
    roundtrip = fd.serialize_film(parsed)
    assert fd.serialize_film(fd.parse_film(roundtrip)) == roundtrip

    bad = _source_with_runtime_metadata({
        "data-film-mask-keyframes": json.dumps([
            {"at": 0, "value": [[0, 0], [1, 0]], "easing": "linear"},
        ]),
    })
    with pytest.raises(fd.FilmError, match="data-film-mask-keyframes 无效"):
        fd.parse_film(bad)



def test_duplicate_keyframe_channels_keep_runtime_first_channel_semantics():
    raw = [
        {"property": "scale_x", "keyframes": [
            {"at": 0, "value": 1}, {"at": 1, "value": 1.5},
        ]},
        {"property": "scale_x", "keyframes": [
            {"at": 0, "value": 2}, {"at": 1, "value": 3},
        ]},
    ]
    mapped, warnings = fd._film_keyframes(raw, 2)
    assert len(mapped) == 1
    assert [point["value"] for point in mapped[0]["keyframes"]] == [1, 1.5]
    assert any("重复关键帧通道" in warning for warning in warnings)



def test_duplicate_keyframe_times_are_normalized_with_runtime_last_point_semantics():
    mapped, warnings = fd._film_keyframes([
        {"property": "scale_x", "keyframes": [
            {"at": 0, "value": 1},
            {"at": 0, "value": 1.3},
            {"at": 1, "value": 1.6},
        ]},
    ], 2)
    assert len(mapped) == 1
    points = mapped[0]["keyframes"]
    assert [point["at"] for point in points] == [0, 1]
    assert [point["value"] for point in points] == [1.3, 1.6]
    assert any("重复关键帧" in warning for warning in warnings)



def test_parse_rejects_transform_stage_dimensions_that_disagree_with_viewport():
    document = fd.parse_film(SAMPLE)
    clip = next(item for track in document["tracks"] for item in track["clips"])
    clip["attrs"]["data-film-transform"] = json.dumps({
        "x": 0.5, "y": 0.5, "scale": 1, "scale_x": 1, "scale_y": 1,
        "rotation": 0, "opacity": 1, "anchor": "center",
        "stage_width": 320, "stage_height": 180, "base_width": 320,
        "base_height": 180,
    })
    with pytest.raises(fd.FilmError) as error:
        fd.parse_film(fd.serialize_film(document))
    assert error.value.code == "FILM_TRANSFORM_STAGE_MISMATCH"
    assert "viewport" in str(error.value)



def _source_with_overrides(overrides) -> str:
    document = fd.parse_film(SAMPLE)
    iframe = {
        "id": "scene",
        "tag": "iframe",
        "attrs": {
            "id": "scene",
            "src": "scenes/title.html#t=0,5",
            "at": "0",
        },
    }
    document["tracks"].append({"id": "scene-track", "attrs": {"id": "scene-track"}, "clips": [iframe]})
    iframe["attrs"]["overrides"] = json.dumps(overrides, ensure_ascii=False)
    return fd.serialize_film(document)


def test_parse_rejects_malformed_selector_overrides_instead_of_ignoring_them():
    bad_documents = [
        '{"selector": "#title"}',
        json.dumps({"selector": "#title"}),
        json.dumps([{"selector": "", "text": "title"}]),
        json.dumps([{"selector": "#title", "text": 3}]),
        json.dumps([{"selector": "#title", "style": "color:red"}]),
        json.dumps([{"selector": "#title", "style": {"color": "url(https://invalid.example/x)"}}]),
        json.dumps([{"selector": "#title", "style": {"fontSize": "large"}}]),
    ]
    for raw in bad_documents:
        source = _source_with_overrides([])
        source = source.replace('overrides="[]"', 'overrides="' + raw.replace('"', '&quot;') + '"')
        with pytest.raises(fd.FilmError, match="overrides"):
            fd.parse_film(source)


def test_parse_preserves_legacy_overrides_but_returns_explicit_runtime_warnings():
    legacy = [
        {"n": ".headline", "t": "text", "s": {"x": 20, "y": 30}},
        {"selector": "#title", "text": "新标题", "style": {"color": "#ffffff", "fontSize": "28px"}},
        {"selector": "#title", "style": {"boxShadow": "0 0 4px red"}},
    ]
    document = fd.parse_film(_source_with_overrides(legacy))
    iframe = next(clip for track in document["tracks"] for clip in track["clips"] if clip["id"] == "scene")
    assert json.loads(iframe["attrs"]["overrides"]) == legacy
    assert any("不含 selector 的遗留记录" in warning for warning in document["warnings"])
    assert any("不支持的 style 属性" in warning for warning in document["warnings"])
    payload = fd.film_payload("film-test", {
        "exists": True,
        "rev": fd.revision_of(fd.serialize_film(document)),
        "doc": document,
    })
    assert any("不含 selector 的遗留记录" in warning for warning in payload["warnings"])


def test_parse_reports_unknown_fields_in_selector_override_without_destroying_legacy_values():
    entries = [{"selector": "#title", "text": "Title", "legacyEffect": {"s": 2}}]
    parsed = fd.parse_film(_source_with_overrides(entries))
    assert json.loads(next(
        clip for track in parsed["tracks"] for clip in track["clips"] if clip["id"] == "scene"
    )["attrs"]["overrides"]) == entries
    assert any("当前 Runtime 不识别的字段" in warning for warning in parsed["warnings"])


def test_film_command_streams_model_deltas_before_parsing_final_proposal():
    output = '{"summary":"调整镜头节奏","operations":[]}'
    fragments = []

    class StreamingLLM:
        def generate_stream(self, prompt, on_delta=None):
            assert "用户指令" in prompt
            for fragment in (output[:13], output[13:25], output[25:]):
                if on_delta:
                    on_delta(fragment)
            return output

        def generate(self, prompt):
            raise AssertionError("stream-capable models must use generate_stream")

    state = {"exists": True, "doc": fd.parse_film(HTML_SCENE_SAMPLE)}
    summary = {}
    operations = fd.plan_film_command(
        StreamingLLM(), state, "调整镜头节奏", "scene",
        summary_out=summary, on_token=fragments.append,
    )
    assert operations == []
    assert "".join(fragments) == output
    assert summary["summary"] == "调整镜头节奏"


def test_film_command_stream_falls_back_for_legacy_llm_providers():
    output = '{"summary":"不需要修改","operations":[]}'
    fragments = []

    class LegacyLLM:
        def generate(self, prompt):
            return output

    state = {"exists": True, "doc": fd.parse_film(HTML_SCENE_SAMPLE)}
    summary = {}
    operations = fd.plan_film_command(
        LegacyLLM(), state, "检查当前片子", "scene",
        summary_out=summary, on_token=fragments.append,
    )
    assert operations == []
    assert fragments == [output]
    assert summary["summary"] == "不需要修改"


def test_streaming_planner_forwards_cancel_event_and_upstream_handle():
    import threading

    output = '{"summary":"调整画面关键帧","operations":[]}'
    received = []
    cancellation = threading.Event()
    upstream = object()

    class CancellableLLM:
        supports_stream_cancellation = True

        def generate_stream(self, prompt, on_delta=None, on_stream_open=None, cancel_event=None):
            assert cancel_event is cancellation
            on_stream_open(upstream)
            for part in (output[:17], output[17:]):
                on_delta(part)
            return output

        def generate(self, prompt):
            raise AssertionError("cancellable stream provider must not use generate()")

    state = {"exists": True, "doc": fd.parse_film(HTML_SCENE_SAMPLE)}
    summary = {}
    result = fd.plan_film_command(
        CancellableLLM(), state, "调整画面关键帧", "scene",
        summary_out=summary, on_token=received.append,
        on_stream_open=lambda stream: received.append(("opened", stream)),
        cancel_event=cancellation,
    )
    assert result == []
    assert received[0] == ("opened", upstream)
    assert "".join(item for item in received[1:] if isinstance(item, str)) == output
    assert summary["summary"] == "调整画面关键帧"


def test_visual_keyframes_are_rebased_without_changing_curve_on_split_and_trim_longer_source():
    doc = fd.parse_film(SAMPLE)
    channels = [{
        "property": "opacity",
        "keyframes": [
            {"at": 0, "value": 0, "easing": "linear"},
            {"at": 4, "value": 1, "easing": "ease_in"},
            {"at": 8, "value": 0.5, "easing": "linear"},
            {"at": 12, "value": 1, "easing": "ease_in_out"},
        ],
    }]
    source, _ = fd.apply_ops(doc, [
        {"op": "set", "clip": "clip-a", "field": "src", "value": "../assets/a.mp4#t=2,26"},
        {"op": "move", "clip": "clip-b", "at": 12},
        {"op": "set", "clip": "clip-a", "field": "data-film-keyframes", "value": channels},
    ])
    split, _ = fd.apply_ops(source, [
        {"op": "split", "clip": "clip-a", "at": 2, "new_id": "clip-a-keyframe-right"},
    ])
    by_id = {clip["id"]: clip for track in split["tracks"] for clip in track["clips"]}
    left = json.loads(by_id["clip-a"]["attrs"]["data-film-keyframes"])[0]["keyframes"]
    right = json.loads(by_id["clip-a-keyframe-right"]["attrs"]["data-film-keyframes"])[0]["keyframes"]
    assert left[0]["at"] == 0 and left[-1]["at"] == 2
    assert right[0]["at"] == 0 and right[0]["value"] == pytest.approx(0.25)
    assert any(isinstance(point.get("easing"), dict) and point["easing"].get("type") == "window" for point in right)
    assert not fd.validate_film(fd.serialize_film(split))

    trimmed, _ = fd.apply_ops(source, [
        {"op": "trim", "clip": "clip-a", "edge": "start", "at": 2},
        {"op": "trim", "clip": "clip-a", "edge": "end", "end": 7},
    ])
    clip = next(c for track in trimmed["tracks"] for c in track["clips"] if c["id"] == "clip-a")
    keyframes = json.loads(clip["attrs"]["data-film-keyframes"])[0]["keyframes"]
    assert keyframes[0]["at"] == 0 and keyframes[0]["value"] == pytest.approx(0.25)
    assert keyframes[-1]["at"] == 5
    assert all(point["at"] <= 5 for point in keyframes)
    assert not fd.validate_film(fd.serialize_film(trimmed))


def test_visual_keyframes_are_rebased_without_changing_curve_on_split_and_trim():
    doc = fd.parse_film(SAMPLE)
    channels = [{
        "property": "opacity",
        "keyframes": [
            {"at": 0, "value": 0, "easing": "linear"},
            {"at": 4, "value": 1, "easing": "ease_in"},
            {"at": 8, "value": 0.5, "easing": "linear"},
            {"at": 10, "value": 1, "easing": "ease_in_out"},
        ],
    }]
    source, _ = fd.apply_ops(doc, [
        {"op": "set", "clip": "clip-a", "field": "src", "value": "../assets/a.mp4#t=2,26"},
        {"op": "move", "clip": "clip-b", "at": 12},
        {"op": "set", "clip": "clip-a", "field": "data-film-keyframes", "value": channels},
    ])
    split, _ = fd.apply_ops(source, [
        {"op": "split", "clip": "clip-a", "at": 2, "new_id": "clip-a-keyframe-right"},
    ])
    by_id = {clip["id"]: clip for track in split["tracks"] for clip in track["clips"]}
    left = json.loads(by_id["clip-a"]["attrs"]["data-film-keyframes"])[0]["keyframes"]
    right = json.loads(by_id["clip-a-keyframe-right"]["attrs"]["data-film-keyframes"])[0]["keyframes"]
    assert left[0]["at"] == 0 and left[-1]["at"] == 2
    assert right[0]["at"] == 0 and right[0]["value"] == pytest.approx(0.25)
    assert any(isinstance(point.get("easing"), dict) and point["easing"].get("type") == "window" for point in right)
    assert not fd.validate_film(fd.serialize_film(split))

    trimmed, _ = fd.apply_ops(source, [
        {"op": "trim", "clip": "clip-a", "edge": "start", "at": 2},
        {"op": "trim", "clip": "clip-a", "edge": "end", "end": 7},
    ])
    clip = next(c for track in trimmed["tracks"] for c in track["clips"] if c["id"] == "clip-a")
    keyframes = json.loads(clip["attrs"]["data-film-keyframes"])[0]["keyframes"]
    assert keyframes[0]["at"] == 0 and keyframes[0]["value"] == pytest.approx(0.25)
    assert keyframes[-1]["at"] == 5
    assert all(point["at"] <= 5 for point in keyframes)
    assert not fd.validate_film(fd.serialize_film(trimmed))


def test_keyframe_window_easing_is_endpoint_normalized_and_roundtrips():
    window = {"type": "window", "preset": "ease_in", "start": 0.5, "end": 1.0}
    # E(t)=t², normalized to the residual interval [0.5, 1]: at half progress,
    # (E(0.75)-E(0.5))/(E(1)-E(0.5)) = (0.5625-0.25)/0.75.
    assert fd._film_keyframe_ease(0.5, window) == pytest.approx(5 / 12)
    doc = fd.parse_film(SAMPLE)
    updated, _ = fd.apply_ops(doc, [{
        "op": "set", "clip": "clip-a", "field": "data-film-keyframes",
        "value": [{"property": "opacity", "keyframes": [
            {"at": 0, "value": 0, "easing": "linear"},
            {"at": 4, "value": 1, "easing": window},
        ]}],
    }])
    text_value = fd.serialize_film(updated)
    assert not fd.validate_film(text_value)
    assert window == json.loads(
        next(c for t in fd.parse_film(text_value)["tracks"] for c in t["clips"] if c["id"] == "clip-a")
        ["attrs"]["data-film-keyframes"]
    )[0]["keyframes"][1]["easing"]


def test_film_keyframe_mapper_preserves_cubic_bezier_and_window_curves():
    bezier = {"type": "bezier", "x1": 0.2, "y1": 0.35, "x2": 0.8, "y2": 1.0}
    curve_window = {"type": "window", "curve": bezier, "start": 0.25, "end": 0.9}
    channels, unsupported = fd._film_keyframes([
        {"property": "x", "keyframes": [
            {"at": 0, "value": 0.1, "easing": bezier},
            {"at": 1, "value": 0.8, "easing": curve_window},
        ]},
    ], 4.0)
    assert unsupported == []
    assert channels == [{
        "property": "x",
        "keyframes": [
            {"at": 0.0, "value": 0.1, "easing": bezier},
            {"at": 1.0, "value": 0.8, "easing": curve_window},
        ],
    }]


def test_film_keyframe_mapper_rejects_invalid_bezier_control_order():
    channels, unsupported = fd._film_keyframes([
        {"property": "x", "keyframes": [
            {"at": 0, "value": 0.1, "easing": {
                "type": "bezier", "x1": 0.9, "y1": 0.35, "x2": 0.2, "y2": 1.0,
            }},
        ]},
    ], 4.0)
    assert channels[0]["keyframes"][0]["easing"] == "linear"
    assert unsupported == ["x 贝塞尔缓动格式无效"]


def test_visual_keyframes_stay_inside_clip_after_roll_and_slide_trim():
    doc = fd.parse_film(SAMPLE)
    channels = [{
        "property": "opacity",
        "keyframes": [
            {"at": 0, "value": 1, "easing": "linear"},
            {"at": 2, "value": 0.5, "easing": "ease_in"},
            {"at": 4, "value": 0.25, "easing": "ease_out"},
            {"at": 8, "value": 1, "easing": "linear"},
            {"at": 10, "value": 1, "easing": "linear"},
        ],
    }]
    with_keys, _ = fd.apply_ops(doc, [
        {"op": "set", "clip": "clip-a", "field": "src", "value": "../assets/a.mp4#t=2,22"},
        {"op": "move", "clip": "clip-b", "at": 12},
        {"op": "set", "clip": "clip-a", "field": "data-film-keyframes", "value": channels},
    ])
    split, _ = fd.apply_ops(with_keys, [
        {"op": "split", "clip": "clip-a", "at": 2, "new_id": "clip-a-keyframe-right"},
    ])
    rolled, _ = fd.apply_ops(split, [
        {"op": "roll", "clip": "clip-a", "other_clip": "clip-a-keyframe-right", "delta": 0.5},
    ])
    right = next(c for t in rolled["tracks"] for c in t["clips"] if c["id"] == "clip-a-keyframe-right")
    right_points = json.loads(right["attrs"]["data-film-keyframes"])[0]["keyframes"]
    assert max(point["at"] for point in right_points) <= fd.clip_duration(right) + 1e-6
    assert not fd.validate_film(fd.serialize_film(rolled))

    slide_doc = fd.parse_film(SLIDE_SAMPLE)
    slide_keys, _ = fd.apply_ops(slide_doc, [
        {"op": "set", "clip": "slide-left", "field": "data-film-keyframes",
         "value": [{"property": "opacity", "keyframes": [
             {"at": 0, "value": 1}, {"at": 1, "value": 0.5}, {"at": 2, "value": 0.8},
         ]}]},
        {"op": "set", "clip": "slide-right", "field": "data-film-keyframes",
         "value": [{"property": "opacity", "keyframes": [
             {"at": 0, "value": 0.2}, {"at": 2, "value": 0.8}, {"at": 4, "value": 1},
         ]}]},
    ])
    slid, _ = fd.apply_ops(slide_keys, [
        {"op": "slide", "clip": "slide-middle", "delta": 0.5},
    ])
    clips = {c["id"]: c for t in slid["tracks"] for c in t["clips"]}
    for clip_id in ("slide-left", "slide-right"):
        clip = clips[clip_id]
        points = json.loads(clip["attrs"]["data-film-keyframes"])[0]["keyframes"]
        assert max(point["at"] for point in points) <= fd.clip_duration(clip) + 1e-6
    assert not fd.validate_film(fd.serialize_film(slid))


def test_ai_planner_context_includes_existing_visual_keyframe_channels():
    doc = fd.parse_film(HTML_SCENE_SAMPLE)
    channels = [{
        "property": "opacity",
        "keyframes": [
            {"at": 0, "value": 0.2, "easing": "linear"},
            {"at": 1, "value": 0.9, "easing": {
                "type": "window", "preset": "ease_out", "start": 0.25, "end": 1.0,
            }},
        ],
    }]
    edited, _ = fd.apply_ops(doc, [{
        "op": "set", "clip": "scene", "field": "data-film-keyframes", "value": channels,
    }])
    state = {"exists": True, "doc": edited}

    class CaptureLLM:
        prompt = ""

        def generate(self, prompt):
            self.prompt = prompt
            return '{"summary":"保留现有动画","operations":[]}'

    llm = CaptureLLM()
    summary = {}
    operations = fd.plan_film_command(llm, state, "不要改变现有关键帧", "scene", summary_out=summary)
    clips_text = llm.prompt.split("\n当前片段：", 1)[1].split("\n用户指令：", 1)[0]
    clips = json.loads(clips_text)
    current = next(item for item in clips if item["id"] == "scene")
    assert json.loads(current["film_keyframes"]) == channels
    assert operations == []
    assert summary["summary"] == "保留现有动画"


def test_audio_clips_reject_visual_keyframes_but_keep_volume_envelopes():
    doc = fd.parse_film(SAMPLE)
    with pytest.raises(fd.FilmError, match="音频片段不支持视觉属性关键帧"):
        fd.apply_ops(doc, [{
            "op": "set", "clip": "audio-1", "field": "data-film-keyframes",
            "value": [{"property": "opacity", "keyframes": [
                {"at": 0, "value": 1}, {"at": 1, "value": 0},
            ]}],
        }])
    updated, _ = fd.apply_ops(doc, [{
        "op": "set", "clip": "audio-1", "field": "volume_envelope",
        "value": [{"t": 0, "v": 1}, {"t": 1, "v": 0.5}],
    }])
    audio = next(c for track in updated["tracks"] for c in track["clips"] if c["id"] == "audio-1")
    assert "volume_envelope" in audio["attrs"]
    assert "data-film-keyframes" not in audio["attrs"]



def test_ai_stream_endpoint_delivers_token_fragments_and_rejects_stale_proposal(tmp_path, monkeypatch):
    """Exercise the real HTTP/SSE route with a streaming model double and revision conflict."""
    import http.client
    import importlib
    from http.server import ThreadingHTTPServer
    import threading

    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server
    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-ai-stream-integration"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    (project_root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")
    original = fd.read_project_film(project_id)
    assert original["exists"]

    first_plan = json.dumps({
        "summary": "调整第一个镜头速度",
        "operations": [{"op": "set", "clip": "clip-a", "field": "speed", "value": 2.5}],
    }, ensure_ascii=False, separators=(",", ":"))
    stale_plan = json.dumps({
        "summary": "调整第二个镜头速度",
        "operations": [{"op": "set", "clip": "clip-b", "field": "speed", "value": 1.25}],
    }, ensure_ascii=False, separators=(",", ":"))

    class FakeStreamingModel:
        supports_stream_cancellation = True

        def __init__(self):
            self.plans = [first_plan, stale_plan]
            self.closed = False

        def generate_stream(self, prompt, system=None, on_delta=None,
                            on_stream_open=None, cancel_event=None):
            text = self.plans.pop(0)
            self.closed = False
            if callable(on_stream_open):
                on_stream_open(self)
            try:
                for start in range(0, len(text), 17):
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("cancelled")
                    fragment = text[start:start + 17]
                    if callable(on_delta):
                        on_delta(fragment)
                return text
            finally:
                self.closed = True

        def close(self):
            self.closed = True

    model = FakeStreamingModel()
    monkeypatch.setattr(web_server.vd_llm, "get_llm", lambda: model)

    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post_json(pathname, payload):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request(
                "POST", pathname,
                body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Connection": "close"},
            )
            response = connection.getresponse()
            body = response.read()
            return response.status, json.loads(body.decode("utf-8")) if body else {}
        finally:
            connection.close()

    def post_stream(base_revision, request_id):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request(
                "POST", "/api/editor/film/command",
                body=json.dumps({
                    "project_id": project_id,
                    "base": base_revision,
                    "message": "按计划修改指定镜头速度",
                    "selected_clip_id": "",
                    "stream": True,
                    "request_id": request_id,
                }, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Connection": "close"},
            )
            response = connection.getresponse()
            assert response.status == 200, (response.status, response.read().decode("utf-8", "replace"))
            assert response.getheader("Content-Type", "").startswith("text/event-stream")

            events = []
            event_name = ""
            data_lines = []
            while True:
                raw_line = response.fp.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if not line:
                    if event_name and data_lines:
                        packet = (event_name, json.loads("\n".join(data_lines)))
                        events.append(packet)
                        if event_name in {"result", "error"}:
                            break
                    event_name, data_lines = "", []
                elif line.startswith("event:"):
                    event_name = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:"):].lstrip())
            return events
        finally:
            connection.close()

    try:
        events = post_stream(original["rev"], "stream-request-0001")
        event_names = [name for name, _payload in events]
        token_fragments = [payload["fragment"] for name, payload in events if name == "token"]
        result_packets = [payload for name, payload in events if name == "result"]
        assert len(token_fragments) > 1, event_names
        assert "".join(token_fragments) == first_plan
        assert "result" in event_names, {"events": events, "event_names": event_names}
        assert event_names.index("token") < event_names.index("result")
        assert len(result_packets) == 1
        first_response = result_packets[0]
        assert first_response["ok"] is True
        proposal = first_response["proposal"]
        assert proposal["base_revision"] == original["rev"]
        assert proposal["operations"] == [{
            "op": "set", "clip": "clip-a", "field": "speed", "value": "2.5"
        }]
        # Streaming is feedback only: generating a proposal must not write film.html.
        assert fd.read_project_film(project_id)["rev"] == original["rev"]

        status, applied = post_json("/api/editor/film/apply-proposal", {
            "project_id": project_id,
            "proposal_id": proposal["id"],
            "base_revision": proposal["base_revision"],
        })
        assert status == 200, applied
        assert applied["ok"] is True
        assert len(applied["ops"]) == 1
        committed = fd.read_project_film(project_id)
        clip_a = next(clip for track in committed["doc"]["tracks"]
                      for clip in track["clips"] if clip["id"] == "clip-a")
        assert clip_a["attrs"]["speed"] == "2.5"

        stale_events = post_stream(committed["rev"], "stream-request-0002")
        stale_result = next(payload for name, payload in stale_events if name == "result")
        stale_proposal = stale_result["proposal"]
        before_external_change = fd.read_project_film(project_id)
        assert before_external_change["rev"] == committed["rev"]

        # Simulate an external Agent deleting the exact target after AI preview.
        fd.edit_project_film(
            project_id, before_external_change["rev"],
            [{"op": "remove", "clip": "clip-b"}],
        )
        changed = fd.read_project_film(project_id)
        status, rejected = post_json("/api/editor/film/apply-proposal", {
            "project_id": project_id,
            "proposal_id": stale_proposal["id"],
            "base_revision": stale_proposal["base_revision"],
        })
        assert status == 409, rejected
        assert rejected.get("ok") is False
        after_rejection = fd.read_project_film(project_id)
        assert after_rejection["rev"] == changed["rev"]
        ids = {clip["id"] for track in after_rejection["doc"]["tracks"] for clip in track["clips"]}
        assert "clip-b" not in ids
        assert next(clip for track in after_rejection["doc"]["tracks"]
                    for clip in track["clips"] if clip["id"] == "clip-a")["attrs"]["speed"] == "2.5"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_legacy_composition_save_serializes_with_film_initialization(tmp_path, monkeypatch):
    """A legacy writer queued before migration must not persist after film.html wins authority."""
    from video_director import config, editor
    from video_director.models import CompositionIR, Layer, LayerType, ProjectSpec, SceneIR
    from video_director.project_lock import project_lock

    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "legacy-save-film-init-race"
    root = config.project_dir(project_id)
    plans = root / "plans"
    plans.mkdir(parents=True, exist_ok=True)

    original_ir = CompositionIR(
        project=ProjectSpec(width=160, height=90, fps=30, duration=1.0),
        scenes=[SceneIR(
            id="legacy-scene", start=0, duration=1.0, text="before",
            layers=[Layer(type=LayerType.image, duration=1.0)],
        )],
    )
    editor.save_composition(project_id, original_ir)
    composition_path = plans / "composition.json"
    before = composition_path.read_bytes()

    lock_held = threading.Event()
    release_initializer = threading.Event()
    save_started = threading.Event()
    save_lock_attempted = threading.Event()
    save_finished = threading.Event()
    outcome = {}
    from contextlib import contextmanager
    original_editor_lock = editor.project_lock

    @contextmanager
    def observe_save_lock(project_id):
        save_lock_attempted.set()
        with original_editor_lock(project_id):
            yield

    monkeypatch.setattr(editor, "project_lock", observe_save_lock)

    def initialize_film_under_project_lock():
        with project_lock(project_id):
            lock_held.set()
            assert release_initializer.wait(timeout=5)
            (root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")

    def legacy_save():
        changed_ir = original_ir.model_copy(deep=True)
        changed_ir.scenes[0].text = "stale legacy write"
        save_started.set()
        try:
            editor.save_composition(project_id, changed_ir)
            outcome["saved"] = True
        except Exception as exc:  # the expected authority rejection is asserted below
            outcome["error"] = exc
        finally:
            save_finished.set()

    initializer = threading.Thread(target=initialize_film_under_project_lock, daemon=True)
    initializer.start()
    assert lock_held.wait(timeout=5)

    writer = threading.Thread(target=legacy_save, daemon=True)
    writer.start()
    assert save_started.wait(timeout=5)
    assert save_lock_attempted.wait(timeout=5), "legacy save never entered the project lock"
    assert not save_finished.wait(timeout=0.1), "legacy persistence bypassed the project lock"

    release_initializer.set()
    initializer.join(timeout=5)
    writer.join(timeout=5)
    assert not initializer.is_alive()
    assert not writer.is_alive()
    assert (root / fd.FILM_NAME).is_file()
    assert isinstance(outcome.get("error"), editor.EditorError)
    assert outcome["error"].code == "FILM_DOCUMENT_AUTHORITATIVE"
    assert composition_path.read_bytes() == before



def test_film_initialization_uses_unlocked_version_reader_inside_project_transaction(tmp_path, monkeypatch):
    from video_director import config, editor
    from video_director.models import CompositionIR, Layer, LayerType, ProjectSpec, SceneIR

    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "film-init-unlocked-version-read"
    ir = CompositionIR(
        project=ProjectSpec(width=160, height=90, fps=30, duration=1.0),
        scenes=[SceneIR(
            id="legacy-scene", start=0, duration=1.0, text="before migration",
            layers=[Layer(type=LayerType.image, duration=1.0)],
        )],
    )
    editor.save_composition(project_id, ir)
    source_version = editor.composition_version(project_id)
    document = fd.parse_film(SAMPLE)

    def forbidden_public_version_read(_project_id):
        raise AssertionError("Film initialization must not re-enter public project_lock")

    monkeypatch.setattr(editor, "composition_version", forbidden_public_version_read)
    initialized = fd.initialize_project_film(
        project_id, document, {}, source_version, [], selected_variant_id=None,
    )

    assert initialized["ok"] is True
    assert initialized["migration"]["source_composition_version"] == source_version
    assert fd.read_project_film(project_id)["exists"] is True


def test_legacy_composition_read_waits_for_film_initialization(tmp_path, monkeypatch):
    """A legacy reader queued before migration must recheck authority after the lock."""
    from contextlib import contextmanager
    from video_director import config, editor, film_document as fd
    from video_director.models import CompositionIR, Layer, LayerType, ProjectSpec, SceneIR
    from video_director.project_lock import project_lock

    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(tmp_path / "composer"))
    project_id = "legacy-read-film-init-race"
    root = config.project_dir(project_id)
    plans = root / "plans"
    plans.mkdir(parents=True, exist_ok=True)

    original_ir = CompositionIR(
        project=ProjectSpec(width=160, height=90, fps=30, duration=1.0),
        scenes=[SceneIR(
            id="legacy-scene", start=0, duration=1.0, text="before",
            layers=[Layer(type=LayerType.image, duration=1.0)],
        )],
    )
    editor.save_composition(project_id, original_ir)

    lock_held = threading.Event()
    release_initializer = threading.Event()
    read_started = threading.Event()
    read_lock_attempted = threading.Event()
    read_finished = threading.Event()
    outcome = {}
    original_editor_lock = editor.project_lock

    @contextmanager
    def observe_read_lock(pid):
        read_lock_attempted.set()
        with original_editor_lock(pid):
            yield

    monkeypatch.setattr(editor, "project_lock", observe_read_lock)

    def initialize_film_under_project_lock():
        with project_lock(project_id):
            lock_held.set()
            assert release_initializer.wait(timeout=5)
            (root / fd.FILM_NAME).write_text(SAMPLE, encoding="utf-8")

    def legacy_read():
        read_started.set()
        try:
            outcome["composition"] = editor.load_composition(project_id)
        except Exception as exc:
            outcome["error"] = exc
        finally:
            read_finished.set()

    initializer = threading.Thread(target=initialize_film_under_project_lock, daemon=True)
    initializer.start()
    assert lock_held.wait(timeout=5)

    reader = threading.Thread(target=legacy_read, daemon=True)
    reader.start()
    assert read_started.wait(timeout=5)
    assert read_lock_attempted.wait(timeout=5), "legacy read never entered the project lock"
    assert not read_finished.wait(timeout=0.1), "legacy read bypassed the project lock"

    release_initializer.set()
    initializer.join(timeout=5)
    reader.join(timeout=5)
    assert not initializer.is_alive()
    assert not reader.is_alive()
    assert (root / fd.FILM_NAME).is_file()
    assert isinstance(outcome.get("error"), editor.EditorError)
    assert outcome["error"].code == "FILM_DOCUMENT_AUTHORITATIVE"
    assert "composition" not in outcome

def test_apply_proposal_http_route_is_single_use_and_ignores_client_operations(tmp_path, monkeypatch):
    """The HTTP approval route writes the registered proposal once and rejects replay."""
    storage_root = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-proposal-single-use-api"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    before_bytes = film_path.read_bytes()
    before = fd.read_project_film(project_id)
    assert before["exists"] and before["rev"]

    class FakeModel:
        def generate(self, _prompt):
            return json.dumps({
                "summary": "将第二个片段移动到 8.5 秒",
                "operations": [{
                    "op": "move", "clip": "clip-b",
                    "track_id": "video-main", "at": 8.5,
                }],
            }, ensure_ascii=False)

    monkeypatch.setattr(web_server.vd_llm, "get_llm", lambda: FakeModel())
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post_json(route, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}{route}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    try:
        status, generated = post_json("/api/editor/film/command", {
            "project_id": project_id,
            "base": before["rev"],
            "message": "将 clip-b 移动到 8.5 秒",
        })
        assert status == 200, generated
        proposal = generated.get("proposal")
        assert isinstance(proposal, dict) and proposal.get("id"), generated
        assert proposal["base_revision"] == before["rev"]
        assert fd.read_project_film(project_id)["rev"] == before["rev"]
        assert film_path.read_bytes() == before_bytes, "generation must not write the accepted FilmDocument"

        # Deliberately send forged operation content. The server must consume only
        # the operation set retained in its proposal registry.
        approval = {
            "project_id": project_id,
            "proposal_id": proposal["id"],
            "base_revision": before["rev"],
            "ops": [{"op": "remove", "clip": "clip-a"}],
        }
        status, applied_response = post_json("/api/editor/film/apply-proposal", approval)
        assert status == 200, applied_response
        assert applied_response.get("ok") is True
        assert applied_response.get("base") == before["rev"]
        assert applied_response.get("source") == "ai_proposal"
        assert applied_response.get("applied_proposal") == proposal["id"]
        assert applied_response.get("rev") != before["rev"]

        applied = fd.read_project_film(project_id)
        applied_bytes = film_path.read_bytes()
        assert applied["rev"] == applied_response["rev"]
        clips = {
            clip["id"]: clip
            for track in applied["doc"]["tracks"]
            for clip in track["clips"]
        }
        assert "clip-a" in clips, "client-supplied ops must not override registered proposal operations"
        assert abs(float(clips["clip-b"]["attrs"]["at"]) - 8.5) < 0.001

        try:
            post_json("/api/editor/film/apply-proposal", approval)
        except urllib.error.HTTPError as error:
            retry_status = error.code
            retry_body = json.loads(error.read().decode("utf-8"))
        else:
            pytest.fail("a consumed proposal must not be applicable a second time")
        assert retry_status == 409, retry_body
        assert retry_body.get("code") == "PROPOSAL_NOT_FOUND_OR_STALE", retry_body

        after_retry = fd.read_project_film(project_id)
        assert after_retry["rev"] == applied["rev"], "replaying a proposal must not create another revision"
        assert film_path.read_bytes() == applied_bytes, "replaying a proposal must not rewrite film.html"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

def test_apply_proposal_concurrent_http_requests_commit_exactly_once(tmp_path, monkeypatch):
    """Two simultaneous approvals of one proposal must produce one write and one 409."""
    storage_root = tmp_path / "composer"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    import webui.server as web_server

    web_server = importlib.reload(web_server)
    web_server.COMPOSER_ROOT = storage_root

    project_id = "film-proposal-concurrent-api"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_text(SAMPLE, encoding="utf-8")
    original_bytes = film_path.read_bytes()
    before = fd.read_project_film(project_id)
    assert before["exists"] and before["rev"]

    class FakeModel:
        def generate(self, _prompt):
            return json.dumps({
                "summary": "将第二个片段移动到 8.5 秒",
                "operations": [{
                    "op": "move", "clip": "clip-b",
                    "track_id": "video-main", "at": 8.5,
                }],
            }, ensure_ascii=False)

    monkeypatch.setattr(web_server.vd_llm, "get_llm", lambda: FakeModel())
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"

    def send_json(route, payload):
        request = urllib.request.Request(
            endpoint + route,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    try:
        status, generated = send_json("/api/editor/film/command", {
            "project_id": project_id,
            "base": before["rev"],
            "message": "将 clip-b 移动到 8.5 秒",
        })
        assert status == 200, generated
        proposal = generated.get("proposal")
        assert isinstance(proposal, dict) and proposal.get("id"), generated
        assert proposal["base_revision"] == before["rev"]
        assert film_path.read_bytes() == original_bytes
        assert fd.read_project_film(project_id)["rev"] == before["rev"]

        approval = {
            "project_id": project_id,
            "proposal_id": proposal["id"],
            "base_revision": before["rev"],
            "ops": [{"op": "remove", "clip": "clip-a"}],
        }
        barrier = threading.Barrier(2)

        def approve_once():
            barrier.wait(timeout=10)
            request = urllib.request.Request(
                endpoint + "/api/editor/film/apply-proposal",
                data=json.dumps(approval, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read().decode("utf-8"))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(approve_once) for _ in range(2)]
            results = [future.result(timeout=25) for future in futures]

        successes = [(status, body) for status, body in results if status == 200]
        conflicts = [(status, body) for status, body in results if status == 409]
        assert len(successes) == 1, results
        assert len(conflicts) == 1, results
        applied_status, applied_payload = successes[0]
        assert applied_payload.get("ok") is True, applied_payload
        assert applied_payload.get("base") == before["rev"], applied_payload
        assert applied_payload.get("source") == "ai_proposal", applied_payload
        assert applied_payload.get("applied_proposal") == proposal["id"], applied_payload
        assert applied_payload.get("rev") != before["rev"], applied_payload
        assert conflicts[0][1].get("code") == "PROPOSAL_NOT_FOUND_OR_STALE", results

        final = fd.read_project_film(project_id)
        final_bytes = film_path.read_bytes()
        assert final["rev"] == applied_payload["rev"]
        assert final_bytes != original_bytes
        clips = {
            clip["id"]: clip
            for track in final["doc"]["tracks"]
            for clip in track["clips"]
        }
        assert "clip-a" in clips, "client-supplied operations must never replace registered proposal operations"
        assert abs(float(clips["clip-b"]["attrs"]["at"]) - 8.5) < 0.001
        assert applied_status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


