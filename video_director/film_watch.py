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
        with self._lock:
            state = self._projects.get(pid)
            if state is None:
                self._projects[pid] = {
                    "last_revision": baseline_revision,
                    "last_text": baseline_text,
                    "last_phase": "revision_changed" if matched_initial_snapshot and path.is_file() else "missing",
                    "last_error": None,
                    "pending_signature": signature,
                    "processed_signature": signature if matched_initial_snapshot else None,
                    "changed_at": now - self.settle_seconds if not matched_initial_snapshot else now,
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
