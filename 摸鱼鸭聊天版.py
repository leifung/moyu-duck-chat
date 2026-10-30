#!/usr/bin/env python3
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

import json
import os
import queue
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime

APP_NAME = "欸~你听说没~"
VERSION = "3.0"
DEFAULT_PORT = 50010
PORT_SPAN = 11                  # 端口顺延范围：50010 ~ 50020
CHUNK_SIZE = 64 * 1024          # 文件分块大小
HELLO_TIMEOUT = 10              # 握手超时（秒）
ACCEPT_TIMEOUT = 120            # 等对方同意接收文件的超时（秒）
LEN = struct.Struct("!I")       # 4 字节大端长度前缀

# 局域网发现（UDP 广播）------------------------------------------------------
DISC_MAGIC = "FXL-DISC1"        # 询问：谁在线？
HERE_MAGIC = "FXL-HERE1"        # 应答：我在这
DISC_PORT_BASE = 50010          # 广播收发端口，与 TCP 端口互不冲突
DISC_PORT_SPAN = 11

# 帧类型 -------------------------------------------------------------------
T_HELLO = "hello"
T_DUP = "dup"
T_TEXT = "text"
T_OFFER = "file_offer"
T_ACCEPT = "file_accept"
T_REJECT = "file_reject"
T_DATA = "file_data"
T_CANCEL = "file_cancel"

# 群聊帧 -------------------------------------------------------------------
T_GINVITE = "group_invite"      # 邀请入群 / 同步成员表
T_GTEXT = "group_text"          # 群消息（可一跳转发）
T_GLEAVE = "group_leave"        # 退群

GSEEN_TTL = 180                 # 群消息去重记录保留时长（秒）
GSEEN_MAX = 3000                # 去重表上限，超出后清理过期项
GROUP_TTL = 2                   # 群消息转发跳数：2 跳足够覆盖小群


def now_str():
    return datetime.now().strftime("%H:%M:%S")


def human_size(num):
    """字节数转易读字符串"""
    try:
        num = float(num)
    except Exception:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024.0 or unit == "TB":
            if unit == "B":
                return "%d B" % int(num)
            return "%.1f %s" % (num, unit)
        num /= 1024.0
    return "%.1f TB" % num


GKEY_PREFIX = "g:"


def gkey(gid):
    """群聊的会话 key"""
    return GKEY_PREFIX + str(gid)


def gid_of(key):
    return str(key)[len(GKEY_PREFIX):]


def is_group_key(key):
    return str(key or "").startswith(GKEY_PREFIX)


def pick_font(size=10):
    """挑一个系统里真实存在的中文字体，避免界面出现方块"""
    try:
        import tkinter.font as tkfont
        fams = set(tkfont.families())
    except Exception:
        return ("Sans", size)
    for f in ("Noto Sans CJK SC", "Source Han Sans SC", "Noto Sans SC",
              "WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "Noto Sans CJK TC",
              "Microsoft YaHei", "SimHei", "Heiti SC", "Sans"):
        if f in fams:
            return (f, size)
    return ("Sans", size)


def _mask_to_bits(mask):
    """子网掩码 -> 前缀长度"""
    try:
        return bin(struct.unpack("!I", socket.inet_aton(mask))[0]).count("1")
    except Exception:
        return 24


def _calc_bcast(ip, bits):
    """由 IP + 前缀长度推算广播地址"""
    try:
        import ipaddress
        return str(ipaddress.IPv4Network("%s/%d" % (ip, bits),
                                         strict=False).broadcast_address)
    except Exception:
        try:
            num = struct.unpack("!I", socket.inet_aton(ip))[0]
            m = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF
            return socket.inet_ntoa(struct.pack("!I", num | (~m & 0xFFFFFFFF)))
        except Exception:
            return ".".join(ip.split(".")[:3]) + ".255"


def _run_cmd(cmd, timeout=2.0):
    """跑命令取输出。PATH 补上 /sbin、/usr/sbin：
    统信 UOS / 麒麟 上 ip、ifconfig 常常不在普通用户的 PATH 里。"""
    env = os.environ.copy()
    env["PATH"] = (env.get("PATH", "") + os.pathsep
                   + "/sbin:/usr/sbin:/bin:/usr/bin")
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL,
                           timeout=timeout, env=env)
        return (p.stdout or b"").decode("utf-8", "ignore")
    except Exception:
        return ""


def _nics_linux_ioctl():
    """Linux：/proc/net/dev 列接口名 + ioctl 取 IP / 掩码 / 广播。
    纯标准库，不依赖 ip 命令、不需要 root，离线环境照样能用。"""
    try:
        import fcntl
    except Exception:
        return []
    SIOCGIFFLAGS, SIOCGIFADDR = 0x8913, 0x8915
    SIOCGIFBRDADDR, SIOCGIFNETMASK = 0x8919, 0x891B
    names = []
    try:
        with open("/proc/net/dev", "r") as f:
            for ln in f.read().splitlines()[2:]:
                if ":" in ln:
                    n = ln.split(":")[0].strip()
                    if n:
                        names.append(n)
    except Exception:
        return []
    # 虚拟网卡（docker/虚拟机/VPN）优先让位给物理网卡
    VIRT = ("docker", "virbr", "veth", "br-", "tun", "tap", "vmnet",
            "vboxnet", "lxcbr", "kube", "cni", "flannel", "zt",
            "tailscale", "utun", "ppp")
    all_nic, phys = [], []
    for name in names:
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            ifn = name.encode("utf-8")[:15]
            pack = struct.pack("256s", ifn)
            flags = struct.unpack(
                "H", fcntl.ioctl(s.fileno(), SIOCGIFFLAGS, pack)[16:18])[0]
            if not (flags & 0x1) or (flags & 0x8):      # 未启用 / 回环
                continue
            ip = socket.inet_ntoa(
                fcntl.ioctl(s.fileno(), SIOCGIFADDR, pack)[20:24])
            mask = socket.inet_ntoa(
                fcntl.ioctl(s.fileno(), SIOCGIFNETMASK, pack)[20:24])
            try:
                bcast = socket.inet_ntoa(
                    fcntl.ioctl(s.fileno(), SIOCGIFBRDADDR, pack)[20:24])
            except Exception:
                bcast = "0.0.0.0"
            if not ip or ip.startswith(("127.", "0.", "169.254.")):
                continue
            bits = _mask_to_bits(mask)
            if not bcast or bcast.startswith(("0.", "127.")):
                bcast = _calc_bcast(ip, bits)
            rec = (ip, bits, bcast, name, "ioctl")
            all_nic.append(rec)
            if not name.startswith(VIRT):
                phys.append(rec)
        except Exception:
            continue
        finally:
            try:
                if s:
                    s.close()
            except Exception:
                pass
    return phys if phys else all_nic


def _nics_ip_cmd():
    """解析 `ip -4 -o addr show`"""
    out = []
    txt = _run_cmd(["ip", "-4", "-o", "addr", "show"])
    for line in txt.splitlines():
        m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
        if not m:
            continue
        ip, bits = m.group(1), int(m.group(2))
        bm = re.search(r"brd\s+(\d+\.\d+\.\d+\.\d+)", line)
        nm = re.search(r"^\d+:\s+(\S+)", line)
        out.append((ip, bits,
                    (bm.group(1) if bm else "") or _calc_bcast(ip, bits),
                    (nm.group(1) if nm else ""), "ip"))
    return out


def _nics_ifconfig():
    """解析 ifconfig -a（macOS / 老 Linux）"""
    out = []
    txt = _run_cmd(["ifconfig", "-a"])
    cur, ip, mask, bcast = "", "", "", ""

    def flush():
        if ip:
            bits = _mask_to_bits(mask) if mask else 24
            out.append((ip, bits, bcast or _calc_bcast(ip, bits),
                        cur, "ifconfig"))

    for line in txt.splitlines():
        if line and not line[0].isspace():
            flush()
            cur, ip, mask, bcast = line.split(":")[0].strip(), "", "", ""
        m = re.search(r"inet\s+(?:addr:)?(\d+\.\d+\.\d+\.\d+)", line)
        if m:
            ip = m.group(1)
        m = re.search(r"(?:netmask|Mask:)\s*(\d+\.\d+\.\d+\.\d+)", line)
        if m:
            mask = m.group(1)
        m = re.search(r"(?:broadcast|Bcast:)\s*(\d+\.\d+\.\d+\.\d+)", line)
        if m:
            bcast = m.group(1)
    flush()
    return out


