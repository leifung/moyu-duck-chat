#!/usr/bin/env bash
# 摸鱼鸭 · 欸~你听说没~（合并版）一键启动
set -u

cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" 2>/dev/null || \
    cd "$(dirname "$0")"

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

if ! "$PY" -c "import PyQt5" >/dev/null 2>&1; then
    echo "=========================================================="
    echo " 这台机器还没有 PyQt5，界面起不来。"
    echo " 请先安装（内网机器就找管理员要离线包）："
    echo "     sudo apt install python3-pyqt5"
    echo " 或： $PY -m pip install PyQt5"
    echo "=========================================================="
    read -r -p "按回车键退出…" _tmp
    exit 1
fi

exec "$PY" 摸鱼鸭聊天版.py "$@"
