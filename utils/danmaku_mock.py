"""
utils/danmaku_mock.py —— 弹幕/礼物 模拟数据源（无真实直播间时联调用）

把「模拟的直播间参与数据」经 ws://127.0.0.1:8765 注入到 live_server 的事件总线，
于是 MUGEN 驱动桥(mugen_bridge.py)、小票页等所有 WS 客户端都会实时收到，等同真实弹幕。

原理：live_server 的 8765 WS 现已支持「上行注入」——客户端连入后发一条含 source 字段的
JSON，服务端即 bus.publish 并广播给所有订阅者。本模块就是那个「假数据源」客户端。

用法：
  # 一次性发一条礼物 / 弹幕
  python utils/danmaku_mock.py --gift 玫瑰 --nick 张三
  python utils/danmaku_mock.py --chat "666" --nick 李四

  # 持续随机刷（模拟热闹直播间），默认每 2 秒一条，Ctrl+C 退出
  python utils/danmaku_mock.py --demo

  # 当类库使用
  from utils.danmaku_mock import DanmakuMockSender
  async with DanmakuMockSender() as s:
      await s.send_gift("玫瑰")
      await s.send_chat("888")
"""

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
from pathlib import Path

# 允许以脚本直接运行（把项目根加入 sys.path，便于 import utils / server 等）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import websockets
except ImportError:
    print("缺少 websockets 库，请先安装：pip install websockets")
    sys.exit(1)

# 强制绕过系统代理：开 VPN 时系统设了 http_proxy，websockets 默认走代理，
# 连本机 127.0.0.1:8765 会被代理返回 502。这里清掉代理环境变量并显式 proxy=None。
for _k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_k, None)

# 默认 WS 地址（与 live_server 下行推送端口一致）
DEFAULT_WS = "ws://127.0.0.1:8765"

# 常用礼物名（与 data/mugen_config.json 的 gifts key 对应，方便直接驱动 MUGEN）
GIFTS = ["小红心", "啤酒", "粉丝团灯牌", "玫瑰", "鲜花", "棒棒糖"]
CHATS = ["666", "888", "667", "889", "上啊", "加油"]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class DanmakuMockSender:
    """连 8765，把模拟弹幕/礼物事件注入 live_server 总线。"""

    def __init__(self, ws_url: str = DEFAULT_WS, timeout: float = 5.0):
        self.ws_url = ws_url
        self.timeout = timeout
        self._ws = None
        self._drain_task = None

    # ---- 上下文管理器（with / async with）----
    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    # ---- 连接管理 ----
    async def connect(self):
        # ws:// 直连本机，websockets 仅对 http(s) scheme 走代理，故 127.0.0.1 不受影响
        self._ws = await asyncio.wait_for(
            websockets.connect(self.ws_url, proxy=None), self.timeout)
        # 后台持续读取服务端推送（含首条 connected 通知、以及我们自己的回显），
        # 丢弃即可，避免接收窗口被回显填满导致服务端对本次连接 send 背压。
        self._drain_task = asyncio.ensure_future(self._drain())
        return self

    async def close(self):
        if self._ws is not None:
            if self._drain_task is not None:
                self._drain_task.cancel()
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _drain(self):
        try:
            async for _ in self._ws:
                pass
        except Exception:
            pass

    # ---- 发送 ----
    async def send_event(self, event: dict):
        if self._ws is None:
            await self.connect()
        await self._ws.send(json.dumps(event, ensure_ascii=False))

    async def send_gift(self, gift_name: str, nick: str = "模拟观众", count: int = 1):
        await self.send_event({
            "source": "礼物",
            "nickName": nick,
            "giftName": gift_name,
            "count": count,
            "timestamp": now_iso(),
        })

    async def send_chat(self, msg: str, nick: str = "模拟观众"):
        await self.send_event({
            "source": "聊天",
            "nickName": nick,
            "msg": msg,
            "timestamp": now_iso(),
        })

    async def send_like(self, nick: str = "模拟观众"):
        await self.send_event({"source": "点赞", "nickName": nick, "timestamp": now_iso()})

    async def send_enter(self, nick: str = "模拟观众"):
        await self.send_event({"source": "进场", "nickName": nick, "timestamp": now_iso()})

    async def send_follow(self, nick: str = "模拟观众"):
        await self.send_event({"source": "关注", "nickName": nick, "timestamp": now_iso()})

    # ---- 演示：持续随机刷 ----
    async def run_demo(self, interval: float = 2.0, duration: float = None):
        end = time.time() + duration if duration else None
        print(f"[mock] 开始模拟发送（每 {interval}s 一条），Ctrl+C 停止...")
        n = 0
        try:
            while end is None or time.time() < end:
                n += 1
                if random.random() < 0.6:
                    g = random.choice(GIFTS)
                    await self.send_gift(g, nick=f"观众{n}")
                    print(f"  -> 礼物: {g} (观众{n})")
                else:
                    c = random.choice(CHATS)
                    await self.send_chat(c, nick=f"观众{n}")
                    print(f"  -> 弹幕: {c} (观众{n})")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        print(f"[mock] 共发送 {n} 条模拟事件")


def main():
    p = argparse.ArgumentParser(description="弹幕/礼物 模拟数据源（注入 8765 总线）")
    p.add_argument("--ws", default=DEFAULT_WS, help="live_server WS 地址")
    p.add_argument("--gift", help="发一条礼物（礼物名，如 玫瑰）")
    p.add_argument("--chat", help="发一条弹幕（内容，如 666）")
    p.add_argument("--nick", default="模拟观众", help="观众昵称")
    p.add_argument("--demo", action="store_true", help="持续随机刷（默认模式）")
    p.add_argument("--interval", type=float, default=2.0, help="demo 间隔秒")
    p.add_argument("--duration", type=float, default=None, help="demo 持续秒（默认无限）")
    args = p.parse_args()

    async def _run():
        async with DanmakuMockSender(args.ws) as s:
            if args.gift:
                await s.send_gift(args.gift, nick=args.nick)
                print(f"已发送礼物: {args.gift}")
            elif args.chat:
                await s.send_chat(args.chat, nick=args.nick)
                print(f"已发送弹幕: {args.chat}")
            else:
                await s.run_demo(interval=args.interval, duration=args.duration)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n[mock] 已停止")


if __name__ == "__main__":
    main()
