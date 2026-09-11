#!/usr/bin/env bash
# 把合并版放进系统启动器（开始菜单 / 应用列表）
set -u

DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

if ! "$PY" -c "import PyQt5" >/dev/null 2>&1; then
    echo "缺少 PyQt5，请先安装：sudo apt install python3-pyqt5"
    exit 1
fi

ICON="$DIR/icon.png"
"$PY" "$DIR/摸鱼鸭聊天版.py" --make-icon "$ICON" >/dev/null 2>&1

APPDIR="$HOME/.local/share/applications"
mkdir -p "$APPDIR"
DESK="$APPDIR/moyuya-chat.desktop"

cat > "$DESK" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=摸鱼鸭·聊天
GenericName=局域网聊天
Comment=桌面宠物摸鱼鸭 + 局域网点对点聊天，收到消息小鸭子会跑去吃鱼
Exec=bash "$DIR/启动.sh"
Icon=$ICON
Path=$DIR
Terminal=false
Categories=Network;Chat;Utility;
Keywords=摸鱼;聊天;传文件;局域网;UOS;
StartupNotify=false
EOF

chmod +x "$DESK"
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$APPDIR" >/dev/null 2>&1

echo "已安装到应用菜单：摸鱼鸭·聊天"
echo "桌面文件：$DESK"
echo "（若列表里没立刻出现，注销重登一次即可）"
