"""AI Video Director WebUI 启动器（零配置，自动定位有依赖的 Python）。

为什么之前打不开：run_webui.py 用了 sys.executable（你命令行里默认的 python），
但依赖装在项目的 venv 里。本启动器按以下顺序自动找一个"真正能跑"的 Python：
    1. 环境变量 VD_PYTHON（你显式指定）
    2. 项目同级 venv（D:\\autocut\\venv）—— 本项目依赖所在
    3. 当前 sys.executable
    4. PATH 里的 python

找到后做依赖探测（streamlit + video_director 可导入才算数），失败给中文指引。
启动后自动用默认浏览器打开 http://localhost:8501。

用法：
    python run_webui.py
    python run_webui.py --port 8502
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent

#: 候选 Python 探测顺序（第一个"依赖齐全"的胜出）
def _candidate_pythons():
    import os
    order = []
    env = os.environ.get("VD_PYTHON")
    if env:
        order.append(env)
    if sys.platform == "win32":
        order.append(str(ROOT.parent / "venv" / "Scripts" / "python.exe"))
        # portable（MPT 便携包的 python 也带完整依赖，作为兜底）
        p2 = ROOT.parent / "MoneyPrinterTurbo-Portable-Windows-1.3.7" / "lib" / "python" / "python.exe"
        if p2.exists():
            order.append(str(p2))
    else:
        order.append(str(ROOT.parent / "venv" / "bin" / "python"))
    order.append(sys.executable)
    return order


def _check_deps(python: str) -> str | None:
    """探测该 python 能否 import 本项目核心依赖，OK 返回 None，否则返回缺项说明。"""
    probe = (
        "import importlib.util as u;"
        "mods=['streamlit','edge_tts','pydantic','loguru','video_director'];"
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
        missing = _check_deps(py)
        if missing is None:
            return py
    return None  # 全都不行


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
        print("    然后重新运行 python run_webui.py")
        print("=" * 56)
        sys.exit(1)

    print(f"使用 Python: {python}")
    cmd = [
        python, "-m", "streamlit", "run",
        str(ROOT / "webui" / "App.py"),
        "--server.headless=false",      # 允许自动打开浏览器
        "--server.port", port,
        "--server.address=127.0.0.1",
        "--browser.gatherUsageStats=false",
    ]
    url = f"http://127.0.0.1:{port}"

    # 浏览器自动打开（headless=false 已够，但部分环境仍不弹，这里兜底手动开）
    def _open_browser():
        try:
            webbrowser.open(url)
        except Exception:
            pass

    import threading
    # 延迟几秒待服务就绪再开浏览器
    timer = threading.Timer(3.0, _open_browser)
    timer.daemon = True
    timer.start()

    print(f"AI Video Director WebUI → {url}")
    print("（若浏览器未自动打开，请手动访问上面的地址）")
    try:
        sys.exit(subprocess.call(cmd))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()