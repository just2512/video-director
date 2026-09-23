"""AI Video Director WebUI 启动器（零第三方前端框架，自有 http.server 实现）。

自动定位有依赖的 Python，启动 webui/server.py（Python 标准库 http.server），
单次打开浏览器。
"""

from __future__ import annotations

import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


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


def main():
    port = "8501"
    args = sys.argv[1:]
    if "--port" in args:
        port = args[args.index("--port") + 1]

    python = _pick_python()
    if python is None:
        print("=" * 56)
        print("[X] 找不到带依赖的 Python 环境，无法启动 WebUI。")
        print("    本项目依赖安装在 D:\\autocut\\venv，请确认它存在；")
        print("    或指定： set VD_PYTHON=D:\\autocut\\venv\\Scripts\\python.exe")
        print("=" * 56)
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    print(f"使用 Python: {python}")
    print(f"AI Video Director WebUI → {url}")

    # 延迟 1.5s 待服务就绪后，只开一次浏览器
    import threading
    def _open_once():
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Timer(1.5, _open_once).daemon = True

    try:
        sys.exit(subprocess.call([python, str(ROOT / "webui" / "server.py"), port]))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()