#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vjoy_driver.py —— 用 vJoy 虚拟手柄把招式指令直接发进 MUGEN。

为什么用 vJoy（而不是 AHK / pynput / SendInput）：
  MUGEN 1.1 用 SDL 读键盘，走 RawInput，会过滤所有"注入"的键盘事件，
  所以任何模拟键盘的方案（AHK 四种 Send 模式、pynput、SendInput）全失败。
  vJoy 是 DirectInput 虚拟手柄，MUGEN 把它当真实手柄收，输入走设备级
  DirectInput 流，RawInput 根本拦不到；而且设备级输入不需要 MUGEN 有焦点。

用法：
  from vjoy_driver import VJoyDriver
  vj = VJoyDriver(p1_dev=1, p2_dev=2)
  vj.play("A", "D R -D x -x -R")   # 八神庵暗拂 ↓↘→ + X

逻辑键 -> 手柄按钮号（必须与 mugen.cfg 的 [P1]/[P2 Joystick] 段一致）：
  U=1 上/跳  D=2 下/蹲  L=3 左  R=4 右
  a=5 b=6 c=7 x=8 y=9 z=10 start=11
（八神庵招式统一用 x 键触发，对应 mugen.cfg 里的 X 键）

DSL 序列：空格分隔的 token。普通 token = 按下并保持；'-' + 键名 = 松开。
  例如 "D R -D x -x -R"：
    按 D(下) -> 按 R(右, 此时 D+R=↘) -> 松 D(留 R=→) -> 按 x(攻击, R 仍按) -> 松 x -> 松 R
  每个 token 之间 sleep step_ms，让 MUGEN 的指令缓冲识别斜向/招式。

依赖：本机装好 vJoy 驱动 + pip install pyvjoy
"""
import time

try:
    import pyvjoy
    HAVE_VJOY = True
except Exception:
    HAVE_VJOY = False

# 逻辑键名 -> joystick 按钮号（1-based，与 mugen.cfg [P1]/[P2 Joystick] 一致）
BUTTONS = {
    "U": 1, "D": 2, "L": 3, "R": 4,
    "a": 5, "b": 6, "c": 7, "x": 8, "y": 9, "z": 10, "start": 11,
}


class VJoyError(Exception):
    pass


class VJoyDriver:
    def __init__(self, p1_dev=1, p2_dev=2, hold_ms=45, step_ms=45):
        if not HAVE_VJOY:
            raise VJoyError(
                "pyvjoy 未安装。请在本机执行: pip install pyvjoy "
                "(用系统 Python312: %LOCALAPPDATA%\\Programs\\Python\\Python312\\python.exe -m pip install pyvjoy)"
            )
        self.hold = hold_ms / 1000.0
        self.step = step_ms / 1000.0
        self._dev = {}
        self._pressed = {}
        for player, dev_id in (("A", p1_dev), ("B", p2_dev)):
            try:
                self._dev[player] = pyvjoy.VJoyDevice(dev_id)
            except Exception as e:
                raise VJoyError(
                    "无法打开 vJoy 设备 %d (%s 用)。请确认已装 vJoy 驱动、"
                    "Configure vJoy 里启用了该设备、且按钮数 >= 11。错误: %s"
                    % (dev_id, "P1" if player == "A" else "P2", e)
                )
            self._pressed[player] = set()
        self.p1_dev = p1_dev
        self.p2_dev = p2_dev

    def play(self, player, seq):
        """player: 'A' 或 'B'；seq: 逻辑 DSL 字符串。返回 True/False。"""
        dev = self._dev.get(player)
        if dev is None:
            return False
        pressed = self._pressed[player]
        tokens = seq.split()
        for tok in tokens:
            if tok.startswith("-"):
                key = tok[1:]
                btn = BUTTONS.get(key)
                if btn is not None:
                    dev.set_button(btn, 0)
                    pressed.discard(key)
            else:
                btn = BUTTONS.get(tok)
                if btn is not None:
                    dev.set_button(btn, 1)
                    pressed.add(tok)
            time.sleep(self.step)
        # 兜底：松开本招式残留未按下的键，避免卡键
        for key in list(pressed):
            btn = BUTTONS.get(key)
            if btn is not None:
                dev.set_button(btn, 0)
            pressed.discard(key)
        return True

    def release_all(self, player=None):
        players = [player] if player else list(self._dev.keys())
        for p in players:
            dev = self._dev.get(p)
            if dev is None:
                continue
            for key in list(self._pressed[p]):
                btn = BUTTONS.get(key)
                if btn is not None:
                    dev.set_button(btn, 0)
                self._pressed[p].discard(key)

    def close(self):
        self.release_all()
        for dev in self._dev.values():
            try:
                dev.close()
            except Exception:
                pass


def test():
    """最小自测：连上后依次按每个按钮再松开（用于验证 vJoy 安装）。"""
    vj = VJoyDriver()
    for player in ("A", "B"):
        for key, btn in BUTTONS.items():
            vj._dev[player].set_button(btn, 1)
            time.sleep(0.05)
            vj._dev[player].set_button(btn, 0)
            time.sleep(0.05)
        dev_id = vj.p1_dev if player == "A" else vj.p2_dev
        print("device %d (%s) OK" % (dev_id, player))
    vj.close()


if __name__ == "__main__":
    test()
