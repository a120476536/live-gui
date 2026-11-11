# -*- coding: utf-8 -*-
"""
printer_manager.py —— 58mm 热敏小票打印机封装（USB 模式，兼容 macOS / Windows）

支持按事件类型打印：聊天 / 礼物 / 点赞 / 进场 / 关注 / 粉丝团（各类型独立渲染）。

设计要点：
  1. 跨平台打印，三条路线（按可靠性排序）：
       A. 自动探测系统打印机名（推荐，默认开启 PRINTER_AUTO_DETECT）：
          在系统打印机列表里按关键字认出 58mm 热敏机，然后走系统队列。
          什么都不用填，零原生依赖，走官方驱动最稳。
       B. 手填 PRINTER_NAME（系统里已装官方 USB 驱动、能在系统打印机列表看到该名）：
           * macOS  -> 用系统 CUPS 队列 `lp -d <name> -o raw`（无需 libusb）
           * Windows-> 用 winspool 原生 API 写 RAW（ctypes，无需 pywin32）
       C. libusb 直连 `Usb(vendor_id, product_id)`（兜底，最不推荐）：
          需额外装 pyusb + libusb；Windows 上还要用 Zadig 把驱动换成 WinUSB，
          换完官方驱动失效、别的软件打不了，且 0x0483 等 VID 是通用芯片号可能抓错设备。
  2. 中文：统一用 GB18030 码页（58mm 热敏机普遍只认 GBK/GB18030，不认 UTF-8）。
  3. 不卡 UI：打印在独立守护线程里顺序消费（USB 同一时刻只连一个），主线程只入队。
  4. 防刷：同一去重键（dedup_key）DEDUP_SECONDS 秒内只打一次，避免连击狂刷小票。

依赖：python-escpos、pyusb（均已加入 requirements.txt）。A/B 路线 macOS/Windows 均无需
      额外原生依赖；仅 C 路线（libusb 直连）还需系统级 libusb 原生库
      （Windows: libusb-1.0.dll；macOS: brew install libusb）——pyusb 只是 Python
      绑定层、**不含**该库，所以只装 pyusb 会直接报 NoBackendError。
"""

import sys
import os
import re
import time
import itertools
import queue
import threading
import tempfile
import logging
import subprocess
import unicodedata
import hashlib
import json
from datetime import datetime

# ========================= 配置区（按需修改） =========================
# 当前机型：芯烨（Xprinter）经济性 58TS —— 标准 ESC/POS，58mm 热敏，USB 接口。
#   代码已按 58mm（每行约 16 个汉字）排版，无需改渲染逻辑。
#
# 三种接法（任选其一，按可靠性排序）：
#   ★ A. 全自动（最省事，默认就是这个）：
#        什么都不用填。只要 58TS 已经装好官方驱动、出现在系统打印机列表里，
#        程序会用 PRINTER_NAME_KEYWORDS 里的关键字自动认出它。
#        macOS / Windows 都适用，走官方驱动，最稳。
#   B. 手填系统打印机名（自动认不出来时用这个）：
#        - macOS：接上 58TS，在「系统设置 → 打印机与扫描仪」添加，
#                  选「使用：Xprinter / 通用 PostScript」或官方驱动，记下名字（如 "XP-58"）。
#        - Windows：在「设备和打印机」里添加 58TS（装官方驱动），记下名字。
#        - 然后把这个名字填到下面的 PRINTER_NAME。
#   C. libusb 直连（兜底，不推荐）：
#        - pyusb 已随 requirements.txt 装好，不用再装；
#        - 但还需**系统级 libusb 原生库**：Linux/macOS `apt/brew install libusb`；
#          Windows 放 libusb-1.0.dll（pyusb 只是绑定层，wheel 里不含该库）；
#        - Windows 还必须用 Zadig 把打印机驱动换成 WinUSB —— 换完官方驱动失效，
#          别的软件（收银机/POS 系统）会打不了。所以只在「没装官方驱动、也不想装」时用。
#        - 并可填下面的 VID/PID（留空会自动扫描常见 ESC/POS 厂家）。
PRINTER_NAME = ""

# 若上面留空且要直连 Usb，填你的打印机 VID/PID（16 进制）。
# 芯烨 58TS 常见 VID: 0x0483（STM32 方案）/ 0x1FC9（Xprinter）/ 0x0416（部分批次）；
# 佳博常见 VID: 0x0416 ；Epson: 0x04B8。不知道就保持 None，程序会自动扫描常见 ESC/POS 厂家。
#
# ⚠ 重要：libusb 直连这条路（上面留空 + 下面也留空）需要**系统级 libusb 原生库**，
#   且 Windows 上必须用 Zadig 把打印机驱动换成 WinUSB —— 换成 WinUSB 后官方驱动就失效了，
#   别的软件（收银机/POS 系统）会打不了。所以只在「没装官方驱动、也不想装」时用。
#   （pyusb 已随 requirements.txt 装好，不用再装；它只是绑定层，不含 libusb 本身。）
#   正常推荐路线是 PRINTER_NAME（系统队列，见下）或让它自动探测（PRINTER_AUTO_DETECT）。
PRINTER_VENDOR_ID = None
PRINTER_PRODUCT_ID = None

# ★ 全自动模式（推荐）：PRINTER_NAME 留空时，自动从系统打印机列表里认出 58mm 热敏小票机。
#   走的是系统打印队列（官方驱动，Windows: winspool / macOS: CUPS），零原生依赖、最稳。
#   这样 PRINTER_NAME / VID / PID 三个都不用填。
PRINTER_AUTO_DETECT = True

# 自动识别的关键字：打印机「名称 / 驱动名 / 端口名」里含任意一个即认为是热敏小票机
# （不区分大小写）。你的机器名字不在里面也简单——把关键词加到这个列表即可。
PRINTER_NAME_KEYWORDS = [
    "xprinter", "xp-58", "xp-80", "芯烨",
    "gprinter", "gp-58", "gp-80", "佳博",
    "pos58", "pos-58", "pos80", "pos-80", "58mm", "80mm",
    "thermal", "receipt", "热敏", "小票", "票据",
    "tm-t", "epson tm", "rongta", "容大", "snbc", "新北洋", "hprt", "汉印",
]

# 小票顶部店铺名（各类小票都会显示，可改成你的直播间/店铺名）
PRINTER_HEADER = "树洞"

# 打印模式总开关（唯一！真实路径与 __main__ 自测共用这一个）：
#   True  = 所有打印只输出到「控制台」（render_to_str 排版预览），不连 USB / 系统打印机、不报错，
#           适合先调格式；
#   False = 恢复真实出纸（需先配好 PRINTER_NAME 或 VID/PID + libusb）。
# 接好打印机后把这里改成 False 即可，无需再改别处。
PRINTER_CONSOLE_PREVIEW = True

# 同一去重键去重间隔（秒），避免连击狂刷小票
DEDUP_SECONDS = 2.0

# USB（libusb）探测结果的缓存秒数——**失败结果也缓存**（负缓存）。
# 直播高峰每秒几十条事件，若每条都去扫描 USB 并报一次错，日志会被刷爆且毫无意义。
# 设短一点只会让你的日志变脏，设长一点则接上打印机后最多等这么久才被认出来。
USB_PROBE_CACHE_SECONDS = 30.0

# 完全没有可用打印出口时，隔多久提醒一次（期间只是静默跳过，不刷屏）
NO_OUTLET_NOTICE_SECONDS = 120.0

