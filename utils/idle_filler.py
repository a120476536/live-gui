# -*- coding: utf-8 -*-
"""空闲补偿发送器（Idle Filler）。

参考 server/live_server.py 的「空闲随机补发」(RandomFiller) 逻辑，独立成一个
不依赖 PyQt / Qt 的纯逻辑模块，供小票模拟 / 手机预览接入：

- 当超过空闲阈值秒数没有「真实数据」到达时，触发一次补偿发送；
- 空闲阈值支持随机区间（idle_seconds_range=(30,60)）：每一轮间隔在 30~60s 内随机，
  避免机械式固定节拍被平台识别为脚本；
- 补偿内容默认是随机生成的一条互动事件（进场 / 关注 / 点赞 / 粉丝 / 普通聊天 /
  玩法聊天；礼物除外——礼物必须真实，不伪造打赏）。玩法聊天会复用
  core.printer_manager.CHAT_STYLES，拼出必命中某玩法的弹幕，使补偿画面里频繁出现
  隐藏玩法小票，内容更丰富。字段与 page.receipt_simulator._build_ticket 的契约一致
  （source / nickName / goodCount / msg）；
- 仅在 is_active() 返回 True 时（例如预览窗口可见）才补发；
- 触发一次后重置计时，下一轮重新抽取随机空闲窗口。

使用方负责把「真实数据到达」通过 mark_activity() 上报，把「补偿发送」通过
on_fill 回调接管（通常再切回主线程去操作 UI）。
"""

import logging
import random
import threading
import time


# ---- 空闲检测参数（与原 live_server 一致）----
DEFAULT_IDLE_SECONDS = 60      # 无真实数据超过该秒数则触发一次补偿
DEFAULT_CHECK_INTERVAL = 5     # 轮询间隔（秒）


# 随机昵称池（补偿小票用，风格与 live_server 的 RandomFiller 一致）
_RANDOM_SURNAMES = list("王李张刘陈杨赵黄周吴")
_RANDOM_GIVEN = ["伟", "芳", "娜", "秀英", "敏", "静", "磊", "洋", "艳", "勇", "军", "杰"]
_RANDOM_NICKS = ["风一样", "小可爱", "夜未央", "猫不吃鱼", "追梦人",
                 "一杯敬自由", "咸鱼翻身", "不羁的风", "抹茶星冰乐", "霁云",
                 "枫叶红了", "一只柴犬", "云端漫步", "半糖去冰", "深海鲸落",
                 "晚风温柔", "果冻星球", "柠檬不萌", "糖炒栗子", "南风知我意"]

# 普通聊天语料池（补偿的普通弹幕；玩法弹幕另从 CHAT_STYLES 生成，保证一致）
_RANDOM_CHATS = ["主播讲得真好", "哈哈哈太有意思了", "这操作可以啊", "蹲一个更新",
                 "刚来，错过了啥", "声音好好听", "已三连支持", "学到了学到了",
                 "这波稳了", "求连麦", "氛围组已就位", "笑不活了", "主播加油鸭"]

# 补偿可出现的互动类型（不含礼物——礼物必须真实，不伪造打赏）。
# 玩法聊天单独占一股权重，让画面更常出现隐藏玩法小票，内容更丰富。
_INTERACTION_TYPES = ("进场", "关注", "点赞", "粉丝", "聊天", "聊天玩法", "聊天玩法")


def _rand_name() -> str:
    return random.choice(_RANDOM_SURNAMES) + random.choice(_RANDOM_GIVEN)


def _rand_play_msg():
    """从 printer_manager 的 CHAT_STYLES 抽一个玩法，拼出该玩法的弹幕文案。

    返回 (msg, key)；无玩法配置时返回 None。key 用于在渲染端精确匹配玩法，
    避免弹幕恰好命中多个玩法关键字时被分类器归到别的玩法。
    """
    try:
        from core.printer_manager import CHAT_STYLES
    except Exception:
        return None
    if not CHAT_STYLES:
        return None
    style = random.choice(CHAT_STYLES)
    kws = style.get("keywords") or ["互动"]
    kw = random.choice(kws)
    flavor = random.choice(["这个我必须参与一下", "安排！", "来啦", "有点意思",
                            "这就安排上", "已就位", "坐等", "必须支持", "冲冲冲"])
    return "%s，%s" % (kw, flavor), style.get("key", "")


