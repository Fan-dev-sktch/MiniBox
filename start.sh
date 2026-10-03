#!/usr/bin/env bash
# MiniBox 一键启动（macOS / Linux）
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then echo "请先安装 Python 3.9+：https://www.python.org/downloads/"; read -r; exit 1; fi
if [ ! -d .venv ]; then
  echo "首次运行，正在准备环境（只需一次，约 1 分钟）…"
  "$PY" -m venv .venv || { echo "创建虚拟环境失败"; read -r; exit 1; }
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt || { echo "依赖安装失败，请检查网络"; read -r; exit 1; }
fi
exec .venv/bin/python app.py "$@"