# ---------- 礼物小票头像 ----------
# 头像来源：抖音 CDN 的 HTTP 图片 URL（由 WebSocketServerD.get_avatar_url 抓取，
# 经 liveMain._maybe_print 放进 data["avatar"]）。
# 58mm 热敏只有黑白，所以流程是：
#   下载 -> 居中正方裁切 -> 圆形遮罩(圆外留白) -> 灰度+自动对比度
#        -> 缩到目标尺寸 -> Floyd-Steinberg 抖动二值化 -> 画实心圆边 -> 补齐纸宽居中 -> GS v 0 光栅
# 任一步失败都只表现为「本次不打头像」，绝不影响小票其余内容。
GIFT_AVATAR_ENABLED = True         # 总开关：礼物小票是否打印头像
GIFT_AVATAR_DOTS = 160             # 头像直径（点）。58mm 纸宽 384 点，160 约 20mm（约 10 行纸高）
GIFT_AVATAR_BRIGHTNESS = 1.12      # 二值化前的提亮系数（1.0=不变）。热敏纸打点会渗开，
                                   # 实测偏亮一点（1.1~1.2）打出来更清楚；照片偏暗可再调大
GIFT_AVATAR_MIN_VALUE = 0          # 礼物价值 >= 该值才打头像（0 = 都打）；想按价值省纸就调大
GIFT_AVATAR_TIMEOUT = 4.0          # 单张头像下载超时（秒），避免拖住打印队列
GIFT_AVATAR_RETRY_GAP = 60.0       # 同一 URL 下载失败后，多久内不再重试（秒）
GIFT_AVATAR_ASCII_PREVIEW = True   # 控制台预览时把头像打成 ASCII，不出纸也能先看二值化效果
PAPER_DOTS = 384                   # 58mm @203dpi 可打印宽度（点）。头像按此宽度居中
# 头像本地缓存目录（下载一次后就命中缓存，不再联网）
GIFT_AVATAR_CACHE_DIR = os.path.join(tempfile.gettempdir(), "live_gui_avatar_cache")
# 抖音 CDN 对裸请求可能 403，带上常见浏览器 UA + Referer 更稳
GIFT_AVATAR_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
GIFT_AVATAR_REFERER = "https://live.douyin.com/"

# ---------- 聊天「玩法」分类（关键字 + 文字长度 命中即打印花样小票） ----------
# 每加一种玩法，往 CHAT_STYLES 里加一条 dict 即可，无需改逻辑：
#   key       : 唯一标识
#   name      : 玩法名（小票上显示）
#   title     : 小票抬头
#   keywords  : 命中任一关键字即算匹配（支持中/英）
#   min_len   : 文字最少字数（含）；max_len: 最多字数（含）
#   decoration: 小票中部装饰语（list）
#   footer    : 小票底部一句
CHAT_STYLE_ENABLED = True          # 总开关：False 则聊天只走普通小票
CHAT_PRINT_ONLY_MATCHED = False    # True 则「只有命中玩法才打印」，未命中不打印；False 则未命中也打普通聊天小票
CHAT_STYLES = [
    {
        "key": "love",
        "name": "爱情宣言",
        "title": "爱情宣言",
        "keywords": ["爱", "喜欢", "心动", "嫁", "娶", "在一起", "宝宝", "老公", "老婆",
                     "男友", "女友", "表白", "爱你", "么么", "亲亲", "脱单", "官宣", "想你"],
        "min_len": 6, "max_len": 60,
        "decoration": ["愿得一人心，白首不相离", "整屏都是粉红泡泡~"],
        "footer": "愿天下有情人终成眷属",
    },
    {
        "key": "vent",
        "name": "情绪发泄",
        "title": "心情树洞",
        "keywords": ["烦", "累", "哭", "崩溃", "生气", "讨厌", "压力", "难受", "委屈",
                     "想死", "滚", "破防", "emo", "摆烂", "焦虑", "失眠", "抑郁"],
        "min_len": 4, "max_len": 80,
        "decoration": ["生活偶有风雨，说出来会好受点", "主播陪你一起扛"],
        "footer": "抱抱你，明天会更好",
    },
    {
        "key": "bless",
        "name": "祝福许愿",
        "title": "祝福许愿",
        "keywords": ["祝", "保佑", "好运", "暴富", "脱单", "上岸", "顺利", "健康",
                     "发财", "心想事成", "锦鲤", "逢考必过"],
        "min_len": 4, "max_len": 60,
        "decoration": ["愿望已签收，正在派送中", "好运 +1"],
        "footer": "愿你所求皆如愿",
    },
    {
        "key": "roast",
        "name": "吐槽八卦",
        "title": "吃瓜现场",
        "keywords": ["哈哈", "笑死", "无语", "服了", "离谱", "绝了", "草", "卧槽",
                     "真的假的", "666", "神金", "离大谱"],
        "min_len": 3, "max_len": 50,
        "decoration": ["吃瓜群众已就位", "这瓜保熟"],
        "footer": "主播：确实离谱",
    },
    {
        "key": "secret",
        "name": "神秘树洞",
        "title": "神秘树洞",
        "keywords": ["秘密", "树洞", "心里话", "悄悄话", "偷偷", "不敢说", "其实我",
                     "告诉你一个秘密", "说个秘密", "憋不住", "藏", "我只告诉你", "说实话",
                     "不敢跟别人", "只有你知道", "心事", "暗恋"],
        "min_len": 5, "max_len": 80,
        "decoration": ["树洞已收到，这个秘密只属于你", "说出来，心里就轻松啦"],
        "footer": "你的秘密，树洞替你守着",
    },
    {
        "key": "dialect",
        "name": "方言情话",
        "title": "方言情话",
        "keywords": ["方言", "土味", "情话", "土味情话", "家乡话"],
        "min_len": 4, "max_len": 40,
        "decoration": ["用家乡话念出来更带感", "土到极致便是潮"],
        "footer": "主播用方言回你一句",
    },
    {
        "key": "birthday",
        "name": "生日祝福",
        "title": "生日祝福",
        "keywords": ["生日", "寿星", "生日快乐", "过生日"],
        "min_len": 3, "max_len": 40,
        "decoration": ["生日快乐，心想事成", "全场一起唱生日歌"],
        "footer": "今天你是最大牌的星",
    },
    {
        "key": "askhost",
        "name": "灵魂拷问",
        "title": "灵魂拷问",
        "keywords": ["主播", "问你", "你觉不觉得", "你觉得", "敢不敢", "你敢"],
        "min_len": 4, "max_len": 60,
        "decoration": ["评论区蹲一个回答", "这题有点烫手"],
        "footer": "主播，正面回答！",
    },
    {
        "key": "fortune",
        "name": "今日运势",
        "title": "今日运势",
        "keywords": ["运势", "星座", "算命", "今日运", "塔罗", "占卜"],
        "min_len": 3, "max_len": 40,
        "decoration": ["抽一张今日签", "吉星高照的一天"],
        "footer": "主播帮你算一卦",
    },
    {
        "key": "tongue",
        "name": "绕口令",
        "title": "绕口令",
        "keywords": ["绕口令", "顺口溜", "溜溜"],
        "min_len": 3, "max_len": 40,
        "decoration": ["一口气念完算你赢", "舌头打结现场"],
        "footer": "挑战成功奖礼物",
    },
    {
        "key": "wish",
        "name": "愿望清单",
        "title": "愿望清单",
        "keywords": ["愿望", "想去", "想做", "清单", "dream", "许愿"],
        "min_len": 3, "max_len": 50,
        "decoration": ["愿望已投进愿望池", "总有一个会实现"],
        "footer": "一起把愿望一个个勾掉",
    },
]
# ================================================================

