#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本目录发布到 GitHub（不依赖本机 git，直接走 GitHub REST API）。

准备一个 Personal Access Token（classic，勾选 repo 权限）：
  https://github.com/settings/tokens  ->  Generate new token (classic)

用法：
  export GH_TOKEN=ghp_xxxxxxxxxxxx          # Linux / macOS
  set GH_TOKEN=ghp_xxxxxxxxxxxx             # Windows CMD
  $env:GH_TOKEN="ghp_xxxxxxxxxxxx"          # Windows PowerShell

  python3 tools/publish_to_github.py                     # 默认 leifung/moyu-duck-chat
  python3 tools/publish_to_github.py --repo 别的名字
  python3 tools/publish_to_github.py --private           # 建私有仓库
  python3 tools/publish_to_github.py --dry-run           # 只看要传哪些文件
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
SKIP_DIRS = {".git", "__pycache__", ".workbuddy", "build", ".idea", ".vscode"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_opener)


def read_saved_token():
    """本机已保存的令牌：~/.workbuddy/.github_token（用户明确要求记住）"""
    p = os.path.join(os.path.expanduser("~"), ".workbuddy", ".github_token")
    try:
        if os.path.isfile(p):
            return open(p, encoding="utf-8").read().strip()
    except Exception:
        pass
    return ""


def pick_token(cands):
    """挑一个真正能用的令牌：环境变量里可能留着已吊销的旧令牌，
    而 ~/.workbuddy/.github_token 里是后来换的新令牌。"""
    for t in cands:
        t = (t or "").strip()
        if not t:
            continue
        try:
            req("GET", "/user", token=t)
            return t
        except Exception:
            continue
    return ""


def req(method, path, data=None, token=""):
    url = path if path.startswith("http") else API + path
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") \
        if data is not None else None
    r = urllib.request.Request(url, data=body, method=method)
    r.add_header("Authorization", "Bearer " + token)
    r.add_header("Accept", "application/vnd.github+json")
    r.add_header("X-GitHub-Api-Version", "2022-11-28")
    r.add_header("User-Agent", "moyu-duck-publish")
    if body:
        r.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            payload = resp.read()
            return json.loads(payload.decode("utf-8")) if payload else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        raise RuntimeError("HTTP %s %s\n%s" % (e.code, e.reason, detail))


def collect(root):
    """收集要上传的文件：[(相对路径, 模式, 绝对路径)]"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if fn in SKIP_FILES:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            mode = "100755" if fn.endswith(".sh") else "100644"
            out.append((rel, mode, full))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", default="leifung")
    ap.add_argument("--repo", default="moyu-duck-chat")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--message", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--token-file", default="")
    args = ap.parse_args()

    cands = [os.environ.get("GH_TOKEN", ""), read_saved_token()]
    if args.token_file and os.path.isfile(args.token_file):
        cands.insert(0, open(args.token_file, encoding="utf-8").read())
    token = pick_token(cands) if not args.dry_run else ""
    if not token and not args.dry_run:
        print("缺少 GitHub 令牌。请先：")
        print("  https://github.com/settings/tokens  生成 classic token（勾 repo）")
        print("  然后 export GH_TOKEN=ghp_xxx，或把令牌存到 ~/.workbuddy/.github_token")
        return 2

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = collect(root)
    print("待上传 %d 个文件：")
    for rel, mode, full in files:
        print("  %s  %-34s %8d B" % (mode, rel, os.path.getsize(full)))
    if args.dry_run:
        return 0

    full_name = "%s/%s" % (args.owner, args.repo)

    # 1) 仓库：存在就用，不存在就建
    try:
        repo = req("GET", "/repos/" + full_name, token=token)
        print("仓库已存在：%s" % repo["html_url"])
    except Exception:
        repo = req("POST", "/user/repos", token=token, data={
            "name": args.repo,
            "description": "摸鱼鸭 + 局域网点对点聊天（合并版）：收到消息时小鸭子会跑到桌面顶层把消息变成的小鱼吃掉",
            "private": bool(args.private),
            "auto_init": True,          # 先生成一个初始提交，main 分支才存在
        })
        print("已创建仓库：%s" % repo["html_url"])
        time.sleep(2)

    # 2) 拿 main 分支最新的提交
    base = None
    for _ in range(10):
        try:
            ref = req("GET", "/repos/%s/git/ref/heads/main" % full_name, token=token)
            base = ref["object"]["sha"]
            break
        except Exception:
            time.sleep(2)
    if base is None:
        raise RuntimeError("拿不到 main 分支，去网页上确认一下仓库是否创建成功")

    # 3) 逐个文件建 blob
    tree = []
    for rel, mode, full in files:
        with open(full, "rb") as f:
            content = f.read()
        blob = req("POST", "/repos/%s/git/blobs" % full_name, token=token, data={
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        })
        tree.append({"path": rel, "mode": mode, "type": "blob",
                     "sha": blob["sha"]})
        print("  blob %-34s %s" % (rel, blob["sha"][:8]))

    new_tree = req("POST", "/repos/%s/git/trees" % full_name,
                   token=token, data={"tree": tree})
    msg = args.message or ("""feat: 摸鱼鸭 + 局域网聊天合并版（统信 UOS 桌面宠物 + P2P 聊天联动）

- 合并为一个进程、一个托盘图标、一套配置
- 联动：收到消息 -> 顶层弹出小鱼（气泡显示消息）-> 小鸭子跑来吃掉 -> 跑出屏幕
- 下班时间可在菜单里自定义（精确到分钟），点小鱼查看离下班还有多久
- 聊天：单聊 / 群聊 / 文件传输 / 局域网扫描，协议兼容旧版「欸~你听说没~」
- 纯 Python 标准库通信内核 + PyQt5 界面，无第三方依赖""")
    commit = req("POST", "/repos/%s/git/commits" % full_name, token=token, data={
        "message": msg, "tree": new_tree["sha"], "parents": [base]})
    req("PATCH", "/repos/%s/git/refs/heads/main" % full_name,
        token=token, data={"sha": commit["sha"]})
    print("已更新 main -> %s" % commit["sha"][:8])

    try:
        req("PUT", "/repos/%s/topics" % full_name, token=token, data={
            "names": ["uos", "deepin", "linux", "lan-chat", "p2p",
                      "desktop-pet", "pyqt5", "python"]})
    except Exception:
        pass

    print("\n完成： https://github.com/%s" % full_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
