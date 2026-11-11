# -*- coding: utf-8 -*-
"""「3D打印」预览窗口：照片级实景底图 + 动态打印件叠加。

静态场景（房间、木地板、龙门架打印机、线材、玻璃热床、橡胶传送带）来自
assets/images/printer3d_bg.jpg —— 一张按真实直播间构图生成的照片级底图，
避免程序化绘制的「插画感」。Qt 只叠加会动的部分：逐层挤出的头像圆牌、
当前层熔丝高光、落带后随传送带向右运走的成品、左立柱上的电子状态屏。

运行模式参照真实直播间录屏：成品在热床右侧落带，随传送带**从左向右**运走，
多张牌按出片间隔自然排队、右端出画；不做抛物线飞送，也不推挤旧牌。
打印完成后由左侧机械臂取件：下臂/上臂按逆运动学摆动，夹爪把成品从热床
搬到履带上方松爪，再回待机位。机械臂部件是从实景底图抠出的照片素材，
待机时直接使用底图里的静态实拍臂，动作时贴补丁擦除静态臂再重绘，无重影。

不依赖 QtWebEngine / Three.js / 本地 HTTP 服务 / 任何新端口。底图缺失时
自动退回内置的纯 QPainter 程序化场景（含完整动态机架），窗口仍可用。

数据来源与「小票模拟 / 手机预览」完全一致：ReceiptSimulator.add_event() 把
同一条结构化消息转发到本窗口。窗口只做可视化，不产生也不推送任何数据；
空闲补偿事件（synthetic）不会送进来，保证「抓到啥就打啥」。
"""

import logging
import math
import os
import random
import sys

from PyQt5.QtCore import (Qt, QTimer, QRectF, QPointF, QThread, QObject,
                          pyqtSignal, QElapsedTimer)
from PyQt5.QtGui import (QColor, QPainter, QPainterPath, QPen, QBrush, QPixmap,
                         QLinearGradient, QRadialGradient, QFont, QFontMetrics,
                         QPolygonF)
from PyQt5.QtWidgets import QWidget, QPushButton, QHBoxLayout, QVBoxLayout, QLabel

from core.printer_manager import download_avatar

# 设计分辨率：paintEvent 整体等比缩放，窗口可任意拉伸 / 适合 OBS 窗口采集
W, H = 1280.0, 720.0

# 以下坐标按 assets/images/printer3d_bg.jpg 实测标定（设计分辨率 1280x720）
PRINT_CX = 575.0                 # 热床打印位 X（对齐底图喷头）
BED_Y = 470.0                    # 热床上打印件底部 Y
NOZZLE_X, NOZZLE_Y = 575.0, 365.0   # 底图中喷嘴尖端（fallback 程序化场景用）
BELT_Y = 520.0                   # 传送带上成品底部 Y（对齐底图带面，落带即坐在带上）

# 喷头精灵：从实景底图抠出，可沿导轨左右扫掠（打印时动，待机回中）
HEAD_BG_NO = "printer3d_bg_noh.jpg"       # 已抠除喷头的背景
HEAD_PNG = "printer3d_head.png"           # 喷头精灵（带 alpha）
HEAD_LEFT, HEAD_TOP = 561.0, 261.0        # 精灵静态贴图在设计坐标的左上角
HEAD_NOZZLE_DX, HEAD_NOZZLE_DY = 24.0, 127.0  # 喷嘴尖相对精灵左上角的偏移
HEAD_SWEEP = 32.0                          # 沿导轨左右扫掠幅度（与回填包络匹配）
HEAD_WIRE_ANCHOR = (602.0, 200.0)          # 顶部机架固定锚点：背景线材束下垂末端所在，软管从此垂下
BELT_X_START = 700.0             # 成品落带位置（打印位右侧，避免与热床上打印件重叠）
BELT_X_EXIT = 1290.0             # 带右端出画
DISC_R = 54.0                    # 头像圆牌半径
PRINT_SECONDS = 3.5              # 一张牌的打印时长（贴近直播间快节奏）
BELT_SPEED = 52.0                # 传送带向右速度 px/s（与带面条纹同向）
BELT_STRIPE_SPEED = 52.0         # 带面条纹滚动速度 px/s
TRANSIT_SECONDS = 0.7            # 打印完成 → 落带的短距离搬运
MAX_QUEUE = 10
MAX_FINISHED = 4
FRAME_MS = 33                    # ~30fps

BG_REL = os.path.join("assets", "images", "printer3d_bg.jpg")

# ---- 机械臂：底图实测关节坐标 + 照片部件 ----
ARM_BASE = (322.0, 468.0)            # 底座旋转中心（固定）
ARM_SHOULDER0 = (220.0, 310.0)       # 待机肩关节
ARM_WRIST0 = (382.0, 336.0)          # 待机腕点
ARM_L1 = math.hypot(ARM_SHOULDER0[0] - ARM_BASE[0],
                    ARM_SHOULDER0[1] - ARM_BASE[1])
ARM_L2 = math.hypot(ARM_WRIST0[0] - ARM_SHOULDER0[0],
                    ARM_WRIST0[1] - ARM_SHOULDER0[1])
ARM_TH1_0 = math.atan2(ARM_SHOULDER0[1] - ARM_BASE[1],
                       ARM_SHOULDER0[0] - ARM_BASE[0])
ARM_TH2_0 = math.atan2(ARM_WRIST0[1] - ARM_SHOULDER0[1],
                       ARM_WRIST0[0] - ARM_SHOULDER0[0])
ARM_SECONDS = 3.0                    # 取件 → 投放 → 回位总时长
ARM_DROP_T = 2.20                   # 松爪落带时刻（与松爪完成对齐）
ARM_DROP_X, ARM_DROP_Y = 650.0, 495.0   # 松爪点：落在履带带面上（已按实测履带斜率校正）
# 腕点轨迹关键帧：(时刻, 目标腕点)。用 Catmull-Rom 平滑插值，使机械臂在
# 「打印完成瞬间→伸向成品顶部→夹取→抬起→转向履带→松爪→回待机」整段连续运动，
# 不再逐段急停。夹取/松爪处的重复点会让臂自然减速短暂停顿（符合真实动作）。
ARM_PATH = [
    (0.00, (382.0, 336.0)),   # 待机（与底图静态臂重合，启动无跳变）
    (0.55, (575.0, 375.0)),   # 移动到成品顶部
    (0.80, (575.0, 375.0)),   # 短暂停顿：执行夹取
    (1.30, (505.0, 300.0)),   # 夹稳后抬起
    (1.95, (650.0, 400.0)),   # 转向履带上方（腕点压低，使夹取的成品正好落在带面）
    (2.20, (650.0, 400.0)),   # 短暂停顿：松爪放件
    (3.00, (382.0, 336.0)),   # 空手回待机
]
# 夹爪开合区间：到达成品顶部后闭合，到达履带上方后张开
ARM_GRIP_CLOSE = (0.55, 0.80)   # (开始夹, 完全夹紧)
ARM_GRIP_OPEN = (1.95, 2.20)    # (开始松, 完全张开)
# name: (文件名, 设计坐标 bbox(x,y,w,h), 锚点在部件图内的局部偏移)
ARM_ASSETS = {
    "patch": ("printer3d_arm_patch.png", (185.0, 282.0, 227.0, 220.0), None),
    "base":  ("printer3d_arm_base.png",  (290.0, 443.0,  64.0,  56.0), None),
    "lower": ("printer3d_arm_lower.png", (195.0, 285.0, 155.0, 207.0), (127.0, 183.0)),
    "upper": ("printer3d_arm_upper.png", (200.0, 292.0, 202.0,  68.0), (20.0, 18.0)),
    "joint": ("printer3d_arm_joint.png", (198.0, 288.0,  44.0,  44.0), (22.0, 22.0)),
    "grip":  ("printer3d_arm_grip.png",  (360.0, 316.0,  42.0,  74.0), (22.0, 20.0)),
}


def _asset_path(name):
    """兼容源码运行与 PyInstaller 解包目录；找不到返回 None。"""
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(os.path.join(meipass, "assets", "images", name))
        roots.append(os.path.join(meipass, name))
    roots.append(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "images", name))
    for path in roots:
        if os.path.isfile(path):
            return path
    return None


def _bg_image_path():
    return _asset_path("printer3d_bg.jpg")


def _arm_ik(target):
    """二连杆逆运动学：给定腕点，返回 (肩点, 实际腕点, 下臂转角°, 上臂转角°)。"""
    dx, dy = target[0] - ARM_BASE[0], target[1] - ARM_BASE[1]
    d = math.hypot(dx, dy)
    d = max(abs(ARM_L1 - ARM_L2) + 2.0, min(d, ARM_L1 + ARM_L2 - 2.0))
    a = math.atan2(dy, dx)
    c = (ARM_L1 ** 2 + d ** 2 - ARM_L2 ** 2) / (2.0 * ARM_L1 * d)
    th1 = a - math.acos(max(-1.0, min(1.0, c)))
    sx = ARM_BASE[0] + ARM_L1 * math.cos(th1)
    sy = ARM_BASE[1] + ARM_L1 * math.sin(th1)
    th2 = math.atan2(target[1] - sy, target[0] - sx)
    wx = sx + ARM_L2 * math.cos(th2)
    wy = sy + ARM_L2 * math.sin(th2)
    return ((sx, sy), (wx, wy),
            math.degrees(th1 - ARM_TH1_0), math.degrees(th2 - ARM_TH2_0))

DEMO_NICKS = ["抹茶星冰乐", "小碗宽面", "咬一口糯米", "霁云", "气泡水哥", "阿白"]
DEMO_SOURCES = ["聊天", "礼物", "点赞", "关注"]


