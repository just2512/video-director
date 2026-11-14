from __future__ import annotations

import pytest

from video_director import film_document as fd


FILM = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"></head>
<body>
<section id="video-main" data-label="主画面">
  <video id="clip-a" src="../assets/a.mp4#t=0,5" at="0"></video>
  <video id="clip-b" src="../assets/b.mp4#t=0,5" at="6"></video>
</section>
</body></html>'''


def test_film_markers_round_trip_through_the_authoritative_document():
    doc = fd.parse_film(FILM)
    assert doc["markers"] == []

    added, add_ops = fd.apply_ops(doc, [
        {"op": "marker_add", "id": "marker-intro", "at": 4.12349, "label": '片头 & "A" <B>'},
    ])
    assert add_ops == [
        {"op": "marker_add", "id": "marker-intro", "at": 4.123, "label": '片头 & "A" <B>'}
    ]
    serialized = fd.serialize_film(added)
    assert 'name="film-markers"' in serialized
    assert "&amp;" in serialized and "&lt;" in serialized
    restored = fd.parse_film(serialized)
    assert restored["markers"] == [{"id": "marker-intro", "at": 4.123, "label": '片头 & "A" <B>'}]

    updated, set_ops = fd.apply_ops(restored, [
        {"op": "marker_set", "id": "marker-intro", "at": 2.5, "label": "开场"},
    ])
    assert set_ops == [
        {"op": "marker_set", "id": "marker-intro", "at": 2.5, "label": "开场"}
    ]
    round_trip = fd.parse_film(fd.serialize_film(updated))
    assert round_trip["markers"] == [{"id": "marker-intro", "at": 2.5, "label": "开场"}]

    removed, remove_ops = fd.apply_ops(round_trip, [{"op": "marker_remove", "id": "marker-intro"}])
    assert remove_ops == [{"op": "marker_remove", "id": "marker-intro"}]
    assert removed["markers"] == []
    assert 'name="film-markers"' not in fd.serialize_film(removed)


def test_film_marker_operations_reject_invalid_or_stale_targets():
    doc = fd.parse_film(FILM)

    cases = [
        ({"op": "marker_add", "id": "bad/id", "at": 1, "label": "非法 ID"}, "INVALID_FILM_MARKER_ID"),
        ({"op": "marker_add", "id": "negative", "at": -1, "label": "负时间"}, "INVALID_FILM_MARKER_TIME"),
        ({"op": "marker_add", "id": "blank", "at": 1, "label": "   "}, "INVALID_FILM_MARKER_LABEL"),
    ]
    for operation, code in cases:
        with pytest.raises(fd.FilmError) as caught:
            fd.apply_ops(doc, [operation])
        assert caught.value.code == code

    doc_with_marker, _ = fd.apply_ops(doc, [
        {"op": "marker_add", "id": "stable-marker", "at": 1, "label": "目标"}
    ])
    with pytest.raises(fd.FilmError) as caught:
        fd.apply_ops(doc_with_marker, [
            {"op": "marker_add", "id": "stable-marker", "at": 2, "label": "重复"}
        ])
    assert caught.value.code == "DUPLICATE_FILM_MARKER_ID"

    with pytest.raises(fd.FilmError) as caught:
        fd.apply_ops(doc_with_marker, [{"op": "marker_set", "id": "deleted-marker", "label": "不能复用"}])
    assert caught.value.code == "FILM_MARKER_NOT_FOUND"
    with pytest.raises(fd.FilmError) as caught:
        fd.apply_ops(doc_with_marker, [{"op": "marker_remove", "id": "deleted-marker"}])
    assert caught.value.code == "FILM_MARKER_NOT_FOUND"

    with pytest.raises(fd.FilmError) as caught:
        fd.apply_ops(doc, [{"op": "marker_add", "id": "after-end", "at": 12, "label": "超出片尾"}])
    assert caught.value.code == "FILM_MARKER_OUT_OF_BOUNDS"


def test_film_marker_metadata_is_strict_and_rejects_duplicate_tags():
    malformed = FILM.replace(
        "</head>",
        '<meta name="film-markers" content="not-json"></head>',
    )
    with pytest.raises(fd.FilmError) as caught:
        fd.parse_film(malformed)
    assert caught.value.code == "INVALID_FILM_MARKERS"

    out_of_bounds = FILM.replace(
        "</head>",
        '<meta name="film-markers" content="[{&quot;id&quot;:&quot;after-end&quot;,&quot;at&quot;:12,&quot;label&quot;:&quot;超出片尾&quot;}]"></head>',
    )
    with pytest.raises(fd.FilmError) as caught:
        fd.parse_film(out_of_bounds)
    assert caught.value.code == "FILM_MARKER_OUT_OF_BOUNDS"

    duplicate = FILM.replace(
        "</head>",
        '<meta name="film-markers" content="[]"><meta name="film-markers" content="[]"></head>',
    )
    with pytest.raises(fd.FilmError) as caught:
        fd.parse_film(duplicate)
    assert caught.value.code == "DUPLICATE_FILM_MARKERS_META"


def test_film_markers_commit_as_one_revision_and_share_undo_redo_history(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    project_id = "film-document-markers-history"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    # write_bytes 保持 LF：Windows write_text 转 CRLF 会让 revision/original_text 漂移
    film_path.write_bytes(FILM.encode("utf-8"))
    original_text = film_path.read_text(encoding="utf-8")
    before = fd.read_project_film(project_id)

    edit = fd.edit_project_film(project_id, before["rev"], [
        {"op": "marker_add", "id": "marker-one", "at": 2.0, "label": "片头"},
        {"op": "marker_set", "id": "marker-one", "at": 3.0, "label": "开场"}
    ], source="studio")
    after = fd.read_project_film(project_id)
    assert after["rev"] == edit["rev"]
    assert after["rev"] != before["rev"]
    assert after["doc"]["markers"] == [{"id": "marker-one", "at": 3.0, "label": "开场"}]
    assert fd.parse_film(after["text"])["markers"] == after["doc"]["markers"]

    fd.undo_redo_project_film(project_id, after["rev"], "undo")
    undone = fd.read_project_film(project_id)
    assert undone["text"] == original_text
    assert undone["doc"]["markers"] == []

    fd.undo_redo_project_film(project_id, undone["rev"], "redo")
    redone = fd.read_project_film(project_id)
    assert redone["text"] == after["text"]
    assert redone["doc"]["markers"] == after["doc"]["markers"]


def test_marker_and_clip_mutations_share_one_atomic_revision_and_undo_redo_history(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("VIDEO_DIRECTOR_STORAGE_ROOT", str(storage_root))
    project_id = "film-document-marker-clip-atomicity"
    project_root = storage_root / project_id
    project_root.mkdir(parents=True)
    film_path = project_root / fd.FILM_NAME
    film_path.write_bytes(FILM.encode("utf-8"))

    before = fd.read_project_film(project_id)
    original_text = before["text"]
    edit = fd.edit_project_film(project_id, before["rev"], [
        {"op": "marker_add", "id": "marker-one", "at": 2.0, "label": "片头"},
        {"op": "set", "clip": "clip-a", "field": "title", "value": "已更新标题"},
    ], source="studio")

    after = fd.read_project_film(project_id)
    assert after["rev"] == edit["rev"]
    assert after["rev"] != before["rev"]
    assert after["doc"]["markers"] == [{"id": "marker-one", "at": 2.0, "label": "片头"}]
    clip_a = next(
        clip for track in after["doc"]["tracks"] for clip in track["clips"]
        if clip["id"] == "clip-a"
    )
    assert clip_a["attrs"]["title"] == "已更新标题"

    fd.undo_redo_project_film(project_id, after["rev"], "undo")
    undone = fd.read_project_film(project_id)
    assert undone["text"] == original_text
    assert undone["doc"]["markers"] == []
    undone_clip_a = next(
        clip for track in undone["doc"]["tracks"] for clip in track["clips"]
        if clip["id"] == "clip-a"
    )
    assert "title" not in undone_clip_a["attrs"]

    fd.undo_redo_project_film(project_id, undone["rev"], "redo")
    redone = fd.read_project_film(project_id)
    assert redone["text"] == after["text"]
    assert redone["doc"]["markers"] == after["doc"]["markers"]


def test_marker_then_invalid_clip_operation_leaves_input_document_unchanged():
    doc = fd.parse_film(FILM)
    original = fd.copy.deepcopy(doc)

    with pytest.raises(fd.FilmError, match="不存在或已被删除"):
        fd.apply_ops(doc, [
            {"op": "marker_add", "id": "marker-one", "at": 2.0, "label": "片头"},
            {"op": "set", "clip": "missing-clip", "field": "title", "value": "不能提交"},
        ])

    assert doc == original
