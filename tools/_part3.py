
# =============================================================================
#  第三部分：合并版界面
#     PetLayer   桌面宠物层（置底/贴桌面，鸭子平时在这层溜达）
#     MsgStage   消息联动舞台（置顶，收到消息时鱼在这层钻出、鸭子跑过来吃）
#     ChatWindow Qt 聊天主窗口
#     Controller 把通信内核 / 宠物 / 舞台 / 托盘串起来
# =============================================================================

import html as _html
import importlib as _il

_QTPKG = QT                                   # PyQt5 / PySide6 / PyQt6
_qtcore = _il.import_module(_QTPKG + ".QtCore")
_qtgui = _il.import_module(_QTPKG + ".QtGui")
_qtw = _il.import_module(_QTPKG + ".QtWidgets")

QObject = _qtcore.QObject
QEvent = _qtcore.QEvent
_Sig = getattr(_qtcore, "pyqtSignal", None) or getattr(_qtcore, "Signal")

QAction = getattr(_qtw, "QAction", None) or getattr(_qtgui, "QAction")
QMainWindow = _qtw.QMainWindow
QLabel = _qtw.QLabel
QLineEdit = _qtw.QLineEdit
QPushButton = _qtw.QPushButton
QTextEdit = _qtw.QTextEdit
QListWidget = _qtw.QListWidget
QListWidgetItem = _qtw.QListWidgetItem
QSplitter = _qtw.QSplitter
QVBoxLayout = _qtw.QVBoxLayout
QHBoxLayout = _qtw.QHBoxLayout
QDialog = _qtw.QDialog
QDialogButtonBox = _qtw.QDialogButtonBox
QCheckBox = _qtw.QCheckBox
QSpinBox = _qtw.QSpinBox
QComboBox = _qtw.QComboBox
QFileDialog = _qtw.QFileDialog
QMessageBox = _qtw.QMessageBox
QMenuBar = _qtw.QMenuBar
QFrame = _qtw.QFrame
QSizePolicy = _qtw.QSizePolicy


def _e(group, name):
    """Qt5 简写枚举 / Qt6 分组枚举 通用取值"""
    v = getattr(Qt, name, None)
    if v is not None:
        return v
    return getattr(getattr(Qt, group), name)


W_STAY_TOP = _e("WindowType", "WindowStaysOnTopHint")
W_STAY_BOTTOM = _e("WindowType", "WindowStaysOnBottomHint")
W_FRAMELESS = _e("WindowType", "FramelessWindowHint")
W_NOFOCUS = _e("WindowType", "WindowDoesNotAcceptFocus")
W_TOOL = _e("WindowType", "Tool")
W_X11BYPASS = _e("WindowType", "X11BypassWindowManagerHint")
WA_TRANSLUCENT = _e("WidgetAttribute", "WA_TranslucentBackground")
WA_MOUSE_THROUGH = _e("WidgetAttribute", "WA_TransparentForMouseEvents")
WA_NO_BG = _e("WidgetAttribute", "WA_NoSystemBackground")
KEY_RETURN = _e("Key", "Key_Return")
KEY_ENTER = _e("Key", "Key_Enter")
SHIFT_MOD = _e("KeyboardModifier", "ShiftModifier")
LEFT_BTN = _e("MouseButton", "LeftButton")
USER_ROLE = _e("ItemDataRole", "UserRole")
APP_MODAL = _e("WindowModality", "ApplicationModal")

# 界面配色（消息区）
C_SYS = "#888780"
C_ME = "#185FA5"
C_PEER = "#2C2C2A"
C_OK = "#0F6E56"
C_ERR = "#A32D2D"

APP_TITLE = "摸鱼鸭 · 欸~你听说没~"
CFG_FILE = os.path.join(os.path.expanduser("~"), ".moyuya_chat.json")

DEFAULT_CFG = {
    "nickname": "",
    "port": DEFAULT_PORT,
    "peer_port": DEFAULT_PORT,
    "last_ip": "",
    "off_hour": 18,          # 下班时间
    "off_minute": 0,
    "start_hour": 9,         # 上班时间（用于统计摸鱼时长）
    "pet": True,             # 是否显示桌面宠物
    "link": True,            # 收到消息时是否播放鸭子吃鱼
    "scale": 1.0,
    "speed": 1.0,
    "mouse_mode": "smart",
    "bottom_only": False,
    "layer": "bottom",
}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    old = os.path.join(os.path.expanduser("~"), ".eynsm.json")
    for p in (old, CFG_FILE):                 # 老配置先读，新配置覆盖
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update(data)
        except Exception:
            pass
    return cfg