# ---------- 系统打印机自动探测（PRINTER_AUTO_DETECT） ----------
# 思路：枚举系统打印队列，用 PRINTER_NAME_KEYWORDS 匹配「名称 / 驱动名 / 端口名」。
# 走官方驱动 + 系统队列，不需要 pyusb/libusb，也不会像 libusb 那样把驱动换成 WinUSB。
def _list_windows_printers():
    """枚举 Windows 打印队列，返回 [(名称, 驱动名, 端口名), ...]；失败返回 []。

    用 winspool 的 EnumPrintersW（level 2，PRINTER_ENUM_LOCAL|PRINTER_ENUM_CONNECTIONS）。
    注意 DLL 名必须写成 "winspool.drv"：不存在 winspool.dll，
    所以 `ctypes.windll.winspool.drv` 这种写法会在加载阶段就 AttributeError。
    """
    import ctypes
    from ctypes import wintypes, byref, POINTER, Structure

    class PRINTER_INFO_2W(Structure):
        _fields_ = [
            ("pServerName", wintypes.LPWSTR), ("pPrinterName", wintypes.LPWSTR),
            ("pShareName", wintypes.LPWSTR), ("pPortName", wintypes.LPWSTR),
            ("pDriverName", wintypes.LPWSTR), ("pComment", wintypes.LPWSTR),
            ("pLocation", wintypes.LPWSTR), ("pDevMode", ctypes.c_void_p),
            ("pSepFile", wintypes.LPWSTR), ("pPrintProcessor", wintypes.LPWSTR),
            ("pDatatype", wintypes.LPWSTR), ("pParameters", wintypes.LPWSTR),
            ("pSecurityDescriptor", ctypes.c_void_p), ("Attributes", wintypes.DWORD),
            ("Priority", wintypes.DWORD), ("DefaultPriority", wintypes.DWORD),
            ("StartTime", wintypes.DWORD), ("UntilTime", wintypes.DWORD),
            ("Status", wintypes.DWORD), ("cJobs", wintypes.DWORD),
            ("AveragePPM", wintypes.DWORD),
        ]

    spool = ctypes.WinDLL("winspool.drv")
    spool.EnumPrintersW.argtypes = [
        wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, POINTER(wintypes.DWORD), POINTER(wintypes.DWORD)]
    spool.EnumPrintersW.restype = wintypes.BOOL

    need, count = wintypes.DWORD(), wintypes.DWORD()
    flags = 0x00000002 | 0x00000004          # PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    # 第一次只探需要的缓冲区大小：此时缓冲区为 0，Windows 必然返回 FALSE
    # （并置 ERROR_INSUFFICIENT_BUFFER），所以这一步**不能判返回值**，只看 pcbNeeded。
    spool.EnumPrintersW(flags, None, 2, None, 0, byref(need), byref(count))
    if not need.value:
        return []
    buf = ctypes.create_string_buffer(need.value)
    if not spool.EnumPrintersW(flags, None, 2, buf, need.value, byref(need), byref(count)):
        return []
    arr = ctypes.cast(buf, POINTER(PRINTER_INFO_2W))
    return [(arr[i].pPrinterName or "", arr[i].pDriverName or "", arr[i].pPortName or "")
            for i in range(count.value)]


def _list_unix_printers():
    """枚举 macOS/Linux 的 CUPS 打印队列，返回 [(名称, '', ''), ...]；失败返回 []。"""
    try:
        out = subprocess.run(["lpstat", "-p"], capture_output=True, text=True,
                             timeout=5).stdout
    except Exception as e:
        logging.debug("[打印机] lpstat 不可用: %s", e)
        return []
    names = []
    for ln in out.splitlines():
        m = re.match(r"printer\s+(\S+)\s+is\s", ln)
        if m:
            names.append((m.group(1), "", ""))
    return names


def list_system_printers():
    """列出系统打印机，返回 [(名称, 驱动名, 端口名), ...]。"""
    if sys.platform == "win32":
        try:
            return _list_windows_printers()
        except Exception as e:
            logging.warning("[打印机] 枚举 Windows 打印队列失败: %s", e)
            return []
    return _list_unix_printers()


def autodetect_printer_name():
    """从系统打印机里认出 58mm 热敏小票机，返回其名称；认不出返回 ""。

    匹配「名称 / 驱动名 / 端口名」拼接后的整串，忽略大小写。
    """
    cands = list_system_printers()
    if not cands:
        return ""
    for name, driver, port in cands:
        blob = ("%s %s %s" % (name, driver, port)).lower()
        for kw in PRINTER_NAME_KEYWORDS:
            if kw.lower() in blob:
                logging.info("[打印机] 自动识别到热敏打印机 %r（驱动=%r 端口=%r，命中关键词 %r）",
                             name, driver, port, kw)
                return name
    logging.info("[打印机] 自动探测没认出热敏小票机；系统里的打印机有：%s",
                 ", ".join(repr(n) for n, _, _ in cands) or "（无）")
    return ""


# 常见 ESC/POS 热敏打印机厂家的 VID（用于自动探测）
KNOWN_ESCPOS_VIDS = {
    0x0483,  # STMicroelectronics（大量 58mm 通用机/芯烨）
    0x0416,  # Winbond / 佳博、芯烨部分型号
    0x1FC9,  # Xprinter 芯烨
    0x0FE6,  # Logic Controls
    0x04B8,  # Epson
    0x0519,  # Zebra
    0x28E9,  # Gprinter 佳博
    0x25D7,  # Gprinter 佳博
    0x0DD4,  # 映美
    0x0C27,  # 佳博
}


def classify_chat_style(text):
    """根据关键字 + 文字长度，把一条聊天归类为某个『玩法』类型；无匹配返回 None。"""
    if not CHAT_STYLE_ENABLED:
        return None
    s = "" if text is None else str(text).strip()
    if not s:
        return None
    n = len(s)
    for style in CHAT_STYLES:
        mn = style.get("min_len", 0)
        mx = style.get("max_len", 9999)
        if n < mn or n > mx:
            continue
        kws = style.get("keywords", [])
        if any(kw in s for kw in kws):
            return style
    return None


def _escpos_module():
    """懒加载 escpos，缺依赖时给清晰报错而不是崩溃。"""
    try:
        import escpos.printer as P
        return P
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "未安装 python-escpos，请先 `pip install python-escpos` 并加入 requirements.txt"
        ) from e


# ========================= 礼物头像：下载 + 位图化 =========================
# 设计原则：全程「失败即降级」——头像拿不到就只是不打头像，绝不抛异常影响小票。
_avatar_fail_until = {}          # {url: 可重试时间戳}，防止坏 URL 反复卡住打印队列


def _avatar_cache_file(url: str) -> str:
    """按 URL 哈希算本地缓存路径（同一老板连刷多单只下载一次）。"""
    return os.path.join(GIFT_AVATAR_CACHE_DIR,
                        hashlib.md5(url.encode("utf-8")).hexdigest() + ".img")


