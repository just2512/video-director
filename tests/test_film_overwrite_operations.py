from __future__ import annotations

import pytest

from video_director import film_document as fd


def _film(body: str) -> dict:
    return fd.parse_film(
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=640,height=360"></head><body>'
        + body + '</body></html>'
    )


def _asset(clip_id: str = "overwrite-asset", source: str = "../assets/new.mp4#t=0,3") -> dict:
    return {
        "id": clip_id, "tag": "video",
        "attrs": {"id": clip_id, "src": source, "at": "0", "volume": "1"},
    }


def test_overwrite_trims_edges_removes_covered_clip_and_preserves_other_tracks():
    doc = _film(
        '<section id="video-main" data-label="画面">'
        '<video id="left" src="../assets/a.mp4#t=0,2" at="0"></video>'
        '<video id="covered" src="../assets/b.mp4#t=0,1" at="2"></video>'
        '<video id="right" src="../assets/c.mp4#t=0,2" at="3"></video>'
        '</section>'
        '<section id="audio-main" data-label="旁白">'
        '<audio id="voice" src="../assets/voice.wav#t=0,5" at="0"></audio>'
        '</section>'
    )
    audio_before = doc["tracks"][1]
    operations = fd.build_overwrite_operations(doc, "video-main", _asset(), 1)
    updated, normalized = fd.apply_ops(doc, operations)

    video = {clip["id"]: clip for clip in updated["tracks"][0]["clips"]}
    assert video["left"]["attrs"]["src"].endswith("#t=0,1")
    assert "covered" not in video
    assert video["right"]["attrs"]["src"].endswith("#t=1,2")
    assert float(video["right"]["attrs"]["at"]) == 4
    assert video["overwrite-asset"]["attrs"]["src"].endswith("#t=0,3")
    assert float(video["overwrite-asset"]["attrs"]["at"]) == 1
    assert updated["tracks"][1] == audio_before
    assert [op["op"] for op in normalized] == ["trim", "remove", "trim", "insert"]
    assert fd.validate_film(fd.serialize_film(updated)) == []


def test_overwrite_splits_clip_covering_both_boundaries():
    doc = _film(
        '<section id="video-main" data-label="画面">'
        '<video id="long-shot" src="../assets/a.mp4#t=0,6" at="0"></video>'
        '</section>'
    )
    operations = fd.build_overwrite_operations(
        doc, "video-main", _asset(source="../assets/new.mp4#t=0,2"), 2
    )
    updated, normalized = fd.apply_ops(doc, operations)
    clips = {clip["id"]: clip for clip in updated["tracks"][0]["clips"]}

    assert clips["long-shot"]["attrs"]["src"].endswith("#t=0,2")
    right = next(clip for clip_id, clip in clips.items() if clip_id not in {"long-shot", "overwrite-asset"})
    assert float(right["attrs"]["at"]) == 4
    assert right["attrs"]["src"].endswith("#t=4,6")
    assert float(clips["overwrite-asset"]["attrs"]["at"]) == 2
    assert clips["overwrite-asset"]["attrs"]["src"].endswith("#t=0,2")
    assert [op["op"] for op in normalized] == ["split", "split", "remove", "insert"]
    assert fd.validate_film(fd.serialize_film(updated)) == []


def test_overwrite_rejects_locked_target_track_and_locked_overlap_clip():
    locked_track = _film(
        '<section id="video-main" data-label="画面" locked>'
        '<video id="shot" src="../assets/a.mp4#t=0,5" at="0"></video>'
        '</section>'
    )
    with pytest.raises(fd.FilmError) as track_error:
        fd.build_overwrite_operations(locked_track, "video-main", _asset(source="../assets/new.mp4#t=0,2"), 1)
    assert track_error.value.code == "TRACK_LOCKED"

    locked_clip = _film(
        '<section id="video-main" data-label="画面">'
        '<video id="shot" src="../assets/a.mp4#t=0,5" at="0" data-clip-locked></video>'
        '</section>'
    )
    with pytest.raises(fd.FilmError) as clip_error:
        fd.build_overwrite_operations(locked_clip, "video-main", _asset(source="../assets/new.mp4#t=0,2"), 1)
    assert clip_error.value.code == "CLIP_LOCKED"