def save_cfg(cfg):
    try:
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def offwork_text(hour, minute):
    """距离下班还有多久（可自定义下班时间）"""
    now = datetime.now()
    try:
        end = now.replace(hour=int(hour), minute=int(minute),
                          second=0, microsecond=0)
    except Exception:
        end = now.replace(hour=18, minute=0, second=0, microsecond=0)
    secs = int((end - now).total_seconds())
    if secs <= 0:
        return "已经下班啦！"
    h, m = divmod(secs // 60, 60)
    if h <= 0:
        return "离下班还有%d分钟" % m
    return "离下班还有%d小时%d分钟" % (h, m)


def fished_text(start_hour):
    now = datetime.now()
    start = now.replace(hour=int(start_hour), minute=0, second=0, microsecond=0)
    secs = int((now - start).total_seconds())
    if secs < 0:
        return "还没到点，先摸为敬"
    h, m = divmod(secs // 60, 60)
    if h <= 0:
        return "今天摸了%d分钟鱼了" % m
    return "今天摸了%d小时%d分钟鱼了" % (h, m)


class Bridge(QObject):
    """子线程 -> 主线程 的信号桥"""
    event = _Sig(object)
    status = _Sig(str)


# ---------------------------------------------------------------------------
# 多行气泡（消息提醒用）
# ---------------------------------------------------------------------------
def wrap_lines(text, max_w, font, max_lines=3):
    try:
        fm = QFontMetrics(font)
        measure = fm.horizontalAdvance
    except Exception:
        fm = None
        measure = lambda s: len(s) * 12
    lines = []
    cur = ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if measure(cur + ch) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    return [l for l in lines if l != ""] or ["…"]


def draw_bubble_multi(p, cx, bottom_y, lines, max_w=250.0):
    font = _bubble_font(10)
    p.setFont(font)
    fm = QFontMetrics(font)
    widths = [fm.horizontalAdvance(t) for t in lines]
    w = min(max_w, max(widths)) + 20
    lh = fm.height()
    h = lh * len(lines) + 12
    rect = QRectF(cx - w / 2.0, bottom_y - h - 4, w, h)
    path = QPainterPath()
    path.addRoundedRect(rect, 10, 10)
    p.setPen(QPen(QColor(60, 60, 60, 150), 1.2))
    p.setBrush(QColor(255, 255, 255, 242))
    p.drawPath(path)
    p.setPen(NO_PEN)
    p.setBrush(QColor(255, 255, 255, 242))
    p.drawPolygon(QPolygonF([
        QPointF(cx - 6, rect.bottom() - 1),
        QPointF(cx + 6, rect.bottom() - 1),
        QPointF(cx, rect.bottom() + 8),
    ]))
    p.setPen(QColor(45, 45, 45))
    y = rect.top() + 6
    for i, t in enumerate(lines):
        p.drawText(QRectF(rect.left(), y + i * lh, rect.width(), lh),
                   ALIGN_CENTER, t)
    return rect


# ===========================================================================
#  桌面宠物层
# ===========================================================================
class PetLayer(QWidget):
    """平时置底（或贴桌面）的那一层：鸭子在这儿溜达、钻鱼、拉屎。"""

    def __init__(self, layer="bottom", scale=1.0, speed=1.0,
                 mouse_mode="smart", bottom_only=False,
                 off_hour=18, off_minute=0, start_hour=9):
        super().__init__()
        self.layer_mode = layer
        self.scale_default = scale
        self.speed_mul = speed
        self.mouse_mode = mouse_mode
        self.bottom_only = bottom_only
        self.off_hour = int(off_hour)
        self.off_minute = int(off_minute)
        self.start_hour = int(start_hour)

        self.paused = False
        self.score = 0
        self.fish = None
        self.poops = []
        self.fx = []
        self.next_fish_in = random.uniform(3.0, 7.0)
        self.next_brag_in = random.uniform(90.0, 180.0)
        self.drag_offset = None
        self._mask_dirty = True
        self._last_mask = None
        self._mask_tick = 0
        self.duck_away = False          # True：鸭子跑到顶层舞台吃鱼去了

        self.setWindowTitle(PET_NAME)
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

        self._apply_mouse_mode()
        scr = QApplication.primaryScreen()
        if scr is not None and hasattr(scr, "availableGeometryChanged"):
            try:
                scr.availableGeometryChanged.connect(self._on_screen_change)
            except Exception:
                pass

    # -- 窗口 ---------------------------------------------------------------
    def _apply_window_flags(self):
        flags = W_FRAMELESS | W_NOFOCUS
        if self.layer_mode == "desktop":
            flags |= W_X11BYPASS | W_STAY_BOTTOM
        elif self.layer_mode == "normal":
            flags |= W_STAY_TOP
        else:
            flags |= W_STAY_BOTTOM | W_TOOL
        self.setWindowFlags(flags)
        self.setAttribute(WA_TRANSLUCENT, True)
        self.setAttribute(WA_NO_BG, True)

    def _apply_mouse_mode(self):
        if self.mouse_mode == "through":
            self.setAttribute(WA_MOUSE_THROUGH, True)
            self.clearMask()
            self._last_mask = None
        elif self.mouse_mode == "full":
            self.setAttribute(WA_MOUSE_THROUGH, False)
            self.clearMask()
            self._last_mask = None
        else:
            self.setAttribute(WA_MOUSE_THROUGH, False)
            self._mask_dirty = True
            self._refresh_hit_mask(force=True)

    def _refresh_hit_mask(self, force=False):
        if self.mouse_mode != "smart":
            return
        self._mask_tick += 1
        if not force and self._mask_tick % 2:
            return
        area = self._roam_area()
        region = QRegion()
        pad = 26
        rects = []
        if not self.duck_away:
            rects.append(self.duck.bbox())
        if self.fish is not None and self.fish.alive:
            rects.append(self.fish.bbox())
        for pp in self.poops:
            rects.append(pp.bbox())
        for r in rects:
            region = region.united(QRegion(r.adjusted(-pad, -pad, pad, pad).toRect()))
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
            self.mouse_mode = "through"
            self.setAttribute(WA_MOUSE_THROUGH, True)
            self.clearMask()

    def _bubble_mask_rects(self, area):
        out = []
        if not self.duck_away and self.duck.bubble_t > 0 and self.duck.bubble:
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
        return scr.availableGeometry() if scr is not None else self.geometry()

    def _roam_area(self):
        g = self._area()
        top = g.top() + (int(g.height() * 0.45) if self.bottom_only else 10)
        return QRectF(g.left(), top, g.width(), g.bottom() - top)

    def _on_screen_change(self, _g=None):
        self.setGeometry(self._area())
        a = self._roam_area()
        self.duck.x = clamp(self.duck.x, a.left() + 40, a.right() - 40)
        self.duck.y = clamp(self.duck.y, a.top() + 60, a.bottom() - 5)

    def _depth_at(self, y):
        a = self._roam_area()
        rel = clamp((y - a.top()) / max(1.0, a.height()), 0.0, 1.0)
        return 0.84 + 0.16 * rel

    # -- 外部控制 -----------------------------------------------------------
    def set_running(self, on):
        """关掉桌面宠物时连定时器一起停，别白占 CPU"""
        if on:
            if not self.timer.isActive():
                self.clock.restart()
                self.timer.start(int(1000.0 / FPS))
        else:
            self.timer.stop()

    def set_duck_away(self, away):
        """鸭子去顶层吃鱼 / 回到桌面"""
        if self.duck_away == bool(away):
            return
        self.duck_away = bool(away)
        if not away:
            a = self._roam_area()
            self.duck.x = clamp(self.duck.x, a.left() + 60, a.right() - 60)
            self.duck.y = clamp(self.duck.y, a.top() + 90, a.bottom() - 8)
            self.duck.state = "idle"
            self.duck.timer = 0.6
        self.update()

    def set_offwork(self, hour, minute):
        self.off_hour = int(hour)
        self.off_minute = int(minute)

    def offwork(self):
        return offwork_text(self.off_hour, self.off_minute)

    def set_scale(self, v):
        self.scale_default = v
        self.duck.scale = v
        if self.fish is not None:
            self.fish.scale = v
        self.update()

    def set_speed(self, v):
        self.speed_mul = v
        self.duck.speed_mul = v

    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.duck.say("ZZZ…", 3.0)
        self.update()

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
        if not self.duck_away and self.duck.state != "rest":
            self.duck.chase(self.fish)
        self.update()

    def drop_poop(self):
        if len(self.poops) >= Poop.MAX:
            self.poops.pop(0)
        self.poops.append(Poop(self.duck.x, self.duck.y,
                               self.duck.scale * self.duck.depth))

    # -- 主循环 -------------------------------------------------------------
    def _tick(self):
        dt = self.clock.restart() / 1000.0
        dt = clamp(dt, 0.0, 0.1)
        if self.paused:
            self.update()
            return

        area = self._roam_area()

        # 鸭子不在（去顶层吃鱼）时，桌面层暂停刷新鱼
        if not self.duck_away:
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
                elif self.duck.state not in ("run", "eat", "rest") \
                        and self.fish.state == "swim":
                    self.duck.chase(self.fish)

            self.next_brag_in -= dt
            if self.next_brag_in <= 0:
                self.next_brag_in = random.uniform(150.0, 330.0)
                if self.duck.state != "eat" and self.duck.bubble_t <= 0:
                    self.duck.say(fished_text(self.start_hour), 4.0)

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
                    if random.random() < 0.5:
                        self.drop_poop()
                    if self.score % 5 == 0:
                        self.duck.state = "rest"
                        self.duck.timer = random.uniform(6.0, 10.0)
                        self.duck.say("吃饱了，歇会儿~", 5.0)
        else:
            if self.fish is not None:
                if self.fish.update(dt) == "gone":
                    self.fish = None

        if self.poops:
            self.poops = [pp for pp in self.poops if not pp.update(dt)]
        if self.fx:
            self.fx = [f for f in self.fx if not f.update(dt)]

        self._refresh_hit_mask()
        self.update()

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, _e_):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        items = []
        for pp in self.poops:
            items.append((pp.y, "poop", pp))
        if self.fish is not None:
            items.append((self.fish.y, "fish", self.fish))
        if not self.duck_away:
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
        if not self.duck_away and self.duck.bubble_t > 0 and self.duck.bubble:
            s = self.duck.scale * self.duck.depth
            by = self.duck.y - 100 * s
            if by < area.top() + 34:
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
        return e.position() if hasattr(e, "position") else QPointF(e.pos())

    def mousePressEvent(self, e):
        if e.button() != LEFT_BTN:
            return
        pos = self._ev_pos(e)
        # 点小鱼：告诉你离下班还有多久（下班时间可自定义）
        if self.fish is not None and self.fish.alive \
                and self.fish.bbox().contains(pos):
            self.fish.say(self.offwork(), 3.5)
            self.update()
            return
        for pp in self.poops:
            if pp.bbox().contains(pos):
                pp.say("土地被污染，无法点击", 3.0)
                self.update()
                return
        if (not self.duck_away) and self.duck.bbox().contains(pos):
            self.duck.dragging = True
            self.drag_offset = pos - QPointF(self.duck.x, self.duck.y)

    def mouseMoveEvent(self, e):
        if self.duck_away or not self.duck.dragging or self.drag_offset is None:
            return
        pos = self._ev_pos(e)
        a = self._roam_area()
        self.duck.x = clamp(pos.x() - self.drag_offset.x(),
                            a.left() + 30, a.right() - 30)
        self.duck.y = clamp(pos.y() - self.drag_offset.y(),
                            a.top() + 60, a.bottom() - 5)
        self.duck.state = "idle"
        self.duck.timer = 0.4
        self.update()

    def mouseReleaseEvent(self, _e_):
        self.duck.dragging = False
        self.drag_offset = None

    def mouseDoubleClickEvent(self, e):
        if (not self.duck_away) and self.duck.bbox().contains(self._ev_pos(e)):
            self.duck.jump()
            self.duck.say(random.choice(QUACKS), 1.4)
            self.update()

    def closeEvent(self, e):
        e.ignore()
        self.hide()


# ===========================================================================
#  消息联动舞台（置顶）
# ===========================================================================
class MsgStage(QWidget):
    """收到消息时：鱼从桌面钻出来 -> 鸭子跑过来吃掉 -> 跑出屏幕。
    这一层始终置顶、完全透明、鼠标穿透，不会挡任何操作。"""

    MAX_QUEUE = 3
    TIMEOUT = 26.0          # 兜底：整场动画最长多少秒

    def __init__(self, get_scale, get_offwork=None, on_done=None):
        super().__init__()
        self.get_scale = get_scale
        self.get_offwork = get_offwork
        self.on_done = on_done
        self.setWindowTitle(PET_NAME + " 消息提醒")
        self.setWindowFlags(W_FRAMELESS | W_STAY_TOP | W_NOFOCUS | W_TOOL)
        self.setAttribute(WA_TRANSLUCENT, True)
        self.setAttribute(WA_NO_BG, True)
        self.setAttribute(WA_MOUSE_THROUGH, True)

        self.queue = []
        self.item = None
        self.fish = None
        self.duck = None
        self.fx = []
        self.phase = ""
        self.elapsed = 0.0
        self.lines = []
        self.duck_lines = []
        self.side = 1

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.clock = QElapsedTimer()
        self.hide()

    # -- 对外 ---------------------------------------------------------------
    def play(self, who, text):
        """收到一条消息：立刻演，或在当前这场演完后接着演"""
        text = (text or "").replace("\r", " ").replace("\n", " ").strip()
        if not text:
            text = "(发来了一条消息)"
        item = (who or "有人", text)
        if self.item is None:
            self._begin(item)
        elif len(self.queue) < self.MAX_QUEUE:
            self.queue.append(item)

    def stop(self):
        self.timer.stop()
        self.item = None
        self.fish = None
        self.duck = None
        self.fx = []
        self.phase = ""
        self.hide()

    # -- 演出 ---------------------------------------------------------------
    def _begin(self, item):
        scr = QApplication.primaryScreen()
        g = scr.availableGeometry() if scr is not None else self.geometry()
        self.setGeometry(g)
        self.w = max(320, g.width())
        self.h = max(240, g.height())
        self.big_area = QRectF(-560, 0, self.w + 1120, self.h)

        who, text = item
        self.item = item
        self.phase = "run"
        self.elapsed = 0.0
        self.fx = []

        scale = 1.0
        try:
            scale = float(self.get_scale() or 1.0)
        except Exception:
            scale = 1.0

        # 鱼：屏幕中下部随机位置
        fx_ = random.uniform(self.w * 0.18, self.w * 0.82)
        fy_ = random.uniform(self.h * 0.55, max(self.h * 0.6, self.h - 90))
        self.fish = Fish(fx_, fy_)
        self.fish.scale = scale
        self.fish.depth = 0.96
        self.fish.life = 60.0
        self.lines = wrap_lines("%s：%s" % (who, text), 250.0,
                                _bubble_font(10), 3)

        # 鸭子：从离鱼较远的一侧屏幕外跑进来
        self.side = -1 if fx_ > self.w * 0.5 else 1
        dx = -220.0 if self.side > 0 else self.w + 220.0
        self.duck = Duck(dx, clamp(fy_ + random.uniform(-40, 40),
                                   self.h * 0.5, self.h - 30))
        self.duck.scale = scale
        self.duck.depth = 0.96
        self.duck.speed_mul = 1.25
        self.duck.facing = self.side
        self.duck.state = "run"
        self.duck.target_x = fx_
        self.duck.target_y = fy_
        self.duck_lines = []

        for _i in range(12):
            self.fx.append(Fx("crumb", fx_ + random.uniform(-16, 16), fy_,
                              random.uniform(-80, 80), random.uniform(-160, -70),
                              life=random.uniform(0.5, 0.9)))

        self.show()
        self.raise_()
        self.clock.start()
        self.timer.start(int(1000.0 / FPS))
        self.update()

    def _finish(self):
        self.timer.stop()
        who_text = self.item
        self.item = None
        self.fish = None
        self.duck = None
        self.fx = []
        self.phase = ""
        self.hide()
        if self.on_done is not None:
            try:
                self.on_done()
            except Exception:
                pass
        if self.queue:
            nxt = self.queue.pop(0)
            QTimer.singleShot(500, lambda: self._begin(nxt))

    def _tick(self):
        dt = self.clock.restart() / 1000.0
        dt = clamp(dt, 0.0, 0.1)
        if self.duck is None or self.item is None:
            self._finish()
            return

        self.elapsed += dt
        if self.elapsed > self.TIMEOUT:
            self._finish()
            return

        fish = self.fish if (self.fish is not None and self.fish.alive) else None
        if fish is not None and self.phase == "run":
            if fish.update(dt) == "gone":
                self.fish = None
                fish = None

        events = self.duck.update(dt, self.big_area, fish)

        if self.phase == "run" and "eat_done" in events:
            # 吃掉：撒花，说句话，然后跑出屏幕
            fxp = self.fish.x if self.fish is not None else self.duck.x
            fyp = (self.fish.y + self.fish.head_y()
                   if self.fish is not None else self.duck.y)
            for _i in range(10):
                self.fx.append(Fx("star", fxp + random.uniform(-18, 18),
                                  fyp + random.uniform(-12, 12),
                                  random.uniform(-45, 45),
                                  random.uniform(-130, -40),
                                  life=random.uniform(0.6, 1.0)))
            for _i in range(4):
                self.fx.append(Fx("splash", fxp, fyp,
                                  life=random.uniform(0.4, 0.7)))
            self.fish = None
            self.phase = "out"
            self.duck.state = "run"
            self.duck.target_x = self.w + 620.0 if self.side > 0 else -620.0
            self.duck.target_y = self.duck.y
            self.duck_lines = ["嘎！消息我吃掉了~"]
            self.duck.bubble = "嘎！消息我吃掉了~"
            self.duck.bubble_t = 2.2
            self.duck.jump()

        elif self.phase == "out":
            if self.duck.x < -170 or self.duck.x > self.w + 170:
                self._finish()
                return

        if self.fx:
            self.fx = [f for f in self.fx if not f.update(dt)]
        self.update()

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, _e_):
        if self.duck is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self.fish is not None:
            draw_fish(p, self.fish)
        draw_duck(p, self.duck)
        for f in self.fx:
            draw_fx(p, f)

        # 鱼头顶的消息气泡
        if self.fish is not None:
            fy = self.fish.y + self.fish.head_y() - 44 * self.fish.scale
            fy = clamp(fy, 60.0, self.h - 120.0)
            draw_bubble_multi(p, clamp(self.fish.x, 140.0, self.w - 140.0),
                              fy, self.lines)
        # 鸭子气泡
        if self.duck.bubble_t > 0 and self.duck.bubble:
            by = self.duck.y - 100 * self.duck.scale
            by = clamp(by, 60.0, self.h - 60.0)
            draw_bubble(p, clamp(self.duck.x, 80.0, self.w - 80.0), by,
                        self.duck.bubble)
        p.end()