def _nics_ipconfig():
    """Windows：解析 ipconfig 的 IPv4 地址 + 子网掩码"""
    raw = b""
    try:
        p = subprocess.run(["ipconfig"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=5)
        raw = p.stdout or b""
    except Exception:
        return []
    txt = ""
    for enc in ("utf-8", "gbk", "mbcs", "latin-1"):
        try:
            t = raw.decode(enc)
            if "IPv4" in t or "IP Address" in t or "地址" in t:
                txt = t
                break
        except Exception:
            continue
    if not txt:
        return []
    out, ip = [], ""
    for line in txt.splitlines():
        if ("IPv4" in line) or ("IP Address" in line):
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)", line.split(":")[-1])
            ip = m.group(1) if m else ""
        elif ("子网掩码" in line) or ("Subnet Mask" in line):
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)", line.split(":")[-1])
            if m and ip:
                bits = _mask_to_bits(m.group(1))
                out.append((ip, bits, _calc_bcast(ip, bits), "", "ipconfig"))
                ip = ""
    return out


def _nics_probe():
    """UDP 探测出口网卡（不真发包，只查路由表）；需要默认路由，
    所以离线/内网机器上可能拿不到，只作兜底。"""
    for target in ("223.5.5.5", "10.255.255.255", "192.168.255.255"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1)
            s.connect((target, 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith(("127.", "0.", "169.254.")):
                return [(ip, 24, _calc_bcast(ip, 24), "", "probe")]
        except Exception:
            continue
    return []


def _nics_hostname():
    """最后兜底：主机名解析（Debian 系常把主机名指向 127.0.1.1，会被过滤）"""
    try:
        _n, _a, ips = socket.gethostbyname_ex(socket.gethostname())
    except Exception:
        return []
    return [(ip, 24, _calc_bcast(ip, 24), "", "hostname")
            for ip in ips
            if not ip.startswith(("127.", "0.", "169.254."))]


def _collect_nets():
    """按可靠程度依次尝试，返回 [(ip, bits, bcast, ifname, method)]
    （已排除回环 / 链路本地地址，局域网网段排在前面）。"""
    found = []
    for fn in (_nics_linux_ioctl, _nics_ip_cmd, _nics_ifconfig,
               _nics_ipconfig, _nics_probe, _nics_hostname):
        try:
            got = [r for r in fn()
                   if r and r[0]
                   and not r[0].startswith(("127.", "0.", "169.254."))]
        except Exception:
            got = []
        for r in got:
            if r[0] not in [x[0] for x in found]:
                found.append(r)
        if found:
            break

    def rank(r):
        ip = r[0]
        try:
            a, b = (int(x) for x in ip.split(".")[:2])
        except Exception:
            return (9, ip)
        if ip.startswith("192.168."):
            k = 0
        elif ip.startswith("10."):
            k = 1
        elif a == 172 and 16 <= b <= 31:
            k = 2
        else:
            k = 3
        return (k, ip)

    found.sort(key=rank)
    return found


def get_local_ips(timeout=3.0):
    """本机可用 IPv4（局域网优先）。与扫描网段取自同一份结果，
    不会出现「显示一个 IP、却扫另一个网段」的情况。"""
    box = []

    def work():
        box.extend([n[0] for n in _collect_nets()])

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    return box if box else ["127.0.0.1"]


def get_local_nets(timeout=3.0):
    """[(ip, prefix, broadcast), ...]：广播发现与网段扫描都依赖它，
    只有拿到正确的广播地址，才能找到同网段的人。"""
    box = []

    def work():
        for ip, bits, bcast, _n, _m in _collect_nets():
            box.append((ip, bits, bcast or _calc_bcast(ip, bits)))

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    box.append(("127.0.0.1", 8, "127.0.0.1"))
    return box if box else [("127.0.0.1", 8, "127.0.0.1")]




# ---------------------------------------------------------------------------
#  运行日志 / 诊断报告
#  出问题不用猜：程序一直在记日志，菜单里一键导出报告发给开发即可
# ---------------------------------------------------------------------------
LOG_PATH = os.path.join(os.path.expanduser("~"), ".moyuya_chat.log")
LOG_MAX_BYTES = 512 * 1024        # 超过就滚动裁剪，避免无限增长
LOG_KEEP_BYTES = 256 * 1024
_log_lock = threading.Lock()


def _roll_log():
    """日志太大时保留后半段"""
    try:
        if os.path.isfile(LOG_PATH) \
                and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            with open(LOG_PATH, "rb") as f:
                f.seek(-LOG_KEEP_BYTES, 2)
                data = f.read()
            with open(LOG_PATH, "wb") as f:
                f.write(b"...(earlier log omitted)...\n"
                        + data.split(b"\n", 1)[-1])
    except Exception:
        pass


def log_line(msg, exc=False):
    """记一行运行日志。exc=True 时把当前异常堆栈一并写入。"""
    try:
        import traceback
        with _log_lock:
            _roll_log()
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write("[%s] %s\n" % (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
                if exc:
                    f.write(traceback.format_exc())
    except Exception:
        pass


def install_log_hooks():
    """把没人接住的异常也写进日志（主线程 + 子线程）。"""
    import traceback
    base_hook = sys.__excepthook__

    def hook(etype, value, tb):
        try:
            with _log_lock:
                _roll_log()
                with open(LOG_PATH, "a", encoding="utf-8") as f:
                    f.write("[%s] 未捕获异常：%s: %s\n%s"
                            % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                               getattr(etype, "__name__", etype), value,
                               "".join(traceback.format_exception(
                                   etype, value, tb))))
        except Exception:
            pass
        try:
            base_hook(etype, value, tb)
        except Exception:
            pass

    sys.excepthook = hook

    def thread_hook(a):
        try:
            with _log_lock:
                _roll_log()
                with open(LOG_PATH, "a", encoding="utf-8") as f:
                    f.write("[%s] 线程 %s 未捕获异常：%s: %s\n%s"
                            % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                               getattr(getattr(a, "thread", None),
                                       "name", "?"),
                               getattr(a.exc_type, "__name__", a.exc_type),
                               a.exc_value,
                               "".join(traceback.format_exception(
                                   a.exc_type, a.exc_value,
                                   a.exc_traceback))))
        except Exception:
            pass

    try:
        threading.excepthook = thread_hook
    except Exception:
        pass


def build_report(extra=None, tail_lines=400):
    """生成诊断报告文本：环境 + 网卡 + 端口 + 运行状态 + 最近日志。"""
    import platform
    line = []
    add = line.append
    add("=" * 66)
    add(" 摸鱼鸭 · 欸~你听说没~   诊断报告")
    add(" 生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    add("=" * 66)
    add("")
    add("【环境】")
    add("  系统      : %s" % platform.platform())
    add("  Python    : %s" % sys.version.replace("\n", " "))
    qt = globals().get("QT", "?")
    add("  界面库    : %s" % qt)
    try:
        if qt == "PyQt5":
            from PyQt5.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
            add("  Qt / PyQt : %s / %s" % (QT_VERSION_STR, PYQT_VERSION_STR))
        elif qt == "PySide6":
            from PySide6.QtCore import __version__ as _v
            add("  PySide6   : %s" % _v)
    except Exception:
        pass
    add("  程序位置  : %s" % os.path.abspath(sys.argv[0] if sys.argv else ""))
    add("  家目录    : %s" % os.path.expanduser("~"))

    add("")
    add("【本机网卡】")
    try:
        nics = _collect_nets()
        if nics:
            for ip, bits, bcast, name, method in nics:
                add("  %-15s /%-2d  广播 %-15s %-9s [%s]"
                    % (ip, bits, bcast, name or "-", method))
        else:
            add("  没识别到任何网卡 —— 界面会显示 127.0.0.1，也扫不到人")
    except Exception as e:
        add("  获取失败：%r" % (e,))

    add("")
    add("【端口】")
    for port in (DEFAULT_PORT, DEFAULT_PORT + 1):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", port))
            s.close()
            udp = "UDP 可绑定"
        except Exception as e:
            udp = "UDP 不可用（%s）" % e
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", port))
            s.listen(1)
            s.close()
            tcp = "TCP 可监听"
        except Exception as e:
            tcp = "TCP 不可用（%s）" % e
        add("  %d : %s，%s" % (port, tcp, udp))

    if extra:
        add("")
        add("【运行状态】")
        for ln in extra:
            add("  " + str(ln))

    add("")
    add("【最近日志 · %s】" % LOG_PATH)
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        if lines:
            for ln in lines[-tail_lines:]:
                add("  " + ln)
        else:
            add("  （日志为空）")
    except Exception as e:
        add("  （读不到日志：%r）" % (e,))

    add("")
    add("=" * 66)
    add("把以上内容发给开发即可。若含敏感信息，请先自行删改。")
    add("=" * 66)
    return "\n".join(line)


def save_report(extra=None, path=None):
    """把报告写成文本文件，返回路径。默认放在家目录。"""
    txt = build_report(extra)
    if not path:
        path = os.path.join(
            os.path.expanduser("~"),
            "摸鱼鸭诊断报告-%s.txt"
            % datetime.now().strftime("%Y%m%d-%H%M%S"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)
    return path


def report_cli():
    """命令行：python3 摸鱼鸭聊天版.py --report"""
    p = save_report()
    print("诊断报告已生成：")
    print("  " + p)
    print("把这个文件发给开发即可（里面不含聊天内容，只含环境、网卡、端口、日志）。")
    return 0




def expand_targets(spec, limit=1024):
    """把网段写法展开成 IP 列表。
    支持：192.168.1        -> 192.168.1.1 ~ .254
          192.168.1.0/24   -> 同上
          192.168.1.5      -> 单个地址
          192.168.1.10-60  -> 区间
    """
    spec = (spec or "").strip()
    if not spec:
        return []
    ips = []
    m = re.match(r"^(\d+\.\d+\.\d+)\.(\d+)\s*-\s*(\d+)$", spec)
    if m:
        base, a, b = m.group(1), int(m.group(2)), int(m.group(3))
        for i in range(max(1, a), min(254, b) + 1):
            ips.append("%s.%d" % (base, i))
        return ips[:limit]
    if "/" in spec:
        try:
            import ipaddress
            net = ipaddress.IPv4Network(spec, strict=False)
            if net.prefixlen < 8:
                return []
            for h in net.hosts():
                ips.append(str(h))
                if len(ips) >= limit:
                    break
            return ips
        except Exception:
            return []
    if re.match(r"^\d+\.\d+\.\d+$", spec):          # 192.168.1
        return ["%s.%d" % (spec, i) for i in range(1, 255)]
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", spec):     # 单个 IP
        return [spec]
    return []


def tcp_scan(node, targets, ports, workers=100, conn_timeout=1.0,
             hello_timeout=1.5, progress=None, stop=None):
    """对「IP × 端口」逐个尝试真实握手，返回命中的设备列表。
    直接握手而不是连一下就断开：既能在命中时立刻建立连接，
    也不会在对方机器上留下大量半开连接。
    """
    jobs = queue.Queue()
    for ip in targets:
        for p in ports:
            jobs.put((ip, p))
    total = jobs.qsize()
    hits = []
    lock = threading.Lock()
    done = [0]

    def worker():
        while True:
            if stop is not None and stop.is_set():
                return
            try:
                ip, p = jobs.get_nowait()
            except queue.Empty:
                return
            try:
                ok = node.connect(ip, p, timeout=conn_timeout, quiet=True,
                                  hello_timeout=hello_timeout)
            except Exception:
                ok = False
            if ok:
                with lock:
                    hits.append({"ip": ip, "port": p})
            with lock:
                done[0] += 1
                if progress and (done[0] % 10 == 0 or done[0] == total):
                    try:
                        progress(done[0], total)
                    except Exception:
                        pass

    n = max(1, min(workers, total))
    ths = [threading.Thread(target=worker, daemon=True) for _ in range(n)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return hits


# =============================================================================
#  底层帧收发
# =============================================================================
def send_frame(sock, header, body=b"", stream=None, total=0, progress=None):
    """发送一帧：4 字节头长 + JSON 头 + 数据体
    body   : 已准备好的完整字节（小数据）
    stream : 文件对象（大数据，边读边发）
    total  : stream 需要发送的字节数
    """
    raw = json.dumps(header, ensure_ascii=False).encode("utf-8")
    sock.sendall(LEN.pack(len(raw)) + raw)
    if body:
        sock.sendall(body)
    if stream is not None and total > 0:
        left = total
        sent = 0
        last_tick = 0.0
        while left > 0:
            n = min(CHUNK_SIZE, left)
            data = stream.read(n)
            if not data:
                raise IOError("文件读取中断，可能已被移动或删除")
            sock.sendall(data)
            left -= len(data)
            sent += len(data)
            if progress:
                t = time.time()
                if t - last_tick > 0.15 or left == 0:
                    last_tick = t
                    progress(sent, total)
        if progress:
            progress(total, total)


def recv_exact(sock, n):
    """精确接收 n 字节，连接关闭返回 None"""
    buf = bytearray()
    while len(buf) < n:
        try:
            part = sock.recv(min(65536, n - len(buf)))
        except socket.timeout:
            return None
        except OSError:
            return None
        if not part:
            return None
        buf.extend(part)
    return bytes(buf)


def recv_header(sock):
    raw = recv_exact(sock, LEN.size)
    if raw is None:
        return None
    (hlen,) = LEN.unpack(raw)
    if hlen <= 0 or hlen > 8 * 1024 * 1024:
        return None
    raw = recv_exact(sock, hlen)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def recv_body(sock, size, fp=None, progress=None):
    """接收 size 字节；fp 不为 None 时边收边写文件"""
    got = 0
    last_tick = 0.0
    while got < size:
        n = min(CHUNK_SIZE, size - got)
        data = recv_exact(sock, n)
        if data is None:
            return False
        if fp:
            fp.write(data)
        got += len(data)
        if progress:
            t = time.time()
            if t - last_tick > 0.15 or got == size:
                last_tick = t
                progress(got, size)
    return True


# =============================================================================
#  节点：同时具备服务端与客户端能力
# =============================================================================
class Peer(object):
    def __init__(self, sock, addr, peer_uuid, peer_ip, peer_port, outgoing):
        self.sock = sock
        self.addr = addr
        self.uuid = peer_uuid
        self.ip = peer_ip
        self.port = peer_port
        self.outgoing = outgoing
        self.name = ""
        self.send_lock = threading.Lock()

    @property
    def title(self):
        return "%s:%s" % (self.ip, self.port)


class ChatNode(object):
    """点对点通信核心。通过 on_event(dict) 回调向上层（UI）汇报事件。"""

    def __init__(self, on_event, nickname=None, auto_accept_dir=None):
        self.on_event = on_event or (lambda e: None)
        self.uuid = str(uuid.uuid4())
        self.nickname = nickname or socket.gethostname()
        self.port = DEFAULT_PORT
        self.auto_accept_dir = auto_accept_dir   # 自检/自动接收用
        self.peers = {}                          # uuid -> Peer
        self.peers_lock = threading.Lock()
        self.server_sock = None
        self.running = False
        self._accept_events = {}                 # file_id -> threading.Event
        self._accept_result = {}                 # file_id -> save_path or None
        self._stop_event = threading.Event()
        self._hs_lock = threading.Lock()         # 握手与注册串行化
        # 局域网广播发现
        self._disc_sock = None
        self._disc_port = 0
        self._disc_running = False
        self._disc_hits = {}                     # uuid -> info
        self._disc_lock = threading.Lock()
        # 群聊
        self.groups = {}                         # group_id -> dict
        self.groups_lock = threading.Lock()
        self._gseen = {}                         # msg_id -> 时间戳（防重复/回环）
        self._gseen_lock = threading.Lock()

    # ---------------- 事件 ----------------
    def emit(self, etype, **kw):
        kw["type"] = etype
        try:
            self.on_event(kw)
        except Exception:
            pass

    # ---------------- 监听 ----------------
    def start_listening(self, port=DEFAULT_PORT):
        if self.running:
            return True, "已在监听"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", port))
            s.listen(16)
            s.settimeout(0.5)
        except Exception as e:
            log_line("监听端口 %s 失败：%s" % (port, e))
            return False, "监听端口 %s 失败：%s" % (port, e)
        self.server_sock = s
        self.port = s.getsockname()[1]
        self.running = True
        self._stop_event.clear()
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        self.start_discovery()
        log_line("开始监听 TCP %d，UDP 发现端口 %s"
                 % (self.port, getattr(self, "_disc_port", "?")))
        return True, "监听端口 %d" % self.port

    # ---------------- 局域网广播发现 ----------------
    def start_discovery(self):
        """绑定一个 UDP 端口常驻监听广播询问。
        UDP 与 TCP 端口号互不冲突，所以可以复用 50010；
        同机多开时会自动往后顺延，询问方会遍历整个端口段，仍能收到。"""
        if self._disc_sock:
            return True
        sock = None
        for p in range(DISC_PORT_BASE, DISC_PORT_BASE + DISC_PORT_SPAN):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                except Exception:
                    pass
                s.bind(("", p))
                s.settimeout(0.5)
                sock = s
                break
            except Exception:
                try:
                    s.close()
                except Exception:
                    pass
                continue
        if not sock:
            return False
        self._disc_sock = sock
        self._disc_port = sock.getsockname()[1]
        self._disc_running = True
        threading.Thread(target=self._disc_loop, daemon=True).start()
        return True

    def stop_discovery(self):
        self._disc_running = False
        if self._disc_sock:
            try:
                self._disc_sock.close()
            except Exception:
                pass
            self._disc_sock = None

    def _disc_loop(self):
        while self._disc_running and self._disc_sock:
            try:
                data, addr = self._disc_sock.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                # Windows 上未连接 UDP socket 可能抛出 WSAECONNRESET，
                # 那是收到 ICMP 不可达导致的，并不代表 socket 坏了：
                # 只有 socket 真被关掉才退出，否则继续收。
                s = self._disc_sock
                if (not self._disc_running) or s is None:
                    break
                try:
                    if s.fileno() == -1:
                        break
                except Exception:
                    break
                continue
            try:
                msg = json.loads(data.decode("utf-8", "ignore"))
            except Exception:
                continue
            kind = msg.get("m")
            who = msg.get("uuid", "")
            if not who or who == self.uuid:
                continue
            if kind == DISC_MAGIC:
                # 有人找同伴，单播回复自己的地址
                reply = json.dumps({"m": HERE_MAGIC, "uuid": self.uuid,
                                    "name": self.nickname,
                                    "port": self.port}).encode("utf-8")
                try:
                    self._disc_sock.sendto(reply, (addr[0], addr[1]))
                except Exception:
                    pass
            elif kind == HERE_MAGIC:
                info = {"uuid": who, "ip": addr[0],
                        "port": int(msg.get("port") or DEFAULT_PORT),
                        "name": msg.get("name", "")}
                with self._disc_lock:
                    self._disc_hits[who] = info
                self.emit("discovered", **info)

    def discover(self, timeout=2.0, extra_nets=None):
        """发一轮广播询问，收集应答。
        返回 [{"ip","port","name","uuid"}, ...]（不含自己）。"""
        if not self._disc_sock and not self.start_discovery():
            return []
        with self._disc_lock:
            self._disc_hits = {}
        payload = json.dumps({"m": DISC_MAGIC, "uuid": self.uuid,
                              "name": self.nickname,
                              "port": self.port}).encode("utf-8")
        addrs = set()
        for _ip, _bits, bcast in get_local_nets(timeout=1.5):
            if bcast and bcast != "127.0.0.1":
                addrs.add(bcast)
        for spec in (extra_nets or []):
            for b in self._bcasts_of(spec):
                addrs.add(b)
        addrs.add("255.255.255.255")
        # 广播容易丢，发两轮（隔 0.3 秒），显著提高一次扫到的概率
        for _round in range(2):
            for b in list(addrs):
                for p in range(DISC_PORT_BASE, DISC_PORT_BASE + DISC_PORT_SPAN):
                    try:
                        self._disc_sock.sendto(payload, (b, p))
                    except Exception:
                        pass
            # 同机多开时广播可能不通，补一轮本机单播，保证自己人一定能互相看见
            for p in range(DISC_PORT_BASE, DISC_PORT_BASE + DISC_PORT_SPAN):
                try:
                    self._disc_sock.sendto(payload, ("127.0.0.1", p))
                except Exception:
                    pass
            if _round == 0:
                time.sleep(0.3)
        deadline = time.time() + max(0.3, timeout)
        while time.time() < deadline:
            time.sleep(0.1)
        with self._disc_lock:
            hits = list(self._disc_hits.values())
        log_line("广播发现：向 %d 个广播地址发了询问（%s），收到 %d 个应答"
                 % (len(addrs), "、".join(sorted(addrs)) or "无", len(hits)))
        return hits

    @staticmethod
    def _bcasts_of(spec):
        out = []
        spec = (spec or "").strip()
        try:
            import ipaddress
            if "/" in spec:
                net = ipaddress.IPv4Network(spec, strict=False)
                out.append(str(net.broadcast_address))
            elif re.match(r"^\d+\.\d+\.\d+$", spec):
                out.append(spec + ".255")
            elif re.match(r"^\d+\.\d+\.\d+\.\d+$", spec):
                out.append(spec)
        except Exception:
            pass
        return out

    def stop_listening(self):
        self.running = False
        self._stop_event.set()
        self.stop_discovery()
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
            self.server_sock = None

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            except AttributeError:
                break
            threading.Thread(target=self._handshake_in, args=(conn, addr),
                             daemon=True).start()

    # ---------------- 主动连接 ----------------
    def connect(self, ip, port, timeout=8, quiet=False, hello_timeout=None):
        ip = (ip or "").strip()

        def fail(msg):
            log_line("连接 %s:%s 失败：%s" % (ip, port, msg))
            if not quiet:
                self.emit("error", msg=msg)
            return False

        try:
            port = int(port)
        except Exception:
            return fail("端口必须是数字")
        if not ip:
            return fail("请填写对方 IP 地址")
        try:
            sock = socket.create_connection((ip, port), timeout=timeout)
        except Exception as e:
            return fail("连接 %s:%s 失败：%s" % (ip, port, e))
        try:
            sock.settimeout(hello_timeout or HELLO_TIMEOUT)
            send_frame(sock, {"t": T_HELLO, "uuid": self.uuid,
                              "name": self.nickname, "port": self.port})
            hdr = recv_header(sock)
            if not hdr:
                sock.close()
                return fail("对方无响应（握手超时）")
            if hdr.get("t") == T_DUP:
                sock.close()
                return True
            if hdr.get("t") != T_HELLO:
                sock.close()
                return fail("对方协议不匹配")
            peer_uuid = hdr.get("uuid", "")
            if peer_uuid == self.uuid:
                sock.close()
                return fail("不能连接自己")
            sock.settimeout(None)
            with self._hs_lock:
                keep = self._decide_locked(peer_uuid, True)
                if not keep:
                    try:
                        send_frame(sock, {"t": T_DUP})
                    except Exception:
                        pass
                else:
                    peer = Peer(sock, (ip, port), peer_uuid, ip,
                                hdr.get("port", port), True)
                    peer.name = hdr.get("name", "")
                    self._register(peer)
                    log_line("已连接 %s:%s（对方昵称：%s）"
                             % (ip, port, peer.name or "-"))
            if not keep:
                sock.close()
                return True
            return True
        except Exception as e:
            try:
                sock.close()
            except Exception:
                pass
            return fail("连接出错：%s" % e)

    def _handshake_in(self, sock, addr):
        sock.settimeout(HELLO_TIMEOUT)
        hdr = recv_header(sock)
        if not hdr or hdr.get("t") != T_HELLO:
            try:
                sock.close()
            except Exception:
                pass
            return
        peer_uuid = hdr.get("uuid", "")
        if peer_uuid == self.uuid:
            try:
                sock.close()
            except Exception:
                pass
            return
        try:
            sock.settimeout(None)
        except Exception:
            pass
        with self._hs_lock:
            keep = self._decide_locked(peer_uuid, False)
            if not keep:
                try:
                    send_frame(sock, {"t": T_DUP})
                except Exception:
                    pass
            else:
                try:
                    send_frame(sock, {"t": T_HELLO, "uuid": self.uuid,
                                      "name": self.nickname,
                                      "port": self.port})
                except Exception:
                    keep = False
                if keep:
                    peer = Peer(sock, addr, peer_uuid, addr[0],
                                hdr.get("port", addr[1]), False)
                    peer.name = hdr.get("name", "")
                    self._register(peer)
        if not keep:
            try:
                sock.close()
            except Exception:
                pass

    # ---------------- 连接管理 ----------------
    def _decide_locked(self, peer_uuid, is_outgoing):
        """握手去重：仅在已存在同一对端连接时生效。
        双方同时发起时会建立两条连接，约定保留"发起方 uuid 较大"的那条，
        两端判断结果一致，从而只保留一条可用连接。
        必须在 self._hs_lock 内调用。"""
        existing = self.peers.get(peer_uuid)
        if existing is None:
            return True
        new_init = self.uuid if is_outgoing else peer_uuid
        old_init = self.uuid if existing.outgoing else peer_uuid
        if new_init == old_init:
            return False
        if new_init > old_init:
            self._unregister(existing)
            return True
        return False

    def _register(self, peer):
        with self.peers_lock:
            self.peers[peer.uuid] = peer
        self.emit("peer_connected", uuid=peer.uuid, title=peer.title,
                  name=peer.name)
        threading.Thread(target=self._recv_loop, args=(peer,), daemon=True).start()

    def _unregister(self, peer):
        remove = False
        with self.peers_lock:
            if self.peers.get(peer.uuid) is peer:
                del self.peers[peer.uuid]
                remove = True
        try:
            peer.sock.close()
        except Exception:
            pass
        if remove:
            self.emit("peer_disconnected", uuid=peer.uuid, title=peer.title)

    def get_peer(self, key):
        with self.peers_lock:
            return self.peers.get(key)

    def peer_list(self):
        with self.peers_lock:
            return [(p.uuid, p.title, p.name) for p in self.peers.values()]

    def disconnect(self, key):
        p = self.get_peer(key)
        if p:
            self._unregister(p)

    def stop(self):
        self.stop_listening()
        with self.peers_lock:
            peers = list(self.peers.values())
        for p in peers:
            self._unregister(p)

    # ---------------- 接收循环 ----------------
    def _recv_loop(self, peer):
        sock = peer.sock
        try:
            while self.running or True:
                hdr = recv_header(sock)
                if not hdr:
                    break
                t = hdr.get("t")
                if t == T_TEXT:
                    size = hdr.get("body_size", 0)
                    ok, body = self._recv_small(sock, size)
                    if not ok:
                        break
                    self.emit("message", uuid=peer.uuid, title=peer.title,
                              text=body.decode("utf-8", "replace"),
                              time=hdr.get("time", now_str()))
                elif t == T_OFFER:
                    self._on_offer(peer, hdr)
                elif t == T_ACCEPT:
                    ev = self._accept_events.get(hdr.get("file_id"))
                    self._accept_result[hdr.get("file_id")] = True
                    if ev:
                        ev.set()
                elif t == T_REJECT:
                    fid = hdr.get("file_id")
                    self._accept_result[fid] = False
                    ev = self._accept_events.get(fid)
                    if ev:
                        ev.set()
                    self.emit("file_result", uuid=peer.uuid, ok=False,
                              name=hdr.get("name", ""),
                              msg="对方拒绝了文件：" + hdr.get("name", ""))
                elif t == T_DATA:
                    self._on_data(peer, hdr)
                elif t == T_CANCEL:
                    self.emit("file_result", uuid=peer.uuid, ok=False,
                              name=hdr.get("name", ""),
                              msg="对方取消了传输：" + hdr.get("name", ""))
                elif t == T_GINVITE:
                    self._on_ginvite(peer, hdr)
                elif t == T_GTEXT:
                    self._on_gtext(peer, hdr)
                elif t == T_GLEAVE:
                    self._on_gleave(peer, hdr)
                else:
                    continue
        except Exception:
            pass
        finally:
            self._unregister(peer)

    def _recv_small(self, sock, size):
        if size <= 0:
            return True, b""
        data = recv_exact(sock, size)
        if data is None:
            return False, b""
        return True, data

    # ---------------- 发送文本 ----------------
    def send_text(self, peer_uuid, text):
        peer = self.get_peer(peer_uuid)
        if not peer:
            self.emit("error", msg="未连接到该对端")
            return False
        body = text.encode("utf-8")
        try:
            with peer.send_lock:
                send_frame(peer.sock, {"t": T_TEXT, "time": now_str(),
                                       "name": self.nickname,
                                       "body_size": len(body)}, body=body)
        except Exception as e:
            self.emit("error", msg="发送失败：%s" % e)
            self._unregister(peer)
            return False
        return True

    # ================= 群聊 =================
    # 无服务器怎么做群聊：
    #   群里每个成员都保存一份「群成员表」，发消息时直接发给所有在线成员；
    #   收到消息的人若发现还有别的成员，就把消息再转一跳（靠 msg_id 去重防回环）。
    #   这样只要群内连接是连通的，就算 A 和 C 没直连，消息也能经 B 送到 C。
    def create_group(self, name, member_uuids=None):
        """建群：把自己和选定的对端拉进来，并通知每个人"""
        gid = uuid.uuid4().hex[:12]
        members = {self.uuid: {"uuid": self.uuid, "name": self.nickname}}
        for u in (member_uuids or []):
            p = self.get_peer(u)
            if p:
                members[u] = {"uuid": u, "name": p.name or p.title}
        with self.groups_lock:
            self.groups[gid] = {"id": gid, "name": name or "群聊",
                                "owner": self.uuid, "members": members}
        self._group_broadcast(gid, {"t": T_GINVITE, "group_id": gid,
                                    "name": name or "群聊",
                                    "owner": self.uuid,
                                    "members": list(members.values())})
        self.emit("group_created", group_id=gid, name=name or "群聊",
                  count=len(members))
        return gid

    def add_to_group(self, gid, uuids):
        """拉人：给新人发邀请，给老人同步成员表"""
        g = self.groups.get(gid)
        if not g:
            return 0
        added = []
        for u in (uuids or []):
            p = self.get_peer(u)
            if p and u not in g["members"]:
                g["members"][u] = {"uuid": u, "name": p.name or p.title}
                added.append(u)
        if not added:
            return 0
        frame = {"t": T_GINVITE, "group_id": gid, "name": g["name"],
                 "owner": g.get("owner"), "members": list(g["members"].values())}
        self._group_broadcast(gid, frame)
        self.emit("group_member", group_id=gid, name=g["name"], added=added,
                  count=len(g["members"]))
        return len(added)

    def leave_group(self, gid):
        g = self.groups.get(gid)
        if not g:
            return False
        self._group_broadcast(gid, {"t": T_GLEAVE, "group_id": gid,
                                    "uuid": self.uuid,
                                    "name": self.nickname})
        with self.groups_lock:
            self.groups.pop(gid, None)
        self.emit("group_left", group_id=gid, name=g["name"])
        return True

    def group_list(self):
        """[(group_id, 群名, 成员数, 在线数), ...]"""
        out = []
        with self.groups_lock:
            for gid, g in self.groups.items():
                mem = g.get("members", {})
                online = sum(1 for u in mem
                             if u != self.uuid and self.get_peer(u))
                out.append((gid, g.get("name", "群聊"), len(mem), online))
        return out

    def group_members(self, gid):
        """[[uuid, 名字, 是否在线, 是否是自己], ...]"""
        g = self.groups.get(gid)
        if not g:
            return []
        out = []
        for u, m in g.get("members", {}).items():
            out.append([u, m.get("name", ""), bool(self.get_peer(u)),
                        u == self.uuid])
        return out

    def send_group_text(self, gid, text):
        g = self.groups.get(gid)
        if not g:
            self.emit("error", msg="群聊不存在或已退出")
            return False
        mid = uuid.uuid4().hex
        self._gseen_mark(mid)
        frame = {"t": T_GTEXT, "group_id": gid, "msg_id": mid, "text": text,
                 "from": self.uuid, "from_name": self.nickname,
                 "time": now_str(), "ttl": GROUP_TTL}
        n = self._group_broadcast(gid, frame)
        self.emit("group_message", group_id=gid, msg_id=mid, text=text,
                  from_uuid=self.uuid, from_name=self.nickname,
                  time=frame["time"], mine=True, reached=n)
        return True

    def send_group_file(self, gid, path, on_progress=None, disp_name=None):
        """群文件：逐个成员发送（对方各自确认），阻塞式，请在线程里调用"""
        g = self.groups.get(gid)
        if not g:
            self.emit("error", msg="群聊不存在或已退出")
            return 0
        n = 0
        for u in list(g.get("members", {})):
            if u == self.uuid or not self.get_peer(u):
                continue
            self.send_file(u, path, on_progress=on_progress,
                           disp_name=disp_name)
            n += 1
        return n

    def _group_broadcast(self, gid, frame, exclude=None):
        """把一帧发给群里所有在线成员（自己和 exclude 除外），返回送达数"""
        g = self.groups.get(gid)
        if not g:
            return 0
        n = 0
        for u in list(g.get("members", {})):
            if u == self.uuid or u == exclude:
                continue
            p = self.get_peer(u)
            if not p:
                continue
            try:
                with p.send_lock:
                    send_frame(p.sock, dict(frame))
                n += 1
            except Exception:
                pass
        return n

    def _gseen_mark(self, mid):
        with self._gseen_lock:
            now = time.time()
            if len(self._gseen) > GSEEN_MAX:
                for k in [k for k, v in self._gseen.items()
                          if now - v > GSEEN_TTL]:
                    self._gseen.pop(k, None)
                if len(self._gseen) > GSEEN_MAX:
                    self._gseen.clear()
            if mid in self._gseen:
                return False
            self._gseen[mid] = now
            return True

    def _on_ginvite(self, peer, hdr):
        gid = hdr.get("group_id")
        if not gid:
            return
        members = {}
        for m in (hdr.get("members") or []):
            if isinstance(m, dict) and m.get("uuid"):
                members[m["uuid"]] = {"uuid": m["uuid"],
                                      "name": m.get("name", "")}
        members.setdefault(peer.uuid, {"uuid": peer.uuid,
                                       "name": peer.name or peer.title})
        members[self.uuid] = {"uuid": self.uuid, "name": self.nickname}
        with self.groups_lock:
            g = self.groups.get(gid)
            if g:
                g["members"].update(members)
                if hdr.get("name"):
                    g["name"] = hdr["name"]
                fresh = False
            else:
                self.groups[gid] = {"id": gid,
                                    "name": hdr.get("name") or "群聊",
                                    "owner": hdr.get("owner") or peer.uuid,
                                    "members": members}
                fresh = True
            name = self.groups[gid]["name"]
            cnt = len(self.groups[gid]["members"])
        if fresh:
            self.emit("group_invited", group_id=gid, name=name,
                      by=peer.uuid, by_name=peer.name or peer.title,
                      count=cnt)
        else:
            self.emit("group_member", group_id=gid, name=name, added=[],
                      count=cnt)

    def _on_gtext(self, peer, hdr):
        gid = hdr.get("group_id")
        mid = hdr.get("msg_id") or ""
        if mid and not self._gseen_mark(mid):
            return                       # 已经收过，丢弃（防回环/重复）
        with self.groups_lock:
            g = self.groups.get(gid)
            if not g:
                return                   # 不在群里就当没看见
            frm = hdr.get("from")
            if frm and frm != self.uuid:
                g["members"].setdefault(frm, {"uuid": frm,
                                              "name": hdr.get("from_name", "")})
        self.emit("group_message", group_id=gid, msg_id=mid,
                  text=hdr.get("text", ""), from_uuid=frm,
                  from_name=hdr.get("from_name", ""),
                  time=hdr.get("time", now_str()), mine=False)
        ttl = int(hdr.get("ttl") or 0)
        if ttl > 0:
            fwd = dict(hdr)
            fwd["ttl"] = ttl - 1
            self._group_broadcast(gid, fwd, exclude=peer.uuid)

    def _on_gleave(self, peer, hdr):
        gid = hdr.get("group_id")
        with self.groups_lock:
            g = self.groups.get(gid)
            if not g:
                return
            g["members"].pop(hdr.get("uuid") or peer.uuid, None)
            cnt = len(g["members"])
            name = g["name"]
        self.emit("group_member", group_id=gid, name=name, added=[],
                  count=cnt, left=hdr.get("uuid") or peer.uuid,
                  left_name=hdr.get("name", ""))

    # ---------------- 文件：发送 ----------------
    def send_file(self, peer_uuid, path, on_progress=None, disp_name=None):
        peer = self.get_peer(peer_uuid)
        if not peer:
            self.emit("error", msg="未连接到该对端")
            return
        name = disp_name or os.path.basename(path)
        try:
            size = os.path.getsize(path)
        except Exception as e:
            self.emit("file_result", uuid=peer_uuid, ok=False, name=name,
                      msg="读取文件失败：%s" % e)
            return
        file_id = uuid.uuid4().hex
        ev = threading.Event()
        self._accept_events[file_id] = ev
        self._accept_result.pop(file_id, None)
        try:
            with peer.send_lock:
                send_frame(peer.sock, {"t": T_OFFER, "file_id": file_id,
                                       "name": name, "size": size})
        except Exception as e:
            self.emit("file_result", uuid=peer_uuid, ok=False, name=name,
                      msg="发送失败：%s" % e)
            self._unregister(peer)
            return
        self.emit("file_waiting", uuid=peer_uuid, name=name, size=size)
        if not ev.wait(ACCEPT_TIMEOUT):
            self.emit("file_result", uuid=peer_uuid, ok=False, name=name,
                      msg="对方未在 %d 秒内响应，已取消" % ACCEPT_TIMEOUT)
            self._clean_file(file_id)
            return
        if not self._accept_result.get(file_id):
            self._clean_file(file_id)
            return
        try:
            fp = open(path, "rb")
        except Exception as e:
            self.emit("file_result", uuid=peer_uuid, ok=False, name=name,
                      msg="打开文件失败：%s" % e)
            self._clean_file(file_id)
            return
        try:
            with peer.send_lock:
                send_frame(peer.sock, {"t": T_DATA, "file_id": file_id,
                                       "name": name, "body_size": size},
                           stream=fp, total=size, progress=on_progress)
        except Exception as e:
            fp.close()
            self._clean_file(file_id)
            self.emit("file_result", uuid=peer_uuid, ok=False, name=name,
                      msg="传输中断：%s" % e)
            if not self.get_peer(peer_uuid):
                pass
            return
        fp.close()
        self._clean_file(file_id)
        self.emit("file_result", uuid=peer_uuid, ok=True, name=name,
                  size=size, send=True, msg="已发送文件 %s（%s）"
                  % (name, human_size(size)))

    def _clean_file(self, file_id):
        self._accept_events.pop(file_id, None)
        self._accept_result.pop(file_id, None)

    def cancel_send(self, peer_uuid, file_id):
        peer = self.get_peer(peer_uuid)
        if peer and file_id:
            try:
                with peer.send_lock:
                    send_frame(peer.sock, {"t": T_CANCEL, "file_id": file_id})
            except Exception:
                pass

    # ---------------- 文件：接收 ----------------
    def _on_offer(self, peer, hdr):
        file_id = hdr.get("file_id")
        name = hdr.get("name", "未命名")
        size = hdr.get("size", 0)
        self._incoming = getattr(self, "_incoming", {})
        self._incoming[file_id] = (peer.uuid, hdr)
        if self.auto_accept_dir:
            path = os.path.join(self.auto_accept_dir, name)
            self.accept_file(peer.uuid, file_id, path)
            return
        self.emit("file_offer", uuid=peer.uuid, title=peer.title,
                  file_id=file_id, name=name, size=size)

    def accept_file(self, peer_uuid, file_id, save_path):
        peer = self.get_peer(peer_uuid)
        if not peer:
            return
        try:
            with peer.send_lock:
                send_frame(peer.sock, {"t": T_ACCEPT, "file_id": file_id})
        except Exception:
            pass
        self._pending_save = getattr(self, "_pending_save", {})
        self._pending_save[file_id] = save_path

    def reject_file(self, peer_uuid, file_id):
        peer = self.get_peer(peer_uuid)
        if not peer:
            return
        try:
            with peer.send_lock:
                send_frame(peer.sock, {"t": T_REJECT, "file_id": file_id})
        except Exception:
            pass

    def _on_data(self, peer, hdr):
        file_id = hdr.get("file_id")
        name = hdr.get("name", "接收文件")
        size = hdr.get("body_size", 0)
        pending = getattr(self, "_pending_save", {})
        path = pending.pop(file_id, None)
        if not path:
            path = os.path.join(self.auto_accept_dir or ".", name)
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            try:
                os.makedirs(d)
            except Exception:
                path = os.path.join(os.path.expanduser("~"), name)
        base, ext = os.path.splitext(path)
        n = 1
        while os.path.exists(path):
            path = "%s(%d)%s" % (base, n, ext)
            n += 1
        try:
            fp = open(path, "wb")
        except Exception as e:
            self.emit("file_result", uuid=peer.uuid, ok=False, name=name,
                      msg="保存失败：%s" % e)
            return

        def prog(got, total):
            self.emit("file_progress", uuid=peer.uuid, name=name, send=False,
                      got=got, total=total)

        ok = recv_body(peer.sock, size, fp=fp, progress=prog)
        fp.close()
        if ok:
            real = os.path.getsize(path)
            if real != size:
                self.emit("file_result", uuid=peer.uuid, ok=False, name=name,
                          msg="文件不完整（%s / %s）"
                          % (human_size(real), human_size(size)))
                return
            self.emit("file_result", uuid=peer.uuid, ok=True, name=name,
                      size=size, send=False, path=path,
                      msg="已接收文件 %s（%s）→ %s"
                      % (name, human_size(size), path))
        else:
            self.emit("file_result", uuid=peer.uuid, ok=False, name=name,
                      msg="接收中断：%s" % name)


# =============================================================================
#  图形界面
# =============================================================================

import argparse
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
        from PyQt6.QtWidgets import (QActionGroup, QApplication,
                                     QMenu, QSystemTrayIcon, QWidget)
        QT = "PyQt6"

# PyQt6 取消了枚举简写别名，这里做一层兼容
ALIGN_CENTER = getattr(Qt, "AlignCenter", None) or Qt.AlignmentFlag.AlignCenter
NO_PEN = getattr(Qt, "NoPen", None) or Qt.PenStyle.NoPen
SOLID_LINE = getattr(Qt, "SolidLine", None) or Qt.PenStyle.SolidLine
ROUND_CAP = getattr(Qt, "RoundCap", None) or Qt.PenCapStyle.RoundCap
ROUND_JOIN = getattr(Qt, "RoundJoin", None) or Qt.PenJoinStyle.RoundJoin

PET_NAME = "摸鱼鸭"
FPS = 30

# 探测系统中文字体（用于气泡/标签）。仅在已有 QApplication 时调用，
# 因此做成惰性初始化（首次画气泡时探测并缓存）。
if QT == "PyQt5":
    from PyQt5.QtGui import QFontDatabase
elif QT == "PySide6":
    from PySide6.QtGui import QFontDatabase
else:
    from PyQt6.QtGui import QFontDatabase
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

# ===========================================================================
#  自检 / 环境诊断（无需图形界面）
# ===========================================================================

def selftest():
    import hashlib
    import tempfile

    print("=" * 56)
    print(" %s v%s  网络层自检" % (APP_NAME, VERSION))
    print("=" * 56)
    tmpdir = tempfile.mkdtemp(prefix="fxl_selftest_")
    src = os.path.join(tmpdir, "testfile.bin")
    with open(src, "wb") as f:
        f.write(os.urandom(1024 * 1024))          # 1 MB 随机数据
    md5_src = hashlib.md5(open(src, "rb").read()).hexdigest()

    events_a, events_b = [], []
    a = ChatNode(lambda e: events_a.append(e), nickname="A")
    b = ChatNode(lambda e: events_b.append(e), nickname="B",
                 auto_accept_dir=tmpdir)
    ok1, m1 = a.start_listening(0)
    ok2, m2 = b.start_listening(0)
    print("A 监听 -> %s / %s" % (ok1, m1))
    print("B 监听 -> %s / %s" % (ok2, m2))
    if not (ok1 and ok2):
        print("结果：失败（无法监听端口）")
        return 1

    # 0) 局域网广播发现（先于手动连接测试，验证自动找人能力）
    disc_ok = False
    try:
        hits = a.discover(timeout=2.5)
        disc_ok = any(h.get("uuid") == b.uuid for h in hits)
        print("广播发现：%s  应答 %d 台 %s"
              % ("通过" if disc_ok else "失败", len(hits),
                 [h.get("ip") for h in hits]))
        if not disc_ok:
            print("         提示：同一台机器上广播可能被系统策略拦下，"
                  "跨机使用时以实际扫描结果为准")
    except Exception as e:
        print("广播发现：异常 -> %s" % e)

    print("B 连接 A（127.0.0.1:%d）..." % a.port)
    if not b.connect("127.0.0.1", a.port):
        print("结果：失败（连接未建立）")
        return 1
    for _ in range(50):
        if a.peer_list() and b.peer_list():
            break
        time.sleep(0.1)
    if not (a.peer_list() and b.peer_list()):
        print("结果：失败（对端未出现在列表中）")
        return 1
    print("连接建立成功，A 看到 %d 个对端，B 看到 %d 个对端"
          % (len(a.peer_list()), len(b.peer_list())))

    key_b = a.peer_list()[0][0]
    key_a = b.peer_list()[0][0]

    # 1) 文字消息
    a.send_text(key_b, "你好，这是一条测试消息")
    got = None
    for _ in range(50):
        for e in events_b:
            if e.get("type") == "message":
                got = e.get("text")
        if got:
            break
        time.sleep(0.1)
    print("文字消息：%s  -> %r" % ("通过" if got else "失败", got))
    if not got:
        return 1

    # 2) 文件传输（B 自动接收，A 发送）
    t0 = time.time()
    a.send_file(key_b, src)
    done = None
    for _ in range(200):
        for e in events_a:
            if e.get("type") == "file_result" and e.get("send"):
                done = e
        if done:
            break
        time.sleep(0.05)
    if not done or not done.get("ok"):
        print("文件传输：失败 ->", done)
        return 1
    dst = os.path.join(tmpdir, "testfile.bin")
    if not os.path.exists(dst):
        print("文件传输：失败（未找到接收文件）")
        return 1
    md5_dst = hashlib.md5(open(dst, "rb").read()).hexdigest()
    same = (md5_src == md5_dst) and os.path.getsize(dst) == 1024 * 1024
    print("文件传输：%s  1MB 用时 %.2fs  MD5 %s"
          % ("通过" if same else "失败", time.time() - t0,
             "一致" if same else "不一致"))

    # 3) 断开检测
    b.stop()
    for _ in range(50):
        if not a.peer_list():
            break
        time.sleep(0.1)
    disc = (len(a.peer_list()) == 0)
    print("断线检测：%s" % ("通过" if disc else "失败"))

    a.stop()
    try:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass
    print("-" * 56)
    allok = bool(same and disc and got)
    print("结果：%s%s" % ("全部通过 ✅" if allok else "存在问题 ❌",
                          "" if disc_ok else "（广播发现未通过，"
                          "跨机时请用「扫描局域网」兜底）"))
    return 0 if allok else 1


# =============================================================================
def diag():
    """环境自检：把可能导致打不开的原因一次性查清楚"""
    import platform
    print("=" * 58)
    print(" 欸~你听说没~ · 环境诊断")
    print("=" * 58)
    print("系统      : %s" % platform.platform())
    print("Python    : %s" % sys.version.split()[0])
    print("程序位置  : %s" % os.path.abspath(__file__))

    try:
        import tkinter
        print("图形库    : 可用（Tk %s）" % tkinter.TkVersion)
    except Exception as e:
        print("图形库    : 不可用 -> %s" % e)
        print("           请执行：sudo apt install python3-tk")

    t0 = time.time()
    nics = _collect_nets()
    ips = [n[0] for n in nics] or ["127.0.0.1"]
    print("本机 IP   : %s（探测耗时 %.2f 秒）" % ("、".join(ips),
                                            time.time() - t0))
    if ips == ["127.0.0.1"]:
        print("           警告：没拿到局域网 IP —— 检查网线/WiFi 是否连上、")
        print("           是否拿到了 IP（终端里 ip addr 或 设置→网络 里看）")
    if nics:
        print("网卡明细  :")
        for ip, bits, bcast, name, method in nics:
            print("           %-15s /%-2d  广播 %-15s %-9s [%s]"
                  % (ip, bits, bcast, name or "-", method))
    else:
        print("网卡明细  : 一张都没识别出来（ioctl/ip/ifconfig/ipconfig 全失败）")

    for port in (DEFAULT_PORT, DEFAULT_PORT + 1):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", port))
            s.listen(1)
            s.close()
            print("端口 %d  : 可监听 ✅" % port)
        except Exception as e:
            print("端口 %d  : 监听失败 -> %s" % (port, e))

    nets = get_local_nets()
    print("网段      : %s" % "、".join(
        "%s/%d(bcast %s)" % n for n in nets if not n[0].startswith("127."))
        or "未识别到")
    # UDP 广播收发能力
    try:
        n = ChatNode(lambda e: None, nickname="diag")
        if n.start_discovery():
            print("广播端口  : %d 可绑定 ✅" % n._disc_port)
        else:
            print("广播端口  : 50010~50020 全部绑定失败 ❌")
        hits = n.discover(timeout=1.5)
        print("广播测试  : 收到 %d 个应答（本机其它实例/同网段电脑）" % len(hits))
        for h in hits:
            print("            - %s:%s  %s" % (h["ip"], h["port"], h["name"]))
        n.stop_discovery()
    except Exception as e:
        print("广播测试  : 异常 -> %s" % e)

    print("日志文件  : %s" % LOG_PATH)
    print("-" * 58)
    print("要发给开发看：python3 摸鱼鸭聊天版.py --report 导出诊断报告")
    print("说明：本程序为纯本地点对点通信，不连接任何外部服务器。")
    print("      若系统安全软件弹出联网/监听询问，请选择「允许」")
    print("      （它需要监听端口供对方连入，这是正常工作方式）。")
    print("=" * 58)
    return 0


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
        self.ip_combo.setToolTip(
            "把这个 IP 告诉对方。\n若显示 127.0.0.1，说明没识别到网卡，"
            "点「刷新 IP」重试。")
        top.addWidget(self.ip_combo)
        self.btn_ip = QPushButton("刷新 IP")
        self.btn_ip.setFixedWidth(70)
        self.btn_ip.clicked.connect(self.refresh_ips)
        top.addWidget(self.btn_ip)

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
        a = QAction("导出诊断报告…", self)
        a.setToolTip("把运行环境、网卡、端口和最近日志导出成一个文本文件，"
                     "发给开发排查问题")
        a.triggered.connect(self.export_report)
        m_help.addAction(a)
        a = QAction("打开运行日志…", self)
        a.triggered.connect(self.open_log)
        m_help.addAction(a)
        self.refresh_offwork_action()

    def export_report(self):
        extra = []
        try:
            extra.append("监听状态：%s（端口 %s）"
                         % ("监听中" if self.node.running else "未启动",
                            self.node.port or "-"))
            extra.append("本机 IP（界面显示）：%s"
                         % (self.ip_combo.currentText() or "-"))
            extra.append("已连接 %d 人：%s"
                         % (len(self.node.peer_list()),
                            "、".join(sorted(self.node.peer_list())) or "无"))
        except Exception:
            pass
        try:
            path = save_report(extra)
            log_line("已导出诊断报告：%s" % path)
            QMessageBox.information(
                self, "诊断报告已导出",
                "报告已保存到：\n\n%s\n\n把这个文件发给开发即可。\n"
                "里面只有环境、网卡、端口和日志，不含聊天内容。\n\n"
                "（命令行也可以生成：python3 摸鱼鸭聊天版.py --report）"
                % path)
        except Exception as e:
            log_line("导出诊断报告失败", exc=True)
            QMessageBox.warning(self, "导出失败", "生成报告时出错：\n%s" % e)

    def open_log(self):
        import subprocess
        path = LOG_PATH
        try:
            if not os.path.isfile(path):
                open(path, "a", encoding="utf-8").close()
            if sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", path])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                os.startfile(path)  # noqa: S606
        except Exception as e:
            log_line("打开日志失败：%s" % e)
            QMessageBox.information(self, "运行日志",
                                    "日志文件在这里：\n%s\n\n（打开失败：%s）"
                                    % (path, e))

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
        self.ip_combo.addItem("检测中…")

        def work():
            ips = get_local_ips()
            QTimer.singleShot(0, lambda: self._set_ips(ips))

        threading.Thread(target=work, daemon=True).start()

    def _set_ips(self, ips):
        self.ip_combo.clear()
        for ip in ips:
            self.ip_combo.addItem(ip)
        if ips:
            self.ip_combo.setCurrentIndex(0)
        if not ips or ips == ["127.0.0.1"]:
            log_line("警告：没识别到局域网 IP（get_local_ips 只返回 %s）" % ips)
            self.ctrl.bridge.status.emit("没识别到局域网 IP（只有 127.0.0.1）")
            self.sys_msg(
                "⚠ 没识别到局域网 IP：请确认网线/WiFi 已连上并拿到了 IP，"
                "然后点「刷新 IP」。")
            self.sys_msg("  还是不行：帮助 → 导出诊断报告，把报告发给开发"
                         "（或跑 python3 摸鱼鸭聊天版.py --diag）")
            return
        log_line("本机 IP：%s" % "、".join(ips))
        self.ctrl.bridge.status.emit("本机 IP：" + "、".join(ips))

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
            for ip, bits, _c in get_local_nets(timeout=1.5):
                if ip.startswith("127."):
                    continue
                # 掩码比 /24 大时按真实网段来，否则会漏掉同网段的机器
                if bits and bits < 24:
                    try:
                        import ipaddress
                        nets.append(str(ipaddress.IPv4Network(
                            "%s/%d" % (ip, bits), strict=False)))
                        continue
                    except Exception:
                        pass
                nets.append(".".join(ip.split(".")[:3]))
        if not nets:
            self.sys_msg("没检测到局域网 IP，无法扫描：先点「刷新 IP」确认能拿到 IP；"
                         "也可以直接在「网段」里手填，例如 192.168.5")
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
        log_line("扫描结束：网段 %s，深度=%s；连上 %d 台 [%s]，"
                 "看到但连不上 [%s]"
                 % ("、".join(nets), deep, len(ok_ips),
                    "、".join(sorted(ok_ips)), "、".join(sorted(bad_ips))))
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
        menu.addAction("导出诊断报告…", self.export_report_tray)
        menu.addSeparator()
        menu.addAction("退出", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def export_report_tray(self):
        """托盘菜单导出报告（窗口打不开时也能用）"""
        extra = []
        try:
            extra.append("监听状态：%s（端口 %s）"
                         % ("监听中" if self.win.node.running else "未启动",
                            self.win.node.port or "-"))
            extra.append("已连接 %d 人：%s"
                         % (len(self.win.node.peer_list()),
                            "、".join(sorted(self.win.node.peer_list())) or "无"))
        except Exception:
            pass
        try:
            path = save_report(extra)
            log_line("已导出诊断报告：%s" % path)
            self.tray.showMessage(
                "诊断报告已导出",
                "已保存到 %s\n把这个文件发给开发即可。" % path,
                QSystemTrayIcon.Information
                if hasattr(QSystemTrayIcon, "Information")
                else QSystemTrayIcon.MessageIcon.Information,
                6000)
        except Exception:
            log_line("导出诊断报告失败", exc=True)

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
        log_line("程序退出")
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
    ap.add_argument("--report", action="store_true",
                    help="导出诊断报告（环境/网卡/端口/日志）后退出")
    args = ap.parse_args()

    install_log_hooks()
    log_line("=" * 50)
    log_line("程序启动（%s）参数：%s"
             % (APP_TITLE, " ".join(sys.argv[1:]) or "无"))

    if args.selftest:
        log_line("运行网络层自检")
        return selftest()
    if args.diag:
        log_line("运行环境诊断")
        return diag()
    if args.report:
        return report_cli()

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
