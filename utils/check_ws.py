#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
check_ws.py —— 本机「小票页连不上 / 数据过不来」一键体检（Windows / macOS 通用，新增文件，不改主逻辑）

用法（在 live-gui 目录下，用项目的 python 运行）：
    python check_ws.py

会依次体检：
  1. 本机 8765 是否有进程在听、能否真正完成 WebSocket 握手
  2. 8765 端口的监听进程
  3. live_server_gui.log 尾部（GUI 拉起 live_server 的日志，含失败/自杀原因）
  4. GUI 的 8766 /live-config 是否可达（本机 HTTP 配置接口，非 WS）
  5. liveHelper(webman) 8790 是否在听
  6. 给出小票页当前应该连的 WS 地址

把完整输出贴给开发者即可定位。
"""
import json
import os
import socket
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(BASE_DIR, "live_server_gui.log")
PORTS = {
    "对外 WS(全项目唯一，GUI 进程内)": 8765,
    "GUI 本机 HTTP 配置接口(非 WS)": 8766,
    "liveHelper 页面服务(外部 webman，非本项目 WS)": 8790,
}
# 全项目只有 8765 一个 WS 监听端口，数据来自 GUI 进程内的 WebSocketServerD；
# 外部浏览器 / python 脚本直连 ws://127.0.0.1:8765 即可。


def line(title=""):
    print("\n" + "=" * 62)
    if title:
        print(title)
        print("=" * 62)


def port_listen(port):
    """端口是否有进程在监听（TCP connect 探测）。"""
    s = socket.socket()
    s.settimeout(0.6)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def ws_handshake(port, timeout=2.5):
    """真正做一次 WebSocket 握手（端口在听 ≠ 能连）。"""
    try:
        import asyncio

        import websockets

        async def _probe():
            async with websockets.connect("ws://127.0.0.1:%d" % port, open_timeout=timeout) as ws:
                # live_server 连上后会立刻推一条 {"source":"系统","message":"connected"}
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except Exception:
                    msg = "(连上但 2s 内没收到数据)"
                return msg

        return asyncio.run(_probe())
    except ImportError:
        return "SKIP: 当前 python 没装 websockets"
    except Exception as e:
        return "FAIL: %r" % e


def list_procs():
    """列出与本项目相关的进程。"""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe"],
                                 capture_output=True, text=True, timeout=8).stdout
            out += subprocess.run(["tasklist", "/FI", "IMAGENAME eq pythonw.exe"],
                                  capture_output=True, text=True, timeout=8).stdout
            return out.strip()
        out = subprocess.run(["pgrep", "-fl", "live_server.py|page/liveMain.py|server.live_server|liveMain.py"],
                             capture_output=True, text=True, timeout=8)
        return (out.stdout or "").strip() or "(无)"
    except Exception as e:
        return "(获取失败: %s)" % e


def tail_log(n=40):
    if not os.path.exists(LOG_FILE):
        return "(不存在: %s)" % LOG_FILE
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except Exception as e:
        return "(读取失败: %s)" % e


def http_get(url, timeout=2.0):
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")[:400]
    except Exception as e:
        return "不可达: %r" % e


def main():
    print("live-gui 本机链路体检 —— %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("目录: %s" % BASE_DIR)
    print("python: %s" % sys.executable)

    line("1) 端口监听情况")
    for name, p in PORTS.items():
        print("  %-32s %5d  %s" % (name, p, "在听" if port_listen(p) else "没在听"))

    line("2) 8765 真实 WebSocket 握手")
    print("  %s" % ws_handshake(8765))

    line("3) 相关进程")
    print(list_procs())

    line("4) GUI 配置接口 8766 /live-config（本机 HTTP，非 WS）")
    print("  %s" % http_get("http://127.0.0.1:8766/live-config"))

    line("5) live_server_gui.log 尾部（历史日志）")
    print(tail_log())

    line("6) 小票页地址建议")
    print("  本机访问请用:  http://127.0.0.1:8790/live/receipt")
    print("  页面会自动连:  ws://127.0.0.1:8765")
    print("  若用局域网 IP 打开页面(如 http://192.168.x.x:8790)，页面会连 ws://192.168.x.x:8765，")
    print("  此时需确认 Windows 防火墙放行 8765 入站。")


if __name__ == "__main__":
    main()
