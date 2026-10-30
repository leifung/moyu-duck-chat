#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
  欸~你听说没~ —— 局域网点对点聊天工具  v3.0
  ----------------------------------------------------------------------
  特点：
    * 免注册、免登录、免服务器，知道对方 IP 即可聊天
    * 支持文字消息 + 任意文件传输（带进度）
    * 局域网发现：UDP 广播自动找人，找不到再按网段逐个握手
    * 同时可连接多个对端，左侧列表切换会话
    * 纯 Python 标准库实现，无需 pip 安装任何依赖
  适用：统信 UOS / deepin / 银河麒麟 / Ubuntu 等 Linux 桌面，Windows 亦可运行
  用法：
    python3 欸你听说没.py                启动图形界面
    python3 欸你听说没.py --selftest     网络层自检（无需图形界面）
    python3 欸你听说没.py --diag         环境诊断
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
            return False, "监听端口 %s 失败：%s" % (port, e)
        self.server_sock = s
        self.port = s.getsockname()[1]
        self.running = True
        self._stop_event.clear()
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        self.start_discovery()
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
            return list(self._disc_hits.values())

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
class ChatUI(object):
    """图形界面。会话 key 的约定：
    单聊用对端 uuid，群聊用「g:群ID」，靠 gkey/gid_of/is_group_key 区分。
    """

    def __init__(self, root):
        self.root = root
        self.cfg = self.load_cfg()
        self.node = ChatNode(on_event=self.on_event,
                             nickname=self.cfg.get("nickname", ""))
        self.current = None                      # 当前会话 uuid
        self.conv = {}                           # uuid -> 历史文本
        self.pending_offer = None                # 正在处理的接收弹窗
        self.progress_job = None
        self.msgs = {}                           # uuid -> list[(who, text, time)]
        self.root.title("%s  v%s" % (APP_NAME, VERSION))
        self.root.geometry("880x620")
        self.root.minsize(760, 520)
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.sys_msg("欢迎使用 %s v%s" % (APP_NAME, VERSION))
        self.sys_msg("使用方法：把上方「本机 IP」告诉对方，或在下方填入对方 IP "
                     "点「连接」，也可以点「扫描局域网」自动发现。")
        self.auto_start()

    # ---------------- 配置 ----------------
    @staticmethod
    def cfg_path():
        return os.path.join(os.path.expanduser("~"), ".eynsm.json")

    def load_cfg(self):
        try:
            with open(self.cfg_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_cfg(self):
        try:
            with open(self.cfg_path(), "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------------- 界面 ----------------
    def build_ui(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.ttk = ttk

        top = tk.Frame(self.root, padx=10, pady=8)
        top.pack(fill="x")

        tk.Label(top, text="昵称").pack(side="left")
        self.nick_var = tk.StringVar(value=self.cfg.get("nickname", ""))
        e = tk.Entry(top, textvariable=self.nick_var, width=12)
        e.pack(side="left", padx=(4, 14))
        e.bind("<FocusOut>", lambda ev: self.save_nick())

        tk.Label(top, text="本机 IP").pack(side="left")
        self.ip_var = tk.StringVar(value="")
        self.ip_combo = ttk.Combobox(top, textvariable=self.ip_var, width=16,
                                     state="readonly", values=[])
        self.ip_combo.pack(side="left", padx=(4, 14))

        tk.Label(top, text="端口").pack(side="left")
        self.port_var = tk.StringVar(value=str(self.cfg.get("port",
                                                            DEFAULT_PORT)))
        tk.Entry(top, textvariable=self.port_var, width=7).pack(side="left",
                                                                padx=(4, 10))
        self.btn_listen = tk.Button(top, text="启动监听", width=10,
                                    command=self.toggle_listen)
        self.btn_listen.pack(side="left")
        self.lbl_state = tk.Label(top, text="● 未启动", fg="#A32D2D")
        self.lbl_state.pack(side="left", padx=(10, 0))

        body = tk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        left = tk.Frame(body, width=210)
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)
        tk.Label(left, text="会话（单聊）", anchor="w").pack(fill="x")
        self.list_box = tk.Listbox(left, activestyle="none", height=7,
                                   selectbackground="#378ADD")
        self.list_box.pack(fill="both", expand=True, pady=(2, 2))
        self.list_box.bind("<<ListboxSelect>>", self.on_select)
        tk.Button(left, text="断开选中连接", command=self.disconnect_sel)\
            .pack(fill="x")

        tk.Label(left, text="群聊", anchor="w").pack(fill="x", pady=(8, 0))
        self.group_box = tk.Listbox(left, activestyle="none", height=6,
                                    selectbackground="#0F6E56")
        self.group_box.pack(fill="both", expand=True, pady=(2, 2))
        self.group_box.bind("<<ListboxSelect>>", self.on_group_select)
        gbtns = tk.Frame(left)
        gbtns.pack(fill="x")
        tk.Button(gbtns, text="新建群聊", command=self.new_group)\
            .pack(side="left", fill="x", expand=True)
        tk.Button(gbtns, text="退出群聊", command=self.leave_group_sel)\
            .pack(side="left", fill="x", expand=True, padx=(4, 0))

        right = tk.Frame(body)
        right.pack(side="left", fill="both", expand=True)

        self.font = pick_font(10)
        self.chat = tk.Text(right, wrap="word", state="disabled",
                            font=self.font, spacing1=2,
                            spacing3=2)
        sb = tk.Scrollbar(right, command=self.chat.yview)
        self.chat.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        for tag, color in (("sys", "#888780"), ("me", "#185FA5"),
                           ("peer", "#2C2C2A"), ("ok", "#0F6E56"),
                           ("err", "#A32D2D"), ("time", "#888780")):
            self.chat.tag_config(tag, foreground=color)
        self.chat.tag_config("bold", font=(self.font[0], self.font[1], "bold"))

        bottom = tk.Frame(self.root, padx=10, pady=6)
        bottom.pack(fill="x")

        conn = tk.Frame(bottom)
        conn.pack(fill="x", pady=(0, 6))
        tk.Label(conn, text="对方 IP").pack(side="left")
        self.peer_ip = tk.StringVar(value=self.cfg.get("last_ip", ""))
        tk.Entry(conn, textvariable=self.peer_ip, width=18).pack(side="left",
                                                                 padx=(4, 10))
        tk.Label(conn, text="端口").pack(side="left")
        self.peer_port = tk.StringVar(value=str(self.cfg.get("peer_port",
                                                             DEFAULT_PORT)))
        tk.Entry(conn, textvariable=self.peer_port, width=7).pack(side="left",
                                                                  padx=(4, 10))
        tk.Button(conn, text="连接", width=8, command=self.do_connect)\
            .pack(side="left")
        self.btn_scan = tk.Button(conn, text="扫描局域网", width=10,
                                  command=self.scan_lan)
        self.btn_scan.pack(side="left", padx=(6, 0))
        tk.Label(conn, text="网段").pack(side="left", padx=(10, 0))
        self.scan_net = tk.StringVar(value="")
        tk.Entry(conn, textvariable=self.scan_net, width=14).pack(side="left",
                                                                  padx=(4, 2))
        self.deep_var = tk.BooleanVar(value=False)
        tk.Checkbutton(conn, text="深度", variable=self.deep_var)\
            .pack(side="left", padx=(4, 0))
        tk.Label(conn, text="（留空自动识别；跨网段时手填如 192.168.5）",
                 fg="#888780").pack(side="left", padx=(8, 0))

        send = tk.Frame(bottom)
        send.pack(fill="x")
        self.input = tk.Text(send, height=3, wrap="word", font=self.font)
        self.input.pack(side="left", fill="x", expand=True)
        self.input.bind("<Return>", self.on_return)
        self.input.bind("<Shift-Return>", lambda ev: None)
        btns = tk.Frame(send)
        btns.pack(side="left", padx=(8, 0))
        tk.Button(btns, text="发送", width=9, command=self.send_msg)\
            .pack(fill="x", pady=(0, 4))
        tk.Button(btns, text="发送文件", width=9, command=self.send_file)\
            .pack(fill="x")

        self.status = tk.StringVar(value="就绪")
        tk.Label(self.root, textvariable=self.status, anchor="w",
                 fg="#5F5E5A", padx=10, pady=3).pack(fill="x")

        self.refresh_ips()

    def refresh_ips(self):
        """异步获取，避免网卡探测把启动界面卡住"""
        self.ip_combo["values"] = ["127.0.0.1"]
        self.ip_var.set("127.0.0.1")

        def work():
            ips = get_local_ips()
            self.root.after(0, lambda: self._set_ips(ips))

        threading.Thread(target=work, daemon=True).start()

    def _set_ips(self, ips):
        self.ip_combo["values"] = ips
        self.ip_var.set(ips[0])
        self.status.set("本机 IP：%s" % "、".join(ips))

    def save_nick(self):
        self.cfg["nickname"] = self.nick_var.get().strip()
        self.node.nickname = self.cfg["nickname"] or socket.gethostname()
        self.save_cfg()

    # ---------------- 监听开关 ----------------
    def auto_start(self):
        """启动监听；端口被占用时自动顺延尝试后面的 10 个端口"""
        try:
            base = int(self.port_var.get() or DEFAULT_PORT)
        except Exception:
            base = DEFAULT_PORT
        tried = [base] + list(range(base + 1, base + 11))
        last = ""
        for port in tried:
            ok, msg = self.node.start_listening(port)
            if ok:
                self.btn_listen.config(text="停止监听")
                self.lbl_state.config(text="● 监听中 %d" % self.node.port,
                                      fg="#0F6E56")
                self.port_var.set(str(self.node.port))
                self.cfg["port"] = self.node.port
                self.save_cfg()
                if port != base:
                    self.sys_msg("端口 %d 被占用，已自动改用 %d，"
                                 "请告诉对方这个端口。" % (base, port))
                self.sys_msg("已启动监听，端口 %d。把本机 IP 告诉对方即可。"
                             % self.node.port)
                return
            last = msg
        self.lbl_state.config(text="● 启动失败", fg="#A32D2D")
        self.sys_msg("启动监听失败：%s" % last)
        self.sys_msg("可换一个端口（如 50011）后重新点「启动监听」。")

    def toggle_listen(self):
        if self.node.running:
            self.node.stop_listening()
            self.btn_listen.config(text="启动监听")
            self.lbl_state.config(text="● 未启动", fg="#A32D2D")
            self.sys_msg("已停止监听")
        else:
            self.auto_start()

    # ---------------- 连接 ----------------
    def do_connect(self):
        ip = self.peer_ip.get().strip()
        port = self.peer_port.get().strip()
        if not ip:
            self.sys_msg("请先填写对方 IP 地址")
            return
        self.cfg["last_ip"] = ip
        try:
            self.cfg["peer_port"] = int(port)
        except Exception:
            pass
        self.save_cfg()
        self.status.set("正在连接 %s:%s ..." % (ip, port))
        threading.Thread(target=self.node.connect, args=(ip, port),
                         daemon=True).start()

    def scan_lan(self):
        """先 UDP 广播找人，广播不通再按网段逐个握手"""
        if not self.node.running:
            self.sys_msg("请先点「启动监听」再扫描 —— 没监听时别人也看不到你")
            return
        nets = []
        spec = self.scan_net.get().strip() if hasattr(self, "scan_net") else ""
        if spec:
            nets.append(spec)
        if not nets:
            for ip, _bits, _bc in get_local_nets(timeout=1.5):
                if ip.startswith("127."):
                    continue
                nets.append(".".join(ip.split(".")[:3]))
        if not nets:
            self.sys_msg("没检测到局域网 IP，无法扫描；可在「网段」里手填，"
                         "例如 192.168.5")
            return
        try:
            port = int(self.peer_port.get().strip() or DEFAULT_PORT)
        except Exception:
            port = DEFAULT_PORT
        deep = bool(getattr(self, "deep_var", None) and self.deep_var.get())
        my_port = self.node.port or DEFAULT_PORT
        before = len(self.node.peer_list())
        self.btn_scan.config(state="disabled", text="扫描中…")
        self.status.set("正在广播寻找同网段电脑 …")
        self._scan_stop = threading.Event()
        stop = self._scan_stop

        def work():
            seen = {}          # ip -> {"ok":bool, "name":str, "via":str}
            # ---- 第 1 步：广播发现 ----
            try:
                for h in self.node.discover(timeout=2.0, extra_nets=nets):
                    ip = h.get("ip")
                    ok = (not stop.is_set()) and self.node.connect(
                        ip, h.get("port") or my_port, timeout=3, quiet=True)
                    seen[ip] = {"ok": ok, "name": h.get("name", ""),
                                "via": "广播"}
            except Exception:
                pass
            # ---- 第 2 步：广播没结果才逐个网段握手 ----
            if not seen and not stop.is_set():
                # 端口可能被顺延过，别人的端口未必和自己一样，
                # 所以除了自己的端口，还要覆盖默认端口段。
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
                self.root.after(0, lambda: self.status.set(
                    "广播无应答，改为逐个尝试 %d 个地址 …" % len(targets)))

                def prog(done, total):
                    self.root.after(0, lambda: self.status.set(
                        "逐个尝试中 %d/%d" % (done, total)))

                try:
                    for hit in tcp_scan(self.node, targets, ports,
                                        progress=prog, stop=stop):
                        seen[hit["ip"]] = {"ok": True, "name": "",
                                           "via": "扫描"}
                except Exception:
                    pass
            self.root.after(0, lambda: self.scan_done(seen, nets, before, deep))

        threading.Thread(target=work, daemon=True).start()

    def scan_done(self, seen, nets, before, deep):
        try:
            self.btn_scan.config(state="normal", text="扫描局域网")
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
        self.status.set("就绪")
        self.refresh_list()

    def disconnect_sel(self):
        if self.current and not is_group_key(self.current):
            self.node.disconnect(self.current)
            self.sys_msg("已断开连接")

    def on_select(self, ev=None):
        sel = self.list_box.curselection()
        if not sel:
            return
        idx = sel[0]
        uuids = getattr(self, "_list_uuids", [])
        if idx < len(uuids):
            self.switch_to(uuids[idx])

    def on_group_select(self, ev=None):
        sel = self.group_box.curselection()
        if not sel:
            return
        idx = sel[0]
        gids = getattr(self, "_list_groups", [])
        if idx < len(gids):
            self.switch_to(gkey(gids[idx]))

    def switch_to(self, key):
        self.current = key
        self.render_chat()

    def refresh_list(self):
        uuids = []
        labels = []
        for u, title, name in self.node.peer_list():
            uuids.append(u)
            labels.append("%s  %s" % (title, ("(%s)" % name) if name else ""))
        self._list_uuids = uuids
        self.list_box.delete(0, "end")
        for lb in labels:
            self.list_box.insert("end", lb)

        gids = []
        glabels = []
        for gid, gname, cnt, online in self.node.group_list():
            gids.append(gid)
            glabels.append("%s  (%d人·%d在线)" % (gname, cnt, online))
        self._list_groups = gids
        self.group_box.delete(0, "end")
        for lb in glabels:
            self.group_box.insert("end", lb)

        keys = uuids + [gkey(g) for g in gids]
        if self.current not in keys:
            self.current = keys[0] if keys else None
            self.render_chat()
        else:
            try:
                if is_group_key(self.current):
                    self.group_box.selection_set(
                        gids.index(gid_of(self.current)))
                else:
                    self.list_box.selection_set(uuids.index(self.current))
            except Exception:
                pass

    # ---------------- 群聊 ----------------
    def new_group(self):
        from tkinter import messagebox
        peers = self.node.peer_list()
        if not peers:
            try:
                messagebox.showinfo(
                    "新建群聊", "还没有连接任何电脑。\n"
                    "先连接对方，或点「扫描局域网」找人。")
            except Exception:
                self.sys_msg("还没有连接任何电脑，无法建群")
            return
        win = tk.Toplevel(self.root)
        win.title("新建群聊")
        try:
            win.geometry("340x380")
            win.transient(self.root)
        except Exception:
            pass
        tk.Label(win, text="群名称").pack(anchor="w", padx=12, pady=(12, 4))
        name_var = tk.StringVar(value="群聊")
        tk.Entry(win, textvariable=name_var).pack(fill="x", padx=12)
        tk.Label(win, text="选择成员（Ctrl / Shift 可多选）")\
            .pack(anchor="w", padx=12, pady=(10, 4))
        lb = tk.Listbox(win, selectmode="multiple")
        for u, title, name in peers:
            lb.insert("end", "%s  %s" % (title, "(%s)" % name if name else ""))
        lb.pack(fill="both", expand=True, padx=12)

        def do_ok():
            sel = lb.curselection()
            if not sel:
                try:
                    messagebox.showinfo("新建群聊", "请至少选择一位成员")
                except Exception:
                    pass
                return
            uuids = [peers[i][0] for i in sel]
            gid = self.node.create_group(name_var.get().strip() or "群聊",
                                         uuids)
            key = gkey(gid)
            self.msgs.setdefault(key, [])
            self.refresh_list()
            self.switch_to(key)
            try:
                win.destroy()
            except Exception:
                pass

        tk.Button(win, text="创建", command=do_ok).pack(pady=10)

    def leave_group_sel(self):
        from tkinter import messagebox
        key = self.current
        if not (key and is_group_key(key)):
            try:
                messagebox.showinfo("退出群聊",
                                    "请先在左侧「群聊」列表里选中一个群")
            except Exception:
                self.sys_msg("请先在群聊列表里选中一个群")
            return
        gid = gid_of(key)
        name = self.node.groups.get(gid, {}).get("name", "")
        try:
            yes = messagebox.askyesno("退出群聊",
                                      "确定退出群聊「%s」吗？" % name)
        except Exception:
            yes = True
        if not yes:
            return
        self.node.leave_group(gid)
        self.msgs.pop(key, None)
        self.current = None
        self.refresh_list()

    # ---------------- 消息区 ----------------
    def render_chat(self):
        self.chat.config(state="normal")
        self.chat.delete("1.0", "end")
        for kind, text, tm in self.msgs.get(self.current or "__sys__", []):
            self._append_raw(kind, text, tm)
        self.chat.config(state="disabled")
        self.chat.see("end")

    def _append_raw(self, kind, text, tm):
        self.chat.insert("end", "[%s] " % tm, "time")
        if kind == "sys":
            self.chat.insert("end", text + "\n", "sys")
        elif kind == "me":
            self.chat.insert("end", "我：", "me")
            self.chat.insert("end", text + "\n", "me")
        elif kind == "peer":
            self.chat.insert("end", "对方：", "peer")
            self.chat.insert("end", text + "\n", "peer")
        elif kind == "gme":          # 群消息：文本里已带发言人名字
            self.chat.insert("end", text + "\n", "me")
        elif kind == "gpeer":
            self.chat.insert("end", text + "\n", "peer")
        elif kind == "ok":
            self.chat.insert("end", text + "\n", "ok")
        elif kind == "err":
            self.chat.insert("end", text + "\n", "err")

    def append_msg(self, kind, text, key=None):
        tm = now_str()
        key = key or self.current or "__sys__"
        if key not in self.msgs:
            self.msgs[key] = []
        self.msgs[key].append((kind, text, tm))
        if key == (self.current or "__sys__"):
            self.chat.config(state="normal")
            self._append_raw(kind, text, tm)
            self.chat.config(state="disabled")
            self.chat.see("end")

    def sys_msg(self, text):
        self.append_msg("sys", text)

    # ---------------- 发送 ----------------
    def on_return(self, ev):
        self.send_msg()
        return "break"

    def send_msg(self):
        text = self.input.get("1.0", "end").rstrip("\n").strip()
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
            self.input.delete("1.0", "end")
            self.append_msg("gme", "%s：%s" % (self.node.nickname, text),
                            key=key)
            threading.Thread(target=self.node.send_group_text,
                             args=(gid, text), daemon=True).start()
            return
        peer = self.node.get_peer(key)
        if not peer:
            self.sys_msg("连接已断开")
            return
        self.input.delete("1.0", "end")
        self.append_msg("me", text, key=key)

        def run():
            self.node.send_text(key, text)
        threading.Thread(target=run, daemon=True).start()

    def send_file(self):
        from tkinter import filedialog
        if not self.current:
            self.sys_msg("还没有建立连接，请先连接对方")
            return
        path = filedialog.askopenfilename(title="选择要发送的文件")
        if not path:
            return
        key = self.current
        name = os.path.basename(path)
        if is_group_key(key):
            gid = gid_of(key)
            self.append_msg("sys", "正在向群成员逐个发送：%s" % name, key=key)

            def run_g():
                n = self.node.send_group_file(gid, path)
                self.root.after(0, lambda: self.append_msg(
                    "ok" if n else "err",
                    "群文件已发给 %d 位成员：%s" % (n, name)
                    if n else "没有可发送的群成员（成员都不在线）", key=key))
                self.root.after(0, lambda: self.status.set("就绪"))
            threading.Thread(target=run_g, daemon=True).start()
            return
        self.append_msg("sys", "正在等待对方接收文件：%s" % name, key=key)

        def prog(got, total):
            pct = got * 100.0 / max(total, 1)
            self.root.after(0, lambda: self.status.set(
                "发送 %s：%s / %s（%.0f%%）"
                % (name, human_size(got), human_size(total), pct)))

        def run():
            self.node.send_file(key, path, on_progress=prog)
            self.root.after(0, lambda: self.status.set("就绪"))
        threading.Thread(target=run, daemon=True).start()

    # ---------------- 事件处理 ----------------
    def on_event(self, ev):
        self.root.after(0, lambda: self.handle_event(ev))

    def handle_event(self, ev):
        t = ev.get("type")
        if t == "peer_connected":
            if ev["uuid"] not in self.msgs:
                self.msgs[ev["uuid"]] = []
            self.refresh_list()
            self.append_msg("sys", "已与 %s 建立连接" % ev.get("title"),
                            key=ev["uuid"])
            self.status.set("已连接 %s" % ev.get("title"))
        elif t == "peer_disconnected":
            self.append_msg("sys", "连接已断开：%s" % ev.get("title"),
                            key=ev["uuid"])
            self.refresh_list()
        elif t == "message":
            self.append_msg("peer", ev.get("text", ""), key=ev["uuid"])
            self.status.set("收到新消息")
        elif t == "file_offer":
            self.ask_receive(ev)
        elif t == "file_waiting":
            self.status.set("等待对方接收：%s" % ev.get("name"))
        elif t == "file_progress":
            got, total = ev.get("got", 0), ev.get("total", 1)
            self.status.set("接收 %s：%s / %s（%.0f%%）"
                            % (ev.get("name"), human_size(got),
                               human_size(total), got * 100.0 / max(total, 1)))
        elif t == "file_result":
            ok = ev.get("ok")
            self.append_msg("ok" if ok else "err", ev.get("msg", ""),
                            key=ev["uuid"])
            self.status.set("就绪")
        elif t == "error":
            self.append_msg("err", ev.get("msg", ""),
                            key=self.current or "__sys__")
            self.status.set("就绪")
        elif t == "group_invited":
            key = gkey(ev["group_id"])
            self.msgs.setdefault(key, [])
            self.refresh_list()
            self.append_msg("sys", "%s 把你拉进群聊「%s」（%d 人）"
                            % (ev.get("by_name") or "对方",
                               ev.get("name"), ev.get("count", 0)), key=key)
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
                self.status.set("群聊新消息：%s" % who)
        elif t == "group_member":
            key = gkey(ev["group_id"])
            self.refresh_list()
            if ev.get("left"):
                self.append_msg("sys", "%s 退出了群聊"
                                % (ev.get("left_name") or "有人"), key=key)
            elif ev.get("added"):
                self.append_msg("sys", "新增 %d 位成员，现在共 %d 人"
                                % (len(ev["added"]), ev.get("count", 0)),
                                key=key)
        elif t == "group_left":
            self.refresh_list()

    def ask_receive(self, ev):
        from tkinter import messagebox, filedialog
        dft = os.path.join(os.path.expanduser("~"), "下载")
        if not os.path.isdir(dft):
            dft = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.isdir(dft):
            dft = os.path.expanduser("~")
        name = ev.get("name")
        size = human_size(ev.get("size", 0))
        ok = messagebox.askyesno(
            "收到文件",
            "对方（%s）向你发送文件：\n\n%s\n大小：%s\n\n是否接收？"
            % (ev.get("title"), name, size))
        if not ok:
            self.node.reject_file(ev["uuid"], ev["file_id"])
            self.append_msg("err", "已拒绝接收 %s" % name, key=ev["uuid"])
            return
        path = filedialog.asksaveasfilename(title="保存到",
                                           initialdir=dft, initialfile=name)
        if not path:
            self.node.reject_file(ev["uuid"], ev["file_id"])
            self.append_msg("err", "已取消接收 %s" % name, key=ev["uuid"])
            return
        self.append_msg("sys", "开始接收：%s" % name, key=ev["uuid"])
        self.node.accept_file(ev["uuid"], ev["file_id"], path)

    def on_close(self):
        try:
            self.save_nick()
        except Exception:
            pass
        try:
            self.node.stop()
        except Exception:
            pass
        self.root.destroy()


# =============================================================================
#  自检：无需图形界面，验证文字消息与文件传输
# =============================================================================
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

    print("-" * 58)
    print("说明：本程序为纯本地点对点通信，不连接任何外部服务器。")
    print("      若系统安全软件弹出联网/监听询问，请选择「允许」")
    print("      （它需要监听端口供对方连入，这是正常工作方式）。")
    print("=" * 58)
    return 0


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--diag" in sys.argv:
        sys.exit(diag())
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("未检测到 tkinter，请先安装：")
        print("  UOS / deepin / Ubuntu： sudo apt install python3-tk")
        print("  CentOS / 麒麟：         sudo yum install python3-tkinter")
        sys.exit(1)
    import tkinter as tk
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except Exception:
        pass
    app = ChatUI(root)
    print("[欸~你听说没~] 界面已启动，监听端口 %d。"
          "如果看不到窗口，请把本窗口的报错信息反馈。" % app.node.port)
    root.mainloop()


if __name__ == "__main__":
    main()