def download_avatar(url):
    """下载头像到本地缓存并返回文件路径；失败返回 None（不抛异常）。

    - 已缓存 -> 直接返回，不联网
    - 非 http(s) 或空 URL -> 返回 None
    - 该 URL 刚失败过 -> GIFT_AVATAR_RETRY_GAP 秒内直接返回 None，不再浪费时间
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return None

    path = _avatar_cache_file(url)
    try:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    except OSError:
        pass

    if time.time() < _avatar_fail_until.get(url, 0.0):
        return None

    try:
        import requests                      # 懒加载：没装 requests 也不影响文本小票
    except Exception as e:
        logging.warning("[打印机] 未安装 requests，无法下载头像: %s", e)
        return None

    try:
        os.makedirs(GIFT_AVATAR_CACHE_DIR, exist_ok=True)
        headers = {"User-Agent": GIFT_AVATAR_UA, "Referer": GIFT_AVATAR_REFERER}
        resp = requests.get(url, headers=headers, timeout=GIFT_AVATAR_TIMEOUT)
        if resp.status_code != 200 or not resp.content:
            raise RuntimeError("HTTP %s / 空内容" % resp.status_code)
        tmp = path + ".part"                 # 先写临时文件再原子改名，避免半截文件被当缓存
        with open(tmp, "wb") as f:
            f.write(resp.content)
        os.replace(tmp, path)
        logging.info("[打印机] 头像已缓存 %s（%d 字节）",
                     os.path.basename(path), len(resp.content))
        return path
    except Exception as e:
        _avatar_fail_until[url] = time.time() + GIFT_AVATAR_RETRY_GAP
        logging.warning("[打印机] 头像下载失败，本次不打头像：%s（%s）", e, url[:100])
        return None


def build_avatar_image(src, dots=None, paper_dots=None):
    """把头像文件/图片对象处理成可直接送 ESC/POS 的 1-Bit 位图。

    返回 PIL.Image（模式 "1"，宽度 = 纸宽、头像居中、圆外留白）；任何失败返回 None。
    之所以补齐到整纸宽并居中：python-escpos 的 image(center=True) 依赖 printer profile
    的 media.width.pixels，默认 profile 里该值是 "Unknown"，居中会静默失效；
    自己补齐宽度则 100% 可控，也不会触发 ImageWidthError。
    """
    try:
        from PIL import Image, ImageDraw, ImageEnhance, ImageOps
    except Exception as e:
        logging.warning("[打印机] 缺少 Pillow，无法打印头像: %s", e)
        return None

    dots = GIFT_AVATAR_DOTS if dots is None else int(dots)
    paper_dots = PAPER_DOTS if paper_dots is None else int(paper_dots)
    try:
        # 兜底夹取：太小看不清，太大超纸宽
        dots = max(48, min(dots, paper_dots))
        paper_dots = max(dots, min(int(paper_dots), 1024))
        paper_dots = (paper_dots + 7) // 8 * 8        # GS v 0 按字节走，补成 8 的整数倍

        # PIL 常量兼容（新版走 Image.Resampling / Image.Dither 枚举）
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", None)
        dither = getattr(getattr(Image, "Dither", Image), "FLOYDSTEINBERG", 3)

        img = src if hasattr(src, "convert") else Image.open(src)
        img = img.convert("RGB")

        # 1) 居中正方裁切：避免非方形头像被拉伸变形
        w, h = img.size
        side = min(w, h)
        left, top = (w - side) // 2, (h - side) // 2
        img = img.crop((left, top, left + side, top + side))

        # 2) 先放大到 3 倍处理，最后再缩回来 —— 缩放质量明显更干净
        big = dots * 3
        img = img.resize((big, big), resample or Image.BICUBIC)

        # 3) 灰度 + 自动对比度（抖音头像普遍偏暗，autocontrast 能救回层次）
        gray = ImageOps.autocontrast(img.convert("L"), cutoff=1)
        # 热敏纸打点会往周围渗，偏亮一点实际打出来更清楚（可调 GIFT_AVATAR_BRIGHTNESS）
        if GIFT_AVATAR_BRIGHTNESS and GIFT_AVATAR_BRIGHTNESS != 1.0:
            gray = ImageEnhance.Brightness(gray).enhance(GIFT_AVATAR_BRIGHTNESS)

        # 4) 圆形遮罩：圆外保持留白。热敏打大面积黑边又费纸又脏，不如留白
        mask = Image.new("L", (big, big), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
        circle = Image.composite(gray, Image.new("L", (big, big), 255), mask)

        # 5) 缩到目标尺寸 -> 抖动二值化。直接塞灰度是一坨脏点，必须抖动
        small = circle.resize((dots, dots), resample or Image.BICUBIC)
        bw = small.convert("1", dither=dither)

        # 6) 抖动之后再画实心圆边：保证轮廓是清晰黑线而不是麻点
        ring_w = max(1, dots // 64)
        ImageDraw.Draw(bw).ellipse((1, 1, dots - 2, dots - 2), outline=0, width=ring_w)

        # 7) 平铺到整纸宽并居中（居中在这里一次做掉，不依赖 profile）
        canvas = Image.new("1", (paper_dots, dots), 1)      # 模式 "1" 里 1 = 白底
        canvas.paste(bw, ((paper_dots - dots) // 2, 0))
        return canvas
    except Exception as e:
        logging.warning("[打印机] 头像位图化失败，本次不打头像：%s", e)
        return None


def _avatar_ascii(img, cols=44):
    """把 1-Bit 位图转成多级灰阶 ASCII，用于控制台预览头像效果（不出纸先看）。

    先轻微高斯模糊把抖动点阵「糊」回灰阶 —— 这正是人眼看小票时发生的视觉混合，
    所以预览呈现的是实际贴到纸上的观感，而不是误导性的黑白点阵。
    """
    try:
        from PIL import Image, ImageFilter
    except Exception:
        return "[头像位图]"
    try:
        # 画布是整纸宽、头像只占中间；先裁出黑色内容的包围盒再预览，否则头像小得看不清
        bbox = img.convert("L").point(lambda v: 0 if v else 255).getbbox()
        if bbox:
            img = img.crop(bbox)

        w, h = img.size
        cols = max(16, min(int(cols), w))
        rows = max(8, int(round(h * cols / float(w) / 2.0)))   # /2：字符格高约为宽的两倍
        small = (img.convert("L")
                 .filter(ImageFilter.GaussianBlur(radius=1.2))  # 抖动点阵 -> 连续灰阶
                 .resize((cols, rows)))
        px = small.load()
        ramp = "@%#*+=-:. "                                     # 深 -> 浅
        n = len(ramp) - 1
        return "\n".join("".join(ramp[min(n, px[x, y] * n // 256)] for x in range(cols))
                         for y in range(rows))
    except Exception:
        return "[头像位图]"


class _ConsolePrinter:
    """本地预览用的『假打印机』：只收集 text() 输出、不真正发 USB。
    用于在 __main__ 里把各类型小票的排版以文本形式打到控制台（python-escpos 接口的最小子集）。
    """

    def __init__(self):
        self._buf = []
        self.codepage = "GB18030"

    def set(self, **kwargs):
        # align/bold/font/width 等排版属性在控制台预览里忽略
        pass

    def text(self, s):
        self._buf.append(s)

    def hw(self, hw):
        """硬件指令（INIT/SELECT/RESET）在控制台预览里无意义，显式空实现。"""
        pass

    def image(self, img_source, high_density_vertical=True, high_density_horizontal=True,
              impl="bitImageRaster", fragment_height=960, center=False):
        """预览模式下的「假图片打印」：把位图打成 ASCII，能直观看到二值化效果和占位高度。
        签名与 python-escpos 的 Printer.image 对齐，切换真实打印时零改动。
        """
        try:
            if GIFT_AVATAR_ASCII_PREVIEW and hasattr(img_source, "convert"):
                self._buf.append(_avatar_ascii(img_source) + "\n")
                return
        except Exception:
            pass
        self._buf.append("[图片]\n")

    def ln(self, n=1):
        self._buf.append("\n" * n)

    def cut(self, mode="FULL", **kwargs):
        self._buf.append("[✂ 切纸]\n")

    def close(self):
        pass

    def getvalue(self):
        # 兜底去掉残留的 ESC/POS 控制字节，保证控制台干净
        raw = "".join(self._buf)
        return "".join(ch for ch in raw if ch == "\n" or ord(ch) >= 0x20 or ch in "\t")


class EventPrinter:
    """事件小票打印机（单例风格，进程内建一个即可），支持多类型。"""

    def __init__(self, printer_name: str = PRINTER_NAME,
                 vendor_id=PRINTER_VENDOR_ID, product_id=PRINTER_PRODUCT_ID,
                 header: str = PRINTER_HEADER):
        self.printer_name = printer_name
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.header = header

        self._queue = queue.PriorityQueue()
        self._seq = itertools.count()          # 同优先级内 FIFO 排序用单调序号
        self._lock = threading.Lock()          # 保证 USB 同一时刻只一个连接
        self._last_print = {}                  # 去重：{dedup_key: timestamp}
        self._auto_name = ""                   # 自动探测到的打印机名（探测成功后缓存）
        self._auto_probe_ts = 0.0              # 上次探测时间（没认出来时用于节流）
        self._usb_probe_ts = 0.0               # 上次 libusb 探测时间
        self._usb_probe_res = None             # libusb 探测结果缓存：None 或 (vid, pid)
        self._usb_reason = ""                  # libusb 不可用的具体原因（给用户看的）
        self._no_outlet_ts = 0.0               # 上次「无可用打印出口」提醒时间
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        logging.info("[打印机] EventPrinter 已启动（PRINTER_NAME=%r, vid=%s, pid=%s）",
                     printer_name, vendor_id, product_id)

    # ---------- 对外接口 ----------
    def print_event(self, event_type: str, data: dict, dedup_key=None, header: str = None,
                    priority: bool = False):
        """主线程调用：把一条事件入队，由后台线程打印。
        event_type: 礼物 / 聊天 / 点赞 / 进场 / 关注 / 粉丝团
        data: 各类型所需的字段 dict
        dedup_key: 去重键（同键 DEDUP_SECONDS 秒内只打一次）；传 None 不去重
        priority: True 时视为「大礼物」插队打印（强制头像 + 大礼物样式）
        """
        if priority:
            data = dict(data or {})
            data["_priority"] = True
        # PriorityQueue 元素：(优先级数字越小越先出, 序号, event_type, data, dedup_key, header)
        self._queue.put((0 if priority else 1, next(self._seq),
                         str(event_type), dict(data or {}), dedup_key, header or self.header))

    # 兼容旧调用：礼物
    def print_gift(self, nick: str, gift: str, count=1, value=None, header: str = None,
                   avatar: str = None):
        self.print_event("礼物",
                         {"nick": nick, "gift": gift, "count": count, "value": value,
                          "avatar": avatar},
                         dedup_key=(str(nick), str(gift)),
                         header=header)

    def render_to_str(self, event_type: str, data: dict, header: str = None) -> str:
        """把某类事件渲染成『可读文本』（不真正打印），用于本地预览 / 控制台演示排版。
        复用真实渲染逻辑（含聊天玩法自动分类），只是把输出重定向到 _ConsolePrinter。
        """
        fake = _ConsolePrinter()
        self._render_event(fake, str(event_type), dict(data or {}), header or self.header)
        return fake.getvalue()

    # ---------- 后台消费 ----------
    def _run(self):
        while True:
            try:
                item = self._queue.get()
            except Exception:
                continue
            try:
                # PriorityQueue 元素：(优先级, 序号, event_type, data, dedup_key, header)
                _, _, event_type, data, dedup_key, header = item
                self._do_print(event_type, data, dedup_key, header)
            except Exception as e:
                logging.error("[打印机] 打印失败: %s", e)
            finally:
                self._queue.task_done()

    def _do_print(self, event_type, data, dedup_key, header):
        # 去重：同一去重键短时间内只打一次
        if dedup_key is not None:
            now = time.time()
            if now - self._last_print.get(dedup_key, 0.0) < DEDUP_SECONDS:
                logging.debug("[打印机] 去重跳过：%s/%s", event_type, dedup_key)
                return
            self._last_print[dedup_key] = now

        # 临时调试：仅控制台预览排版，不连打印机、不报错
        if PRINTER_CONSOLE_PREVIEW:
            txt = self.render_to_str(event_type, data, header)
            print("─" * 52 + "\n[控制台打印预览] 类型=" + str(event_type) + "\n" + txt.rstrip())
            return

        render_fn = lambda p: self._render_job(p, event_type, data, header)
        with self._lock:
            outlet = self._outlet()
            if outlet is None:
                self._warn_no_outlet()
                return
            kind, arg = outlet
            if kind == "os":
                try:
                    self._print_via_os(render_fn, arg)
                    return
                except Exception as e:
                    logging.warning("[打印机] 系统队列打印失败（%r），回退 Usb: %s", arg, e)
            self._print_via_usb(render_fn)

    def _render_job(self, p, event_type, data, header):
        """统一的任务渲染入口：先复位打印机状态，再渲染本次小票。

        发 ESC @（INIT）是必要的：打印机状态在任务之间是保留的，
        上一张若停在加粗/居中/倍宽，下一张开头就会跟着变形。
        `hw()` 在 python-escpos 里是硬编码实现（不查 profile），任何机型都能用。
        """
        try:
            p.hw("INIT")
        except Exception as e:
            logging.debug("[打印机] 发送初始化指令失败（忽略，不影响后续渲染）: %s", e)
        self._render_event(p, event_type, data, header)

    def _effective_printer_name(self):
        """本次实际要用的系统打印机名。

        优先用配置的 PRINTER_NAME；留空且开启 PRINTER_AUTO_DETECT 时自动探测。
        探测到就永久缓存；没探测到则 30 秒内不重复扫描（避免每次打印都枚举一遍）。
        """
        if self.printer_name:
            return self.printer_name
        if not PRINTER_AUTO_DETECT:
            return ""
        if self._auto_name:
            return self._auto_name
        now = time.time()
        if now - self._auto_probe_ts < 30.0:
            return ""
        self._auto_probe_ts = now
        self._auto_name = autodetect_printer_name()
        return self._auto_name

    # ---------- 打印出口选择（带负缓存，避免直播高峰把日志刷爆） ----------
    def _probe_usb_cached(self):
        """探测 libusb 直连设备；结果（含"没找到"）缓存 USB_PROBE_CACHE_SECONDS 秒。"""
        now = time.time()
        if now - self._usb_probe_ts < USB_PROBE_CACHE_SECONDS:
            return self._usb_probe_res
        self._usb_probe_ts = now
        vid, pid = self._probe_vid_pid()
        self._usb_probe_res = (vid, pid) if vid is not None else None
        return self._usb_probe_res

    def _outlet(self):
        """本次实际可用的打印出口：("os", 打印机名) / ("usb", (vid, pid)) / None。

        按可靠性排序，与真实打印顺序一致：系统队列（官方驱动）优先，libusb 兜底。
        """
        name = self._effective_printer_name()
        if name:
            return ("os", name)
        usb = self._probe_usb_cached()
        if usb:
            return ("usb", usb)
        return None

    def _warn_no_outlet(self):
        """完全没有可用打印出口时提醒一次；NO_OUTLET_NOTICE_SECONDS 内不重复。

        以前这里对**每一条**事件都抛一次 ERROR，直播间人多时日志会被瞬间刷爆，
        而且刷的全是同一件事。现在改为：静默跳过 + 定时提醒一次，并把"为什么不行"说清楚。
        """
        now = time.time()
        if now - self._no_outlet_ts < NO_OUTLET_NOTICE_SECONDS:
            return
        self._no_outlet_ts = now
        logging.warning(
            "[打印机] 当前没有可用的打印出口，小票已跳过（%.0f 秒内不再重复提醒）。\n"
            "  已尝试：\n"
            "    1) 系统打印队列自动识别 → 没认出热敏小票机；\n"
            "    2) libusb 直连 → %s\n"
            "  系统里现有的打印机：%s\n"
            "  建议：接上 58TS 并装好官方驱动，让它出现在系统打印机列表里——\n"
            "        PRINTER_AUTO_DETECT 会自动命中；若认不出，把名字里的特征词加进\n"
            "        PRINTER_NAME_KEYWORDS，或直接把打印机名填到 PRINTER_NAME。",
            NO_OUTLET_NOTICE_SECONDS,
            self._usb_reason or "不可用",
            ", ".join(repr(n) for n, _, _ in list_system_printers()) or "（无）",
        )

    # ---------- 事件分发 -> 渲染 ----------
    def _render_event(self, p, event_type, data, header):
        if event_type == "礼物":
            self._render_gift(p, data, header)
        elif event_type == "聊天":
            style = classify_chat_style(data.get("text", ""))
            if style is not None:
                self._render_chat_style(p, data, header, style)
            # elif not CHAT_PRINT_ONLY_MATCHED:
                # self._render_chat(p, data, header)
        elif event_type == "点赞":
            self._render_like(p, data, header)
        elif event_type == "进场":
            self._render_enter(p, data, header)
        elif event_type == "关注":
            self._render_follow(p, data, header)
        elif event_type == "粉丝团":
            self._render_fans(p, data, header)
        else:
            logging.warning("[打印机] 未知事件类型 %s，跳过", event_type)

    # ---------- 渲染工具 ----------
    @staticmethod
    def _safe(s):
        """清洗文本：去掉 GB18030 无法编码的字符（emoji 等），避免打印崩溃。"""
        s = "" if s is None else str(s)
        try:
            s.encode("gb18030")
            return s
        except Exception:
            return s.encode("gb18030", "ignore").decode("gb18030")

    @staticmethod
    def _cjk_width(ch):
        return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1

    def _wrap(self, text, width=16):
        """按显示宽度折行（中文算 2 宽，英文算 1 宽），58mm 约每行 16 个汉字。"""
        text = self._safe(text)
        lines, cur, cur_w = [], "", 0
        for ch in text:
            cw = self._cjk_width(ch)
            if cur_w + cw > width and cur:
                lines.append(cur)
                cur, cur_w = ch, cw
            else:
                cur += ch
                cur_w += cw
        if cur:
            lines.append(cur)
        return lines or [""]

    def _ticket_head(self, p, header, title):
        p.codepage = "GB18030"
        p.set(align="center", bold=True)
        # 抬头与标题合并成一行，省一行纸，仍保留「==== 标题 ====」花边
        label = "%s·%s" % (self._safe(header), self._safe(title)) if header else self._safe(title)
        p.text("==== %s ====\n" % label)

    def _ticket_tail(self, p, extra=None):
        p.set(align="center")
        p.text("----------------\n")                          # 单条分隔（原 26 太长），省纸
        if extra:
            for ln in extra:
                p.text(self._safe(ln) + "\n")
        # p.text(datetime.now().strftime("%m-%d %H:%M") + "\n")   # 短日期，省横向宽度 时间先不打印 省纸
        p.text("\n")                                           # 仅 1 个空行（原 2 个）
        # p.cut() # 暂时不切纸

    # ---------- 各类型小票渲染（ESC/POS） ----------
    def _print_avatar(self, p, data):
        """在礼物小票上打印头像（居中）。

        失败即降级：下载失败 / 解码失败 / 送打印机失败，都只是「本次不打头像」，
        已在各自函数内 log.warning，绝不让异常冒泡影响小票其余内容。
        价值阈值 GIFT_AVATAR_MIN_VALUE > 0 时，低于阈值的礼物不打头像（省纸）。
        """
        if not GIFT_AVATAR_ENABLED:
            return

        # 大礼物（优先打印）强制打印头像，不受价值阈值限制
        if GIFT_AVATAR_MIN_VALUE > 0 and not data.get("_priority"):
            if self._coerce_int(data.get("value"), 0) < GIFT_AVATAR_MIN_VALUE:
                logging.debug("[打印机] 礼物价值未达 %s，跳过头像", GIFT_AVATAR_MIN_VALUE)
                return

        path = download_avatar(data.get("avatar") or "")
        if not path:
            return
        img = build_avatar_image(path)
        if img is None:
            return
        try:
            # 居中已由 build_avatar_image 补齐纸宽完成，这里不要再用 center=True
            #（默认 profile 的 media width 是 Unknown，center 会静默失效）
            p.image(img, impl="bitImageRaster", center=False)
        except Exception as e:
            logging.warning("[打印机] 头像送打印机失败，本次不打头像：%s", e)

    def _render_gift(self, p, data, header):
        nick = self._safe(data.get("nick", ""))
        gift = self._safe(data.get("gift", ""))
        count = self._coerce_int(data.get("count", 1))
        is_big = bool(data.get("_priority"))
        # 风控规避：小票不印「礼物」字样，用中性「回执」替代
        self._ticket_head(p, header, "专属回执" if is_big else "感谢回执")
        self._print_avatar(p, data)              # 头像在最上方居中；无头像时此行为空操作
        p.set(align="center", bold=True)
        if is_big:
            p.text("★ 特别鸣谢 ★\n")
        p.text("感谢 %s 的支持！\n" % nick)
        p.set(align="left")
        # 礼物价值(value)仅用于底层逻辑判断（高价值优先打印/头像阈值），
        # 不打印到小票纸面，避免用户直观看到金额。
        p.text("收到：%s x%d\n" % (gift, count))
        p.set(align="center")
        p.text("您的支持是我们最大的动力\n")
        p.text("谢谢老板！\n")
        self._ticket_tail(p)

    def _render_chat(self, p, data, header):
        nick = self._safe(data.get("nick", ""))
        text = data.get("text", "")
        self._ticket_head(p, header, "聊天记录")
        p.set(align="left")
        p.text("网友 %s：\n" % nick)
        for ln in self._wrap(text, 16):
            p.text(ln + "\n")
        self._ticket_tail(p)

    def _render_chat_style(self, p, data, header, style):
        """命中『玩法』时打印花样小票：抬头=玩法名，正文=原话，附装饰语+底部语。"""
        self._ticket_head(p, header, style.get("title", style.get("name", "弹幕玩法")))
        p.set(align="left")
        nick = self._safe(data.get("nick", ""))
        p.text("来自 %s 的%s：\n" % (nick, self._safe(style.get("name", ""))))
        text = data.get("text", "")
        for ln in self._wrap(text, 16):
            p.text(ln + "\n")
        p.set(align="center")
        for ln in style.get("decoration", []):
            p.text(self._safe(ln) + "\n")
        footer = style.get("footer")
        self._ticket_tail(p, extra=[footer] if footer else None)

    def _render_like(self, p, data, header):
        nick = self._safe(data.get("nick", ""))
        count = self._coerce_int(data.get("count", 1))
        self._ticket_head(p, header, "点赞记录")
        p.set(align="left")
        p.text("%s 点亮了小心心\n" % nick)
        p.text("点赞数：x%d\n" % count)
        self._ticket_tail(p)

    def _render_enter(self, p, data, header):
        nick = self._safe(data.get("nick", ""))
        self._ticket_head(p, header, "欢迎光临")
        p.set(align="center", bold=True)
        p.text("欢迎 %s\n" % nick)
        p.set(align="center")
        p.text("进入直播间\n")
        self._ticket_tail(p)

    def _render_follow(self, p, data, header):
        nick = self._safe(data.get("nick", ""))
        self._ticket_head(p, header, "关注记录")
        p.set(align="left")
        p.text("感谢 %s\n" % nick)
        p.text("关注了主播，么么哒\n")
        self._ticket_tail(p)

    def _render_fans(self, p, data, header):
        nick = self._safe(data.get("nick", ""))
        text = data.get("text", "")
        self._ticket_head(p, header, "粉丝团")
        p.set(align="left")
        p.text("%s 加入粉丝团\n" % nick)
        if text:
            for ln in self._wrap(text, 16):
                p.text(ln + "\n")
        self._ticket_tail(p)

    # ---------- 传输：系统打印队列（USB 经驱动注册） ----------
    def _print_via_os(self, render_fn, printer_name=None):
        printer_name = printer_name or self.printer_name
        P = _escpos_module()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".bin")
        tmp.close()
        try:
            fp = P.File(tmp.name)              # 纯 Python 生成 ESC/POS 字节
            render_fn(fp)
            fp.close()
            if sys.platform == "win32":
                self._win_raw_print(tmp.name, printer_name)
            else:
                self._mac_cups_print(tmp.name, printer_name)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def _mac_cups_print(self, path, printer_name=None):
        """macOS：用系统 CUPS 把原始 ESC/POS 发给已注册的 USB 打印机。"""
        printer_name = printer_name or self.printer_name
        try:
            subprocess.run(["lp", "-d", printer_name, "-o", "raw", path],
                           check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            # 打印机名不对时，列出可用名方便排查
            try:
                avail = subprocess.run(["lpstat", "-p"], capture_output=True,
                                       text=True, timeout=5).stdout
            except Exception:
                avail = ""
            raise RuntimeError(
                "CUPS 打印失败（打印机名=%r）。可用打印机：\n%s" % (printer_name, avail)
            ) from e

    def _win_raw_print(self, path, printer_name=None):
        """Windows：用 winspool 原生 API 写 RAW（ctypes，零依赖）。

        注意：DLL 名必须写 "winspool.drv"。系统里**不存在 winspool.dll**，
        所以不能写成 `ctypes.windll.winspool.drv`——那样会在加载阶段直接
        AttributeError: winspool（windll.winspool 会去找 winspool.dll）。
        """
        import ctypes
        from ctypes import wintypes, byref
        printer_name = printer_name or self.printer_name
        # use_last_error=True：让 GetLastError 由 ctypes 保存，避免被中间调用覆盖
        winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)   # 必须带 .drv

        class DOCINFO(ctypes.Structure):
            _fields_ = [("pDocName", ctypes.c_wchar_p),
                        ("pOutputFile", ctypes.c_wchar_p),
                        ("pDataType", ctypes.c_wchar_p)]

        # 显式声明签名：64 位下句柄若不声明，可能被当成 32 位 int 截断
        winspool.OpenPrinterW.argtypes = [wintypes.LPCWSTR,
                                          ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]
        winspool.OpenPrinterW.restype = wintypes.BOOL
        winspool.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                              ctypes.POINTER(DOCINFO)]
        winspool.StartDocPrinterW.restype = wintypes.DWORD
        winspool.StartPagePrinter.argtypes = [wintypes.HANDLE]
        winspool.StartPagePrinter.restype = wintypes.BOOL
        winspool.WritePrinter.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD)]
        winspool.WritePrinter.restype = wintypes.BOOL
        winspool.EndPagePrinter.argtypes = [wintypes.HANDLE]
        winspool.EndPagePrinter.restype = wintypes.BOOL
        winspool.EndDocPrinter.argtypes = [wintypes.HANDLE]
        winspool.EndDocPrinter.restype = wintypes.BOOL
        winspool.ClosePrinter.argtypes = [wintypes.HANDLE]
        winspool.ClosePrinter.restype = wintypes.BOOL

        h = wintypes.HANDLE()
        if not winspool.OpenPrinterW(printer_name, byref(h), None):
            err = ctypes.WinError(ctypes.get_last_error())
            raise RuntimeError(
                "打不开打印机 %r：%s\n系统里可用的打印机：%s"
                % (printer_name, err,
                   ", ".join(repr(n) for n, _, _ in list_system_printers()) or "（无）"))

        try:
            di = DOCINFO("escpos_event", None, "RAW")
            if not winspool.StartDocPrinterW(h, 1, byref(di)):
                raise RuntimeError("StartDocPrinter 失败：%s"
                                   % ctypes.WinError(ctypes.get_last_error()))
            try:
                if not winspool.StartPagePrinter(h):
                    raise RuntimeError("StartPagePrinter 失败：%s"
                                       % ctypes.WinError(ctypes.get_last_error()))
                with open(path, "rb") as f:
                    data = f.read()
                buf = ctypes.create_string_buffer(data, len(data))
                written = wintypes.DWORD()
                if not winspool.WritePrinter(h, ctypes.cast(buf, ctypes.c_void_p),
                                            len(data), byref(written)):
                    raise RuntimeError("WritePrinter 失败：%s"
                                       % ctypes.WinError(ctypes.get_last_error()))
                if written.value != len(data):
                    logging.warning("[打印机] 只写出 %d/%d 字节，可能被截断",
                                    written.value, len(data))
                winspool.EndPagePrinter(h)
            finally:
                winspool.EndDocPrinter(h)
        finally:
            winspool.ClosePrinter(h)

    # ---------- 传输：libusb 直连（兜底） ----------
    def _print_via_usb(self, render_fn):
        P = _escpos_module()
        vid, pid = self._resolve_vid_pid()
        up = P.Usb(vid, pid)
        try:
            render_fn(up)
        finally:
            up.close()

    def _resolve_vid_pid(self):
        if self.vendor_id and self.product_id:
            return self.vendor_id, self.product_id
        vid, pid = self._probe_vid_pid()
        if vid is None:
            raise RuntimeError("libusb 直连不可用：%s"
                               % (self._usb_reason or "未找到可用的 ESC/POS USB 设备"))
        logging.info("[打印机] 自动探测到 ESC/POS 设备 vid=%#06x pid=%#06x", vid, pid)
        return vid, pid

    def _probe_vid_pid(self):
        """探测 libusb 直连的 ESC/POS 设备，返回 (vid, pid)；不可用返回 (None, None)。

        失败原因写入 self._usb_reason，供用户看到**具体**是哪一环缺东西
        （以前只笼统列两种可能，装了 pyusb 之后那段提示就不准了）。
        """
        self._usb_reason = ""
        try:
            import usb.core
        except Exception as e:
            self._usb_reason = "未安装 pyusb（%s）。装法：pip install pyusb" % type(e).__name__
            return None, None

        # pyusb 只是 Python 绑定层，真正的 USB 传输由 libusb 原生库提供
        # （Windows: libusb-1.0.dll；macOS: libusb-1.0.dylib）。wheel 里不含该库，
        # 缺了它后面 usb.core.find 必然抛 NoBackendError——先在这里单独说清楚。
        try:
            from usb.backend import libusb1
            if libusb1.get_backend() is None:
                self._usb_reason = (
                    "pyusb 已安装，但缺 libusb 原生库（Windows 上找不到 libusb-1.0.dll）。\n"
                    "    pyusb 只是绑定层、不自带该库，所以扫描会直接 NoBackendError。\n"
                    "    解法：把 libusb-1.0.dll 放到 venv\\Scripts\\ 下（或系统 PATH 里）。\n"
                    "    注意：Windows 上即便装好 libusb，也还需用 Zadig 把打印机驱动换成 WinUSB，\n"
                    "    换完官方驱动失效、别的软件打不了——所以这条路只在没有官方驱动时才用。"
                )
                return None, None
        except Exception as e:
            self._usb_reason = "libusb 后端初始化失败：%s: %s" % (type(e).__name__, e)
            return None, None

        try:
            for dev in usb.core.find(find_all=True):
                if dev.idVendor in KNOWN_ESCPOS_VIDS:
                    return dev.idVendor, dev.idProduct
        except Exception as e:
            self._usb_reason = "USB 扫描失败：%s: %s" % (type(e).__name__, e)
            return None, None

        self._usb_reason = (
            "USB 总线上没有已知 ESC/POS 厂家的设备（按 VID 白名单匹配：%s）。\n"
            "    若你的打印机已通过 USB 接上，说明它走的是官方驱动（不出现在 libusb 里）——\n"
            "    这属于正常情况，应该走系统打印队列，而不是这条路。"
            % ", ".join("%#06x" % v for v in sorted(KNOWN_ESCPOS_VIDS))
        )
        return None, None

    @staticmethod
    def _coerce_int(v, default=1):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def has_printer(self):
        """是否已有可用的打印出口（不抛异常，结果带缓存）。

        判定顺序与真实打印一致：配置的 PRINTER_NAME → 自动探测到的系统打印机
        → libusb 探测到的 USB ESC/POS 设备。
        """
        if self.printer_name:
            return True
        try:
            return self._outlet() is not None
        except Exception:
            return False


# 便捷单例（liveMain 里直接 from printer_manager import print_mgr）
print_mgr = EventPrinter()
# 兼容别名（旧代码可能仍引用 gift_printer）
gift_printer = print_mgr


def map_raw_message(raw):
    """把 WebSocketServerD.py 推送的『原始消息』拆包成 print_event 需要的
    (event_type, data, dedup_key)。字段映射与 liveMain._maybe_print 完全一致。
    原文字段名：source / nickName / giftName / giftCount / giftValue / combo / msg / goodCount。
    缺少必要字段（如 nickName）返回 None，表示不打印（与 _maybe_print 的守卫一致）。
    """
    if not isinstance(raw, dict):
        return None
    src = str(raw.get("source", ""))
    nick = str(raw.get("nickName", "") or "")
    if src == "礼物":
        gift = str(raw.get("giftName", "") or "")
        if not (nick and gift):
            return None
        return ("礼物",
                {"nick": nick, "gift": gift,
                 "count": raw.get("giftCount", 1),
                 "value": raw.get("giftValue") or raw.get("combo"),
                 "avatar": raw.get("avatar", "")},
                (nick, gift))
    if src == "聊天":
        text = str(raw.get("msg", "") or "")
        if not (nick and text):
            return None
        return ("聊天", {"nick": nick, "text": text}, (nick, text))
    if src == "点赞":
        if not nick:
            return None
        return ("点赞", {"nick": nick, "count": raw.get("goodCount", 1)}, (nick,))
    if src == "进场":
        if not nick:
            return None
        return ("进场", {"nick": nick}, (nick,))
    if src == "关注":
        if not nick:
            return None
        return ("关注", {"nick": nick}, (nick,))
    if src == "粉丝":
        text = str(raw.get("msg", "") or "")
        if not nick:
            return None
        return ("粉丝团", {"nick": nick, "text": text}, (nick, text))
    return None


if __name__ == "__main__":
    # 本地调试：用与 WebSocketServerD / liveMain 一致的字段结构，在控制台演示各类型小票排版
    logging.basicConfig(level=logging.INFO)

    # 打印模式统一由模块顶部 PRINTER_CONSOLE_PREVIEW 控制（True=仅控制台预览，False=真出纸），
    # 不再有第二个开关，切换只改那一行即可。
    HEADER = "直播小票"

    # ① 原始消息：字段名严格按 WebSocketServerD.py 实际推送结构
    # avatar 字段来自 WebSocketServerD.get_avatar_url（抖音 CDN 图片 URL），
    # 有值且 GIFT_AVATAR_ENABLED=True 时，礼物小票会在文字上方打印圆形头像
    raw_messages = [
        {"source": "礼物", "nickName": "小明", "giftName": "玫瑰", "giftCount": 10, "giftValue": 520},
        {"source": "聊天", "nickName": "路人甲", "msg": "主播今天好帅气，关注了！"},            # 未命中玩法 -> 普通聊天票
        {"source": "聊天", "nickName": "夜未央", "msg": "主播我今天向你表白了，我真的好喜欢你"},  # 命中「爱情宣言」
        {"source": "聊天", "nickName": "小透明", "msg": "树洞，我其实暗恋我们班长好久了"},        # 命中「神秘树洞」
        {"source": "点赞", "nickName": "风一样", "goodCount": 30},
        {"source": "进场", "nickName": "夜未央"},
        {"source": "关注", "nickName": "猫不吃鱼"},
        # 注意：WebSocketServerD._parseFansclubMsg 当前原始消息缺 nickName，
        # 这里补上以便演示（真实流里粉丝团可能因此不打，属已知小坑）。
        {"source": "粉丝", "nickName": "小可爱", "msg": "打卡第 99 天"},
    ]

    print("=" * 52)
    print("  打印机排版预览（58mm）—— 字段与 WebSocketServerD / liveMain 对齐")
    print("=" * 52)
    for raw in raw_messages:
        mapped = map_raw_message(raw)
        if mapped is None:
            print("\n【原始消息 WS】%s" % json.dumps(raw, ensure_ascii=False))
            print("  -> 拆分后缺少必要字段（如 nickName），跳过打印")
            continue
        et, data, dedup = mapped
        # ② 拆包：展示原始消息 -> 传入 print_event 的结构
        print("\n【原始消息 WS】%s" % json.dumps(raw, ensure_ascii=False))
        print("【拆分->print_event】event_type=%r, data=%s, dedup_key=%s"
              % (et, json.dumps(data, ensure_ascii=False), dedup))
        # ③ 渲染：复用真实排版逻辑，输出到控制台
        preview = print_mgr.render_to_str(et, data, HEADER).rstrip("\n")
        print("【出纸预览】")
        if preview:
            print(preview)
        else:
            # 当前 聊天 的「普通票兜底分支」已被注释（仅命中玩法才打印），未命中即空
            print("（无小票输出：未命中『玩法』，且普通聊天票兜底已关闭 -> 按当前配置不打印）")

    # ④ 真实出纸（仅 PRINTER_CONSOLE_PREVIEW=False 且检测到打印机时入队；否则跳过，避免无谓报错）
    if PRINTER_CONSOLE_PREVIEW:
        print("\n" + "=" * 52)
        print("  [PRINTER_CONSOLE_PREVIEW=True] 仅演示排版，未连接打印机。")
        print("  要真出纸：把文件顶部 PRINTER_CONSOLE_PREVIEW 改为 False。")
        print("  打印机不用手填：PRINTER_AUTO_DETECT=True 时会自动从系统打印机列表认出热敏机。")
        print("=" * 52)
    else:
        print("\n" + "=" * 52)
        if print_mgr.has_printer():
            print("  以下通过 print_event 入队（真实出纸）")
            print("  实际使用打印机: %r" % (print_mgr._effective_printer_name() or "(libusb 直连)"))
            print("=" * 52)
            for raw in raw_messages:
                mapped = map_raw_message(raw)
                if mapped:
                    print_mgr.print_event(mapped[0], mapped[1], dedup_key=mapped[2], header=HEADER)
            time.sleep(2)
            print("已入队测试小票，请查看打印机。")
        else:
            print("  没找到可用打印机，跳过真实出纸。排查：")
            print("    1) 58TS 是否已装好官方驱动、出现在系统打印机列表里（控制面板→设备和打印机）")
            print("    2) 名字里若不含 PRINTER_NAME_KEYWORDS 里的关键词，把关键词补进那个列表")
            print("       当前系统里的打印机：%s"
                  % ", ".join(repr(n) for n, _, _ in list_system_printers()) or "（无）")
            print("    3) 或直接手填 PRINTER_NAME")
            print("  上面【出纸预览】即为实际格式效果。")
            print("=" * 52)
