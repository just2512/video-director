from __future__ import annotations

from video_director.film_scene_runtime import (
    inject_scene_css,
    inject_scene_runtime,
)


def test_scene_bridge_is_injected_before_author_scripts():
    source = """<!doctype html><html><head><meta charset="utf-8"><script>window.authored = true;</script></head><body></body></html>"""
    result = inject_scene_runtime(source, "film-test", "scenes/title.html")
    assert result.index('data-vd-film-bridge="1"') < result.index("window.authored = true")
    assert "window.film.frame(t)" in result
    assert "film-frame:ready" in result


def test_scene_relative_media_paths_are_rewritten_inside_project_boundary():
    source = """<html><head><style>.card{background-image:url('../assets/card.png')}</style></head><body><img src="../assets/logo.png"><video poster="../assets/poster.jpg"></video><a href="#credits">credits</a><img src="data:image/png;base64,AAAA"><img src="https://example.com/remote.png"></body></html>"""
    result = inject_scene_runtime(source, "film-test", "scenes/title.html")
    assert "src=\"/api/editor/film/media?project_id=film-test&amp;src=assets%2Flogo.png\"" in result
    assert "poster=\"/api/editor/film/media?project_id=film-test&amp;src=assets%2Fposter.jpg\"" in result
    assert "url('/api/editor/film/media?project_id=film-test&src=assets%2Fcard.png')" in result
    assert 'href="#credits"' in result
    assert "data:image/png;base64,AAAA" in result
    assert "https://example.com/remote.png" in result


def test_css_rewriter_resolves_paths_relative_to_the_stylesheet():
    source = ".hero { background: url('../images/hero.webp'); mask: url(#shape); }"
    result = inject_scene_css(source, "film-test", "scenes/css/theme.css", "/media")
    assert "url('/media?project_id=film-test&src=images%2Fhero.webp')" in result
    assert "url(#shape)" in result


def test_bridge_contract_includes_exact_time_and_deterministic_primitives():
    source = inject_scene_runtime(
        "<html><head></head><body></body></html>",
        "film-test",
        "scenes/motion.html",
    )
    assert "NativeDate" in source
    assert "Math.random = function" in source
    assert "window.performance, 'now'" in source
    assert "animation.currentTime = t * 1000" in source
    assert "window.film.frame(t)" in source
