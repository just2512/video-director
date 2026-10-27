"""Short-lived server-side registry for explicitly approved FilmDocument proposals."""
from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable


class FilmProposalStore:
    def __init__(self, ttl_seconds: float = 900.0, clock: Callable[[], float] = time.monotonic):
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.clock = clock
        self._lock = threading.RLock()
        self._items: dict[str, dict[str, Any]] = {}

    def _prune_locked(self, now: float) -> None:
        expired = [
            proposal_id for proposal_id, item in self._items.items()
            if now - item["created_at"] >= self.ttl_seconds
        ]
        for proposal_id in expired:
            self._items.pop(proposal_id, None)

    def put(
        self,
        proposal_id: str,
        project_id: str,
        base_revision: str,
        operations: list[dict],
    ) -> None:
        if not proposal_id or not project_id or not base_revision or not isinstance(operations, list) or not operations:
            raise ValueError("proposal requires id, project, revision and non-empty operations")
        now = self.clock()
        with self._lock:
            self._prune_locked(now)
            self._items[proposal_id] = {
                "id": proposal_id,
                "project_id": project_id,
                "base_revision": base_revision,
                "operations": copy.deepcopy(operations),
                "created_at": now,
            }

    def discard(self, project_id: str, proposal_id: str) -> bool:
        """Remove a proposal that could not be delivered to its requesting client."""
        with self._lock:
            item = self._items.get(proposal_id)
            if item is None or item["project_id"] != project_id:
                return False
            self._items.pop(proposal_id, None)
            return True

    def take(
        self,
        project_id: str,
        proposal_id: str,
        base_revision: str,
    ) -> dict[str, Any] | None:
        """Consume a single-use proposal only for its original project and base revision."""
        now = self.clock()
        with self._lock:
            self._prune_locked(now)
            item = self._items.get(proposal_id)
            if not item or item["project_id"] != project_id or item["base_revision"] != base_revision:
                return None
            return copy.deepcopy(self._items.pop(proposal_id))


__all__ = ["FilmProposalStore"]