# ===========================================================================
#  聊天主窗口
# ===========================================================================
class ChatWindow(QMainWindow):

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.node = ctrl.node
        self.cfg = ctrl.cfg
        self.current = None
        self.msgs = {}
        self._scan_stop = None

        self.setWindowTitle("%s  v%s" % (APP_TITLE, VERSION))
        self.resize(940, 660)
        self.setMinimumSize(780, 540)
        self._build()
        self.statusBar().showMessage("就绪")
        self.sys_msg("欢迎使用 %s v%s" % (APP_TITLE, VERSION))
        self.sys_msg("使用方法：把上方「本机 IP」告诉对方，或在下方填入对方 IP "
                     "点「连接」，也可以点「扫描局域网」自动发现。")
        self.sys_msg("收到消息时，桌面小鸭子会跑去把消息变成的小鱼吃掉 —— "
                     "不想被打扰可在「摸鱼鸭」菜单里关掉。")
        self.refresh_ips()
        self.auto_start()

    # ---------------- 界面 ----------------
    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 6)
        root.setSpacing(6)

        # 顶栏
        top = QHBoxLayout()
        top.addWidget(QLabel("昵称"))
        self.nick_edit = QLineEdit(self.cfg.get("nickname", ""))
        self.nick_edit.setFixedWidth(110)
        self.nick_edit.editingFinished.connect(self.save_nick)
        top.addWidget(self.nick_edit)

        top.addWidget(QLabel("本机 IP"))
        self.ip_combo = QComboBox()
        self.ip_combo.setFixedWidth(140)
        top.addWidget(self.ip_combo)

        top.addWidget(QLabel("端口"))
        self.port_edit = QLineEdit(str(self.cfg.get("port", DEFAULT_PORT)))
        self.port_edit.setFixedWidth(62)
        top.addWidget(self.port_edit)

        self.btn_listen = QPushButton("启动监听")
        self.btn_listen.setFixedWidth(90)
        self.btn_listen.clicked.connect(self.toggle_listen)
        top.addWidget(self.btn_listen)

        self.lbl_state = QLabel("● 未启动")
        self.lbl_state.setStyleSheet("color:#A32D2D")
        top.addWidget(self.lbl_state)
        top.addStretch(1)
        root.addLayout(top)

        line = QFrame()
        line.setFrameShape(QFrame.HLine if hasattr(QFrame, "HLine")
                           else QFrame.Shape.HLine)
        line.setStyleSheet("color:#DDDDDD")
        root.addWidget(line)

        # 主体
        body = QSplitter()
        body.setOrientation(Qt.Horizontal if hasattr(Qt, "Horizontal")
                            else Qt.Orientation.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)
        lv.addWidget(QLabel("会话（单聊）"))
        self.list_peer = QListWidget()
        self.list_peer.itemSelectionChanged.connect(self.on_select)
        lv.addWidget(self.list_peer, 3)
        b1 = QPushButton("断开选中连接")
        b1.clicked.connect(self.disconnect_sel)
        lv.addWidget(b1)
        lv.addWidget(QLabel("群聊"))
        self.list_group = QListWidget()
        self.list_group.itemSelectionChanged.connect(self.on_group_select)
        lv.addWidget(self.list_group, 2)
        gb = QHBoxLayout()
        b2 = QPushButton("新建群聊")
        b2.clicked.connect(self.new_group)
        b3 = QPushButton("退出群聊")
        b3.clicked.connect(self.leave_group_sel)
        gb.addWidget(b2)
        gb.addWidget(b3)
        lv.addLayout(gb)
        left.setFixedWidth(226)
        body.addWidget(left)

        self.chat = QTextEdit()
        self.chat.setReadOnly(True)
        f = QFont()
        f.setPointSize(10)
        self.chat.setFont(f)
        body.addWidget(self.chat)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        root.addWidget(body, 1)

        # 连接行
        conn = QHBoxLayout()
        conn.addWidget(QLabel("对方 IP"))
        self.peer_ip = QLineEdit(self.cfg.get("last_ip", ""))
        self.peer_ip.setFixedWidth(150)
        conn.addWidget(self.peer_ip)
        conn.addWidget(QLabel("端口"))
        self.peer_port = QLineEdit(str(self.cfg.get("peer_port", DEFAULT_PORT)))
        self.peer_port.setFixedWidth(62)
        conn.addWidget(self.peer_port)
        b4 = QPushButton("连接")
        b4.setFixedWidth(70)
        b4.clicked.connect(self.do_connect)
        conn.addWidget(b4)
        self.btn_scan = QPushButton("扫描局域网")
        self.btn_scan.setFixedWidth(100)
        self.btn_scan.clicked.connect(self.scan_lan)
        conn.addWidget(self.btn_scan)
        conn.addWidget(QLabel("网段"))
        self.scan_net = QLineEdit("")
        self.scan_net.setFixedWidth(110)
        conn.addWidget(self.scan_net)
        self.deep_box = QCheckBox("深度")
        conn.addWidget(self.deep_box)
        conn.addWidget(QLabel("（留空自动识别；跨网段时手填如 192.168.5）"))
        conn.addStretch(1)
        root.addLayout(conn)

        # 输入行
        send = QHBoxLayout()
        self.input = QTextEdit()
        self.input.setFixedHeight(64)
        self.input.installEventFilter(self)
        send.addWidget(self.input, 1)
        sb = QVBoxLayout()
        b5 = QPushButton("发送")
        b5.setFixedWidth(84)
        b5.clicked.connect(self.send_msg)
        b6 = QPushButton("发送文件")
        b6.setFixedWidth(84)
        b6.clicked.connect(self.send_file)
        sb.addWidget(b5)
        sb.addWidget(b6)
        sb.addStretch(1)
        send.addLayout(sb)
        root.addLayout(send)

        self._build_menu()

    def _build_menu(self):
        mb = self.menuBar()
        m_chat = mb.addMenu("聊天")
        a = QAction("新建群聊", self)
        a.triggered.connect(self.new_group)
        m_chat.addAction(a)
        a = QAction("退出群聊", self)
        a.triggered.connect(self.leave_group_sel)
        m_chat.addAction(a)
        a = QAction("断开选中连接", self)
        a.triggered.connect(self.disconnect_sel)
        m_chat.addAction(a)
        m_chat.addSeparator()
        a = QAction("扫描局域网", self)
        a.triggered.connect(self.scan_lan)
        m_chat.addAction(a)

        m_pet = mb.addMenu("摸鱼鸭")
        self.act_offwork = QAction("", self)
        self.act_offwork.triggered.connect(self.edit_offwork)
        m_pet.addAction(self.act_offwork)
        a = QAction("立刻放一条鱼", self)
        a.triggered.connect(self.ctrl.spawn_fish)
        m_pet.addAction(a)
        a = QAction("数一数摸鱼值", self)
        a.triggered.connect(self.ctrl.show_score)
        m_pet.addAction(a)
        m_pet.addSeparator()

        size_menu = m_pet.addMenu("鸭子大小")
        grp = QActionGroup(self)
        for label, val in (("小", 0.7), ("中", 1.0), ("大", 1.45)):
            ac = QAction(label, self)
            ac.setCheckable(True)
            ac.setActionGroup(grp)
            ac.setChecked(abs(val - float(self.cfg.get("scale", 1.0))) < 0.01)
            ac.triggered.connect(lambda _c, v=val: self.ctrl.set_pet_scale(v))
            size_menu.addAction(ac)

        sp_menu = m_pet.addMenu("溜达速度")
        grp2 = QActionGroup(self)
        for label, val in (("慢悠悠", 0.6), ("正常", 1.0), ("闪电", 1.8)):
            ac = QAction(label, self)
            ac.setCheckable(True)
            ac.setActionGroup(grp2)
            ac.setChecked(abs(val - float(self.cfg.get("speed", 1.0))) < 0.01)
            ac.triggered.connect(lambda _c, v=val: self.ctrl.set_pet_speed(v))
            sp_menu.addAction(ac)

        m_pet.addSeparator()
        self.act_pet = QAction("显示桌面小鸭子", self)
        self.act_pet.setCheckable(True)
        self.act_pet.setChecked(bool(self.cfg.get("pet", True)))
        self.act_pet.triggered.connect(lambda _c: self.ctrl.toggle_pet())
        m_pet.addAction(self.act_pet)

        self.act_link = QAction("收到消息时播放吃鱼动画", self)
        self.act_link.setCheckable(True)
        self.act_link.setChecked(bool(self.cfg.get("link", True)))
        self.act_link.triggered.connect(lambda _c: self.ctrl.toggle_link())
        m_pet.addAction(self.act_link)

        self.act_pause = QAction("暂停溜达", self)
        self.act_pause.setCheckable(True)
        self.act_pause.triggered.connect(lambda _c: self.ctrl.toggle_pause())
        m_pet.addAction(self.act_pause)

        m_help = mb.addMenu("帮助")
        a = QAction("关于", self)
        a.triggered.connect(self.about)
        m_help.addAction(a)
        self.refresh_offwork_action()

    def refresh_offwork_action(self):
        self.act_offwork.setText("自定义下班时间（当前 %02d:%02d）…"
                                 % (int(self.cfg.get("off_hour", 18)),
                                    int(self.cfg.get("off_minute", 0))))

    # ---------------- 事件过滤（回车发送） ----------------
    def eventFilter(self, obj, ev):
        if obj is self.input and ev.type() == QEvent.KeyPress:
            if ev.key() in (KEY_RETURN, KEY_ENTER) \
                    and not (ev.modifiers() & SHIFT_MOD):
                self.send_msg()
                return True
        return super().eventFilter(obj, ev)

    # ---------------- 配置 ----------------
    def save_nick(self):
        self.cfg["nickname"] = self.nick_edit.text().strip()
        self.node.nickname = self.cfg["nickname"] or socket.gethostname()
        self.ctrl.save()

    def refresh_ips(self):
        self.ip_combo.clear()
        self.ip_combo.addItem("127.0.0.1")

        def work():
            ips = get_local_ips()
            self.ctrl.bridge.status.emit("本机 IP：" + "、".join(ips))
            QTimer.singleShot(0, lambda: self._set_ips(ips))

        threading.Thread(target=work, daemon=True).start()

    def _set_ips(self, ips):
        self.ip_combo.clear()
        for ip in ips:
            self.ip_combo.addItem(ip)
        if ips:
            self.ip_combo.setCurrentIndex(0)

    # ---------------- 监听 ----------------
    def auto_start(self):
        try:
            base = int(self.port_edit.text().strip() or DEFAULT_PORT)
        except Exception:
            base = DEFAULT_PORT
        tried = [base] + list(range(base + 1, base + 11))
        last = ""
        for port in tried:
            ok, msg = self.node.start_listening(port)
            if ok:
                self.btn_listen.setText("停止监听")
                self.lbl_state.setText("● 监听中 %d" % self.node.port)
                self.lbl_state.setStyleSheet("color:#0F6E56")
                self.port_edit.setText(str(self.node.port))
                self.cfg["port"] = self.node.port
                self.ctrl.save()
                if port != base:
                    self.sys_msg("端口 %d 被占用，已自动改用 %d，"
                                 "请告诉对方这个端口。" % (base, port))
                self.sys_msg("已启动监听，端口 %d。把本机 IP 告诉对方即可。"
                             % self.node.port)
                self.refresh_list()
                return
            last = msg
        self.lbl_state.setText("● 启动失败")
        self.lbl_state.setStyleSheet("color:#A32D2D")
        self.sys_msg("启动监听失败：%s" % last)
        self.sys_msg("可换一个端口（如 50011）后重新点「启动监听」。")

    def toggle_listen(self):
        if self.node.running:
            self.node.stop_listening()
            self.btn_listen.setText("启动监听")
            self.lbl_state.setText("● 未启动")
            self.lbl_state.setStyleSheet("color:#A32D2D")
            self.sys_msg("已停止监听")
        else:
            self.auto_start()

    # ---------------- 连接 / 扫描 ----------------
    def do_connect(self):
        ip = self.peer_ip.text().strip()
        port = self.peer_port.text().strip()
        if not ip:
            self.sys_msg("请先填写对方 IP 地址")
            return
        self.cfg["last_ip"] = ip
        try:
            self.cfg["peer_port"] = int(port)
        except Exception:
            pass
        self.ctrl.save()
        self.statusBar().showMessage("正在连接 %s:%s ..." % (ip, port))
        threading.Thread(target=self.node.connect, args=(ip, port),
                         daemon=True).start()

    def scan_lan(self):
        if not self.node.running:
            self.sys_msg("请先点「启动监听」再扫描 —— 没监听时别人也看不到你")
            return
        nets = []
        spec = self.scan_net.text().strip()
        if spec:
            nets.append(spec)
        if not nets:
            for ip, _b, _c in get_local_nets(timeout=1.5):
                if ip.startswith("127."):
                    continue
                nets.append(".".join(ip.split(".")[:3]))
        if not nets:
            self.sys_msg("没检测到局域网 IP，无法扫描；可在「网段」里手填，"
                         "例如 192.168.5")
            return
        try:
            port = int(self.peer_port.text().strip() or DEFAULT_PORT)
        except Exception:
            port = DEFAULT_PORT
        deep = self.deep_box.isChecked()
        my_port = self.node.port or DEFAULT_PORT
        before = len(self.node.peer_list())
        self.btn_scan.setEnabled(False)
        self.btn_scan.setText("扫描中…")
        self.statusBar().showMessage("正在广播寻找同网段电脑 …")
        self._scan_stop = threading.Event()
        stop = self._scan_stop

        def work():
            seen = {}
            try:
                for h in self.node.discover(timeout=2.0, extra_nets=nets):
                    ip = h.get("ip")
                    ok = (not stop.is_set()) and self.node.connect(
                        ip, h.get("port") or my_port, timeout=3, quiet=True)
                    seen[ip] = {"ok": ok, "name": h.get("name", ""),
                                "via": "广播"}
            except Exception:
                pass
            if not seen and not stop.is_set():
                ports = [my_port]
                rng = range(DEFAULT_PORT, DEFAULT_PORT + PORT_SPAN) if deep \
                    else range(DEFAULT_PORT, DEFAULT_PORT + 4)
                for p in rng:
                    if p not in ports:
                        ports.append(p)
                if port not in ports:
                    ports.append(port)
                targets = []
                for n in nets:
                    for t in expand_targets(n):
                        if t not in targets:
                            targets.append(t)
                self.ctrl.bridge.status.emit(
                    "广播无应答，改为逐个尝试 %d 个地址 …" % len(targets))

                def prog(done, total):
                    self.ctrl.bridge.status.emit("逐个尝试中 %d/%d" % (done, total))

                try:
                    for hit in tcp_scan(self.node, targets, ports,
                                        progress=prog, stop=stop):
                        seen[hit["ip"]] = {"ok": True, "name": "", "via": "扫描"}
                except Exception:
                    pass
            QTimer.singleShot(0, lambda: self.scan_done(seen, nets, before, deep))

        threading.Thread(target=work, daemon=True).start()

    def scan_done(self, seen, nets, before, deep):
        try:
            self.btn_scan.setEnabled(True)
            self.btn_scan.setText("扫描局域网")
        except Exception:
            pass
        ok_ips = [ip for ip, v in seen.items() if v["ok"]]
        bad_ips = [ip for ip, v in seen.items() if not v["ok"]]
        if ok_ips:
            self.sys_msg("扫描完成，已连上 %d 台：%s"
                         % (len(ok_ips), "、".join(sorted(ok_ips))))
        elif bad_ips:
            self.sys_msg("看到了 %s，但连不上 —— 多半是对方防火墙拦了 %d 端口，"
                         "请在对方电脑放行后再试。"
                         % ("、".join(sorted(bad_ips)), self.node.port))
        else:
            hint = ["双方都要先点「启动监听」",
                    "确认在同一网段（本机网段：%s）" % "、".join(nets),
                    "对方防火墙放行 %d 端口" % (self.node.port or DEFAULT_PORT)]
            if not deep:
                hint.append("可勾选「深度」再扫一次（会尝试 %d~%d 全部端口）"
                            % (DEFAULT_PORT, DEFAULT_PORT + PORT_SPAN - 1))
            self.sys_msg("没找到其它电脑。请检查：" + "；".join(hint))
        self.statusBar().showMessage("就绪")
        self.refresh_list()

    def disconnect_sel(self):
        if self.current and not is_group_key(self.current):
            self.node.disconnect(self.current)
            self.sys_msg("已断开连接")

    # ---------------- 会话列表 ----------------
    def on_select(self):
        items = self.list_peer.selectedItems()
        if not items:
            return
        key = items[0].data(USER_ROLE)
        if key:
            self.switch_to(key)
            self.list_group.clearSelection()

    def on_group_select(self):
        items = self.list_group.selectedItems()
        if not items:
            return
        key = items[0].data(USER_ROLE)
        if key:
            self.switch_to(key)
            self.list_peer.clearSelection()

    def switch_to(self, key):
        self.current = key
        self.render_chat()

    def refresh_list(self):
        keys = []
        self.list_peer.blockSignals(True)
        self.list_peer.clear()
        for u, title, name in self.node.peer_list():
            it = QListWidgetItem("%s  %s" % (title, ("(%s)" % name) if name else ""))
            it.setData(USER_ROLE, u)
            self.list_peer.addItem(it)
            keys.append(u)

        self.list_group.blockSignals(True)
        self.list_group.clear()
        for gid, gname, cnt, online in self.node.group_list():
            it = QListWidgetItem("%s  (%d人·%d在线)" % (gname, cnt, online))
            it.setData(USER_ROLE, gkey(gid))
            self.list_group.addItem(it)
            keys.append(gkey(gid))
        self.list_peer.blockSignals(False)
        self.list_group.blockSignals(False)

        if self.current not in keys:
            self.current = keys[0] if keys else None
            self.render_chat()
        self._sync_selection()

    def _sync_selection(self):
        self.list_peer.blockSignals(True)
        self.list_group.blockSignals(True)
        self.list_peer.clearSelection()
        self.list_group.clearSelection()
        if self.current:
            target = self.list_peer if not is_group_key(self.current) \
                else self.list_group
            for i in range(target.count()):
                if target.item(i).data(USER_ROLE) == self.current:
                    target.setCurrentRow(i)
                    break
        self.list_peer.blockSignals(False)
        self.list_group.blockSignals(False)

    # ---------------- 群聊 ----------------
    def new_group(self):
        peers = self.node.peer_list()
        if not peers:
            QMessageBox.information(self, "新建群聊",
                                    "还没有连接任何电脑。\n"
                                    "先连接对方，或点「扫描局域网」找人。")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("新建群聊")
        dlg.resize(340, 400)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("群名称"))
        name_edit = QLineEdit("群聊")
        v.addWidget(name_edit)
        v.addWidget(QLabel("选择成员（Ctrl / Shift 可多选）"))
        lb = QListWidget()
        lb.setSelectionMode(QListWidget.MultiSelection
                            if hasattr(QListWidget, "MultiSelection")
                            else QListWidget.SelectionMode.MultiSelection)
        for u, title, name in peers:
            it = QListWidgetItem("%s  %s" % (title, "(%s)" % name if name else ""))
            it.setData(USER_ROLE, u)
            lb.addItem(it)
        v.addWidget(lb, 1)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel
                               if hasattr(QDialogButtonBox, "Ok")
                               else QDialogButtonBox.StandardButton.Ok |
                               QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        v.addWidget(box)

        if dlg.exec() != QDialog.Accepted:
            return
        sel = [it.data(USER_ROLE) for it in lb.selectedItems()]
        if not sel:
            QMessageBox.information(self, "新建群聊", "请至少选择一位成员")
            return
        gid = self.node.create_group(name_edit.text().strip() or "群聊", sel)
        key = gkey(gid)
        self.msgs.setdefault(key, [])
        self.refresh_list()
        self.switch_to(key)

    def leave_group_sel(self):
        key = self.current
        if not (key and is_group_key(key)):
            QMessageBox.information(self, "退出群聊",
                                    "请先在左侧「群聊」列表里选中一个群")
            return
        gid = gid_of(key)
        name = self.node.groups.get(gid, {}).get("name", "")
        yes = QMessageBox.question(
            self, "退出群聊", "确定退出群聊「%s」吗？" % name,
            QMessageBox.Yes | QMessageBox.No
            if hasattr(QMessageBox, "Yes")
            else QMessageBox.StandardButton.Yes |
            QMessageBox.StandardButton.No) == QMessageBox.Yes
        if not yes:
            return
        self.node.leave_group(gid)
        self.msgs.pop(key, None)
        self.current = None
        self.refresh_list()

    # ---------------- 消息区 ----------------
    def render_chat(self):
        rows = self.msgs.get(self.current or "__sys__", [])
        self.chat.setHtml("".join(self._row_html(k, t, tm)
                                  for k, t, tm in rows))
        self.chat.moveCursor(_qtgui.QTextCursor.End
                             if hasattr(_qtgui, "QTextCursor")
                             else self.chat.textCursor().End)
        self.chat.ensureCursorVisible()

    @staticmethod
    def _row_html(kind, text, tm):
        esc = _html.escape(text).replace("\n", "<br>")
        head = '<span style="color:%s">[%s]</span> ' % (C_SYS, _html.escape(tm))
        if kind == "sys":
            return head + '<span style="color:%s">%s</span><br>' % (C_SYS, esc)
        if kind == "me":
            return head + '<span style="color:%s">我：</span>' % C_ME + \
                '<span style="color:%s">%s</span><br>' % (C_PEER, esc)
        if kind == "peer":
            return head + '<span style="color:%s">对方：</span>' % C_PEER + \
                '<span style="color:%s">%s</span><br>' % (C_PEER, esc)
        if kind == "gme":
            return head + '<span style="color:%s">%s</span><br>' % (C_ME, esc)
        if kind == "gpeer":
            return head + '<span style="color:%s">%s</span><br>' % (C_PEER, esc)
        if kind == "ok":
            return head + '<span style="color:%s">%s</span><br>' % (C_OK, esc)
        if kind == "err":
            return head + '<span style="color:%s">%s</span><br>' % (C_ERR, esc)
        return head + esc + "<br>"

    def append_msg(self, kind, text, key=None):
        tm = now_str()
        key = key or self.current or "__sys__"
        self.msgs.setdefault(key, []).append((kind, text, tm))
        if key == (self.current or "__sys__"):
            self.chat.moveCursor(_qtgui.QTextCursor.End)
            self.chat.insertHtml(self._row_html(kind, text, tm))
            self.chat.ensureCursorVisible()

    def sys_msg(self, text):
        self.append_msg("sys", text)

    def set_status(self, text):
        self.statusBar().showMessage(text)

    # ---------------- 发送 ----------------
    def send_msg(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        if not self.current:
            self.sys_msg("还没有建立连接，请先在下方输入对方 IP 并点“连接”")
            return
        key = self.current
        if is_group_key(key):
            gid = gid_of(key)
            if gid not in self.node.groups:
                self.sys_msg("这个群聊已不存在")
                return
            self.input.clear()
            self.append_msg("gme", "%s：%s" % (self.node.nickname, text), key=key)
            threading.Thread(target=self.node.send_group_text,
                             args=(gid, text), daemon=True).start()
            return
        if not self.node.get_peer(key):
            self.sys_msg("连接已断开")
            return
        self.input.clear()
        self.append_msg("me", text, key=key)
        threading.Thread(target=self.node.send_text,
                         args=(key, text), daemon=True).start()

    def send_file(self):
        if not self.current:
            self.sys_msg("还没有建立连接，请先连接对方")
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择要发送的文件")
        if not path:
            return
        key = self.current
        name = os.path.basename(path)
        if is_group_key(key):
            gid = gid_of(key)
            self.append_msg("sys", "正在向群成员逐个发送：%s" % name, key=key)

            def run_g():
                n = self.node.send_group_file(gid, path)
                self.ctrl.bridge.status.emit("就绪")
                QTimer.singleShot(0, lambda: self.append_msg(
                    "ok" if n else "err",
                    ("群文件已发给 %d 位成员：%s" % (n, name)) if n
                    else "没有可发送的群成员（成员都不在线）", key=key))
            threading.Thread(target=run_g, daemon=True).start()
            return
        self.append_msg("sys", "正在等待对方接收文件：%s" % name, key=key)

        def prog(got, total):
            self.ctrl.bridge.status.emit(
                "发送 %s：%s / %s（%.0f%%）"
                % (name, human_size(got), human_size(total),
                   got * 100.0 / max(total, 1)))

        def run():
            self.node.send_file(key, path, on_progress=prog)
            self.ctrl.bridge.status.emit("就绪")
        threading.Thread(target=run, daemon=True).start()

    # ---------------- 下班时间 ----------------
    def edit_offwork(self):
        self.ctrl.edit_offwork(self)

    def about(self):
        QMessageBox.information(
            self, "关于",
            "%s  v%s\n\n"
            "摸鱼鸭 + 局域网点对点聊天，合并成一个软件。\n"
            "收到消息时，桌面小鸭子会跑到屏幕最上层，把消息变成的小鱼吃掉。\n\n"
            "点桌面上冒出来的小鱼，它会告诉你离下班还有多久；\n"
            "下班时间可以在「摸鱼鸭」菜单里自定义。\n\n"
            "免注册、免服务器，知道对方 IP 就能聊。"
            % (APP_TITLE, VERSION))

    # ---------------- 事件 ----------------
    def handle_event(self, ev):
        t = ev.get("type")
        if t == "peer_connected":
            self.msgs.setdefault(ev["uuid"], [])
            self.refresh_list()
            self.append_msg("sys", "已与 %s 建立连接" % ev.get("title"),
                            key=ev["uuid"])
            self.statusBar().showMessage("已连接 %s" % ev.get("title"))
        elif t == "peer_disconnected":
            self.append_msg("sys", "连接已断开：%s" % ev.get("title"),
                            key=ev["uuid"])
            self.refresh_list()
        elif t == "message":
            self.append_msg("peer", ev.get("text", ""), key=ev["uuid"])
            self.statusBar().showMessage("收到新消息")
        elif t == "file_offer":
            self.ask_receive(ev)
        elif t == "file_waiting":
            self.statusBar().showMessage("等待对方接收：%s" % ev.get("name"))
        elif t == "file_progress":
            got, total = ev.get("got", 0), ev.get("total", 1)
            self.statusBar().showMessage(
                "接收 %s：%s / %s（%.0f%%）"
                % (ev.get("name"), human_size(got), human_size(total),
                   got * 100.0 / max(total, 1)))
        elif t == "file_result":
            self.append_msg("ok" if ev.get("ok") else "err",
                            ev.get("msg", ""), key=ev["uuid"])
            self.statusBar().showMessage("就绪")
        elif t == "error":
            self.append_msg("err", ev.get("msg", ""),
                            key=self.current or "__sys__")
            self.statusBar().showMessage("就绪")
        elif t == "group_invited":
            key = gkey(ev["group_id"])
            self.msgs.setdefault(key, [])
            self.refresh_list()
            self.append_msg("sys", "%s 把你拉进群聊「%s」（%d 人）"
                            % (ev.get("by_name") or "对方", ev.get("name"),
                               ev.get("count", 0)), key=key)
        elif t == "group_created":
            key = gkey(ev["group_id"])
            self.msgs.setdefault(key, [])
            self.refresh_list()
            self.append_msg("sys", "已创建群聊「%s」（%d 人）"
                            % (ev.get("name"), ev.get("count", 0)), key=key)
        elif t == "group_message":
            key = gkey(ev["group_id"])
            if key not in self.msgs:
                self.msgs[key] = []
                self.refresh_list()
            who = ev.get("from_name") or "有人"
            self.append_msg("gme" if ev.get("mine") else "gpeer",
                            "%s：%s" % (who, ev.get("text", "")), key=key)
            if not ev.get("mine"):
                self.statusBar().showMessage("群聊新消息：%s" % who)
        elif t == "group_member":
            self.refresh_list()
            if ev.get("left"):
                self.append_msg("sys", "%s 退出了群聊"
                                % (ev.get("left_name") or "有人"),
                                key=gkey(ev["group_id"]))
            elif ev.get("added"):
                self.append_msg("sys", "新增 %d 位成员，现在共 %d 人"
                                % (len(ev["added"]), ev.get("count", 0)),
                                key=gkey(ev["group_id"]))
        elif t == "group_left":
            self.refresh_list()

    def ask_receive(self, ev):
        dft = os.path.join(os.path.expanduser("~"), "下载")
        if not os.path.isdir(dft):
            dft = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.isdir(dft):
            dft = os.path.expanduser("~")
        name = ev.get("name")
        size = human_size(ev.get("size", 0))
        yes = QMessageBox.question(
            self, "收到文件",
            "对方（%s）向你发送文件：\n\n%s\n大小：%s\n\n是否接收？"
            % (ev.get("title"), name, size),
            QMessageBox.Yes | QMessageBox.No
            if hasattr(QMessageBox, "Yes")
            else QMessageBox.StandardButton.Yes |
            QMessageBox.StandardButton.No) == QMessageBox.Yes
        if not yes:
            self.node.reject_file(ev["uuid"], ev["file_id"])
            self.append_msg("err", "已拒绝接收 %s" % name, key=ev["uuid"])
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存到",
                                              os.path.join(dft, name or ""))
        if not path:
            self.node.reject_file(ev["uuid"], ev["file_id"])
            self.append_msg("err", "已取消接收 %s" % name, key=ev["uuid"])
            return
        self.append_msg("sys", "开始接收：%s" % name, key=ev["uuid"])
        self.node.accept_file(ev["uuid"], ev["file_id"], path)

    def closeEvent(self, e):
        if self.ctrl.tray is not None:
            e.ignore()
            self.hide()
            return
        self.ctrl.quit()


