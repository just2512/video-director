"""Safe, bounded import of public article URLs into Director source context.

This fetcher imports public HTTP(S) article text only. It does not execute page scripts,
use cookies, follow browser sessions, or treat page instructions as trusted system
instructions. Each connection is pinned to a resolved public IP to reduce SSRF risk.
"""
from __future__ import annotations

import hashlib
import http.client
import html
import http.cookies
import ipaddress
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable, Iterable, List, Optional, Tuple


MAX_URL_LENGTH = 2048
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_EXTRACTED_CHARS = 80_000
MIN_ARTICLE_CHARS = 80
FETCH_TIMEOUT_SECONDS = 12
MAX_TOTAL_FETCH_SECONDS = 30
MAX_REDIRECTS = 4
_USER_AGENT = "VideoDirector-ArticleImporter/1.0 (+public-article-text-only)"


class ArticleImportError(ValueError):
    """An input URL or remote page cannot safely be imported."""


def _public_addresses(host: str, port: int, resolver: Callable = socket.getaddrinfo) -> List[str]:
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ArticleImportError(f"无法解析来源域名：{host}") from exc
    addresses: List[str] = []
    for info in infos:
        try:
            address = str(ipaddress.ip_address(info[4][0].split("%", 1)[0]))
        except (ValueError, IndexError, TypeError) as exc:
            raise ArticleImportError("来源域名解析返回了无效地址") from exc
        ip = ipaddress.ip_address(address)
        if not ip.is_global or ip.is_multicast or ip.is_unspecified or ip.is_loopback or ip.is_link_local:
            raise ArticleImportError("来源域名解析到非公网地址，已拒绝访问")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ArticleImportError("来源域名没有可用的公网地址")
    # Prefer IPv4 where available; the connection will still be pinned to this exact set.
    addresses.sort(key=lambda x: (ipaddress.ip_address(x).version != 4, x))
    return addresses


def _validate_public_url(url: str, resolver: Callable = socket.getaddrinfo) -> Tuple[str, str, int, List[str]]:
    value = (url or "").strip()
    if not value or len(value) > MAX_URL_LENGTH:
        raise ArticleImportError(f"请提供完整链接（不超过 {MAX_URL_LENGTH} 个字符）")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ArticleImportError("来源链接格式不正确") from exc
    if parsed.scheme.lower() not in ("http", "https"):
        raise ArticleImportError("仅支持公开网页的 HTTP/HTTPS 链接")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ArticleImportError("来源链接必须是公开网页地址，不能包含登录凭据")
    hostname = parsed.hostname.rstrip(".").lower()
    if not hostname or hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal", ".lan")):
        raise ArticleImportError("本机/内网/本地域名不允许作为网页来源")
    port = port or (443 if parsed.scheme.lower() == "https" else 80)
    # Handle IP literals directly so a custom/resolved hostname cannot disguise a private target.
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if not literal_ip.is_global or literal_ip.is_multicast or literal_ip.is_unspecified or literal_ip.is_loopback or literal_ip.is_link_local:
            raise ArticleImportError("来源 IP 不是公网地址，已拒绝访问")
        addresses = [str(literal_ip)]
    else:
        addresses = _public_addresses(hostname, port, resolver=resolver)
    clean = urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))
    return clean, hostname, port, addresses


