@echo off
rem AI Video Director WebUI 双击启动器（隐藏黑窗后台运行，自动开浏览器）
rem 自愈：无 venv 时先建环境装依赖（干净机器双击即可用）；
rem 解释器选择全权交 run_webui.py（venv 优先 > 当前 Python）
cd /d "%~dp0"
if not exist "..\venv\Scripts\python.exe" (
  echo 首次运行：创建 venv 并安装依赖（约 1-3 分钟）...
  python -m venv ..\venv 2>nul || py -m venv ..\venv
  ..\venv\Scripts\python -m pip install -r requirements.txt
)
start "" /min python run_webui.py
