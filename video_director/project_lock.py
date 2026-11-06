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
    if plans.is_symlink():
        raise ValueError("项目 plans 目录不能是符号链接")
    try:
        plans.mkdir()
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

            # msvcrt 锁定区域要求文件至少 1 字节。首次创建时写入占位字节；
            # 并发下另一进程可能已抢先写入（其锁会拒绝本进程对锁区的写），
            # 写失败不代表加锁失败——只要文件非空即可继续。
            try:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
            except OSError:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    raise  # 文件仍为空且写不进——真实故障，不吞
            handle.seek(0)
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
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
