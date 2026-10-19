"""Debounced watcher for local Agent writes to active film.html documents."""
from __future__ import annotations

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
        stale_after_seconds: float = 600.0,
    ):
        self.publish = publish
        self.path_for_project = path_for_project or fd._film_path
        self.settle_seconds = max(0.05, float(settle_seconds))
        self.interval_seconds = max(0.02, float(interval_seconds))
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

    def set_publisher(self, publish: Callable[[str, dict], None]) -> None:
        with self._lock:
            self.publish = publish

    def watch(self, project_id: str, initial_revision: str | None = None) -> None:
        pid = fd.validate_project_id(project_id)
        path = self.path_for_project(pid)
        signature = self._signature(path)
        if initial_revision is None:
            try:
                initial_revision = fd.revision_of(path.read_text(encoding="utf-8"))
            except OSError:
                initial_revision = fd.revision_of("")
        now = time.monotonic()
        with self._lock:
            state = self._projects.get(pid)
            if state is None:
                self._projects[pid] = {
                    "last_revision": initial_revision,
                    "pending_signature": signature,
                    "processed_signature": signature,
                    "changed_at": now,
                    "last_access": now,
                }
            else:
                state["last_access"] = now
                if state["last_revision"] is None:
                    state["last_revision"] = initial_revision

    def poll_once(self, now: float | None = None) -> None:
        """Run one scan synchronously; deterministic tests can inject monotonic time."""
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
                continue
            if current_time - snapshot["changed_at"] < self.settle_seconds:
                continue

            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                source = ""
            if self._signature(path) != signature:
                with self._lock:
                    live = self._projects.get(pid)
                    if live is not None:
                        live["pending_signature"] = self._signature(path)
                        live["changed_at"] = current_time
                continue

            revision = fd.revision_of(source)
            try:
                fd.parse_film(source)
                phase, error = "revision_changed", None
            except fd.FilmError as exc:
                phase, error = "document_broken", str(exc)[:1200]

            event = None
            with self._lock:
                live = self._projects.get(pid)
                if live is None or live["pending_signature"] != signature:
                    continue
                live["processed_signature"] = signature
                if revision != live["last_revision"]:
                    live["last_revision"] = revision
                    event = {
                        "phase": phase,
                        "project_id": pid,
                        "revision": revision,
                        "source": "external_file",
                    }
                    if error:
                        event["error"] = error
            if event:
                self.publish("film", event)

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
