#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
摸鱼鸭 (Moyu Duck) —— 统信 UOS / Linux 桌面宠物

一只小鸭子在桌面上随便溜达；不定时会有小鱼从桌面里钻出来，
小鸭子发现后一路小跑过去吃掉，然后继续溜达，循环往复。

依赖：Python3 + PyQt5（UOS 一般自带，安装见 README.md）

用法：
    python3 moyu_duck.py                # 启动
    python3 moyu_duck.py --size big     # 大号鸭子
    python3 moyu_duck.py --layer desktop  # 真正贴桌面（不被窗口管理器管理）
    python3 moyu_duck.py --help

所有图形均由 QPainter 代码绘制，不依赖任何外部图片资源。
"""

import argparse
import datetime
import math
import os
import random
import sys

# ---------------------------------------------------------------------------
# Qt 绑定：优先 PyQt5（UOS 自带），其次 PySide6 / PyQt6
# ---------------------------------------------------------------------------
QT = None
try:
    from PyQt5.QtCore import Qt, QPointF, QRectF, QSize, QTimer, QElapsedTimer
    from PyQt5.QtGui import (QBrush, QColor, QFont, QFontMetrics, QIcon,
                             QPainter, QPainterPath, QPen, QPixmap,
                             QPolygonF, QRegion)
    from PyQt5.QtWidgets import (QAction, QActionGroup, QApplication, QMenu,
                                 QSystemTrayIcon, QWidget)
    QT = "PyQt5"
except ImportError:
    try:
        from PySide6.QtCore import Qt, QPointF, QRectF, QSize, QTimer, QElapsedTimer
        from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QIcon,
                                   QPainter, QPainterPath, QPen, QPixmap,
                             QPolygonF, QRegion)
        from PySide6.QtWidgets import (QAction, QActionGroup, QApplication,
                                       QMenu, QSystemTrayIcon, QWidget)
        QT = "PySide6"
    except ImportError:
        from PyQt6.QtCore import Qt, QPointF, QRectF, QSize, QTimer, QElapsedTimer
        from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QIcon,
                                 QPainter, QPainterPath, QPen, QPixmap,
                             QPolygonF, QRegion)
        from PyQt6.QtWidgets import (QAction, QActionGroup, QApplication,
                                     QMenu, QSystemTrayIcon, QWidget)
        QT = "PyQt6"

# PyQt6 取消了枚举简写别名，这里做一层兼容
ALIGN_CENTER = getattr(Qt, "AlignCenter", None) or Qt.AlignmentFlag.AlignCenter
NO_PEN = getattr(Qt, "NoPen", None) or Qt.PenStyle.NoPen
SOLID_LINE = getattr(Qt, "SolidLine", None) or Qt.PenStyle.SolidLine
ROUND_CAP = getattr(Qt, "RoundCap", None) or Qt.PenCapStyle.RoundCap
ROUND_JOIN = getattr(Qt, "RoundJoin", None) or Qt.PenJoinStyle.RoundJoin

APP_NAME = "摸鱼鸭"
FPS = 30

# 探测系统中文字体（用于气泡/标签）。仅在已有 QApplication 时调用，
# 因此做成惰性初始化（首次画气泡时探测并缓存）。
try:
    from PyQt5.QtGui import QFontDatabase
except ImportError:
    from PySide6.QtGui import QFontDatabase
_BUBBLE_FONT_FAMILY = None
_BUBBLE_CJK_CANDIDATES = ("Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC",
                          "WenQuanYi Micro Hei", "Source Han Sans SC", "SimHei")


def _bubble_font(point_size=10):
    global _BUBBLE_FONT_FAMILY
    if _BUBBLE_FONT_FAMILY is None:
        try:
            fams = set(QFontDatabase().families())
        except Exception:
            fams = set()
        _BUBBLE_FONT_FAMILY = ""
        for fam in _BUBBLE_CJK_CANDIDATES:
            if fam in fams:
                _BUBBLE_FONT_FAMILY = fam
                break
    f = QFont()
    if _BUBBLE_FONT_FAMILY:
        f.setFamily(_BUBBLE_FONT_FAMILY)
    f.setPointSize(point_size)
    f.setBold(True)
    return f

# ---------------------------------------------------------------------------
# 配色
# ---------------------------------------------------------------------------
C_BODY = QColor("#FFD84D")
C_BODY_DK = QColor("#F2B705")
C_WING = QColor("#FFC107")
C_BEAK = QColor("#FF9E2C")
C_BEAK_DK = QColor("#E8801A")
C_FOOT = QColor("#FFA321")
C_FOOT_DK = QColor("#E88F10")
C_LINE = QColor("#8A5A00")
C_EYE = QColor("#2B2B2B")
C_BLUSH = QColor(255, 140, 140, 120)

C_FISH = QColor("#4FC3F7")
C_FISH_DK = QColor("#0288D1")
C_FISH_BELLY = QColor("#B3E5FC")

C_POOP = QColor("#8B5A2B")
C_POOP_DK = QColor("#5C3A1E")

# 气泡文案
QUACKS = ["嘎！", "嘎嘎", "这条鱼归我了", "摸鱼中…勿扰", "工作？不存在的",
          "再来一条！", "下班了吗", "嘎~"]
EAT_WORDS = ["好吃！", "嘎！", "摸到鱼了", "+1"]


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def ease_out(t):
    """出洞/入洞的缓动"""
    t = clamp(t, 0.0, 1.0)
    return 1.0 - (1.0 - t) * (1.0 - t)


def copysign(v, sign):
    return -v if sign < 0 else v


# ---------------------------------------------------------------------------
# 小鸭子
# ---------------------------------------------------------------------------
class Duck:
    """状态机：idle(发呆) -> walk(溜达) -> run(追鱼) -> eat(吃鱼) -> idle"""

    def __init__(self, x, y):
        self.x = x                 # 脚底中心 x
        self.y = y                 # 脚底 y（可在屏幕任意高度，越大越靠下）
        self.facing = 1            # 1 朝右 / -1 朝左
        self.state = "idle"
        self.scale = 1.0
        self.depth = 1.0           # 远近透视：越靠下越大
        self.speed_mul = 1.0

        self.walk_speed = 48.0     # px/s
        self.run_speed = 170.0     # px/s

        self.target_x = x          # 溜达目标（二维）
        self.target_y = y
        self.timer = random.uniform(0.5, 1.5)   # 状态计时
        self.phase = 0.0           # 步态相位
        self.beak = 0.0            # 喙张合 0~1
        self.happy = 0.0           # 开心跳跃 1->0
        self.bubble = ""
        self.bubble_t = 0.0
        self.dragging = False

    # -- 命中框（用于拖动 / 点击摸头） -------------------------------------
    def bbox(self):
        s = self.scale * self.depth
        w, h = 92 * s, 90 * s
        return QRectF(self.x - w * 0.5, self.y - h * 0.88, w, h)

    def say(self, text, secs=1.6):
        self.bubble = text
        self.bubble_t = secs

    def jump(self):
        self.happy = 1.0

    # -- 行为 ---------------------------------------------------------------
    def pick_target(self, area):
        """随机选一个新的溜达目标（全屏二维）"""
        s = self.scale * self.depth
        roll = random.random()
        if roll < 0.18:
            # 原地发呆一会儿（可能叫一声）
            self.state = "idle"
            self.timer = random.uniform(1.2, 3.4)
            if random.random() < 0.35:
                self.say(random.choice(QUACKS))
            return
        span_x = area.width() * random.uniform(0.10, 0.42)
        span_y = area.height() * random.uniform(0.10, 0.55)
        nx = self.x + copysign(span_x, 1 if random.random() < 0.5 else -1)
        ny = self.y + copysign(span_y, 1 if random.random() < 0.5 else -1)
        self.target_x = clamp(nx, area.left() + 55 * s, area.right() - 55 * s)
        self.target_y = clamp(ny, area.top() + 78 * s, area.bottom() - 8)
        # 小几率一路小跑
        self.state = "run" if random.random() < 0.18 else "walk"

    def chase(self, fish):
        self.state = "run"
        self.target_x = fish.x
        self.target_y = fish.y

    def _step_to(self, tx, ty, speed, dt):
        """朝二维目标点走一步，返回剩余距离"""
        dx, dy = tx - self.x, ty - self.y
        dist = math.hypot(dx, dy)
        if dist < 0.001:
            return 0.0
        if abs(dx) > 1.0:
            self.facing = 1 if dx > 0 else -1
        step = min(speed * self.speed_mul * dt, dist)
        self.x += dx / dist * step
        self.y += dy / dist * step
        return max(0.0, dist - step)

    def update(self, dt, area, fish):
        """返回事件字符串列表"""
        events = []
        self.phase += dt * (13.0 if self.state == "run" else 5.5)
        if self.happy > 0:
            self.happy = max(0.0, self.happy - dt * 2.2)
        if self.bubble_t > 0:
            self.bubble_t -= dt

        st = self.state
        if st == "run":
            # 追鱼时朝鱼跑，否则朝溜达目标跑
            if fish is not None and fish.alive:
                tx, ty = fish.x, fish.y
            else:
                tx, ty = self.target_x, self.target_y
            dx, dy = tx - self.x, ty - self.y
            if fish is not None and fish.alive and math.hypot(dx, dy) < 34 * self.scale:
                self.state = "eat"
                self.timer = 0.8
            else:
                left = self._step_to(tx, ty, self.run_speed, dt)
                if left < 2:
                    self.state = "idle"
                    self.timer = random.uniform(0.4, 1.6)

        elif st == "walk":
            left = self._step_to(self.target_x, self.target_y, self.walk_speed, dt)
            if left < 3:
                self.state = "idle"
                self.timer = random.uniform(0.6, 3.0)
                if random.random() < 0.22:
                    self.say(random.choice(QUACKS))

        elif st == "idle":
            self.timer -= dt
            if self.timer <= 0:
                self.pick_target(area)

        elif st == "eat":
            self.timer -= dt
            self.beak = abs(math.sin(self.timer * 16.0))
            if self.timer <= 0:
                self.beak = 0.0
                self.state = "idle"
                self.timer = random.uniform(0.6, 1.4)
                self.say(random.choice(EAT_WORDS), 1.3)
                self.jump()
                events.append("eat_done")

        elif st == "rest":
            # 吃饱了，原地歇一会儿
            self.timer -= dt
            if self.timer <= 0:
                self.state = "idle"
                self.timer = random.uniform(0.4, 1.0)

        # 边界保护 + 透视缩放（越靠下越大）
        s = self.scale * self.depth
        mx, my = 45 * s, 76 * s
        if self.x < area.left() + mx:
            self.x = area.left() + mx
            if self.state not in ("eat", "rest"):
                self.target_x = area.right() - mx
                self.facing = 1
        elif self.x > area.right() - mx:
            self.x = area.right() - mx
            if self.state not in ("eat", "rest"):
                self.target_x = area.left() + mx
                self.facing = -1
        if self.y < area.top() + my:
            self.y = area.top() + my
            if self.state not in ("eat", "rest"):
                self.target_y = min(area.bottom() - 8, self.y + area.height() * 0.35)
        elif self.y > area.bottom() - 8:
            self.y = area.bottom() - 8
            if self.state not in ("eat", "rest"):
                self.target_y = max(area.top() + my, self.y - area.height() * 0.35)

        rel = clamp((self.y - area.top()) / max(1.0, area.height()), 0.0, 1.0)
        self.depth = 0.84 + 0.16 * rel
        return events


def _draw_leg(p, phase, dark=False):
    base = C_FOOT_DK if dark else C_FOOT
    swing = math.sin(phase) * 9.0
    lift = max(0.0, math.sin(phase)) * 5.0
    hip = QPointF(0, -22)
    knee = QPointF(swing * 0.45, -13 - lift * 0.35)
    foot = QPointF(swing, -2 - lift)
    p.setPen(QPen(base, 5.5, SOLID_LINE, ROUND_CAP, ROUND_JOIN))
    p.drawLine(hip, knee)
    p.drawLine(knee, foot)
    p.setPen(NO_PEN)
    p.setBrush(QBrush(base))
    p.drawPolygon(QPolygonF([
        QPointF(foot.x() - 5, foot.y() + 1),
        QPointF(foot.x() + 11, foot.y() + 1),
        QPointF(foot.x() + swing * 0.15, foot.y() + 7),
    ]))


def draw_duck(p, d):
    """绘制小黄鸭。原点 = 脚底中心，默认朝右。"""
    s = d.scale * getattr(d, "depth", 1.0)
    p.save()
    p.translate(d.x, d.y)

    # 影子
    p.setPen(NO_PEN)
    p.setBrush(QColor(0, 0, 0, 42))
    p.drawEllipse(QRectF(-30 * s, -8 * s, 60 * s, 13 * s))

    # 呼吸 / 走路起伏 / 前倾
    bob = 0.0
    tilt = 0.0
    if d.state == "walk":
        bob = -abs(math.sin(d.phase)) * 3.0
    elif d.state == "run":
        bob = -abs(math.sin(d.phase)) * 5.5
        tilt = -11.0
    elif d.state == "eat":
        bob = -1.5
        tilt = -9.0
    elif d.state == "rest":
        bob = 3.0                      # 吃饱了蹲着歇会儿
    else:
        bob = math.sin(d.phase * 0.55) * 1.2
    if d.happy > 0:
        bob -= math.sin(clamp(1.0 - d.happy, 0.0, 1.0) * math.pi) * 16.0 * d.happy

    p.translate(0, bob * s)
    p.scale(d.facing * s, s)
    if tilt:
        p.rotate(tilt)
    p.setRenderHint(QPainter.Antialiasing, True)

    # 1) 后腿（暗）
    p.save()
    p.translate(-5, 0)
    _draw_leg(p, d.phase + math.pi, dark=True)
    p.restore()

    # 2) 身体（小黄鸭：圆滚滚，没有尾巴）
    p.setPen(QPen(C_LINE, 2.0, SOLID_LINE, ROUND_CAP, ROUND_JOIN))
    p.setBrush(QBrush(C_BODY))
    p.drawEllipse(QRectF(-30, -50, 58, 42))

    # 4) 肚皮高光
    p.setPen(NO_PEN)
    p.setBrush(QColor(255, 255, 255, 90))
    p.drawEllipse(QRectF(-16, -32, 30, 15))

    # 5) 翅膀（走路/跑步时扑扇）
    flap = math.sin(d.phase * 1.0) * (16.0 if d.state == "run" else 8.0)
    p.save()
    p.translate(-2, -38)
    p.rotate(flap if d.state in ("walk", "run") else math.sin(d.phase * 0.5) * 3)
    p.setPen(QPen(C_LINE, 1.6, SOLID_LINE, ROUND_CAP, ROUND_JOIN))
    p.setBrush(QBrush(C_WING))
    p.drawEllipse(QRectF(-16, -8, 32, 17))
    p.restore()

    # 6) 前腿（亮）
    p.save()
    p.translate(6, 0)
    _draw_leg(p, d.phase, dark=False)
    p.restore()

    # 7) 头（小黄鸭：又圆又大的脑袋）
    head = QPointF(20, -63)
    p.setPen(QPen(C_LINE, 2.2, SOLID_LINE, ROUND_CAP, ROUND_JOIN))
    p.setBrush(QBrush(C_BODY))
    p.drawEllipse(QRectF(head.x() - 18, head.y() - 17, 36, 34))

    # 8) 喙：小黄鸭的短圆喙（上下两片，前端圆钝）
    open_y = d.beak * 5.5
    p.setPen(QPen(C_BEAK_DK, 1.8, SOLID_LINE, ROUND_CAP, ROUND_JOIN))
    p.setBrush(QBrush(C_BEAK))
    upper = QPainterPath()
    upper.moveTo(30, -69)
    upper.quadTo(53, -68, 54, -60)          # 上缘 -> 圆钝的喙尖
    upper.quadTo(53, -53, 30, -55)          # 下缘收回
    upper.closeSubpath()
    p.drawPath(upper)
    lower = QPainterPath()
    lower.moveTo(30, -56)
    lower.quadTo(43, -56 + open_y * 0.5, 45, -52 + open_y)
    lower.quadTo(42, -45 + open_y, 30, -48)
    lower.closeSubpath()
    p.drawPath(lower)

    # 9) 眼睛（小黄鸭的黑豆眼；休息时眯成一条缝）
    ex, ey = head.x() + 4, head.y() - 2
    if d.state == "rest":
        p.setPen(QPen(C_EYE, 2.4, SOLID_LINE, ROUND_CAP, ROUND_JOIN))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(ex - 6, ey - 4, 12, 9), 20 * 16, 140 * 16)
        p.setPen(NO_PEN)
    else:
        p.setPen(NO_PEN)
        look = 1.6 if d.state in ("run", "eat") else 0.6
        p.setBrush(QBrush(C_EYE))
        p.drawEllipse(QRectF(ex - 4.2 + look, ey - 4.6, 8.4, 9.2))
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QRectF(ex - 1.4 + look, ey - 3.4, 3.2, 3.4))

    # 10) 腮红
    p.setBrush(QBrush(C_BLUSH))
    p.drawEllipse(QRectF(head.x() - 11, head.y() + 6, 12, 6))

    p.restore()


# ---------------------------------------------------------------------------
# 小鱼
# ---------------------------------------------------------------------------
class Fish:
    """从桌面里钻出来的小鱼：rise -> swim -> dive -> gone"""

    RISE_T = 0.9
    DIVE_T = 0.5
    # 停留时间给足：正常情况下都是等鸭子跑过来吃掉才消失，
    # 这个时长只是"鸭子始终没吃到"时的兜底。
    LIFE = 120.0

    def __init__(self, x, y):
        self.x = x
        self.y = y                  # 洞口所在的地面线
        self.state = "rise"
        self.progress = 0.0         # 出洞程度 0~1
        self.life = self.LIFE
        self.wobble = random.uniform(0.0, 6.28)
        self.facing = random.choice((-1, 1))
        self.alive = True
        self.scale = 1.0
        self.depth = 1.0
        self.bubble = ""
        self.bubble_t = 0.0

    def say(self, text, secs=3.0):
        self.bubble = text
        self.bubble_t = secs

    def bbox(self):
        """点击命中框"""
        s = self.scale * self.depth
        cy = self.y + self.head_y()
        w, h = 62 * s, 58 * s
        return QRectF(self.x - w * 0.5, cy - h * 0.8, w, h)

    def update(self, dt):
        self.wobble += dt * 3.2
        if self.bubble_t > 0:
            self.bubble_t -= dt
        if self.state == "rise":
            self.progress += dt / self.RISE_T
            if self.progress >= 1.0:
                self.progress = 1.0
                self.state = "swim"
        elif self.state == "swim":
            self.life -= dt
            if self.life <= 0:
                self.state = "dive"
        elif self.state == "dive":
            self.alive = False
            self.progress -= dt / self.DIVE_T
            if self.progress <= 0:
                self.progress = 0.0
                return "gone"
        return None

    def head_y(self):
        """鱼身中心相对地面线的高度（负数=在地面之上）"""
        return -ease_out(self.progress) * 46.0 * self.scale


def draw_fish(p, f):
    s = f.scale * getattr(f, "depth", 1.0)
    p.save()
    p.translate(f.x, f.y)
    p.setRenderHint(QPainter.Antialiasing, True)

    # 地上的小水洼
    p.setPen(NO_PEN)
    p.setBrush(QColor(120, 200, 255, 60))
    p.drawEllipse(QRectF(-32 * s, -7 * s, 64 * s, 14 * s))
    # 洞口
    p.setBrush(QColor(70, 58, 44, 150))
    p.drawEllipse(QRectF(-15 * s, -5 * s, 30 * s, 10 * s))

    # 只画地面线以上（实现"钻出桌面"）
    p.save()
    p.setClipRect(QRectF(-46 * s, -78 * s, 92 * s, 74 * s))
    wob = math.sin(f.wobble) * 3.0 * s
    p.translate(wob * 0.5, f.head_y() + math.sin(f.wobble * 0.8) * 2.0 * s)
    p.scale(f.facing * s, s)

    # 尾巴
    p.setPen(QPen(C_FISH_DK, 1.8, SOLID_LINE, ROUND_CAP, ROUND_JOIN))
    p.setBrush(QBrush(C_FISH_DK))
    swing = math.sin(f.wobble * 2.2) * 5.0
    p.drawPolygon(QPolygonF([
        QPointF(-16, -22), QPointF(-32, -30 + swing), QPointF(-30, -13 + swing),
    ]))
    # 身体
    p.setBrush(QBrush(C_FISH))
    p.drawEllipse(QRectF(-19, -33, 38, 23))
    # 肚皮
    p.setPen(NO_PEN)
    p.setBrush(QBrush(C_FISH_BELLY))
    p.drawEllipse(QRectF(-12, -18, 26, 9))
    # 背鳍
    p.setBrush(QBrush(C_FISH_DK))
    p.drawPolygon(QPolygonF([QPointF(-4, -33), QPointF(3, -41), QPointF(8, -31)]))
    # 眼 + 嘴
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(QRectF(9, -28, 7, 7))
    p.setBrush(QBrush(C_EYE))
    p.drawEllipse(QRectF(11.5, -26.5, 3.4, 3.8))
    p.setBrush(QBrush(C_FISH_DK))
    p.drawEllipse(QRectF(15, -20, 4, 3))
    p.restore()
    p.restore()


# ---------------------------------------------------------------------------
# 鸭屎
# ---------------------------------------------------------------------------
class Poop:
    """吃完鱼随手留下的"地雷"，3 分钟后自动消失"""

    LIFE = 180.0      # 存活 3 分钟
    FADE = 12.0       # 最后 12 秒淡出
    MAX = 6           # 场上最多留几坨

    def __init__(self, x, y, scale=1.0):
        self.x = x
        self.y = y
        self.scale = scale
        self.age = 0.0
        self.bubble = ""
        self.bubble_t = 0.0
        self.pop = 1.0            # 落下时的挤压动画 1->0

    def update(self, dt):
        self.age += dt
        if self.bubble_t > 0:
            self.bubble_t -= dt
        if self.pop > 0:
            self.pop = max(0.0, self.pop - dt * 4.0)
        return self.age >= self.LIFE

    def alpha(self):
        left = self.LIFE - self.age
        if left >= self.FADE:
            return 1.0
        return clamp(left / self.FADE, 0.0, 1.0)

    def bbox(self):
        s = self.scale
        w, h = 46 * s, 46 * s
        return QRectF(self.x - w * 0.5, self.y - h * 0.92, w, h)

    def say(self, text, secs=3.0):
        self.bubble = text
        self.bubble_t = secs


def draw_poop(p, poop):
    s = poop.scale
    alpha = poop.alpha()
    if alpha <= 0.01:
        return
    p.save()
    p.translate(poop.x, poop.y)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setOpacity(alpha)

    squash = 1.0 + poop.pop * 0.35          # 刚落下时被压扁一点
    flat = 1.0 - poop.pop * 0.25

    p.setPen(NO_PEN)
    p.setBrush(QColor(0, 0, 0, 38))
    p.drawEllipse(QRectF(-19 * s * squash, -6 * s, 38 * s * squash, 10 * s))

    p.setPen(QPen(C_POOP_DK, 2.0, SOLID_LINE, ROUND_CAP, ROUND_JOIN))
    p.setBrush(QBrush(C_POOP))
    # 三层由下往上堆叠
    p.drawEllipse(QRectF(-17 * s * squash, -13 * s * flat, 34 * s * squash, 15 * s * flat))
    p.drawEllipse(QRectF(-13 * s, -22 * s * flat, 26 * s, 14 * s * flat))
    p.drawEllipse(QRectF(-9 * s, -30 * s * flat, 18 * s, 13 * s * flat))
    # 顶尖
    p.drawEllipse(QRectF(-5 * s, -37 * s * flat, 10 * s, 10 * s * flat))
    tip = QPainterPath()
    tip.moveTo(-2 * s, -36 * s * flat)
    tip.quadTo(0, -45 * s * flat, 3 * s, -35 * s * flat)
    tip.closeSubpath()
    p.drawPath(tip)
    # 高光
    p.setPen(NO_PEN)
    p.setBrush(QColor(255, 255, 255, 55))
    p.drawEllipse(QRectF(-11 * s, -28 * s * flat, 9 * s, 5 * s * flat))
    # 两颗小眼睛（让它看着欠揍一点）
    p.setBrush(QBrush(C_EYE))
    p.drawEllipse(QRectF(-7 * s, -20 * s * flat, 3.4 * s, 3.8 * s * flat))
    p.drawEllipse(QRectF(3.4 * s, -20 * s * flat, 3.4 * s, 3.8 * s * flat))
    p.restore()


# ---------------------------------------------------------------------------
# 特效粒子
# ---------------------------------------------------------------------------
class Fx:
    def __init__(self, kind, x, y, vx=0.0, vy=0.0, life=0.8, color=None, text=""):
        self.kind = kind
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.life = life
        self.age = 0.0
        self.color = color or QColor("#FFD54F")
        self.text = text

    def update(self, dt):
        self.age += dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        if self.kind in ("crumb", "star"):
            self.vy += 260.0 * dt
        return self.age >= self.life


def draw_fx(p, f):
    k = 1.0 - f.age / f.life
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(NO_PEN)
    if f.kind == "crumb":
        p.setBrush(QColor(120, 100, 80, int(220 * k)))
        r = 2.6 * k + 0.8
        p.drawEllipse(QRectF(f.x - r, f.y - r, r * 2, r * 2))
    elif f.kind == "star":
        p.setBrush(QColor(255, 213, 79, int(240 * k)))
        p.save()
        p.translate(f.x, f.y)
        p.rotate(f.age * 260)
        size = 9.0 * k + 3
        path = QPainterPath()
        for i in range(5):
            a1 = math.radians(-90 + i * 72)
            a2 = math.radians(-90 + i * 72 + 36)
            if i == 0:
                path.moveTo(math.cos(a1) * size, math.sin(a1) * size)
            else:
                path.lineTo(math.cos(a1) * size, math.sin(a1) * size)
            path.lineTo(math.cos(a2) * size * 0.45, math.sin(a2) * size * 0.45)
        path.closeSubpath()
        p.drawPath(path)
        p.restore()
    elif f.kind == "splash":
        p.setBrush(QColor(120, 200, 255, int(200 * k)))
        r = 5.0 + (1 - k) * 16
        p.drawEllipse(QRectF(f.x - r, f.y - r * 0.5, r * 2, r))
    p.restore()


def draw_bubble(p, x, y, text):
    font = _bubble_font(10)
    p.setFont(font)
    fm = QFontMetrics(font)
    w = fm.horizontalAdvance(text) + 20
    h = fm.height() + 10
    rect = QRectF(x - w / 2.0, y - h - 4, w, h)
    path = QPainterPath()
    path.addRoundedRect(rect, 9, 9)
    p.setPen(QPen(QColor(60, 60, 60, 140), 1.2))
    p.setBrush(QColor(255, 255, 255, 238))
    p.drawPath(path)
    p.setPen(NO_PEN)
    p.setBrush(QColor(255, 255, 255, 238))
    p.drawPolygon(QPolygonF([
        QPointF(x - 6, rect.bottom() - 1),
        QPointF(x + 6, rect.bottom() - 1),
        QPointF(x, rect.bottom() + 8),
    ]))
    p.setPen(QColor(45, 45, 45))
    p.drawText(rect, ALIGN_CENTER, text)


# ---------------------------------------------------------------------------
# 托盘图标（代码绘制，无需图片文件）
# ---------------------------------------------------------------------------
def make_tray_icon(size=64):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.translate(size * 0.5, size * 0.56)
    s = size / 78.0
    p.scale(s, s)
    p.setPen(QPen(C_LINE, 2.2 * 1.2, SOLID_LINE, ROUND_CAP, ROUND_JOIN))
    p.setBrush(QBrush(C_BODY))
    p.drawEllipse(QRectF(-30, -34, 60, 46))          # 身体
    p.drawEllipse(QRectF(2, -62, 34, 32))            # 头
    beak = QPainterPath()                            # 圆钝的小黄鸭喙
    beak.moveTo(30, -56)
    beak.quadTo(50, -55, 51, -49)
    beak.quadTo(49, -43, 30, -45)
    beak.closeSubpath()
    p.setBrush(QBrush(C_BEAK))
    p.drawPath(beak)
    p.setBrush(QBrush(C_EYE))
    p.drawEllipse(QRectF(21, -53, 6.5, 7.5))
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(QRectF(22.5, -52, 2.6, 2.8))
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class DesktopPet(QWidget):

    def __init__(self, layer="bottom", scale=1.0, speed=1.0,
                 mouse_mode="smart", bottom_only=False, tray=True,
                 off_hour=18, start_hour=8):
        super().__init__()
        self.layer_mode = layer
        self.scale_default = scale
        self.speed_mul = speed
        self.mouse_mode = mouse_mode      # smart / through / full
        self.bottom_only = bottom_only
        self.use_tray = tray
        self.off_hour = off_hour            # 下班时间（24h）
        self.start_hour = start_hour        # 当天几点开始"上班摸鱼"

        self.paused = False
        self.score = 0
        self.fish = None
        self.poops = []
        self.fx = []
        self.next_fish_in = random.uniform(3.0, 7.0)
        self.next_brag_in = random.uniform(90.0, 180.0)   # 摸鱼时长播报
        self.drag_offset = None
        self._hidden = False
        self._mask_dirty = True
        self._last_mask = None
        self._mask_tick = 0

        self.setWindowTitle(APP_NAME)
        self._apply_window_flags()

        area = self._area()
        self.setGeometry(area)
        self.duck = Duck(area.center().x(), area.bottom() - 20)
        self.duck.scale = scale
        self.duck.speed_mul = speed

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.clock = QElapsedTimer()
        self.clock.start()
        self.timer.start(int(1000.0 / FPS))

        self._build_tray()
        self._apply_mouse_mode()

        scr = QApplication.primaryScreen()
        if scr is not None and hasattr(scr, "availableGeometryChanged"):
            scr.availableGeometryChanged.connect(self._on_screen_change)

    # -- 窗口属性 -----------------------------------------------------------
    def _apply_window_flags(self):
        flags = Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
        if self.layer_mode == "desktop":
            # 彻底脱离窗口管理器，永远贴在壁纸之上、所有窗口之下
            flags |= Qt.X11BypassWindowManagerHint | Qt.WindowStaysOnBottomHint
        elif self.layer_mode == "normal":
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags |= Qt.WindowStaysOnBottomHint | Qt.Tool
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def _apply_mouse_mode(self):
        """smart  : 只在鸭子/鱼/鸭屎这几个小方块上接收鼠标，其余区域完全穿透
        through: 完全穿透，桌面图标、窗口随便点
        full   : 整屏接收鼠标（可拖鸭子），但会挡住下面的图标
        """
        if self.mouse_mode == "through":
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.clearMask()
            self._last_mask = None
        elif self.mouse_mode == "full":
            self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self.clearMask()
            self._last_mask = None
        else:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self._mask_dirty = True
            self._refresh_hit_mask(force=True)

    def _refresh_hit_mask(self, force=False):
        """把窗口的可点击区域裁成几个角色（含气泡）所在的小矩形，
        X11 下其余区域自动穿透，桌面图标照常可点。"""
        if self.mouse_mode != "smart":
            return
        self._mask_tick += 1
        if not force and self._mask_tick % 2:      # 15Hz 足够，省点开销
            return
        area = self._roam_area()
        region = QRegion()
        pad = 26                                   # 角色本体留边，避免裁到抗锯齿边缘
        rects = [self.duck.bbox()]
        if self.fish is not None and self.fish.alive:
            rects.append(self.fish.bbox())
        for pp in self.poops:
            rects.append(pp.bbox())
        for r in rects:
            region = region.united(QRegion(r.adjusted(-pad, -pad, pad, pad).toRect()))
        # 气泡也要纳入遮罩，否则会被窗口形状裁掉看不见
        for br in self._bubble_mask_rects(area):
            region = region.united(QRegion(br.adjusted(-10, -10, 10, 10).toRect()))
        if region.isEmpty():
            return
        if not force and self._last_mask is not None and self._last_mask == region:
            return
        try:
            self.setMask(region)
            self._last_mask = region
        except Exception:
            # 某些桌面环境不支持窗口遮罩 -> 自动退回完全穿透，保证不挡操作
            self.mouse_mode = "through"
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.clearMask()

    def _bubble_mask_rects(self, area):
        """返回当前所有活跃气泡的矩形（窗口坐标），用于补进点击遮罩"""
        out = []
        if self.duck.bubble_t > 0 and self.duck.bubble:
            s = self.duck.scale * self.duck.depth
            by = self.duck.y - 100 * s
            if by < area.top() + 34:
                by = self.duck.y + 26
            out.append(self._bubble_rect(self.duck.x, by, self.duck.bubble))
        if self.fish is not None and self.fish.bubble_t > 0 and self.fish.bubble:
            fs = self.fish.scale * self.fish.depth
            fy = self.fish.y + self.fish.head_y() - 44 * fs
            if fy < area.top() + 34:
                fy = self.fish.y + 30
            out.append(self._bubble_rect(self.fish.x, fy, self.fish.bubble))
        for pp in self.poops:
            if pp.bubble_t > 0 and pp.bubble:
                py = pp.y - 52 * pp.scale
                if py < area.top() + 34:
                    py = pp.y + 22
                out.append(self._bubble_rect(pp.x, py, pp.bubble))
        return out

    @staticmethod
    def _bubble_rect(cx, bottom_y, text):
        font = _bubble_font(10)
        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(text) + 20
        h = fm.height() + 10
        return QRectF(cx - w / 2.0, bottom_y - h - 4, w, h)

    def _area(self):
        scr = QApplication.primaryScreen()
        g = scr.availableGeometry() if scr is not None else self.geometry()
        return g

    def _roam_area(self):
        """鸭子可活动的区域"""
        g = self._area()
        top = g.top() + (g.height() * 0.45 if self.bottom_only else 10)
        return QRectF(g.left(), top, g.width(), g.bottom() - top)

    def _on_screen_change(self, _g=None):
        self.setGeometry(self._area())
        a = self._roam_area()
        self.duck.x = clamp(self.duck.x, a.left() + 40, a.right() - 40)
        self.duck.y = clamp(self.duck.y, a.top() + 60, a.bottom() - 5)

    # -- 托盘 ---------------------------------------------------------------
    def _build_tray(self):
        if not self.use_tray or not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        self.tray = QSystemTrayIcon(make_tray_icon(), self)
        self.tray.setToolTip("%s · 摸鱼值 %d" % (APP_NAME, self.score))
        menu = QMenu()
        self.tray.setContextMenu(menu)

        self.act_pause = QAction("暂停溜达", self)
        self.act_pause.setCheckable(True)
        self.act_pause.triggered.connect(self.toggle_pause)
        menu.addAction(self.act_pause)

        menu.addSeparator()

        size_menu = menu.addMenu("鸭子大小")
        grp = QActionGroup(self)
        for label, val in (("小", 0.7), ("中", 1.0), ("大", 1.45)):
            a = QAction(label, self)
            a.setCheckable(True)
            a.setActionGroup(grp)
            a.setChecked(abs(val - self.scale_default) < 0.01)
            a.triggered.connect(lambda _c, v=val: self.set_scale(v))
            size_menu.addAction(a)

        sp_menu = menu.addMenu("溜达速度")
        grp2 = QActionGroup(self)
        for label, val in (("慢悠悠", 0.6), ("正常", 1.0), ("闪电", 1.8)):
            a = QAction(label, self)
            a.setCheckable(True)
            a.setActionGroup(grp2)
            a.setChecked(abs(val - self.speed_mul) < 0.01)
            a.triggered.connect(lambda _c, v=val: self.set_speed(v))
            sp_menu.addAction(a)

        mouse_menu = menu.addMenu("鼠标模式")
        grp3 = QActionGroup(self)
        for label, val, tip in (
                ("智能（只挡住小家伙）", "smart",
                 "只有鸭子/小鱼/鸭屎这几个小块会挡鼠标，其余照常点桌面"),
                ("完全穿透", "through",
                 "任何位置都不挡操作，但点不到鸭子和小鱼"),
                ("整屏互动", "full",
                 "可拖动鸭子，但整屏都会挡住下面的图标")):
            a = QAction(label, self)
            a.setCheckable(True)
            a.setActionGroup(grp3)
            a.setChecked(self.mouse_mode == val)
            a.setToolTip(tip)
            a.triggered.connect(lambda _c, v=val: self.set_mouse_mode(v))
            mouse_menu.addAction(a)

        self.act_bottom = QAction("只在桌面下半部分活动", self)
        self.act_bottom.setCheckable(True)
        self.act_bottom.setChecked(self.bottom_only)
        self.act_bottom.triggered.connect(self.toggle_bottom)
        menu.addAction(self.act_bottom)

        menu.addSeparator()
        menu.addAction("立刻放一条鱼", self.spawn_fish)
        menu.addAction("数一数摸鱼值", self.show_score)
        menu.addSeparator()
        menu.addAction("退出", self.quit)
        self.tray.show()

    def _refresh_tray(self):
        if getattr(self, "tray", None):
            self.tray.setToolTip("%s · 摸鱼值 %d" % (APP_NAME, self.score))

    # -- 托盘动作 -----------------------------------------------------------
    def toggle_pause(self):
        self.paused = not self.paused
        self.act_pause.setChecked(self.paused)
        self.act_pause.setText("继续溜达" if self.paused else "暂停溜达")
        if self.paused:
            self.duck.say("ZZZ…", 3.0)
        self.update()

    def set_scale(self, v):
        self.duck.scale = v
        self.update()

    def set_speed(self, v):
        self.speed_mul = v
        self.duck.speed_mul = v

    def toggle_bottom(self):
        self.bottom_only = not self.bottom_only
        a = self._roam_area()
        self.duck.y = clamp(self.duck.y, a.top() + 60, a.bottom() - 5)
        self.update()

    def set_mouse_mode(self, mode):
        self.mouse_mode = mode
        self._apply_mouse_mode()
        self.update()

    def spawn_fish(self):
        if self.fish is not None:
            return
        a = self._roam_area()
        m = 90
        x = random.uniform(a.left() + m, a.right() - m)
        y = random.uniform(a.top() + 110, a.bottom() - 12)
        # 别刷在鸭子脸上（二维距离）
        if math.hypot(x - self.duck.x, y - self.duck.y) < 150:
            x += 200 if x < a.center().x() else -200
            x = clamp(x, a.left() + m, a.right() - m)
        self.fish = Fish(x, y)
        self.fish.scale = self.duck.scale
        self.fish.depth = self._depth_at(y)
        for _ in range(10):
            self.fx.append(Fx("crumb", x + random.uniform(-14, 14), y,
                              random.uniform(-70, 70), random.uniform(-150, -60),
                              life=random.uniform(0.5, 0.9)))
        if self.duck.state != "rest":
            self.duck.chase(self.fish)
        self.update()

    def _depth_at(self, y):
        """透视缩放：越靠屏幕下方越大"""
        a = self._roam_area()
        rel = clamp((y - a.top()) / max(1.0, a.height()), 0.0, 1.0)
        return 0.84 + 0.16 * rel

    # -- 时间播报 -----------------------------------------------------------
    def _fished_text(self):
        """今天从 start_hour 点开始到现在，摸了多久的鱼"""
        now = datetime.datetime.now()
        start = now.replace(hour=self.start_hour, minute=0, second=0, microsecond=0)
        secs = int((now - start).total_seconds())
        if secs < 0:                       # 还没到上班点（比如凌晨）
            return "还没到点，先摸为敬"
        h, m = divmod(secs // 60, 60)
        if h <= 0:
            return "今天摸了%d分钟鱼了" % m
        return "今天摸了%d小时%d分钟鱼了" % (h, m)

    def _offwork_text(self):
        """距离下班还有多久"""
        now = datetime.datetime.now()
        end = now.replace(hour=self.off_hour, minute=0, second=0, microsecond=0)
        secs = int((end - now).total_seconds())
        if secs <= 0:
            return "已经下班啦！"
        h, m = divmod(secs // 60, 60)
        if h <= 0:
            return "离下班还有%d分钟" % m
        return "离下班还有%d小时%d分钟" % (h, m)

    def show_score(self):
        self.duck.say("摸鱼值 %d" % self.score, 2.2)

    def quit(self):
        self.timer.stop()
        if getattr(self, "tray", None):
            self.tray.hide()
        QApplication.quit()

    # -- 主循环 -------------------------------------------------------------
    def _tick(self):
        dt = self.clock.restart() / 1000.0
        dt = clamp(dt, 0.0, 0.1)          # 切回桌面时不要瞬移
        if self.paused:
            self.update()
            return

        area = self._roam_area()

        # 鱼
        if self.fish is None:
            self.next_fish_in -= dt
            if self.next_fish_in <= 0:
                self.spawn_fish()
                self.next_fish_in = random.uniform(9.0, 22.0)
        else:
            r = self.fish.update(dt)
            if r == "gone":
                self.fish = None
                if self.duck.state in ("run", "eat"):
                    self.duck.state = "idle"
                    self.duck.timer = 0.6
            elif self.duck.state not in ("run", "eat", "rest") and self.fish.state == "swim":
                self.duck.chase(self.fish)

        # 摸鱼时长播报：随机每隔几分钟来一句
        self.next_brag_in -= dt
        if self.next_brag_in <= 0:
            self.next_brag_in = random.uniform(150.0, 330.0)
            if self.duck.state != "eat" and self.duck.bubble_t <= 0:
                self.duck.say(self._fished_text(), 4.0)

        # 鸭子
        events = self.duck.update(dt, area, self.fish)
        for ev in events:
            if ev == "eat_done" and self.fish is not None:
                fx, fy = self.fish.x, self.fish.y + self.fish.head_y()
                for _ in range(9):
                    self.fx.append(Fx("star", fx + random.uniform(-16, 16),
                                      fy + random.uniform(-10, 10),
                                      random.uniform(-40, 40),
                                      random.uniform(-120, -40),
                                      life=random.uniform(0.6, 1.0)))
                for _ in range(4):
                    self.fx.append(Fx("splash", fx, fy,
                                      life=random.uniform(0.4, 0.7)))
                self.fish = None
                self.score += 1
                self._refresh_tray()
                # 吃完随手来一坨（大概一半的概率）
                if random.random() < 0.5:
                    self.drop_poop()
                # 每 5 条吃撑了，歇一会儿
                if self.score % 5 == 0:
                    self.duck.state = "rest"
                    self.duck.timer = random.uniform(6.0, 10.0)
                    self.duck.say("吃饱了，歇会儿~", 5.0)

        # 鸭屎：3 分钟后自动消失
        if self.poops:
            self.poops = [pp for pp in self.poops if not pp.update(dt)]

        # 粒子
        if self.fx:
            self.fx = [f for f in self.fx if not f.update(dt)]

        self._refresh_hit_mask()
        self.update()

    def drop_poop(self):
        if len(self.poops) >= Poop.MAX:
            self.poops.pop(0)
        self.poops.append(Poop(self.duck.x, self.duck.y,
                               self.duck.scale * self.duck.depth))

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 后面的先画（按脚底 y 排序，营造前后关系）
        items = []
        for pp in self.poops:
            items.append((pp.y, "poop", pp))
        if self.fish is not None:
            items.append((self.fish.y, "fish", self.fish))
        items.append((self.duck.y, "duck", self.duck))
        items.sort(key=lambda t: t[0])

        for _, kind, obj in items:
            if kind == "fish":
                draw_fish(p, obj)
            elif kind == "poop":
                draw_poop(p, obj)
            else:
                draw_duck(p, obj)

        for f in self.fx:
            draw_fx(p, f)

        area = self._roam_area()
        if self.duck.bubble_t > 0 and self.duck.bubble:
            s = self.duck.scale * self.duck.depth
            by = self.duck.y - 100 * s
            if by < area.top() + 34:          # 走到屏幕顶部时气泡翻到脚下
                by = self.duck.y + 26
            draw_bubble(p, self.duck.x, by, self.duck.bubble)

        if self.fish is not None and self.fish.bubble_t > 0 and self.fish.bubble:
            fs = self.fish.scale * self.fish.depth
            fy = self.fish.y + self.fish.head_y() - 44 * fs
            if fy < area.top() + 34:
                fy = self.fish.y + 30
            draw_bubble(p, self.fish.x, fy, self.fish.bubble)

        for pp in self.poops:
            if pp.bubble_t > 0 and pp.bubble:
                py = pp.y - 52 * pp.scale
                if py < area.top() + 34:
                    py = pp.y + 22
                draw_bubble(p, pp.x, py, pp.bubble)
        p.end()

    # -- 交互 ---------------------------------------------------------------
    @staticmethod
    def _ev_pos(e):
        """Qt5 / Qt6 鼠标坐标兼容"""
        return e.position() if hasattr(e, "position") else QPointF(e.pos())

    def mousePressEvent(self, e):
        pos = self._ev_pos(e)
        if e.button() != Qt.LeftButton:
            return
        # 1) 小鱼：点一下它会告诉你离下班还有多久
        if self.fish is not None and self.fish.alive \
                and self.fish.bbox().contains(pos):
            self.fish.say(self._offwork_text(), 3.5)
            self.update()
            return
        # 2) 鸭屎：点一下提示"土地被污染，无法点击"
        for pp in self.poops:
            if pp.bbox().contains(pos):
                pp.say("土地被污染，无法点击", 3.0)
                self.update()
                return
        # 3) 鸭子：按住可拖动
        if self.duck.bbox().contains(pos):
            self.duck.dragging = True
            self.drag_offset = pos - QPointF(self.duck.x, self.duck.y)

    def mouseMoveEvent(self, e):
        if not self.duck.dragging or self.drag_offset is None:
            return
        pos = self._ev_pos(e)
        a = self._roam_area()
        self.duck.x = clamp(pos.x() - self.drag_offset.x(), a.left() + 30, a.right() - 30)
        self.duck.y = clamp(pos.y() - self.drag_offset.y(), a.top() + 60, a.bottom() - 5)
        self.duck.state = "idle"
        self.duck.timer = 0.4
        self.update()

    def mouseReleaseEvent(self, e):
        self.duck.dragging = False
        self.drag_offset = None

    def mouseDoubleClickEvent(self, e):
        if self.duck.bbox().contains(self._ev_pos(e)):
            self.duck.jump()
            self.duck.say(random.choice(QUACKS), 1.4)
            self.update()

    def closeEvent(self, e):
        e.ignore()
        self.hide()


# ---------------------------------------------------------------------------
def make_icon_png(path, size=128):
    """生成应用图标（供 .desktop 使用）"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.translate(size * 0.5, size * 0.60)
    s = size / 90.0
    p.scale(s, s)
    p.setPen(QPen(C_LINE, 2.4, SOLID_LINE, ROUND_CAP, ROUND_JOIN))
    p.setBrush(QBrush(C_BODY))
    p.drawEllipse(QRectF(-34, -52, 62, 46))       # 身体（圆滚滚，无尾巴）
    p.setBrush(QBrush(C_WING))
    p.save()
    p.translate(-2, -40)
    p.rotate(-6)
    p.drawEllipse(QRectF(-18, -9, 34, 19))
    p.restore()
    p.setBrush(QBrush(C_BODY))
    p.drawEllipse(QRectF(4, -76, 36, 35))
    beak = QPainterPath()                            # 圆钝的小黄鸭喙
    beak.moveTo(32, -70)
    beak.quadTo(54, -69, 55, -62)
    beak.quadTo(53, -56, 32, -58)
    beak.closeSubpath()
    p.setBrush(QBrush(C_BEAK))
    p.drawPath(beak)
    p.setBrush(QBrush(C_EYE))
    p.drawEllipse(QRectF(23, -68, 7.5, 8.5))
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(QRectF(24.8, -67, 3.0, 3.2))
    p.setPen(NO_PEN)
    p.setBrush(QBrush(C_FOOT))
    p.drawPolygon(QPolygonF([QPointF(-8, -8), QPointF(10, -8), QPointF(2, 2)]))
    p.drawPolygon(QPolygonF([QPointF(6, -8), QPointF(24, -8), QPointF(16, 2)]))
    p.end()
    pm.save(path, "PNG")
    return path