def random_interaction_event() -> dict:
    """随机生成一条互动补偿事件（不含礼物）。

    字段与 page.receipt_simulator._build_ticket 的契约一致：
    source / nickName / goodCount（仅点赞）/ msg（聊天 / 粉丝）/ chat_style_key（玩法弹幕）。

    - 含「进场 / 关注 / 点赞 / 粉丝 / 普通聊天」；
    - 额外有较高概率出「玩法聊天」：从 printer_manager 的 CHAT_STYLES 抽一个玩法，
      拼出该玩法弹幕并带上 key，使补偿画面里频繁出现隐藏玩法小票，内容更丰富。
    """
    etype = random.choice(_INTERACTION_TYPES)
    nick = random.choice(_RANDOM_NICKS + [_rand_name()])
    if etype == "聊天玩法":
        res = _rand_play_msg()
        if res:
            msg, key = res
            return {"source": "聊天", "nickName": nick,
                    "msg": msg, "chat_style_key": key}
        etype = "聊天"          # 兜底：玩法配置缺失则退化为普通聊天
    evt = {"source": etype, "nickName": nick}
    if etype == "点赞":
        evt["goodCount"] = random.randint(1, 99)
    elif etype == "粉丝":
        evt["msg"] = random.choice(["", "守护主播第99天", "灯牌已点亮", "冲鸭"])
    elif etype == "聊天":
        evt["msg"] = random.choice(_RANDOM_CHATS)
    return evt


class IdleFiller:
    """空闲补偿发送器：计时 + 触发回调。

    后台 daemon 线程按 check_interval 轮询；当距上次真实数据（mark_activity）
    超过 idle_seconds，且 is_active() 为真（或 is_active 为 None），
    则调用 on_fill()。触发后重置计时。
    """

    def __init__(self, on_fill, idle_seconds=DEFAULT_IDLE_SECONDS,
                 idle_seconds_range=None, check_interval=DEFAULT_CHECK_INTERVAL,
                 is_active=None):
        if not callable(on_fill):
            raise TypeError("on_fill 必须是可调用对象（函数 / 绑定方法 / lambda）")
        self._on_fill = on_fill
        self._is_active = is_active
        self.idle_seconds = idle_seconds
        # 随机空闲窗口：传入 (lo, hi) 时，每一轮补偿间隔在上限内随机，避免机械式固定节拍
        self._idle_range = None
        if idle_seconds_range:
            lo, hi = idle_seconds_range
            self._idle_range = (min(lo, hi), max(lo, hi))
        self._reset_idle_window()
        self.check_interval = check_interval
        self._lock = threading.Lock()
        self._last = time.time()
        self._stop_evt = threading.Event()
        self._thread = None

    def _reset_idle_window(self):
        """重新抽取本轮的空闲阈值（仅当设置了随机区间时生效）。"""
        if self._idle_range:
            lo, hi = self._idle_range
            self.idle_seconds = random.uniform(lo, hi)

    # ---------- 计时 ----------
    def mark_activity(self) -> None:
        """真实数据到达时调用，刷新最近活跃时间。线程安全。"""
        with self._lock:
            self._last = time.time()

    def seconds_since_activity(self) -> float:
        with self._lock:
            return time.time() - self._last

    # ---------- 触发判断 ----------
    def _should_emit(self) -> bool:
        if self.seconds_since_activity() < self.idle_seconds:
            return False
        # 窗口不可见 / 无接收方时不补发（保持空闲计时，待可见时立即补一次）
        if self._is_active is not None and not self._is_active():
            return False
        return True

    # ---------- 后台线程 ----------
    def _run(self) -> None:
        while not self._stop_evt.is_set():
            if self._should_emit():
                try:
                    self._on_fill()
                except Exception:
                    logging.exception("[idle_filler] 补偿发送回调失败")
                # 触发后重置计时：下一次需再空闲 idle_seconds 秒或期间无真实命中
                self.mark_activity()
                # 重新抽取下一轮的随机空闲窗口（如设置了随机区间）
                self._reset_idle_window()
            self._stop_evt.wait(self.check_interval)

    def start(self) -> None:
        """启动后台轮询线程（已运行则忽略）。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="idle-filler")
        self._thread.start()

    def stop(self) -> None:
        """停止后台线程并等待退出。"""
        self._stop_evt.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=1.0)
        self._thread = None


if __name__ == "__main__":
    # 简单自测：5 秒无数据则打印一次补偿事件，验证计时与随机生成
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    seen = []

    def on_fill():
        evt = random_interaction_event()
        seen.append(evt)
        logging.info("补偿事件: %s", evt)

    filler = IdleFiller(on_fill, idle_seconds=5, check_interval=1, is_active=lambda: True)
    filler.start()
    logging.info("启动，5 秒后将触发第一次补偿（之后每 5 秒一次）……按 Ctrl+C 退出")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        filler.stop()
        logging.info("已停止，共补偿 %d 次", len(seen))
        sys.exit(0)