# ===========================================================================
#  下班时间设置对话框
# ===========================================================================
class OffworkDialog(QDialog):

    def __init__(self, parent, hour, minute, start_hour):
        super().__init__(parent)
        self.setWindowTitle("自定义下班时间")
        self.setModal(True)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("下班时间（24 小时制）"))
        row = QHBoxLayout()
        self.hour = QSpinBox()
        self.hour.setRange(0, 23)
        self.hour.setValue(int(hour))
        self.minute = QSpinBox()
        self.minute.setRange(0, 59)
        self.minute.setSingleStep(5)
        self.minute.setValue(int(minute))
        row.addWidget(self.hour)
        row.addWidget(QLabel(":"))
        row.addWidget(self.minute)
        row.addStretch(1)
        v.addLayout(row)
        v.addWidget(QLabel("上班时间（只用于统计今天摸了多久鱼）"))
        row2 = QHBoxLayout()
        self.start = QSpinBox()
        self.start.setRange(0, 23)
        self.start.setValue(int(start_hour))
        row2.addWidget(self.start)
        row2.addWidget(QLabel("点"))
        row2.addStretch(1)
        v.addLayout(row2)
        self.tip = QLabel("")
        self.tip.setStyleSheet("color:#185FA5")
        v.addWidget(self.tip)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel
                               if hasattr(QDialogButtonBox, "Ok")
                               else QDialogButtonBox.StandardButton.Ok |
                               QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        v.addWidget(box)
        self.hour.valueChanged.connect(self.refresh_tip)
        self.minute.valueChanged.connect(self.refresh_tip)
        self.refresh_tip()

    def refresh_tip(self):
        self.tip.setText("现在点小鱼，它会说：%s"
                         % offwork_text(self.hour.value(), self.minute.value()))