def main():
    ap = argparse.ArgumentParser(description="摸鱼鸭 - UOS 桌面宠物")
    ap.add_argument("--layer", choices=("bottom", "desktop", "normal"),
                    default=os.environ.get("MOYUYA_LAYER", "bottom"),
                    help="窗口层级：bottom=置底(默认) / desktop=完全贴桌面 / normal=置顶(调试)")
    ap.add_argument("--size", choices=("small", "normal", "big"), default="normal")
    ap.add_argument("--speed", type=float, default=1.0, help="速度倍率 0.5~2.0")
    ap.add_argument("--click-through", action="store_true",
                    help="鼠标穿透，完全不挡操作（不可拖动）")
    ap.add_argument("--bottom-only", action="store_true",
                    help="只在桌面下半部分活动（默认整个屏幕自由溜达）")
    ap.add_argument("--off-hour", type=int, default=18,
                    help="下班时间（24 小时制，默认 18），点小鱼时会说还有多久下班")
    ap.add_argument("--start-hour", type=int, default=8,
                    help="当天开始摸鱼的时间（默认 8），用于统计摸鱼时长")
    ap.add_argument("--no-tray", action="store_true", help="不显示托盘图标")
    ap.add_argument("--make-icon", metavar="PNG", help="生成图标后退出")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)

    if args.make_icon:
        make_icon_png(args.make_icon)
        print("icon ->", args.make_icon)
        return 0

    scale = {"small": 0.7, "normal": 1.0, "big": 1.45}[args.size]
    # 默认"智能"鼠标模式：只挡住小家伙们所在的小块，桌面照常可点
    mouse_mode = "through" if args.click_through else "smart"
    pet = DesktopPet(layer=args.layer, scale=scale,
                     speed=clamp(args.speed, 0.3, 3.0),
                     mouse_mode=mouse_mode,
                     bottom_only=args.bottom_only,
                     tray=not args.no_tray,
                     off_hour=clamp(args.off_hour, 0, 23),
                     start_hour=clamp(args.start_hour, 0, 23))
    pet.show()
    if not pet.tray and args.no_tray:
        print("摸鱼鸭已启动（无托盘，按 Ctrl+C 结束）")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
