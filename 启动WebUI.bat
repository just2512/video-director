@echo off
rem AI Video Director WebUI 双击启动器（隐藏黑窗后台运行，自动开浏览器）
cd /d "%~dp0"
start "" /min python run_webui.py
if errorlevel 1 (
  start "" /min py run_webui.py
)
