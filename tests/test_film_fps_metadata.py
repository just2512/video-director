"""The FilmDocument keeps FPS authoritative across parse/serialize operations."""
from __future__ import annotations

import json

import pytest

from video_director import config, film_document as fd


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

SLIDE_SAMPLE = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"><style>body{margin:0}</style></head>
<body><section id="video-main" data-label="主画面">
<video id="slide-left" src="../assets/a.mp4#t=0,2" at="0" speed="1" volume="1"></video>
<video id="slide-middle" src="../assets/a.mp4#t=2,5" at="2" speed="1" volume="1"></video>
<video id="slide-right" src="../assets/a.mp4#t=5,9" at="5" speed="1" volume="1"></video>
</section></body></html>'''

HTML_SCENE_SAMPLE = '''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=1920,height=1080"></head>
<body>
<section id="scene-track" data-label="网页镜头">
<iframe id="scene" src="scenes/title.html#t=0,4" at="0"></iframe>
</section>
</body></html>'''


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
            {"at": 0, "value": 0, "easing": "ease_in"},
            {"at": 4, "value": 1, "easing": "ease_out"},
            {"at": 8, "value": 0.5, "easing": "linear"},
            {"at": 12, "value": 1, "easing": "ease_in_out"},
        ],
    }]
    source, _ = fd.apply_ops(doc, [
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
            {"at": 0, "value": 0, "easing": "ease_in"},
            {"at": 4, "value": 1, "easing": "ease_out"},
            {"at": 8, "value": 0.5, "easing": "linear"},
            {"at": 10, "value": 1, "easing": "ease_in_out"},
        ],
    }]
    source, _ = fd.apply_ops(doc, [
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
