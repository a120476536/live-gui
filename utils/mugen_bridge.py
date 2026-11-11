#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mugen_bridge.py —— live-gui 弹幕/礼物 驱动 MUGEN 拳皇对战

两条驱动路线（由启动方式决定）：

A. vJoy 虚拟手柄（精确驱动，需装驱动）：
   1. 连上 live-gui 的 WS 服务 ws://127.0.0.1:8765。
   2. 解析礼物/弹幕事件，按 data/mugen_config.json 映射成「某玩家某招式」。
   3. 通过 vJoy 虚拟手柄把招式直接发进 MUGEN（设备级 DirectInput，绕开键盘 raw-input）。
   依赖：websockets（已装）；pyvjoy（pip install pyvjoy；本机需装 vJoy 驱动）。

B. 免驱动 AI 模式（默认当前方案）：
   八神庵(Yagami) 用 MUGEN 内置 AI 自动连招/放必杀，不碰输入层、不需驱动、不需焦点。
   角色自带 AI 在 Yagami-3.cns，启动命令 -p2.ai <等级> 即激活（AILevel>0 -> var(59)）。
   此时本桥只负责解析并记录礼物/弹幕事件（日志），不精确驱动某一招——
   因为 MUGEN 1.1 没有"外部事件->角色内部状态"的通道。要"每条礼物=固定招式"请用 A。

流程（通用）：
  1. 连 WS 8765。
  2. 解析礼物事件(source=礼物) / 弹幕事件(source=聊天)。
  3. 解析成功则记录；若 vJoy 可用则同时精确驱动招式。