# ===========================================================================
#  控制器：把内核 / 宠物 / 舞台 / 托盘串起来
# ===========================================================================
class Controller(object):

    def __init__(self, cfg, pet=True, link=True, tray=True, demo=False):
        self.cfg = cfg
        self.bridge = Bridge()
        self.bridge.event.connect(self.handle_event)
        self.bridge.status.connect(self._on_status)
        self.node = ChatNode(on_event=self.on_node_event,
                             nickname=cfg.get("nickname", ""))
        self.tray = None
        self.pet = None

        self.pet = PetLayer(
            layer=cfg.get("layer", "bottom"),
            scale=float(cfg.get("scale", 1.0)),
            speed=float(cfg.get("speed", 1.0)),
            mouse_mode=cfg.get("mouse_mode", "smart"),
            bottom_only=bool(cfg.get("bottom_only", False)),
            off_hour=cfg.get("off_hour", 18),
            off_minute=cfg.get("off_minute", 0),
            start_hour=cfg.get("start_hour", 9))
        self.pet.setVisible(bool(pet))
        self.pet.set_running(bool(pet))
        self.pet_visible = bool(pet)

        self.stage = MsgStage(get_scale=lambda: float(self.cfg.get("scale", 1.0)),
                              get_offwork=self.pet.offwork,
                              on_done=self._stage_done)
        self.link_enabled = bool(link)

        self.win = ChatWindow(self)
        self._build_tray(tray)
        self.win.show()

        if demo:
            QTimer.singleShot(2500, lambda: self.notify(
                "摸鱼鸭", "这是一条测试消息，看看小鸭子会不会跑过来吃掉它"))

    # -- 通信内核回调（子线程） --------------------------------------------
    def on_node_event(self, ev):
        self.bridge.event.emit(ev)

    def _on_status(self, text):
        self.win.set_status(text)

    def handle_event(self, ev):
        try:
            self.win.handle_event(ev)
        except Exception:
            pass
        t = ev.get("type")
        if not self.link_enabled:
            return
        if t == "message":
            peer = None
            try:
                peer = self.node.get_peer(ev.get("uuid"))
            except Exception:
                peer = None
            who = ""
            if peer is not None:
                who = getattr(peer, "name", "") or ""
            if not who:
                who = (ev.get("title") or "").split(":")[0] or "有人"
            self.notify(who, ev.get("text", ""))
        elif t == "group_message" and not ev.get("mine"):
            name = ""
            try:
                name = self.node.groups.get(ev.get("group_id"), {}).get("name", "")
            except Exception:
                name = ""
            self.notify("群·%s" % (ev.get("from_name") or name or "有人"),
                        ev.get("text", ""))

    def notify(self, who, text):
        """收到消息 -> 顶层弹出小鱼，鸭子跑过来吃"""
        if not self.link_enabled or self.stage is None:
            return
        if self.pet is not None and self.pet_visible:
            self.pet.set_duck_away(True)
        self.stage.play(who, text)

    def _stage_done(self):
        if self.pet is not None:
            self.pet.set_duck_away(False)

    # -- 宠物控制 ----------------------------------------------------------
    def spawn_fish(self):
        if self.pet is not None and self.pet_visible:
            self.pet.spawn_fish()

    def show_score(self):
        if self.pet is not None:
            self.pet.duck.say("摸鱼值 %d" % self.pet.score, 2.2)
            self.pet.update()

    def set_pet_scale(self, v):
        self.cfg["scale"] = v
        if self.pet is not None:
            self.pet.set_scale(v)
        self.save()

    def set_pet_speed(self, v):
        self.cfg["speed"] = v
        if self.pet is not None:
            self.pet.set_speed(v)
        self.save()

    def toggle_pause(self):
        if self.pet is not None:
            self.pet.toggle_pause()
            state = self.pet.paused
            if getattr(self, "act_pause", None) is not None:
                self.act_pause.setChecked(state)

    def toggle_pet(self):
        self.pet_visible = not self.pet_visible
        self.cfg["pet"] = self.pet_visible
        if self.pet is not None:
            self.pet.setVisible(self.pet_visible)
            self.pet.set_running(self.pet_visible)
        if getattr(self, "act_pet", None) is not None:
            self.act_pet.setChecked(self.pet_visible)
        self.save()

    def toggle_link(self):
        self.link_enabled = not self.link_enabled
        self.cfg["link"] = self.link_enabled
        if getattr(self, "act_link", None) is not None:
            self.act_link.setChecked(self.link_enabled)
        self.save()
        if not self.link_enabled and self.stage is not None:
            self.stage.stop()
            self._stage_done()

    def toggle_bottom(self):
        if self.pet is not None:
            self.pet.toggle_bottom()
            self.cfg["bottom_only"] = self.pet.bottom_only
            self.save()

    def edit_offwork(self, parent=None):
        dlg = OffworkDialog(parent or self.win,
                            self.cfg.get("off_hour", 18),
                            self.cfg.get("off_minute", 0),
                            self.cfg.get("start_hour", 9))
        if dlg.exec() != QDialog.Accepted:
            return
        self.cfg["off_hour"] = dlg.hour.value()
        self.cfg["off_minute"] = dlg.minute.value()
        self.cfg["start_hour"] = dlg.start.value()
        if self.pet is not None:
            self.pet.set_offwork(self.cfg["off_hour"], self.cfg["off_minute"])
            self.pet.start_hour = self.cfg["start_hour"]
        self.save()
        if hasattr(self.win, "refresh_offwork_action"):
            self.win.refresh_offwork_action()
        if self.tray is not None and getattr(self, "act_tray_off", None):
            self.act_tray_off.setText(
                "下班时间 %02d:%02d（点击修改…）"
                % (self.cfg["off_hour"], self.cfg["off_minute"]))
        self.win.sys_msg("下班时间已设为 %02d:%02d —— 现在点桌面上的小鱼试试"
                         % (self.cfg["off_hour"], self.cfg["off_minute"]))

    def save(self):
        save_cfg(self.cfg)

    # -- 托盘 --------------------------------------------------------------
    def _build_tray(self, tray):
        if not tray or not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        self.tray = QSystemTrayIcon(make_tray_icon(), self.win)
        self.tray.setToolTip(APP_TITLE)
        menu = QMenu()

        a = QAction("显示聊天窗口", self.win)
        a.triggered.connect(self.show_window)
        menu.addAction(a)
        menu.addSeparator()

        self.act_tray_off = QAction(
            "下班时间 %02d:%02d（点击修改…）"
            % (int(self.cfg.get("off_hour", 18)),
               int(self.cfg.get("off_minute", 0))), self.win)
        self.act_tray_off.triggered.connect(self.edit_offwork)
        menu.addAction(self.act_tray_off)

        self.act_tray_link = QAction("收到消息时播放吃鱼动画", self.win)
        self.act_tray_link.setCheckable(True)
        self.act_tray_link.setChecked(self.link_enabled)
        self.act_tray_link.triggered.connect(lambda _c: self.toggle_link())
        menu.addAction(self.act_tray_link)

        self.act_tray_pet = QAction("显示桌面小鸭子", self.win)
        self.act_tray_pet.setCheckable(True)
        self.act_tray_pet.setChecked(self.pet_visible)
        self.act_tray_pet.triggered.connect(lambda _c: self.toggle_pet())
        menu.addAction(self.act_tray_pet)

        self.act_pause = QAction("暂停溜达", self.win)
        self.act_pause.setCheckable(True)
        self.act_pause.triggered.connect(lambda _c: self.toggle_pause())
        menu.addAction(self.act_pause)
        menu.addSeparator()

        size_menu = menu.addMenu("鸭子大小")
        grp = QActionGroup(self.win)
        for label, val in (("小", 0.7), ("中", 1.0), ("大", 1.45)):
            ac = QAction(label, self.win)
            ac.setCheckable(True)
            ac.setActionGroup(grp)
            ac.setChecked(abs(val - float(self.cfg.get("scale", 1.0))) < 0.01)
            ac.triggered.connect(lambda _c, v=val: self.set_pet_scale(v))
            size_menu.addAction(ac)

        sp_menu = menu.addMenu("溜达速度")
        grp2 = QActionGroup(self.win)
        for label, val in (("慢悠悠", 0.6), ("正常", 1.0), ("闪电", 1.8)):
            ac = QAction(label, self.win)
            ac.setCheckable(True)
            ac.setActionGroup(grp2)
            ac.setChecked(abs(val - float(self.cfg.get("speed", 1.0))) < 0.01)
            ac.triggered.connect(lambda _c, v=val: self.set_pet_speed(v))
            sp_menu.addAction(ac)

        mouse_menu = menu.addMenu("鼠标模式")
        grp3 = QActionGroup(self.win)
        for label, val, tip in (
                ("智能（只挡住小家伙）", "smart",
                 "只有鸭子/小鱼/鸭屎这几个小块会挡鼠标，其余照常点桌面"),
                ("完全穿透", "through",
                 "任何位置都不挡操作，但点不到鸭子和小鱼"),
                ("整屏互动", "full",
                 "可拖动鸭子，但整屏都会挡住下面的图标")):
            ac = QAction(label, self.win)
            ac.setCheckable(True)
            ac.setActionGroup(grp3)
            ac.setChecked(self.cfg.get("mouse_mode", "smart") == val)
            ac.setToolTip(tip)
            ac.triggered.connect(lambda _c, v=val: self.set_mouse_mode(v))
            mouse_menu.addAction(ac)

        self.act_bottom = QAction("只在桌面下半部分活动", self.win)
        self.act_bottom.setCheckable(True)
        self.act_bottom.setChecked(bool(self.cfg.get("bottom_only", False)))
        self.act_bottom.triggered.connect(lambda _c: self.toggle_bottom())
        menu.addAction(self.act_bottom)
        menu.addSeparator()

        menu.addAction("立刻放一条鱼", self.spawn_fish)
        menu.addAction("数一数摸鱼值", self.show_score)
        menu.addSeparator()
        menu.addAction("退出", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        try:
            trig = QSystemTrayIcon.Trigger
            dbl = QSystemTrayIcon.DoubleClick
        except AttributeError:
            trig = QSystemTrayIcon.ActivationReason.Trigger
            dbl = QSystemTrayIcon.ActivationReason.DoubleClick
        if reason in (trig, dbl):
            self.show_window()

    def show_window(self):
        self.win.show()
        self.win.raise_()
        self.win.activateWindow()

    def set_mouse_mode(self, mode):
        self.cfg["mouse_mode"] = mode
        if self.pet is not None:
            self.pet.set_mouse_mode(mode)
        self.save()

    def quit(self):
        try:
            if self.stage is not None:
                self.stage.stop()
        except Exception:
            pass
        try:
            if self.pet is not None:
                self.pet.timer.stop()
        except Exception:
            pass
        try:
            self.node.stop()
        except Exception:
            pass
        try:
            self.save()
        except Exception:
            pass
        if self.tray is not None:
            self.tray.hide()
        QApplication.quit()


# ===========================================================================
#  图标
# ===========================================================================
def make_icon_png(path, size=128):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.translate(size * 0.5, size * 0.60)
    s = size / 90.0
    p.scale(s, s)
    p.setPen(QPen(C_LINE, 2.4, SOLID_LINE, ROUND_CAP, ROUND_JOIN))
    p.setBrush(QBrush(C_BODY))
    p.drawEllipse(QRectF(-34, -52, 62, 46))
    p.setBrush(QBrush(C_WING))
    p.save()
    p.translate(-2, -40)
    p.rotate(-6)
    p.drawEllipse(QRectF(-18, -9, 34, 19))
    p.restore()
    p.setBrush(QBrush(C_BODY))
    p.drawEllipse(QRectF(4, -76, 36, 35))
    beak = QPainterPath()
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
    # 右下角画一条小鱼，暗示"聊天 + 摸鱼"
    p.setBrush(QBrush(C_FISH))
    p.drawEllipse(QRectF(-30, 4, 30, 17))
    p.setBrush(QBrush(C_FISH_DK))
    p.drawPolygon(QPolygonF([QPointF(-28, 12), QPointF(-40, 5),
                             QPointF(-40, 20)]))
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(QRectF(-13, 8, 6, 6))
    p.setBrush(QBrush(C_EYE))
    p.drawEllipse(QRectF(-11.5, 10, 3, 3.4))
    p.end()
    pm.save(path, "PNG")
    return path


# ===========================================================================
#  入口
# ===========================================================================
def main():
    ap = argparse.ArgumentParser(
        description="%s —— 桌面宠物 + 局域网点对点聊天（合并版）" % APP_TITLE)
    ap.add_argument("--layer", choices=("bottom", "desktop", "normal"),
                    default=None,
                    help="鸭子所在层级：bottom=置底(默认) / desktop=贴桌面 / normal=置顶")
    ap.add_argument("--size", choices=("small", "normal", "big"), default=None)
    ap.add_argument("--speed", type=float, default=None)
    ap.add_argument("--click-through", action="store_true",
                    help="鼠标穿透，完全不挡操作（不可拖动鸭子）")
    ap.add_argument("--bottom-only", action="store_true",
                    help="鸭子只在桌面下半部分活动")
    ap.add_argument("--off-hour", type=int, default=None, help="下班时间（小时）")
    ap.add_argument("--off-minute", type=int, default=None, help="下班时间（分钟）")
    ap.add_argument("--start-hour", type=int, default=None, help="上班时间（小时）")
    ap.add_argument("--p2p-port", type=int, default=None,
                    help="聊天监听端口（默认 %d，被占用自动顺延）" % DEFAULT_PORT)
    ap.add_argument("--no-pet", action="store_true", help="不显示桌面小鸭子")
    ap.add_argument("--no-link", action="store_true",
                    help="收到消息时不播放鸭子吃鱼动画")
    ap.add_argument("--no-tray", action="store_true", help="不显示托盘图标")
    ap.add_argument("--demo", action="store_true",
                    help="启动 2 秒后自动演示一次「收到消息 -> 鸭子吃鱼」")
    ap.add_argument("--make-icon", metavar="PNG", help="生成图标后退出")
    ap.add_argument("--selftest", action="store_true", help="网络层自检")
    ap.add_argument("--diag", action="store_true", help="环境诊断")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.diag:
        return diag()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(make_tray_icon())

    if args.make_icon:
        make_icon_png(args.make_icon)
        print("icon ->", args.make_icon)
        return 0

    cfg = load_cfg()
    if args.layer:
        cfg["layer"] = args.layer
    if args.size:
        cfg["scale"] = {"small": 0.7, "normal": 1.0, "big": 1.45}[args.size]
    if args.speed is not None:
        cfg["speed"] = clamp(args.speed, 0.3, 3.0)
    if args.click_through:
        cfg["mouse_mode"] = "through"
    if args.bottom_only:
        cfg["bottom_only"] = True
    if args.off_hour is not None:
        cfg["off_hour"] = clamp(args.off_hour, 0, 23)
    if args.off_minute is not None:
        cfg["off_minute"] = clamp(args.off_minute, 0, 59)
    if args.start_hour is not None:
        cfg["start_hour"] = clamp(args.start_hour, 0, 23)
    if args.p2p_port:
        cfg["port"] = int(args.p2p_port)
    if args.no_pet:
        cfg["pet"] = False
    if args.no_link:
        cfg["link"] = False

    ctrl = Controller(cfg, pet=bool(cfg.get("pet", True)),
                      link=bool(cfg.get("link", True)),
                      tray=not args.no_tray, demo=args.demo)
    if ctrl.tray is None:
        print("%s 已启动（无系统托盘，关闭窗口即退出）" % APP_TITLE)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
