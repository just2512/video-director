"""AI Video Director 独立 WebUI（单文件入口，零外部项目依赖）。

用法：
    python run_webui.py            # http://localhost:8501
    python run_webui.py --port 8502
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    port = "8501"
    args = sys.argv[1:]
    if "--port" in args:
        port = args[args.index("--port") + 1]
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(ROOT / "webui" / "App.py"),
        "--server.headless=true",
        "--server.port", port,
        "--server.address=127.0.0.1",
        "--browser.gatherUsageStats=false",
    ]
    print(f"AI Video Director WebUI → http://127.0.0.1:{port}")
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()