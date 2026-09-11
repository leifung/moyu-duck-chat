#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把三部分拼成一个单文件主程序 ../摸鱼鸭聊天版.py

    vendor/欸你听说没.py 的通信内核（ChatNode 及之前的全部代码）
  + vendor/moyu_duck.py  的角色与绘制（Duck/Fish/Poop/Fx/draw_*）
  + _part3.py            合并版界面（宠物层 / 消息舞台 / 聊天窗口 / 控制器）

用法：  python3 tools/build.py
"""
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CHAT = os.path.join(HERE, "vendor", "欸你听说没.py")
DUCK = os.path.join(HERE, "vendor", "moyu_duck.py")
PART3 = os.path.join(HERE, "_part3.py")
OUT = os.path.join(os.path.dirname(HERE), "摸鱼鸭聊天版.py")


def read(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read().split("\n")


chat = read(CHAT)
duck = read(DUCK)
with io.open(PART3, "r", encoding="utf-8") as f:
    part3 = f.read()

# 1) 通信内核：常量 / 工具 / Peer / ChatNode（取到 ChatUI 类之前）
start = next(i for i, l in enumerate(chat) if l.startswith("import json"))
end = next(i for i, l in enumerate(chat) if l.startswith("class ChatUI"))
core = "\n".join(chat[start:end]).rstrip() + "\n"

# 2) 自检 / 诊断（无需图形界面，原样搬过来）
i_sel = next(i for i, l in enumerate(chat) if l.startswith("def selftest()"))
i_main = next(i for i, l in enumerate(chat) if l.startswith("def main()"))
extra = "\n".join(chat[i_sel:i_main]).rstrip() + "\n"

# 3) 摸鱼鸭：Qt 绑定 + 角色 + 绘制（取到 DesktopPet 类之前）
p_start = next(i for i, l in enumerate(duck) if l.startswith("import argparse"))
p_end = next(i for i, l in enumerate(duck) if l.startswith("class DesktopPet"))
pet = "\n".join(duck[p_start:p_end])

# 拼在一起会撞车的几处，改名/改写法
pet = pet.replace("import datetime\n", "", 1)          # 与内核 from datetime import 冲突
pet = pet.replace('APP_NAME = "摸鱼鸭"', 'PET_NAME = "摸鱼鸭"', 1)
pet = pet.replace(
    "try:\n    from PyQt5.QtGui import QFontDatabase\n"
    "except ImportError:\n    from PySide6.QtGui import QFontDatabase",
    'if QT == "PyQt5":\n    from PyQt5.QtGui import QFontDatabase\n'
    'elif QT == "PySide6":\n    from PySide6.QtGui import QFontDatabase\n'
    "else:\n    from PyQt6.QtGui import QFontDatabase", 1)
pet = pet.replace(                                      # PyQt6 的 QAction 在 QtGui
    "    from PyQt6.QtWidgets import (QAction, QActionGroup, QApplication,\n",
    "    from PyQt6.QtWidgets import (QActionGroup, QApplication,\n", 1)

header = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
  摸鱼鸭 · 欸~你听说没~   （合并版 v1.0）
  ----------------------------------------------------------------------
  把「摸鱼鸭」桌面宠物和「欸~你听说没~」局域网点对点聊天合成了一个软件：

    * 一个进程、一个托盘图标、一套配置（~/.moyuya_chat.json）
    * 聊天：免注册、免服务器，知道对方 IP 就能单聊、群聊、传文件
    * 联动：收到消息 -> 屏幕顶层钻出一条小鱼（气泡里是消息内容）
            -> 小鸭子跑到顶层把鱼吃掉 -> 跑出屏幕 -> 回到桌面继续溜达
    * 点桌面上的小鱼，它告诉你离下班还有多久；
      下班时间可在「摸鱼鸭」菜单 / 托盘菜单里自定义（精确到分钟）

  适用：统信 UOS / deepin / 银河麒麟 / Ubuntu 等 Linux 桌面（需 PyQt5）
  用法：
    python3 摸鱼鸭聊天版.py               启动
    python3 摸鱼鸭聊天版.py --demo        启动后自动演示一次吃鱼动画
    python3 摸鱼鸭聊天版.py --no-pet      只聊天，不要桌面鸭子
    python3 摸鱼鸭聊天版.py --no-link     收到消息时不弹鱼
    python3 摸鱼鸭聊天版.py --p2p-port 50011
    python3 摸鱼鸭聊天版.py --selftest    网络层自检
    python3 摸鱼鸭聊天版.py --help

  本文件由 tools/build.py 自动生成，改代码请改 tools/ 下的源文件。
==============================================================================
"""

'''

body = (header + core + "\n" + pet.rstrip() + "\n\n"
        + "# " + "=" * 75 + "\n"
        + "#  自检 / 环境诊断（无需图形界面）\n"
        + "# " + "=" * 75 + "\n\n" + extra + "\n" + part3)

with io.open(OUT, "w", encoding="utf-8") as f:
    f.write(body)

print("已生成 %s（%d 字符）" % (OUT, len(body)))