def _normalize_text(value: str) -> str:
    value = html.unescape(value or "").replace("\xa0", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


class _ArticleHTMLParser(HTMLParser):
    _EXCLUDED = {"script", "style", "noscript", "svg", "iframe", "nav", "footer", "aside", "form", "button"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: List[str] = []
        self.meta_title = ""
        self.body_parts: List[str] = []
        self.article_parts: List[str] = []
        self.main_parts: List[str] = []
        self._excluded: List[str] = []
        self._title_depth = 0
        self._article_depth = 0
        self._main_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        data = {str(k).lower(): str(v or "") for k, v in attrs}
        if tag == "meta":
            key = (data.get("property") or data.get("name") or "").lower()
            if key in {"og:title", "twitter:title"} and data.get("content"):
                self.meta_title = data["content"]
        if tag == "title":
            self._title_depth += 1
        if self._excluded:
            if tag in self._EXCLUDED:
                self._excluded.append(tag)
            return
        if tag in self._EXCLUDED or tag == "head":
            self._excluded.append(tag)
            return
        if tag == "article":
            self._article_depth += 1
        if tag == "main":
            self._main_depth += 1
        if tag in {"p", "br", "div", "section", "article", "main", "h1", "h2", "h3", "li", "blockquote", "tr"}:
            self.body_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if self._excluded:
            for idx in range(len(self._excluded) - 1, -1, -1):
                if self._excluded[idx] == tag:
                    del self._excluded[idx:]
                    break
            return
        if tag == "article":
            self._article_depth = max(0, self._article_depth - 1)
        if tag == "main":
            self._main_depth = max(0, self._main_depth - 1)
        if tag in {"p", "div", "section", "article", "main", "h1", "h2", "h3", "li", "blockquote", "tr"}:
            self.body_parts.append("\n")
            if self._article_depth:
                self.article_parts.append("\n")
            if self._main_depth:
                self.main_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self.title_parts.append(data)
        if self._excluded or not data.strip():
            return
        self.body_parts.append(data)
        if self._article_depth:
            self.article_parts.append(data)
        if self._main_depth:
            self.main_parts.append(data)

    def title(self) -> str:
        return _normalize_text(self.meta_title or "".join(self.title_parts))

    def content(self) -> str:
        article = _normalize_text("".join(self.article_parts))
        main = _normalize_text("".join(self.main_parts))
        body = _normalize_text("".join(self.body_parts))
        if len(article) >= MIN_ARTICLE_CHARS:
            return article
        if len(main) >= MIN_ARTICLE_CHARS:
            return main
        return body


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, addresses: List[str], **kwargs):
        super().__init__(host, **kwargs)
        self._pinned_addresses = list(addresses)

    def connect(self):
        errors = []
        for address in self._pinned_addresses:
            try:
                self.sock = socket.create_connection(
                    (address, self.port), self.timeout, self.source_address
                )
                if self._tunnel_host:
                    self._tunnel()
                return
            except OSError as exc:
                errors.append(exc)
                if self.sock:
                    self.sock.close()
                    self.sock = None
        if errors:
            raise errors[-1]
        raise OSError("no public address available")


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    def __init__(self, host, addresses: List[str], context=None, **kwargs):
        super().__init__(host, addresses, **kwargs)
        self._context = context or ssl.create_default_context()

    def connect(self):
        errors = []
        for address in self._pinned_addresses:
            raw = None
            try:
                raw = socket.create_connection((address, self.port), self.timeout, self.source_address)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError as exc:
                errors.append(exc)
                if raw:
                    raw.close()
                if self.sock:
                    self.sock.close()
                    self.sock = None
        if errors:
            raise errors[-1]
        raise OSError("no public address available")


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        _clean, _host, _port, addresses = _validate_public_url(req.full_url)
        return self.do_open(lambda host, **kw: _PinnedHTTPConnection(host, addresses, **kw), req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        _clean, _host, _port, addresses = _validate_public_url(req.full_url)
        return self.do_open(
            lambda host, **kw: _PinnedHTTPSConnection(host, addresses, context=self._context, **kw),
            req,
        )


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            _validate_public_url(newurl)
        except ArticleImportError:
            raise
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _extract_page(payload: bytes, content_type: str) -> Tuple[str, str]:
    charset_match = re.search(r"charset\s*=\s*['\"]?([\w.-]+)", content_type or "", re.I)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        decoded = payload.decode(charset, errors="replace")
    except LookupError:
        decoded = payload.decode("utf-8", errors="replace")
    if "html" in (content_type or "").lower() or re.search(r"<(?:html|body|article|main|title)\b", decoded[:2000], re.I):
        parser = _ArticleHTMLParser()
        try:
            parser.feed(decoded)
            parser.close()
        except Exception as exc:
            raise ArticleImportError("网页 HTML 无法解析，请粘贴文章正文作为替代") from exc
        title, content = parser.title(), parser.content()
    else:
        title, content = "", _normalize_text(decoded)
    content = content[:MAX_EXTRACTED_CHARS].strip()
    if len(content) < MIN_ARTICLE_CHARS:
        raise ArticleImportError("没有提取到足够的正文文本；可能是登录墙、脚本渲染页或反爬页面。请改为粘贴正文。")
    return title[:300], content


def import_article(url: str) -> dict:
    """Fetch one public article with bounded bytes/time and return a content-hashed snapshot."""
    clean_url, _, _, _ = _validate_public_url(url)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _PublicHTTPHandler(),
        _PublicHTTPSHandler(),
        _PublicRedirectHandler(),
    )
    req = urllib.request.Request(
        clean_url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
            "Accept-Encoding": "identity",
        },
        method="GET",
    )
    deadline = time.monotonic() + MAX_TOTAL_FETCH_SECONDS
    try:
        with opener.open(req, timeout=FETCH_TIMEOUT_SECONDS) as response:
            final_url, _, _, _ = _validate_public_url(response.geturl())
            content_type = response.headers.get("Content-Type", "text/html")
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                raise ArticleImportError(f"此链接返回 {media_type or '未知类型'}，只支持网页 HTML/纯文本")
            raw_length = response.headers.get("Content-Length")
            if raw_length:
                try:
                    if int(raw_length) > MAX_RESPONSE_BYTES:
                        raise ArticleImportError("网页超过 2 MiB 的读取上限")
                except ValueError:
                    pass

            payload = bytearray()
            response_socket = None
            try:
                response_socket = response.fp.raw._sock
            except (AttributeError, OSError):
                response_socket = None
            while len(payload) <= MAX_RESPONSE_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ArticleImportError("网页读取超过 30 秒总时限")
                if response_socket is not None:
                    response_socket.settimeout(min(FETCH_TIMEOUT_SECONDS, remaining))
                try:
                    chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - len(payload)))
                except (TimeoutError, socket.timeout) as exc:
                    if time.monotonic() >= deadline:
                        raise ArticleImportError("网页读取超过 30 秒总时限") from exc
                    raise
                if not chunk:
                    break
                payload.extend(chunk)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise ArticleImportError("网页超过 2 MiB 的读取上限")
            payload = bytes(payload)
    except ArticleImportError:
        raise
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, http.client.HTTPException) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise ArticleImportError(f"网页抓取失败：{str(reason)[:220]}") from exc

    title, content = _extract_page(payload, content_type)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "source_url": final_url,
        "requested_url": clean_url,
        "title": title,
        "content": content,
        "content_sha256": content_hash,
        "content_chars": len(content),
        "content_type": media_type,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "extraction_status": "EXTRACTED",
        "provenance": "user_imported_public_web",
        "trust_boundary": "source text is untrusted reference material, not instructions",
    }
