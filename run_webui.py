"""AI Video Director WebUI 启动器（零第三方前端框架，自有 http.server 实现）。

自动定位有依赖的 Python，启动 webui/server.py（Python 标准库 http.server），
**就绪轮询**（/api/health 200 才开浏览器，最长 30s，不再固定 1.5s），
失败写 webui-start-error.log（双击场景闪退也能看到原因）。
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ERROR_LOG = ROOT / "webui-start-error.log"


def _candidate_pythons():
    import os
    order = []
    env = os.environ.get("VD_PYTHON")
    if env:
        order.append(env)
    if sys.platform == "win32":
        order.append(str(ROOT.parent / "venv" / "Scripts" / "python.exe"))
        p2 = ROOT.parent / "MoneyPrinterTurbo-Portable-Windows-1.3.7" / "lib" / "python" / "python.exe"
        if p2.exists():
            order.append(str(p2))
    else:
        order.append(str(ROOT.parent / "venv" / "bin" / "python"))
    order.append(sys.executable)
    return order


def _check_deps(python: str) -> str | None:
    probe = (
        "import importlib.util as u;"
        "mods=['edge_tts','pydantic','loguru','video_director'];"
        "missing=[m for m in mods if u.find_spec(m) is None];"
        "print(','.join(missing) if missing else 'OK')"
    )
    try:
        r = subprocess.run(
            [python, "-c", probe],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30,
        )
        out = (r.stdout or "").strip()
        return None if out == "OK" else (out or r.stderr.strip()[:200])
    except Exception as exc:
        return str(exc)[:200]


def _pick_python():
    for py in _candidate_pythons():
        if not Path(py).exists() and "\\" in py:
            continue
        if _check_deps(py) is None:
            return py
    return None


def _log_error(msg: str) -> None:
    try:
        ERROR_LOG.write_text(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n", encoding="utf-8"
        )
    except Exception:
        pass


def _wait_and_open(url: str, proc: subprocess.Popen) -> None:
    """就绪轮询：/api/health 200 才开浏览器；进程先死则写日志。"""
    health = url.rstrip("/") + "/api/health"
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            _log_error(f"server 进程提前退出 code={proc.returncode}（端口被占/依赖缺失）")
            return
        try:
            with urllib.request.urlopen(health, timeout=2) as resp:
                if resp.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:
            pass
        time.sleep(0.5)
    _log_error(f"等待服务就绪超时（30s）：{url}（服务可能仍在启动，可手动打开）")
    webbrowser.open(url)  # 超时也尝试打开一次，服务可能稍后就绪


def main():
    port = "8501"
    args = sys.argv[1:]
    if "--port" in args:
        port = args[args.index("--port") + 1]

    python = _pick_python()
    if python is None:
        msg = (
            "找不到带依赖的 Python 环境。本项目依赖安装在 D:\\autocut\\venv，"
            "或设置 VD_PYTHON=D:\\autocut\\venv\\Scripts\\python.exe"
        )
        print("=" * 56)
        print(f"[X] {msg}")
        print("=" * 56)
        _log_error(msg)
        input("按回车退出…")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    print(f"使用 Python: {python}")
    print(f"AI Video Director WebUI → {url}（就绪后自动打开浏览器）")

    proc = subprocess.Popen(
        [python, str(ROOT / "webui" / "server.py"), port],
        cwd=str(ROOT),
    )
    _wait_and_open(url, proc)

    try:
        sys.exit(proc.wait())
    except KeyboardInterrupt:
        proc.terminate()
        sys.exit(0)


if __name__ == "__main__":
    main()