# ---------------------------------------------------------------------------
# 头像：复用 printer_manager 的磁盘缓存下载器，URL 去重 + 后台线程，不卡 UI
# ---------------------------------------------------------------------------

class _AvatarWorker(QThread):
    loaded = pyqtSignal(str, str)                 # (url, 本地路径或空串)

    def __init__(self, url):
        super().__init__()
        self._url = url

    def run(self):
        try:
            path = download_avatar(self._url)
        except Exception as e:
            logging.warning("[3D打印] 头像下载异常: %s", e)
            path = None
        self.loaded.emit(self._url, path or "")


class _AvatarCache(QObject):
    """按 URL 去重的头像缓存；缓存未命中时返回 None 并触发后台下载。"""

    def __init__(self, on_ready):
        super().__init__()
        self._on_ready = on_ready
        self._pixmaps = {}                        # url -> QPixmap（空 pixmap = 已失败）
        self._pending = set()
        self._workers = []

    def get(self, url):
        if not url:
            return None
        pix = self._pixmaps.get(url)
        if pix is not None:
            return pix
        if url not in self._pending:
            self._pending.add(url)
            w = _AvatarWorker(url)
            w.loaded.connect(self._on_loaded)
            w.finished.connect(lambda w=w: self._cleanup(w))
            self._workers.append(w)
            w.start()
        return None

    def _on_loaded(self, url, path):
        self._pending.discard(url)
        self._pixmaps[url] = QPixmap(path) if path else QPixmap()
        self._on_ready(url)

    def _cleanup(self, worker):
        try:
            self._workers.remove(worker)
        except ValueError:
            pass

    def shutdown(self):
        for w in list(self._workers):
            if w.isRunning():
                w.quit()
                if not w.wait(300):
                    w.terminate()
                    w.wait(200)
        self._workers.clear()


# ---------------------------------------------------------------------------
# 事件 → 打印任务
# ---------------------------------------------------------------------------

def _make_job(msg):
    """把一条直播消息转成打印任务；无昵称的事件（统计/系统）不出牌。"""
    if not isinstance(msg, dict):
        return None
    nick = str(msg.get("nickName") or msg.get("nick") or "").strip()
    if not nick:
        return None
    src = str(msg.get("source") or "").strip()
    tag = {"礼物": str(msg.get("giftName") or "礼物"),
           "聊天": "评论", "点赞": "点赞", "进场": "进场",
           "关注": "关注", "粉丝团": "粉丝团"}.get(src, src or "互动")
    return {
        "nick": nick,
        "source": src,
        "tag": tag[:8],
        "hue": sum(ord(c) for c in nick) % 360,
        "avatar": str(msg.get("avatar") or ""),
        "score": random.randint(80, 99),
    }


def _demo_job():
    src = random.choice(DEMO_SOURCES)
    return _make_job({"source": src, "nickName": random.choice(DEMO_NICKS),
                      "giftName": "小心心"})


# ---------------------------------------------------------------------------
# 绘制小工具
# ---------------------------------------------------------------------------

def _poly(p, points, fill, border=None, width=1.0):
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
    path.closeSubpath()
    p.setBrush(QBrush(fill))
    p.setPen(Qt.NoPen if border is None else QPen(QColor(border), width))
    p.drawPath(path)


def _lerp(a, b, t):
    return a + (b - a) * t


def _ease(t):
    return 1.0 - (1.0 - t) ** 3


