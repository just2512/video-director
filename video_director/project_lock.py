"""Cross-process lock for project state transactions.

The same lock serializes editor Composition/Revision updates and Shotbook
read/validate/write operations.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Iterator

from video_director import config

_PROJECT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def validate_project_id(value: str) -> str:
    project_id = str(value or "").strip()
    if not _PROJECT_ID.fullmatch(project_id):
        raise ValueError("项目编号格式无效")
    return project_id


@contextmanager
def project_lock(project_id: str) -> Iterator[None]:
    """Hold an exclusive process lock for one project's persisted state."""
    pid = validate_project_id(project_id)
    plans = config.project_dir(pid) / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    lock_path = plans / "project.lock"
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
