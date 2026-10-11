from __future__ import annotations

import hashlib

import pytest

from video_director.article_import import (
    ArticleImportError,
    _ArticleHTMLParser,
    _extract_page,
    _validate_public_url,
)


def _resolver(address):
    def resolve(host, port, type=None):
        family = 2 if ":" not in address else 10
        return [(family, 1, 6, "", (address, port))]
    return resolve


def test_public_article_url_is_normalized_and_fragment_removed():
    clean, host, port, addresses = _validate_public_url(
        "https://news.example/story?id=7#comments", resolver=_resolver("93.184.216.34")
    )
    assert clean == "https://news.example/story?id=7"
    assert host == "news.example"
    assert port == 443
    assert addresses == ["93.184.216.34"]


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/article",
    "http://user:secret@example.com/article",
    "http://localhost:8080/",
    "http://127.0.0.1/",
    "https://example.local/",
])
def test_non_public_or_unsafe_url_forms_are_rejected(url):
    with pytest.raises(ArticleImportError):
        _validate_public_url(url, resolver=_resolver("93.184.216.34"))


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.2", "169.254.169.254", "::1", "fd00::2"])
def test_private_resolved_addresses_are_rejected(address):
    with pytest.raises(ArticleImportError):
        _validate_public_url("https://news.example/article", resolver=_resolver(address))


def test_html_extractor_prefers_article_and_ignores_script_navigation_and_footer():
    html = """
    <html><head><title>Original title</title><script>secret()</script></head><body>
      <nav>Home Menu Skip</nav><main>
        <h1>Article heading</h1><article><p>First real paragraph with enough words to represent a useful article body.</p>
        <p>Second paragraph provides additional detail and a concrete explanation for the reader.</p></article>
      </main><footer>Footer boilerplate to ignore.</footer><script>ignore me</script>
    </body></html>
    """
    title, content = _extract_page(html.encode(), "text/html; charset=utf-8")
    assert title == "Original title"
    assert "First real paragraph" in content
    assert "Second paragraph" in content
    assert "Home Menu" not in content
    assert "Footer boilerplate" not in content
    assert "ignore me" not in content
    assert hashlib.sha256(content.encode()).hexdigest()


def test_body_text_is_bounded_and_rejects_login_wall_or_empty_page():
    with pytest.raises(ArticleImportError, match="没有提取到足够的正文"):
        _extract_page(b"<html><body><div>Please log in</div></body></html>", "text/html")
    assert _extract_page(("x" * 200).encode(), "text/plain")[1] == "x" * 200


def test_page_extraction_limits_retained_article_characters():
    title, content = _extract_page(("alpha " * 30000).encode(), "text/plain")
    assert title == ""
    assert len(content) == 80_000
