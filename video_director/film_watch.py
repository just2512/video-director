"""Debounced watcher for local Agent writes to active film.html documents."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

from video_director import film_document as fd


class FilmRevisionWatcher:
    """Watch registered projects, validate settled file snapshots, publish SSE events."""

    def __init__(
        self,
        publish: Callable[[str, dict], None],
        *,
        path_for_project: Callable[[str], Path] | None = None,
        settle_seconds: float = 0.12,
        interval_seconds: float = 0.05,
        scene_scan_interval_seconds: float = 0.2,
        content_audit_interval_seconds: float = 1.0,
        scene_content_audit_interval_seconds: float = 10.0,
        stale_after_seconds: float = 600.0,
    ):
        self.publish = publish
        self.path_for_project = path_for_project or fd._film_path
        self.settle_seconds = max(0.05, float(settle_seconds))
        self.interval_seconds = max(0.02, float(interval_seconds))
        self.scene_scan_interval_seconds = max(0.1, float(scene_scan_interval_seconds))
        # Stat signatures catch normal writes cheaply. Periodic content audits
        # also catch in-place edits that preserve inode, size and mtime_ns.
        self.content_audit_interval_seconds = max(0.1, float(content_audit_interval_seconds))
        # Scene trees may contain large media files. Keep stat polling cheap and
        # stream a full content audit on a separately bounded, slower cadence.
        self.scene_content_audit_interval_seconds = max(0.1, float(scene_content_audit_interval_seconds))
        self.stale_after_seconds = max(10.0, float(stale_after_seconds))
        self._lock = threading.RLock()
        self._projects: dict[str, dict] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _signature(path: Path):
        try:
            stat = path.stat()
            return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
        except OSError:
            return None

    @staticmethod
    def _scan_scene_files(
        root: Path, *, project_root: Path | None = None,
    ) -> dict[str, tuple[int, int, int, int]]:
        """Snapshot project-local regular files, never following symlinks."""
        try:
            # Check before resolve(): a scenes/ symlink to an external directory
            # must not become indistinguishable from an ordinary real directory.
            if root.is_symlink():
                return {}
            resolved_root = root.resolve()
            resolved_project = project_root.resolve() if project_root is not None else resolved_root.parent
            resolved_root.relative_to(resolved_project)
            if resolved_root == resolved_project or not resolved_root.is_dir():
                return {}
        except (OSError, ValueError):
            return {}

        snapshot: dict[str, tuple[int, int, int, int]] = {}
        try:
            # Path.rglob can traverse symlinked directory entries on some Python
            # versions. os.walk with followlinks=False plus pruning makes the
            # boundary explicit for both file and directory symlinks.
            for directory, dir_names, file_names in os.walk(
                resolved_root, topdown=True, followlinks=False
            ):
                parent = Path(directory)
                dir_names[:] = sorted(
                    name for name in dir_names
                    if not name.startswith(".") and not (parent / name).is_symlink()
                )
                for name in sorted(file_names):
                    candidate = parent / name
                    try:
                        if candidate.is_symlink() or not candidate.is_file():
                            continue
                        resolved = candidate.resolve()
                        relative = resolved.relative_to(resolved_root)
                        if any(part.startswith(".") for part in relative.parts):
                            continue
                        lowered = relative.name.lower()
                        if lowered.endswith(("~", ".tmp", ".temp", ".swp", ".swo", ".bak", ".part")):
                            continue
                        stat = resolved.stat()
                        snapshot[relative.as_posix()] = (
                            int(stat.st_dev), int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns)
                        )
                    except (OSError, ValueError):
                        continue
        except OSError:
            return snapshot
        return snapshot

    @staticmethod
    def _scene_tree_signature(snapshot: dict[str, tuple[int, int, int, int]]) -> str:
        stable = [[path, *signature] for path, signature in sorted(snapshot.items())]
        return hashlib.sha256(
            json.dumps(stable, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _scan_scene_file_contents(
        root: Path,
        *,
        project_root: Path,
        files: dict[str, tuple[int, int, int, int]],
    ) -> dict[str, str] | None:
        """Hash a stable, project-local scene snapshot without loading files into memory."""
        try:
            if root.is_symlink():
                return {}
            resolved_root = root.resolve()
            resolved_project = project_root.resolve()
            resolved_root.relative_to(resolved_project)
            if resolved_root == resolved_project or not resolved_root.is_dir():
                return {}
        except (OSError, ValueError):
            return {}

        contents: dict[str, str] = {}
        for relative_path, expected in sorted(files.items()):
            candidate = resolved_root / relative_path
            try:
                if candidate.is_symlink():
                    return None
                resolved = candidate.resolve()
                resolved.relative_to(resolved_root)
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(str(candidate), flags)
                with os.fdopen(descriptor, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    before_signature = (
                        int(before.st_dev), int(before.st_ino),
                        int(before.st_size), int(before.st_mtime_ns),
                    )
                    if before_signature != expected:
                        return None
                    digest = hashlib.sha256()
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                    after = os.fstat(stream.fileno())
                    after_signature = (
                        int(after.st_dev), int(after.st_ino),
                        int(after.st_size), int(after.st_mtime_ns),
                    )
                    if after_signature != expected:
                        return None
                contents[relative_path] = digest.hexdigest()
            except (OSError, ValueError):
                return None
        return contents

    @staticmethod
    def _scene_content_signature(contents: dict[str, str]) -> str:
        stable = [[path, digest] for path, digest in sorted(contents.items())]
        return hashlib.sha256(
            json.dumps(stable, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _scene_content_changes(before: dict[str, str], after: dict[str, str]) -> list[dict]:
        changes = []
        for relative_path in sorted(set(before) | set(after)):
            previous, current = before.get(relative_path), after.get(relative_path)
            if previous == current:
                continue
            kind = "added" if previous is None else ("removed" if current is None else "modified")
            changes.append({"path": "scenes/" + relative_path, "kind": kind})
        return changes

    def set_publisher(self, publish: Callable[[str, dict], None]) -> None:
        with self._lock:
            self.publish = publish

    def watch(
        self, project_id: str, initial_revision: str | None = None,
        initial_text: str | None = None,
    ) -> None:
        pid = fd.validate_project_id(project_id)
        path = self.path_for_project(pid)
        signature = self._signature(path)
        try:
            current_text = path.read_text(encoding="utf-8")
            actual_revision = fd.revision_of(current_text)
        except (OSError, UnicodeError):
            current_text = None
            actual_revision = fd.revision_of("")
        # The caller may provide the exact text it just read before registering
        # the watcher. Prefer that paired snapshot over a second read so an
        # external write between GET and watch registration remains detectable.
        if isinstance(initial_text, str):
            baseline_text = initial_text
            baseline_revision = fd.revision_of(initial_text)
            if initial_revision is not None and initial_revision != baseline_revision:
                # Never bind a revision hash to text that does not produce it.
                initial_revision = baseline_revision
        else:
            baseline_text = current_text
            baseline_revision = initial_revision if initial_revision is not None else actual_revision
        if initial_revision is None:
            initial_revision = baseline_revision
        matched_initial_snapshot = actual_revision == initial_revision
        now = time.monotonic()
        try:
            watched_path = str(path.resolve())
        except OSError:
            watched_path = str(path.absolute())
        project_root = path.parent
        scene_root = project_root / "scenes"
        # Store the absolute lexical path, not resolve(), so polling can still
        # detect and reject scenes/ if it is replaced with a symlink afterwards.
        watched_scene_root = str(scene_root.absolute())
        scene_files = self._scan_scene_files(scene_root, project_root=project_root)
        scene_content_files = self._scan_scene_file_contents(
            scene_root, project_root=project_root, files=scene_files
        ) or {}
        scene_signature = self._scene_tree_signature(scene_files)
        with self._lock:
            state = self._projects.get(pid)
            # Storage roots may change when a project is re-created under a new
            # workspace (and when tests reload the WebUI module). A project ID is
            # only unique within one storage root; never reuse watcher phase/error
            # state across different physical film.html paths.
            if state is None or state.get("watched_path") != watched_path:
                self._projects[pid] = {
                    "watched_path": watched_path,
                    "last_revision": baseline_revision,
                    "last_text": baseline_text,
                    "last_phase": "revision_changed" if matched_initial_snapshot and path.is_file() else "missing",
                    "last_error": None,
                    "last_history_result": None,
                    "pending_signature": signature,
                    "processed_signature": signature if matched_initial_snapshot else None,
                    "changed_at": now - self.settle_seconds if not matched_initial_snapshot else now,
                    "last_content_audit_at": now,
                    "last_access": now,
                    "scene_root": watched_scene_root,
                    "scene_files": scene_files,
                    "scene_content_files": scene_content_files,
                    "scene_content_pending_files": None,
                    "scene_content_changed_at": now,
                    "scene_last_content_audit_at": now,
                    "scene_pending_signature": scene_signature,
                    "scene_processed_signature": scene_signature,
                    "scene_changed_at": now,
                    "scene_last_scan_at": now - self.scene_scan_interval_seconds,
                }
            else:
                state["last_access"] = now
                if state["last_revision"] is None:
                    state["last_revision"] = initial_revision
                if state.get("scene_root") != watched_scene_root or "scene_files" not in state:
                    state.update({
                        "scene_root": watched_scene_root,
                        "scene_files": scene_files,
                        "scene_content_files": scene_content_files,
                        "scene_content_pending_files": None,
                        "scene_content_changed_at": now,
                        "scene_last_content_audit_at": now,
                        "scene_pending_signature": scene_signature,
                        "scene_processed_signature": scene_signature,
                        "scene_changed_at": now,
                        "scene_last_scan_at": now - self.scene_scan_interval_seconds,
                    })

    def _poll_film_once(self, now: float | None = None) -> None:
        """Poll film.html revision/history independently from scene resource files."""
        current_time = time.monotonic() if now is None else float(now)
        with self._lock:
            for pid, state in list(self._projects.items()):
                if current_time - state["last_access"] > self.stale_after_seconds:
                    self._projects.pop(pid, None)
            projects = [(pid, dict(state)) for pid, state in self._projects.items()]

        for pid, snapshot in projects:
            path = self.path_for_project(pid)
            signature = self._signature(path)
            if signature != snapshot["pending_signature"]:
                with self._lock:
                    live = self._projects.get(pid)
                    if live is not None:
                        live["pending_signature"] = signature
                        live["changed_at"] = current_time
                continue
            if signature == snapshot["processed_signature"]:
                if current_time - float(snapshot.get("last_content_audit_at", current_time)) < self.content_audit_interval_seconds:
                    continue
                # A few external writers update a file in place and restore its
                # timestamp (or run on filesystems with coarse timestamp
                # resolution). Stat-only polling would miss that revision forever.
                try:
                    audit_source = path.read_text(encoding="utf-8")
                    audit_error = None
                except UnicodeError as exc:
                    audit_source = ""
                    audit_error = exc
                except OSError as exc:
                    audit_source = ""
                    audit_error = exc
                audit_signature = self._signature(path)
                with self._lock:
                    live = self._projects.get(pid)
                    if live is None or live.get("pending_signature") != signature:
                        continue
                    live["last_content_audit_at"] = current_time
                    if audit_signature != signature:
                        live["pending_signature"] = audit_signature
                        live["changed_at"] = current_time
                        continue
                    audit_revision = fd.revision_of(audit_source)
                    if audit_error is not None or audit_revision != snapshot.get("last_revision"):
                        # Re-enter the normal settled-read/history path. Keep the
                        # same stat signature but invalidate its processed marker.
                        live["processed_signature"] = None
                        live["changed_at"] = current_time - self.settle_seconds
                continue
            if current_time - snapshot["changed_at"] < self.settle_seconds:
                continue

            read_error = None
            try:
                source = path.read_text(encoding="utf-8")
            except UnicodeError as exc:
                source = ""
                read_error = f"film.html 必须使用 UTF-8 编码：{exc}"
            except OSError as exc:
                source = ""
                read_error = f"film.html 无法读取：{exc}"
            if self._signature(path) != signature:
                with self._lock:
                    live = self._projects.get(pid)
                    if live is not None:
                        live["pending_signature"] = self._signature(path)
                        live["changed_at"] = current_time
                continue

            revision = fd.revision_of(source)
            try:
                if read_error:
                    code = "FILM_ENCODING_INVALID" if "UTF-8" in read_error else "FILM_READ_FAILED"
                    raise fd.FilmError(code, read_error, 422)
                fd.parse_film(source)
                phase, error = "revision_changed", None
            except fd.FilmError as exc:
                phase, error = "document_broken", str(exc)[:1200]

            history_result = None
            if phase == "revision_changed" and revision != snapshot["last_revision"]:
                before_text = snapshot.get("last_text")
                try:
                    authoritative_path = fd._film_path(pid).resolve()
                    watched_path = path.resolve()
                except OSError:
                    authoritative_path = watched_path = None
                if isinstance(before_text, str) and watched_path == authoritative_path:
                    try:
                        history_result = fd.record_external_film_revision(
                            pid, before_text, source, source="external_file"
                        )
                    except fd.FilmError as exc:
                        history_result = {"status": "history_error", "error": str(exc)[:800]}
                    if history_result.get("status") == "superseded":
                        with self._lock:
                            live = self._projects.get(pid)
                            if live is not None:
                                live["pending_signature"] = self._signature(path)
                                live["changed_at"] = current_time
                        continue

            event = None
            with self._lock:
                live = self._projects.get(pid)
                if live is None or live["pending_signature"] != signature:
                    continue
                live["processed_signature"] = signature
                if history_result is not None:
                    # Keep the exact journal outcome beside the observed file
                    # revision; this distinguishes a successful external write
                    # from a watcher event whose history transaction was rejected.
                    live["last_history_result"] = dict(history_result)
                previous_revision = live.get("last_revision")
                previous_phase = live.get("last_phase")
                previous_error = live.get("last_error")
                state_changed = (
                    revision != previous_revision
                    or phase != previous_phase
                    or error != previous_error
                )
                if state_changed:
                    live["last_revision"] = revision
                    live["last_phase"] = phase
                    live["last_error"] = error
                    if phase == "revision_changed":
                        # Update the content baseline only after strict parse success.
                        live["last_text"] = source
                    already_recorded = bool(
                        phase == "revision_changed"
                        and revision != previous_revision
                        and history_result
                        and history_result.get("status") == "already_recorded"
                    )
                    if not already_recorded:
                        event = {
                            "phase": phase,
                            "project_id": pid,
                            "revision": revision,
                            "source": "external_file",
                        }
                        if error:
                            event["error"] = error
                        if history_result and history_result.get("status") not in {"recorded", "unchanged"}:
                            event["history_status"] = history_result.get("status")
                            if history_result.get("error"):
                                event["history_error"] = history_result["error"]
            if event:
                self.publish("film", event)

    def _poll_scene_files(self, now: float | None = None) -> None:
        current_time = time.monotonic() if now is None else float(now)
        with self._lock:
            projects = [(pid, dict(state)) for pid, state in self._projects.items()]

        for pid, snapshot in projects:
            root_value = snapshot.get("scene_root")
            if not isinstance(root_value, str) or not root_value:
                continue
            if current_time - float(snapshot.get("scene_last_scan_at", 0.0)) < self.scene_scan_interval_seconds:
                continue

            root = Path(root_value)
            current_files = self._scan_scene_files(root, project_root=root.parent)
            signature = self._scene_tree_signature(current_files)
            with self._lock:
                live = self._projects.get(pid)
                if live is None or live.get("scene_root") != root_value:
                    continue
                live["scene_last_scan_at"] = current_time
                if signature != live.get("scene_pending_signature"):
                    live["scene_pending_signature"] = signature
                    live["scene_changed_at"] = current_time
                    live["scene_content_pending_files"] = None
                    continue

                stat_pending = signature != live.get("scene_processed_signature")
                content_pending = live.get("scene_content_pending_files")
                content_due = (
                    current_time - float(live.get("scene_last_content_audit_at", current_time))
                    >= self.scene_content_audit_interval_seconds
                )
                if stat_pending and current_time - float(live.get("scene_changed_at", current_time)) < self.settle_seconds:
                    continue
                if not stat_pending and content_pending is None and not content_due:
                    continue

            content_files = self._scan_scene_file_contents(
                root, project_root=root.parent, files=current_files
            )
            if content_files is None:
                with self._lock:
                    live = self._projects.get(pid)
                    if live is not None and live.get("scene_root") == root_value:
                        live["scene_last_content_audit_at"] = current_time
                continue

            if stat_pending:
                # Confirm both the file set and every file's content after the debounce.
                settled_files = self._scan_scene_files(root, project_root=root.parent)
                settled_signature = self._scene_tree_signature(settled_files)
                if settled_signature != signature:
                    with self._lock:
                        live = self._projects.get(pid)
                        if live is not None and live.get("scene_root") == root_value:
                            live["scene_pending_signature"] = settled_signature
                            live["scene_changed_at"] = current_time
                    continue
                settled_contents = self._scan_scene_file_contents(
                    root, project_root=root.parent, files=settled_files
                )
                if settled_contents is None:
                    continue
                with self._lock:
                    live = self._projects.get(pid)
                    if (
                        live is None
                        or live.get("scene_root") != root_value
                        or live.get("scene_pending_signature") != settled_signature
                    ):
                        continue
                    before = live.get("scene_content_files") or {}
                    changes = self._scene_content_changes(before, settled_contents)
                    live["scene_files"] = settled_files
                    live["scene_content_files"] = settled_contents
                    live["scene_content_pending_files"] = None
                    live["scene_content_changed_at"] = current_time
                    live["scene_last_content_audit_at"] = current_time
                    live["scene_pending_signature"] = settled_signature
                    live["scene_processed_signature"] = settled_signature
                    live["scene_changed_at"] = current_time
                if changes:
                    self.publish("film", {
                        "phase": "scene_changed",
                        "project_id": pid,
                        "source": "external_file",
                        "scene_revision": self._scene_content_signature(settled_contents),
                        "paths": [item["path"] for item in changes],
                        "changes": changes,
                    })
                continue

            # Stat metadata can remain identical after an in-place rewrite. Periodically
            # hash the scene tree; require the same content snapshot across the debounce
            # before publishing a new scene_revision.
            with self._lock:
                live = self._projects.get(pid)
                if live is None or live.get("scene_root") != root_value:
                    continue
                live["scene_last_content_audit_at"] = current_time
                if live.get("scene_pending_signature") != signature:
                    continue
                before = live.get("scene_content_files") or {}
                pending = live.get("scene_content_pending_files")
                if content_files == before:
                    live["scene_content_pending_files"] = None
                    continue
                if pending is None or content_files != pending:
                    live["scene_content_pending_files"] = content_files
                    live["scene_content_changed_at"] = current_time
                    continue
                if current_time - float(live.get("scene_content_changed_at", current_time)) < self.settle_seconds:
                    continue
                changes = self._scene_content_changes(before, content_files)
                live["scene_files"] = current_files
                live["scene_content_files"] = content_files
                live["scene_content_pending_files"] = None
                live["scene_content_changed_at"] = current_time
            if changes:
                self.publish("film", {
                    "phase": "scene_changed",
                    "project_id": pid,
                    "source": "external_file",
                    "scene_revision": self._scene_content_signature(content_files),
                    "paths": [item["path"] for item in changes],
                    "changes": changes,
                })

    def poll_once(self, now: float | None = None) -> None:
        """Poll film.html and scene files independently with deterministic injectable time."""
        current_time = time.monotonic() if now is None else float(now)
        try:
            self._poll_film_once(now=current_time)
        finally:
            self._poll_scene_files(now=current_time)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.poll_once()
            except Exception:
                # File watching is advisory and must not terminate the WebUI server.
                continue

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="film-revision-watcher")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)


_WATCHER: FilmRevisionWatcher | None = None
_WATCHER_LOCK = threading.Lock()


def get_film_watcher(publish: Callable[[str, dict], None]) -> FilmRevisionWatcher:
    """Keep one daemon across WebUI module reloads in tests."""
    global _WATCHER
    with _WATCHER_LOCK:
        if _WATCHER is None:
            _WATCHER = FilmRevisionWatcher(publish)
        else:
            _WATCHER.set_publisher(publish)
        _WATCHER.start()
        return _WATCHER
