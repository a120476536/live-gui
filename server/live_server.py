#!/usr/bin/env python3
# coding: utf-8
"""对外 WS 暴露：把 WebSocketServerD 抓到的事件原样广播给本机浏览器 / python 脚本。

单进程、单数据源、单端口：

    WebSocketServerD.DouyinLiveWebFetcher（唯一抓取器，连抖音 wss 的出站客户端）
        └─ dyDataReceiver
             ├─ liveMain.onDyDataReceiver   -> 界面显示 / 语音 / 小票打印
             └─ LiveFetcherService         -> LiveWebSocketServer.broadcast
                                          -> ws://127.0.0.1:8765（对外服务端）

注意：抖音那条 wss 是「出站客户端」，8765 是「入站服务端」，两者角色不同，
但都在同一个进程里、共用唯一一个抓取器、只暴露唯一一个端口。

这里不再有 EventBus / HTTP 查询接口 / GUI 配置同步 / 玩法过滤 / 空闲补发：
抓到什么就原样推什么，由消费端自己解析。
"""

# ---------- 让项目根目录进入 sys.path（支持 python server/live_server.py 直接运行） ----------
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

# ---------- 控制台/日志编码兜底（必须早于所有业务模块 import） ----------
# WebSocketServerD / SerialManager 等在导入期就调 logging.basicConfig()，
# 不先加固的话 GBK 控制台写 emoji 昵称会刷「--- Logging error ---」。
from utils.console_safe import install_safe_console
install_safe_console()

import argparse
import ast
import asyncio
import json
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import websockets
from PyQt5.QtCore import Qt
from server.WebSocketServerD import DouyinLiveWebFetcher
from core.app_state import AppState

# ---------- 对外端口：全项目唯一 ----------
WS_PORT = 8765


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class LiveWebSocketServer:
    """对外 WS 服务端：只把事件广播给所有已连接客户端。

    不读客户端消息、不缓存事件、不生成数据。broadcast() 可从任意线程调用，
    内部用 run_coroutine_threadsafe 投递到 asyncio 事件循环。
    """

    def __init__(self, host: str = "0.0.0.0", port: int = WS_PORT):
        self.host, self.port = host, port
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server = None
        self._ready = threading.Event()
        self._clients = set()
        self._serve_error: Optional[str] = None

    # ---------- 对外接口 ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._serve_error = None
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="realtime-ws-server")
        self._thread.start()
        self._ready.wait(timeout=3)

    def wait_ready(self, timeout: float = 8.0) -> bool:
        self._ready.wait(timeout)
        return self._serve_error is None

    @property
    def serve_error(self) -> Optional[str]:
        return self._serve_error

    def broadcast(self, event: Dict[str, Any]) -> None:
        """线程安全广播一条事件；未就绪或没有客户端时直接丢弃。"""
        loop = self._loop
        if loop is None or not loop.is_running() or not self._clients:
            return
        try:
            payload = json.dumps(event, ensure_ascii=False)
        except Exception:
            return

        async def _send():
            for ws in list(self._clients):
                try:
                    await ws.send(payload)
                except Exception:
                    pass

        try:
            asyncio.run_coroutine_threadsafe(_send(), loop)
        except Exception:
            pass

    def stop(self) -> None:
        """踢掉所有客户端、关闭监听、释放端口。可重复调用。"""
        loop, server = self._loop, self._server
        if loop is not None and loop.is_running():
            async def _shutdown():
                for ws in list(self._clients):
                    try:
                        await ws.close(code=1001, reason="server shutting down")
                    except Exception:
                        pass
                self._clients.clear()
                if server is not None:
                    server.close()
            try:
                fut = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
                fut.result(timeout=3)
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        self._loop = None
        self._server = None
        logging.info("WebSocket server stopped")

    # ---------- 内部 ----------
    async def _handler(self, websocket):
        self._clients.add(websocket)
        try:
            await websocket.send(json.dumps(
                {"source": "系统", "message": "connected", "timestamp": now_iso()},
                ensure_ascii=False))
            await websocket.wait_closed()
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)

    async def _serve(self) -> None:
        self._server = await websockets.serve(
            self._handler, self.host, self.port,
            ping_interval=20, ping_timeout=20, max_queue=256,
            reuse_port=False,          # 独占端口：重复启动直接失败，不静默共享
        )
        logging.info("WebSocket server started: ws://%s:%s", self.host, self.port)
        self._ready.set()
        await self._server.wait_closed()

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except RuntimeError as exc:
            # stop() 调 loop.stop 的正常产物，不是故障，不要打 ERROR traceback
            if "Event loop stopped before Future completed" not in str(exc):
                self._serve_error = str(exc)
                logging.exception("WebSocket server crashed: %s", exc)
        except OSError as exc:
            self._serve_error = str(exc)
            logging.error("WebSocket 启动失败：端口 %s 无法绑定（%s）", self.port, exc)
        except Exception as exc:
            self._serve_error = str(exc)
            logging.exception("WebSocket server crashed: %s", exc)
        finally:
            try:
                if self._server:
                    self._server.close()
                    self._loop.run_until_complete(self._server.wait_closed())
            except Exception:
                pass
            self._ready.set()
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None
            self._server = None