def _smoothstep(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def _catmull_rom(pts, times, t):
    """对 (time, point) 关键帧做 Catmull-Rom 插值，得到 C1 连续（速度连续）的腕点。

    与逐段 ease-out 不同：ease-out 在每段末端速度归零，会在每个关键帧处出现
    「减速→停顿→再加速」的急停感（取件卡顿的根源）。Catmull-Rom 让臂在整条
    轨迹上速度连续，只在真正需要停顿的夹取/松爪点自然减速。
    """
    n = len(pts)
    if t <= times[0]:
        return pts[0]
    if t >= times[-1]:
        return pts[-1]
    i = 0
    while i < n - 1 and not (times[i] <= t <= times[i + 1]):
        i += 1
    p0 = pts[i - 1] if i - 1 >= 0 else pts[0]
    p1 = pts[i]
    p2 = pts[i + 1]
    p3 = pts[i + 2] if i + 2 < n else pts[-1]
    u = (t - times[i]) / (times[i + 1] - times[i])
    u2, u3 = u * u, u * u * u
    x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * u +
               (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * u2 +
               (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * u3)
    y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * u +
               (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * u2 +
               (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * u3)
    return (x, y)


def _font(size, bold=False, family="Microsoft YaHei"):
    f = QFont(family)
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


def _metal_h(p, rect, dark=False):
    """水平方向的铝型材拉丝渐变（左暗—亮边—中槽—亮边—右暗）。"""
    g = QLinearGradient(rect.left(), 0, rect.right(), 0)
    if dark:
        stops = [(0.00, "#2b3136"), (0.12, "#70787e"), (0.26, "#4a5157"),
                 (0.48, "#22282d"), (0.58, "#191e22"), (0.80, "#5b6369"),
                 (0.93, "#7f878d"), (1.00, "#2a3035")]
    else:
        stops = [(0.00, "#4a5157"), (0.10, "#a2aab0"), (0.24, "#6f777d"),
                 (0.46, "#3a4147"), (0.56, "#282e33"), (0.78, "#767e84"),
                 (0.92, "#9ba3a9"), (1.00, "#333a3f")]
    for pos, color in stops:
        g.setColorAt(pos, QColor(color))
    p.fillRect(rect, g)


def _metal_v(p, rect):
    """垂直方向的金属渐变（上亮下暗），用于横梁 / 铭牌。"""
    g = QLinearGradient(0, rect.top(), 0, rect.bottom())
    g.setColorAt(0.00, QColor("#a8b0b6"))
    g.setColorAt(0.16, QColor("#767e84"))
    g.setColorAt(0.48, QColor("#3c4348"))
    g.setColorAt(0.72, QColor("#272d32"))
    g.setColorAt(1.00, QColor("#151a1e"))
    p.fillRect(rect, g)


def _shadow_round(p, rect, radius, dx=5.0, dy=7.0, alpha=95):
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(12, 10, 8, alpha))
    p.drawRoundedRect(rect.translated(dx, dy), radius, radius)


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------

class Printer3DScene(QWidget):
    """纯绘制的打印现场：静态背景缓存 + 每帧仅重绘动态部件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(640, 360)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._avatars = _AvatarCache(lambda _url: self.update())
        self._bg = None
        self._photo_mode = False         # 是否成功加载照片级底图

        self._t = 0.0
        self._clock = QElapsedTimer()             # 真实时钟：掉帧也不改变打印时长
        self._clock.start()
        self._belt_offset = 0.0
        self._head_x = PRINT_CX
        self._head_y = NOZZLE_Y                  # 当前挤出层 / 喷嘴尖端 Y（待机位）
        self._progress = 0.0
        self._current = None                     # 正在打印的 job
        self._queue = []                         # 待打印（礼物插到最前）
        self._finished = []                      # 转运中 / 传送带上的成品
        self._done = 0
        self._arm = None                         # 机械臂取件状态
        self._arm_pix = {}                      # 照片部件 QPixmap
        self._arm_ready = False
        self._load_arm_assets()
        self._head_pix = QPixmap()               # 喷头精灵（照片底图可动件）
        self._head_ready = False
        self._load_head_asset()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(FRAME_MS)

    def _load_arm_assets(self):
        """加载机械臂照片部件；缺任一文件则退回原来的直接落带逻辑。"""
        self._arm_pix = {}
        for key, (name, _box, _anchor) in ARM_ASSETS.items():
            path = _asset_path(name)
            pm = QPixmap(path) if path else QPixmap()
            if pm.isNull():
                logging.warning("[3D打印] 缺少机械臂素材 %s，取件动画关闭", name)
                self._arm_pix = {}
                self._arm_ready = False
                return
            self._arm_pix[key] = pm
        self._arm_ready = True

    def _load_head_asset(self):
        """加载可动喷头精灵；与抠除喷头的背景配合使用，缺文件则退回到
        原背景（原背景里喷头是静止实拍，仍可显示，只是不会动）。"""
        path = _asset_path(HEAD_PNG)
        pm = QPixmap(path) if path else QPixmap()
        if pm.isNull():
            logging.warning("[3D打印] 缺少喷头素材 %s，喷头保持原背景静止", HEAD_PNG)
            self._head_ready = False
        else:
            self._head_pix = pm
            self._head_ready = True

    # ---------- 外部接口 ----------
    def add_job(self, job, priority=False):
        if not isinstance(job, dict):
            return
        if priority:
            self._queue.insert(0, job)
        else:
            self._queue.append(job)
        while len(self._queue) > MAX_QUEUE:
            self._queue.pop(0)                   # 队列满时丢最旧的等待任务

    def add_event(self, msg, priority=None):
        job = _make_job(msg)
        if job is None:
            return
        if priority is None:
            priority = (job["source"] == "礼物")
        self.add_job(job, priority=priority)

    def seed(self, history):
        """打开窗口时回放最近的真实事件：最后一张开始打印，其余作为带上成品。"""
        jobs = [j for j in (_make_job(m) for m in (history or [])) if j][-4:]
        # 直播间排队方式：先出的牌已走到右侧，后出的靠左，固定间距
        for i, job in enumerate(reversed(jobs[:-1])):
            self._finished.append({"job": job,
                                   "x": BELT_X_START + 182.0 * i,
                                   "transit": 1.0,
                                   "y": self._belt_ground_y(BELT_X_START + 182.0 * i),
                                   "scale": 1.12})
        if jobs:
            self._queue.append(jobs[-1])
        # 没有任何真实数据时，自动演示一张（仅本窗口视觉，不进入任何数据流）
        if not self._queue and not self._finished:
            self._queue.append(_demo_job())

    def demo(self):
        self.add_job(_demo_job())

    def set_paused(self, paused):
        if paused:
            self._timer.stop()
        else:
            self._clock.restart()                 # 丢弃暂停期间的时间
            self._timer.start(FRAME_MS)

    def clear(self):
        self._queue.clear()
        self._finished.clear()
        self._current = None
        self._arm = None
        self._progress = 0.0
        self._done = 0
        self.update()

    def status(self):
        left = len(self._queue) + (1 if self._current else 0)
        return "队列 %d  ·  今日完成 %d" % (left, self._done)

    def shutdown(self):
        self._timer.stop()
        self._avatars.shutdown()

    # ---------- 动画推进 ----------
    def _tick(self):
        dt = min(self._clock.restart() / 1000.0, 0.1)
        self._t += dt
        self._belt_offset = (self._belt_offset + BELT_STRIPE_SPEED * dt) % 26.0

        if self._current is None and self._queue and self._arm is None:
            self._current = self._queue.pop(0)
            self._progress = 0.0

        if self._current is not None:
            self._progress += dt / PRINT_SECONDS
            # 喷头做左右填充往复，并随层数升高
            self._head_x = PRINT_CX + HEAD_SWEEP * math.sin(self._t * 6.4)
            self._head_y = BED_Y - 2 * DISC_R * min(self._progress, 1.0) - 5.0
            if self._progress >= 1.0:
                if self._photo_mode and self._arm_ready:
                    # 打印完成 → 机械臂取件；成品在松爪时才落带
                    self._arm = {"t": 0.0, "job": self._current,
                                 "dropped": False, "picked": False}
                else:
                    self._spawn_finished(self._current)
                self._current = None
                self._progress = 0.0
                self._done += 1
        else:
            # 待机：挤出位缓慢回中、轻微浮动
            self._head_x = _lerp(self._head_x, PRINT_CX, 0.06)
            self._head_y = _lerp(self._head_y, NOZZLE_Y + 3.0 * math.sin(self._t * 1.6), 0.08)

        if self._arm is not None:
            a = self._arm
            a["t"] += dt
            # 夹爪完全闭合后标记为「已夹住」，松爪前成品始终跟随夹爪（避免回弹到热床）
            if not a.get("picked") and a["t"] >= ARM_GRIP_CLOSE[1]:
                a["picked"] = True
            if not a["dropped"] and a["t"] >= ARM_DROP_T:
                self._spawn_finished(a["job"], start_x=ARM_DROP_X,
                                     start_y=ARM_DROP_Y)
                a["dropped"] = True
            if a["t"] >= ARM_SECONDS:
                self._arm = None

        for it in self._finished:
            if it["transit"] < 1.0:
                # 直播间模式：松爪后短距离落带，不做抛物线飞送
                it["transit"] = min(1.0, it["transit"] + dt / TRANSIT_SECONDS)
                t = _ease(it["transit"])
                x0, y0 = it.get("x0", PRINT_CX), it.get("y0", BED_Y)
                it["x"] = _lerp(x0, BELT_X_START, t)
                it["y"] = _lerp(y0, self._belt_ground_y(BELT_X_START), t) \
                    - 4.0 * math.sin(math.pi * t)
                it["scale"] = _lerp(0.95, 1.12, t)
            else:
                # 随传送带向右运走；成品 Y 跟随履带前缘斜率，全程贴带不沉不浮
                it["x"] += BELT_SPEED * dt
                it["y"] = self._belt_ground_y(it["x"])
        self._finished = [it for it in self._finished if it["x"] < BELT_X_EXIT]
        self.update()

    def _spawn_finished(self, job, start_x=None, start_y=None):
        # 机械臂模式从松爪点出发；无机械臂时从打印位出发（兼容兜底场景）
        x0 = PRINT_CX if start_x is None else start_x
        y0 = BED_Y if start_y is None else start_y
        self._finished.append({"job": job, "x": x0, "y": y0,
                               "x0": x0, "y0": y0,
                               "transit": 0.0, "scale": 0.95})
        while len(self._finished) > MAX_FINISHED:
            self._finished.pop(0)

    def _belt_ground_y(self, x):
        """成品在履带上时，底座底边应落在履带『带面』上（履带非水平，随 x 斜置）。

        照片里履带是一条梯形带面（左高右低），逐列像素扫描拟合出：
            后沿（上沿）y ≈ 0.1114 * x + 420.62
            前沿（下沿）y ≈ 0.0783 * x + 522.93
        带面高约 60~80px。成品需"坐"进带面中下部才自然——
        之前底座底边压在后沿附近（0.0746x+463.75），看着悬在带子上半部、偏高。
        这里把底座底边落在 带面 60% 处（靠近前沿但留约 24~32px 余量），
        ground_y = (后沿 + 0.6*(前沿-后沿)) - 38.08（底座高度 34*scale(1.12)≈38.08），
        使成品全程贴带、底座完整落在带区内、不超出前/后沿。
        程序化兜底场景用合成履带平移同量。
        """
        if getattr(self, "_photo_mode", True):
            band_top = 0.1114 * x + 420.62
            band_bot = 0.0783 * x + 522.93
        else:
            t = (x - 26.0) / (1268.0 - 26.0)
            tt = max(0.0, min(1.0, t))
            band_top = 392.0 + tt * (354.0 - 392.0)   # 合成履带后沿
            band_bot = 644.0 + tt * (616.0 - 644.0)    # 合成履带前沿
        seat = band_top + 0.60 * (band_bot - band_top)
        return seat - 38.08

    # ---------- 绘制 ----------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.fillRect(self.rect(), QColor("#ddd3c1"))
        s = min(self.width() / W, self.height() / H)
        p.translate((self.width() - W * s) / 2.0, (self.height() - H * s) / 2.0)
        p.scale(s, s)
        p.setClipRect(QRectF(0, 0, W, H))

        if self._bg is None:
            self._bg = self._render_background()
        p.drawPixmap(QRectF(0, 0, W, H), self._bg, QRectF(0, 0, W, H))

        self._draw_belt_stripes(p)
        self._draw_bed_glow(p)
        for it in sorted(self._finished, key=lambda a: a["x"]):
            self._draw_product(p, it["x"], it["y"], it["scale"], it["job"])
        if self._current is not None:
            self._draw_product(p, PRINT_CX, BED_Y, 1.0, self._current,
                               grow=min(self._progress, 1.0), printing=True)
        if self._photo_mode:
            # 底图自带真实机架/线材/喷头，只叠加挤出动作，避免画出「第二个喷头」
            self._draw_extrusion(p)
        else:
            # 底图缺失：退回纯绘制场景，仍保留完整动态机架
            self._draw_x_axis(p)
            self._draw_cables(p)
            self._draw_head(p)
        self._draw_arm(p)
        self._draw_status(p)
        self._draw_overlay(p)
        p.end()

    # ---------- 静态背景（只渲染一次，缓存成 QPixmap） ----------
    def _render_background(self):
        """优先贴照片级底图；缺图时退回内置程序化场景。"""
        pm = QPixmap(int(W), int(H))
        pm.fill(QColor("#1b1f24"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 优先用「已抠除喷头」的背景 + 可动喷头精灵；缺喷头素材时退回原背景
        # （原背景里喷头是静止实拍，仍能显示，只是不会沿导轨扫掠）。
        using_noh = _asset_path(HEAD_PNG) is not None
        path = _asset_path(HEAD_BG_NO) if using_noh else _bg_image_path()
        photo = QPixmap(path) if path else QPixmap()
        if not photo.isNull():
            p.drawPixmap(QRectF(0, 0, W, H), photo,
                         QRectF(0, 0, photo.width(), photo.height()))
            self._photo_mode = True
            if not using_noh:
                self._head_ready = False   # 原背景自带静止喷头，不再叠加精灵
            logging.info("[3D打印] 已加载实景底图: %s", path)
        else:
            self._photo_mode = False
            logging.warning("[3D打印] 未找到 %s，退回程序化场景", BG_REL)
            pm.fill(QColor("#ddd3c1"))
            self._bg_room(p)          # 墙 / 木地板 / 窗光 / 颗粒
            self._bg_furniture(p)     # 浅景深虚化的柜子 / 书架 / 绿植
            self._bg_frame(p)         # 铝型材龙门架 / 线材卷盘
            self._bg_bed(p)           # 黑玻璃热床
            self._bg_belt(p)          # 橡胶传送带 / 滚轴 / 支脚
            self._bg_vignette(p)      # 统一暗角，收住四角
        p.end()
        return pm

    def _bg_room(self, p):
        """暖色房间：墙面颗粒 + 左上窗光 + 带木纹与反光的地板。"""
        rng = random.Random(20261110)             # 固定种子，纹理每次一致
        wall = QLinearGradient(0, 0, 0, 430)
        wall.setColorAt(0, QColor("#efe8da"))
        wall.setColorAt(0.62, QColor("#e3d9c6"))
        wall.setColorAt(1, QColor("#cec0aa"))
        p.fillRect(QRectF(0, 0, W, 430), wall)
        light = QRadialGradient(QPointF(210, 30), 760)
        light.setColorAt(0, QColor(255, 250, 236, 120))
        light.setColorAt(0.55, QColor(255, 246, 226, 42))
        light.setColorAt(1, QColor(255, 246, 226, 0))
        p.fillRect(QRectF(0, 0, W, 430), light)
        # 墙面细微颗粒（乳胶漆质感）
        p.setPen(Qt.NoPen)
        for _ in range(2400):
            a = rng.randint(3, 11)
            p.setBrush(QColor(96, 80, 60, a))
            p.drawRect(QRectF(rng.uniform(0, W), rng.uniform(0, 430), 1.3, 1.3))
        # 踢脚线
        p.fillRect(QRectF(0, 408, W, 20), QColor("#ded3bf"))
        p.fillRect(QRectF(0, 408, W, 2), QColor(255, 255, 255, 75))
        p.fillRect(QRectF(0, 425, W, 3), QColor("#ad9d84"))
        p.fillRect(QRectF(0, 428, W, 2), QColor(60, 44, 28, 40))

        # 地板
        floor = QLinearGradient(0, 430, 0, H)
        floor.setColorAt(0, QColor("#b58a5c"))
        floor.setColorAt(0.45, QColor("#9a6f43"))
        floor.setColorAt(1, QColor("#6b4727"))
        p.fillRect(QRectF(0, 430, W, H - 430), floor)
        p.setPen(QPen(QColor(58, 36, 18, 60), 1.6))
        for x in range(-800, 2100, 148):
            p.drawLine(QPointF(640, 430), QPointF(x, H))
        y, step = 437.0, 9.0
        while y < H:
            p.setPen(QPen(QColor(52, 32, 15, 58), 1.3))
            p.drawLine(QPointF(0, y), QPointF(W, y))
            p.setPen(QPen(QColor(240, 210, 170, 22), 1))
            p.drawLine(QPointF(0, y + 2), QPointF(W, y + 2))
            y += step
            step *= 1.33
        # 木纹：细长、轻微弯曲、深浅交错
        for _ in range(760):
            yy = rng.uniform(432, H)
            xx = rng.uniform(-120, W)
            ln = rng.uniform(35, 230)
            a = rng.randint(8, 26)
            col = (QColor(48, 28, 12, a) if rng.random() < 0.55
                   else QColor(238, 208, 168, a))
            p.setPen(QPen(col, rng.uniform(0.5, 1.3)))
            path = QPainterPath(QPointF(xx, yy))
            path.quadTo(QPointF(xx + ln * 0.5, yy + rng.uniform(-2.5, 2.5)),
                        QPointF(xx + ln, yy + rng.uniform(-1.5, 1.5)))
            p.drawPath(path)
        # 地板上的柔和反光（打印机/传送带方向的落地光）
        refl = QRadialGradient(QPointF(650, 545), 460)
        refl.setColorAt(0, QColor(255, 232, 194, 40))
        refl.setColorAt(1, QColor(255, 232, 194, 0))
        p.fillRect(QRectF(180, 430, 940, 290), refl)

    def _bg_furniture(self, p):
        """浅景深虚化的背景陈设：左侧木柜 / 绿植 / 右侧书架。"""
        rng = random.Random(77)
        # 木柜
        _poly(p, [(0, 190), (256, 190), (256, 428), (0, 428)], QColor("#9a7347"))
        _poly(p, [(0, 182), (264, 182), (264, 198), (0, 198)], QColor("#7b5732"))
        p.fillRect(QRectF(0, 182, 264, 3), QColor(255, 240, 210, 55))
        p.setPen(QPen(QColor(60, 40, 20, 45), 1))
        for xx in range(6, 256, 11):
            p.drawLine(QPointF(xx, 200), QPointF(xx + rng.uniform(-3, 3), 428))
        for x in (16, 138):
            # 门板：外亮边 + 内凹阴影
            p.fillRect(QRectF(x, 210, 108, 202), QColor("#a87e4e"))
            p.setPen(QPen(QColor(58, 38, 18, 95), 2))
            p.drawRect(QRectF(x + 10, 222, 88, 178))
            p.setPen(QPen(QColor(230, 205, 165, 55), 1))
            p.drawRect(QRectF(x + 11, 223, 86, 176))
            p.setBrush(QColor("#c9b487"))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(x + 86, 304, 8, 8))
            p.setBrush(QColor(255, 255, 255, 90))
            p.drawEllipse(QRectF(x + 87, 305, 3, 3))
        # 右侧书架
        _poly(p, [(1146, 44), (1280, 44), (1280, 428), (1146, 428)], QColor("#866039"))
        for yy in (148, 258, 366):
            p.fillRect(QRectF(1142, yy, 142, 9), QColor("#654424"))
            p.fillRect(QRectF(1142, yy, 142, 2), QColor(255, 230, 190, 45))
        for xx, yy, hh, cc in [(1154, 96, 50, "#5c6f5b"), (1168, 84, 62, "#c1a486"),
                              (1184, 104, 42, "#8a5a4a"), (1199, 90, 58, "#4f6572"),
                              (1217, 108, 40, "#b39a6c"), (1232, 98, 50, "#6b5c50")]:
            p.fillRect(QRectF(xx, yy, 13, hh), QColor(cc))
            p.fillRect(QRectF(xx, yy, 2, hh), QColor(255, 255, 255, 30))
        # 绿植（细长叶片，不做卡通圆球）
        _poly(p, [(286, 396), (346, 396), (340, 442), (292, 442)], QColor("#b08069"))
        p.fillRect(QRectF(282, 392, 68, 7), QColor("#9c6c57"))
        p.setPen(Qt.NoPen)
        for bx, by, ex, ey, w in [(316, 396, 286, 318, 12), (316, 396, 318, 306, 13),
                                  (316, 396, 350, 320, 12), (316, 396, 268, 352, 10),
                                  (316, 396, 362, 356, 10), (316, 396, 300, 336, 9)]:
            leaf = QPainterPath(QPointF(bx, by))
            leaf.quadTo(QPointF((bx + ex) / 2 - w, (by + ey) / 2), QPointF(ex, ey))
            leaf.quadTo(QPointF((bx + ex) / 2 + w, (by + ey) / 2), QPointF(bx, by))
            p.setBrush(QColor.fromHsl(96, 30, rng.randint(85, 130)))
            p.drawPath(leaf)
        # 景深雾：降低背景对比，让焦点落在打印机上
        p.fillRect(QRectF(0, 0, 290, 430), QColor(233, 224, 208, 52))
        p.fillRect(QRectF(1120, 0, 160, 430), QColor(233, 224, 208, 52))
        p.fillRect(QRectF(250, 280, 120, 170), QColor(233, 224, 208, 40))

    def _bg_frame(self, p):
        """铝型材龙门架：拉丝金属 + 中央槽 + 螺栓 + 深蓝阳极件 + 线材卷盘。"""
        # 接地投影
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 14, 8, 70))
        p.drawEllipse(QRectF(340, 452, 520, 46))

        # 顶梁
        _shadow_round(p, QRectF(378, 96, 444, 26), 3, 6, 7, 70)
        _metal_v(p, QRectF(378, 96, 444, 26))
        p.fillRect(QRectF(378, 96, 444, 2), QColor(255, 255, 255, 90))
        p.fillRect(QRectF(378, 119, 444, 3), QColor(8, 12, 15, 150))
        p.fillRect(QRectF(392, 106, 416, 4), QColor("#214a6b"))
        for x in (390, 810):
            p.setBrush(QColor("#b9c1c7"))
            p.setPen(QPen(QColor("#3a4147"), 1))
            p.drawEllipse(QRectF(x - 4, 104, 8, 8))
            p.setBrush(QColor("#4b5359"))
            p.drawEllipse(QRectF(x - 1.5, 106.5, 3, 3))

        # 双立柱（2020 型材）
        for x in (392, 802):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(12, 10, 8, 80))
            p.drawRect(QRectF(x - 6, 118, 38, 344))
            _metal_h(p, QRectF(x - 13, 112, 26, 346))
            p.fillRect(QRectF(x + 13, 112, 7, 346), QColor("#1b2126"))   # 侧面厚度
            p.fillRect(QRectF(x - 13, 112, 2, 346), QColor(255, 255, 255, 65))
            p.fillRect(QRectF(x - 3, 112, 6, 346), QColor("#20262b"))     # 中央 T 槽
            p.fillRect(QRectF(x - 4, 112, 1, 346), QColor(255, 255, 255, 35))
            p.fillRect(QRectF(x + 3, 112, 1, 346), QColor(0, 0, 0, 70))
            p.setPen(QPen(QColor(10, 14, 17, 55), 1))
            for yy in range(124, 456, 19):
                p.drawLine(QPointF(x - 13, yy), QPointF(x + 20, yy))
            p.setPen(QPen(QColor(255, 255, 255, 30), 1))
            for yy in range(129, 456, 19):
                p.drawLine(QPointF(x - 13, yy), QPointF(x + 20, yy))
            # 蓝色阳极角件 + 螺栓
            p.fillRect(QRectF(x - 15, 436, 30, 22), QColor("#1e4a6c"))
            p.fillRect(QRectF(x - 15, 436, 30, 2), QColor(150, 195, 220, 70))
            for yy in (126, 446):
                p.setBrush(QColor("#aeb6bc"))
                p.setPen(QPen(QColor("#3b4248"), 1))
                p.drawEllipse(QRectF(x - 4, yy, 8, 8))

        # 底梁与橡胶脚
        _metal_v(p, QRectF(368, 440, 464, 20))
        p.fillRect(QRectF(368, 440, 464, 2), QColor(255, 255, 255, 70))
        p.fillRect(QRectF(368, 456, 464, 4), QColor("#10151a"))
        for x in (372, 788):
            _poly(p, [(x, 458), (x + 44, 458), (x + 50, 476), (x - 6, 476)],
                  QColor("#171d22"), "#3d464d", 1)
            p.fillRect(QRectF(x - 6, 474, 56, 3), QColor(0, 0, 0, 70))

        # 线材卷盘（真实缠绕层 + 金属毂）
        cx, cy = 404.0, 196.0
        p.setPen(QPen(QColor("#5c646a"), 5))
        p.drawLine(QPointF(404, 120), QPointF(404, 148))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(10, 8, 6, 70))
        p.drawEllipse(QRectF(cx - 51, cy - 47, 108, 108))
        p.setBrush(QColor("#14191d"))
        p.drawEllipse(QRectF(cx - 54, cy - 54, 108, 108))
        for rr in range(50, 25, -2):             # 一圈圈线材
            p.setPen(QPen(QColor("#3c444a") if rr % 4 else QColor("#242b30"), 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0d1216"))
        p.drawEllipse(QRectF(cx - 28, cy - 28, 56, 56))
        hub = QLinearGradient(cx - 22, cy - 22, cx + 22, cy + 22)
        hub.setColorAt(0, QColor("#b6bec4"))
        hub.setColorAt(0.5, QColor("#565e64"))
        hub.setColorAt(1, QColor("#242a2f"))
        p.setBrush(QBrush(hub))
        p.drawEllipse(QRectF(cx - 22, cy - 22, 44, 44))
        p.setBrush(QColor("#173c58"))
        p.drawEllipse(QRectF(cx - 13, cy - 13, 26, 26))
        p.setBrush(QColor("#0b1115"))
        p.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))
        p.setPen(QPen(QColor(255, 255, 255, 65), 2))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(cx - 50, cy - 50, 100, 100), 28 * 16, 96 * 16)

    def _bg_bed(self, p):
        """黑玻璃热床：金属边框 + 网格 + 斜向反射 + 落地投影。"""
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(18, 12, 7, 80))
        p.drawEllipse(QRectF(290, 470, 630, 60))
        _poly(p, [(362, 396), (838, 396), (906, 500), (294, 500)],
              QColor("#2d343a"), "#69737b", 1.6)
        inner = [(374, 402), (826, 402), (888, 492), (312, 492)]
        glass = QLinearGradient(320, 400, 880, 500)
        glass.setColorAt(0, QColor("#1b2126"))
        glass.setColorAt(0.42, QColor("#2b343b"))
        glass.setColorAt(0.62, QColor("#1a2025"))
        glass.setColorAt(1, QColor("#0d1216"))
        _poly(p, inner, glass)
        # 斜向玻璃反射
        _poly(p, [(420, 402), (610, 402), (548, 492), (352, 492)],
              QColor(214, 230, 240, 13))
        _poly(p, [(636, 402), (686, 402), (662, 492), (604, 492)],
              QColor(230, 240, 248, 9))
        p.setPen(QPen(QColor(150, 175, 190, 16), 1))
        for i in range(1, 10):
            t = i / 10.0
            p.drawLine(QPointF(_lerp(374, 312, t), _lerp(402, 492, t)),
                       QPointF(_lerp(826, 888, t), _lerp(402, 492, t)))
        for i in range(1, 7):
            t = i / 7.0
            p.drawLine(QPointF(_lerp(374, 826, t), 402),
                       QPointF(_lerp(312, 888, t), 492))
        p.setPen(QPen(QColor(210, 226, 236, 55), 1.4))
        p.drawLine(QPointF(374, 402), QPointF(826, 402))
        p.setPen(QPen(QColor(0, 0, 0, 120), 2))
        p.drawLine(QPointF(312, 492), QPointF(888, 492))
        # 立柱在玻璃上的淡淡倒影
        p.fillRect(QRectF(378, 404, 8, 84), QColor(180, 200, 215, 12))
        p.fillRect(QRectF(806, 404, 8, 84), QColor(180, 200, 215, 12))

    def _bg_belt(self, p):
        """橡胶传送带：细密纹路 + 金属滚轴 + 支脚 + 投影。"""
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(16, 11, 6, 90))
        p.drawEllipse(QRectF(20, 610, 1250, 78))
        _poly(p, [(120, 508), (1178, 494), (1280, 626), (18, 654)],
              QColor("#333b41"), "#6d777e", 1.8)
        band = QLinearGradient(0, 500, 0, 650)
        band.setColorAt(0, QColor("#2c3236"))
        band.setColorAt(0.35, QColor("#22282c"))
        band.setColorAt(1, QColor("#12171a"))
        _poly(p, [(128, 516), (1172, 502), (1268, 616), (26, 644)], band)
        # 橡胶细纹（静态细密横纹，动态层再叠滚动纹）
        clip = QPainterPath()
        clip.addPolygon(QPolygonF([QPointF(128, 517), QPointF(1172, 503),
                                   QPointF(1268, 615), QPointF(26, 643)]))
        clip.closeSubpath()
        p.save()
        p.setClipPath(clip)
        p.setPen(QPen(QColor(255, 255, 255, 7), 1))
        yy = 512.0
        while yy < 646:
            p.drawLine(QPointF(20, yy), QPointF(1280, yy - 12))
            yy += 5.0
        p.restore()
        p.setPen(QPen(QColor(190, 205, 215, 60), 1.5))
        p.drawLine(QPointF(128, 516), QPointF(1172, 502))
        p.setPen(QPen(QColor(0, 0, 0, 140), 2))
        p.drawLine(QPointF(26, 644), QPointF(1268, 616))
        # 两端滚轴
        for x1, x2, y1, y2 in [(112, 140, 508, 650), (1150, 1180, 496, 638)]:
            roll = QLinearGradient(x1, 0, x2, 0)
            roll.setColorAt(0, QColor("#4b5359"))
            roll.setColorAt(0.35, QColor("#aab2b8"))
            roll.setColorAt(0.7, QColor("#5c646a"))
            roll.setColorAt(1, QColor("#262c31"))
            _poly(p, [(x1, y1), (x2, y1 - 1), (x2 + 10, y2), (x1 + 10, y2 + 1)], roll)
        # 支脚
        for x in (110, 640, 1160):
            p.fillRect(QRectF(x - 9, 636, 18, 24), QColor("#20262b"))
            p.fillRect(QRectF(x - 9, 636, 3, 24), QColor(255, 255, 255, 30))
            p.fillRect(QRectF(x - 13, 658, 26, 5), QColor("#12171a"))

    def _bg_vignette(self, p):
        """四角暗角 + 统一暖色，让画面更像实拍而非插画。"""
        v = QRadialGradient(QPointF(640, 350), 790)
        v.setColorAt(0, QColor(20, 14, 8, 0))
        v.setColorAt(0.62, QColor(20, 14, 8, 0))
        v.setColorAt(1, QColor(20, 14, 8, 95))
        p.fillRect(QRectF(0, 0, W, H), v)
        p.fillRect(QRectF(0, 0, W, H), QColor(226, 196, 150, 9))

    # ---------- 动态层 ----------
    def _draw_belt_stripes(self, p):
        """带面滚动的橡胶纹路；照片底图上只留极淡的动态高光。"""
        if self._photo_mode:
            corners = [QPointF(195, 442), QPointF(1280, 563),
                       QPointF(1280, 623), QPointF(195, 538)]
            alpha, top, bottom, skew = 7, 495, 615, 52
        else:
            corners = [QPointF(128, 517), QPointF(1172, 503),
                       QPointF(1268, 615), QPointF(26, 643)]
            alpha, top, bottom, skew = 11, 500, 650, 62
        clip = QPainterPath()
        clip.addPolygon(QPolygonF(corners))
        clip.closeSubpath()
        p.save()
        p.setClipPath(clip)
        p.setPen(QPen(QColor(255, 255, 255, alpha), 1.4))
        x = -40.0 + self._belt_offset
        while x < 1330:
            p.drawLine(QPointF(x, top), QPointF(x - skew, bottom))
            x += 26.0
        p.restore()

    def _draw_bed_glow(self, p):
        """打印位的热床微光（很淡，避免卡通式蓝色光晕）。"""
        if self._current is None:
            return
        glow = QRadialGradient(QPointF(PRINT_CX, BED_Y - 4), 100)
        glow.setColorAt(0, QColor(255, 178, 108, 34))
        glow.setColorAt(1, QColor(255, 178, 108, 0))
        p.setBrush(QBrush(glow))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(PRINT_CX - 110, BED_Y - 38, 220, 76))

    def _draw_extrusion(self, p):
        """照片底图模式：叠加可动喷头（沿导轨左右扫掠）+ 挤出动作。

        喷头从实景底图抠成精灵，待机回中、打印时左右往复扫描（像真实打印机那样
        喷嘴在热床上往复填充）；喷嘴热光与当前层熔丝高光跟随喷头移动。
        """
        # 1) 可动喷头：待机回中，打印时左右扫掠；底图已抠除喷头，由这里重绘
        self._draw_head_photo(p)
        if self._current is None:
            return
        layer_y = BED_Y - 2.0 * DISC_R * min(self._progress, 1.0)
        nx = HEAD_LEFT + HEAD_NOZZLE_DX + (self._head_x - PRINT_CX)
        ny = HEAD_TOP + HEAD_NOZZLE_DY
        # 2) 喷嘴热光（跟随喷头）
        g = QRadialGradient(QPointF(nx, ny + 2), 17)
        g.setColorAt(0, QColor(255, 186, 116, 92))
        g.setColorAt(1, QColor(255, 186, 116, 0))
        p.setBrush(QBrush(g))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(nx - 18, ny - 16, 36, 36))
        # 3) 当前层熔丝高光（随喷头左右往复）
        wobble = 9.0 * math.sin(self._t * 12.0)
        hx = self._head_x + wobble
        p.setPen(QPen(QColor(255, 214, 164, 205), 2.2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(hx - 20, layer_y), QPointF(hx + 20, layer_y))
        p.setPen(QPen(QColor(255, 236, 206, 150), 1.0))
        p.drawLine(QPointF(hx - 12, layer_y + 2), QPointF(hx + 12, layer_y + 2))
        # 4) 极细挤出丝：从喷嘴连到当前层
        if layer_y > ny + 6:
            p.setPen(QPen(QColor(255, 226, 182, 165), 1.4))
            p.drawLine(QPointF(nx, ny + 2), QPointF(hx, layer_y - 1))
            p.setPen(QPen(QColor(255, 245, 222, 120), 0.6))
            p.drawLine(QPointF(nx + 0.8, ny + 2), QPointF(hx + 0.8, layer_y - 1))

    def _draw_head_photo(self, p):
        """照片底图里可动的打印头：沿导轨左右扫掠，待机回中。

        线材走线保持静止（钉在机架锚点与喷头待机位之间），不随喷头往复摆动——
        小幅扫掠（±32 < 喷头半宽）下线材下端始终落在喷头贴图体内，视觉上始终连着，
        且不出现「甩动/绷直」这类失真。
        """
        if not (self._photo_mode and self._head_ready):
            return
        off = self._head_x - PRINT_CX            # 喷头真实左右偏移（仅用于贴图）

        # —— 固定送料线材（不随喷头动）——
        ax, ay = HEAD_WIRE_ANCHOR                # 顶端机架固定锚点
        hx, hy = 602.0, HEAD_TOP + 2.0           # 喷头待机位顶部进线口（固定）
        # 一段轻微自然下垂的固定曲线（非动画）；中段略偏左做松弛感
        for dx_, col, wt in ((-3.5, QColor(196, 64, 64), 2.6),
                             (0.0,  QColor(70, 96, 170), 2.6),
                             (3.5, QColor(28, 28, 30), 2.6)):
            path = QPainterPath()
            path.moveTo(ax + dx_, ay)
            path.quadTo(QPointF((ax + hx) / 2.0 + dx_, (ay + hy) / 2.0 + 10.0),
                        QPointF(hx + dx_, hy))
            p.setPen(QPen(QColor(0, 0, 0, 30), wt + 1.5,
                          Qt.SolidLine, Qt.RoundCap))   # 细阴影/体积感
            p.drawPath(path)
            p.setPen(QPen(col, wt, Qt.SolidLine, Qt.RoundCap))
            p.drawPath(path)
        # 一束浅色特氟龙送料管（背景线材束末端的米白管）
        path0 = QPainterPath()
        path0.moveTo(ax + 7.0, ay)
        path0.quadTo(QPointF((ax + 7.0 + hx + 7.0) / 2.0, (ay + hy) / 2.0 + 10.0),
                     QPointF(hx + 7.0, hy))
        p.setPen(QPen(QColor(214, 206, 188, 120), 3.6, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(path0)

        # —— 打印头精灵：沿导轨左右扫掠（线不动、头动）——
        w, h = self._head_pix.width(), self._head_pix.height()
        p.drawPixmap(QRectF(HEAD_LEFT + off, HEAD_TOP, w, h),
                     self._head_pix, QRectF(0, 0, w, h))

    def _draw_disc(self, p, cx, cy, r, job, show_text=True):
        """亚克力头像立牌：厚边 + 金属圈 + 环境反射 + 真实头像/首字。

        底图是暖光照片，所以外圈略偏暖、下半部叠木地板反光，
        避免冷灰塑料片贴在实景上显得像贴纸。
        """
        p.setPen(Qt.NoPen)
        # 落在热床 / 带面上的接触阴影
        p.setBrush(QColor(16, 11, 7, 76))
        p.drawEllipse(QRectF(cx - r * 0.95, cy + r * 0.80,
                             r * 1.90, r * 0.34))
        # 亚克力厚度：右下偏移的暗侧壁
        p.setBrush(QColor("#0a0f13"))
        p.drawEllipse(QRectF(cx - r + 7, cy - r + 6, 2 * r, 2 * r))
        # 金属外圈（暖调，贴合房间光）
        rim = QLinearGradient(cx - r, cy - r, cx + r, cy + r)
        rim.setColorAt(0, QColor("#b9b3a6"))
        rim.setColorAt(0.32, QColor("#5a5a55"))
        rim.setColorAt(0.70, QColor("#252a2c"))
        rim.setColorAt(1, QColor("#716b60"))
        p.setBrush(QBrush(rim))
        p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
        p.setBrush(QColor("#10151a"))
        p.drawEllipse(QRectF(cx - r + 4, cy - r + 4, 2 * r - 8, 2 * r - 8))

        inner = QRectF(cx - r + 9, cy - r + 9, 2 * r - 18, 2 * r - 18)
        clip = QPainterPath()
        clip.addEllipse(inner)
        p.save()
        p.setClipPath(clip)
        pix = self._avatars.get(job["avatar"])
        if pix is not None and not pix.isNull():
            side = inner.width()
            scaled = pix.scaled(int(side), int(side), Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation)
            sx = (scaled.width() - side) / 2.0
            sy = (scaled.height() - side) / 2.0
            p.drawPixmap(inner, scaled, QRectF(sx, sy, side, side))
            p.fillRect(inner, QColor(20, 24, 28, 30))     # 压低饱和度，融入实景
        else:
            bg = QRadialGradient(QPointF(cx - r * 0.34, cy - r * 0.42), r * 1.7)
            bg.setColorAt(0, QColor.fromHsl(job["hue"], 22, 214))
            bg.setColorAt(0.62, QColor.fromHsl(job["hue"], 20, 178))
            bg.setColorAt(1, QColor.fromHsl(job["hue"], 18, 132))
            p.fillRect(inner, bg)
        # 环境光：上方冷窗光、下方暖木地板反射
        env = QLinearGradient(0, inner.top(), 0, inner.bottom())
        env.setColorAt(0, QColor(255, 255, 255, 34))
        env.setColorAt(0.42, QColor(255, 255, 255, 0))
        env.setColorAt(1, QColor(255, 190, 128, 38))
        p.fillRect(inner, env)
        if show_text:
            ch = job["nick"][:1]
            p.setPen(QColor(20, 24, 28, 95))
            p.setFont(_font(r * 0.56, family="Microsoft YaHei"))
            p.drawText(inner.translated(1.2, 1.6), Qt.AlignCenter, ch)
            p.setPen(QColor(255, 255, 255, 205))
            p.drawText(inner, Qt.AlignCenter, ch)
        # 斜向玻璃反光（一强一弱）
        _poly(p, [(cx - r, cy - r * 0.15), (cx - r * 0.2, cy - r),
                  (cx + r * 0.14, cy - r), (cx - r * 0.68, cy + r * 0.3)],
              QColor(255, 255, 255, 28))
        _poly(p, [(cx + r * 0.42, cy - r), (cx + r * 0.72, cy - r),
                  (cx + r * 0.16, cy + r), (cx - r * 0.06, cy + r)],
              QColor(255, 255, 255, 10))
        p.restore()
        # 外圈折射亮环 + 底部暗边
        p.setPen(QPen(QColor(255, 250, 236, 105), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(cx - r + 2, cy - r + 2, 2 * r - 4, 2 * r - 4),
                  28 * 16, 104 * 16)
        p.setPen(QPen(QColor(0, 0, 0, 80), 2))
        p.drawArc(QRectF(cx - r + 3, cy - r + 3, 2 * r - 6, 2 * r - 6),
                  200 * 16, 140 * 16)

    def _draw_base(self, p, x, ground_y, scale, job, printing=False):
        """黑色亚克力铭牌：倒角高光 + 细体文字，不再用粗白字。"""
        w, h = 140.0 * scale, 34.0 * scale
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(8, 6, 4, 90))
        p.drawEllipse(QRectF(x - w * 0.56, ground_y + h * 0.6,
                             w * 1.12, 12 * scale))
        plate = QLinearGradient(0, ground_y, 0, ground_y + h)
        plate.setColorAt(0, QColor("#333b43"))
        plate.setColorAt(0.4, QColor("#20272d"))
        plate.setColorAt(1, QColor("#11171c"))
        _poly(p, [(x - w / 2, ground_y - 2), (x + w / 2, ground_y - 2),
                  (x + w / 2 - 8, ground_y + h), (x - w / 2 + 8, ground_y + h)],
              plate, "#5a666f", 1.1)
        p.setPen(QPen(QColor(255, 255, 255, 80), 1))
        p.drawLine(QPointF(x - w / 2 + 7, ground_y), QPointF(x + w / 2 - 7, ground_y))
        if printing:
            p.setPen(QColor("#c3ced7"))
            p.setFont(_font(9.5 * scale))
            p.drawText(QRectF(x - w / 2, ground_y + 2, w, h - 2),
                       Qt.AlignCenter, "打印中 · %s" % job["tag"])
        else:
            p.setPen(QColor("#d2dae0"))
            p.setFont(_font(9.5 * scale))
            fm = QFontMetrics(p.font())
            nick = fm.elidedText(job["nick"], Qt.ElideRight, int(w - 14))
            p.drawText(QRectF(x - w / 2, ground_y + 1, w, 13 * scale),
                       Qt.AlignCenter, nick)
            p.setPen(QColor("#f2f5f7"))
            p.setFont(_font(15 * scale))
            p.drawText(QRectF(x - w / 2, ground_y + 12 * scale, w, 20 * scale),
                       Qt.AlignCenter, "%d分" % job["score"])

    def _draw_product(self, p, x, ground_y, scale, job,
                      grow=1.0, printing=False):
        r = DISC_R * scale
        cy = ground_y - r + 2.0 * scale          # 圆牌下缘嵌入铭牌
        if printing:
            # 逐层成型：只露出底部 grow 高度的圆盘，叠加 FDM 层纹 + 挤出亮线
            bottom = cy + r
            top = bottom - 2 * r * grow
            body = QPainterPath()
            body.addEllipse(QRectF(x - r, cy - r, 2 * r, 2 * r))
            clip = QPainterPath()
            clip.addRect(QRectF(x - r - 2, top, 2 * r + 4, 2 * r + 4))
            p.save()
            p.setClipPath(body.intersected(clip))
            # 未成型的部分只显示材料色与层纹，不露出半个昵称首字
            self._draw_disc(p, x, cy, r, job, show_text=False)
            p.setPen(QPen(QColor(0, 0, 0, 55), 1))
            yy = bottom
            while yy > top:
                p.drawLine(QPointF(x - r, yy), QPointF(x + r, yy))
                yy -= 3.5
            p.restore()
            # 当前层的熔融材料高光
            half = r * math.sqrt(max(0.0, 1.0 - ((top - cy) / r) ** 2))
            p.setPen(QPen(QColor(255, 206, 150, 190), 2.2))
            p.drawLine(QPointF(x - half, top), QPointF(x + half, top))
        else:
            self._draw_disc(p, x, cy, r, job)
        self._draw_base(p, x, ground_y, scale, job, printing=printing)

    # ---------- 机械臂：照片部件 + 逆运动学 ----------
    def _arm_pose(self, t):
        """按关键帧解出当前肩点/腕点/两段转角/夹爪开合(0=张开 1=夹紧)。

        腕点用 Catmull-Rom 平滑插值（速度连续），夹爪开合用 smoothstep，
        让「伸过去→夹住→搬走→松爪」整段动作顺滑，没有逐段急停的卡顿。
        """
        pts = [p for _, p in ARM_PATH]
        times = [tm for tm, _ in ARM_PATH]
        target = _catmull_rom(pts, times, t)
        g = 0.0                                  # 夹爪开合度
        if t > ARM_GRIP_CLOSE[0]:
            if t <= ARM_GRIP_OPEN[0]:
                g = _smoothstep((t - ARM_GRIP_CLOSE[0]) /
                                (ARM_GRIP_CLOSE[1] - ARM_GRIP_CLOSE[0]))
            elif t < ARM_GRIP_OPEN[1]:
                g = 1.0 - _smoothstep((t - ARM_GRIP_OPEN[0]) /
                                      (ARM_GRIP_OPEN[1] - ARM_GRIP_OPEN[0]))
        S, Wr, d1, d2 = _arm_ik(target)
        return S, Wr, d1, d2, g

    def _draw_arm(self, p):
        """待机时用底图里的静态实拍臂；动作期间擦除静态臂并用照片部件重绘。"""
        if not (self._photo_mode and self._arm_ready and self._arm):
            return
        S, Wr, d1, d2, g = self._arm_pose(self._arm["t"])
        pix = self._arm_pix

        def blit(key, x, y, w, h):
            pm = pix[key]
            p.drawPixmap(QRectF(x, y, w, h), pm,
                         QRectF(0, 0, pm.width(), pm.height()))

        # 1) 补丁只擦掉静态臂轮廓，其余像素保持原照片不动
        b = ARM_ASSETS["patch"][1]
        blit("patch", *b)
        # 2) 固定底座
        b = ARM_ASSETS["base"][1]
        blit("base", *b)
        # 3) 下臂绕底座关节旋转
        b, a = ARM_ASSETS["lower"][1], ARM_ASSETS["lower"][2]
        p.save()
        p.translate(ARM_BASE[0], ARM_BASE[1])
        p.rotate(d1)
        blit("lower", -a[0], -a[1], b[2], b[3])
        p.restore()
        # 4) 肩关节圆盘（盖住两段连杆的接缝）
        b, a = ARM_ASSETS["joint"][1], ARM_ASSETS["joint"][2]
        blit("joint", S[0] - a[0], S[1] - a[1], b[2], b[3])
        # 5) 上臂绕肩关节旋转
        b, a = ARM_ASSETS["upper"][1], ARM_ASSETS["upper"][2]
        p.save()
        p.translate(S[0], S[1])
        p.rotate(d2)
        blit("upper", -a[0], -a[1], b[2], b[3])
        p.restore()
        # 6) 夹爪始终竖直向下
        b, a = ARM_ASSETS["grip"][1], ARM_ASSETS["grip"][2]
        blit("grip", Wr[0] - a[0], Wr[1] - a[1], b[2], b[3])
        # 7) 打印成品的可见性（关键修复：打印完成后成品必须停在热床等待取件，
        #    不能凭空消失，也不能在夹爪到位前就「突然出现」）。
        #   - 夹爪仍张开且尚未夹住(g=0)：成品停在打印结束位（热床）等待；
        #     该位置与夹爪到位时的持物位(腕点+95)重合 → 夹爪一闭合即无缝接手。
        #   - 已夹住/搬运中(含松爪前)：成品跟随夹爪，随闭合程度由 1.0 平滑收到 0.86。
        #   - 松爪落带后(dropped)：成品交给传送带绘制，此处置空。
        a = self._arm
        if not a.get("dropped"):
            hx, hy = Wr[0], Wr[1] + 95.0
            if g <= 0.02 and not a.get("picked"):
                self._draw_product(p, PRINT_CX, BED_Y, 1.0, a["job"])
            else:
                self._draw_product(p, hx, hy, _lerp(1.0, 0.86, g), a["job"])

    def _draw_x_axis(self, p):
        """X 轴线性导轨随 Z 升降：银色导轨 + 同步带 + 金属滑块。"""
        gy = self._head_y - 58.0
        p.setPen(Qt.NoPen)
        p.fillRect(QRectF(390, gy - 4, 420, 20), QColor(8, 10, 12, 75))
        _metal_v(p, QRectF(386, gy - 8, 428, 15))
        p.fillRect(QRectF(386, gy - 8, 428, 1.5), QColor(255, 255, 255, 85))
        p.fillRect(QRectF(386, gy + 5, 428, 2), QColor("#10161a"))
        p.setPen(QPen(QColor(12, 14, 16, 170), 2))   # 同步带
        p.drawLine(QPointF(392, gy + 12), QPointF(808, gy + 12))
        for x in (392, 802):
            _metal_h(p, QRectF(x - 15, gy - 14, 30, 28), dark=True)
            p.setPen(QPen(QColor("#0c1115"), 1))
            p.drawRect(QRectF(x - 15, gy - 14, 30, 28))
            p.setBrush(QColor("#b9c1c7"))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(x - 4, gy - 4, 8, 8))

    def _draw_cables(self, p):
        """四色线材（低饱和）从卷盘拱到喷头，带细阴影。"""
        colors = ["#b2584f", "#56836a", "#b39a5b", "#4f7f9c"]
        for i, c in enumerate(colors):
            dx = (i - 1.5) * 6.0
            path = QPainterPath(QPointF(404 + dx * 0.5, 146))
            path.cubicTo(QPointF(408 + dx, 48), QPointF(566 + dx, 36 + i * 3),
                         QPointF(self._head_x + dx, self._head_y - 46))
            shadow = QPainterPath(path)
            shadow.translate(1.5, 2.0)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 45), 2.6, Qt.SolidLine, Qt.RoundCap))
            p.drawPath(shadow)
            p.setPen(QPen(QColor(c), 2.2, Qt.SolidLine, Qt.RoundCap))
            p.drawPath(path)
            p.setPen(QPen(QColor(255, 255, 255, 45), 0.7))
            p.drawPath(path)

    def _draw_head(self, p):
        """打印头：金属滑块 + 散热鳍片 + 风扇 + 黄铜加热块/喷嘴 + 挤出丝。"""
        hx, hy = self._head_x, self._head_y
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(8, 8, 8, 80))
        p.drawRoundedRect(QRectF(hx - 28, hy - 46, 64, 42), 4, 4)
        # 滑块
        _metal_h(p, QRectF(hx - 30, hy - 52, 60, 38), dark=True)
        p.setPen(QPen(QColor("#0d1216"), 1))
        p.drawRect(QRectF(hx - 30, hy - 52, 60, 38))
        # 散热鳍片
        for i in range(5):
            yy = hy - 47 + i * 6
            p.fillRect(QRectF(hx - 24, yy, 48, 3), QColor("#333b41"))
            p.fillRect(QRectF(hx - 24, yy, 48, 1), QColor(255, 255, 255, 40))
        # 风扇
        p.setBrush(QColor("#10151a"))
        p.setPen(QPen(QColor("#5b646a"), 1))
        p.drawEllipse(QRectF(hx - 13, hy - 43, 26, 26))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2c343a"))
        for a in (20, 140, 260):
            blade = QPainterPath(QPointF(hx, hy - 30))
            blade.quadTo(QPointF(hx + 9 * math.cos(math.radians(a + 35)),
                                 hy - 30 + 9 * math.sin(math.radians(a + 35))),
                         QPointF(hx + 10 * math.cos(math.radians(a + 70)),
                                 hy - 30 + 10 * math.sin(math.radians(a + 70))))
            blade.quadTo(QPointF(hx + 4 * math.cos(math.radians(a + 30)),
                                 hy - 30 + 4 * math.sin(math.radians(a + 30))),
                         QPointF(hx, hy - 30))
            p.drawPath(blade)
        p.setBrush(QColor("#9aa3a9"))
        p.drawEllipse(QRectF(hx - 3, hy - 33, 6, 6))
        # 黄铜加热块 + 喷嘴
        brass = QLinearGradient(hx - 12, 0, hx + 12, 0)
        brass.setColorAt(0, QColor("#8a6636"))
        brass.setColorAt(0.5, QColor("#d8b578"))
        brass.setColorAt(1, QColor("#74532c"))
        p.setBrush(QBrush(brass))
        p.drawRoundedRect(QRectF(hx - 12, hy - 17, 24, 11), 2, 2)
        _poly(p, [(hx - 9, hy - 7), (hx + 9, hy - 7),
                  (hx + 3, hy + 1), (hx - 3, hy + 1)],
              QColor("#c9a76a"), "#6e5028", 1)
        p.setPen(QPen(QColor(255, 226, 178, 170), 1))
        p.drawLine(QPointF(hx - 7, hy - 15), QPointF(hx + 7, hy - 15))
        # 挤出丝
        p.setPen(QPen(QColor(255, 214, 160, 210), 1.4))
        p.drawLine(QPointF(hx, hy), QPointF(hx, hy + 4))
        if self._current is not None:
            g = QRadialGradient(QPointF(hx, hy + 2), 14)
            g.setColorAt(0, QColor(255, 176, 104, 105))
            g.setColorAt(1, QColor(255, 176, 104, 0))
            p.setBrush(QBrush(g))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(hx - 16, hy - 14, 32, 32))

    def _draw_status(self, p):
        """固定在打印机左前方热床上的实物电子屏（非悬浮 HUD）。

        对齐直播间录屏：黑色 LCD 小屏立在左立柱前，金属拉丝外壳 + 螺丝 +
        电源 LED + 玻璃反光 + 支架；内容竖排分区，数字独立成列不挤压。
        """
        x, y, w, h = 330.0, 356.0, 164.0, 102.0
        prog = min(self._progress, 1.0) if self._current else 0.0
        left = len(self._queue) + (1 if self._current else 0)

        # 支架与底座（先画，让屏幕压在上面）
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(10, 8, 6, 105))
        p.drawEllipse(QRectF(x + w * 0.22, y + h + 20, w * 0.56, 11))
        _metal_v(p, QRectF(x + w / 2 - 5, y + h, 10, 22))
        p.fillRect(QRectF(x + w / 2 - 5, y + h, 2, 22), QColor(255, 255, 255, 55))
        _poly(p, [(x + w * 0.30, y + h + 19), (x + w * 0.70, y + h + 19),
                  (x + w * 0.76, y + h + 27), (x + w * 0.24, y + h + 27)],
              QColor("#20272d"), "#4c565e", 1.0)

        # 外壳：厚边 + 拉丝金属渐变 + 接地投影
        _shadow_round(p, QRectF(x, y, w, h), 6, 4, 6, 110)
        p.setBrush(QColor("#0a0e12"))
        p.drawRoundedRect(QRectF(x - 3, y - 3, w + 6, h + 6), 7, 7)
        shell = QLinearGradient(0, y, 0, y + h)
        shell.setColorAt(0, QColor("#4c565d"))
        shell.setColorAt(0.18, QColor("#2b3339"))
        shell.setColorAt(0.75, QColor("#1a2126"))
        shell.setColorAt(1, QColor("#0f1519"))
        p.setBrush(QBrush(shell))
        p.setPen(QPen(QColor("#66717a"), 1.1))
        p.drawRoundedRect(QRectF(x, y, w, h), 5, 5)
        p.setPen(QPen(QColor(255, 255, 255, 70), 1))
        p.drawLine(QPointF(x + 6, y + 1), QPointF(x + w - 6, y + 1))
        # 顶部电源指示灯
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#63c8a8"))
        p.drawRoundedRect(QRectF(x + w / 2 - 11, y + 3, 22, 2.5), 1.2, 1.2)
        # 四角螺丝
        for sx, sy in ((x + 5, y + 5), (x + w - 8, y + 5),
                       (x + 5, y + h - 8), (x + w - 8, y + h - 8)):
            p.setBrush(QColor("#5c666d"))
            p.drawEllipse(QRectF(sx, sy, 3.2, 3.2))
            p.setBrush(QColor(255, 255, 255, 70))
            p.drawEllipse(QRectF(sx + 0.7, sy + 0.7, 1.2, 1.2))

        # LCD 内屏
        sx, sy, sw, sh = x + 9.0, y + 10.0, w - 18.0, h - 20.0
        screen = QLinearGradient(0, sy, 0, sy + sh)
        screen.setColorAt(0, QColor("#0e1b22"))
        screen.setColorAt(0.55, QColor("#091318"))
        screen.setColorAt(1, QColor("#050a0d"))
        p.setBrush(QBrush(screen))
        p.setPen(QPen(QColor("#05090c"), 1.2))
        p.drawRoundedRect(QRectF(sx, sy, sw, sh), 2, 2)
        # LCD 极淡的背光网格
        p.setPen(QPen(QColor(110, 190, 210, 9), 0.6))
        gy = sy + 4
        while gy < sy + sh:
            p.drawLine(QPointF(sx + 3, gy), QPointF(sx + sw - 3, gy))
            gy += 6.0

        # 标题行：状态 + 百分比
        p.setPen(QColor("#7fa6b4"))
        p.setFont(_font(8.5))
        p.drawText(QRectF(sx + 7, sy + 3, 78, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, "打印状态")
        p.setPen(QColor("#6fc4d8"))
        p.setFont(_font(9, family="Arial"))
        p.drawText(QRectF(sx + sw - 48, sy + 3, 41, 16),
                   Qt.AlignRight | Qt.AlignVCenter, "%d%%" % int(prog * 100))
        p.setPen(QPen(QColor(120, 190, 210, 40), 1))
        p.drawLine(QPointF(sx + 7, sy + 21), QPointF(sx + sw - 7, sy + 21))

        # 两行数据：标签左、数字右，互不遮挡
        for row, (label, value, color) in enumerate([
                ("剩余打印", left, "#e8c58a"),
                ("今日完成", self._done, "#e9eef2")]):
            ry = sy + 24 + row * 24
            p.setPen(QColor("#b6c7ce"))
            p.setFont(_font(9.5))
            p.drawText(QRectF(sx + 7, ry, 80, 22),
                       Qt.AlignLeft | Qt.AlignVCenter, label)
            p.setPen(QColor(color))
            p.setFont(_font(16, bold=True, family="Arial"))
            p.drawText(QRectF(sx + sw - 62, ry, 55, 22),
                       Qt.AlignRight | Qt.AlignVCenter, str(value))
        # 进度条独立一行
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(120, 190, 210, 45))
        p.drawRoundedRect(QRectF(sx + 7, sy + sh - 9, sw - 14, 4), 2, 2)
        if prog > 0:
            p.setBrush(QColor("#5fb6d0"))
            p.drawRoundedRect(QRectF(sx + 7, sy + sh - 9,
                                     (sw - 14) * prog, 4), 2, 2)

        # 玻璃斜向反光（压在所有文字之上，很淡）
        _poly(p, [(sx, sy + sh * 0.10), (sx + sw * 0.34, sy),
                  (sx + sw * 0.52, sy), (sx, sy + sh * 0.62)],
              QColor(210, 240, 250, 12))

    def _draw_overlay(self, p):
        """不再叠加任何角标 / 标签；仅在完全空闲时给一行淡提示。"""
        if not self._current and not self._queue and not self._finished:
            p.setPen(QColor(238, 232, 220, 150))
            p.setFont(_font(11))
            p.drawText(QRectF(440, 548, 400, 30), Qt.AlignCenter,
                       "等待观众消息… 点下方「模拟一条」看效果")


# ---------------------------------------------------------------------------
# 独立窗口
# ---------------------------------------------------------------------------

_BTN = ("QPushButton{padding:5px 16px; font-size:12px; color:#e8ecf3;"
        "background:#2c3948; border:1px solid #3d4d5e; border-radius:4px;}"
        "QPushButton:hover{background:#3b4c60;}")


class Printer3DWindow(QWidget):
    """主页「3D打印」按钮弹出的独立窗口（1280×720 画面 + 底部工具条）。"""

    def __init__(self, parent=None):
        super().__init__(None, Qt.Window)
        self.setWindowTitle("树洞 · 3D打印现场")
        self.resize(1280, 782)
        self._paused = False

        self.scene = Printer3DScene()
        bar = QWidget()
        bar.setFixedHeight(46)
        bar.setStyleSheet("background:#161d26;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 5, 14, 5)
        self.lbl_status = QLabel("队列 0  ·  今日完成 0")
        self.lbl_status.setStyleSheet("color:#9fb0c2; font-size:12px;")
        lay.addWidget(self.lbl_status)
        lay.addStretch()
        self.btn_demo = QPushButton("模拟一条")
        self.btn_pause = QPushButton("暂停")
        self.btn_clear = QPushButton("清空")
        for b in (self.btn_demo, self.btn_pause, self.btn_clear):
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(_BTN)
            lay.addWidget(b)
        self.btn_demo.setToolTip("注入一条演示打印任务（仅本窗口视觉，不进入数据流）")
        self.btn_demo.clicked.connect(self.scene.demo)
        self.btn_pause.clicked.connect(self._on_pause)
        self.btn_clear.clicked.connect(self.scene.clear)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.scene, 1)
        root.addWidget(bar)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(400)

    # ---------- 外部（由 ReceiptSimulator 转发同一条真实事件） ----------
    def add_event(self, msg):
        self.scene.add_event(msg)

    def seed(self, history):
        self.scene.seed(history)

    # ---------- 按钮 ----------
    def _on_pause(self):
        self._paused = not self._paused
        self.scene.set_paused(self._paused)
        self.btn_pause.setText("继续" if self._paused else "暂停")

    def _refresh_status(self):
        self.lbl_status.setText(self.scene.status())

    def closeEvent(self, event):
        self._status_timer.stop()
        self.scene.shutdown()
        super().closeEvent(event)
