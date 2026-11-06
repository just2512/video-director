"""Durable atomic replacement for project and Asset Registry state files.

Callers must fsync the temporary file before replacement. POSIX then fsyncs the
containing directory; Windows uses MoveFileExW with WRITE_THROUGH so the rename
is not reported as durable until the native operation succeeds.
"""
from __future__ import annotations

import os
from pathlib import Path


def fsync_directory(path: str | Path) -> bool:
    """Flush a directory entry on POSIX; Windows uses write-through renames."""
    if os.name == "nt":
        # Windows has no portable directory-fsync handle through Python. Every
        # durable rename in this module uses MoveFileExW(MOVEFILE_WRITE_THROUGH).
        return True
    try:
        descriptor = os.open(
            os.fspath(path),
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        return False
    return True


def atomic_replace(source: str | Path, destination: str | Path) -> bool:
    """Atomically replace a sibling file and confirm its directory entry is durable.

    Returns False only when the POSIX rename succeeded but directory fsync could
    not be confirmed. Callers must then keep their higher-level transaction
    recoverable and must not submit a paid provider request or mark output done.
    """
    source_path = Path(source)
    destination_path = Path(destination)
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        move_file_ex = kernel32.MoveFileExW
        move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
        move_file_ex.restype = ctypes.c_int
        flags = 0x00000001 | 0x00000008  # MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH
        if not move_file_ex(str(source_path), str(destination_path), flags):
            raise ctypes.WinError(ctypes.get_last_error())
        return True

    os.replace(source_path, destination_path)
    return fsync_directory(destination_path.parent)
