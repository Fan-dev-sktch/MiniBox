@echo off
chcp 65001 >nul
title MiniBox
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY ( where python >nul 2>nul && set PY=python )
if not defined PY (
  echo 请先安装 Python 3.9+ ：https://www.python.org/downloads/  （安装时勾选 Add Python to PATH）
  pause & exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo 首次运行，正在准备环境（只需一次，约 1 分钟）...
  %PY% -m venv .venv || ( echo 创建虚拟环境失败 & pause & exit /b 1 )
  ".venv\Scripts\python.exe" -m pip install -q --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || ( echo 依赖安装失败，请检查网络 & pause & exit /b 1 )
)
".venv\Scripts\python.exe" app.py %*
pause
