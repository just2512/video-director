"""Cross-process lock for project state transactions.

The same lock serializes editor Composition/Revision updates and Shotbook
read/validate/write operations.
"""
from __future__ import annotations

import os
import re
import stat
import time
from contextlib import contextmanager
from typing import Iterator

from video_director import config

try:
    from filelock import FileLock as _FileLockBase
except ImportError:  # pragma: no cover - filelock 是 requirements 硬依赖，仅防御
    _FileLockBase = object  # type: ignore[assignment,misc]

_PROJECT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class StableFileLock(_FileLockBase):
    """释放后保留锁文件的 FileLock（filelock 3.x 默认 unlink → 双锁分裂）。

    等待方按旧 inode 排队时，释放方删文件+新进程建同名新文件，两把锁并存。
    锁文件保留为稳定 inode 后，所有等待方始终竞争同一文件。
    已验证：Windows 空文件可直接 msvcrt.locking，不需要先写占位字节。
    """

    def _release(self) -> None:
        fd = self._context.lock_file_fd
        self._context.lock_file_fd = None
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


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
    if plans.is_symlink():
        raise ValueError("项目 plans 目录不能是符号链接")
    try:
        # The production project_dir creates its parent. parents=True also
        # preserves the lock contract for injected/test project roots.
        plans.mkdir(parents=True)
    except FileExistsError:
        if plans.is_symlink() or not plans.is_dir():
            raise ValueError("项目 plans 路径不是可信的普通目录")
    if plans.is_symlink() or not plans.is_dir():
        raise ValueError("项目 plans 路径不是可信的普通目录")

    lock_path = plans / "project.lock"
    if lock_path.is_symlink():
        raise ValueError("项目锁文件不能是符号链接")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if (os.open in getattr(os, "supports_dir_fd", set())
            and hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW")):
        # Pin the directory inode and open the lock relative to it. This prevents
        # a concurrent replacement of plans/ from redirecting the lock outside.
        directory_fd = os.open(
            str(plans), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            descriptor = os.open("project.lock", flags, 0o600, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)
    else:
        descriptor = os.open(str(lock_path), flags, 0o600)
    try:
        if lock_path.is_symlink() or not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("项目锁文件不是可信的普通文件")
        handle_context = os.fdopen(descriptor, "r+b")
    except Exception:
        os.close(descriptor)
        raise
    with handle_context as handle:
        if os.name == "nt":
            import msvcrt

            # 空文件可以直接锁定（已验证），不需要占位字节。写占位字节会在
            # 并发首建时与持锁方撞车：msvcrt 区域锁拒绝其他句柄重叠写同一区域
            # → flush() EACCES → 竞态失败。所以这里一行不写，直接加锁。
            # msvcrt.LK_LOCK 只等约 10s 后抛 OSError——不是无限等待（与 POSIX
            # flock LOCK_EX 不同）。持锁方冷启动（registries 加载）+写盘可能
            # 超 10s，等待方就会失败。LK_NBLCK 轮询直到拿到，语义与 flock 对齐。
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.02)
            try:
                yield
            finally:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