"""
import sys
import os
import json
import time
import asyncio
import argparse

# Windows GBK 下打印中文/emoji 可能崩，先强制 stdout/stderr 走 utf-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import websockets
except ImportError:
    sys.stderr.write("缺少 websockets 库，请先安装：pip install websockets\n")
    sys.exit(2)

# 强制绕过系统代理（开 VPN 时系统会设 http_proxy，websockets 默认走代理，
# 连本机 127.0.0.1:8765 会被代理返回 502 -> 桥连不上、无驱动、MUGEN 不动）。
for _k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_k, None)

CONFIG_PATH = os.path.join(ROOT, "data", "mugen_config.json")

_vjoy = None          # VJoyDriver 实例（延迟初始化）
_vjoy_failed = False  # vJoy 初始化失败标记（避免每帧重试）


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_vjoy(cfg):
    """延迟初始化 vJoy 驱动；失败则标记并告警，不崩溃。"""
    global _vjoy, _vjoy_failed
    if _vjoy is not None or _vjoy_failed:
        return _vjoy
    try:
        from vjoy_driver import VJoyDriver
        vj_cfg = cfg.get("vjoy", {})
        _vjoy = VJoyDriver(
            p1_dev=int(vj_cfg.get("p1_device", 1)),
            p2_dev=int(vj_cfg.get("p2_device", 2)),
            hold_ms=int(vj_cfg.get("button_hold_ms", 45)),
            step_ms=int(vj_cfg.get("step_ms", 45)),
        )
        sys.stdout.write("[bridge] vJoy 已连接 (P1=设备%d, P2=设备%d)\n"
                         % (_vjoy.p1_dev, _vjoy.p2_dev))
    except Exception as e:
        _vjoy_failed = True
        sys.stderr.write("[warn] vJoy 初始化失败：%s\n" % e)
        sys.stderr.write("        当前为 AI 模式：八神庵由角色内置 AI 自动出招，弹幕/礼物仅记录不驱动。\n")
        sys.stderr.write("        若想\"每条礼物=固定招式\"，请按 docs/vjoy_setup.md 装好 vJoy 驱动 + pyvjoy。\n")
    return _vjoy


def resolve_key(cfg, player, action):
    """player + action -> 逻辑 DSL 序列串。找不到返回 None。"""
    keys = cfg.get("keys", {}).get(player)
    if not keys:
        return None
    return keys.get(action)


_last_send = {}  # (player, action) -> 时间戳，去抖


def handle_event(cfg, evt, dry_run=False):
    """把一条 WS 事件转成 MUGEN 招式指令。返回是否产生指令。"""
    source = evt.get("source")
    if source == "礼物":
        name = (evt.get("giftName") or "").strip()
        player_action = cfg.get("gifts", {}).get(name)
        who = "礼物:%s" % name
    elif source == "聊天":
        msg = (evt.get("msg") or "").strip()
        player_action = None
        for kw, pa in cfg.get("danmaku", {}).items():
            if kw and kw in msg:
                player_action = pa
                break
        who = "弹幕:%s" % msg
    else:
        return False

    if not player_action:
        return False

    player = player_action.get("player")
    action = player_action.get("action")
    seq = resolve_key(cfg, player, action)
    if not seq:
        sys.stderr.write("[warn] 未配置招式 %s/%s（%s）\n" % (player, action, who))
        return False

    # 去抖：同一招式在 cooldown 内不再重复发
    cooldown = float(cfg.get("cooldown_ms", 200)) / 1000.0
    now = time.time()
    k = (player, action)
    if k in _last_send and (now - _last_send[k]) < cooldown:
        return False
    _last_send[k] = now

    if dry_run:
        sys.stdout.write("[dry-run] %s -> %s/%s -> 序列: %s\n" % (who, player, action, seq))
        return True

    vj = get_vjoy(cfg)
    if vj is None:
        # vJoy 未就绪：降级只记录
        sys.stdout.write("[no-vjoy] %s -> %s/%s (vJoy 未就绪，未驱动)\n" % (who, player, action))
        return False
    try:
        vj.play(player, seq)
        sys.stdout.write("[ok] %s -> %s/%s\n" % (who, player, action))
    except Exception as e:
        sys.stderr.write("[err] vJoy 驱动出错：%s\n" % e)
    return True


async def run(cfg, dry_run=False, once=False):
    ws_url = cfg.get("ws_url", "ws://127.0.0.1:8765")
    sys.stdout.write("[bridge] 连接 %s ...\n" % ws_url)
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20,
                                           proxy=None) as ws:
                sys.stdout.write("[bridge] 已连上 8765，开始接收事件（Ctrl+C 退出）\n")
                while True:
                    raw = await ws.recv()
                    try:
                        evt = json.loads(raw)
                    except Exception:
                        continue
                    if evt.get("source") == "系统":
                        continue  # 忽略 connected 通知
                    handle_event(cfg, evt, dry_run=dry_run)
                    if once:
                        return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            sys.stderr.write("[bridge] 连接断开/错误：%s，3s 后重连...\n" % e)
            await asyncio.sleep(3)


def main():
    ap = argparse.ArgumentParser(description="live-gui 弹幕/礼物 驱动 MUGEN（vJoy 虚拟手柄版）")
    ap.add_argument("--dry-run", action="store_true", help="只打印解析结果，不连 vJoy / 不连 WS")
    ap.add_argument("--once", action="store_true", help="收到第一条事件就退出（用于测试）")
    ap.add_argument("--no-vjoy", action="store_true", help="不初始化 vJoy（仅测试解析/WS）")
    ap.add_argument("--list", action="store_true", help="打印当前映射配置后退出")
    args = ap.parse_args()

    cfg = load_config()
    if args.list:
        sys.stdout.write(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
        return

    if args.dry_run:
        sys.stdout.write("[dry-run] 不连接 WS，仅测试解析。下面模拟几条事件：\n")
        samples = [
            {"source": "礼物", "nickName": "测试", "giftName": "玫瑰"},
            {"source": "聊天", "nickName": "测试", "msg": "666"},
            {"source": "聊天", "nickName": "测试", "msg": "888 上啊"},
            {"source": "礼物", "nickName": "测试", "giftName": "棒棒糖"},
        ]
        for s in samples:
            handle_event(cfg, s, dry_run=True)
        return

    if not args.no_vjoy:
        get_vjoy(cfg)  # 启动时尝试连接，失败则告警（不崩溃）

    try:
        asyncio.run(run(cfg, dry_run=False, once=args.once))
    except KeyboardInterrupt:
        sys.stdout.write("\n[bridge] 已退出\n")


if __name__ == "__main__":
    main()