class LiveFetcherService:
    """一个 DouyinLiveWebFetcher + 一个回调：抓到什么就原样交给回调。"""

    def __init__(self, room_id: str, cookie: str = "", on_event=None):
        self.room_id = room_id
        self.on_event = on_event
        self.fetcher = DouyinLiveWebFetcher(room_id, cookie_string=cookie)
        # DirectConnection：在抓取线程内同步回调，不依赖 Qt 事件循环
        self.fetcher.dyDataReceiver.connect(self._on_message, Qt.DirectConnection)
        self._thread: Optional[threading.Thread] = None

    def _on_message(self, text: str) -> None:
        # 真实抖音事件已改为 JSON 序列化（见 WebSocketServerD.action），
        # 这里优先 json.loads；仍兼容旧 repr 格式（兜底）。
        try:
            obj = json.loads(text)
            event = obj if isinstance(obj, dict) else {"source": "系统", "message": text}
        except Exception:
            try:
                obj = ast.literal_eval(text)
                event = obj if isinstance(obj, dict) else {"source": "系统", "message": text}
            except Exception:
                event = {"source": "系统", "message": text}
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                logging.exception("推送事件失败")
        logging.info("[事件][%s] %s", event.get("source", "未知"), event)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="dy-fetcher")
        self._thread.start()

    def _run(self) -> None:
        try:
            self.fetcher.start()
        except Exception as exc:
            logging.exception("Live fetcher exited: %s", exc)

    def stop(self) -> None:
        try:
            self.fetcher.stop()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None


# ---------- 独立运行入口（不带 GUI，仍只暴露 8765） ----------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Douyin live WebSocket push (raw events)")
    p.add_argument("--room-id", default="")
    p.add_argument("--cookie", default="")
    p.add_argument("--cookie-file", default="")
    p.add_argument("--ws-host", default="0.0.0.0")
    p.add_argument("--ws-port", type=int, default=WS_PORT,
                   help="兼容旧命令；对外 WS 端口固定 %d，不允许改成其他值" % WS_PORT)
    p.add_argument("--gui-offline-exit", type=float, default=0.0,
                   help="已废弃（GUI 配置同步已移除），保留仅为兼容旧命令")
    return p.parse_args()


def _resolve_cookie(cookie: str, cookie_file: str) -> str:
    if cookie_file:
        try:
            with open(cookie_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            logging.error("读取 cookie-file 失败: %s", e)
    return (cookie or "").strip()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if args.ws_port != WS_PORT:
        logging.error("对外 WS 端口固定为 %d，不允许使用 %d", WS_PORT, args.ws_port)
        sys.exit(2)
    room_id = args.room_id.strip() or input("请输入抖音直播间 room-id: ").strip()
    cookie = _resolve_cookie(args.cookie, args.cookie_file)
    # 独立运行没有 GUI，锁死本机语音播报
    for setter in (AppState.set_show_gift_voice, AppState.set_show_msg_voice,
                   AppState.set_show_like_voice, AppState.set_show_follow_voice,
                   AppState.set_show_enter_voice, AppState.set_high_value_priority_voice):
        setter(False)

    ws = LiveWebSocketServer(host=args.ws_host, port=WS_PORT)
    ws.start()
    if not ws.wait_ready(timeout=8) or ws.serve_error is not None:
        logging.error("WS 服务启动失败（%s）", ws.serve_error)
        sys.exit(3)
    service = LiveFetcherService(room_id=room_id, cookie=cookie, on_event=ws.broadcast)
    service.start()
    logging.info("实时推送已就绪：ws://%s:%s（原样转发）", args.ws_host, WS_PORT)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        ws.stop()


if __name__ == "__main__":
    main()
