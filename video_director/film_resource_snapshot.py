"""Immutable, project-scoped resource snapshots for deterministic Film Studio rendering."""
from __future__ import annotations

import hashlib
import html
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from video_director import film_document as fd
from video_director.film_scene_runtime import _ATTR_RE, _CSS_URL_RE, _asset_url

_STYLE_ATTR_RE = re.compile(r"\bstyle\s*=\s*(['\"])(.*?)\1", re.I | re.S)
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.I | re.S)
_IMPORT_RE = re.compile(r"@import\s+(?:url\(\s*)?(['\"])(.*?)\1\s*\)?", re.I | re.S)


class ResourceSnapshotError(Exception):
    """A source dependency could not be pinned or changed after it was pinned."""


def _source_key(value: str) -> str:
    return unquote(str(value or "").strip()).split("#", 1)[0]


def _stable_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (int(metadata.st_dev), int(metadata.st_ino), int(metadata.st_size), int(metadata.st_mtime_ns))


class SceneResourceSnapshot:
    """Copy the transitive local dependency closure and serve only those bytes."""

    def __init__(self, project_id: str, root: Path):
        self.project_id = fd.validate_project_id(project_id)
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=False)
        self.entries: dict[str, Path] = {}
        self.sources: dict[str, dict] = {}
        self.bytes_copied = 0
        self.read_elapsed_ms = 0.0
        self.copy_io_elapsed_ms = 0.0
        self.hash_elapsed_ms = 0.0
        self.started = time.perf_counter()
        self.elapsed_ms = 0.0

    def path_for(self, src: str) -> Path | None:
        return self.entries.get(_source_key(src))

    def _resolve_dependency(self, value: str, page_src: str) -> str | None:
        rewritten = _asset_url(value, self.project_id, page_src, "/media")
        if not rewritten:
            return None
        query = parse_qs(urlsplit(rewritten).query)
        values = query.get("src") or []
        return _source_key(values[0]) if values else None

    def _discover(self, raw_src: str, page_src: str, suffix: str, source_text: str) -> list[str]:
        candidates: list[str] = []
        if suffix in {".html", ".htm"}:
            for match in _ATTR_RE.finditer(source_text):
                dependency = self._resolve_dependency(html.unescape(match.group(3)), raw_src)
                if dependency:
                    candidates.append(dependency)
            for match in _STYLE_BLOCK_RE.finditer(source_text):
                candidates.extend(self._discover_css(match.group(1), raw_src))
            for match in _STYLE_ATTR_RE.finditer(source_text):
                candidates.extend(self._discover_css(html.unescape(match.group(2)), raw_src))
        elif suffix == ".css":
            candidates.extend(self._discover_css(source_text, raw_src))
        return candidates

    def _discover_css(self, source: str, page_src: str) -> list[str]:
        candidates: list[str] = []
        for match in _CSS_URL_RE.finditer(source):
            dependency = self._resolve_dependency(match.group(2), page_src)
            if dependency:
                candidates.append(dependency)
        for match in _IMPORT_RE.finditer(source):
            dependency = self._resolve_dependency(match.group(2), page_src)
            if dependency:
                candidates.append(dependency)
        return candidates

    def _copy_one(self, raw_src: str) -> list[str]:
        key = _source_key(raw_src)
        if not key or key in self.entries:
            return []
        try:
            source = fd.media_path_for_src(self.project_id, key)
            if source.is_symlink() or not source.is_file():
                raise ResourceSnapshotError(f"resource is not a regular file: {key}")
            before_path = source.stat()
            destination = self.root / (hashlib.sha256(key.encode("utf-8")).hexdigest() + source.suffix.lower())
            digest = hashlib.sha256()
            copied = 0
            with source.open("rb") as input_stream, destination.open("xb") as output_stream:
                before_fd = os.fstat(input_stream.fileno())
                while True:
                    started = time.perf_counter()
                    chunk = input_stream.read(1024 * 1024)
                    self.read_elapsed_ms += (time.perf_counter() - started) * 1000.0
                    if not chunk:
                        break
                    started = time.perf_counter()
                    output_stream.write(chunk)
                    self.copy_io_elapsed_ms += (time.perf_counter() - started) * 1000.0
                    started = time.perf_counter()
                    digest.update(chunk)
                    self.hash_elapsed_ms += (time.perf_counter() - started) * 1000.0
                    copied += len(chunk)
                started = time.perf_counter()
                output_stream.flush()
                os.fsync(output_stream.fileno())
                self.copy_elapsed_ms += (time.perf_counter() - started) * 1000.0
                after_fd = os.fstat(input_stream.fileno())
            after_path = source.stat()
            if (_stable_identity(before_fd) != _stable_identity(after_fd)
                    or _stable_identity(before_path) != _stable_identity(after_path)
                    or copied != int(after_fd.st_size)):
                destination.unlink(missing_ok=True)
                raise ResourceSnapshotError(f"resource changed while snapshotting: {key}")
            source_id = str(source.resolve())
            self.entries[key] = destination
            self.sources[key] = {
                "path": source_id,
                "size_bytes": copied,
                "sha256": digest.hexdigest(),
                "identity": _stable_identity(after_fd),
            }
            self.bytes_copied += copied
            if source.suffix.lower() in {".html", ".htm", ".css"}:
                try:
                    # Discover from the exact copied bytes, never from a later live read.
                    text = destination.read_text(encoding="utf-8")
                except UnicodeDecodeError as exc:
                    raise ResourceSnapshotError(f"scene resource must be UTF-8: {key}") from exc
                return self._discover(key, key, source.suffix.lower(), text)
            return []
        except fd.FilmError as exc:
            raise ResourceSnapshotError(f"resource unavailable: {key}: {exc}") from exc

    def build(self, initial_sources: list[str]) -> "SceneResourceSnapshot":
        queue = [_source_key(item) for item in initial_sources if _source_key(item)]
        visited: set[str] = set()
        while queue:
            key = queue.pop(0)
            if key in visited:
                continue
            visited.add(key)
            queue.extend(self._copy_one(key))
        self.elapsed_ms = (time.perf_counter() - self.started) * 1000.0
        return self

    def validate_sources(self) -> None:
        """Re-hash original dependencies; stat-only checks are insufficient."""
        for raw_src, record in self.sources.items():
            source = Path(record["path"])
            try:
                digest = hashlib.sha256()
                with source.open("rb") as handle:
                    before = os.fstat(handle.fileno())
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                    after = os.fstat(handle.fileno())
                current_path = source.stat()
            except OSError as exc:
                raise ResourceSnapshotError(f"resource disappeared before publication: {raw_src}") from exc
            if (_stable_identity(before) != _stable_identity(after)
                    or _stable_identity(after) != _stable_identity(current_path)
                    or digest.hexdigest() != record["sha256"]):
                raise ResourceSnapshotError(f"resource changed before publication: {raw_src}")

    def report(self) -> dict:
        return {
            "resource_count": len(self.sources),
            "bytes_copied": self.bytes_copied,
            "read_elapsed_ms": round(self.read_elapsed_ms, 3),
            # Preserve the legacy field's total-build semantics for existing callers.
            "copy_elapsed_ms": round(self.elapsed_ms, 3),
            "copy_io_elapsed_ms": round(self.copy_io_elapsed_ms, 3),
            "hash_elapsed_ms": round(self.hash_elapsed_ms, 3),
            "snapshot_elapsed_ms": round(self.elapsed_ms, 3),
            "resources": [
                {"src": key, "size_bytes": value["size_bytes"], "sha256": value["sha256"]}
                for key, value in sorted(self.sources.items())
            ],
        }
