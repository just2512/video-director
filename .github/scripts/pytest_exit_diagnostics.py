"""Post-session diagnostics for pytest processes that fail to exit cleanly.

The timer is daemonized: it never keeps a healthy test process alive. If pytest
has completed its test session but Python is blocked during interpreter
shutdown, it records all Python thread stacks and the remaining OS processes.
"""
from __future__ import annotations

import faulthandler
import os
import subprocess
import sys
import threading
import time


def pytest_sessionfinish(session, exitstatus):
    def report():
        print(
            f"PYTEST_POST_SESSION_DIAGNOSTICS pid={os.getpid()} "
            f"exitstatus={exitstatus} elapsed_after_session={time.monotonic() - started:.1f}s",
            file=sys.stderr,
            flush=True,
        )
        for thread in threading.enumerate():
            print(
                f"PYTEST_THREAD name={thread.name!r} ident={thread.ident} "
                f"daemon={thread.daemon} alive={thread.is_alive()}",
                file=sys.stderr,
                flush=True,
            )
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        try:
            result = subprocess.run(
                ["ps", "-eo", "pid,ppid,stat,etime,args", "--forest"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            print("PYTEST_REMAINING_PROCESSES_BEGIN", file=sys.stderr, flush=True)
            print(result.stdout, file=sys.stderr, flush=True)
            print("PYTEST_REMAINING_PROCESSES_END", file=sys.stderr, flush=True)
        except Exception as exc:
            print(f"PYTEST_PROCESS_LIST_ERROR {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    started = time.monotonic()
    timer = threading.Timer(45.0, report)
    timer.daemon = True
    timer.start()
